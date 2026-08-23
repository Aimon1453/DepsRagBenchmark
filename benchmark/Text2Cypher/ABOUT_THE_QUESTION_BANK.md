# About the question bank

**9,317 questions that test whether a model can turn a natural-language question
about a software supply chain into a correct Cypher query.**

Each question comes with a *gold* Cypher query and the exact answer that query
returns on our Neo4j graph. A model under test never sees the gold query — it only
sees the question. We run the Cypher it writes and compare its result table with
the gold one, so the score measures the answer, not the wording of the query.

👉 **To see real questions, open [`SAMPLE_QUESTIONS.md`](SAMPLE_QUESTIONS.md)** —
every one of the 41 templates with a real question drawn from it, its gold Cypher,
and its gold answer.

---

## How 41 templates become 9,317 questions

A **template** is a question with holes in it, plus the Cypher that answers it:

> What are the direct dependencies of software `{pkg}` version `{ver}`?
> ```cypher
> MATCH (s:Software {name: '{pkg}'})-[:HAS_VERSION]->(root:SoftwareVersion {versionName: '{ver}'})
> MATCH (root)-[:DEPENDS_ON]->(dep:SoftwareVersion)
> ...
> ```

A **binding** fills those holes with a real package and version from the graph.
We fill each template 250 times, so one template gives 250 questions. For every
binding the generator runs the filled-in gold query against Neo4j and stores the
result as the correct answer.

| Family | Questions | What it asks about |
|---|---|---|
| C1 | 500 | which packages and versions exist |
| C2 | 750 | direct dependencies |
| C3 | 1,440 | transitive dependencies and paths |
| C4 | 500 | counting dependencies |
| C5 | 1,420 | comparing two packages |
| C6 | 657 | CVEs and CWEs of one version |
| C7 | 750 | reverse direction — who depends on this |
| C8 | 1,750 | vulnerabilities inside a dependency tree |
| C9 | 1,550 | comparing versions of the same package |

Seven templates ship fewer than 250 questions because the graph runs out of
material — mostly vulnerability data, which is thin (only 47 versions in the graph
carry a CVE).

## The bindings are not picked at random

If every question had a real answer, a system that always replies with *something*
would never be caught. So each template draws its 250 bindings from several
groups, and some groups are chosen so that **"nothing" is the correct answer**:

- a package that does not exist in the graph at all,
- a version with no dependencies, so the honest answer is an empty list,
- a package that *is* in the dependency tree but two hops down, when the question
  asks about **direct** dependencies only.

**2,294 of the 9,317 questions (24.6%) have an empty gold answer** for reasons like
these. The last group is the interesting one: it separates a model that understands
"directly depends on" from one that reads it as "is somewhere in the tree".

## How we know the gold answers are right

Every answer is computed **twice, by two programs that share no code**.

1. While building, the generator runs the gold query against Neo4j and checks eight
   things — that it executes, returns the same rows twice, has the shape the
   template declared, and produces what its group claims it will.
2. Afterwards, a separate program reads the raw graph edges into Python and
   recomputes the same answer from scratch — breadth-first search for reachability,
   plain set algebra for the comparisons — never reusing the gold Cypher.

All 9,317 agree. The scripts that do the second pass are in
[`verification/`](verification/), one per family.

This second pass is what found the real problems: three gold queries in our shared
template sheet were quietly wrong (two returned only part of the answer, one could
not run at all on the packages it was about). None of those were caught by the
build-time checks, because the queries executed perfectly well — they just answered
the wrong question.

## Files

| File | What it is |
|---|---|
| [`SAMPLE_QUESTIONS.md`](SAMPLE_QUESTIONS.md) | **start here** — one real question per template |
| `t2c_purdue_dataset_v4.json` | the bank itself (26 MB, too large for GitHub to display) |
| `t2c_templates_v4.py` | the 41 templates and their binding groups |
| `t2c_build_v4.py` | the generator |
| [`verification/`](verification/) | the independent re-computation scripts |
| `DATASET_V4.md` | the full build log — every design decision and defect, for our own reference |
