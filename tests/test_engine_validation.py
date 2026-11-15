"""接手复现回归：结构/有限数值/反应等级、补丁定位与输入原子性。"""

import copy
import json

import pytest

from genshin_engine import (
    PatchError,
    TemplateError,
    apply_patch,
    calculate,
    validate_template,
)


def template():
    return {
        "version": 1, "form": "resolved", "meta": {"name": "边界测试"},
        "enemy": {"level": 90, "resistance": {"default": 0.1}},
        "characters": [{"id": "a", "level": 90,
                        "stats": {"atk": 2000, "em": 100, "crit_rate": 0.4, "crit_dmg": 2}}],
        "switches": [{"t": 0, "character": "a"}],
        "buffs": [{"id": "b", "start": 0, "end": 10,
                   "effects": [{"zone": "dmg_bonus", "stat": "all", "value": 0.5}]}],
        "hits": [{"t": 1, "character": "a", "skill": "s", "damage_system": "classic",
                  "element": "pyro", "multiplier": 2}],
        "loop_duration": 20,
    }


def put(t, path, value):
    node = t
    for key in path[:-1]:
        node = node[key]
    node[path[-1]] = value


@pytest.mark.parametrize("path,value,location", [
    (("meta",), None, "meta"),
    (("enemy",), [], "enemy"),
    (("characters",), None, "characters"),
    (("characters", 0), None, "characters[0]"),
    (("characters", 0, "id"), [], "characters[0].id"),
    (("characters", 0, "stats"), None, "characters[0].stats"),
    (("switches",), None, "switches"),
    (("switches", 0), "bad", "switches[0]"),
    (("switches", 0, "character"), {}, "switches[0].character"),
    (("buffs",), None, "buffs"),
    (("buffs", 0), [], "buffs[0]"),
    (("buffs", 0, "effects"), None, "buffs[0].effects"),
    (("buffs", 0, "effects", 0), None, "buffs[0].effects[0]"),
    (("buffs", 0, "target"), [], "buffs[0].target"),
    (("hits",), None, "hits"),
    (("hits", 0), None, "hits[0]"),
    (("hits", 0, "reaction"), "overload", "hits[0].reaction"),
    (("hits", 0, "snapshot"), "false", "hits[0].snapshot"),
    (("hits", 0, "reaction_base"), 1, "hits[0].reaction_base"),
    (("hits", 0, "scaling_stat"), "attack_typo", "hits[0].scaling_stat"),
    (("hits", 0, "element"), "fire", "hits[0].element"),
    (("enemy", "resistance", "default"), "10%", "enemy.resistance.default"),
    (("enemy", "defense_ignore"), "0.3", "enemy.defense_ignore"),
    (("characters", 0, "stats", "crit_rate"), -1, "characters[0].stats.crit_rate"),
    (("characters", 0, "stats", "crit_rate"), 4, "characters[0].stats.crit_rate"),
    (("characters", 0, "stats", "atk"), -2000, "characters[0].stats.atk"),
    (("version",), True, "version"),
    (("enemy", "level"), True, "enemy.level"),
    (("characters", 0, "level"), True, "characters[0].level"),
    (("hits", 0, "count"), True, "hits[0].count"),
    (("meta", "assumptions"), "one string", "meta.assumptions"),
    (("meta", "assumptions"), ["valid", 2], "meta.assumptions[1]"),
])
def test_malformed_values_are_located_template_errors(path, value, location):
    t = template()
    put(t, path, value)
    with pytest.raises(TemplateError) as error:
        calculate(t)
    assert location in str(error.value)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf"), 10 ** 400])
@pytest.mark.parametrize("path", [
    ("characters", 0, "stats", "atk"), ("hits", 0, "t"), ("meta", "extra_number"),
])
def test_nonfinite_numbers_rejected_even_in_extensions(path, value):
    t = template()
    put(t, path, value)
    with pytest.raises(TemplateError, match="有限"):
        validate_template(t)


def test_snapshot_invalid_cast_type_reports_field_instead_of_typeerror():
    t = template()
    t["hits"][0].update(snapshot=True, cast_t="bad")
    with pytest.raises(TemplateError) as error:
        validate_template(t)
    assert "hits[0].cast_t" in str(error.value)


@pytest.mark.parametrize("value", ["bad", -1, 0, None, True])
def test_factor_override_is_checked_even_for_known_reaction(value):
    t = template()
    t["hits"][0].update(damage_system="transformative",
                        reaction={"type": "overload", "factor": value})
    with pytest.raises(TemplateError) as error:
        validate_template(t)
    assert "hits[0].reaction.factor" in str(error.value)


