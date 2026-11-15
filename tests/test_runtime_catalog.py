import json

import pytest

from genshin_db.raw_store import RAW_SOURCES
from genshin_db.runtime_catalog import (
    RuntimeConfigError,
    enumerate_runtime_sources,
    extract_definitions,
    extract_parameter_bindings,
    extract_references,
    parse_source,
    resolve_pointer,
)
from tests.test_pipeline import fake_repo  # noqa: F401

# Fixture imports are registered by pytest and used as function arguments.
# ruff: noqa: F811


def test_profile_requires_every_source_family(fake_repo):
    manifest = enumerate_runtime_sources(fake_repo)
    assert len(manifest["files"]) == 34
    global_source = "BinOutput/Common/ConfigGlobalCombat.json"
    assert manifest["indexer_version"] == "7"
    assert manifest["files"][global_source]["family"] == "global_combat"
    assert global_source not in RAW_SOURCES
    gadget_source = "ExcelBinOutput/GadgetExcelConfigData.json"
    assert manifest["files"][gadget_source]["family"] == "gadget_excel"
    assert gadget_source not in RAW_SOURCES  # Runtime-only family; no curated raw-table duplicate.
    material_source = "ExcelBinOutput/MaterialExcelConfigData.json"
    assert manifest["files"][material_source]["family"] == "material_excel"
    assert material_source not in RAW_SOURCES
    (fake_repo / "BinOutput/Avatar/Fixture.json").unlink()
    with pytest.raises(RuntimeConfigError, match="来源族缺失"):
        enumerate_runtime_sources(fake_repo)


@pytest.mark.parametrize("text", ['{"x":1,"x":2}', '{"n":NaN}', '{"n":1e999}', '{"n":Infinity}'])
def test_reject_lossy_or_nonfinite_json(text):
    with pytest.raises(RuntimeConfigError):
        parse_source(text)


def test_preserve_zero_false_negative_and_pointer_escapes():
    original = {"a/b~": [0, False, -1, None]}
    parsed = parse_source(json.dumps(original))
    assert parsed == original
    assert resolve_pointer(parsed, "/a~1b~0/0") == 0
    with pytest.raises(ValueError):
        resolve_pointer(parsed, "/a~1b~0/00")


def test_duplicate_names_and_variants_keep_distinct_identities():
    source = [{"Default": {"abilityName": "Same"}, "Other": {"abilityName": "Same"}}]
    definitions = extract_definitions("BinOutput/Ability/Temp/a.json", "ability", source, "snapshot")
    assert len(definitions) == 2
    assert definitions[0]["definition_id"] != definitions[1]["definition_id"]


def test_parameter_binding_retains_order_and_raw_expression():
    source = {"Entry": [{"$type": "ModifyAbility", "abilityName": "A", "paramSpecial": "R", "paramDelta": "%2"},
                        {"$type": "ModifyAbility", "abilityName": "A", "paramSpecial": "R", "paramDelta": ["X", 0, "ADD"]}]}
    definition = extract_definitions("f.json", "talent", source, "s")[0]
    bindings = extract_parameter_bindings(definition)
    assert [b["operation_index"] for b in bindings] == [0, 1]
    assert json.loads(bindings[1]["raw_expression_json"]) == ["X", 0, "ADD"]
    assert len(extract_references(definition)) == 2
