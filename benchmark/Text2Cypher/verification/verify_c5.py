"""Independent verification of the C5 cases: recompute every answer in Python
from the raw edge lists, sharing no Cypher with the golds.
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
c5 = [c for c in cases if c["template_id"].startswith("C5")]
print(f"{len(c5)} C5 cases")

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
cve = q("MATCH (v:SoftwareVersion)-[:VULNERABLE_TO]->(c:Vulnerability) "
        "RETURN elementId(v) AS v, elementId(c) AS c")
cwe = q("MATCH (v:SoftwareVersion)-[:VULNERABLE_TO]->(:Vulnerability)"
        "-[:VULNERABILITY_TYPE]->(w:VulnerabilityType) "
        "RETURN DISTINCT elementId(v) AS v, w.cweId AS cweId")
succ = collections.defaultdict(set)
for r in dr: succ[r["a"]].add(r["b"])
cves = collections.defaultdict(list)          # multiset: gold uses count(), not count(DISTINCT)
for r in cve: cves[r["v"]].append(r["c"])
cwes = collections.defaultdict(set)
for r in cwe: cwes[r["v"]].add(r["cweId"])
vname, owners, node_of, pkgs = {}, collections.defaultdict(set), {}, set()
for r in vr:
    vname[r["vid"]] = r["ver"]; owners[r["vid"]].add(r["pkg"])
    node_of[(r["pkg"], r["ver"])] = r["vid"]; pkgs.add(r["pkg"])
print(f"graph: {len(vname)} versions, {sum(len(v) for v in succ.values())} dep edges, "
      f"{len(cves)} vulnerable versions, {len(cwes)} versions with a CWE")

def rows_of(nodes):
    out = set()
    for n in nodes:
        for o in (owners.get(n) or {None}):
            out.add((o, vname[n]))
    return out

fails, times = [], []
for i, c in enumerate(c5):
    cid, tid, st, p = c["id"], c["template_id"], c["stratum"], c["params"]
    tpl, shape, exp = TEMPLATES[tid], TEMPLATES[c["template_id"]]["answer_shape"], c["expected_result"]

    # structural
    if c["answer_shape"] != shape["kind"]:
        fails.append((cid, "shape-label", c["answer_shape"]))
    if exp and list(exp[0].keys()) != shape["columns"]:
        fails.append((cid, "shape-columns", str(list(exp[0].keys()))))
    if shape.get("ordered_columns") and not c.get("ordered_columns"):
        fails.append((cid, "ordered_columns", "template declares it, case does not carry it"))
    if "{" in c["question"]:
        fails.append((cid, "placeholder", ""))

    # gold replay x2
    t1 = time.time(); r1 = q(c["cypher_query"]); r2 = q(c["cypher_query"])
    times.append(time.time() - t1)
    if r1 != exp: fails.append((cid, "gold-mismatch", f"stored {len(exp)} live {len(r1)}"))
    if r1 != r2:  fails.append((cid, "nondeterministic", ""))

    a = node_of.get((p["pkg"], p["ver"]))
    b = node_of.get((p["dep"], p["dep_ver"]))
    if a is None or b is None:
        fails.append((cid, "binding", "a version in the pair is not in the graph")); continue

    if tid == "C5.1":
        want = rows_of(succ.get(a, set()) & succ.get(b, set()))
        got = {(r["software"], r["version"]) for r in exp}
        if want != got: fails.append((cid, "recompute", f"gold {len(got)} vs python {len(want)}"))
        if st == "shares_direct" and not want: fails.append((cid, "stratum", "no shared direct dep"))
        if st == "no_shared_direct" and (want or not succ.get(a) or not succ.get(b)):
            fails.append((cid, "stratum", "not a both-have-deps disjoint pair"))
        if st == "first_is_leaf" and succ.get(a): fails.append((cid, "stratum", "first side has deps"))
    elif tid == "C5.5":
        want = rows_of(succ.get(a, set()) - succ.get(b, set()))
        got = {(r["software"], r["version"]) for r in exp}
        if want != got: fails.append((cid, "recompute", f"gold {len(got)} vs python {len(want)}"))
        if st == "a_subset_b" and (want or not succ.get(a)):
            fails.append((cid, "stratum", "not a non-leaf subset pair"))
        if st == "first_is_leaf" and succ.get(a): fails.append((cid, "stratum", "first side has deps"))
    elif tid == "C5.2":
        w1, w2 = len(succ.get(a, set())), len(succ.get(b, set()))
        g1, g2 = exp[0]["c1"], exp[0]["c2"]
        if (w1, w2) != (g1, g2): fails.append((cid, "recompute", f"gold {g1},{g2} vs python {w1},{w2}"))
        rel = "first_greater" if w1 > w2 else "second_greater" if w1 < w2 else "equal"
        expect = {"first_more": "first_greater", "second_more": "second_greater",
                  "tie_nonzero": "equal", "tie_zero": "equal"}[st]
        if rel != expect: fails.append((cid, "stratum", f"{st} but {rel}"))
        if st == "tie_nonzero" and w1 == 0: fails.append((cid, "stratum", "tie_nonzero is 0 vs 0"))
        if st == "tie_zero" and w1 != 0: fails.append((cid, "stratum", "tie_zero is not 0"))
    elif tid == "C5.3":
        w1, w2 = len(cves.get(a, [])), len(cves.get(b, []))
        g1, g2 = exp[0]["n1"], exp[0]["n2"]
        if (w1, w2) != (g1, g2): fails.append((cid, "recompute", f"gold {g1},{g2} vs python {w1},{w2}"))
        rel = "first_greater" if w1 > w2 else "second_greater" if w1 < w2 else "equal"
        expect = {"both_vuln_first_more": "first_greater", "both_vuln_second_more": "second_greater",
                  "both_vuln_tie": "equal", "only_first_vuln": "first_greater",
                  "neither_vuln": "equal"}[st]
        if rel != expect: fails.append((cid, "stratum", f"{st} but {rel}"))
        if st == "only_first_vuln" and w2 != 0: fails.append((cid, "stratum", "second side is vulnerable"))
        if st == "neither_vuln" and (w1 or w2): fails.append((cid, "stratum", "a side is vulnerable"))
        if st.startswith("both_vuln") and (not w1 or not w2): fails.append((cid, "stratum", "a side has no CVE"))
    elif tid == "C5.6":
        want = cwes.get(a, set()) & cwes.get(b, set())
        got = {r["cweId"] for r in exp}
        if want != got: fails.append((cid, "recompute", f"gold {sorted(got)} vs python {sorted(want)}"))
        if st == "shares_cwe" and not want: fails.append((cid, "stratum", "no shared CWE"))
        if st == "no_shared_cwe" and (want or not cwes.get(a) or not cwes.get(b)):
            fails.append((cid, "stratum", "not a both-have-CWE disjoint pair"))
        if st == "second_has_no_cwe" and cves.get(b): fails.append((cid, "stratum", "second side is vulnerable"))

    if (i + 1) % 300 == 0:
        print(f"  ...{i+1}/{len(c5)}, {len(fails)} failures", flush=True)

print(f"\nRESULT: {len(c5)} cases, {len(fails)} failures")
for f in fails[:20]: print("  FAIL", f)
out = {"n": len(c5), "failures": [{"id": a_, "check": b_, "detail": d} for a_, b_, d in fails],
       "gold_ms": {"median": round(statistics.median(times)*1000, 1), "max": round(max(times)*1000, 1)}}
(T2C / "verify_c5_report.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
print("gold_ms:", out["gold_ms"], "-> verify_c5_report.json")
ses.close(); drv.close()
