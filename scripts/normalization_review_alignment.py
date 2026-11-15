"""Conservative source-review helpers, never runtime normalization authority.

Erasing identifiers can find candidates but cannot prove their semantics. In
particular, equal bodies with different $type values are ambiguous siblings;
their array positions must never be used as an identity proof.
"""
from __future__ import annotations

import hashlib
import json
from collections import Counter
from collections.abc import Callable
from typing import Any


def body_signature(value: Any, cache: dict[int, str] | None = None) -> str:
    cache = {} if cache is None else cache
    if isinstance(value, (dict, list)) and id(value) in cache:
        return cache[id(value)]
    if isinstance(value, dict):
        payload = ["object", sorted("$type" if key == "$type" else body_signature(child, cache)
                                    for key, child in value.items())]
    elif isinstance(value, list):
        payload = ["array", [body_signature(child, cache) for child in value]]
    else:
        payload = [type(value).__name__, value]
    result = hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True,
                                       separators=(",", ":"), allow_nan=False).encode()).hexdigest()
    if isinstance(value, (dict, list)):
        cache[id(value)] = result
    return result


def unique_array_pairs(left: list[Any], right: list[Any], *,
                       signature: Callable[[Any], str] = body_signature) -> tuple[tuple[int, int], ...]:
    """Match only bodies that occur once on each side, independent of order."""
    old, new = [signature(value) for value in left], [signature(value) for value in right]
    old_counts, new_counts = Counter(old), Counter(new)
    targets = {value: index for index, value in enumerate(new) if new_counts[value] == 1}
    return tuple((index, targets[value]) for index, value in enumerate(old)
                 if old_counts[value] == new_counts[value] == 1)
