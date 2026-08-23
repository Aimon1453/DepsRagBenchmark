# Sample questions — one per template
The full bank is `t2c_purdue_dataset_v4.json` (9,317 questions, 26 MB — too large
for GitHub to render, so this file exists to be read in the browser). Each template
below is shown with one real question drawn from it, the gold Cypher that defines
the correct answer, and the first rows of that answer.

**9,317 questions · 41 templates · 2,294 with a deliberately empty answer**

New here? [`ABOUT_THE_QUESTION_BANK.md`](ABOUT_THE_QUESTION_BANK.md) explains in
one page how these questions are generated and how the answers are checked.

---

## C1: Entity / Version Lookup

### C1.1 · Easy · SR
**250 questions** in the bank · 37 of them answer "nothing" · groups: `multi_version` 138, `single_version` 75, `absent_package` 37

> What are the available versions for software 'clap'?

```cypher
MATCH (s:Software {name: 'clap'})-[:HAS_VERSION]->(v:SoftwareVersion) RETURN v.versionName AS version ORDER BY version
```

Gold answer (4 rows):
```json
[
 {
  "version": "2.34.0"
 },
 {
  "version": "3.2.25"
 },
 {
  "version": "4.1.13"
 },
 {
  "version": "4.5.37"
 }
]
```

### C1.2 · Easy · SA
**250 questions** in the bank · 0 of them answer "nothing" · groups: `exists` 138, `near_miss_version` 67, `absent_package` 45

> Does software 'ffmpeg' version '4.2.1' exist in the graph?

```cypher
MATCH (s:Software {name: 'ffmpeg'})-[:HAS_VERSION]->(v:SoftwareVersion {versionName: '4.2.1'}) RETURN count(v) > 0 AS exists
```

Gold answer (1 row):
```json
[
 {
  "exists": true
 }
]
```

---

## C2: Direct DEPENDS_ON (out)

### C2.1 · Easy · CR
**250 questions** in the bank · 82 of them answer "nothing" · groups: `has_direct_deps` 168, `leaf_version` 55, `absent_package` 27

> What are the direct dependencies of software 'crossbeam-channel' version '0.5.15'?

```cypher
MATCH (s:Software {name: 'crossbeam-channel'})-[:HAS_VERSION]->(root:SoftwareVersion {versionName: '0.5.15'}) MATCH (root)-[:DEPENDS_ON]->(dep:SoftwareVersion) OPTIONAL MATCH (ds:Software)-[:HAS_VERSION]->(dep) RETURN DISTINCT ds.name AS software, dep.versionName AS version ORDER BY software, version
```

Gold answer (4 rows):
```json
[
 {
  "software": "crossbeam-utils",
  "version": "0.8.21"
 },
 {
  "software": "num_cpus",
  "version": "1.16.0"
 },
 {
  "software": "rand",
  "version": "0.8.5"
 },
 {
  "software": "signal-hook",
  "version": "0.3.17"
 }
]
```

### C2.2 · Easy · SA
**250 questions** in the bank · 0 of them answer "nothing" · groups: `direct_dep` 100, `grandchild_not_direct` 88, `unrelated_dep` 62

> Does software 'ffmpeg' version '4.2.1' directly depend on software 'libaom-av1'?

```cypher
MATCH (s:Software {name: 'ffmpeg'})-[:HAS_VERSION]->(root:SoftwareVersion {versionName: '4.2.1'}) MATCH (root)-[:DEPENDS_ON]->(dep:SoftwareVersion)<-[:HAS_VERSION]-(ds:Software {name: 'libaom-av1'}) RETURN count(dep) > 0 AS depends
```

Gold answer (1 row):
```json
[
 {
  "depends": true
 }
]
```

### C2.3 · Easy · SR
**250 questions** in the bank · 100 of them answer "nothing" · groups: `direct_dep` 150, `grandchild_not_direct` 63, `unrelated_dep` 37

> What version(s) of software 'libpng' does software 'freetype' version '2.13.2' directly depend on?

```cypher
MATCH (s:Software {name: 'freetype'})-[:HAS_VERSION]->(root:SoftwareVersion {versionName: '2.13.2'}) MATCH (root)-[:DEPENDS_ON]->(dep:SoftwareVersion)<-[:HAS_VERSION]-(ds:Software {name: 'libpng'}) RETURN DISTINCT dep.versionName AS version ORDER BY version
```

Gold answer (3 rows):
```json
[
 {
  "version": "1.6.40"
 },
 {
  "version": "1.6.42"
 },
 {
  "version": "1.6.43"
 }
]
```

---

## C3: Transitive / path

### C3.1 · Medium · CR
**250 questions** in the bank · 82 of them answer "nothing" · groups: `has_indirect` 125, `leaf_version` 55, `direct_only` 43, `absent_package` 27

> What are all dependencies of software 'yarl' version '1.20.0', including both direct and indirect?

```cypher
MATCH (s:Software {name: 'yarl'})-[:HAS_VERSION]->(root:SoftwareVersion {versionName: '1.20.0'}) MATCH (root)-[:DEPENDS_ON*1..6]->(dep:SoftwareVersion) WHERE dep <> root OPTIONAL MATCH (ds:Software)-[:HAS_VERSION]->(dep) RETURN DISTINCT ds.name AS software, dep.versionName AS version ORDER BY software, version
```

Gold answer (4 rows):
```json
[
 {
  "software": "idna",
  "version": "3.10.0"
 },
 {
  "software": "multidict",
  "version": "6.4.3"
 },
 {
  "software": "propcache",
  "version": "0.3.1"
 },
 {
  "software": "typing-extensions",
  "version": "4.13.2"
 }
]
```

