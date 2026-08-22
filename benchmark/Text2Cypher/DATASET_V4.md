# Text2Cypher dataset v4 — the merged template bank

v4 is the merge of the two parallel template designs into one bank. It is being
built **one template family at a time**, and every template is validated against
the frozen graph before the next one lands. This document tracks what is in the
bank and what the build guarantees.

## Why v4 exists

Three decisions forced a rebuild rather than another patch of v3:

1. **The paraphrase layer is gone.** v3 multiplied 2,000 cases by five phrasings
   of the same question. That layer was cut, so the bank's only remaining
   multipliers are *templates* and *bindings* — the design has to grow along the
   template axis to grow at all.
2. **The bank is keyed on the shared sheet's numbering.** Both authors design
   into the same Google Sheet ("Template", 54 rows), so that numbering is now
   authoritative. The two designs' IDs collide semantically — sheet `C1.2` asks
   whether a version *exists*, v3 `C1.2` asks whether it has a *vulnerability* —
   so every template carries a `v3_id` field and **no per-template score may be
   joined across the two banks by ID alone.**
3. **Templates now have to prove they are scoreable, not just runnable.** v3
   validated that a gold query executes. v4 validates that its answer has a
   defined shape, a defined row order, and a stratum whose cases actually behave
   the way the stratum claims.

## Files

| file | what it is |
|---|---|
| `t2c_templates_v4.py` | the template registry: question, gold Cypher, answer shape, strata |
| `t2c_build_v4.py` | enumerates bindings, executes gold, validates, writes the bank |
| `t2c_purdue_dataset_v4.json` | the bank itself |
| `t2c_bindings_v4.json` | per-template binding report (pool sizes, draws, ecosystem split) |
| `../../tests/test_t2c_v4_validation.py` | tests for the validation checks (no Neo4j needed) |

```bash
# build a family, validate it, write nothing yet
python benchmark/Text2Cypher/t2c_build_v4.py --templates C1.1 C1.2 --dry-run --samples 9

# build for real; unnamed templates are carried over, retired ones are dropped
python benchmark/Text2Cypher/t2c_build_v4.py --templates C2.1 C2.2 C2.3 --quota 250
```

## What the build guarantees

Nothing enters the bank unvalidated:

| check | what it catches |
|---|---|
| V1 execute | a gold query that errors or does not terminate inside 60 s |
| V2 determinism | the gold is run **twice** and the row lists must be identical in order |
| V3 answer shape | undeclared columns; a bool/scalar template returning ≠ 1 row |
| V4 stratum expectation | an `absent_package` case that answered non-empty, i.e. enumeration drifted |
| V5 placeholders | an unfilled `{pkg}` surviving into the question or the query |
| V6 uniqueness | two cases with the same template and the same **parameters** (a slug collision such as `typing-extensions` / `typing_extensions` is two valid questions, so it gets a suffixed id instead of being dropped) |
| V7 template coherence | a list answer with **no `ORDER BY`**; a bool template with several columns; strata whose shares do not sum to 1 |
| V8 duplicate rows | a list answer containing the same row twice, i.e. a missing `DISTINCT` |

V7 is the one that pays for itself. A multi-row answer with no `ORDER BY` has no
defined row order; re-running it usually *happens* to return the same order, so
V2 alone cannot catch it. The check is regression-tested against the real defect
it was written for — the sheet's `C2.3`, which returns a bare `dep.versionName`
with neither `DISTINCT` nor `ORDER BY`.

Row order being *defined* is not the same as row order being *scored*: the
evaluator compares answers row-wise, so `ORDER BY` exists to make the build
reproducible. This is why `C1.1` on `syn` legitimately lists `2.0.101` before
`2.0.56` — the gold sorts version strings lexicographically, not by semver, and
the answer set is unaffected.

## In the bank so far

**2,690 cases, 11 templates, 590 with an empty answer (21.9 %).** 250 per
template except C3.5, which ships at its measured capacity of 190 (see the C3
notes below).

