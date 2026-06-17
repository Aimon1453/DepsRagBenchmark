# Purdue SecureChain — Cypher templates (neo4j-securechain @ bolt://127.0.0.1:7689)

Placeholders: `{pkg}`, `{ver}`, `{dep}`, `{dep_ver}` → `Software.name` / `SoftwareVersion.versionName`.

Anchor (prepend mentally; each query includes its own `MATCH`):

```cypher
MATCH (s:Software {name: '{pkg}'})-[:HAS_VERSION]->(root:SoftwareVersion {versionName: '{ver}'})
```

Transitive depth: `*1..6` (aligned with import `max-depth=6`).

---

| ID | query_type | topic | cypher_template |
|----|------------|-------|-----------------|
| C1.1 | SR | version | See below |
| C1.2 | SA | vulnerability | See below |
| C1.3 | SA | version | See below |
| C2.1 | CR | dependency | See below |
| C2.2 | SA | dependency | See below |
| C2.3 | CR | vulnerability | See below |
| C2.4 | CR | vulnerability | See below |
| C3.1 | CR | dependency | See below |
| C3.2 | SA | dependency | See below |
| C3.3 | CR | dependency | See below |
| C4.1 | SA | dependency | See below |
| C4.2 | SA | dependency | See below |
| C4.3 | SA | vulnerability | See below |
| C4.4 | SA | vulnerability | See below |
| C5.1 | CR | dependency | See below |
| C5.2 | SA | dependency | See below |
| C5.3 | SA | vulnerability | See below |

---

## C1: Entity / Version Lookup

**C1.1** — available versions

```cypher
MATCH (s:Software {name: '{pkg}'})-[:HAS_VERSION]->(v:SoftwareVersion)
RETURN v.versionName AS version
ORDER BY version
```

**C1.2** — any known vulnerabilities?

```cypher
MATCH (s:Software {name: '{pkg}'})-[:HAS_VERSION]->(root:SoftwareVersion {versionName: '{ver}'})
OPTIONAL MATCH (root)-[:VULNERABLE_TO]->(c:Vulnerability)
RETURN count(c) > 0 AS has_vuln
```

**C1.3** — version exists in graph?

```cypher
MATCH (s:Software {name: '{pkg}'})-[:HAS_VERSION]->(v:SoftwareVersion {versionName: '{ver}'})
RETURN count(v) > 0 AS exists
```

---

## C2: Direct Neighbor Retrieval

**C2.1** — direct dependencies

```cypher
MATCH (s:Software {name: '{pkg}'})-[:HAS_VERSION]->(root:SoftwareVersion {versionName: '{ver}'})
MATCH (root)-[:DEPENDS_ON]->(dep:SoftwareVersion)
OPTIONAL MATCH (ds:Software)-[:HAS_VERSION]->(dep)
RETURN DISTINCT ds.name AS software, dep.versionName AS version
ORDER BY software, version
```

**C2.2** — directly depends on software `{dep}`?

```cypher
MATCH (s:Software {name: '{pkg}'})-[:HAS_VERSION]->(root:SoftwareVersion {versionName: '{ver}'})
MATCH (root)-[:DEPENDS_ON]->(dep:SoftwareVersion)<-[:HAS_VERSION]-(ds:Software {name: '{dep}'})
RETURN count(dep) > 0 AS depends
```

**C2.3** — CVE IDs

```cypher
MATCH (s:Software {name: '{pkg}'})-[:HAS_VERSION]->(root:SoftwareVersion {versionName: '{ver}'})
MATCH (root)-[:VULNERABLE_TO]->(c:Vulnerability)
RETURN DISTINCT c.cveId AS cveId
ORDER BY cveId
```

**C2.4** — CWE IDs

```cypher
MATCH (s:Software {name: '{pkg}'})-[:HAS_VERSION]->(root:SoftwareVersion {versionName: '{ver}'})
MATCH (root)-[:VULNERABLE_TO]->(:Vulnerability)-[:VULNERABILITY_TYPE]->(w:VulnerabilityType)
RETURN DISTINCT w.cweId AS cweId
ORDER BY cweId
```

