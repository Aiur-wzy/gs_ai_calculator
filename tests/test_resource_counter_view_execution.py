"""Live counter views on actual hits, with independent numerical expectations."""
from __future__ import annotations

import copy

import pytest

from genshin_engine.mechanics import execute_mechanics
from genshin_engine.shared_pools import SharedPoolError
from tests.test_mechanics_execution import add, damages, rule
from tests.test_resource_counter_execution import case


def view_case(times=(.15, .25, .35), *, benefit="benefit.dmg_bonus.all", factor=1):
    args = case(times=times)
    args[2]["resource_counter_views"] = {"schema_version": 1, "views": [{
        "id": "read", "name_zh": "当前实际消费读数", "owner": "a", "source_id": "view-benefit",
        "counter_id": "count", "key": {"kind": "total"}, "transform": {"kind": "scaled"},
        "status": "assumed", "source": "工程合成读数，没有角色事实授权"}]}
    add(args[2], rule("view-benefit", benefit=benefit, domain="provider", value={
        "kind": "counter_view", "view_id": "read", "sample": "current", "factor": factor}))
    return args


def reads(out):
    return [r for r in out["resource_counter_views"]["trace"] if r["cycle"] == 0]


@pytest.mark.parametrize("benefit,factor", [("benefit.dmg_bonus.all", 1), ("benefit.base_add.all", 100)])
def test_current_real_counter_values_drive_classic_damage(benefit, factor):
    args = view_case(benefit=benefit, factor=factor)
    before = copy.deepcopy(args)
    out = execute_mechanics(*args)
    assert out["result"] is not None, out["issues"]
    assert damages(out) == pytest.approx([81, 117, 130.5])
    assert [r["reading"]["raw_atoms"] for r in reads(out)] == [8, 16, 19]
    assert all(r["reading"]["sampling_authority"] == "mechanics_committed_ledger" for r in reads(out))
    assert args == before


def test_cap_changes_view_without_truncating_actual_accounting():
    args = view_case()
    args[2]["resource_counter_views"]["views"][0]["transform"]["max_atoms"] = 10
    out = execute_mechanics(*args)
    assert out["result"] is not None, out["issues"]
    assert damages(out) == pytest.approx([81, 90, 90])
    assert [r["reading"]["numerator"] for r in reads(out)] == [8, 10, 10]
    assert out["resource_counters"]["state"]["values"]["count"]["total"] == 19


def test_pre_read_post_order_uses_current_committed_cursor():
    args = view_case(times=(.2, .3))
    model = args[2]
    model["state_runtime"]["timers"] = []
    op = model["shared_pools"]["operations"][0]
    op["trigger"] = {"event": "hit", "phase": "pre", "event_ids": ["h0"]}
    later = copy.deepcopy(op)
    later.update(id="later", order=2)
    later["trigger"]["phase"] = "post"
    model["shared_pools"]["operations"].append(later)
    model["resource_counters"]["counters"][0]["operation_ids"].append("later")
    out = execute_mechanics(*args)
    assert out["result"] is not None, out["issues"]
    assert damages(out) == pytest.approx([81, 117])
    assert [r["reading"]["counter_cursor"]["ledger_sequence"] for r in reads(out)] == [1, 2]


@pytest.mark.parametrize("mutation", [
    lambda m: m.pop("resource_counters"),
    lambda m: m.pop("resource_counter_views"),
    lambda m: m["rules"][0]["effects"][0].update(domain="self"),
    lambda m: m["rules"][0]["effects"][0].update(domain="enemy"),
    lambda m: m["rules"][0]["effects"][0].update(benefit_type_id="benefit.em.all"),
    lambda m: m["rules"][0]["effects"][0].update(benefit_type_id="benefit.energy_recharge.all"),
    lambda m: m["rules"][0]["effects"][0]["value"].update(sample="trigger"),
    lambda m: m["rules"][0]["effects"][0]["value"].update(sample="event", event_id="cast"),
    lambda m: m["rules"][0]["effects"][0]["value"].update(view_id="absent"),
    lambda m: m["rules"][0].update(owner="other"),
    lambda m: m["resource_counter_views"]["views"][0].update(owner="other"),
])
def test_unsupported_or_missing_view_contract_is_rejected(mutation):
    args = view_case()
    mutation(args[2])
    out = execute_mechanics(*args)
    assert out["status"] == "invalid" and out["result"] is None


