"""Attack metadata projection uses real structural excerpts, not a character switch."""
from __future__ import annotations

import copy
import hashlib
import json
from importlib import resources

import pytest

from genshin_db.equipment_queries import equipment_read_session
from genshin_db.runtime_attack_metadata import (
    ATTACK_METADATA_LIMITS,
    get_attack_metadata_context,
    project_attack_metadata,
)
from genshin_db.runtime_catalog import RuntimeConfigError, fingerprint, resolve_pointer
from genshin_db.runtime_queries import query_runtime, runtime_capabilities
from tests import fixture_candidate_builder as fixture_builder
from tests.test_pipeline import fake_repo, stub_fetcher  # noqa: F401

# ruff: noqa: F811
SOURCE = "BinOutput/Ability/Temp/AvatarAbilities/ConfigAbility_Avatar_MetadataFixture.json"
TALENT = "BinOutput/Talent/AvatarTalents/ConfigTalent_MetadataFixture.json"
ATTENUATION = "ExcelBinOutput/AttackAttenuationExcelConfigData.json"
COMMIT = "224708edc8056f27e9ffc4eacabe797d75274636"


def recipe():
    return json.loads(resources.files("genshin_runtime").joinpath("resources/attack_metadata_profiles.json").read_text("utf8"))


@pytest.fixture
def metadata_db(tmp_path, fake_repo, stub_fetcher):
    stub_fetcher["commit"] = COMMIT
    resource = recipe()

    def write(path, value):
        target = fake_repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(value, ensure_ascii=False), "utf8")

    write(SOURCE, [{"Default": c["raw"]} for c in resource["evidence"]["contexts"]])
    operations = [p["operation_raw"] for p in resource["evidence"]["parameter_links"]]
    for context, symbol in [(resource["evidence"]["contexts"][3], "Elemental_Art_H_Damage"),
                            (resource["evidence"]["contexts"][4], "ElementalArt_Damage_Percentage")]:
        operations.append({"$type": "BIAHEMMGABC", "GBPFBNHLOFD": True, "IIBMNPFLNMJ": context["ability_name"],
                           "PCABAADALND": "%1", "PEOJKHKHAKL": symbol})
    write(TALENT, {"MetadataFixtureSkill": operations})
    path = "ExcelBinOutput/AvatarSkillDepotExcelConfigData.json"
    rows = json.loads((fake_repo / path).read_text("utf8"))
    rows[0]["skills"][1] = 90012
    write(path, rows)
    path = "ExcelBinOutput/AvatarSkillExcelConfigData.json"
    rows = json.loads((fake_repo / path).read_text("utf8"))
    rows.append({"id": 90012, "proudSkillGroupId": 992, "nameTextMapHash": 500, "descTextMapHash": 501})
    write(path, rows)
    write("ExcelBinOutput/ProudSkillExcelConfigData.json", [{"proudSkillId": 99210, "proudSkillGroupId": 992,
        "level": 10, "paramList": [4.305600166320801, 10, 4.694399833679199], "paramDescList": [501], "openConfig": "MetadataFixtureSkill"}])
    path = "TextMap/TextMap_MediumCHS.json"
    texts = json.loads((fake_repo / path).read_text("utf8"))
    texts.update({"500": "工程技能", "501": "短按伤害|{param1:P}；长按伤害|{param3:P}"})
    write(path, texts)
    write(ATTENUATION, [{"group": "default", "resetCycle": 2.5, "durabilitySequence": [1, 0, 0, 1, 0, 0],
                         "damageSequence": [1] * 6, "enbreakSequence": [1] * 6}])
    fixture_builder.build_database(tmp_path)
    return tmp_path / "genshin.db"


def query(db, **extra):
    return query_runtime(db, {"action": "attack_metadata_profile", "character_id": 90000001, "slot": "skill", **extra})


def candidate(db, ability="Avatar_Ayaka_Icespine_Strike_New"):
    return next(c for c in query(db)["candidates"] if c["ability_name"] == ability and c["status"] == "ready")


def change(db, repo, path, apply):
    target = repo / path
    raw = json.loads(target.read_text("utf8"))
    apply(raw)
    target.write_text(json.dumps(raw, ensure_ascii=False), "utf8")
    fixture_builder.build_database(db.parent, force=True)


