"""Generate the scaled Text2Cypher gold dataset (v2) from curated template bindings.

For every (template, binding) pair this script fills the NL question and the gold
Cypher, executes the gold Cypher against the frozen SecureChain graph in Neo4j,
and stores the result as `expected_result` — the oracle is *defined* as the gold
query's result on the frozen graph, so regenerating after a re-import keeps
dataset and graph consistent by construction.

Bindings were curated from a graph exploration pass (2026-08-01 snapshot,
11 anchors, see the repo import tooling): each template gets 10 instances mixing
positive, negative, and empty-result parameters across ecosystems and subgraph
sizes. Placeholder values are drawn from inside the imported closures, so
non-anchor packages (e.g. crates reached through click's closure) are valid
targets too.

Usage (repo root, Neo4j running with the graph imported):
  python benchmark/Text2Cypher/t2c_generate_dataset.py
  python benchmark/Text2Cypher/t2c_generate_dataset.py --out t2c_purdue_dataset_v2.json

Then run the benchmark against it:
  set BENCHMARK_DATASET=t2c_purdue_dataset_v2.json
  python benchmark/Text2Cypher/t2c_agent_evaluator.py
"""

from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path

from dotenv import load_dotenv
from neo4j import GraphDatabase

T2C_DIR = Path(__file__).resolve().parent

# ---------------------------------------------------------------------------
# Templates: query_type / difficulty / NL question / gold Cypher.
# Wording and Cypher match t2c_purdue_cypher_templates.md and the original
# 17-case dataset exactly.
# ---------------------------------------------------------------------------