### C3.2 · Medium · SA
**250 questions** in the bank · 0 of them answer "nothing" · groups: `indirect_true` 88, `reverse_only` 75, `unreachable` 50, `direct_true` 37

> Does software 'freetype' version '2.13.2' depend on software 'ninja' at any depth?

```cypher
MATCH (s:Software {name: 'freetype'})-[:HAS_VERSION]->(root:SoftwareVersion {versionName: '2.13.2'}) MATCH (root)-[:DEPENDS_ON*1..6]->(dep:SoftwareVersion)<-[:HAS_VERSION]-(ds:Software {name: 'ninja'}) RETURN count(dep) > 0 AS depends
```

Gold answer (1 row):
```json
[
 {
  "depends": true
 }
]
```

### C3.3 · Hard · CR
**250 questions** in the bank · 99 of them answer "nothing" · groups: `unique_path_2plus` 113, `no_path` 62, `unique_path_direct` 38, `absent_dep` 37

> What dependency path exists from software 'pkgconf' version '2.1.0' to software 'ninja'?

```cypher
MATCH (s:Software {name: 'pkgconf'})-[:HAS_VERSION]->(root:SoftwareVersion {versionName: '2.1.0'}) MATCH (ds:Software {name: 'ninja'})-[:HAS_VERSION]->(target:SoftwareVersion) MATCH p = shortestPath((root)-[:DEPENDS_ON*1..6]->(target)) RETURN [n IN nodes(p) | n.versionName] AS path ORDER BY path
```

Gold answer (1 row):
```json
[
 {
  "path": [
   "2.1.0",
   "1.2.2",
   "1.11.1"
  ]
 }
]
```

### C3.4 · Medium · CR
**250 questions** in the bank · 95 of them answer "nothing" · groups: `has_dist2` 155, `deps_no_dist2` 40, `leaf_version` 28, `absent_package` 27

> What dependencies of software 'pretty_assertions' version '1.4.1' are exactly 2 hops away (and not also direct dependencies)?

```cypher
MATCH (s:Software {name: 'pretty_assertions'})-[:HAS_VERSION]->(root:SoftwareVersion {versionName: '1.4.1'}) MATCH (root)-[:DEPENDS_ON*2..2]->(dep:SoftwareVersion) WHERE NOT (root)-[:DEPENDS_ON]->(dep) AND dep <> root OPTIONAL MATCH (ds:Software)-[:HAS_VERSION]->(dep) RETURN DISTINCT ds.name AS software, dep.versionName AS version ORDER BY software, version
```

Gold answer (4 rows):
```json
[
 {
  "software": "criterion",
  "version": "0.3.6"
 },
 {
  "software": "is-terminal",
  "version": "0.4.16"
 },
 {
  "software": "quickcheck",
  "version": "1.0.3"
 },
 {
  "software": "speculate",
  "version": "0.1.2"
 }
]
```

### C3.5 · Medium · SA
**190 questions** in the bank · 0 of them answer "nothing" · groups: `depth_exactly1` 70, `leaf_version` 63, `depth_ge2` 57

> What is the maximum dependency depth of software 'openh264' version '2.3.1'?

```cypher
MATCH (s:Software {name: 'openh264'})-[:HAS_VERSION]->(root:SoftwareVersion {versionName: '2.3.1'}) OPTIONAL MATCH p = (root)-[:DEPENDS_ON*1..6]->(dep:SoftwareVersion) RETURN coalesce(max(length(p)), 0) AS max_depth
```

Gold answer (1 row):
```json
[
 {
  "max_depth": 3
 }
]
```

### C3.6 · Medium · CR
**250 questions** in the bank · 95 of them answer "nothing" · groups: `has_indirect` 155, `all_deps_direct` 40, `leaf_version` 28, `absent_package` 27

> What are the indirect (not direct) dependencies of software 'freetype' version '2.13.2'?

```cypher
MATCH (s:Software {name: 'freetype'})-[:HAS_VERSION]->(root:SoftwareVersion {versionName: '2.13.2'}) MATCH (root)-[:DEPENDS_ON*2..6]->(dep:SoftwareVersion) WHERE NOT (root)-[:DEPENDS_ON]->(dep) AND dep <> root OPTIONAL MATCH (ds:Software)-[:HAS_VERSION]->(dep) RETURN DISTINCT ds.name AS software, dep.versionName AS version ORDER BY software, version
```

Gold answer (3 rows):
```json
[
 {
  "software": "meson",
  "version": "1.2.2"
 },
 {
  "software": "ninja",
  "version": "1.11.1"
 },
 {
  "software": "zlib",
  "version": "1.2.13"
 }
]
```

---

## C4: Aggregation (dependency)

### C4.3 · Medium · SA
**250 questions** in the bank · 0 of them answer "nothing" · groups: `has_indirect` 125, `leaf_version` 55, `direct_only` 40, `absent_package` 30

> How many distinct software products are in the dependency closure of software 'pkgconf' version '2.2.0'?

```cypher
MATCH (s:Software {name: 'pkgconf'})-[:HAS_VERSION]->(root:SoftwareVersion {versionName: '2.2.0'}) OPTIONAL MATCH (root)-[:DEPENDS_ON*0..6]->(dep:SoftwareVersion)<-[:HAS_VERSION]-(ds:Software) RETURN count(DISTINCT ds) AS cnt
```

Gold answer (1 row):
```json
[
 {
  "cnt": 3
 }
]
```

### C4.4 · Easy · SA
**250 questions** in the bank · 0 of them answer "nothing" · groups: `has_indirect` 125, `leaf_version` 55, `direct_only` 40, `absent_package` 30

