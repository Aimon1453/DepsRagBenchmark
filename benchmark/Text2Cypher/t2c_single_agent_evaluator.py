"""Text2Cypher benchmark runner — single-agent track with result persistence.

Unlike t2c_agent_evaluator.py (which runs the full DepsRAG team), this runner
initializes ONLY the DependencyGraphAgent, so the score isolates NL->Cypher
query generation: no team coordinator, no SearchAgent, no CriticAgent.
This matches the benchmark methodology's Text2Cypher track definition.

Cases run concurrently (--workers) and each result is appended to a JSONL file
as it completes, including the generated Cypher, execution status, and metrics.
A summary by template and difficulty is written to a .summary.json sidecar at
the end. Because the output filename is derived from dataset/model/protocol,
rerunning an interrupted campaign with the same command line resumes it: cases
already present in the JSONL are skipped by id.

Usage (repo root, Neo4j running, LLM key in .env):
  python benchmark/Text2Cypher/t2c_single_agent_evaluator.py
  python benchmark/Text2Cypher/t2c_single_agent_evaluator.py --dataset t2c_purdue_dataset_v2.json --provider google
  python benchmark/Text2Cypher/t2c_single_agent_evaluator.py --limit 3   # smoke test

  # self-hosted vLLM / DeepSeek / Kimi (any OpenAI-compatible endpoint)
  python benchmark/Text2Cypher/t2c_single_agent_evaluator.py \
      --provider vllm --model Qwen/Qwen3-32B --base-url http://localhost:8000/v1 \
      --dataset t2c_purdue_dataset_v2.json --workers 16 --format-hints
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

T2C_DIR = Path(__file__).resolve().parent
REPO_ROOT = T2C_DIR.parents[1]

sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(T2C_DIR))

load_dotenv(REPO_ROOT / ".env")

from dependencyrag.agno_agents import create_dependency_graph_agent
from dependencyrag.model_factory import create_model
from dependencyrag.neo4j_tools import get_neo4j_connection
import t2c_evaluator
from t2c_evaluator import evaluate_single_case

# Consecutive provider errors (in completion order) that abort the campaign.
PROVIDER_ERROR_ABORT = 3

PURDUE_SCHEMA_INSTRUCTIONS = """
CRITICAL INSTRUCTIONS (Purdue SecureChain subgraph in Neo4j):
1. Software products are `Software` nodes; use property `name` (e.g. 'requests').
2. Versions are `SoftwareVersion` nodes; use property `versionName` (e.g. '2.31.0'). NOT `Version`, NOT property `name` for versions.
3. Link software to version: `(s:Software {name: 'pkg'})-[:HAS_VERSION]->(v:SoftwareVersion {versionName: 'ver'})`
4. Direct/transitive dependencies: `(v)-[:DEPENDS_ON]->(dep:SoftwareVersion)`. Multi-hop: `[:DEPENDS_ON*1..6]`
5. To get dependency software name, match `(ds:Software)-[:HAS_VERSION]->(dep)`.
6. Vulnerabilities are nodes `Vulnerability` linked by `(v)-[:VULNERABLE_TO]->(c)`; CVE id in `c.cveId`.
7. CWE types: `(c)-[:VULNERABILITY_TYPE]->(w:VulnerabilityType)` with `w.cweId`.
8. Do NOT use Spanish-schema labels (`PyPIPackage`, `Version`, `HAVE`, `REQUIRE`) or `Version.vulnerabilities`.
9. Do NOT query `SecureChainImport` unless the question asks for import metadata.
10. DO NOT execute the query.
11. Final response: ONLY the Cypher in ```cypher ... ``` blocks, no other text.
"""


ANSWER_FORMAT_INSTRUCTIONS = """
12. Answer-format contract (your query's result table is compared against a gold result):
    - Dependency listing questions (direct or transitive): RETURN two columns — dependency software name AS software, and its version AS version. Never list the root version itself, even when a dependency cycle leads back to it (add `dep <> root` to transitive traversals).
    - EXCEPT when the question names the dependency and asks only for its version(s) ("What version(s) of X does Y depend on?"): RETURN one column — the version — since the software name is fixed by the question.
    - Yes/no questions ("Does ...?"): RETURN a single boolean value (e.g. count(x) > 0).
    - Counting questions ("How many ...?"): RETURN a single integer count.
    - Comparison questions ("Which has more ...?"): RETURN the two counts (first subject first) — do NOT return the winner's name or a CASE expression.
    - Dependency-path questions: use shortestPath over DEPENDS_ON and RETURN the list of versionName values along the path, e.g. RETURN [n IN nodes(p) | n.versionName] AS path.
    - Depth questions ("maximum dependency depth ..."): RETURN a single integer; if the version has no dependencies at all the answer is 0, not an empty table (use coalesce(max(...), 0)).
    - CVE / CWE listing questions: RETURN the id values only.
    - Top-N questions ("Which ... has the most ...?"): RETURN two columns — the winning software's name, and the quantity the question asks you to maximise — with ORDER BY on that quantity and LIMIT 1. Do NOT return only the name, and do NOT return only the quantity. (What the quantity means is stated in the question; this rule only fixes the shape of the answer.)
"""


class TimeoutConnection:
    """Neo4jConnection wrapper that enforces a server-side execution timeout.

    A generated query can be syntactically valid yet non-terminating on this
    graph — e.g. an unbounded `[:DEPENDS_ON*]` traversal over a closure with
    thousands of nodes enumerates paths combinatorially. Without a timeout such
    a case blocks the whole campaign, so the protocol scores it as a failed
    execution instead.
    """

    def __init__(self, conn, timeout_s: float):
        self._driver = conn.driver
        self._database = conn.database
        self._timeout = timeout_s

    def execute_query(self, query: str, parameters: dict | None = None):
        with self._driver.session(database=self._database) as session:
            with session.begin_transaction(timeout=self._timeout) as tx:
                result = tx.run(query, parameters or {})
                return [record.data() for record in result]

    def close(self):
        self._driver.close()


def detect_provider_error(response_text: str) -> str | None:
    """Return an error description if the response is an LLM-provider error payload.

    Some provider/SDK combinations surface API failures (quota exhaustion, auth,
    5xx) as the *content* of an otherwise successful response object rather than
    raising. Without this guard the harness would treat the error JSON as the
    generated query, fail to execute it, and silently record 170 "syntax errors"
    that look like model incompetence. Infrastructure failures must never be
    scored as wrong answers.
    """
    text = (response_text or "").strip()
    # A TLS-inspecting corporate proxy (observed: Netskope) can substitute an
    # HTML block page for the API response; that is infrastructure, not a model
    # answer, and must not be executed as Cypher.
    if text[:15].lower().startswith(("<html", "<!doctype")):
        return f"provider error: HTML response (proxy block page?): {text[:120]}"
    if not text.startswith("{") or '"error"' not in text:
        return None
    try:
        payload = json.loads(text)
    except ValueError:
        return None
    err = payload.get("error")
    if not isinstance(err, dict):
        return None
    return f"provider error {err.get('code')} {err.get('status')}: {str(err.get('message'))[:200]}"


def extract_cypher_from_response(response_text: str) -> str:
    """Extract the Cypher query from a ```cypher ...``` block, else raw text."""
    matches = re.findall(r"```(?:cypher)?\s*(.*?)\s*```", response_text, re.DOTALL | re.IGNORECASE)
    if matches:
        return matches[0].strip()
    return response_text.strip()


def build_prompt(question: str, format_hints: bool = False) -> str:
    instructions = PURDUE_SCHEMA_INSTRUCTIONS
    if format_hints:
        instructions = instructions.rstrip() + "\n" + ANSWER_FORMAT_INSTRUCTIONS
    return f"""You are being tested in a Text2Cypher benchmark.
Write a Cypher query that answers the question below.

Question: {question}

{instructions}
"""


def evaluate_case(case: dict, agent, conn, format_hints: bool) -> dict:
    """Run one case end to end: generate Cypher, execute it, score it."""
    t0 = time.time()
    try:
        response = agent.run(build_prompt(case["question"], format_hints=format_hints))
        raw = response.content or ""
        provider_error = detect_provider_error(raw)
        if provider_error:
            generated_cypher, agent_error = "", provider_error
        else:
            generated_cypher, agent_error = extract_cypher_from_response(raw), None
    except Exception as e:  # noqa: BLE001 - record and continue the campaign
        generated_cypher, agent_error = "", f"{type(e).__name__}: {e}"

    if generated_cypher:
        eval_result = evaluate_single_case(conn, case, generated_cypher)
    else:
        eval_result = {
            "executed_successfully": False,
            "error_message": agent_error or "empty response",
            "metrics": {},
        }

    metrics = eval_result.get("metrics") or {}
    if case["query_type"] in ("SR", "CR"):
        score = float(metrics.get("f1", 0.0))
    else:
        score = float(metrics.get("exact_match", 0.0))
    if not eval_result["executed_successfully"]:
        score = 0.0

    return {
        "id": case["id"],
        "template_id": case["template_id"],
        "query_type": case["query_type"],
        "difficulty": case["difficulty"],
        "question": case["question"],
        "gold_cypher": case["cypher_query"],
        "generated_cypher": generated_cypher,
        "agent_error": agent_error,
        "executed_successfully": eval_result["executed_successfully"],
        "error_message": eval_result.get("error_message"),
        "metrics": metrics,
        "score": round(score, 4),
        "latency_s": round(time.time() - t0, 2),
    }


def load_completed(path: Path) -> tuple[list[dict], set[str]]:
    """Read an existing results JSONL so an interrupted campaign can resume.

    Concurrency makes positional resume (`--start N`) meaningless, so completed
    work is identified by case id instead.
    """
    if not path.exists():
        return [], set()
    records = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except ValueError:
                continue  # tolerate a torn final line from a hard kill
    return records, {r["id"] for r in records}


def summarize(results: list[dict]) -> dict:
    def bucket(keyfn):
        out: dict = {}
        for r in results:
            k = keyfn(r)
            b = out.setdefault(k, {"n": 0, "executed": 0, "score_sum": 0.0})
            b["n"] += 1
            if r["executed_successfully"]:
                b["executed"] += 1
            b["score_sum"] += r["score"]
        for b in out.values():
            b["avg_score"] = round(b["score_sum"] / b["n"], 4) if b["n"] else 0.0
            del b["score_sum"]
        return dict(sorted(out.items()))

    n = len(results)
    executed = sum(1 for r in results if r["executed_successfully"])
    return {
        "total_cases": n,
        "executed_successfully": executed,
        "execution_rate": round(executed / n, 4) if n else 0.0,
        "avg_score": round(sum(r["score"] for r in results) / n, 4) if n else 0.0,
        "by_template": bucket(lambda r: r["template_id"]),
        "by_category": bucket(lambda r: r["template_id"].split(".")[0]),
        "by_difficulty": bucket(lambda r: r["difficulty"]),
        "by_query_type": bucket(lambda r: r["query_type"]),
    }


def main() -> int:
    p = argparse.ArgumentParser(description="Single-agent Text2Cypher benchmark runner.")
    p.add_argument("--dataset", default=os.getenv("BENCHMARK_DATASET", "t2c_purdue_dataset.json"))
    p.add_argument("--out", default=None, help="Results JSON (default: results_<dataset>_<timestamp>.json)")
    p.add_argument(
        "--provider",
        default=None,
        help='"openai" | "azure" | "google" | "openai_like"/"local"/"vllm"/"deepseek"/"kimi" (default: auto-detect)',
    )
    p.add_argument("--model", default="gpt-4o", help="Model id (google default comes from GOOGLE_MODEL_ID)")
    p.add_argument("--base-url", default=None, help="Endpoint for OpenAI-compatible providers (or OPENAI_COMPAT_BASE_URL)")
    p.add_argument("--api-key", default=None, help="Key for OpenAI-compatible providers (or OPENAI_COMPAT_API_KEY)")
    p.add_argument("--limit", type=int, default=None, help="Run only the first N cases (smoke test)")
    p.add_argument("--start", type=int, default=0, help="Skip the first N cases before selecting work")
    p.add_argument("--workers", type=int, default=8, help="Concurrent cases in flight")
    p.add_argument("--verbose", action="store_true", help="Print per-case expected/actual comparison dumps")
    p.add_argument(
        "--format-hints",
        action="store_true",
        help="Append the answer-format contract to the prompt (protocol variant under evaluation)",
    )
    p.add_argument(
        "--query-timeout",
        type=float,
        default=30.0,
        help="Per-query execution timeout in seconds; exceeding it scores the case as a failed execution",
    )
    args = p.parse_args()

    dataset_path = T2C_DIR / args.dataset
    with dataset_path.open(encoding="utf-8") as f:
        dataset = json.load(f)
    cases = dataset[args.start : (args.start + args.limit) if args.limit else None]

    t2c_evaluator.VERBOSE = args.verbose

    conn = TimeoutConnection(get_neo4j_connection(), args.query_timeout)

    def build_model():
        return create_model(
            args.model, provider=args.provider, base_url=args.base_url, api_key=args.api_key
        )

    model_id = getattr(build_model(), "id", args.model)

    # agno Agents are not documented as thread-safe, so each worker gets its own.
    local = threading.local()

    def agent_for_thread():
        if not hasattr(local, "agent"):
            local.agent = create_dependency_graph_agent(model=build_model(), db=None)
        return local.agent

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    protocol = "hints" if args.format_hints else "bare"
    default_name = f"results_{Path(args.dataset).stem}_{re.sub(r'[^A-Za-z0-9]+', '-', model_id)}_{protocol}.jsonl"
    out_path = T2C_DIR / (args.out or default_name)

    # Resume: a stable filename plus id-based skipping means an interrupted
    # campaign is restarted with the same command line.
    results, done_ids = load_completed(out_path)
    pending = [c for c in cases if c["id"] not in done_ids]

    print(f"Dataset : {args.dataset} ({len(cases)} cases, start={args.start})")
    print(f"Model   : {model_id} (single DependencyGraphAgent, no team)")
    print(f"Protocol: {protocol}, workers={args.workers}")
    if done_ids:
        print(f"Resuming: {len(done_ids)} already done, {len(pending)} to run")
    print(f"Results : {out_path}\n")

    run_meta = {
        "run_type": "single-agent Text2Cypher",
        "dataset": args.dataset,
        "model": model_id,
        "format_hints": args.format_hints,
        "query_timeout_s": args.query_timeout,
        "workers": args.workers,
        "started_utc": stamp,
        "note": "Dataset validation run; Text2Cypher track per methodology (DependencyGraphAgent only).",
    }

    write_lock = threading.Lock()
    abort = threading.Event()
    consecutive_provider_errors = 0
    done_count = len(results)

    # Append-only: rewriting the whole file per case is O(n^2) I/O and becomes
    # the bottleneck well before 10k cases.
    with out_path.open("a", encoding="utf-8") as sink:

        def work(case):
            if abort.is_set():
                return None
            return evaluate_case(case, agent_for_thread(), conn, args.format_hints)

        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            futures = {pool.submit(work, c): c for c in pending}
            for fut in as_completed(futures):
                record = fut.result()
                if record is None:
                    continue
                with write_lock:
                    results.append(record)
                    sink.write(json.dumps(record, ensure_ascii=False) + "\n")
                    sink.flush()
                    done_count += 1
                    n = done_count

                    # An infrastructure outage must abort the campaign, not be
                    # scored as a run of wrong answers. Under concurrency
                    # "consecutive" is in completion order, which still trips
                    # promptly on a real outage. Any agent_error counts: it is
                    # only ever set by a provider-error payload or an exception
                    # out of agent.run (network/SDK failure), never by a wrong
                    # answer — an SSL outage once scored 170 straight zeros here
                    # without tripping the old provider-error-prefix check.
                    if record["agent_error"]:
                        consecutive_provider_errors += 1
                        tripped = consecutive_provider_errors >= PROVIDER_ERROR_ABORT
                    else:
                        consecutive_provider_errors = 0
                        tripped = False

                status = "PASS" if record["executed_successfully"] else "FAIL"
                print(
                    f"[{n}/{len(cases)}] {record['id']} ({record['query_type']}/{record['difficulty']}) "
                    f"{status} score={record['score']:.2f} ({record['latency_s']}s)"
                )
                if record["agent_error"]:
                    print(f"    ERROR: {record['agent_error']}")

                if tripped and not abort.is_set():
                    abort.set()
                    print(
                        f"\nABORTING after {PROVIDER_ERROR_ABORT} consecutive provider errors "
                        f"— results so far are in {out_path}. Fix the provider issue and rerun "
                        f"the same command; completed cases are skipped automatically."
                    )

    conn.close()

    summary = summarize(results)
    summary_path = out_path.with_suffix(".summary.json")
    with summary_path.open("w", encoding="utf-8") as f:
        json.dump({"run": run_meta, "summary": summary}, f, indent=2, ensure_ascii=False)

    print("\n=== Overall (single-agent Text2Cypher) ===")
    print(f"Cases    : {summary['total_cases']}")
    print(f"Executed : {summary['executed_successfully']} ({summary['execution_rate']*100:.1f}%)")
    print(f"Avg score: {summary['avg_score']:.3f}")
    print(f"Saved    : {out_path}")
    print(f"Summary  : {summary_path}")
    return 1 if abort.is_set() else 0


if __name__ == "__main__":
    raise SystemExit(main())
