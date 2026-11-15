"""Production gateway invariants, including a real private-pipe write race."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from tests.pinned_native_node import pinned_native_node

CASES = ("reading_duplicate", "pending_duplicate", "cancel", "close", "pause", "send_failure",
         "next_failure", "end", "uplink_end", "cross_stream", "synchronous_reentry", "terminal_send_failure",
         "reentrant_send_failure", "real_pipe")
ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("case", CASES)
def test_single_credit_has_one_bounded_sending_lookahead(case):
    node = pinned_native_node()
    completed = subprocess.run([str(node), str(ROOT / "agent-runtime/src/native-dsh-mux-credit-probe.mjs"), case],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8", check=False, timeout=60,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    assert completed.returncode == 0, completed.stderr
    evidence = json.loads(completed.stdout)
    assert evidence["status"] == "passed" and evidence["case"] == case
    assert evidence["single_pending_window"] and evidence["no_concurrent_next"]
    assert evidence["provider_used"] is False
    if case == "real_pipe":
        assert evidence["real_named_pipe"] and evidence["real_carrier_writeNext"] and evidence["visible_before_callback"]
        assert evidence["control_priority_interleaving"]
