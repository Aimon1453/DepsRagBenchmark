"""Build and validate the v4 Text2Cypher bank, template by template.

Usage (repo root, Neo4j running with the frozen import):
  python benchmark/Text2Cypher/t2c_build_v4.py --templates C1.1 C1.2
  python benchmark/Text2Cypher/t2c_build_v4.py --templates C1.1 --quota 40 --dry-run

The build is deliberately incremental: `--templates` selects which entries of
`t2c_templates_v4.TEMPLATES` to build, so the merged 54-row bank can grow a
family at a time with every family validated before the next one lands. Running
without `--templates` builds everything registered.

Nothing enters the bank unvalidated. Every case must:

  V1  execute inside the timeout;
  V2  be **deterministic** - the gold query is run twice and the two row lists
      must be identical *in order*. This is the check that catches a gold query
      with no ORDER BY over a multi-row answer, which scores as noise later;
  V3  match its template's declared answer shape (columns, and one-row-ness for
      bool/scalar templates);
  V4  satisfy its stratum's expectation (an `absent_package` case that returned
      a non-empty answer means enumeration drifted, not that the case is hard);
  V5  carry no unfilled '{placeholder}' in question or Cypher;
  V6  be unique - no two cases with the same template and the same parameters;
  V7  (per template, no graph needed) declare a coherent shape: a list answer
      needs ORDER BY, a bool/scalar answer needs exactly one column, and the
      stratum shares must sum to 1;
  V8  contain no duplicate rows in a list answer, which means a missing DISTINCT.

A failure aborts the build, except V6 duplicates, which are dropped and counted.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

from dotenv import load_dotenv
from neo4j import GraphDatabase

T2C_DIR = Path(__file__).resolve().parent
REPO_ROOT = T2C_DIR.parents[1]
sys.path.insert(0, str(T2C_DIR))
load_dotenv(REPO_ROOT / ".env")

# The ecosystem round-robin, the ECO expression and the absent-package list are
# v3 work that v4 has no reason to reimplement.
from t2c_enumerate_bindings import ABSENT_PACKAGES, ECO, draw, run  # noqa: E402
from t2c_templates_v4 import TEMPLATES  # noqa: E402

GOLD_TIMEOUT = 60.0
PARAM_KEYS = ("pkg", "ver", "dep", "dep_ver", "depth", "cve")


# ---------------------------------------------------------------------------
# Pools and synthetic bindings
# ---------------------------------------------------------------------------

class Context:
    """Graph pools fetched once and shared by every template in the run."""

    def __init__(self, ses):
        self.ses = ses
        self.versions = run(ses, (
            f"MATCH (sw:Software)-[:HAS_VERSION]->(v:SoftwareVersion) "
            f"RETURN sw.name AS pkg, v.versionName AS ver, {ECO} AS eco"
        ))
        self.package_names = {r["pkg"] for r in self.versions}
        self.pkg_ver = {(r["pkg"], r["ver"]) for r in self.versions}
        self.a_version_of = {}
        for r in self.versions:
            self.a_version_of.setdefault(r["pkg"], r["ver"])


_NUM = re.compile(r"^(\d+)")
_PLACEHOLDER = re.compile(r"\{[A-Za-z_][A-Za-z0-9_]*\}")


def _bump(ver: str) -> list[str]:
    """Plausible neighbours of a real version string, most plausible first.

    "One patch up" is the interesting negative: it is exactly the string a model
    would produce if it were guessing from the package name instead of reading
    the graph.
    """
    parts = ver.split(".")
    out: list[str] = []
    for idx in (len(parts) - 1, 1, 0):
        if idx < 0 or idx >= len(parts):
            continue
        m = _NUM.match(parts[idx])
        if not m:
            continue
        head = m.group(1)
        bumped = list(parts)
        bumped[idx] = str(int(head) + 1) + parts[idx][len(head):]
        cand = ".".join(bumped)
        if cand != ver and cand not in out:
            out.append(cand)
    return out


def _near_miss_names(name: str) -> list[str]:
    """Names a careless entity resolver would accept for `name`.

    The KG identifies software by `schema:name` only, and the 2026-08-01 probe
    found real cross-ecosystem collisions (flask, click and cryptography exist as
    both PyPI packages and Rust crates). Negatives built from near-miss names
    test that resolution, rather than testing whether a model recognises an
    obviously foreign name.
    """
    cands = [name + "2", name + "-dev", "lib" + name, name + "-rs", name + "js"]
    if "-" in name:
        cands.insert(0, name.replace("-", "_"))
    if "_" in name:
        cands.insert(0, name.replace("_", "-"))
    if name.startswith("lib"):
        cands.insert(0, name[3:])
    return cands


def synth(kind: str, ctx: Context, rng: random.Random, n: int) -> list[dict]:
    out: list[dict] = []

    if kind in ("absent_package", "absent_package_versioned"):
        # Half from names that are real elsewhere in the world but provably
        # absent from SecureChain (npm has no presence in this KG at all), half
        # from near-miss variants of in-graph names.
        want_versioned = kind.endswith("_versioned")
        pool = sorted(ctx.package_names)
        # Consumed as we go. There are only ~10 of these, so once they run out
        # every candidate has to come from the near-miss branch - alternating
        # against an exhausted list would stall the loop at 2x its length.
        real_world = [p for p in ABSENT_PACKAGES if p not in ctx.package_names]
        seen: set[str] = set()
        attempts = 0
        while len(out) < n and attempts < n * 80:
            attempts += 1
            if len(out) % 2 == 0 and real_world:
                name = real_world.pop()
                ver = rng.choice(["1.0.0", "2.3.4", "4.17.21", "0.1.0"])
            else:
                base = rng.choice(pool)
                cands = [c for c in _near_miss_names(base) if c not in ctx.package_names]
                if not cands:
                    continue
                name = rng.choice(cands)
                ver = ctx.a_version_of.get(base, "1.0.0")
            if name in seen or name in ctx.package_names:
                continue
            seen.add(name)
            row = {"pkg": name, "eco": "absent"}
            if want_versioned:
                row["ver"] = ver
            out.append(row)

    elif kind == "near_miss_version":
        # A real package asked about a version one bump away from a real one.
        pool = list(ctx.versions)
        rng.shuffle(pool)
        for r in pool:
            if len(out) >= n:
                break
            cand = next((c for c in _bump(r["ver"]) if (r["pkg"], c) not in ctx.pkg_ver), None)
            if cand is None:
                continue
            out.append({"pkg": r["pkg"], "ver": cand, "eco": r["eco"]})

    else:
        raise ValueError("unknown synthetic stratum: " + kind)

    return out


# ---------------------------------------------------------------------------
# Enumeration
# ---------------------------------------------------------------------------

def enumerate_bindings(ses, tid: str, quota: int, rng: random.Random,
                       ctx: Context) -> tuple[list[dict], dict]:
    tpl = TEMPLATES[tid]
    picked: list[dict] = []
    leftovers: list[dict] = []
    report: dict = {"quota": quota, "strata": {}}
    seen: set[tuple] = set()

    # Largest-remainder allocation, so the per-stratum quotas sum to exactly
    # `quota`. Rounding each share independently overshoots (0.67/0.22/0.11 of
    # 80 rounds to 54+18+9 = 81), and a template that ships 81 cases while its
    # neighbours ship 80 puts an inconsistent n in every per-template table.
    exact = [quota * share for _, share, _, _ in tpl["strata"]]
    wants = [int(x) for x in exact]
    order = sorted(range(len(exact)), key=lambda i: exact[i] - wants[i], reverse=True)
    for i in order[: quota - sum(wants)]:
        wants[i] += 1

    for (name, share, source, expect), want in zip(tpl["strata"], wants):
        if isinstance(source, str) and source.startswith("SYNTH:"):
            cands = synth(source.split(":", 1)[1], ctx, rng, want * 3)
        else:
            cands = run(ses, source.format(ECO=ECO))
        got = draw(cands, want, rng)
        fresh = []
        for b in got:
            key = tuple(str(b.get(k)) for k in PARAM_KEYS)
            if key in seen:
                continue
            seen.add(key)
            fresh.append(dict(b, _stratum=name, _expect=expect))
        picked.extend(fresh)
        for b in cands:
            key = tuple(str(b.get(k)) for k in PARAM_KEYS)
            if key not in seen:
                leftovers.append(dict(b, _stratum=name, _expect=expect))
        report["strata"][name] = {"available": len(cands), "wanted": want, "drawn": len(fresh)}

    # Top up from whichever stratum still has candidates, so a template whose
    # small stratum ran dry still reaches the quota instead of shipping short.
    rng.shuffle(leftovers)
    topped = 0
    for b in leftovers:
        if len(picked) >= quota:
            break
        key = tuple(str(b.get(k)) for k in PARAM_KEYS)
        if key in seen:
            continue
        seen.add(key)
        picked.append(b)
        topped += 1
    report["topped_up"] = topped
    report["total"] = len(picked)
    report["ecosystems"] = dict(Counter(b.get("eco") for b in picked))
    return picked, report


# ---------------------------------------------------------------------------
# Gold execution + validation
# ---------------------------------------------------------------------------

class BuildError(Exception):
    pass


def _slug(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", s).strip("_")


def check_template(tid: str, tpl: dict) -> None:
    """Static checks that do not need the graph, run once per template (V7).

    A multi-row answer whose gold query has no ORDER BY has no defined row
    order. Re-running it usually *happens* to give the same order, so V2 cannot
    be relied on to catch it - the defect is that the order is unspecified, not
    that it is unstable. The review of the 54-row sheet found exactly this in
    its C2.3 ("what version of {dep} does {pkg} {ver} depend on"), which returns
    a bare `dep.versionName` with neither DISTINCT nor ORDER BY.
    """
    kind = tpl["answer_shape"]["kind"]
    cypher = tpl["cypher"]
    if kind in ("list", "table") and "ORDER BY" not in cypher.upper():
        raise BuildError(tid + ": V7 a " + kind + " template needs ORDER BY, "
                         "otherwise its row order is undefined")
    if kind in ("bool", "scalar") and len(tpl["answer_shape"]["columns"]) != 1:
        raise BuildError(tid + ": V7 a " + kind + " template must declare exactly one column")
    shares = sum(share for _, share, _, _ in tpl["strata"])
    if abs(shares - 1.0) > 1e-6:
        raise BuildError(tid + ": V7 stratum shares sum to " + str(shares) + ", not 1.0")


def _check_shape(rows: list[dict], shape: dict, where: str) -> None:
    kind, cols = shape["kind"], shape["columns"]
    if rows:
        got = list(rows[0].keys())
        if got != cols:
            raise BuildError(where + ": V3 columns " + repr(got) + " != declared " + repr(cols))
    if kind in ("bool", "scalar") and len(rows) != 1:
        raise BuildError(where + ": V3 " + kind + " template returned "
                         + str(len(rows)) + " rows, expected 1")
    if kind == "bool" and rows and not isinstance(rows[0][cols[0]], bool):
        raise BuildError(where + ": V3 bool template returned " + repr(rows[0][cols[0]]))
    # V8: a list answer scored row-wise must not contain the same row twice - a
    # duplicate is a missing DISTINCT in the gold, and it silently changes what
    # a row-level F1 score means.
    if kind in ("list", "table") and not shape.get("allow_duplicate_rows"):
        keys = [tuple(sorted(r.items())) for r in rows]
        if len(keys) != len(set(keys)):
            raise BuildError(where + ": V8 gold returned duplicate rows "
                             "(" + str(len(keys) - len(set(keys))) + " of " + str(len(keys))
                             + ") - the gold query is missing DISTINCT")


def _check_expect(rows: list[dict], expect: str | None, shape: dict, where: str) -> None:
    if expect is None:
        return
    col = shape["columns"][0]
    if expect == "empty" and rows:
        raise BuildError(where + ": V4 stratum expects an empty answer, got "
                         + str(len(rows)) + " rows")
    if expect == "nonempty" and not rows:
        raise BuildError(where + ": V4 stratum expects a non-empty answer, got none")
    if expect in ("true", "false"):
        want = expect == "true"
        if not rows or rows[0][col] is not want:
            raise BuildError(where + ": V4 stratum expects " + col + "=" + str(want)
                             + ", got " + repr(rows))


def build(ses, tids: list[str], quota: int, seed: int) -> tuple[list[dict], dict]:
    ctx = Context(ses)
    cases: list[dict] = []
    reports: dict = {}
    seen_ids: set[str] = set()
    seen_params: set[tuple] = set()

    for tid in tids:
        tpl = TEMPLATES[tid]
        # Seeded per template, not per run: the bank is built a family at a
        # time, and a shared stream would make a template's bindings depend on
        # which *other* templates happened to be built alongside it. With this,
        # rebuilding one template reproduces exactly the cases it had before.
        rng = random.Random("%d:%s" % (seed, tid))
        check_template(tid, tpl)                                       # V7
        bindings, report = enumerate_bindings(ses, tid, quota, rng, ctx)
        dropped = 0
        for raw in bindings:
            params = {k: v for k, v in raw.items() if k in PARAM_KEYS}
            question = tpl["question"].format(**params)
            cypher = tpl["cypher"].format(**params)
            where = tid + " " + repr(params)

            # A filled Cypher template still contains braces - Neo4j map
            # literals like `{name: 'meson'}` - so V5 looks for a bare
            # `{identifier}`, which only an unfilled placeholder produces.
            if "{" in question or _PLACEHOLDER.search(cypher):          # V5
                raise BuildError(where + ": V5 unfilled placeholder")

            with ses.begin_transaction(timeout=GOLD_TIMEOUT) as tx:    # V1
                rows = [dict(r) for r in tx.run(cypher)]
            with ses.begin_transaction(timeout=GOLD_TIMEOUT) as tx:
                rows2 = [dict(r) for r in tx.run(cypher)]
            if rows != rows2:                                          # V2
                raise BuildError(where + ": V2 gold query is not deterministic ("
                                 + str(len(rows)) + " rows, order differs between runs)")

            _check_shape(rows, tpl["answer_shape"], where)             # V3
            _check_expect(rows, raw.get("_expect"), tpl["answer_shape"], where)  # V4

            # V6 is about duplicate *questions*, so it compares parameters,
            # not slugs. Two packages whose names differ only in punctuation
            # ("typing-extensions" / "typing_extensions") slug to one id while
            # being two perfectly good questions; those get a suffix instead of
            # being thrown away.
            key = (tid, tuple(str(params[k]) for k in tpl["params"]))
            if key in seen_params:                                     # V6
                dropped += 1
                continue
            seen_params.add(key)
            base = "-".join([tid.replace(".", "_")]
                            + [_slug(str(params[k])) for k in tpl["params"]])
            cid, bump = base, 1
            while cid in seen_ids:
                bump += 1
                cid = base + "-" + str(bump)
            seen_ids.add(cid)

            cases.append({
                "id": cid,
                "template_id": tid,
                "family": tpl["family"],
                "source": tpl["source"],
                "v3_id": tpl["v3_id"],
                "params": params,
                "stratum": raw.get("_stratum"),
                "ecosystem": raw.get("eco"),
                "query_type": tpl["query_type"],
                "difficulty": tpl["difficulty"],
                "answer_shape": tpl["answer_shape"]["kind"],
                "question": question,
                "cypher_query": cypher,
                "expected_result": rows,
            })
        report["dropped_duplicates"] = dropped
        report["built"] = sum(1 for c in cases if c["template_id"] == tid)
        reports[tid] = report

    return cases, reports


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def summarize(cases: list[dict], reports: dict) -> None:
    by_tpl: dict[str, list[dict]] = defaultdict(list)
    for c in cases:
        by_tpl[c["template_id"]].append(c)

    print("\n%-7s%5s%7s%10s  %s" % ("tpl", "n", "empty", "distinct", "strata (drawn/available)"))
    for tid, group in by_tpl.items():
        empty = sum(1 for c in group if not c["expected_result"])
        distinct = len({json.dumps(c["expected_result"], sort_keys=True) for c in group})
        st = " ".join(k + "=" + str(v["drawn"]) + "/" + str(v["available"])
                      for k, v in reports[tid]["strata"].items())
        print("%-7s%5d%7d%10d  %s" % (tid, len(group), empty, distinct, st))

    print("\ntotal cases: %d" % len(cases))
    for key in ("difficulty", "query_type", "stratum", "ecosystem"):
        counts = Counter(c.get(key) for c in cases)
        print("  by %-10s: %s" % (key, dict(sorted(counts.items(), key=lambda kv: -kv[1]))))
    n_empty = sum(1 for c in cases if not c["expected_result"])
    print("  empty-answer cases: %d (%.1f%%)" % (n_empty, n_empty / max(len(cases), 1) * 100))


def print_samples(cases: list[dict], n: int) -> None:
    by_tpl: dict[str, list[dict]] = defaultdict(list)
    for c in cases:
        by_tpl[c["template_id"]].append(c)
    for tid, group in by_tpl.items():
        print("\n--- %s samples ---" % tid)
        strata = sorted({c["stratum"] for c in group})
        per = max(1, n // max(len(strata), 1))
        shown, count = [], Counter()
        for c in group:
            if count[c["stratum"]] < per:
                count[c["stratum"]] += 1
                shown.append(c)
        for c in shown[:n]:
            ans = json.dumps(c["expected_result"], ensure_ascii=False)
            print("[%-18s] %s" % (c["stratum"], c["question"]))
            print("    -> %s%s" % (ans[:150], "..." if len(ans) > 150 else ""))


def main() -> int:
    p = argparse.ArgumentParser(description="Build and validate the v4 Text2Cypher bank.")
    p.add_argument("--templates", nargs="*", default=None,
                   help="Template IDs to build (default: everything registered)")
    p.add_argument("--quota", type=int, default=80, help="Bindings per template")
    p.add_argument("--seed", type=int, default=20260819)
    p.add_argument("--out", default="t2c_purdue_dataset_v4.json")
    p.add_argument("--bindings-out", default="t2c_bindings_v4.json")
    p.add_argument("--samples", type=int, default=0, help="Print N sample cases per template")
    p.add_argument("--dry-run", action="store_true", help="Validate but write nothing")
    args = p.parse_args()

    tids = args.templates or list(TEMPLATES)
    unknown = [t for t in tids if t not in TEMPLATES]
    if unknown:
        print("unknown template ids: " + repr(unknown), file=sys.stderr)
        return 2

    driver = GraphDatabase.driver(
        os.environ["NEO4J_URI"],
        auth=(os.environ["NEO4J_USERNAME"], os.environ["NEO4J_PASSWORD"]),
    )
    try:
        with driver.session(database=os.environ.get("NEO4J_DATABASE", "neo4j")) as ses:
            cases, reports = build(ses, tids, args.quota, args.seed)
    finally:
        driver.close()

    summarize(cases, reports)
    if args.samples:
        print_samples(cases, args.samples)

    if args.dry_run:
        print("\ndry run: nothing written")
        return 0

    out = T2C_DIR / args.out
    bout = T2C_DIR / args.bindings_out
    # Merge with whatever is already built, so families can land one at a time.
    existing = json.loads(out.read_text(encoding="utf-8")) if out.exists() else []
    # A template struck out of the shared sheet is deleted from TEMPLATES, and
    # its cases have to leave the bank with it - otherwise a partial rebuild
    # silently carries retired questions forward.
    kept = [c for c in existing if c["template_id"] not in tids and c["template_id"] in TEMPLATES]
    retired = len(existing) - len(kept) - sum(1 for c in existing if c["template_id"] in tids)
    if retired:
        print("dropped %d case(s) of retired templates" % retired)
    merged = kept + cases
    out.write_text(json.dumps(merged, indent=1, ensure_ascii=False), encoding="utf-8")

    breports = json.loads(bout.read_text(encoding="utf-8")) if bout.exists() else {}
    breports["seed"] = args.seed
    breports["quota"] = args.quota
    breports.setdefault("report", {}).update(reports)
    bout.write_text(json.dumps(breports, indent=1, ensure_ascii=False), encoding="utf-8")

    print("\nwrote %d cases -> %s (%d rebuilt for %s, %d carried over)"
          % (len(merged), out.name, len(cases), ", ".join(tids), len(kept)))
    print("wrote binding report -> " + bout.name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