> How many leaf dependencies (no further DEPENDS_ON) does the tree of software 'pkgconf' version '2.2.0' contain?

```cypher
MATCH (s:Software {name: 'pkgconf'})-[:HAS_VERSION]->(root:SoftwareVersion {versionName: '2.2.0'}) MATCH (root)-[:DEPENDS_ON*0..6]->(n:SoftwareVersion) WHERE NOT (n)-[:DEPENDS_ON]->(:SoftwareVersion) RETURN count(DISTINCT n) AS cnt
```

Gold answer (1 row):
```json
[
 {
  "cnt": 1
 }
]
```

---

## C5: Comparative / set

### C5.1 · Hard · CR
**250 questions** in the bank · 85 of them answer "nothing" · groups: `shares_direct` 165, `no_shared_direct` 55, `first_is_leaf` 30

> What common direct dependencies are shared by software 'rstest' version '0.13.0' and software 'rstest_macros' version '0.18.2'?

```cypher
MATCH (s1:Software {name: 'rstest'})-[:HAS_VERSION]->(r1:SoftwareVersion {versionName: '0.13.0'}) MATCH (s2:Software {name: 'rstest_macros'})-[:HAS_VERSION]->(r2:SoftwareVersion {versionName: '0.18.2'}) MATCH (r1)-[:DEPENDS_ON]->(d:SoftwareVersion)<-[:DEPENDS_ON]-(r2) OPTIONAL MATCH (ds:Software)-[:HAS_VERSION]->(d) RETURN DISTINCT ds.name AS software, d.versionName AS version ORDER BY software, version
```

Gold answer (4 rows):
```json
[
 {
  "software": "actix-rt",
  "version": "2.10.0"
 },
 {
  "software": "async-std",
  "version": "1.13.1"
 },
 {
  "software": "pretty_assertions",
  "version": "1.4.1"
 },
 {
  "software": "rustc_version",
  "version": "0.4.1"
 }
]
```

### C5.2 · Medium · SA
**250 questions** in the bank · 0 of them answer "nothing" · groups: `first_more` 80, `second_more` 80, `tie_nonzero` 50, `tie_zero` 40

> Which has more direct dependencies: software 'libpng' version '1.6.43' or software 'idna' version '3.10.0'?

```cypher
MATCH (s1:Software {name: 'libpng'})-[:HAS_VERSION]->(r1:SoftwareVersion {versionName: '1.6.43'}) OPTIONAL MATCH (r1)-[:DEPENDS_ON]->(d1:SoftwareVersion) WITH count(DISTINCT d1) AS c1 MATCH (s2:Software {name: 'idna'})-[:HAS_VERSION]->(r2:SoftwareVersion {versionName: '3.10.0'}) OPTIONAL MATCH (r2)-[:DEPENDS_ON]->(d2:SoftwareVersion) WITH c1, count(DISTINCT d2) AS c2 RETURN c1, c2
```

Gold answer (1 row):
```json
[
 {
  "c1": 1,
  "c2": 0
 }
]
```

### C5.3 · Medium · SA
**250 questions** in the bank · 0 of them answer "nothing" · groups: `both_vuln_first_more` 60, `both_vuln_second_more` 60, `both_vuln_tie` 50, `only_first_vuln` 45, `neither_vuln` 35

> Which has more known vulnerabilities (CVEs): software 'ffmpeg' version '4.2.1' or software 'crossbeam-utils' version '0.7.2'?

```cypher
MATCH (s1:Software {name: 'ffmpeg'})-[:HAS_VERSION]->(r1:SoftwareVersion {versionName: '4.2.1'}) OPTIONAL MATCH (r1)-[:VULNERABLE_TO]->(cve1:Vulnerability) WITH count(cve1) AS n1 MATCH (s2:Software {name: 'crossbeam-utils'})-[:HAS_VERSION]->(r2:SoftwareVersion {versionName: '0.7.2'}) OPTIONAL MATCH (r2)-[:VULNERABLE_TO]->(cve2:Vulnerability) WITH n1, count(cve2) AS n2 RETURN n1, n2
```

Gold answer (1 row):
```json
[
 {
  "n1": 10,
  "n2": 1
 }
]
```

### C5.4 · Hard · CR
**250 questions** in the bank · 85 of them answer "nothing" · groups: `shares_tree` 165, `disjoint_trees` 55, `first_is_leaf` 30

> What dependencies appear in both full dependency trees (direct and indirect) of software 'aiohttp' version '3.8.0' and software 'yarl' version '1.20.0'?

```cypher
MATCH (s1:Software {name: 'aiohttp'})-[:HAS_VERSION]->(r1:SoftwareVersion {versionName: '3.8.0'}) MATCH (r1)-[:DEPENDS_ON*1..6]->(d1:SoftwareVersion) WHERE d1 <> r1 WITH collect(DISTINCT d1) AS t1 MATCH (s2:Software {name: 'yarl'})-[:HAS_VERSION]->(r2:SoftwareVersion {versionName: '1.20.0'}) MATCH (r2)-[:DEPENDS_ON*1..6]->(d2:SoftwareVersion) WHERE d2 <> r2 AND d2 IN t1 OPTIONAL MATCH (ds:Software)-[:HAS_VERSION]->(d2) RETURN DISTINCT ds.name AS software, d2.versionName AS version ORDER BY software, version
```

Gold answer (4 rows):
```json
[
 {
  "software": "idna",
  "version": "3.10.0"
 },
 {
  "software": "multidict",
  "version": "6.4.3"
 },
 {
  "software": "propcache",
  "version": "0.3.1"
 },
 {
  "software": "typing-extensions",
  "version": "4.13.2"
 }
]
```

