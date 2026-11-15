"""New authoring policy vs immutable historical replay semantics."""
import copy

import pytest

from genshin_app.equipment import compose_equipment_plan, new_plan
from genshin_app.equipment.energy_policy import (
    POLICY,
    attach_energy_policy,
    require_replay_energy_policy,
    validate_energy_policy_binding,
)
from genshin_app.equipment.execution_verification import ExecutionVerificationError
from tests.test_mechanics_execution import fixture
from tests.test_plan_mechanics import native


@pytest.mark.parametrize("explicit", [False, True])
def test_create_and_compose_preserve_native_and_compile_policy(tmp_path, explicit):
    assert new_plan()['direct_energy_policy'] == POLICY
    plan = native()
    t, r, m = fixture((.2,))
    plan.update(manual_template=t, rotation_model=r, mechanics_model=m)
    plan['effect_bindings'] = []
    if explicit:
        plan['rotation_model']['direct_energy_policy'] = POLICY
    else:
        plan.pop('direct_energy_policy', None)
        plan['rotation_model'].pop('direct_energy_policy', None)
    before = copy.deepcopy(plan)
    out = compose_equipment_plan(plan, tmp_path / 'absent.db')
    from genshin_app.equipment.files import input_view
    assert input_view(out['plan']) == input_view(before)
    compiled = out.get('compiled_rotation_model', out['plan']['rotation_model'])
    assert compiled['direct_energy_policy'] == POLICY
    assert compiled is not plan['rotation_model']
    assert plan == before


def test_policy_binding_freezes_actual_rotation_and_unknown_policy_rejects():
    _, rotation, mechanics = fixture((.2,))
    rotation['direct_energy_policy'] = POLICY
    result = {}
    attach_energy_policy(result, rotation, mechanics)
    receipt = copy.deepcopy(result)
    assert validate_energy_policy_binding(result, receipt, rotation=rotation)
    changed = copy.deepcopy(rotation); changed['period'] += 1
    with pytest.raises(ValueError):
        validate_energy_policy_binding(result, receipt, rotation=changed)
    result['energy_settlement_policy']['policy_id'] = 'future-unknown'
    with pytest.raises(ValueError):
        validate_energy_policy_binding(result, receipt)


def test_old_direct_material_is_not_reinterpreted_during_replay():
    _, rotation, mechanics = fixture((.2,))
    rotation['events'] = [{'type': 'energy_direct', 'amounts': {'a': 3}}]
    execution = {'rotation_model': rotation, 'mechanics_model': mechanics}
    before = copy.deepcopy(execution)
    with pytest.raises(ExecutionVerificationError) as exc:
        require_replay_energy_policy(execution)
    assert exc.value.code == 'energy_policy_replay_incompatible'
    assert execution == before
    rotation['direct_energy_policy'] = POLICY
    require_replay_energy_policy(execution)


def test_old_no_direct_material_and_frozen_result_do_not_gain_current_markers():
    _, rotation, mechanics = fixture((.2,))
    require_replay_energy_policy({'rotation_model': rotation, 'mechanics_model': mechanics})
    result, receipt = {}, {}
    assert validate_energy_policy_binding(result, receipt) is None
    assert result == {} and receipt == {}


def test_rule_derived_direct_energy_requires_policy_without_static_energy_event():
    # Condition/state/timer dispatch invokes rules.resources: this is the other
    # actual runtime producer, even when the authored rotation has no direct event.
    _, rotation, mechanics = fixture((.2,))
    rotation['events'] = []
    from tests.test_mechanics_execution import rule
    mechanics['rules'] = [rule(resources=[{'id': 'refund', 'kind': 'energy_direct',
        'delay': 0, 'amounts': {'a': 3}, 'source_part': 'refund'}])]
    execution = {'rotation_model': rotation, 'mechanics_model': mechanics}
    before = copy.deepcopy(execution)
    with pytest.raises(ExecutionVerificationError) as exc:
        require_replay_energy_policy(execution)
    assert exc.value.code == 'energy_policy_replay_incompatible'
    assert execution == before
    rotation['direct_energy_policy'] = POLICY
    require_replay_energy_policy(execution)
    result = {}
    attach_energy_policy(result, rotation, mechanics)
    assert result['energy_settlement_policy']['direct_energy_present'] is True


