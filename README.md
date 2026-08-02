# DepsRAG Benchmark

Benchmark suite for evaluating **DepsRAG** on dependency knowledge graphs — starting with a **Text2Cypher** task over a Purdue SecureChain subgraph in Neo4j.

This repository is **not** the upstream DepsRAG project. It bundles a copy of DepsRAG under `dependencyrag/` so the benchmark can be reproduced end-to-end. For the original DepsRAG background and paper, see [Mohannadcse/DepsRAG](https://github.com/Mohannadcse/DepsRAG).

## What is measured (and what is not)

| In scope | Out of scope |
|----------|-----------------------------------------------|
| Text2Cypher: Nature language question → Cypher → execute on Neo4j → score vs gold | Unpublished E2E answer-level benchmark |
| Reproducible pipeline: import graph → run agent → score |  |

The benchmark reports **execution success** and **F1 / exact-match** against a fixed gold dataset. Those scores characterize the system under test on this task; the research contribution here is the **evaluation setup** (graph, templates, dataset, scorer), not a claim that DepsRAG is “good” or “bad” at a particular percentage.

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
| `t2c_purdue_dataset.json` | Gold cases (question, gold Cypher, expected Neo4j result) |
| `t2c_purdue_cypher_templates.md` | Human-readable Cypher templates |
| `t2c_agent_evaluator.py` | Runs DependencyGraphAgent on each question, extracts Cypher, scores |
| `t2c_evaluator.py` | Executes Cypher; F1 for set retrieval (SR/CR), exact match for SA |

**How they relate:** templates document the intended gold queries → dataset instantiates them for anchors (e.g. `requests` @ `2.31.0`) with frozen `expected_result` → agent evaluator asks **DependencyGraphAgent** for Cypher **without executing it**, then the scorer runs generated Cypher on Neo4j and compares to gold.

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

# Plus one LLM provider, e.g.:
# OPENAI_API_KEY=...
# or GOOGLE_API_KEY=...
# or Azure OpenAI vars (see .env-template)
```

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

> This script recreates the container. To keep existing data, start the container from Docker Desktop instead.

Details: [`securechain_import/README.md`](securechain_import/README.md).

### 3. Import SecureChain subgraph

```powershell
.\securechain_import\import_three.ps1
```

Default anchors (BFS over the public SPARQL endpoint): `requests` 2.31.0, `django` 4.2.11, and a lodash-side / `js-lodash` fallback. Schema uses `Software`, `SoftwareVersion`, `DEPENDS_ON`, `Vulnerability`, etc. (not DepsRAG’s deps.dev `Package` construction path).

### 4. Run Text2Cypher evaluation

```powershell
poetry run python benchmark/Text2Cypher/t2c_agent_evaluator.py
```

Optional: `BENCHMARK_DATASET=...` to point at another JSON file in `benchmark/Text2Cypher/`.

Example summary line:

```text
=== Overall Text2Cypher Results ===
Total Cases: 17
Successful Executions: …/17
Average Score (F1/EM): …
```

- **Successful Executions** — generated Cypher ran without Neo4j error  
- **Average Score** — mean of per-case F1 (SR/CR) or exact match (SA)

More detail: [`benchmark/Text2Cypher/README.md`](benchmark/Text2Cypher/README.md).

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
