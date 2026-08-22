"""Independent verification of the C2.1/C2.2/C2.3 cases in the v4 bank.

Independent = the stratum semantics are re-checked with *different* Cypher than
the gold queries, so a bug shared by template and builder cannot vouch for
itself. Also replays every gold twice (determinism) and re-checks structure.
"""
import json, os, sys, time, collections, statistics
from pathlib import Path
from dotenv import load_dotenv
from neo4j import GraphDatabase

ROOT = Path(".").resolve()
load_dotenv(ROOT / ".env")
DS = ROOT / "benchmark/Text2Cypher/t2c_purdue_dataset_v4.json"
cases = [c for c in json.loads(DS.read_text(encoding="utf-8")) if c["template_id"].startswith("C2")]

drv = GraphDatabase.driver(os.environ["NEO4J_URI"],
    auth=(os.environ["NEO4J_USERNAME"], os.environ["NEO4J_PASSWORD"]))
ses = drv.session(database=os.environ.get("NEO4J_DATABASE", "neo4j"))

def q(cypher, **params):
    with ses.begin_transaction(timeout=60.0) as tx:
        return [dict(r) for r in tx.run(cypher, **params)]

# Independent semantic probes (parameterised, no string formatting).
IS_DIRECT = ("MATCH (:Software {name:$pkg})-[:HAS_VERSION]->(r:SoftwareVersion {versionName:$ver}) "
             "RETURN EXISTS { (r)-[:DEPENDS_ON]->(:SoftwareVersion)<-[:HAS_VERSION]-(:Software {name:$dep}) } AS x")
IS_REACH_2_6 = ("MATCH (:Software {name:$pkg})-[:HAS_VERSION]->(r:SoftwareVersion {versionName:$ver}) "
                "RETURN EXISTS { (r)-[:DEPENDS_ON*2..6]->(:SoftwareVersion)<-[:HAS_VERSION]-(:Software {name:$dep}) } AS x")
IS_REACH_2_4 = ("MATCH (:Software {name:$pkg})-[:HAS_VERSION]->(r:SoftwareVersion {versionName:$ver}) "
                "RETURN EXISTS { (r)-[:DEPENDS_ON*2..4]->(:SoftwareVersion)<-[:HAS_VERSION]-(:Software {name:$dep}) } AS x")
HAS_OUT = ("MATCH (:Software {name:$pkg})-[:HAS_VERSION]->(r:SoftwareVersion {versionName:$ver}) "
           "RETURN EXISTS { (r)-[:DEPENDS_ON]->() } AS x")
PKG_IN_GRAPH = "OPTIONAL MATCH (s:Software {name:$pkg}) RETURN s IS NOT NULL AS x"
VER_IN_GRAPH = ("OPTIONAL MATCH (:Software {name:$pkg})-[:HAS_VERSION]->(v:SoftwareVersion {versionName:$ver}) "
                "RETURN v IS NOT NULL AS x")

t0 = time.time()
fails = []          # (case id, check, detail)
times = []
by_tpl = collections.defaultdict(list)
seen_params = {}

