"""Enumerate stratified parameter bindings for the v3 Text2Cypher dataset.

The v2 dataset carried 10 hand-curated bindings per template. v3 needs ~80, which
is past the point where hand-curation is honest work: this script draws them from
the frozen graph instead.

Each template declares **strata** — candidate pools that differ in the *kind* of
answer they produce (a populated result, an empty one, a true, a false). Quotas
per stratum are what keep the dataset from being gameable by a system that always
answers "something": a benchmark where every question has a non-empty answer
never punishes over-answering.

Within a stratum, candidates are drawn round-robin across ecosystems. This matters
here more than it sounds: the frozen graph is 94% crates.io by version count
(1,558 of 1,650, nearly all pulled in by the single `click 0.6.1` anchor), so
proportional sampling would produce a "supply chain" benchmark that is really a
Rust benchmark. Round-robin spends the small PyPI/Debian/Conan pools first and
lets crates.io fill the remainder, maximising ecosystem spread subject to what
the graph actually holds.

Sampling is seeded, so the same graph and seed give the same bindings.

Usage (repo root, Neo4j running with the frozen import):
  python benchmark/Text2Cypher/t2c_enumerate_bindings.py
  python benchmark/Text2Cypher/t2c_enumerate_bindings.py --quota 80 --seed 20260816
  python benchmark/Text2Cypher/t2c_enumerate_bindings.py --report-only
"""

from __future__ import annotations

import argparse
import json
import os
import random
from collections import Counter, defaultdict
from pathlib import Path

from dotenv import load_dotenv
from neo4j import GraphDatabase

T2C_DIR = Path(__file__).resolve().parent
REPO_ROOT = T2C_DIR.parents[1]
load_dotenv(REPO_ROOT / ".env")

# Ecosystem is not a stored property; it is the host inside the SecureChain URI
# (e.g. https://pypi.org/project/requests/ -> pypi.org).
ECO = "split(sw.uri,'/')[2]"

QUERY_TIMEOUT = 60.0

# Packages that are definitely absent from this KG, for existence negatives.
# npm has no presence in SecureChain at all, so npm-only names are safe picks.
ABSENT_PACKAGES = [
    "lodash", "express", "react", "webpack", "axios",
    "tensorflow", "pytorch", "rails", "laravel", "symfony",
]


# Version pairs that provably share at least one direct dependency. Drawing
# these at random from the version pool does not work: with 1,650 versions and
# 6,286 edges, two randomly chosen versions almost never overlap, which is how
# the first C5.1 draw ended up 89% empty-result.
PAIRS_SHARING = (
    "MATCH (sa:Software)-[:HAS_VERSION]->(a:SoftwareVersion)-[:DEPENDS_ON]->(:SoftwareVersion)"
    "<-[:DEPENDS_ON]-(b:SoftwareVersion)<-[:HAS_VERSION]-(sb:Software) "
    "WHERE elementId(a) < elementId(b) "
    "WITH DISTINCT sa, a, sb, b LIMIT 4000 "
    "RETURN sa.name AS pkg, a.versionName AS ver, sb.name AS dep, b.versionName AS dep_ver, "
    "split(sa.uri,'/')[2] AS eco"
)


def anchors(where: str, extra: str = "") -> str:
    """A (pkg, ver, eco) candidate query with a WHERE clause over version `v`."""
    return (
        f"MATCH (sw:Software)-[:HAS_VERSION]->(v:SoftwareVersion) WHERE {where} "
        f"{extra}RETURN sw.name AS pkg, v.versionName AS ver, {ECO} AS eco"
    )