@pytest.mark.parametrize("drop", ["working", "selected", "both"])
def test_adapter_marker_loss_is_rejected_even_without_direct_events(tmp_path, monkeypatch, drop):
    from dataclasses import replace

    from genshin_app.equipment import composer
    from genshin_app.equipment.schema import PlanError
    plan = native()
    template, rotation, mechanics = fixture((.2,))
    assert rotation["events"] == [] and mechanics["rules"] == []
    plan.update(manual_template=template, rotation_model=rotation, mechanics_model=mechanics)
    plan["effect_bindings"] = []
    real = composer.prepare_agent_execution_overlay
    def lost(*args, **kwargs):
        overlay = real(*args, **kwargs)
        context = args[0]
        if drop in {"working", "both"}:
            context.plan["rotation_model"] = copy.deepcopy(context.plan["rotation_model"])
            context.plan["rotation_model"].pop("direct_energy_policy")
        if drop in {"selected", "both"}:
            broken = copy.deepcopy(overlay.compiled_rotation)
            broken.pop("direct_energy_policy")
            overlay = replace(overlay, compiled_rotation=broken)
        return overlay
    monkeypatch.setattr(composer, "prepare_agent_execution_overlay", lost)
    with pytest.raises(PlanError) as exc:
        compose_equipment_plan(plan, tmp_path / "absent.db")
    assert exc.value.reason == "energy_policy_execution_unmarked"


def test_attach_and_self_signed_binding_cannot_claim_unmarked_nonconsumer():
    from genshin_app.equipment.energy_policy import compile_current_energy_rotation
    from genshin_app.equipment.files import fingerprint
    _, raw, mechanics = fixture((.2,))
    assert raw["events"] == [] and mechanics["rules"] == []
    result = {}
    with pytest.raises(ValueError):
        attach_energy_policy(result, raw, mechanics)
    assert result == {}
    compiled = compile_current_energy_rotation(raw)
    attach_energy_policy(result, compiled, mechanics)
    forged = result["energy_settlement_policy"]
    forged["execution_rotation_fingerprint"] = fingerprint(raw)
    forged["binding_fingerprint"] = fingerprint({k:v for k,v in forged.items() if k!="binding_fingerprint"})
    receipt = copy.deepcopy(result)
    with pytest.raises(ValueError):
        validate_energy_policy_binding(result, receipt, rotation=raw)


def test_no_axis_and_no_energy_consumer_can_bind_actual_none():
    result = {"rotation_check": {"resolved_rotation_model": None}}
    attach_energy_policy(result, None)
    assert result["energy_settlement_policy"]["direct_energy_present"] is False
    assert validate_energy_policy_binding(result, copy.deepcopy(result), rotation=None)


def test_policy_compilation_does_not_alias_author_axis():
    from genshin_app.equipment.energy_policy import compile_current_energy_rotation
    _, raw, _ = fixture((.2,))
    before = copy.deepcopy(raw)
    compiled = compile_current_energy_rotation(raw)
    compiled["actions"][0]["start"] = 99
    assert raw == before and "direct_energy_policy" not in raw


def test_v2_actual_panel_er_and_author_identity(tmp_path):
    from genshin_app.equipment import run_plan_calc
    from genshin_app.equipment.delivery import validate_delivery_calculated
    from genshin_app.equipment.files import input_view
    from genshin_app.rotation_check import check_document
    from tests.test_mechanics_execution import EVIDENCE
    template, rotation, _ = fixture((.2,))
    template["characters"][0]["stats"]["energy_recharge"] = 2
    rotation["characters"][0].update(energy_recharge=1, initial_energy=0)
    rotation["abilities"][0].update(
        cooldown={"group": "e", "duration": 0, "start": "action_start",
                  "charges": 1, "recovery": "sequential"},
        energy={"required": 0, "amount": 0})
    rotation["initial_cooldowns"] = [
        {"character": "a", "group": "e", "ready_in": []}]
    rotation["events"] = [
        {"id": "cost", "type": "energy_spend", "t": 0, "order": 1,
         "character": "a", "action_id": "cast", "required": 0, "amount": 0,
         "repeat": {"mode": "each_cycle"}, **EVIDENCE},
        {"id": "refund", "type": "energy_direct", "t": .05, "order": 2,
         "amounts": {"a": 3}, "repeat": {"mode": "once"}, **EVIDENCE},
    ]
    rotation["action_hit_links"] = [{"action_id": "cast", "hit_id": "h0"}]
    plan = new_plan(template)
    plan["plan_version"] = 2
    plan["rotation_model"] = rotation
    plan.pop("direct_energy_policy", None)
    before = copy.deepcopy(plan)
    value = run_plan_calc(plan, tmp_path/"absent.db", capture_display=True)
    validate_delivery_calculated(value)
    assert plan == before
    assert input_view(value["plan"]) == input_view(before)
    check = value["result"]["rotation_check"]
    assert check["action_check"]["status"] == "passed"
    assert check["energy_check"]["opening_status"] == "passed"
    assert check["resolved_rotation_model"]["characters"][0]["energy_recharge"] == 2
    event = next(row for row in check["event_log"] if row.get("type") == "energy_direct")
    gain = event["energy"][0]
    assert gain["base_amount"] == 3
    assert gain["energy_recharge"] == 2
    assert gain["theoretical_amount"] == 6
    assert gain["after"] == 6
    assert check_document(plan, tmp_path/"absent.db") == check


