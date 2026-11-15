"""G1 桥层测试：ping / load_template / save_template（含引擎 schema 校验）。"""

import json
from pathlib import Path

import pytest

from genshin_app import version as app_version
from genshin_app.cli import CLI_VERSION
from genshin_app.gui.api import GuiApi

EXAMPLE = Path(__file__).resolve().parent.parent / "examples" / "freeze_team.json"


def make_api(tmp_path):
    return GuiApi(data_dir=tmp_path)


def test_ping(tmp_path):
    r = make_api(tmp_path).ping()
    assert r["code"] == 0
    assert r["data"]["version"] == CLI_VERSION
    assert "app_identity" not in r["data"]


def test_external_link_uses_reviewed_system_browser_boundary(tmp_path):
    opened = []
    api = GuiApi(
        data_dir=tmp_path,
        external_url_opener=lambda url: opened.append(url) or True,
    )

    assert api.open_external_link("https://example.com/source?q=1") == {
        "code": 0,
        "data": {"status": "opened"},
    }
    assert opened == ["https://example.com/source?q=1"]
    for unsafe in (
        "file:///C:/secret.txt",
        "javascript:alert(1)",
        "https://user:password@example.com/private",
        "https://example.com/\nheader",
        "relative/path",
    ):
        assert api.open_external_link(unsafe)["code"] == 1
    assert opened == ["https://example.com/source?q=1"]


def test_external_link_reports_system_browser_rejection(tmp_path):
    api = GuiApi(data_dir=tmp_path, external_url_opener=lambda _url: False)
    assert api.open_external_link("https://example.com")["code"] == 1


def test_agent_budget_settings_bridge_preserves_generation_and_sparse_overrides(tmp_path):
    def must_not_initialize_provider(_data_dir):
        raise AssertionError("local budget settings must not initialize the provider")

    api = GuiApi(tmp_path, agent_manager_factory=must_not_initialize_provider)
    initial = api.agent_budget_settings()["data"]
    assert initial["generation"] == 0
    assert initial["effective"]["wall_clock_seconds"] == 7200
    updated = api.agent_update_budget_settings(
        {"wall_clock_seconds": 2400}, initial["generation"]
    )["data"]
    assert updated["apply_scope"] == "next_turn"
    assert updated["overrides"] == {"wall_clock_seconds": 2400}
    stale = api.agent_update_budget_settings(
        {"wall_clock_seconds": 3000}, initial["generation"]
    )
    assert stale["reason"] == "native_turn_budget_generation_conflict"
    reset = api.agent_reset_budget_settings(updated["generation"])["data"]
    assert reset["follows_defaults"] is True
    assert reset["effective"]["wall_clock_seconds"] == 7200


def test_agent_dsh_settings_bridge_is_local_sparse_and_next_runtime(tmp_path):
    def must_not_initialize_provider(_data_dir):
        raise AssertionError("local DSH settings must not initialize the provider")

    api = GuiApi(tmp_path, agent_manager_factory=must_not_initialize_provider)
    initial = api.agent_dsh_settings()["data"]
    assert initial["generation"] == 0
    assert initial["apply_scope"] == "next_runtime"
    changed = api.agent_update_dsh_settings(
        {"agentLoop": {"maxParallelToolCalls": 12}}, initial["generation"]
    )["data"]
    assert changed["effective"]["agentLoop"]["maxParallelToolCalls"] == 12
    stale = api.agent_update_dsh_settings(
        {"agentLoop": {"maxParallelToolCalls": 8}}, initial["generation"]
    )
    assert stale["reason"] == "native_dsh_settings_generation_conflict"
    reset = api.agent_reset_dsh_settings(changed["generation"])["data"]
    assert reset["follows_defaults"] is True


def test_development_identity_is_detached_and_database_can_be_shared(tmp_path, monkeypatch):
    profile = tmp_path / "profile"
    shared_database = tmp_path / "source" / "genshin.db"
    shared_database.parent.mkdir()
    shared_database.write_bytes(b"catalog")
    identity = {
        "contract": "genshin-native-agent-development-acceptance-v1",
        "mode": "development_acceptance",
        "label": "本地开发验收版",
        "network_policy": "disabled",
        "service_mode": "skipped_external_database",
    }
    seen = []
    monkeypatch.setattr(
        "genshin_app.gui.ports.database.queries.get_meta",
        lambda path: seen.append(Path(path)) or {"schema_version": 1},
    )

    api = GuiApi(profile, database_path=shared_database, app_identity=identity)
    identity["label"] = "调用方后来修改"
    assert api.db_overview()["code"] == 0
    assert seen == [shared_database.resolve()]
    assert api.plans_dir == profile / "plans"
    ping = api.ping()["data"]
    assert ping["app_identity"]["label"] == "本地开发验收版"
    assert ping["app_identity"]["network_policy"] == "disabled"


