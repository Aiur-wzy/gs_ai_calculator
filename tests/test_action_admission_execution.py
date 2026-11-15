"""Runtime action-admission certificates; deterministic, database-free fixtures."""
from __future__ import annotations

import copy

import pytest

from genshin_engine import action_admission_audit as admission
from genshin_engine.elemental import ElementalPending
from genshin_engine.mechanics import _Mechanics, execute_mechanics
from genshin_engine.rotation import check_rotation, validate_rotation_model
from tests.test_mechanics_execution import EVIDENCE, fixture


def _marker():
    return {
        "contract": admission.SOURCE_CONTRACT,
        "binding_id": "admission.fixture.q",
        "model_ref": {"id": "fixture.action-admission", "revision": 1},
        "source_fingerprint": "a" * 64,
        "static_certificate_fingerprint": "b" * 64,
    }


def admission_fixture(*, initial_energy=80):
    template, rotation, mechanics = fixture((0.2,))
    rotation["characters"][0].update(
        energy_capacity=80, initial_energy=initial_energy
    )
    rotation["abilities"][0].update(
        id="q",
        action_type_id="action.elemental_burst",
        variant="default",
        cooldown={
            "group": "skill:fixture:q",
            "duration": 20,
            "charges": 1,
            "start": "action_start",
            "recovery": "parallel",
        },
        energy={"required": 80, "amount": 80, "delivery": "action_start"},
        action_admission_source=_marker(),
    )
    rotation["actions"][0].update(
        ability_id="q", repeat={"mode": "once"}, occupation_end=0.1
    )
    rotation["initial_cooldowns"] = [
        {"character": "a", "group": "skill:fixture:q", "ready_in": []}
    ]
    rotation["action_hit_links"] = [
        {"action_id": "cast", "hit_id": template["hits"][0]["id"]}
    ]
    return template, rotation, mechanics


def _managed_rows(result):
    return [
        row
        for row in result["event_log"]
        if row.get("type") == "action" and "action_admission" in row
    ]


def test_schema_marker_is_strict_and_bounded():
    _, rotation, _ = admission_fixture()
    assert validate_rotation_model(rotation)["status"] == "valid"

    changed = copy.deepcopy(rotation)
    changed["abilities"][0]["action_admission_source"]["unknown"] = True
    assert any(
        issue["code"] == "unknown_field"
        for issue in validate_rotation_model(changed)["issues"]
    )

    changed = copy.deepcopy(rotation)
    changed["abilities"][0]["action_admission_source"]["binding_id"] = "x" * 1025
    assert validate_rotation_model(changed)["status"] == "invalid"

    changed = copy.deepcopy(rotation)
    changed["abilities"][0]["action_admission_source"]["model_ref"][
        "revision"
    ] = True
    assert validate_rotation_model(changed)["status"] == "invalid"


@pytest.mark.parametrize(
    "mutation",
    [
        lambda ability: ability.update(variant="tap"),
        lambda ability: ability["cooldown"].update(start="occupation_end"),
        lambda ability: ability["cooldown"].update(recovery="sequential"),
        lambda ability: ability["cooldown"].update(charges=2),
        lambda ability: ability["energy"].update(amount=40),
        lambda ability: ability["cooldown"].update(
            capacity_source={
                "contract": "action-capacity-source-v1",
                "binding_id": "legacy.capacity",
                "source_fingerprint": "c" * 64,
            }
        ),
    ],
)
def test_private_marker_cannot_expand_first_slice_scope(mutation):
    template, rotation, _ = admission_fixture()
    mutation(rotation["abilities"][0])
    checked = check_rotation(template, rotation)
    assert checked["status"] == "invalid"
    assert any(issue["code"].startswith("action_admission_") for issue in checked["issues"])