def test_real_structural_metadata_and_holdout_have_no_character_id_branch(metadata_db):
    before = hashlib.sha256(metadata_db.read_bytes()).hexdigest()
    found = query(metadata_db)
    assert found["status"] == "pending" and found["selected_candidate"] is None
    ready = [c for c in found["candidates"] if c["status"] == "ready"]
    assert len(ready) == 3
    assert sorted((c["projection"]["element"]["value"], c["projection"]["gauge"]["value"]) for c in ready) == [("anemo", 1), ("anemo", 2), ("cryo", 2)]
    for c in ready:
        selected = query(metadata_db, candidate_id=c["candidate_id"])
        assert selected["status"] == "ready" and selected["whole_source_status"] == "partial"
        assert selected["source_fingerprint"] == fingerprint(selected["semantic_dependency"])
        assert selected["action_dispatch"] == "pending"
        assert selected["selected_candidate"]["projection"]["gauge"]["tax_applied"] is False
    assert next(c for c in ready if c["ability_name"].endswith("FlyAway"))["projection"]["parameter"]["param_index"] == 2
    assert len(next(c for c in ready if c["ability_name"].endswith("EmptyJump"))["branches"]) == 2
    assert hashlib.sha256(metadata_db.read_bytes()).hexdigest() == before


def test_raw_evidence_pointers_and_hashes_resolve(metadata_db, fake_repo):
    out = query(metadata_db, candidate_id=candidate(metadata_db)["candidate_id"])
    for node in out["source_graph"]:
        src = node["source"]
        raw = json.loads((fake_repo / src["source_path"]).read_text("utf8"))
        assert resolve_pointer(raw, src["json_pointer"]) == node["raw"]
        assert fingerprint(node["raw"]) == src["node_sha256"]
    projection = out["selected_candidate"]["projection"]
    assert projection["parameter"]["parameter_descriptions"]["matched"][0]["text"].startswith("短按伤害")
    for field in ("element", "gauge"):
        src = projection[field]["source"]
        raw = json.loads((fake_repo / src["source_path"]).read_text("utf8"))
        assert fingerprint(resolve_pointer(raw, src["json_pointer"])) == src["node_sha256"]


def test_missing_attenuation_tag_is_explicit_pending_and_never_uses_attack_tag(metadata_db):
    projection = candidate(metadata_db)["projection"]
    assert projection["attack_tag"]["status"] == "observed"
    assert projection["attack_tag"]["value"] == "Elemental_Art"
    assert projection["attack_tag"]["application_identity"] is False
    assert projection["application_identity"]["status"] == "pending"
    assert projection["application_identity"]["tag"] is None
    assert projection["application_identity"]["evidence"] == "attenuation_tag_missing"
    assert "具体攻击段" in projection["application_identity"]["next_action"]


def test_attenuation_tag_is_projected_as_sourced_application_identity(metadata_db, fake_repo):
    def add_tag(rows):
        info = rows[0]["Default"]["GPPBBDEICKP"]["Icespine_Strike_Delay"]["HHDOFNHCIAE"][0][
            "IDCIIIFMDCM"]["LGHDGGJIJOK"]
        info["FHNDLIJJDEA"] = "Fixture_Icespine_Counter"

    change(metadata_db, fake_repo, SOURCE, add_tag)
    selected = candidate(metadata_db)
    identity = selected["projection"]["application_identity"]
    assert identity["status"] == "ready"
    assert identity["tag"] == "Fixture_Icespine_Counter"
    assert identity["evidence"] == "upstream_attenuation_tag"
    raw = json.loads((fake_repo / identity["source"]["source_path"]).read_text("utf8"))
    assert resolve_pointer(raw, identity["source"]["json_pointer"]) == identity["tag"]
    assert selected["projection"]["attack_tag"]["value"] == "Elemental_Art"


def test_composite_and_antifire_holdouts_stay_pending(metadata_db):
    out = query(metadata_db)
    rejected = [c for c in out["candidates"] if c["status"] == "pending"]
    assert len(rejected) == 5
    assert any(i["code"] == "attack_composite_damage_unsupported" for c in rejected for i in c["issues"])
    assert all(c["projection"]["attenuation"]["profile"] is None for c in rejected)


