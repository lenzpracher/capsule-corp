"""Evaluation of pre-registered deterministic checks.

These checks are the hard gate: they decide whether a capsule's registered predictions
held, with no language model involved. That only works if the expressions are evaluated
safely and predictably, so this module walks the AST itself rather than calling
``eval``. There is no code path here that can import a module, call an arbitrary
function, or reach a dunder attribute.
"""

from __future__ import annotations

import ast
import json
import operator
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from capsule_corp.models import Check, CheckKind, Prereg

# Only these builtins are reachable from a check expression.
ALLOWED_FUNCTIONS: dict[str, Callable[..., Any]] = {
    "abs": abs,
    "all": all,
    "any": any,
    "len": len,
    "max": max,
    "min": min,
    "round": round,
    "sum": sum,
}

_BIN_OPS: dict[type[ast.operator], Callable[[Any, Any], Any]] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}

_COMPARE_OPS: dict[type[ast.cmpop], Callable[[Any, Any], Any]] = {
    ast.Eq: operator.eq,
    ast.NotEq: operator.ne,
    ast.Lt: operator.lt,
    ast.LtE: operator.le,
    ast.Gt: operator.gt,
    ast.GtE: operator.ge,
    ast.In: lambda a, b: a in b,
    ast.NotIn: lambda a, b: a not in b,
}

_UNARY_OPS: dict[type[ast.unaryop], Callable[[Any], Any]] = {
    ast.USub: operator.neg,
    ast.UAdd: operator.pos,
    ast.Not: operator.not_,
}

RESULTS_NAME = "results"


class CheckEvaluationError(Exception):
    """A check could not be evaluated. Distinct from a check that evaluated to False."""


@dataclass(frozen=True)
class CheckResult:
    """Outcome of one pre-registered check."""

    id: str
    kind: CheckKind
    passed: bool
    description: str = ""
    detail: str = ""
    error: str | None = None

    @property
    def errored(self) -> bool:
        return self.error is not None


def evaluate_expression(expression: str, results: Any) -> Any:
    """Evaluate a restricted expression against loaded results."""
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError as exc:
        raise CheckEvaluationError(f"could not parse expression: {exc}") from exc
    return _eval(tree.body, results)


def _eval(node: ast.AST, results: Any) -> Any:
    """Evaluate one AST node.

    Dispatch is table-driven and closed: a node type with no handler is refused rather
    than falling through to something permissive. That is the property the safety of
    this module rests on, so it is worth keeping the table easy to read.
    """
    handler = _HANDLERS.get(type(node))
    if handler is None:
        raise CheckEvaluationError(f"{type(node).__name__} is not permitted in a check expression")
    return handler(node, results)


def _eval_constant(node: ast.Constant, _results: Any) -> Any:
    return node.value


def _eval_name(node: ast.Name, results: Any) -> Any:
    if node.id == RESULTS_NAME:
        return results
    if node.id in ALLOWED_FUNCTIONS:
        return ALLOWED_FUNCTIONS[node.id]
    raise CheckEvaluationError(
        f"unknown name {node.id!r}; a check may only reference '{RESULTS_NAME}' and "
        f"{', '.join(sorted(ALLOWED_FUNCTIONS))}"
    )


def _eval_attribute(node: ast.Attribute, results: Any) -> Any:
    if node.attr.startswith("_"):
        raise CheckEvaluationError(f"attribute {node.attr!r} is not permitted")
    return _lookup(_eval(node.value, results), node.attr)


def _eval_subscript(node: ast.Subscript, results: Any) -> Any:
    return _lookup(_eval(node.value, results), _eval(node.slice, results))


def _eval_compare(node: ast.Compare, results: Any) -> Any:
    """Evaluate a comparison, including chains such as ``10.2 < results.ratio < 12.4``."""
    left = _eval(node.left, results)
    for op, comparator in zip(node.ops, node.comparators, strict=True):
        handler = _COMPARE_OPS.get(type(op))
        if handler is None:
            raise CheckEvaluationError(f"comparison {type(op).__name__} is not permitted")
        right = _eval(comparator, results)
        if not handler(left, right):
            return False
        left = right
    return True


def _eval_boolop(node: ast.BoolOp, results: Any) -> Any:
    values = [_eval(value, results) for value in node.values]
    return all(values) if isinstance(node.op, ast.And) else any(values)


def _eval_unaryop(node: ast.UnaryOp, results: Any) -> Any:
    handler = _UNARY_OPS.get(type(node.op))
    if handler is None:
        raise CheckEvaluationError(f"unary operator {type(node.op).__name__} is not permitted")
    return handler(_eval(node.operand, results))


def _eval_binop(node: ast.BinOp, results: Any) -> Any:
    handler = _BIN_OPS.get(type(node.op))
    if handler is None:
        raise CheckEvaluationError(f"operator {type(node.op).__name__} is not permitted")
    return handler(_eval(node.left, results), _eval(node.right, results))


