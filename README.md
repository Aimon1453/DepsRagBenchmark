# DepsRAG Benchmark

Benchmark suite for evaluating **DepsRAG** on dependency knowledge graphs — starting with a **Text2Cypher** task over a Purdue SecureChain subgraph in Neo4j.

This repository is **not** the upstream DepsRAG project. It bundles a copy of DepsRAG under `dependencyrag/` so the benchmark can be reproduced end-to-end. For the original DepsRAG background and paper, see [Mohannadcse/DepsRAG](https://github.com/Mohannadcse/DepsRAG).

## What is measured (and what is not)

| In scope | Out of scope |
|----------|-----------------------------------------------|
| Text2Cypher: natural-language question → Cypher → execute on Neo4j → score vs gold | End-to-end answer-level benchmark (cut) |
| Reproducible pipeline: import graph → generate dataset → run any model → score | Claims about real-world ecosystem proportions |
| Ranking several models on one fixed dataset | |

The benchmark reports **execution success** and **F1 / exact-match** against a fixed gold dataset. Those scores characterize the system under test on this task; the research contribution here is the **evaluation setup** (graph, templates, dataset, scorer), not a claim that any one model is “good” or “bad” at a particular percentage.

### The two halves

Keeping these apart is what makes the benchmark reusable:

- **Benchmark** — the static specification: 10,000 cases (question, gold Cypher, frozen `expected_result`, template/difficulty/type labels) **plus the definition of what counts as correct** (F1 for SR/CR, exact match for SA, empty-vs-empty scores 1.0, result normalisation ignoring column names and row order).
- **Harness** — the executable machinery that turns *a model* plus *the benchmark* into *scores*: model slot, prompt construction, Cypher extraction, query timeout, provider-error bypass, concurrency, resume, persistence.

In short: the benchmark says what counts as right, the harness says how to get the run done.

## Repository map

| Path | Role | Read when… |
|------|------|------------|
| [`benchmark/Text2Cypher/`](benchmark/Text2Cypher/) | Text2Cypher dataset, gold Cypher templates, agent runner, scorer | Running or extending the benchmark |
| [`securechain_import/`](securechain_import/) | Pull SecureChain SPARQL subgraph → local Neo4j | Preparing the graph DB |
| [`dependencyrag/`](dependencyrag/) | Bundled DepsRAG (Agno `Team` coordinator + agents) | Changing the system under test |
| [`benchmarkDocs/`](benchmarkDocs/) | Diagrams (e.g. agent workflow) | Understanding architecture visually |

### Text2Cypher file roles

| File | Role |
|------|------|
| `t2c_purdue_dataset_v3.json` | **Current dataset — 10,000 gold cases** |
| `t2c_bindings_v3.json` | The 2,000 parameter bindings the dataset was built from, plus the sampling report |
| `t2c_enumerate_bindings.py` | Draws stratified bindings from the graph (replaces hand-curated binding lists) |
| `t2c_generate_dataset.py` | Templates + phrasings + bindings → executes gold Cypher → writes the dataset |
| `t2c_single_agent_evaluator.py` | **Campaign runner** — concurrent, resumable, any OpenAI-compatible model |
| `t2c_evaluator.py` | Executes Cypher; F1 for set retrieval (SR/CR), exact match for SA |
| `t2c_agent_evaluator.py` | Older runner that drives the full DepsRAG Team (not the Text2Cypher track) |
| `DATASET_V3.md` | How the 10,000 cases were built, validated, and where they fall short |
| `DIFFICULTY_RUBRIC.md` | The structural rubric that assigns Easy / Medium / Hard |
| `t2c_purdue_cypher_templates.md` | Human-readable Cypher templates (v2 set) |
| `t2c_purdue_dataset_v2.json` / `t2c_purdue_dataset.json` | 170-case regression set / original 17-case smoke test |

**How they relate:** the enumerator reads candidate bindings out of the frozen graph → the generator fills each template with each binding and each phrasing, runs the gold Cypher, and freezes the result as `expected_result` → the runner asks a model for Cypher **without executing it**, then the scorer runs the generated Cypher and compares against gold.

### How 10,000 cases are constructed

```
25 templates  (structural diversity — what shape of Cypher is required)
  × 5 phrasings   (surface diversity — how the question is worded)
  × 80 bindings   (data diversity — which packages/versions fill the slots)
  = 10,000 cases
```

These three numbers measure different things and should be reported separately.
A model that cannot express a template's structure fails all 400 of its cases,
so the effective structural coverage is 25 — not 10,000.

The five phrasings of a binding share one gold Cypher and one `expected_result`,
so building the dataset costs 2,000 database round-trips, not 10,000.

## Benchmark pipeline

Prerequisites: **Python ≥ 3.11**, **Poetry**, **Docker**, one LLM API key (OpenAI / Azure / Gemini).

### 1. Install

```bash
git clone https://github.com/Aimon1453/DepsRagBenchmark.git
cd DepsRagBenchmark
poetry install
cp .env-template .env
```

Edit `.env` for the **local SecureChain Neo4j** used by the benchmark:

```bash
NEO4J_URI=bolt://127.0.0.1:7689
NEO4J_USERNAME=neo4j
NEO4J_PASSWORD=password
NEO4J_DATABASE=neo4j

# Plus at least one LLM provider:
# GOOGLE_API_KEY=...            # --provider google
# OPENAI_API_KEY=...            # --provider openai
# Azure OpenAI vars             # --provider azure   (see .env-template)

# OpenAI-compatible endpoints — each provider reads its own pair, so several
# can be configured side by side:
# DEEPSEEK_API_KEY=...   DEEPSEEK_BASE_URL=https://api.deepseek.com/v1
# KIMI_API_KEY=...       KIMI_BASE_URL=https://api.moonshot.cn/v1
# VLLM_BASE_URL=http://localhost:8000/v1     # self-hosted, key optional
# OPENAI_COMPAT_API_KEY / OPENAI_COMPAT_BASE_URL are the shared fallback
```

`.env` is gitignored. Never put keys anywhere else in the repo.

### 2. Start Neo4j (`neo4j-securechain`)

From the repo root (PowerShell):

```powershell
.\securechain_import\run_neo4j_securechain.ps1
```

| | |
|--|--|
| Container | `neo4j-securechain` |
| Browser | http://localhost:7476 |
| Bolt | `bolt://127.0.0.1:7689` |
| Auth | `neo4j` / `password` |

> **This script deletes and recreates the container, and the container has no
> volume — running it destroys an imported graph.** The import takes hours of
> SPARQL BFS, so to restart an existing container use `docker start
> neo4j-securechain` and only run the script when building a graph from scratch.

Details: [`securechain_import/README.md`](securechain_import/README.md).

### 3. Import SecureChain subgraph

```powershell
.\securechain_import\import_three.ps1
```

`import_three.ps1` pulls the three original anchors. The frozen graph the v3
dataset is built against holds **11 anchors** imported one at a time with
ecosystem pinning:

```powershell
poetry run python -m securechain_import --software flask --version 2.3.3 --host pypi.org
```

The `--host` filter matters: package names collide across ecosystems (`click`,
`flask` and `cryptography` exist on both PyPI and crates.io), and an import that
resolves a name to its first SPARQL hit will silently pull the wrong one.

Frozen snapshot **2026-08-01**: 1,131 `Software` / 1,650 `SoftwareVersion` /
6,286 `DEPENDS_ON` / 196 `VULNERABLE_TO`. Schema uses `Software`,
`SoftwareVersion`, `DEPENDS_ON`, `Vulnerability`, etc. (not DepsRAG’s deps.dev
`Package` construction path).

Two properties of this graph shape everything downstream, and are documented in
full under Limitations in [`DATASET_V3.md`](benchmark/Text2Cypher/DATASET_V3.md):
by version count it is **94% crates.io** (the enumerator counteracts this by
sampling ecosystems round-robin), and only **47 versions carry a CVE**, which
caps what the vulnerability templates can measure.

### 4. Run Text2Cypher evaluation

```powershell
# hosted provider
poetry run python benchmark/Text2Cypher/t2c_single_agent_evaluator.py `
    --dataset t2c_purdue_dataset_v3.json --provider google --model gemini-3.1-flash-lite `
    --workers 16 --format-hints

# OpenAI-compatible provider (DeepSeek, Kimi, self-hosted vLLM, …)
poetry run python benchmark/Text2Cypher/t2c_single_agent_evaluator.py `
    --dataset t2c_purdue_dataset_v3.json --provider deepseek --model deepseek-v4-flash `
    --workers 16 --format-hints
```

| Flag | Meaning |
|------|---------|
| `--workers N` | Cases in flight. ~0.5 s/case at 8 workers, so 10,000 cases ≈ 80 min; ≈ 40 min at 16 |
| `--format-hints` | Append the answer-format contract to the prompt. **This is a protocol variable, not a tweak** — it moved one model from 0.682 to 0.882, so bare and contract runs must be reported separately |
| `--query-timeout` | Per-query execution limit, default 30 s. A generated query with an unbounded `DEPENDS_ON*` once ran 96 minutes; exceeding the limit scores as a failed execution |
| `--limit` / `--verbose` | Smoke-test a few cases / print per-case expected-vs-actual dumps |

Results append to a JSONL file named after dataset, model and protocol, with a
`.summary.json` sidecar written at the end. **Rerunning the identical command
resumes an interrupted campaign** — completed cases are skipped by id, so a
crash or a rate-limit pause costs nothing.

If three consecutive provider errors occur the campaign aborts rather than
recording a run of zeros: an API outage is not a model being wrong.

```text
=== Overall (single-agent Text2Cypher) ===
Cases    : 10000
Executed : …/10000
Avg score: …
```

- **Executed** — generated Cypher ran without a Neo4j error
- **Avg score** — mean of per-case F1 (SR/CR) or exact match (SA)

More detail: [`benchmark/Text2Cypher/README.md`](benchmark/Text2Cypher/README.md),
[`DATASET_V3.md`](benchmark/Text2Cypher/DATASET_V3.md).

### 5. Rebuild the dataset (optional)

```powershell
poetry run python benchmark/Text2Cypher/t2c_enumerate_bindings.py            # -> t2c_bindings_v3.json
poetry run python benchmark/Text2Cypher/t2c_generate_dataset.py `
    --bindings t2c_bindings_v3.json --out t2c_purdue_dataset_v3.json
```

Sampling is seeded, so the same graph and seed reproduce the same dataset.
`t2c_enumerate_bindings.py --report-only` prints how many candidate bindings the
graph can supply per template — the quickest way to see what a quota change
would cost before making it.

## Graph schema (SecureChain import)

- Package **name** → `Software.name`
- Version → `SoftwareVersion.versionName` (linked by `HAS_VERSION`)
- Dependencies → `DEPENDS_ON` between `SoftwareVersion` nodes
- Vulnerabilities → `VULNERABLE_TO` → `Vulnerability`

**Inspect in Neo4j Browser** (http://localhost:7476):

```cypher
MATCH (s:Software)-[:HAS_VERSION]->(v:SoftwareVersion)
RETURN s.name AS package, v.versionName AS version
ORDER BY package, version
```

Visualize the whole graph:

```cypher
MATCH (n)-[r]->(m)
RETURN n, r, m
```

## DepsRAG in this repo (system under test)

Bundled under `dependencyrag/`, aligned with upstream’s Agno migration:

- **Coordinator:** Agno `Team` (`TeamMode.coordinate`)
- **Members:** `DependencyGraphAgent`, `SearchAgent`, `CriticAgent`

**Text2Cypher** evaluates **DependencyGraphAgent only** (no Team coordinator / Search / Critic):

```python
from dependencyrag.agno_agents import create_dependency_graph_agent

agent = create_dependency_graph_agent()
response = agent.run(prompt)  # Cypher extracted from response.content
```

**Interactive DepsRAG** (full Team; optional — not used by Text2Cypher):

```powershell
poetry run python dependencyrag/main.py
# poetry run python dependencyrag/main.py --provider google --model gemini-2.0-flash
```

Note: DepsRAG’s **deps.dev graph construction** path expects Neo4j **APOC** (`apoc.load.json`). The SecureChain Docker setup used for Text2Cypher does **not** install APOC and does **not** need construction — the graph is pre-imported. For Text2Cypher, the agent is instructed to emit Cypher only (no tools / no execute).

Upstream project: [github.com/Mohannadcse/DepsRAG](https://github.com/Mohannadcse/DepsRAG).

## Tests (DepsRAG)

```powershell
poetry run pytest tests/ -v
# or: poetry run python tests/test_neo4j_tools.py
```

See [`tests/README.md`](tests/README.md).

## License

MIT — see [LICENSE](LICENSE).

## Acknowledgments

- [DepsRAG](https://github.com/Mohannadcse/DepsRAG) (Mohannad Alhanahnah) — multi-agent dependency analysis  
- [Purdue SecureChain](https://frink.apps.renci.org/securechainkg/sparql) knowledge graph (SPARQL)  
- [Agno](https://github.com/agno-agi/agno), [deps.dev](https://deps.dev/), [OSV](https://osv.dev/), Neo4j  

## Citation (upstream DepsRAG)

If you use DepsRAG itself, please cite the upstream project (see their README). This repository adds the Text2Cypher benchmark tooling and SecureChain import path on top of that stack.
