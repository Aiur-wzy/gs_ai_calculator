"""Real PyWebView acceptance for the production GUI native-Agent route.

This complements the CLI Host projection. It loads the normal production
index and operates the official DSH composer, workspace picker and App review
controls. Tests may inject the complete DSH shell and native Save As picker;
neither an old Agent manager nor a second Session/Turn driver is involved.
"""

from __future__ import annotations

import copy
import hashlib
import json
import logging
import sqlite3
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from genshin_app.equipment.delivery import PACKAGE_KIND, PACKAGE_SCHEMA
from genshin_app.gui.headless_document_client import (
    GuiDocumentCallError,
    GuiDocumentClient,
)

CONTRACT = "gui-native-agent-webview-smoke-receipt-v1"
_MAX_RECEIPT_BYTES = 64 * 1024
_TERMINAL = frozenset(
    {"verified", "draft_verified", "awaiting_user_answer", "failed"}
)
_PROVIDER_MODES = frozenset(
    {"saved_credential", "deterministic_provider_transport"}
)
_EXPECTATION_KEYS = frozenset(
    {
        "title_contains",
        "required_character_ids",
        "required_action_type_ids",
        "min_actions",
        "min_revision_increment",
        "require_calculated",
        "min_result_hits",
    }
)


class GuiWebViewSmokeError(RuntimeError):
    """Stable failure from the real WebView acceptance boundary."""

    def __init__(
        self,
        message: str,
        *,
        reason: str,
        details: object | None = None,
    ) -> None:
        super().__init__(message)
        self.reason = reason
        if isinstance(details, Mapping) and details.get("contract") == CONTRACT:
            receipt = copy.deepcopy(dict(details))
            receipt.update(
                status="failed",
                real_webview_dom_verified=False,
                files_published=False,
                reload_verified=False,
                display_verified=False,
            )
            layers = receipt.get("verification_layers")
            if not isinstance(layers, Mapping):
                layers = {}
            receipt["verification_layers"] = {
                **dict(layers),
                "fresh_gui_reload": "not_verified",
            }
            self.details = receipt
        else:
            # CLI failures before or below Renderer startup must still be
            # machine-distinguishable from a passed real-WebView acceptance.
            # Do not echo arbitrary provider, native-window or filesystem
            # exception objects across this public boundary.
            self.details = {
                "contract": CONTRACT,
                "status": "failed",
                "error_code": str(reason)[:128],
                "error_message": str(message)[:1000],
                "verification_layers": {
                    "backend_host_chain": "not_verified",
                    "real_webview_dom": "not_verified",
                },
                "real_webview_dom_verified": False,
                "files_published": False,
                "reload_verified": False,
                "display_verified": False,
            }


def _fail(message: str, reason: str, details: object | None = None) -> None:
    raise GuiWebViewSmokeError(message, reason=reason, details=details)


def _json_script(value: Mapping[str, Any]) -> str:
    payload = json.dumps(
        dict(value),
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
    )
    return payload.replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")


def _bounded_receipt(value: object) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        _fail(
            "真实WebView没有返回机器可读回执",
            "webview_smoke_receipt_invalid",
        )
    receipt = copy.deepcopy(dict(value))
    if receipt.get("contract") != CONTRACT or receipt.get("status") not in _TERMINAL:
        _fail(
            "真实WebView返回的回执合同无效",
            "webview_smoke_receipt_invalid",
        )
    try:
        encoded = json.dumps(
            receipt,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError, OverflowError, RecursionError) as error:
        raise GuiWebViewSmokeError(
            "真实WebView返回的回执不是有限JSON",
            reason="webview_smoke_receipt_invalid",
        ) from error
    if len(encoded) > _MAX_RECEIPT_BYTES:
        _fail(
            "真实WebView回执超过64 KiB上限",
            "webview_smoke_receipt_oversized",
        )
    return receipt


