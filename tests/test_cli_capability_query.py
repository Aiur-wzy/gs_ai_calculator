"""CLI selection returns real contracts and preserves local reference targets."""
import json

from genshin_app.cli import main
from genshin_app.cli_capability_query import query_capabilities


def test_actual_runtime_contract_can_be_read_without_database(capsys, tmp_path):
    assert main(["--data-dir", str(tmp_path), "capabilities", "--section", "runtime_request_contract",
                 "--pointer", "/actions/mechanism_catalog/input_schema/properties/page_size"]) == 0
    result = json.loads(capsys.readouterr().out)["data"]
    assert result["data"] == {"type": "integer", "minimum": 1, "maximum": 50, "default": 20}
    assert result["complete"] and result["authority"] == "description_only"
    assert not (tmp_path / "genshin.db").exists()


def test_actual_command_selection_and_pointer(capsys):
    assert main(["capabilities", "--command", "db runtime", "--pointer", "/args"]) == 0
    result = json.loads(capsys.readouterr().out)["data"]
    assert any(row["name"] == "--input" for row in result["data"])
    assert result["source_pointer"].endswith("/args")


def test_local_reference_transitive_closure_and_cycle_are_preserved_without_inlining():
    schema = {"$schema": "https://json-schema.org/draft/2020-12/schema", "properties": {
        "thing": {"$ref": "#/$defs/a"}}, "$defs": {"a": {"$ref": "#/$defs/b"}, "b": {"$ref": "#/$defs/a"}}}
    document = {"cli": "genshin_app", "version": "test", "plan_contract": {"schema": schema}}
    selected = query_capabilities(document, section="plan_contract", pointer="/schema/properties/thing")
    assert set(selected["local_references"]) == {"#/$defs/a", "#/$defs/b"}
    assert selected["data"] == schema["properties"]["thing"]
    assert selected["unresolved_or_external_references"] == []


def test_bad_queries_use_repairable_error_envelope(capsys):
    for arguments in (["--command", "db runtime", "--section", "plan_contract"],
                      ["--pointer", "/bad"], ["--pointer", "/bad~7"],
                      ["--command", "missing command"]):
        assert main(["capabilities", *arguments]) == 1
        result = json.loads(capsys.readouterr().err)
        assert result["category"] == "request_error"
        assert "system_fault" not in result
