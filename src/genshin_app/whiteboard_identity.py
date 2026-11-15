"""Stable whiteboard business identity and compatibility projections."""
from __future__ import annotations

import copy

from genshin_app.equipment.files import fingerprint
from genshin_app.whiteboard_schema import SCHEMA_VERSION, _fail


def intent_view(board):
    intent = {key: value for key, value in board.items() if key not in ("revision", "title")}
    # Editor sourcing preferences do not change the saved numerical game inputs.
    # The actual preference is still persisted and checked on Agent return.
    intent["team"] = [{key: value for key, value in actor.items() if key != "panel_basis_mode"}
                      for actor in board["team"]]
    return intent


def board_fingerprint(board):
    return fingerprint(intent_view(board))


def legacy_intent_view(board):
    """Project an unchanged v3 board onto the exact v1 business view."""
    if (board.get("schema_version") != SCHEMA_VERSION or board.get("capacity_intents") != []
            or board.get("action_admission_intents") != []):
        _fail("含容量意图的白板不能映射为旧版意图", "capacity_intents", "whiteboard_intent_mismatch")
    value = copy.deepcopy(board)
    value["schema_version"] = 1
    value.pop("capacity_intents")
    value.pop("action_admission_intents")
    return intent_view(value)


def legacy_board_fingerprint(board):
    return fingerprint(legacy_intent_view(board))


def v2_intent_view(board):
    """Project a v3 board without admission intents onto its exact v2 view."""
    if board.get("schema_version") != SCHEMA_VERSION or board.get("action_admission_intents") != []:
        _fail("含动作接纳意图的白板不能映射为v2意图", "action_admission_intents",
              "whiteboard_intent_mismatch")
    value = copy.deepcopy(board)
    value["schema_version"] = 2
    value.pop("action_admission_intents")
    return intent_view(value)


def v2_board_fingerprint(board):
    return fingerprint(v2_intent_view(board))
