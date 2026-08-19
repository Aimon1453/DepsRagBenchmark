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

# build for real; templates not named are carried over from the existing file
python benchmark/Text2Cypher/t2c_build_v4.py --templates C1.1 C1.2 --quota 80
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
| V6 uniqueness | two cases with the same template and the same parameters |
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

| id | sheet family | v3 id | shape | n | strata (drawn / pool) |
|---|---|---|---|---|---|
| C1.1 | C1 Entity / Version Lookup | C1.1 | list | 80 | multi_version 44/296 · single_version 24/835 · **absent_package 12/20** |
| C1.2 | C1 Entity / Version Lookup | **C1.3** | bool | 80 | exists 44/1650 · **near_miss_version 22/66** · **absent_package 14/20** |

160 cases, 12 with an empty answer (7.5 %). Ecosystem split: crates.io 75,
absent 26, conan.io 24, pypi.org 22, sources.debian.org 13.

### What v4 adds to these two beyond a straight copy of the sheet

The sheet has no notion of a stratum, so a template copied from it has no
empty-answer case, no false case, and no absent-entity case. The bold strata
above are new:

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

### On the quota

80 bindings per template, matching v3 so per-template results stay comparable.
The pool column shows this is nowhere near a capacity limit: C1.1 could be bound
to all **1,131** packages in the graph. It is not, because the 900th "list the
versions of X" question adds cost and no discrimination. **The bank grows by
templates, not by bindings** — capacity only binds later, on the
vulnerability-heavy templates, where just 47 versions in the graph carry a CVE.

## Graph snapshot

The frozen SecureChain subgraph, 11 anchors, imported 2026-08-01, container
`neo4j-securechain` on `bolt://127.0.0.1:7689`.

| element | count |
|---|---|
| `Software` | 1,131 |
| `SoftwareVersion` | 1,650 |
| `DEPENDS_ON` | **14,052** |
| `VULNERABLE_TO` | 196 |
| `Vulnerability` | 155 |

> **Correction.** `DATASET_V2.md`, `DATASET_V3.md`, `README.md` and
> `t2c_v2_validation_report.md` state **6,286** `DEPENDS_ON`. That figure was
> measured immediately after the 2026-08-01 import; an edge-completion pass
> afterwards raised it to 14,052, and all 14,052 are *distinct* `(a, b)` pairs —
> not duplicate-import noise — with no node count changed. The v3 dataset was
> enumerated after that change: re-running 100 stored gold queries spanning all
> 25 v3 templates on 2026-08-19 produced **0 mismatches**, so the v3 bank and the
> four finished campaigns are consistent with the graph. Only the documentation
> is stale.
