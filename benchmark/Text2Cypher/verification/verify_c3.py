"""Independent verification of the C3 cases in the v4 bank.

Strongest possible independence: the whole graph (HAS_VERSION + DEPENDS_ON
edges) is pulled once and every C3 answer is recomputed IN PYTHON - BFS for
reachability and distances, DFS with edge-uniqueness for walk depth, layered
BFS for shortest-path counting. No Cypher semantics are shared with the gold.

Also replays every gold twice against Neo4j (determinism + storage match).
"""
import json, os, sys, time, collections, statistics
from pathlib import Path
from dotenv import load_dotenv
from neo4j import GraphDatabase

ROOT = Path(r"C:/Master Thesis/DepsRagBenchmark")
load_dotenv(ROOT / ".env")
DS = ROOT / "benchmark/Text2Cypher/t2c_purdue_dataset_v4.json"
cases = [c for c in json.loads(DS.read_text(encoding="utf-8")) if c["template_id"].startswith("C3")]
print(f"{len(cases)} C3 cases")

drv = GraphDatabase.driver(os.environ["NEO4J_URI"],
    auth=(os.environ["NEO4J_USERNAME"], os.environ["NEO4J_PASSWORD"]))
ses = drv.session(database=os.environ.get("NEO4J_DATABASE", "neo4j"))
def q(cy, **params):
    with ses.begin_transaction(timeout=60.0) as tx:
        return [dict(r) for r in tx.run(cy, **params)]

# ---- pull the graph into Python -------------------------------------------
t0 = time.time()
ver_rows = q("MATCH (sw:Software)-[:HAS_VERSION]->(v:SoftwareVersion) "
             "RETURN sw.name AS pkg, v.versionName AS ver, elementId(v) AS vid")
dep_rows = q("MATCH (a:SoftwareVersion)-[:DEPENDS_ON]->(b:SoftwareVersion) "
             "RETURN elementId(a) AS a, elementId(b) AS b")
vname = {}          # vid -> versionName
owner = {}          # vid -> set of software names (collisions possible)
node_of = {}        # (pkg, ver) -> vid  (assume unique per pair)
vids_of_pkg = collections.defaultdict(set)
for r in ver_rows:
    vname[r["vid"]] = r["ver"]
    owner.setdefault(r["vid"], set()).add(r["pkg"])
    node_of[(r["pkg"], r["ver"])] = r["vid"]
    vids_of_pkg[r["pkg"]].add(r["vid"])
succ = collections.defaultdict(list)
for r in dep_rows:
    succ[r["a"]].append(r["b"])
pkg_names = set(vids_of_pkg)
print(f"graph pulled: {len(vname)} versions, {sum(len(v) for v in succ.values())} edges ({time.time()-t0:.1f}s)")

def bfs_layers(src, maxd=6):
    """dist map for nodes reachable within maxd (shortest distance)."""
    dist = {src: 0}
    frontier = [src]
    d = 0
    while frontier and d < maxd:
        d += 1
        nxt = []
        for u in frontier:
            for w in succ.get(u, ()):
                if w not in dist:
                    dist[w] = d
                    nxt.append(w)
        frontier = nxt
    dist.pop(src, None) if False else None
    return dist

def reach_within(src, maxd=6):
    """set of nodes with a WALK of length <= maxd (== BFS distance <= maxd)."""
    return {n for n, d in bfs_layers(src, maxd).items() if n != src or d > 0}

def max_walk_depth(src, cap=6):
    """max length over relationship-unique walks, capped. DFS; only called on
    shallow roots so the state space is tiny."""
    best = 0
    used = set()
    def dfs(u, depth):
        nonlocal best
        best = max(best, depth)
        if depth >= cap:
            return
        for i, w in enumerate(succ.get(u, ())):
            e = (u, w, i)
            if e in used:
                continue
            used.add(e)
            dfs(w, depth + 1)
            used.remove(e)
    dfs(src, 0)
    return best

def count_shortest_paths(src, dst, maxd=6):
    """(#shortest paths, dist, the unique path as vids if count==1 else None)"""
    dist = {src: 0}
    cnt = collections.Counter({src: 1})
    parent = collections.defaultdict(list)
    frontier = [src]
    d = 0
    while frontier and d < maxd and dst not in dist:
        d += 1
        nxt = []
        for u in frontier:
            for w in succ.get(u, ()):
                if w not in dist:
                    dist[w] = d
                    nxt.append(w)
                if dist[w] == d and dist[u] == d - 1:
                    cnt[w] += cnt[u]
                    parent[w].append(u)
        frontier = nxt
    if dst not in dist:
        return 0, None, None
    if cnt[dst] != 1:
        return cnt[dst], dist[dst], None
    path = [dst]
    while path[-1] != src:
        path.append(parent[path[-1]][0])
    return 1, dist[dst], list(reversed(path))

fails = []
times = []
by_tpl = collections.defaultdict(list)

