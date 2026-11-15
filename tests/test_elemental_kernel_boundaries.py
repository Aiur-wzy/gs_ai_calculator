"""The target kernels share one state; canonical target previews stay isolated."""

from __future__ import annotations

import pytest
from test_elemental_icd_identity import model, profile
from test_elemental_multiaura_v4 import model as multi_model

from genshin_engine import elemental as owner
from genshin_engine.elemental_world import ElementalWorldRuntime


def test_icd_leaf_runs_once_with_the_same_target_state(monkeypatch):
    value = owner.ElementalRuntime(model([profile("component", tag="normal")]))
    root_icd, root_bindings = value.icd, value.counter_bindings
    original = owner.admit
    seen = []

    def observe(kernel, actor, item, **ports):
        seen.append(kernel)
        return original(kernel, actor, item, **ports)

    monkeypatch.setattr(owner, "admit", observe)
    assert value._admit("actor", value.profiles["component"])[0] is True
    assert seen == [value]
    assert value.icd is root_icd and value.counter_bindings is root_bindings


def test_icd_canonical_preview_does_not_run_the_actual_leaf(monkeypatch):
    value = owner.ElementalRuntime(model([profile("component", tag="normal")]))
    canonical = ElementalWorldRuntime._reference_target_copy(value)

    def corrupted(*_args, **_kwargs):
        raise ValueError("injected actual ICD defect")

    monkeypatch.setattr(owner, "admit", corrupted)
    assert canonical._admit("actor", canonical.profiles["component"])[0] is True
    with pytest.raises(ValueError, match="injected"):
        value._admit("actor", value.profiles["component"])
    assert value.icd == {}
    assert "admit" not in canonical._admit.__func__.__code__.co_names


def test_aura_canonical_preview_does_not_run_the_actual_leaf(monkeypatch):
    value = owner.ElementalRuntime(model([profile("component", tag="normal")]))
    canonical = ElementalWorldRuntime._reference_target_copy(value)

    def corrupted(*_args, **_kwargs):
        raise ValueError("injected actual aura defect")

    monkeypatch.setattr(owner, "advance_elemental_state", corrupted)
    canonical._advance_elemental_state(1_000_000)
    assert canonical.time == 1_000_000
    with pytest.raises(ValueError, match="injected"):
        value._advance_elemental_state(1_000_000)
    assert value.time == 0


def test_kernel_preserves_instance_rule_and_prune_fault_hooks(monkeypatch):
    value = owner.ElementalRuntime(model([profile("component", tag="normal")]))

    def rule_fault(*_args):
        raise ValueError("instance rule hook")

    monkeypatch.setattr(value, "_rule_signature", rule_fault)
    with pytest.raises(ValueError, match="instance rule"):
        value._admit("actor", value.profiles["component"])
    assert value.icd == {} and value.counter_bindings == {}

    def prune_fault():
        raise ValueError("instance prune hook")

    monkeypatch.setattr(value, "_prune_recent_elemental_applications", prune_fault)
    with pytest.raises(ValueError, match="instance prune"):
        value._advance_elemental_state(123)
    # Existing local partial-commit behavior remains: time advancement happens
    # before pruning; this refactor does not invent a stronger transaction.
    assert value.time == 123


def test_multi_resolver_canonical_does_not_run_the_actual_dispatcher(monkeypatch):
    value = owner.ElementalRuntime(multi_model([], freezable=False))
    canonical = ElementalWorldRuntime._reference_target_copy(value)

    def corrupted(_contact):
        raise ValueError("actual resolver failure")

    monkeypatch.setattr(owner, "apply_multi_contact", corrupted)
    arguments = {
        "event_id": "contact", "action_id": "action", "actor": "actor", "time": 0,
        "application": {"profile_id": "pyro", "target_id": "enemy", "landed": True},
    }
    assert canonical.attempt(**arguments)["application_allowed"] is True
    with pytest.raises(ValueError, match="actual resolver"):
        value.attempt(**arguments)
    assert value.multi_state.auras == {}
    assert canonical.multi_state.auras["pyro"]["remaining_gu"] == .8
    assert "apply_multi_contact" not in canonical._apply_multi.__func__.__code__.co_names
