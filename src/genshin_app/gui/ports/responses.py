"""Pure bridge envelopes shared by domain ports; no transaction state."""
from __future__ import annotations


class GuiResponses:
    @staticmethod
    def _ok(data):
        return {"code": 0, "data": data}

    @staticmethod
    def _err(message):
        return {"code": 1, "message": message}
