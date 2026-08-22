"""Template registry for the merged v4 Text2Cypher bank.

v4 exists because the two parallel template designs were merged. The bank is now
keyed on **Lu Xu's sheet numbering** (Google Sheet "Template", 54 rows), because
that sheet is the one both authors design into. The v3 IDs are kept in `v3_id` so
per-template scores from the four finished v3 campaigns can still be joined to
the new bank: the IDs collide semantically across the two designs (v3 `C1.2` asks
about vulnerabilities, sheet `C1.2` asks about existence), so *nothing* may be
compared by ID alone.

What v4 adds on top of a straight copy of the sheet:

1. **Strata.** The sheet has no notion of an answer stratum, so a template taken
   from it straight would have no empty-answer, no false-answer, and no
   absent-entity cases. A bank where every question has a non-empty answer never
   punishes a system that always answers something.
2. **A declared answer shape.** Each template states the columns it returns and
   whether row order is part of the answer. This is what makes a template
   *scoreable* rather than merely hard, and it is checked at build time.
3. **Stratum expectations.** A stratum declares what its cases must produce
   (`exists = false`, empty list, ...). If enumeration drifts, the build fails
   instead of quietly shipping mislabelled cases.

Adding the remaining templates means adding entries here; the builder needs no
change.
"""

from __future__ import annotations

# Answer-shape kinds:
#   "list"   0..n rows, row order irrelevant unless ordered=True
#   "bool"   exactly one row, one boolean column
#   "scalar" exactly one row, one numeric column
#   "table"  0..n rows of several columns
#   "row"    exactly one row of several columns, and COLUMN POSITION IS THE
#            ANSWER (the comparison templates: gold {c1: 1, c2: 4} and a
#            backwards {c1: 4, c2: 1} must not score the same)

# ---------------------------------------------------------------------------
# Shared candidate pools
#
# Several templates draw from the same pools, so they are named once here. Each
# returns the parameter columns a template needs plus `eco`, which the round-
# robin draw uses to keep the 94%-crates.io graph from producing a Rust-only
# benchmark. `{ECO}` is filled in by the builder.
# ---------------------------------------------------------------------------

ROOTS_WITH_DEPS = (
    "MATCH (sw:Software)-[:HAS_VERSION]->(v:SoftwareVersion) WHERE (v)-[:DEPENDS_ON]->() "
    "RETURN sw.name AS pkg, v.versionName AS ver, {ECO} AS eco"
)

LEAF_VERSIONS = (
    "MATCH (sw:Software)-[:HAS_VERSION]->(v:SoftwareVersion) WHERE NOT (v)-[:DEPENDS_ON]->() "
    "RETURN sw.name AS pkg, v.versionName AS ver, {ECO} AS eco"
)

DIRECT_PAIRS = (
    "MATCH (sw:Software)-[:HAS_VERSION]->(v:SoftwareVersion)-[:DEPENDS_ON]->(:SoftwareVersion)"
    "<-[:HAS_VERSION]-(ds:Software) "
    "RETURN DISTINCT sw.name AS pkg, v.versionName AS ver, ds.name AS dep, {ECO} AS eco"
)

# The hard negative for every "directly depends on" question: `dep` sits exactly
# two hops down, so it IS in the dependency tree but is NOT a direct dependency.
# A model that resolves "depends on" to reachability answers these wrong.
GRANDCHILD_NOT_DIRECT = (
    "MATCH (sw:Software)-[:HAS_VERSION]->(v:SoftwareVersion)-[:DEPENDS_ON]->(:SoftwareVersion)"
    "-[:DEPENDS_ON]->(:SoftwareVersion)<-[:HAS_VERSION]-(ds:Software) "
    "WHERE NOT (v)-[:DEPENDS_ON]->(:SoftwareVersion)<-[:HAS_VERSION]-(ds) AND ds.name <> sw.name "
    "RETURN DISTINCT sw.name AS pkg, v.versionName AS ver, ds.name AS dep, {ECO} AS eco"
)

# The easy negative: unrelated within four hops. The LIMITs keep the cross join
# bounded - without them this is a 1,121 x 1,131 product with a path check on
# every cell, which times out.
UNRELATED_PAIRS = (
    "MATCH (sw:Software)-[:HAS_VERSION]->(v:SoftwareVersion) WHERE (v)-[:DEPENDS_ON]->() "
    "WITH sw, v LIMIT 600 "
    "MATCH (ds:Software) WHERE ds.name <> sw.name "
    "AND NOT (v)-[:DEPENDS_ON]->(:SoftwareVersion)<-[:HAS_VERSION]-(ds) "
    "AND NOT (v)-[:DEPENDS_ON*2..4]->(:SoftwareVersion)<-[:HAS_VERSION]-(ds) "
    "WITH sw, v, ds, {ECO} AS eco LIMIT 4000 "
    "RETURN sw.name AS pkg, v.versionName AS ver, ds.name AS dep, eco"
)


# --- C3 pools --------------------------------------------------------------
# The depth-6 convention: schema instruction 4 tells every model "Multi-hop:
# [:DEPENDS_ON*1..6]", and the sheet's C3 golds use the same bound, so gold and
# an obedient model compute the same answer BY PROTOCOL. This matters because
# the graph is deep: 993 of 1,121 roots have dependency nodes beyond 6 hops
# (measured 2026-08-22), so the cap is a real convention, not a formality.

# Roots with at least one 2-hop dependency - the transitive question is about
# something real for these.
ROOTS_WITH_INDIRECT = (
    "MATCH (sw:Software)-[:HAS_VERSION]->(v:SoftwareVersion) "
    "WHERE EXISTS {{ (v)-[:DEPENDS_ON*2..2]->() }} "
    "RETURN sw.name AS pkg, v.versionName AS ver, {ECO} AS eco"
)

# Roots whose dependencies are all one hop deep: transitive == direct. A model
# that always writes *1..6 is not punished; one that writes *2..6 for C3.1 is.
ROOTS_DIRECT_ONLY = (
    "MATCH (sw:Software)-[:HAS_VERSION]->(v:SoftwareVersion) "
    "WHERE (v)-[:DEPENDS_ON]->() AND NOT EXISTS {{ (v)-[:DEPENDS_ON*2..2]->() }} "
    "RETURN sw.name AS pkg, v.versionName AS ver, {ECO} AS eco"
)

# (root, dep) reachable at 2..6 hops but NOT directly - the true stratum that
# separates "depends at any depth" from "depends directly".
INDIRECT_PAIRS_2_6 = (
    "MATCH (sw:Software)-[:HAS_VERSION]->(v:SoftwareVersion) WHERE (v)-[:DEPENDS_ON]->() "
    "CALL (sw, v) {{ "
    "  MATCH (v)-[:DEPENDS_ON*2..6]->(d:SoftwareVersion)<-[:HAS_VERSION]-(ds:Software) "
    "  WHERE ds.name <> sw.name AND NOT (v)-[:DEPENDS_ON]->(:SoftwareVersion)<-[:HAS_VERSION]-(ds) "
    "  RETURN DISTINCT ds LIMIT 8 }} "
    "RETURN sw.name AS pkg, v.versionName AS ver, ds.name AS dep, {ECO} AS eco"
)

