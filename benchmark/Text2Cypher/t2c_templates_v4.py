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


# C5.4 pools. 88% of non-leaf version pairs have intersecting 6-hop closures
# (measured over 40,000 sampled pairs), intersection sizes running 1 / 274 /
# 1,239 for min / median / max, so both strata are wide. The membership test
# uses `x IN t1` against a collected closure rather than a second
# variable-length pattern - the same rewrite the gold needs.
PAIRS_SHARE_TREE = (
    "MATCH (sw:Software)-[:HAS_VERSION]->(a:SoftwareVersion) WHERE (a)-[:DEPENDS_ON]->() "
    "CALL (sw, a) {{ "
    "  MATCH (a)-[:DEPENDS_ON*1..6]->(d:SoftwareVersion) WITH collect(DISTINCT d) AS t1 "
    "  MATCH (sb:Software)-[:HAS_VERSION]->(b:SoftwareVersion) "
    "  WHERE sb.name <> sw.name AND EXISTS {{ MATCH (b)-[:DEPENDS_ON*1..2]->(x) WHERE x IN t1 }} "
    "  RETURN sb, b LIMIT 6 }} "
    "RETURN sw.name AS pkg, a.versionName AS ver, sb.name AS dep, b.versionName AS dep_ver, {ECO} AS eco"
)

# Both sides have dependencies and the two closures still do not meet: the
# stratum that punishes answering with one side's tree.
PAIRS_DISJOINT_TREE = (
    "MATCH (sw:Software)-[:HAS_VERSION]->(a:SoftwareVersion) WHERE (a)-[:DEPENDS_ON]->() "
    "CALL (sw, a) {{ "
    "  MATCH (a)-[:DEPENDS_ON*1..6]->(d:SoftwareVersion) WITH collect(DISTINCT d) AS t1 "
    "  MATCH (sb:Software)-[:HAS_VERSION]->(b:SoftwareVersion) "
    "  WHERE sb.name <> sw.name AND (b)-[:DEPENDS_ON]->() "
    "  AND NOT EXISTS {{ MATCH (b)-[:DEPENDS_ON*1..6]->(x) WHERE x IN t1 }} "
    "  RETURN sb, b LIMIT 4 }} "
    "RETURN sw.name AS pkg, a.versionName AS ver, sb.name AS dep, b.versionName AS dep_ver, {ECO} AS eco"
)


# --- C9 pools --------------------------------------------------------------
# The same-package family. Three templates key on a package alone, one on
# (package, dependency), one on (package, CVE), and the three upgrade-diff
# templates on a (package, ver, ver2) triple drawn from the 1,070 ordered
# same-package version pairs. Every pair pool is first-side correlated
# (the C5.5 sampling lesson): a bare `ORDER BY name LIMIT n` over version
# pairs would draw them all from the alphabetically first packages.

PKGS_WITH_VULN_VERSION = (
    "MATCH (sw:Software)-[:HAS_VERSION]->(v:SoftwareVersion)-[:VULNERABLE_TO]->() "
    "RETURN DISTINCT sw.name AS pkg, {ECO} AS eco"
)

# NAME-LEVEL POOLS, and the reason they have to be. C9's package-keyed
# templates take only `{pkg}`, and the golds resolve it with
# `MATCH (s:Software {name: ...})` - which matches EVERY Software node of that
# name. Five names are shared across ecosystems (openssl x3; brotli, click,
# freetype, idna x2), so a pool that groups by NODE can call `click` clean
# because the crates.io node is clean while the gold, asking by name, also
# sees the vulnerable PyPI one. Grouping by name makes the stratum's claim and
# the gold's behaviour the same claim. Found by verify_c9.py on C9.2/click and
# C9.2/brotli.
PKGS_NO_VULN_VERSION = (
    "MATCH (sw:Software) "
    "WHERE NOT EXISTS {{ MATCH (x:Software)-[:HAS_VERSION]->(:SoftwareVersion)-[:VULNERABLE_TO]->() "
    "                    WHERE x.name = sw.name }} "
    "RETURN DISTINCT sw.name AS pkg, {ECO} AS eco"
)

# C9.2's three grades. multi_version_with_vuln is the discriminating one: the
# per-version counts actually differ, so an answer that reports one number
# for the package is visibly wrong.
PKGS_MULTI_VERSION_VULN = (
    "MATCH (sw:Software)-[:HAS_VERSION]->(v:SoftwareVersion) "
    "WITH sw.name AS pkg, min({ECO}) AS eco, count(DISTINCT v) AS c, "
    "     sum(CASE WHEN EXISTS {{ (v)-[:VULNERABLE_TO]->() }} THEN 1 ELSE 0 END) AS nv "
    "WHERE c >= 2 AND nv > 0 "
    "RETURN pkg, eco"
)

PKGS_MULTI_VERSION_CLEAN = (
    "MATCH (sw:Software)-[:HAS_VERSION]->(v:SoftwareVersion) "
    "WITH sw.name AS pkg, min({ECO}) AS eco, count(DISTINCT v) AS c, "
    "     sum(CASE WHEN EXISTS {{ (v)-[:VULNERABLE_TO]->() }} THEN 1 ELSE 0 END) AS nv "
    "WHERE c >= 2 AND nv = 0 "
    "RETURN pkg, eco"
)

PKGS_SINGLE_VERSION = (
    "MATCH (sw:Software)-[:HAS_VERSION]->(v:SoftwareVersion) "
    "WITH sw.name AS pkg, min({ECO}) AS eco, count(DISTINCT v) AS c WHERE c = 1 "
    "RETURN pkg, eco"
)

# C9.3: (package, dependency) pairs. varying_dep_version is the stratum the
# question exists for - different versions of the package pull different
# versions of the same dependency (393 such pairs).
PKG_DEP_VARYING_VERSION = (
    "MATCH (sw:Software)-[:HAS_VERSION]->(v:SoftwareVersion)-[:DEPENDS_ON]->(d:SoftwareVersion)"
    "<-[:HAS_VERSION]-(ds:Software) WHERE ds.name <> sw.name "
    "WITH sw.name AS pkg, ds.name AS dep, min({ECO}) AS eco, count(DISTINCT d) AS depvers "
    "WHERE depvers >= 2 "
    "RETURN pkg, dep, eco"
)

PKG_DEP_UNIFORM_VERSION = (
    "MATCH (sw:Software)-[:HAS_VERSION]->(v:SoftwareVersion)-[:DEPENDS_ON]->(d:SoftwareVersion)"
    "<-[:HAS_VERSION]-(ds:Software) WHERE ds.name <> sw.name "
    "WITH sw.name AS pkg, ds.name AS dep, min({ECO}) AS eco, count(DISTINCT d) AS depvers "
    "WHERE depvers = 1 "
    "RETURN pkg, dep, eco"
)

# C9.4: (package, CVE) pairs. cve_elsewhere is the trap - the CVE is real but
# affects a different package, so every row is `false` and the answer is
# still a full table. A model that writes MATCH instead of OPTIONAL MATCH
# returns nothing.
PKG_CVE_REAL = (
    "MATCH (sw:Software)-[:HAS_VERSION]->(:SoftwareVersion)-[:VULNERABLE_TO]->(c:Vulnerability) "
    "RETURN DISTINCT sw.name AS pkg, c.cveId AS cve, {ECO} AS eco"
)

PKG_CVE_ELSEWHERE = (
    "MATCH (sw:Software) "
    "WHERE NOT EXISTS {{ MATCH (x:Software)-[:HAS_VERSION]->(:SoftwareVersion)-[:VULNERABLE_TO]->() "
    "                    WHERE x.name = sw.name }} "
    "CALL (sw) {{ MATCH (c:Vulnerability) RETURN c.cveId AS cve LIMIT 3 }} "
    "RETURN DISTINCT sw.name AS pkg, cve, {ECO} AS eco"
)

# --- C9.5-9.7 upgrade-diff pair pools --------------------------------------
# `a` is the OLD version (parameter `ver`), `b` the NEW one (`ver2`).

# C9.5: the new tree carries a CVE the old tree did not. *0..6 on both sides,
# because the question says "(including each root)".
PAIRS_NEW_EXTRA_CVE = (
    "MATCH (sw:Software)-[:HAS_VERSION]->(a:SoftwareVersion) "
    "CALL (sw, a) {{ "
    "  OPTIONAL MATCH (a)-[:DEPENDS_ON*0..6]->(:SoftwareVersion)-[:VULNERABLE_TO]->(oc:Vulnerability) "
    "  WITH collect(DISTINCT oc.cveId) AS old_cves "
    "  MATCH (sw)-[:HAS_VERSION]->(b:SoftwareVersion) WHERE b.versionName > a.versionName "
    "  AND EXISTS {{ MATCH (b)-[:DEPENDS_ON*0..6]->(:SoftwareVersion)-[:VULNERABLE_TO]->(c:Vulnerability) "
    "                WHERE NOT c.cveId IN old_cves }} "
    "  RETURN b LIMIT 3 }} "
    "RETURN sw.name AS pkg, a.versionName AS ver, b.versionName AS ver2, {ECO} AS eco"
)

# Both trees carry exactly the same CVE set (often both empty): the answer is
# nothing, and a model that just lists the new tree's CVEs scores 0.
PAIRS_SAME_CVES = (
    "MATCH (sw:Software)-[:HAS_VERSION]->(a:SoftwareVersion) "
    "CALL (sw, a) {{ "
    "  OPTIONAL MATCH (a)-[:DEPENDS_ON*0..6]->(:SoftwareVersion)-[:VULNERABLE_TO]->(oc:Vulnerability) "
    "  WITH collect(DISTINCT oc.cveId) AS old_cves "
    "  MATCH (sw)-[:HAS_VERSION]->(b:SoftwareVersion) WHERE b.versionName > a.versionName "
    "  AND NOT EXISTS {{ MATCH (b)-[:DEPENDS_ON*0..6]->(:SoftwareVersion)-[:VULNERABLE_TO]->(c:Vulnerability) "
    "                    WHERE NOT c.cveId IN old_cves }} "
    "  RETURN b LIMIT 3 }} "
    "RETURN sw.name AS pkg, a.versionName AS ver, b.versionName AS ver2, {ECO} AS eco"
)

