"""File roles are independent: editable plan CAS never waives immutable requests."""

import os
from types import SimpleNamespace

import pytest

from genshin_app.equipment.files import file_sha, publish_files
from genshin_app.equipment.schema import PlanError
from genshin_app.output_policy import cli_output_scope

MUTABLE = (
    "_cmd_enable_mechanics",
    "_cmd_assemble_attack",
    "_cmd_assemble_periodic",
    "_cmd_equipment",
)


def args(tmp, handler, plan, **changes):
    a = SimpleNamespace(
        _handler=SimpleNamespace(__name__=handler),
        data_dir=str(tmp / "profile"),
        plan=str(plan),
        output=str(plan),
        overwrite=True,
        _equipment="compose",
        _workflow=None,
    )
    for k, v in changes.items():
        setattr(a, k, v)
    return a


@pytest.mark.parametrize("handler", MUTABLE)
def test_allowed_exact_inplace_keeps_real_cas_and_backup(tmp_path, handler):
    plan = tmp_path / "plan.json"
    plan.write_bytes(b"original")
    with cli_output_scope(args(tmp_path, handler, plan)):
        published = publish_files(
            [(plan, b"changed")],
            expected_shas={plan: file_sha(plan)},
            input_paths=[plan],
        )
    assert published["published_paths"] == [str(plan.resolve())]
    assert plan.with_name(plan.name + ".previous").read_bytes() == b"original"


@pytest.mark.parametrize("handler", MUTABLE)
@pytest.mark.parametrize("alias", ["same", "hardlink"])
def test_immutable_request_cannot_be_waived_by_plan_cas(tmp_path, handler, alias):
    plan = tmp_path / "plan.json"
    plan.write_bytes(b"original")
    request = plan if alias == "same" else tmp_path / "request.json"
    if alias == "hardlink":
        os.link(plan, request)
    with (
        cli_output_scope(args(tmp_path, handler, plan, request=str(request))),
        pytest.raises(PlanError),
    ):
        publish_files(
            [(plan, b"bad")], expected_shas={plan: file_sha(plan)}, input_paths=[plan]
        )
    assert plan.read_bytes() == b"original"


@pytest.mark.parametrize("handler", MUTABLE)
def test_request_alias_introduced_during_prepare_is_still_rejected(
    tmp_path, monkeypatch, handler
):
    from genshin_app.equipment import files

    plan = tmp_path / "plan.json"
    plan.write_bytes(b"original")
    request = tmp_path / "request.json"
    request.write_bytes(b"request")
    original = files.tempfile.mkstemp

    def race(*a, **k):
        answer = original(*a, **k)
        request.unlink()
        os.link(plan, request)
        return answer

    monkeypatch.setattr(files.tempfile, "mkstemp", race)
    with (
        cli_output_scope(args(tmp_path, handler, plan, request=str(request))),
        pytest.raises(PlanError),
    ):
        publish_files(
            [(plan, b"bad")], expected_shas={plan: file_sha(plan)}, input_paths=[plan]
        )
    assert plan.read_bytes() == b"original"
    assert not plan.with_name(plan.name + ".previous").exists()


@pytest.mark.parametrize("handler", MUTABLE)
def test_database_is_never_mutable_even_with_exact_cas(tmp_path, handler):
    db = tmp_path / "profile/genshin.db"
    db.parent.mkdir()
    db.write_bytes(b"database")
    with cli_output_scope(args(tmp_path, handler, db)), pytest.raises(PlanError):
        publish_files([(db, b"bad")], expected_shas={db: file_sha(db)})
    assert db.read_bytes() == b"database"


@pytest.mark.parametrize("command", ["calc", "export"])
def test_no_new_inplace_permission_for_readonly_commands(tmp_path, command):
    plan = tmp_path / "plan.json"
    plan.write_bytes(b"original")
    with (
        cli_output_scope(args(tmp_path, "_cmd_equipment", plan, _equipment=command)),
        pytest.raises(PlanError),
    ):
        publish_files([(plan, b"bad")], expected_shas={plan: file_sha(plan)})
    assert plan.read_bytes() == b"original"


