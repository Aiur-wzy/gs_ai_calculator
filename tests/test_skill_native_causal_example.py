"""Run the maintained Skill example, not another test-only copy of its input."""
from __future__ import annotations

import copy
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

from genshin_app.calculation_completion import calculation_completion
from genshin_app.calculation_qualification import qualification_for_claim
from genshin_app.calculation_review import review_calculation
from genshin_app.equipment import (
    compose_equipment_plan,
    plan_capabilities,
    run_plan_calc,
)
from genshin_app.equipment.calculation_wire import result_only_bytes

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skill"
EXAMPLE = SKILL / "references/app-mechanics/native-causal-example.md"
NAV = SKILL / "references/app-mechanics/authoring-intents.json"


def blocks(language):
    return re.findall(rf"```{language}\n(.*?)\n```", EXAMPLE.read_text(encoding="utf-8"), re.DOTALL)


def draft():
    return json.loads(blocks("json")[0])


def reviewed(plan, tmp_path):
    report = compose_equipment_plan(plan, tmp_path / "absent.db")
    assert report["state"] == "pending"
    assert {r["code"] for r in report["missing_inputs"]} == {"effect_pending"}
    (tmp_path / "causal-composed.json").write_text(
        json.dumps(report["plan"]), encoding="utf-8")
    (tmp_path / "compose-response.json").write_text(
        json.dumps({"code": 0, "data": report}), encoding="utf-8")
    script = tmp_path / "confirm-teaching.py"
    script.write_text(blocks("python")[0], encoding="utf-8")
    run = subprocess.run([sys.executable, "-X", "utf8", str(script)], cwd=tmp_path,
                         capture_output=True, text=True, encoding="utf-8", check=False)
    assert run.returncode == 0, (run.stdout, run.stderr)
    return json.loads((tmp_path / "causal-reviewed.json").read_text(encoding="utf-8"))


@pytest.fixture
def calculated(tmp_path):
    return run_plan_calc(reviewed(draft(), tmp_path), tmp_path / "absent.db")