### C5.5 · Hard · CR
**250 questions** in the bank · 85 of them answer "nothing" · groups: `has_extra_dep` 165, `a_subset_b` 55, `first_is_leaf` 30

> Which direct dependencies of software 'humantime-serde' version '1.1.1' are not direct dependencies of software 'charset-normalizer' version '3.4.1'?

```cypher
MATCH (s1:Software {name: 'humantime-serde'})-[:HAS_VERSION]->(r1:SoftwareVersion {versionName: '1.1.1'}) MATCH (r1)-[:DEPENDS_ON]->(d:SoftwareVersion) OPTIONAL MATCH (ds:Software)-[:HAS_VERSION]->(d) WITH d, ds MATCH (s2:Software {name: 'charset-normalizer'})-[:HAS_VERSION]->(r2:SoftwareVersion {versionName: '3.4.1'}) WHERE NOT (r2)-[:DEPENDS_ON]->(d) RETURN DISTINCT ds.name AS software, d.versionName AS version ORDER BY software, version
```

Gold answer (4 rows):
```json
[
 {
  "software": "humantime",
  "version": "2.2.0"
 },
 {
  "software": "serde",
  "version": "1.0.210"
 },
 {
  "software": "serde_json",
  "version": "1.0.140"
 },
 {
  "software": "version-sync",
  "version": "0.8.1"
 }
]
```

### C5.6 · Medium · CR
**170 questions** in the bank · 85 of them answer "nothing" · groups: `shares_cwe` 85, `no_shared_cwe` 51, `second_has_no_cwe` 34

> What CWE IDs are shared by software 'cryptography' version '2.2.0' and software 'openssl' version '0.9.6c-2.woody.7'?

```cypher
MATCH (s1:Software {name: 'cryptography'})-[:HAS_VERSION]->(r1:SoftwareVersion {versionName: '2.2.0'}) MATCH (r1)-[:VULNERABLE_TO]->(:Vulnerability)-[:VULNERABILITY_TYPE]->(w:VulnerabilityType) MATCH (s2:Software {name: 'openssl'})-[:HAS_VERSION]->(r2:SoftwareVersion {versionName: '0.9.6c-2.woody.7'}) MATCH (r2)-[:VULNERABLE_TO]->(:Vulnerability)-[:VULNERABILITY_TYPE]->(w) RETURN DISTINCT w.cweId AS cweId ORDER BY cweId
```

Gold answer (3 rows):
```json
[
 {
  "cweId": "CWE-20"
 },
 {
  "cweId": "CWE-203"
 },
 {
  "cweId": "CWE-476"
 }
]
```

---

## C6: Vulnerability

### C6.2 · Medium · CR
**70 questions** in the bank · 23 of them answer "nothing" · groups: `has_cves` 47, `clean_version` 15, `absent_package` 8

> What are the CVE IDs of known vulnerabilities affecting software 'perl' version '5.14.2-21+deb7u3'?

```cypher
MATCH (s:Software {name: 'perl'})-[:HAS_VERSION]->(root:SoftwareVersion {versionName: '5.14.2-21+deb7u3'}) MATCH (root)-[:VULNERABLE_TO]->(c:Vulnerability) RETURN DISTINCT c.cveId AS cveId ORDER BY cveId
```

Gold answer (4 rows):
```json
[
 {
  "cveId": "CVE-2012-5195"
 },
 {
  "cveId": "CVE-2012-6329"
 },
 {
  "cveId": "CVE-2013-1667"
 },
 {
  "cveId": "CVE-2016-1238"
 }
]
```

### C6.3 · Medium · CR
**67 questions** in the bank · 22 of them answer "nothing" · groups: `has_cwes` 45, `clean_version` 13, `absent_package` 7, `cves_without_cwe` 2

> What CWE IDs are associated with vulnerabilities of software 'perl' version '5.8.8-7'?

```cypher
MATCH (s:Software {name: 'perl'})-[:HAS_VERSION]->(root:SoftwareVersion {versionName: '5.8.8-7'}) MATCH (root)-[:VULNERABLE_TO]->(:Vulnerability)-[:VULNERABILITY_TYPE]->(w:VulnerabilityType) RETURN DISTINCT w.cweId AS cweId ORDER BY cweId
```

Gold answer (4 rows):
```json
[
 {
  "cweId": "CWE-189"
 },
 {
  "cweId": "CWE-264"
 },
 {
  "cweId": "CWE-362"
 },
 {
  "cweId": "CWE-399"
 }
]
```

### C6.6 · Easy · SA
**250 questions** in the bank · 0 of them answer "nothing" · groups: `has_cve` 125, `vuln_other_cve` 50, `near_miss_cve` 38, `clean_version_real_cve` 37

> Does software 'yasm' version '1.3.0' have vulnerability 'CVE-2023-31975'?

```cypher
MATCH (s:Software {name: 'yasm'})-[:HAS_VERSION]->(root:SoftwareVersion {versionName: '1.3.0'}) OPTIONAL MATCH (root)-[:VULNERABLE_TO]->(c:Vulnerability {cveId: 'CVE-2023-31975'}) RETURN count(c) > 0 AS has_cve
```

Gold answer (1 row):
```json
[
 {
  "has_cve": true
 }
]
```

### C6.7 · Easy · SR
**200 questions** in the bank · 66 of them answer "nothing" · groups: `cve_with_cwe` 134, `absent_cve` 45, `cve_without_cwe` 21

> What CWE IDs are linked to vulnerability 'CVE-2024-27308'?