def _write_progress_checkpoint(path: Path, value: Mapping[str, object]) -> None:
    """Persist only bounded public acceptance progress for live diagnosis.

    A real provider turn may run for many minutes.  If the WebView later
    stops settling, the terminal failure receipt alone cannot show which
    Renderer cursor/transport state was last observed.  This checkpoint is
    intentionally *not* a transcript: it excludes messages, reasoning,
    tool arguments, credentials, candidates and document content.
    """

    progress = value.get("progress")
    public = {
        "contract": CONTRACT,
        "status": str(value.get("status") or "running")[:64],
        "phase": str(value.get("phase") or "unknown")[:128],
        "session_id": (
            str(value["session_id"])[:256]
            if value.get("session_id") is not None
            else None
        ),
        "progress": copy.deepcopy(dict(progress))
        if isinstance(progress, Mapping)
        else {},
        "updated_at_monotonic": time.monotonic(),
    }
    try:
        encoded = json.dumps(
            public,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError, OverflowError, RecursionError):
        return
    if len(encoded) > _MAX_RECEIPT_BYTES:
        return
    temporary = path.with_name(f".{path.name}.tmp")
    try:
        temporary.write_bytes(encoded)
        temporary.replace(path)
    except OSError:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _read_json_object(path: Path) -> Mapping[str, object] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, Mapping) else None


def _result_receipt_identity(value: object) -> dict[str, object] | None:
    if not isinstance(value, Mapping):
        return None
    fields = (
        "plan_id",
        "revision",
        "input_fingerprint",
        "database_fingerprint",
        "database_snapshot_fingerprint",
    )
    return {
        key: value[key]
        for key in fields
        if isinstance(value.get(key), (str, int))
        and not isinstance(value.get(key), bool)
    }


def _release_identity(data_dir: Path, database_path: Path | None) -> dict[str, object]:
    from genshin_app.paths import web_dir
    from genshin_app.version import (
        APP_RELEASE_ID,
        APP_VERSION,
        DATABASE_RELEASE_ID,
        NATIVE_AGENT_DSH_SHELL_MANIFEST_SHA256,
        NATIVE_AGENT_RUNTIME_GENERATION,
        RELEASE_PAIR_ID,
    )

    selected = database_path or (data_dir / "genshin.db")
    game_version = None
    upstream_commit = None
    try:
        from genshin_db.queries import get_meta

        meta = get_meta(selected) if selected.is_file() else {}
        game_version = meta.get("game_version:release") or meta.get("game_version")
        upstream_commit = meta.get("upstream_commit:release") or meta.get(
            "upstream_commit"
        )
    except (OSError, ValueError, sqlite3.Error):
        pass
    return {
        "app_version": APP_VERSION,
        "app_release_id": APP_RELEASE_ID,
        "database_release_id": DATABASE_RELEASE_ID,
        "release_pair_id": RELEASE_PAIR_ID,
        "actual_database_game_version": game_version,
        "actual_database_upstream_commit": upstream_commit,
        "native_runtime_generation": NATIVE_AGENT_RUNTIME_GENERATION,
        "native_runtime_manifest_sha256": NATIVE_AGENT_DSH_SHELL_MANIFEST_SHA256,
        "static_index_sha256": _sha256_file(web_dir() / "index.html"),
    }


