"""Parsing compatibility: source identity, malformed input and legacy depth order."""
import hashlib
import json
import math
import sys

import pytest

from genshin_db.runtime_catalog import (
    RuntimeConfigError,
    _object,
    fingerprint,
    parse_source,
    walk,
)


def _old_parse_source(payload):
    """The frozen pre-EC17a parser, for boundary/error differential checks only."""
    def bad_constant(value):
        raise ValueError(f"non-finite number: {value}")
    try:
        value = json.loads(payload.decode("utf-8-sig") if isinstance(payload, bytes) else payload,
                           object_pairs_hook=_object, parse_constant=bad_constant)
        for _, node in walk(value):
            if isinstance(node, float) and not math.isfinite(node):
                raise ValueError("non-finite number")
        return value
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise RuntimeConfigError(f"运行配置JSON无效: {exc}", "dataset_invalid", 500) from exc


def test_finite_value_identity_and_ieee_sign_are_unchanged():
    source = b'{"a/b~":[0,false,-1,null,-0.0,1e-999,-1e-999],"n":9007199254740991,"x":1.7976931348623157e308}'
    parsed = parse_source(source)
    assert parsed["a/b~"][:4] == [0, False, -1, None]
    assert type(parsed["a/b~"][0]) is int
    assert type(parsed["a/b~"][1]) is bool
    assert [item.hex() for item in parsed["a/b~"][4:]] == ["-0x0.0p+0", "0x0.0p+0", "-0x0.0p+0"]
    assert parsed["n"] == 9007199254740991
    assert parsed["x"].hex() == "0x1.fffffffffffffp+1023"
    canonical = b'{"a/b~":[0,false,-1,null,-0.0,0.0,-0.0],"n":9007199254740991,"x":1.7976931348623157e+308}'
    assert fingerprint(parsed) == hashlib.sha256(canonical).hexdigest()


@pytest.mark.parametrize(("source", "message"), [
    ('{"x":1e400}', "non-finite number"),
    ('[-1e400]', "non-finite number"),
    ('{"x":NaN}', "non-finite number: NaN"),
    ('{"x":Infinity}', "non-finite number: Infinity"),
    ('{"x":-Infinity}', "non-finite number: -Infinity"),
    ('{"x":1e400,"x":0}', "运行配置含重复JSON键: x"),
    ('{"x":0,"x":1e400}', "运行配置含重复JSON键: x"),
    ('[1e400,{"x":1,"x":2}]', "运行配置含重复JSON键: x"),
    ('[1e400,NaN]', "non-finite number: NaN"),
    ('{"x":NaN,"x":0}', "non-finite number: NaN"),
    ('[{"x":1,"x":2},NaN]', "运行配置含重复JSON键: x"),
    ('[1e400,]', "Expecting value: line 1 column 8 (char 7)"),
    ('[1e400] false', "Extra data: line 1 column 9 (char 8)"),
    ('{"a":1,"\\u0061":2}', "运行配置含重复JSON键: a"),
])
def test_invalid_number_duplicate_and_syntax_precedence(source, message):
    with pytest.raises(RuntimeConfigError) as result:
        parse_source(source)
    assert result.value.reason == "dataset_invalid"
    assert result.value.status == 500
    assert str(result.value) == "运行配置JSON无效: " + message


@pytest.mark.parametrize("source", [
    b'\xef\xbb\xbf{"x":1.5}', bytearray(b'\xef\xbb\xbf{"x":1.5}'),
    bytearray('{"x":1.5}'.encode("utf-16")), '{"x":1.5}',
])
def test_preserve_existing_text_and_bytearray_encoding_paths(source):
    assert parse_source(source) == {"x": 1.5}


@pytest.mark.parametrize("source", [b'{"x":"\xff"}', '{"x":1.5}'.encode("utf-16"), '\ufeff{"x":1.5}'])
def test_rejected_encoding_matches_frozen_parser(source):
    errors = []
    for parser in (parse_source, _old_parse_source):
        with pytest.raises(RuntimeConfigError) as result:
            parser(source)
        errors.append((str(result.value), result.value.reason, result.value.status))
    assert errors[0] == errors[1]


def test_unicode_lone_surrogate_is_preserved_as_existing_data():
    parsed = parse_source('{"s":"\\ud800","角色":"神里绫华"}')
    assert parsed == {"s": "\ud800", "角色": "神里绫华"}


@pytest.mark.parametrize("source", [None, 7, True, []])
def test_non_json_input_type_error_remains_unwrapped(source):
    with pytest.raises(TypeError):
        parse_source(source)


def test_public_walk_keeps_exact_pointer_escape_and_array_order():
    value = {"a/b~": [0, False], "": {"~1": -1}}
    assert list(walk(value)) == [("", value), ("/a~1b~0", [0, False]), ("/a~1b~0/0", 0),
                                 ("/a~1b~0/1", False), ("/", {"~1": -1}), ("//~01", -1)]


def _call_outcome(parser, source, padding):
    if padding:
        return _call_outcome(parser, source, padding - 1)
    try:
        parser(source)
        return ("accepted",)
    except RuntimeConfigError as exc:
        return ("rejected", type(exc).__name__, exc.reason, exc.status, str(exc))


@pytest.mark.parametrize("kind", ["array", "escaped_object", "alternating"])
@pytest.mark.parametrize("overflow", ["none", "before", "after"])
@pytest.mark.parametrize("padding", [0, 13])
def test_recursive_boundary_and_overflow_order_match_legacy(kind, overflow, padding):
    prior = sys.getrecursionlimit()
    try:
        sys.setrecursionlimit(250)
        compared, accepted, rejected = 0, False, False
        for depth in range(150, 252):
            layers = ["[" if kind == "array" or (kind == "alternating" and i % 2 == 0)
                      else '{"a/b~":' for i in range(depth)]
            nested = ''.join(layers) + '0' + ''.join(']' if token == '[' else '}' for token in reversed(layers))
            source = nested if overflow == "none" else (
                '[1e400,' + nested + ']' if overflow == "before" else '[' + nested + ',1e400]')
            expected = _call_outcome(_old_parse_source, source, padding)
            actual = _call_outcome(parse_source, source, padding)
            assert actual == expected, (kind, overflow, padding, depth, expected, actual)
            accepted |= expected[0] == "accepted"
            rejected |= expected[0] == "rejected"
            compared += 1
        assert compared == 102 and rejected
        if overflow == "none":
            assert accepted
    finally:
        sys.setrecursionlimit(prior)
