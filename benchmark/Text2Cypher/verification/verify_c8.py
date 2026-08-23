"""Independent verification of the C8 (Dep x Vuln) cases: recompute every
answer in Python from the raw edge lists - BFS distances for the traversals,
set algebra for the intersections, DAG counting for C8.8's path uniqueness -
sharing no Cypher with the golds.

C8-specific extras: independently re-proves C8.8's binding guarantee (unique
nearest vulnerable dependency, unique shortest path), and replays the sheet's
original two-pattern C8.10 gold on shipped cases to quantify the
relationship-uniqueness loss (the C5.4 twin). Ends with a full C1-C7
regression replay because the C8 build rewrote the shared dataset file.
"""
import json, os, sys, collections, statistics, time
from pathlib import Path
from dotenv import load_dotenv
from neo4j import GraphDatabase

ROOT = Path(r"C:/Master Thesis/DepsRagBenchmark")
T2C = ROOT / "benchmark/Text2Cypher"
sys.path.insert(0, str(T2C))
load_dotenv(ROOT / ".env")

cases = json.loads((T2C / "t2c_purdue_dataset_v4.json").read_text(encoding="utf-8"))
c8 = [c for c in cases if c["template_id"].startswith("C8")]
rest = [c for c in cases if not c["template_id"].startswith("C8")]
print(f"{len(c8)} C8 cases, {len(rest)} other cases for regression replay")

drv = GraphDatabase.driver(os.environ["NEO4J_URI"],
    auth=(os.environ["NEO4J_USERNAME"], os.environ["NEO4J_PASSWORD"]),
    warn_notification_severity="OFF")
ses = drv.session(database=os.environ.get("NEO4J_DATABASE", "neo4j"))
def q(cy, timeout=120.0, **p):
    with ses.begin_transaction(timeout=timeout) as tx:
        return [dict(r) for r in tx.run(cy, **p)]

# ---- graph -> Python ------------------------------------------------------
vr = q("MATCH (sw:Software)-[:HAS_VERSION]->(v:SoftwareVersion) "
       "RETURN sw.name AS pkg, v.versionName AS ver, elementId(v) AS vid")
dr = q("MATCH (a:SoftwareVersion)-[:DEPENDS_ON]->(b:SoftwareVersion) "
       "RETURN elementId(a) AS a, elementId(b) AS b")
cv = q("MATCH (v:SoftwareVersion)-[:VULNERABLE_TO]->(c:Vulnerability) "
       "RETURN elementId(v) AS v, elementId(c) AS c, c.cveId AS cveId")
succs = collections.defaultdict(set)
for r in dr: succs[r["a"]].add(r["b"])
owners = collections.defaultdict(set)
vname, node_of, pkg_names = {}, {}, set()
for r in vr:
    vname[r["vid"]] = r["ver"]; owners[r["vid"]].add(r["pkg"])
    node_of[(r["pkg"], r["ver"])] = r["vid"]; pkg_names.add(r["pkg"])
cve_nodes_of = collections.defaultdict(set)   # version -> {cve node id}
cve_ids_of = collections.defaultdict(set)     # version -> {cveId string}
for r in cv:
    cve_nodes_of[r["v"]].add(r["c"]); cve_ids_of[r["v"]].add(r["cveId"])
vuln = set(cve_nodes_of)
print(f"graph: {len(vname)} versions, {len(dr)} edges, {len(vuln)} vulnerable versions")

def dists(start, cap=6):
    """BFS distances from start (start itself at 0), up to cap."""
    d = {start: 0}
    frontier = {start}
    depth = 0
    while frontier and depth < cap:
        depth += 1
        frontier = {x for f in frontier for x in succs[f] if x not in d}
        for x in frontier: d[x] = depth
    return d

def norm(rows):
    return sorted(tuple(sorted(str(v) for v in r.values())) for r in rows)

def norm_t(tuples):
    return sorted(tuple(sorted(str(x) for x in t)) for t in tuples)

fails = []
def check(cid, label, ok, detail=""):
    if not ok:
        fails.append({"id": cid, "check": label, "detail": str(detail)[:300]})