---

## C3: Transitive Dependency Traversal

**C3.1** — all transitive dependencies

```cypher
MATCH (s:Software {name: '{pkg}'})-[:HAS_VERSION]->(root:SoftwareVersion {versionName: '{ver}'})
MATCH (root)-[:DEPENDS_ON*1..6]->(dep:SoftwareVersion)
OPTIONAL MATCH (ds:Software)-[:HAS_VERSION]->(dep)
RETURN DISTINCT ds.name AS software, dep.versionName AS version
ORDER BY software, version
```

**C3.2** — depends on `{dep}` at any depth?

```cypher
MATCH (s:Software {name: '{pkg}'})-[:HAS_VERSION]->(root:SoftwareVersion {versionName: '{ver}'})
MATCH (root)-[:DEPENDS_ON*1..6]->(dep:SoftwareVersion)<-[:HAS_VERSION]-(ds:Software {name: '{dep}'})
RETURN count(dep) > 0 AS depends
```

**C3.3** — dependency path to software `{dep}`

```cypher
MATCH (s:Software {name: '{pkg}'})-[:HAS_VERSION]->(root:SoftwareVersion {versionName: '{ver}'})
MATCH (ds:Software {name: '{dep}'})-[:HAS_VERSION]->(target:SoftwareVersion)
MATCH p = shortestPath((root)-[:DEPENDS_ON*1..6]->(target))
RETURN [n IN nodes(p) | n.versionName] AS path
```

---

## C4: Aggregation / Statistics

**C4.1** — count direct dependencies

```cypher
MATCH (s:Software {name: '{pkg}'})-[:HAS_VERSION]->(root:SoftwareVersion {versionName: '{ver}'})
OPTIONAL MATCH (root)-[:DEPENDS_ON]->(dep:SoftwareVersion)
RETURN count(DISTINCT dep) AS cnt
```

**C4.2** — count transitive dependencies

```cypher
MATCH (s:Software {name: '{pkg}'})-[:HAS_VERSION]->(root:SoftwareVersion {versionName: '{ver}'})
OPTIONAL MATCH (root)-[:DEPENDS_ON*1..6]->(dep:SoftwareVersion)
RETURN count(DISTINCT dep) AS cnt
```

**C4.3** — count CVEs

```cypher
MATCH (s:Software {name: '{pkg}'})-[:HAS_VERSION]->(root:SoftwareVersion {versionName: '{ver}'})
OPTIONAL MATCH (root)-[:VULNERABLE_TO]->(c:Vulnerability)
RETURN count(c) AS cnt
```

**C4.4** — count distinct CWE types

```cypher
MATCH (s:Software {name: '{pkg}'})-[:HAS_VERSION]->(root:SoftwareVersion {versionName: '{ver}'})
OPTIONAL MATCH (root)-[:VULNERABLE_TO]->(:Vulnerability)-[:VULNERABILITY_TYPE]->(w:VulnerabilityType)
RETURN count(DISTINCT w) AS cnt
```

---

## C5: Comparative / Set Analysis

**C5.1** — common direct dependencies (two anchors)

```cypher
MATCH (s1:Software {name: '{pkg}'})-[:HAS_VERSION]->(r1:SoftwareVersion {versionName: '{ver}'})
MATCH (s2:Software {name: '{dep}'})-[:HAS_VERSION]->(r2:SoftwareVersion {versionName: '{dep_ver}'})
MATCH (r1)-[:DEPENDS_ON]->(d:SoftwareVersion)<-[:DEPENDS_ON]-(r2)
OPTIONAL MATCH (ds:Software)-[:HAS_VERSION]->(d)
RETURN DISTINCT ds.name AS software, d.versionName AS version
ORDER BY software, version
```

**C5.2** — which has more direct dependencies?

