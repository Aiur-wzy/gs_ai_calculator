"""Pure, human-readable release status projections; no discovery or mutation."""
from __future__ import annotations

from .release_update_contracts import CurrentReleaseIdentity, ReleaseUpdateSettings

PRODUCT_RELEASE_NOTES = {
    "app": "https://github.com/Aiur-wzy/gs_ai_calculator/releases",
    "database": "https://github.com/Aiur-wzy/gs_ai_calculator_db/releases",
}


def product_release_details(document, *, app_released_at=None):
    return {
        "app_released_at": app_released_at,
        "database_released_at": document["database_manifest"]["released_at"],
        "release_notes": dict(PRODUCT_RELEASE_NOTES),
    }


def host_restriction_reason(settings: ReleaseUpdateSettings):
    if settings.network_disabled:
        return "development_acceptance_network_disabled"
    return ("native_agent_development_host_network_restricted" if settings.native_development
            else "provider_acceptance_host_network_restricted")


def current_release_status(identity: CurrentReleaseIdentity, settings: ReleaseUpdateSettings, meta):
    return {
        "current_app_version": identity.app_version,
        "current_app_release_id": identity.app_release_id,
        "current_release_pair_id": identity.release_pair_id,
        "current_database_release_id": identity.database_release_id,
        "actual_database_game_version": meta.get("game_version:release") or meta.get("game_version"),
        "actual_database_upstream_commit": meta.get("upstream_commit:release") or meta.get("upstream_commit"),
        "pair_policy": identity.pair_policy,
        "release_channel": identity.release_channel,
        "pair_status": (settings.app_identity or {}).get("release_plan_state", "not_checked"),
    }


def unavailable_database_status(current, settings: ReleaseUpdateSettings, *, initialized: bool):
    if settings.host_network_restricted:
        reason = ("native_agent_development_host_network_restricted" if settings.native_development
                  else "provider_acceptance_host_network_restricted" if not settings.network_disabled
                  else "development_acceptance_network_disabled")
        message = (
            "宿主App与数据库联网已禁用；原生Agent仍可使用受审的DeepSeek Search和公开地址Fetch"
            if settings.native_development else
            "真实DeepSeek联调仅允许Agent访问受审provider端点；数据库远端检查已禁用"
            if not settings.network_disabled else "本地开发验收模式已禁用外网数据检查"
        )
    else:
        reason = "database_profile_missing"
        message = "数据库尚未初始化；请安装与当前应用匹配的完整版本包"
    return {
        "available": False, "initialized": initialized, "disabled": True,
        "coordinator_available": False, "update_action_available": False,
        "requires_restart": True, **current, "reason": reason, "message": message,
    }


