"""Authenticated Node→Python import bytes, never a model-owned permission field."""
from __future__ import annotations

import base64
import hashlib
from contextlib import contextmanager
from contextvars import ContextVar

from genshin_app.equipment.schema import PlanError

_CURRENT = ContextVar("native_game_input_file_snapshot", default=None)
MAX_BYTES = 64 * 1024 * 1024


@contextmanager
def native_input_snapshot_scope(snapshot):
    """Only the authenticated reverse-domain carrier installs this scope."""
    token = _CURRENT.set({"snapshot": snapshot})
    try:
        yield
    finally:
        _CURRENT.reset(token)


def native_input_snapshot(request):
    context = _CURRENT.get()
    if context is None:
        return None  # Independent CLI file caller retains its ordinary authority.
    snapshot = context["snapshot"]
    fields = {"contract", "requested_path", "path", "file_sha", "bytes", "content_base64"}
    if (not isinstance(snapshot, dict) or set(snapshot) != fields
            or snapshot["contract"] != "dsh-game-input-file-snapshot-v1"
            or snapshot["requested_path"] != request.get("file_path")
            or not isinstance(snapshot["path"], str) or not snapshot["path"]
            or type(snapshot["bytes"]) is not int or not 0 <= snapshot["bytes"] <= MAX_BYTES
            or not isinstance(snapshot["content_base64"], str)
            or len(snapshot["content_base64"]) > ((MAX_BYTES + 2) // 3) * 4):
        raise PlanError("原生导入必须使用DSH文件服务捕获的完整快照，候选未修改", "input_import_snapshot_required")
    try:
        raw = base64.b64decode(snapshot["content_base64"], validate=True)
    except (ValueError, TypeError) as exc:
        raise PlanError("DSH输入快照格式不合法", "input_import_snapshot_invalid") from exc
    actual = hashlib.sha256(raw).hexdigest()
    if len(raw) != snapshot["bytes"] or actual != snapshot["file_sha"]:
        raise PlanError("DSH输入快照字节与SHA不一致", "input_import_snapshot_invalid")
    expected = request.get("expected_file_sha")
    if expected is not None and expected != actual:
        raise PlanError("输入文件SHA已变化，候选未修改", "file_conflict", {"expected": expected, "actual": actual})
    return {"raw": raw, **{key: snapshot[key] for key in ("path", "file_sha", "bytes")}}