def _normalize_expectation(value: Mapping[str, Any] | None) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping) or set(value) - _EXPECTATION_KEYS:
        _fail("验收语义约束包含未知字段", "webview_smoke_expectation_invalid")
    result = copy.deepcopy(dict(value))
    title = result.get("title_contains")
    if title is not None and (
        not isinstance(title, str) or not title or len(title) > 256
    ):
        _fail("标题验收约束无效", "webview_smoke_expectation_invalid")
    for key, maximum in (
        ("required_character_ids", 8),
        ("required_action_type_ids", 32),
    ):
        rows = result.get(key, [])
        if not isinstance(rows, list) or len(rows) > maximum:
            _fail("验收列表无效或超限", "webview_smoke_expectation_invalid")
        if key == "required_character_ids":
            valid = all(type(row) is int and row > 0 for row in rows)
        else:
            valid = all(
                isinstance(row, str) and 0 < len(row) <= 128 for row in rows
            )
        if not valid or len(set(rows)) != len(rows):
            _fail("验收列表身份无效或重复", "webview_smoke_expectation_invalid")
    for key, maximum in (
        ("min_actions", 4096),
        ("min_revision_increment", 1_000_000),
        ("min_result_hits", 1_000_000),
    ):
        number = result.get(key)
        if number is not None and (
            type(number) is not int or not 0 <= number <= maximum
        ):
            _fail("验收数量约束无效", "webview_smoke_expectation_invalid")
    required = result.get("require_calculated")
    if required is not None and type(required) is not bool:
        _fail("正式计算验收开关无效", "webview_smoke_expectation_invalid")
    return result


def read_webview_expectation_file(
    path: str | Path, *, maximum_bytes: int = 64 * 1024
) -> dict[str, Any]:
    source = Path(path).expanduser().resolve()
    with source.open("rb") as stream:
        raw = stream.read(maximum_bytes + 1)
    if len(raw) > maximum_bytes:
        _fail("验收语义约束超过64 KiB", "webview_smoke_expectation_invalid")
    try:
        value = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, ValueError) as error:
        raise GuiWebViewSmokeError(
            "验收语义约束必须是UTF-8 JSON对象",
            reason="webview_smoke_expectation_invalid",
        ) from error
    return _normalize_expectation(value)


def _verify_success_receipt(
    receipt: dict[str, Any],
    *,
    board_path: Path,
    calculated_path: Path,
) -> None:
    status = receipt["status"]
    if status == "failed":
        _fail(
            str(receipt.get("error_message") or "真实WebView smoke失败"),
            str(receipt.get("error_code") or "webview_smoke_failed"),
            receipt,
        )
    if status == "awaiting_user_answer":
        if (
            receipt.get("real_webview_dom_verified") is not False
            or receipt.get("real_webview_dom_observed") is not True
        ):
            _fail(
                "等待用户回答的回执没有区分已观测WebView与完整DOM验证",
                "webview_smoke_receipt_invalid",
            )
        return
    layers = receipt.get("verification_layers")
    if (
        receipt.get("real_webview_dom_verified") is not True
        or not isinstance(layers, Mapping)
        or layers.get("real_webview_dom") != "verified"
        or layers.get("backend_host_chain") != "verified"
        or receipt.get("reload_verified") is not True
        or receipt.get("display_verified") is not True
        or receipt.get("candidate_review_rendered") is not True
    ):
        _fail(
            "回执没有证明真实WebView审核、重载和显示链路",
            "webview_smoke_verification_incomplete",
            receipt,
        )
    board = receipt.get("board")
    if (
        not isinstance(board, Mapping)
        or Path(str(board.get("path", ""))).resolve() != board_path
        or not board_path.is_file()
        or not isinstance(board.get("board_id"), str)
        or not board.get("board_id")
        or board.get("file_sha") != _sha256_file(board_path)
    ):
        _fail(
            "真实WebView没有发布并重载预期白板",
            "webview_smoke_board_publication_invalid",
            receipt,
        )
    board_payload = _read_json_object(board_path)
    if (
        not isinstance(board_payload, Mapping)
        or board_payload.get("kind") != "genshin-whiteboard"
        or board_payload.get("schema_version") != 3
        or board_payload.get("board_id") != board.get("board_id")
    ):
        _fail(
            "发布的白板文件未通过独立schema与身份检查",
            "webview_smoke_board_schema_invalid",
            receipt,
        )
    calculated = receipt.get("calculated")
    if status == "draft_verified":
        if calculated is not None or receipt.get("files_published") is not False:
            _fail(
                "阶段草稿错误地宣称发布计算结果",
                "webview_smoke_draft_receipt_invalid",
                receipt,
            )
        return
    if (
        not isinstance(calculated, Mapping)
        or Path(str(calculated.get("path", ""))).resolve() != calculated_path
        or not calculated_path.is_file()
        or calculated.get("file_sha")
        != _sha256_file(calculated_path)
        or not isinstance(calculated.get("result_receipt_identity"), Mapping)
        or receipt.get("files_published") is not True
    ):
        _fail(
            "真实WebView没有发布并显示预期计算结果",
            "webview_smoke_calculated_publication_invalid",
            receipt,
        )
    calculated_package = _read_json_object(calculated_path)
    calculated_payload = (
        calculated_package.get("calculated")
        if isinstance(calculated_package, Mapping)
        else None
    )
    if (
        not isinstance(calculated_package, Mapping)
        or calculated_package.get("package_schema") != PACKAGE_SCHEMA
        or calculated_package.get("kind") != PACKAGE_KIND
        or not isinstance(calculated_package.get("integrity"), Mapping)
        or not isinstance(calculated_payload, Mapping)
        or not isinstance(calculated_payload.get("plan"), Mapping)
        or not isinstance(calculated_payload.get("result"), Mapping)
        or _result_receipt_identity(calculated_payload.get("result_receipt"))
        != calculated.get("result_receipt_identity")
    ):
        _fail(
            "发布的计算包未通过独立schema与回执身份检查",
            "webview_smoke_calculated_schema_invalid",
            receipt,
        )


