"""Echo audit members outside DPS cutoff retain one full execution proof."""
from __future__ import annotations

import copy

import pytest
from test_special_damage_echo import _fixture_equipment_projection, app_plan, authorize

from genshin_app.calculation_integrity import validate_result_binding
from genshin_app.equipment import run_plan_calc
from genshin_app.equipment.files import fingerprint
from genshin_engine.damage_report_window import CONTRACT


def test_cutoff_echo_reuses_window_context_and_rejects_audit_semantic_tamper(tmp_path, monkeypatch):
    _fixture_equipment_projection(monkeypatch)
    plan, db = app_plan(), tmp_path / 'absent.db'
    plan['rotation_model']['damage_report'] = {'contract': CONTRACT, 'mode': 'explicit', 'end': .3}
    fresh = run_plan_calc(authorize(plan, db), db)
    result, receipt = fresh['result'], fresh['result_receipt']
    assert 'special_damage_echo_execution' not in result
    assert result['damage_report_execution']['mechanics_model']['special_damage_echoes']
    assert len([h for h in result['damage_report_audit_hits'] if ':extra:' in h['id']]) == 2
    validate_result_binding(result, receipt)
    bad = copy.deepcopy(result)
    bad['damage_report_audit_hits'][-1]['skill'] = 'fake owner attribution'
    changed = {**receipt, 'result_fingerprint': fingerprint(bad)}
    with pytest.raises(ValueError):
        validate_result_binding(bad, changed)
