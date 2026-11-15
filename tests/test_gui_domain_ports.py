"""Facade protocol and domain ownership survive internal GUI restructuring."""
from __future__ import annotations

import ast
import json
import sys
from pathlib import Path
from types import SimpleNamespace

from genshin_app.gui.api import GuiApi
from genshin_app.gui.ports import agent, database, document, equipment, release
from genshin_app.release_update_contracts import ReleaseUpdateFailure
from tests.gui_api_contract import reviewed_expected_contract

ROOT = Path(__file__).resolve().parents[1]


def test_public_bridge_signatures_match_frozen_pre_refactor_contract():
    source = ROOT / "src/genshin_app/gui/api.py"
    parsed = ast.parse(source.read_text(encoding="utf-8"))
    facade = next(row for row in parsed.body if isinstance(row, ast.ClassDef) and row.name == "GuiApi")
    actual = {row.name: {"arguments": ast.unparse(row.args),
        "returns": ast.unparse(row.returns) if row.returns else None}
        for row in facade.body if isinstance(row, ast.FunctionDef) and not row.name.startswith("_")}
    baseline = json.loads((ROOT / "tests/fixtures/gui-api-public-contract-v1.json").read_text(encoding="utf-8"))
    previous = json.loads((ROOT / "tests/fixtures/gui-api-public-contract-r9-additive.json").read_text(encoding="utf-8"))
    delta = json.loads((ROOT / "tests/fixtures/gui-api-public-contract-r16-additive.json").read_text(encoding="utf-8"))
    additions = json.loads((ROOT / "tests/fixtures/gui-api-public-contract-r18-additive.json").read_text(encoding="utf-8"))
    for category in ("added_methods", "extended_methods"):
        for name, declaration in previous[category].items():
            assert delta[category].get(name) == declaration
    expected = reviewed_expected_contract(baseline, delta)
    new_methods = reviewed_expected_contract(baseline, additions)
    assert additions["extended_methods"] == {}
    assert not expected.keys() & additions["added_methods"].keys()
    expected.update({name: new_methods[name] for name in additions["added_methods"]})
    assert actual == expected


def test_domain_ports_are_explicit_and_never_import_or_callback_the_facade(tmp_path):
    for module in (agent, database, document, equipment, release):
        parsed = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
        assert not any(isinstance(row, ast.ImportFrom) and row.module == "genshin_app.gui.api"
                       for row in ast.walk(parsed))
        assert not any(isinstance(row, ast.FunctionDef) and row.name == "__getattr__"
                       for row in ast.walk(parsed))
    untouched = tmp_path / "untouched"
    api = GuiApi(untouched, release_pair_coordinator=False)
    assert api._agent_manager is None and api._dsh_shell is None
    assert api._equipment_port._get_agent_manager.__self__ is api._agent_port
    assert api._document_port.get_display_catalog.__self__ is api._database_port
    assert api._database_port._db_path == api._db_path
    # No model, credential or filesystem activity is needed to build adapters.
    assert not untouched.exists()


def test_public_domain_methods_delegate_once_without_running_domain_transactions():
    tree = ast.parse((ROOT / "src/genshin_app/gui/api.py").read_text(encoding="utf-8"))
    facade = next(row for row in tree.body if isinstance(row, ast.ClassDef) and row.name == "GuiApi")
    domain_ports = {"_agent_port", "_document_port", "_equipment_port", "_database_port", "_release_port"}
    checked = 0
    for method in facade.body:
        if not isinstance(method, ast.FunctionDef):
            continue
        calls = [row for row in ast.walk(method) if isinstance(row, ast.Call)
                 and isinstance(row.func, ast.Attribute) and isinstance(row.func.value, ast.Attribute)
                 and isinstance(row.func.value.value, ast.Name) and row.func.value.value.id == "self"
                 and row.func.value.attr in domain_ports]
        if calls:
            assert len(calls) == 1
            assert len(method.body) == 1 and isinstance(method.body[0], ast.Return)
            assert calls[0].func.attr == method.name
            checked += 1
    assert checked >= 140


def test_offline_local_database_picker_forwards_paths_and_preserves_error(tmp_path, monkeypatch):
    api = GuiApi(tmp_path, release_pair_coordinator=False, database_setup=object())
    package, signed = tmp_path / "database.gz", tmp_path / "signed.json"
    dialog_mode = object()
    selections = iter([[str(package)], [str(signed)], None])
    seen = []

    def pick(mode, **options):
        assert mode is dialog_mode and options["allow_multiple"] is False
        seen.append(options["file_types"])
        return next(selections)

    calls = []

    def preview(database_path, document_path):
        calls.append((database_path, document_path))
        raise ReleaseUpdateFailure("数据库文件与签名清单不匹配。", "product_database_setup_state_changed")

    monkeypatch.setitem(sys.modules, "webview", SimpleNamespace(FileDialog=SimpleNamespace(OPEN=dialog_mode)))
    api._window = SimpleNamespace(create_file_dialog=pick)
    monkeypatch.setattr(api._release_port._service, "preview_local_database", preview)
    assert api.choose_local_database_release() == {"code": 1, "message": "数据库文件与签名清单不匹配。",
                                                  "reason": "product_database_setup_state_changed"}
    assert calls == [(package, signed)]
    assert api.choose_local_database_release() == {"code": 0, "data": {"cancelled": True}}
    assert len(seen) == 3 and len(calls) == 1