def _verify_with_fresh_gui_api(
    receipt: dict[str, Any],
    *,
    data_dir: Path,
    database_path: Path | None,
    app_identity: Mapping[str, Any] | None,
    api_factory: Callable[[Path], object] | None,
) -> None:
    if receipt.get("status") == "awaiting_user_answer":
        return
    if api_factory is None:
        from genshin_app.gui.api import GuiApi

        api_factory = lambda directory: GuiApi(
            directory,
            database_path=database_path,
            app_identity=app_identity,
            release_pair_coordinator=False,
        )

    board = receipt["board"]
    verifier = api_factory(data_dir)
    documents = GuiDocumentClient(verifier)
    try:
        opened = documents.call("whiteboard_load", board["path"])
        reloaded = opened.get("board") if isinstance(opened, Mapping) else None
        if (
            not isinstance(reloaded, Mapping)
            or reloaded.get("board_id") != board.get("board_id")
        ):
            _fail(
                "全新GuiApi无法读回同一白板身份",
                "webview_smoke_fresh_gui_reload_mismatch",
                receipt,
            )
        calculated = receipt.get("calculated")
        if calculated is not None:
            result = documents.call(
                "whiteboard_read_result",
                dict(reloaded),
                calculated["path"],
            )
            verified_calculated = result.get("calculated")
            if not isinstance(verified_calculated, Mapping):
                _fail(
                    "全新GuiApi无法读回完整计算包",
                    "webview_smoke_fresh_gui_result_missing",
                    receipt,
                )
            actual_identity = _result_receipt_identity(
                verified_calculated.get("result_receipt")
            )
            if actual_identity != calculated.get("result_receipt_identity"):
                _fail(
                    "全新GuiApi读回的计算回执身份与WebView不同",
                    "webview_smoke_fresh_gui_result_identity_mismatch",
                    receipt,
                )
    except GuiDocumentCallError as error:
        raise GuiWebViewSmokeError(
            "全新GuiApi回流核验失败",
            reason=error.reason,
            details={
                **receipt,
                "failure_stage": "fresh_gui_reload",
                "transport_details": error.details,
            },
        ) from error
    finally:
        close = getattr(verifier, "confirm_close", None)
        if callable(close):
            try:
                close()
            except Exception:
                logging.getLogger(__name__).debug(
                    "全新GuiApi回流核验关闭失败",
                    exc_info=True,
                )
    receipt.setdefault("verification_layers", {})["fresh_gui_reload"] = "verified"
    receipt["fresh_gui_reload_verified"] = True