```cypher
MATCH (c:Vulnerability {cveId: 'CVE-2024-27308'})-[:VULNERABILITY_TYPE]->(w:VulnerabilityType) RETURN DISTINCT w.cweId AS cweId ORDER BY cweId
```

Gold answer (2 rows):
```json
[
 {
  "cweId": "CWE-416"
 },
 {
  "cweId": "CWE-672"
 }
]
```

### C6.8 · Medium · CR
**70 questions** in the bank · 23 of them answer "nothing" · groups: `has_cves` 47, `clean_version` 15, `absent_package` 8

> What CVE-CWE pairs affect software 'perl' version '5.14.2-21+deb7u3'?

```cypher
MATCH (s:Software {name: 'perl'})-[:HAS_VERSION]->(root:SoftwareVersion {versionName: '5.14.2-21+deb7u3'}) MATCH (root)-[:VULNERABLE_TO]->(c:Vulnerability) OPTIONAL MATCH (c)-[:VULNERABILITY_TYPE]->(w:VulnerabilityType) RETURN DISTINCT c.cveId AS cveId, w.cweId AS cweId ORDER BY cveId, cweId
```

Gold answer (4 rows):
```json
[
 {
  "cveId": "CVE-2012-5195",
  "cweId": "CWE-119"
 },
 {
  "cveId": "CVE-2012-6329",
  "cweId": "CWE-94"
 },
 {
  "cveId": "CVE-2013-1667",
  "cweId": "CWE-399"
 },
 {
  "cveId": "CVE-2016-1238",
  "cweId": "CWE-264"
 }
]
```

---

## C7: Reverse DEPENDS_ON (in)

### C7.1 · Easy · CR
**250 questions** in the bank · 37 of them answer "nothing" · groups: `has_dependents` 213, `absent_package` 27, `no_dependents` 10

> Which software versions directly depend on software 'yasna' version '0.5.2'?

```cypher
MATCH (s:Software {name: 'yasna'})-[:HAS_VERSION]->(root:SoftwareVersion {versionName: '0.5.2'}) MATCH (other:SoftwareVersion)-[:DEPENDS_ON]->(root) OPTIONAL MATCH (os:Software)-[:HAS_VERSION]->(other) RETURN DISTINCT os.name AS software, other.versionName AS version ORDER BY software, version
```

Gold answer (4 rows):
```json
[
 {
  "software": "click",
  "version": "0.6.1"
 },
 {
  "software": "p12",
  "version": "0.6.3"
 },
 {
  "software": "rcgen",
  "version": "0.10.0"
 },
 {
  "software": "webpki-roots",
  "version": "0.25.4"
 }
]
```

### C7.2 · Medium · CR
**250 questions** in the bank · 53 of them answer "nothing" · groups: `has_dependents` 197, `absent_product` 45, `no_version_depended_on` 8

> Which software products have some version that directly depends on software 'meson'?

```cypher
MATCH (ds:Software {name: 'meson'})-[:HAS_VERSION]->(target:SoftwareVersion) MATCH (other:SoftwareVersion)-[:DEPENDS_ON]->(target) OPTIONAL MATCH (os:Software)-[:HAS_VERSION]->(other) RETURN DISTINCT os.name AS software, other.versionName AS version ORDER BY software, version
```

Gold answer (4 rows):
```json
[
 {
  "software": "freetype",
  "version": "2.13.2"
 },
 {
  "software": "openh264",
  "version": "2.3.1"
 },
 {
  "software": "pkgconf",
  "version": "2.1.0"
 },
 {
  "software": "pkgconf",
  "version": "2.2.0"
 }
]
```

### C7.5 · Medium · CR
**250 questions** in the bank · 45 of them answer "nothing" · groups: `has_indirect_dependents` 150, `direct_only_dependents` 55, `absent_package` 35, `no_dependents` 10

> Which software versions can reach software 'idna' version '3.10.0' via DEPENDS_ON at any depth?

```cypher
MATCH (s:Software {name: 'idna'})-[:HAS_VERSION]->(root:SoftwareVersion {versionName: '3.10.0'}) MATCH (other:SoftwareVersion)-[:DEPENDS_ON*1..6]->(root) WHERE other <> root OPTIONAL MATCH (os:Software)-[:HAS_VERSION]->(other) RETURN DISTINCT os.name AS software, other.versionName AS version ORDER BY software, version
```

Gold answer (4 rows):
```json
[
 {
  "software": "aiohttp",
  "version": "3.8.0"
 },
 {
  "software": "cryptography",
  "version": "2.2.0"
 },
 {
  "software": "requests",
  "version": "2.31.0"
 },
 {
  "software": "yarl",
  "version": "1.20.0"
 }
]
```

---

## C8: Dep x Vuln

### C8.2 · Medium · CR
**250 questions** in the bank · 90 of them answer "nothing" · groups: `vuln_in_closure` 160, `deps_no_vuln` 40, `leaf_version` 25, `absent_package` 25

> What CVE IDs affect any direct or indirect dependency of software 'float-ord' version '0.3.2' (excluding the root itself)?

```cypher
MATCH (s:Software {name: 'float-ord'})-[:HAS_VERSION]->(root:SoftwareVersion {versionName: '0.3.2'}) MATCH (root)-[:DEPENDS_ON*1..6]->(dep:SoftwareVersion) WHERE dep <> root MATCH (dep)-[:VULNERABLE_TO]->(c:Vulnerability) RETURN DISTINCT c.cveId AS cveId ORDER BY cveId
```

Gold answer (4 rows):
```json
[
 {
  "cveId": "CVE-2018-20993"
 },
 {
  "cveId": "CVE-2019-25010"
 },
 {
  "cveId": "CVE-2020-25575"
 },
 {
  "cveId": "CVE-2020-26235"
 }
]
```

