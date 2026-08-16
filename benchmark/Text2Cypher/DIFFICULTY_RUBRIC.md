# Difficulty rubric — how a case gets labelled Easy / Medium / Hard

Difficulty is a property of the **gold Cypher's structure**, not of how hard the
question feels in English. Every template is scored by counting structural
factors, so two people scoring the same template independently get the same
label, and a new template's difficulty falls out of its query rather than being
argued about.

Instances inherit their template's label: filling `{pkg}` with a package that
has 1,557 transitive dependencies instead of 8 changes the *size* of the answer,
not the *structure* of the query.

## Factors

Each factor present in the gold Cypher scores 1 point. A factor counts once no
matter how many times it appears.

| # | Factor | Recognised by |
|---|---|---|
| F1 | Fixed multi-hop | A path of 2–3 named relationships, e.g. `VULNERABLE_TO` then `VULNERABILITY_TYPE` |
| F2 | Variable-length traversal | `[:DEPENDS_ON*1..6]`, or `shortestPath` |
| F3 | Exact-depth constraint | `[:DEPENDS_ON*n..n]` with n ≥ 2 — the model must pin both bounds |
| F4 | Numeric aggregation | `count()`, `sum()`, `max()` returning a number. **Existence booleans do not count** (see below) |
| F5 | Negation | `WHERE NOT (...)`, `NOT EXISTS` |
| F6 | Multi-entity | Two independently anchored subjects (`{pkg}`/`{ver}` *and* `{dep}`/`{dep_ver}`) |
| F7 | Ordering + LIMIT | Top-N: `ORDER BY ... LIMIT` where the ordering decides the answer |
| F8 | Set operation | Intersection or difference between two anchors' neighbourhoods |
| F9 | Direction reversal | Traversing `DEPENDS_ON` backwards (dependents rather than dependencies) |

**Existence booleans are not aggregation.** `RETURN count(c) > 0 AS has_vuln`
uses `count()` syntactically, but the model only has to express "does this
pattern match", which is the same cognitive step as writing the pattern. A
question whose answer is a *number the model must compute* scores F4; a question
whose answer is yes/no does not.

## Thresholds

| Factors | Difficulty |
|---|---|
| 0 | Easy |
| 1 | Medium |
| ≥ 2 | Hard |

## Applied to the v3 template set

| Template | Factors | Difficulty | v2 label |
|---|---|---|---|
| C1.1 available versions | — | Easy | Easy |
| C1.2 has vulnerabilities? | — | Easy | Easy |
| C1.3 version exists? | — | Easy | Easy |
| C2.1 direct dependencies | — | Easy | Easy |
| C2.2 directly depends on? | — | Easy | Easy |
| C2.3 CVE ids | — | Easy | *Medium* |
| C2.4 CWE ids | F1 | Medium | Medium |
| C2.5 direct deps with no CVEs | F5 | Medium | *new* |
| C3.1 all transitive dependencies | F2 | Medium | Medium |
| C3.2 depends at any depth? | F2 | Medium | Medium |
| C3.3 dependency path | F2, F6 | Hard | Hard |
| C3.4 dependencies at exactly depth N | F3 | Medium | *new* |
| C3.5 direct dependents | F9 | Medium | *new* |
| C3.6 transitive dependents | F2, F9 | Hard | *new* |
| C4.1 count direct dependencies | F4 | Medium | *Easy* |
| C4.2 count transitive dependencies | F2, F4 | Hard | *Medium* |
| C4.3 count CVEs | F4 | Medium | *Easy* |
| C4.4 count distinct CWE types | F1, F4 | Hard | *Easy* |
| C4.5 count vulnerable transitive deps | F2, F4 | Hard | *new* |
| C4.6 direct dep with most versions | F4, F7 | Hard | *new* |
| C4.7 distinct packages in closure | F2, F4 | Hard | *new* |
| C5.1 common direct dependencies | F6, F8 | Hard | Hard |
| C5.2 which has more dependencies | F4, F6 | Hard | *Medium* |
| C5.3 which has more CVEs | F4, F6 | Hard | *Medium* |
| C5.4 dependencies of A not in B | F5, F6, F8 | Hard | *new* |

Italics mark labels that differ from v2. **v2's labels were assigned by hand and
are not self-consistent** — C3.1 and C4.2 have the same traversal but were both
called Medium although C4.2 additionally aggregates; C4.4 walks two hops and
aggregates yet was called Easy. v3 applies the rubric uniformly, which is the
point of having one.

Resulting distribution: **Easy 6 / Medium 8 / Hard 11** (24% / 32% / 44%),
against v2's 8 / 7 / 2 (47% / 41% / 12%).

## Why the distribution moved this far

Two things happened at once. The rubric promoted templates that combine factors
(most of the C4 and C5 families), and the eight new templates were deliberately
chosen to add factors the v2 set never exercised: negation (F5), exact depth
(F3), top-N (F7), set difference (F8), and direction reversal (F9). Before v3
the benchmark could not distinguish a model that handles `WHERE NOT` from one
that does not, because no case contained a negation.

If the Hard tier turns out to be too large to discriminate usefully — everything
scoring near zero carries no information — the threshold is the knob to turn:
moving Hard to "≥ 3 factors" yields Easy 6 / Medium 13 / Hard 6. That change
should be made before freezing, not after seeing the scores.
