"""Persistence service for whiteboard documents."""
from __future__ import annotations

import copy
import hashlib
import json
import re
from pathlib import Path

from genshin_app.equipment.files import fingerprint, publish_files
from genshin_app.equipment.schema import PlanError, _json_guard
from genshin_app.query_cache import protect_inputs
from genshin_app.whiteboard_identity import intent_view
from genshin_app.whiteboard_revision import validate_whiteboard_revision_transition
from genshin_app.whiteboard_schema import (
    _V1_BOARD_KEYS,
    _V2_BOARD_KEYS,
    KIND,
    MAX_BYTES,
    SCHEMA_VERSION,
    _fail,
    _fields,
    validate_whiteboard,
    whiteboard_response,
)
from genshin_runtime.diagnostics import diagnosed


def _read(path, maximum=MAX_BYTES):
    path = Path(path).resolve()
    from genshin_app.equipment.files import read_input_bytes
    raw = read_input_bytes(path, maximum_bytes=maximum)
    if len(raw) > maximum:
        _fail("文件超过字节上限", str(path))
    try:
        value = json.loads(raw.decode("utf-8-sig"))
        _json_guard(value)
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise PlanError("文件不是有限JSON", "invalid_whiteboard") from exc
    return value, hashlib.sha256(raw).hexdigest(), str(path)


def _upgrade_loaded_board(value):
    """Validate a saved board and upgrade v1/v2 only in the returned memory value."""
    if not isinstance(value, dict):
        _fail("白板根节点必须是对象")
    source_version = value.get("schema_version")
    if source_version == 1:
        _fields(value, _V1_BOARD_KEYS, "whiteboard", {"provisional_entities"})
        if value.get("kind") != KIND:
            _fail("不是受支持的排轴白板")
        board = copy.deepcopy(value)
        board["schema_version"] = SCHEMA_VERSION
        board["capacity_intents"] = []
        board["action_admission_intents"] = []
    elif source_version == 2:
        _fields(value, _V2_BOARD_KEYS, "whiteboard", {"provisional_entities"})
        if value.get("kind") != KIND:
            _fail("不是受支持的排轴白板")
        board = copy.deepcopy(value)
        board["schema_version"] = SCHEMA_VERSION
        board["action_admission_intents"] = []
    elif source_version == SCHEMA_VERSION:
        board = copy.deepcopy(value)
    else:
        _fail("不是受支持的排轴白板")
    board.setdefault("provisional_entities", [])
    validate_whiteboard(board)
    return board, source_version


@diagnosed('whiteboard', 'load-whiteboard-v1', result_metadata=lambda _arguments, result: {
    "board_id": result["board"]["board_id"], "revision": result["board"]["revision"],
    "intent_fingerprint": result["board_fingerprint"]})
def load_whiteboard(path):
    saved, sha, path = _read(path)
    board, source_version = _upgrade_loaded_board(saved)
    response = {**whiteboard_response(board), "file_sha": sha, "path": path,
                "source_schema_version": source_version}
    if source_version in (1, 2):
        response["source_board_fingerprint"] = fingerprint(intent_view(saved))
    return response


@diagnosed('whiteboard', 'save-whiteboard-v1', result_metadata=lambda _arguments, result: {
    "board_id": result["board"]["board_id"], "revision": result["board"]["revision"],
    "intent_fingerprint": result["board_fingerprint"]})
def save_whiteboard(path, board, expected_file_sha=None, *, db_path=None):
    response = whiteboard_response(board)
    path = Path(path).resolve()
    if path.suffix.lower() != ".json":
        _fail("白板需要保存为JSON文件", str(path), "file_conflict")
    protect_inputs([path], [db_path] if db_path is not None else [])
    existing_board, existing_sha = None, None
    if path.exists():
        # A known SHA does not authorize replacing a database or native plan.
        existing, existing_sha, _ = _read(path)
        existing_board, _ = _upgrade_loaded_board(existing)
    if expected_file_sha is not None and (not isinstance(expected_file_sha, str) or not re.fullmatch(r"[0-9a-f]{64}", expected_file_sha)):
        _fail("保存回执必须为64位SHA256", reason="file_conflict")
    transition, replacement = None, None
    if existing_board is not None and expected_file_sha == existing_sha:
        if existing_board["board_id"] == board["board_id"]:
            transition = validate_whiteboard_revision_transition(existing_board, board)
        else:
            # Explicit file-SHA replacement is the existing Save-As contract,
            # not a same-document revision or authority to replace other formats.
            replacement = {"previous_board_id": existing_board["board_id"],
                           "board_id": board["board_id"],
                           "authorization": "explicit_expected_file_sha"}
    published = publish_files([(path, json.dumps(board, ensure_ascii=False, indent=2, allow_nan=False).encode("utf-8"))],
                              expected_shas={path: expected_file_sha},
                              input_paths=[db_path] if db_path is not None else [])
    return {**response, "path": str(path), "file_sha": published["file_shas"][str(path)],
            **({"revision_transition": transition} if transition is not None else {}),
            **({"document_replacement": replacement} if replacement is not None else {})}