# Each entry: template -> list of (stratum name, share of quota, source).
# A source is either a Cypher string or a callable(ctx, rng, n) -> list[dict].
STRATA: dict[str, list[tuple[str, float, object]]] = {
    "C1.1": [
        ("multi_version", 0.7,
         f"MATCH (sw:Software)-[:HAS_VERSION]->(v:SoftwareVersion) WITH sw, count(v) AS c WHERE c >= 2 "
         f"RETURN sw.name AS pkg, {ECO} AS eco"),
        ("single_version", 0.3,
         f"MATCH (sw:Software)-[:HAS_VERSION]->(v:SoftwareVersion) WITH sw, count(v) AS c WHERE c = 1 "
         f"RETURN sw.name AS pkg, {ECO} AS eco"),
    ],
    "C1.2": [
        ("has_vuln", 0.4, anchors("(v)-[:VULNERABLE_TO]->()")),
        ("no_vuln", 0.6, anchors("NOT (v)-[:VULNERABLE_TO]->()")),
    ],
    "C1.3": [
        ("exists", 0.6, anchors("true")),
        ("bad_version", 0.25, "SYNTH:bad_version"),
        ("bad_package", 0.15, "SYNTH:bad_package"),
    ],
    "C2.1": [
        ("has_deps", 0.7, anchors("(v)-[:DEPENDS_ON]->()")),
        ("no_deps", 0.3, anchors("NOT (v)-[:DEPENDS_ON]->()")),
    ],
    "C2.2": [
        ("direct", 0.5,
         f"MATCH (sw:Software)-[:HAS_VERSION]->(v:SoftwareVersion)-[:DEPENDS_ON]->(:SoftwareVersion)"
         f"<-[:HAS_VERSION]-(dsw:Software) "
         f"RETURN DISTINCT sw.name AS pkg, v.versionName AS ver, dsw.name AS dep, {ECO} AS eco"),
        ("transitive_only", 0.3,
         f"MATCH (sw:Software)-[:HAS_VERSION]->(v:SoftwareVersion)-[:DEPENDS_ON*2..4]->(:SoftwareVersion)"
         f"<-[:HAS_VERSION]-(dsw:Software) "
         f"WHERE NOT (v)-[:DEPENDS_ON]->(:SoftwareVersion)<-[:HAS_VERSION]-(dsw) "
         f"RETURN DISTINCT sw.name AS pkg, v.versionName AS ver, dsw.name AS dep, {ECO} AS eco"),
        ("unrelated", 0.2, "SYNTH:unrelated_dep"),
    ],
    "C2.3": [
        ("has_cve", 0.5, anchors("(v)-[:VULNERABLE_TO]->()")),
        ("no_cve", 0.5, anchors("NOT (v)-[:VULNERABLE_TO]->()")),
    ],
    "C2.4": [
        ("has_cwe", 0.5, anchors("(v)-[:VULNERABLE_TO]->()-[:VULNERABILITY_TYPE]->()")),
        ("no_cwe", 0.5, anchors("NOT (v)-[:VULNERABLE_TO]->()-[:VULNERABILITY_TYPE]->()")),
    ],
    "C2.5": [
        ("has_clean_dep", 0.7,
         anchors("(v)-[:DEPENDS_ON]->(:SoftwareVersion) AND "
                 "exists { MATCH (v)-[:DEPENDS_ON]->(d) WHERE NOT (d)-[:VULNERABLE_TO]->() }")),
        ("no_deps", 0.3, anchors("NOT (v)-[:DEPENDS_ON]->()")),
    ],
    "C3.1": [
        ("has_deps", 0.7, anchors("(v)-[:DEPENDS_ON]->()")),
        ("no_deps", 0.3, anchors("NOT (v)-[:DEPENDS_ON]->()")),
    ],
    "C3.2": [
        ("reachable_deep", 0.6,
         f"MATCH (sw:Software)-[:HAS_VERSION]->(v:SoftwareVersion)-[:DEPENDS_ON*2..4]->(:SoftwareVersion)"
         f"<-[:HAS_VERSION]-(dsw:Software) "
         f"RETURN DISTINCT sw.name AS pkg, v.versionName AS ver, dsw.name AS dep, {ECO} AS eco"),
        ("unreachable", 0.4, "SYNTH:unrelated_dep"),
    ],
    "C3.3": [
        ("reachable", 0.7,
         f"MATCH (sw:Software)-[:HAS_VERSION]->(v:SoftwareVersion)-[:DEPENDS_ON*1..4]->(:SoftwareVersion)"
         f"<-[:HAS_VERSION]-(dsw:Software) "
         f"RETURN DISTINCT sw.name AS pkg, v.versionName AS ver, dsw.name AS dep, {ECO} AS eco"),
        ("unreachable", 0.3, "SYNTH:unrelated_dep"),
    ],
    "C3.4": [
        ("depth_2", 0.35, "SYNTH:depth:2"),
        ("depth_3", 0.3, "SYNTH:depth:3"),
        ("depth_4", 0.2, "SYNTH:depth:4"),
        ("depth_empty", 0.15, "SYNTH:depth_empty"),
    ],
    "C3.5": [
        ("has_dependents", 0.7, anchors("(v)<-[:DEPENDS_ON]-()")),
        ("no_dependents", 0.3, anchors("NOT (v)<-[:DEPENDS_ON]-()")),
    ],
    "C3.6": [
        ("has_dependents", 0.7, anchors("(v)<-[:DEPENDS_ON]-()")),
        ("no_dependents", 0.3, anchors("NOT (v)<-[:DEPENDS_ON]-()")),
    ],
    "C4.1": [
        ("has_deps", 0.7, anchors("(v)-[:DEPENDS_ON]->()")),
        ("zero", 0.3, anchors("NOT (v)-[:DEPENDS_ON]->()")),
    ],
    "C4.2": [
        ("has_deps", 0.7, anchors("(v)-[:DEPENDS_ON]->()")),
        ("zero", 0.3, anchors("NOT (v)-[:DEPENDS_ON]->()")),
    ],
    "C4.3": [
        ("has_cve", 0.4, anchors("(v)-[:VULNERABLE_TO]->()")),
        ("zero", 0.6, anchors("NOT (v)-[:VULNERABLE_TO]->()")),
    ],
    "C4.4": [
        ("has_cwe", 0.4, anchors("(v)-[:VULNERABLE_TO]->()-[:VULNERABILITY_TYPE]->()")),
        ("zero", 0.6, anchors("NOT (v)-[:VULNERABLE_TO]->()-[:VULNERABILITY_TYPE]->()")),
    ],
    "C4.5": [
        ("has_vuln_dep", 0.5,
         anchors("exists { MATCH (v)-[:DEPENDS_ON*1..4]->(d) WHERE (d)-[:VULNERABLE_TO]->() }")),
        ("zero", 0.5,
         anchors("NOT exists { MATCH (v)-[:DEPENDS_ON*1..4]->(d) WHERE (d)-[:VULNERABLE_TO]->() }")),
    ],
    "C4.6": [
        # Only versions whose dependencies differ in version count make the
        # question meaningful; otherwise every candidate ties at 1 and the
        # tie-break carries the answer.
        ("spread", 0.7,
         f"MATCH (sw:Software)-[:HAS_VERSION]->(v:SoftwareVersion)-[:DEPENDS_ON]->(:SoftwareVersion)"
         f"<-[:HAS_VERSION]-(dsw:Software) "
         f"MATCH (dsw)-[:HAS_VERSION]->(av:SoftwareVersion) "
         f"WITH sw, v, dsw, count(DISTINCT av) AS nv WHERE nv > 1 "
         f"WITH sw, v, count(DISTINCT dsw) AS k WHERE k >= 2 "
         f"RETURN sw.name AS pkg, v.versionName AS ver, {ECO} AS eco"),
        ("no_deps", 0.3, anchors("NOT (v)-[:DEPENDS_ON]->()")),
    ],
    "C4.7": [
        ("has_deps", 0.7, anchors("(v)-[:DEPENDS_ON]->()")),
        ("zero", 0.3, anchors("NOT (v)-[:DEPENDS_ON]->()")),
    ],
    "C5.1": [
        # Random pairs almost never share a dependency, so the sharing stratum
        # must be read out of the graph rather than sampled and hoped for.
        ("sharing", 0.75, PAIRS_SHARING),
        ("disjoint", 0.25, "SYNTH:pair_random"),
    ],
    "C5.2": [
        ("both_deps", 0.6, "SYNTH:pair_random"),
        ("one_empty", 0.4, "SYNTH:pair_one_empty"),
    ],
    "C5.3": [
        ("both_vuln", 0.35, "SYNTH:pair_vuln"),
        ("one_vuln", 0.35, "SYNTH:pair_one_vuln"),
        ("neither", 0.3, "SYNTH:pair_clean"),
    ],
    "C5.4": [
        ("sharing", 0.5, PAIRS_SHARING),
        ("disjoint", 0.5, "SYNTH:pair_random"),
    ],
}

