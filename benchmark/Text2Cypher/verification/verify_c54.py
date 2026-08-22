"""Independent verification of C5.4, plus a re-measurement of what the sheet's
original gold would have produced on the very cases now shipped.
"""
import json, os, sys, time, collections, statistics
from pathlib import Path
from dotenv import load_dotenv
from neo4j import GraphDatabase

ROOT = Path(r"C:/Master Thesis/DepsRagBenchmark")
T2C = ROOT / "benchmark/Text2Cypher"
load_dotenv(ROOT / ".env")

cases = [c for c in json.loads((T2C / "t2c_purdue_dataset_v4.json").read_text(encoding="utf-8"))
         if c["template_id"] == "C5.4"]
print(f"{len(cases)} C5.4 cases")

drv = GraphDatabase.driver(os.environ["NEO4J_URI"],
    auth=(os.environ["NEO4J_USERNAME"], os.environ["NEO4J_PASSWORD"]),
    warn_notification_severity="OFF")
ses = drv.session(database=os.environ.get("NEO4J_DATABASE", "neo4j"))
def q(cy, timeout=180.0, **p):
    with ses.begin_transaction(timeout=timeout) as tx:
        return [dict(r) for r in tx.run(cy, **p)]

vr = q("MATCH (sw:Software)-[:HAS_VERSION]->(v:SoftwareVersion) "
       "RETURN sw.name AS pkg, v.versionName AS ver, elementId(v) AS vid")
dr = q("MATCH (a:SoftwareVersion)-[:DEPENDS_ON]->(b:SoftwareVersion) "
       "RETURN elementId(a) AS a, elementId(b) AS b")
succ = collections.defaultdict(set)
for r in dr: succ[r["a"]].add(r["b"])
vname, owners, node_of = {}, collections.defaultdict(set), {}
for r in vr:
    vname[r["vid"]] = r["ver"]; owners[r["vid"]].add(r["pkg"]); node_of[(r["pkg"], r["ver"])] = r["vid"]

def reach6(s):
    seen, fr, d = {s}, [s], 0
    while fr and d < 6:
        d += 1; nx = []
        for u in fr:
            for w in succ.get(u, ()):
                if w not in seen: seen.add(w); nx.append(w)
        fr = nx
    return seen - {s}

# the sheet's original gold, for the record
SHEET = ("MATCH (s1:Software {name:$pkg})-[:HAS_VERSION]->(r1:SoftwareVersion {versionName:$ver}) "
         "MATCH (s2:Software {name:$dep})-[:HAS_VERSION]->(r2:SoftwareVersion {versionName:$dep_ver}) "
         "MATCH (r1)-[:DEPENDS_ON*1..6]->(d:SoftwareVersion)<-[:DEPENDS_ON*1..6]-(r2) "
         "OPTIONAL MATCH (ds:Software)-[:HAS_VERSION]->(d) "
         "RETURN DISTINCT ds.name AS software, d.versionName AS version ORDER BY software, version")

fails, times, sizes = [], [], []
sheet_delta, sheet_checked, sheet_timeouts = [], 0, 0
rng_every = max(1, len(cases) // 25)

for i, c in enumerate(cases):
    cid, st, p = c["id"], c["stratum"], c["params"]
    exp = c["expected_result"]

    t1 = time.time(); r1 = q(c["cypher_query"]); r2 = q(c["cypher_query"])
    times.append(time.time() - t1)
    if r1 != exp: fails.append((cid, "gold-mismatch", f"stored {len(exp)} live {len(r1)}"))
    if r1 != r2:  fails.append((cid, "nondeterministic", ""))
    keys = [json.dumps(r, sort_keys=True) for r in exp]
    if len(keys) != len(set(keys)): fails.append((cid, "duplicate-rows", str(len(keys))))

    a = node_of.get((p["pkg"], p["ver"])); b = node_of.get((p["dep"], p["dep_ver"]))
    if a is None or b is None:
        fails.append((cid, "binding", "version not in graph")); continue

    ta, tb = reach6(a), reach6(b)
    want = set()
    for n in (ta & tb):
        for o in (owners.get(n) or {None}):
            want.add((o, vname[n]))
    got = {(r["software"], r["version"]) for r in exp}
    if want != got:
        fails.append((cid, "recompute", f"gold {len(got)} vs python {len(want)}"))
    if exp: sizes.append(len(exp))

    if st == "shares_tree" and not want: fails.append((cid, "stratum", "trees do not intersect"))
    if st == "disjoint_trees" and (want or not succ.get(a) or not succ.get(b)):
        fails.append((cid, "stratum", "not a both-have-deps disjoint pair"))
    if st == "first_is_leaf" and succ.get(a): fails.append((cid, "stratum", "first side has deps"))

    # every ~10th non-empty case: what would the sheet's gold have said?
    if st == "shares_tree" and i % rng_every == 0:
        try:
            n_sheet = len(q(SHEET, timeout=60.0, **p))
            sheet_checked += 1
            sheet_delta.append((len(exp) - n_sheet, len(exp), n_sheet, cid))
        except Exception:
            sheet_timeouts += 1

    if (i + 1) % 100 == 0:
        print(f"  ...{i+1}/{len(cases)}, {len(fails)} failures", flush=True)

print(f"\nRESULT: {len(cases)} cases, {len(fails)} failures")
for f in fails[:15]: print("  FAIL", f)
sizes.sort()
print(f"answer sizes (non-empty): min {sizes[0]} / median {sizes[len(sizes)//2]} / max {sizes[-1]}")
print(f"gold cost: median {statistics.median(times)*1000:.0f} ms, max {max(times)*1000:.0f} ms")

if sheet_delta:
    missed = [d for d in sheet_delta if d[0] > 0]
    print(f"\n--- what the sheet's original gold would have returned "
          f"({sheet_checked} sampled shares_tree cases, {sheet_timeouts} timed out) ---")
    print(f"  {len(missed)} of {sheet_checked} would have LOST rows")
    if missed:
        tot_true = sum(d[1] for d in sheet_delta); tot_sheet = sum(d[2] for d in sheet_delta)
        print(f"  rows returned in total: sheet {tot_sheet} vs truth {tot_true} "
              f"({tot_sheet/tot_true*100:.0f}% of the answer)")
        missed.sort(reverse=True)
        for d, tru, sh, cid in missed[:5]:
            print(f"    {cid}: sheet {sh} vs truth {tru}  (missing {d})")

out = {"n": len(cases), "failures": [{"id": a_, "check": b_, "detail": d} for a_, b_, d in fails],
       "answer_sizes": {"min": sizes[0], "median": sizes[len(sizes)//2], "max": sizes[-1]},
       "sheet_gold_sample": [{"id": c_, "sheet": s_, "truth": t_} for _, t_, s_, c_ in sheet_delta]}
(T2C / "verify_c54_report.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
print("-> verify_c54_report.json")
ses.close(); drv.close()