### C8.5 · Medium · CR
**250 questions** in the bank · 95 of them answer "nothing" · groups: `vuln_in_tree` 146, `clean_tree` 39, `leaf_clean` 31, `absent_package` 25, `root_only_vuln` 9

> Which software products in the dependency tree of software 'futures-preview' version '0.3.0-alpha.19' (including the root) have known vulnerabilities?

```cypher
MATCH (s:Software {name: 'futures-preview'})-[:HAS_VERSION]->(root:SoftwareVersion {versionName: '0.3.0-alpha.19'}) MATCH (root)-[:DEPENDS_ON*0..6]->(n:SoftwareVersion)-[:VULNERABLE_TO]->(:Vulnerability) MATCH (ds:Software)-[:HAS_VERSION]->(n) RETURN DISTINCT ds.name AS software, n.versionName AS version ORDER BY software, version
```

Gold answer (4 rows):
```json
[
 {
  "software": "crossbeam-utils",
  "version": "0.7.2"
 },
 {
  "software": "idna",
  "version": "0.2.3"
 },
 {
  "software": "time",
  "version": "0.1.45"
 },
 {
  "software": "tokio",
  "version": "0.1.22"
 }
]
```

### C8.6 · Medium · CR
**250 questions** in the bank · 93 of them answer "nothing" · groups: `cwe_in_tree` 149, `clean_tree` 38, `leaf_clean` 30, `absent_package` 25, `root_only_cwe` 8

> What CWE IDs appear in the dependency tree of software 'ppv-lite86' version '0.2.21' (including the root)?

```cypher
MATCH (s:Software {name: 'ppv-lite86'})-[:HAS_VERSION]->(root:SoftwareVersion {versionName: '0.2.21'}) MATCH (root)-[:DEPENDS_ON*0..6]->(n:SoftwareVersion)-[:VULNERABLE_TO]->(:Vulnerability)-[:VULNERABILITY_TYPE]->(w:VulnerabilityType) RETURN DISTINCT w.cweId AS cweId ORDER BY cweId
```

Gold answer (4 rows):
```json
[
 {
  "cweId": "CWE-362"
 },
 {
  "cweId": "CWE-400"
 },
 {
  "cweId": "CWE-476"
 },
 {
  "cweId": "CWE-835"
 }
]
```

### C8.7 · Hard · CR
**250 questions** in the bank · 94 of them answer "nothing" · groups: `has_indirect_vuln` 156, `no_vuln_deps` 45, `leaf_version` 20, `absent_package` 20, `vuln_direct_only` 9

> Which indirect (not direct) dependencies of software 'futures-preview' version '0.3.0-alpha.19' have known vulnerabilities, and what are their CVE IDs?

```cypher
MATCH (s:Software {name: 'futures-preview'})-[:HAS_VERSION]->(root:SoftwareVersion {versionName: '0.3.0-alpha.19'}) MATCH (root)-[:DEPENDS_ON*2..6]->(dep:SoftwareVersion) WHERE dep <> root AND NOT (root)-[:DEPENDS_ON]->(dep) MATCH (dep)-[:VULNERABLE_TO]->(c:Vulnerability) OPTIONAL MATCH (ds:Software)-[:HAS_VERSION]->(dep) RETURN DISTINCT ds.name AS software, dep.versionName AS version, c.cveId AS cveId ORDER BY software, version, cveId
```

Gold answer (4 rows):
```json
[
 {
  "software": "crossbeam-utils",
  "version": "0.7.2",
  "cveId": "CVE-2022-23639"
 },
 {
  "software": "idna",
  "version": "0.2.3",
  "cveId": "CVE-2024-12224"
 },
 {
  "software": "time",
  "version": "0.1.45",
  "cveId": "CVE-2020-26235"
 },
 {
  "software": "tokio",
  "version": "0.1.22",
  "cveId": "CVE-2021-45710"
 }
]
```

### C8.8 · Hard · CR
**250 questions** in the bank · 100 of them answer "nothing" · groups: `unique_nearest` 150, `no_vuln_reachable` 50, `leaf_version` 25, `absent_package` 25

> What is the shortest DEPENDS_ON path from software 'web-sys' version '0.3.77' to a dependency that has a known vulnerability (excluding the root)?

```cypher
MATCH (s:Software {name: 'web-sys'})-[:HAS_VERSION]->(root:SoftwareVersion {versionName: '0.3.77'}) MATCH (dep:SoftwareVersion)-[:VULNERABLE_TO]->(:Vulnerability) WHERE dep <> root MATCH p = shortestPath((root)-[:DEPENDS_ON*1..6]->(dep)) WITH p, length(p) AS hops WITH min(hops) AS min_hops, collect({path: [n IN nodes(p) | n.versionName], hops: hops}) AS allp UNWIND allp AS row WITH row, min_hops WHERE row.hops = min_hops RETURN DISTINCT row.path AS path, min_hops AS hops ORDER BY hops, path
```

Gold answer (1 row):
```json
[
 {
  "path": [
   "0.3.77",
   "0.3.31",
   "0.1.22"
  ],
  "hops": 2
 }
]
```

### C8.9 · Hard · CR
**250 questions** in the bank · 62 of them answer "nothing" · groups: `mixed_subtrees` 125, `all_zero_subtrees` 63, `leaf_version` 37, `absent_package` 25

> For each direct dependency of software 'rstest_test' version '0.6.0', how many distinct CVEs appear in that dependency's subtree (including the dependency itself)?

