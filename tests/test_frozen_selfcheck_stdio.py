"""Actual CPython subprocess audit shapes and the narrow standard-NUL carveout."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlencode

import pytest

from genshin_app.frozen_selfcheck_isolation import (
    SelfcheckContext,
    SelfcheckError,
    clean_environment,
)


def _context(tmp_path):
    package = tmp_path / "package with spaces"
    package.mkdir()
    sandbox = tmp_path / "sandbox with spaces"
    context = SelfcheckContext.create(sandbox, sandbox / "report.json",
        package_root=package, executable=package / "app.exe")
    return context


def _commands(context):
    # These are the concrete caller contracts, not the implementation helper.
    return [
        [str(context.node), "--import", context.entry.as_uri() + "?" + urlencode({
             "context": (context.root / "production-provider-context.json").as_uri()}),
         "--expose-internals",
         str(context.node.parent / "src/native-dsh-runtime-entry.mjs"),
         str(context.root / "data/native_agent_sessions_v2/dsh-production-v1"),
         str(context.root / "data/native_agent_sessions_v2/dsh-file-preview"),
         str(context.package_root / "_internal/agent-client-assets")],
        [str(context.executable), "internal", "verify-frozen-readback", "--sandbox-root",
         str(context.root), "--report", str(context.root / "fresh-readback.json")],
    ]


@pytest.mark.parametrize("command_index", [0, 1])
@pytest.mark.parametrize("explicit_executable", [False, True])
@pytest.mark.parametrize("representation", ["list", "tuple", "windows_command_line"])
def test_only_two_complete_popen_commands_accept_real_audit_shapes(
    tmp_path, command_index, explicit_executable, representation,
):
    context = _context(tmp_path)
    command = _commands(context)[command_index]
    executable = command[0] if explicit_executable else None
    cwd = context.node.parent if command_index == 0 else context.root
    arguments = tuple(command) if representation == "tuple" else command
    if representation == "windows_command_line":
        arguments = subprocess.list2cmdline(command)
        if os.name != "nt":
            with pytest.raises(SelfcheckError):
                context.audit("subprocess.Popen", (executable, arguments, cwd, {}))
            return
    context.audit("subprocess.Popen", (executable, arguments, cwd, {}))
    context.assert_normal_clean()


@pytest.mark.parametrize("explicit_executable", [False, True])
@pytest.mark.parametrize("representation", ["list", "tuple", "windows_command_line"])
def test_required_runtime_flag_cannot_be_omitted_in_any_audit_shape(
    tmp_path, explicit_executable, representation,
):
    context = _context(tmp_path)
    command = _commands(context)[0]
    # Retain an independent concrete caller contract: do not derive this
    # fixture from the same production helper that the guard is checking.
    command.remove("--expose-internals")
    arguments = tuple(command) if representation == "tuple" else command
    if representation == "windows_command_line":
        arguments = subprocess.list2cmdline(command)
    executable = command[0] if explicit_executable else None
    with pytest.raises(SelfcheckError) as caught:
        context.audit("subprocess.Popen", (executable, arguments, context.node.parent, {}))
    assert caught.value.reason == "selfcheck_process_denied"


@pytest.mark.parametrize("mutation", ["extra", "reorder", "wrong_binary", "wrong_entry",
                                     "prefix", "shell_suffix", "wrong_explicit", "wrong_cwd",
                                     "blank", "quoted_second_binary", "trailing_space"])
@pytest.mark.parametrize("command_index", [0, 1])
def test_popen_near_misses_never_start_an_unreviewed_process(tmp_path, mutation, command_index):
    context = _context(tmp_path)
    command = _commands(context)[command_index]
    executable, cwd = None, context.node.parent if command_index == 0 else context.root
    if mutation == "extra":
        command.append("--extra")
    elif mutation == "reorder":
        command[2], command[3] = command[3], command[2]
    elif mutation == "wrong_binary":
        command[0] = str(context.package_root / "GenshinCalcGUI.exe")
    elif mutation == "wrong_entry":
        command[1] += ".wrong"
    elif mutation == "prefix":
        command = command[:3]
    elif mutation == "wrong_explicit":
        executable = str(context.package_root / "other.exe")
    elif mutation == "wrong_cwd":
        cwd = context.root / "different"
    arguments = subprocess.list2cmdline(command) if os.name == "nt" else command
    if mutation == "shell_suffix":
        arguments = subprocess.list2cmdline(command) + " & ignored.exe"
    elif mutation == "blank":
        arguments = "   "
    elif mutation == "quoted_second_binary":
        arguments = subprocess.list2cmdline(command) + ' "unexpected second.exe"'
    elif mutation == "trailing_space":
        arguments = subprocess.list2cmdline(command) + " "
    with pytest.raises(SelfcheckError):
        context.audit("subprocess.Popen", (executable, arguments, cwd, {}))


def test_only_actual_standard_windows_devnull_open_is_exempt(tmp_path):
    context = _context(tmp_path)
    if os.name == "nt":
        context.audit("open", (os.devnull, None, os.O_RDWR | os.O_NOINHERIT))
        context.assert_normal_clean()
    else:
        with pytest.raises(SelfcheckError):
            context.audit("open", (os.devnull, None, os.O_RDWR))


@pytest.mark.parametrize("spelling", ["NUL", "nul.txt", "nul ", "nul:", r"\\.\NUL",
                                      r"\\.\PhysicalDrive0", "CON", "LPT1", "absolute",
                                      "absolute_str", "path_object"])
def test_null_device_aliases_and_real_paths_get_no_device_exception(tmp_path, spelling):
    context = _context(tmp_path)
    value = tmp_path / "nul" if spelling == "absolute" else spelling
    if spelling == "absolute_str":
        value = str(tmp_path / "nul")
    if spelling == "path_object":
        value = Path(os.devnull)
    flags = os.O_RDWR | getattr(os, "O_NOINHERIT", 0)
    with pytest.raises(SelfcheckError):
        context.audit("open", (value, None, flags))


@pytest.mark.parametrize("mode,extra,base", [("w", 0, "readwrite"), ("r+", 0, "readwrite"),
    (None, "O_CREAT", "readwrite"), (None, "O_TRUNC", "readwrite"),
    (None, "O_APPEND", "readwrite"), (None, "O_BINARY", "readwrite"),
    (None, 0, "readonly"), (None, 0, "writeonly"), (None, 0, "inheritable"),
    (None, 0, "bool")])
def test_null_open_other_modes_and_flags_are_not_exempt(tmp_path, mode, extra, base):
    context = _context(tmp_path)
    flags = os.O_RDWR | getattr(os, "O_NOINHERIT", 0)
    if base == "readonly":
        flags = getattr(os, "O_NOINHERIT", 0)
    elif base == "writeonly":
        flags = os.O_WRONLY | getattr(os, "O_NOINHERIT", 0)
    elif base == "inheritable":
        flags = os.O_RDWR
    elif base == "bool":
        flags = True
    if isinstance(extra, str):
        flags |= getattr(os, extra, 0x10000000)
    with pytest.raises(SelfcheckError):
        context.audit("open", (os.devnull, mode, flags))


def test_actual_disposable_python_audit_accepts_devnull_and_retains_guards(tmp_path):
    repository = Path(__file__).resolve().parents[1]
    outside = tmp_path / "outside-sentinel.txt"
    outside.write_text("keep", encoding="utf-8")
    script = r'''
import json,os,socket,subprocess,sys
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from genshin_app.frozen_selfcheck_isolation import SelfcheckContext,SelfcheckError,DeniedCredentials,clean_environment
root,outside=map(Path,sys.argv[2:])
context=SelfcheckContext.create(root,root/'report.json',package_root=Path(sys.executable).parent,executable=sys.executable)
context.prepare()
(root/'internal').write_text('raise SystemExit(0)\n',encoding='utf-8')
command=[sys.executable,'internal','verify-frozen-readback','--sandbox-root',str(root),'--report',str(root/'fresh-readback.json')]
result=subprocess.run(command,cwd=root,env=clean_environment(root),stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0),check=False,timeout=10)
if result.returncode or result.stderr: raise RuntimeError('offline_source_child_failed')
denied=[]
with context.negative_probe():
 for action in (lambda:outside.write_text('forbidden',encoding='utf-8'),lambda:outside.read_text(encoding='utf-8'),lambda:DeniedCredentials(context).status(),lambda:subprocess.run([*command,'--extra'],cwd=root,env=clean_environment(root),stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,check=False)):
  try: action()
  except SelfcheckError as error: denied.append(error.reason)
  else: raise RuntimeError('guard_was_not_enforced')
 with socket.socket() as connection:
  try: connection.connect(('127.0.0.1',1))
  except SelfcheckError as error: denied.append(error.reason)
  else: raise RuntimeError('network_guard_was_not_enforced')
context.assert_normal_clean()
print(json.dumps({'status':'verified_source_audit_probe','child_exit_code':result.returncode,'denied':denied,'counters':context.counters,'environment_keys':sorted(os.environ),'actual_frozen_tested':False}))
'''
    environment = clean_environment(tmp_path / "parent-environment")
    for key in ("TEMP", "TMP", "APPDATA", "LOCALAPPDATA", "USERPROFILE", "HOME"):
        Path(environment[key]).mkdir(parents=True, exist_ok=True)
    child = subprocess.run([sys.executable, "-c", script, str(repository / "src"),
        str(tmp_path / "audit-root"), str(outside)], cwd=repository, env=environment,
        capture_output=True, text=True, encoding="utf-8", check=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), timeout=30)
    report = json.loads(child.stdout)
    assert report["status"] == "verified_source_audit_probe"
    assert report["child_exit_code"] == 0 and report["actual_frozen_tested"] is False
    assert report["counters"]["normal"] == {}
    assert report["counters"]["negative"] == {
        "outside_write": 1, "outside_read": 1, "credential": 1, "process": 1, "network": 1,
    }
    assert outside.read_text(encoding="utf-8") == "keep"
    assert not {"DEEPSEEK_API_KEY", "OPENAI_API_KEY", "HTTP_PROXY", "HTTPS_PROXY", "PYTHONPATH"}.intersection(
        report["environment_keys"])
