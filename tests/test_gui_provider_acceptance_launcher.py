from pathlib import Path

from genshin_app.gui.api import GuiApi


def test_provider_acceptance_allows_agent_but_blocks_unrelated_network_and_db_writes(
    tmp_path,
    monkeypatch,
):
    profile = tmp_path / "profile"
    database = tmp_path / "source" / "genshin.db"
    database.parent.mkdir()
    database.write_bytes(b"catalog")
    manager = object()
    api = GuiApi(
        profile,
        database_path=database,
        app_identity={
            "contract": "genshin-native-agent-provider-acceptance-v1",
            "mode": "provider_acceptance",
            "build_channel": "provider_acceptance",
            "label": "真实 DeepSeek 开发联调",
            "network_policy": "deepseek_provider_only",
            "network_mode": "deepseek_provider_only",
            "host_network_policy": "app_and_database_remote_disabled",
            "agent_network_policy": "deepseek_official_llm_search_public_http_fetch",
        },
        agent_manager_factory=lambda _path: manager,
    )

    monkeypatch.setattr(
        "genshin_app.version.check_update",
        lambda: (_ for _ in ()).throw(AssertionError("App update network called")),
    )
    monkeypatch.setattr(
        "subprocess.run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("database update network called")
        ),
    )
    assert api._get_agent_manager() is manager
    assert api.check_update()["data"]["reason"] == (
        "provider_acceptance_host_network_restricted"
    )
    assert api.check_database_update()["data"]["reason"] == (
        "provider_acceptance_host_network_restricted"
    )
    assert api.update_database()["reason"] == (
        "provider_acceptance_database_read_only"
    )
    assert api.create_annotation("character", 1, "x")["reason"] == (
        "accepted_database_is_immutable"
    )
    assert api.update_annotation(1, {"content": "x"})["reason"] == (
        "accepted_database_is_immutable"
    )
    assert api.delete_annotation(1)["reason"] == (
        "accepted_database_is_immutable"
    )
    assert api._db_path == database.resolve()
    assert not (profile / "genshin.db").exists()
    identity = api.ping()["data"]["app_identity"]
    assert identity["host_network_policy"] == "app_and_database_remote_disabled"
    assert identity["agent_network_policy"] == (
        "deepseek_official_llm_search_public_http_fetch"
    )


def test_native_agent_development_banner_explains_both_network_boundaries():
    source = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "genshin_app"
        / "gui"
        / "web"
        / "app.js"
    ).read_text(encoding="utf-8")

    assert "宿主App的更新检查和数据库联网已关闭" in source
    assert "web_search访问受审DeepSeek官方服务" in source
    assert "web_fetch可访问符合公开地址约束的HTTP(S)目标" in source
    assert "app_and_database_remote_disabled" in source


def test_provider_identity_is_public_and_contains_no_local_path_or_secret(tmp_path):
    api = GuiApi(
        tmp_path,
        app_identity={
            "mode": "provider_acceptance",
            "label": "真实 DeepSeek 开发联调",
            "network_policy": "deepseek_provider_only",
        },
    )
    identity = api.ping()["data"]["app_identity"]
    assert identity == {
        "mode": "provider_acceptance",
        "label": "真实 DeepSeek 开发联调",
        "network_policy": "deepseek_provider_only",
    }
    assert "key" not in str(identity).casefold()
    assert str(tmp_path) not in str(identity)


def test_explicit_database_is_read_only_even_if_caller_omits_acceptance_identity(
    tmp_path,
):
    database = tmp_path / "source" / "genshin.db"
    database.parent.mkdir()
    database.write_bytes(b"catalog")
    api = GuiApi(tmp_path / "profile", database_path=database)

    result = api.create_annotation("character", 1, "must not write")
    assert result["code"] == 1
    assert result["reason"] == "accepted_database_is_immutable"
    assert database.read_bytes() == b"catalog"


def test_native_agent_development_keeps_shared_database_and_host_network_read_only(
    tmp_path,
    monkeypatch,
):
    profile = tmp_path / "profile"
    database = tmp_path / "source" / "genshin.db"
    database.parent.mkdir()
    database.write_bytes(b"catalog")
    manager = object()
    api = GuiApi(
        profile,
        database_path=database,
        app_identity={
            "contract": "genshin-native-agent-development-v1",
            "mode": "native_agent_development",
            "build_channel": "native_agent_development",
            "label": "原生 Agent 完整开发联调",
            "network_policy": "deepseek_provider_only",
            "network_mode": "deepseek_provider_only",
        },
        agent_manager_factory=lambda _path: manager,
    )

    monkeypatch.setattr(
        "genshin_app.version.check_update",
        lambda: (_ for _ in ()).throw(AssertionError("App update network called")),
    )
    monkeypatch.setattr(
        "subprocess.run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("database update network called")
        ),
    )

    assert api._get_agent_manager() is manager
    assert api.check_update()["data"]["reason"] == (
        "native_agent_development_host_network_restricted"
    )
    database_status = api.check_database_update()["data"]
    assert database_status["reason"] == (
        "native_agent_development_host_network_restricted"
    )
    assert "DeepSeek Search和公开地址Fetch" in database_status["message"]
    assert api.update_database()["reason"] == (
        "native_agent_development_database_read_only"
    )
    assert api.create_annotation("character", 1, "x")["reason"] == (
        "accepted_database_is_immutable"
    )
    assert api.update_annotation(1, {"content": "x"})["reason"] == (
        "accepted_database_is_immutable"
    )
    assert api.delete_annotation(1)["reason"] == (
        "accepted_database_is_immutable"
    )
    assert api._db_path == database.resolve()
    assert not (profile / "genshin.db").exists()
