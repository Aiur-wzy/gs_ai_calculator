import copy

import pytest

from genshin_app.equipment.periodic_lifecycle import (
    DERIVED_COUNT_POLICY,
    RESOURCE_EVENT_CONTRACT,
    PeriodicLifecycleError,
    project_periodic_lifecycle,
)


def lifecycle(refresh="none", pause="none", off_field="continue", duration=10):
    return {"duration_ticks": duration, "refresh_policy": refresh,
            "pause_resume_policy": pause, "off_field_policy": off_field}


def establish(identity="cast", at=0, order=0):
    return {"trigger_id": identity, "root_action_id": identity,
            "time_ticks": at, "order": order, "status": "confirmed"}


def channel(kind="equal_interval", *, deliveries=None, **schedule):
    defaults = {"kind": kind}
    defaults.update(schedule)
    return {"channel_id": "storm", "schedule": defaults,
            "deliveries": ([{"kind": "damage"}, {"kind": "application"}]
                           if deliveries is None else deliveries)}


def control(identity, operation, at, order=0, **extra):
    return {"control_id": identity, "operation": operation, "time_ticks": at,
            "order": order, "status": "confirmed", **extra}


def project(channels, life=None, establishments=None, controls=(), intervals=(), window=100):
    return project_periodic_lifecycle(channels=channels, lifecycle=life or lifecycle(),
        establishments=establishments or [establish()], controls=controls,
        on_field_intervals=intervals, window_end_ticks=window)


def test_fixed_equal_interval_and_explicit_offsets_share_one_bounded_projection():
    rows = [
        {"channel_id": "fixed", "schedule": {"kind": "fixed", "offset_ticks": 2},
         "deliveries": [{"kind": "damage"}]},
        {"channel_id": "equal", "schedule": {"kind": "equal_interval",
            "first_offset_ticks": 1, "interval_ticks": 3, "count": 3},
         "deliveries": [{"kind": "application"}]},
        {"channel_id": "unequal", "schedule": {"kind": "explicit_offsets",
            "offset_ticks": [0, 4, 9], "orders": [1, 1, 0]},
         "deliveries": [{"kind": "damage"}, {"kind": "application"}]},
    ]
    receipt = project(rows)
    assert [(row["channel_id"], row["time_ticks"]) for row in receipt["occurrences"]] == [
        ("unequal", 0), ("equal", 1), ("fixed", 2), ("equal", 4),
        ("unequal", 4), ("equal", 7), ("unequal", 9)]
    assert receipt["summary"] == {"generations": 1, "materialized_occurrences": 7,
        "cancelled_occurrences": 0, "deferred_intervals": 0, "skipped_controls": 0}


def test_derived_count_requires_exact_inclusive_lifecycle_closure_and_is_bounded():
    derived = channel(first_offset_ticks=1, interval_ticks=3, count=None,
                      derive_count=DERIVED_COUNT_POLICY)
    assert [row["time_ticks"] for row in project([derived], life=lifecycle(duration=10))["occurrences"]] == [1, 4, 7, 10]
    with pytest.raises(PeriodicLifecycleError, match="close exactly"):
        project([derived], life=lifecycle(duration=9))
    too_many = channel(first_offset_ticks=0, interval_ticks=1, count=None,
                       derive_count=DERIVED_COUNT_POLICY, order=1)
    with pytest.raises(PeriodicLifecycleError, match="outside 1..256"):
        project([too_many], life=lifecycle(duration=300), window=400)


def test_replace_cancels_old_generation_at_boundary_and_restarts_all_channels_atomically():
    periodic = channel(first_offset_ticks=1, interval_ticks=2, count=3)
    receipt = project([periodic], life=lifecycle(refresh="replace_previous", duration=5),
        controls=[control("refresh", "replace", 4, 0)])
    assert [(row["generation"], row["time_ticks"]) for row in receipt["occurrences"]] == [
        (1, 1), (1, 3), (2, 5), (2, 7), (2, 9)]
    assert [(row["generation"], row["reason"]) for row in receipt["cancelled"]] == [
        (1, "replace_control")]
    assert [row["status"] for row in receipt["generations"]] == ["replaced", "complete"]


@pytest.mark.parametrize(("policy", "operation", "extra", "expected"), [
    ("reset_duration", "reset_duration", {}, [1, 3, 5, 7, 9]),
    ("extend_duration", "extend_duration", {"duration_ticks": 2}, [1, 3, 5, 7]),
])
def test_reset_and_extend_are_explicit_axis_controls_not_policy_side_effects(
        policy, operation, extra, expected):
    periodic = channel(first_offset_ticks=1, interval_ticks=2, count=None,
        derive_count=DERIVED_COUNT_POLICY)
    duration = 5
    # The derived schedule must close over the maximum explicit active horizon.
    reset_at = 4
    receipt = project([periodic], life=lifecycle(refresh=policy, duration=duration),
        controls=[control("refresh", operation, reset_at, **extra)])
    assert [row["time_ticks"] for row in receipt["occurrences"]] == expected
    without_control = project([channel(first_offset_ticks=1, interval_ticks=2, count=3)],
                              life=lifecycle(refresh=policy, duration=duration))
    assert [row["time_ticks"] for row in without_control["occurrences"]] == [1, 3, 5]