# C9.6: the new tree contains a software product the old tree did not
# (excluding the package itself - see the gold's deviation note).
PAIRS_NEW_EXTRA_SOFTWARE = (
    "MATCH (sw:Software)-[:HAS_VERSION]->(a:SoftwareVersion) "
    "CALL (sw, a) {{ "
    "  OPTIONAL MATCH (a)-[:DEPENDS_ON*1..6]->(:SoftwareVersion)<-[:HAS_VERSION]-(os:Software) "
    "  WITH collect(DISTINCT os.name) AS old_names "
    "  MATCH (sw)-[:HAS_VERSION]->(b:SoftwareVersion) WHERE b.versionName > a.versionName "
    "  AND EXISTS {{ MATCH (b)-[:DEPENDS_ON*1..6]->(:SoftwareVersion)<-[:HAS_VERSION]-(ns:Software) "
    "                WHERE ns.name <> sw.name AND NOT ns.name IN old_names }} "
    "  RETURN b LIMIT 3 }} "
    "RETURN sw.name AS pkg, a.versionName AS ver, b.versionName AS ver2, {ECO} AS eco"
)

PAIRS_SAME_SOFTWARE = (
    "MATCH (sw:Software)-[:HAS_VERSION]->(a:SoftwareVersion) WHERE (a)-[:DEPENDS_ON]->() "
    "CALL (sw, a) {{ "
    "  OPTIONAL MATCH (a)-[:DEPENDS_ON*1..6]->(:SoftwareVersion)<-[:HAS_VERSION]-(os:Software) "
    "  WITH collect(DISTINCT os.name) AS old_names "
    "  MATCH (sw)-[:HAS_VERSION]->(b:SoftwareVersion) "
    "  WHERE b.versionName > a.versionName AND (b)-[:DEPENDS_ON]->() "
    "  AND NOT EXISTS {{ MATCH (b)-[:DEPENDS_ON*1..6]->(:SoftwareVersion)<-[:HAS_VERSION]-(ns:Software) "
    "                    WHERE ns.name <> sw.name AND NOT ns.name IN old_names }} "
    "  RETURN b LIMIT 3 }} "
    "RETURN sw.name AS pkg, a.versionName AS ver, b.versionName AS ver2, {ECO} AS eco"
)

# The new version is a leaf: it has no tree at all, so nothing can be new.
# The sharp empty for C9.6/C9.7 - the OLD version may have a large tree, so
# a model that answers with the wrong side's closure is punished.
PAIRS_NEW_IS_LEAF = (
    "MATCH (sw:Software)-[:HAS_VERSION]->(a:SoftwareVersion) WHERE (a)-[:DEPENDS_ON]->() "
    "CALL (sw, a) {{ "
    "  MATCH (sw)-[:HAS_VERSION]->(b:SoftwareVersion) "
    "  WHERE b.versionName > a.versionName AND NOT (b)-[:DEPENDS_ON]->() "
    "  RETURN b LIMIT 3 }} "
    "RETURN sw.name AS pkg, a.versionName AS ver, b.versionName AS ver2, {ECO} AS eco"
)

# The old version is a leaf: every product in the new tree is new. The
# mirror-image stratum, and non-empty.
PAIRS_OLD_IS_LEAF = (
    "MATCH (sw:Software)-[:HAS_VERSION]->(a:SoftwareVersion) WHERE NOT (a)-[:DEPENDS_ON]->() "
    "CALL (sw, a) {{ "
    "  MATCH (sw)-[:HAS_VERSION]->(b:SoftwareVersion) "
    "  WHERE b.versionName > a.versionName AND (b)-[:DEPENDS_ON]->() "
    "  RETURN b LIMIT 3 }} "
    "RETURN sw.name AS pkg, a.versionName AS ver, b.versionName AS ver2, {ECO} AS eco"
)

# C9.7: a vulnerable INDIRECT dependency (2..6 and not direct) present in the
# new tree and absent from the old one.
PAIRS_NEW_EXTRA_INDIRECT_VULN = (
    "MATCH (sw:Software)-[:HAS_VERSION]->(a:SoftwareVersion) "
    "CALL (sw, a) {{ "
    "  OPTIONAL MATCH (a)-[:DEPENDS_ON*1..6]->(o:SoftwareVersion) "
    "  WITH collect(DISTINCT o) AS old_nodes "
    "  MATCH (sw)-[:HAS_VERSION]->(b:SoftwareVersion) WHERE b.versionName > a.versionName "
    "  AND EXISTS {{ MATCH (b)-[:DEPENDS_ON*2..6]->(n:SoftwareVersion)-[:VULNERABLE_TO]->() "
    "                WHERE n <> b AND NOT (b)-[:DEPENDS_ON]->(n) AND NOT n IN old_nodes }} "
    "  RETURN b LIMIT 3 }} "
    "RETURN sw.name AS pkg, a.versionName AS ver, b.versionName AS ver2, {ECO} AS eco"
)

# Both versions have trees, and the new one adds no vulnerable indirect
# dependency - the discriminating empty for C9.7.
PAIRS_NO_NEW_INDIRECT_VULN = (
    "MATCH (sw:Software)-[:HAS_VERSION]->(a:SoftwareVersion) WHERE (a)-[:DEPENDS_ON]->() "
    "CALL (sw, a) {{ "
    "  OPTIONAL MATCH (a)-[:DEPENDS_ON*1..6]->(o:SoftwareVersion) "
    "  WITH collect(DISTINCT o) AS old_nodes "
    "  MATCH (sw)-[:HAS_VERSION]->(b:SoftwareVersion) "
    "  WHERE b.versionName > a.versionName AND (b)-[:DEPENDS_ON]->() "
    "  AND NOT EXISTS {{ MATCH (b)-[:DEPENDS_ON*2..6]->(n:SoftwareVersion)-[:VULNERABLE_TO]->() "
    "                    WHERE n <> b AND NOT (b)-[:DEPENDS_ON]->(n) AND NOT n IN old_nodes }} "
    "  RETURN b LIMIT 3 }} "
    "RETURN sw.name AS pkg, a.versionName AS ver, b.versionName AS ver2, {ECO} AS eco"
)


# --- C8 pools --------------------------------------------------------------
# Dep x Vuln is where the 47-version CVE pool stops being the cap: these
# templates bind on ROOTS whose closures contain a vulnerable version, and the
# 47 vulnerable versions are hub nodes - 849 of 1,121 dependency-having roots
# reach one within 6 hops (measured 2026-08-23). The 272 that do not are the
# discriminating empty. Only 8 roots are both vulnerable and self-reaching
# within 6 hops, which is why C8.2's `dep <> root` guard exists.

ROOTS_VULN_IN_CLOSURE = (
    "MATCH (sw:Software)-[:HAS_VERSION]->(v:SoftwareVersion) "
    "WHERE EXISTS {{ MATCH (v)-[:DEPENDS_ON*1..6]->(d) WHERE d <> v AND (d)-[:VULNERABLE_TO]->() }} "
    "RETURN sw.name AS pkg, v.versionName AS ver, {ECO} AS eco"
)

ROOTS_DEPS_NO_VULN = (
    "MATCH (sw:Software)-[:HAS_VERSION]->(v:SoftwareVersion) "
    "WHERE (v)-[:DEPENDS_ON]->() "
    "AND NOT EXISTS {{ MATCH (v)-[:DEPENDS_ON*1..6]->(d) WHERE d <> v AND (d)-[:VULNERABLE_TO]->() }} "
    "RETURN sw.name AS pkg, v.versionName AS ver, {ECO} AS eco"
)

# The *0..6 trap stratum for C8.5/C8.6: the root itself is vulnerable and its
# proper closure is clean, so a model that writes *1..6 (missing "including
# the root") returns empty against a non-empty gold. Pool: 26 versions.
ROOTS_ONLY_SELF_VULN = (
    "MATCH (sw:Software)-[:HAS_VERSION]->(v:SoftwareVersion) "
    "WHERE (v)-[:VULNERABLE_TO]->() "
    "AND NOT EXISTS {{ MATCH (v)-[:DEPENDS_ON*1..6]->(d) WHERE d <> v AND (d)-[:VULNERABLE_TO]->() }} "
    "RETURN sw.name AS pkg, v.versionName AS ver, {ECO} AS eco"
)

# Clean everywhere: the root is not vulnerable and neither is anything in its
# closure - the honest empty for the *0..6 templates.
ROOTS_ALL_CLEAN = (
    "MATCH (sw:Software)-[:HAS_VERSION]->(v:SoftwareVersion) "
    "WHERE (v)-[:DEPENDS_ON]->() AND NOT (v)-[:VULNERABLE_TO]->() "
    "AND NOT EXISTS {{ MATCH (v)-[:DEPENDS_ON*1..6]->(d) WHERE d <> v AND (d)-[:VULNERABLE_TO]->() }} "
    "RETURN sw.name AS pkg, v.versionName AS ver, {ECO} AS eco"
)

LEAF_CLEAN_VERSIONS = (
    "MATCH (sw:Software)-[:HAS_VERSION]->(v:SoftwareVersion) "
    "WHERE NOT (v)-[:DEPENDS_ON]->() AND NOT (v)-[:VULNERABLE_TO]->() "
    "RETURN sw.name AS pkg, v.versionName AS ver, {ECO} AS eco"
)

# Tree-level (*0..6) positives for C8.5/C8.6: the root COUNTS, per the
# question's own "(including the root)".
ROOTS_VULN_IN_TREE06 = (
    "MATCH (sw:Software)-[:HAS_VERSION]->(v:SoftwareVersion) "
    "WHERE EXISTS {{ MATCH (v)-[:DEPENDS_ON*0..6]->(n) WHERE (n)-[:VULNERABLE_TO]->() }} "
    "RETURN sw.name AS pkg, v.versionName AS ver, {ECO} AS eco"
)

