"""Merge reviewed additive GUI ABI declarations without rewriting history."""
from __future__ import annotations

import ast
import copy
import hashlib
import json


def semantic_sha(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode()).hexdigest()


def _arguments(signature):
    return ast.parse(f"def _({signature['arguments']}): pass").body[0].args


def _same(left, right):
    return ast.dump(left, include_attributes=False) == ast.dump(right, include_attributes=False)


def reviewed_expected_contract(baseline, delta):
    if (delta["contract"] != "genshin-gui-api-additive-contract-v1"
            or delta["baseline_contract"] != baseline["contract"]
            or delta["baseline_semantic_sha256"] != semantic_sha(baseline)):
        raise ValueError("GUI ABI baseline identity changed")
    expected = copy.deepcopy(baseline["methods"])
    for name, signature in delta["added_methods"].items():
        if name in expected:
            raise ValueError("Existing GUI method cannot be added again")
        _arguments(signature)
        expected[name] = copy.deepcopy(signature)
    for name, change in delta["extended_methods"].items():
        if (name not in baseline["methods"] or change["before"] != baseline["methods"][name]
                or change["kind"] != "trailing_optional_parameters"
                or change["after"]["returns"] != change["before"]["returns"]):
            raise ValueError("GUI ABI extension must preserve its exact historical signature")
        old, new = _arguments(change["before"]), _arguments(change["after"])
        extra = len(new.args) - len(old.args)
        if (extra <= 0 or len(new.defaults) != len(old.defaults) + extra
                or any(not _same(a, b) for a, b in zip(old.args, new.args[:len(old.args)], strict=True))
                or any(not _same(a, b) for a, b in zip(old.defaults, new.defaults[:len(old.defaults)], strict=True))):
            raise ValueError("Only trailing optional GUI parameters may be added")
        for field in ("posonlyargs", "vararg", "kwonlyargs", "kw_defaults", "kwarg"):
            left, right = getattr(old, field), getattr(new, field)
            if isinstance(left, list):
                equal = len(left) == len(right) and all(
                    a is b if a is None or b is None else _same(a, b)
                    for a, b in zip(left, right, strict=True))
            else:
                equal = left is right if left is None or right is None else _same(left, right)
            if not equal:
                raise ValueError("Existing GUI parameter structure cannot change")
        expected[name] = copy.deepcopy(change["after"])
    return expected