def test_read_failure_keeps_previously_completed_reads_and_resource_accounting(monkeypatch):
    from genshin_engine.mechanics import _Mechanics
    original = _Mechanics._reserve_events
    def reserve(ctx, count, kind=None):
        if kind == "resource_counter_view" and ctx.time == 250_000:
            raise SharedPoolError("counter_view_limit", "工程读预算失败", severity="pending")
        return original(ctx, count, kind)
    monkeypatch.setattr(_Mechanics, "_reserve_events", reserve)
    out = execute_mechanics(*view_case())
    assert out["status"] == "pending" and out["result"] is None and out["resolved_template"] is None
    assert [r["reading"]["raw_atoms"] for r in reads(out)] == [8]
    assert out["resource_counters"]["applied_state"]["values"]["count"]["total"] == 16
    assert any(r["id"] == "h0" for r in out["rotation_check"]["event_log"])


def test_overflow_before_cap_is_pending_and_does_not_publish_read():
    args = view_case(factor=1e308)
    args[2]["rules"][0]["effects"][0]["value"]["max"] = 1
    out = execute_mechanics(*args)
    assert out["status"] == "pending" and out["result"] is None
    assert [r["reading"]["raw_atoms"] for r in reads(out)] == [8, 16]
    assert any(i["code"] == "counter_view_value" for i in out["issues"])


def test_no_view_model_keeps_previous_shape():
    out = execute_mechanics(*case())
    assert out["result"] is not None
    assert "resource_counter_views" not in out and "resource_counter_views" not in out["result"]


def test_normal_swirl_rejects_view_without_reading_or_charging_budget():
    from tests.test_mechanics_swirl import setup
    args = view_case(times=(.15,))
    elemental = setup(times=(.15,))
    args[2]["elemental_model"] = elemental[2]["elemental_model"]
    args[2]["hit_events"][0]["application"] = elemental[2]["hit_events"][0]["application"]
    args[0]["hits"][0]["element"] = "anemo"
    out = execute_mechanics(*args)
    assert out["result"] is not None, out["issues"]
    assert damages(out) == pytest.approx([81, 781.299])
    assert len(reads(out)) == 1
    assert any(r.get("reason") == "unsupported_counter_damage_scope" for r in out["mechanics_trace"])


def test_provider_view_coexists_with_em_conversion_and_old_self_snapshot():
    from tests.test_attribute_graph_runtime import classify, conversion, enable_graph
    args = view_case(times=(.15, .25, .35))
    enable_graph(*args)
    add(args[2], conversion())
    classify(args[2], "share", "b")
    for descriptor in args[2]["hit_events"]:
        descriptor["self_snapshot_event"] = "cast"
    out = execute_mechanics(*args)
    assert out["result"] is not None, out["issues"]
    assert damages(out) == pytest.approx([81, 117, 130.5])
    assert [r["reading"]["raw_atoms"] for r in reads(out)] == [8, 16, 19]
    assert out["attribute_ledger"]["contract"] == "attribute-ledger-v2"


def test_excluded_or_expired_rule_does_not_read_nonzero_counter():
    args = view_case()
    args[2]["rules"][0]["timing"]["duration"] = .3
    matches = [{"rule_id": "view-benefit", "effect_id": "value", "hit_id": "h1", "operation": "exclude"}]
    out = execute_mechanics(*args, matches=matches)
    assert damages(out) == pytest.approx([81, 45, 45])
    assert len(reads(out)) == 1


def test_gate_insufficient_never_reads_even_for_explicit_include():
    from tests.test_shared_pools import config
    args = view_case(times=(.15, .25))
    consumer = config()["consumers"][0]
    consumer.update(eligibility_rule_id="view-benefit", source_id="view-benefit", actor_relation="party_member", amount=12)
    args[2]["shared_pools"]["consumers"] = [consumer]
    args[2]["rules"][0]["resource_gate"] = {"consumer_id": consumer["id"]}
    out = execute_mechanics(*args, matches=[{"rule_id": "view-benefit", "effect_id": "value", "hit_id": "h0", "operation": "include"}])
    assert out["result"] is not None, out["issues"]
    assert damages(out) == [45, 45]
    assert reads(out) == []


def test_failed_read_preserves_current_hit_gate_debit(monkeypatch):
    from genshin_engine.mechanics import _Mechanics
    from tests.test_shared_pools import config
    args = view_case(times=(.15,))
    consumer = config()["consumers"][0]
    consumer.update(eligibility_rule_id="view-benefit", source_id="view-benefit", actor_relation="party_member", amount=1)
    args[2]["shared_pools"]["consumers"] = [consumer]
    args[2]["rules"][0]["resource_gate"] = {"consumer_id": consumer["id"]}
    original = _Mechanics._reserve_events
    def reserve(ctx, count, kind=None):
        if kind == "resource_counter_view":
            raise SharedPoolError("counter_view_limit", "工程限制", severity="pending")
        return original(ctx, count, kind)
    monkeypatch.setattr(_Mechanics, "_reserve_events", reserve)
    out = execute_mechanics(*args)
    assert out["status"] == "pending" and out["result"] is None
    assert reads(out) == []
    assert [(r["accepted"], r["after"]) for r in out["shared_pool_trace"] if r["type"] == "consume"] == [(1, 10)]


