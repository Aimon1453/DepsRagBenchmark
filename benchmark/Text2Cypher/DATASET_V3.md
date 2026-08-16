# Text2Cypher dataset v3 — how the 10,000 cases were built

`t2c_purdue_dataset_v3.json` scales the benchmark from 170 cases to **10,000**:

```
25 templates  (17 from v2 + 8 new)
  × 5 phrasings   → 125 natural-language question forms
  × 80 bindings   → 2,000 parameter bindings drawn from the frozen graph
  = 10,000 cases
```

v2 (170 cases) is kept as a regression set; the original 17-case file remains the
smoke test.

## What changed and why

**Scale.** 170 cases give roughly 10 observations per template. That is enough to
say a template is broken and not much else. At 400 per template, a per-template
score has a usable error bar, and the per-difficulty and per-phrasing cuts below
become reportable in their own right.

**Three multipliers, not one.** The three layers are deliberately separated
because they buy different things and cost different amounts:

| Layer | What it varies | What it measures | Cost |
|---|---|---|---|
| Template | The shape of the gold Cypher | Whether the model can express a query *structure* | Hand-written and hand-verified |
| Binding | Which packages/versions fill the placeholders | Whether the model's query survives real data (empty sets, 1,557-node closures) | Enumerated from the graph |
| Phrasing | How the question is worded | Whether the model is robust to surface form | Written once per template |

Reporting must keep them apart. The dataset has **structural diversity 25**,
**binding diversity 2,000**, and **surface diversity 10,000** — these are not the
same number and the paper should not present them as one.

**Eight new templates.** The v2 set could not distinguish a model that handles
`WHERE NOT` from one that does not, because no case contained a negation. v3 adds
negation (C2.5, C5.4), exact-depth constraints (C3.4), top-N ordering (C4.6), set
difference (C5.4), and traversal against the direction of `DEPENDS_ON` (C3.5,
C3.6) — the last of which answers the question supply-chain security actually
asks: *what depends on this?*

**Difficulty is now derived, not assigned.** See
[`DIFFICULTY_RUBRIC.md`](DIFFICULTY_RUBRIC.md). Labels come from counting
structural factors in the gold Cypher, which relabelled seven v2 templates whose
hand-assigned labels were not self-consistent.

## The graph underneath

Unchanged from v2: the frozen 11-anchor import, snapshot **2026-08-01**.
Re-measured while building v3:

| | |
|---|---|
| `Software` / `SoftwareVersion` | 1,131 / 1,650 |
| `DEPENDS_ON` / `VULNERABLE_TO` | 6,286 / 196 |
| `Vulnerability` / `VulnerabilityType` | 155 / 49 |
| Versions with ≥1 direct dependency | 1,121 |
| Versions with ≥1 CVE | **47** (only 19 have ≥2) |
| Versions that nothing depends on | **11** |

Two of these numbers are binding constraints on the dataset and are discussed
under Limitations.

## Construction

### 1. Bindings — `t2c_enumerate_bindings.py`

v2's 10 bindings per template were hand-curated. 2,000 cannot be, so they are
drawn from the graph under a stratified policy.

Each template declares **strata**: candidate pools that differ in the *kind* of
answer they produce — populated vs empty, true vs false, reachable vs
unreachable. Quotas across strata are what stop the dataset being gameable: if
every question had a non-empty answer, a system that always returns something
would never be punished. **12.8% of v3 cases have an empty gold result**, and the
evaluator scores empty-against-empty as correct, so these are valid cases rather
than filler.

Within a stratum, candidates are drawn **round-robin across ecosystems**. This
matters more than it sounds. By version count the frozen graph is 94% crates.io
(1,558 of 1,650, nearly all dragged in by the single `click 0.6.1` anchor), so
proportional sampling would have produced a benchmark that calls itself
"supply chain" but is really a Rust benchmark. Round-robin spends the small
PyPI / Conan / Debian pools first and lets crates.io fill the remainder:

| Ecosystem | Versions in graph | Share of v3 cases |
|---|---|---|
| crates.io | 94.4% | 49.4% |
| pypi.org | 2.3% | 19.6% |
| conan.io | 2.1% | 19.3% |
| sources.debian.org | 1.2% | 11.1% |

Sampling is seeded (`--seed`, default 20260816), so the same graph and seed
reproduce the same bindings.

Four templates cannot reach the 80-binding quota and are capped by the graph:
C3.5 and C3.6 at 68, because only 11 versions in the whole import have no
dependents. The shortfall is redistributed to templates that still have
candidates, so the total lands on exactly 2,000 rather than forcing a uniform
quota the graph cannot support.

### 2. Phrasings — `PHRASINGS` in `t2c_generate_dataset.py`

Five forms per template, on a fixed axis so per-phrasing scores are
interpretable:

| | Style | Example (C2.1) |
|---|---|---|
| P1 | canonical (the v2 wording) | What are the direct dependencies of software 'flask' version '2.3.3'? |
| P2 | colloquial | What does flask 2.3.3 depend on directly? |
| P3 | imperative | List the direct dependencies of software 'flask' version '2.3.3'. |
| P4 | scenario | I'm auditing flask 2.3.3 — which packages does it pull in directly? |
| P5 | terse | flask 2.3.3 direct dependencies? |

P1 is preserved verbatim from v2, so v2 results remain comparable against the P1
slice of v3.

