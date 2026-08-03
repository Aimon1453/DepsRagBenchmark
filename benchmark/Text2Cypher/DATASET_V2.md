# Text2Cypher dataset v2 — how the 170 cases were built

`t2c_purdue_dataset_v2.json` scales the original 17-case gold dataset to **170 cases
(17 templates × 10 instances)**. This page documents how the cases were constructed,
what the underlying graph looks like, and how the dataset was validated, so the
process can be checked and repeated.

The original 17-case file (`t2c_purdue_dataset.json`) is kept as a smoke test.

## Why scale up

The 17-case set has exactly one instance per template, all bound to the same anchor
(`requests` 2.31.0, plus `django` for comparisons). That is enough to check the
pipeline, but not enough to score a system:

1. **One instance per template is one data point.** A template score of 0 or 1 tells
   us nothing about whether the failure is systematic. With 10 instances per template,
   a per-template average starts to mean something.
2. **All-positive questions are gameable.** If every question has a non-empty answer,
   a system that always returns *something* is never punished. v2 mixes positive,
   negative, and empty-result instances (12 cases, ~7%, have an empty gold result —
   the evaluator scores empty-vs-empty as a match, so these are valid gold cases).
3. **A single small anchor hides scale effects.** The `requests` closure is 4 versions /
   3 edges. Whether generated Cypher survives a 1,500-node closure is a different
   question, and v2 asks it.

## The graph under the dataset

All gold results are frozen against one Neo4j import (snapshot **2026-08-01**):
**11 anchors**, giving 1,131 `Software` / 1,650 `SoftwareVersion` / 6,286 `DEPENDS_ON` /
196 `VULNERABLE_TO` in total. Anchors were chosen after probing the SecureChain SPARQL
endpoint for what is actually there — the public graph is sparse, and several
assumptions did not survive contact with the data (see "Findings" below).

| Anchor | Ecosystem (host) | Closure (versions / edges / vuln links) | Role |
|---|---|---|---|
| requests 2.31.0 | pypi.org | 4 / 3 / 1 | original anchor, kept |
| django 4.2.11 | pypi.org | 4 / 3 / 15 | original anchor, kept |
| flask 2.3.3 | pypi.org | 9 / 9 / 0 | small, modern, no CVEs |
| aiohttp 3.8.0 | pypi.org | 11 / 12 / 12 | mid-size with CVEs |
| cryptography 2.2.0 | pypi.org | 6 / 5 / 7 | both deps and CVEs |
| urllib3 1.18.0 | pypi.org | 1 / 0 / 10 | leaf with CVEs (empty dep sets) |
| pandas 2.0.0 | pypi.org | 6 / 5 / 0 | small and clean |
| click 0.6.1 | crates.io | **1,558 / 6,184 / 45** | stress anchor for deep traversals |
| openssl 0.9.6c-2.woody.7 | sources.debian.org | 20 / 19 / 87 | CVE-heavy |
| ffmpeg 4.2.1 | conan.io | 34 / 46 / 33 | mid-large, both |
| js-lodash 4.17.4 | pypi.org | 2 / 0 / 0 | kept for negative/existence cases only |