ROOTS_CWE_IN_TREE06 = (
    "MATCH (sw:Software)-[:HAS_VERSION]->(v:SoftwareVersion) "
    "WHERE EXISTS {{ MATCH (v)-[:DEPENDS_ON*0..6]->(n) "
    "WHERE (n)-[:VULNERABLE_TO]->(:Vulnerability)-[:VULNERABILITY_TYPE]->() }} "
    "RETURN sw.name AS pkg, v.versionName AS ver, {ECO} AS eco"
)

ROOTS_ONLY_SELF_CWE = (
    "MATCH (sw:Software)-[:HAS_VERSION]->(v:SoftwareVersion) "
    "WHERE (v)-[:VULNERABLE_TO]->(:Vulnerability)-[:VULNERABILITY_TYPE]->() "
    "AND NOT EXISTS {{ MATCH (v)-[:DEPENDS_ON*1..6]->(d) "
    "WHERE d <> v AND (d)-[:VULNERABLE_TO]->(:Vulnerability)-[:VULNERABILITY_TYPE]->() }} "
    "RETURN sw.name AS pkg, v.versionName AS ver, {ECO} AS eco"
)

# C8.7's two sides of "indirect": a vulnerable version at 2..6 hops that is
# not also a direct dependency (pool 840), vs the sharp empty where every
# vulnerable dependency IS direct (pool 9 - shipped in full).
ROOTS_INDIRECT_VULN = (
    "MATCH (sw:Software)-[:HAS_VERSION]->(v:SoftwareVersion) "
    "WHERE EXISTS {{ MATCH (v)-[:DEPENDS_ON*2..6]->(d) "
    "WHERE d <> v AND NOT (v)-[:DEPENDS_ON]->(d) AND (d)-[:VULNERABLE_TO]->() }} "
    "RETURN sw.name AS pkg, v.versionName AS ver, {ECO} AS eco"
)

ROOTS_VULN_DIRECT_ONLY = (
    "MATCH (sw:Software)-[:HAS_VERSION]->(v:SoftwareVersion) "
    "WHERE EXISTS {{ MATCH (v)-[:DEPENDS_ON]->(d) WHERE (d)-[:VULNERABLE_TO]->() }} "
    "AND NOT EXISTS {{ MATCH (v)-[:DEPENDS_ON*2..6]->(d) "
    "WHERE d <> v AND NOT (v)-[:DEPENDS_ON]->(d) AND (d)-[:VULNERABLE_TO]->() }} "
    "RETURN sw.name AS pkg, v.versionName AS ver, {ECO} AS eco"
)

# C8.8 binds only roots where nothing is left for shortestPath() to
# arbitrate (the C3.3 G2 precedent): a UNIQUE nearest vulnerable dependency
# with a UNIQUE shortest path to it. Roots that are themselves vulnerable and
# self-reaching are excluded so the root never enters the candidate set.
# Pool: 277, mean answer depth 3.0 hops.
ROOTS_UNIQUE_NEAREST_VULN = (
    "MATCH (sw:Software)-[:HAS_VERSION]->(v:SoftwareVersion) WHERE (v)-[:DEPENDS_ON]->() "
    "AND NOT ((v)-[:VULNERABLE_TO]->() AND EXISTS {{ (v)-[:DEPENDS_ON*1..6]->(v) }}) "
    "CALL (v) {{ "
    "  MATCH (d:SoftwareVersion)-[:VULNERABLE_TO]->() WHERE d <> v "
    "  MATCH p = shortestPath((v)-[:DEPENDS_ON*1..6]->(d)) "
    "  WITH d, length(p) AS hops "
    "  WITH collect({{d: d, hops: hops}}) AS cands, min(hops) AS mh "
    "  WITH [c IN cands WHERE c.hops = mh | c.d] AS mins, mh "
    "  WHERE size(mins) = 1 "
    "  WITH mins[0] AS target, mh "
    "  MATCH p2 = allShortestPaths((v)-[:DEPENDS_ON*1..6]->(target)) "
    "  WITH target, mh, count(p2) AS np WHERE np = 1 "
    "  RETURN target }} "
    "RETURN sw.name AS pkg, v.versionName AS ver, {ECO} AS eco"
)

# C8.9's positive split: at least one direct dependency whose <=5-hop subtree
# holds a CVE, vs the all-zero trap - the root HAS direct dependencies and
# every count in the answer is 0, so a model that filters to vulnerable rows
# (plain MATCH instead of OPTIONAL MATCH) collapses the table. The single
# direct self-loop version is excluded from both.
ROOTS_MIXED_SUBTREES = (
    "MATCH (sw:Software)-[:HAS_VERSION]->(v:SoftwareVersion) "
    "WHERE NOT (v)-[:DEPENDS_ON]->(v) "
    "AND EXISTS {{ MATCH (v)-[:DEPENDS_ON*1..6]->(d) WHERE d <> v AND (d)-[:VULNERABLE_TO]->() }} "
    "RETURN sw.name AS pkg, v.versionName AS ver, {ECO} AS eco"
)

ROOTS_ALL_ZERO_SUBTREES = (
    "MATCH (sw:Software)-[:HAS_VERSION]->(v:SoftwareVersion) "
    "WHERE (v)-[:DEPENDS_ON]->() AND NOT (v)-[:DEPENDS_ON]->(v) "
    "AND NOT EXISTS {{ MATCH (v)-[:DEPENDS_ON*1..6]->(d) WHERE d <> v AND (d)-[:VULNERABLE_TO]->() }} "
    "RETURN sw.name AS pkg, v.versionName AS ver, {ECO} AS eco"
)

# C8.10 pair pools, all first-side correlated (the C5.5 sampling lesson).
# share_vuln_dep: the intersection of the two trees contains a vulnerable
# version (2,522 pairs over 848 first sides at LIMIT 3).
PAIRS_SHARE_VULN_DEP = (
    "MATCH (sw:Software)-[:HAS_VERSION]->(a:SoftwareVersion) WHERE (a)-[:DEPENDS_ON]->() "
    "CALL (sw, a) {{ "
    "  MATCH (a)-[:DEPENDS_ON*1..6]->(d1:SoftwareVersion) "
    "  WHERE d1 <> a AND (d1)-[:VULNERABLE_TO]->() "
    "  WITH collect(DISTINCT d1) AS tv WHERE size(tv) > 0 "
    "  MATCH (sb:Software)-[:HAS_VERSION]->(b:SoftwareVersion) "
    "  WHERE sb.name <> sw.name AND EXISTS {{ MATCH (b)-[:DEPENDS_ON*1..2]->(x) WHERE x IN tv }} "
    "  RETURN sb, b LIMIT 3 }} "
    "RETURN sw.name AS pkg, a.versionName AS ver, sb.name AS dep, b.versionName AS dep_ver, {ECO} AS eco"
)

# The discriminating empty: the trees DO intersect, but nothing in the
# intersection is vulnerable - answering C5.4's question instead of C8.10's
# scores 0 here.
PAIRS_SHARE_TREE_NO_VULN = (
    "MATCH (sw:Software)-[:HAS_VERSION]->(a:SoftwareVersion) WHERE (a)-[:DEPENDS_ON]->() "
    "CALL (sw, a) {{ "
    "  MATCH (a)-[:DEPENDS_ON*1..6]->(d1:SoftwareVersion) WHERE d1 <> a "
    "  WITH collect(DISTINCT d1) AS t1, "
    "       [x IN collect(DISTINCT d1) WHERE (x)-[:VULNERABLE_TO]->()] AS tv "
    "  MATCH (sb:Software)-[:HAS_VERSION]->(b:SoftwareVersion) "
    "  WHERE sb.name <> sw.name "
    "  AND EXISTS {{ MATCH (b)-[:DEPENDS_ON*1..2]->(x) WHERE x IN t1 }} "
    "  AND NOT EXISTS {{ MATCH (b)-[:DEPENDS_ON*1..6]->(y) WHERE y <> b AND y IN tv }} "
    "  RETURN sb, b LIMIT 3 }} "
    "RETURN sw.name AS pkg, a.versionName AS ver, sb.name AS dep, b.versionName AS dep_ver, {ECO} AS eco"
)


# --- C7 pools --------------------------------------------------------------
# The reverse direction is where this graph is almost never empty: 1,639 of
# 1,650 versions have at least one direct dependent (measured 2026-08-23), so
# the empty strata are graph-capped at 11 versions / 10 products and every
# other pool reaches 250 comfortably. Two structural facts shape the pools:
# the graph contains exactly ONE direct self-loop version (excluded from
# C7.1's positive pool so the verbatim gold never lists a version as its own
# dependent), and 466 versions sit on a cycle that returns within 6 hops, so
# C7.5's gold carries the same `<> root` guard the C3 transitive golds got.

REV_HAS_DEPENDENTS = (
    "MATCH (sw:Software)-[:HAS_VERSION]->(v:SoftwareVersion) "
    "WHERE EXISTS {{ MATCH (o:SoftwareVersion)-[:DEPENDS_ON]->(v) WHERE o <> v }} "
    "AND NOT (v)-[:DEPENDS_ON]->(v) "
    "RETURN sw.name AS pkg, v.versionName AS ver, {ECO} AS eco"
)

REV_NO_DEPENDENTS = (
    "MATCH (sw:Software)-[:HAS_VERSION]->(v:SoftwareVersion) WHERE NOT ()-[:DEPENDS_ON]->(v) "
    "RETURN sw.name AS pkg, v.versionName AS ver, {ECO} AS eco"
)

PRODUCTS_WITH_DEPENDENTS = (
    "MATCH (sw:Software) "
    "WHERE EXISTS {{ (sw)-[:HAS_VERSION]->(:SoftwareVersion)<-[:DEPENDS_ON]-() }} "
    "RETURN sw.name AS dep, {ECO} AS eco"
)

PRODUCTS_NO_DEPENDENTS = (
    "MATCH (sw:Software) "
    "WHERE NOT EXISTS {{ (sw)-[:HAS_VERSION]->(:SoftwareVersion)<-[:DEPENDS_ON]-() }} "
    "RETURN sw.name AS dep, {ECO} AS eco"
)