def test_pause_preserves_phase_while_reset_policy_restarts_pending_schedule():
    periodic = channel(first_offset_ticks=1, interval_ticks=2, count=3)
    controls = [control("pause", "pause", 2), control("resume", "resume", 5)]
    preserved = project([periodic], life=lifecycle(pause="pause_preserve_phase", duration=5),
                        controls=controls)
    assert [row["time_ticks"] for row in preserved["occurrences"]] == [1, 6, 8]
    assert preserved["deferred"] == [{"generation": 1, "reason": "pause_preserve_phase",
        "duration_ticks": 3, "resume_position": {"time_ticks": 5, "order": 0}}]
    reset = project([periodic], life=lifecycle(pause="pause_reset_phase", duration=5),
                    controls=controls)
    assert [(row["phase"], row["time_ticks"]) for row in reset["occurrences"]] == [
        (1, 1), (2, 6), (2, 8)]
    assert [row["reason"] for row in reset["cancelled"]] == [
        "pause_reset_phase", "pause_reset_phase", "lifecycle_complete"]


def test_preserved_same_active_tick_must_follow_resume_order():
    periodic = channel("fixed", offset_ticks=2, order=1)
    controls = [control("pause", "pause", 2, order=0),
                control("resume", "resume", 5, order=1)]
    with pytest.raises(PeriodicLifecycleError, match="ordered after resume"):
        project([periodic], life=lifecycle(pause="pause_preserve_phase"),
                controls=controls)
    controls[1]["order"] = 0
    receipt = project([periodic], life=lifecycle(pause="pause_preserve_phase"),
                      controls=controls)
    assert [(row["time_ticks"], row["order"]) for row in receipt["occurrences"]] == [(5, 1)]


def test_overlapping_establishment_reset_expands_derived_candidate_horizon():
    periodic = channel(first_offset_ticks=1, interval_ticks=2, count=None,
        derive_count=DERIVED_COUNT_POLICY)
    receipt = project([periodic], life=lifecycle(refresh="reset_duration", duration=5),
        establishments=[establish("first", 0), establish("second", 4)])
    assert [row["time_ticks"] for row in receipt["occurrences"]] == [1, 3, 5, 7, 9]
    assert receipt["controls"][1]["outcome"] == "reset_active_duration"


def test_ignore_new_records_the_overlap_without_replacing_the_active_generation():
    periodic = channel(first_offset_ticks=1, interval_ticks=2, count=3)
    receipt = project(
        [periodic], life=lifecycle(refresh="ignore_new", duration=5),
        establishments=[establish("first", 0), establish("second", 2)],
    )
    assert [(row["generation"], row["time_ticks"])
            for row in receipt["occurrences"]] == [(1, 1), (1, 3), (1, 5)]
    assert len(receipt["generations"]) == 1
    assert receipt["controls"][1]["outcome"] == "ignored_active"
    assert receipt["skipped"] == [{
        "trigger_id": "second", "operation": "establish",
        "position": {"time_ticks": 2, "order": 0},
        "outcome": "ignored_active", "reason": "ignore_new_policy",
    }]


def test_explicit_cancel_owns_its_boundary_and_cancels_pending_occurrences():
    periodic = channel(first_offset_ticks=1, interval_ticks=2, count=3)
    receipt = project(
        [periodic], life=lifecycle(duration=5),
        controls=[control("cancel", "cancel", 3, order=0)],
    )
    assert [row["time_ticks"] for row in receipt["occurrences"]] == [1]
    assert [(row["time_ticks"], row["reason"])
            for row in receipt["cancelled"]] == [
        (3, "cancel_control"), (3, "cancel_control")]
    assert receipt["generations"][0]["status"] == "cancelled"
    assert receipt["controls"][1]["outcome"] == "cancelled_active"


def test_explicit_replace_can_move_the_new_generation_to_its_own_root_action():
    periodic = channel(first_offset_ticks=1, interval_ticks=2, count=3)
    replacement = control("replace", "replace", 2)
    replacement["root_action_id"] = "action.refresh"
    receipt = project(
        [periodic], life=lifecycle(refresh="replace_previous", duration=5),
        controls=[replacement],
    )
    assert [(row["generation"], row["root_action_id"], row["time_ticks"])
            for row in receipt["occurrences"]] == [
        (1, "cast", 1),
        (2, "action.refresh", 3),
        (2, "action.refresh", 5),
        (2, "action.refresh", 7),
    ]


