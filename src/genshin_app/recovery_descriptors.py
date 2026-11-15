"""Render shared recovery intents for the actual entrypoint."""
from __future__ import annotations

_CLI_ROUTES = {
    "inspect_selected_character_identity": ("db", "context"),
    "inspect_effect_binding_requirements": ("equipment", "compose"),
    "preview_attribute_recovery": ("equipment", "attribute-draft"),
}
_NATIVE_ROUTES = {
    "inspect_selected_character_identity": {"tool": "gs_query_context",
                                            "arguments": {"request": {"characters": [{"id": "实际角色ID"}]}}},
    "inspect_effect_binding_requirements": {"tool": "gs_compose", "arguments": {},
                                           "follow_up": {"tool": "gs_read_context",
                                                         "arguments": {"resource": "report", "pointer": "/effect_requirements"}}},
    "preview_attribute_recovery": {"tool": "gs_preview_attribute_recovery"},
}


def project_recovery(recovery, operation):
    from copy import deepcopy
    value = deepcopy(dict(recovery))
    action = value.get("action")
    if not operation:
        return value
    if operation.startswith("cli."):
        value.pop("tool", None)
        value.pop("resource", None)
        value.pop("pointer", None)
        route = _CLI_ROUTES.get(action)
        if route:
            command = " ".join(route)
            value["cli"] = command
            value["contract_argv"] = ["capabilities", "--command", command]
            if action == "inspect_effect_binding_requirements":
                value["report_pointer"] = "/effect_requirements"
    elif operation.startswith("gs_"):
        value.pop("cli", None)
        value.update(deepcopy(_NATIVE_ROUTES.get(action, {})))
    return value
