"""Independent verification of the C4.3 / C4.4 cases, plus the *0..6 measurement.

Same house method as C3: pull the raw edge list and recompute every answer in
Python, sharing no Cypher with the gold. Then quantify the thing that is being
held for discussion rather than patched - how far the sheet's `*0..6` gold sits
from the `*1..6` bound that schema instruction 4 hands every model.
"""
import json, os, sys, time, collections, statistics
from pathlib import Path
from dotenv import load_dotenv
from neo4j import GraphDatabase

ROOT = Path(r"C:/Master Thesis/DepsRagBenchmark")
T2C = ROOT / "benchmark/Text2Cypher"
sys.path.insert(0, str(T2C))
load_dotenv(ROOT / ".env")
from t2c_templates_v4 import TEMPLATES  # noqa: E402

cases = json.loads((T2C / "t2c_purdue_dataset_v4.json").read_text(encoding="utf-8"))
c4 = [c for c in cases if c["template_id"].startswith("C4")]
print(f"{len(c4)} C4 cases")

drv = GraphDatabase.driver(os.environ["NEO4J_URI"],
    auth=(os.environ["NEO4J_USERNAME"], os.environ["NEO4J_PASSWORD"]))
ses = drv.session(database=os.environ.get("NEO4J_DATABASE", "neo4j"))
def q(cy, timeout=120.0, **p):
    with ses.begin_transaction(timeout=timeout) as tx:
        return [dict(r) for r in tx.run(cy, **p)]

# ---- graph into Python ----------------------------------------------------
vr = q("MATCH (sw:Software)-[:HAS_VERSION]->(v:SoftwareVersion) "
       "RETURN sw.name AS pkg, v.versionName AS ver, elementId(v) AS vid, elementId(sw) AS sid")
dr = q("MATCH (a:SoftwareVersion)-[:DEPENDS_ON]->(b:SoftwareVersion) "
       "RETURN elementId(a) AS a, elementId(b) AS b")
succ = collections.defaultdict(list)
for r in dr:
    succ[r["a"]].append(r["b"])
owners = collections.defaultdict(set)   # vid -> {sid}
node_of = {}
pkg_names = set()
for r in vr:
    owners[r["vid"]].add(r["sid"])
    node_of[(r["pkg"], r["ver"])] = r["vid"]
    pkg_names.add(r["pkg"])
print(f"graph: {len(node_of)} versions, {sum(len(v) for v in succ.values())} edges")

def reach(src, maxd=6):
    """nodes with BFS distance 1..maxd (root excluded)."""
    seen, frontier, d = {src}, [src], 0
    while frontier and d < maxd:
        d += 1
        nxt = []
        for u in frontier:
            for w in succ.get(u, ()):
                if w not in seen:
                    seen.add(w); nxt.append(w)
        frontier = nxt
    return seen - {src}

def c43(src, include_root):
    nodes = reach(src, 6) | ({src} if include_root else set())
    out = set()
    for n in nodes:
        out |= owners.get(n, set())
    return len(out)

def c44(src, include_root):
    nodes = reach(src, 6) | ({src} if include_root else set())
    return sum(1 for n in nodes if not succ.get(n))

fails = []
times = []
divergence = collections.defaultdict(lambda: {"differ": 0, "same": 0, "examples": []})

for i, c in enumerate(c4):
    cid, tid, st, p = c["id"], c["template_id"], c["stratum"], c["params"]
    tpl = TEMPLATES[tid]
    exp = c["expected_result"]

    # structural
    if c["answer_shape"] != tpl["answer_shape"]["kind"] or len(exp) != 1 \
       or list(exp[0].keys()) != tpl["answer_shape"]["columns"]:
        fails.append((cid, "shape", json.dumps(exp)[:60]))
    if "{" in c["question"]:
        fails.append((cid, "placeholder", ""))

    # gold replay x2
    t1 = time.time()
    r1 = q(c["cypher_query"]); r2 = q(c["cypher_query"])
    times.append(time.time() - t1)
    if r1 != exp: fails.append((cid, "gold-mismatch", f"stored {exp} live {r1}"))
    if r1 != r2:  fails.append((cid, "nondeterministic", ""))

    got = exp[0]["cnt"] if exp else None
    src = node_of.get((p["pkg"], p["ver"]))

    if st == "absent_package":
        if p["pkg"] in pkg_names:
            fails.append((cid, "stratum", "absent pkg exists"))
        if got != 0:
            fails.append((cid, "python-recompute", f"absent should be 0, got {got}"))
        continue
    if src is None:
        fails.append((cid, "stratum", "root not in graph")); continue

    # stratum semantics, independent of the gold
    has_direct = bool(succ.get(src))
    has_2hop = any(succ.get(w) for w in succ.get(src, ()))
    if st == "leaf_version" and has_direct:
        fails.append((cid, "stratum", "leaf_version root has dependencies"))
    if st == "direct_only" and (not has_direct or has_2hop):
        fails.append((cid, "stratum", f"direct_only: direct={has_direct} twohop={has_2hop}"))
    if st == "has_indirect" and not has_2hop:
        fails.append((cid, "stratum", "has_indirect root has no 2-hop dependency"))

    # the answer itself, recomputed with the gold's *0..6 reading
    want = c43(src, True) if tid == "C4.3" else c44(src, True)
    if got != want:
        fails.append((cid, "python-recompute", f"{tid} gold {got} vs python {want}"))

    # ... and with the *1..6 reading schema instruction 4 hands the model
    alt = c43(src, False) if tid == "C4.3" else c44(src, False)
    key = (tid, st)
    if alt != want:
        divergence[key]["differ"] += 1
        if len(divergence[key]["examples"]) < 2:
            divergence[key]["examples"].append(f"{cid}: *0..6={want} vs *1..6={alt}")
    else:
        divergence[key]["same"] += 1

    if (i + 1) % 150 == 0:
        print(f"  ...{i+1}/{len(c4)}, {len(fails)} failures", flush=True)

print(f"\nRESULT: {len(c4)} cases, {len(fails)} failures")
for f in fails[:20]:
    print("  FAIL", f)

print("\n--- `*0..6` (sheet gold) vs `*1..6` (schema instruction 4) ---")
tot = collections.defaultdict(lambda: [0, 0])
for (tid, st), v in sorted(divergence.items()):
    tot[tid][0] += v["differ"]; tot[tid][1] += v["same"]
    print(f"  {tid} {st:16s} differ {v['differ']:3d} / same {v['same']:3d}"
          + ("   e.g. " + v["examples"][0] if v["examples"] else ""))
for tid, (d, s_) in sorted(tot.items()):
    n = d + s_
    print(f"  => {tid}: an *1..6 model gets a DIFFERENT number on {d}/{n} real-root cases ({d/n*100:.0f}%)")
    print(f"     exact-match score ceiling for such a model on this template: "
          f"{(n - d + 30) / (n + 30):.3f}  (30 absent-package cases answer 0 either way)")

json.dump({"n": len(c4), "failures": [{"id": a, "check": b, "detail": c_} for a, b, c_ in fails],
           "gold_ms": {"median": round(statistics.median(times) * 1000, 1),
                       "max": round(max(times) * 1000, 1)},
           "divergence": {f"{k[0]}|{k[1]}": v for k, v in divergence.items()}},
          open(str(T2C / "verify_c4_report.json"), "w"), indent=1)
print("\nreport -> verify_c4_report.json")
ses.close(); drv.close()