# (root, dep) where dep reaches the root but the root does not reach dep: the
# direction trap. Both truths are within-6, i.e. exact under the convention.
# Roots are restricted to versions whose own forward closure stays inside 6
# hops (leaves included): for those the not-reachable check is both cheap and
# EXACT - "not within 6" is "not at all". Dense crates roots made the
# unrestricted version time out, and their falsity would be conventional
# rather than true anyway.
REVERSE_ONLY_PAIRS = (
    "MATCH (sw:Software)-[:HAS_VERSION]->(v:SoftwareVersion) "
    "WHERE (v)<-[:DEPENDS_ON]-() AND NOT EXISTS {{ (v)-[:DEPENDS_ON*7..7]->() }} "
    "CALL (sw, v) {{ "
    "  MATCH (ds:Software)-[:HAS_VERSION]->(dv:SoftwareVersion)-[:DEPENDS_ON*1..6]->(v) "
    "  WHERE ds.name <> sw.name "
    "  AND NOT (v)-[:DEPENDS_ON*1..6]->(:SoftwareVersion)<-[:HAS_VERSION]-(ds) "
    "  RETURN DISTINCT ds LIMIT 8 }} "
    "RETURN sw.name AS pkg, v.versionName AS ver, ds.name AS dep, {ECO} AS eco"
)

# (root, dep) unreachable within 6 hops in the forward direction.
# Same shallow-root restriction as REVERSE_ONLY_PAIRS, same two reasons.
UNREACHABLE_6_PAIRS = (
    "MATCH (sw:Software)-[:HAS_VERSION]->(v:SoftwareVersion) "
    "WHERE (v)-[:DEPENDS_ON]->() AND NOT EXISTS {{ (v)-[:DEPENDS_ON*7..7]->() }} "
    "CALL (sw, v) {{ "
    "  MATCH (ds:Software) WHERE ds.name <> sw.name "
    "  AND NOT (v)-[:DEPENDS_ON*1..6]->(:SoftwareVersion)<-[:HAS_VERSION]-(ds) "
    "  RETURN ds LIMIT 6 }} "
    "RETURN sw.name AS pkg, v.versionName AS ver, ds.name AS dep, {ECO} AS eco"
)

# (root, dep) with exactly ONE reachable target version and exactly ONE
# shortest path to it, at length >= 2. shortestPath() picks an arbitrary
# representative when several shortest paths tie, so a scoreable path question
# must bind only pairs where nothing is left to arbitrate (G2).
UNIQUE_PATH_PAIRS = (
    "MATCH (sw:Software)-[:HAS_VERSION]->(v:SoftwareVersion) WHERE (v)-[:DEPENDS_ON]->() "
    "CALL (sw, v) {{ "
    "  MATCH (v)-[:DEPENDS_ON*1..6]->(t:SoftwareVersion)<-[:HAS_VERSION]-(ds:Software) "
    "  WHERE ds.name <> sw.name "
    "  WITH ds, collect(DISTINCT t) AS ts WHERE size(ts) = 1 "
    "  WITH ds, ts[0] AS t "
    "  MATCH p = allShortestPaths((v)-[:DEPENDS_ON*1..6]->(t)) "
    "  WITH ds, collect(p) AS ps WHERE size(ps) = 1 AND length(ps[0]) >= 2 "
    "  RETURN ds LIMIT 6 }} "
    "RETURN sw.name AS pkg, v.versionName AS ver, ds.name AS dep, {ECO} AS eco"
)

# Same, but the trivial grade: the unique path is the direct edge.
UNIQUE_PATH_DIRECT = (
    "MATCH (sw:Software)-[:HAS_VERSION]->(v:SoftwareVersion) WHERE (v)-[:DEPENDS_ON]->() "
    "CALL (sw, v) {{ "
    "  MATCH (v)-[:DEPENDS_ON*1..6]->(t:SoftwareVersion)<-[:HAS_VERSION]-(ds:Software) "
    "  WHERE ds.name <> sw.name "
    "  WITH ds, collect(DISTINCT t) AS ts WHERE size(ts) = 1 "
    "  WITH ds, ts[0] AS t "
    "  MATCH p = allShortestPaths((v)-[:DEPENDS_ON*1..6]->(t)) "
    "  WITH ds, collect(p) AS ps WHERE size(ps) = 1 AND length(ps[0]) = 1 "
    "  RETURN ds LIMIT 4 }} "
    "RETURN sw.name AS pkg, v.versionName AS ver, ds.name AS dep, {ECO} AS eco"
)

# Roots with a node at distance exactly 2 (2-hop and not also direct).
ROOTS_WITH_DIST2 = (
    "MATCH (sw:Software)-[:HAS_VERSION]->(v:SoftwareVersion) "
    "WHERE EXISTS {{ MATCH (v)-[:DEPENDS_ON*2..2]->(x) WHERE NOT (v)-[:DEPENDS_ON]->(x) }} "
    "RETURN sw.name AS pkg, v.versionName AS ver, {ECO} AS eco"
)

# Roots with dependencies but no node at distance exactly 2: every 2-hop node
# (if any) is also a direct dependency. The smart empty for C3.4.
ROOTS_DEPS_NO_DIST2 = (
    "MATCH (sw:Software)-[:HAS_VERSION]->(v:SoftwareVersion) "
    "WHERE (v)-[:DEPENDS_ON]->() "
    "AND NOT EXISTS {{ MATCH (v)-[:DEPENDS_ON*2..2]->(x) WHERE NOT (v)-[:DEPENDS_ON]->(x) }} "
    "RETURN sw.name AS pkg, v.versionName AS ver, {ECO} AS eco"
)

# Roots with deps but nothing at 2..6 hops beyond the direct set - the C3.6
# analogue of the above, checked over the whole depth window.
ROOTS_NO_INDIRECT_2_6 = (
    "MATCH (sw:Software)-[:HAS_VERSION]->(v:SoftwareVersion) "
    "WHERE (v)-[:DEPENDS_ON]->() "
    "AND NOT EXISTS {{ MATCH (v)-[:DEPENDS_ON*2..6]->(x) WHERE NOT (v)-[:DEPENDS_ON]->(x) }} "
    "RETURN sw.name AS pkg, v.versionName AS ver, {ECO} AS eco"
)

# C3.5 pools: only roots whose ENTIRE walk structure stays within 6 hops, so
# the gold's *1..6 max is the true maximum depth AND the answers spread over
# 1..5 instead of collapsing to a constant 6 (994 of 1,121 roots reach 7 hops;
# for them "always answer 6" would score ~90%).
ROOTS_DEPTH_GE2_SHALLOW = (
    "MATCH (sw:Software)-[:HAS_VERSION]->(v:SoftwareVersion) "
    "WHERE EXISTS {{ (v)-[:DEPENDS_ON*2..2]->() }} AND NOT EXISTS {{ (v)-[:DEPENDS_ON*7..7]->() }} "
    "RETURN sw.name AS pkg, v.versionName AS ver, {ECO} AS eco"
)

ROOTS_DEPTH_EXACTLY1 = (
    "MATCH (sw:Software)-[:HAS_VERSION]->(v:SoftwareVersion) "
    "WHERE (v)-[:DEPENDS_ON]->() AND NOT EXISTS {{ (v)-[:DEPENDS_ON*2..2]->() }} "
    "RETURN sw.name AS pkg, v.versionName AS ver, {ECO} AS eco"
)