def test_document_example_public_validation_and_exact_independent_arithmetic(tmp_path, calculated):
    path = tmp_path / "draft.json"
    path.write_text(json.dumps(draft()), encoding="utf-8")
    validated = subprocess.run(
        [sys.executable, "-m", "genshin_app", "equipment", "validate", "--plan", str(path)],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8", check=False)
    assert validated.returncode == 0, (validated.stdout, validated.stderr)
    result = calculated["result"]
    assert [h["id"] for h in result["hits"]] == [
        "skill.ticks:1", "skill.ticks:2", "skill.ticks:3", "burst.hit"]
    assert [h["expected"] for h in result["hits"]] == pytest.approx([67.5, 90, 45, 90])
    assert result["summary"]["total_damage"] == pytest.approx(292.5)
    assert result["summary"]["dps"] == pytest.approx(146.25)
    applied = [r for r in result["mechanics_trace"] if r["type"] == "elemental_application"]
    assert [r["application_allowed"] for r in applied] == [True, False, False, False]
    assert applied[0]["reaction"]["type"] == "melt"
    assert all(not r.get("reaction_occurred") for r in applied[1:])
    assert calculation_completion(calculated)["task_admission"] == "accepted"
    accepted = qualification_for_claim(calculated, energy_intent="single_window_legal")
    assert accepted["status"] == "accepted"
    assert accepted["character_semantics"] == "agent_declared"
    assert calculation_completion(calculated)["steady_state"] == "unproven"
    assert not qualification_for_claim(calculated, energy_intent="sustainable_loop")["can_accept"]
    assert not (tmp_path / "absent.db").exists()


@pytest.mark.parametrize("change,expected_damage", [("empty_aura", [45, 90, 45, 90]),
                                                   ("expiry", [67.5, 45, 45, 90])])
def test_causal_counterexamples_change_only_actual_outcome(tmp_path, change, expected_damage):
    plan = draft()
    if change == "empty_aura":
        plan["mechanics_model"]["elemental_model"]["target"]["initial_auras"] = []
    else:
        plan["mechanics_model"]["rules"][0]["timing"]["duration"] = .2
    output = run_plan_calc(reviewed(plan, tmp_path), tmp_path / "absent.db")
    assert [h["expected"] for h in output["result"]["hits"]] == pytest.approx(expected_damage)
    assert calculation_completion(output)["task_admission"] == "accepted"


@pytest.mark.parametrize("change", ["late_arrival", "no_income"])
def test_missing_or_late_energy_cannot_be_called_complete(tmp_path, change):
    plan = draft()
    resource = plan["mechanics_model"]["rules"][0]["resources"][0]
    if change == "late_arrival":
        resource["travel_time"] = 1
    else:
        plan["mechanics_model"]["rules"][0]["resources"] = []
        plan["mechanics_model"]["resource_ownership"] = []
    output = run_plan_calc(reviewed(plan, tmp_path), tmp_path / "absent.db")
    assert calculation_completion(output)["task_admission"] != "accepted"
    assert not qualification_for_claim(output, energy_intent="single_window_legal")["can_accept"]
    assert "burst.hit" not in [h["id"] for h in output["result"]["hits"]]
    events = output["result"]["rotation_check"]["event_log"]
    failed = [r for r in events if r.get("id") == "burst.cast"]
    assert len(failed) == 1 and failed[0]["status"] == "failed"
    assert (failed[0]["before"], failed[0]["required"], failed[0]["deficit"]) == (0, 6, 6)


def _pointer(value, pointer):
    for token in pointer.strip("/").split("/") if pointer else []:
        token = token.replace("~1", "/").replace("~0", "~")
        value = value[int(token)] if isinstance(value, list) else value[token]
    return value


def test_navigation_points_to_current_author_schemas_and_real_result_queries(calculated):
    navigation = json.loads(NAV.read_text(encoding="utf-8"))
    caps = plan_capabilities()
    saved = json.loads(result_only_bytes(calculated["result"], calculated["result_receipt"],
                                        calculated["equipment_summary"]))
    assert len(navigation["intents"]) == len({r["id"] for r in navigation["intents"]})
    for row in navigation["intents"]:
        assert row["schema"]["resource"] == "capabilities"
        assert _pointer(caps, row["schema"]["pointer"])
        assert (SKILL / row["guide"]["resource"]).is_file()
        assert (SKILL / row["example"]["resource"]).resolve() == EXAMPLE.resolve()
        assert _pointer(draft(), row["fragment"]["pointer"])
        # Reuse the result proof rather than accepting a request-shaped fiction.
        review = review_calculation(saved, copy.deepcopy(row["observe"]["request"]))
        assert review["view"] == row["observe"]["request"]["view"]
    for raw in blocks("json")[1:]:
        query = json.loads(raw)
        review = review_calculation(saved, query)
        assert review["view"] == query["view"]


def test_generated_navigation_matches_single_source(tmp_path):
    from scripts.update_skill_resource_map import authoring_navigation

    path = NAV.with_name("authoring-guide.md")
    copied = tmp_path / NAV.relative_to(ROOT)
    copied.parent.mkdir(parents=True)
    copied.write_bytes(NAV.read_bytes())
    authoring_navigation(tmp_path)
    assert copied.with_name(path.name).read_bytes() == path.read_bytes()


def test_document_authoring_and_utf8_subprocess_example_really_runs(tmp_path):
    guide = EXAMPLE.with_name("authoring-assistance.md").read_text(encoding="utf-8")
    scripts = re.findall(r"```python\n(.*?)\n```", guide, re.DOTALL)
    assert len(scripts) == 2
    (tmp_path / "causal-draft.json").write_text(json.dumps(draft()), encoding="utf-8")
    for index, source in enumerate(scripts):
        script = tmp_path / f"author-{index}.py"
        script.write_text(source, encoding="utf-8")
        run = subprocess.run([sys.executable, "-X", "utf8", str(script)], cwd=tmp_path,
                             capture_output=True, text=True, encoding="utf-8", errors="strict", check=False)
        assert run.returncode == 0, (run.stdout, run.stderr)
    assert "尚未计算" in run.stdout
    plan = json.loads((tmp_path / "author-adopted.json").read_text(encoding="utf-8"))
    assert plan["revision"] == draft()["revision"] + 1
    out = run_plan_calc(reviewed(plan, tmp_path), tmp_path / "absent.db")
    assert out["result"]["summary"]["total_damage"] == pytest.approx(292.5)


def test_document_subprocess_example_does_not_treat_error_envelope_as_success(tmp_path):
    guide = EXAMPLE.with_name("authoring-assistance.md").read_text(encoding="utf-8")
    source = re.findall(r"```python\n(.*?)\n```", guide, re.DOTALL)[1]
    definition = source.split("\nresponse = call_app(", 1)[0]
    script = tmp_path / "bad-request.py"
    script.write_text(definition + '\ncall_app(["equipment", "materialization"])\n', encoding="utf-8")
    run = subprocess.run([sys.executable, "-X", "utf8", str(script)], cwd=tmp_path,
                         capture_output=True, text=True, encoding="utf-8", errors="strict", check=False)
    assert run.returncode != 0
    assert "命令未成功" in run.stderr and "materialization_plan_required" in run.stderr