TEMPLATES = {
    "C1.1": {
        "query_type": "SR",
        "difficulty": "Easy",
        "question": "What are the available versions for software '{pkg}'?",
        "cypher": (
            "MATCH (s:Software {{name: '{pkg}'}})-[:HAS_VERSION]->(v:SoftwareVersion) "
            "RETURN v.versionName AS version ORDER BY version"
        ),
    },
    "C1.2": {
        "query_type": "SA",
        "difficulty": "Easy",
        "question": "Does software '{pkg}' version '{ver}' have any known vulnerabilities?",
        "cypher": (
            "MATCH (s:Software {{name: '{pkg}'}})-[:HAS_VERSION]->(root:SoftwareVersion {{versionName: '{ver}'}}) "
            "OPTIONAL MATCH (root)-[:VULNERABLE_TO]->(c:Vulnerability) "
            "RETURN count(c) > 0 AS has_vuln"
        ),
    },
    "C1.3": {
        "query_type": "SA",
        "difficulty": "Easy",
        "question": "Does software '{pkg}' version '{ver}' exist in the graph?",
        "cypher": (
            "MATCH (s:Software {{name: '{pkg}'}})-[:HAS_VERSION]->(v:SoftwareVersion {{versionName: '{ver}'}}) "
            "RETURN count(v) > 0 AS exists"
        ),
    },
    "C2.1": {
        "query_type": "CR",
        "difficulty": "Easy",
        "question": "What are the direct dependencies of software '{pkg}' version '{ver}'?",
        "cypher": (
            "MATCH (s:Software {{name: '{pkg}'}})-[:HAS_VERSION]->(root:SoftwareVersion {{versionName: '{ver}'}}) "
            "MATCH (root)-[:DEPENDS_ON]->(dep:SoftwareVersion) "
            "OPTIONAL MATCH (ds:Software)-[:HAS_VERSION]->(dep) "
            "RETURN DISTINCT ds.name AS software, dep.versionName AS version ORDER BY software, version"
        ),
    },
    "C2.2": {
        "query_type": "SA",
        "difficulty": "Easy",
        "question": "Does software '{pkg}' version '{ver}' directly depend on software '{dep}'?",
        "cypher": (
            "MATCH (s:Software {{name: '{pkg}'}})-[:HAS_VERSION]->(root:SoftwareVersion {{versionName: '{ver}'}}) "
            "MATCH (root)-[:DEPENDS_ON]->(dep:SoftwareVersion)<-[:HAS_VERSION]-(ds:Software {{name: '{dep}'}}) "
            "RETURN count(dep) > 0 AS depends"
        ),
    },
    "C2.3": {
        "query_type": "CR",
        "difficulty": "Easy",
        "question": "What are the CVE IDs of known vulnerabilities affecting software '{pkg}' version '{ver}'?",
        "cypher": (
            "MATCH (s:Software {{name: '{pkg}'}})-[:HAS_VERSION]->(root:SoftwareVersion {{versionName: '{ver}'}}) "
            "MATCH (root)-[:VULNERABLE_TO]->(c:Vulnerability) "
            "RETURN DISTINCT c.cveId AS cveId ORDER BY cveId"
        ),
    },
    "C2.4": {
        "query_type": "CR",
        "difficulty": "Medium",
        "question": "What CWE IDs are associated with vulnerabilities of software '{pkg}' version '{ver}'?",
        "cypher": (
            "MATCH (s:Software {{name: '{pkg}'}})-[:HAS_VERSION]->(root:SoftwareVersion {{versionName: '{ver}'}}) "
            "MATCH (root)-[:VULNERABLE_TO]->(:Vulnerability)-[:VULNERABILITY_TYPE]->(w:VulnerabilityType) "
            "RETURN DISTINCT w.cweId AS cweId ORDER BY cweId"
        ),
    },
    "C3.1": {
        "query_type": "CR",
        "difficulty": "Medium",
        "question": "What are all transitive dependencies of software '{pkg}' version '{ver}'?",
        "cypher": (
            "MATCH (s:Software {{name: '{pkg}'}})-[:HAS_VERSION]->(root:SoftwareVersion {{versionName: '{ver}'}}) "
            "MATCH (root)-[:DEPENDS_ON*1..6]->(dep:SoftwareVersion) "
            "OPTIONAL MATCH (ds:Software)-[:HAS_VERSION]->(dep) "
            "RETURN DISTINCT ds.name AS software, dep.versionName AS version ORDER BY software, version"
        ),
    },
    "C3.2": {
        "query_type": "SA",
        "difficulty": "Medium",
        "question": "Does software '{pkg}' version '{ver}' depend on software '{dep}' at any depth?",
        "cypher": (
            "MATCH (s:Software {{name: '{pkg}'}})-[:HAS_VERSION]->(root:SoftwareVersion {{versionName: '{ver}'}}) "
            "MATCH (root)-[:DEPENDS_ON*1..6]->(dep:SoftwareVersion)<-[:HAS_VERSION]-(ds:Software {{name: '{dep}'}}) "
            "RETURN count(dep) > 0 AS depends"
        ),
    },
    "C3.3": {
        "query_type": "CR",
        "difficulty": "Hard",
        "question": "What dependency path exists from software '{pkg}' version '{ver}' to software '{dep}'?",
        "cypher": (
            "MATCH (s:Software {{name: '{pkg}'}})-[:HAS_VERSION]->(root:SoftwareVersion {{versionName: '{ver}'}}) "
            "MATCH (ds:Software {{name: '{dep}'}})-[:HAS_VERSION]->(target:SoftwareVersion) "
            "MATCH p = shortestPath((root)-[:DEPENDS_ON*1..6]->(target)) "
            "RETURN [n IN nodes(p) | n.versionName] AS path"
        ),
    },
    "C4.1": {
        "query_type": "SA",
        "difficulty": "Medium",
        "question": "How many direct dependencies does software '{pkg}' version '{ver}' have?",
        "cypher": (
            "MATCH (s:Software {{name: '{pkg}'}})-[:HAS_VERSION]->(root:SoftwareVersion {{versionName: '{ver}'}}) "
            "OPTIONAL MATCH (root)-[:DEPENDS_ON]->(dep:SoftwareVersion) "
            "RETURN count(DISTINCT dep) AS cnt"
        ),
    },
    "C4.2": {
        "query_type": "SA",
        "difficulty": "Hard",
        "question": "How many total transitive dependencies does software '{pkg}' version '{ver}' have?",
        "cypher": (
            "MATCH (s:Software {{name: '{pkg}'}})-[:HAS_VERSION]->(root:SoftwareVersion {{versionName: '{ver}'}}) "
            "OPTIONAL MATCH (root)-[:DEPENDS_ON*1..6]->(dep:SoftwareVersion) "
            "RETURN count(DISTINCT dep) AS cnt"
        ),
    },
    "C4.3": {
        "query_type": "SA",
        "difficulty": "Medium",
        "question": "How many vulnerabilities (CVEs) does software '{pkg}' version '{ver}' have?",
        "cypher": (
            "MATCH (s:Software {{name: '{pkg}'}})-[:HAS_VERSION]->(root:SoftwareVersion {{versionName: '{ver}'}}) "
            "OPTIONAL MATCH (root)-[:VULNERABLE_TO]->(c:Vulnerability) "
            "RETURN count(c) AS cnt"
        ),
    },
    "C4.4": {
        "query_type": "SA",
        "difficulty": "Hard",
        "question": "How many distinct CWE types affect software '{pkg}' version '{ver}'?",
        "cypher": (
            "MATCH (s:Software {{name: '{pkg}'}})-[:HAS_VERSION]->(root:SoftwareVersion {{versionName: '{ver}'}}) "
            "OPTIONAL MATCH (root)-[:VULNERABLE_TO]->(:Vulnerability)-[:VULNERABILITY_TYPE]->(w:VulnerabilityType) "
            "RETURN count(DISTINCT w) AS cnt"
        ),
    },
    "C5.1": {
        "query_type": "CR",
        "difficulty": "Hard",
        "question": (
            "What common direct dependencies are shared by software '{pkg}' version '{ver}' "
            "and software '{dep}' version '{dep_ver}'?"
        ),
        "cypher": (
            "MATCH (s1:Software {{name: '{pkg}'}})-[:HAS_VERSION]->(r1:SoftwareVersion {{versionName: '{ver}'}}) "
            "MATCH (s2:Software {{name: '{dep}'}})-[:HAS_VERSION]->(r2:SoftwareVersion {{versionName: '{dep_ver}'}}) "
            "MATCH (r1)-[:DEPENDS_ON]->(d:SoftwareVersion)<-[:DEPENDS_ON]-(r2) "
            "OPTIONAL MATCH (ds:Software)-[:HAS_VERSION]->(d) "
            "RETURN DISTINCT ds.name AS software, d.versionName AS version ORDER BY software, version"
        ),
    },
    "C5.2": {
        "query_type": "SA",
        "difficulty": "Hard",
        # The answer is "which of the two", carried by the position of the counts.
        # Without this the scorer sorts the row and a backwards answer matches.
        "ordered_columns": True,
        "question": (
            "Which has more direct dependencies: software '{pkg}' version '{ver}' "
            "or software '{dep}' version '{dep_ver}'?"
        ),
        "cypher": (
            "MATCH (s1:Software {{name: '{pkg}'}})-[:HAS_VERSION]->(r1:SoftwareVersion {{versionName: '{ver}'}}) "
            "OPTIONAL MATCH (r1)-[:DEPENDS_ON]->(d1:SoftwareVersion) "
            "WITH count(DISTINCT d1) AS c1 "
            "MATCH (s2:Software {{name: '{dep}'}})-[:HAS_VERSION]->(r2:SoftwareVersion {{versionName: '{dep_ver}'}}) "
            "OPTIONAL MATCH (r2)-[:DEPENDS_ON]->(d2:SoftwareVersion) "
            "WITH c1, count(DISTINCT d2) AS c2 "
            "RETURN c1, c2"
        ),
    },
    "C5.3": {
        "query_type": "SA",
        "difficulty": "Hard",
        "ordered_columns": True,
        "question": (
            "Which has more known vulnerabilities (CVEs): software '{pkg}' version '{ver}' "
            "or software '{dep}' version '{dep_ver}'?"
        ),
        "cypher": (
            "MATCH (s1:Software {{name: '{pkg}'}})-[:HAS_VERSION]->(r1:SoftwareVersion {{versionName: '{ver}'}}) "
            "OPTIONAL MATCH (r1)-[:VULNERABLE_TO]->(cve1:Vulnerability) "
            "WITH count(cve1) AS n1 "
            "MATCH (s2:Software {{name: '{dep}'}})-[:HAS_VERSION]->(r2:SoftwareVersion {{versionName: '{dep_ver}'}}) "
            "OPTIONAL MATCH (r2)-[:VULNERABLE_TO]->(cve2:Vulnerability) "
            "WITH n1, count(cve2) AS n2 "
            "RETURN n1, n2"
        ),
    },
    # ----------------------------------------------------------------- v3 -----
    # Added in v3 to exercise structural factors the 17-template set never
    # contained: negation, exact depth, top-N, set difference, and traversal
    # against the direction of DEPENDS_ON. See DIFFICULTY_RUBRIC.md.
    "C2.5": {
        "query_type": "CR",
        "difficulty": "Medium",
        "question": "Which direct dependencies of software '{pkg}' version '{ver}' have no known vulnerabilities?",
        "cypher": (
            "MATCH (s:Software {{name: '{pkg}'}})-[:HAS_VERSION]->(root:SoftwareVersion {{versionName: '{ver}'}}) "
            "MATCH (root)-[:DEPENDS_ON]->(d:SoftwareVersion) "
            "WHERE NOT (d)-[:VULNERABLE_TO]->() "
            "OPTIONAL MATCH (ds:Software)-[:HAS_VERSION]->(d) "
            "RETURN DISTINCT ds.name AS software, d.versionName AS version ORDER BY software, version"
        ),
    },
    "C3.4": {
        "query_type": "CR",
        "difficulty": "Medium",
        "question": "Which dependencies of software '{pkg}' version '{ver}' are at exactly depth {depth}?",
        "cypher": (
            "MATCH (s:Software {{name: '{pkg}'}})-[:HAS_VERSION]->(root:SoftwareVersion {{versionName: '{ver}'}}) "
            "MATCH (root)-[:DEPENDS_ON*{depth}..{depth}]->(d:SoftwareVersion) "
            "OPTIONAL MATCH (ds:Software)-[:HAS_VERSION]->(d) "
            "RETURN DISTINCT ds.name AS software, d.versionName AS version ORDER BY software, version"
        ),
    },
    "C3.5": {
        "query_type": "CR",
        "difficulty": "Medium",
        "question": "Which packages directly depend on software '{pkg}' version '{ver}'?",
        "cypher": (
            "MATCH (s:Software {{name: '{pkg}'}})-[:HAS_VERSION]->(target:SoftwareVersion {{versionName: '{ver}'}}) "
            "MATCH (dependent:SoftwareVersion)-[:DEPENDS_ON]->(target) "
            "OPTIONAL MATCH (ds:Software)-[:HAS_VERSION]->(dependent) "
            "RETURN DISTINCT ds.name AS software, dependent.versionName AS version ORDER BY software, version"
        ),
    },
    "C3.6": {
        "query_type": "CR",
        "difficulty": "Hard",
        "question": "Which packages depend on software '{pkg}' version '{ver}' at any depth?",
        "cypher": (
            "MATCH (s:Software {{name: '{pkg}'}})-[:HAS_VERSION]->(target:SoftwareVersion {{versionName: '{ver}'}}) "
            "MATCH (dependent:SoftwareVersion)-[:DEPENDS_ON*1..6]->(target) "
            "OPTIONAL MATCH (ds:Software)-[:HAS_VERSION]->(dependent) "
            "RETURN DISTINCT ds.name AS software, dependent.versionName AS version ORDER BY software, version"
        ),
    },
    "C4.5": {
        "query_type": "SA",
        "difficulty": "Hard",
        "question": (
            "How many of the transitive dependencies of software '{pkg}' version '{ver}' "
            "have at least one known vulnerability?"
        ),
        "cypher": (
            "MATCH (s:Software {{name: '{pkg}'}})-[:HAS_VERSION]->(root:SoftwareVersion {{versionName: '{ver}'}}) "
            "OPTIONAL MATCH (root)-[:DEPENDS_ON*1..6]->(d:SoftwareVersion) "
            "WHERE (d)-[:VULNERABLE_TO]->() "
            "RETURN count(DISTINCT d) AS cnt"
        ),
    },
    "C4.6": {
        "query_type": "SA",
        "difficulty": "Hard",
        "question": "Which direct dependency of software '{pkg}' version '{ver}' has the most versions in the graph?",
        "cypher": (
            "MATCH (s:Software {{name: '{pkg}'}})-[:HAS_VERSION]->(root:SoftwareVersion {{versionName: '{ver}'}}) "
            "MATCH (root)-[:DEPENDS_ON]->(d:SoftwareVersion)<-[:HAS_VERSION]-(ds:Software) "
            "MATCH (ds)-[:HAS_VERSION]->(av:SoftwareVersion) "
            "WITH ds.name AS software, count(DISTINCT av) AS versions "
            "RETURN software, versions ORDER BY versions DESC, software LIMIT 1"
        ),
    },
    "C4.7": {
        "query_type": "SA",
        "difficulty": "Hard",
        "question": (
            "How many distinct software packages appear in the transitive dependency closure "
            "of software '{pkg}' version '{ver}'?"
        ),
        "cypher": (
            "MATCH (s:Software {{name: '{pkg}'}})-[:HAS_VERSION]->(root:SoftwareVersion {{versionName: '{ver}'}}) "
            "OPTIONAL MATCH (root)-[:DEPENDS_ON*1..6]->(:SoftwareVersion)<-[:HAS_VERSION]-(ds:Software) "
            "RETURN count(DISTINCT ds) AS cnt"
        ),
    },
    "C5.4": {
        "query_type": "CR",
        "difficulty": "Hard",
        "question": (
            "Which direct dependencies does software '{pkg}' version '{ver}' have that "
            "software '{dep}' version '{dep_ver}' does not?"
        ),
        "cypher": (
            "MATCH (s1:Software {{name: '{pkg}'}})-[:HAS_VERSION]->(r1:SoftwareVersion {{versionName: '{ver}'}}) "
            "MATCH (s2:Software {{name: '{dep}'}})-[:HAS_VERSION]->(r2:SoftwareVersion {{versionName: '{dep_ver}'}}) "
            "MATCH (r1)-[:DEPENDS_ON]->(d:SoftwareVersion) "
            "WHERE NOT (r2)-[:DEPENDS_ON]->(d) "
            "OPTIONAL MATCH (ds:Software)-[:HAS_VERSION]->(d) "
            "RETURN DISTINCT ds.name AS software, d.versionName AS version ORDER BY software, version"
        ),
    },
}

