"""Independent verification of the C9 (same package, multi-version) cases:
recompute every answer in Python from the raw edge lists, sharing no Cypher
with the golds.

C9-specific extras:
  * quantifies what the `<> package` cycle guard changed on C9.6 and C9.7 by
    recomputing each answer both with and without it (the justification for
    the two deviations);
  * checks distinct first sides and distinct answers on the three pair
    templates (the C5.5 sampling lesson);
  * replays the sheet's unguarded C9.6 gold on shipped cases to price the
    defect in the same terms used for C5.4 and C8.10.
Ends with a full C1-C8 regression replay because the C9 build rewrote the
shared dataset file.
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
c9 = [c for c in cases if c["template_id"].startswith("C9")]
rest = [c for c in cases if not c["template_id"].startswith("C9")]
print(f"{len(c9)} C9 cases, {len(rest)} other cases for regression replay")

drv = GraphDatabase.driver(os.environ["NEO4J_URI"],
    auth=(os.environ["NEO4J_USERNAME"], os.environ["NEO4J_PASSWORD"]),
    warn_notification_severity="OFF")
ses = drv.session(database=os.environ.get("NEO4J_DATABASE", "neo4j"))
def q(cy, timeout=180.0, **p):
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
owners = collections.defaultdict(set)          # version node -> {package name}
versions_of = collections.defaultdict(set)     # package -> {version node}
vname, node_of, pkg_names = {}, {}, set()
for r in vr:
    vname[r["vid"]] = r["ver"]; owners[r["vid"]].add(r["pkg"])
    versions_of[r["pkg"]].add(r["vid"])
    node_of[(r["pkg"], r["ver"])] = r["vid"]; pkg_names.add(r["pkg"])
cve_nodes_of, cve_ids_of = collections.defaultdict(set), collections.defaultdict(set)
for r in cv:
    cve_nodes_of[r["v"]].add(r["c"]); cve_ids_of[r["v"]].add(r["cveId"])
all_cve_ids = {r["cveId"] for r in cv}
print(f"graph: {len(vname)} versions, {len(dr)} edges, {len(cve_nodes_of)} vulnerable versions")

def reach(start, lo, hi):
    """Nodes at BFS depth in [lo, hi] from start; depth 0 is start itself."""
    d, frontier, depth = {start: 0}, {start}, 0
    while frontier and depth < hi:
        depth += 1
        frontier = {x for f in frontier for x in succs[f] if x not in d}
        for x in frontier: d[x] = depth
    return {x for x, dd in d.items() if lo <= dd <= hi}

def reach_min1(start, hi):
    """Nodes with a path of length 1..hi from start.

    NOT the same as "BFS distance in [1, hi]": Cypher's `*1..hi` lets a walk
    return to its own start node, so on this cyclic graph `start` itself
    belongs in the set whenever it sits on a cycle of length <= hi. C9.7's
    gold collects exactly this set as `old_nodes`, and getting it wrong here
    made the verifier - not the bank - disagree on 3 tokio/rustls cases.
    """
    seen, frontier = set(succs[start]), set(succs[start])
    for _ in range(hi - 1):
        frontier = {x for f in frontier for x in succs[f]} - seen
        if not frontier:
            break
        seen |= frontier
    return seen

def norm(rows):
    return sorted(tuple(sorted(str(v) for v in r.values())) for r in rows)

def norm_t(tuples):
    return sorted(tuple(sorted(str(x) for x in t)) for t in tuples)

fails = []
def check(cid, label, ok, detail=""):
    if not ok:
        fails.append({"id": cid, "check": label, "detail": str(detail)[:300]})

guard_changed = collections.Counter()   # template -> answers the guard changed
in_graph = collections.Counter()

for c in c9:
    cid, tid, st, p = c["id"], c["template_id"], c["stratum"], c["params"]
    exp = c["expected_result"]
    pkg, ver, ver2, dep, cve = (p.get("pkg"), p.get("ver"), p.get("ver2"),
                                p.get("dep"), p.get("cve"))
    truth = []

    if tid == "C9.1":
        truth = [(vname[v],) for v in versions_of.get(pkg, set()) if v in cve_nodes_of]
        check(cid, "python-recompute", norm_t(set(truth)) == norm(exp),
              f"truth_n={len(set(truth))} exp_n={len(exp)}")
    elif tid == "C9.2":
        truth = [(vname[v], len(cve_nodes_of.get(v, set())))
                 for v in versions_of.get(pkg, set())]
        check(cid, "python-recompute", norm_t(truth) == norm(exp),
              f"truth_n={len(truth)} exp_n={len(exp)}")
    elif tid == "C9.3":
        dep_nodes = versions_of.get(dep, set())
        for v in versions_of.get(pkg, set()):
            for d in succs[v] & dep_nodes:
                truth.append((vname[v], vname[d]))
        check(cid, "python-recompute", norm_t(set(truth)) == norm(exp),
              f"truth_n={len(set(truth))} exp_n={len(exp)}")
    elif tid == "C9.4":
        truth = [(vname[v], cve in cve_ids_of.get(v, set()))
                 for v in versions_of.get(pkg, set())]
        check(cid, "python-recompute", norm_t(truth) == norm(exp),
              f"truth_n={len(truth)} exp_n={len(exp)}")
    elif tid in ("C9.5", "C9.6", "C9.7"):
        old = node_of.get((pkg, ver)); new = node_of.get((pkg, ver2))
        if old is not None and new is not None:
            in_graph[tid] += 1
            if tid == "C9.5":
                # *0..6 on BOTH sides: the question says "including each root"
                old_cves = set()
                for x in reach(old, 0, 6): old_cves |= cve_ids_of.get(x, set())
                new_cves = set()
                for x in reach(new, 0, 6): new_cves |= cve_ids_of.get(x, set())
                truth = [(i,) for i in (new_cves - old_cves)]
            elif tid == "C9.6":
                old_names = set()
                for x in reach_min1(old, 6): old_names |= owners.get(x, set())
                new_names = set()
                for x in reach_min1(new, 6): new_names |= owners.get(x, set())
                unguarded = new_names - old_names
                guarded = unguarded - {pkg}
                if unguarded != guarded:
                    guard_changed[tid] += 1
                truth = [(s,) for s in guarded]
            else:   # C9.7
                old_nodes = reach_min1(old, 6)
                cand = {x for x in reach(new, 2, 6)
                        if x not in succs[new] and x not in old_nodes}
                unguarded = {x for x in cand if x in cve_nodes_of}
                guarded = unguarded - {new}
                if unguarded != guarded:
                    guard_changed[tid] += 1
                for x in guarded:
                    for o in (owners.get(x) or {None}):
                        for i in cve_ids_of[x]:
                            truth.append((o, vname[x], i))
        check(cid, "python-recompute", norm_t(set(truth)) == norm(exp),
              f"truth_n={len(set(truth))} exp_n={len(exp)}")
    else:
        check(cid, "known-template", False, tid); continue

    # ---- stratum claims, independent logic ----
    vs = versions_of.get(pkg, set())
    if st == "has_vuln_version":
        check(cid, "stratum", any(v in cve_nodes_of for v in vs), pkg)
    elif st == "no_vuln_version":
        check(cid, "stratum", bool(vs) and not any(v in cve_nodes_of for v in vs), pkg)
    elif st in ("absent_package", "absent_package_cve"):
        check(cid, "stratum", pkg not in pkg_names, pkg)
    elif st == "multi_version_with_vuln":
        check(cid, "stratum", len(vs) >= 2 and any(v in cve_nodes_of for v in vs), pkg)
    elif st == "multi_version_clean":
        check(cid, "stratum", len(vs) >= 2 and not any(v in cve_nodes_of for v in vs), pkg)
    elif st == "single_version":
        check(cid, "stratum", len(vs) == 1, pkg)
    elif st == "varying_dep_version":
        dn = versions_of.get(dep, set())
        used = {d for v in vs for d in succs[v] & dn}
        check(cid, "stratum", len(used) >= 2, f"{len(used)} dep versions")
    elif st == "uniform_dep_version":
        dn = versions_of.get(dep, set())
        used = {d for v in vs for d in succs[v] & dn}
        check(cid, "stratum", len(used) == 1, f"{len(used)} dep versions")
    elif st == "unrelated_dep":
        dn = versions_of.get(dep, set())
        check(cid, "stratum", not any(succs[v] & dn for v in vs), (pkg, dep))
    elif st == "absent_dep":
        check(cid, "stratum", dep not in pkg_names, dep)
    elif st == "real_pair":
        check(cid, "stratum", any(cve in cve_ids_of.get(v, set()) for v in vs), (pkg, cve))
    elif st == "cve_elsewhere":
        check(cid, "stratum", cve in all_cve_ids
              and not any(cve in cve_ids_of.get(v, set()) for v in vs), (pkg, cve))
    elif st == "near_miss_cve":
        check(cid, "stratum", pkg in pkg_names and cve not in all_cve_ids, (pkg, cve))
    elif st in ("new_extra_cve", "new_extra_software", "new_extra_indirect_vuln",
                "old_is_leaf"):
        check(cid, "stratum", bool(exp), "expected nonempty")
        if st == "old_is_leaf":
            check(cid, "old-really-leaf", not succs[node_of[(pkg, ver)]], "old has deps")
    elif st in ("same_cves", "same_software", "no_new_indirect_vuln"):
        check(cid, "stratum", exp == [], "expected empty")
    elif st == "new_is_leaf":
        nnode = node_of.get((pkg, ver2))
        check(cid, "stratum", nnode is not None and not succs[nnode], "new has deps")
        check(cid, "empty-attributable", exp == [], "expected empty")
    else:
        check(cid, "known-stratum", False, st)

# ---- pair-template sampling health (C5.5 lesson) --------------------------
pair_health = {}
for tid in ("C9.5", "C9.6", "C9.7"):
    rows = [c for c in c9 if c["template_id"] == tid]
    first = {(c["params"]["pkg"], c["params"]["ver"]) for c in rows}
    ne = [c for c in rows if c["expected_result"]]
    answers = {json.dumps(c["expected_result"], sort_keys=True) for c in ne}
    pair_health[tid] = {"n": len(rows), "distinct_first_sides": len(first),
                        "nonempty": len(ne), "distinct_nonempty_answers": len(answers)}

# ---- sheet C9.6 replay: price the unguarded gold --------------------------
SHEET_C96 = (
    "MATCH (s:Software {name: $pkg})-[:HAS_VERSION]->(old:SoftwareVersion {versionName: $ver}) "
    "OPTIONAL MATCH (old)-[:DEPENDS_ON*1..6]->(:SoftwareVersion)<-[:HAS_VERSION]-(os:Software) "
    "WITH s, collect(DISTINCT os.name) AS old_names "
    "MATCH (s)-[:HAS_VERSION]->(new:SoftwareVersion {versionName: $ver2}) "
    "MATCH (new)-[:DEPENDS_ON*1..6]->(:SoftwareVersion)<-[:HAS_VERSION]-(ns:Software) "
    "WHERE NOT ns.name IN old_names "
    "RETURN DISTINCT ns.name AS software ORDER BY software"
)
c96 = [c for c in c9 if c["template_id"] == "C9.6"]
sheet_wrong, sheet_sample = 0, []
for c in c96:
    got = q(SHEET_C96, **{k: c["params"][k] for k in ("pkg", "ver", "ver2")})
    if norm(got) != norm(c["expected_result"]):
        sheet_wrong += 1
        if len(sheet_sample) < 8:
            extra = [r["software"] for r in got
                     if r["software"] not in {x["software"] for x in c["expected_result"]}]
            sheet_sample.append({"id": c["id"], "sheet_n": len(got),
                                 "truth_n": len(c["expected_result"]),
                                 "spurious": extra[:3]})

# ---- gold replay: C9 twice, C1-C8 once ------------------------------------
times = []
for c in c9:
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
    "n": len(c9),
    "regression_n": len(rest),
    "regression_failures": reg_fail,
    "cycle_guard_changed_answers": dict(guard_changed),
    "pairs_in_graph": dict(in_graph),
    "sheet_c96_cases_wrong": sheet_wrong,
    "sheet_c96_of": len(c96),
    "sheet_c96_sample": sheet_sample,
    "pair_sampling_health": pair_health,
    "failures": fails,
    "gold_ms": {"median": round(statistics.median(times), 1),
                "max": round(max(times), 1)},
}
out = T2C / "verify_c9_report.json"
out.write_text(json.dumps(report, indent=1), encoding="utf-8")
print(json.dumps({k: v for k, v in report.items()
                  if k not in ("failures", "sheet_c96_sample")}, indent=1))
print(f"failures: {len(fails)}")
for f in fails[:20]:
    print(" ", f)
print("wrote", out)
ses.close(); drv.close()
