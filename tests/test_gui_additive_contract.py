from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from genshin_app.equipment.schema import PlanError
from genshin_app.gui.api import GuiApi
from tests.gui_api_contract import reviewed_expected_contract

ROOT = Path(__file__).resolve().parents[1]


def contracts():
    return tuple(json.loads((ROOT / "tests/fixtures" / name).read_text(encoding="utf-8")) for name in (
        "gui-api-public-contract-v1.json", "gui-api-public-contract-r9-additive.json"))


def test_typed_save_failure_keeps_human_operation_reason_and_details(tmp_path, monkeypatch):
    details = {"expected_sha256": "a" * 64, "actual_sha256": "b" * 64}

    def fail(*_args, **_kwargs):
        raise PlanError("文件已被其它写入更新", "file_conflict", details)

    monkeypatch.setattr("genshin_app.equipment.files.publish_files", fail)
    template = json.loads((ROOT / "examples/freeze_team.json").read_text(encoding="utf-8"))
    target = tmp_path / "copy.json"
    result = GuiApi(tmp_path / "data", release_pair_coordinator=False).save_template(str(target), template)
    assert result["code"] == 1 and "保存模板失败" in result["message"]
    assert str(target) in result["message"]
    assert result["reason"] == "file_conflict" and result["details"] == details
    assert not target.exists()


@pytest.mark.parametrize("change", ["baseline", "before", "required", "return_type", "rename_existing", "duplicate_add"])
def test_additive_review_rejects_undeclared_or_breaking_changes(change):
    baseline, delta = contracts()
    delta = copy.deepcopy(delta)
    item = delta["extended_methods"]["save_template"]
    if change == "baseline":
        delta["baseline_semantic_sha256"] = "0" * 64
    elif change == "before":
        item["before"]["arguments"] = "self, renamed: str, template: dict"
    elif change == "required":
        item["after"]["arguments"] = "self, path: str, template: dict, expected_file_sha"
    elif change == "return_type":
        item["after"]["returns"] = "str"
    elif change == "rename_existing":
        item["after"]["arguments"] = "self, renamed: str, template: dict, expected_file_sha=None"
    else:
        delta["added_methods"]["ping"] = baseline["methods"]["ping"]
    with pytest.raises(ValueError):
        reviewed_expected_contract(baseline, delta)


def test_reviewed_delta_preserves_all_old_methods_and_adds_only_declared_extensions():
    baseline, delta = contracts()
    expected = reviewed_expected_contract(baseline, delta)
    assert set(expected) - set(baseline["methods"]) == {"equipment_reference_panel"}
    assert set(baseline["methods"]) <= set(expected)
    assert [name for name in baseline["methods"] if baseline["methods"][name] != expected[name]] == ["save_template"]
