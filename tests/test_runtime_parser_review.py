"""Independent EC17a compatibility checks against the frozen 0.21 parser.

The oracle retains the old pointer walk, including error order and stack shape;
it does not call the new value-only helper. No formal database is opened.
"""
import hashlib
import json
import math
import sys

import pytest

from genshin_db import runtime_catalog as catalog


def _old_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise catalog.RuntimeConfigError(f"运行配置含重复JSON键: {key}", "dataset_invalid", 500)
        result[key] = value
    return result


def _old_escape(token):
    return str(token).replace("~", "~0").replace("/", "~1")


def _old_walk(value, pointer=""):
    yield pointer, value
    if isinstance(value, dict):
        for key, item in value.items():
            yield from _old_walk(item, pointer + "/" + _old_escape(key))
    elif isinstance(value, list):
        for i, item in enumerate(value):
            yield from _old_walk(item, pointer + f"/{i}")


def _old_parse(payload):
    # Frozen runtime_catalog.py SHA before EC17a:
    # 7bed218da5f29cef7e01e305c0c12a9bc42ecab6c60ec3e2100e7dbd40e41d28
    def bad_constant(value):
        raise ValueError(f"non-finite number: {value}")
    try:
        value = json.loads(payload.decode("utf-8-sig") if isinstance(payload, bytes) else payload,
                           object_pairs_hook=_old_object, parse_constant=bad_constant)
        for _, node in _old_walk(value):
            if isinstance(node, float) and not math.isfinite(node):
                raise ValueError("non-finite number")
        return value
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise catalog.RuntimeConfigError(f"运行配置JSON无效: {exc}", "dataset_invalid", 500) from exc


def _outcome(function, raw):
    try:
        function(raw)
        return ("accepted",)
    except (catalog.RuntimeConfigError, TypeError) as error:
        return (type(error).__name__, getattr(error, "reason", None),
                getattr(error, "status", None), str(error))


@pytest.mark.parametrize("raw,priority", [
    ('{"x":1e400,"x":0}', "含重复JSON键: x"),
    ('[1e400,{"a":1,"\\u0061":2}]', "含重复JSON键: a"),
    ('[1e400] false', "Extra data"),
    ('[1e400,]', "Expecting value"),
    ('{"x":NaN,"x":0}', "non-finite number: NaN"),
    ('[1e400,Infinity]', "non-finite number: Infinity"),
    ('[{"x":0,"x":1},NaN]', "含重复JSON键: x"),
    ('[-Infinity]', "non-finite number: -Infinity"),
    ('{"x":-1e400}', "non-finite number"),
])
def test_review_error_priority_matches_frozen_parser(raw, priority):
    actual = _outcome(catalog.parse_source, raw)
    assert actual == _outcome(_old_parse, raw)
    assert actual[:3] == ("RuntimeConfigError", "dataset_invalid", 500)
    assert priority in actual[3]


@pytest.mark.parametrize("raw,accepted", [
    (b'\xef\xbb\xbf{"x":1}', True),
    ('\ufeff{"x":1}', False),
    (bytearray('{"x":1}'.encode("utf-16")), True),
    ('{"x":1}'.encode("utf-16"), False),
    (b'{"x":"\xff"}', False),
    (bytearray(b'\xef\xbb\xbf{"x":1}'), True),
])
def test_review_encoding_keeps_bytes_and_bytearray_distinct(raw, accepted):
    actual = _outcome(catalog.parse_source, raw)
    assert actual == _outcome(_old_parse, raw)
    assert (actual == ("accepted",)) is accepted


@pytest.mark.parametrize("raw", [None, True, 7, memoryview(b'null')])
def test_review_invalid_input_type_is_not_rewrapped(raw):
    actual = _outcome(catalog.parse_source, raw)
    assert actual == _outcome(_old_parse, raw)
    assert actual[0] == "TypeError"


def test_review_exact_values_signed_zero_and_surrogate_are_preserved():
    raw = '{"角/~":[-0.0,0.0,-1e-999,1e-999,1.7976931348623157e308,2.2250738585072014e-308,1,false,null,"\\ud800"]}'
    value = catalog.parse_source(raw)["角/~"]
    old = _old_parse(raw)["角/~"]
    assert [type(item) for item in value] == [float] * 6 + [int, bool, type(None), str]
    assert [item.hex() for item in value[:6]] == [item.hex() for item in old[:6]]
    assert [math.copysign(1, item) for item in value[:4]] == [-1, 1, -1, 1]
    assert value[-1] == "\ud800"
    assert catalog.resolve_pointer(catalog.parse_source(raw), "/角~1~0/7") is False


