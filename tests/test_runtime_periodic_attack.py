"""Finite periodic sources use complete structural excerpts and synthetic identities."""
import copy
import hashlib
import json
from importlib import resources

import pytest

from genshin_db.equipment_queries import equipment_read_session
from genshin_db.runtime_periodic_attack import get_periodic_attack_context
from genshin_db.runtime_queries import query_runtime
from genshin_runtime.normalization import for_commit
from genshin_runtime.periodic_attack_identity import periodic_source_fingerprint
from tests import fixture_candidate_builder as fixture_builder
from tests.test_pipeline import fake_repo, stub_fetcher  # noqa: F401

# ruff: noqa: F811
SOURCE = "BinOutput/Ability/Temp/AvatarAbilities/PeriodicFixture.json"
TALENT = "BinOutput/Talent/AvatarTalents/PeriodicFixture.json"
GADGET = "BinOutput/Gadget/PeriodicFixture.json"


def recipe():
    return json.loads(resources.files("genshin_runtime").joinpath("resources/periodic_attack_profiles.json").read_text("utf8"))


def write(repo, path, value):
    dest = repo / path
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(value, ensure_ascii=False), "utf8")


@pytest.fixture
def periodic_db(tmp_path, fake_repo, stub_fetcher):
    r = recipe()
    stub_fetcher["commit"] = r["commit"]
    contexts = r["evidence"]["contexts"]
    raw = [{"Default": copy.deepcopy(c["raw"])} for c in contexts[:4]]
    # Complete material ability is not an attack candidate and has no parameter writer.
    raw.append({"Default": {"abilityName": "Avatar_Ayaka_FrozenWindmill_Material"}})
    raw.append({"Default": {"abilityName": "Avatar_Ayaka_FrozenWindmill_Sub_Material"}})
    for context, name in ((contexts[2], "Avatar_Ayaka_FrozenWindmill_Sub_Damage"),
                          (contexts[3], "Avatar_Ayaka_FrozenWindmill_Sub_Dissipate")):
        value = copy.deepcopy(context["raw"])
        key = for_commit(r["commit"]).raw_pointer(value, "/abilityName", kind="ability").removeprefix("/")
        value[key] = name
        raw.append({"Default": value})
    write(fake_repo, SOURCE, raw)
    write(fake_repo, TALENT, {"PeriodicFixtureQ": contexts[4]["raw"]})
    write(fake_repo, GADGET, {c["name"].removeprefix("gadget:"): c["raw"] for c in r["evidence"]["gadgets"]})
    write(fake_repo, "ExcelBinOutput/GadgetExcelConfigData.json", [c["excel_record"] for c in r["evidence"]["creation_links"][:2]])
    write(fake_repo, "ExcelBinOutput/AttackAttenuationExcelConfigData.json", [r["evidence"]["attenuation"]["record"]])
    write(fake_repo, "ExcelBinOutput/ProudSkillExcelConfigData.json", [{"proudSkillId": 9100210,
        "proudSkillGroupId": 91002, "level": 10, "paramList": [2.021399974822998, 3.032099962234497, 5],
        "paramDescList": [501, 502], "openConfig": "PeriodicFixtureQ"}])
    path = "TextMap/TextMap_MediumCHS.json"
    text = json.loads((fake_repo / path).read_text("utf8"))
    text.update({"501": "切割伤害|{param1:P}", "502": "独立攻击伤害|{param2:P}"})
    write(fake_repo, path, text)
    fixture_builder.build_database(tmp_path)
    return tmp_path / "genshin.db"


def query(db, **fields):
    return query_runtime(db, {"action": "periodic_attack_profile", "character_id": 90000001, "slot": "burst", **fields})


def chosen(db):
    discovered = query(db)
    origin = next(c["origin_reference_id"] for c in discovered["creation_candidates"] if c["gadget_id"] == 41002001)
    channels = query(db, origin_reference_id=origin)
    return origin, [c["candidate_id"] for c in channels["candidates"]]