### 3. Gold answers — `t2c_generate_dataset.py`

Unchanged in principle from v2: the oracle is *defined* as the gold query's
result on the frozen graph, so dataset and graph cannot drift apart. Rerunning
the generator after a re-import restores consistency by construction.

One optimisation matters at this scale: the gold Cypher is executed **once per
binding, not once per case**. The five phrasings of a binding ask the same
question, so a 10,000-case build costs 2,000 database round-trips. Gold execution
now runs under a 60-second transaction timeout — a generated query is not the
only thing that can fail to terminate on the `click` closure, and dataset
generation must not be the step that hangs a build.

## Distribution

| Cut | |
|---|---|
| Difficulty | Hard 4,390 / Medium 3,175 / Easy 2,435 |
| Query type | SA 5,275 / CR 4,325 / SR 400 |
| Phrasing | 2,000 each, exactly balanced |
| Empty gold result | 1,285 (12.8%) |
| Distinct case ids | 10,000 / 10,000 |
| Duplicate question strings | 0 |

Per-template answer diversity is reported by the generator's summary. Two
patterns are expected and worth stating plainly:

- **Boolean templates (C1.2, C1.3, C2.2, C3.2) have exactly 2 distinct answers.**
  A model that always answers "true" scores near the true-rate. Per-template
  accuracy on these should be read against that baseline, not against zero.
- **C5.1 needed its sampling fixed.** The first draw paired versions at random
  and produced 89% empty results with 6 distinct answers, because two randomly
  chosen versions in a 1,650-node graph almost never share a dependency. The
  sharing stratum is now read out of the graph directly; it is 23% empty with 40
  distinct answers.

## Validation

- **Freeze check:** all 170 v2 cases regenerate byte-identical
  `expected_result` values under the v3 code path (170/170).
- **Gold execution:** 2,000/2,000 gold queries execute; none hit the 60s timeout.
- **Integrity:** 10,000 unique ids, 0 duplicate question strings.
- **End-to-end:** a stratified 50-case sample (2 per template) run through
  `t2c_single_agent_evaluator.py` on gemini-3.1-flash-lite with the format
  contract: 48/50 executed, avg 0.851, and the difficulty ordering holds
  (Easy 0.92 > Medium 0.85 > Hard 0.81). Every new template is answerable and
  discriminating — scores land between 0.50 and 1.00 rather than all-or-nothing.

Throughput on that sample was 0.49 s/case at 8 workers, which extrapolates to
roughly 80 minutes per model for the full 10,000, or about 40 at 16 workers.

## Limitations

**Vulnerability coverage is thin, and this bounds what v3 can say about CVE
reasoning.** Only 47 versions in the entire import carry a CVE link and only 19
carry more than one. Every vulnerability-side template therefore draws its
positive cases from the same small pool, and roughly half of their bindings
answer "none" or "zero". Those are legitimate cases, but a model can score well
on the CVE templates while being poor at the ones that actually retrieve CVE
data. Fixing this needs CVE-dense anchors imported, not more sampling.

**Ecosystem balance is engineered, not natural.** The 49/20/19/11 split above is
the product of the round-robin policy; the underlying graph is 94% crates.io.
The claim v3 supports is "the benchmark covers four ecosystems", not "the
benchmark reflects their real-world proportions".

**Surface diversity is 10,000; structural diversity is 25.** A model that cannot
express a template's structure fails all 400 of its cases. The phrasing layer
measures robustness to wording, which is a real property, but it does not add
query-structure coverage.

**Known KG noise is retained, not cleaned:** nodes sourced from `www.google.com`,
version strings like `*` and `^4.6.1`, and package names that collide across
ecosystems (`click`, `flask`, and `cryptography` exist on both PyPI and
crates.io). Because the gold Cypher matches `Software {name: ...}` on name alone,
a colliding name is genuinely ambiguous; the enumerator deduplicates such
bindings rather than emitting both. The benchmark measures systems on the graph
as it is.

## Regenerating

```bash
# Graph must be running with the 11-anchor import (securechain_import/README.md).
# NOTE: run_neo4j_securechain.ps1 recreates the container and destroys the
# imported graph. To restart an existing container use: docker start neo4j-securechain

python benchmark/Text2Cypher/t2c_enumerate_bindings.py                     # -> t2c_bindings_v3.json
python benchmark/Text2Cypher/t2c_generate_dataset.py \
    --bindings t2c_bindings_v3.json --out t2c_purdue_dataset_v3.json       # -> 10,000 cases

python benchmark/Text2Cypher/t2c_single_agent_evaluator.py \
    --dataset t2c_purdue_dataset_v3.json --workers 16 --format-hints
```

`t2c_enumerate_bindings.py --report-only` prints per-template candidate capacity
without writing, which is the quickest way to see what the graph can support
before changing a quota.

## Open items before freezing

- The **125 phrasing strings need a human read-through**. They were drafted to a
  fixed style axis, but a phrasing that quietly changes the question changes the
  gold answer, and no automated check catches that.
- **Instance-level QA sampling** (2–3 cases per template per difficulty) has not
  been done; only the 50-case end-to-end sample above.
- The Hard tier is 44% of cases. If it proves too coarse to discriminate, the
  rubric threshold is the knob — see the last section of `DIFFICULTY_RUBRIC.md`.