@pytest.mark.parametrize("mutation", [
    lambda mod: mod.update(unknown_control=True),
    lambda mod: mod["HHDOFNHCIAE"].append(copy.deepcopy(mod["HHDOFNHCIAE"][0])),
    lambda mod: mod["HHDOFNHCIAE"][0]["IDCIIIFMDCM"]["LGHDGGJIJOK"]["EHKJIDFINEK"].pop("DBGOKAKAHFP"),
    lambda mod: mod["HHDOFNHCIAE"][0]["IDCIIIFMDCM"]["LGHDGGJIJOK"]["EHKJIDFINEK"].update(DBGOKAKAHFP=True),
    lambda mod: mod["HHDOFNHCIAE"][0]["IDCIIIFMDCM"]["LGHDGGJIJOK"]["EHKJIDFINEK"].update(DBGOKAKAHFP=37.5),
    lambda mod: mod["HHDOFNHCIAE"][0]["IDCIIIFMDCM"]["LGHDGGJIJOK"]["EHKJIDFINEK"].update(DBGOKAKAHFP=10**400),
    lambda mod: mod["HHDOFNHCIAE"][0]["IDCIIIFMDCM"]["LGHDGGJIJOK"]["EHKJIDFINEK"].update(EPPIDAILPBN={"dynamic": "Ice"}),
    lambda mod: mod["HHDOFNHCIAE"][0]["IDCIIIFMDCM"]["LGHDGGJIJOK"]["EHKJIDFINEK"].update(EMILGODPBIG=["FIGHT_PROP_MAX_HP", 1, "MUL"]),
])
def test_full_parent_mutations_and_missing_gauge_never_default(metadata_db, fake_repo, mutation):
    ident = candidate(metadata_db)["candidate_id"]
    change(metadata_db, fake_repo, SOURCE, lambda rows: mutation(rows[0]["Default"]["GPPBBDEICKP"]["Icespine_Strike_Delay"]))
    assert query(metadata_db, candidate_id=ident)["status"] == "pending"


def test_supported_leaf_value_change_is_not_a_sample_sha_allowlist(metadata_db, fake_repo):
    first = candidate(metadata_db)
    def mutate(rows):
        rows[0]["Default"]["GPPBBDEICKP"]["Icespine_Strike_Delay"]["HHDOFNHCIAE"][0]["IDCIIIFMDCM"]["LGHDGGJIJOK"]["EHKJIDFINEK"]["DBGOKAKAHFP"] = 25
    change(metadata_db, fake_repo, SOURCE, mutate)
    second = query(metadata_db, candidate_id=first["candidate_id"])
    assert second["status"] == "ready"
    assert second["selected_candidate"]["projection"]["gauge"]["value"] == 1
    assert second["source_fingerprint"] != first["source_fingerprint"]


def test_dynamic_element_source_can_project_pyro_without_character_branch(metadata_db, fake_repo):
    first = candidate(metadata_db)

    def mutate(rows):
        prop = rows[0]["Default"]["GPPBBDEICKP"]["Icespine_Strike_Delay"]["HHDOFNHCIAE"][0][
            "IDCIIIFMDCM"]["LGHDGGJIJOK"]["EHKJIDFINEK"]
        prop["EPPIDAILPBN"] = "Fire"

    change(metadata_db, fake_repo, SOURCE, mutate)
    second = query(metadata_db, candidate_id=first["candidate_id"])
    assert second["status"] == "ready"
    assert second["selected_candidate"]["projection"]["element"]["value"] == "pyro"


def test_exact_nonzero_constellation_is_discoverable_instead_of_c0_gate(metadata_db):
    found = query(metadata_db, constellation=1, talent_level=10)
    assert found["selection"]["constellation"] == 1
    assert not any(issue["code"] == "attack_selection_scope_unsupported" for issue in found["issues"])
    assert any(row["status"] == "ready" for row in found["candidates"])