def run_gui_webview_smoke(
    *,
    data_dir: str | Path,
    prompt: str,
    output_dir: str | Path,
    input_path: str | Path | None = None,
    workflow: str = "artifact",
    model: str | None = None,
    effort: str | None = None,
    timeout_seconds: int = 1800,
    allow_draft: bool = False,
    visible: bool = False,
    provider_mode: str = "saved_credential",
    expectation: Mapping[str, Any] | None = None,
    database_path: str | Path | None = None,
    app_identity: Mapping[str, Any] | None = None,
    dsh_shell_factory: Callable[[Path], object] | None = None,
    fresh_api_factory: Callable[[Path], object] | None = None,
    launcher: Callable[..., int] | None = None,
    monotonic: Callable[[], float] = time.monotonic,
    poll_interval_seconds: float = 0.1,
) -> dict[str, Any]:
    """Run one full native-Agent turn in the production WebView bundle.

    Deterministic mode requires an explicitly injected complete DSH shell.
    The output directory is the caller's exact test Save As selection; the
    driver clicks the real App button and cannot choose another output path.
    """

    if not isinstance(prompt, str) or not prompt.strip():
        _fail("任务文本不能为空", "webview_smoke_prompt_required")
    if workflow not in {"artifact", "build", "calculate", "staged"}:
        _fail("工作流类型不受支持", "webview_smoke_invalid_input")
    if effort is not None and (not isinstance(effort, str) or not effort):
        _fail("推理档位不能为空", "webview_smoke_invalid_input")
    if type(timeout_seconds) is not int or not 1 <= timeout_seconds <= 86_400:
        _fail("WebView smoke时限必须为1到86400秒", "webview_smoke_invalid_input")
    if provider_mode not in _PROVIDER_MODES:
        _fail("提供方验收模式不受支持", "webview_smoke_invalid_input")
    normalized_expectation = _normalize_expectation(expectation)
    identity = dict(app_identity) if app_identity is not None else {}
    if provider_mode == "deterministic_provider_transport" and (
        dsh_shell_factory is None
        or identity.get("provider_transport") != "deterministic"
    ):
        _fail(
            "确定性模式需要显式注入仅替换提供方传输的完整Agent composition",
            "webview_smoke_deterministic_composition_required",
        )

    directory = Path(data_dir).expanduser().resolve()
    target = Path(output_dir).expanduser().resolve()
    if target.exists():
        _fail(
            "输出目录已经存在；不会覆盖或混入旧结果",
            "webview_smoke_output_exists",
            {"output_dir": str(target)},
        )
    target.mkdir(parents=True, exist_ok=False)
    board_path = target / "result.whiteboard.json"
    calculated_path = target / "result.calculated.json"
    progress_path = target / "webview-smoke-progress.json"
    selected_input = (
        str(Path(input_path).expanduser().resolve())
        if input_path is not None
        else None
    )
    holder: dict[str, object] = {}
    deadline = monotonic() + timeout_seconds

    def main_window_callback(window: object, api: object) -> None:
        try:
            evaluate = getattr(window, "evaluate_js", None)
            if not callable(evaluate):
                _fail(
                    "PyWebView窗口缺少JavaScript执行接口",
                    "webview_smoke_javascript_unavailable",
                )
            while monotonic() < deadline:
                try:
                    available = evaluate(
                        "Boolean(window.GenshinWebviewSmoke && "
                        "typeof window.GenshinWebviewSmoke.start === 'function')"
                    )
                except Exception:  # noqa: BLE001 - page may still be loading
                    available = False
                if available is True:
                    break
                time.sleep(poll_interval_seconds)
            else:
                _fail(
                    "生产GUI脚本没有加载WebView smoke入口",
                    "webview_smoke_bundle_not_loaded",
                )

            remaining_ms = max(1_000, int((deadline - monotonic()) * 1000))
            config = {
                "prompt": prompt.strip(),
                "input_path": selected_input,
                "board_path": str(board_path),
                "calculated_path": str(calculated_path),
                "workflow": workflow,
                "model": model,
                "effort": effort,
                "allow_draft": allow_draft,
                "timeout_ms": remaining_ms,
                "provider_mode": provider_mode,
                "expectation": normalized_expectation,
            }
            started = evaluate(
                "window.GenshinWebviewSmoke.start(" + _json_script(config) + ")"
            )
            if started is not True:
                _fail(
                    "真实WebView smoke没有启动或被重复启动",
                    "webview_smoke_start_rejected",
                )
            last_snapshot: Mapping[str, object] | None = None
            last_progress_write = 0.0
            while monotonic() < deadline:
                raw = evaluate(
                    "JSON.stringify(window.GenshinWebviewSmoke.snapshot())"
                )
                try:
                    snapshot = json.loads(raw) if isinstance(raw, str) else None
                except (TypeError, ValueError, RecursionError):
                    snapshot = None
                if isinstance(snapshot, Mapping):
                    previous_snapshot = last_snapshot
                    last_snapshot = snapshot
                    checkpoint_snapshot = snapshot
                    if (
                        not isinstance(snapshot.get("progress"), Mapping)
                        and isinstance(previous_snapshot, Mapping)
                        and isinstance(previous_snapshot.get("progress"), Mapping)
                    ):
                        checkpoint_snapshot = {
                            **dict(snapshot),
                            "phase": snapshot.get("phase")
                            or previous_snapshot.get("phase"),
                            "session_id": snapshot.get("session_id")
                            or previous_snapshot.get("session_id"),
                            "progress": previous_snapshot.get("progress"),
                        }
                    now = monotonic()
                    if now - last_progress_write >= 1.0:
                        _write_progress_checkpoint(
                            progress_path, checkpoint_snapshot
                        )
                        last_progress_write = now
                    if snapshot.get("status") in _TERMINAL:
                        _write_progress_checkpoint(
                            progress_path, checkpoint_snapshot
                        )
                        holder["receipt"] = _bounded_receipt(snapshot)
                        break
                time.sleep(poll_interval_seconds)
            else:
                progress = (
                    last_snapshot.get("progress")
                    if isinstance(last_snapshot, Mapping)
                    and isinstance(last_snapshot.get("progress"), Mapping)
                    else {}
                )
                holder["receipt"] = {
                    "contract": CONTRACT,
                    "status": "failed",
                    "error_code": "webview_smoke_timeout",
                    "error_message": "真实WebView smoke在指定时限内没有返回终态。",
                    "failure_stage": (
                        str(last_snapshot.get("phase", "unknown"))[:64]
                        if isinstance(last_snapshot, Mapping)
                        else "unknown"
                    ),
                    **(
                        {"session_id": str(last_snapshot["session_id"])[:256]}
                        if isinstance(last_snapshot, Mapping)
                        and isinstance(last_snapshot.get("session_id"), str)
                        else {}
                    ),
                    "progress": {
                        str(key)[:64]: str(value)[:128]
                        for key, value in progress.items()
                    },
                    "verification_layers": {
                        "backend_host_chain": "not_verified",
                        "real_webview_dom": "not_verified",
                    },
                    "real_webview_dom_verified": False,
                    "files_published": False,
                    "reload_verified": False,
                    "display_verified": False,
                }
        except GuiWebViewSmokeError as error:
            holder["error"] = error
        except Exception as error:  # noqa: BLE001 - native GUI callback boundary
            holder["error"] = GuiWebViewSmokeError(
                "真实WebView smoke控制器异常",
                reason="webview_smoke_controller_failed",
                details={"error_type": type(error).__name__},
            )
        finally:
            close = getattr(api, "confirm_close", None)
            if callable(close):
                try:
                    close()
                except Exception:
                    logging.getLogger(__name__).debug(
                        "WebView smoke关闭请求失败；交由GUI launcher最终清理",
                        exc_info=True,
                    )

    if launcher is None:
        from genshin_app.gui.launcher import run_gui

        launcher = run_gui
    # The caller has explicitly selected these acceptance paths. Substitute
    # only the native picker result, preserving every real App file operation.
    # No model tool is given this callback or a general filesystem capability.
    open_selections = ([str(Path(input_path).expanduser().resolve())]
                       if input_path is not None else []) + [str(board_path)]

    def pick_acceptance_file(request: Mapping[str, object]) -> str:
        action, kind = request.get("action"), request.get("document_kind")
        if action == "open" and kind == "calculated":
            return str(calculated_path)
        if action == "open" and kind in {"native", "whiteboard"} and open_selections:
            return open_selections.pop(0)
        if action == "save" and kind == "whiteboard":
            return str(board_path)
        raise GuiWebViewSmokeError(
            "验收没有授权这次文件选择",
            reason="webview_smoke_file_selection_unexpected",
        )

    try:
        launcher(
            directory,
            database_path=(
                Path(database_path).expanduser().resolve()
                if database_path is not None
                else None
            ),
            app_identity=identity or None,
            dsh_shell_factory=dsh_shell_factory,
            dsh_result_save_dialog=lambda _name: str(calculated_path),
            dsh_file_dialog=pick_acceptance_file,
            main_window_callback=main_window_callback,
            window_hidden=not visible,
            emit_startup_status=False,
        )
    except GuiWebViewSmokeError:
        raise
    except Exception as error:
        raise GuiWebViewSmokeError(
            str(error) or "真实WebView启动失败",
            reason=str(getattr(error, "reason", "webview_smoke_launcher_failed")),
            details=getattr(error, "details", None),
        ) from error
    callback_error = holder.get("error")
    if isinstance(callback_error, GuiWebViewSmokeError):
        raise callback_error
    receipt = _bounded_receipt(holder.get("receipt"))
    _verify_success_receipt(
        receipt,
        board_path=board_path,
        calculated_path=calculated_path,
    )
    if receipt.get("status") == "awaiting_user_answer":
        return receipt
    _verify_with_fresh_gui_api(
        receipt,
        data_dir=directory,
        database_path=(
            Path(database_path).expanduser().resolve()
            if database_path is not None
            else None
        ),
        app_identity=identity or None,
        api_factory=fresh_api_factory,
    )
    layers = receipt.get("verification_layers")
    required_layers = {
        "gui_user_controls",
        "backend_host_chain",
        "candidate_review_application",
        "file_hash_schema_identity",
        "webview_reload",
        "fresh_gui_reload",
        "real_webview_dom",
    }
    if (
        not isinstance(layers, Mapping)
        or any(layers.get(key) != "verified" for key in required_layers)
    ):
        _fail(
            "真实WebView验收缺少完整分层证据",
            "webview_smoke_verification_incomplete",
            receipt,
        )
    if normalized_expectation:
        if receipt.get("semantic_expectations_verified") is not True:
            _fail(
                "真实WebView没有证明题面语义约束",
                "webview_smoke_semantic_verification_missing",
                receipt,
            )
        receipt["acceptance_level"] = "semantic_acceptance"
    else:
        receipt["semantic_expectations_verified"] = False
        receipt["semantic_expectation_status"] = "not_requested"
        receipt["acceptance_level"] = "transport_diagnostic"
    receipt["release_identity"] = _release_identity(
        directory,
        (
            Path(database_path).expanduser().resolve()
            if database_path is not None
            else None
        ),
    )
    return receipt


__all__ = [
    "CONTRACT",
    "GuiWebViewSmokeError",
    "read_webview_expectation_file",
    "run_gui_webview_smoke",
]
