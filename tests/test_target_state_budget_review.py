"""Independent small-threshold oracles for the EC08 ledger/work bounds."""
from __future__ import annotations

import copy
import json

import pytest
from test_target_state_review import aura, direct, inputs

from genshin_engine import mechanics


def encoded(rows):
    return json.dumps(rows, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf8")


def stopped(out, code):
    assert out["status"] == "pending"
    assert out["result"] is None and out["resolved_template"] is None
    assert [r for r in out["mechanics_trace"] if r["type"] == "target_condition"] == out["target_condition_trace"]
    return next(issue for issue in out["issues"] if issue["code"] == code)


def test_review_count_boundary_counts_false_and_true_conditions_without_partial_result(monkeypatch):
    data = inputs(("physical", "physical"), initial=aura())
    untouched = copy.deepcopy(data)
    monkeypatch.setattr(mechanics, "MAX_TARGET_CONDITION_CHECKS", 4)
    out = mechanics.execute_mechanics(*data)
    assert direct(out) == pytest.approx({"h0": 54, "h1": 54})
    assert [row["automatic_value"] for row in out["target_condition_trace"]] == [True, False, True, False]
    monkeypatch.setattr(mechanics, "MAX_TARGET_CONDITION_CHECKS", 3)
    limited = mechanics.execute_mechanics(*data)
    issue = stopped(limited, "target_condition_limit")
    assert issue["checks_completed"] == issue["limit"] == 3
    assert limited["target_condition_trace"] == out["target_condition_trace"][:3]
    assert data == untouched


def test_review_work_budget_is_shared_across_later_check_windows(monkeypatch):
    data = inputs(("physical",), (.2,), initial=aura(remaining=3.2, decay=1e-9))
    data[1]["actions"][0]["repeat"] = {"mode": "each_cycle"}
    monkeypatch.setattr(mechanics, "MAX_TARGET_CONDITION_CHECKS", 3)
    out = mechanics.execute_mechanics(*data)
    stopped(out, "target_condition_limit")
    rows = out["target_condition_trace"]
    assert [row["cycle"] for row in rows] == [0, 0, 1]
    assert len({row["run_id"] for row in rows}) == 2


def test_review_utf8_ledger_exact_boundary_includes_every_comma_and_bracket(monkeypatch):
    data = inputs(("physical", "physical"), initial=aura())
    data[2]["fixed_effect_sources"][0]["source"] = "独立依据：冰元素\n制表\t以及 emoji ❄️"
    rows = mechanics.execute_mechanics(*data)["target_condition_trace"]
    exact = len(encoded(rows))
    monkeypatch.setattr(mechanics, "MAX_TARGET_CONDITION_TRACE_BYTES", exact)
    full = mechanics.execute_mechanics(*data)
    assert full["result"] is not None and full["target_condition_trace"] == rows
    monkeypatch.setattr(mechanics, "MAX_TARGET_CONDITION_TRACE_BYTES", exact - 1)
    out = mechanics.execute_mechanics(*data)
    issue = stopped(out, "target_condition_trace_limit")
    assert out["target_condition_trace"] == rows[:-1]
    assert issue["bytes_used"] == len(encoded(rows[:-1]))
    assert issue["attempted_bytes"] == exact and issue["checks_attempted"] == 4


def test_review_long_first_source_is_blocked_without_ledger_or_verbose_diagnostic(monkeypatch):
    data = inputs(("physical",), initial=aura())
    long_source = "独立长来源不能放进错误回执" * 1024
    data[2]["fixed_effect_sources"][0]["source"] = long_source
    monkeypatch.setattr(mechanics, "MAX_TARGET_CONDITION_TRACE_BYTES", 512)
    out = mechanics.execute_mechanics(*data)
    issue = stopped(out, "target_condition_trace_limit")
    assert out["target_condition_trace"] == []
    assert issue["bytes_used"] == len(b"[]") and issue["checks_attempted"] == 1
    assert issue["attempted_bytes"] > len(long_source.encode("utf8"))
    diagnostic = encoded(out["issues"])
    assert len(diagnostic) < 4096 and long_source.encode("utf8") not in diagnostic


def test_review_count_exhaustion_precedes_an_oversized_next_source(monkeypatch):
    data = inputs(("physical",), initial=aura())
    first = data[2]["fixed_effect_sources"][0]
    first["effects"] = first["effects"][:1]
    baseline = mechanics.execute_mechanics(*data)
    second = copy.deepcopy(first)
    second.update(id="second-fixed", source_id="second-source", source="第二个巨大来源" * 1024)
    data[2]["fixed_effect_sources"].append(second)
    monkeypatch.setattr(mechanics, "MAX_TARGET_CONDITION_CHECKS", 1)
    monkeypatch.setattr(mechanics, "MAX_TARGET_CONDITION_TRACE_BYTES", len(encoded(baseline["target_condition_trace"])))
    out = mechanics.execute_mechanics(*data)
    stopped(out, "target_condition_limit")
    assert out["target_condition_trace"] == baseline["target_condition_trace"]
    assert not any(issue["code"] == "target_condition_trace_limit" for issue in out["issues"])