# ---------------------------------------------------------------------------
# Curated bindings: 10 instances per template.
# Notation in comments: pos = expected non-trivial/true result,
# neg = expected false, empty = expected empty result set (valid oracle).
# ---------------------------------------------------------------------------

BINDINGS = {
    # Multi-version packages inside the imported closures (anchors mostly have
    # a single imported version, which would make this template degenerate).
    "C1.1": [
        {"pkg": "perl"},          # 19 versions (Debian chain)
        {"pkg": "openssl"},       # Debian chain + conan/crates nodes
        {"pkg": "quickcheck"},    # 9
        {"pkg": "env_logger"},    # 8
        {"pkg": "rand"},          # 7
        {"pkg": "itertools"},     # 7
        {"pkg": "toml"},          # 6
        {"pkg": "base64"},        # 5
        {"pkg": "syn"},           # 5
        {"pkg": "criterion"},     # 7
    ],
    # 5 with CVEs (true) / 5 without (false)
    "C1.2": [
        {"pkg": "django", "ver": "4.2.11"},
        {"pkg": "aiohttp", "ver": "3.8.0"},
        {"pkg": "urllib3", "ver": "1.18.0"},
        {"pkg": "yasm", "ver": "1.3.0"},
        {"pkg": "lock_api", "ver": "0.3.4"},
        {"pkg": "flask", "ver": "2.3.3"},
        {"pkg": "pandas", "ver": "2.0.0"},
        {"pkg": "click", "ver": "0.6.1"},
        {"pkg": "reqwest", "ver": "0.11.23"},
        {"pkg": "tokio", "ver": "1.44.2"},
    ],
    # 7 existing / 3 non-existing (package absent, or version absent)
    "C1.3": [
        {"pkg": "requests", "ver": "2.31.0"},
        {"pkg": "openssl", "ver": "0.9.6c-2.woody.7"},
        {"pkg": "ffmpeg", "ver": "4.2.1"},
        {"pkg": "js-lodash", "ver": "4.17.4"},
        {"pkg": "perl", "ver": "5.10.1-17squeeze6"},
        {"pkg": "hyper", "ver": "0.14.32"},
        {"pkg": "cryptography", "ver": "2.2.0"},
        {"pkg": "requests", "ver": "1.0.0"},      # neg: version not in graph
        {"pkg": "lodash", "ver": "4.17.21"},      # neg: the npm package is absent
        {"pkg": "torch", "ver": "2.0.0"},         # neg: package not in graph
    ],
    # Dependency counts from 0 (empty) to 53
    "C2.1": [
        {"pkg": "django", "ver": "4.2.11"},       # 2
        {"pkg": "flask", "ver": "2.3.3"},         # 6
        {"pkg": "aiohttp", "ver": "3.8.0"},       # 7
        {"pkg": "cryptography", "ver": "2.2.0"},  # 4
        {"pkg": "pandas", "ver": "2.0.0"},        # 4
        {"pkg": "openssl", "ver": "0.9.6c-2.woody.7"},  # 19 (perl chain)
        {"pkg": "ffmpeg", "ver": "4.2.1"},        # 19
        {"pkg": "urllib3", "ver": "1.18.0"},      # empty
        {"pkg": "js-lodash", "ver": "4.17.4"},    # empty
        {"pkg": "reqwest", "ver": "0.11.23"},     # 53
    ],
    # 5 pos / 5 neg; negatives deliberately include transitive-only deps
    # (the strongest distractors for "directly depends on")
    "C2.2": [
        {"pkg": "flask", "ver": "2.3.3", "dep": "click"},
        {"pkg": "aiohttp", "ver": "3.8.0", "dep": "yarl"},
        {"pkg": "pandas", "ver": "2.0.0", "dep": "numpy"},
        {"pkg": "ffmpeg", "ver": "4.2.1", "dep": "openssl"},
        {"pkg": "openssl", "ver": "0.9.6c-2.woody.7", "dep": "perl"},
        {"pkg": "flask", "ver": "2.3.3", "dep": "markupsafe"},        # neg: transitive only
        {"pkg": "aiohttp", "ver": "3.8.0", "dep": "idna"},            # neg: transitive only
        {"pkg": "django", "ver": "4.2.11", "dep": "typing-extensions"},  # neg: transitive only
        {"pkg": "cryptography", "ver": "2.2.0", "dep": "pycparser"},  # neg: transitive only
        {"pkg": "requests", "ver": "2.31.0", "dep": "six"},           # neg: unrelated in-graph pkg
    ],
    # 8 with CVEs / 2 empty
    "C2.3": [
        {"pkg": "django", "ver": "4.2.11"},       # 14
        {"pkg": "aiohttp", "ver": "3.8.0"},       # 10
        {"pkg": "urllib3", "ver": "1.18.0"},      # 10
        {"pkg": "openssl", "ver": "0.9.6c-2.woody.7"},  # 41
        {"pkg": "ffmpeg", "ver": "4.2.1"},        # 10
        {"pkg": "cryptography", "ver": "2.2.0"},  # 6
        {"pkg": "yasm", "ver": "1.3.0"},          # 21
        {"pkg": "perl", "ver": "5.10.1-17squeeze6"},  # 8
        {"pkg": "flask", "ver": "2.3.3"},         # empty
        {"pkg": "pandas", "ver": "2.0.0"},        # empty
    ],
    # 8 with CWEs / 2 empty
    "C2.4": [
        {"pkg": "django", "ver": "4.2.11"},       # 9
        {"pkg": "aiohttp", "ver": "3.8.0"},       # 8
        {"pkg": "urllib3", "ver": "1.18.0"},      # 7
        {"pkg": "openssl", "ver": "0.9.6c-2.woody.7"},  # 10
        {"pkg": "ffmpeg", "ver": "4.2.1"},        # 5
        {"pkg": "cryptography", "ver": "2.2.0"},  # 7
        {"pkg": "requests", "ver": "2.31.0"},     # 1
        {"pkg": "yasm", "ver": "1.3.0"},
        {"pkg": "click", "ver": "0.6.1"},         # empty
        {"pkg": "flask", "ver": "2.3.3"},         # empty
    ],
    # Closure sizes from 0 (empty) to 1557 (stress)
    "C3.1": [
        {"pkg": "flask", "ver": "2.3.3"},         # 8
        {"pkg": "aiohttp", "ver": "3.8.0"},       # 10
        {"pkg": "cryptography", "ver": "2.2.0"},  # 5
        {"pkg": "pandas", "ver": "2.0.0"},        # 5
        {"pkg": "django", "ver": "4.2.11"},       # 3
        {"pkg": "ffmpeg", "ver": "4.2.1"},        # 33
        {"pkg": "openssl", "ver": "0.9.6c-2.woody.7"},  # 19
        {"pkg": "urllib3", "ver": "1.18.0"},      # empty
        {"pkg": "tokio", "ver": "0.1.22"},        # crates closure
        {"pkg": "click", "ver": "0.6.1"},         # 1557 (stress case)
    ],
    # 7 pos (mostly reachable only beyond depth 1) / 3 neg
    "C3.2": [
        {"pkg": "flask", "ver": "2.3.3", "dep": "markupsafe"},        # depth 2
        {"pkg": "django", "ver": "4.2.11", "dep": "typing-extensions"},  # depth 2
        {"pkg": "aiohttp", "ver": "3.8.0", "dep": "propcache"},       # depth 2
        {"pkg": "cryptography", "ver": "2.2.0", "dep": "pycparser"},  # depth 2
        {"pkg": "ffmpeg", "ver": "4.2.1", "dep": "ninja"},            # depth 3
        {"pkg": "click", "ver": "0.6.1", "dep": "adler"},             # depth 4
        {"pkg": "pandas", "ver": "2.0.0", "dep": "numpy"},            # depth 1
        {"pkg": "requests", "ver": "2.31.0", "dep": "six"},           # neg
        {"pkg": "urllib3", "ver": "1.18.0", "dep": "idna"},           # neg: no deps at all
        {"pkg": "openssl", "ver": "0.9.6c-2.woody.7", "dep": "libpng"},  # neg
    ],
    # Path targets at depth 1 / 2 / 3 / 4; one unreachable (empty result)
    "C3.3": [
        {"pkg": "pandas", "ver": "2.0.0", "dep": "numpy"},            # depth 1
        {"pkg": "openssl", "ver": "0.9.6c-2.woody.7", "dep": "perl"},  # depth 1 (many targets)
        {"pkg": "flask", "ver": "2.3.3", "dep": "markupsafe"},        # depth 2
        {"pkg": "django", "ver": "4.2.11", "dep": "typing-extensions"},  # depth 2
        {"pkg": "aiohttp", "ver": "3.8.0", "dep": "propcache"},       # depth 2
        {"pkg": "cryptography", "ver": "2.2.0", "dep": "pycparser"},  # depth 2
        {"pkg": "ffmpeg", "ver": "4.2.1", "dep": "ninja"},            # depth 3
        {"pkg": "click", "ver": "0.6.1", "dep": "aes"},               # depth 3
        {"pkg": "click", "ver": "0.6.1", "dep": "adler"},             # depth 4
        {"pkg": "flask", "ver": "2.3.3", "dep": "numpy"},             # unreachable -> empty
    ],
    # Counts 0..53
    "C4.1": [
        {"pkg": "django", "ver": "4.2.11"},       # 2
        {"pkg": "flask", "ver": "2.3.3"},         # 6
        {"pkg": "aiohttp", "ver": "3.8.0"},       # 7
        {"pkg": "cryptography", "ver": "2.2.0"},  # 4
        {"pkg": "pandas", "ver": "2.0.0"},        # 4
        {"pkg": "openssl", "ver": "0.9.6c-2.woody.7"},  # 19
        {"pkg": "ffmpeg", "ver": "4.2.1"},        # 19
        {"pkg": "urllib3", "ver": "1.18.0"},      # 0
        {"pkg": "reqwest", "ver": "0.11.23"},     # 53
        {"pkg": "hyper", "ver": "0.14.32"},       # 28
    ],
    # Counts 0..1557
    "C4.2": [
        {"pkg": "flask", "ver": "2.3.3"},         # 8
        {"pkg": "aiohttp", "ver": "3.8.0"},       # 10
        {"pkg": "django", "ver": "4.2.11"},       # 3
        {"pkg": "cryptography", "ver": "2.2.0"},  # 5
        {"pkg": "pandas", "ver": "2.0.0"},        # 5
        {"pkg": "ffmpeg", "ver": "4.2.1"},        # 33
        {"pkg": "openssl", "ver": "0.9.6c-2.woody.7"},  # 19
        {"pkg": "urllib3", "ver": "1.18.0"},      # 0
        {"pkg": "tokio", "ver": "1.44.2"},
        {"pkg": "click", "ver": "0.6.1"},         # 1557 (stress case)
    ],
    # Counts 0..41
    "C4.3": [
        {"pkg": "django", "ver": "4.2.11"},       # 14
        {"pkg": "aiohttp", "ver": "3.8.0"},       # 10
        {"pkg": "urllib3", "ver": "1.18.0"},      # 10
        {"pkg": "openssl", "ver": "0.9.6c-2.woody.7"},  # 41
        {"pkg": "ffmpeg", "ver": "4.2.1"},        # 10
        {"pkg": "cryptography", "ver": "2.2.0"},  # 6
        {"pkg": "yasm", "ver": "1.3.0"},          # 21
        {"pkg": "perl", "ver": "5.10.1-17squeeze6"},  # 8
        {"pkg": "flask", "ver": "2.3.3"},         # 0
        {"pkg": "click", "ver": "0.6.1"},         # 0
    ],
    # Counts 0..10
    "C4.4": [
        {"pkg": "django", "ver": "4.2.11"},       # 9
        {"pkg": "aiohttp", "ver": "3.8.0"},       # 8
        {"pkg": "urllib3", "ver": "1.18.0"},      # 7
        {"pkg": "openssl", "ver": "0.9.6c-2.woody.7"},  # 10
        {"pkg": "ffmpeg", "ver": "4.2.1"},        # 5
        {"pkg": "cryptography", "ver": "2.2.0"},  # 7
        {"pkg": "requests", "ver": "2.31.0"},     # 1
        {"pkg": "yasm", "ver": "1.3.0"},
        {"pkg": "pandas", "ver": "2.0.0"},        # 0
        {"pkg": "js-lodash", "ver": "4.17.4"},    # 0
    ],
    # 6 pairs with shared direct deps (from the crates closures) / 4 empty
    "C5.1": [
        {"pkg": "reqwest", "ver": "0.11.23", "dep": "trust-dns-proto", "dep_ver": "0.23.2"},  # 19
        {"pkg": "tokio", "ver": "0.1.22", "dep": "tokio-core", "dep_ver": "0.1.18"},          # 17
        {"pkg": "hyper", "ver": "0.14.32", "dep": "reqwest", "dep_ver": "0.11.23"},           # 14
        {"pkg": "async-io", "ver": "1.13.0", "dep": "smol", "dep_ver": "1.3.0"},              # 10
        {"pkg": "h2", "ver": "0.3.26", "dep": "hyper", "dep_ver": "0.14.32"},                 # 8
        {"pkg": "requests", "ver": "2.31.0", "dep": "cryptography", "dep_ver": "2.2.0"},      # 1
        {"pkg": "flask", "ver": "2.3.3", "dep": "django", "dep_ver": "4.2.11"},               # empty
        {"pkg": "aiohttp", "ver": "3.8.0", "dep": "pandas", "dep_ver": "2.0.0"},              # empty
        {"pkg": "openssl", "ver": "0.9.6c-2.woody.7", "dep": "ffmpeg", "dep_ver": "4.2.1"},   # empty
        {"pkg": "urllib3", "ver": "1.18.0", "dep": "js-lodash", "dep_ver": "4.17.4"},         # empty
    ],
    # Left wins / right wins / ties, including a 0-vs-0 tie
    "C5.2": [
        {"pkg": "flask", "ver": "2.3.3", "dep": "aiohttp", "dep_ver": "3.8.0"},               # 6 vs 7
        {"pkg": "django", "ver": "4.2.11", "dep": "cryptography", "dep_ver": "2.2.0"},        # 2 vs 4
        {"pkg": "openssl", "ver": "0.9.6c-2.woody.7", "dep": "ffmpeg", "dep_ver": "4.2.1"},   # 19 vs 19 tie
        {"pkg": "urllib3", "ver": "1.18.0", "dep": "pandas", "dep_ver": "2.0.0"},             # 0 vs 4
        {"pkg": "reqwest", "ver": "0.11.23", "dep": "hyper", "dep_ver": "0.14.32"},           # 53 vs 28
        {"pkg": "click", "ver": "0.6.1", "dep": "ffmpeg", "dep_ver": "4.2.1"},                # 33 vs 19
        {"pkg": "aiohttp", "ver": "3.8.0", "dep": "cryptography", "dep_ver": "2.2.0"},        # 7 vs 4
        {"pkg": "flask", "ver": "2.3.3", "dep": "pandas", "dep_ver": "2.0.0"},                # 6 vs 4
        {"pkg": "django", "ver": "4.2.11", "dep": "urllib3", "dep_ver": "1.18.0"},            # 2 vs 0
        {"pkg": "requests", "ver": "2.31.0", "dep": "flask", "dep_ver": "2.3.3"},             # 3 vs 6
    ],
    # Left wins / right wins / ties, including a 0-vs-0 tie
    "C5.3": [
        {"pkg": "django", "ver": "4.2.11", "dep": "aiohttp", "dep_ver": "3.8.0"},             # 14 vs 10
        {"pkg": "openssl", "ver": "0.9.6c-2.woody.7", "dep": "yasm", "dep_ver": "1.3.0"},     # 41 vs 21
        {"pkg": "urllib3", "ver": "1.18.0", "dep": "cryptography", "dep_ver": "2.2.0"},       # 10 vs 6
        {"pkg": "flask", "ver": "2.3.3", "dep": "django", "dep_ver": "4.2.11"},               # 0 vs 14
        {"pkg": "ffmpeg", "ver": "4.2.1", "dep": "aiohttp", "dep_ver": "3.8.0"},              # 10 vs 10 tie
        {"pkg": "pandas", "ver": "2.0.0", "dep": "click", "dep_ver": "0.6.1"},                # 0 vs 0 tie
        {"pkg": "requests", "ver": "2.31.0", "dep": "urllib3", "dep_ver": "1.18.0"},          # 1 vs 10
        {"pkg": "openssl", "ver": "0.9.6c-2.woody.7", "dep": "django", "dep_ver": "4.2.11"},  # 41 vs 14
        {"pkg": "yasm", "ver": "1.3.0", "dep": "perl", "dep_ver": "5.10.1-17squeeze6"},       # 21 vs 8
        {"pkg": "cryptography", "ver": "2.2.0", "dep": "requests", "dep_ver": "2.31.0"},      # 6 vs 1
    ],
    # --- v3 seed bindings -----------------------------------------------------
    # Hand-verified against the frozen graph so the new templates are runnable
    # today. These are placeholders: t2c_enumerate_bindings.py replaces every
    # list here with a stratified draw once the v3 quotas are fixed.
    "C2.5": [
        {"pkg": "flask", "ver": "2.3.3"},                 # 6
        {"pkg": "openssl", "ver": "0.9.6c-2.woody.7"},    # 9 of 19 are clean
        {"pkg": "click", "ver": "0.6.1"},                 # 32
        {"pkg": "urllib3", "ver": "1.18.0"},              # empty (no deps)
    ],
    "C3.4": [
        {"pkg": "flask", "ver": "2.3.3", "depth": 2},     # 2
        {"pkg": "ffmpeg", "ver": "4.2.1", "depth": 3},    # 4
        {"pkg": "click", "ver": "0.6.1", "depth": 4},     # 828 (stress)
        {"pkg": "urllib3", "ver": "1.18.0", "depth": 2},  # empty
    ],
    "C3.5": [
        {"pkg": "idna", "ver": "3.10.0"},                 # 3
        {"pkg": "perl", "ver": "5.10.1-17squeeze6"},      # 1
        {"pkg": "requests", "ver": "2.31.0"},             # empty (nothing depends on it)
    ],
    "C3.6": [
        {"pkg": "idna", "ver": "3.10.0"},                 # 4
        {"pkg": "perl", "ver": "5.10.1-17squeeze6"},      # 1
        {"pkg": "requests", "ver": "2.31.0"},             # empty
    ],
    "C4.5": [
        {"pkg": "click", "ver": "0.6.1"},                 # 29
        {"pkg": "ffmpeg", "ver": "4.2.1"},                # 1
        {"pkg": "flask", "ver": "2.3.3"},                 # 0
        {"pkg": "urllib3", "ver": "1.18.0"},              # 0
    ],
    "C4.6": [
        {"pkg": "ffmpeg", "ver": "4.2.1"},                # pkgconf, 2 versions
        {"pkg": "flask", "ver": "2.3.3"},                 # all deps tie at 1 -> weak instance
        {"pkg": "urllib3", "ver": "1.18.0"},              # empty
    ],
    "C4.7": [
        {"pkg": "click", "ver": "0.6.1"},                 # 1065
        {"pkg": "flask", "ver": "2.3.3"},                 # 8
        {"pkg": "urllib3", "ver": "1.18.0"},              # 0
    ],
    "C5.4": [
        {"pkg": "flask", "ver": "2.3.3", "dep": "aiohttp", "dep_ver": "3.8.0"},        # 6
        {"pkg": "reqwest", "ver": "0.11.23", "dep": "hyper", "dep_ver": "0.14.32"},    # 39
        {"pkg": "urllib3", "ver": "1.18.0", "dep": "flask", "dep_ver": "2.3.3"},       # empty
    ],
}


