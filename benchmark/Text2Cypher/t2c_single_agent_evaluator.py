"""Text2Cypher benchmark runner — single-agent track with result persistence.

Unlike t2c_agent_evaluator.py (which runs the full DepsRAG team), this runner
initializes ONLY the DependencyGraphAgent, so the score isolates NL->Cypher
query generation: no team coordinator, no SearchAgent, no CriticAgent.
This matches the benchmark methodology's Text2Cypher track definition.

Per-case results are persisted incrementally to a JSON file (crash-safe:
the file is rewritten after every case), including the generated Cypher,
execution status, and metrics, plus a summary by template and difficulty.

Usage (repo root, Neo4j running, LLM key in .env):
  python benchmark/Text2Cypher/t2c_single_agent_evaluator.py
  python benchmark/Text2Cypher/t2c_single_agent_evaluator.py --dataset t2c_purdue_dataset_v2.json --provider google
  python benchmark/Text2Cypher/t2c_single_agent_evaluator.py --limit 3   # smoke test
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
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
from t2c_evaluator import evaluate_single_case

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
    - Dependency listing questions (direct or transitive): RETURN two columns — dependency software name AS software, and its version AS version.
    - Yes/no questions ("Does ...?"): RETURN a single boolean value (e.g. count(x) > 0).
    - Counting questions ("How many ...?"): RETURN a single integer count.
    - Comparison questions ("Which has more ...?"): RETURN the two counts (first subject first) — do NOT return the winner's name or a CASE expression.
    - Dependency-path questions: use shortestPath over DEPENDS_ON and RETURN the list of versionName values along the path, e.g. RETURN [n IN nodes(p) | n.versionName] AS path.
    - CVE / CWE listing questions: RETURN the id values only.
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
    p.add_argument("--provider", default=None, help='"openai" | "azure" | "google" (default: auto-detect)')
    p.add_argument("--model", default="gpt-4o", help="Model id (google default comes from GOOGLE_MODEL_ID)")
    p.add_argument("--limit", type=int, default=None, help="Run only the first N cases (smoke test)")
    p.add_argument("--start", type=int, default=0, help="Skip the first N cases (resume)")
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

    conn = TimeoutConnection(get_neo4j_connection(), args.query_timeout)

    model = create_model(args.model, provider=args.provider)
    agent = create_dependency_graph_agent(model=model, db=None)
    model_id = getattr(model, "id", args.model)

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_name = args.out or f"results_{Path(args.dataset).stem}_{stamp}.json"
    out_path = T2C_DIR / out_name

    print(f"Dataset : {args.dataset} ({len(cases)} cases, start={args.start})")
    print(f"Model   : {model_id} (single DependencyGraphAgent, no team)")
    print(f"Results : {out_path}\n")

    results: list[dict] = []
    run_meta = {
        "run_type": "single-agent Text2Cypher",
        "dataset": args.dataset,
        "model": model_id,
        "format_hints": args.format_hints,
        "query_timeout_s": args.query_timeout,
        "started_utc": stamp,
        "note": "Dataset validation run; Text2Cypher track per methodology (DependencyGraphAgent only).",
    }

    consecutive_provider_errors = 0

    for i, case in enumerate(cases, 1):
        print(f"[{i}/{len(cases)}] {case['id']} ({case['query_type']}/{case['difficulty']})")
        t0 = time.time()
        try:
            response = agent.run(build_prompt(case["question"], format_hints=args.format_hints))
            raw = response.content or ""
            provider_error = detect_provider_error(raw)
            if provider_error:
                generated_cypher = ""
                agent_error = provider_error
                print(f"    PROVIDER ERROR: {provider_error}")
            else:
                generated_cypher = extract_cypher_from_response(raw)
                agent_error = None
        except Exception as e:  # noqa: BLE001 - record and continue the campaign
            generated_cypher = ""
            agent_error = f"{type(e).__name__}: {e}"
            print(f"    AGENT ERROR: {agent_error}")

        # An infrastructure outage must abort the campaign, not be scored as a
        # run of wrong answers.
        if agent_error and agent_error.startswith("provider error"):
            consecutive_provider_errors += 1
            if consecutive_provider_errors >= 3:
                print(
                    f"\nABORTING after {consecutive_provider_errors} consecutive provider errors "
                    f"— results so far are in {out_path}. Fix the provider issue and resume with "
                    f"--start {args.start + i - consecutive_provider_errors}."
                )
                break
        else:
            consecutive_provider_errors = 0

        if generated_cypher:
            eval_result = evaluate_single_case(conn, case, generated_cypher)
        else:
            eval_result = {"executed_successfully": False, "error_message": agent_error or "empty response", "metrics": {}}

        metrics = eval_result.get("metrics") or {}
        if case["query_type"] in ("SR", "CR"):
            score = float(metrics.get("f1", 0.0))
        else:
            score = float(metrics.get("exact_match", 0.0))
        if not eval_result["executed_successfully"]:
            score = 0.0

        results.append(
            {
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
        )
        status = "PASS" if eval_result["executed_successfully"] else "FAIL"
        print(f"    {status} score={score:.2f} ({results[-1]['latency_s']}s)")

        # Crash-safe incremental persistence
        with out_path.open("w", encoding="utf-8") as f:
            json.dump({"run": run_meta, "summary": summarize(results), "results": results}, f, indent=2, ensure_ascii=False)

    summary = summarize(results)
    print("\n=== Overall (single-agent Text2Cypher) ===")
    print(f"Cases    : {summary['total_cases']}")
    print(f"Executed : {summary['executed_successfully']} ({summary['execution_rate']*100:.1f}%)")
    print(f"Avg score: {summary['avg_score']:.3f}")
    print(f"Saved    : {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
