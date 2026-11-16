from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from genshin_app import version
from genshin_app.windows_product_registration import (
    APP_USER_MODEL_ID,
    DISPLAY_NAME,
    PRODUCT_KEY,
    UNINSTALL_KEY,
    WindowsProductRegistration,
    current_build_registration,
)


class Key:
    def __init__(self, path, values):
        self.path, self.values = path, values
    def __enter__(self):
        return self
    def __exit__(self, *_):
        pass


@pytest.fixture
def registry(monkeypatch):
    values, calls = {}, []
    def create(_hive, path, *_args):
        calls.append(path)
        return Key(path, values.setdefault(path, {}))
    def open_key(_hive, path, *_args):
        calls.append(path)
        if path not in values:
            raise FileNotFoundError(path)
        return Key(path, values[path])
    fake = SimpleNamespace(HKEY_CURRENT_USER="HKCU", REG_SZ=1, REG_DWORD=4, KEY_WRITE=2, KEY_SET_VALUE=2,
        CreateKeyEx=create, OpenKey=open_key, FlushKey=lambda _: None,
        SetValueEx=lambda key, name, _, kind, value: key.values.__setitem__(name, (value, kind)),
        QueryValueEx=lambda key, name: key.values[name],
        DeleteValue=lambda key, name: key.values.pop(name), DeleteKey=lambda _, path: values.pop(path))
    monkeypatch.setitem(sys.modules, "winreg", fake)
    return values, calls


def test_production_defaults_do_not_change():
    system = WindowsProductRegistration()
    assert (system.product_key, system.uninstall_key, system.display_name, system.app_user_model_id) == (
        PRODUCT_KEY, UNINSTALL_KEY, DISPLAY_NAME, APP_USER_MODEL_ID)
    assert system.programs_folder(Path("programs")) == Path("programs")
    assert system.process_app_id("uninstall") == APP_USER_MODEL_ID + ".Uninstall"
    assert system.process_app_id("setup") == APP_USER_MODEL_ID + ".Setup"
    with pytest.raises(ValueError, match="role_invalid"):
        system.process_app_id("arbitrary-app-id")


@pytest.mark.parametrize("bad", ["Stable", "../Stable", "a" * 31, "A" * 32, 42, "a" * 32 + "\\Stable"])
def test_qa_cannot_choose_keys_or_production_names(bad):
    with pytest.raises(ValueError, match="identity_invalid"):
        WindowsProductRegistration(qa_run_id=bad)


def test_same_os_operations_only_touch_generated_qa_keys(registry, tmp_path):
    values, calls = registry
    system = WindowsProductRegistration(qa_run_id="a" * 32)
    location = {"root": str(tmp_path), "installation_id": "qa-installation"}
    system.write_location(location)
    assert system.read_location() == location
    system.register_app(root=tmp_path, installation_id="qa-installation", version="0.46.0-qa",
                        executable=tmp_path / "maintenance/GenshinCalcSetup.exe")
    assert values[system.uninstall_key]["DisplayName"][0] == system.display_name
    assert values[system.uninstall_key]["UninstallString"][0] == subprocess.list2cmdline([
        str(tmp_path / "maintenance/GenshinCalcSetup.exe"), "--uninstall", "--install-root", str(tmp_path)])
    system.unregister_app("other-owner")
    assert system.uninstall_key in values
    system.unregister_app("qa-installation")
    system.remove_location("other-owner")
    assert system.read_location() == location
    system.remove_location("qa-installation")
    assert values[system.product_key] == {}
    assert set(calls) == {system.product_key, system.uninstall_key}
    assert PRODUCT_KEY not in calls and UNINSTALL_KEY not in calls
    assert system.programs_folder(tmp_path).name == "GenshinCalc-QA-" + "a" * 32


def test_only_explicit_qa_build_uses_qa_identity(monkeypatch):
    monkeypatch.delattr(version, "INSTALLATION_QA_RUN_ID", raising=False)
    assert current_build_registration().qa_run_id is None
    monkeypatch.setattr(version, "INSTALLATION_QA_RUN_ID", "b" * 32, raising=False)
    with pytest.raises(ValueError, match="build_identity_invalid"):
        current_build_registration()
    monkeypatch.setattr(version, "APP_RELEASE_ID", "app-0.46.0-r21-qa")
    monkeypatch.setattr(version, "APP_DIR_NAME", "GenshinCalcQA")
    assert current_build_registration().app_user_model_id == "Aiur.GenshinCalc.QA." + "b" * 32