def test_v2_no_axis_no_energy_consumer_preserves_native(tmp_path):
    from genshin_app.equipment import run_plan_calc
    from genshin_app.equipment.delivery import validate_delivery_calculated
    from genshin_app.equipment.files import input_view
    template, _, _ = fixture((.2,))
    plan = new_plan(template)
    plan["plan_version"] = 2
    plan["rotation_model"] = None
    plan.pop("direct_energy_policy", None)
    before = copy.deepcopy(plan)
    value = run_plan_calc(plan, tmp_path/"absent.db", capture_display=True)
    validate_delivery_calculated(value)
    assert plan == before
    assert input_view(value["plan"]) == input_view(before)
    assert value["result"]["rotation_check"]["resolved_rotation_model"] is None
    assert value["result"]["energy_settlement_policy"]["direct_energy_present"] is False


def test_no_axis_with_actual_direct_consumer_cannot_claim_current_policy():
    result = {}
    mechanics = {"rules": [{"resources": [{"kind": "energy_direct"}]}]}
    with pytest.raises(ValueError):
        attach_energy_policy(result, None, mechanics)
    assert result == {}


@pytest.mark.parametrize("missing", ["check", "resolved_model"])
def test_result_reader_rejects_current_claim_without_actual_axis_context(missing):
    from genshin_app.calculation_integrity import validate_result_binding
    from genshin_app.equipment.files import fingerprint
    result = {"rotation_check": {"resolved_rotation_model": None},
              "summary": {"total_damage": 0}}
    attach_energy_policy(result, None)
    if missing == "check":
        result.pop("rotation_check")
    else:
        result["rotation_check"].pop("resolved_rotation_model")
    receipt = {"result_fingerprint": fingerprint(result),
               "energy_settlement_policy": copy.deepcopy(result["energy_settlement_policy"])}
    with pytest.raises(ValueError):
        validate_result_binding(result, receipt)


def test_result_reader_accepts_actual_none_and_preserves_legacy_nonconsumer():
    from genshin_app.calculation_integrity import validate_result_binding
    from genshin_app.equipment.files import fingerprint
    current = {"rotation_check": {"resolved_rotation_model": None},
               "summary": {"total_damage": 0}}
    attach_energy_policy(current, None)
    receipt = {"result_fingerprint": fingerprint(current),
               "energy_settlement_policy": copy.deepcopy(current["energy_settlement_policy"])}
    assert validate_result_binding(current, receipt) == fingerprint(current)
    legacy = {"summary": {"total_damage": 0}}
    before = copy.deepcopy(legacy)
    assert validate_result_binding(legacy, {"result_fingerprint": fingerprint(legacy)}) == fingerprint(legacy)
    assert legacy == before and "energy_settlement_policy" not in legacy


def test_saved_policy_reconstruction_uses_known_contract_without_upgrading_legacy():
    from genshin_app.equipment.energy_policy import (
        compile_current_energy_rotation,
        reconstruct_energy_policy_rotation,
    )
    _, native_rotation, _ = fixture((.2,))
    original = copy.deepcopy(native_rotation)
    compiled = compile_current_energy_rotation(native_rotation)
    result = {"rotation_check": {"resolved_rotation_model": compiled}}
    attach_energy_policy(result, compiled)
    receipt = copy.deepcopy(result)
    rebuilt = reconstruct_energy_policy_rotation(native_rotation, result, receipt)
    assert rebuilt == compiled and rebuilt is not native_rotation
    assert native_rotation == original
    legacy = reconstruct_energy_policy_rotation(native_rotation, {}, {})
    assert legacy == original and "direct_energy_policy" not in legacy
    assert legacy is not native_rotation


@pytest.mark.parametrize("tamper", ["unknown_policy", "missing_axis", "unmarked_axis"])
def test_saved_policy_reconstruction_cannot_upgrade_invalid_claim(tamper):
    from genshin_app.equipment.energy_policy import (
        compile_current_energy_rotation,
        reconstruct_energy_policy_rotation,
    )
    from genshin_app.equipment.files import fingerprint
    _, native_rotation, _ = fixture((.2,))
    original = copy.deepcopy(native_rotation)
    compiled = compile_current_energy_rotation(native_rotation)
    result = {"rotation_check": {"resolved_rotation_model": compiled}}
    attach_energy_policy(result, compiled)
    if tamper == "unknown_policy":
        binding = result["energy_settlement_policy"]
        binding["policy_id"] = "unknown-policy"
        binding["binding_fingerprint"] = fingerprint({
            k: v for k, v in binding.items() if k != "binding_fingerprint"})
    elif tamper == "missing_axis":
        result.pop("rotation_check")
    else:
        result["rotation_check"]["resolved_rotation_model"].pop("direct_energy_policy")
    receipt = copy.deepcopy(result)
    with pytest.raises(ValueError):
        reconstruct_energy_policy_rotation(native_rotation, result, receipt)
    assert native_rotation == original
