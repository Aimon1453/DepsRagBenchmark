"""
Text2Cypher benchmark: DepsRAG Team → extract Cypher → score vs t2c_purdue_dataset.json.

Usage (repo root):
  python benchmark/Text2Cypher/t2c_agent_evaluator.py
  set BENCHMARK_DATASET=t2c_toy_dataset.json  # optional
"""

import json
import os
import re
import sys
from pathlib import Path

from dotenv import load_dotenv

T2C_DIR = Path(__file__).resolve().parent
REPO_ROOT = T2C_DIR.parents[1]

sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(T2C_DIR))

load_dotenv(REPO_ROOT / ".env")

from dependencyrag.depsrag_team import create_depsrag_team
from dependencyrag.neo4j_tools import get_neo4j_connection
from t2c_evaluator import evaluate_single_case


def extract_cypher_from_response(response_text):
    """
    Extract Cypher query from the agent's response.
    Looks for ```cypher ... ``` code blocks first, then falls back to raw text.
    """
    cypher_block_pattern = r"```(?:cypher)?\s*(.*?)\s*```"
    matches = re.findall(cypher_block_pattern, response_text, re.DOTALL | re.IGNORECASE)

    if matches:
        return matches[0].strip()

    return response_text.strip()


PURDUE_SCHEMA_INSTRUCTIONS = """
CRITICAL INSTRUCTIONS (Purdue SecureChain subgraph in Neo4j):
1. Software products are `Software` nodes; use property `name` (e.g. 'requests').
2. Versions are `SoftwareVersion` nodes; use property `versionName` (e.g. '2.31.0'). NOT `Version`, NOT property `name` for versions.
3. Link software to version: `(s:Software {name: 'pkg'})-[:HAS_VERSION]->(v:SoftwareVersion {versionName: 'ver'})`
4. Direct/transitive dependencies: `(v)-[:DEPENDS_ON]->(dep:SoftwareVersion)`. Multi-hop: `[:DEPENDS_ON*1..6]`
5. To get dependency software name, match `(ds:Software)-[:HAS_VERSION]->(dep)`.
6. Vulnerabilities are nodes `Vulnerability` linked by `(v)-[:VULNERABLE_TO]->(c)`; CVE id in `c.cveId`.
7. CWE types: `(c)-[:VULNERABILITY_TYPE]->(w:VulnerabilityType)` with `w.cweId`.
8. Do NOT use Spanish-schema labels (`PyPIPackage`, `Version`, `HAVE`, `REQUIRE`) or `Version.vulnerabilities`.
9. Do NOT query `SecureChainImport` unless the question asks for import metadata.
10. DO NOT execute the query.
11. Final response: ONLY the Cypher in ```cypher ... ``` blocks, no other text.
"""


if __name__ == "__main__":
    dataset_name = os.getenv("BENCHMARK_DATASET", "t2c_purdue_dataset.json")
    dataset_path = T2C_DIR / dataset_name
    with dataset_path.open(encoding="utf-8") as f:
        dataset = json.load(f)

    try:
        conn = get_neo4j_connection()
    except ValueError as e:
        print(f"Error: {e}")
        print("Please ensure your .env file is loaded or Neo4j environment variables are set.")
        sys.exit(1)

    print("Initializing DepsRAG Team for evaluation...\n")
    team = create_depsrag_team()

    print(f"Dataset: {dataset_name}")
    print("Neo4j: Purdue SecureChain (see NEO4J_URI in .env, expected bolt://127.0.0.1:7689)")
    print("Starting Text2Cypher Benchmark Evaluation Protocol...\n")

    total_success = 0
    total_score = 0.0

    for case in dataset:
        tid = case.get("template_id", "")
        print(f"Evaluating {case['id']} {tid} ({case['query_type']}) - {case['difficulty']}")
        print(f"Question: {case['question']}")

        prompt = f"""
You are being tested in a benchmark.
Ask the DependencyGraphAgent to write Cypher for:

Question: {case['question']}

{PURDUE_SCHEMA_INSTRUCTIONS}
"""

        print("  Waiting for Agent response...")
        response = team.run(prompt)

        generated_cypher = extract_cypher_from_response(response.content)
        print(f"  [Extracted Cypher]:\n{generated_cypher}\n")

        eval_result = evaluate_single_case(conn, case, generated_cypher)

        if eval_result["executed_successfully"]:
            total_success += 1
            metrics = eval_result["metrics"]
            total_score += metrics["f1"]
            print("  [Execution]: PASS")
            if case["query_type"] in ["SR", "CR"]:
                print(f"  [Score]: F1={metrics['f1']:.2f} (P={metrics['precision']:.2f}, R={metrics['recall']:.2f})")
            else:
                print(f"  [Score]: Exact Match={metrics['exact_match']}")
        else:
            print(f"  [Execution]: FAIL - {eval_result['error_message']}")
            print("  [Score]: 0.00 (Syntax/Runtime Error)")
        print("-" * 50)

    print("\n=== Overall Text2Cypher Results ===")
    print(f"Total Cases: {len(dataset)}")
    print(f"Successful Executions: {total_success}/{len(dataset)} ({(total_success/len(dataset))*100:.1f}%)")
    print(f"Average Score (F1/EM): {(total_score/len(dataset)):.2f}")