def test_end_tick_earlier_order_extend_precedes_later_order_last_occurrence():
    periodic = channel(first_offset_ticks=1, interval_ticks=2, count=None,
        derive_count=DERIVED_COUNT_POLICY, orders=[0, 0, 10, 0])
    receipt = project([periodic], life=lifecycle(refresh="extend_duration", duration=5),
        controls=[control("extend", "extend_duration", 5, order=0, duration_ticks=2)])
    assert [(row["time_ticks"], row["order"]) for row in receipt["occurrences"]] == [
        (1, 0), (3, 0), (5, 10), (7, 0)]
    assert receipt["controls"][1]["outcome"] == "extended_active_duration"


def test_natural_lifecycle_end_accepts_a_real_last_occurrence_at_order_max():
    periodic = channel("fixed", offset_ticks=5, order=10**9)
    receipt = project([periodic], life=lifecycle(duration=5))
    assert [(row["time_ticks"], row["order"]) for row in receipt["occurrences"]] == [
        (5, 10**9)]


def test_derived_candidates_expand_per_actual_generation_not_global_axis_counts():
    periodic = channel(first_offset_ticks=1, interval_ticks=1, count=None,
        derive_count=DERIVED_COUNT_POLICY)
    establishments = [establish(f"cast.{index}", index * 5)
                      for index in range(100)]
    receipt = project([periodic], life=lifecycle(
        refresh="reset_duration", duration=3), establishments=establishments,
        window=500)
    assert receipt["summary"]["generations"] == 100
    assert receipt["summary"]["materialized_occurrences"] == 300


def test_inactive_late_controls_do_not_inflate_derived_candidate_horizon():
    periodic = channel(first_offset_ticks=1, interval_ticks=1, count=None,
        derive_count=DERIVED_COUNT_POLICY)
    controls = [control(f"late.{index}", "reset_duration", 10 + index)
                for index in range(300)]
    receipt = project([periodic], life=lifecycle(
        refresh="reset_duration", duration=3), controls=controls, window=400)
    assert [row["time_ticks"] for row in receipt["occurrences"]] == [1, 2, 3]
    assert receipt["summary"]["skipped_controls"] == 300


def test_zero_duration_legacy_instance_is_limited_to_ordered_zero_offset_contacts():
    fixed = {"channel_id": "storm",
             "schedule": {"kind": "fixed", "offset_ticks": 0, "order": 1},
             "deliveries": []}
    receipt = project([fixed], life=lifecycle(duration=0),
                      establishments=[establish(order=0)])
    assert [(row["time_ticks"], row["order"], row["deliveries"])
            for row in receipt["occurrences"]] == [(0, 1, [])]
    with pytest.raises(PeriodicLifecycleError, match="zero-duration"):
        project([channel(first_offset_ticks=0, interval_ticks=1, count=1)],
                life=lifecycle(duration=0))
    with pytest.raises(PeriodicLifecycleError, match="zero-duration"):
        project([fixed], life=lifecycle(refresh="extend_duration", duration=0))


def test_pause_without_resume_fails_closed_and_does_not_return_partial_receipt():
    with pytest.raises(PeriodicLifecycleError, match="without a confirmed resume"):
        project([channel(first_offset_ticks=1, interval_ticks=2, count=3)],
            life=lifecycle(pause="pause_preserve_phase", duration=5),
            controls=[control("pause", "pause", 2)])


def test_off_field_stop_uses_confirmed_frontline_intervals_and_continue_ignores_them():
    periodic = channel(first_offset_ticks=1, interval_ticks=2, count=5)
    intervals = [{"start_ticks": 0, "start_order": -10, "end_ticks": 4,
                  "end_order": 0, "source_event_id": "switch.away",
                  "status": "confirmed"}]
    stopped = project([periodic], life=lifecycle(off_field="stop", duration=9), intervals=intervals)
    assert [row["time_ticks"] for row in stopped["occurrences"]] == [1, 3]
    assert stopped["generations"][0]["status"] == "stopped_off_field"
    continued = project([periodic], life=lifecycle(off_field="continue", duration=9), intervals=intervals)
    assert [row["time_ticks"] for row in continued["occurrences"]] == [1, 3, 5, 7, 9]
    with pytest.raises(PeriodicLifecycleError, match="confirmed frontline"):
        project([periodic], life=lifecycle(off_field="stop", duration=9),
            intervals=[{"start_ticks": 0, "start_order": -10, "end_ticks": 4,
                        "end_order": 0, "source_event_id": "switch.away",
                        "status": "assumed"}])