Binding values are not limited to anchor packages: any `SoftwareVersion` inside an
imported closure is a legal target (e.g. Rust crates reached through `click`'s
closure, or Debian's `perl` with 19 versions). This is what makes 170 distinct,
non-repetitive instances possible on a sparse graph.

### Findings from the probing pass

These affected the design and are worth knowing before extending the dataset:

- **npm does not exist in the SecureChain KG.** There is no npmjs-hosted node at all;
  the `js-lodash` anchor is actually a PyPI mirror package with 2 versions and no
  dependencies. The ecosystem story of this dataset is therefore
  PyPI + crates.io + Debian + Conan.
- **Package names collide across ecosystems.** `click`, `flask`, and `cryptography`
  also exist on crates.io. An import that resolves a name to its first SPARQL hit can
  silently import the wrong ecosystem. `securechain_import` gained a `--host` filter
  (e.g. `--host pypi.org`) for this reason, and every v2 anchor was imported with it.
- **The KG carries noise**: nodes with `www.google.com` as host, version strings like
  `*` and `^4.6.1`. Bindings avoid these; they are documented as a data-quality
  limitation rather than cleaned away, since the benchmark should measure systems on
  the graph as it is.

## Construction method

The dataset is generated, not hand-written, by
[`t2c_generate_dataset.py`](t2c_generate_dataset.py):

1. The 17 template definitions (NL question + gold Cypher, identical wording to
   `t2c_purdue_cypher_templates.md` and the 17-case file) live in the script.
2. Each template has **10 hand-curated bindings** (a `params` dict). Curation used a
   graph exploration pass to find suitable values: packages with many versions,
   packages with zero dependencies, version pairs with known common dependencies,
   unreachable pairs for path queries, and so on. Comments in the script mark each
   binding as positive / negative / empty-result.
3. For every (template, binding) pair the script fills the question and the gold
   Cypher, **executes the gold Cypher on the frozen graph**, and stores the result as
   `expected_result`.

The oracle is therefore *defined* as the gold query's result on the frozen graph.
There is no separately maintained answer key that can drift: after any re-import,
rerunning the generator restores dataset–graph consistency by construction. (After
the 2026-08-01 re-import, the 17 original cases were also re-executed as a freeze
check: 17/17 stored results still match.)

Deliberate coverage choices, per template family:

- **C1.3 (existence)** gets three kinds of negatives: version missing, package
  missing, and an npm package that cannot exist in this KG.
- **C2.2 (direct dependency?)** uses transitive-but-not-direct dependencies as
  distractors — the strongest wrong-answer temptation.
- **C3.1 / C4.2 (transitive)** include closures of 940, 1,139, and 1,557 nodes on the
  `click` / `tokio` subgraphs.
- **C5.2 / C5.3 (comparisons)** cover both winners and a 0-vs-0 tie.
- **C3.3 (path)** includes an unreachable pair (empty path is the gold answer).

### Example cases

A stress instance (transitive count over the full `click` closure):

```json
{
  "id": "C4_2-click-0_6_1",
  "template_id": "C4.2",
  "params": { "pkg": "click", "ver": "0.6.1" },
  "query_type": "SA",
  "difficulty": "Medium",
  "question": "How many total transitive dependencies does software 'click' version '0.6.1' have?",
  "cypher_query": "MATCH (s:Software {name: 'click'})-[:HAS_VERSION]->(root:SoftwareVersion {versionName: '0.6.1'}) OPTIONAL MATCH (root)-[:DEPENDS_ON*1..6]->(dep:SoftwareVersion) RETURN count(DISTINCT dep) AS cnt",
  "expected_result": [ { "cnt": 1557 } ]
}
```

The case schema is identical to the 17-case file; the only addition is the `params`
field (full binding record), which the evaluator ignores. Distribution:
Easy 80 / Medium 70 / Hard 20; SA 100 / CR 60 / SR 10.

## Validation

The full dataset was run end-to-end through the single-agent track
(`t2c_single_agent_evaluator.py`, DependencyGraphAgent only) before freezing.
Details and per-template tables: [`t2c_v2_validation_report.md`](t2c_v2_validation_report.md).

| Run | Model | Protocol | Executed | Avg score |
|---|---|---|---|---|
| 1 | gemini-3.1-flash-lite | bare (schema instructions only) | 166/170 (97.7%) | 0.682 |
| 2 | gemini-3.1-flash-lite | + answer-format contract (`--format-hints`) | 155/170 (91.2%) | 0.882 |
| 3 | gemini-3.1-pro | bare | 147/170 (86.5%) | 0.787 |

Per-case outputs (generated Cypher, execution status, metrics, latency) are in
`validation_results_v2_gemini.json`, `..._gemini_hints.json`, `..._gemini_pro.json`.

Two conclusions matter for the dataset itself:

1. **The gold side holds.** All 170 cases load, run, and score through the unchanged
   evaluator; every score difference between runs traces to answer-shape policy or to
   genuine model mistakes, not to broken gold data.
2. **The score spread is informative, not noise.** Without a format contract, three
   templates (C5.1/C5.2/C5.3) fail mostly on *projection shape* rather than query
   logic — the contract fixes exactly those (C5.2/C5.3: 0.00 → 1.00) while surfacing
   real weaknesses elsewhere (Neo4j-5 pattern-expression syntax in boolean templates).
   A dataset where every case scores 1.0 or 0.0 regardless of protocol would not be
   worth running; this one discriminates.

The validation run also caught two harness gaps now fixed in the runner: a generated
query with an unbounded `DEPENDS_ON*` (no `*1..6` bound) ran for 96 minutes on the
`click` closure before being killed — hence the per-query timeout (default 30 s,
scored as a failed execution) — and an LLM-provider quota error was once scored as
generated Cypher — hence the provider-error guard (provider failures are excluded
from scoring, never counted as model mistakes).

## Why 170 and not more

Scaling further is cheap mechanically (bindings are a dict; the generator does the
rest), but the graph runs out of material where it matters most: across the whole
import only **47 versions carry any CVE link, and 14 carry three or more**. The six
vulnerability templates would start recycling the same handful of packages well
before 20 instances each. Dependency-side material is plentiful (1,121 versions with
direct dependencies), so if the dataset grows, the honest way is importing more
CVE-dense anchors first, not padding instance counts.

## Regenerating / extending

```bash
# Graph must be running with the 11-anchor import (see securechain_import/README.md)
python benchmark/Text2Cypher/t2c_generate_dataset.py --out t2c_purdue_dataset_v2.json

# Run the benchmark against it
python benchmark/Text2Cypher/t2c_single_agent_evaluator.py --dataset t2c_purdue_dataset_v2.json
```

To extend: add bindings (or templates) in `t2c_generate_dataset.py`, re-import if new
anchors are needed, regenerate, and re-run the freeze check on existing cases.

Open protocol questions (answer-format contract, boolean-question guidance, C1.2
wording) are listed at the end of the validation report — input welcome.
