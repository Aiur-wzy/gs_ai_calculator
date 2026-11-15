"""App-only candidate projection; DSH remains the generic UI authority."""
from __future__ import annotations

import copy

from genshin_app.equipment.files import fingerprint

from .harness_v3.candidate import derive_candidate_state


def project_workspace_candidate(checkpoint, summary, *, commands=(), review_verified=False,
                                application_state="none"):
    candidate = derive_candidate_state(checkpoint=checkpoint, summary=summary,
        database_current=summary.get("database_current"),
        review_authority_verified=review_verified, application_state=application_state)
    available = [{"command_id": "draft.inspect", "enabled": True,
                  "scope": {"candidate_revision": checkpoint["candidate_revision"]},
                  "risk": "read_only", "invocation_kind": "host_command", "reason_code": None}]
    available.extend(copy.deepcopy(list(commands)))
    source = {"candidate": candidate, "document_id": checkpoint["document_id"],
              "database_fingerprint": checkpoint["database_fingerprint"],
              "focus": {"recommended_stage": summary.get("focus_stage", summary.get("workflow_stage")),
                        "reason": "host_recommendation"},
              "authority_mode": "app-domain", "generic_owner": "dsh",
              "artifacts": [], "interruptions": [], "issues": candidate["issues"]}
    revision = int(fingerprint({**source, "commands": available})[:13], 16)
    for command in available:
        command["expected_projection_revision"] = revision
    return {"contract": "native-harness-projection-v3", "projection_revision": revision,
            **source, "available_commands": available}
