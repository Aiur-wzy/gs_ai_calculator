import hashlib
import json
from pathlib import Path

import pytest

from genshin_app.gui.webview_smoke import (
    CONTRACT,
    GuiWebViewSmokeError,
    run_gui_webview_smoke,
)


class _Api:
    def __init__(self):
        self.closed = 0

    def confirm_close(self):
        self.closed += 1
        return {"code": 0, "data": {"closed": True}}


class _Window:
    def __init__(self, receipt):
        self.receipt = receipt
        self.started = 0

    def evaluate_js(self, script):
        if script.startswith("Boolean(window.GenshinWebviewSmoke"):
            return True
        if script.startswith("window.GenshinWebviewSmoke.start("):
            self.started += 1
            return True
        if script == "JSON.stringify(window.GenshinWebviewSmoke.snapshot())":
            return json.dumps(self.receipt, ensure_ascii=False)
        raise AssertionError(script)


class _FreshApi:
    def document_json_call(self, method, _args):
        if method == "whiteboard_load":
            data = {
                "board": {
                    "kind": "genshin-whiteboard",
                    "schema_version": 3,
                    "board_id": "board-one",
                }
            }
        elif method == "whiteboard_read_result":
            data = {
                "calculated": {
                    "result_receipt": {"plan_id": "plan-one", "revision": 4}
                }
            }
        else:
            raise AssertionError(method)
        return json.dumps({"code": 0, "data": data}, ensure_ascii=False)

    def confirm_close(self):
        return {"code": 0, "data": {"closed": True}}


class _MismatchedFreshApi(_FreshApi):
    def document_json_call(self, method, args):
        if method == "whiteboard_load":
            return json.dumps(
                {
                    "code": 0,
                    "data": {
                        "board": {
                            "kind": "genshin-whiteboard",
                            "schema_version": 3,
                            "board_id": "different-board",
                        }
                    },
                }
            )
        return super().document_json_call(method, args)


def _receipt(target: Path):
    return {
        "contract": CONTRACT,
        "status": "verified",
        "session_id": "session-one",
        "provider_mode": "saved_credential",
        "revision": 4,
        "receipt_fingerprint": "receipt-four",
        "candidate_review_rendered": True,
        "candidate_application_kind": "whiteboard_calculated",
        "board": {
            "path": str(target / "result.whiteboard.json"),
            "file_sha": "board-sha",
            "board_id": "board-one",
        },
        "calculated": {
            "path": str(target / "result.calculated.json"),
            "file_sha": "calculated-sha",
            "result_receipt_identity": {"plan_id": "plan-one", "revision": 4},
        },
        "files_published": True,
        "reload_verified": True,
        "display_verified": True,
        "semantic_expectations_verified": False,
        "semantic_expectation_status": "not_requested",
        "verification_layers": {
            "gui_user_controls": "verified",
            "backend_host_chain": "verified",
            "candidate_review_application": "verified",
            "file_hash_schema_identity": "verified",
            "webview_reload": "verified",
            "real_webview_dom": "verified",
        },
        "real_webview_dom_verified": True,
        "dom": {
            "bundle": "production_index",
            "candidate_review": True,
            "whiteboard_summary": True,
            "damage_view": True,
        },
    }


def _write_valid_packages(target: Path) -> None:
    (target / "result.whiteboard.json").write_text(
        json.dumps(
            {
                "kind": "genshin-whiteboard",
                "schema_version": 3,
                "board_id": "board-one",
            }
        ),
        encoding="utf-8",
    )
    (target / "result.calculated.json").write_text(
        json.dumps(
            {
                "package_schema": "genshin.calculated-package.v1",
                "kind": "historical_calculation_snapshot",
                "integrity": {},
                "calculated": {
                    "plan": {},
                    "result": {},
                    "result_receipt": {"plan_id": "plan-one", "revision": 4},
                },
            }
        ),
        encoding="utf-8",
    )


