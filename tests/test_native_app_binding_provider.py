from __future__ import annotations

import copy
import json
import shutil
import uuid
from pathlib import Path

import pytest

from genshin_app.agent_chat.artifact_candidate import ArtifactCandidateSession
from genshin_app.agent_chat.board_candidate import BoardCandidateSession
from genshin_app.agent_chat.candidate import CandidateSession
from genshin_app.agent_chat.native_app_binding_provider import (
    BINDING_FILE,
    CandidateNativeAppBindingProviderV2,
)
from genshin_app.agent_chat.native_prompt_v2 import build_native_prompt_v2
from genshin_app.agent_chat.native_write_ledger import SessionWriteAuthority
from genshin_app.agent_chat.staged_candidate import StagedCandidateSession
from genshin_app.agent_chat.tools import skill_resource_names
from genshin_app.equipment.files import fingerprint
from genshin_app.equipment.schema import PlanError
from genshin_app.whiteboard import new_whiteboard
from genshin_db import queries
from tests.test_calculation_delivery import delivery_plan


@pytest.fixture
def app_database(tmp_path, monkeypatch):
    database = tmp_path / "app.sqlite"
    database.touch()
    state = {
        "value": {
            "version_lines": ["release"],
            "datasets": {
                "equipment": {
                    "lines": {
                        "release": {
                            "state": "ready",
                            "schema_version": 1,
                            "upstream_commit": "a" * 40,
                            "built_at": "2026-10-23T00:00:00Z",
                        }
                    }
                }
            },
        }
    }
    monkeypatch.setattr(queries, "get_meta", lambda _path: copy.deepcopy(state["value"]))
    return database, state


def _root(tmp_path: Path, name: str = "conversation") -> Path:
    root = tmp_path / name
    root.mkdir()
    return root


def _prepare(
    provider: CandidateNativeAppBindingProviderV2,
    *,
    conversation_id: str,
    workflow: str,
    document_kind: str,
    document: dict,
    database: Path,
    delivery_root: Path | None = None,
) -> None:
    provider.prepare_create(
        conversation_id=conversation_id,
        workflow=workflow,
        document_kind=document_kind,
        document_seed=document,
        database_path=database,
        delivery_root=delivery_root,
    )


def _strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, item in value.items():
            yield key
            yield from _strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)


