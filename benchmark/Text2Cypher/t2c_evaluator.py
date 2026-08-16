import json
import os
import sys
from pathlib import Path

# Load environment variables from .env file
from dotenv import load_dotenv

_REPO_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(_REPO_ROOT / ".env")

if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
from dependencyrag.neo4j_tools import get_neo4j_connection

# Per-case comparison dumps are useful when debugging a handful of cases and
# ruinous at campaign scale: a single transitive-closure case prints 1,557 rows,
# and concurrent workers interleave the lines into noise. Runners set this.
VERBOSE = False


def _debug(label, value):
    if VERBOSE:
        print(f"    [DEBUG] {label}: {value}")


def normalize_result(result):
    """
    Result Normalization: 
    To eliminate misjudgments caused by random database return orders and 
    different Cypher return aliases (e.g. 'dep.name' vs 'name'), 
    we extract only the values, convert them to strings, and sort them.
    """
    if not isinstance(result, list):
        return result
    
    normalized = []
    for item in result:
        if isinstance(item, dict):
            # Extract values, convert to string, and sort to ignore column order
            vals = sorted([str(v) for v in item.values()])
            normalized.append(tuple(vals))
        else:
            normalized.append(str(item))
    
    # Sort the list of tuples lexicographically
    return sorted(normalized)

def calculate_f1(expected, actual):
    """
    Calculate F1-Score for Retrieval queries (SR, CR).
    Utilizes Precision and Recall to tolerate partial omissions.
    """
    _debug("Raw Expected", expected)
    _debug("Raw Actual", actual)

    norm_expected = set(normalize_result(expected))
    norm_actual = set(normalize_result(actual))

    _debug("Norm Expected", norm_expected)
    _debug("Norm Actual", norm_actual)

    # Both empty (e.g. no common dependencies): treat as correct retrieval.
    if len(norm_expected) == 0 and len(norm_actual) == 0:
        return {
            "precision": 1.0,
            "recall": 1.0,
            "f1": 1.0,
            "exact_match": True,
        }
    
    tp = len(norm_expected.intersection(norm_actual))# tp(true positive),using intersection
    fp = len(norm_actual - norm_expected)# fp(false positive)
    fn = len(norm_expected - norm_actual)# fn(false negative)
    
    precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0# precision = tp / (tp + fp)
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0# recall = tp / (tp + fn)
    f1 = (2 * precision * recall) / (precision + recall) if (precision + recall) > 0 else 0.0# f1 = 2 * precision * recall / (precision + recall)
    
    return {
        "precision": precision, 
        "recall": recall, 
        "f1": f1, 
        "exact_match": f1 == 1.0
    }

def calculate_em(expected, actual):
    """
    Calculate Exact Match for Aggregation/Exact queries (SA, CA, EQ).
    The answer is unique and requires an absolute match.
    """
    _debug("Raw Expected", expected)
    _debug("Raw Actual", actual)

    norm_expected = normalize_result(expected)
    norm_actual = normalize_result(actual)

    _debug("Norm Expected", norm_expected)
    _debug("Norm Actual", norm_actual)
    
    is_match = (norm_expected == norm_actual)
    return {
        "exact_match": is_match, 
        "f1": 1.0 if is_match else 0.0
    }

def evaluate_single_case(conn, test_case, generated_cypher):
    """
    Direct Evaluation Protocol:
    Executes the generated Cypher. If it fails, scores are 0.
    If it succeeds, calculates F1 or Exact Match based on query type.
    """
    query_type = test_case["query_type"]
    expected_result = test_case["expected_result"]
    
    result_metrics = {
        "id": test_case["id"],
        "executed_successfully": False,
        "error_message": None,
        "metrics": {
            "f1": 0.0,
            "exact_match": False,
            "precision": 0.0,
            "recall": 0.0
        }
    }
    
    try:
        actual_result = conn.execute_query(generated_cypher)
        result_metrics["executed_successfully"] = True
    except Exception as e:
        result_metrics["error_message"] = str(e)
        return result_metrics
        
    if query_type in ["SR", "CR"]:
        result_metrics["metrics"] = calculate_f1(expected_result, actual_result)
    elif query_type in ["SA", "CA", "EQ"]:
        result_metrics["metrics"] = calculate_em(expected_result, actual_result)
        
    return result_metrics