def test_success_commits_one_certificate_without_queue_event_or_self_hash():
    template, rotation, _ = admission_fixture()
    original = copy.deepcopy(rotation)
    checked = check_rotation(template, rotation)
    assert rotation == original

    parent = _managed_rows(checked)[0]
    assert parent["action_admission"]["status"] == "committed"
    reference = parent["action_admission"]["certificate"]
    assert set(reference) == {"contract", "certificate_id", "run_id"}
    assert reference["certificate_id"].startswith(admission.PREFIX)

    summary = checked["action_admission_execution"]
    assert summary["committed_actions"] == summary["receipt_count"] == 1
    assert summary["rejected_actions"] == summary["not_committed_actions"] == 0
    assert summary["not_verified_actions"] == 0
    assert len(summary["receipts"]) == 1
    assert summary["reported_actions"] == sum(
        summary[key]
        for key in (
            "committed_actions",
            "rejected_actions",
            "not_committed_actions",
            "not_verified_actions",
        )
    )
    assert "receipts_fingerprint" in summary
    receipt = summary["receipts"][0]
    assert "receipt_fingerprint" not in receipt
    assert (receipt["energy_before"], receipt["energy_after"]) == (80, 0)
    assert (receipt["charges_before"], receipt["charges_after"]) == (1, 0)
    assert receipt["next_ready_at"] == 20
    assert (receipt["frontline_before"], receipt["frontline_after"]) == ("a", "a")
    assert receipt["gates"] == {
        "frontline": "passed",
        "occupation": "passed",
        "energy": "passed",
        "cooldown": "passed",
    }
    assert not any(row.get("id", "").startswith(admission.PREFIX) for row in checked["event_log"])
    assert checked["characters"][0]["final_energy"] == 0


@pytest.mark.parametrize(
    ("case", "reason"),
    [
        ("not_frontline", "not_frontline"),
        ("action_overlap", "action_overlap"),
        ("insufficient_energy", "insufficient_energy"),
        ("cooldown_unavailable", "cooldown_unavailable"),
    ],
)
def test_gate_failure_is_rejected_without_certificate(case, reason):
    template, rotation, _ = admission_fixture()
    if case == "not_frontline":
        template["characters"].insert(
            0,
            {"id": "b", "level": 90, "stats": copy.deepcopy(template["characters"][0]["stats"])}
        )
        rotation["characters"].append(
            {
                "id": "b",
                "element": "cryo",
                "energy_recharge": 1,
                "energy_capacity": 0,
                "initial_energy": 0,
                **EVIDENCE,
            }
        )
        rotation["initial_frontline"] = "b"
    elif case == "action_overlap":
        rotation["abilities"].append(
            {
                "id": "wait",
                "character": "a",
                "action_type_id": "action.wait",
                "cooldown": None,
                "energy": None,
                **EVIDENCE,
            }
        )
        rotation["actions"].insert(
            0,
            {
                "id": "wait-first",
                "ability_id": "wait",
                "start": 0,
                "occupation_end": 2,
                "order": -1,
                "repeat": {"mode": "once"},
                **EVIDENCE,
            },
        )
        rotation["actions"][1].update(start=1, occupation_end=1.1)
        template["hits"][0]["t"] = 1.2
    elif case == "insufficient_energy":
        rotation["characters"][0]["initial_energy"] = 0
    else:
        rotation["initial_cooldowns"][0]["ready_in"] = [5]

    checked = check_rotation(template, rotation)
    parent = _managed_rows(checked)[0]
    assert parent["action_admission"] == {
        "contract": admission.CONTRACT,
        "binding_id": "admission.fixture.q",
        "source_fingerprint": "a" * 64,
        "static_certificate_fingerprint": "b" * 64,
        "status": "rejected",
        "reason": reason,
    }
    summary = checked["action_admission_execution"]
    assert summary["committed_actions"] == summary["receipt_count"] == 0
    assert summary["rejected_actions"] == 1
    assert summary["receipts"] == []
    if case in {"insufficient_energy", "cooldown_unavailable"}:
        assert checked["characters"][0]["final_energy"] == rotation["characters"][0]["initial_energy"]