# The discriminating split for C7.5, mirroring C3's has_indirect/direct_only:
# a version whose dependents include some at depth >= 2 (the transitive
# question is about something real) vs one whose dependents are all one hop
# up (reverse *1..6 == reverse *1..1 there, so a model that writes *2..6 for
# "at any depth" is punished). Both sides exclude the version itself so the
# claim survives cycles.
REV_INDIRECT_DEPENDENTS = (
    "MATCH (sw:Software)-[:HAS_VERSION]->(v:SoftwareVersion) "
    "WHERE EXISTS {{ MATCH (o:SoftwareVersion)-[:DEPENDS_ON*2..2]->(v) WHERE o <> v }} "
    "RETURN sw.name AS pkg, v.versionName AS ver, {ECO} AS eco"
)

REV_DIRECT_ONLY_DEPENDENTS = (
    "MATCH (sw:Software)-[:HAS_VERSION]->(v:SoftwareVersion) "
    "WHERE EXISTS {{ MATCH (o:SoftwareVersion)-[:DEPENDS_ON]->(v) WHERE o <> v }} "
    "AND NOT EXISTS {{ MATCH (o2:SoftwareVersion)-[:DEPENDS_ON*2..2]->(v) WHERE o2 <> v }} "
    "RETURN sw.name AS pkg, v.versionName AS ver, {ECO} AS eco"
)


# --- C6 pools --------------------------------------------------------------
# The vulnerability side of the graph is small and fixed (measured 2026-08-23):
# 47 versions carry a CVE, via 196 (version, CVE) pairs over 155 Vulnerability
# nodes - 134 of those CVEs have a CWE classification and 21 do not, and
# exactly 2 versions have CVEs none of which carries a CWE. Every positive
# stratum below draws from these pools, so C6.2/C6.3/C6.8 ship at the honest
# cap (positive_pool / positive_share) instead of 250 - the same
# graph-capacity precedent as C3.5 and C5.6. C6.6 (196 true pairs) reaches
# 250; C6.7 (155 CVEs + synthetic absences) ships at 200.

VULN_VERSIONS = (
    "MATCH (sw:Software)-[:HAS_VERSION]->(v:SoftwareVersion) WHERE (v)-[:VULNERABLE_TO]->() "
    "RETURN sw.name AS pkg, v.versionName AS ver, {ECO} AS eco"
)

CLEAN_VERSIONS = (
    "MATCH (sw:Software)-[:HAS_VERSION]->(v:SoftwareVersion) WHERE NOT (v)-[:VULNERABLE_TO]->() "
    "RETURN sw.name AS pkg, v.versionName AS ver, {ECO} AS eco"
)

CWE_VERSIONS = (
    "MATCH (sw:Software)-[:HAS_VERSION]->(v:SoftwareVersion) "
    "WHERE (v)-[:VULNERABLE_TO]->(:Vulnerability)-[:VULNERABILITY_TYPE]->() "
    "RETURN sw.name AS pkg, v.versionName AS ver, {ECO} AS eco"
)

# The sharp empty stratum for C6.3: the version IS vulnerable, but none of its
# CVEs has a CWE classification, so the honest answer is still nothing. Only 2
# such versions exist; both are included.
VULN_NO_CWE_VERSIONS = (
    "MATCH (sw:Software)-[:HAS_VERSION]->(v:SoftwareVersion) "
    "WHERE (v)-[:VULNERABLE_TO]->() "
    "AND NOT (v)-[:VULNERABLE_TO]->(:Vulnerability)-[:VULNERABILITY_TYPE]->() "
    "RETURN sw.name AS pkg, v.versionName AS ver, {ECO} AS eco"
)

# The true pool for C6.6: real (version, CVE) links.
VULN_PAIRS = (
    "MATCH (sw:Software)-[:HAS_VERSION]->(v:SoftwareVersion)-[:VULNERABLE_TO]->(c:Vulnerability) "
    "RETURN DISTINCT sw.name AS pkg, v.versionName AS ver, c.cveId AS cve, {ECO} AS eco"
)

