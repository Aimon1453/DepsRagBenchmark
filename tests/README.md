# DepsRAG tests

Tests for the bundled DepsRAG package (`dependencyrag/`). These are **not** the Text2Cypher benchmark — for that, see [`benchmark/Text2Cypher/README.md`](../benchmark/Text2Cypher/README.md).

## Files

| File | Role |
|------|------|
| `test_neo4j_tools.py` | Neo4j connection, schema, graph construction helpers, Cypher execution |
| `test_integration.py` | Tools, agents, Team creation (`TeamMode.coordinate`) |

## Requirements

`.env` at repo root with Neo4j credentials and an LLM key (integration / agent tests).

For the SecureChain Docker instance used by the benchmark:

```bash
NEO4J_URI=bolt://127.0.0.1:7689
NEO4J_USERNAME=neo4j
NEO4J_PASSWORD=password
NEO4J_DATABASE=neo4j
```

Note: some unit tests exercise **deps.dev-style** `Package` graph construction, which expects APOC on Neo4j. That path is separate from the SecureChain Text2Cypher graph.

## Run

From repo root:

```powershell
poetry run pytest tests/ -v
poetry run python tests/test_neo4j_tools.py
poetry run python tests/test_integration.py
```

## Notes

- Neo4j must be reachable for DB-backed tests
- Team tests expect members `DependencyGraphAgent`, `SearchAgent`, `CriticAgent` (no separate AssistantAgent)