def _eval_list(node: ast.List, results: Any) -> Any:
    return [_eval(element, results) for element in node.elts]


def _eval_tuple(node: ast.Tuple, results: Any) -> Any:
    return tuple(_eval(element, results) for element in node.elts)


def _eval_dict(node: ast.Dict, results: Any) -> Any:
    return {
        _eval(key, results): _eval(value, results)
        for key, value in zip(node.keys, node.values, strict=True)
        if key is not None
    }


def _eval_call(node: ast.Call, results: Any) -> Any:
    """Evaluate a call, which may only target one of the allowed builtins by name."""
    if not isinstance(node.func, ast.Name):
        raise CheckEvaluationError("only direct calls to the allowed helper functions are permitted")
    if node.func.id not in ALLOWED_FUNCTIONS:
        raise CheckEvaluationError(
            f"function {node.func.id!r} is not permitted; allowed: {', '.join(sorted(ALLOWED_FUNCTIONS))}"
        )
    if node.keywords:
        raise CheckEvaluationError("keyword arguments are not permitted in a check expression")
    arguments = [_eval(argument, results) for argument in node.args]
    try:
        return ALLOWED_FUNCTIONS[node.func.id](*arguments)
    except Exception as exc:
        raise CheckEvaluationError(f"{node.func.id}() failed: {exc}") from exc


# The complete set of node types a check expression may contain. Anything absent here
# is rejected by _eval.
_HANDLERS: dict[type[ast.AST], Callable[[Any, Any], Any]] = {
    ast.Attribute: _eval_attribute,
    ast.BinOp: _eval_binop,
    ast.BoolOp: _eval_boolop,
    ast.Call: _eval_call,
    ast.Compare: _eval_compare,
    ast.Constant: _eval_constant,
    ast.Dict: _eval_dict,
    ast.List: _eval_list,
    ast.Name: _eval_name,
    ast.Subscript: _eval_subscript,
    ast.Tuple: _eval_tuple,
    ast.UnaryOp: _eval_unaryop,
}


def _lookup(container: Any, key: Any) -> Any:
    """Look a key up in loaded JSON, with an error a researcher can act on."""
    if isinstance(container, dict):
        if key not in container:
            available = ", ".join(sorted(str(k) for k in container)) or "(empty)"
            raise CheckEvaluationError(f"results.json has no key {key!r}; available keys: {available}")
        return container[key]
    if isinstance(container, (list, tuple)) and isinstance(key, int):
        try:
            return container[key]
        except IndexError as exc:
            raise CheckEvaluationError(f"index {key} out of range (length {len(container)})") from exc
    raise CheckEvaluationError(f"cannot look up {key!r} in a value of type {type(container).__name__}")


# --------------------------------------------------------------------------- checks


def load_results(capsule_dir: Path) -> Any:
    """Load ``results/results.json`` from a capsule."""
    path = capsule_dir / "results" / "results.json"
    if not path.is_file():
        raise CheckEvaluationError("results/results.json does not exist; run the capsule first")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise CheckEvaluationError(f"results/results.json is not valid JSON: {exc}") from exc


def evaluate_check(check: Check, capsule_dir: Path, results: Any) -> CheckResult:
    """Evaluate one check, converting any failure into a reportable result."""
    common: dict[str, Any] = {"id": check.id, "kind": check.kind, "description": check.description}
    try:
        if check.kind is CheckKind.EXPR:
            assert check.expr is not None
            value = evaluate_expression(check.expr, results)
            return CheckResult(**common, passed=bool(value), detail=f"{check.expr} → {value!r}")

        if check.kind is CheckKind.ARTIFACT:
            assert check.path is not None
            artifact = capsule_dir / check.path
            if not artifact.is_file():
                return CheckResult(**common, passed=False, detail=f"{check.path} does not exist")
            if artifact.stat().st_size == 0:
                return CheckResult(**common, passed=False, detail=f"{check.path} is empty")
            return CheckResult(**common, passed=True, detail=f"{check.path} ({artifact.stat().st_size} bytes)")

        return CheckResult(
            **common,
            passed=False,
            detail="",
            error="script checks are not implemented yet",
        )
    except CheckEvaluationError as exc:
        return CheckResult(**common, passed=False, detail="", error=str(exc))


def evaluate_checks(prereg: Prereg, capsule_dir: Path) -> list[CheckResult]:
    """Evaluate every registered check against the capsule's results."""
    try:
        results = load_results(capsule_dir)
    except CheckEvaluationError as exc:
        return [
            CheckResult(id=c.id, kind=c.kind, description=c.description, passed=False, error=str(exc))
            for c in prereg.checks
        ]
    return [evaluate_check(check, capsule_dir, results) for check in prereg.checks]