@pytest.mark.parametrize("mutation", [lambda ops: ops[0].update(GBPFBNHLOFD=False),
    lambda ops: ops[0].update(unknown=True), lambda ops: ops.append(copy.deepcopy(ops[0])),
    lambda ops: ops[0].update(PCABAADALND="%1+2")])
def test_full_operation_and_duplicate_writers_are_guarded(metadata_db, fake_repo, mutation):
    ident = candidate(metadata_db)["candidate_id"]
    change(metadata_db, fake_repo, TALENT, lambda data: mutation(data["MetadataFixtureSkill"]))
    assert query(metadata_db, candidate_id=ident)["status"] == "pending"


def test_same_selection_two_candidates_share_facts_and_attenuation(metadata_db, monkeypatch):
    import genshin_db.runtime_action_parameter as parameter
    import genshin_db.runtime_attenuation as attenuation
    found = [c for c in query(metadata_db)["candidates"] if c["status"] == "ready"]
    original_p, original_a, counts = parameter.get_action_parameter_context, attenuation.get_runtime_attenuation_profile, [0, 0]
    def p(*a, **kw):
        counts[0] += 1
        return original_p(*a, **kw)
    def q(*a, **kw):
        counts[1] += 1
        return original_a(*a, **kw)
    monkeypatch.setattr(parameter, "get_action_parameter_context", p)
    monkeypatch.setattr(attenuation, "get_runtime_attenuation_profile", q)
    with equipment_read_session(metadata_db):
        for c in [v for v in found if "Kazuha" in v["ability_name"]]:
            context = get_attack_metadata_context(metadata_db, character_id=90000001, slot="skill", candidate_id=c["candidate_id"])
            assert project_attack_metadata(context)["status"] == "ready"
        context.public_projection["snapshot"]["counts"]["runtime_definition"] = -1
        context.parameter_context["facts"]["snapshot"]["counts"]["runtime_definition"] = -2
        assert query(metadata_db)["snapshot"]["counts"]["runtime_definition"] > 0
    assert counts == [1, 1]


@pytest.mark.parametrize("fields", [{"character_id": True}, {"slot": []}, {"candidate_id": []},
    {"talent_level": None}, {"candidate_id": " "}, {"extra": True}, {"constellation": 7}])
def test_invalid_selection_is_rejected_before_read(monkeypatch, fields):
    import genshin_db.runtime_queries as runtime
    monkeypatch.setattr(runtime, "_read", lambda *a, **kw: pytest.fail("invalid selection opened DB"))
    with pytest.raises(ValueError):
        query("does-not-exist", **fields)


def test_unknown_candidate_never_falls_back_and_caps_batch_are_public(metadata_db):
    assert query(metadata_db, candidate_id="other-selection")["status"] == "pending"
    caps = runtime_capabilities()
    assert "attack_metadata_profile" in caps["batch"]["actions"]
    assert caps["attack_metadata_profile"]["whole_source_status"] == "partial"
    single = query(metadata_db)
    batch = query_runtime(metadata_db, {"action": "batch", "items": [{"id": "metadata", "request": {
        "action": "attack_metadata_profile", "character_id": 90000001, "slot": "skill"}}]})
    assert batch["items"][0]["data"] == single


def test_public_byte_and_tree_limits_refuse_not_truncate(metadata_db, monkeypatch):
    monkeypatch.setitem(ATTACK_METADATA_LIMITS, "max_bytes", 100)
    with pytest.raises(RuntimeConfigError) as error:
        query(metadata_db)
    assert error.value.reason == "response_too_large"


@pytest.mark.parametrize("field,value", [("unknown_control", True),
    ("FBHJJNMDIPC", [{"$type": "UnreviewedProviderRewrite", "element": "Ice"}])])
def test_unreviewed_root_or_sibling_mixin_does_not_hide_behind_partial(metadata_db, fake_repo, field, value):
    ident = candidate(metadata_db)["candidate_id"]
    change(metadata_db, fake_repo, SOURCE, lambda rows: rows[0]["Default"].update({field: value}))
    selected = query(metadata_db, candidate_id=ident)
    assert selected["status"] == "pending"
    assert any(i["code"] == "attack_context_structure_unsupported" for i in selected["selected_candidate"]["issues"])