@pytest.mark.parametrize(
    ("workflow", "document_kind", "factory", "session_class"),
    [
        ("build", "whiteboard", new_whiteboard, BoardCandidateSession),
        ("staged", "whiteboard", new_whiteboard, StagedCandidateSession),
        ("artifact", "whiteboard", new_whiteboard, ArtifactCandidateSession),
        ("calculate", "native", delivery_plan, CandidateSession),
    ],
)
def test_create_binds_all_supported_candidate_workflows_and_projects_no_paths(
    app_database,
    tmp_path,
    workflow,
    document_kind,
    factory,
    session_class,
):
    database, _state = app_database
    root = _root(tmp_path, workflow)
    delivery = tmp_path / (workflow + "-deliveries")
    provider = CandidateNativeAppBindingProviderV2()
    document = factory()
    original = copy.deepcopy(document)
    _prepare(
        provider,
        conversation_id="conversation-" + workflow,
        workflow=workflow,
        document_kind=document_kind,
        document=document,
        database=database,
        delivery_root=delivery,
    )
    document["title"] = "调用方随后修改的值"

    binding = provider.create(
        conversation_id="conversation-" + workflow,
        session_root=root,
        workflow=workflow,
    )

    assert isinstance(binding.candidate_session, session_class)
    assert binding.candidate_session.document == original
    assert binding.workflow_stage() == (
        "loadout_draft" if workflow == "staged" else workflow
    )
    checkpoint = binding.domain_checkpoint()
    assert set(checkpoint) == {
        "document_id",
        "candidate_revision",
        "stage_revision",
        "database_fingerprint",
        "receipt_fingerprint",
    }
    assert checkpoint["candidate_revision"] == 0
    assert checkpoint["stage_revision"] == 0
    assert len(binding.tool_manifest_hash()) == 64
    first_state_hash = binding.app_state_hash(
        runtime_generation="dsh_v2",
        writer_fence="writer-fence-state",
    )
    assert len(first_state_hash) == 64
    assert binding.app_state_hash(
        runtime_generation="dsh_v2",
        writer_fence="writer-fence-state",
    ) == first_state_hash
    assert binding.app_state_hash(
        runtime_generation="dsh_v2",
        writer_fence="writer-fence-other",
    ) != first_state_hash
    context = binding.runtime_context()
    assert context["workflow"] == workflow
    assert context["workflow_stage"] == binding.workflow_stage()
    actual_summary = binding.candidate_session.summary()
    for key in ("representation", "application_kind", "ready_scope", "assessment"):
        assert context["candidate"][key] == actual_summary[key]
    serialized_context = json.dumps(context, ensure_ascii=False, sort_keys=True)
    assert "available_skill_resources" not in context
    assert "原神拉表计算技能" not in serialized_context
    assert context["skill_access"] == {
        "policy": "all_bundled_markdown_on_demand",
        "index_tool": "gs_read_skill",
        "resource_count": len(skill_resource_names()),
    }
    tool_capabilities = context["tool_capabilities"]
    assert tool_capabilities["domain_tools"] == context["allowed_domain_tools"]
    assert tool_capabilities["app_tools"] == [
        "gs_app_get_workspace", "gs_app_request_action",
    ]
    assert tool_capabilities["research_tools"] == [
        "gs_list_research_calls", "gs_report_research_usage",
        "web_fetch", "web_search",
    ]
    assert set(tool_capabilities["recommended_for_stage"]) <= set(
        tool_capabilities["domain_tools"]
    )
    expected_required = ["SKILL.md"]
    if workflow == "staged":
        assert "available_skill_resources" not in context["stage"]
        assert "skill_access" not in context["stage"]
        expected_required.extend([
            "references/workflow/context-use.md",
            context["stage"]["current_stage_skill"],
        ])
        assert context["stage"]["current_stage_skill"] in context["stage"][
            "recommended_skill_resources"
        ]
        assert context["stage"]["recommended_tools"] == tool_capabilities[
            "recommended_for_stage"
        ]
        assert len(context["stage"]["recommended_skill_resources"]) <= 12
        assert len(json.dumps(
            context["stage"]["recommended_skill_resources"],
            ensure_ascii=False,
            separators=(",", ":"),
        )) <= 1024
    elif workflow == "artifact":
        expected_required.extend([
            "references/workflow/index.md", context["stage"]["current_stage_skill"]])
        assert context["stage"]["focus_is_authorization"] is False
        assert context["workflow_stage"] == "artifact"
        from genshin_app.agent_chat.skill_context import current_focus_card
        assert current_focus_card(context)["focus"] == "loadout_draft"
    assert context["required_skill_resources_once"] == expected_required

    prompt = build_native_prompt_v2(workflow=workflow, runtime_context=context)
    prompt_context = json.loads(prompt.runtime_context_text.splitlines()[2])
    assert "available_skill_resources" not in prompt_context
    assert prompt_context["required_skill_resources_once"] == (
        expected_required if workflow == "artifact" else
        ["references/runtime/native-agent.md", *expected_required])
    listed = binding.read_domain_tool("gs_read_skill", {})
    assert listed["code"] == 0
    assert listed["data"]["resources"] == skill_resource_names()
    assert not set(listed["data"]["resources"]).issubset(set(_strings(context)))
    for resource in prompt_context["required_skill_resources_once"]:
        assert resource in listed["data"]["resources"]
        skill_page = binding.read_domain_tool(
            "gs_read_skill", {"resource": resource}
        )
        assert skill_page["code"] == 0
        page = skill_page["data"]
        assert page["resource"] == resource
        assert 0 < page["delivered_chars"] == len(page["text"]) <= page["total"]
    assert context["document"]["document_id"] == checkpoint["document_id"]
    local_values = {
        str(root),
        str(root.resolve()),
        str(database),
        str(database.resolve()),
        str(delivery),
        str(delivery.resolve()),
    }
    assert not local_values.intersection(_strings(context))


