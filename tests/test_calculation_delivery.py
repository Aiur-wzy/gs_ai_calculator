"""Single fresh calculation -> legal native, full package and both offline workbooks."""
from __future__ import annotations

import copy
import hashlib
import io
import json
import zipfile
from pathlib import Path

import pytest

from genshin_app import calculation_delivery_files as publication
from genshin_app.cli import main
from genshin_app.equipment import delivery as delivery_module
from genshin_app.equipment import new_plan, service
from genshin_app.equipment.delivery import (
    PACKAGE_SCHEMA,
    build_calculation_delivery,
    deliver_plan_file,
    validate_delivery_calculated,
)
from genshin_app.equipment.files import fingerprint, input_view, json_bytes, read_plan
from genshin_app.equipment.schema import PlanError, validate_plan


@pytest.fixture(autouse=True)
def _runtime_database_already_admitted(monkeypatch):
    """Keep delivery tests focused on delivery after the startup gate."""
    monkeypatch.setattr(
        "genshin_app.cli._require_runtime_database",
        lambda _data_dir: {"can_start": True, "state": "test_ready"},
    )


def delivery_plan():
    plan = new_plan()
    plan["plan_id"] = "5b67c67f-8364-41de-8011-e975e96fbe1f"
    plan["manual_template"].update(
        loop_duration=2,
        characters=[{"id": "a", "name": "工程角色", "level": 90,
                     "stats": {"atk": 100, "crit_rate": 0, "crit_dmg": .5}}],
        hits=[{"id": "h", "t": 1, "character": "a", "skill": "手工工程命中",
               "multiplier": 1, "damage_type_ids": ["damage.elemental_skill"],
               "damage_system": "classic", "element": "cryo"}])
    return plan


@pytest.fixture
def calculated(tmp_path):
    return service.run_plan_calc(delivery_plan(), tmp_path / "absent.db", capture_display=True)


def test_builder_native_package_and_two_real_workbooks(calculated, monkeypatch):
    original = copy.deepcopy(calculated)
    monkeypatch.setattr("sqlite3.connect", lambda *a, **k: pytest.fail("offline builder read DB"))
    monkeypatch.setattr(service, "run_plan_calc", lambda *a, **k: pytest.fail("builder recalculated"))
    built = build_calculation_delivery(calculated, input_file_sha="a" * 64)
    assert calculated == original
    native = json.loads(built["artifacts"]["native_plan"])
    validate_plan(native)
    assert native["resolution"] == {"state": "pending"} and native["resolved_template"] is None
    assert fingerprint(input_view(native)) == calculated["result_receipt"]["input_fingerprint"]
    package = json.loads(built["artifacts"]["calculated"])
    assert package["package_schema"] == PACKAGE_SCHEMA and package["calculated"] == original
    assert package["integrity"]["calculated_fingerprint"] == fingerprint(original)
    assert built["summary"]["total_damage"] == pytest.approx(45)
    for role in ("result_workbook", "formula_workbook"):
        with zipfile.ZipFile(io.BytesIO(built["artifacts"][role])) as book:
            assert "xl/worksheets/sheet1.xml" in book.namelist()
            assert "工程角色" in "".join(book.read(name).decode("utf8") for name in book.namelist() if name.endswith(".xml"))


def test_app_only_delivery_never_builds_unrequested_excel(calculated, monkeypatch):
    monkeypatch.setattr(service, "result_bytes", lambda *a, **k: pytest.fail("unrequested workbook"))
    monkeypatch.setattr("genshin_app.formula_workbook.build_formula_workbook",
                        lambda *a, **k: pytest.fail("unrequested formula workbook"))
    built = build_calculation_delivery(calculated, input_file_sha="a" * 64,
                                       roles=["native_plan", "calculated"])
    assert set(built["artifacts"]) == {"native_plan", "calculated"}
    validate_plan(json.loads(built["artifacts"]["native_plan"]))
    assert json.loads(built["artifacts"]["calculated"])["calculated"] == calculated
    assert built["metadata"]["formula_support"]["status"] == "not_requested"


@pytest.mark.parametrize("roles", [[], ["wrong"], ["native_plan", "native_plan"], "native_plan"])
def test_delivery_rejects_invalid_product_selection_before_calculation(tmp_path, monkeypatch, roles):
    monkeypatch.setattr(service, "run_plan_calc", lambda *a, **k: pytest.fail("invalid selection calculated"))
    with pytest.raises(PlanError, match="有效交付产物"):
        deliver_plan_file(tmp_path / "missing.json", tmp_path / "missing.db", tmp_path / "out", roles=roles)


def test_legacy_plan_without_dispatch_does_not_publish_materialization_receipt(calculated):
    assert "action_materialization_receipt" not in calculated["report"]
    assert "action_materialization_receipt" not in calculated["result"]
    assert "action_materialization_receipt" not in calculated["result_receipt"]
    encoded = service.result_bytes(
        calculated["result"], calculated["result_receipt"], "json",
        display=calculated.get("display_context"),
        equipment_summary=calculated.get("equipment_summary"),
    )
    assert json.loads(encoded)["result"]["summary"]["total_damage"] == pytest.approx(45)
    built = build_calculation_delivery(calculated, input_file_sha="b" * 64)
    assert "calculated" in built["artifacts"]


