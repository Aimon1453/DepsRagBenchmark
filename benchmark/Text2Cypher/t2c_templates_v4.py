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
}
