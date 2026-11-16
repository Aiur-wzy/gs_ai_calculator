"""Linked launch preserves the original input and the chosen local database."""
import ctypes
import json
import subprocess
import sys
from pathlib import Path

import pytest

from genshin_app.cli import main
from genshin_app.equipment import new_plan
from genshin_app.equipment.schema import PlanError
from genshin_app.shortcuts import create_shortcut

EXAMPLE = Path(__file__).resolve().parents[1] / "examples/freeze_team.json"


@pytest.fixture
def input_file(tmp_path):
    path = tmp_path / "中文 空格 单'引 & $(nothing) %_计划.json"
    path.write_bytes(EXAMPLE.read_bytes())
    return path


@pytest.fixture
def link_renderer(monkeypatch):
    calls = []
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr("genshin_app.shortcuts._launcher", lambda: (Path(sys.executable), []))
    monkeypatch.setattr("genshin_app.shortcuts._windows_link", lambda spec: calls.append(spec) or b"link")
    return calls


def test_link_points_to_original_and_captures_explicit_database(input_file, tmp_path, link_renderer):
    before = input_file.read_bytes()
    destination = tmp_path / "打开.lnk"
    database = tmp_path / "单独 数据库"
    result = create_shortcut(input_file, destination, database)
    assert result["input_kind"] == "resolved_template"
    assert result["argv"] == ["--data-dir", str(database.resolve()), "gui", "--open", str(input_file)]
    assert link_renderer[0]["arguments"] == subprocess.list2cmdline(result["argv"])
    assert input_file.read_bytes() == before
    assert not database.exists()  # Export doesn't migrate or initialize the DB.
    assert destination.read_bytes() == b"link"
    assert not list(tmp_path.glob("*.json.previous"))


def test_native_pending_plan_is_linkable_without_composition(input_file, tmp_path, link_renderer):
    plan = new_plan(json.loads(input_file.read_text(encoding="utf-8")))
    input_file.write_text(json.dumps(plan), encoding="utf-8")
    assert create_shortcut(input_file, tmp_path/"open.lnk", tmp_path)["input_kind"] == "native_plan"


def test_whiteboard_links_original_pending_input_without_database(input_file, tmp_path, link_renderer):
    from genshin_app.whiteboard import new_whiteboard
    board = new_whiteboard()
    input_file.write_text(json.dumps(board, ensure_ascii=False), encoding="utf-8")
    before = input_file.read_bytes()
    result = create_shortcut(input_file, tmp_path/"白板.lnk", tmp_path/"missing-data")
    assert result["input_kind"] == "whiteboard"
    assert result["argv"][-2:] == ["--open", str(input_file)]
    assert input_file.read_bytes() == before
    assert not (tmp_path/"missing-data").exists()


def test_invalid_whiteboard_is_not_linked(input_file, tmp_path, link_renderer):
    input_file.write_text('{"kind":"genshin-whiteboard"}', encoding="utf-8")
    with pytest.raises(PlanError):
        create_shortcut(input_file, tmp_path/"bad.lnk", tmp_path)
    assert link_renderer == []


def test_rejects_result_or_receipt_instead_of_silently_linking(input_file, tmp_path, link_renderer):
    input_file.write_text('{"result":{"summary":{"dps":123}}}', encoding="utf-8")
    with pytest.raises(ValueError):
        create_shortcut(input_file, tmp_path/"open.lnk", tmp_path)
    assert link_renderer == []


def test_creation_failure_and_overwrite_preserve_files(input_file, tmp_path, link_renderer, monkeypatch):
    out = tmp_path/"open.lnk"
    out.write_bytes(b"original")
    with pytest.raises(PlanError, match="已存在"):
        create_shortcut(input_file,out,tmp_path)
    assert out.read_bytes() == b"original"
    create_shortcut(input_file,out,tmp_path,overwrite=True)
    assert out.with_name("open.lnk.previous").read_bytes() == b"original"
    monkeypatch.setattr("genshin_app.shortcuts._windows_link", lambda _: (_ for _ in ()).throw(RuntimeError("COM unavailable")))
    with pytest.raises(RuntimeError, match="COM unavailable"):
        create_shortcut(input_file,out,tmp_path,overwrite=True)
    assert out.read_bytes() == b"link"


def test_input_cannot_be_overwritten_even_if_named_lnk(input_file, tmp_path, link_renderer):
    source = tmp_path/"misnamed.lnk"
    source.write_bytes(input_file.read_bytes())
    before = source.read_bytes()
    with pytest.raises(PlanError):
        create_shortcut(source,source,tmp_path,overwrite=True)
    assert source.read_bytes() == before and link_renderer == []


def test_output_extension_is_explicit(input_file, tmp_path, link_renderer):
    with pytest.raises(ValueError, match=".lnk"):
        create_shortcut(input_file,tmp_path/"not-link.json",tmp_path)
    assert link_renderer == []


def test_cli_open_passes_path_without_shell_or_script_evaluation(input_file,tmp_path,monkeypatch,capsys):
    calls = []
    monkeypatch.setattr("genshin_app.gui.launcher.run_gui", lambda data_dir,open_path=None: calls.append((data_dir,open_path)))
    assert main(["--data-dir",str(tmp_path),"gui","--open",str(input_file)]) == 0
    assert calls == [(tmp_path,input_file)]
    assert not capsys.readouterr().err


def test_cli_shortcut_envelope_and_required_inputs(input_file,tmp_path,link_renderer,capsys):
    assert main(["shortcut","create"]) == 1
    assert json.loads(capsys.readouterr().err)["code"] == 1
    assert main(["--data-dir",str(tmp_path),"shortcut","create","--input",str(input_file),"--output",str(tmp_path/"open.lnk")]) == 0
    assert json.loads(capsys.readouterr().out)["data"]["input"] == str(input_file)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows Shell Link integration")
def test_real_windows_shortcut_unicode_and_argv_round_trip(input_file,tmp_path):
    destination = tmp_path/"真实 快捷方式.lnk"
    report = create_shortcut(input_file,destination,tmp_path/"数据 & 引'号")
    assert destination.read_bytes()[:4] == b"L\x00\x00\x00"
    assert destination.stat().st_size > 76
    # The COM writer also reopens and verifies the saved target/arguments/workdir.


    command = subprocess.list2cmdline([report["launcher"], *report["argv"]])
    parse = ctypes.windll.shell32.CommandLineToArgvW
    parse.argtypes = [ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_int)]
    parse.restype = ctypes.POINTER(ctypes.c_wchar_p)
    count = ctypes.c_int()
    pointer = parse(command,ctypes.byref(count))
    try:
        assert [pointer[i] for i in range(count.value)] == [report["launcher"], *report["argv"]]
    finally:
        ctypes.windll.kernel32.LocalFree(ctypes.cast(pointer,ctypes.c_void_p))


@pytest.mark.skipif(sys.platform != "win32", reason="Windows property store integration")
def test_installed_shortcut_app_user_model_id_round_trip(tmp_path):
    from genshin_app.shortcuts import _windows_link
    from genshin_app.windows_product_registration import APP_USER_MODEL_ID
    raw = _windows_link({"target": sys.executable, "arguments": "", "working_directory": str(Path(sys.executable).parent),
                         "description": "isolated installation test", "app_user_model_id": APP_USER_MODEL_ID})
    assert raw[:4] == b"L\x00\x00\x00"  # helper verifies property-store readback before returning