@pytest.mark.parametrize("limited", ["admission", "automatic_energy"])
def test_all_ledgers_reserve_before_energy_and_cooldown(monkeypatch, limited):
    template, rotation, _ = admission_fixture()
    if limited == "admission":
        monkeypatch.setattr(admission, "MAX_ACTION_ADMISSION_RECEIPTS", 0)
        reason = "action_admission_receipt_limit"
    else:
        from genshin_engine import automatic_energy

        monkeypatch.setattr(automatic_energy, "MAX_AUTOMATIC_ENERGY_RECEIPTS", 0)
        reason = "automatic_energy_receipt_limit"
    checked = check_rotation(template, rotation)
    assert checked["status"] == "pending"
    assert checked["characters"][0]["final_energy"] == 80
    assert checked["cycles"][0]["boundary_state"]["cooldown_ready_in"] == {
        '["a","skill:fixture:q"]': []
    }
    parent = _managed_rows(checked)[0]
    assert parent["action_admission"]["status"] == "not_committed"
    assert parent["action_admission"]["reason"] == reason
    summary = checked["action_admission_execution"]
    assert summary["receipt_count"] == summary["committed_actions"] == 0
    assert summary["not_committed_actions"] == 1
    assert summary["rejected_actions"] == 0
    # The dependent automatic-energy ledger was reserved but never committed.
    assert checked["automatic_energy_delivery"]["receipt_count"] == 0


def test_second_budget_failure_preserves_first_committed_prefix(monkeypatch):
    template, rotation, _ = admission_fixture()
    rotation["period"] = template["loop_duration"] = 30
    second = copy.deepcopy(rotation["actions"][0])
    second.update(id="cast-second", start=21, occupation_end=21.1, order=2)
    third = copy.deepcopy(rotation["actions"][0])
    third.update(id="cast-third", start=22, occupation_end=22.1, order=3)
    rotation["actions"].extend([second, third])
    rotation["events"].append(
        {
            "id": "refund",
            "type": "energy_direct",
            "t": 20,
            "order": 1,
            "repeat": {"mode": "once"},
            "amounts": {"a": 80},
            **EVIDENCE,
        }
    )
    monkeypatch.setattr(admission, "MAX_ACTION_ADMISSION_RECEIPTS", 1)
    checked = check_rotation(template, rotation)
    summary = checked["action_admission_execution"]
    assert checked["status"] == "pending"
    assert summary["committed_actions"] == summary["receipt_count"] == 1
    assert summary["not_committed_actions"] == summary["not_verified_actions"] == 1
    assert summary["rejected_actions"] == 0
    assert [row["action_id"] for row in summary["receipts"]] == ["cast"]
    assert [
        row["action_admission"]["status"] for row in _managed_rows(checked)
    ] == ["committed", "not_committed", "not_verified"]
    assert checked["characters"][0]["final_energy"] == 80


def test_observer_receives_detached_reference_and_pending_keeps_commit(monkeypatch):
    template, rotation, mechanics = admission_fixture()
    original = copy.deepcopy(rotation)
    seen = []
    original_phase = _Mechanics.phase

    def capture(self, event, phase, parent, **kwargs):
        if event.get("event") == "action_start" and phase == "pre":
            seen.append(copy.deepcopy(event["action_admission_certificate"]))
        return original_phase(self, event, phase, parent, **kwargs)

    monkeypatch.setattr(_Mechanics, "phase", capture)
    output = execute_mechanics(template, rotation, mechanics)
    parent = _managed_rows(output["rotation_check"])[0]
    assert seen == [parent["action_admission"]["certificate"]]
    assert rotation == original

    def stop(self, event, phase, parent, **kwargs):
        if event.get("event") == "action_start" and phase == "pre":
            assert event["action_admission_certificate"]["certificate_id"].startswith(
                admission.PREFIX
            )
            raise ElementalPending("fixture_pending", "证书提交后的观察者中止", event["id"])
        return original_phase(self, event, phase, parent, **kwargs)

    monkeypatch.setattr(_Mechanics, "phase", stop)
    output = execute_mechanics(template, rotation, mechanics)
    checked = output["rotation_check"]
    assert output["status"] == checked["status"] == "pending"
    parent = _managed_rows(checked)[0]
    assert parent["status"] == "pending"
    assert parent["action_admission"]["status"] == "committed"
    assert checked["action_admission_execution"]["committed_actions"] == 1
    assert checked["action_admission_execution"]["receipt_count"] == 1


def test_unmarked_legacy_result_has_no_new_runtime_output():
    template, rotation, _ = fixture((0.2,))
    checked = check_rotation(template, rotation)
    assert "action_admission_execution" not in checked
    assert all("action_admission" not in row for row in checked["event_log"])