def test_webview_smoke_uses_launcher_callback_and_requires_real_dom_receipt(tmp_path):
    target = tmp_path / "published"
    receipt = _receipt(target)
    window = _Window(receipt)
    api = _Api()
    captured = {}

    def launcher(data_dir, **kwargs):
        captured.update(data_dir=Path(data_dir), **kwargs)
        _write_valid_packages(target)
        receipt["board"]["file_sha"] = hashlib.sha256(
            (target / "result.whiteboard.json").read_bytes()
        ).hexdigest()
        receipt["calculated"]["file_sha"] = hashlib.sha256(
            (target / "result.calculated.json").read_bytes()
        ).hexdigest()
        kwargs["main_window_callback"](window, api)
        return 0

    result = run_gui_webview_smoke(
        data_dir=tmp_path / "profile",
        prompt="完成一次计算",
        output_dir=target,
        workflow="calculate",
        visible=False,
        launcher=launcher,
        fresh_api_factory=lambda _directory: _FreshApi(),
    )

    assert result["contract"] == receipt["contract"]
    assert result["session_id"] == receipt["session_id"]
    assert result["real_webview_dom_verified"] is True
    assert result["verification_layers"]["fresh_gui_reload"] == "verified"
    assert result["acceptance_level"] == "transport_diagnostic"
    assert captured["window_hidden"] is True
    assert captured["emit_startup_status"] is False
    assert captured["database_path"] is None
    assert captured["dsh_result_save_dialog"]("suggested.json") == str(target / "result.calculated.json")
    picker = captured["dsh_file_dialog"]
    assert picker({"action": "save", "document_kind": "whiteboard"}) == str(target / "result.whiteboard.json")
    assert picker({"action": "open", "document_kind": "native"}) == str(target / "result.whiteboard.json")
    assert picker({"action": "open", "document_kind": "calculated"}) == str(target / "result.calculated.json")
    with pytest.raises(GuiWebViewSmokeError, match="验收没有授权"):
        picker({"action": "export", "document_kind": "native"})
    assert captured["dsh_shell_factory"] is None
    assert "agent_manager_factory" not in captured
    assert window.started == 1 and api.closed == 1
    checkpoint = json.loads(
        (target / "webview-smoke-progress.json").read_text(encoding="utf-8")
    )
    assert checkpoint["contract"] == CONTRACT
    assert checkpoint["status"] == "verified"
    assert set(checkpoint) == {
        "contract",
        "status",
        "phase",
        "session_id",
        "progress",
        "updated_at_monotonic",
    }
    assert "reasoning" not in json.dumps(checkpoint).lower()


def test_webview_smoke_never_promotes_backend_only_failure_to_dom_success(tmp_path):
    target = tmp_path / "failed"
    window = _Window({
        "contract": CONTRACT,
        "status": "failed",
        "error_code": "webview_smoke_result_not_displayed",
        "error_message": "结果没有显示",
        "verification_layers": {
            "backend_host_chain": "verified",
            "real_webview_dom": "not_verified",
        },
        "real_webview_dom_verified": False,
    })

    def launcher(_data_dir, **kwargs):
        kwargs["main_window_callback"](window, _Api())
        return 0

    with pytest.raises(GuiWebViewSmokeError) as failure:
        run_gui_webview_smoke(
            data_dir=tmp_path / "profile",
            prompt="测试显示",
            output_dir=target,
            launcher=launcher,
        )
    assert failure.value.reason == "webview_smoke_result_not_displayed"
    assert failure.value.details["real_webview_dom_verified"] is False