BINDING_KEYS = ("pkg", "ver", "dep", "dep_ver", "depth")


def run(ses, cypher: str) -> list[dict]:
    with ses.begin_transaction(timeout=QUERY_TIMEOUT) as tx:
        return [r.data() for r in tx.run(cypher)]


class Context:
    """Pools reused across templates, fetched once."""

    def __init__(self, ses):
        self.ses = ses
        self.with_deps = run(ses, anchors("(v)-[:DEPENDS_ON]->()"))
        self.no_deps = run(ses, anchors("NOT (v)-[:DEPENDS_ON]->()"))
        self.vulnerable = run(ses, anchors("(v)-[:VULNERABLE_TO]->()"))
        self.clean = run(ses, anchors("NOT (v)-[:VULNERABLE_TO]->()"))
        self.all_versions = self.with_deps + self.no_deps
        self.package_names = {r["pkg"] for r in self.all_versions}
        # Anchors that actually reach the given depth, for C3.4.
        self.at_depth = {
            d: run(ses, anchors(f"exists {{ MATCH (v)-[:DEPENDS_ON*{d}..{d}]->() }}"))
            for d in (2, 3, 4)
        }
        self.shallow = run(ses, anchors("(v)-[:DEPENDS_ON]->() AND "
                                        "NOT exists { MATCH (v)-[:DEPENDS_ON*3..3]->() }"))