c88_unique_ok = 0
for c in c8:
    cid, tid, st, p = c["id"], c["template_id"], c["stratum"], c["params"]
    exp = c["expected_result"]
    pkg, ver = p.get("pkg"), p.get("ver")
    n = node_of.get((pkg, ver))

    # ---- recompute the answer ----
    truth = []
    if tid == "C8.2":
        if n is not None:
            cl = {x for x in dists(n) if x != n}
            ids = set()
            for x in cl & vuln: ids |= cve_ids_of[x]
            truth = [(i,) for i in ids]
        check(cid, "python-recompute", norm_t(truth) == norm(exp),
              f"truth_n={len(truth)} exp_n={len(exp)}")
    elif tid == "C8.5":
        if n is not None:
            tree = set(dists(n))
            for x in tree & vuln:
                for o in owners.get(x, set()):       # gold uses MATCH: owned only
                    truth.append((o, vname[x]))
        check(cid, "python-recompute", norm_t(set(truth)) == norm(exp),
              f"truth_n={len(set(truth))} exp_n={len(exp)}")
    elif tid == "C8.6":
        if n is not None:
            tree = set(dists(n))
            cwes = set()
            cve_nodes = set()
            for x in tree & vuln: cve_nodes |= cve_nodes_of[x]
            if cve_nodes:
                got = q("UNWIND $ids AS i MATCH (c:Vulnerability) WHERE elementId(c) = i "
                        "MATCH (c)-[:VULNERABILITY_TYPE]->(w) RETURN DISTINCT w.cweId AS cwe",
                        ids=list(cve_nodes))
                cwes = {r["cwe"] for r in got}
            truth = [(w,) for w in cwes]
        check(cid, "python-recompute", norm_t(truth) == norm(exp),
              f"truth_n={len(truth)} exp_n={len(exp)}")
    elif tid == "C8.7":
        if n is not None:
            d = dists(n)
            indirect = {x for x, dd in d.items() if 2 <= dd <= 6 and x not in succs[n] and x != n}
            for x in indirect & vuln:
                os_ = owners.get(x) or {None}
                for o in os_:
                    for i in cve_ids_of[x]:
                        truth.append((o, vname[x], i))
        check(cid, "python-recompute", norm_t(set(truth)) == norm(exp),
              f"truth_n={len(set(truth))} exp_n={len(exp)}")
    elif tid == "C8.8":
        if n is not None:
            d = dists(n)
            cands = {x: dd for x, dd in d.items() if x in vuln and x != n and dd >= 1}
            if cands:
                mh = min(cands.values())
                mins = [x for x, dd in cands.items() if dd == mh]
                # independent uniqueness proof: DAG-count shortest paths
                if st == "unique_nearest":
                    # count shortest paths by forward BFS layers
                    npaths = collections.defaultdict(int); npaths[n] = 1
                    layer = {n}
                    for depth in range(1, mh + 1):
                        nxt = collections.defaultdict(int)
                        for u in layer:
                            for w in succs[u]:
                                if d.get(w) == depth:
                                    nxt[w] += npaths[u]
                        for w, k in nxt.items(): npaths[w] += k
                        layer = set(nxt)
                    ok_unique = len(mins) == 1 and npaths[mins[0]] == 1
                    check(cid, "c88-unique-binding", ok_unique,
                          f"mins={len(mins)} paths={npaths[mins[0]] if len(mins)==1 else '-'}")
                    if ok_unique: c88_unique_ok += 1
                    # reconstruct THE path and compare
                    tgt = mins[0]; path = [tgt]
                    cur = tgt
                    for depth in range(mh - 1, -1, -1):
                        cur = next(u for u in (d.keys()) if d[u] == depth and cur in succs[u])
                        path.append(cur)
                    path = [vname[x] for x in reversed(path)]
                    check(cid, "python-recompute",
                          len(exp) == 1 and exp[0].get("path") == path and exp[0].get("hops") == mh,
                          f"path={path} mh={mh} exp={exp[:1]}")
            else:
                check(cid, "python-recompute", exp == [], f"exp_n={len(exp)}")
        else:
            check(cid, "python-recompute", exp == [], f"exp_n={len(exp)}")
    elif tid == "C8.9":
        if n is not None:
            for direct in succs[n]:
                sub = set(dists(direct, cap=5))
                cnodes = set()
                for x in sub & vuln: cnodes |= cve_nodes_of[x]
                os_ = owners.get(direct) or {None}
                for o in os_:
                    truth.append((o, vname[direct], len(cnodes)))
        check(cid, "python-recompute", norm_t(truth) == norm(exp),
              f"truth_n={len(truth)} exp_n={len(exp)}")
    elif tid == "C8.10":
        dep, dep_ver = p["dep"], p["dep_ver"]
        n2 = node_of.get((dep, dep_ver))
        if n is not None and n2 is not None:
            t1 = {x for x in dists(n) if x != n}
            t2 = {x for x in dists(n2) if x != n2}
            for x in (t1 & t2) & vuln:
                os_ = owners.get(x) or {None}
                for o in os_:
                    for i in cve_ids_of[x]:
                        truth.append((o, vname[x], i))
        check(cid, "python-recompute", norm_t(set(truth)) == norm(exp),
              f"truth_n={len(set(truth))} exp_n={len(exp)}")
    else:
        check(cid, "known-template", False, tid); continue

    # ---- stratum claims, independent logic ----
    def closure_vuln(node):
        return {x for x in dists(node) if x != node} & vuln
    if st in ("vuln_in_closure", "mixed_subtrees"):
        check(cid, "stratum", n is not None and bool(closure_vuln(n)), "vuln in closure expected")
    elif st in ("deps_no_vuln", "no_vuln_deps", "no_vuln_reachable", "all_zero_subtrees"):
        check(cid, "stratum", n is not None and bool(succs[n]) and not closure_vuln(n),
              "deps but clean closure expected")
    elif st in ("leaf_version",):
        check(cid, "stratum", n is not None and not succs[n], "leaf expected")
    elif st == "leaf_clean":
        check(cid, "stratum", n is not None and not succs[n] and n not in vuln, "clean leaf expected")
    elif st == "absent_package":
        check(cid, "stratum", pkg not in pkg_names, pkg)
    elif st == "vuln_in_tree":
        check(cid, "stratum", n is not None and bool(set(dists(n)) & vuln), "vuln in tree expected")
    elif st == "root_only_vuln":
        check(cid, "stratum", n is not None and n in vuln and not closure_vuln(n),
              "vulnerable root, clean closure expected")
    elif st == "cwe_in_tree":
        check(cid, "stratum", n is not None and bool(exp), "nonempty CWE tree expected")
    elif st == "root_only_cwe":
        check(cid, "stratum", n is not None and n in vuln and bool(exp), "root CWE expected")
    elif st == "clean_tree":
        check(cid, "stratum", n is not None and bool(succs[n]) and not (set(dists(n)) & vuln),
              "fully clean tree expected")
    elif st == "has_indirect_vuln":
        d = dists(n) if n is not None else {}
        check(cid, "stratum", any(2 <= dd <= 6 and x in vuln and x not in succs[n] and x != n
                                  for x, dd in d.items()), "indirect vuln expected")
    elif st == "vuln_direct_only":
        d = dists(n) if n is not None else {}
        has_direct_vuln = n is not None and bool(succs[n] & vuln)
        has_indirect = any(2 <= dd <= 6 and x in vuln and x not in succs[n] and x != n
                           for x, dd in d.items())
        check(cid, "stratum", has_direct_vuln and not has_indirect,
              "vulnerable direct deps only expected")
    elif st == "unique_nearest":
        pass  # proven above
    elif st == "share_vuln_dep":
        check(cid, "stratum", bool(exp), "nonempty intersection expected")
    elif st == "share_tree_no_vuln":
        dep, dep_ver = p["dep"], p["dep_ver"]
        n2 = node_of.get((dep, dep_ver))
        t1 = {x for x in dists(n) if x != n} if n is not None else set()
        t2 = {x for x in dists(n2) if x != n2} if n2 is not None else set()
        check(cid, "stratum", bool(t1 & t2) and not ((t1 & t2) & vuln),
              "intersect but no vuln expected")
    elif st == "disjoint_trees":
        dep, dep_ver = p["dep"], p["dep_ver"]
        n2 = node_of.get((dep, dep_ver))
        t1 = {x for x in dists(n) if x != n} if n is not None else set()
        t2 = {x for x in dists(n2) if x != n2} if n2 is not None else set()
        check(cid, "stratum", not (t1 & t2), "disjoint trees expected")
    else:
        check(cid, "known-stratum", False, st)

