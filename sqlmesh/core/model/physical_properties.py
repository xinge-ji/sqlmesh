from __future__ import annotations

import re
import typing as t

from sqlglot import exp
from sqlglot.optimizer.simplify import gen


_DORIS_DISTRIBUTION_PROPERTIES = {
    "distributed_by",
    "distribution",
    "distribution_key",
}
_DORIS_DISTRIBUTION_KEYS = {
    "kind",
    "expressions",
    "buckets",
}


def normalize_physical_property_key(key: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", key.lower()).strip("_")


def physical_property_value_gen(dialect: str, property_name: str, value: exp.Expression) -> str:
    if dialect == "doris" and normalize_physical_property_key(
        property_name
    ) in _DORIS_DISTRIBUTION_PROPERTIES:
        return gen(_normalize_doris_distribution_value(value))

    return gen(value)


def _normalize_doris_distribution_value(value: exp.Expression) -> exp.Expression:
    if isinstance(value, exp.Tuple):
        normalized = value.copy()
        normalized.set(
            "expressions",
            [
                _normalize_doris_distribution_assignment(expression)
                for expression in value.expressions
            ],
        )
        return normalized

    if isinstance(value, exp.Paren) and isinstance(value.this, exp.EQ):
        normalized = value.copy()
        normalized.set("this", _normalize_doris_distribution_assignment(value.this))
        return normalized

    return value


def _normalize_doris_distribution_assignment(expression: exp.Expression) -> exp.Expression:
    if not isinstance(expression, exp.EQ):
        return expression.copy()

    key = _distribution_assignment_key(expression.this)
    if key not in _DORIS_DISTRIBUTION_KEYS:
        return expression.copy()

    normalized = expression.copy()
    normalized.set("this", exp.column(key))
    return normalized


def _distribution_assignment_key(expression: exp.Expression) -> t.Optional[str]:
    if isinstance(expression, exp.Column):
        return expression.name.lower()
    if isinstance(expression, exp.Identifier):
        return expression.name.lower()
    return None
