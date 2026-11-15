"""Bounded production calculation probes; no game answers or test fixtures.

The full DSH/GUI projection retains its independent 594 oracle. These separate
synthetic inputs qualify error contracts, conversion, files and source clocks;
they never grant game-mechanic authority or manufacture a benchmark result.
"""
from __future__ import annotations

import contextlib
import copy
import hashlib
import io
import itertools
import json
import math
import sys
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

from genshin_app.frozen_selfcheck_inputs import EVIDENCE, SOURCE
from genshin_app.frozen_selfcheck_isolation import SelfcheckError

FLOW_ATTRIBUTE_LIMITS = {"frames": 2048}


def _require(condition, code, details=None):
    if not condition:
        raise SelfcheckError("selfcheck_calculation_" + code, details)


def _number(actual, expected):
    _require(type(actual) in (float, int) and math.isfinite(actual)
             and math.isclose(actual, expected, rel_tol=1e-12, abs_tol=1e-9),
             "numeric_mismatch", {"actual": actual, "expected": expected})


def _execution(root):
    loader = sys.modules[__name__].__loader__
    if root is None:
        return {"layer": "source_probe", "actual_frozen_tested": False}
    _require(getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"), "helper_not_frozen")
    _require(type(loader).__name__ == "PyiFrozenLoader"
             and type(loader).__module__.endswith("pyimod02_importers"), "helper_loader_not_frozen")
    _require(Path(sys.modules[__name__].__spec__.origin).resolve().is_relative_to(Path(root).resolve())
             and Path(sys._MEIPASS).resolve().is_relative_to(Path(root).resolve()), "helper_outside_package")
    return {"layer": "frozen_cli_execution", "actual_frozen_tested": True, "source_fallback": False}


def verify_flow_budget(calculated):
    """Inspect original App proof after DSH calls and again after fresh load."""
    from genshin_app.equipment.attribute_budget_binding import (
        validate_attribute_budget_binding,
    )
    binding = validate_attribute_budget_binding(calculated["result"], calculated["result_receipt"], calculated["plan"])
    _require(binding is not None and binding["policy"]["requested"] == FLOW_ATTRIBUTE_LIMITS,
             "flow_budget_not_consumed")
    _require(binding["model_contract"] == "attribute-ledger-v2", "flow_attribute_graph_missing")
    usage = calculated["result"]["attribute_ledger"]["usage"]
    _require(0 < usage["frames"] <= FLOW_ATTRIBUTE_LIMITS["frames"], "flow_frames_not_metered")
    return {"binding_fingerprint": binding["binding_fingerprint"], "model_contract": binding["model_contract"],
            "requested": binding["policy"]["requested"], "frames": usage["frames"]}


def verify_appendix_index_response(response, db_character_id):
    """Check the real read-only tool reply, not just its declared schema."""
    data = response.get("data") if isinstance(response, dict) else None
    _require(isinstance(response, dict) and response.get("code") == 0 and isinstance(data, dict), "appendix_navigation_failed")
    _require(data.get("contract_id") == "agent-character-appendix-index-v1"
             and data.get("db_character_id") == db_character_id
             and data.get("status") in {"complete", "partial"}
             and isinstance(data.get("database_fingerprint"), str)
             and len(data["database_fingerprint"]) == 64
             and data.get("execution_authority") == "none" and data.get("body_read") is False
             and isinstance(data.get("resource"), str) and bool(data["resource"]),
             "appendix_navigation_contract_mismatch")
    return {"contract": data["contract_id"], "status": "verified", "tool": "gs_get_character_appendix_index",
            "db_character_id": db_character_id, "index_status": data["status"],
            "database_fingerprint": data["database_fingerprint"], "body_read": False, "execution_authority": "none"}


def verify_flow_damage_explanations(calculated):
    """Inspect persisted evidence for the fixed flow, never solve damage again."""
    from genshin_app.damage_explanation_reporting import validate_damage_explanations
    result = calculated.get("result", {})
    document = result.get("damage_explanations")
    _require(isinstance(document, dict) and document.get("contract") == "damage-explanation-v1"
             and document.get("schema_version") == 1
             and result.get("damage_explanation_contract") == "damage-explanation-v1",
             "flow_damage_explanation_missing")
    try:
        status = validate_damage_explanations(result, calculated.get("result_receipt"), report=calculated.get("report"))
    except (ValueError, TypeError, KeyError) as error:
        raise SelfcheckError("selfcheck_calculation_flow_damage_explanation_inconsistent", str(error)) from error
    _require(status == "complete" and document.get("coverage", {}).get("omitted") == 0,
             "flow_damage_explanation_incomplete")
    hits = result.get("hits", [])
    _require(len(hits) == 5, "flow_damage_explanation_hit_count")
    references = [hit.get("explanation_ref") for hit in hits]
    _require(all(isinstance(ref, str) and ref in document["events"] for ref in references)
             and len(set(references)) == len(hits), "flow_damage_explanation_reference_open")
    # These are the explicitly supplied 100 ATK engineering panels. Checking
    # the consumed input does not recompute the 594 damage oracle or introduce
    # a second formula implementation.
    scaling = []
    for hit, reference in zip(hits, references, strict=True):
        event = document["events"][reference]
        panel = document["panels"].get(event.get("panel_ref"), {})
        formula = document["formulas"].get(event.get("formula_ref"), {})
        _require(hit.get("damage_system") == "classic" and formula.get("kind") == "classic"
                 and event.get("outcome", {}).get("origin") == "engine_formula",
                 "flow_damage_explanation_formula_mismatch")
        value = panel.get("inputs", {}).get("scaling")
        _number(value, 100)
        scaling.append(value)
    return {"contract": document["contract"], "schema_version": document["schema_version"], "status": "verified",
            "document_fingerprint": result["damage_explanation_fingerprint"], "hit_count": len(hits),
            "explained_hit_count": len(references), "all_hit_references_closed": True,
            "ordinary_scaling": scaling, "evidence_only_no_second_solver": True}


def _inputs(*, actors=("a",), times=(.3,)):
    stats = {"atk": 100, "base_atk": 50, "em": 100, "crit_rate": 0, "crit_dmg": 1.5, "energy_recharge": 1}
    template = {"version": 2, "form": "resolved", "id_registry_version": 1, "type_definitions": {"damage_types": []},
        "enemy": {"level": 90, "resistance": {"default": .1}}, "loop_duration": 10,
        "characters": [{"id": actor, "level": 90, "stats": {**stats, "em": 100*(i+1)}} for i, actor in enumerate(actors)],
        "hits": [{"id": f"hit.{i}", "t": time, "character": actors[0], "skill": "工程技能", "hit_name": "工程命中",
            "damage_type_ids": ["damage.elemental_skill"], "damage_system": "classic", "element": "cryo", "multiplier": 1}
            for i, time in enumerate(times)], "buffs": [], "switches": []}
    rotation = {"schema_version": 1, "period": 10, "max_cycles": 3, "initial_frontline": actors[0],
        "characters": [{"id": actor, "element": "cryo", "energy_recharge": 1,
            "energy_capacity": 100, "initial_energy": 0, **EVIDENCE} for actor in actors],
        "abilities": [{"id": "skill", "character": actors[0], "action_type_id": "action.elemental_skill",
            "cooldown": None, "energy": None, **EVIDENCE}],
        "actions": [{"id": "cast", "ability_id": "skill", "start": 0, "occupation_end": .1, "order": 0,
            "repeat": {"mode": "each_cycle"}, **EVIDENCE}], "events": [], "initial_cooldowns": []}
    model = {"schema_version": 1, "rules": [], "hit_events": [{"hit_id": hit["id"], "action_id": "cast", "order": 10+i}
        for i, hit in enumerate(template["hits"])], "hit_patterns": [], "condition_events": [], "initial_state": {"rules": []}}
    return template, rotation, model


def _rule(identity, benefit, value, *, owner="a"):
    return {"id": identity, "name_zh": "独立工程收益", "owner": owner, "source_id": identity,
        "trigger": {"event": "action_start", "phase": "pre", "action_ids": ["cast"]},
        "beneficiaries": {"mode": "characters", "characters": ["a"], "scope": "locked"},
        "timing": {"delay": 0, "duration": 30, "cooldown": 0, "cooldown_anchor": "success"},
        "stacking": {"max": 1, "add": 1, "policy": "refresh"},
        "effects": [{"id": "value", "benefit_type_id": benefit, "value": value, "domain": "provider"}], **EVIDENCE}


def _rules(model, *rules):
    model["rules"].extend(rules)
    model["initial_state"]["rules"].extend({"rule_id": row["id"], "cooldown_remaining": 0, "instances": []} for row in rules)


def _declaration_probes():
    from genshin_app.domain_errors import classify_domain_failure, tool_failure_guidance
    from genshin_engine.attribute_ledger import AttributeLedger, AttributeLedgerError
    from genshin_engine.mechanics_validation import validate_mechanics_model

    failures = []
    declaration = {"contract": "attribute-ledger-v1", "initials": [], "contributions": [], "frames": []}
    for missing in declaration:
        value = {key: item for key, item in declaration.items() if key != missing}
        try:
            AttributeLedger(value)
        except AttributeLedgerError as error:
            _require(error.issue["code"] == "attribute_schema", "declaration_untyped_failure", error.issue)
            failures.append(error.issue)
        else:
            raise SelfcheckError("selfcheck_calculation_missing_declaration_accepted", missing)
    _, _, model = _inputs()
    model["attribute_model"] = {"contract": "attribute-ledger-v3", "initial_basis": [
        {"actor": "a", "stat": "atk", "included_components": []}], "effect_classifications": [], "limits": {}}
    issues = validate_mechanics_model(model)["issues"]
    _require(len(issues) == 1 and issues[0]["path"].endswith("attribute_model.limits"), "union_wrong_branch", issues)
    model["attribute_model"].pop("limits")
    model["attribute_model"]["effect_classifications"] = [{"effect_ref": {"kind": "rule", "rule_id": "r", "effect_id": "e"},
        "component": {"provider": "a", "part_id": "p", "effect_id": "e"}, "nonextra_inclusion": "unknown", "status": "pending"}]
    incomplete = validate_mechanics_model(model)["issues"]
    _require(any(row["path"].endswith(".source") for row in incomplete), "missing_source_not_diagnosed", incomplete)
    ledger = AttributeLedger(declaration, limits={"frames": 1})
    frame = {"id": "first", "origin": 0, "run_id": "0:cast", "event_id": "cast", "phase": "pre",
        "state_epoch": 0, "t_us": 0, "contribution_ids": []}
    ledger.adopt_frame(frame)
    try:
        ledger.adopt_frame({**frame, "id": "second", "run_id": "0:second", "event_id": "second"})
    except AttributeLedgerError as error:
        capacity = error.issue
    else:
        raise SelfcheckError("selfcheck_calculation_budget_not_enforced")
    guidance = tool_failure_guidance("mechanics_pending", details={"issues": [capacity]})
    _require(ledger.usage()["frames"] == 1 and capacity["dimension"] == "frames" and capacity["attempted"] == 2,
             "capacity_partial_commit", capacity)
    _require(classify_domain_failure("mechanics_pending", details={"issues": [capacity]})["category"] == "engine_capability_missing"
             and guidance["recovery"]["action"] == "adjust_execution_budget", "capacity_misclassified", guidance)
    return {"missing_fields": failures, "precise_union_issue": issues[0], "classification_issues": incomplete,
            "capacity_issue": capacity, "capacity_guidance": guidance}


def _conversion_probe(root, *, model_contract="attribute-ledger-v2", capture_display=False):
    from genshin_app.equipment import compose_equipment_plan, new_plan, run_plan_calc
    from genshin_app.equipment.attribute_recovery import preview_attribute_recovery
    from genshin_app.equipment.attributes import prepare_attribute_review
    from genshin_app.equipment.files import fingerprint
    from genshin_app.equipment.mechanics import enable_mechanics
    from genshin_app.equipment.schema import PlanError

    template, rotation, model = _inputs(actors=("a", "b"))
    _rules(model, _rule("share", "benefit.em.all", {"kind": "stat", "subject": "owner", "stat": "em",
        "sample": "current", "factor": .2}, owner="b"),
        _rule("reader", "benefit.base_add.all", {"kind": "stat", "subject": "owner", "stat": "em", "sample": "current", "factor": 1}))
    plan = enable_mechanics(new_plan(template))
    plan.update(rotation_model=rotation, mechanics_model=model)
    plan["effect_bindings"] = []
    for row in model["rules"]:
        row["source_id"] = row["id"]+":part"
        plan["effect_bindings"].append({"binding_id": row["id"], "provider_character_id": row["owner"],
            "owner": {"kind": "manual", "note": SOURCE}, "source_refs": [{"kind": "manual", "note": SOURCE}],
            "source_fingerprint": None,
            "coverage": {"state": "unreviewed", "reviewed_by": None, "source_fingerprint": None, "note": None},
            "parts": [{"part_id": "part", "label": "工程收益", "state": "resolved",
                "rule": {"kind": "mechanics", "rule_id": row["id"]}, "delivery": {"kind": "mechanics"},
                "conditions": [], "dependencies": {"mode": "source_only", "context_fingerprint": None, "note": SOURCE}, "reason": None}]})
    original = fingerprint(plan)
    database = Path(root)/"intentionally-absent.db"
    try:
        compose_equipment_plan(plan, database)
    except PlanError as error:
        _require(any(row.get("dependency_kind") == "unsupported_conversion_closure" and row.get("known_cycle") is False
                     for row in error.details), "conversion_wrong_rejection", error.details)
    else:
        raise SelfcheckError("selfcheck_calculation_legacy_conversion_silently_admitted")
    draft = preview_attribute_recovery(plan)
    _require(draft["status"] == "draft_only" and draft["executes"] is draft["adopted"] is False
             and fingerprint(plan) == original, "recovery_changed_authority")
    candidate = copy.deepcopy(plan)
    candidate["mechanics_model"]["attribute_model"] = draft["operations"][0]["value"]
    if model_contract == "attribute-ledger-v3":
        candidate["mechanics_model"]["attribute_model"] = {"contract": model_contract,
            "initial_basis": [{"actor": actor, "stat": stat, "included_components": [],
                **({"base": {"mode": "unknown", **EVIDENCE}} if stat == "em" else {})}
                for actor in ("a", "b") for stat in ("em", "atk")],
            "effect_classifications": []}
    report = compose_equipment_plan(candidate, database)
    for requirement in report["effect_requirements"]:
        binding = next(row for row in candidate["effect_bindings"] if row["binding_id"] == requirement["binding_id"])
        binding.update(source_fingerprint=requirement["source_fingerprint"], coverage={"state": "complete", "reviewed_by": SOURCE,
            "source_fingerprint": requirement["source_fingerprint"], "note": SOURCE})
    adopted = prepare_attribute_review(candidate, SOURCE, database)
    calculated = run_plan_calc(adopted["plan"], database, capture_display=capture_display)
    _number(calculated["result"]["hits"][0]["expected"], (100+(100+.2*200))*.5*.9)
    _require(not database.exists() and fingerprint(plan) == original, "conversion_mutated_source_or_database")
    return calculated, {"draft_contract": draft["contract"], "executes": draft["executes"], "adopted": draft["adopted"],
        "result": calculated["result"]["hits"][0]["expected"], "independent_equation": "(100 + (100 + .2*200)) * .5 * .9"}


def _public_cli_probe(root):
    """Use the real parser/handler/envelope in this qualified interpreter."""
    from genshin_app import cli

    def invoke(arguments):
        output, errors = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            code = cli.main(["--data-dir", str(root), *arguments])
        _require(code == 0 and not errors.getvalue(), "public_cli_failed", {"arguments": arguments,
            "exit_code": code, "stderr": errors.getvalue()[:2048]})
        payload = json.loads(output.getvalue())
        _require(payload.get("code") == 0, "public_cli_invalid_envelope")
        return payload["data"]

    capabilities = invoke(["capabilities"])
    special = capabilities["special_reaction_contract"]
    _require(special["contract"] == "genshin-native-special-reaction-capabilities-v1"
             and set(special["engine_catalog"]["profiles"]) == {
                 "stellar_superconduct", "stellar_swirl", "lunar_electro", "lunar_bloom", "lunar_crystallize"},
             "public_special_catalog_incomplete")
    _require(special["input_contract"]["schemas"]["direct_stellar_damage_profile"]
             and special["input_contract"]["schemas"]["direct_lunar_damage_profile"]
             and capabilities["input_hash_contract"]["authority"] == "none", "public_special_schema_missing")
    digests = []
    for kind in ("canonical_json", "direct_stellar_damage_profile", "direct_lunar_damage_profile"):
        # Deliberately only a technical object, never a valid ability profile.
        # A digest must not promote it to executable/confirmed game knowledge.
        value = {"note": "固定技术输入", "status": "assumed", "count": 3, "profile_content_hash": "not-authority"}
        path = Path(root)/("hash-"+kind+".json")
        path.write_text(json.dumps({"kind": kind, "value": value}, ensure_ascii=False), encoding="utf-8")
        result = invoke(["engine", "hash-input", "--input", str(path)])
        included = value if kind == "canonical_json" else {key: item for key, item in value.items() if key != "profile_content_hash"}
        oracle = hashlib.sha256(json.dumps(included, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
        _require(result["content_hash"] == oracle and result["game_facts_verified"] is False
                 and result["authority"] == "none", "public_hash_wrong_or_authorizing", result)
        digests.append(result)
    return {"entry": "cli.main parser/handlers/envelopes", "special_contract": special["contract"],
        "family_count": len(special["engine_catalog"]["profiles"]), "hashes": digests, "database_used": False}


def _v3_saved_proof_probe(root):
    """Check specialized saved proof after bypassing only the generic hash."""
    from genshin_app.calculation_review import review_calculation
    from genshin_app.equipment.files import fingerprint
    from genshin_app.equipment.schema import PlanError

    calculated, numeric = _conversion_probe(root, model_contract="attribute-ledger-v3", capture_display=True)
    _require(calculated.get("display_context") is not None
             and calculated["result_receipt"].get("attribute_ledger", {}).get("verification_scope")
                 == "saved_attribute_execution_consistency_no_replay", "actual_v3_proof_missing")
    path = Path(root)/"v3-calculated.json"
    path.write_text(json.dumps(calculated, ensure_ascii=False, allow_nan=False), encoding="utf-8")
    original = fingerprint(calculated)
    saved = json.loads(path.read_text(encoding="utf-8"))
    accepted = review_calculation({key: saved[key] for key in ("result", "result_receipt")}, {"view": "attributes"})
    _require(accepted["items"], "v3_saved_attributes_unreadable")
    rejected = []
    for mutation in ("ledger", "sampling", "hit", "proof"):
        altered = copy.deepcopy(saved)
        result, receipt = altered["result"], altered["result_receipt"]
        if mutation == "ledger":
            known = next(row for row in result["attribute_ledger"]["receipts"] if row.get("status") == "known")
            known["value"] += 1
        elif mutation == "sampling":
            _require(result["sampling_trace"], "v3_sample_evidence_missing")
            result["sampling_trace"][0]["value"] += 1
        elif mutation == "hit":
            result["hits"][0]["expected"] += 1
        else:
            receipt.pop("attribute_ledger")
        # An adversary recomputes the generic content hash, but has no new
        # execution proof. This must still fail the original v3 consistency.
        receipt["result_fingerprint"] = fingerprint(result)
        try:
            review_calculation({"result": result, "result_receipt": receipt}, {"view": "summary"})
        except (PlanError, ValueError):
            rejected.append({"mutation": mutation, "generic_hash_recomputed": True, "rejected": True})
        else:
            raise SelfcheckError("selfcheck_calculation_v3_tamper_accepted", mutation)
    _require(fingerprint(saved) == original, "v3_original_mutated")
    return {"model_contract": "attribute-ledger-v3", "numeric_check": numeric, "capture_display": True,
        "saved_path": str(path), "proof_scope": saved["result_receipt"]["attribute_ledger"]["verification_scope"],
        "positive_attribute_rows": len(accepted["items"]), "recomputed_hash_tamper_rejections": rejected}


def _sheet(raw, number):
    ns = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        _require(archive.testzip() is None, "workbook_corrupt")
        tree = ET.fromstring(archive.read(f"xl/worksheets/sheet{number}.xml"))
    rows = {}
    for cell in tree.findall(".//s:c", ns):
        value, text, formula = cell.find("s:v", ns), cell.find("s:is/s:t", ns), cell.find("s:f", ns)
        rows[cell.attrib["r"]] = {"value": float(value.text) if value is not None and cell.attrib.get("t") != "str"
            else value.text if value is not None else text.text if text is not None else None,
            "formula": formula.text if formula is not None else None}
    return rows


def _weighted_probe(root, *, zero=False):
    from genshin_app.equipment.files import fingerprint
    from genshin_app.formula_workbook import (
        build_formula_workbook,
        formula_workbook_support,
    )
    from genshin_engine import calculate
    from genshin_engine.mechanics_special_damage import _apply_special_damage_weights

    template, _, _ = _inputs(actors=("a", "b"), times=(1, 1))
    template["characters"][0]["stats"].update(em=0, crit_rate=.2)
    template["characters"][1]["stats"].update(em=0, crit_rate=.5, atk=130)
    for index, hit in enumerate(template["hits"]):
        hit.update(character=("a", "b")[index], damage_system="lunar", reaction={"type": "lunar_electro"}, reaction_base=False,
            extensions={"elemental": {"cycle": 0}, "special_reaction": {"request_id": "group", "reaction_type": "lunar_electro"}})
    if zero:
        template["buffs"] = [{"id": "zero", "start": 0, "end": 10, "scope": "team", "effects": [
            {"id": "zero", "zone": "lunar_base", "stat": "all", "value": -1,
             "benefit_type_id": "benefit.lunar_base.all", "applies_to": {"mode": "all"}}]}]
    baseline = calculate(template)
    groups = {"group": {"hit_ids": [hit["id"] for hit in template["hits"]],
        "request": {"event_id": "group", "reaction_type": "lunar_electro", "t": 1}}}
    weighting = _apply_special_damage_weights(template, baseline, groups)
    result = calculate(template)
    result["special_reactions"] = {"damage_weighting": weighting}
    # Independently enumerate four Crit outcomes and re-rank actual damages.
    expected = [0.0, 0.0]
    for bits in itertools.product((False, True), repeat=2):
        probability = math.prod(rate if critical else 1-rate for rate, critical in zip((.2, .5), bits, strict=True))
        amounts = [base*3*.9*(2.5 if critical else 1) if not zero else 0 for base, critical in zip((100, 130), bits, strict=True)]
        for rank, index in enumerate(sorted(range(2), key=lambda i: (-amounts[i], f"hit.{i}"))):
            expected[index] += probability*amounts[index]*(.6, .3)[rank]
    for row, oracle in zip(result["hits"], expected, strict=True):
        _number(row["expected"], oracle)
        _require(row["result_weight"] > 0, "zero_lost_positive_weight")
    plan = {"plan_id": "synthetic-weighting", "revision": 0, "resolved_template": template}
    calculated = {"plan": plan, "result": result, "report": {}, "result_receipt": {
        "plan_id": plan["plan_id"], "revision": 0, "output_fingerprint": fingerprint(template), "result_fingerprint": fingerprint(result)}}
    support = formula_workbook_support(calculated)
    raw = build_formula_workbook(calculated)
    sheet = _sheet(raw, 3)
    for index, row in enumerate(result["hits"], 5):
        _number(sheet[f"V{index}"]["value"], row["expected"])
        _number(sheet[f"W{index}"]["value"], row["crit"])
        _require("需重新生成" in sheet[f"V{index}"]["formula"], "workbook_silently_reuses_derived_weight")
    target = Path(root)/("zero.xlsx" if zero else "weighted.xlsx")
    target.write_bytes(raw)
    return {"expected_by_independent_joint_outcomes": expected, "actual": [row["expected"] for row in result["hits"]],
        "weights": [row["result_weight"] for row in result["hits"]], "hit_count": len(result["hits"]),
        "workbook": str(target), "edit_policy": support["special_weighting"]["edit_policy"]}


def _fixed_weight_workbooks():
    from genshin_app.equipment.files import fingerprint
    from genshin_app.formula_workbook import build_formula_workbook
    from genshin_engine import calculate

    checks = []
    for system, reaction in (("classic", None), ("transformative", "swirl"), ("lunar", "lunar_electro"),
                             ("stellar", "stellar_superconduct")):
        template, _, _ = _inputs()
        template["characters"][0]["stats"].update(crit_rate=.5, em=0)
        hit = template["hits"][0]
        hit.update(damage_system=system, count=3)
        if reaction:
            hit["reaction"] = {"type": reaction, **({"layers": 4} if system == "stellar" else {})}
        baseline = calculate(template)["hits"][0]
        hit["result_weight"] = .3
        result = calculate(template)
        _number(result["hits"][0]["expected"], baseline["expected"]*.3)
        plan = {"plan_id": "synthetic-fixed-weight", "revision": 0, "resolved_template": template}
        calculated = {"plan": plan, "result": result, "result_receipt": {"plan_id": plan["plan_id"], "revision": 0,
            "output_fingerprint": fingerprint(template), "result_fingerprint": fingerprint(result)}}
        sheet = _sheet(build_formula_workbook(calculated), 3)
        _number(sheet["V5"]["value"], baseline["expected"]*.3)
        _number(sheet["AF5"]["value"], .3)
        if baseline["crit"] is not None:
            _number(sheet["W5"]["value"], baseline["crit"]*.3)
        checks.append({"system": system, "count": 3, "result_weight": .3, "expected": sheet["V5"]["value"]})
    return checks


def _tamper_probes(calculated, root):
    from genshin_app.calculation_integrity import validate_result_binding
    from genshin_app.equipment.service import result_bytes
    from genshin_app.export import export_result
    from genshin_app.formula_workbook import build_formula_workbook

    failures = []
    for label in ("hit", "summary", "budget"):
        altered = copy.deepcopy(calculated)
        if label == "hit":
            altered["result"]["hits"][0]["expected"] += 1
        elif label == "summary":
            altered["result"]["summary"]["total_damage"] += 1
        else:
            altered["result_receipt"].pop("attribute_execution_budget")
        path = Path(root)/("must-not-publish-"+label+".json")
        operations = (lambda value=altered: validate_result_binding(value["result"], value["result_receipt"]),
            lambda value=altered: result_bytes(value["result"], value["result_receipt"], "json"),
            lambda value=altered, target=path: export_result(value["result"], "json", target, receipt=value["result_receipt"]),
            lambda value=altered: build_formula_workbook(value))
        for index, operation in enumerate(operations):
            try:
                operation()
            except ValueError:
                failures.append({"tamper": label, "consumer": index, "rejected": True})
            else:
                raise SelfcheckError("selfcheck_calculation_tamper_accepted", {"tamper": label, "consumer": index})
        _require(not path.exists(), "tampered_file_published")
    return failures


def _delayed_credit_probe():
    from genshin_engine.mechanics import execute_mechanics

    template, rotation, model = _inputs()
    rotation["abilities"][0]["rules"] = [{"id": "particles", "kind": "energy_spawn"}]
    rotation["actions"][0]["rule_outcomes"] = [{"rule_id": "particles", "status": "triggered", "event_ids": ["spawn"]}]
    rotation["events"] = [{"id": "spawn", "type": "energy_spawn", "t": .2, "order": 1, "action_id": "cast", "count": 1,
        "element": "cryo", "particle_type": "particle", "source_kind": "skill", "repeat": {"mode": "each_cycle"}, **EVIDENCE},
        {"id": "credit", "type": "energy_credit", "credit_at": 10.2, "order": 2, "spawn_id": "spawn", "count": 1,
         "repeat": {"mode": "each_cycle"}, **EVIDENCE}]
    _rules(model, _rule("er", "benefit.energy_recharge.all", {"kind": "stat", "subject": "owner", "stat": "atk",
        "factor": .001, "sample": "event", "event_id": "cast"}))
    template["buffs"] = [{"id": "later", "start": 9, "end": 30, "scope": "team", "effects": [
        {"id": "atk", "zone": "stat_add", "stat": "atk", "value": 100,
         "benefit_type_id": "benefit.stat_add.atk", "applies_to": {"mode": "all"}}]}]
    before = copy.deepcopy((template, rotation, model))
    executed = execute_mechanics(template, rotation, model)
    _require(executed["result"] is not None and not executed["issues"], "delayed_credit_failed", executed["issues"])
    credits = [row for row in executed["rotation_check"]["event_log"] if row["id"] == "credit"]
    _require(len(credits) == 2, "delayed_credit_count")
    for row, expected in zip(credits, (3.3, 3.6), strict=True):
        _number(row["energy"][0]["gain"], expected)
    samples = [row for row in executed["sampling_trace"] if row["event_id"] == "credit"]
    _require([(row["cycle"], row["sample_t"], row["source_value"]) for row in samples] == [(0, 0, 100), (1, 10, 200)],
             "wrong_delayed_source_cycle", samples)
    _require((template, rotation, model) == before, "delayed_input_mutated")
    return {"credits": credits, "samples": samples, "independent_gains": [3*(1+.001*100), 3*(1+.001*200)]}


def _atomic_credit_probe():
    from genshin_engine.mechanics import execute_mechanics

    template, rotation, model = _inputs(actors=("a", "b", "c"))
    rotation["actions"][0]["repeat"] = {"mode": "once"}
    rotation["abilities"][0]["rules"] = [{"id": "particles", "kind": "energy_spawn"}]
    rotation["actions"][0]["rule_outcomes"] = [{"rule_id": "particles", "status": "triggered", "event_ids": ["spawn"]}]
    rotation["events"] = [{"id": "spawn", "type": "energy_spawn", "t": .2, "order": 1, "action_id": "cast", "count": 1,
        "element": "cryo", "particle_type": "particle", "source_kind": "skill", "repeat": {"mode": "once"}, **EVIDENCE},
        {"id": "credit", "type": "energy_credit", "credit_at": .3, "order": 2, "spawn_id": "spawn", "count": 1,
         "repeat": {"mode": "once"}, **EVIDENCE}]
    first = _rule("valid", "benefit.energy_recharge.all", {"kind": "stat", "subject": "owner", "stat": "atk",
        "factor": .001, "sample": "event", "event_id": "cast"})
    first["beneficiaries"]["characters"] = ["a", "b"]
    third = _rule("future", "benefit.energy_recharge.all", {"kind": "stat", "subject": "owner", "stat": "atk",
        "factor": .001, "sample": "event", "event_id": "later"})
    third["beneficiaries"]["characters"] = ["c"]
    _rules(model, first, third)
    model["attribute_model"] = {"contract": "attribute-ledger-v3", "initial_basis": [
        {"actor": actor, "stat": stat, "included_components": []} for actor in ("a", "b", "c") for stat in ("em", "atk")],
        "effect_classifications": []}
    model["condition_events"] = [{"id": "later", "t": .4, "order": 1, "condition": "later", "value": True,
        "repeat": {"mode": "once"}, **EVIDENCE}]
    result = execute_mechanics(template, rotation, model)
    _require(result["status"] == "pending" and result["result"] is None, "future_sample_silently_accepted", result["issues"])
    credit = result["rotation_check"]["event_log"][-1]
    balances = {row["id"]: row["final_energy"] for row in result["rotation_check"]["characters"]}
    attempts = [row for row in result["mechanics_trace"] if row["type"] == "energy_credit_sampling"]
    _require(credit["id"] == "credit" and "energy" not in credit and set(balances.values()) == {0}, "partial_energy_commit", credit)
    _require(len(attempts) == 1 and attempts[0]["status"] == "not_committed" and attempts[0]["sample_count"] == 2,
             "partial_sampling_not_identified", attempts)
    return {"issues": result["issues"], "credit": credit, "balances": balances, "attempt": attempts[0]}


def verify_fixed_calculations(root, *, frozen_package_root=None):
    execution = _execution(frozen_package_root)
    root = Path(root)
    root.mkdir(exist_ok=True)
    calculated, conversion = _conversion_probe(root)
    return {"contract": "genshin-frozen-calculation-engineering-v1", "status": "verified", "execution": execution,
        "scope": "synthetic_engineering_not_character_benchmark", "included_in_594_total": False,
        "declarations": _declaration_probes(), "conversion": conversion, "weighted_workbook": _weighted_probe(root),
        "zero_workbook": _weighted_probe(root, zero=True), "fixed_weight_workbooks": _fixed_weight_workbooks(),
        "tamper_rejections": _tamper_probes(calculated, root), "delayed_credit": _delayed_credit_probe(),
        "atomic_credit": _atomic_credit_probe(), "public_cli": _public_cli_probe(root),
        "v3_saved_proof": _v3_saved_proof_probe(root)}