```cypher
MATCH (s:Software {name: 'rstest_test'})-[:HAS_VERSION]->(root:SoftwareVersion {versionName: '0.6.0'}) MATCH (root)-[:DEPENDS_ON]->(direct:SoftwareVersion) OPTIONAL MATCH (ds:Software)-[:HAS_VERSION]->(direct) OPTIONAL MATCH (direct)-[:DEPENDS_ON*0..5]->(n:SoftwareVersion)-[:VULNERABLE_TO]->(c:Vulnerability) RETURN ds.name AS software, direct.versionName AS version, count(DISTINCT c) AS cve_cnt ORDER BY software, version
```

Gold answer (4 rows):
```json
[
 {
  "software": "lazy_static",
  "version": "1.5.0",
  "cve_cnt": 5
 },
 {
  "software": "regex",
  "version": "1.7.3",
  "cve_cnt": 2
 },
 {
  "software": "temp_testdir",
  "version": "0.2.3",
  "cve_cnt": 0
 },
 {
  "software": "toml_edit",
  "version": "0.5.0",
  "cve_cnt": 17
 }
]
```

### C8.10 · Hard · CR
**250 questions** in the bank · 100 of them answer "nothing" · groups: `share_vuln_dep` 150, `share_tree_no_vuln` 60, `disjoint_trees` 40

> Which dependencies appear in both full trees of software 'darling' version '0.20.11' and software 'anyhow' version '1.0.98', and have known vulnerabilities?

```cypher
MATCH (s1:Software {name: 'darling'})-[:HAS_VERSION]->(r1:SoftwareVersion {versionName: '0.20.11'}) MATCH (r1)-[:DEPENDS_ON*1..6]->(d1:SoftwareVersion) WHERE d1 <> r1 WITH collect(DISTINCT d1) AS t1 MATCH (s2:Software {name: 'anyhow'})-[:HAS_VERSION]->(r2:SoftwareVersion {versionName: '1.0.98'}) MATCH (r2)-[:DEPENDS_ON*1..6]->(d:SoftwareVersion) WHERE d <> r2 AND d IN t1 AND (d)-[:VULNERABLE_TO]->() WITH DISTINCT d MATCH (d)-[:VULNERABLE_TO]->(c:Vulnerability) OPTIONAL MATCH (ds:Software)-[:HAS_VERSION]->(d) RETURN DISTINCT ds.name AS software, d.versionName AS version, c.cveId AS cveId ORDER BY software, version, cveId
```

Gold answer (4 rows):
```json
[
 {
  "software": "time",
  "version": "0.1.45",
  "cveId": "CVE-2020-26235"
 },
 {
  "software": "tokio",
  "version": "0.1.22",
  "cveId": "CVE-2021-45710"
 },
 {
  "software": "tungstenite",
  "version": "0.17.3",
  "cveId": "CVE-2023-43669"
 },
 {
  "software": "yaml-rust",
  "version": "0.3.5",
  "cveId": "CVE-2018-20993"
 }
]
```

---

## C9: Same package multi-version

### C9.1 · Easy · SR
**50 questions** in the bank · 23 of them answer "nothing" · groups: `has_vuln_version` 27, `no_vuln_version` 16, `absent_package` 7

> Which versions of software 'idna' have known vulnerabilities?

```cypher
MATCH (s:Software {name: 'idna'})-[:HAS_VERSION]->(v:SoftwareVersion)-[:VULNERABLE_TO]->(:Vulnerability) RETURN DISTINCT v.versionName AS version ORDER BY version
```

Gold answer (3 rows):
```json
[
 {
  "version": "0.1.5"
 },
 {
  "version": "0.2.3"
 },
 {
  "version": "0.4.0"
 }
]
```

### C9.2 · Medium · CR
**250 questions** in the bank · 32 of them answer "nothing" · groups: `multi_version_clean` 100, `single_version` 99, `absent_package` 32, `multi_version_with_vuln` 19

> How many distinct CVEs does each version of software 'openssl' have?

```cypher
MATCH (s:Software {name: 'openssl'})-[:HAS_VERSION]->(v:SoftwareVersion) OPTIONAL MATCH (v)-[:VULNERABLE_TO]->(c:Vulnerability) RETURN v.versionName AS version, count(DISTINCT c) AS cve_cnt ORDER BY version
```

Gold answer (4 rows):
```json
[
 {
  "version": "0.10.72",
  "cve_cnt": 0
 },
 {
  "version": "0.7.14",
  "cve_cnt": 2
 },
 {
  "version": "0.9.6c-2.woody.7",
  "cve_cnt": 41
 },
 {
  "version": "3.3.1",
  "cve_cnt": 0
 }
]
```

### C9.3 · Medium · CR
**250 questions** in the bank · 70 of them answer "nothing" · groups: `varying_dep_version` 100, `uniform_dep_version` 80, `unrelated_dep` 40, `absent_dep` 30

> For each version of software 'libpng', what version of software 'zlib' is a direct dependency?

```cypher
MATCH (s:Software {name: 'libpng'})-[:HAS_VERSION]->(v:SoftwareVersion) MATCH (v)-[:DEPENDS_ON]->(d:SoftwareVersion)<-[:HAS_VERSION]-(ds:Software {name: 'zlib'}) RETURN v.versionName AS pkg_version, d.versionName AS dep_version ORDER BY pkg_version, dep_version
```

Gold answer (4 rows):
```json
[
 {
  "pkg_version": "1.6.40",
  "dep_version": "1.2.13"
 },
 {
  "pkg_version": "1.6.40",
  "dep_version": "1.3.1"
 },
 {
  "pkg_version": "1.6.42",
  "dep_version": "1.3.1"
 },
 {
  "pkg_version": "1.6.43",
  "dep_version": "1.3.1"
 }
]
```

