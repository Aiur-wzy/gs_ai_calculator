from __future__ import annotations

import hashlib
import json

import pytest

from genshin_app.agent_chat.native_dsh_checkpoint import (
    compare_native_dsh_checkpoints,
    normalize_native_dsh_checkpoint,
    require_native_dsh_checkpoint_progress,
)
from genshin_app.equipment.schema import PlanError


def _checkpoint(
    *,
    session_id: str = "dsh-session-1",
    event_count: int = 2,
    size_bytes: int = 256,
    composition_hash: str = "a" * 64,
) -> dict:
    row = {
        "contract": "genshin-native-dsh-checkpoint-v1",
        "session_id": session_id,
        "storage_format": "dsh-jsonl-zstd-v1",
        "composition_hash": composition_hash,
        "revision": f"revision-{event_count}",
        "last_event_seq": None if event_count == 0 else event_count - 1,
        "event_count": event_count,
        "size_bytes": size_bytes,
    }
    return {
        **row,
        "flush_digest": hashlib.sha256(
            json.dumps(row, separators=(",", ":")).encode()
        ).hexdigest(),
    }


def test_checkpoint_is_path_free_and_bound_to_exact_session():
    checked = normalize_native_dsh_checkpoint(
        _checkpoint(), expected_session_id="dsh-session-1"
    )

    assert "path" not in checked
    with pytest.raises(PlanError, match="不属于当前会话"):
        normalize_native_dsh_checkpoint(
            checked, expected_session_id="dsh-session-2"
        )


def test_checkpoint_rejects_forged_flush_digest():
    forged = _checkpoint()
    forged["flush_digest"] = "f" * 64

    with pytest.raises(PlanError, match="刷新摘要不匹配"):
        normalize_native_dsh_checkpoint(forged)


@pytest.mark.parametrize(
    "current",
    [
        _checkpoint(event_count=1, size_bytes=256),
        _checkpoint(event_count=2, size_bytes=128),
        _checkpoint(event_count=2, size_bytes=256, composition_hash="b" * 64),
    ],
)
def test_checkpoint_rejects_event_size_or_composition_regression(current):
    with pytest.raises(PlanError, match="回退|组合已变化"):
        compare_native_dsh_checkpoints(_checkpoint(), current)


def test_terminal_checkpoint_must_prove_a_new_durable_event():
    baseline = _checkpoint()

    with pytest.raises(PlanError, match="没有"):
        require_native_dsh_checkpoint_progress(baseline, baseline)

    require_native_dsh_checkpoint_progress(
        baseline,
        _checkpoint(event_count=3, size_bytes=384),
    )
