"""Shared byte ceilings for one complete calculated artifact and its envelopes."""
from __future__ import annotations

MIB = 1024 * 1024

# Frozen gen17 measured 69,969,898 bytes with all 505 explanations retained.
# Ninety-six MiB leaves 30,693,398 bytes of headroom without widening the
# unrelated Agent/DSH packet, token, engine-work, or source-approval budgets.
CALCULATED_ARTIFACT_BYTES = 96 * MIB
CALCULATION_DELIVERY_TOTAL_BYTES = 192 * MIB

# GUI/RPC documents may wrap one calculated artifact with board and recovery
# metadata.  This remains an envelope ceiling, not permission to place an
# artifact in one mux frame or one model result.
GUI_DOCUMENT_TRANSPORT_BYTES = 2 * CALCULATED_ARTIFACT_BYTES