# --- C5 pools --------------------------------------------------------------
# Every C5 template takes a PAIR of versions, so each pool returns
# (pkg, ver, dep, dep_ver). Random pairs do not work here: with 1,650 versions
# two of them almost never overlap, which is how the first C5.1 draw in v3 came
# out 89% empty. Each stratum is therefore enumerated by the relation it needs.
#
# **Give every first side a few partners, never `ORDER BY name LIMIT n`.**
# Truncating the outer side of the join takes the alphabetically first n
# versions, and since the answer of a pair template is mostly determined by its
# first side, the bank then re-asks a handful of questions: measured on the
# first C5.5 build, 250 cases came from 38 distinct first sides and produced
# only 10 distinct answers. A correlated `CALL {{ WITH ... LIMIT k }}` covers
# every eligible first side at bounded cost.

PAIRS_SHARE_DIRECT = (
    "MATCH (sw:Software)-[:HAS_VERSION]->(a:SoftwareVersion) WHERE (a)-[:DEPENDS_ON]->() "
    "CALL (sw, a) {{ "
    "  MATCH (a)-[:DEPENDS_ON]->(:SoftwareVersion)<-[:DEPENDS_ON]-(b:SoftwareVersion)"
    "<-[:HAS_VERSION]-(sb:Software) WHERE sb.name <> sw.name "
    "  RETURN DISTINCT sb, b LIMIT 8 }} "
    "RETURN sw.name AS pkg, a.versionName AS ver, sb.name AS dep, b.versionName AS dep_ver, {ECO} AS eco"
)

PAIRS_NO_SHARED_DIRECT = (
    "MATCH (sw:Software)-[:HAS_VERSION]->(a:SoftwareVersion) WHERE (a)-[:DEPENDS_ON]->() "
    "CALL (sw, a) {{ "
    "  MATCH (sb:Software)-[:HAS_VERSION]->(b:SoftwareVersion) "
    "  WHERE (b)-[:DEPENDS_ON]->() AND sb.name <> sw.name "
    "  AND NOT (a)-[:DEPENDS_ON]->(:SoftwareVersion)<-[:DEPENDS_ON]-(b) "
    "  RETURN sb, b LIMIT 6 }} "
    "RETURN sw.name AS pkg, a.versionName AS ver, sb.name AS dep, b.versionName AS dep_ver, {ECO} AS eco"
)

# First side is a leaf: nothing to share, nothing to subtract from.
PAIRS_FIRST_IS_LEAF = (
    "MATCH (sw:Software)-[:HAS_VERSION]->(a:SoftwareVersion) WHERE NOT (a)-[:DEPENDS_ON]->() "
    "CALL (sw, a) {{ "
    "  MATCH (sb:Software)-[:HAS_VERSION]->(b:SoftwareVersion) "
    "  WHERE sb.name <> sw.name AND (b)-[:DEPENDS_ON]->() "
    "  RETURN sb, b LIMIT 4 }} "
    "RETURN sw.name AS pkg, a.versionName AS ver, sb.name AS dep, b.versionName AS dep_ver, {ECO} AS eco"
)

PAIRS_A_HAS_EXTRA_DEP = (
    "MATCH (sw:Software)-[:HAS_VERSION]->(a:SoftwareVersion) WHERE (a)-[:DEPENDS_ON]->() "
    "CALL (sw, a) {{ "
    "  MATCH (sb:Software)-[:HAS_VERSION]->(b:SoftwareVersion) WHERE sb.name <> sw.name "
    "  AND EXISTS {{ MATCH (a)-[:DEPENDS_ON]->(d) WHERE NOT (b)-[:DEPENDS_ON]->(d) }} "
    "  RETURN sb, b LIMIT 6 }} "
    "RETURN sw.name AS pkg, a.versionName AS ver, sb.name AS dep, b.versionName AS dep_ver, {ECO} AS eco"
)

# A's direct dependencies are a subset of B's: the difference is empty even
# though A is not a leaf. The stratum that punishes "just list A's deps".
PAIRS_A_SUBSET_B = (
    "MATCH (sw:Software)-[:HAS_VERSION]->(a:SoftwareVersion) WHERE (a)-[:DEPENDS_ON]->() "
    "CALL (sw, a) {{ "
    "  MATCH (sb:Software)-[:HAS_VERSION]->(b:SoftwareVersion) WHERE sb.name <> sw.name "
    "  AND NOT EXISTS {{ MATCH (a)-[:DEPENDS_ON]->(d) WHERE NOT (b)-[:DEPENDS_ON]->(d) }} "
    "  RETURN sb, b LIMIT 6 }} "
    "RETURN sw.name AS pkg, a.versionName AS ver, sb.name AS dep, b.versionName AS dep_ver, {ECO} AS eco"
)

_DEPCNT = ("MATCH (sw:Software)-[:HAS_VERSION]->(a:SoftwareVersion) "
           "OPTIONAL MATCH (a)-[:DEPENDS_ON]->(d1:SoftwareVersion) "
           "WITH sw, a, count(DISTINCT d1) AS c1 ")

def _cmp_pool(rel, first_nonzero=True):
    return (_DEPCNT + ("WHERE c1 > 0 " if first_nonzero else "") +
            "CALL (sw, a, c1) {{ "
            "  MATCH (sb:Software)-[:HAS_VERSION]->(b:SoftwareVersion) WHERE sb.name <> sw.name "
            "  OPTIONAL MATCH (b)-[:DEPENDS_ON]->(d2:SoftwareVersion) "
            "  WITH sb, b, c1, count(DISTINCT d2) AS c2 "
            "  WHERE " + rel + " "
            "  RETURN sb, b LIMIT 5 }} "
            "RETURN sw.name AS pkg, a.versionName AS ver, sb.name AS dep, b.versionName AS dep_ver, {ECO} AS eco")

CMP_FIRST_MORE = _cmp_pool("c1 > c2")
CMP_SECOND_MORE = _cmp_pool("c1 < c2", first_nonzero=False)
CMP_TIE_NONZERO = _cmp_pool("c1 = c2 AND c1 > 0")
# Two leaves: the tie is 0 vs 0, which is also the "both answers are nothing"
# trap for a model that reads the question as "name the winner".
CMP_TIE_ZERO = (
    "MATCH (sw:Software)-[:HAS_VERSION]->(a:SoftwareVersion) WHERE NOT (a)-[:DEPENDS_ON]->() "
    "CALL (sw, a) {{ "
    "  MATCH (sb:Software)-[:HAS_VERSION]->(b:SoftwareVersion) "
    "  WHERE NOT (b)-[:DEPENDS_ON]->() AND sb.name <> sw.name "
    "  RETURN sb, b LIMIT 4 }} "
    "RETURN sw.name AS pkg, a.versionName AS ver, sb.name AS dep, b.versionName AS dep_ver, {ECO} AS eco"
)

_VULNCNT = ("MATCH (sw:Software)-[:HAS_VERSION]->(a:SoftwareVersion)-[:VULNERABLE_TO]->(x:Vulnerability) "
            "WITH sw, a, count(x) AS n1 ")

def _vuln_pool(rel):
    return (_VULNCNT +
            "MATCH (sb:Software)-[:HAS_VERSION]->(b:SoftwareVersion)-[:VULNERABLE_TO]->(y:Vulnerability) "
            "WHERE sb.name <> sw.name "
            "WITH sw, a, n1, sb, b, count(y) AS n2 "
            "WHERE " + rel + " "
            "WITH sw, a, sb, b, {ECO} AS eco LIMIT 4000 "
            "RETURN sw.name AS pkg, a.versionName AS ver, sb.name AS dep, b.versionName AS dep_ver, eco")