# ---- sheet C8.10 replay: quantify the relationship-uniqueness loss --------
SHEET_C810 = (
    "MATCH (s1:Software {name: $pkg})-[:HAS_VERSION]->(r1:SoftwareVersion {versionName: $ver}) "
    "MATCH (s2:Software {name: $dep})-[:HAS_VERSION]->(r2:SoftwareVersion {versionName: $dep_ver}) "
    "MATCH (r1)-[:DEPENDS_ON*1..6]->(d:SoftwareVersion)<-[:DEPENDS_ON*1..6]-(r2) "
    "MATCH (d)-[:VULNERABLE_TO]->(c:Vulnerability) "
    "OPTIONAL MATCH (ds:Software)-[:HAS_VERSION]->(d) "
    "RETURN DISTINCT ds.name AS software, d.versionName AS version, c.cveId AS cveId "
    "ORDER BY software, version, cveId"
)
c810_nonempty = [c for c in c8 if c["template_id"] == "C8.10" and c["expected_result"]]
import random
random.Random(20260823).shuffle(c810_nonempty)
sheet_sample, sheet_lost_cases, sheet_rows, true_rows = [], 0, 0, 0
for c in c810_nonempty[:40]:
    try:
        got = q(SHEET_C810, timeout=90.0, **c["params"])
    except Exception as e:
        sheet_sample.append({"id": c["id"], "error": str(e)[:120]}); continue
    lost = len(c["expected_result"]) - len(got)
    sheet_rows += len(got); true_rows += len(c["expected_result"])
    if lost > 0: sheet_lost_cases += 1
    if len(sheet_sample) < 10:
        sheet_sample.append({"id": c["id"], "sheet": len(got), "truth": len(c["expected_result"])})