@pytest.mark.parametrize("value", ["bad", -1, 1.5, None, True])
def test_layers_are_nonnegative_integers(value):
    t = template()
    t["hits"][0].update(damage_system="stellar",
                        reaction={"type": "stellar_superconduct", "layers": value})
    with pytest.raises(TemplateError) as error:
        validate_template(t)
    assert "hits[0].reaction.layers" in str(error.value)


@pytest.mark.parametrize("system,reaction,react_base", [
    ("classic", {"type": "aggravate"}, False),
    ("classic", {"type": "spread"}, False),
    ("transformative", {"type": "overload"}, False),
    ("lunar", {"type": "lunar_electro"}, True),
    ("stellar", {"type": "stellar_swirl"}, True),
])
def test_unsupported_reaction_level_fails_at_validation(system, reaction, react_base):
    t = template()
    t["characters"][0]["level"] = 91
    t["hits"][0].update(damage_system=system, reaction=reaction, reaction_base=react_base)
    with pytest.raises(TemplateError) as error:
        validate_template(t)
    message = str(error.value)
    assert "hits[0].character" in message and "91" in message and "70, 80, 90, 95, 100" in message


@pytest.mark.parametrize("system,reaction", [
    ("classic", None), ("classic", {"type": "amplify", "factor": 1.5}),
    ("lunar", {"type": "lunar_electro"}), ("stellar", {"type": "stellar_superconduct"}),
])
def test_direct_damage_does_not_require_reaction_base_level(system, reaction):
    t = template()
    t["characters"][0]["level"] = 91
    t["hits"][0].update(damage_system=system, reaction=reaction)
    assert calculate(t)["summary"]["total_damage"] > 0


def test_missing_scaling_property_and_duplicate_buff_id_are_rejected():
    t = template()
    t["hits"][0]["scaling_stat"] = "hp"
    t["buffs"].append(copy.deepcopy(t["buffs"][0]))
    with pytest.raises(TemplateError) as error:
        validate_template(t)
    assert "stats.hp" in str(error.value)
    assert "buffs[1].id" in str(error.value)


def test_optional_lists_can_be_absent_and_extra_json_metadata_is_preserved():
    t = template()
    t.pop("switches")
    t.pop("buffs")
    t["meta"]["custom"] = {"notes": [None, True, 3, "ok"]}
    before = copy.deepcopy(t)
    assert calculate(t)["summary"]["total_damage"] > 0
    assert t == before


@pytest.mark.parametrize("ops", [{}, False, 0, ""])
def test_nonlist_patch_arguments_are_not_silently_ignored(ops):
    with pytest.raises(PatchError, match="列表"):
        calculate(template(), ops)


def test_empty_patch_is_valid_and_has_no_audit_operations():
    t = template()
    assert apply_patch(t, []) == t
    assert calculate(t, []) == calculate(t)


@pytest.mark.parametrize("op", [
    None, "bad", {}, {"op": "add", "path": 42, "value": 1},
    {"op": "replace", "path": "/characters/-1/stats/atk", "value": 1},
    {"op": "add", "path": "/hits/999", "value": {}},
    {"op": "remove", "path": "/hits/-"},
    {"op": "remove", "path": "/hits/01"},
    {"op": "replace", "path": "/meta/~x", "value": 1},
    {"op": "replace", "path": "", "value": 1},
])
def test_patch_errors_include_operation_and_path_without_mutating_input(op):
    t = template()
    before = copy.deepcopy(t)
    with pytest.raises(PatchError) as error:
        apply_patch(t, [op])
    assert "补丁[0]" in str(error.value) and "path=" in str(error.value)
    assert t == before


def test_pointer_escaping_and_patch_value_isolation():
    t = template()
    ops = [
        {"op": "add", "path": "/meta/a~1b~0c", "value": {"items": [1]}},
        {"op": "add", "path": "/meta/a~1b~0c/items/-", "value": 2},
    ]
    before = copy.deepcopy(ops)
    result = apply_patch(t, ops)
    assert result["meta"]["a/b~c"]["items"] == [1, 2]
    assert ops == before
    assert "a/b~c" not in t["meta"]


def test_final_validation_failure_preserves_patch_and_template():
    t = template()
    ops = [
        {"op": "add", "path": "/meta/extra", "value": {"items": [1]}},
        {"op": "add", "path": "/meta/extra/items/-", "value": 2},
        {"op": "replace", "path": "/characters/0/stats/atk", "value": -1},
    ]
    before_t, before_ops = copy.deepcopy(t), copy.deepcopy(ops)
    with pytest.raises(PatchError, match="校验失败"):
        apply_patch(t, ops)
    assert t == before_t and ops == before_ops


