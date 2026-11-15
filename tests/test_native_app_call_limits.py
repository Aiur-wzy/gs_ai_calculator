"""Receipt retention is capacity reuse, not a new per-task cutoff."""
from __future__ import annotations

import re
from pathlib import Path

from genshin_app.agent_chat.native_app_call_limits import MAX_APP_CALLS

ROOT = Path(__file__).resolve().parents[1]


def test_app_capacity_and_node_receipt_retention_share_existing_value():
    catalog = (ROOT / "agent-runtime/src/native-app-tool-catalog.mjs").read_text("utf-8")
    transport = (ROOT / "agent-runtime/src/native-dsh-domain-transport.mjs").read_text("utf-8")
    value = re.search(r"MAX_SEQUENTIAL_APP_CALLS\s*=\s*([\d_]+)", catalog)
    retained = re.search(r"retainedReceipts\s*=\s*([\d_]+)", transport)
    assert value is not None and retained is not None
    assert int(value[1].replace("_", "")) == MAX_APP_CALLS
    assert int(retained[1].replace("_", "")) == MAX_APP_CALLS