```cypher
MATCH (s1:Software {name: '{pkg}'})-[:HAS_VERSION]->(r1:SoftwareVersion {versionName: '{ver}'})
OPTIONAL MATCH (r1)-[:DEPENDS_ON]->(d1:SoftwareVersion)
WITH count(DISTINCT d1) AS c1
MATCH (s2:Software {name: '{dep}'})-[:HAS_VERSION]->(r2:SoftwareVersion {versionName: '{dep_ver}'})
OPTIONAL MATCH (r2)-[:DEPENDS_ON]->(d2:SoftwareVersion)
WITH c1, count(DISTINCT d2) AS c2
RETURN c1, c2
```

**C5.3** — which has more CVEs?

```cypher
MATCH (s1:Software {name: '{pkg}'})-[:HAS_VERSION]->(r1:SoftwareVersion {versionName: '{ver}'})
OPTIONAL MATCH (r1)-[:VULNERABLE_TO]->(cve1:Vulnerability)
WITH count(cve1) AS n1
MATCH (s2:Software {name: '{dep}'})-[:HAS_VERSION]->(r2:SoftwareVersion {versionName: '{dep_ver}'})
OPTIONAL MATCH (r2)-[:VULNERABLE_TO]->(cve2:Vulnerability)
WITH n1, count(cve2) AS n2
RETURN n1, n2
```

---

## Sheet copy-paste column (single-line templates)

Replace `{pkg}` etc. when instantiating; for Google Sheets one cell per row:

