# Text2Cypher benchmark

Evaluate DepsRAG on **natural language → Cypher** over a local Neo4j copy of a Purdue SecureChain subgraph.

Root README has the full install → Neo4j → import → run pipeline. This page covers **files, scoring, and how to extend cases**.

## Prerequisites

1. `neo4j-securechain` running (`bolt://127.0.0.1:7689`) with three-anchor import done — see [`securechain_import/README.md`](../../securechain_import/README.md)
2. `.env` at repo root with Neo4j + an LLM key
3. Run commands from the **repo root**

## Run

```powershell
poetry run python benchmark/Text2Cypher/t2c_agent_evaluator.py
```

Optional dataset override:

```powershell
$env:BENCHMARK_DATASET = "t2c_purdue_dataset.json"   # default
poetry run python benchmark/Text2Cypher/t2c_agent_evaluator.py
```

## Files

| File | Role |
|------|------|
| `t2c_purdue_dataset.json` | Gold dataset (17 cases): question, gold Cypher, `expected_result` |
| `t2c_purdue_cypher_templates.md` | Human-editable templates (IDs C1.1–C5.3) matching `template_id` in the JSON |
| `t2c_agent_evaluator.py` | DependencyGraphAgent → extract ```cypher``` → score |
| `t2c_evaluator.py` | Execute Cypher on Neo4j; F1 / exact match |

### Templates vs dataset

- **Templates** (`t2c_purdue_cypher_templates.md`): readable gold patterns with placeholders `{pkg}`, `{ver}`, …
- **Dataset** (`t2c_purdue_dataset.json`): concrete instances (mostly `requests` @ `2.31.0`, some compare with `django` @ `4.2.11`) plus frozen expected query results from the imported graph

When extending the suite: update the template doc → add a JSON case → re-run import if new software/version is needed → re-run the agent evaluator.

## Pipeline (what the agent evaluator does)

```text
for each case in dataset:
  1. Prompt DependencyGraphAgent with the NL question + Purdue schema instructions
     (no tools; must NOT execute Cypher; return ```cypher``` only)
  2. Extract Cypher from the response
  3. Execute generated Cypher on Neo4j
  4. Compare to expected_result (normalized)
  5. Report PASS/FAIL + F1 or exact match
```

System under test: `create_dependency_graph_agent()` only — not the full DepsRAG Team. SearchAgent, CriticAgent, and the Team coordinator are not used. Text2Cypher does **not** require deps.dev graph construction or APOC.

## Scoring

| `query_type` | Metric | Meaning |
|--------------|--------|---------|
| `SR`, `CR` | F1 on result sets | After normalizing cell values (ignore column names / row order) |
| `SA` | Exact match | Normalized result lists must match |

**Successful execution** = Cypher ran without Neo4j error (independent of F1/EM).

Normalization (see `normalize_result` in `t2c_evaluator.py`) drops column-alias differences so `dep.name` vs `name` does not alone decide the score. Remaining mismatches (e.g. returning only package names when gold includes versions, or returning a path object vs a version list) still lower the score — that is intentional for a strict gold comparison.

## Schema reminders (Purdue SecureChain)

- `Software.name`, `SoftwareVersion.versionName` (not `Package` / `version`)
- `(Software)-[:HAS_VERSION]->(SoftwareVersion)-[:DEPENDS_ON*1..6]->…`
- Vulnerabilities: `(SoftwareVersion)-[:VULNERABLE_TO]->(Vulnerability)` ; CWE via `VulnerabilityType`

Full prompt constraints live in `PURDUE_SCHEMA_INSTRUCTIONS` inside `t2c_agent_evaluator.py`.