# ---- gold replay: C8 twice, C1-C7 once ------------------------------------
times = []
for c in c8:
    t0 = time.perf_counter()
    r1 = q(c["cypher_query"])
    times.append((time.perf_counter() - t0) * 1000)
    r2 = q(c["cypher_query"])
    check(c["id"], "gold-replay", norm(r1) == norm(c["expected_result"]), f"got_n={len(r1)}")
    check(c["id"], "gold-deterministic", norm(r1) == norm(r2))

reg_fail = 0
for c in rest:
    r = q(c["cypher_query"])
    if norm(r) != norm(c["expected_result"]):
        reg_fail += 1
        check(c["id"], "regression-replay", False, f"got_n={len(r)}")

report = {
    "n": len(c8),
    "regression_n": len(rest),
    "regression_failures": reg_fail,
    "c88_unique_bindings_proven": c88_unique_ok,
    "sheet_c810_sampled": min(40, len(c810_nonempty)),
    "sheet_c810_cases_losing_rows": sheet_lost_cases,
    "sheet_c810_row_recall": round(sheet_rows / true_rows, 3) if true_rows else None,
    "sheet_c810_sample": sheet_sample,
    "failures": fails,
    "gold_ms": {"median": round(statistics.median(times), 1),
                "max": round(max(times), 1)},
}
out = T2C / "verify_c8_report.json"
out.write_text(json.dumps(report, indent=1), encoding="utf-8")
print(json.dumps({k: v for k, v in report.items() if k not in ("failures", "sheet_c810_sample")}, indent=1))
print(f"failures: {len(fails)}")
for f in fails[:20]:
    print(" ", f)
print("wrote", out)
ses.close(); drv.close()
