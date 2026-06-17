# SecureChain → Neo4j import

Pull a small **dependency subgraph** from the [Purdue SecureChain knowledge graph](https://frink.apps.renci.org/securechainkg/sparql) into a local Neo4j instance for `benchmark/Text2Cypher` and DepsRAG queries.

The public graph is exposed via SPARQL; DepsRAG talks to Neo4j. This folder contains the scripts that move data between the two.

## Prerequisites

- **Docker** (local Neo4j)
- **Poetry** (`poetry install` at the repo root)
- **`.env`** at the repo root (at minimum Neo4j connection settings; LLM API keys are only needed when running the benchmark)

```bash
# Copy and edit (.env Neo4j example below)
cp .env-template .env
```

Local instance used by the benchmark (matches `run_neo4j_securechain.ps1`):

```bash
NEO4J_URI=bolt://127.0.0.1:7689
NEO4J_USERNAME=neo4j
NEO4J_PASSWORD=password
NEO4J_DATABASE=neo4j
```

## Two-step setup (PowerShell, repo root)

### 1. Start the Neo4j container

```powershell
.\securechain_import\run_neo4j_securechain.ps1
```

- Container name: `neo4j-securechain`
- Browser: <http://localhost:7476>
- Bolt: `bolt://127.0.0.1:7689`
- Credentials: `neo4j` / `password`
- If the container already exists, it is removed and recreated; wait ~20 seconds after start

### 2. Import three anchor packages

Writes data into Neo4j:

```powershell
.\securechain_import\import_three.ps1
```

## What gets imported

The script runs BFS over the public SPARQL endpoint (default depth 6) and writes into local Neo4j:

| Package | Version | Notes |
|---------|---------|-------|
| requests | 2.31.0 | Test anchor used by Text2Cypher |
| lodash-side | 4.17.21 | Often missing on the public KG; falls back to `js-lodash` @ 4.17.4 for comparison with the Spanish subgraph |
| django | 4.2.11 | Comparison benchmark cases; paired with the Spanish subgraph |

Nodes and relationships written include `Software`, `SoftwareVersion`, `DEPENDS_ON`, `Vulnerability`, etc. (aligned with Cypher templates in the benchmark).

## Files

| File | Role |
|------|------|
| `run_neo4j_securechain.ps1` | Start `neo4j-securechain` via Docker |
| `import_three.ps1` | One-shot import of the three anchors above |
| `import_three_anchors.py` | CLI entry for the three-anchor import |
| `import_graph.py` | Core logic: SPARQL BFS + vulnerabilities + Neo4j write |
| `sparql_client.py` | HTTP client for the public SPARQL endpoint |
| `neo4j_write.py` | Batch MERGE into Neo4j |
| `__main__.py` | Single-package import (see below) |

## Single-package import (optional)

Use this if you later expand the question bank and need additional subgraphs:

```powershell
poetry run python -m securechain_import --software requests --version 2.31.0
```

Common flags: `--max-depth`, `--neo4j-uri` (defaults to `.env` or `bolt://127.0.0.1:7689`).

## After import

Run the Text2Cypher benchmark (see [`benchmark/Text2Cypher/README.md`](../benchmark/Text2Cypher/README.md)):

```powershell
poetry run python benchmark/Text2Cypher/t2c_agent_evaluator.py
```

## Troubleshooting

| Symptom | What to do |
|---------|------------|
| Cannot connect to Neo4j | Check Docker is running, `NEO4J_URI=bolt://127.0.0.1:7689`, and the container has finished warming up |
| SPARQL timeout | Public endpoint can be flaky; retry or increase `--timeout` |
| lodash-related warning | Expected; the script falls back to `js-lodash` automatically |

Default SPARQL endpoint: `https://frink.apps.renci.org/securechainkg/sparql` (no API key required).
