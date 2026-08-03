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
        "difficulty": "Medium",
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
        "difficulty": "Easy",
        "question": "How many direct dependencies does software '{pkg}' version '{ver}' have?",
        "cypher": (
            "MATCH (s:Software {{name: '{pkg}'}})-[:HAS_VERSION]->(root:SoftwareVersion {{versionName: '{ver}'}}) "
            "OPTIONAL MATCH (root)-[:DEPENDS_ON]->(dep:SoftwareVersion) "
            "RETURN count(DISTINCT dep) AS cnt"
        ),
    },
    "C4.2": {
        "query_type": "SA",
        "difficulty": "Medium",
        "question": "How many total transitive dependencies does software '{pkg}' version '{ver}' have?",
        "cypher": (
            "MATCH (s:Software {{name: '{pkg}'}})-[:HAS_VERSION]->(root:SoftwareVersion {{versionName: '{ver}'}}) "
            "OPTIONAL MATCH (root)-[:DEPENDS_ON*1..6]->(dep:SoftwareVersion) "
            "RETURN count(DISTINCT dep) AS cnt"
        ),
    },
    "C4.3": {
        "query_type": "SA",
        "difficulty": "Easy",
        "question": "How many vulnerabilities (CVEs) does software '{pkg}' version '{ver}' have?",
        "cypher": (
            "MATCH (s:Software {{name: '{pkg}'}})-[:HAS_VERSION]->(root:SoftwareVersion {{versionName: '{ver}'}}) "
            "OPTIONAL MATCH (root)-[:VULNERABLE_TO]->(c:Vulnerability) "
            "RETURN count(c) AS cnt"
        ),
    },
    "C4.4": {
        "query_type": "SA",
        "difficulty": "Easy",
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
        "difficulty": "Medium",
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
        "difficulty": "Medium",
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
}


def _slug(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", s).strip("_")


def _case_id(tid: str, b: dict) -> str:
    parts = [tid.replace(".", "_"), _slug(b["pkg"])]
    if "ver" in b:
        parts.append(_slug(b["ver"]))
    if "dep" in b:
        parts.append(_slug(b["dep"]))
    if "dep_ver" in b:
        parts.append(_slug(b["dep_ver"]))
    return "-".join(parts)


def generate(session) -> list[dict]:
    cases = []
    seen_ids = set()
    for tid, tpl in TEMPLATES.items():
        for b in BINDINGS[tid]:
            question = tpl["question"].format(**b)
            cypher = tpl["cypher"].format(**b)
            result = [dict(rec) for rec in session.run(cypher)]
            cid = _case_id(tid, b)
            if cid in seen_ids:
                raise ValueError(f"duplicate case id: {cid}")
            seen_ids.add(cid)
            cases.append(
                {
                    "id": cid,
                    "template_id": tid,
                    "anchor": {"pkg": b["pkg"], "ver": b.get("ver", "")},
                    "params": b,
                    "query_type": tpl["query_type"],
                    "difficulty": tpl["difficulty"],
                    "question": question,
                    "cypher_query": cypher,
                    "expected_result": result,
                }
            )
    return cases


def summarize(cases: list[dict]) -> None:
    by_tpl: dict[str, list[dict]] = {}
    for c in cases:
        by_tpl.setdefault(c["template_id"], []).append(c)
    print(f"{'tpl':<6}{'n':>3}  result sizes / SA values")
    for tid in TEMPLATES:
        group = by_tpl.get(tid, [])
        desc = []
        for c in group:
            res = c["expected_result"]
            if c["query_type"] == "SA" and len(res) == 1:
                desc.append(",".join(str(v) for v in res[0].values()))
            else:
                desc.append(str(len(res)))
        print(f"{tid:<6}{len(group):>3}  [{' | '.join(desc)}]")
    n_empty = sum(1 for c in cases if not c["expected_result"])
    print(f"\ntotal cases: {len(cases)}  (empty-result cases: {n_empty})")


def main() -> int:
    p = argparse.ArgumentParser(description="Generate the scaled Text2Cypher gold dataset.")
    p.add_argument("--out", default="t2c_purdue_dataset_v2.json", help="Output file (in this directory)")
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

    driver = GraphDatabase.driver(uri, auth=(user, password))
    try:
        with driver.session(database=database) as session:
            cases = generate(session)
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
