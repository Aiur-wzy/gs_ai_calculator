from __future__ import annotations

import copy
import hashlib

import pytest

from genshin_app.agent_chat.native_prompt_v2 import (
    NATIVE_V2_SKILL_RESOURCE,
    NativePromptV2Error,
    build_native_prompt_v2,
    validate_native_prompt_v2_bundle,
)


def _context():
    return {
        "contract": "genshin-native-agent-context-v2",
        "connection_kind": "native_api",
        "runtime_generation": "dsh_v2",
        "workflow": "staged",
        "conversation_id": "conversation-1",
        "turn_id": "turn-1",
        "epoch_id": "epoch-1",
        "workflow_stage": "event_topology",
        "candidate_revision": 3,
        "stage_revision": 2,
        "allowed_tools": ["gs_read_context", "web_search", "web_fetch"],
        "tool_capabilities": {
            "domain_tools": ["gs_read_context"],
            "app_tools": [],
            "research_tools": ["web_fetch", "web_search"],
            "recommended_for_stage": ["gs_read_context"],
        },
        "required_skill_resources_once": ["references/workflow/focus/events.md"],
        "workspace": {"display_name": "未命名方案", "dirty": True},
    }


def test_build_is_deterministic_and_repairs_v2_manual_without_mutating_input():
    source = _context()
    before = copy.deepcopy(source)
    first = build_native_prompt_v2(workflow="staged", runtime_context=source)
    second = build_native_prompt_v2(
        workflow="staged",
        runtime_context={key: source[key] for key in reversed(source)},
    )

    assert source == before
    assert first.system_prompt_hash == second.system_prompt_hash
    assert first.runtime_context_hash == second.runtime_context_hash
    assert NATIVE_V2_SKILL_RESOURCE in first.runtime_context_text
    assert "web_search" in first.system_prompt
    assert "web_fetch" in first.system_prompt
    assert "没有互联网" not in first.system_prompt
    assert "unknown_after_interruption" in first.system_prompt
    assert "全部捆绑Skill资料与角色" in first.system_prompt
    assert "只读知识均可在任意阶段按需读取" in first.system_prompt
    assert "recommended_for_stage 只是当前任务的推荐小集合" in first.system_prompt


@pytest.mark.parametrize("workflow", ["build", "calculate", "staged"])
def test_each_workflow_has_a_distinct_fixed_prompt(workflow):
    context = _context()
    context["workflow"] = workflow
    bundle = build_native_prompt_v2(workflow=workflow, runtime_context=context)
    assert bundle.workflow == workflow
    assert len(bundle.system_prompt_hash) == 64
    assert len(bundle.runtime_context_hash) == 64


@pytest.mark.parametrize(
    ("field", "value", "code"),
    [
        ("connection_kind", "cli", "native_prompt_connection_kind_mismatch"),
        ("runtime_generation", "python_v1", "native_prompt_runtime_generation_mismatch"),
        ("workflow", "build", "native_prompt_workflow_mismatch"),
    ],
)
def test_generation_and_workflow_cannot_cross(field, value, code):
    context = _context()
    context[field] = value
    with pytest.raises(NativePromptV2Error) as caught:
        build_native_prompt_v2(workflow="staged", runtime_context=context)
    assert caught.value.code == code


@pytest.mark.parametrize(
    ("field", "value", "code"),
    [
        ("api_key", "sentinel", "native_prompt_sensitive_field_rejected"),
        ("document_path", "plan.json", "native_prompt_sensitive_field_rejected"),
        ("note", r"C:\\Users\\me\\secret.txt", "native_prompt_local_path_rejected"),
        ("note", "file:///tmp/secret", "native_prompt_local_path_rejected"),
    ],
)
def test_secret_and_local_path_material_is_rejected(field, value, code):
    context = _context()
    context[field] = value
    with pytest.raises(NativePromptV2Error) as caught:
        build_native_prompt_v2(workflow="staged", runtime_context=context)
    assert caught.value.code == code


def test_public_web_urls_are_allowed_in_structured_context():
    context = _context()
    context["research_summary"] = {
        "source": "https://example.com/reference?q=rotation",
    }
    bundle = build_native_prompt_v2(workflow="staged", runtime_context=context)
    assert "https://example.com/reference?q=rotation" in bundle.runtime_context_text


def test_arbitrary_skill_paths_and_oversized_context_fail_closed():
    context = _context()
    context["required_skill_resources_once"] = ["../secret.md"]
    with pytest.raises(NativePromptV2Error) as caught:
        build_native_prompt_v2(workflow="staged", runtime_context=context)
    assert caught.value.code == "native_prompt_skill_resources_invalid"

    context = _context()
    context["note"] = "x" * (64 * 1024 + 1)
    with pytest.raises(NativePromptV2Error) as caught:
        build_native_prompt_v2(workflow="staged", runtime_context=context)
    assert caught.value.code == "native_prompt_context_text_invalid"


def test_public_bundle_validation_rejects_hash_tampering_and_alternate_prompt():
    bundle = build_native_prompt_v2(workflow="staged", runtime_context=_context())
    assert validate_native_prompt_v2_bundle(bundle.public()) == bundle

    stale_hash = {**bundle.public(), "runtime_context_text": (
        bundle.runtime_context_text.replace("event_topology", "numeric_model")
    )}
    with pytest.raises(NativePromptV2Error) as caught:
        validate_native_prompt_v2_bundle(stale_hash)
    assert caught.value.code == "native_prompt_context_hash"

    alternate = {**bundle.public(), "system_prompt": "legacy alternate prompt"}
    alternate["system_prompt_hash"] = hashlib.sha256(
        alternate["system_prompt"].encode("utf-8"),
    ).hexdigest()
    with pytest.raises(NativePromptV2Error) as caught:
        validate_native_prompt_v2_bundle(alternate)
    assert caught.value.code == "native_prompt_system_text"


def test_context_integer_must_round_trip_through_javascript_safely():
    context = _context()
    context["unsafe_counter"] = 2**53
    with pytest.raises(NativePromptV2Error) as caught:
        build_native_prompt_v2(workflow="staged", runtime_context=context)
    assert caught.value.code == "native_prompt_context_number_invalid"


def test_full_dsh_artifact_context_allows_normal_locations_but_not_secrets():
    context = _context()
    context.update(workflow="artifact", workflow_stage="artifact",
        candidate_cli={"connection_file": r"C:\Users\local\app\current.json"},
        workspace={"cwd": r"D:\tasks\experiment", "output_path": "/tmp/experiment/input.json"})
    bundle = build_native_prompt_v2(workflow="artifact", runtime_context=context)
    assert validate_native_prompt_v2_bundle(bundle.public()) == bundle
    assert "你没有 shell" not in bundle.system_prompt
    assert "保存、另存、打开、导出和导航只能" not in bundle.system_prompt
    assert "DSH原生权限" in bundle.system_prompt
    assert "已经是原生输入时无需重复转换" in bundle.system_prompt
    for key in ("api_key", "api_key_path", "credential_file", "writer_fence", "access_token"):
        with pytest.raises(NativePromptV2Error) as failure:
            build_native_prompt_v2(workflow="artifact", runtime_context={**context, key: "private"})
        assert failure.value.code == "native_prompt_sensitive_field_rejected"