def test_periodic_real_shapes_synthetic_character_and_source_readonly(periodic_db):
    before = hashlib.sha256(periodic_db.read_bytes()).hexdigest()
    origin, candidates = chosen(periodic_db)
    out = query(periodic_db, origin_reference_id=origin, candidate_ids=candidates)
    assert out["status"] == "ready", out["issues"] + out["selected_candidates"]
    assert len(out["selected_candidates"]) == 2
    bykind = {c["channel_kind"]: c["projection"] for c in out["selected_candidates"]}
    assert bykind["periodic_callback_attack"]["timer"]["ticks"] == 250000
    assert bykind["ability_start_attack"]["timer"]["ticks"] is None
    assert sorted(c["projection"]["parameter"]["value"] for c in out["selected_candidates"]) == [2.021399974822998, 3.032099962234497]
    assert all(c["projection"]["gauge"]["value"] == 1 for c in out["selected_candidates"])
    assert out["source_fingerprint"] == periodic_source_fingerprint(out)
    assert out["whole_source_status"] == "partial" and out["action_creation_association"] == "unproven"
    assert hashlib.sha256(periodic_db.read_bytes()).hexdigest() == before


def test_periodic_projection_accepts_pyro_without_character_specific_branch(periodic_db, fake_repo):
    def set_pyro(rows):
        for row in rows:
            raw = row.get("Default", {})
            for modifier in raw.get("GPPBBDEICKP", {}).values():
                for operation in modifier.get("FMBHOKCHKOH", []):
                    attack = (operation.get("IDCIIIFMDCM") or {}).get("LGHDGGJIJOK")
                    if isinstance(attack, dict) and isinstance(attack.get("EHKJIDFINEK"), dict):
                        attack["EHKJIDFINEK"]["EPPIDAILPBN"] = "Fire"

    change(periodic_db, fake_repo, SOURCE, set_pyro)
    origin, candidates = chosen(periodic_db)
    out = query(periodic_db, origin_reference_id=origin, candidate_ids=candidates)
    assert out["status"] == "ready", out["issues"]
    assert "pyro" in {row["projection"]["element"]["value"] for row in out["selected_candidates"]}


def test_periodic_projection_uses_explicit_nonzero_constellation_level(periodic_db, fake_repo):
    path = "ExcelBinOutput/ProudSkillExcelConfigData.json"

    def add_level(rows):
        level = copy.deepcopy(rows[0])
        level["proudSkillId"] = 9100213
        level["level"] = 13
        rows.append(level)

    change(periodic_db, fake_repo, path, add_level)
    discovered = query(periodic_db, constellation=1, talent_level=13)
    origin = next(c["origin_reference_id"] for c in discovered["creation_candidates"] if c["gadget_id"] == 41002001)
    channels = query(periodic_db, constellation=1, talent_level=13, origin_reference_id=origin)
    ids = [c["candidate_id"] for c in channels["candidates"]]
    out = query(periodic_db, constellation=1, talent_level=13,
                origin_reference_id=origin, candidate_ids=ids)
    assert out["status"] == "ready", out["issues"]
    assert out["level"]["effective_level"] == 13
    assert not any(row["code"] == "periodic_selection_scope_unsupported" for row in out["issues"])


def test_origin_discovery_never_combines_same_parent_two_leaves(periodic_db):
    out = query(periodic_db)
    assert out["mode"] == "origin_discovery" and out["status"] == "pending"
    assert len(out["creation_candidates"]) == 3
    assert len({c["origin_reference_id"] for c in out["creation_candidates"]}) == len(out["creation_candidates"])


def test_context_deepcopy_and_two_channels_cache(periodic_db, monkeypatch):
    import genshin_db.runtime_action_parameter as parameters
    import genshin_db.runtime_attenuation as attenuation
    import genshin_db.runtime_gadget_source as gadgets
    origin, ids = chosen(periodic_db)
    counts = [0, 0, 0]
    for i, (module, name) in enumerate([(parameters, "get_action_parameter_context"), (attenuation, "get_runtime_attenuation_profile"), (gadgets, "get_runtime_gadget_source_profile")]):
        original = getattr(module, name)
        def counted(*a, _i=i, _fn=original, **kw):
            counts[_i] += 1
            return _fn(*a, **kw)
        monkeypatch.setattr(module, name, counted)
    with equipment_read_session(periodic_db):
        for ident in ids:
            ctx = get_periodic_attack_context(periodic_db, character_id=90000001, slot="burst", origin_reference_id=origin, candidate_ids=[ident])
            assert ctx.public_projection["status"] == "ready"
            assert len(ctx.selected_parameter_projections) == 1
            ctx.parameter_context["facts"]["selection"]["character_id"] = -1
    assert counts == [1, 1, 1]