def test_repeated_read_does_not_consume_again_and_scaled_value_keeps_fraction():
    args = view_case(times=(.15,))
    op = args[2]["shared_pools"]["operations"][0]
    op["amount"] = 399
    args[2]["shared_pools"]["pools"][0]["initial"] = {"team": 399}
    args[2]["shared_pools"]["pools"][0]["capacity"] = 500
    args[2]["rules"][0]["effects"].append({**copy.deepcopy(args[2]["rules"][0]["effects"][0]), "id": "again"})
    out = execute_mechanics(*args)
    assert out["result"] is not None, out["issues"]
    assert damages(out) == pytest.approx([45*(1+39.9*2)])
    assert len(reads(out)) == 2
    assert reads(out)[0]["reading"]["counter_cursor"] == reads(out)[1]["reading"]["counter_cursor"]


def test_trace_byte_budget_fails_before_shared_work_or_publication(monkeypatch):
    from genshin_engine import resource_counter_view_runtime as runtime
    from genshin_engine.mechanics import _Mechanics
    monkeypatch.setitem(runtime.READ_LIMITS, "trace_bytes", 2)
    original, calls = _Mechanics._reserve_events, []
    def reserve(ctx, count, kind=None):
        if kind == "resource_counter_view": calls.append(count)
        return original(ctx, count, kind)
    monkeypatch.setattr(_Mechanics, "_reserve_events", reserve)
    out = execute_mechanics(*view_case())
    assert out["status"] == "pending" and out["result"] is None
    assert reads(out) == [] and calls == []


def test_explicit_include_cannot_expand_scope_to_manual_transformative_hit():
    args = view_case(times=(.15,))
    args[0]["hits"][0].update(damage_system="transformative", reaction={"type": "swirl"})
    out = execute_mechanics(*args, matches=[{"rule_id": "view-benefit", "effect_id": "value", "hit_id": "h0", "operation": "include"}])
    assert out["status"] == "invalid" and out["result"] is None
    assert any(i["code"] == "counter_view_damage_scope" for i in out["issues"])


def test_fixed_source_does_not_accept_counter_expression():
    from genshin_engine.target_state import TARGET_STATE_PROFILE
    args = view_case()
    args[2]["fixed_effect_sources"] = [{"id": "fixed", "name_zh": "固定来源", "owner": "a", "source_id": "fixed",
        "availability": "fixed", "damage_scope": "classic_hits_only", "target_state_profile": TARGET_STATE_PROFILE,
        "effects": [{"id": "value", "benefit_type_id": "benefit.crit_rate.all", "domain": "self",
                     "value": copy.deepcopy(args[2]["rules"][0]["effects"][0]["value"]),
                     "filter": {"event": "hit", "phase": "pre"},
                     "target_predicate": {"kind": "has_aura", "element": "cryo"}}], "status": "assumed", "source": "工程反例"}]
    out = execute_mechanics(*args)
    assert out["status"] == "invalid" and out["result"] is None


def test_read_count_limit_retains_only_completed_receipts(monkeypatch):
    from genshin_engine import resource_counter_view_runtime as runtime
    monkeypatch.setitem(runtime.READ_LIMITS, "reads", 1)
    out = execute_mechanics(*view_case())
    assert out["status"] == "pending" and out["result"] is None
    assert [r["reading"]["raw_atoms"] for r in reads(out)] == [8]


def test_current_read_audit_identity_never_enters_rotation_boundary():
    args = view_case()
    args[0]["loop_duration"] = args[1]["period"] = .4
    out = execute_mechanics(*args)
    assert out["result"] is not None, out["issues"]
    boundary = out["rotation_check"]["cycles"][0]["boundary_state"]["mechanics"]
    assert "counter-view:" not in repr(boundary)
    assert "counter_cursor" not in repr(boundary)


@pytest.mark.parametrize("transform,expected", [({"kind": "steps", "step_atoms": 5, "max_steps": 3}, [90, 180, 180]),
                                               ({"kind": "scaled"}, [81, 117, 130.5])])
def test_explicit_steps_and_scaled_contracts_remain_distinct(transform, expected):
    args = view_case()
    args[2]["resource_counter_views"]["views"][0]["transform"] = transform
    out = execute_mechanics(*args)
    assert out["result"] is not None, out["issues"]
    assert damages(out) == pytest.approx(expected)