def test_off_field_boundary_uses_confirmed_switch_order_not_timestamp_only():
    after_switch = channel("fixed", offset_ticks=4, order=1)
    interval = [{"start_ticks": 0, "start_order": -1, "end_ticks": 4,
                 "end_order": 0, "source_event_id": "switch.away", "status": "confirmed"}]
    stopped = project([after_switch], life=lifecycle(off_field="stop"), intervals=interval)
    assert stopped["occurrences"] == []
    assert stopped["cancelled"][0]["reason"] == "off_field_stop"
    before_switch = channel("fixed", offset_ticks=4, order=-1)
    delivered = project([before_switch], life=lifecycle(off_field="stop"), intervals=interval)
    assert [(row["time_ticks"], row["order"]) for row in delivered["occurrences"]] == [(4, -1)]


def test_frontline_start_cannot_collide_with_explicit_axis_position_and_ids_stay_bounded():
    interval = [{"start_ticks": 0, "start_order": 0, "end_ticks": 4,
                 "end_order": 0, "source_event_id": "switch." + "x" * 121,
                 "status": "confirmed"}]
    with pytest.raises(PeriodicLifecycleError, match="interval start"):
        project([channel("fixed", offset_ticks=1)],
                life=lifecycle(off_field="stop"), intervals=interval)
    interval[0]["start_order"] = -1
    receipt = project([channel("fixed", offset_ticks=1)],
        life=lifecycle(off_field="stop"), intervals=interval)
    stop = next(row for row in receipt["controls"]
                if row["operation"] == "off_field_stop")
    assert len(stop["trigger_id"]) <= 128


def test_same_tick_requires_distinct_order_and_zero_offset_must_follow_establishment():
    first = {"channel_id": "a", "schedule": {"kind": "fixed", "offset_ticks": 2},
             "deliveries": [{"kind": "damage"}]}
    second = copy.deepcopy(first)
    second["channel_id"] = "b"
    with pytest.raises(PeriodicLifecycleError, match="distinct order"):
        project([first, second])
    zero = channel("fixed", offset_ticks=0, order=0)
    with pytest.raises(PeriodicLifecycleError, match="ordered after"):
        project([zero], establishments=[establish(order=0)])
    colliding = channel("fixed", offset_ticks=2, order=0)
    with pytest.raises(PeriodicLifecycleError, match="cannot share"):
        project([colliding], life=lifecycle(pause="pause_preserve_phase"),
            controls=[control("pause", "pause", 2, order=0)])


def test_resource_delivery_requires_closed_reference_and_fails_without_app_consumer():
    deliveries = [{"kind": "resource", "contract": RESOURCE_EVENT_CONTRACT,
                   "event": {"type": "energy_spawn", "template_id": "particle.template"}}]
    with pytest.raises(PeriodicLifecycleError, match="no closed App consumer"):
        project([channel("fixed", offset_ticks=1, deliveries=deliveries)])
    invalid = copy.deepcopy(deliveries)
    invalid[0]["contract"] = "invented-resource-v1"
    with pytest.raises(PeriodicLifecycleError, match="supported closed event contract"):
        project([channel("fixed", offset_ticks=1, deliveries=invalid)])


def test_inactive_refresh_is_a_recorded_noop_and_controls_are_bounded_and_confirmed():
    receipt = project([channel("fixed", offset_ticks=1)],
        life=lifecycle(refresh="replace_previous", duration=2),
        establishments=[establish(at=10)], controls=[control("early", "replace", 1)], window=20)
    assert receipt["skipped"][0]["reason"] == "no_active_generation"
    bad = control("bad", "replace", 1)
    bad["status"] = "assumed"
    with pytest.raises(PeriodicLifecycleError, match="confirmed axis"):
        project([channel("fixed", offset_ticks=1)], life=lifecycle(refresh="replace_previous"),
                controls=[bad])


def test_extended_lifecycle_cannot_escape_the_axis_window():
    with pytest.raises(PeriodicLifecycleError, match="exceeds the projection window"):
        project([channel("fixed", offset_ticks=1)],
            life=lifecycle(refresh="extend_duration", duration=5),
            controls=[control("extend", "extend_duration", 4, duration_ticks=5)], window=8)


def test_axis_id_position_and_public_receipt_bytes_are_bounded(monkeypatch):
    bad_root = establish()
    bad_root["root_action_id"] = "x" * 129
    with pytest.raises(PeriodicLifecycleError, match="root_action_id"):
        project([channel("fixed", offset_ticks=1)], establishments=[bad_root])
    with pytest.raises(PeriodicLifecycleError, match="exceeds the projection window"):
        project([channel("fixed", offset_ticks=1)], establishments=[establish(at=101)])
    import genshin_app.equipment.periodic_lifecycle as module
    monkeypatch.setattr(module, "MAX_RECEIPT_BYTES", 100)
    with pytest.raises(PeriodicLifecycleError, match="2MiB delivery budget"):
        project([channel("fixed", offset_ticks=1)])