def test_delivery_metadata_keeps_declared_reaction_verification_visible():
    calculated = {
        "result": {
            'summary': {'total_damage': 0, 'dps': 0},
            "rotation_check": {
                "status": "passed",
                "damage_interpretation": {
                    "kind": "planned_damage", "complete_first_cycle": True,
                    "reported_hit_count": 3,
                    "reaction_verification": {
                        "status": "unverified", "mode": "declared_not_simulated",
                        "declared_reaction_hit_count": 2, "omitted_hit_count": 0,
                        "hit_ids": ["h1", "h2"],
                    },
                },
            },
            "mechanics_status": "passed",
            "mechanics": {"complete_first_cycle": True},
        },
    }
    state = delivery_module._state(calculated)
    assert state["calculation_status"] == "complete"
    assert state["damage_interpretation"]["reaction_verification"] == {
        "status": "unverified", "mode": "declared_not_simulated",
        "declared_reaction_hit_count": 2, "omitted_hit_count": 0,
    }


@pytest.mark.parametrize(("status", "calculation_class", "expected"), [
    ("incomplete", "diagnostic", "diagnostic"),
    # Unknown means that no reviewed character-dispatch manifest exists.  In
    # staged mode character semantics remain Agent-declared, so this is an
    # informational state rather than evidence of a partially expanded action.
    ("unknown", "diagnostic", "complete"),
])
def test_delivery_classifies_unresolved_action_materialization(
        status, calculation_class, expected):
    calculated = {
        "result": {
            'summary': {'total_damage': 0, 'dps': 0},
            "rotation_check": {"status": "passed", "damage_interpretation": {
                "kind": "planned_damage", "complete_first_cycle": True,
            }},
            "mechanics_status": "passed",
            "mechanics": {"execution_status": "passed", "complete_first_cycle": True},
            "action_materialization_receipt": {
                "status": status,
                "calculation_class": calculation_class,
                "summary": {"complete_actions": 0, "incomplete_actions": 1,
                            "unknown_actions": 0},
            },
        },
    }
    state = delivery_module._state(calculated)
    assert state["calculation_status"] == expected
    assert state["action_materialization"]["status"] == status


@pytest.mark.parametrize("target", ["input_fingerprint", "output_fingerprint", "source_fingerprint", "result_fingerprint",
                                    "calculated_at", "engine_version", "template_version", "data_versions", "assumptions"])
def test_generic_receipt_fields_are_required_even_for_manual_plan(calculated, target):
    calculated["result_receipt"].pop(target)
    with pytest.raises(ValueError):
        validate_delivery_calculated(calculated)


@pytest.mark.parametrize(("field", "value"), [("revision", False), ("template_version", 2.0)])
def test_receipt_numeric_equality_does_not_replace_json_types(calculated, field, value):
    calculated["result_receipt"][field] = value
    with pytest.raises(PlanError):
        validate_delivery_calculated(calculated)


@pytest.mark.parametrize("target", ["report", "equipment_summary", "presentation_issues", "display_context"])
def test_old_result_only_and_incomplete_packages_are_rejected(calculated, target):
    calculated.pop(target)
    with pytest.raises(ValueError):
        validate_delivery_calculated(calculated)


def test_big_cache_does_not_inherit_native_eight_mib_cap(calculated):
    calculated["plan"]["resolution"]["cache_probe"] = "x" * (8 * 1024 * 1024)
    assert calculated["report"]["plan"] is calculated["plan"]
    with pytest.raises(ValueError):
        validate_plan(calculated["plan"])
    checked = validate_delivery_calculated(calculated)
    assert len(checked["calculated_bytes"]) > 8 * 1024 * 1024
    validate_plan(checked["native"])


def test_export_mutation_and_small_artifact_budget_fail_before_publish(calculated, monkeypatch):
    monkeypatch.setitem(publication.LIMITS, "workbook_bytes", 10)
    with pytest.raises(PlanError, match="预算"):
        build_calculation_delivery(calculated, input_file_sha="a" * 64)
    monkeypatch.setitem(publication.LIMITS, "workbook_bytes", 32 * 1024 * 1024)
    original = service.result_bytes

    def mutate(*args, **kwargs):
        output = original(*args, **kwargs)
        calculated["presentation_issues"].append({"note": "unexpected exporter mutation"})
        return output

    monkeypatch.setattr(service, "result_bytes", mutate)
    with pytest.raises(PlanError):
        build_calculation_delivery(calculated, input_file_sha="a" * 64)