# CVE-keyed pools for C6.7. A Vulnerability node has no ecosystem of its own,
# so the round-robin eco comes from an affected version (min() makes the
# choice deterministic; every one of the 155 CVEs has at least one).
CVES_WITH_CWE = (
    "MATCH (sw:Software)-[:HAS_VERSION]->(v:SoftwareVersion)-[:VULNERABLE_TO]->(c:Vulnerability) "
    "WHERE (c)-[:VULNERABILITY_TYPE]->() "
    "WITH c.cveId AS cve, min({ECO}) AS eco RETURN cve, eco"
)
CVES_WITHOUT_CWE = (
    "MATCH (sw:Software)-[:HAS_VERSION]->(v:SoftwareVersion)-[:VULNERABLE_TO]->(c:Vulnerability) "
    "WHERE NOT (c)-[:VULNERABILITY_TYPE]->() "
    "WITH c.cveId AS cve, min({ECO}) AS eco RETURN cve, eco"
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
    # All six rows are built. C5.1, C5.2, C5.3, C5.5 and C5.6 are verbatim from
    # the sheet; **C5.4's gold is rewritten** because the sheet's version is
    # wrong rather than debatable - see the note on the template itself.
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
    "C5.4": {
        "family": "C5: Comparative / set",
        "source": "luxu",
        "v3_id": None,
        "query_type": "CR",
        "difficulty": "Hard",
        "params": ("pkg", "ver", "dep", "dep_ver"),
        "question": "What dependencies appear in both full dependency trees (direct and indirect) of software '{pkg}' version '{ver}' and software '{dep}' version '{dep_ver}'?",
        # THE ONE GOLD IN THE BANK REWRITTEN FOR CORRECTNESS RATHER THAN
        # READING. The sheet writes the intersection as two variable-length
        # patterns in a single MATCH:
        #     (r1)-[:DEPENDS_ON*1..6]->(d)<-[:DEPENDS_ON*1..6]-(r2)
        # Cypher's relationship-uniqueness rule then requires the two paths to
        # share no edge, and the intersection silently loses most of its rows.
        # Measured 2026-08-22: click 0.6.1 vs dashmap 5.4.0 returned 490 where
        # the truth is 799, and ab_glyph_rasterizer 0.1.8 vs ab_glyph 0.2.29
        # returned 1 where the truth is 137. No reading of the question makes
        # those numbers right, so this is a bug fix, not a deviation.
        #
        # Collect one closure, then test membership. Also 350-2500x faster
        # (0.07 s vs 25.1 s on the click/dashmap pair), which matters because
        # the median answer here is 274 rows.
        #
        # `d1 <> r1` / `d2 <> r2` follow the C3.1 precedent: this graph has
        # dependency cycles, and a version is not its own dependency.
        "cypher": (
            "MATCH (s1:Software {{name: '{pkg}'}})-[:HAS_VERSION]->(r1:SoftwareVersion {{versionName: '{ver}'}}) "
            "MATCH (r1)-[:DEPENDS_ON*1..6]->(d1:SoftwareVersion) WHERE d1 <> r1 "
            "WITH collect(DISTINCT d1) AS t1 "
            "MATCH (s2:Software {{name: '{dep}'}})-[:HAS_VERSION]->(r2:SoftwareVersion {{versionName: '{dep_ver}'}}) "
            "MATCH (r2)-[:DEPENDS_ON*1..6]->(d2:SoftwareVersion) WHERE d2 <> r2 AND d2 IN t1 "
            "OPTIONAL MATCH (ds:Software)-[:HAS_VERSION]->(d2) "
            "RETURN DISTINCT ds.name AS software, d2.versionName AS version ORDER BY software, version"
        ),
        "answer_shape": {"kind": "table", "columns": ["software", "version"], "ordered": False},
        "strata": [
            ("shares_tree", 0.66, PAIRS_SHARE_TREE, "nonempty"),
            ("disjoint_trees", 0.22, PAIRS_DISJOINT_TREE, "empty"),
            ("first_is_leaf", 0.12, PAIRS_FIRST_IS_LEAF, "empty"),
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
    # ---------------------------------------------------------------------
    # C6: Vulnerability
    #
    # The sheet lists eight C6 rows and strikes three: C6.1 ("any known
    # vulnerabilities", the boolean degenerate of C6.4 > 0) and C6.4/C6.5, the
    # one-hop count halves of the C6.2/C6.3 list-vs-count pairs. Live rows are
    # C6.2, C6.3, C6.6, C6.7 and C6.8; all five golds ship verbatim from the
    # sheet - the strata, quotas and negatives are what v4 adds.
    #
    # This is the family the vulnerability pools cap. 47 CVE-bearing versions
    # divided by a 0.67 positive share puts the honest ceiling for
    # C6.2 and C6.8 at 70; C6.3's is 67 (45 CWE-bearing versions). Padding
    # them to 250 would mean templates whose correct answer is "nothing" about
    # 80% of the time, which measures willingness to stay silent, not query
    # skill. The vulnerability-driven re-import remains the only real fix.
    # ---------------------------------------------------------------------
    "C6.2": {
        "family": "C6: Vulnerability",
        "source": "luxu",
        "v3_id": "C2.3",           # identical question and gold in the v3 bank
        "query_type": "CR",
        "difficulty": "Medium",
        "params": ("pkg", "ver"),
        "question": "What are the CVE IDs of known vulnerabilities affecting software '{pkg}' version '{ver}'?",
        "cypher": (
            "MATCH (s:Software {{name: '{pkg}'}})-[:HAS_VERSION]->(root:SoftwareVersion {{versionName: '{ver}'}}) "
            "MATCH (root)-[:VULNERABLE_TO]->(c:Vulnerability) "
            "RETURN DISTINCT c.cveId AS cveId ORDER BY cveId"
        ),
        "answer_shape": {"kind": "list", "columns": ["cveId"], "ordered": False},
        # CAPACITY CEILING: 47 versions in the whole KG carry a CVE, and V6
        # uniqueness is on (pkg, ver), so 47 is the entire positive pool.
        "max_quota": 70,
        "strata": [
            ("has_cves", 0.67, VULN_VERSIONS, "nonempty"),
            ("clean_version", 0.22, CLEAN_VERSIONS, "empty"),
            ("absent_package", 0.11, "SYNTH:absent_package_versioned", "empty"),
        ],
    },
    "C6.3": {
        "family": "C6: Vulnerability",
        "source": "luxu",
        "v3_id": "C2.4",           # identical question and gold in the v3 bank
        "query_type": "CR",
        "difficulty": "Medium",
        "params": ("pkg", "ver"),
        "question": "What CWE IDs are associated with vulnerabilities of software '{pkg}' version '{ver}'?",
        "cypher": (
            "MATCH (s:Software {{name: '{pkg}'}})-[:HAS_VERSION]->(root:SoftwareVersion {{versionName: '{ver}'}}) "
            "MATCH (root)-[:VULNERABLE_TO]->(:Vulnerability)-[:VULNERABILITY_TYPE]->(w:VulnerabilityType) "
            "RETURN DISTINCT w.cweId AS cweId ORDER BY cweId"
        ),
        "answer_shape": {"kind": "list", "columns": ["cweId"], "ordered": False},
        # CAPACITY CEILING: 45 CWE-bearing versions. The 0.03 stratum is the
        # entire graph supply of "vulnerable but unclassified" - 2 versions -
        # and it is the discriminating empty: the version HAS CVEs, and a
        # model that answers the CVE question it expected still scores 0.
        "max_quota": 67,
        "strata": [
            ("has_cwes", 0.67, CWE_VERSIONS, "nonempty"),
            ("cves_without_cwe", 0.03, VULN_NO_CWE_VERSIONS, "empty"),
            ("clean_version", 0.19, CLEAN_VERSIONS, "empty"),
            ("absent_package", 0.11, "SYNTH:absent_package_versioned", "empty"),
        ],
    },
    "C6.6": {
        "family": "C6: Vulnerability",
        "source": "luxu",
        "v3_id": None,
        "query_type": "SA",
        "difficulty": "Easy",
        "params": ("pkg", "ver", "cve"),
        "question": "Does software '{pkg}' version '{ver}' have vulnerability '{cve}'?",
        "cypher": (
            "MATCH (s:Software {{name: '{pkg}'}})-[:HAS_VERSION]->(root:SoftwareVersion {{versionName: '{ver}'}}) "
            "OPTIONAL MATCH (root)-[:VULNERABLE_TO]->(c:Vulnerability {{cveId: '{cve}'}}) "
            "RETURN count(c) > 0 AS has_cve"
        ),
        "answer_shape": {"kind": "bool", "columns": ["has_cve"], "ordered": False},
        # The one C6 template that reaches 250: the true pool is the 196 real
        # (version, CVE) links. The false side comes in three grades:
        # `vuln_other_cve` is the discriminating one (the version IS
        # vulnerable, just not to this CVE - a model that resolves "has
        # vulnerability X" to "has any vulnerability" answers true);
        # `near_miss_cve` asks a vulnerable version about an id one number
        # away from one of ITS OWN CVEs, so "no" has to come from the graph
        # rather than from the id looking foreign; `clean_version_real_cve`
        # is the easy grade.
        "strata": [
            ("has_cve", 0.50, VULN_PAIRS, "true"),
            ("vuln_other_cve", 0.20, "SYNTH:vuln_other_cve", "false"),
            ("near_miss_cve", 0.15, "SYNTH:near_miss_cve", "false"),
            ("clean_version_real_cve", 0.15, "SYNTH:clean_version_real_cve", "false"),
        ],
    },
    "C6.7": {
        "family": "C6: Vulnerability",
        "source": "luxu",
        "v3_id": None,
        "query_type": "SR",
        "difficulty": "Easy",
        "params": ("cve",),
        "question": "What CWE IDs are linked to vulnerability '{cve}'?",
        "cypher": (
            "MATCH (c:Vulnerability {{cveId: '{cve}'}})-[:VULNERABILITY_TYPE]->(w:VulnerabilityType) "
            "RETURN DISTINCT w.cweId AS cweId ORDER BY cweId"
        ),
        "answer_shape": {"kind": "list", "columns": ["cweId"], "ordered": False},
        # CAPACITY CEILING: 155 Vulnerability nodes in the graph, split 134
        # with a CWE / 21 without. The 21 are the attributable empty ("the CVE
        # exists, it just has no classification"); the synthetic absences are
        # near-miss ids one number away from real ones. 134/21/45 lands the
        # shares exactly at quota 200.
        "max_quota": 200,
        "strata": [
            ("cve_with_cwe", 0.67, CVES_WITH_CWE, "nonempty"),
            ("cve_without_cwe", 0.105, CVES_WITHOUT_CWE, "empty"),
            ("absent_cve", 0.225, "SYNTH:absent_cve", "empty"),
        ],
    },
    "C6.8": {
        "family": "C6: Vulnerability",
        "source": "luxu",
        "v3_id": None,
        "query_type": "CR",
        "difficulty": "Medium",
        "params": ("pkg", "ver"),
        "question": "What CVE-CWE pairs affect software '{pkg}' version '{ver}'?",
        "cypher": (
            "MATCH (s:Software {{name: '{pkg}'}})-[:HAS_VERSION]->(root:SoftwareVersion {{versionName: '{ver}'}}) "
            "MATCH (root)-[:VULNERABLE_TO]->(c:Vulnerability) "
            "OPTIONAL MATCH (c)-[:VULNERABILITY_TYPE]->(w:VulnerabilityType) "
            "RETURN DISTINCT c.cveId AS cveId, w.cweId AS cweId ORDER BY cveId, cweId"
        ),
        "answer_shape": {"kind": "table", "columns": ["cveId", "cweId"], "ordered": False},
        # Same 47-version positive pool and ceiling as C6.2. The sheet's
        # OPTIONAL MATCH on the CWE hop is right and is kept: 27 of the 196
        # (version, CVE) links involve a CVE with no CWE, so some gold rows
        # carry a null cweId - a NEW ANSWER SHAPE for the bank (nullable
        # cell), covered by its own format-contract clause per the house rule
        # that every new shape needs one.
        "max_quota": 70,
        "strata": [
            ("has_cves", 0.67, VULN_VERSIONS, "nonempty"),
            ("clean_version", 0.22, CLEAN_VERSIONS, "empty"),
            ("absent_package", 0.11, "SYNTH:absent_package_versioned", "empty"),
        ],
    },
    # ---------------------------------------------------------------------
    # C7: Reverse DEPENDS_ON (in)
    #
    # The sheet lists five C7 rows (plus the backup C7.6) and strikes two:
    # C7.3 (the count half of the C7.1 list-vs-count pair at one hop) and
    # C7.4 ("is it a root", the boolean degenerate of C7.3 = 0). Live rows
    # are C7.1, C7.2 and C7.5.
    #
    # One deliberate deviation and one recorded defect:
    # - C7.5's gold gets `WHERE other <> root` (the C3.1 cycle precedent):
    #   466 versions can reach themselves within 6 hops, so the sheet's
    #   verbatim gold lists a version as its own transitive dependent for
    #   every binding in that region. Same defect class, same fix, and the
    #   contract's "never list the root itself" clause already covers it.
    # - C7.2's question asks for "software products" while its gold returns
    #   (software, version) pairs — one row per dependent VERSION, not per
    #   product. Shipped verbatim (the C4 rule: change nothing, collect the
    #   problems); the smoke run measures what the mismatch costs.
    # ---------------------------------------------------------------------
    "C7.1": {
        "family": "C7: Reverse DEPENDS_ON (in)",
        "source": "luxu",
        "v3_id": "C3.5",           # same gold in v3; wording said "packages"
        "query_type": "CR",
        "difficulty": "Easy",
        "params": ("pkg", "ver"),
        "question": "Which software versions directly depend on software '{pkg}' version '{ver}'?",
        "cypher": (
            "MATCH (s:Software {{name: '{pkg}'}})-[:HAS_VERSION]->(root:SoftwareVersion {{versionName: '{ver}'}}) "
            "MATCH (other:SoftwareVersion)-[:DEPENDS_ON]->(root) "
            "OPTIONAL MATCH (os:Software)-[:HAS_VERSION]->(other) "
            "RETURN DISTINCT os.name AS software, other.versionName AS version ORDER BY software, version"
        ),
        "answer_shape": {"kind": "table", "columns": ["software", "version"], "ordered": False},
        # The graph's own bias does the discriminating here: with 1,639 of
        # 1,650 versions depended on, the honest empty pool is 11 versions -
        # all of them anchor roots - and it ships in full.
        "strata": [
            ("has_dependents", 0.85, REV_HAS_DEPENDENTS, "nonempty"),
            ("no_dependents", 0.04, REV_NO_DEPENDENTS, "empty"),
            ("absent_package", 0.11, "SYNTH:absent_package_versioned", "empty"),
        ],
    },
    "C7.2": {
        "family": "C7: Reverse DEPENDS_ON (in)",
        "source": "luxu",
        "v3_id": None,
        "query_type": "CR",
        "difficulty": "Medium",
        "params": ("dep",),
        "question": "Which software products have some version that directly depends on software '{dep}'?",
        "cypher": (
            "MATCH (ds:Software {{name: '{dep}'}})-[:HAS_VERSION]->(target:SoftwareVersion) "
            "MATCH (other:SoftwareVersion)-[:DEPENDS_ON]->(target) "
            "OPTIONAL MATCH (os:Software)-[:HAS_VERSION]->(other) "
            "RETURN DISTINCT os.name AS software, other.versionName AS version ORDER BY software, version"
        ),
        "answer_shape": {"kind": "table", "columns": ["software", "version"], "ordered": False},
        "strata": [
            ("has_dependents", 0.78, PRODUCTS_WITH_DEPENDENTS, "nonempty"),
            ("no_version_depended_on", 0.04, PRODUCTS_NO_DEPENDENTS, "empty"),
            ("absent_product", 0.18, "SYNTH:absent_product", "empty"),
        ],
    },
    "C7.5": {
        "family": "C7: Reverse DEPENDS_ON (in)",
        "source": "luxu",
        "v3_id": "C3.6",           # same traversal in v3; wording said "packages"
        "query_type": "CR",
        "difficulty": "Medium",
        "params": ("pkg", "ver"),
        "question": "Which software versions can reach software '{pkg}' version '{ver}' via DEPENDS_ON at any depth?",
        # DEVIATION FROM THE SHEET: `WHERE other <> root` added, per the C3.1
        # cycle precedent - 466 versions return to themselves within 6 hops.
        "cypher": (
            "MATCH (s:Software {{name: '{pkg}'}})-[:HAS_VERSION]->(root:SoftwareVersion {{versionName: '{ver}'}}) "
            "MATCH (other:SoftwareVersion)-[:DEPENDS_ON*1..6]->(root) WHERE other <> root "
            "OPTIONAL MATCH (os:Software)-[:HAS_VERSION]->(other) "
            "RETURN DISTINCT os.name AS software, other.versionName AS version ORDER BY software, version"
        ),
        "answer_shape": {"kind": "table", "columns": ["software", "version"], "ordered": False},
        # NOTE the question says "at any depth" while gold and schema
        # instruction 4 cap at 6 hops - the same open wording decision as
        # C3.1/C3.6; the verifier measures the truncation share.
        "strata": [
            ("has_indirect_dependents", 0.60, REV_INDIRECT_DEPENDENTS, "nonempty"),
            ("direct_only_dependents", 0.22, REV_DIRECT_ONLY_DEPENDENTS, "nonempty"),
            ("no_dependents", 0.04, REV_NO_DEPENDENTS, "empty"),
            ("absent_package", 0.14, "SYNTH:absent_package_versioned", "empty"),
        ],
    },
    # ---------------------------------------------------------------------
    # C8: Dep x Vuln
    #
    # All seven listed rows are live (C8.1/C8.3/C8.4 were deleted from the
    # sheet, not struck). This is the family where the 47-version CVE pool
    # stops being the cap: the templates bind on roots whose closures contain
    # a vulnerable version - pool 849 - so every row reaches 250.
    #
    # Deviations, each on an established precedent:
    # - C8.2 and C8.7 golds get `dep <> root` (C3.1 cycle precedent): 8 roots
    #   are vulnerable AND return to themselves within 6 hops, and both
    #   questions say the root does not count.
    # - C8.8 binds only provably-unique answers (C3.3 G2 precedent):
    #   shortestPath() arbitrates ties, so every bound root has exactly one
    #   nearest vulnerable dependency and exactly one shortest path to it.
    # - C8.10's gold is REWRITTEN collect-then-diff (C5.4 precedent): the
    #   sheet's two variable-length patterns in one MATCH silently drop every
    #   path pair sharing an edge. Same bug, same fix; the verifier replays
    #   the sheet's formulation to quantify the loss.
    # C8.5, C8.6 and C8.9 ship verbatim - C8.5/C8.6's *0..6 agrees with
    # their questions' "(including the root)".
    # ---------------------------------------------------------------------
    "C8.2": {
        "family": "C8: Dep x Vuln",
        "source": "luxu",
        "v3_id": None,
        "query_type": "CR",
        "difficulty": "Medium",
        "params": ("pkg", "ver"),
        "question": "What CVE IDs affect any direct or indirect dependency of software '{pkg}' version '{ver}' (excluding the root itself)?",
        # DEVIATION: `dep <> root` added - the question says "excluding the
        # root itself", and 8 vulnerable roots reach themselves within 6 hops.
        "cypher": (
            "MATCH (s:Software {{name: '{pkg}'}})-[:HAS_VERSION]->(root:SoftwareVersion {{versionName: '{ver}'}}) "
            "MATCH (root)-[:DEPENDS_ON*1..6]->(dep:SoftwareVersion) WHERE dep <> root "
            "MATCH (dep)-[:VULNERABLE_TO]->(c:Vulnerability) "
            "RETURN DISTINCT c.cveId AS cveId ORDER BY cveId"
        ),
        "answer_shape": {"kind": "list", "columns": ["cveId"], "ordered": False},
        "strata": [
            ("vuln_in_closure", 0.64, ROOTS_VULN_IN_CLOSURE, "nonempty"),
            ("deps_no_vuln", 0.16, ROOTS_DEPS_NO_VULN, "empty"),
            ("leaf_version", 0.10, LEAF_VERSIONS, "empty"),
            ("absent_package", 0.10, "SYNTH:absent_package_versioned", "empty"),
        ],
    },
    "C8.5": {
        "family": "C8: Dep x Vuln",
        "source": "luxu",
        "v3_id": None,
        "query_type": "CR",
        "difficulty": "Medium",
        "params": ("pkg", "ver"),
        "question": "Which software products in the dependency tree of software '{pkg}' version '{ver}' (including the root) have known vulnerabilities?",
        # Verbatim: *0..6 matches the question's "(including the root)". Note
        # the same products-vs-(software, version) wording looseness as C7.2,
        # recorded, not repaired.
        "cypher": (
            "MATCH (s:Software {{name: '{pkg}'}})-[:HAS_VERSION]->(root:SoftwareVersion {{versionName: '{ver}'}}) "
            "MATCH (root)-[:DEPENDS_ON*0..6]->(n:SoftwareVersion)-[:VULNERABLE_TO]->(:Vulnerability) "
            "MATCH (ds:Software)-[:HAS_VERSION]->(n) "
            "RETURN DISTINCT ds.name AS software, n.versionName AS version ORDER BY software, version"
        ),
        "answer_shape": {"kind": "table", "columns": ["software", "version"], "ordered": False},
        # root_only_vuln is the stratum the *0..6 exists for: the root itself
        # is vulnerable and its proper closure is clean (26 versions), so a
        # model that writes *1..6 returns empty against a non-empty gold.
        "strata": [
            ("vuln_in_tree", 0.55, ROOTS_VULN_IN_TREE06, "nonempty"),
            ("root_only_vuln", 0.10, ROOTS_ONLY_SELF_VULN, "nonempty"),
            ("clean_tree", 0.15, ROOTS_ALL_CLEAN, "empty"),
            ("leaf_clean", 0.10, LEAF_CLEAN_VERSIONS, "empty"),
            ("absent_package", 0.10, "SYNTH:absent_package_versioned", "empty"),
        ],
    },
    "C8.6": {
        "family": "C8: Dep x Vuln",
        "source": "luxu",
        "v3_id": None,
        "query_type": "CR",
        "difficulty": "Medium",
        "params": ("pkg", "ver"),
        "question": "What CWE IDs appear in the dependency tree of software '{pkg}' version '{ver}' (including the root)?",
        "cypher": (
            "MATCH (s:Software {{name: '{pkg}'}})-[:HAS_VERSION]->(root:SoftwareVersion {{versionName: '{ver}'}}) "
            "MATCH (root)-[:DEPENDS_ON*0..6]->(n:SoftwareVersion)-[:VULNERABLE_TO]->(:Vulnerability)"
            "-[:VULNERABILITY_TYPE]->(w:VulnerabilityType) "
            "RETURN DISTINCT w.cweId AS cweId ORDER BY cweId"
        ),
        "answer_shape": {"kind": "list", "columns": ["cweId"], "ordered": False},
        # The positive pools are CWE-exact (not reused from the CVE side):
        # 2 vulnerable versions have no classified CVE at all, and a root
        # whose tree holds only unclassified CVEs belongs in the empty side.
        "strata": [
            ("cwe_in_tree", 0.55, ROOTS_CWE_IN_TREE06, "nonempty"),
            ("root_only_cwe", 0.10, ROOTS_ONLY_SELF_CWE, "nonempty"),
            ("clean_tree", 0.15, ROOTS_ALL_CLEAN, "empty"),
            ("leaf_clean", 0.10, LEAF_CLEAN_VERSIONS, "empty"),
            ("absent_package", 0.10, "SYNTH:absent_package_versioned", "empty"),
        ],
    },
    "C8.7": {
        "family": "C8: Dep x Vuln",
        "source": "luxu",
        "v3_id": None,
        "query_type": "CR",
        "difficulty": "Hard",
        "params": ("pkg", "ver"),
        "question": "Which indirect (not direct) dependencies of software '{pkg}' version '{ver}' have known vulnerabilities, and what are their CVE IDs?",
        # DEVIATION: `dep <> root` added (cycle precedent). The sheet's
        # `*2..6 + NOT direct` encoding of "indirect but not also direct" is
        # kept - it is the correct one.
        "cypher": (
            "MATCH (s:Software {{name: '{pkg}'}})-[:HAS_VERSION]->(root:SoftwareVersion {{versionName: '{ver}'}}) "
            "MATCH (root)-[:DEPENDS_ON*2..6]->(dep:SoftwareVersion) "
            "WHERE dep <> root AND NOT (root)-[:DEPENDS_ON]->(dep) "
            "MATCH (dep)-[:VULNERABLE_TO]->(c:Vulnerability) "
            "OPTIONAL MATCH (ds:Software)-[:HAS_VERSION]->(dep) "
            "RETURN DISTINCT ds.name AS software, dep.versionName AS version, c.cveId AS cveId "
            "ORDER BY software, version, cveId"
        ),
        "answer_shape": {"kind": "table", "columns": ["software", "version", "cveId"], "ordered": False},
        # vuln_direct_only is the whole graph supply (9 roots) of "the
        # vulnerable dependencies are all DIRECT": the root demonstrably has
        # vulnerable dependencies, and the right answer is still nothing.
        "strata": [
            ("has_indirect_vuln", 0.62, ROOTS_INDIRECT_VULN, "nonempty"),
            ("vuln_direct_only", 0.04, ROOTS_VULN_DIRECT_ONLY, "empty"),
            ("no_vuln_deps", 0.18, ROOTS_DEPS_NO_VULN, "empty"),
            ("leaf_version", 0.08, LEAF_VERSIONS, "empty"),
            ("absent_package", 0.08, "SYNTH:absent_package_versioned", "empty"),
        ],
    },
    "C8.8": {
        "family": "C8: Dep x Vuln",
        "source": "luxu",
        "v3_id": None,
        "query_type": "CR",
        "difficulty": "Hard",
        "params": ("pkg", "ver"),
        "question": "What is the shortest DEPENDS_ON path from software '{pkg}' version '{ver}' to a dependency that has a known vulnerability (excluding the root)?",
        # Scoreability comes from the BINDINGS (the C3.3 precedent): every
        # bound root has a unique nearest vulnerable dependency and a unique
        # shortest path, so nothing is left for shortestPath() to arbitrate
        # and the question's singular "the shortest path" is true.
        #
        # DEVIATION: `dep <> root` added, and here it is not merely the cycle
        # precedent - WITHOUT it the sheet's gold cannot execute on any of
        # the graph's 47 vulnerable roots: the root itself enters the
        # candidate set and Neo4j refuses shortestPath() with identical
        # endpoints (Neo.DatabaseError.Statement.ExecutionFailed). The
        # question's own "(excluding the root)" says the guard is what was
        # meant.
        "cypher": (
            "MATCH (s:Software {{name: '{pkg}'}})-[:HAS_VERSION]->(root:SoftwareVersion {{versionName: '{ver}'}}) "
            "MATCH (dep:SoftwareVersion)-[:VULNERABLE_TO]->(:Vulnerability) WHERE dep <> root "
            "MATCH p = shortestPath((root)-[:DEPENDS_ON*1..6]->(dep)) "
            "WITH p, length(p) AS hops "
            "WITH min(hops) AS min_hops, collect({{path: [n IN nodes(p) | n.versionName], hops: hops}}) AS allp "
            "UNWIND allp AS row "
            "WITH row, min_hops WHERE row.hops = min_hops "
            # ORDER BY appended per the C2.3/V7 precedent: a table answer
            # needs a defined row order even when the bindings guarantee it
            # has exactly one row.
            "RETURN DISTINCT row.path AS path, min_hops AS hops ORDER BY hops, path"
        ),
        "answer_shape": {"kind": "table", "columns": ["path", "hops"], "ordered": False},
        "strata": [
            ("unique_nearest", 0.60, ROOTS_UNIQUE_NEAREST_VULN, "nonempty"),
            ("no_vuln_reachable", 0.20, ROOTS_DEPS_NO_VULN, "empty"),
            ("leaf_version", 0.10, LEAF_VERSIONS, "empty"),
            ("absent_package", 0.10, "SYNTH:absent_package_versioned", "empty"),
        ],
    },
    "C8.9": {
        "family": "C8: Dep x Vuln",
        "source": "luxu",
        "v3_id": None,
        "query_type": "CR",
        "difficulty": "Hard",
        "params": ("pkg", "ver"),
        "question": "For each direct dependency of software '{pkg}' version '{ver}', how many distinct CVEs appear in that dependency's subtree (including the dependency itself)?",
        # Verbatim. *0..5 from the direct dependency = depth <= 6 from the
        # root, consistent with the depth convention.
        "cypher": (
            "MATCH (s:Software {{name: '{pkg}'}})-[:HAS_VERSION]->(root:SoftwareVersion {{versionName: '{ver}'}}) "
            "MATCH (root)-[:DEPENDS_ON]->(direct:SoftwareVersion) "
            "OPTIONAL MATCH (ds:Software)-[:HAS_VERSION]->(direct) "
            "OPTIONAL MATCH (direct)-[:DEPENDS_ON*0..5]->(n:SoftwareVersion)-[:VULNERABLE_TO]->(c:Vulnerability) "
            "RETURN ds.name AS software, direct.versionName AS version, count(DISTINCT c) AS cve_cnt "
            "ORDER BY software, version"
        ),
        "answer_shape": {"kind": "table", "columns": ["software", "version", "cve_cnt"], "ordered": False},
        # all_zero_subtrees is the trap: the root HAS direct dependencies and
        # every count is 0, so a model that writes a plain MATCH on the
        # vulnerability hop (dropping the zero rows) collapses the table.
        "strata": [
            ("mixed_subtrees", 0.50, ROOTS_MIXED_SUBTREES, "nonempty"),
            ("all_zero_subtrees", 0.25, ROOTS_ALL_ZERO_SUBTREES, "nonempty"),
            ("leaf_version", 0.15, LEAF_VERSIONS, "empty"),
            ("absent_package", 0.10, "SYNTH:absent_package_versioned", "empty"),
        ],
    },
    "C8.10": {
        "family": "C8: Dep x Vuln",
        "source": "luxu",
        "v3_id": None,
        "query_type": "CR",
        "difficulty": "Hard",
        "params": ("pkg", "ver", "dep", "dep_ver"),
        "question": "Which dependencies appear in both full trees of software '{pkg}' version '{ver}' and software '{dep}' version '{dep_ver}', and have known vulnerabilities?",
        # GOLD REWRITTEN, the C5.4 fix applied to its twin. The sheet writes
        # the intersection as two variable-length patterns in one MATCH, and
        # relationship-uniqueness silently drops every path pair sharing an
        # edge (C5.4 measured: 69% of the true answer survives). Collect one
        # closure, test membership, keep the vulnerability filter and the
        # (software, version, cveId) return. `<> r1` / `<> r2` per the cycle
        # precedent. The verifier replays the sheet's formulation on the
        # shipped cases to quantify the loss for this template too.
        "cypher": (
            "MATCH (s1:Software {{name: '{pkg}'}})-[:HAS_VERSION]->(r1:SoftwareVersion {{versionName: '{ver}'}}) "
            "MATCH (r1)-[:DEPENDS_ON*1..6]->(d1:SoftwareVersion) WHERE d1 <> r1 "
            "WITH collect(DISTINCT d1) AS t1 "
            "MATCH (s2:Software {{name: '{dep}'}})-[:HAS_VERSION]->(r2:SoftwareVersion {{versionName: '{dep_ver}'}}) "
            "MATCH (r2)-[:DEPENDS_ON*1..6]->(d:SoftwareVersion) "
            "WHERE d <> r2 AND d IN t1 AND (d)-[:VULNERABLE_TO]->() "
            "WITH DISTINCT d "
            "MATCH (d)-[:VULNERABLE_TO]->(c:Vulnerability) "
            "OPTIONAL MATCH (ds:Software)-[:HAS_VERSION]->(d) "
            "RETURN DISTINCT ds.name AS software, d.versionName AS version, c.cveId AS cveId "
            "ORDER BY software, version, cveId"
        ),
        "answer_shape": {"kind": "table", "columns": ["software", "version", "cveId"], "ordered": False},
        "strata": [
            ("share_vuln_dep", 0.60, PAIRS_SHARE_VULN_DEP, "nonempty"),
            ("share_tree_no_vuln", 0.24, PAIRS_SHARE_TREE_NO_VULN, "empty"),
            ("disjoint_trees", 0.16, PAIRS_DISJOINT_TREE, "empty"),
        ],
    },
    # ---------------------------------------------------------------------
    # C9: Same package, multi-version
    #
    # All seven rows are live. This family is the sheet's best original
    # contribution: C9.5-C9.7 are upgrade-diff questions ("what did upgrading
    # from v1 to v2 add"), which is the real supply-chain question and is
    # structurally a set difference over two closures.
    #
    # Two things measured before building, both recorded here because they
    # settle questions the earlier review left open:
    #
    # 1. **C9.5's `*0..6` and C9.6's `*1..6` are NOT an inconsistency.** The
    #    2026-08-19 review flagged the mismatch as a defect. Measured: C9.5's
    #    question says "(including each root)" and the roots really do
    #    contribute (392 answer rows came from a root itself in a 200-pair
    #    sample), while C9.6's question does not. Each gold agrees with its
    #    own question, so both ship verbatim on that point.
    # 2. **C9.6 has a real cycle defect, and it is large.** Because it
    #    returns software *names*, a new version that reaches its own package
    #    within 6 hops lists the package itself as "newly added". Measured on
    #    600 pairs: the package's own name lands in the answer **131 times
    #    (21.8 %)**. 563 of the 1,070 version pairs can reach their own
    #    package. C9.7 has the same defect at 2/330. Both get the
    #    `<> package` guard, the C3.1 precedent applied to the reverse of the
    #    same problem.
    # ---------------------------------------------------------------------
    "C9.1": {
        "family": "C9: Same package multi-version",
        "source": "luxu",
        "v3_id": None,
        "query_type": "SR",
        "difficulty": "Easy",
        "params": ("pkg",),
        "question": "Which versions of software '{pkg}' have known vulnerabilities?",
        "cypher": (
            "MATCH (s:Software {{name: '{pkg}'}})-[:HAS_VERSION]->(v:SoftwareVersion)"
            "-[:VULNERABLE_TO]->(:Vulnerability) "
            "RETURN DISTINCT v.versionName AS version ORDER BY version"
        ),
        "answer_shape": {"kind": "list", "columns": ["version"], "ordered": False},
        # CAPACITY CEILING: exactly 30 packages in the graph have a vulnerable
        # version, and this template keys on the package alone, so 30 is the
        # entire positive pool. Same cause as C6 - see the graph snapshot.
        "max_quota": 50,
        "strata": [
            ("has_vuln_version", 0.56, PKGS_WITH_VULN_VERSION, "nonempty"),
            ("no_vuln_version", 0.30, PKGS_NO_VULN_VERSION, "empty"),
            ("absent_package", 0.14, "SYNTH:absent_package", "empty"),
        ],
    },
    "C9.2": {
        "family": "C9: Same package multi-version",
        "source": "luxu",
        "v3_id": None,
        "query_type": "CR",
        "difficulty": "Medium",
        "params": ("pkg",),
        "question": "How many distinct CVEs does each version of software '{pkg}' have?",
        "cypher": (
            "MATCH (s:Software {{name: '{pkg}'}})-[:HAS_VERSION]->(v:SoftwareVersion) "
            "OPTIONAL MATCH (v)-[:VULNERABLE_TO]->(c:Vulnerability) "
            "RETURN v.versionName AS version, count(DISTINCT c) AS cve_cnt ORDER BY version"
        ),
        "answer_shape": {"kind": "table", "columns": ["version", "cve_cnt"], "ordered": False},
        # multi_version_clean is the trap and it is the majority case: every
        # row is `0`, so a model that writes MATCH instead of OPTIONAL MATCH
        # returns an empty table against a full one. The template's only
        # empty answer comes from an absent package.
        "strata": [
            ("multi_version_with_vuln", 0.22, PKGS_MULTI_VERSION_VULN, "nonempty"),
            ("multi_version_clean", 0.38, PKGS_MULTI_VERSION_CLEAN, "nonempty"),
            ("single_version", 0.28, PKGS_SINGLE_VERSION, "nonempty"),
            ("absent_package", 0.12, "SYNTH:absent_package", "empty"),
        ],
    },
    "C9.3": {
        "family": "C9: Same package multi-version",
        "source": "luxu",
        "v3_id": None,
        "query_type": "CR",
        "difficulty": "Medium",
        "params": ("pkg", "dep"),
        "question": "For each version of software '{pkg}', what version of software '{dep}' is a direct dependency?",
        # Verbatim, including the non-standard column names `pkg_version` /
        # `dep_version` - both sides of the pairing are versions here, so
        # the contract's usual (software, version) shape does not apply and
        # the family gets its own clause instead.
        "cypher": (
            "MATCH (s:Software {{name: '{pkg}'}})-[:HAS_VERSION]->(v:SoftwareVersion) "
            "MATCH (v)-[:DEPENDS_ON]->(d:SoftwareVersion)<-[:HAS_VERSION]-(ds:Software {{name: '{dep}'}}) "
            "RETURN v.versionName AS pkg_version, d.versionName AS dep_version "
            "ORDER BY pkg_version, dep_version"
        ),
        "answer_shape": {"kind": "table", "columns": ["pkg_version", "dep_version"], "ordered": False},
        "strata": [
            ("varying_dep_version", 0.40, PKG_DEP_VARYING_VERSION, "nonempty"),
            ("uniform_dep_version", 0.32, PKG_DEP_UNIFORM_VERSION, "nonempty"),
            ("unrelated_dep", 0.16, UNRELATED_PAIRS, "empty"),
            ("absent_dep", 0.12, "SYNTH:absent_dep", "empty"),
        ],
    },
    "C9.4": {
        "family": "C9: Same package multi-version",
        "source": "luxu",
        "v3_id": None,
        "query_type": "CR",
        "difficulty": "Medium",
        "params": ("pkg", "cve"),
        "question": "For each version of software '{pkg}', does it have vulnerability '{cve}'?",
        "cypher": (
            "MATCH (s:Software {{name: '{pkg}'}})-[:HAS_VERSION]->(v:SoftwareVersion) "
            "OPTIONAL MATCH (v)-[:VULNERABLE_TO]->(c:Vulnerability {{cveId: '{cve}'}}) "
            "RETURN v.versionName AS version, count(c) > 0 AS has_cve ORDER BY version"
        ),
        # A table of booleans - one row per version, `false` included. New
        # shape for the bank, so it gets its own contract clause.
        "answer_shape": {"kind": "table", "columns": ["version", "has_cve"], "ordered": False},
        # cve_elsewhere and near_miss_cve both produce all-`false` tables, not
        # empty ones: the package exists, so every version still gets a row.
        # This is what separates "the CVE does not affect it" from "there is
        # nothing to report", and a plain MATCH collapses both to nothing.
        "strata": [
            ("real_pair", 0.44, PKG_CVE_REAL, "nonempty"),
            ("cve_elsewhere", 0.28, PKG_CVE_ELSEWHERE, "nonempty"),
            ("near_miss_cve", 0.16, "SYNTH:pkg_near_miss_cve", "nonempty"),
            ("absent_package", 0.12, "SYNTH:absent_package_cve", "empty"),
        ],
    },
    "C9.5": {
        "family": "C9: Same package multi-version",
        "source": "luxu",
        "v3_id": None,
        "query_type": "CR",
        "difficulty": "Hard",
        "params": ("pkg", "ver", "ver2"),
        "question": "What CVE IDs appear in the dependency tree of software '{pkg}' version '{ver2}' but not in the tree of version '{ver}' (including each root)?",
        # Verbatim. The *0..6 is correct here and is NOT the C4 defect: the
        # question says "(including each root)", and the roots really do
        # contribute (392 of the answer rows in a 200-pair probe came from a
        # root itself).
        "cypher": (
            "MATCH (s:Software {{name: '{pkg}'}})-[:HAS_VERSION]->(old:SoftwareVersion {{versionName: '{ver}'}}) "
            "OPTIONAL MATCH (old)-[:DEPENDS_ON*0..6]->(:SoftwareVersion)-[:VULNERABLE_TO]->(oldc:Vulnerability) "
            "WITH s, collect(DISTINCT oldc.cveId) AS old_cves "
            "MATCH (s)-[:HAS_VERSION]->(new:SoftwareVersion {{versionName: '{ver2}'}}) "
            "MATCH (new)-[:DEPENDS_ON*0..6]->(:SoftwareVersion)-[:VULNERABLE_TO]->(c:Vulnerability) "
            "WHERE NOT c.cveId IN old_cves "
            "RETURN DISTINCT c.cveId AS cveId ORDER BY cveId"
        ),
        "answer_shape": {"kind": "list", "columns": ["cveId"], "ordered": False},
        "strata": [
            ("new_extra_cve", 0.58, PAIRS_NEW_EXTRA_CVE, "nonempty"),
            ("same_cves", 0.30, PAIRS_SAME_CVES, "empty"),
            ("new_is_leaf", 0.12, PAIRS_NEW_IS_LEAF, "empty"),
        ],
    },
    "C9.6": {
        "family": "C9: Same package multi-version",
        "source": "luxu",
        "v3_id": None,
        "query_type": "SR",
        "difficulty": "Hard",
        "params": ("pkg", "ver", "ver2"),
        "question": "Which software products appear in the dependency tree of software '{pkg}' version '{ver2}' but not in the tree of version '{ver}'?",
        # DEVIATION: `ns.name <> s.name` added. Because this template returns
        # software NAMES, a new version that reaches its own package within 6
        # hops reports the package itself as newly added - measured at
        # **131 of 600 pairs (21.8 %)**, the largest cycle contamination
        # found in the bank. 563 of the 1,070 version pairs can reach their
        # own package. Same class as the C3.1 defect, same fix.
        "cypher": (
            "MATCH (s:Software {{name: '{pkg}'}})-[:HAS_VERSION]->(old:SoftwareVersion {{versionName: '{ver}'}}) "
            "OPTIONAL MATCH (old)-[:DEPENDS_ON*1..6]->(:SoftwareVersion)<-[:HAS_VERSION]-(os:Software) "
            "WITH s, collect(DISTINCT os.name) AS old_names "
            "MATCH (s)-[:HAS_VERSION]->(new:SoftwareVersion {{versionName: '{ver2}'}}) "
            "MATCH (new)-[:DEPENDS_ON*1..6]->(:SoftwareVersion)<-[:HAS_VERSION]-(ns:Software) "
            "WHERE ns.name <> s.name AND NOT ns.name IN old_names "
            "RETURN DISTINCT ns.name AS software ORDER BY software"
        ),
        "answer_shape": {"kind": "list", "columns": ["software"], "ordered": False},
        "strata": [
            ("new_extra_software", 0.52, PAIRS_NEW_EXTRA_SOFTWARE, "nonempty"),
            ("old_is_leaf", 0.12, PAIRS_OLD_IS_LEAF, "nonempty"),
            ("same_software", 0.24, PAIRS_SAME_SOFTWARE, "empty"),
            ("new_is_leaf", 0.12, PAIRS_NEW_IS_LEAF, "empty"),
        ],
    },
    "C9.7": {
        "family": "C9: Same package multi-version",
        "source": "luxu",
        "v3_id": None,
        "query_type": "CR",
        "difficulty": "Hard",
        "params": ("pkg", "ver", "ver2"),
        "question": "Which indirect vulnerable dependencies appear in the tree of software '{pkg}' version '{ver2}' but not in the tree of version '{ver}'?",
        # DEVIATION: `n <> new` added (same cycle guard as C9.6, measured at
        # 2/330 here - small but real). The sheet's `*2..6` + `NOT (new)-
        # [:DEPENDS_ON]->(n)` encoding of "indirect but not also direct" is
        # correct and is kept.
        "cypher": (
            "MATCH (s:Software {{name: '{pkg}'}})-[:HAS_VERSION]->(old:SoftwareVersion {{versionName: '{ver}'}}) "
            "OPTIONAL MATCH (old)-[:DEPENDS_ON*1..6]->(o:SoftwareVersion) "
            "WITH s, collect(DISTINCT o) AS old_nodes "
            "MATCH (s)-[:HAS_VERSION]->(new:SoftwareVersion {{versionName: '{ver2}'}}) "
            "MATCH (new)-[:DEPENDS_ON*2..6]->(n:SoftwareVersion)-[:VULNERABLE_TO]->(c:Vulnerability) "
            "WHERE n <> new AND NOT (new)-[:DEPENDS_ON]->(n) AND NOT n IN old_nodes "
            "OPTIONAL MATCH (ds:Software)-[:HAS_VERSION]->(n) "
            "RETURN DISTINCT ds.name AS software, n.versionName AS version, c.cveId AS cveId "
            "ORDER BY software, version, cveId"
        ),
        "answer_shape": {"kind": "table", "columns": ["software", "version", "cveId"], "ordered": False},
        "strata": [
            ("new_extra_indirect_vuln", 0.56, PAIRS_NEW_EXTRA_INDIRECT_VULN, "nonempty"),
            ("no_new_indirect_vuln", 0.32, PAIRS_NO_NEW_INDIRECT_VULN, "empty"),
            ("new_is_leaf", 0.12, PAIRS_NEW_IS_LEAF, "empty"),
        ],
    },
}
