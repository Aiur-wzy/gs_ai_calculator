"""Opt-in real Windows QA registration using the production installation code.

Uses a QA fixture's public trust only. Never reads a private key, accepts a
production registration name, launches an App, or downloads an artifact.
The source-based OS check is separate from frozen lifecycle/production release
qualification. Installation remains present until the explicit uninstall step.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from time import sleep
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

from installation_qa_fixture import _root

from genshin_app.installation_location import checked_path, read_registration
from genshin_app.managed_installation import ManagedInstallationCoordinator
from genshin_app.product_first_install import _PROGRAMS_FOLDER_ID
from genshin_app.product_release_index import validate_product_release_index
from genshin_app.release_pair_trust import build_trust_verifier
from genshin_app.windows_known_folders import is_windows_elevated, known_folder_path
from genshin_app.windows_product_registration import WindowsProductRegistration


def qa_registration(root):
    root, row = _root(root)
    token = hashlib.sha256(row["test_key_id"].encode()).hexdigest()[:32]
    return root, row, WindowsProductRegistration(qa_run_id=token)


def _inside(root, path):
    path = checked_path(path)
    if path == root or not path.is_relative_to(root):
        raise ValueError("qa_installation_must_stay_inside_qa_root")
    return path


def _registry_values(path):
    import winreg
    result = {}
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path) as key:
            index = 0
            while True:
                try:
                    name, value, kind = winreg.EnumValue(key, index)
                except OSError as error:
                    if error.winerror == 259:
                        break
                    raise
                result[name] = [kind, value]
                index += 1
    except FileNotFoundError:
        return None
    return result


def _production_registration_digest():
    production = WindowsProductRegistration()
    values = [_registry_values(production.product_key), _registry_values(production.uninstall_key)]
    return hashlib.sha256(json.dumps(values, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _start_apps(system):
    # Use the actual OS applications catalogue, not a list inferred from our
    # created .lnk files. Typed Start search is a later manual/CUA check.
    powershell = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    result = subprocess.run([str(powershell), "-NoProfile", "-NonInteractive", "-Command",
                             "[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new($false); @(Get-StartApps | Where-Object {$_.AppID -eq $env:GENSHIN_QA_APP_ID -or $_.AppID -eq ($env:GENSHIN_QA_APP_ID + '.Uninstall') -or $_.Name -like ('*' + $env:GENSHIN_QA_TOKEN + '*')}) | ConvertTo-Json -Compress"],
                            env={**os.environ, "GENSHIN_QA_APP_ID": system.app_user_model_id, "GENSHIN_QA_TOKEN": system.qa_run_id[:8]},
                            text=True, encoding="utf-8", errors="replace", capture_output=True, timeout=30,
                            creationflags=subprocess.CREATE_NO_WINDOW, check=True)
    value = json.loads(result.stdout) if result.stdout.strip() else []
    return value if isinstance(value, list) else [value]


def _snapshot(system, programs):
    return {"location": system.read_location(), "uninstall_values": _registry_values(system.uninstall_key),
            "start_menu_directory": str(programs), "start_menu_links": sorted(path.name for path in programs.glob("*.lnk")),
            "start_apps": _start_apps(system), "typed_windows_search": "not_verified",
            "expected_app_user_model_id": system.app_user_model_id}


def _remove_empty_qa_containers(system, programs):
    import winreg
    if not system.qa_run_id:
        raise ValueError("qa_identity_required")
    if _registry_values(system.product_key) == {}:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, system.product_key)
    if programs.is_dir() and not any(programs.iterdir()):
        programs.rmdir()


def _smoke_registration(root, system, programs):
    """OS integration only: harmless Python target, never claim App install."""
    from genshin_app.shortcuts import _windows_link
    if system.read_location() is not None or _registry_values(system.uninstall_key) is not None or programs.exists():
        raise ValueError("qa_smoke_requires_unused_registration_identity")
    install_id = uuid4().hex
    target = Path(sys.executable).resolve()
    location = _inside(root, root / "Windows 登记探针（非产品安装）")
    location.mkdir()
    programs.mkdir()
    from genshin_app.database_transaction import atomic_json
    proof_path = root / "windows-registration-smoke.json"
    if proof_path.exists():
        raise ValueError("qa_smoke_already_has_evidence")
    proof = {"contract": "genshin-windows-registration-smoke-v1", "qa_run_id": system.qa_run_id,
             "installation_id": install_id, "links": {}, "state": "creating"}
    atomic_json(proof_path, proof)
    before, body = _production_registration_digest(), {}
    try:
        with system.mutation_lock():
            system.write_location({"root": str(location), "installation_id": install_id,
                                   "state": "qa_registry_smoke_only"})
            system.register_app(root=location, installation_id=install_id, version="QA-registration-smoke",
                                executable=target)
            for prefix, role in [("", "app"), ("卸载", "uninstall")]:
                path = checked_path(programs / (prefix + system.display_name + ".lnk"))
                arguments = subprocess.list2cmdline(["-c", "print('QA registration smoke only; no App was installed or removed')"])
                raw = _windows_link({"target": str(target), "arguments": arguments,
                                     "working_directory": str(target.parent), "description": "临时系统登记机制检查，不是产品安装",
                                     "app_user_model_id": system.process_app_id(role)})
                proof["links"][path.name] = hashlib.sha256(raw).hexdigest()
                atomic_json(proof_path, proof)
                with path.open("xb") as stream:
                    stream.write(raw)
            if system.read_location()["installation_id"] != install_id:
                raise ValueError("qa_smoke_location_readback_failed")
            values = _registry_values(system.uninstall_key)
            if values["InstallationId"][1] != install_id:
                raise ValueError("qa_smoke_uninstall_readback_failed")
            # _windows_link already reopens the real WScript .lnk and checks
            # exact target/args/working directory, then sets/reads AUMID via COM.
            windows = _snapshot(system, programs)
            for _ in range(5):
                if windows["start_apps"]:
                    break
                sleep(1)
                windows["start_apps"] = _start_apps(system)
            body = {"status": "verified", "qualification": "real_windows_registration_mechanism_only",
                    "target_executable": str(target), "link_target_arguments_and_aumid_readback": "verified",
                    "windows": windows, "start_apps_catalogue": "listed" if windows["start_apps"] else "not_listed_within_probe",
                    "production_authority": False,
                    "not_verified": ["app_installation", "frozen_code", "uninstall_exe_execution", "typed_windows_search"]}
    finally:
        _smoke_cleanup(root, system, programs)
        if _production_registration_digest() != before:
            raise ValueError("qa_production_registration_unexpected_change")
    if system.read_location() is not None or _registry_values(system.uninstall_key) is not None or programs.exists():
        raise ValueError("qa_smoke_cleanup_incomplete")
    return {**body, "qa_registration_removed": True, "production_registration_unchanged": True}


def _smoke_cleanup(root, system, programs):
    from genshin_app.database_transaction import atomic_json
    proof_path = checked_path(root / "windows-registration-smoke.json")
    proof = json.loads(proof_path.read_text("utf-8"))
    names = {system.display_name + ".lnk", "卸载" + system.display_name + ".lnk"}
    if (proof.get("contract") != "genshin-windows-registration-smoke-v1"
            or proof.get("qa_run_id") != system.qa_run_id
            or not isinstance(proof.get("links"), dict) or not set(proof["links"]) <= names):
        raise ValueError("qa_smoke_cleanup_proof_invalid")
    for name, digest in proof["links"].items():
        path = checked_path(programs / name)
        if path.exists():
            if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
                raise ValueError("qa_smoke_link_changed_preserved")
            path.unlink()
    system.unregister_app(proof["installation_id"])
    system.remove_location(proof["installation_id"])
    _remove_empty_qa_containers(system, programs)
    atomic_json(proof_path, {**proof, "state": "cleaned"})
    return {"status": "cleaned", "qualification": "real_windows_registration_mechanism_only"}


def execute(args):
    if os.name != "nt" or is_windows_elevated():
        raise ValueError("qa_registration_requires_unelevated_windows_user")
    root, row, system = qa_registration(args.qa_root)
    programs = checked_path(system.programs_folder(known_folder_path(_PROGRAMS_FOLDER_ID)))
    located = system.read_location()
    target = _inside(root, located["root"] if located else root / "Windows 自选安装 QA")
    verifier = build_trust_verifier(tuple(row["trust_keys"]))
    setup = ManagedInstallationCoordinator(local=root, install_root=target, system_registration=system,
        programs_folder=lambda: programs, url="https://qa.invalid/latest.json", trust_verifier=verifier)
    before = _production_registration_digest()
    result = None
    try:
        if args.command != "status" and not args.confirm_windows_qa_registration:
            raise ValueError("qa_windows_registration_explicit_opt_in_required")
        if args.command == "smoke-registration":
            result = _smoke_registration(root, system, programs)
        elif args.command == "smoke-cleanup":
            result = _smoke_cleanup(root, system, programs)
        elif args.command == "install":
            index = checked_path(args.index)
            checked = validate_product_release_index(json.loads(index.read_text("utf-8")), channel="stable", trust_verifier=verifier)
            document = checked["pairs"][checked["combinations"][checked["latest"]]]
            if ("-qa" not in document["pair_manifest"]["app"]["app_release_id"]
                    or document["signature"]["key_id"] != row["test_key_id"]):
                raise ValueError("qa_signed_app_required")
            preview = setup.preview(local_index=index)
            result = setup.install(local_index=index, local_artifacts={"app": checked_path(args.app_archive),
                "database": checked_path(args.database_archive)}, user_confirmed=True,
                confirmation_digest=preview["confirmation_digest"])
        elif args.command == "migrate":
            destination = _inside(root, root / "Windows 迁移后 QA")
            choice = setup.migration_preview(destination)
            result = setup.migrate(destination, choice["confirmation_digest"], user_confirmed=True)
            target = destination
        elif args.command == "uninstall":
            if not located:
                raise ValueError("qa_registered_installation_missing")
            choice = setup.uninstall_preview(keep_user_data=False)
            result = setup.uninstall(choice["confirmation_digest"], user_confirmed=True, keep_user_data=False)
            _remove_empty_qa_containers(system, programs)
        elif args.command != "status":
            raise ValueError("qa_command_invalid")
    finally:
        if _production_registration_digest() != before:
            raise ValueError("qa_production_registration_unexpected_change")
    status = _snapshot(system, programs)
    if (target / "installation.json").is_file():
        status["installation_state"] = read_registration(target)["installation_state"]
    receipt = {"contract": "genshin-real-windows-qa-registration-v1", "production_authority": False,
               "qualification": "source_code_with_real_windows_registration", "command": args.command,
               "qa_run_id": system.qa_run_id, "display_name": system.display_name, "result": result,
               "production_registration_unchanged": True, "windows": status,
               "not_verified": ["production_name_installation", "production_signing", "typed_windows_search", "frozen_lifecycle"]}
    report = root / f"windows-registration-{args.command}-{datetime.now(UTC).strftime('%Y%m%dT%H%M%S%f')}.json"
    report.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {**receipt, "report": str(report)}


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--qa-root", type=Path, required=True, help="New QA signing fixture root; no production private key is read")
    result.add_argument("--confirm-windows-qa-registration", action="store_true", help="Explicitly allow only the generated QA HKCU/Start Menu identity")
    commands = result.add_subparsers(dest="command", required=True)
    install = commands.add_parser("install", help="Install local QA-signed App/DB and register only its generated QA name")
    install.add_argument("--index", type=Path, required=True)
    install.add_argument("--app-archive", type=Path, required=True)
    install.add_argument("--database-archive", type=Path, required=True)
    commands.add_parser("status", help="Read actual QA registration and Windows Start applications catalogue")
    commands.add_parser("migrate", help="Move the QA installation to another fixed child of the same QA root")
    commands.add_parser("uninstall", help="Remove QA App/DB/user files and only its owned registration and links")
    commands.add_parser("smoke-registration", help="Create/read/remove real QA registry and links using a harmless Python target; does not install or launch App")
    commands.add_parser("smoke-cleanup", help="Finish interrupted OS smoke cleanup using its exact ownership record; never remove an installation")
    return result


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(execute(parser().parse_args()), ensure_ascii=False, indent=2))