VULN_FIRST_MORE = _vuln_pool("n1 > n2")
VULN_SECOND_MORE = _vuln_pool("n1 < n2")
VULN_TIE = _vuln_pool("n1 = n2")
VULN_ONE_ZERO = (
    "MATCH (sw:Software)-[:HAS_VERSION]->(a:SoftwareVersion) WHERE (a)-[:VULNERABLE_TO]->() "
    "MATCH (sb:Software)-[:HAS_VERSION]->(b:SoftwareVersion) "
    "WHERE sb.name <> sw.name AND NOT (b)-[:VULNERABLE_TO]->() "
    "WITH sw, a, sb, b, {ECO} AS eco LIMIT 4000 "
    "RETURN sw.name AS pkg, a.versionName AS ver, sb.name AS dep, b.versionName AS dep_ver, eco"
)
VULN_NEITHER = (
    "MATCH (sw:Software)-[:HAS_VERSION]->(a:SoftwareVersion) WHERE NOT (a)-[:VULNERABLE_TO]->() "
    "CALL (sw, a) {{ "
    "  MATCH (sb:Software)-[:HAS_VERSION]->(b:SoftwareVersion) "
    "  WHERE sb.name <> sw.name AND NOT (b)-[:VULNERABLE_TO]->() "
    "  RETURN sb, b LIMIT 3 }} "
    "RETURN sw.name AS pkg, a.versionName AS ver, sb.name AS dep, b.versionName AS dep_ver, {ECO} AS eco"
)

# Only 87 version pairs in the whole graph share a CWE - this is C5.6's ceiling.
CWE_PAIRS_SHARED = (
    "MATCH (sw:Software)-[:HAS_VERSION]->(a:SoftwareVersion)-[:VULNERABLE_TO]->(:Vulnerability)"
    "-[:VULNERABILITY_TYPE]->(w:VulnerabilityType) "
    "MATCH (sb:Software)-[:HAS_VERSION]->(b:SoftwareVersion)-[:VULNERABLE_TO]->(:Vulnerability)"
    "-[:VULNERABILITY_TYPE]->(w) WHERE sb.name > sw.name "
    "WITH DISTINCT sw, a, sb, b, {ECO} AS eco "
    "RETURN sw.name AS pkg, a.versionName AS ver, sb.name AS dep, b.versionName AS dep_ver, eco"
)
CWE_PAIRS_NO_SHARED = (
    "MATCH (sw:Software)-[:HAS_VERSION]->(a:SoftwareVersion)-[:VULNERABLE_TO]->(:Vulnerability)"
    "-[:VULNERABILITY_TYPE]->(:VulnerabilityType) "
    "MATCH (sb:Software)-[:HAS_VERSION]->(b:SoftwareVersion)-[:VULNERABLE_TO]->(:Vulnerability)"
    "-[:VULNERABILITY_TYPE]->(:VulnerabilityType) "
    "WHERE sb.name > sw.name AND NOT EXISTS {{ "
    "  MATCH (a)-[:VULNERABLE_TO]->(:Vulnerability)-[:VULNERABILITY_TYPE]->(w2:VulnerabilityType) "
    "  WHERE EXISTS {{ (b)-[:VULNERABLE_TO]->(:Vulnerability)-[:VULNERABILITY_TYPE]->(w2) }} }} "
    "WITH DISTINCT sw, a, sb, b, {ECO} AS eco LIMIT 4000 "
    "RETURN sw.name AS pkg, a.versionName AS ver, sb.name AS dep, b.versionName AS dep_ver, eco"
)
CWE_SECOND_HAS_NONE = (
    "MATCH (sw:Software)-[:HAS_VERSION]->(a:SoftwareVersion)-[:VULNERABLE_TO]->(:Vulnerability)"
    "-[:VULNERABILITY_TYPE]->(:VulnerabilityType) "
    "MATCH (sb:Software)-[:HAS_VERSION]->(b:SoftwareVersion) "
    "WHERE sb.name <> sw.name AND NOT (b)-[:VULNERABLE_TO]->() "
    "WITH DISTINCT sw, a, sb, b, {ECO} AS eco LIMIT 4000 "
    "RETURN sw.name AS pkg, a.versionName AS ver, sb.name AS dep, b.versionName AS dep_ver, eco"
)