for i, c in enumerate(cases):
    cid, tid, st, p = c["id"], c["template_id"], c["stratum"], c["params"]
    by_tpl[tid].append(c)
    exp = c["expected_result"]

    # gold replay x2 against Neo4j
    t1 = time.time()
    r1 = q(c["cypher_query"]); r2 = q(c["cypher_query"])
    times.append(time.time() - t1)
    if r1 != exp: fails.append((cid, "gold-mismatch", f"stored {len(exp)} vs live {len(r1)}"))
    if r1 != r2: fails.append((cid, "nondeterministic", ""))

    # Python recomputation
    root = node_of.get((p["pkg"], p["ver"]))
    if st in ("absent_package",):
        if p["pkg"] in pkg_names: fails.append((cid, "stratum", "absent pkg exists"))
        continue
    if st == "absent_dep":
        if p["dep"] in pkg_names: fails.append((cid, "stratum", "absent dep exists"))
        if root is None: fails.append((cid, "stratum", "root missing for absent_dep"))
        continue
    if root is None:
        if st != "absent_package":
            fails.append((cid, "stratum", "root not in graph"))
        continue

    if tid in ("C3.1", "C3.4", "C3.6"):
        dist = bfs_layers(root, 6)
        if tid == "C3.1":
            want_nodes = [n for n, d in dist.items() if d >= 1]
        elif tid == "C3.4":
            direct = set(succ.get(root, ()))
            within2 = bfs_layers(root, 2)
            want_nodes = [n for n, d in within2.items() if d >= 1 and n not in direct]
            # distance-2 nodes are exactly BFS layer 2; layer-1 nodes not direct impossible
        else:  # C3.6
            direct = set(succ.get(root, ()))
            want_nodes = [n for n, d in dist.items() if d >= 1 and n not in direct]
        want = set()
        for n in want_nodes:
            for own in (owner.get(n) or {None}):
                want.add((own, vname[n]))
        got = {(r["software"], r["version"]) for r in exp}
        if want != got:
            fails.append((cid, "python-recompute", f"{tid} rows {len(got)} vs bfs {len(want)}"))
    elif tid == "C3.2":
        targets = vids_of_pkg.get(p["dep"], set())
        reach = reach_within(root, 6)
        truth = bool(targets & reach)
        if not exp or exp[0]["depends"] is not truth:
            fails.append((cid, "python-recompute", f"depends should be {truth}"))
        if st == "indirect_true" and (set(succ.get(root, ())) & targets):
            fails.append((cid, "stratum", "indirect_true pair is actually direct"))
        if st == "reverse_only":
            back = any(root in reach_within(t, 6) for t in targets)
            if not back: fails.append((cid, "stratum", "reverse_only: dep does not reach root"))
            if truth: fails.append((cid, "stratum", "reverse_only pair is forward-reachable"))
    elif tid == "C3.3":
        targets = sorted(vids_of_pkg.get(p["dep"], set()))
        reachable_targets = [t for t in targets if t in reach_within(root, 6)]
        if st in ("no_path", "absent_dep"):
            if reachable_targets: fails.append((cid, "stratum", "no_path but reachable"))
            if exp: fails.append((cid, "python-recompute", "expected nonempty for no_path"))
        else:
            if len(reachable_targets) != 1:
                fails.append((cid, "stratum", f"unique_path but {len(reachable_targets)} reachable targets"))
                continue
            n, d, path = count_shortest_paths(root, reachable_targets[0], 6)
            if n != 1:
                fails.append((cid, "stratum", f"{n} shortest paths, not unique"))
                continue
            want_path = [vname[x] for x in path]
            got_path = exp[0]["path"] if exp else None
            if got_path != want_path:
                fails.append((cid, "python-recompute", f"path {got_path} vs {want_path}"))
            if st == "unique_path_2plus" and d < 2:
                fails.append((cid, "stratum", "2plus but distance 1"))
            if st == "unique_path_direct" and d != 1:
                fails.append((cid, "stratum", f"direct but distance {d}"))
    elif tid == "C3.5":
        depth = max_walk_depth(root, 6)
        # verify the root really is shallow: no 7-edge walk
        d7 = max_walk_depth(root, 7)
        if d7 >= 7:
            fails.append((cid, "stratum", "root escapes 6 hops - capped gold would lie"))
        if not exp or exp[0]["max_depth"] != depth:
            fails.append((cid, "python-recompute", f"max_depth {exp} vs python {depth}"))
        if st == "leaf_version" and depth != 0:
            fails.append((cid, "stratum", "leaf has depth > 0"))
        if st == "depth_exactly1" and depth != 1:
            fails.append((cid, "stratum", f"depth_exactly1 but {depth}"))
        if st == "depth_ge2" and depth < 2:
            fails.append((cid, "stratum", f"depth_ge2 but {depth}"))

    if (i + 1) % 300 == 0:
        print(f"  ...{i+1}/{len(cases)}, {len(fails)} failures", flush=True)

print(f"\nRESULT: {len(cases)} cases, {len(fails)} failures")
for f in fails[:25]:
    print("  FAIL", f)
out = {
    "verified_at": time.strftime("%Y-%m-%d %H:%M"),
    "n_cases": len(cases), "n_failures": len(fails),
    "failures": [{"id": a, "check": b, "detail": d} for a, b, d in fails],
    "gold_ms": {"median": round(statistics.median(times)*1000, 1),
                "p95": round(sorted(times)[int(len(times)*0.95)]*1000, 1),
                "max": round(max(times)*1000, 1)},
}
for tid, group in sorted(by_tpl.items()):
    empty = sum(1 for c in group if not c["expected_result"])
    out.setdefault("templates", {})[tid] = {
        "n": len(group), "empty": empty,
        "strata": dict(collections.Counter(c["stratum"] for c in group)),
        "distinct": len({json.dumps(c["expected_result"], sort_keys=True) for c in group}),
    }
(ROOT / "benchmark/Text2Cypher/verify_c3_report.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
print(json.dumps(out["gold_ms"]))
print("report -> verify_c3_report.json")
ses.close(); drv.close()