def test_create_seed_is_single_use_even_after_a_mismatched_create(app_database, tmp_path):
    database, _state = app_database
    provider = CandidateNativeAppBindingProviderV2()
    _prepare(
        provider,
        conversation_id="conversation-seed",
        workflow="build",
        document_kind="whiteboard",
        document=new_whiteboard(),
        database=database,
    )
    root = _root(tmp_path)

    with pytest.raises(PlanError) as error:
        provider.create(
            conversation_id="conversation-seed",
            session_root=root,
            workflow="staged",
        )
    assert error.value.reason == "native_app_binding_mismatch"

    with pytest.raises(PlanError) as error:
        provider.create(
            conversation_id="conversation-seed",
            session_root=root,
            workflow="build",
        )
    assert error.value.reason == "native_app_binding_seed_missing"
    assert not (root / "checkpoint.json").exists()


def test_open_recovers_staged_session_from_same_root_and_app_storage_config(
    app_database,
    tmp_path,
):
    database, _state = app_database
    conversation_id = "conversation-staged-restore"
    root = _root(tmp_path)
    delivery = tmp_path / "deliveries"
    provider = CandidateNativeAppBindingProviderV2()
    _prepare(
        provider,
        conversation_id=conversation_id,
        workflow="staged",
        document_kind="whiteboard",
        document=new_whiteboard(),
        database=database,
        delivery_root=delivery,
    )
    created = provider.create(
        conversation_id=conversation_id,
        session_root=root,
        workflow="staged",
    )
    expected_document = copy.deepcopy(created.candidate_session.document)
    expected_manifest = created.tool_manifest_hash()
    created.close()

    restarted = CandidateNativeAppBindingProviderV2(
        storage_resolver=lambda requested: {
            "database_path": database,
            "delivery_root": delivery,
        }
        if requested == conversation_id
        else (_ for _ in ()).throw(AssertionError("unexpected conversation"))
    )
    opened = restarted.open(
        conversation_id=conversation_id,
        session_root=root,
        workflow="staged",
    )

    assert isinstance(opened.candidate_session, StagedCandidateSession)
    assert opened.candidate_session.document == expected_document
    assert opened.workflow_stage() == "loadout_draft"
    assert opened.tool_manifest_hash() == expected_manifest
    assert opened.runtime_context()["stage"]["representation"] == "whiteboard"


def test_applied_board_can_advance_document_content_without_changing_binding_identity(
    app_database,
    tmp_path,
):
    database, _state = app_database
    conversation_id = "conversation-applied-board"
    root = _root(tmp_path)
    provider = CandidateNativeAppBindingProviderV2()
    original = new_whiteboard()
    _prepare(
        provider,
        conversation_id=conversation_id,
        workflow="build",
        document_kind="whiteboard",
        document=original,
        database=database,
    )
    binding = provider.create(
        conversation_id=conversation_id,
        session_root=root,
        workflow="build",
    )
    session = binding.candidate_session
    changed = copy.deepcopy(session.candidate)
    changed["title"] = "用户已应用的新标题"
    summary = session.replace_candidate(changed, 0, "按用户要求更新标题")
    accepted = session.accept(
        "whiteboard",
        original,
        1,
        summary["receipt_fingerprint"],
    )
    session.resume_document(accepted["board"])
    binding.close()

    reopened = provider.open(
        conversation_id=conversation_id,
        session_root=root,
        workflow="build",
    )
    assert reopened.candidate_session.document["title"] == "用户已应用的新标题"
    assert reopened.domain_checkpoint()["document_id"] == original["board_id"]


def test_open_rejects_cross_conversation_workflow_and_copied_root(app_database, tmp_path):
    database, _state = app_database
    conversation_id = "conversation-owned"
    root = _root(tmp_path, "owned")
    provider = CandidateNativeAppBindingProviderV2()
    _prepare(
        provider,
        conversation_id=conversation_id,
        workflow="build",
        document_kind="whiteboard",
        document=new_whiteboard(),
        database=database,
    )
    provider.create(
        conversation_id=conversation_id,
        session_root=root,
        workflow="build",
    ).close()

    provider.configure_open(
        conversation_id="conversation-other",
        database_path=database,
    )
    with pytest.raises(PlanError) as error:
        provider.open(
            conversation_id="conversation-other",
            session_root=root,
            workflow="build",
        )
    assert error.value.reason == "native_app_binding_mismatch"

    with pytest.raises(PlanError) as error:
        provider.open(
            conversation_id=conversation_id,
            session_root=root,
            workflow="staged",
        )
    assert error.value.reason == "native_app_binding_mismatch"

    copied = tmp_path / "copied"
    shutil.copytree(root, copied)
    with pytest.raises(PlanError) as error:
        provider.open(
            conversation_id=conversation_id,
            session_root=copied,
            workflow="build",
        )
    assert error.value.reason == "native_app_binding_mismatch"