| id | sheet family | v3 id | shape | n | strata (drawn / pool) |
|---|---|---|---|---|---|
| C1.1 | C1 Entity / Version Lookup | C1.1 | list | 250 | multi_version 138/296 · single_version 75/835 · **absent_package 37/111** |
| C1.2 | C1 Entity / Version Lookup | **C1.3** | bool | 250 | exists 138/1650 · **near_miss_version 67/201** · **absent_package 45/135** |
| C2.1 | C2 Direct DEPENDS_ON (out) | C2.1 | table | 250 | has_direct_deps 168/1121 · **leaf_version 55/529** · **absent_package 27/81** |
| C2.2 | C2 Direct DEPENDS_ON (out) | C2.2 | bool | 250 | direct_dep 100/6241 · **grandchild_not_direct 88/20242** · **unrelated_dep 62/4000** |
| C2.3 | C2 Direct DEPENDS_ON (out) | — | list | 250 | direct_dep 150/6241 · **grandchild_not_direct 63/20242** · **unrelated_dep 37/4000** |
| C3.1 | C3 Transitive / path | C3.1 | table | 250 | has_indirect 125/1051 · direct_only 43/70 · **leaf_version 55/529** · **absent_package 27/81** |
| C3.2 | C3 Transitive / path | C3.2 | bool | 250 | **indirect_true 88/25000** · direct_true 37/6241 · **reverse_only 75/6000** · **unreachable 50/5000** |
| C3.3 | C3 Transitive / path | C3.3 | list | 250 | **unique_path_2plus 113/20000** · unique_path_direct 38/1286 · **no_path 62/5000** · **absent_dep 37/111** |
| C3.4 | C3 Transitive / path | — | table | 250 | has_dist2 155/1049 · **deps_no_dist2 40/72** · leaf 28/529 · absent 27/81 |
| C3.5 | C3 Transitive / path | — | scalar | **190** | depth_ge2 57/57 · depth_exactly1 70/70 · leaf 63/529 |
| C3.6 | C3 Transitive / path | — | table | 250 | has_indirect 155/1049 · **all_deps_direct 40/72** · leaf 28/529 · absent 27/81 |

Bold strata are new in v4; the sheet has no notion of a stratum, so a template
copied from it straight has no empty-answer case, no false case and no
absent-entity case.

### Which sheet rows are live

The shared sheet strikes rows out rather than deleting them, and a struck row is
not part of the bank. C2 lists six rows and strikes three: **C2.4** (names of the
direct dependencies), **C2.5** ("does it have any direct dependencies", which is
exactly `C4.1 > 0`) and **C2.6** (the "not directly depend" negation). Only C2.1,
C2.2 and C2.3 are built.

### C1: what the strata add

- **`absent_package` on C1.1.** Without it, "list the versions of X" has a
  non-empty answer in every single case, and a system that returns the versions
  of *something* is never punished. This is the only way the template can produce
  an empty answer at all.
- **`near_miss_version` on C1.2.** v3 built every negative from one sentinel
  string, `99.99.99`, which a model can learn to reject on sight. v4 asks about a
  version one bump away from a real one — `pkgconf 2.2.1` when the graph holds
  `2.2.0` — so a "no" has to come from the graph rather than from the shape of
  the string.
- **Near-miss package names.** Half of each `absent_package` stratum is drawn
  from names that are real in the wider world but provably absent here (npm has
  no presence in SecureChain at all: `react`, `lodash`); the other half are
  near-miss variants of in-graph names (`discard2`, `libhttp`,
  `importlib-metadatajs`). The KG identifies software by `schema:name` only, and
  the 2026-08-01 probe found genuine cross-ecosystem collisions, so these test
  entity resolution rather than testing whether a model recognises an obviously
  foreign name.

### C2: two grades of negative, and one gold repair

Every C2 question is about a **direct** dependency, so the interesting way to be
wrong is to answer the reachability question instead. The negatives are built in
two grades to measure exactly that:

- **`grandchild_not_direct`** — the named package sits exactly two hops down, so
  it *is* in the dependency tree and is *not* a direct dependency. A model that
  reads "depends on" as "reaches" answers these wrong. The pool is large
  (20,242 pairs), so this stratum scales with any quota.
- **`unrelated_dep`** — not within four hops in either direction. Kept so the
  false stratum is not made entirely of trick cases.
- **`leaf_version`** (C2.1) — a real version with no outgoing `DEPENDS_ON`. 529
  of the graph's 1,650 versions are leaves, so an empty answer here is an
  ordinary fact about the graph rather than a synthetic edge case.

**C2.3 deviates from the sheet, and the deviation is the point of V7.** The sheet
returns a bare `dep.versionName` with neither `DISTINCT` nor `ORDER BY` and calls
the result a single answer. Both parts are wrong on this graph: 21 (root,
dependency) pairs resolve to more than one version of the same dependency, so the
answer is a list, and an unordered list has no defined row order to build
against. v4 adds `DISTINCT` + `ORDER BY` and the question asks for
"version(s)", so the question asked and the answer scored are the same question.

### C3: the depth-6 convention, cycles, and a capacity ceiling

The sheet lists seven C3 rows; C3.7 is struck out, C3.1-C3.6 are live.

**The depth-6 convention.** Schema instruction 4 tells every model "Multi-hop:
`[:DEPENDS_ON*1..6]`", and every C3 gold uses the same bound, so gold and an
obedient model compute the same answer *by protocol*. This is not a formality:
993 of the 1,121 roots have dependency walks beyond 6 hops. For the two
templates where the convention would produce a wrong-or-degenerate answer the
bindings are restricted instead:

