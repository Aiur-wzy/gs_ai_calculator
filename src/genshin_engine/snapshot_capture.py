"""Bounded action-dependent observations; no game timing inference."""
from __future__ import annotations

import copy
import hashlib
import json

from genshin_engine.rotation_schema import EVIDENCE, STRING, obj

MAX_SNAPSHOT_CAPTURES = 256
MAX_SNAPSHOT_CAPTURE_DECLARATION_BYTES = 128 * 1024
MAX_SNAPSHOT_CAPTURE_BYTES = 8 * 1024 * 1024
CAPTURE_SCHEMA = obj({"id": STRING, "action_id": STRING,
                      "offset": {"type": "number", "minimum": 0},
                      "precision": {"type": "number", "minimum": 0}, **EVIDENCE},
                     ["id", "action_id", "offset", "precision", "status", "source"])


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf8")


def fingerprint(value):
    return hashlib.sha256(canonical(value)).hexdigest()


class SnapshotCapturePending(Exception):
    def __init__(self, code, message, path, **details):
        self.issue = {"code": code, "message": message, "path": path,
                      "severity": "pending", "details": details}
        super().__init__(message)


def snapshot_capture_capabilities():
    return {"contract": "action-snapshot-capture-v1", "field": "snapshot_captures",
            "event_type": "mechanics_snapshot_capture", "declaration_schema": copy.deepcopy(CAPTURE_SCHEMA),
            "max_declarations": MAX_SNAPSHOT_CAPTURES,
            "max_declaration_bytes": MAX_SNAPSHOT_CAPTURE_DECLARATION_BYTES,
            "max_capture_bytes": MAX_SNAPSHOT_CAPTURE_BYTES,
            "storage_accounting": "累计规范JSON数组：每次捕获的{snapshot,receipt}及每次引用回执；含[]与逗号，跨检查窗口不重置。",
            "ordering": "成功父动作全部pre/post处理完成后入原队列，继承order与因果次序；不触发机制监听。",
            "scope": "每动作至多一次；0<=offset<=占用时长，绝对时点小于输入窗口右端；precision>0，仅声明精度。",
            "cancellation": "成功切出或同actor成功后继非wait先于未执行捕获则pending；已经捕获的值保留。",
            "evidence": "assumed/confirmed均为声明，不代表引擎完成游戏取样实测。"}
