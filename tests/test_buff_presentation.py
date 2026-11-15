"""Explicit sampling labels change presentation, not execution or saved proofs."""
from __future__ import annotations

import copy
import io
import uuid
import xml.etree.ElementTree as ET
import zipfile

import pytest

from genshin_app.equipment.delivery import validate_delivery_calculated
from genshin_app.equipment.files import fingerprint
from genshin_app.equipment.schema import PlanError
from genshin_app.equipment.service import result_bytes, run_plan_calc
from genshin_app.export import coverage_rows
from genshin_app.presentation import buff_presentation
from tests.test_plan_mechanics import native, review

PURPOSE = {"contract": "buff-presentation-v1", "purpose": "calculation_sampling",
           "reason": "本轴逐击条件的计算采样，非游戏效果寿命"}


@pytest.mark.parametrize("value", [None, {}, True, [], {"hidden": True},
    {**PURPOSE, "contract": "future"}, {**PURPOSE, "purpose": "hidden"},
    {**PURPOSE, "reason": ""}, {**PURPOSE, "reason": " \n "}, {**PURPOSE, "reason": 3}])
def test_unknown_or_invalid_presentation_never_hides_a_window(value):
    assert buff_presentation({"extensions": {"presentation": value}}) is None
    assert buff_presentation({"id": "calculation_sampling", "start": 0, "end": .000001}) is None


def plan():
    value = native()
    value["manual_template"]["buffs"] = [{
        "id": "conditional", "name": "有依据的条件暴击", "start": 0, "end": 1,
        "scope": "team", "extensions": {"owner": "a", "source_id": "independent:conditional"},
        "effects": [{"id": "cr", "benefit_type_id": "benefit.crit_rate.all", "zone": "crit_rate",
                     "stat": "all", "value": .1, "applies_to": {"mode": "all"}}]}]
    return value


def calculate(value, db):
    review(value, db)
    return run_plan_calc(value, db, capture_display=True)


def test_same_numeric_events_and_complete_states_survive_save_and_fresh_validation(tmp_path):
    ordinary, sampling = plan(), plan()
    sampling["manual_template"]["buffs"][0]["extensions"]["presentation"] = copy.deepcopy(PURPOSE)
    before = copy.deepcopy(sampling)
    db = tmp_path / "absent.db"
    plain, marked = calculate(ordinary, db), calculate(sampling, db)
    assert sampling["manual_template"] == before["manual_template"]
    assert marked["plan"]["manual_template"]["buffs"][0]["extensions"]["presentation"] == PURPOSE
    assert plain["result"]["summary"] == marked["result"]["summary"]
    assert [(h["id"], h["t"], h["expected"], h["zones"]) for h in plain["result"]["hits"]] == [
        (h["id"], h["t"], h["expected"], h["zones"]) for h in marked["result"]["hits"]]
    assert plain["result"]["mechanics_trace"] == marked["result"]["mechanics_trace"]
    states = marked["result"]["buff_state_timeline"]["states"]
    original_states = plain["result"]["buff_state_timeline"]["states"]
    assert len(states) == len(original_states)
    assert any(s.get("buff", {}).get("extensions", {}).get("presentation") == PURPOSE for s in states)
    validate_delivery_calculated(marked)
    assert not db.exists()
    # A caller cannot rewrite the sealed observation to invent a display purpose.
    tampered = copy.deepcopy(marked)
    state = next(s for s in tampered["result"]["buff_state_timeline"]["states"] if s.get("buff_id") == "conditional")
    state["buff"]["extensions"]["presentation"]["reason"] = "改写已封口记录"
    tampered["result_receipt"]["result_fingerprint"] = fingerprint(tampered["result"])
    with pytest.raises(PlanError):
        validate_delivery_calculated(tampered)


def test_coverage_export_appends_purpose_without_replacing_numeric_columns_or_losing_rows(tmp_path):
    ordinary, sampling = plan(), plan()
    sampling["manual_template"]["buffs"][0]["extensions"]["presentation"] = PURPOSE
    db = tmp_path / "absent.db"
    old, new = calculate(ordinary, db), calculate(sampling, db)
    a, b = coverage_rows(old["result"]), coverage_rows(new["result"])
    assert len(a) == len(b)
    old_title = ["持续 Buff 状态（已保存回执；不代表每击必定受益）"]
    sampling_title = ["增益状态与计算采样（已保存回执；不代表游戏Buff寿命）"]
    def original_columns(rows):
        values = []
        for row in rows:
            value = row[:9]
            if value == sampling_title:
                value = old_title
            elif len(value) >= 9 and value[0] == "有依据的条件暴击" and value[5] == "计算采样编码":
                value[5] = "规则有效"
            values.append(value)
        return values
    assert original_columns(a) == original_columns(b)
    assert old_title in a and sampling_title in b and old_title not in b
    assert all(row[5] == "计算采样编码" for row in b if len(row) >= 9 and row[0] == "有依据的条件暴击")
    assert any(row[-2:] == ["展示用途", "用途说明"] for row in b)
    assert any(row[-2:] == ["计算采样窗口（非游戏Buff寿命）", PURPOSE["reason"]] for row in b)
    assert not any("展示用途" in row for row in a)
    # Actual OOXML coverage sheet retains every original numeric/text column.
    def coverage_sheet(calculated):
        content = result_bytes(calculated["result"], calculated["result_receipt"], "xlsx")
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            sheet = ET.fromstring(archive.read("xl/worksheets/sheet3.xml"))
        namespace = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
        return [["".join(cell.itertext()) for cell in row] for row in sheet.findall("s:sheetData/s:row", namespace)]
    original_sheet, marked_sheet = coverage_sheet(old), coverage_sheet(new)
    assert original_columns(original_sheet) == original_columns(marked_sheet)
    assert any(PURPOSE["reason"] in row for row in marked_sheet)


def test_app_and_native_read_publish_the_same_display_only_contract(tmp_path):
    from genshin_app.agent_chat.artifact_candidate import ArtifactCandidateSession
    from genshin_app.equipment.service import plan_capabilities
    from genshin_app.presentation import buff_presentation_capabilities

    contract = buff_presentation_capabilities()
    assert plan_capabilities()["mechanics_contract"]["buff_state_timeline"]["presentation"] == contract
    session = ArtifactCandidateSession(str(uuid.uuid4()), tmp_path / "session", tmp_path / "absent.db", "native", plan())
    response = session.call_tool("gs_read_context", {"resource": "mechanics_capabilities",
        "pointer": "/buff_state_timeline/presentation", "limit": 50})
    assert response["code"] == 0, response
    assert {row["key"]: row["value"] for row in response["data"]["items"]} == contract
    assert not (tmp_path / "absent.db").exists()