def test_open_rejects_database_snapshot_or_config_change(app_database, tmp_path):
    database, state = app_database
    conversation_id = "conversation-database"
    root = _root(tmp_path)
    provider = CandidateNativeAppBindingProviderV2()
    _prepare(
        provider,
        conversation_id=conversation_id,
        workflow="build",
        document_kind="whiteboard",
        document=new_whiteboard(),
        database=database,
    )
    provider.create(
        conversation_id=conversation_id,
        session_root=root,
        workflow="build",
    ).close()

    other_database = tmp_path / "other.sqlite"
    other_database.touch()
    provider.configure_open(
        conversation_id=conversation_id,
        database_path=other_database,
    )
    with pytest.raises(PlanError) as error:
        provider.open(
            conversation_id=conversation_id,
            session_root=root,
            workflow="build",
        )
    assert error.value.reason == "native_app_binding_mismatch"

    provider.configure_open(conversation_id=conversation_id, database_path=database)
    state["value"]["datasets"]["equipment"]["lines"]["release"][
        "upstream_commit"
    ] = "b" * 40
    with pytest.raises(PlanError) as error:
        provider.open(
            conversation_id=conversation_id,
            session_root=root,
            workflow="build",
        )
    assert error.value.reason == "native_app_binding_database_changed"


def test_binding_record_and_candidate_identity_tamper_fail_closed(app_database, tmp_path):
    database, _state = app_database
    conversation_id = "conversation-tamper"
    root = _root(tmp_path)
    provider = CandidateNativeAppBindingProviderV2()
    _prepare(
        provider,
        conversation_id=conversation_id,
        workflow="build",
        document_kind="whiteboard",
        document=new_whiteboard(),
        database=database,
    )
    provider.create(
        conversation_id=conversation_id,
        session_root=root,
        workflow="build",
    ).close()

    path = root / BINDING_FILE
    record = json.loads(path.read_text(encoding="utf-8"))
    record["document_id"] = str(uuid.uuid4())
    body = {key: value for key, value in record.items() if key != "record_hash"}
    record["record_hash"] = fingerprint(body)
    path.write_text(
        json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )

    with pytest.raises(PlanError) as error:
        provider.open(
            conversation_id=conversation_id,
            session_root=root,
            workflow="build",
        )
    assert error.value.reason == "native_app_binding_mismatch"


def test_binding_record_duplicate_key_is_rejected(app_database, tmp_path):
    database, _state = app_database
    conversation_id = "conversation-duplicate"
    root = _root(tmp_path)
    provider = CandidateNativeAppBindingProviderV2()
    _prepare(
        provider,
        conversation_id=conversation_id,
        workflow="build",
        document_kind="whiteboard",
        document=new_whiteboard(),
        database=database,
    )
    provider.create(
        conversation_id=conversation_id,
        session_root=root,
        workflow="build",
    ).close()
    path = root / BINDING_FILE
    original = path.read_text(encoding="utf-8")
    path.write_text(
        original.replace("{", '{"contract":"duplicate",', 1),
        encoding="utf-8",
    )

    with pytest.raises(PlanError) as error:
        provider.open(
            conversation_id=conversation_id,
            session_root=root,
            workflow="build",
        )
    assert error.value.reason == "native_app_binding_invalid"


def test_writer_fence_install_is_idempotent_and_close_revokes_binding(
    app_database,
    tmp_path,
):
    database, _state = app_database
    root = _root(tmp_path)
    provider = CandidateNativeAppBindingProviderV2()
    _prepare(
        provider,
        conversation_id="conversation-close",
        workflow="calculate",
        document_kind="native",
        document=delivery_plan(),
        database=database,
    )
    binding = provider.create(
        conversation_id="conversation-close",
        session_root=root,
        workflow="calculate",
    )
    binding.install_writer_fence("writer-fence-a")
    binding.install_writer_fence("writer-fence-a")
    binding.install_writer_fence("writer-fence-b")
    binding.close()
    binding.close()

    assert binding.candidate_session.closed is True
    for operation in (
        binding.domain_checkpoint,
        binding.runtime_context,
        binding.tool_manifest_hash,
        binding.workflow_stage,
        lambda: binding.app_state_hash(
            runtime_generation="dsh_v2",
            writer_fence="writer-fence-a",
        ),
    ):
        with pytest.raises(PlanError) as error:
            operation()
        assert error.value.reason == "native_app_binding_closed"