def test_finite_inputs_that_overflow_in_calculation_do_not_escape_as_infinity():
    t = template()
    t["characters"][0]["stats"]["atk"] = 1e308
    t["hits"][0]["multiplier"] = 1e308
    with pytest.raises(TemplateError) as error:
        calculate(t)
    assert "hits[0]" in str(error.value)


def test_finite_hit_results_that_overflow_total_raise_located_domain_error():
    t = template()
    t["characters"][0]["stats"].update(atk=1e308, crit_rate=0, crit_dmg=0)
    t["hits"][0]["multiplier"] = 1
    one = calculate(t)["summary"]["total_damage"]
    assert one == pytest.approx(6.75e307)
    t["hits"] = [copy.deepcopy(t["hits"][0]) for _ in range(3)]
    with pytest.raises(TemplateError) as error:
        calculate(t)
    assert "hits[2].total_damage" in str(error.value)


def test_dps_overflow_is_a_domain_error_not_an_infinite_json_number():
    t = template()
    t["loop_duration"] = 1e-320
    with pytest.raises(TemplateError) as error:
        calculate(t)
    assert "result.summary.dps" in str(error.value)


@pytest.mark.parametrize("zone", ["stat_add", "stat_pct"])
def test_dynamic_panel_zones_reject_non_panel_stats(zone):
    t = template()
    t["buffs"][0]["effects"][0].update(zone=zone, stat="em")
    with pytest.raises(TemplateError) as error:
        validate_template(t)
    assert "buffs[0].effects[0].stat" in str(error.value)


def _deep_json(depth=600):
    value = {}
    node = value
    for _ in range(depth):
        node["x"] = {}
        node = node["x"]
    # The regression uses real JSON-compatible input that the file loader accepts.
    return json.loads(json.dumps(value))


@pytest.mark.parametrize("call,error_type,location", [
    (calculate, TemplateError, "template"),
    (lambda t: apply_patch(t, []), PatchError, "补丁模板"),
    (lambda t: calculate(t, []), PatchError, "补丁模板"),
])
def test_deep_template_copy_raises_domain_error_without_mutation(call, error_type, location):
    t = template()
    t["meta"]["extra"] = _deep_json()
    before = json.dumps(t)
    with pytest.raises(error_type) as error:
        call(t)
    assert location in str(error.value) and "嵌套过深" in str(error.value)
    assert json.dumps(t) == before


@pytest.mark.parametrize("action,path", [
    ("add", "/meta/extra"), ("replace", "/meta/name"),
    ("add", "/meta/assumptions/-"), ("replace", "/meta/assumptions/0"),
])
def test_deep_patch_value_is_located_and_atomic(action, path):
    t = template()
    t["meta"]["assumptions"] = ["valid"]
    ops = [{"op": action, "path": path, "value": _deep_json()}]
    before_t, before_ops = json.dumps(t), json.dumps(ops)
    with pytest.raises(PatchError) as error:
        apply_patch(t, ops)
    message = str(error.value)
    assert "补丁[0]" in message and path in message and "value" in message
    assert "嵌套过深" in message
    assert json.dumps(t) == before_t and json.dumps(ops) == before_ops


def test_deep_unused_patch_metadata_fails_as_audit_copy_domain_error():
    t = template()
    ops = [{"op": "replace", "path": "/characters/0/stats/atk", "value": 2200,
            "extra": _deep_json()}]
    before_t, before_ops = json.dumps(t), json.dumps(ops)
    # Applying the operation succeeds; its unused metadata is copied for result audit later.
    assert apply_patch(t, ops)["characters"][0]["stats"]["atk"] == 2200
    with pytest.raises(PatchError, match="patches_applied.*嵌套过深"):
        calculate(t, ops)
    assert json.dumps(t) == before_t and json.dumps(ops) == before_ops


def test_deep_patch_final_validation_is_located_and_atomic():
    t = template()
    ops = [{"op": "add", "path": "/meta/extra", "value": _deep_json(110)}]
    before_t, before_ops = json.dumps(t), json.dumps(ops)
    with pytest.raises(PatchError, match="补丁应用后模板校验失败.*meta.extra.*嵌套过深"):
        apply_patch(t, ops)
    assert json.dumps(t) == before_t and json.dumps(ops) == before_ops


def test_patch_can_still_repair_invalid_source_schema_before_validation():
    t = template()
    t["characters"][0]["stats"]["atk"] = -1
    del t["enemy"]
    before = copy.deepcopy(t)
    ops = [
        {"op": "replace", "path": "/characters/0/stats/atk", "value": 2200},
        {"op": "add", "path": "/enemy", "value": {"level": 90}},
    ]
    assert calculate(t, ops)["summary"]["total_damage"] > 0
    assert t == before