def discovered_database_status(discovered, coordinator_state, identity: CurrentReleaseIdentity, current):
    pair_manifest, database_manifest = discovered.get("pair_manifest"), discovered.get("database_manifest")
    remote_pair, remote_database = discovered.get("release_pair_id"), discovered.get("database_release_id")
    authoritative = discovered.get("authoritative") is True
    available = (authoritative and discovered.get("can_offer_update") is True
                 and (remote_pair != identity.release_pair_id or remote_database != identity.database_release_id))
    status = discovered.get("status")
    coordinator_available = coordinator_state.get("available") is True
    if status == "disabled":
        message = "尚未配置应用与数据库联合更新源"
    elif not authoritative:
        message = "未发现可验证的应用与数据库联合更新"
    elif available:
        message = ("发现已验证的应用与数据库联合更新；确认后将下载完整版本并重启" if coordinator_available
                   else "发现已验证的应用与数据库联合更新，但外置安装协调器不可用")
    else:
        message = "当前应用与数据库版本对已是最新"
    artifacts = discovered.get("artifacts")
    artifact_summary = ({kind: {"size_bytes": value.get("size_bytes"), "format": value.get("format")}
                         for kind, value in artifacts.items() if isinstance(value, dict)}
                        if isinstance(artifacts, dict) else None)
    result = {
        "available": available, "initialized": True, "disabled": status == "disabled",
        "coordinator_available": coordinator_available,
        "update_action_available": available and coordinator_available, "requires_restart": True,
        "status": status, "reason": discovered.get("reason"), "coordinator_reason": coordinator_state.get("reason"),
        "message": message, "authoritative": authoritative, "source": discovered.get("source"),
        "checked_at": discovered.get("checked_at"), "confirmation_digest": discovered.get("confirmation_digest"),
        "artifacts": artifact_summary, **current,
        "remote_app_release_id": pair_manifest.get("app", {}).get("app_release_id") if isinstance(pair_manifest, dict) else None,
        "remote_release_pair_id": remote_pair, "remote_database_release_id": remote_database,
        "remote_game_version": database_manifest.get("database", {}).get("game_version") if isinstance(database_manifest, dict) else None,
    }
    if identity.release_channel == "stable":
        options = []
        index = (discovered.get("product_index") or {}).get("signed_index", {})
        app_dates = {item["app_release_id"]: item["released_at"] for item in index.get("apps", [])}
        for row in discovered.get("update_options", []):
            document = row["document"]
            app, database = document["pair_manifest"]["app"], document["database_manifest"]
            options.append({"mode": row["mode"], "confirmation_digest": row["confirmation_digest"],
                            "app_release_id": app["app_release_id"],
                            "database_release_id": database["database_release_id"],
                            "app_bytes": 0 if row["reuse_app"] else document["artifacts"]["app"]["size_bytes"],
                            "database_bytes": 0 if row["reuse_database"] else document["artifacts"]["database"]["size_bytes"],
                            **product_release_details(document, app_released_at=app_dates.get(app["app_release_id"]))})
        result["update_options"] = options
        result["confirmation_digest"] = None
        if status == "disabled":
            result["message"] = "尚未配置发布更新源。当前已安装的版本可继续使用。"
        elif not authoritative:
            result["message"] = "暂时无法检查已签名的更新。当前已安装的版本可继续使用。"
        elif options:
            result["message"] = ("发现可用更新。请选择要更新的内容。" if coordinator_available
                                 else "发现可用更新。此安装没有可用的更新器。")
        elif discovered.get("installed_app_in_index") is False:
            result["status"] = "installed_app_outside_index"
            result["message"] = "当前应用版本已不在更新索引覆盖范围。可继续使用本机版本，或从发行页下载兼容安装包。"
        elif remote_pair != identity.release_pair_id:
            result["status"] = "no_verified_update_path"
            result["message"] = "已查看最新版。当前安装与最新版没有可直接更新的已验证组合。"
    return result


def read_only_update_failure(settings: ReleaseUpdateSettings):
    provider = (settings.app_identity or {}).get("mode") in {"provider_acceptance", "native_agent_development"}
    return (
        "真实DeepSeek开发联调以只读方式使用共享数据库" if provider else "本地开发验收模式禁止联网更新共享数据库",
        "native_agent_development_database_read_only" if settings.native_development
        else "provider_acceptance_database_read_only" if provider else "development_acceptance_database_read_only",
    )


def update_failure_message(reason):
    return {
        "release_pair_update_confirmation_required": "用户尚未确认联合更新。",
        "release_pair_update_confirmation_mismatch": "远端版本说明已经变化，请重新检查并确认。",
        "release_pair_artifact_download_cancelled": "联合更新下载已取消。",
        "release_pair_update_recovery_required": "上次联合更新尚待恢复或清理，请先查看更新诊断。",
        "release_pair_update_busy": "另一次更新正在处理。请查看它的进度或等待完成。",
        "release_pair_update_profile_busy": "资料库正在被计算或持续的 CLI 会话使用。请结束这些操作后重试，已验证的下载可以复用。",
        "release_pair_updater_launch_failed": "外置更新器未能启动，当前应用和数据库没有被替换。",
    }.get(reason, "联合更新准备失败，当前应用和数据库没有被替换。")