def test_development_identity_blocks_network_and_shared_database_writes(
    tmp_path,
    monkeypatch,
):
    profile = tmp_path / "profile"
    database = tmp_path / "source" / "genshin.db"
    database.parent.mkdir()
    database.write_bytes(b"catalog")
    api = GuiApi(
        profile,
        database_path=database,
        app_identity={
            "mode": "development_acceptance",
            "network_policy": "disabled",
        },
    )

    monkeypatch.setattr(
        "genshin_app.version.check_update",
        lambda: (_ for _ in ()).throw(AssertionError("network update was called")),
    )
    monkeypatch.setattr(
        "subprocess.run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("git network check was called")
        ),
    )
    assert api.check_update()["data"]["disabled"] is True
    assert api.check_database_update()["data"]["disabled"] is True
    assert api.update_database()["reason"] == "development_acceptance_database_read_only"
    for response in (
        api.create_annotation("character", 1, "x"),
        api.update_annotation(1, {"content": "x"}),
        api.delete_annotation(1),
    ):
        assert response["reason"] == "accepted_database_is_immutable"
        assert response["details"]["active_database_modified"] is False


def test_app_identity_rejects_unknown_or_secret_shaped_fields(tmp_path):
    with pytest.raises(ValueError, match="unknown app_identity"):
        GuiApi(tmp_path, app_identity={"api_key": "must-not-cross-ping"})


def test_check_update_cache(tmp_path, monkeypatch):
    from genshin_app import version

    calls = []

    def fake_check():
        calls.append(1)
        return {"version": "9.9.9", "url": "https://x/d"}

    monkeypatch.setattr(version, "check_update", fake_check)
    api = make_api(tmp_path)
    r = api.check_update()
    assert r["code"] == 0 and r["data"]["version"] == "9.9.9"
    api.check_update()  # 第二次走缓存
    assert len(calls) == 1

    monkeypatch.setattr(version, "check_update", lambda: None)
    r2 = make_api(tmp_path).check_update()
    assert r2["code"] == 0 and r2["data"] == {}


def test_load_template_ok(tmp_path):
    r = make_api(tmp_path).load_template(str(EXAMPLE))
    assert r["code"] == 0
    assert r["data"]["template"]["form"] == "resolved"
    assert len(r["data"]["template"]["hits"]) == 4


def test_load_template_missing(tmp_path):
    r = make_api(tmp_path).load_template(str(tmp_path / "none.json"))
    assert r["code"] == 1 and "不存在" in r["message"]