@pytest.mark.parametrize("fields", [{"character_id": True}, {"slot": "oops"}, {"candidate_ids": []},
    {"origin_reference_id": "bad"}, {"slot": "burst", "skill_id": 1}, {"candidate_ids": ["x"]}])
def test_invalid_request_before_opening_missing_db(tmp_path, fields):
    with pytest.raises(ValueError):
        query(tmp_path / "missing.db", **fields)


def test_source_hash_includes_raw_but_not_echoed_hash(periodic_db):
    origin, ids = chosen(periodic_db)
    out = query(periodic_db, origin_reference_id=origin, candidate_ids=ids)
    original = periodic_source_fingerprint(out)
    out["source_fingerprint"] = "0" * 64
    out["semantic_dependency"] = {}
    assert periodic_source_fingerprint(out) == original
    out["source_graph"]["creation_bundle"]["origin"]["operation"]["raw"]["injected"] = True
    assert periodic_source_fingerprint(out) != original


def change(db, repo, path, mutate):
    data = json.loads((repo / path).read_text("utf8"))
    mutate(data)
    write(repo, path, data)
    fixture_builder.build_database(db.parent, force=True)


@pytest.mark.parametrize("mutation", [
    lambda rows: rows[2]["Default"].update(new_control=True),
    lambda rows: rows[2]["Default"]["GPPBBDEICKP"]["FrozenWindmill_Damage"].update(unknown=True),
])
def test_changed_full_parent_withdraws_prepared(periodic_db, fake_repo, mutation):
    _origin, ids = chosen(periodic_db)
    change(periodic_db, fake_repo, SOURCE, mutation)
    origin2, ids2 = chosen(periodic_db)
    assert ids == ids2
    ctx = get_periodic_attack_context(periodic_db, character_id=90000001, slot="burst", origin_reference_id=origin2, candidate_ids=ids2)
    assert ctx.public_projection["status"] == "pending"
    assert ctx.attenuation_profiles == ctx.selected_parameter_projections == {}


def test_nonexact_timer_retains_independent_sources_for_reviewed_finite_topology(
        periodic_db, fake_repo):
    def timer_inexact(rows):
        rows[2]["Default"]["GPPBBDEICKP"]["FrozenWindmill_Damage"][
            "JFLGPLCIOEC"] = 0.25000001

    change(periodic_db, fake_repo, SOURCE, timer_inexact)
    origin, ids = chosen(periodic_db)
    ctx = get_periodic_attack_context(periodic_db, character_id=90000001,
        slot="burst", origin_reference_id=origin, candidate_ids=ids)

    assert ctx.public_projection["status"] == "pending"
    selected = ctx.public_projection["selected_candidates"]
    periodic = next(row for row in selected
                    if row["channel_kind"] == "periodic_callback_attack")
    assert [row["code"] for row in periodic["issues"]] == ["periodic_timer_not_exact"]
    assert periodic["projection"]["attenuation"]["status"] == "ready"
    assert ctx.attenuation_profiles and ctx.selected_parameter_projections