def test_distinct_hardlink_output_is_not_new_inplace_permission(tmp_path):
    plan = tmp_path / "plan.json"
    plan.write_bytes(b"original")
    alias = tmp_path / "alias.json"
    os.link(plan, alias)
    with (
        cli_output_scope(
            args(tmp_path, "_cmd_assemble_attack", plan, output=str(alias))
        ),
        pytest.raises(PlanError),
    ):
        publish_files([(alias, b"bad")], expected_shas={alias: file_sha(alias)})
    assert plan.read_bytes() == b"original"


def test_existing_runner_fresh_directory_guard_already_preserves_prompt(tmp_path):
    from genshin_app.agent_chat.dsh_gui_workflow_runner import run_gui_agent_workflow

    target = tmp_path / "existing"
    target.mkdir()
    prompt = target / "gui-workflow-receipt.json"
    prompt.write_text("明确任务", encoding="utf-8")

    def no_api(_directory):
        pytest.fail(
            "existing output directory must be rejected before any Agent/API factory"
        )

    with pytest.raises(FileExistsError):
        run_gui_agent_workflow(
            data_dir=tmp_path / "data",
            prompt=prompt.read_text(encoding="utf-8"),
            output_dir=target,
            api_factory=no_api,
        )
    assert prompt.read_text(encoding="utf-8") == "明确任务"


@pytest.mark.parametrize("race", [False, True])
def test_gui_workflow_cli_prompt_role_and_private_owner(
    tmp_path, monkeypatch, capsys, race
):
    from genshin_app import cli
    from genshin_app.agent_chat import dsh_gui_workflow_runner as runner
    from genshin_app.equipment import files

    data = tmp_path / "data"
    prompt = tmp_path / "task.txt"
    prompt.write_bytes(b"explicit task")
    output = tmp_path / "new-output"
    receipt = output / "gui-workflow-receipt.json"
    monkeypatch.setattr(
        cli, "_require_runtime_database", lambda _directory: {"can_start": True}
    )
    calls = []

    def controlled_runner(**options):
        calls.append(options["prompt"])
        output.mkdir()
        # Actual private file publisher remains usable: this CLI scope does
        # not apply ordinary-export restrictions to the DSH profile owner.
        private = data / "native-dsh-workspaces" / "fixture" / "checkpoint.json"
        publish_files([(private, b"private-owner")])
        if race:
            receipt.write_bytes(b"prior-receipt")
            expected = file_sha(receipt)
            original = files.tempfile.mkstemp

            def alias_after_preflight(*values, **keywords):
                answer = original(*values, **keywords)
                prompt.rename(tmp_path / "external-prompt-snapshot")
                os.link(receipt, prompt)
                return answer

            monkeypatch.setattr(files.tempfile, "mkstemp", alias_after_preflight)
            publish_files(
                [(receipt, b"new-receipt")], expected_shas={receipt: expected}
            )
        else:
            publish_files([(receipt, b"new-receipt")])
        return {"status": "controlled_file_boundary_only"}

    monkeypatch.setattr(runner, "run_gui_agent_workflow", controlled_runner)
    result = cli.main(
        [
            "--data-dir",
            str(data),
            "agent",
            "gui-workflow",
            "--prompt-file",
            str(prompt),
            "--output-dir",
            str(output),
        ]
    )
    rendered = capsys.readouterr()
    assert calls == ["explicit task"]
    assert (
        data / "native-dsh-workspaces/fixture/checkpoint.json"
    ).read_bytes() == b"private-owner"
    if race:
        assert result == 1 and '"file_conflict"' in rendered.err
        assert receipt.read_bytes() == b"prior-receipt"
        assert (tmp_path / "external-prompt-snapshot").read_bytes() == b"explicit task"
        assert not receipt.with_name(receipt.name + ".previous").exists()
    else:
        assert result == 0 and prompt.read_bytes() == b"explicit task"
        assert receipt.read_bytes() == b"new-receipt"