TEMPLATES: dict[str, dict] = {
    "C1.1": {
        "family": "C1: Entity / Version Lookup",
        "source": "luxu",          # provenance inside the merged bank
        "v3_id": "C1.1",           # same question in the v3 bank
        "query_type": "SR",
        "difficulty": "Easy",
        "params": ("pkg",),
        "question": "What are the available versions for software '{pkg}'?",
        "cypher": (
            "MATCH (s:Software {{name: '{pkg}'}})-[:HAS_VERSION]->(v:SoftwareVersion) "
            "RETURN v.versionName AS version ORDER BY version"
        ),
        "answer_shape": {"kind": "list", "columns": ["version"], "ordered": False},
        # An absent package is the only way this template can produce an empty
        # answer, and the sheet has no such case. Without it, "list the versions"
        # is a template that is right 100% of the time for any system that
        # returns the versions of *something*.
        "strata": [
            ("multi_version", 0.55, "MATCH (sw:Software)-[:HAS_VERSION]->(v:SoftwareVersion) "
                                    "WITH sw, count(v) AS c WHERE c >= 2 "
                                    "RETURN sw.name AS pkg, {ECO} AS eco", "nonempty"),
            ("single_version", 0.30, "MATCH (sw:Software)-[:HAS_VERSION]->(v:SoftwareVersion) "
                                     "WITH sw, count(v) AS c WHERE c = 1 "
                                     "RETURN sw.name AS pkg, {ECO} AS eco", "nonempty"),
            ("absent_package", 0.15, "SYNTH:absent_package", "empty"),
        ],
    },
    "C1.2": {
        "family": "C1: Entity / Version Lookup",
        "source": "luxu",
        "v3_id": "C1.3",           # NOTE: v3 numbering differs, see module docstring
        "query_type": "SA",
        "difficulty": "Easy",
        "params": ("pkg", "ver"),
        "question": "Does software '{pkg}' version '{ver}' exist in the graph?",
        "cypher": (
            "MATCH (s:Software {{name: '{pkg}'}})-[:HAS_VERSION]->(v:SoftwareVersion {{versionName: '{ver}'}}) "
            "RETURN count(v) > 0 AS exists"
        ),
        "answer_shape": {"kind": "bool", "columns": ["exists"], "ordered": False},
        # v3 built every negative from the single sentinel version "99.99.99",
        # which a model can learn to reject on sight. v4 asks about a version
        # that is one patch bump away from a real one, so a "no" has to come
        # from the graph rather than from the shape of the string.
        "strata": [
            ("exists", 0.55, "MATCH (sw:Software)-[:HAS_VERSION]->(v:SoftwareVersion) "
                             "RETURN sw.name AS pkg, v.versionName AS ver, {ECO} AS eco", "true"),
            ("near_miss_version", 0.27, "SYNTH:near_miss_version", "false"),
            ("absent_package", 0.18, "SYNTH:absent_package_versioned", "false"),
        ],
    },
    # ---------------------------------------------------------------------
    # C2: Direct DEPENDS_ON (out)
    #
    # The sheet lists six C2 rows and strikes three of them out: C2.4 (names of
    # the direct dependencies), C2.5 ("does it have any direct dependencies",
    # a boolean degenerate of C4.1) and C2.6 (the "not directly depend"
    # negation). Only C2.1, C2.2 and C2.3 are live, so only those are built.
    #
    # Every C2 negative comes in two grades. `grandchild_not_direct` is the one
    # that discriminates: the named package IS in the dependency tree, two hops
    # down, so a model that answers "is it in there somewhere" scores false on
    # it while a model that reads "directly" scores true. `unrelated_dep` is the
    # easy grade, kept so the false stratum is not made entirely of trick cases.
    # ---------------------------------------------------------------------
    "C2.1": {
        "family": "C2: Direct DEPENDS_ON (out)",
        "source": "luxu",
        "v3_id": "C2.1",
        "query_type": "CR",
        "difficulty": "Easy",
        "params": ("pkg", "ver"),
        "question": "What are the direct dependencies of software '{pkg}' version '{ver}'?",
        "cypher": (
            "MATCH (s:Software {{name: '{pkg}'}})-[:HAS_VERSION]->(root:SoftwareVersion {{versionName: '{ver}'}}) "
            "MATCH (root)-[:DEPENDS_ON]->(dep:SoftwareVersion) "
            "OPTIONAL MATCH (ds:Software)-[:HAS_VERSION]->(dep) "
            "RETURN DISTINCT ds.name AS software, dep.versionName AS version ORDER BY software, version"
        ),
        "answer_shape": {"kind": "table", "columns": ["software", "version"], "ordered": False},
        "strata": [
            ("has_direct_deps", 0.67, ROOTS_WITH_DEPS, "nonempty"),
            ("leaf_version", 0.22, LEAF_VERSIONS, "empty"),
            ("absent_package", 0.11, "SYNTH:absent_package_versioned", "empty"),
        ],
    },
    "C2.2": {
        "family": "C2: Direct DEPENDS_ON (out)",
        "source": "luxu",
        "v3_id": "C2.2",
        "query_type": "SA",
        "difficulty": "Easy",
        "params": ("pkg", "ver", "dep"),
        "question": "Does software '{pkg}' version '{ver}' directly depend on software '{dep}'?",
        "cypher": (
            "MATCH (s:Software {{name: '{pkg}'}})-[:HAS_VERSION]->(root:SoftwareVersion {{versionName: '{ver}'}}) "
            "MATCH (root)-[:DEPENDS_ON]->(dep:SoftwareVersion)<-[:HAS_VERSION]-(ds:Software {{name: '{dep}'}}) "
            "RETURN count(dep) > 0 AS depends"
        ),
        "answer_shape": {"kind": "bool", "columns": ["depends"], "ordered": False},
        "strata": [
            ("direct_dep", 0.40, DIRECT_PAIRS, "true"),
            ("grandchild_not_direct", 0.35, GRANDCHILD_NOT_DIRECT, "false"),
            ("unrelated_dep", 0.25, UNRELATED_PAIRS, "false"),
        ],
    },
    "C2.3": {
        "family": "C2: Direct DEPENDS_ON (out)",
        "source": "luxu",
        "v3_id": None,
        "query_type": "SR",
        "difficulty": "Easy",
        "params": ("pkg", "ver", "dep"),
        # DEVIATION FROM THE SHEET, and the reason V7 exists. The sheet's C2.3
        # returns a bare `dep.versionName` with neither DISTINCT nor ORDER BY,
        # and calls the result a single answer (SA). Both parts are wrong on
        # this graph: 21 (root, dependency) pairs resolve to more than one
        # version of the same dependency, so the answer is a list, and an
        # unordered list has no defined row order to score against. The gold
        # gets DISTINCT + ORDER BY and the question says "version(s)" so the
        # asked question and the scored answer are the same question.
        "question": "What version(s) of software '{dep}' does software '{pkg}' version '{ver}' directly depend on?",
        "cypher": (
            "MATCH (s:Software {{name: '{pkg}'}})-[:HAS_VERSION]->(root:SoftwareVersion {{versionName: '{ver}'}}) "
            "MATCH (root)-[:DEPENDS_ON]->(dep:SoftwareVersion)<-[:HAS_VERSION]-(ds:Software {{name: '{dep}'}}) "
            "RETURN DISTINCT dep.versionName AS version ORDER BY version"
        ),
        "answer_shape": {"kind": "list", "columns": ["version"], "ordered": False},
        "strata": [
            ("direct_dep", 0.60, DIRECT_PAIRS, "nonempty"),
            ("grandchild_not_direct", 0.25, GRANDCHILD_NOT_DIRECT, "empty"),
            ("unrelated_dep", 0.15, UNRELATED_PAIRS, "empty"),
        ],
    },
    # ---------------------------------------------------------------------
    # C3: Transitive / path
    #
    # The sheet lists seven C3 rows; C3.7 (hops-between, which returns an empty
    # table instead of a number when unreachable) is struck out. C3.1-C3.6 are
    # live. All six inherit the depth-6 convention pinned by schema
    # instruction 4 - see the pool comments above.
    #
    # Three golds deviate from the sheet, all G2 (one defensible reading):
    # C3.4 and C3.6 get `WHERE NOT (root)-[:DEPENDS_ON]->(dep)`, the same
    # encoding the sheet itself uses in C8.7. Without it, a node that is both
    # a direct dependency and 2 hops away would be listed as "exactly 2 hops
    # away" / "indirect (not direct)", which contradicts the question text.
    # C3.1, C3.4 and C3.6 additionally get `dep <> root`: this graph has
    # dependency cycles (serde <-> serde_derive), so a *1..6 walk can return
    # to the root and list the software as its own dependency - the Python
    # BFS cross-check caught the root in 118 of 750 nonempty answers.
    # ---------------------------------------------------------------------
    "C3.1": {
        "family": "C3: Transitive / path",
        "source": "luxu",
        "v3_id": "C3.1",
        "query_type": "CR",
        "difficulty": "Medium",
        "params": ("pkg", "ver"),
        "question": "What are all dependencies of software '{pkg}' version '{ver}', including both direct and indirect?",
        "cypher": (
            "MATCH (s:Software {{name: '{pkg}'}})-[:HAS_VERSION]->(root:SoftwareVersion {{versionName: '{ver}'}}) "
            "MATCH (root)-[:DEPENDS_ON*1..6]->(dep:SoftwareVersion) "
            "WHERE dep <> root "
            "OPTIONAL MATCH (ds:Software)-[:HAS_VERSION]->(dep) "
            "RETURN DISTINCT ds.name AS software, dep.versionName AS version ORDER BY software, version"
        ),
        "answer_shape": {"kind": "table", "columns": ["software", "version"], "ordered": False},
        # direct_only roots keep the *2..6-writers honest: for them the right
        # transitive answer IS the direct answer.
        "strata": [
            ("has_indirect", 0.50, ROOTS_WITH_INDIRECT, "nonempty"),
            ("direct_only", 0.17, ROOTS_DIRECT_ONLY, "nonempty"),
            ("leaf_version", 0.22, LEAF_VERSIONS, "empty"),
            ("absent_package", 0.11, "SYNTH:absent_package_versioned", "empty"),
        ],
    },
    "C3.2": {
        "family": "C3: Transitive / path",
        "source": "luxu",
        "v3_id": "C3.2",
        "query_type": "SA",
        "difficulty": "Medium",
        "params": ("pkg", "ver", "dep"),
        "question": "Does software '{pkg}' version '{ver}' depend on software '{dep}' at any depth?",
        "cypher": (
            "MATCH (s:Software {{name: '{pkg}'}})-[:HAS_VERSION]->(root:SoftwareVersion {{versionName: '{ver}'}}) "
            "MATCH (root)-[:DEPENDS_ON*1..6]->(dep:SoftwareVersion)<-[:HAS_VERSION]-(ds:Software {{name: '{dep}'}}) "
            "RETURN count(dep) > 0 AS depends"
        ),
        "answer_shape": {"kind": "bool", "columns": ["depends"], "ordered": False},
        # indirect_true is the discriminating true: a model that answers the
        # DIRECT question instead says false and is wrong. reverse_only is the
        # direction trap: dep reaches the root, not the other way around.
        "strata": [
            ("indirect_true", 0.35, INDIRECT_PAIRS_2_6, "true"),
            ("direct_true", 0.15, DIRECT_PAIRS, "true"),
            ("reverse_only", 0.30, REVERSE_ONLY_PAIRS, "false"),
            ("unreachable", 0.20, UNREACHABLE_6_PAIRS, "false"),
        ],
    },
    "C3.3": {
        "family": "C3: Transitive / path",
        "source": "luxu",
        "v3_id": "C3.3",
        "query_type": "CR",
        "difficulty": "Hard",
        "params": ("pkg", "ver", "dep"),
        # DEVIATION: ORDER BY path added (V7 - a list answer needs a defined
        # row order). The real G2 work is in the bindings: shortestPath()
        # returns an arbitrary representative when several shortest paths tie,
        # so nonempty cases are bound ONLY to (root, dep) pairs with exactly
        # one reachable target version and exactly one shortest path.
        "question": "What dependency path exists from software '{pkg}' version '{ver}' to software '{dep}'?",
        "cypher": (
            "MATCH (s:Software {{name: '{pkg}'}})-[:HAS_VERSION]->(root:SoftwareVersion {{versionName: '{ver}'}}) "
            "MATCH (ds:Software {{name: '{dep}'}})-[:HAS_VERSION]->(target:SoftwareVersion) "
            "MATCH p = shortestPath((root)-[:DEPENDS_ON*1..6]->(target)) "
            "RETURN [n IN nodes(p) | n.versionName] AS path ORDER BY path"
        ),
        "answer_shape": {"kind": "list", "columns": ["path"], "ordered": False},
        "strata": [
            ("unique_path_2plus", 0.45, UNIQUE_PATH_PAIRS, "nonempty"),
            ("unique_path_direct", 0.15, UNIQUE_PATH_DIRECT, "nonempty"),
            ("no_path", 0.25, UNREACHABLE_6_PAIRS, "empty"),
            ("absent_dep", 0.15, "SYNTH:absent_dep", "empty"),
        ],
    },
    "C3.4": {
        "family": "C3: Transitive / path",
        "source": "luxu",
        "v3_id": None,   # v3 exact-depth template had no not-closer clause; scores must not be joined
        "query_type": "CR",
        "difficulty": "Medium",
        "params": ("pkg", "ver"),
        # DEVIATION from the sheet (G2): "exactly 2 hops away" must not list a
        # node that is also one hop away, so the gold excludes direct
        # dependencies and the question says so. Same encoding as C8.7.
        "question": "What dependencies of software '{pkg}' version '{ver}' are exactly 2 hops away (and not also direct dependencies)?",
        "cypher": (
            "MATCH (s:Software {{name: '{pkg}'}})-[:HAS_VERSION]->(root:SoftwareVersion {{versionName: '{ver}'}}) "
            "MATCH (root)-[:DEPENDS_ON*2..2]->(dep:SoftwareVersion) "
            "WHERE NOT (root)-[:DEPENDS_ON]->(dep) AND dep <> root "
            "OPTIONAL MATCH (ds:Software)-[:HAS_VERSION]->(dep) "
            "RETURN DISTINCT ds.name AS software, dep.versionName AS version ORDER BY software, version"
        ),
        "answer_shape": {"kind": "table", "columns": ["software", "version"], "ordered": False},
        # deps_no_dist2 is the smart empty: the root HAS dependencies, but
        # every 2-hop node is also direct - a model that ignores "exactly"
        # returns the whole neighbourhood and is punished here.
        "strata": [
            ("has_dist2", 0.62, ROOTS_WITH_DIST2, "nonempty"),
            ("deps_no_dist2", 0.16, ROOTS_DEPS_NO_DIST2, "empty"),
            ("leaf_version", 0.11, LEAF_VERSIONS, "empty"),
            ("absent_package", 0.11, "SYNTH:absent_package_versioned", "empty"),
        ],
    },
    "C3.5": {
        "family": "C3: Transitive / path",
        "source": "luxu",
        "v3_id": None,
        "query_type": "SA",
        "difficulty": "Medium",
        "params": ("pkg", "ver"),
        "question": "What is the maximum dependency depth of software '{pkg}' version '{ver}'?",
        "cypher": (
            "MATCH (s:Software {{name: '{pkg}'}})-[:HAS_VERSION]->(root:SoftwareVersion {{versionName: '{ver}'}}) "
            "OPTIONAL MATCH p = (root)-[:DEPENDS_ON*1..6]->(dep:SoftwareVersion) "
            "RETURN coalesce(max(length(p)), 0) AS max_depth"
        ),
        "answer_shape": {"kind": "scalar", "columns": ["max_depth"], "ordered": False},
        # CAPACITY CEILING, by design: only 127 roots have their whole walk
        # structure inside 6 hops. For the other 994 the capped gold would
        # answer a constant 6 - "always say 6" would score ~90% - and the cap
        # would be lying about the true depth on top. So this template binds
        # shallow roots and leaves only, and ships short of the 250 quota.
        "max_quota": 190,
        "strata": [
            ("depth_ge2", 0.30, ROOTS_DEPTH_GE2_SHALLOW, "positive"),
            ("depth_exactly1", 0.37, ROOTS_DEPTH_EXACTLY1, "positive"),
            ("leaf_version", 0.33, LEAF_VERSIONS, "zero"),
        ],
    },
    "C3.6": {
        "family": "C3: Transitive / path",
        "source": "luxu",
        "v3_id": None,
        "query_type": "CR",
        "difficulty": "Medium",
        "params": ("pkg", "ver"),
        # DEVIATION from the sheet (G2): *2..6 alone lists nodes that are ALSO
        # direct dependencies, which "indirect (not direct)" excludes by its
        # own words. The sheet's C8.7 already encodes this correctly; C3.6
        # gets the same WHERE NOT clause.
        "question": "What are the indirect (not direct) dependencies of software '{pkg}' version '{ver}'?",
        "cypher": (
            "MATCH (s:Software {{name: '{pkg}'}})-[:HAS_VERSION]->(root:SoftwareVersion {{versionName: '{ver}'}}) "
            "MATCH (root)-[:DEPENDS_ON*2..6]->(dep:SoftwareVersion) "
            "WHERE NOT (root)-[:DEPENDS_ON]->(dep) AND dep <> root "
            "OPTIONAL MATCH (ds:Software)-[:HAS_VERSION]->(dep) "
            "RETURN DISTINCT ds.name AS software, dep.versionName AS version ORDER BY software, version"
        ),
        "answer_shape": {"kind": "table", "columns": ["software", "version"], "ordered": False},
        "strata": [
            ("has_indirect", 0.62, ROOTS_WITH_DIST2, "nonempty"),
            ("all_deps_direct", 0.16, ROOTS_NO_INDIRECT_2_6, "empty"),
            ("leaf_version", 0.11, LEAF_VERSIONS, "empty"),
            ("absent_package", 0.11, "SYNTH:absent_package_versioned", "empty"),
        ],
    },
    # ---------------------------------------------------------------------
    # C4: Aggregation (dependency)
    #
    # Only C4.3 and C4.4 are live; C4.1 and C4.2 are struck out in the sheet,
    # and the C4.5/C4.6/C4.7 hard templates sit in its separate "backup"
    # section, not adopted.
    #
    # BOTH GOLDS ARE COPIED FROM THE SHEET VERBATIM, INCLUDING `*0..6`, which
    # counts the root itself at depth 0. That is a deliberate hold, not an
    # oversight: the question text and the gold disagree about whether the
    # root belongs in its own answer, and the deviation is queued for the
    # advisor discussion rather than patched here. What it does, measured:
    #
    #   C4.3  the root's own Software is counted in every single case, so the
    #         answer is (products in the closure) + 1 whenever the root's own
    #         product is not otherwise reachable. Affects 100% of cases.
    #   C4.4  the root is a "leaf" only when it has no outgoing DEPENDS_ON, so
    #         `*0..6` and `*1..6` agree on every root WITH dependencies and
    #         differ only on leaf roots, where the gold answers 1 for a
    #         version that has no dependencies at all.
    #
    # Note the collision with schema instruction 4, which tells every model
    # "Multi-hop: [:DEPENDS_ON*1..6]". A model that follows it is off by the
    # root on C4.3, and these are SA templates scored by exact match, so there
    # is no partial credit. The smoke run measures the size of that effect.
    # ---------------------------------------------------------------------
    "C4.3": {
        "family": "C4: Aggregation (dependency)",
        "source": "luxu",
        "v3_id": None,
        "query_type": "SA",
        "difficulty": "Medium",
        "params": ("pkg", "ver"),
        "question": "How many distinct software products are in the dependency closure of software '{pkg}' version '{ver}'?",
        "cypher": (
            "MATCH (s:Software {{name: '{pkg}'}})-[:HAS_VERSION]->(root:SoftwareVersion {{versionName: '{ver}'}}) "
            "OPTIONAL MATCH (root)-[:DEPENDS_ON*0..6]->(dep:SoftwareVersion)<-[:HAS_VERSION]-(ds:Software) "
            "RETURN count(DISTINCT ds) AS cnt"
        ),
        "answer_shape": {"kind": "scalar", "columns": ["cnt"], "ordered": False},
        # An absent package is the only binding that can answer 0 here: the
        # first MATCH fails, the aggregation still returns one row, and the
        # count is 0. Every real root answers at least 1 because of `*0..6`.
        "strata": [
            ("has_indirect", 0.50, ROOTS_WITH_INDIRECT, "positive"),
            ("direct_only", 0.16, ROOTS_DIRECT_ONLY, "positive"),
            ("leaf_version", 0.22, LEAF_VERSIONS, "positive"),
            ("absent_package", 0.12, "SYNTH:absent_package_versioned", "zero"),
        ],
    },
    "C4.4": {
        "family": "C4: Aggregation (dependency)",
        "source": "luxu",
        "v3_id": None,
        "query_type": "SA",
        "difficulty": "Easy",   # the sheet's label; kept, though the query needs
                                # variable length + negation + aggregation
        "params": ("pkg", "ver"),
        "question": "How many leaf dependencies (no further DEPENDS_ON) does the tree of software '{pkg}' version '{ver}' contain?",
        "cypher": (
            "MATCH (s:Software {{name: '{pkg}'}})-[:HAS_VERSION]->(root:SoftwareVersion {{versionName: '{ver}'}}) "
            "MATCH (root)-[:DEPENDS_ON*0..6]->(n:SoftwareVersion) "
            "WHERE NOT (n)-[:DEPENDS_ON]->(:SoftwareVersion) "
            "RETURN count(DISTINCT n) AS cnt"
        ),
        "answer_shape": {"kind": "scalar", "columns": ["cnt"], "ordered": False},
        # leaf_version is the stratum that exposes the `*0..6` reading: a
        # version with no dependencies answers 1, counting itself as the leaf
        # of its own empty tree. Kept as the sheet has it, and kept as its own
        # stratum so the effect is measurable rather than diffused.
        "strata": [
            ("has_indirect", 0.50, ROOTS_WITH_INDIRECT, "positive"),
            ("direct_only", 0.16, ROOTS_DIRECT_ONLY, "positive"),
            ("leaf_version", 0.22, LEAF_VERSIONS, "positive"),
            ("absent_package", 0.12, "SYNTH:absent_package_versioned", "zero"),
        ],
    },
    # ---------------------------------------------------------------------
    # C5: Comparative / set
    #
    # C5.1, C5.2, C5.3, C5.5 and C5.6 are built from the sheet verbatim.
    # **C5.4 IS DELIBERATELY NOT REGISTERED.** Its gold puts two variable-length
    # patterns in one MATCH -
    #     (r1)-[:DEPENDS_ON*1..6]->(d)<-[:DEPENDS_ON*1..6]-(r2)
    # - and Cypher's relationship-uniqueness rule then requires the two paths to
    # share no edge, which silently drops most of the intersection. Re-measured
    # 2026-08-22: click 0.6.1 vs dashmap 5.4.0 returns 490 where the truth is
    # 799, and ab_glyph_rasterizer 0.1.8 vs ab_glyph 0.2.29 returns **1 where
    # the truth is 137**. The collect-then-diff rewrite is also 350-2500x
    # faster (0.01-0.07 s vs 25.1 s). Unlike the C3/C4 holds, no reading of the
    # question makes those numbers right, so the row is left unbuilt pending a
    # decision rather than shipped with wrong gold.
    #
    # C5.2/C5.3 are the reason the answer-format contract exists: they ask
    # "which has more?" and answer with two counts, so column POSITION is the
    # answer. They are the only templates in the bank with `ordered_columns`.
    # ---------------------------------------------------------------------
    "C5.1": {
        "family": "C5: Comparative / set",
        "source": "luxu",
        "v3_id": "C5.1",
        "query_type": "CR",
        "difficulty": "Hard",
        "params": ("pkg", "ver", "dep", "dep_ver"),
        "question": "What common direct dependencies are shared by software '{pkg}' version '{ver}' and software '{dep}' version '{dep_ver}'?",
        # Two DEPENDS_ON patterns in one MATCH here too, but both are SINGLE
        # hop and start from different nodes, so the relationship-uniqueness
        # rule that breaks C5.4 cannot bite: the two edges are distinct by
        # construction.
        "cypher": (
            "MATCH (s1:Software {{name: '{pkg}'}})-[:HAS_VERSION]->(r1:SoftwareVersion {{versionName: '{ver}'}}) "
            "MATCH (s2:Software {{name: '{dep}'}})-[:HAS_VERSION]->(r2:SoftwareVersion {{versionName: '{dep_ver}'}}) "
            "MATCH (r1)-[:DEPENDS_ON]->(d:SoftwareVersion)<-[:DEPENDS_ON]-(r2) "
            "OPTIONAL MATCH (ds:Software)-[:HAS_VERSION]->(d) "
            "RETURN DISTINCT ds.name AS software, d.versionName AS version ORDER BY software, version"
        ),
        "answer_shape": {"kind": "table", "columns": ["software", "version"], "ordered": False},
        "strata": [
            ("shares_direct", 0.66, PAIRS_SHARE_DIRECT, "nonempty"),
            ("no_shared_direct", 0.22, PAIRS_NO_SHARED_DIRECT, "empty"),
            ("first_is_leaf", 0.12, PAIRS_FIRST_IS_LEAF, "empty"),
        ],
    },
    "C5.2": {
        "family": "C5: Comparative / set",
        "source": "luxu",
        "v3_id": "C5.2",
        "query_type": "SA",
        "difficulty": "Medium",
        "params": ("pkg", "ver", "dep", "dep_ver"),
        "question": "Which has more direct dependencies: software '{pkg}' version '{ver}' or software '{dep}' version '{dep_ver}'?",
        "cypher": (
            "MATCH (s1:Software {{name: '{pkg}'}})-[:HAS_VERSION]->(r1:SoftwareVersion {{versionName: '{ver}'}}) "
            "OPTIONAL MATCH (r1)-[:DEPENDS_ON]->(d1:SoftwareVersion) "
            "WITH count(DISTINCT d1) AS c1 "
            "MATCH (s2:Software {{name: '{dep}'}})-[:HAS_VERSION]->(r2:SoftwareVersion {{versionName: '{dep_ver}'}}) "
            "OPTIONAL MATCH (r2)-[:DEPENDS_ON]->(d2:SoftwareVersion) "
            "WITH c1, count(DISTINCT d2) AS c2 RETURN c1, c2"
        ),
        "answer_shape": {"kind": "row", "columns": ["c1", "c2"],
                         "ordered": False, "ordered_columns": True},
        # Ties are the discriminating stratum: a model that answers with a
        # winner's name, or with a CASE expression, has nothing to say here.
        "strata": [
            ("first_more", 0.32, CMP_FIRST_MORE, "first_greater"),
            ("second_more", 0.32, CMP_SECOND_MORE, "second_greater"),
            ("tie_nonzero", 0.20, CMP_TIE_NONZERO, "equal"),
            ("tie_zero", 0.16, CMP_TIE_ZERO, "equal"),
        ],
    },
    "C5.3": {
        "family": "C5: Comparative / set",
        "source": "luxu",
        "v3_id": "C5.3",
        "query_type": "SA",
        "difficulty": "Medium",
        "params": ("pkg", "ver", "dep", "dep_ver"),
        "question": "Which has more known vulnerabilities (CVEs): software '{pkg}' version '{ver}' or software '{dep}' version '{dep_ver}'?",
        "cypher": (
            "MATCH (s1:Software {{name: '{pkg}'}})-[:HAS_VERSION]->(r1:SoftwareVersion {{versionName: '{ver}'}}) "
            "OPTIONAL MATCH (r1)-[:VULNERABLE_TO]->(cve1:Vulnerability) "
            "WITH count(cve1) AS n1 "
            "MATCH (s2:Software {{name: '{dep}'}})-[:HAS_VERSION]->(r2:SoftwareVersion {{versionName: '{dep_ver}'}}) "
            "OPTIONAL MATCH (r2)-[:VULNERABLE_TO]->(cve2:Vulnerability) "
            "WITH n1, count(cve2) AS n2 RETURN n1, n2"
        ),
        "answer_shape": {"kind": "row", "columns": ["n1", "n2"],
                         "ordered": False, "ordered_columns": True},
        # Only 47 versions in the graph carry a CVE, so the both-vulnerable
        # strata are drawn from 1,081 possible pairs. Wide enough for 250, but
        # this is the family where the graph starts to bind.
        "strata": [
            ("both_vuln_first_more", 0.24, VULN_FIRST_MORE, "first_greater"),
            ("both_vuln_second_more", 0.24, VULN_SECOND_MORE, "second_greater"),
            ("both_vuln_tie", 0.20, VULN_TIE, "equal"),
            ("only_first_vuln", 0.18, VULN_ONE_ZERO, "first_greater"),
            ("neither_vuln", 0.14, VULN_NEITHER, "equal"),
        ],
    },
    "C5.5": {
        "family": "C5: Comparative / set",
        "source": "luxu",
        "v3_id": None,
        "query_type": "CR",
        "difficulty": "Hard",
        "params": ("pkg", "ver", "dep", "dep_ver"),
        "question": "Which direct dependencies of software '{pkg}' version '{ver}' are not direct dependencies of software '{dep}' version '{dep_ver}'?",
        "cypher": (
            "MATCH (s1:Software {{name: '{pkg}'}})-[:HAS_VERSION]->(r1:SoftwareVersion {{versionName: '{ver}'}}) "
            "MATCH (r1)-[:DEPENDS_ON]->(d:SoftwareVersion) "
            "OPTIONAL MATCH (ds:Software)-[:HAS_VERSION]->(d) "
            "WITH d, ds "
            "MATCH (s2:Software {{name: '{dep}'}})-[:HAS_VERSION]->(r2:SoftwareVersion {{versionName: '{dep_ver}'}}) "
            "WHERE NOT (r2)-[:DEPENDS_ON]->(d) "
            "RETURN DISTINCT ds.name AS software, d.versionName AS version ORDER BY software, version"
        ),
        "answer_shape": {"kind": "table", "columns": ["software", "version"], "ordered": False},
        # a_subset_b is the stratum that separates set difference from "just
        # list the first side's dependencies": the first side HAS dependencies,
        # and the right answer is still nothing.
        "strata": [
            ("has_extra_dep", 0.66, PAIRS_A_HAS_EXTRA_DEP, "nonempty"),
            ("a_subset_b", 0.22, PAIRS_A_SUBSET_B, "empty"),
            ("first_is_leaf", 0.12, PAIRS_FIRST_IS_LEAF, "empty"),
        ],
    },
    "C5.6": {
        "family": "C5: Comparative / set",
        "source": "luxu",
        "v3_id": None,
        "query_type": "CR",
        "difficulty": "Medium",
        "params": ("pkg", "ver", "dep", "dep_ver"),
        "question": "What CWE IDs are shared by software '{pkg}' version '{ver}' and software '{dep}' version '{dep_ver}'?",
        "cypher": (
            "MATCH (s1:Software {{name: '{pkg}'}})-[:HAS_VERSION]->(r1:SoftwareVersion {{versionName: '{ver}'}}) "
            "MATCH (r1)-[:VULNERABLE_TO]->(:Vulnerability)-[:VULNERABILITY_TYPE]->(w:VulnerabilityType) "
            "MATCH (s2:Software {{name: '{dep}'}})-[:HAS_VERSION]->(r2:SoftwareVersion {{versionName: '{dep_ver}'}}) "
            "MATCH (r2)-[:VULNERABLE_TO]->(:Vulnerability)-[:VULNERABILITY_TYPE]->(w) "
            "RETURN DISTINCT w.cweId AS cweId ORDER BY cweId"
        ),
        "answer_shape": {"kind": "list", "columns": ["cweId"], "ordered": False},
        # CAPACITY CEILING measured on the graph: only **87** version pairs in
        # the entire KG share a CWE. At 0.50 of the quota that caps the template
        # at 174; it ships at 170 to keep a margin for the round-robin draw.
        # This is the graph binding, not the design.
        "max_quota": 170,
        "strata": [
            ("shares_cwe", 0.50, CWE_PAIRS_SHARED, "nonempty"),
            ("no_shared_cwe", 0.30, CWE_PAIRS_NO_SHARED, "empty"),
            ("second_has_no_cwe", 0.20, CWE_SECOND_HAS_NONE, "empty"),
        ],
    },
}