def test_nonexact_timer_cannot_hide_an_unreviewed_parent(periodic_db, fake_repo):
    def timer_and_parent_unknown(rows):
        rows[2]["Default"]["GPPBBDEICKP"]["FrozenWindmill_Damage"][
            "JFLGPLCIOEC"] = 0.25000001
        rows[2]["Default"]["unknown_control"] = True

    change(periodic_db, fake_repo, SOURCE, timer_and_parent_unknown)
    origin, ids = chosen(periodic_db)
    ctx = get_periodic_attack_context(periodic_db, character_id=90000001,
        slot="burst", origin_reference_id=origin, candidate_ids=ids)

    codes = {issue["code"] for row in ctx.public_projection["selected_candidates"]
             for issue in row["issues"]}
    assert {"periodic_complete_parent_unsupported", "periodic_timer_not_exact",
            "periodic_attenuation_not_admitted"} <= codes
    assert ctx.attenuation_profiles == ctx.selected_parameter_projections == {}


def test_conditional_creation_and_unknown_gadget_controls_stay_pending(periodic_db, fake_repo):
    choices = query(periodic_db)["creation_candidates"]
    for sub in [c for c in choices if c["gadget_id"] == 41002005]:
        found = query(periodic_db, origin_reference_id=sub["origin_reference_id"])
        selected = query(periodic_db, origin_reference_id=sub["origin_reference_id"], candidate_ids=[c["candidate_id"] for c in found["candidates"]])
        assert selected["status"] == "pending"
        assert any(i["code"] == "periodic_creation_control_unverified" for i in selected["issues"])
    change(periodic_db, fake_repo, GADGET, lambda value: value["FrozenWindmill"].update(unknown_control=True))
    origin, ids = chosen(periodic_db)
    assert query(periodic_db, origin_reference_id=origin, candidate_ids=ids)["status"] == "pending"


def test_batch_api_and_public_projection_summary(periodic_db):
    origin, ids = chosen(periodic_db)
    request = {"action": "periodic_attack_profile", "character_id": 90000001, "slot": "burst", "origin_reference_id": origin, "candidate_ids": ids}
    single = query_runtime(periodic_db, request)
    batch = query_runtime(periodic_db, {"action": "batch", "items": [{"id": "q", "request": request}]})
    assert batch["items"][0]["data"] == single
    assert all("projection" not in c and "parent_refs" not in c for c in single["candidates"])
    assert all("projection" in c for c in single["selected_candidates"])
    identities = [c["projection_summary"]["application_identity"] for c in single["candidates"]]
    assert all(row == {"status": "ready", "tag": "Elemental_Burst",
                       "evidence": "upstream_attenuation_tag", "next_action": None}
               for row in identities)


def test_source_display_snapshot_does_not_change_confirmation(periodic_db):
    origin, ids = chosen(periodic_db)
    out = query(periodic_db, origin_reference_id=origin, candidate_ids=ids)
    expected = periodic_source_fingerprint(out)
    out["snapshot"]["built_at"] = "display-only"
    out["source_graph"]["creation_bundle"]["snapshot"]["built_at"] = "display-only"
    out["source_graph"]["creation_bundle"]["issues"] = [{"display": True}]
    assert periodic_source_fingerprint(out) == expected


@pytest.mark.parametrize("limit,value,selected", [("max_source_nodes", 5, True), ("max_configs", 1, False),
    ("max_edges", 4, False), ("max_bytes", 10000, True), ("max_origins", 2, False)])
def test_complete_request_budgets_never_deliver_prefix(periodic_db, monkeypatch, limit, value, selected):
    from genshin_db.runtime_catalog import RuntimeConfigError
    from genshin_db.runtime_periodic_attack import PERIODIC_LIMITS
    origin, ids = chosen(periodic_db)
    monkeypatch.setitem(PERIODIC_LIMITS, limit, value)
    with pytest.raises(RuntimeConfigError) as exc:
        query(periodic_db, **({"origin_reference_id": origin, "candidate_ids": ids} if selected else {}))
    assert exc.value.status == 413


def test_selected_project_is_stable_and_cannot_use_client_context(periodic_db):
    from genshin_db.runtime_periodic_attack import project_periodic_attack
    origin, ids = chosen(periodic_db)
    ctx = get_periodic_attack_context(periodic_db, character_id=90000001, slot="burst", origin_reference_id=origin, candidate_ids=ids)
    assert project_periodic_attack(ctx, ids) == ctx.public_projection
    with pytest.raises(ValueError):
        project_periodic_attack(ctx, [ids[0]])
    with pytest.raises(ValueError):
        project_periodic_attack(ctx.public_projection)