def test_semantic_source_excludes_snapshot_display_fields(metadata_db, monkeypatch):
    from contextlib import contextmanager

    import genshin_db.runtime_queries as runtime
    selected_id = candidate(metadata_db)["candidate_id"]
    first = query(metadata_db, candidate_id=selected_id)
    original = runtime._read
    @contextmanager
    def altered(*args, **kw):
        with original(*args, **kw) as (conn, state):
            modified = copy.deepcopy(state)
            modified.update(built_at="changed-display-only", counts={"arbitrary": 1})
            yield conn, modified
    monkeypatch.setattr(runtime, "_read", altered)
    second = query(metadata_db, candidate_id=selected_id)
    assert second["snapshot"]["built_at"] == "changed-display-only"
    assert second["source_fingerprint"] == first["source_fingerprint"]


def test_unrelated_unresolved_candidate_preserved_without_blocking_selected(metadata_db, monkeypatch):
    import genshin_db.runtime_action_parameter as parameters
    ident = candidate(metadata_db)["candidate_id"]
    original = parameters.get_action_parameter_context
    def altered(*args, **kwargs):
        context = original(*args, **kwargs)
        context["facts"]["parameter_bindings"].append({"role": "skill_level", "operation_pointer": "/other/0",
            "target": {"resolution_status": "missing", "candidate_ids": []}})
        return context
    monkeypatch.setattr(parameters, "get_action_parameter_context", altered)
    selected = query(metadata_db, candidate_id=ident)
    assert selected["status"] == "ready" and selected["whole_source_status"] == "partial"
    assert any(i["code"] == "attack_ability_ambiguous" and i["blocks_selected"] is False for i in selected["issues"])


def test_huge_parameter_scalar_is_pending_not_float_overflow(metadata_db, fake_repo):
    ident = candidate(metadata_db)["candidate_id"]
    change(metadata_db, fake_repo, "ExcelBinOutput/ProudSkillExcelConfigData.json", lambda rows: rows[0]["paramList"].__setitem__(0, 10**400))
    out = query(metadata_db, candidate_id=ident)
    assert out["status"] == "pending"
    assert out["selected_candidate"]["projection"]["parameter"]["raw_input"] == 10**400


def test_selection_diagnostic_is_counted_at_final_projection_limit(monkeypatch):
    from genshin_db.runtime_attack_metadata import AttackMetadataContext
    context = AttackMetadataContext({"status": "pending", "issues": [], "candidates": [], "selected_candidate": None}, {}, {})
    monkeypatch.setitem(ATTACK_METADATA_LIMITS, "max_issues", 0)
    with pytest.raises(RuntimeConfigError, match="最终诊断"):
        project_attack_metadata(context)


def test_role_shape_accepts_renamed_ability_symbol_and_modifier(metadata_db, fake_repo):
    rows = json.loads((fake_repo / SOURCE).read_text("utf8"))
    body = rows[0]["Default"]
    body["IIBMNPFLNMJ"] = "Independent_Ability"
    body["LOEHLJGBGAC"]["Independent_Multiplier"] = body["LOEHLJGBGAC"].pop("Icespine_Damage")
    modifier = body["GPPBBDEICKP"]["Independent_Contact"] = body["GPPBBDEICKP"].pop("Icespine_Strike_Delay")
    modifier["HHDOFNHCIAE"][0]["IDCIIIFMDCM"]["LGHDGGJIJOK"]["EHKJIDFINEK"]["KOLOHIDMHHE"] = "Independent_Multiplier"
    for action in body["EMGBJJOIENB"]:
        if action.get("LDFBDMNIADI") == "Icespine_Strike_Delay":
            action["LDFBDMNIADI"] = "Independent_Contact"
    (fake_repo / SOURCE).write_text(json.dumps(rows), "utf8")
    change(metadata_db, fake_repo, TALENT, lambda data: data["MetadataFixtureSkill"][0].update(
        IIBMNPFLNMJ="Independent_Ability", PEOJKHKHAKL="Independent_Multiplier"))
    assert candidate(metadata_db, "Independent_Ability")["status"] == "ready"
