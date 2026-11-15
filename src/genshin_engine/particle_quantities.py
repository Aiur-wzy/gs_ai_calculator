"""Particle quantity inputs and conservation, independent of pickup triggers."""
from __future__ import annotations

import math
from fractions import Fraction

MAX_PARTICLE_COUNT = 1_000_000_000
PARTICLE_COUNT = {"type": "number", "minimum": 0, "maximum": MAX_PARTICLE_COUNT}
PARTICLE_MODE_FIELDS = {
    "count_mode": {"enum": ["fixed", "average"]},
    "count_distribution": {"type": "array", "minItems": 1, "items": {
        "type": "object", "additionalProperties": False,
        "properties": {"count": {**PARTICLE_COUNT, "type": "integer"},
                       "probability": {"type": "number", "minimum": 0, "maximum": 1}},
        "required": ["count", "probability"],
    }},
}
AVERAGE_MESSAGE = "按平均产球计算，实际少产球时可能需要更多充能。结果只适用于本次平均产球情景。"


def quantity(value):
    # Preserve decimal input through splits. In particular .1 + .2 settles .3
    # without leaving an in-flight balance or borrowing a future credit.
    return value if type(value) is int else Fraction(str(value))


def as_number(value):
    return value if type(value) is int else float(value)


def close_quantity(left, right, *, scale=None):
    if left == right:
        return True
    if left <= 0 or right <= 0:
        return False
    return abs(left - right) <= 2 * math.ulp(float(max(left, right) if scale is None else scale))


def uses_average(row, credits=()):
    return (row.get("count_mode") == "average" or "count_distribution" in row
            or any(type(value) is float and not value.is_integer()
                   for value in (row.get("count"), row.get("lost_count"),
                                 *(credit.get("count") for credit in credits))))


def quantity_model_issue(row, credits=()):
    """Check explicitly provided probability evidence without inventing branches."""
    if row.get("count_mode") == "fixed" and uses_average(row, credits):
        return ("particle_count_mode_conflict", "固定粒数情景只能使用整数数量，也不能同时声明平均产球的概率分支。")
    if "count_distribution" in row:
        distribution = row["count_distribution"]
        if not distribution:
            return ("particle_distribution_empty", "概率分布须至少包含一个粒数分支。")
        probabilities = [quantity(branch["probability"]) for branch in distribution]
        if not close_quantity(sum(probabilities), 1):
            return ("particle_distribution_probability", "各粒数分支的概率之和应为 1，零粒分支也参与加权。")
        mean = sum(branch["count"] * p for branch, p in zip(distribution, probabilities, strict=True))
        if row.get("count") is not None and not close_quantity(mean, quantity(row["count"])):
            return ("particle_distribution_mean", "声明的产球数量应等于各粒数分支按概率加权的平均数。")
    return None


def average_scope():
    return {"contract": "particle-quantity-v1", "count_mode": "average",
            "proof_scope": "deterministic_average_scenario", "message": AVERAGE_MESSAGE}
