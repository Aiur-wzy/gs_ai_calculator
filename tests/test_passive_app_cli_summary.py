"""CLI summaries expose adopted scope without copying large evidence receipts."""
from __future__ import annotations

import copy
import json

from genshin_app.cli import _calculation_output_summary
from genshin_app.equipment.service import plan_capabilities


def test_passive_calculation_summary_is_bounded_and_keeps_assumption_limits():
    calculated = {"report": {"state": "ready"}, "result_receipt": {"result_fingerprint": "frozen"},
        "result": {"summary": {"total_damage": 1, "dps": .1}, "hits": [],
            "passive_compilations": [{"status": "ready", "rules": [{"raw": "do-not-echo"*10000}, {}],
                "source": {"原文": "hidden"*10000}, "scope": {"provider_parent_events": [{}, {}, {}]}}]}}
    before = copy.deepcopy(calculated)
    result = _calculation_output_summary(calculated)
    summary = result["passive_sources"]
    assert summary["source_count"] == 1 and summary["rule_count"] == 2 and summary["qualified_parent_count"] == 3
    assert summary["whole_source_status"] == "partial"
    assert summary["gameplay_interpretation"] == "declared_not_verified"
    assert summary["stat_basis"] == "fixed_panel_nonextra_em_equals_total_em"
    assert summary["detail_pointer"] == "/result/passive_compilations"
    assert "default_assistance" not in result
    serialized = json.dumps(result, ensure_ascii=False)
    assert len(serialized.encode("utf8")) < 1800 and "do-not-echo" not in serialized and "hidden" not in serialized
    assert calculated == before


def test_empty_sources_do_not_change_legacy_summary_and_caps_publish_app_limits():
    calculated = {"report": {"state": "ready"}, "result_receipt": {},
        "result": {"summary": {"total_damage": 1, "dps": .1}, "hits": []}}
    before = _calculation_output_summary(calculated)
    calculated["result"]["passive_compilations"] = []
    assert _calculation_output_summary(calculated) == before
    caps = plan_capabilities()["passive_source_contract"]
    assert caps["max_sources"] == 32 and caps["max_receipt_bytes"] == 8*1024*1024
    assert caps["confirmable_issue_codes"] == ["passive_confirmation_required"]