def test_review_integer_digit_error_is_unchanged_without_altering_global_limit():
    limit = sys.get_int_max_str_digits()
    if not limit:
        pytest.skip("Interpreter has explicitly disabled the integer digit limit")
    actual = _outcome(catalog.parse_source, "[" + "9" * (limit + 1) + "]")
    assert actual == _outcome(_old_parse, "[" + "9" * (limit + 1) + "]")
    assert actual[:3] == ("RuntimeConfigError", "dataset_invalid", 500)
    assert "Exceeds the limit" in actual[3]
    assert sys.get_int_max_str_digits() == limit


def _padded(function, raw, remaining):
    if remaining:
        return _padded(function, raw, remaining - 1)
    return _outcome(function, raw)


def _nested(depth, kind):
    starts = ["[" if kind == "array" or i % 2 == 0 else '{"a/~":' for i in range(depth)]
    return "".join(starts) + "0" + "".join("]" if start == "[" else "}" for start in reversed(starts))


@pytest.mark.parametrize("kind,pad", [("array", 0), ("array", 11), ("alternating", 0), ("alternating", 11)])
def test_review_caller_relative_recursion_boundary_and_overflow_order(kind, pad):
    original = sys.getrecursionlimit()
    frame, stack_depth = sys._getframe(), 0
    while frame is not None:
        stack_depth += 1
        frame = frame.f_back
    try:
        sys.setrecursionlimit(stack_depth + 180)
        # Locate the old boundary in this actual pytest caller, rather than
        # baking in a depth observed under a different Python/call stack.
        low, high = 1, 220
        assert _padded(_old_parse, _nested(low, kind), pad) == ("accepted",)
        assert _padded(_old_parse, _nested(high, kind), pad) != ("accepted",)
        while high - low > 1:
            mid = (low + high) // 2
            if _padded(_old_parse, _nested(mid, kind), pad) == ("accepted",):
                low = mid
            else:
                high = mid
        for depth in range(low - 3, high + 4):
            nested = _nested(depth, kind)
            for raw in (nested, "[1e400," + nested + "]", "[" + nested + ",1e400]"):
                assert _padded(catalog.parse_source, raw, pad) == _padded(_old_parse, raw, pad)
    finally:
        sys.setrecursionlimit(original)


def _hash(value):
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def test_review_normal_nodes_pointers_and_both_reference_namespaces_keep_identity(tmp_path):
    original = [{"变体/~": {
        "abilityName": "Owner", "z": -0.0, "flag": False,
        "onAdded": [{"$type": "CreateGadget", "gadgetID": 101},
                    {"$type": "GenerateElemBall", "configID": 2021}],
        "modifiers": {"触发/~": {"onAdded": [{"$type": "AddAbility", "abilityName": "Other"}]}},
    }}]
    raw = json.dumps(original, ensure_ascii=False, indent=2).encode("utf-8")
    path = tmp_path / "source.json"
    path.write_bytes(raw)
    source_path, snapshot = "BinOutput/Ability/Temp/Review.json", "fixed-snapshot"
    new = catalog.extract_definitions(source_path, "ability", catalog.parse_source(path.read_bytes()), snapshot)
    old = catalog.extract_definitions(source_path, "ability", _old_parse(path.read_bytes()), snapshot)
    assert new == old and len(new) == 2
    pointers = ["/0/变体~1~0", "/0/变体~1~0/modifiers/触发~1~0"]
    assert [item["json_pointer"] for item in new] == pointers
    for item, pointer in zip(new, pointers, strict=True):
        assert item["definition_id"] == _hash([snapshot, source_path, pointer, item["definition_kind"]])
        assert item["node_sha256"] == _hash(catalog.resolve_pointer(original, pointer))
    for extract in (catalog.extract_references, catalog.extract_object_references, catalog.extract_material_references):
        assert [extract(item) for item in new] == [extract(item) for item in old]
    obj = catalog.extract_object_references(new[0])
    material = catalog.extract_material_references(new[0])
    assert len(obj) == len(material) == 1
    assert obj[0]["reference_id"] == _hash(["object-reference-v1", new[0]["definition_id"], pointers[0] + "/onAdded/0/gadgetID", "gadget_id_reference"])
    assert material[0]["reference_id"] == _hash(["material-reference-v1", new[0]["definition_id"], pointers[0] + "/onAdded/1/configID", "element_ball_material"])
    assert material[0]["legacy_reference_id"] == _hash([new[0]["definition_id"], pointers[0] + "/onAdded/1/configID"])
    assert obj[0]["reference_id"] != material[0]["reference_id"]
    assert hashlib.sha256(path.read_bytes()).digest() == hashlib.sha256(raw).digest()