```
C1.1	MATCH (s:Software {name: '{pkg}'})-[:HAS_VERSION]->(v:SoftwareVersion) RETURN v.versionName AS version ORDER BY version
C1.2	MATCH (s:Software {name: '{pkg}'})-[:HAS_VERSION]->(root:SoftwareVersion {versionName: '{ver}'}) OPTIONAL MATCH (root)-[:VULNERABLE_TO]->(c:Vulnerability) RETURN count(c) > 0 AS has_vuln
C1.3	MATCH (s:Software {name: '{pkg}'})-[:HAS_VERSION]->(v:SoftwareVersion {versionName: '{ver}'}) RETURN count(v) > 0 AS exists
C2.1	MATCH (s:Software {name: '{pkg}'})-[:HAS_VERSION]->(root:SoftwareVersion {versionName: '{ver}'}) MATCH (root)-[:DEPENDS_ON]->(dep:SoftwareVersion) OPTIONAL MATCH (ds:Software)-[:HAS_VERSION]->(dep) RETURN DISTINCT ds.name AS software, dep.versionName AS version ORDER BY software, version
C2.2	MATCH (s:Software {name: '{pkg}'})-[:HAS_VERSION]->(root:SoftwareVersion {versionName: '{ver}'}) MATCH (root)-[:DEPENDS_ON]->(dep:SoftwareVersion)<-[:HAS_VERSION]-(ds:Software {name: '{dep}'}) RETURN count(dep) > 0 AS depends
C2.3	MATCH (s:Software {name: '{pkg}'})-[:HAS_VERSION]->(root:SoftwareVersion {versionName: '{ver}'}) MATCH (root)-[:VULNERABLE_TO]->(c:Vulnerability) RETURN DISTINCT c.cveId AS cveId ORDER BY cveId
C2.4	MATCH (s:Software {name: '{pkg}'})-[:HAS_VERSION]->(root:SoftwareVersion {versionName: '{ver}'}) MATCH (root)-[:VULNERABLE_TO]->(:Vulnerability)-[:VULNERABILITY_TYPE]->(w:VulnerabilityType) RETURN DISTINCT w.cweId AS cweId ORDER BY cweId
C3.1	MATCH (s:Software {name: '{pkg}'})-[:HAS_VERSION]->(root:SoftwareVersion {versionName: '{ver}'}) MATCH (root)-[:DEPENDS_ON*1..6]->(dep:SoftwareVersion) OPTIONAL MATCH (ds:Software)-[:HAS_VERSION]->(dep) RETURN DISTINCT ds.name AS software, dep.versionName AS version ORDER BY software, version
C3.2	MATCH (s:Software {name: '{pkg}'})-[:HAS_VERSION]->(root:SoftwareVersion {versionName: '{ver}'}) MATCH (root)-[:DEPENDS_ON*1..6]->(dep:SoftwareVersion)<-[:HAS_VERSION]-(ds:Software {name: '{dep}'}) RETURN count(dep) > 0 AS depends
C3.3	MATCH (s:Software {name: '{pkg}'})-[:HAS_VERSION]->(root:SoftwareVersion {versionName: '{ver}'}) MATCH (ds:Software {name: '{dep}'})-[:HAS_VERSION]->(target:SoftwareVersion) MATCH p = shortestPath((root)-[:DEPENDS_ON*1..6]->(target)) RETURN [n IN nodes(p) | n.versionName] AS path
C4.1	MATCH (s:Software {name: '{pkg}'})-[:HAS_VERSION]->(root:SoftwareVersion {versionName: '{ver}'}) OPTIONAL MATCH (root)-[:DEPENDS_ON]->(dep:SoftwareVersion) RETURN count(DISTINCT dep) AS cnt
C4.2	MATCH (s:Software {name: '{pkg}'})-[:HAS_VERSION]->(root:SoftwareVersion {versionName: '{ver}'}) OPTIONAL MATCH (root)-[:DEPENDS_ON*1..6]->(dep:SoftwareVersion) RETURN count(DISTINCT dep) AS cnt
C4.3	MATCH (s:Software {name: '{pkg}'})-[:HAS_VERSION]->(root:SoftwareVersion {versionName: '{ver}'}) OPTIONAL MATCH (root)-[:VULNERABLE_TO]->(c:Vulnerability) RETURN count(c) AS cnt
C4.4	MATCH (s:Software {name: '{pkg}'})-[:HAS_VERSION]->(root:SoftwareVersion {versionName: '{ver}'}) OPTIONAL MATCH (root)-[:VULNERABLE_TO]->(:Vulnerability)-[:VULNERABILITY_TYPE]->(w:VulnerabilityType) RETURN count(DISTINCT w) AS cnt
C5.1	MATCH (s1:Software {name: '{pkg}'})-[:HAS_VERSION]->(r1:SoftwareVersion {versionName: '{ver}'}) MATCH (s2:Software {name: '{dep}'})-[:HAS_VERSION]->(r2:SoftwareVersion {versionName: '{dep_ver}'}) MATCH (r1)-[:DEPENDS_ON]->(d:SoftwareVersion)<-[:DEPENDS_ON]-(r2) OPTIONAL MATCH (ds:Software)-[:HAS_VERSION]->(d) RETURN DISTINCT ds.name AS software, d.versionName AS version ORDER BY software, version
C5.2	MATCH (s1:Software {name: '{pkg}'})-[:HAS_VERSION]->(r1:SoftwareVersion {versionName: '{ver}'}) OPTIONAL MATCH (r1)-[:DEPENDS_ON]->(d1:SoftwareVersion) WITH count(DISTINCT d1) AS c1 MATCH (s2:Software {name: '{dep}'})-[:HAS_VERSION]->(r2:SoftwareVersion {versionName: '{dep_ver}'}) OPTIONAL MATCH (r2)-[:DEPENDS_ON]->(d2:SoftwareVersion) WITH c1, count(DISTINCT d2) AS c2 RETURN c1, c2
C5.3	MATCH (s1:Software {name: '{pkg}'})-[:HAS_VERSION]->(r1:SoftwareVersion {versionName: '{ver}'}) OPTIONAL MATCH (r1)-[:VULNERABLE_TO]->(cve1:Vulnerability) WITH count(cve1) AS n1 MATCH (s2:Software {name: '{dep}'})-[:HAS_VERSION]->(r2:SoftwareVersion {versionName: '{dep_ver}'}) OPTIONAL MATCH (r2)-[:VULNERABLE_TO]->(cve2:Vulnerability) WITH n1, count(cve2) AS n2 RETURN n1, n2
```

**C5.2 / C5.3:** Gold `expected_result` should be the two counts (`c1`/`c2` or `n1`/`n2`), scored by exact match in the evaluator. Do not hardcode the winner (e.g. a package name or `CASE WHEN`) in Cypher—return both counts only.