def test_load_template_bad_json(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    r = make_api(tmp_path).load_template(str(bad))
    assert r["code"] == 1 and "JSON" in r["message"]


def test_save_template_schema_reject(tmp_path):
    out = tmp_path / "plans" / "x.json"
    r = make_api(tmp_path).save_template(str(out), {"form": "unresolved"})
    assert r["code"] == 1 and "校验" in r["message"]
    assert not out.exists()  # 校验失败不落盘


def test_save_then_load_roundtrip(tmp_path):
    api = make_api(tmp_path)
    template = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    out = tmp_path / "plans" / "my_plan.json"
    r = api.save_template(str(out), template)
    assert r["code"] == 0
    assert out.exists()
    r2 = api.load_template(str(out))
    assert r2["code"] == 0
    assert r2["data"]["template"]["characters"][0]["stats"]["atk"] == 2000


def test_get_enums_matches_engine_constants(tmp_path):
    from genshin_engine import constants

    r = make_api(tmp_path).get_enums()
    assert r["code"] == 0
    d = r["data"]
    assert d["buff_zones"] == list(constants.BUFF_ZONES)
    assert d["damage_systems"] == list(constants.DAMAGE_SYSTEMS)
    assert set(d["reaction_types"]) == {"classic", "transformative", "lunar", "stellar"}
    assert "stellar_superconduct" in d["reaction_types"]["stellar"]
    assert d["elements"] == list(constants.ELEMENTS)
    assert d["scaling_stats"] == list(constants.SCALING_STATS)


def test_list_plans(tmp_path):
    api = make_api(tmp_path)
    r = api.list_plans()
    assert r["code"] == 0 and r["data"]["plans"] == []
    template = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    api.save_template(str(tmp_path / "plans" / "p1.json"), template)
    r2 = api.list_plans()
    assert r2["data"]["plans"] == ["p1.json"]


def test_new_template_passes_validation(tmp_path):
    from genshin_engine import validate_template

    r = make_api(tmp_path).new_template()
    assert r["code"] == 0
    validate_template(r["data"]["template"])  # 不抛异常即合法


# ---------- G3 计算与导出 ----------

def test_run_calc_matches_engine(tmp_path):
    """桥层计算必须与 CLI engine calc 同源（同一个 calculate）结果一致。"""
    from genshin_engine import calculate

    template = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    r = make_api(tmp_path).run_calc(template)
    assert r["code"] == 0
    result = r["data"]["result"]
    assert result["rotation_check"]["status"] == "not_checked"
    assert {k: v for k, v in result.items() if k != "rotation_check"} == calculate(template)


def test_run_calc_invalid_template(tmp_path):
    r = make_api(tmp_path).run_calc({"form": "unresolved"})
    assert r["code"] == 1 and "校验" in r["message"]


def test_export_result_three_formats(tmp_path):
    from genshin_engine import calculate

    api = make_api(tmp_path)
    result = calculate(json.loads(EXAMPLE.read_text(encoding="utf-8")))
    for fmt in ("json", "xlsx", "csv"):
        r = api.export_result(result, fmt, "冻结队/示例")  # 非法文件名字符应被清洗
        assert r["code"] == 0, fmt
        p = Path(r["data"]["path"])
        assert p.exists() and p.suffix == f".{fmt}"
        assert p.parent == tmp_path / "exports"
        assert "/" not in p.name and "：" not in p.name


def test_export_result_bad_format(tmp_path):
    r = make_api(tmp_path).export_result({}, "pdf", "x")
    assert r["code"] == 1 and "未知导出格式" in r["message"]


def test_file_io_errors_are_returned_to_gui(tmp_path):
    api = make_api(tmp_path)
    template = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    read_error = api.load_template(str(tmp_path))
    assert read_error["code"] == 1 and "读取模板失败" in read_error["message"]
    assert str(tmp_path) in read_error["message"]

    blocker = tmp_path / "blocked"
    blocker.write_text("not a directory", encoding="utf-8")
    write_error = api.save_template(str(blocker / "plan.json"), template)
    assert write_error["code"] == 1 and "保存模板失败" in write_error["message"]
    assert not (blocker / "plan.json").exists()
    plans_error = GuiApi(tmp_path, plans_dir=blocker).list_plans()
    assert plans_error["code"] == 1 and "方案目录" in plans_error["message"]


def test_load_template_invalid_encoding(tmp_path):
    path = tmp_path / "broken.json"
    path.write_bytes(b"\xff\xff")
    result = make_api(tmp_path).load_template(str(path))
    assert result["code"] == 1 and "读取模板失败" in result["message"]


def test_export_file_error_returns_path(tmp_path):
    from genshin_engine import calculate

    (tmp_path / "exports").write_text("blocked", encoding="utf-8")
    result = calculate(json.loads(EXAMPLE.read_text(encoding="utf-8")))
    response = make_api(tmp_path).export_result(result, "json", "failure")
    assert response["code"] == 1
    assert "exports" in response["message"] and "导出失败" in response["message"]


def test_calculation_value_error_returns_message(tmp_path, monkeypatch):
    def fail(_template):
        raise ValueError("角色 base_hp 缺失")

    monkeypatch.setattr("genshin_app.gui.ports.document.calculate", fail)
    response = make_api(tmp_path).run_calc({})
    assert response["code"] == 1 and "base_hp 缺失" in response["message"]


def test_unexpected_calculation_bug_is_not_hidden(tmp_path, monkeypatch):
    def fail(_template):
        raise RuntimeError("unexpected engine failure")

    monkeypatch.setattr("genshin_app.gui.ports.document.calculate", fail)
    with pytest.raises(RuntimeError, match="unexpected engine"):
        make_api(tmp_path).run_calc({})


def test_database_errors_return_to_gui(tmp_path):
    (tmp_path / "genshin.db").write_text("not sqlite", encoding="utf-8")
    api = make_api(tmp_path)
    for call in (api.db_overview, api.list_characters,
                 lambda: api.character_detail(10000002), lambda: api.search_data("神里"),
                 api.list_annotations):
        response = call()
        assert response["code"] == 1
        assert "database" in response["message"].lower()

    deprecated = api.delete_annotation(1)
    assert deprecated["code"] == 1
    assert deprecated["reason"] == "accepted_database_is_immutable"


def test_update_check_uses_only_reviewed_release_pair_discovery(tmp_path, monkeypatch):
    db = tmp_path / "genshin.db"
    db.write_bytes(b"unchanged")
    calls = []

    def discover(**kwargs):
        calls.append(kwargs)
        return {
            "status": "available",
            "source": "remote",
            "checked_at": "2026-10-26T00:00:00Z",
            "authoritative": True,
            "can_offer_update": True,
            "reason": None,
            "release_pair_id": "pair-next",
            "database_release_id": "db-next",
            "pair_manifest": {"app": {"app_release_id": "app-0.46.0"}},
            "database_manifest": {"database": {"game_version": "7.1.53"}},
        }

    monkeypatch.setattr(
        "genshin_app.release_pair_discovery.discover_latest_release_pair",
        discover,
    )
    monkeypatch.setattr(
        "genshin_app.version.DATABASE_RELEASE_MANIFEST_URL",
        "https://updates.example/latest.json",
    )
    monkeypatch.setattr(
        "genshin_db.queries.get_meta",
        lambda _path: {
            "game_version:release": "7.1.51",
            "upstream_commit:release": "a" * 40,
        },
    )
    response = make_api(tmp_path).check_database_update()
    assert response["code"] == 0 and response["data"]["available"]
    assert response["data"]["authoritative"] is True
    assert response["data"]["coordinator_available"] is False
    assert response["data"]["update_action_available"] is False
    assert response["data"]["requires_restart"] is True
    assert response["data"]["current_app_release_id"] == app_version.APP_RELEASE_ID
    assert response["data"]["current_database_release_id"] == app_version.DATABASE_RELEASE_ID
    assert response["data"]["actual_database_game_version"] == "7.1.51"
    assert response["data"]["actual_database_upstream_commit"] == "a" * 40
    assert response["data"]["remote_app_release_id"] == "app-0.46.0"
    assert response["data"]["remote_game_version"] == "7.1.53"
    assert calls == [{
        "url": "https://updates.example/latest.json",
        "cache_path": tmp_path / "cache" / "release-pair-latest.json",
        "channel": "dev",
    }]
    assert db.read_bytes() == b"unchanged"


def test_update_check_missing_db_does_not_access_network(tmp_path, monkeypatch):
    def unexpected(*_args, **_kwargs):
        raise AssertionError("no remote query needed before initialization")

    monkeypatch.setattr(
        "genshin_app.release_pair_discovery.discover_latest_release_pair",
        unexpected,
    )
    response = make_api(tmp_path).check_database_update()
    assert response["code"] == 0 and not response["data"]["initialized"]
    assert response["data"]["available"] is False
    assert response["data"]["update_action_available"] is False


def test_update_check_discovery_failure_returns_message(tmp_path, monkeypatch):
    from genshin_app.release_pair_discovery import ReleasePairDiscoveryError

    (tmp_path / "genshin.db").touch()

    def rejected(**_kwargs):
        raise ReleasePairDiscoveryError("release_pair_discovery_url_invalid")

    monkeypatch.setattr(
        "genshin_app.release_pair_discovery.discover_latest_release_pair",
        rejected,
    )
    response = make_api(tmp_path).check_database_update()
    assert response["code"] == 1 and "检查数据更新失败" in response["message"]


def test_update_check_unconfigured_is_clear_and_not_available(tmp_path):
    (tmp_path / "genshin.db").touch()
    response = make_api(tmp_path).check_database_update()
    assert response["code"] == 0
    assert response["data"]["disabled"] is True
    assert response["data"]["available"] is False
    assert response["data"]["reason"] == "release_pair_discovery_url_unconfigured"
    assert response["data"]["message"] == "尚未配置应用与数据库联合更新源"
    assert response["data"]["coordinator_available"] is False
    assert response["data"]["update_action_available"] is False


def test_manual_database_only_update_fails_closed_without_touching_live_db(
    tmp_path,
    monkeypatch,
):
    live = tmp_path / "genshin.db"
    live.write_bytes(b"reviewed current database")

    def unexpected(*_args, **_kwargs):
        raise AssertionError("raw upstream update must not run from the GUI")

    monkeypatch.setattr("genshin_db.pipeline.run_update", unexpected)
    response = make_api(tmp_path).update_database()

    assert response == {
        "code": 1,
        "message": (
            "数据库不能脱离应用单独更新。当前尚未配置已验证的应用与数据库"
            "联合更新包；在用数据库未被修改。"
        ),
        "reason": "database_pair_update_coordinator_required",
    }
    assert live.read_bytes() == b"reviewed current database"


def test_gui_pair_update_requires_exact_confirmation_then_hands_off(tmp_path):
    (tmp_path / "genshin.db").write_bytes(b"current")

    class Coordinator:
        def __init__(self):
            self.calls = []

        def availability(self):
            return {"available": True, "reason": None}

        def discover(self):
            return {
                "status": "available",
                "source": "remote",
                "checked_at": "2026-10-28T00:00:00Z",
                "authoritative": True,
                "can_offer_update": True,
                "reason": None,
                "release_pair_id": "pair-next",
                "database_release_id": "db-next",
                "confirmation_digest": "d" * 64,
                "document_sha256": "d" * 64,
                "pair_manifest": {"app": {"app_release_id": "app-next"}},
                "database_manifest": {"database": {"game_version": "7.2"}},
                "artifacts": {
                    "app": {"size_bytes": 12, "format": "zip"},
                    "database": {"size_bytes": 34, "format": "sqlite"},
                },
            }

        def prepare_and_launch(self, discovery, **kwargs):
            self.calls.append((discovery, kwargs))
            return {
                "action": "handoff_launched",
                "operation_id": "op-1",
                "requires_exit": True,
                "requires_restart": True,
            }

        def cancel(self):
            return {"cancel_requested": True}

    coordinator = Coordinator()
    api = GuiApi(tmp_path, release_pair_coordinator=coordinator)
    checked = api.check_database_update()
    assert checked["data"]["coordinator_available"] is True
    assert checked["data"]["update_action_available"] is True
    assert checked["data"]["confirmation_digest"] == "d" * 64
    assert checked["data"]["artifacts"]["database"]["size_bytes"] == 34

    rejected = api.update_database("d" * 64, False)
    assert rejected["reason"] == "release_pair_update_confirmation_required"
    accepted = api.update_database("d" * 64, True)
    assert accepted["code"] == 0
    assert accepted["data"]["action"] == "handoff_launched"
    assert coordinator.calls[-1][1] == {
        "confirmed_document_sha256": "d" * 64,
        "user_confirmed": True,
    }
    assert api.cancel_database_update()["data"] == {"cancel_requested": True}


@pytest.mark.parametrize("omitted", [("buffs",), ("switches",), ("buffs", "switches")])
def test_optional_lists_load_save_and_calculate(tmp_path, omitted):
    api = make_api(tmp_path)
    template = api.new_template()["data"]["template"]
    for key in omitted:
        del template[key]
    path = tmp_path / "optional.json"
    path.write_text(json.dumps(template), encoding="utf-8")
    response = api.load_template(str(path))
    assert response["code"] == 0
    loaded = response["data"]["template"]
    assert all(key not in loaded for key in omitted)
    saved = tmp_path / "round-trip.json"
    assert api.save_template(str(saved), loaded)["code"] == 0
    assert json.loads(saved.read_text(encoding="utf-8")) == template
    assert api.run_calc(loaded)["code"] == 0


def test_omitted_crit_defaults_differ_from_explicit_new_template(tmp_path):
    api = make_api(tmp_path)
    template = api.new_template()["data"]["template"]
    stats = template["characters"][0]["stats"]
    assert stats["crit_rate"] == 0.05 and stats["crit_dmg"] == 0.5
    explicit = api.run_calc(template)["data"]["result"]["hits"][0]
    assert explicit["zones"]["crit_expected"] == pytest.approx(1.025)
    del stats["crit_rate"]
    del stats["crit_dmg"]
    omitted = api.run_calc(template)["data"]["result"]["hits"][0]
    assert omitted["zones"]["crit_expected"] == 1
    assert omitted["expected"] == omitted["crit"] == pytest.approx(900)
