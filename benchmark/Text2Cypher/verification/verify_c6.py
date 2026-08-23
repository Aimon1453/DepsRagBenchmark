"""Independent verification of the C6 cases: recompute every answer in Python
from the raw VULNERABLE_TO / VULNERABILITY_TYPE edge lists, sharing no Cypher
with the golds.

Follows the house method (see README.md): the graph is pulled out as flat edge
lists once, each case's answer is recomputed with set logic, each stratum's
claim is re-checked with independent logic, and the stored expected_result has
to match exactly. Also replays every C1-C5 case's gold once, because the C6
build rewrote the shared dataset file.
"""
import json, os, sys, collections, statistics, time
from pathlib import Path
from dotenv import load_dotenv
from neo4j import GraphDatabase

ROOT = Path(r"C:/Master Thesis/DepsRagBenchmark")
T2C = ROOT / "benchmark/Text2Cypher"
sys.path.insert(0, str(T2C))
load_dotenv(ROOT / ".env")
from t2c_templates_v4 import TEMPLATES  # noqa: E402

cases = json.loads((T2C / "t2c_purdue_dataset_v4.json").read_text(encoding="utf-8"))
c6 = [c for c in cases if c["template_id"].startswith("C6")]
rest = [c for c in cases if not c["template_id"].startswith("C6")]
print(f"{len(c6)} C6 cases, {len(rest)} other cases for regression replay")

drv = GraphDatabase.driver(os.environ["NEO4J_URI"],
    auth=(os.environ["NEO4J_USERNAME"], os.environ["NEO4J_PASSWORD"]),
    warn_notification_severity="OFF")
ses = drv.session(database=os.environ.get("NEO4J_DATABASE", "neo4j"))
def q(cy, timeout=120.0, **p):
    with ses.begin_transaction(timeout=timeout) as tx:
        return [dict(r) for r in tx.run(cy, **p)]

# ---- graph -> Python ------------------------------------------------------
vr = q("MATCH (sw:Software)-[:HAS_VERSION]->(v:SoftwareVersion) "
       "RETURN sw.name AS pkg, v.versionName AS ver")
cv = q("MATCH (sw:Software)-[:HAS_VERSION]->(v:SoftwareVersion)-[:VULNERABLE_TO]->(c:Vulnerability) "
       "RETURN sw.name AS pkg, v.versionName AS ver, c.cveId AS cve")
cw = q("MATCH (c:Vulnerability)-[:VULNERABILITY_TYPE]->(w:VulnerabilityType) "
       "RETURN c.cveId AS cve, w.cweId AS cwe")
pkg_ver = {(r["pkg"], r["ver"]) for r in vr}
pkg_names = {r["pkg"] for r in vr}
cves_of = collections.defaultdict(set)     # (pkg, ver) -> {cveId}
for r in cv:
    cves_of[(r["pkg"], r["ver"])].add(r["cve"])
all_cves = {r["cve"] for r in cv}
cwes_of_cve = collections.defaultdict(set)  # cveId -> {cweId}
for r in cw:
    cwes_of_cve[r["cve"]].add(r["cwe"])
print(f"graph: {len(pkg_ver)} (pkg,ver), {sum(len(v) for v in cves_of.values())} "
      f"(version,CVE) links, {len(all_cves)} CVE ids, {len(cwes_of_cve)} with a CWE")

def norm(rows):
    return sorted(tuple(sorted(str(v) for v in r.values())) for r in rows)

fails = []
def check(cid, label, ok, detail=""):
    if not ok:
        fails.append({"id": cid, "check": label, "detail": str(detail)[:300]})

