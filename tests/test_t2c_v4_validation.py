"""Tests for the v4 bank's build-time validation.

These run without Neo4j: they exercise the checks that decide whether a template
is *scoreable*, using the real defects found while reviewing the shared 54-row
template sheet as the negative cases. If a check here stops failing on its
defective input, the check has become a no-op.

  python -m pytest tests/test_t2c_v4_validation.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

T2C_DIR = Path(__file__).resolve().parents[1] / "benchmark" / "Text2Cypher"
sys.path.insert(0, str(T2C_DIR))

from t2c_build_v4 import BuildError, _check_expect, _check_shape, check_template  # noqa: E402
from t2c_templates_v4 import TEMPLATES  # noqa: E402


LIST_SHAPE = {"kind": "list", "columns": ["version"], "ordered": False}
BOOL_SHAPE = {"kind": "bool", "columns": ["exists"], "ordered": False}


def test_registered_templates_are_coherent():
    for tid, tpl in TEMPLATES.items():
        check_template(tid, tpl)


def test_v7_rejects_a_list_template_without_order_by():
    """The real C2.3 of the shared sheet: a multi-row answer with no ORDER BY.

    Its row order is undefined, so two systems returning the same set of
    versions can score differently for no reason the benchmark intends.
    """
    sheet_c2_3 = {
        "answer_shape": LIST_SHAPE,
        "strata": [("any", 1.0, "SYNTH:x", None)],
        "cypher": (
            "MATCH (s:Software {{name: '{pkg}'}})-[:HAS_VERSION]->"
            "(root:SoftwareVersion {{versionName: '{ver}'}}) "
            "MATCH (root)-[:DEPENDS_ON]->(dep:SoftwareVersion)"
            "<-[:HAS_VERSION]-(ds:Software {{name: '{dep}'}}) "
            "RETURN dep.versionName AS version"
        ),
    }
    with pytest.raises(BuildError, match="V7"):
        check_template("C2.3", sheet_c2_3)


def test_v7_rejects_strata_that_do_not_sum_to_one():
    tpl = dict(TEMPLATES["C1.1"])
    tpl["strata"] = [("a", 0.5, "SYNTH:x", None), ("b", 0.2, "SYNTH:y", None)]
    with pytest.raises(BuildError, match="V7"):
        check_template("bad", tpl)


def test_v8_rejects_duplicate_rows_from_a_missing_distinct():
    rows = [{"version": "1.0.0"}, {"version": "1.0.0"}, {"version": "2.0.0"}]
    with pytest.raises(BuildError, match="V8"):
        _check_shape(rows, LIST_SHAPE, "where")


def test_v3_rejects_a_bool_template_returning_several_rows():
    rows = [{"exists": True}, {"exists": True}]
    with pytest.raises(BuildError, match="V3"):
        _check_shape(rows, BOOL_SHAPE, "where")


def test_v3_rejects_undeclared_columns():
    with pytest.raises(BuildError, match="V3"):
        _check_shape([{"versionName": "1.0.0"}], LIST_SHAPE, "where")


@pytest.mark.parametrize(
    "expect,rows",
    [
        ("empty", [{"version": "1.0.0"}]),          # an absent package that answered
        ("nonempty", []),                            # a real package that did not
        ("false", [{"exists": True}]),               # a near-miss version that exists
        ("true", [{"exists": False}]),
    ],
)
def test_v4_rejects_cases_that_contradict_their_stratum(expect, rows):
    shape = BOOL_SHAPE if expect in ("true", "false") else LIST_SHAPE
    with pytest.raises(BuildError, match="V4"):
        _check_expect(rows, expect, shape, "where")


def test_v4_accepts_cases_that_match_their_stratum():
    _check_expect([], "empty", LIST_SHAPE, "where")
    _check_expect([{"version": "1.0.0"}], "nonempty", LIST_SHAPE, "where")
    _check_expect([{"exists": False}], "false", BOOL_SHAPE, "where")
    _check_expect([{"exists": True}], "true", BOOL_SHAPE, "where")