# ---------------------------------------------------------------------------
# Phrasings: four alternates per template, joined by the template's own
# `question` as P1. All five ask for the same thing and therefore share one gold
# Cypher and one expected_result — only the surface form changes.
#
# The axis is fixed and deliberate, so per-phrasing scores mean something:
#   P1 canonical  — the v2 wording, quoted and fully qualified
#   P2 colloquial — how a developer would say it out loud
#   P3 imperative — an instruction rather than a question
#   P4 scenario   — the question embedded in a task the user is doing
#   P5 terse      — keyword shorthand, minimal grammar
#
# NOTE: these 125 strings need a human read-through before the dataset is
# frozen; a phrasing that quietly changes the question changes the gold answer.
# ---------------------------------------------------------------------------

PHRASINGS = {
    "C1.1": [
        "Which versions of {pkg} are there?",
        "List every version of software '{pkg}'.",
        "I need to pin a version of {pkg} — what releases are available?",
        "{pkg} versions?",
    ],
    "C1.2": [
        "Is {pkg} {ver} affected by any known vulnerabilities?",
        "Tell me whether software '{pkg}' version '{ver}' has any known vulnerability.",
        "Security review: does {pkg} {ver} carry any known vulnerabilities?",
        "{pkg} {ver} vulnerable?",
    ],
    "C1.3": [
        "Is {pkg} {ver} present in the database?",
        "Check whether software '{pkg}' version '{ver}' exists.",
        "I referenced {pkg} {ver} in a manifest — is that version actually recorded here?",
        "{pkg} {ver} exists?",
    ],
    "C2.1": [
        "What does {pkg} {ver} depend on directly?",
        "List the direct dependencies of software '{pkg}' version '{ver}'.",
        "I'm auditing {pkg} {ver} — which packages does it pull in directly?",
        "{pkg} {ver} direct dependencies?",
    ],
    "C2.2": [
        "Is {dep} a direct dependency of {pkg} {ver}?",
        "Check whether software '{pkg}' version '{ver}' depends directly on '{dep}'.",
        "We're removing {dep} — does {pkg} {ver} require it directly?",
        "{pkg} {ver} -> {dep} direct?",
    ],
    "C2.3": [
        "Which CVEs affect {pkg} {ver}?",
        "List the CVE identifiers for software '{pkg}' version '{ver}'.",
        "For the security report on {pkg} {ver}, what CVE numbers should I cite?",
        "{pkg} {ver} CVE ids?",
    ],
    "C2.4": [
        "Which CWE categories show up in {pkg} {ver}'s vulnerabilities?",
        "List the CWE identifiers linked to software '{pkg}' version '{ver}'.",
        "I'm classifying weaknesses in {pkg} {ver} — which CWE ids apply?",
        "{pkg} {ver} CWE ids?",
    ],
    "C2.5": [
        "Which of {pkg} {ver}'s direct dependencies are free of known vulnerabilities?",
        "List the direct dependencies of software '{pkg}' version '{ver}' that carry no known vulnerability.",
        "Triaging {pkg} {ver}: which direct dependencies can I rule out as clean?",
        "{pkg} {ver} direct deps without CVEs?",
    ],
    "C3.1": [
        "What does {pkg} {ver} end up depending on, all the way down?",
        "List the full transitive dependency closure of software '{pkg}' version '{ver}'.",
        "I need the complete dependency footprint of {pkg} {ver} for an SBOM.",
        "{pkg} {ver} transitive dependencies?",
    ],
    "C3.2": [
        "Does {pkg} {ver} end up pulling in {dep}, however indirectly?",
        "Check whether '{dep}' appears anywhere in the dependency closure of software '{pkg}' version '{ver}'.",
        "{dep} just had an advisory — is {pkg} {ver} exposed to it at any depth?",
        "{pkg} {ver} -> {dep} at any depth?",
    ],
    "C3.3": [
        "How does {pkg} {ver} end up depending on {dep}?",
        "Show the dependency path from software '{pkg}' version '{ver}' to software '{dep}'.",
        "I need to explain to my team how {pkg} {ver} reaches {dep} — what's the chain?",
        "{pkg} {ver} -> {dep} path?",
    ],
    "C3.4": [
        "What sits exactly {depth} levels below {pkg} {ver} in the dependency tree?",
        "List the dependencies of software '{pkg}' version '{ver}' at exactly depth {depth}.",
        "Mapping the dependency tree of {pkg} {ver} layer by layer — what is on layer {depth}?",
        "{pkg} {ver} deps at depth exactly {depth}?",
    ],
    "C3.5": [
        "What depends on {pkg} {ver} directly?",
        "List the packages that have software '{pkg}' version '{ver}' as a direct dependency.",
        "If I yank {pkg} {ver}, which packages break immediately?",
        "{pkg} {ver} direct dependents?",
    ],
    "C3.6": [
        "What ends up depending on {pkg} {ver}, however indirectly?",
        "List every package whose dependency closure contains software '{pkg}' version '{ver}'.",
        "{pkg} {ver} has an advisory — what is the full blast radius across the graph?",
        "{pkg} {ver} transitive dependents?",
    ],
    "C4.1": [
        "How many packages does {pkg} {ver} depend on directly?",
        "Count the direct dependencies of software '{pkg}' version '{ver}'.",
        "Give me the direct dependency count for {pkg} {ver} for the risk table.",
        "{pkg} {ver} direct dependency count?",
    ],
    "C4.2": [
        "How big is {pkg} {ver}'s full dependency tree?",
        "Count every package in the transitive dependency closure of software '{pkg}' version '{ver}'.",
        "For the SBOM size estimate, how many transitive dependencies does {pkg} {ver} have?",
        "{pkg} {ver} transitive dependency count?",
    ],
    "C4.3": [
        "How many CVEs are filed against {pkg} {ver}?",
        "Count the known vulnerabilities of software '{pkg}' version '{ver}'.",
        "For the risk score of {pkg} {ver}, how many CVEs does it carry?",
        "{pkg} {ver} CVE count?",
    ],
    "C4.4": [
        "How many different weakness categories show up in {pkg} {ver}?",
        "Count the distinct CWE types linked to software '{pkg}' version '{ver}'.",
        "I want to know how varied the weaknesses in {pkg} {ver} are — how many distinct CWE types?",
        "{pkg} {ver} distinct CWE count?",
    ],
    "C4.5": [
        "How many packages in {pkg} {ver}'s dependency tree are vulnerable?",
        "Count the transitive dependencies of software '{pkg}' version '{ver}' that carry at least one known vulnerability.",
        "Assessing inherited risk for {pkg} {ver}: how many of its transitive dependencies are vulnerable?",
        "{pkg} {ver} vulnerable transitive deps count?",
    ],
    "C4.6": [
        "Among {pkg} {ver}'s direct dependencies, which one has the most versions recorded?",
        "Identify the direct dependency of software '{pkg}' version '{ver}' with the highest version count.",
        "Which of {pkg} {ver}'s direct dependencies churns the most releases?",
        "{pkg} {ver} direct dep with most versions?",
    ],
    "C4.7": [
        "How many different packages, not versions, are under {pkg} {ver}?",
        "Count the distinct software packages in the transitive dependency closure of software '{pkg}' version '{ver}'.",
        "For deduplicated SBOM reporting on {pkg} {ver}, how many distinct packages are involved?",
        "{pkg} {ver} distinct packages in closure?",
    ],
    "C5.1": [
        "What do {pkg} {ver} and {dep} {dep_ver} both depend on directly?",
        "List the direct dependencies shared by software '{pkg}' version '{ver}' and software '{dep}' version '{dep_ver}'.",
        "We ship both {pkg} {ver} and {dep} {dep_ver} — which direct dependencies overlap?",
        "{pkg} {ver} & {dep} {dep_ver} shared direct deps?",
    ],
    "C5.2": [
        "Does {pkg} {ver} or {dep} {dep_ver} depend on more packages directly?",
        "Compare the direct dependency counts of software '{pkg}' version '{ver}' and software '{dep}' version '{dep_ver}'.",
        "Picking between {pkg} {ver} and {dep} {dep_ver} — which drags in more direct dependencies?",
        "{pkg} {ver} vs {dep} {dep_ver}: more direct deps?",
    ],
    "C5.3": [
        "Does {pkg} {ver} or {dep} {dep_ver} have more CVEs?",
        "Compare the CVE counts of software '{pkg}' version '{ver}' and software '{dep}' version '{dep_ver}'.",
        "For a risk comparison between {pkg} {ver} and {dep} {dep_ver}, which carries more CVEs?",
        "{pkg} {ver} vs {dep} {dep_ver}: more CVEs?",
    ],
    "C5.4": [
        "What does {pkg} {ver} depend on directly that {dep} {dep_ver} doesn't?",
        "List the direct dependencies of software '{pkg}' version '{ver}' that are absent from software '{dep}' version '{dep_ver}'.",
        "Migrating from {pkg} {ver} to {dep} {dep_ver} — which direct dependencies would I drop?",
        "{pkg} {ver} direct deps not in {dep} {dep_ver}?",
    ],
}