for c in c6:
    cid, tid, st, p = c["id"], c["template_id"], c["stratum"], c["params"]
    exp = c["expected_result"]
    pkg, ver, cve = p.get("pkg"), p.get("ver"), p.get("cve")

    # ---- recompute the answer with set logic ----
    if tid == "C6.2":
        truth = [{"cveId": x} for x in sorted(cves_of.get((pkg, ver), set()))]
    elif tid == "C6.3":
        cwes = set()
        for x in cves_of.get((pkg, ver), set()):
            cwes |= cwes_of_cve.get(x, set())
        truth = [{"cweId": x} for x in sorted(cwes)]
    elif tid == "C6.6":
        truth = [{"has_cve": cve in cves_of.get((pkg, ver), set())}]
    elif tid == "C6.7":
        truth = [{"cweId": x} for x in sorted(cwes_of_cve.get(cve, set()))]
    elif tid == "C6.8":
        rows = []
        for x in sorted(cves_of.get((pkg, ver), set())):
            ws = cwes_of_cve.get(x, set())
            if ws:
                rows.extend({"cveId": x, "cweId": w} for w in sorted(ws))
            else:
                rows.append({"cveId": x, "cweId": None})
        truth = rows
    else:
        check(cid, "known-template", False, tid); continue

    check(cid, "python-recompute", norm(truth) == norm(exp),
          f"truth={truth[:4]} exp={exp[:4]}")

    # ---- stratum claims, re-derived from the edge lists ----
    if st in ("has_cves", "has_cwes", "has_cve", "vuln_other_cve", "near_miss_cve",
              "cves_without_cwe"):
        check(cid, "params-in-graph", (pkg, ver) in pkg_ver, (pkg, ver))
    if st == "has_cves":
        check(cid, "stratum", bool(cves_of.get((pkg, ver))), "expected >=1 CVE")
    elif st == "has_cwes":
        check(cid, "stratum", any(cwes_of_cve.get(x) for x in cves_of.get((pkg, ver), set())),
              "expected >=1 CWE via some CVE")
    elif st == "cves_without_cwe":
        s = cves_of.get((pkg, ver), set())
        check(cid, "stratum", bool(s) and not any(cwes_of_cve.get(x) for x in s),
              "expected CVEs present but none classified")
    elif st == "clean_version":
        check(cid, "params-in-graph", (pkg, ver) in pkg_ver, (pkg, ver))
        check(cid, "stratum", not cves_of.get((pkg, ver)), "expected no CVEs")
    elif st == "absent_package":
        check(cid, "stratum", pkg not in pkg_names, pkg)
    elif st == "has_cve":
        check(cid, "stratum", cve in cves_of.get((pkg, ver), set()), (pkg, ver, cve))
    elif st == "vuln_other_cve":
        check(cid, "stratum", bool(cves_of.get((pkg, ver)))
              and cve in all_cves and cve not in cves_of.get((pkg, ver), set()),
              "expected vulnerable version + real foreign CVE")
    elif st == "near_miss_cve":
        check(cid, "stratum", bool(cves_of.get((pkg, ver))) and cve not in all_cves,
              "expected vulnerable version + absent CVE id")
    elif st == "clean_version_real_cve":
        check(cid, "stratum", (pkg, ver) in pkg_ver and not cves_of.get((pkg, ver))
              and cve in all_cves, "expected clean version + real CVE")
    elif st == "cve_with_cwe":
        check(cid, "stratum", cve in all_cves and bool(cwes_of_cve.get(cve)), cve)
    elif st == "cve_without_cwe":
        check(cid, "stratum", cve in all_cves and not cwes_of_cve.get(cve), cve)
    elif st == "absent_cve":
        check(cid, "stratum", cve not in all_cves, cve)
    else:
        check(cid, "known-stratum", False, st)

    # near-miss CVE ids must keep the CVE-YYYY-NNNN shape (learnability guard)
    if st in ("near_miss_cve", "absent_cve"):
        import re
        check(cid, "cve-shape", bool(re.match(r"^CVE-\d{4}-\d+$", cve)), cve)

# ---- gold replay: C6 twice (determinism), C1-C5 once (regression) ---------
times = []
for c in c6:
    t0 = time.perf_counter()
    r1 = q(c["cypher_query"])
    times.append((time.perf_counter() - t0) * 1000)
    r2 = q(c["cypher_query"])
    check(c["id"], "gold-replay", norm(r1) == norm(c["expected_result"]),
          f"got={r1[:3]}")
    check(c["id"], "gold-deterministic", norm(r1) == norm(r2))

reg_fail = 0
for c in rest:
    r = q(c["cypher_query"])
    if norm(r) != norm(c["expected_result"]):
        reg_fail += 1
        check(c["id"], "regression-replay", False, f"got={r[:3]}")

report = {
    "n": len(c6),
    "regression_n": len(rest),
    "regression_failures": reg_fail,
    "failures": fails,
    "gold_ms": {"median": round(statistics.median(times), 1),
                "max": round(max(times), 1)},
}
out = T2C / "verify_c6_report.json"
out.write_text(json.dumps(report, indent=1), encoding="utf-8")
print(json.dumps({k: v for k, v in report.items() if k != "failures"}, indent=1))
print(f"failures: {len(fails)}")
for f in fails[:20]:
    print(" ", f)
print("wrote", out)
ses.close(); drv.close()