for i, c in enumerate(cases):
    cid, tid, st, p = c["id"], c["template_id"], c["stratum"], c["params"]
    by_tpl[tid].append(c)

    # -- structural -------------------------------------------------------
    if "{" in c["question"]:
        fails.append((cid, "placeholder", c["question"]))
    key = (tid, tuple(sorted(p.items())))
    if key in seen_params:
        fails.append((cid, "param-duplicate", "same as " + seen_params[key]))
    seen_params[key] = cid
    rows_exp = c["expected_result"]
    if c["answer_shape"] in ("list", "table"):
        keys = [tuple(sorted(r.items())) for r in rows_exp]
        if len(keys) != len(set(keys)):
            fails.append((cid, "duplicate-rows", str(len(keys))))
    if c["answer_shape"] == "bool" and (len(rows_exp) != 1 or not isinstance(list(rows_exp[0].values())[0], bool)):
        fails.append((cid, "bool-shape", json.dumps(rows_exp)[:80]))

    # -- gold replay, twice -------------------------------------------------
    t1 = time.time()
    r1 = q(c["cypher_query"]); r2 = q(c["cypher_query"])
    times.append(time.time() - t1)
    if r1 != rows_exp:
        fails.append((cid, "gold-mismatch", f"stored {len(rows_exp)} rows, live {len(r1)}"))
    if r1 != r2:
        fails.append((cid, "nondeterministic", ""))

    # -- stratum semantics, independent cypher ------------------------------
    if st == "absent_package":
        if q(PKG_IN_GRAPH, pkg=p["pkg"])[0]["x"]:
            fails.append((cid, "stratum", "absent pkg exists: " + p["pkg"]))
    elif st == "has_direct_deps":
        if not q(HAS_OUT, **p)[0]["x"]:
            fails.append((cid, "stratum", "no outgoing DEPENDS_ON"))
    elif st == "leaf_version":
        if not q(VER_IN_GRAPH, pkg=p["pkg"], ver=p["ver"])[0]["x"]:
            fails.append((cid, "stratum", "leaf version not in graph"))
        elif q(HAS_OUT, pkg=p["pkg"], ver=p["ver"])[0]["x"]:
            fails.append((cid, "stratum", "leaf has outgoing deps"))
    elif st == "direct_dep":
        if not q(IS_DIRECT, **p)[0]["x"]:
            fails.append((cid, "stratum", "not a direct dep"))
    elif st == "grandchild_not_direct":
        if q(IS_DIRECT, **p)[0]["x"]:
            fails.append((cid, "stratum", "actually direct"))
        elif not q(IS_REACH_2_6, **p)[0]["x"]:
            fails.append((cid, "stratum", "not even reachable (should be in tree)"))
    elif st == "unrelated_dep":
        if q(IS_DIRECT, **p)[0]["x"] or q(IS_REACH_2_4, **p)[0]["x"]:
            fails.append((cid, "stratum", "reachable within 4 hops"))

    if (i + 1) % 150 == 0:
        print(f"  ...{i+1}/{len(cases)} verified, {len(fails)} failures", flush=True)

# ---- report ----------------------------------------------------------------
out = {
    "verified_at": time.strftime("%Y-%m-%d %H:%M"),
    "n_cases": len(cases),
    "n_failures": len(fails),
    "failures": [{"id": a, "check": b, "detail": d} for a, b, d in fails],
    "gold_ms": {"median": round(statistics.median(times)*1000, 1),
                 "p95": round(sorted(times)[int(len(times)*0.95)]*1000, 1),
                 "max": round(max(times)*1000, 1)},
    "wall_s": round(time.time() - t0, 1),
    "templates": {},
}
for tid, group in sorted(by_tpl.items()):
    strata = collections.Counter(c["stratum"] for c in group)
    ecos = collections.Counter(c["ecosystem"] for c in group)
    empty = sum(1 for c in group if not c["expected_result"])
    sizes = [len(c["expected_result"]) for c in group if c["expected_result"]]
    tinfo = {
        "n": len(group), "empty": empty,
        "strata": dict(strata), "ecosystems": dict(ecos),
        "distinct_answers": len({json.dumps(c["expected_result"], sort_keys=True) for c in group}),
        "rows_nonempty": {"min": min(sizes), "median": int(statistics.median(sizes)), "max": max(sizes)} if sizes else None,
    }
    if tid == "C2.2":
        tinfo["answer_split"] = dict(collections.Counter(str(c["expected_result"][0]["depends"]) for c in group))
    if tid == "C2.3":
        tinfo["multi_version_answers"] = sum(1 for c in group if len(c["expected_result"]) > 1)
    out["templates"][tid] = tinfo

Path("verify_c2_report.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
print(json.dumps(out, indent=1)[:2600])
ses.close(); drv.close()
