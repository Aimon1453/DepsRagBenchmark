# Text2Cypher v2 Dataset — Validation Report (2026-08-01)

Validation of the scaled 170-case gold dataset (`t2c_purdue_dataset_v2.json`)
by running it end-to-end through the single-agent Text2Cypher track. This is a
**dataset validation run**, not the official G1 baseline (G1's LLM choice is
still pending supervisor confirmation).

## Setup

| Item | Value |
|---|---|
| Dataset | `t2c_purdue_dataset_v2.json` — 170 cases, 17 templates × 10 instances |
| System under test | DependencyGraphAgent **only** (single-agent track; no team coordinator, no Search/Critic) |
| Runner | `t2c_single_agent_evaluator.py` (persists per-case results) |
| Model | `gemini-3.1-flash-lite` |
| Graph | Frozen SecureChain subgraph, 11 anchors, snapshot 2026-08-01 (1650 SoftwareVersion / 6286 DEPENDS_ON) |
| Scoring | F1 (SR/CR) and Exact Match (SA) on normalized result sets, per `t2c_evaluator.py` |

Two runs were executed to separate *answer-shape* effects from *query-generation* ability:

- **Run 1 — bare protocol**: schema instructions only (same contract as the original 17-case evaluator).
- **Run 2 — answer-format contract**: schema instructions + an explicit contract for the expected
  result shape per question kind (`--format-hints`; see `ANSWER_FORMAT_INSTRUCTIONS` in the runner).

## Headline results

| | Run 1 (bare) | Run 2 (format contract) |
|---|---|---|
| Execution rate | 166/170 (97.7%) | 155/170 (91.2%) |
| Average score (F1/EM) | **0.682** | **0.882** |

Result files: `validation_results_v2_gemini.json`, `validation_results_v2_gemini_hints.json`
(per-case generated Cypher, execution status, metrics, latency).

## Per-template comparison

| Template | Run 1 | Run 2 | Δ | Reading |
|---|---|---|---|---|
| C1.1 versions | 1.00 | 1.00 | — | trivially solved |
| C1.2 has-vuln? | 0.10 | 0.20 | +0.10 | see failure class D |
| C1.3 exists? | 1.00 | 1.00 | — | negatives handled correctly |
| C2.1 direct deps | 0.60 | 1.00 | +0.40 | class A, fixed by contract |
| C2.2 direct dep on X? | 1.00 | 0.60 | −0.40 | contract side-effect, class D |
| C2.3 CVE ids | 1.00 | 1.00 | — | |
| C2.4 CWE ids | 1.00 | 1.00 | — | |
| C3.1 transitive deps | 0.60 | 1.00 | +0.40 | class A, fixed |
| C3.2 dep at any depth? | 1.00 | 0.50 | −0.50 | contract side-effect, class D |
| C3.3 dependency path | 0.10 | 0.70 | +0.60 | class C, mostly fixed |
| C4.1 count direct | 1.00 | 1.00 | — | |
| C4.2 count transitive | 0.90 | 1.00 | +0.10 | includes the 1557-node stress case |
| C4.3 count CVEs | 1.00 | 1.00 | — | |
| C4.4 count CWEs | 0.90 | 1.00 | +0.10 | |
| C5.1 common deps | 0.40 | 1.00 | +0.60 | class A, fixed |
| C5.2 more deps? | 0.00 | 1.00 | +1.00 | class B, fixed |
| C5.3 more CVEs? | 0.00 | 1.00 | +1.00 | class B, fixed |

## Failure taxonomy

**A. Projection mismatch (Run 1; eliminated by the contract).** The model's query is
semantically right but projects different columns than the gold query — e.g. returning
dependency *names* where the gold returns *(software, version)* pairs. Strict set
comparison scores this 0. Affected C2.1 / C3.1 / C5.1.

**B. Comparison-shape mismatch (Run 1; eliminated by the contract).** For "which has
more X?" the model returns a winner label or a `CASE` expression; the gold returns the
two counts (per the template design note). EM can never match. Also produced the only
Run-1 execution failures (4 cases: implicit-grouping aggregation errors in `CASE`
constructions). Affected C5.2 / C5.3 — without a contract these templates are
non-discriminative (constant 0 for any reasonable model).

**C. Path-shape mismatch (Run 1; mostly eliminated).** The model returns whole `path`
objects over any path; the gold returns the `versionName` list of a `shortestPath`.
Affected C3.3 (0.10 → 0.70).

**D. Boolean-question failures (genuine weaknesses; *surfaced more* under the contract).**
With the boolean hint, the model gravitates to pattern-expression booleans:

- `RETURN EXISTS((v)-[:DEPENDS_ON]->(x))` / pattern expression in `RETURN` — **removed in
  Neo4j 5**; syntax error (8× C1.2, 4× C3.2, 3× C3.3 in Run 2).
- Failure to aggregate over multiple candidate versions of the target software — one
  boolean row *per version* instead of one overall answer (C2.2/C3.2).
- Predicate bugs: `count(ds.name = 'pycparser') > 0` (counts non-null expressions, always
  true), or dropping the target filter entirely (`count(ds) > 0` for "depends on six?").

Class D is real Text2Cypher weakness (dialect + aggregation semantics), exactly the kind
of failure mode the benchmark's failure-recording protocol wants to capture. Note the
interaction effect: in Run 1 the same C2.2/C3.2 cases passed because the model defaulted
to simple `count(...) > 0` chains; the hint's `EXISTS` framing made it worse.

## Dataset verdict

- All 170 cases load, run, and score through the existing evaluator machinery; ids are
  unique; the extra `params` field is ignored by the evaluator as expected.
- Gold-side integrity was verified separately: every gold query re-executed on the frozen
  graph reproduces its stored `expected_result` (by construction via the generator).
- Score variance across templates comes from answer-shape policy and genuine model
  weaknesses — not from broken gold data. **The dataset is fit for purpose.**

## Decision items for cross-review (step 4)

1. **Adopt the answer-format contract into the official protocol?** Recommended: yes —
   it makes scores measure Cypher generation rather than projection guessing, and it is
   uniform across systems. It should be documented in the thesis's scoring-protocol
   section as part of the task instructions.
2. **Boolean-question guidance.** Option (a): keep the current neutral hint and report
   class-D failures as findings (recommended — the schema block already documents the
   dialect, and Neo4j-5 syntax competence is part of what Text2Cypher measures).
   Option (b): extend the schema instructions with "pattern expressions are not valid in
   `RETURN` in Neo4j 5" — reduces syntax noise but starts teaching to the test.
3. **C1.2 question wording.** "Does X have any known vulnerabilities?" invites listing
   CVEs (Run 1) — decide whether the boolean gold is the right contract or whether the
   template should accept either shape.

## Reproduce

```bash
# Run 1 (bare)
python benchmark/Text2Cypher/t2c_single_agent_evaluator.py --dataset t2c_purdue_dataset_v2.json --provider google
# Run 2 (answer-format contract)
python benchmark/Text2Cypher/t2c_single_agent_evaluator.py --dataset t2c_purdue_dataset_v2.json --provider google --format-hints
```
