"""Tests for the check evaluator.

The evaluator runs expressions written by a language model, so its safety properties
matter as much as its arithmetic.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from capsule_corp.checks import (
    CheckEvaluationError,
    evaluate_checks,
    evaluate_expression,
    load_results,
)
from capsule_corp.models import Check, CheckKind, Prereg

RESULTS: dict[str, Any] = {
    "slope": -0.503,
    "endpoint_se_ratio": 11.28,
    "n_seeds": 50,
    "losses": [0.5, 0.4, 0.3],
    "nested": {"inner": 7},
    "flags": {"converged": True},
}


@pytest.mark.parametrize(
    ("expression", "expected"),
    [
        ("results.slope", -0.503),
        ("abs(results.slope + 0.5) < 0.05", True),
        ("abs(results.slope + 0.5) < 0.001", False),
        ("results.n_seeds >= 5", True),
        ("len(results.losses) == 3", True),
        ("min(results.losses) == 0.3", True),
        ("sum(results.losses) > 1.1", True),
        ("results.nested.inner == 7", True),
        ("results['slope'] < 0", True),
        ("results.losses[0] > results.losses[2]", True),
        ("results.flags.converged", True),
        ("not results.flags.converged", False),
        ("results.n_seeds > 10 and results.slope < 0", True),
        ("results.n_seeds > 100 or results.slope < 0", True),
        ("round(results.slope, 1) == -0.5", True),
        ("-results.slope > 0", True),
        ("results.n_seeds in [25, 50]", True),
    ],
)
def test_expressions(expression: str, expected: Any) -> None:
    assert evaluate_expression(expression, RESULTS) == expected


def test_chained_comparison() -> None:
    """The designer model really does emit these, so they must work."""
    assert evaluate_expression("10.2 < results.endpoint_se_ratio < 12.4", RESULTS) is True
    assert evaluate_expression("12.0 < results.endpoint_se_ratio < 12.4", RESULTS) is False


@pytest.mark.parametrize(
    "expression",
    [
        "__import__('os').system('echo pwned')",
        "results.__class__",
        "results.__class__.__bases__",
        "open('/etc/passwd')",
        "eval('1+1')",
        "exec('x=1')",
        "getattr(results, 'slope')",
        "[x for x in results.losses]",
        "lambda: 1",
        "results.losses.append(1)",
        "print(1)",
        "globals()",
    ],
)
def test_dangerous_expressions_are_rejected(expression: str) -> None:
    with pytest.raises(CheckEvaluationError):
        evaluate_expression(expression, RESULTS)


def test_assignment_is_not_an_expression() -> None:
    with pytest.raises(CheckEvaluationError, match="could not parse"):
        evaluate_expression("x = 1", RESULTS)


def test_missing_key_names_the_available_ones() -> None:
    with pytest.raises(CheckEvaluationError, match="no key 'nope'"):
        evaluate_expression("results.nope", RESULTS)
    with pytest.raises(CheckEvaluationError, match="slope"):
        evaluate_expression("results.nope", RESULTS)


def test_division_by_zero_is_reported_not_raised_raw() -> None:
    with pytest.raises(ZeroDivisionError):
        evaluate_expression("1 / 0", RESULTS)


# ------------------------------------------------------------------- whole checks


def _capsule_with_results(tmp_path: Path, results: dict[str, Any] | None = None) -> Path:
    (tmp_path / "results" / "figures").mkdir(parents=True)
    (tmp_path / "results" / "results.json").write_text(json.dumps(results if results is not None else RESULTS))
    return tmp_path


def test_evaluate_checks_mixed(tmp_path: Path) -> None:
    capsule = _capsule_with_results(tmp_path)
    (capsule / "results" / "figures" / "convergence.png").write_bytes(b"\x89PNG data")

    prereg = Prereg(
        hypothesis="h",
        checks=[
            Check(id="slope", kind=CheckKind.EXPR, expr="abs(results.slope + 0.5) < 0.05"),
            Check(id="seeds", kind=CheckKind.EXPR, expr="results.n_seeds == 99"),
            Check(id="figure", kind=CheckKind.ARTIFACT, path="results/figures/convergence.png"),
            Check(id="missing", kind=CheckKind.ARTIFACT, path="results/figures/absent.png"),
        ],
    )
    by_id = {r.id: r for r in evaluate_checks(prereg, capsule)}
    assert by_id["slope"].passed
    assert not by_id["seeds"].passed
    assert by_id["figure"].passed
    assert not by_id["missing"].passed
    assert "does not exist" in by_id["missing"].detail


def test_empty_artifact_fails(tmp_path: Path) -> None:
    capsule = _capsule_with_results(tmp_path)
    (capsule / "results" / "figures" / "blank.png").touch()
    prereg = Prereg(hypothesis="h", checks=[Check(id="f", kind=CheckKind.ARTIFACT, path="results/figures/blank.png")])
    result = evaluate_checks(prereg, capsule)[0]
    assert not result.passed
    assert "empty" in result.detail


def test_detail_records_what_was_evaluated(tmp_path: Path) -> None:
    """A failing check must say what value it saw, or it is useless for debugging."""
    capsule = _capsule_with_results(tmp_path)
    prereg = Prereg(hypothesis="h", checks=[Check(id="c", kind=CheckKind.EXPR, expr="results.slope > 0")])
    result = evaluate_checks(prereg, capsule)[0]
    assert not result.passed
    assert "results.slope > 0" in result.detail
    assert "False" in result.detail


def test_missing_results_file_fails_every_check(tmp_path: Path) -> None:
    prereg = Prereg(
        hypothesis="h",
        checks=[
            Check(id="a", kind=CheckKind.EXPR, expr="results.slope < 0"),
            Check(id="b", kind=CheckKind.ARTIFACT, path="results/figures/x.png"),
        ],
    )
    results = evaluate_checks(prereg, tmp_path)
    assert all(not r.passed and r.errored for r in results)
    assert "run the capsule first" in (results[0].error or "")


def test_malformed_results_json(tmp_path: Path) -> None:
    (tmp_path / "results").mkdir()
    (tmp_path / "results" / "results.json").write_text("{not json")
    with pytest.raises(CheckEvaluationError, match="not valid JSON"):
        load_results(tmp_path)


def test_bad_expression_is_an_error_not_a_failure(tmp_path: Path) -> None:
    """A check that cannot be evaluated must be distinguishable from one that failed."""
    capsule = _capsule_with_results(tmp_path)
    prereg = Prereg(hypothesis="h", checks=[Check(id="c", kind=CheckKind.EXPR, expr="results.typo_key > 0")])
    result = evaluate_checks(prereg, capsule)[0]
    assert not result.passed
    assert result.errored
    assert "typo_key" in (result.error or "")