def test_packaged_complete_old_new_parents_have_honest_hashes_and_timer_mapping():
    from genshin_db.runtime_catalog import fingerprint, resolve_pointer
    r = recipe()
    for node in r["evidence"]["contexts"] + r["evidence"]["gadgets"]:
        assert fingerprint(node["raw"]) == node["node_sha256"]
        assert fingerprint(node["old_raw"]) == node["old_node_sha256"]
    parent = r["evidence"]["contexts"][2]
    assert resolve_pointer(parent["raw"], "/GPPBBDEICKP/FrozenWindmill_Damage/JFLGPLCIOEC") == 0.25
    assert resolve_pointer(parent["old_raw"], "/modifiers/FrozenWindmill_Damage/thinkInterval") == 0.25
    assert len(resolve_pointer(parent["raw"], "/GPPBBDEICKP/FrozenWindmill_Damage/FMBHOKCHKOH")) == 1
    assert len(resolve_pointer(parent["old_raw"], "/modifiers/FrozenWindmill_Damage/onThinkInterval")) == 1
    assert all(f["workspace_sha256"] != f["git_blob_sha256"] for f in r["evidence"]["files"])


def test_huge_parameter_and_extra_modify_flag_are_pending_not_overflow(periodic_db, fake_repo):
    change(periodic_db, fake_repo, "ExcelBinOutput/ProudSkillExcelConfigData.json", lambda rows: rows[0]["paramList"].__setitem__(0, 10**400))
    origin, ids = chosen(periodic_db)
    out = query(periodic_db, origin_reference_id=origin, candidate_ids=ids)
    assert out["status"] == "pending"
    change(periodic_db, fake_repo, TALENT, lambda value: value["PeriodicFixtureQ"][0].update(unknown_flag=True))
    origin, ids = chosen(periodic_db)
    assert query(periodic_db, origin_reference_id=origin, candidate_ids=ids)["status"] == "pending"


@pytest.mark.parametrize("reason,status", [("dataset_invalid", 500), ("mixed_snapshot", 500), ("response_too_large", 413)])
def test_projection_integrity_and_budget_exceptions_are_not_semantic_pending(periodic_db, monkeypatch, reason, status):
    import genshin_db.runtime_attenuation as attenuation
    from genshin_db.runtime_catalog import RuntimeConfigError
    origin, ids = chosen(periodic_db)
    def fail(*args, **kwargs):
        raise RuntimeConfigError("independent failure", reason, status)
    monkeypatch.setattr(attenuation, "get_runtime_attenuation_profile", fail)
    with pytest.raises(RuntimeConfigError) as exc:
        query(periodic_db, origin_reference_id=origin, candidate_ids=ids)
    assert exc.value.reason == reason and exc.value.status == status


def test_other_symbol_writer_on_selected_ability_is_not_silently_ignored(periodic_db, fake_repo):
    def extra_write(talents):
        extra = copy.deepcopy(talents["PeriodicFixtureQ"][0])
        extra["PEOJKHKHAKL"] = "Additional_Unknown_Control"
        extra["PCABAADALND"] = "%3"
        talents["PeriodicFixtureQ"].append(extra)
    change(periodic_db, fake_repo, TALENT, extra_write)
    origin, ids = chosen(periodic_db)
    ctx = get_periodic_attack_context(periodic_db, character_id=90000001, slot="burst", origin_reference_id=origin, candidate_ids=ids)
    assert ctx.public_projection["status"] == "pending"
    assert any(i["code"] == "periodic_additional_parameter_writer_unsupported" for c in ctx.public_projection["selected_candidates"] for i in c["issues"])
    assert ctx.attenuation_profiles == ctx.selected_parameter_projections == {}
    talent = next(n for n in ctx.public_projection["source_graph"]["nodes"] if n["source"]["source_path"] == TALENT)
    assert len(talent["raw"]) == 5 and talent["raw"][-1]["PEOJKHKHAKL"] == "Additional_Unknown_Control"