def test_fresh_delivery_once_and_readable_native(tmp_path, monkeypatch):
    path, target = tmp_path / "input.plan.json", tmp_path / "delivery"
    path.write_bytes(json_bytes(delivery_plan()))
    calls = []
    original = service.run_plan_calc
    original_build = build_calculation_delivery
    build_calls = []

    def run(*args, **kwargs):
        calls.append(kwargs)
        return original(*args, **kwargs)

    def build(*args, **kwargs):
        build_calls.append(kwargs)
        return original_build(*args, **kwargs)

    monkeypatch.setattr(service, "run_plan_calc", run)
    monkeypatch.setattr("genshin_app.equipment.delivery.build_calculation_delivery", build)
    out = deliver_plan_file(path, tmp_path / "absent.db", target)
    assert len(calls) == len(build_calls) == 1
    assert calls[0]["capture_display"] is True
    assert calls[0]["_session"] is build_calls[0]["_session"]
    assert out["diagnostic"] is False and out["data"]["index_committed"] is True
    generation = Path(out["data"]["generation_path"])
    assert {p.name for p in generation.iterdir() if not p.name.endswith(".plan.lock")} == {
        "native.plan.json", "calculated.json", "result.xlsx", "formula.xlsx", "manifest.json"}
    native = read_plan(generation / "native.plan.json")["plan"]
    assert native["resolved_template"] is None
    assert not (tmp_path / "absent.db").exists()


def test_existing_foreign_index_is_rejected_before_fresh_even_with_overwrite(tmp_path, monkeypatch):
    path, target = tmp_path / "input.json", tmp_path / "delivery"
    path.write_bytes(json_bytes(delivery_plan()))
    target.mkdir()
    (target / "index.json").write_text('{"user":"note"}', encoding="utf8")
    monkeypatch.setattr(service, "run_plan_calc", lambda *a, **k: pytest.fail("preflight read DB"))
    with pytest.raises(PlanError) as caught:
        deliver_plan_file(path, tmp_path / "absent.db", target, overwrite=True)
    assert caught.value.details["index_committed"] is False
    assert (target / "index.json").read_text("utf8") == '{"user":"note"}'


def test_source_pending_error_is_bounded_and_not_published(tmp_path, monkeypatch):
    path = tmp_path / "input.json"
    path.write_bytes(json_bytes(delivery_plan()))

    def pending(*a, **k):
        raise PlanError("private large message " + "x" * 100000, "missing_input",
                        {"source": "x" * 100000, "missing_inputs": [{"id": "unconfirmed"}]})

    monkeypatch.setattr(service, "run_plan_calc", pending)
    with pytest.raises(PlanError) as caught:
        deliver_plan_file(path, tmp_path / "absent.db", tmp_path / "delivery")
    assert len(json_bytes(caught.value.details)) < 512
    assert caught.value.details["missing_input_count"] == 1
    assert not (tmp_path / "delivery").exists()


def test_cli_delivers_bounded_data_and_old_calc_stays_result_only(tmp_path, capsys):
    path = tmp_path / "input.json"
    path.write_bytes(json_bytes(delivery_plan()))
    assert main(["--data-dir", str(tmp_path), "equipment", "deliver", "--plan", str(path),
                 "--output-dir", str(tmp_path / "delivery")]) == 0
    printed = capsys.readouterr()
    data = json.loads(printed.out)["data"]
    assert data["index_committed"] is True and "calculated" not in data
    assert len(printed.out.encode("utf8")) <= publication.LIMITS["summary_bytes"]
    assert main(["--data-dir", str(tmp_path), "equipment", "calc", "--plan", str(path),
                 "--output", str(tmp_path / "old-result.json"), "--summary"]) == 0
    capsys.readouterr()
    old = json.loads((tmp_path / "old-result.json").read_text("utf8"))
    assert "result" in old and "plan" not in old and "report" not in old and "package_schema" not in old


def test_cli_diagnostic_keeps_committed_files(tmp_path, calculated, monkeypatch, capsys):
    path = tmp_path / "input.json"
    path.write_bytes(json_bytes(delivery_plan()))
    calculated["result"]["rotation_check"].update(status="failed")
    calculated["result_receipt"]["result_fingerprint"] = fingerprint(calculated["result"])
    monkeypatch.setattr(service, "run_plan_calc", lambda *a, **k: calculated)
    assert main(["--data-dir", str(tmp_path), "equipment", "deliver", "--plan", str(path),
                 "--output-dir", str(tmp_path / "delivery")]) == 1
    printed = capsys.readouterr()
    error = json.loads(printed.err)
    assert error["reason"] == "calculation_diagnostic" and error["details"]["index_committed"] is True
    assert Path(error["details"]["index_path"]).is_file()


def test_caps_have_a_separate_delivery_contract():
    caps = service.plan_capabilities()["calculation_delivery"]
    assert caps["package_schema"] == PACKAGE_SCHEMA and caps["fresh_calculations"] == 1
    assert caps["limits"]["calculated_bytes"] == 96 * 1024 * 1024
    assert caps["limits"]["total_bytes"] == 192 * 1024 * 1024


def test_package_is_canonical_without_serializing_calculated_twice(calculated):
    built = build_calculation_delivery(calculated, input_file_sha="b" * 64)
    encoded = built["artifacts"]["calculated"]
    assert encoded == json_bytes(json.loads(encoded))
    assert hashlib.sha256(json_bytes(calculated)).hexdigest() == built["metadata"]["calculated_fingerprint"]