### C9.4 · Medium · CR
**250 questions** in the bank · 30 of them answer "nothing" · groups: `real_pair` 110, `cve_elsewhere` 70, `near_miss_cve` 40, `absent_package` 30

> For each version of software 'openssl', does it have vulnerability 'CVE-2002-0659'?

```cypher
MATCH (s:Software {name: 'openssl'})-[:HAS_VERSION]->(v:SoftwareVersion) OPTIONAL MATCH (v)-[:VULNERABLE_TO]->(c:Vulnerability {cveId: 'CVE-2002-0659'}) RETURN v.versionName AS version, count(c) > 0 AS has_cve ORDER BY version
```

Gold answer (4 rows):
```json
[
 {
  "version": "0.10.72",
  "has_cve": false
 },
 {
  "version": "0.7.14",
  "has_cve": false
 },
 {
  "version": "0.9.6c-2.woody.7",
  "has_cve": true
 },
 {
  "version": "3.3.1",
  "has_cve": false
 }
]
```

### C9.5 · Hard · CR
**250 questions** in the bank · 105 of them answer "nothing" · groups: `new_extra_cve` 145, `same_cves` 77, `new_is_leaf` 28

> What CVE IDs appear in the dependency tree of software 'byteorder' version '1.5.0' but not in the tree of version '1.3.4' (including each root)?

```cypher
MATCH (s:Software {name: 'byteorder'})-[:HAS_VERSION]->(old:SoftwareVersion {versionName: '1.3.4'}) OPTIONAL MATCH (old)-[:DEPENDS_ON*0..6]->(:SoftwareVersion)-[:VULNERABLE_TO]->(oldc:Vulnerability) WITH s, collect(DISTINCT oldc.cveId) AS old_cves MATCH (s)-[:HAS_VERSION]->(new:SoftwareVersion {versionName: '1.5.0'}) MATCH (new)-[:DEPENDS_ON*0..6]->(:SoftwareVersion)-[:VULNERABLE_TO]->(c:Vulnerability) WHERE NOT c.cveId IN old_cves RETURN DISTINCT c.cveId AS cveId ORDER BY cveId
```

Gold answer (4 rows):
```json
[
 {
  "cveId": "CVE-2018-20993"
 },
 {
  "cveId": "CVE-2019-25010"
 },
 {
  "cveId": "CVE-2020-25575"
 },
 {
  "cveId": "CVE-2020-26235"
 }
]
```

### C9.6 · Hard · SR
**250 questions** in the bank · 91 of them answer "nothing" · groups: `new_extra_software` 130, `same_software` 61, `new_is_leaf` 30, `old_is_leaf` 29

> Which software products appear in the dependency tree of software 'trybuild' version '1.0.89' but not in the tree of version '1.0.0'?

```cypher
MATCH (s:Software {name: 'trybuild'})-[:HAS_VERSION]->(old:SoftwareVersion {versionName: '1.0.0'}) OPTIONAL MATCH (old)-[:DEPENDS_ON*1..6]->(:SoftwareVersion)<-[:HAS_VERSION]-(os:Software) WITH s, collect(DISTINCT os.name) AS old_names MATCH (s)-[:HAS_VERSION]->(new:SoftwareVersion {versionName: '1.0.89'}) MATCH (new)-[:DEPENDS_ON*1..6]->(:SoftwareVersion)<-[:HAS_VERSION]-(ns:Software) WHERE ns.name <> s.name AND NOT ns.name IN old_names RETURN DISTINCT ns.name AS software ORDER BY software
```

Gold answer (4 rows):
```json
[
 {
  "software": "adler"
 },
 {
  "software": "adler32"
 },
 {
  "software": "basic-toml"
 },
 {
  "software": "ioslice"
 }
]
```

### C9.7 · Hard · CR
**250 questions** in the bank · 110 of them answer "nothing" · groups: `new_extra_indirect_vuln` 140, `no_new_indirect_vuln` 80, `new_is_leaf` 30

> Which indirect vulnerable dependencies appear in the tree of software 'phf_generator' version '0.9.1' but not in the tree of version '0.8.0'?

```cypher
MATCH (s:Software {name: 'phf_generator'})-[:HAS_VERSION]->(old:SoftwareVersion {versionName: '0.8.0'}) OPTIONAL MATCH (old)-[:DEPENDS_ON*1..6]->(o:SoftwareVersion) WITH s, collect(DISTINCT o) AS old_nodes MATCH (s)-[:HAS_VERSION]->(new:SoftwareVersion {versionName: '0.9.1'}) MATCH (new)-[:DEPENDS_ON*2..6]->(n:SoftwareVersion)-[:VULNERABLE_TO]->(c:Vulnerability) WHERE n <> new AND NOT (new)-[:DEPENDS_ON]->(n) AND NOT n IN old_nodes OPTIONAL MATCH (ds:Software)-[:HAS_VERSION]->(n) RETURN DISTINCT ds.name AS software, n.versionName AS version, c.cveId AS cveId ORDER BY software, version, cveId
```

Gold answer (4 rows):
```json
[
 {
  "software": "failure",
  "version": "0.1.8",
  "cveId": "CVE-2019-25010"
 },
 {
  "software": "failure",
  "version": "0.1.8",
  "cveId": "CVE-2020-25575"
 },
 {
  "software": "time",
  "version": "0.1.45",
  "cveId": "CVE-2020-26235"
 },
 {
  "software": "yaml-rust",
  "version": "0.3.5",
  "cveId": "CVE-2018-20993"
 }
]
```
