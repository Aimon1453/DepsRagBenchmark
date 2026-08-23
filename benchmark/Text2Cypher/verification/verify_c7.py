"""Independent verification of the C7 cases: recompute every answer in Python
with reverse BFS over the raw edge list, sharing no Cypher with the golds.

Beyond the house method, this one also quantifies the two C7-specific facts:
how many C7.5 answers the `other <> root` cycle guard actually changed (the
justification for the deviation), and how many C7.5 answers are 6-hop
truncations of the true reverse closure (the "at any depth" wording defect,
same class as C3.1/C3.6). Ends with a full C1-C6 regression replay because
the C7 build rewrote the shared dataset file.
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
c7 = [c for c in cases if c["template_id"].startswith("C7")]
rest = [c for c in cases if not c["template_id"].startswith("C7")]
print(f"{len(c7)} C7 cases, {len(rest)} other cases for regression replay")

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
preds = collections.defaultdict(set)
succs = collections.defaultdict(set)
for r in dr:
    preds[r["b"]].add(r["a"]); succs[r["a"]].add(r["b"])
owners = collections.defaultdict(set)
vname, node_of, pkg_names = {}, {}, set()
prods_of_pkg = collections.defaultdict(set)   # pkg -> version node ids
for r in vr:
    vname[r["vid"]] = r["ver"]; owners[r["vid"]].add(r["pkg"])
    node_of[(r["pkg"], r["ver"])] = r["vid"]; pkg_names.add(r["pkg"])
    prods_of_pkg[r["pkg"]].add(r["vid"])
print(f"graph: {len(vname)} versions, {len(dr)} dep edges")

def rev_bfs(start, max_depth=None):
    """Reverse reachability from `start` (excluding it), depth-limited."""
    seen, frontier, depth = set(), {start}, 0
    while frontier and (max_depth is None or depth < max_depth):
        depth += 1
        frontier = {p for n in frontier for p in preds[n]} - seen
        seen |= frontier
    return seen - {start}

def rows_of(nodes):
    out = set()
    for n in nodes:
        for o in (owners.get(n) or {None}):
            out.add((o, vname[n]))
    return out

def norm(rows):
    return sorted(tuple(sorted(str(v) for v in r.values())) for r in rows)

def norm_pairs(pairs):
    return sorted(tuple(sorted((str(a), str(b)))) for a, b in pairs)

fails = []
def check(cid, label, ok, detail=""):
    if not ok:
        fails.append({"id": cid, "check": label, "detail": str(detail)[:300]})

guard_changed = 0          # C7.5 answers where the cycle guard removed root
truncated = 0              # C7.5 answers that lose nodes past 6 hops
trunc_examples = []

for c in c7:
    cid, tid, st, p = c["id"], c["template_id"], c["stratum"], c["params"]
    exp = c["expected_result"]
    pkg, ver, dep = p.get("pkg"), p.get("ver"), p.get("dep")

    # ---- recompute the answer via reverse BFS ----
    if tid == "C7.1":
        n = node_of.get((pkg, ver))
        truth = rows_of({o for o in preds[n]} if n else set()) if n else set()
    elif tid == "C7.2":
        truth = set()
        for tgt in prods_of_pkg.get(dep, set()):
            truth |= rows_of(preds[tgt])
    elif tid == "C7.5":
        n = node_of.get((pkg, ver))
        truth = rows_of(rev_bfs(n, 6)) if n else set()
    else:
        check(cid, "known-template", False, tid); continue

    check(cid, "python-recompute",
          norm_pairs(truth) == norm(exp),
          f"truth_n={len(truth)} exp_n={len(exp)}")

    # ---- C7.5 extras: guard impact and truncation ----
    if tid == "C7.5" and (pkg, ver) in node_of:
        n = node_of[(pkg, ver)]
        depth = 0; frontier = {n}; seen = set()
        while frontier and depth < 6:
            depth += 1
            frontier = {x for f in frontier for x in preds[f]} - seen
            seen |= frontier
        if n in seen:
            guard_changed += 1
        full = rev_bfs(n, None)
        capped = rev_bfs(n, 6)
        if full - capped:
            truncated += 1
            if len(trunc_examples) < 5:
                trunc_examples.append({"id": cid, "within6": len(capped),
                                       "beyond6": len(full - capped)})

    # ---- stratum claims, independent logic ----
    if st == "has_dependents" and tid == "C7.1":
        n = node_of.get((pkg, ver))
        check(cid, "params-in-graph", n is not None, (pkg, ver))
        check(cid, "stratum", n is not None and bool(preds[n] - {n}), "expected a non-self dependent")
        check(cid, "no-self-loop-binding", n is not None and n not in preds[n], "self-loop version bound")
    elif st == "has_dependents":                     # C7.2
        check(cid, "params-in-graph", dep in pkg_names, dep)
        check(cid, "stratum", any(preds[t] for t in prods_of_pkg.get(dep, set())),
              "expected some version with a dependent")
    elif st == "no_dependents":
        n = node_of.get((pkg, ver))
        check(cid, "params-in-graph", n is not None, (pkg, ver))
        check(cid, "stratum", n is not None and not preds[n], "expected no dependents")
    elif st == "no_version_depended_on":
        check(cid, "params-in-graph", dep in pkg_names, dep)
        check(cid, "stratum", not any(preds[t] for t in prods_of_pkg.get(dep, set())),
              "expected no version with a dependent")
    elif st == "absent_package":
        check(cid, "stratum", pkg not in pkg_names, pkg)
    elif st == "absent_product":
        check(cid, "stratum", dep not in pkg_names, dep)
    elif st == "has_indirect_dependents":
        n = node_of.get((pkg, ver))
        two = {x for f in preds[n] for x in preds[f]} - {n} if n else set()
        check(cid, "stratum", bool(two), "expected a non-self 2-hop dependent")
    elif st == "direct_only_dependents":
        n = node_of.get((pkg, ver))
        two = {x for f in preds[n] for x in preds[f]} - {n} if n else set()
        check(cid, "stratum", n is not None and bool(preds[n] - {n}) and not two,
              "expected direct-only dependents")
        # the sharper claim: every guarded reverse-reachable node is at depth 1
        check(cid, "reverse-closure-is-direct",
              rev_bfs(n, 6) == (preds[n] - {n}), "deeper dependents exist")
    else:
        check(cid, "known-stratum", False, st)

# ---- gold replay: C7 twice, C1-C6 once ------------------------------------
times = []
for c in c7:
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

n_c75 = sum(1 for c in c7 if c["template_id"] == "C7.5" and c["params"].get("pkg") in pkg_names
            and (c["params"]["pkg"], c["params"]["ver"]) in node_of)
report = {
    "n": len(c7),
    "regression_n": len(rest),
    "regression_failures": reg_fail,
    "c75_guard_removed_root": guard_changed,
    "c75_in_graph": n_c75,
    "c75_truncated_answers": truncated,
    "c75_truncation_examples": trunc_examples,
    "failures": fails,
    "gold_ms": {"median": round(statistics.median(times), 1),
                "max": round(max(times), 1)},
}
out = T2C / "verify_c7_report.json"
out.write_text(json.dumps(report, indent=1), encoding="utf-8")
print(json.dumps({k: v for k, v in report.items() if k != "failures"}, indent=1))
print(f"failures: {len(fails)}")
for f in fails[:20]:
    print(" ", f)
print("wrote", out)
ses.close(); drv.close()
