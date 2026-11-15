import ast
import copy
import importlib
import inspect

import pytest

from genshin_app.equipment.action_admissions import compile_action_admissions
from genshin_app.equipment.action_resources import (
    compile_action_resources,
    validate_action_resource_bindings,
)
from genshin_app.equipment.mechanics import prepare_mechanics
from genshin_app.equipment.schema import PlanError
from genshin_app.task_capability import classify_task_issues
from genshin_app.task_capability_producer import emit_task_missing
from tests.test_action_admission_sources import plan as admission_plan
from tests.test_action_resources_review import native as resource_plan

MIGRATED_PRODUCER_MODULES = (
    "genshin_app.equipment.action_admissions",
    "genshin_app.equipment.action_capacities",
    "genshin_app.equipment.action_dispatch_sources",
    "genshin_app.equipment.action_events",
    "genshin_app.equipment.action_resources",
    "genshin_app.equipment.attack_metadata",
    "genshin_app.equipment.elemental_sources",
    "genshin_app.equipment.energy_generation_sources",
    "genshin_app.equipment.finite_action_events",
    "genshin_app.equipment.hit_parameters",
    "genshin_app.equipment.limited_hit_benefit_sources",
    "genshin_app.equipment.mechanics",
    "genshin_app.equipment.passive_sources",
    "genshin_app.equipment.periodic_events",
    "genshin_app.equipment.periodic_sources",
    "genshin_app.equipment.reaction_sources",
)


def test_metadata_aware_sink_receives_app_owned_consumer_state():
    rows = []

    def sink(code, path, message, action, *, category, consumer, required,
             consumed, impact, blocking_scope, retryable, repair_hint):
        rows.append({
            "code": code, "path": path, "message": message,
            "category": category, "consumer": consumer,
            "required": required, "consumed": consumed, "impact": impact,
            "blocking_scope": blocking_scope, "retryable": retryable,
            "repair_hint": repair_hint,
        })

    emit_task_missing(
        sink, "optional_source_missing", "source.one", "可选来源缺失",
        "review_source", consumer="optional:one",
        category="knowledge_missing", required=False, consumed=False,
        impact="informational",
    )
    classified = classify_task_issues(rows)
    assert classified["status"] == "degraded"
    assert classified["blocking"] == []
    assert classified["degraded"][0]["consumer"] == "optional:one"


def test_legacy_positional_sink_remains_compatible_without_forging_metadata():
    rows = []
    emit_task_missing(
        lambda *args: rows.append(args),
        "missing_input", "source.one", "缺少输入", "review_source",
        consumer="source:one", category="core_data_missing",
    )
    assert rows == [("missing_input", "source.one", "缺少输入", "review_source")]


def test_selected_action_producers_publish_stable_consumers_without_database_access(
        monkeypatch):
    captured = []

    def sink(*args, **kwargs):
        captured.append((args, kwargs))

    admission = admission_plan()
    admission["rotation_model"] = None
    monkeypatch.setattr(
        "genshin_app.equipment.action_admissions.validate_action_admission_bindings",
        lambda _plan: None,
    )
    compile_action_admissions(admission, "unused.db", sink)

    resource = copy.deepcopy(resource_plan())
    resource["rotation_model"] = None
    compile_action_resources(resource, "unused.db", sink)

    consumers = {kwargs["consumer"] for _args, kwargs in captured}
    assert consumers == {"action_admission:admission.q", "action_resource:q"}
    assert all(kwargs["required"] is kwargs["consumed"] is True
               for _args, kwargs in captured)
    assert all(kwargs["blocking_scope"] == "task_capability"
               for _args, kwargs in captured)
    rows = [
        {"code": args[0], "path": args[1], "message": args[2], **kwargs}
        for args, kwargs in captured
    ]
    classified = classify_task_issues(rows)
    assert classified["status"] == "blocked"
    assert {row["consumer"] for row in classified["blocking"]} == consumers


def test_mechanics_root_gap_publishes_explicit_execution_graph_consumer():
    captured = []

    def sink(*args, **kwargs):
        captured.append((args, kwargs))

    result = prepare_mechanics(
        {"plan_version": 3, "mechanics_model": None, "rotation_model": None},
        [], {"characters": []}, "source-fingerprint", sink,
    )
    assert result == (None, [], [], [])
    assert {args[:2] for args, _metadata in captured} == {
        ("missing_input", "mechanics_model"), ("missing_input", "rotation_model")}
    assert len(captured) == 2
    for _args, metadata in captured:
        assert metadata["category"] == "knowledge_missing"
        assert metadata["blocking_scope"] == "agent_materialization"
        assert metadata["consumer"] == "mechanics.execution_graph"
        assert metadata["required"] is metadata["consumed"] is metadata["retryable"] is True
        assert metadata["impact"] == "high"
        assert "gs_patch_candidate" in metadata["repair_hint"]


@pytest.mark.parametrize(("field", "value"), [
    ("consumer", "forged.optional"),
    ("required", False),
    ("consumed", False),
])
def test_agent_plan_cannot_supply_producer_authority_fields(field, value):
    plan = copy.deepcopy(resource_plan())
    plan["action_resource_sources"][0][field] = value
    with pytest.raises(PlanError):
        validate_action_resource_bindings(plan)


def test_migrated_producer_inventory_has_no_legacy_direct_missing_calls():
    for module_name in MIGRATED_PRODUCER_MODULES:
        source = inspect.getsource(importlib.import_module(module_name))
        legacy_calls = [
            node.lineno for node in ast.walk(ast.parse(source))
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "missing"
        ]
        assert legacy_calls == [], (module_name, legacy_calls)