def test_fresh_gui_reload_rejects_a_different_board_identity(tmp_path):
    target = tmp_path / "published"
    receipt = _receipt(target)
    window = _Window(receipt)

    def launcher(_data_dir, **kwargs):
        board = target / "result.whiteboard.json"
        calculated = target / "result.calculated.json"
        _write_valid_packages(target)
        receipt["board"]["file_sha"] = hashlib.sha256(board.read_bytes()).hexdigest()
        receipt["calculated"]["file_sha"] = hashlib.sha256(
            calculated.read_bytes()
        ).hexdigest()
        kwargs["main_window_callback"](window, _Api())
        return 0

    with pytest.raises(GuiWebViewSmokeError) as failure:
        run_gui_webview_smoke(
            data_dir=tmp_path / "profile",
            prompt="完成一次计算",
            output_dir=target,
            launcher=launcher,
            fresh_api_factory=lambda _directory: _MismatchedFreshApi(),
        )
    assert failure.value.reason == "webview_smoke_fresh_gui_reload_mismatch"


def test_python_boundary_rejects_invalid_package_before_fake_fresh_api(tmp_path):
    target = tmp_path / "published"
    receipt = _receipt(target)
    window = _Window(receipt)

    def launcher(_data_dir, **kwargs):
        board = target / "result.whiteboard.json"
        calculated = target / "result.calculated.json"
        board.write_text("{}", encoding="utf-8")
        calculated.write_text("{}", encoding="utf-8")
        receipt["board"]["file_sha"] = hashlib.sha256(board.read_bytes()).hexdigest()
        receipt["calculated"]["file_sha"] = hashlib.sha256(
            calculated.read_bytes()
        ).hexdigest()
        kwargs["main_window_callback"](window, _Api())
        return 0

    with pytest.raises(GuiWebViewSmokeError) as failure:
        run_gui_webview_smoke(
            data_dir=tmp_path / "profile",
            prompt="完成一次计算",
            output_dir=target,
            launcher=launcher,
            fresh_api_factory=lambda _directory: _FreshApi(),
        )
    assert failure.value.reason == "webview_smoke_board_schema_invalid"


def test_user_question_is_suspension_not_full_dom_verification(tmp_path):
    target = tmp_path / "question"
    receipt = {
        "contract": CONTRACT,
        "status": "awaiting_user_answer",
        "requires_user_input": True,
        "real_webview_dom_observed": True,
        "real_webview_dom_verified": False,
        "verification_layers": {
            "backend_host_chain": "suspended_user_input",
            "real_webview_dom": "observed_before_user_input",
        },
    }
    window = _Window(receipt)

    def launcher(_data_dir, **kwargs):
        kwargs["main_window_callback"](window, _Api())
        return 0

    result = run_gui_webview_smoke(
        data_dir=tmp_path / "profile",
        prompt="ask before changing",
        output_dir=target,
        launcher=launcher,
    )

    assert result["status"] == "awaiting_user_answer"
    assert result["real_webview_dom_observed"] is True
    assert result["real_webview_dom_verified"] is False


def test_deterministic_mode_requires_full_explicit_composition_marker(tmp_path):
    with pytest.raises(GuiWebViewSmokeError) as failure:
        run_gui_webview_smoke(
            data_dir=tmp_path / "profile",
            prompt="fixture",
            output_dir=tmp_path / "output",
            provider_mode="deterministic_provider_transport",
            dsh_shell_factory=lambda _path: object(),
            app_identity={"provider_transport": "something_else"},
            launcher=lambda *_args, **_kwargs: 0,
        )
    assert failure.value.reason == "webview_smoke_deterministic_composition_required"


def test_webview_launcher_failure_is_explicit_failure_not_skip_or_pass(tmp_path):
    def launcher(*_args, **_kwargs):
        raise RuntimeError("native window unavailable")

    with pytest.raises(GuiWebViewSmokeError) as failure:
        run_gui_webview_smoke(
            data_dir=tmp_path / "profile",
            prompt="fixture",
            output_dir=tmp_path / "output",
            launcher=launcher,
        )

    assert failure.value.reason == "webview_smoke_launcher_failed"
    assert failure.value.details["status"] == "failed"
    assert failure.value.details["real_webview_dom_verified"] is False
    assert failure.value.details["verification_layers"] == {
        "backend_host_chain": "not_verified",
        "real_webview_dom": "not_verified",
    }