- **C3.5 (max depth) ships at 190, not 250.** Only 127 roots keep their whole
  walk structure inside 6 hops; for the other 994 the capped gold would answer
  a constant 6 — "always say 6" would score ~90% — while the true depth is
  larger. So C3.5 binds the 127 shallow roots (57 with depth ≥ 2, 70 with depth
  exactly 1) plus 63 leaves (answer 0), and the template declares `max_quota:
  190`. This is a property of the graph, reported, not papered over.
- **C3.2's false strata are exact, not conventional.** `reverse_only` and
  `unreachable` draw their roots from versions whose closure is fully inside 6
  hops, so "not reachable within 6" is "not reachable at all".

**Cycles: the root is not its own dependency.** This graph has real dependency
cycles (serde ↔ serde_derive), so a `*1..6` walk can return to the root and
list the software as a dependency of itself. The Python BFS cross-check caught
exactly this in **118 of 750** nonempty C3.1/C3.4/C3.6 answers before the
golds got `dep <> root`. The contract's dependency-listing clause now states
the same resolution.

**C3.3 (dependency path) is bound to provably unique answers.**
`shortestPath()` returns an arbitrary representative when several shortest
paths tie, so a scoreable path question must leave nothing to arbitrate:
nonempty cases bind only (root, dep) pairs with exactly one reachable target
version and exactly one shortest path (verified independently by a Python
BFS path-counting pass). The gold also gains `ORDER BY path` (V7).

**Two golds repaired the way the sheet's own C8.7 does it** (G2): C3.4
"exactly 2 hops away" and C3.6 "indirect (not direct)" both get
`WHERE NOT (root)-[:DEPENDS_ON]->(dep)` — without it a node that is both a
direct dependency and 2 hops away contradicts the question text. C3.4's
question says "(and not also direct dependencies)" so the asked question and
the scored answer coincide.

**Verification and smoke (2026-08-22):** all 1,440 C3 cases recomputed in
Python from the raw edge list (BFS reachability/distances, DFS walk depth,
BFS shortest-path counting — no Cypher shared with the golds): **0 failures**
after the cycle fix. Smoke (60 stratified cases, deepseek-v4-flash): bare
0.717 / contract 0.967; the contract's new depth clause turns C3.5's leaf
cases from 0.00 to 1.00, and C3.3 goes 0.30 → 1.00 under the path clause. The
remaining failures are the known Neo4j-5 pattern-expression weakness (C3.2).

### On the quota

**250 bindings per template.** The pool column shows the C1 and C2 families are
nowhere near a capacity limit — C2.2 draws 100 of 6,241 available direct pairs —
so this number is a *design* choice, not a ceiling: it is what ~40 live templates
need to approach the 10,000-question target. Capacity binds later, on the
vulnerability-heavy templates, where only 47 versions in the graph carry a CVE;
those families ship with a documented ceiling and the per-template `n` column
reports it.

Quota is a build flag, not a property of the bank, so the whole bank can be
rebuilt at another n with one command. A template may declare `max_quota` — a
capacity ceiling measured on the graph (C3.5: 190) — and ships short with the
ceiling reported rather than padding with degenerate cases. Two builder properties make that safe:
per-stratum quotas are allocated by largest remainder (so every template ships
exactly `n`, never `n + 1`), and each template is seeded independently
(`seed:template_id`), so rebuilding one template reproduces the cases it already
had instead of depending on which other templates were built alongside it.

## Graph snapshot

The frozen SecureChain subgraph, 11 anchors, imported 2026-08-01, container
`neo4j-securechain` on `bolt://127.0.0.1:7689`.

| element | count |
|---|---|
| `Software` | 1,131 |
| `SoftwareVersion` | 1,650 |
| `DEPENDS_ON` (SoftwareVersion→SoftwareVersion) | **6,286** |
| `VULNERABLE_TO` | 196 |
| `Vulnerability` | 155 |

> **Correction of the correction (2026-08-22).** The earlier note here claimed
> the docs' 6,286 was stale and 14,052 was the real count. Measured by label
> pair, both numbers are real but they count different things:
> `SoftwareVersion→SoftwareVersion` (the benchmark subgraph every gold query
> touches) has exactly **6,286** edges — the documented figure was right all
> along — while the remaining 7,766 `DEPENDS_ON` edges live between `Package`
> and `Native` nodes that DepsRAG's own `construct_dependency_graph` tool wrote
> into the same database. Every gold and every pool query pins the
> `:SoftwareVersion` label, so the benchmark is unaffected by the pollution;
> the table above counts the benchmark subgraph.