def synth(kind: str, ctx: Context, rng: random.Random, n: int) -> list[dict]:
    """Bindings that cannot be read straight out of the graph."""
    out: list[dict] = []

    if kind == "bad_version":
        for r in rng.sample(ctx.all_versions, min(n, len(ctx.all_versions))):
            out.append({"pkg": r["pkg"], "ver": "99.99.99", "eco": r["eco"]})

    elif kind == "bad_package":
        for i in range(n):
            out.append({
                "pkg": ABSENT_PACKAGES[i % len(ABSENT_PACKAGES)],
                "ver": rng.choice(["1.0.0", "2.3.4", "4.17.21", "0.1.0"]),
                "eco": "absent",
            })

    elif kind == "unrelated_dep":
        # A real anchor paired with a real package it has no edge to. The gold
        # query decides the answer, so a rare accidental hit is still valid.
        names = sorted(ctx.package_names)
        for r in rng.sample(ctx.all_versions, min(n, len(ctx.all_versions))):
            out.append({"pkg": r["pkg"], "ver": r["ver"], "dep": rng.choice(names), "eco": r["eco"]})

    elif kind.startswith("depth:"):
        d = int(kind.split(":")[1])
        pool = ctx.at_depth[d]
        for r in rng.sample(pool, min(n, len(pool))):
            out.append({"pkg": r["pkg"], "ver": r["ver"], "depth": d, "eco": r["eco"]})

    elif kind == "depth_empty":
        # Shallow closures asked about a depth they do not reach -> empty gold.
        for r in rng.sample(ctx.shallow, min(n, len(ctx.shallow))):
            out.append({"pkg": r["pkg"], "ver": r["ver"], "depth": rng.choice([5, 6]), "eco": r["eco"]})

    elif kind in ("pair_random", "pair_one_empty",
                  "pair_vuln", "pair_one_vuln", "pair_clean"):
        left_pool, right_pool = {
            "pair_random": (ctx.with_deps, ctx.with_deps),
            "pair_one_empty": (ctx.with_deps, ctx.no_deps),
            "pair_vuln": (ctx.vulnerable, ctx.vulnerable),
            "pair_one_vuln": (ctx.vulnerable, ctx.clean),
            "pair_clean": (ctx.clean, ctx.clean),
        }[kind]
        seen = set()
        attempts = 0
        while len(out) < n and attempts < n * 60:
            attempts += 1
            a, b = rng.choice(left_pool), rng.choice(right_pool)
            if (a["pkg"], a["ver"]) == (b["pkg"], b["ver"]):
                continue
            key = (a["pkg"], a["ver"], b["pkg"], b["ver"])
            if key in seen:
                continue
            seen.add(key)
            out.append({"pkg": a["pkg"], "ver": a["ver"],
                        "dep": b["pkg"], "dep_ver": b["ver"], "eco": a["eco"]})
    else:
        raise ValueError(f"unknown synthetic stratum: {kind}")

    return out


def draw(candidates: list[dict], n: int, rng: random.Random) -> list[dict]:
    """Round-robin across ecosystems so small pools are represented at all."""
    by_eco: dict[str, list[dict]] = defaultdict(list)
    for c in candidates:
        by_eco[c.get("eco") or "unknown"].append(c)
    for pool in by_eco.values():
        rng.shuffle(pool)

    picked: list[dict] = []
    ecos = sorted(by_eco)
    while len(picked) < n and any(by_eco[e] for e in ecos):
        for e in ecos:
            if by_eco[e] and len(picked) < n:
                picked.append(by_eco[e].pop())
    return picked


def _key(b: dict) -> tuple:
    return tuple(str(b.get(k)) for k in BINDING_KEYS)


