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

**1,250 cases, 250 per template, 219 with an empty answer (17.5 %).** Ecosystem
split: crates.io 620, pypi.org 289, conan.io 189, absent 109,
sources.debian.org 43.

| id | sheet family | v3 id | shape | n | strata (drawn / pool) |
|---|---|---|---|---|---|
| C1.1 | C1 Entity / Version Lookup | C1.1 | list | 250 | multi_version 138/296 · single_version 75/835 · **absent_package 37/111** |
| C1.2 | C1 Entity / Version Lookup | **C1.3** | bool | 250 | exists 138/1650 · **near_miss_version 67/201** · **absent_package 45/135** |
| C2.1 | C2 Direct DEPENDS_ON (out) | C2.1 | table | 250 | has_direct_deps 168/1121 · **leaf_version 55/529** · **absent_package 27/81** |
| C2.2 | C2 Direct DEPENDS_ON (out) | C2.2 | bool | 250 | direct_dep 100/6241 · **grandchild_not_direct 88/20242** · **unrelated_dep 62/4000** |
| C2.3 | C2 Direct DEPENDS_ON (out) | — | list | 250 | direct_dep 150/6241 · **grandchild_not_direct 63/20242** · **unrelated_dep 37/4000** |

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

### On the quota

**250 bindings per template.** The pool column shows the C1 and C2 families are
nowhere near a capacity limit — C2.2 draws 100 of 6,241 available direct pairs —
so this number is a *design* choice, not a ceiling: it is what ~40 live templates
need to approach the 10,000-question target. Capacity binds later, on the
vulnerability-heavy templates, where only 47 versions in the graph carry a CVE;
those families ship with a documented ceiling and the per-template `n` column
reports it.

Quota is a build flag, not a property of the bank, so the whole bank can be
rebuilt at another n with one command. Two builder properties make that safe:
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