def test_calculation_boundary_lends_only_the_exact_current_native_candidate(
    app_database,
    tmp_path,
):
    database, _state = app_database
    root = _root(tmp_path, "calculation-boundary")
    provider = CandidateNativeAppBindingProviderV2()
    document = delivery_plan()
    _prepare(
        provider,
        conversation_id="conversation-calculation-boundary",
        workflow="calculate",
        document_kind="native",
        document=document,
        database=database,
    )
    binding = provider.create(
        conversation_id="conversation-calculation-boundary",
        session_root=root,
        workflow="calculate",
    )
    binding.install_writer_fence("writer-fence-calculation")

    requested = copy.deepcopy(binding.candidate_session.candidate)
    # Derived resolution caches are not domain inputs and may differ after a
    # prior GUI calculation.
    requested["resolution"] = {"state": "pending"}
    with binding.calculation_session_boundary(requested) as session:
        assert session is binding.candidate_session

    changed = copy.deepcopy(requested)
    changed["manual_template"]["meta"]["name"] = "另一份方案"
    with (
        pytest.raises(PlanError) as error,
        binding.calculation_session_boundary(changed),
    ):
        pass
    assert error.value.reason == "native_candidate_session_plan_mismatch"


def test_open_requires_explicit_trusted_writer_authority_injection(
    app_database,
    tmp_path,
):
    database, _state = app_database
    conversation_id = "conversation-explicit-writer"
    root = _root(tmp_path)
    provider = CandidateNativeAppBindingProviderV2()
    _prepare(
        provider,
        conversation_id=conversation_id,
        workflow="build",
        document_kind="whiteboard",
        document=new_whiteboard(),
        database=database,
    )
    created = provider.create(
        conversation_id=conversation_id,
        session_root=root,
        workflow="build",
    )
    created.install_writer_fence("writer-fence-a")
    document_id = created.domain_checkpoint()["document_id"]
    created.close()

    opened = provider.open(
        conversation_id=conversation_id,
        session_root=root,
        workflow="build",
    )
    with pytest.raises(PlanError) as error:
        opened.current_writer_fence()
    assert error.value.reason == "native_app_writer_not_installed"

    authority = SessionWriteAuthority(
        opened.candidate_session,
        writer_fence="writer-fence-a",
        document_id=document_id,
    )
    opened.install_writer_authority(authority)
    opened.install_writer_authority(authority)
    assert opened.current_writer_fence() == "writer-fence-a"


def test_stale_binding_cannot_discover_and_follow_replacement_writer(
    app_database,
    tmp_path,
):
    database, _state = app_database
    root = _root(tmp_path)
    provider = CandidateNativeAppBindingProviderV2()
    _prepare(
        provider,
        conversation_id="conversation-stale-writer",
        workflow="build",
        document_kind="whiteboard",
        document=new_whiteboard(),
        database=database,
    )
    binding = provider.create(
        conversation_id="conversation-stale-writer",
        session_root=root,
        workflow="build",
    )
    binding.install_writer_fence("writer-fence-a")
    document_id = binding.domain_checkpoint()["document_id"]
    replacement = SessionWriteAuthority(
        binding.candidate_session,
        writer_fence="writer-fence-a",
        document_id=document_id,
    )
    replacement.transfer(
        expected_fence="writer-fence-a",
        new_fence="writer-fence-b",
    )

    with pytest.raises(PlanError) as error:
        binding.current_writer_fence()
    assert error.value.reason == "stale_context"
    with pytest.raises(PlanError) as error:
        binding.execute_domain_tool(
            "gs_patch_whiteboard",
            {
                "expected_revision": 0,
                "reason": "陈旧进程不得写入",
                "operations": [],
            },
        )
    assert error.value.reason == "stale_context"