def enumerate_bindings(ses, quota: int, seed: int, target: int | None = None) -> tuple[dict, dict]:
    rng = random.Random(seed)
    ctx = Context(ses)
    bindings: dict[str, list[dict]] = {}
    report: dict[str, dict] = {}
    leftovers: dict[str, list[dict]] = {}
    taken: dict[str, set] = {}

    for tid, strata in STRATA.items():
        got: list[dict] = []
        spare: list[dict] = []
        detail = {}
        for name, share, source in strata:
            want = max(1, round(quota * share))
            if isinstance(source, str) and source.startswith("SYNTH:"):
                pool = synth(source[len("SYNTH:"):], ctx, rng, want * 4)
            else:
                pool = run(ses, source)
            chosen = draw(pool, want, rng)
            for c in chosen:
                c["_stratum"] = name
            chosen_keys = {_key(c) for c in chosen}
            for c in pool:
                if _key(c) not in chosen_keys:
                    c["_stratum"] = name
                    spare.append(c)
            detail[name] = {"available": len(pool), "wanted": want, "drawn": len(chosen)}
            got.extend(chosen)

        # Drop duplicates, keeping first occurrence. Package names collide across
        # ecosystems (`click` is both a PyPI and a crates.io package), and the
        # gold Cypher matches on name alone, so such a binding is ambiguous and
        # must not appear twice.
        seen, deduped = set(), []
        for b in got:
            if _key(b) in seen:
                continue
            seen.add(_key(b))
            deduped.append(b)

        bindings[tid] = deduped
        taken[tid] = seen
        rng.shuffle(spare)
        leftovers[tid] = spare
        report[tid] = {"total": len(deduped), "quota": quota, "strata": detail}

    # Top-up: templates capped by the graph (C3.5/C3.6 have only 11 versions that
    # nothing depends on) leave the total short. Redistribute the shortfall to
    # templates that still have candidates, rather than forcing a uniform quota
    # the graph cannot support.
    if target:
        added = Counter()
        while sum(len(v) for v in bindings.values()) < target:
            progressed = False
            for tid in bindings:
                if sum(len(v) for v in bindings.values()) >= target:
                    break
                pool = leftovers[tid]
                while pool:
                    cand = pool.pop()
                    if _key(cand) in taken[tid]:
                        continue
                    taken[tid].add(_key(cand))
                    bindings[tid].append(cand)
                    added[tid] += 1
                    progressed = True
                    break
            if not progressed:
                break
        for tid, r in report.items():
            r["topped_up"] = added.get(tid, 0)
            r["total"] = len(bindings[tid])

    for tid, r in report.items():
        r["ecosystems"] = dict(Counter(b.get("eco", "unknown") for b in bindings[tid]))

    return bindings, report


def main() -> int:
    p = argparse.ArgumentParser(description="Enumerate stratified v3 bindings from the frozen graph.")
    p.add_argument("--quota", type=int, default=80, help="Target bindings per template")
    p.add_argument("--target", type=int, default=2000, help="Total bindings; shortfall is redistributed")
    p.add_argument("--seed", type=int, default=20260816)
    p.add_argument("--out", default="t2c_bindings_v3.json")
    p.add_argument("--report-only", action="store_true", help="Print capacity/report without writing")
    args = p.parse_args()

    driver = GraphDatabase.driver(
        os.getenv("NEO4J_URI", "bolt://127.0.0.1:7689"),
        auth=(os.getenv("NEO4J_USERNAME", "neo4j"), os.getenv("NEO4J_PASSWORD", "password")),
    )
    try:
        with driver.session(database=os.getenv("NEO4J_DATABASE", "neo4j")) as ses:
            bindings, report = enumerate_bindings(ses, args.quota, args.seed, args.target)
    finally:
        driver.close()

    total = sum(len(v) for v in bindings.values())
    short = [t for t, r in report.items() if r["total"] < args.quota]

    print(f"{'tpl':<7}{'n':>5}{'+top':>6}  strata (available/drawn)")
    for tid, r in report.items():
        bits = " ".join(f"{k}={v['available']}/{v['drawn']}" for k, v in r["strata"].items())
        flag = "" if r["total"] >= args.quota else "  <-- capped by graph"
        print(f"{tid:<7}{r['total']:>5}{r.get('topped_up', 0):>6}  {bits}{flag}")

    print(f"\nbindings total: {total}  (target {args.target}) -> {total * 5} cases at 5 phrasings")
    if short:
        print(f"below per-template quota: {', '.join(short)}")

    eco_all = Counter()
    for r in report.values():
        eco_all.update(r["ecosystems"])
    print(f"ecosystem spread: {dict(eco_all)}")

    if not args.report_only:
        out_path = T2C_DIR / args.out
        payload = {
            "seed": args.seed,
            "quota": args.quota,
            "bindings": bindings,
            "report": report,
        }
        with out_path.open("w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)
        print(f"\nwrote {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
