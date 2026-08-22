"""Opus-side review of the C3 build.

Deliberately does NOT re-run the existing verifier. Three angles it did not
cover:

  A. THIRD-PATH SEMANTICS. Gold uses `*1..6`; the existing verifier recomputes
     with a Python BFS. Both could share one wrong reading of Cypher's
     variable-length semantics (walks with distinct relationships vs shortest
     distance). So a sample is recomputed a THIRD way, in Cypher, as a UNION of
     six exact-depth patterns - a different query shape with the same intent.
     Agreement of all three settles the semantics question.

  B. CHECKS THE VERIFIER OMITTED: declared answer_shape conformance, parameter
     duplicates, stratum shares vs the registry, empty-answer attributability
     for absent_dep, and a full C1/C2 regression replay (the C3 build rewrote
     the shared dataset file).

  C. THE DEPTH-6 TRUNCATION, QUANTIFIED. C3.1/C3.6 ask for "all" dependencies
     but the gold stops at 6 hops. How many shipped cases actually truncate?
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
c3 = [c for c in cases if c["template_id"].startswith("C3")]
c12 = [c for c in cases if not c["template_id"].startswith("C3")]

drv = GraphDatabase.driver(os.environ["NEO4J_URI"],
    auth=(os.environ["NEO4J_USERNAME"], os.environ["NEO4J_PASSWORD"]))
ses = drv.session(database=os.environ.get("NEO4J_DATABASE", "neo4j"))
def q(cy, timeout=120.0, **params):
    with ses.begin_transaction(timeout=timeout) as tx:
        return [dict(r) for r in tx.run(cy, **params)]

findings = []          # (severity, area, detail)
def note(sev, area, detail):
    findings.append((sev, area, detail))
    print(f"  [{sev}] {area}: {detail}")

print("=" * 72)
print("A. THIRD-PATH SEMANTICS CHECK (Cypher UNION of exact depths)")
print("=" * 72)

UNION_1_6 = " UNION ".join(
    "MATCH (s:Software {name:$pkg})-[:HAS_VERSION]->(root:SoftwareVersion {versionName:$ver}) "
    f"MATCH (root)-[:DEPENDS_ON*{k}..{k}]->(dep:SoftwareVersion) WHERE dep <> root "
    "OPTIONAL MATCH (ds:Software)-[:HAS_VERSION]->(dep) "
    "RETURN DISTINCT ds.name AS software, dep.versionName AS version"
    for k in range(1, 7))

UNION_2_6_NOTDIRECT = " UNION ".join(
    "MATCH (s:Software {name:$pkg})-[:HAS_VERSION]->(root:SoftwareVersion {versionName:$ver}) "
    f"MATCH (root)-[:DEPENDS_ON*{k}..{k}]->(dep:SoftwareVersion) "
    "WHERE dep <> root AND NOT (root)-[:DEPENDS_ON]->(dep) "
    "OPTIONAL MATCH (ds:Software)-[:HAS_VERSION]->(dep) "
    "RETURN DISTINCT ds.name AS software, dep.versionName AS version"
    for k in range(2, 7))

import random
rng = random.Random(4242)
sample31 = rng.sample([c for c in c3 if c["template_id"] == "C3.1" and c["expected_result"]], 25)
sample36 = rng.sample([c for c in c3 if c["template_id"] == "C3.6" and c["expected_result"]], 25)

t = time.time(); mismatch = 0
for c in sample31:
    alt = q(UNION_1_6, **c["params"])
    a = {(r["software"], r["version"]) for r in alt}
    b = {(r["software"], r["version"]) for r in c["expected_result"]}
    if a != b:
        mismatch += 1
        note("FAIL", "C3.1 third-path", f"{c['id']}: union {len(a)} vs gold {len(b)}, sym-diff {len(a ^ b)}")
for c in sample36:
    alt = q(UNION_2_6_NOTDIRECT, **c["params"])
    a = {(r["software"], r["version"]) for r in alt}
    b = {(r["software"], r["version"]) for r in c["expected_result"]}
    if a != b:
        mismatch += 1
        note("FAIL", "C3.6 third-path", f"{c['id']}: union {len(a)} vs gold {len(b)}, sym-diff {len(a ^ b)}")
print(f"  50 cases recomputed with a structurally different query: {mismatch} mismatches ({time.time()-t:.0f}s)")
if mismatch == 0:
    print("  -> `*1..6` reachability == union of exact depths 1..6. Semantics confirmed on a third path.")

print()
print("=" * 72)
print("B. CHECKS THE EXISTING VERIFIER DID NOT MAKE")
print("=" * 72)

# B1 declared answer shape conformance
bad_shape = 0
for c in c3:
    tpl = TEMPLATES[c["template_id"]]
    shape = tpl["answer_shape"]
    rows = c["expected_result"]
    if c["answer_shape"] != shape["kind"]:
        note("FAIL", "shape-label", f"{c['id']} says {c['answer_shape']}, registry says {shape['kind']}"); bad_shape += 1
    if rows and list(rows[0].keys()) != shape["columns"]:
        note("FAIL", "shape-columns", f"{c['id']} {list(rows[0].keys())} != {shape['columns']}"); bad_shape += 1
    if shape["kind"] in ("bool", "scalar") and len(rows) != 1:
        note("FAIL", "shape-rows", f"{c['id']} {shape['kind']} returned {len(rows)} rows"); bad_shape += 1
    if shape["kind"] == "bool" and rows and not isinstance(rows[0][shape["columns"][0]], bool):
        note("FAIL", "shape-bool", f"{c['id']} non-bool value"); bad_shape += 1
    if shape["kind"] == "scalar" and rows and not isinstance(rows[0][shape["columns"][0]], (int, float)):
        note("FAIL", "shape-scalar", f"{c['id']} non-numeric value"); bad_shape += 1
    if set(c["params"]) != set(tpl["params"]):
        note("FAIL", "params-keys", f"{c['id']} {sorted(c['params'])} != {sorted(tpl['params'])}"); bad_shape += 1
    if "{" in c["question"] or "}" in c["question"]:
        note("FAIL", "placeholder", f"{c['id']} unfilled brace in question"); bad_shape += 1
print(f"  B1 answer-shape / params / placeholders on {len(c3)} cases: {bad_shape} problems")

# B2 parameter duplicates inside each template
dupes = 0
seen = {}
for c in c3:
    tpl = TEMPLATES[c["template_id"]]
    key = (c["template_id"], tuple(str(c["params"][k]) for k in tpl["params"]))
    if key in seen:
        note("FAIL", "param-duplicate", f"{c['id']} == {seen[key]}"); dupes += 1
    seen[key] = c["id"]
print(f"  B2 duplicate parameter tuples: {dupes}")

# B3 stratum shares vs registry declaration
print("  B3 stratum allocation vs declared shares:")
for tid in ("C3.1", "C3.2", "C3.3", "C3.4", "C3.5", "C3.6"):
    tpl = TEMPLATES[tid]
    group = [c for c in c3 if c["template_id"] == tid]
    n = len(group)
    got = collections.Counter(c["stratum"] for c in group)
    quota = min(250, tpl.get("max_quota", 250))
    if n != quota:
        note("FAIL", "quota", f"{tid} shipped {n}, expected {quota}")
    line = []
    for name, share, _, _ in tpl["strata"]:
        want = share * quota
        have = got.get(name, 0)
        flag = "" if abs(have - want) <= 1.5 else "  <-- OFF"
        line.append(f"{name} {have}/{want:.0f}{flag}")
        if flag:
            note("WARN", "stratum-share", f"{tid}.{name}: {have} drawn, {want:.0f} declared")
    print(f"    {tid} n={n}: " + " · ".join(line))

# B4 absent_dep / no_path emptiness must be attributable to the dep, not a leaf root
notattrib = 0
for c in c3:
    if c["stratum"] in ("absent_dep", "no_path"):
        r = q("MATCH (:Software {name:$pkg})-[:HAS_VERSION]->(v:SoftwareVersion {versionName:$ver}) "
              "RETURN EXISTS { (v)-[:DEPENDS_ON]->() } AS x", **{k: c["params"][k] for k in ("pkg", "ver")})
        if not r or not r[0]["x"]:
            note("FAIL", "attributability", f"{c['id']} ({c['stratum']}) root has no dependencies at all")
            notattrib += 1
print(f"  B4 empty-answer attributability (absent_dep/no_path roots have deps): {notattrib} problems")

# B5 C1/C2 regression - the C3 build rewrote the shared dataset file
t = time.time(); reg = 0
for c in c12:
    live = q(c["cypher_query"])
    if live != c["expected_result"]:
        note("FAIL", "C1C2-regression", f"{c['id']} stored {len(c['expected_result'])} vs live {len(live)}")
        reg += 1
print(f"  B5 C1/C2 regression replay of {len(c12)} cases: {reg} mismatches ({time.time()-t:.0f}s)")

print()
print("=" * 72)
print("C. DEPTH-6 TRUNCATION, QUANTIFIED")
print("=" * 72)

ver_rows = q("MATCH (sw:Software)-[:HAS_VERSION]->(v:SoftwareVersion) "
             "RETURN sw.name AS pkg, v.versionName AS ver, elementId(v) AS vid")
dep_rows = q("MATCH (a:SoftwareVersion)-[:DEPENDS_ON]->(b:SoftwareVersion) "
             "RETURN elementId(a) AS a, elementId(b) AS b")
succ = collections.defaultdict(list)
for r in dep_rows:
    succ[r["a"]].append(r["b"])
node_of = {(r["pkg"], r["ver"]): r["vid"] for r in ver_rows}

def reach(src, maxd):
    seen_, frontier, d = {src}, [src], 0
    while frontier and d < maxd:
        d += 1
        nxt = []
        for u in frontier:
            for w in succ.get(u, ()):
                if w not in seen_:
                    seen_.add(w); nxt.append(w)
        frontier = nxt
    return seen_

trunc = collections.Counter()
worst = []
for tid in ("C3.1", "C3.6"):
    for c in (x for x in c3 if x["template_id"] == tid and x["expected_result"]):
        src = node_of.get((c["params"]["pkg"], c["params"]["ver"]))
        if src is None:
            continue
        r6 = reach(src, 6)
        rall = reach(src, 99)
        missing = len(rall - r6)
        trunc[(tid, missing > 0)] += 1
        if missing:
            worst.append((missing, len(r6) - 1, c["id"]))
for tid in ("C3.1", "C3.6"):
    tr, ok = trunc[(tid, True)], trunc[(tid, False)]
    print(f"  {tid}: {tr} of {tr+ok} nonempty cases TRUNCATE at 6 hops ({tr/(tr+ok)*100:.0f}%)")
worst.sort(reverse=True)
print("  worst truncations (nodes omitted / nodes returned / case):")
for m, got, cid in worst[:5]:
    print(f"    {m:5d} omitted, {got:5d} returned   {cid}")
if worst:
    note("WARN", "depth-6 truncation",
         f"{len(worst)} C3.1/C3.6 cases answer 'all dependencies' with a 6-hop truncation; "
         f"largest omits {worst[0][0]} nodes")

print()
print("=" * 72)
print(f"REVIEW COMPLETE: {sum(1 for f in findings if f[0]=='FAIL')} FAIL, "
      f"{sum(1 for f in findings if f[0]=='WARN')} WARN")
print("=" * 72)
json.dump({"findings": [{"severity": s, "area": a, "detail": d} for s, a, d in findings],
           "truncation": {f"{k[0]}:{k[1]}": v for k, v in trunc.items()}},
          open(Path(__file__).resolve().parent / "review_c3_findings.json", "w"), indent=1)
ses.close(); drv.close()