PARAM_KEYS = ("pkg", "ver", "dep", "dep_ver", "depth")


def _slug(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", s).strip("_")


def _case_id(tid: str, b: dict) -> str:
    parts = [tid.replace(".", "_"), _slug(b["pkg"])]
    for key in ("ver", "dep", "dep_ver"):
        if key in b:
            parts.append(_slug(b[key]))
    # C3.4 varies only by depth for a given anchor, so it must be in the id.
    if "depth" in b:
        parts.append(f"d{b['depth']}")
    return "-".join(parts)


GOLD_TIMEOUT = 60.0


def generate(session, bindings_by_tpl: dict[str, list[dict]] | None = None) -> list[dict]:
    """Fill every (template, binding, phrasing) triple and execute the gold query.

    The gold Cypher is executed **once per binding**, not once per case: the five
    phrasings of a binding ask the same question, so they share the query and its
    result. That keeps a 10,000-case build to 2,000 database round-trips.
    """
    source = bindings_by_tpl or BINDINGS
    cases = []
    seen_ids = set()
    for tid, tpl in TEMPLATES.items():
        phrasings = [tpl["question"]] + PHRASINGS.get(tid, [])
        for raw in source[tid]:
            # Sampling metadata (`eco`, `_stratum`) is not a query parameter.
            b = {k: v for k, v in raw.items() if k in PARAM_KEYS}
            cypher = tpl["cypher"].format(**b)
            # A generated query is not the only thing that can fail to
            # terminate: an unlucky binding on a large closure can too, and gold
            # generation must not be the step that hangs a build.
            with session.begin_transaction(timeout=GOLD_TIMEOUT) as tx:
                result = [dict(rec) for rec in tx.run(cypher)]
            base = _case_id(tid, b)
            for pi, phrasing in enumerate(phrasings, start=1):
                cid = f"{base}-p{pi}"
                if cid in seen_ids:
                    raise ValueError(f"duplicate case id: {cid}")
                seen_ids.add(cid)
                case = {
                        "id": cid,
                        "template_id": tid,
                        "phrasing": f"P{pi}",
                        "anchor": {"pkg": b["pkg"], "ver": b.get("ver", "")},
                        "params": b,
                        "stratum": raw.get("_stratum"),
                        "ecosystem": raw.get("eco"),
                        "query_type": tpl["query_type"],
                        "difficulty": tpl["difficulty"],
                        "question": phrasing.format(**b),
                        "cypher_query": cypher,
                        "expected_result": result,
                }
                # Only carried where it applies, so every other case keeps the
                # exact schema — and the exact scoring — it had before.
                if tpl.get("ordered_columns"):
                    case["ordered_columns"] = True
                cases.append(case)
    return cases


def summarize(cases: list[dict]) -> None:
    """Per-template distribution. Aggregates only: at 10k cases a per-case dump
    is 50KB of unreadable output."""
    import statistics
    from collections import Counter

    by_tpl: dict[str, list[dict]] = {}
    for c in cases:
        by_tpl.setdefault(c["template_id"], []).append(c)

    print(f"{'tpl':<7}{'n':>6}{'empty':>7}{'min':>6}{'med':>7}{'max':>7}  {'distinct answers':>17}  difficulty")
    for tid in TEMPLATES:
        group = by_tpl.get(tid, [])
        if not group:
            continue
        sizes = [len(c["expected_result"]) for c in group]
        answers = {json.dumps(c["expected_result"], sort_keys=True) for c in group}
        empty = sum(1 for s in sizes if s == 0)
        print(
            f"{tid:<7}{len(group):>6}{empty:>7}{min(sizes):>6}"
            f"{statistics.median(sizes):>7.0f}{max(sizes):>7}"
            f"{len(answers):>19}  {group[0]['difficulty']}"
        )

    n_empty = sum(1 for c in cases if not c["expected_result"])
    print(f"\ntotal cases: {len(cases)}  (empty-result: {n_empty}, {n_empty/len(cases)*100:.1f}%)")
    for key in ("difficulty", "query_type", "phrasing", "ecosystem"):
        counts = Counter(c.get(key) for c in cases)
        print(f"  by {key:<11}: {dict(sorted(counts.items(), key=lambda kv: -kv[1]))}")


def main() -> int:
    p = argparse.ArgumentParser(description="Generate the scaled Text2Cypher gold dataset.")
    p.add_argument("--out", default="t2c_purdue_dataset_v2.json", help="Output file (in this directory)")
    p.add_argument(
        "--bindings",
        default=None,
        help="Bindings JSON from t2c_enumerate_bindings.py (default: the in-file seed BINDINGS)",
    )
    p.add_argument("--neo4j-uri", default=None)
    p.add_argument("--neo4j-user", default=None)
    p.add_argument("--neo4j-password", default=None)
    p.add_argument("--neo4j-database", default=None)
    args = p.parse_args()

    load_dotenv(T2C_DIR.parent.parent / ".env")
    uri = args.neo4j_uri or os.getenv("NEO4J_URI", "bolt://127.0.0.1:7689")
    user = args.neo4j_user or os.getenv("NEO4J_USERNAME", "neo4j")
    password = args.neo4j_password or os.getenv("NEO4J_PASSWORD", "password")
    database = args.neo4j_database or os.getenv("NEO4J_DATABASE", "neo4j")

    bindings_by_tpl = None
    if args.bindings:
        with (T2C_DIR / args.bindings).open(encoding="utf-8") as f:
            payload = json.load(f)
        bindings_by_tpl = payload["bindings"]
        n = sum(len(v) for v in bindings_by_tpl.values())
        print(f"bindings: {args.bindings} ({n} across {len(bindings_by_tpl)} templates, "
              f"seed {payload.get('seed')})")

    driver = GraphDatabase.driver(uri, auth=(user, password))
    try:
        with driver.session(database=database) as session:
            cases = generate(session, bindings_by_tpl)
    finally:
        driver.close()

    out_path = T2C_DIR / args.out
    with out_path.open("w", encoding="utf-8") as f:
        json.dump(cases, f, indent=2, ensure_ascii=False)
    print(f"wrote {len(cases)} cases to {out_path}\n")
    summarize(cases)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
