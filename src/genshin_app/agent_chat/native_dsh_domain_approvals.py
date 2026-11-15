"""App-only public-data correction decisions, not generic DSH questions.

The model proposes the exact correction via the existing authority ledger.
Only the trusted GUI command records a user decision. Its immutable receipt is
written before changing candidate authority, so a lost response can be read or
settled again without guessing a new answer or replaying a model request.
"""
from __future__ import annotations

import copy

from genshin_app.equipment.files import fingerprint

from . import provisional_authority
from .native_dsh_mux_client import NativeDshMuxError
from .sessions import read_owned, safe_path, write_owned

CONTRACT = "genshin-native-dsh-domain-approval-decision-v1"


def approval_cards(session):
    state = getattr(session, "provisional_authority", None) or {}
    return [{"question_id": row["question_id"], "state": row["state"],
        "provisional_id": row["provisional_id"], "entity_type": row["entity_type"],
        "expected_candidate_revision": session.revision,
        "expected_stage_revision": session.stage_state["stage_revision"],
        "payload_fingerprint": row["payload_fingerprint"],
        "question": copy.deepcopy(row["question"])}
        for row in state.get("pending_approvals", {}).values()
        if row["state"] in {"pending", "bound"}]


def decide_approval(session, request):
    fields = {"question_id", "expected_candidate_revision", "expected_stage_revision", "decision"}
    if (type(request) is not dict or set(request) != fields
            or request["decision"] not in {"approve", "reject"}
            or type(request["question_id"]) is not str
            or any(type(request[key]) is not int or request[key] < 0
                   for key in ("expected_candidate_revision", "expected_stage_revision"))):
        raise NativeDshMuxError("dsh_domain_approval_request_invalid")
    pending = session.provisional_authority["pending_approvals"].get(request["question_id"])
    if pending is None:
        raise NativeDshMuxError("dsh_domain_approval_not_found")
    # A hash, never a model/user-selected filename, names the durable decision.
    name = "dsh-domain-decision-" + fingerprint(request["question_id"]) + ".json"
    if safe_path(session.root, name).is_file():
        row = read_owned(session.root, name, 64 * 1024)
        if row != {"contract": CONTRACT, "session_id": session.session_id,
                   "request": request, "question_fingerprint": fingerprint(pending["question"])}:
            raise NativeDshMuxError("dsh_domain_approval_decision_conflict")
    else:
        if (request["expected_candidate_revision"] != session.revision
                or request["expected_stage_revision"] != session.stage_state["stage_revision"]
                or pending["state"] not in {"pending", "bound"}):
            raise NativeDshMuxError("dsh_domain_approval_stale")
        row = {"contract": CONTRACT, "session_id": session.session_id,
               "request": copy.deepcopy(request), "question_fingerprint": fingerprint(pending["question"])}
        write_owned(session.root, name, row, 64 * 1024)
    question = pending["question"]
    item = {"question_id": question["id"], "prompt": question["question"],
        "detail": question.get("detail"), "header": question.get("header"),
        "kind": "single", "allow_skip": question["allow_skip"],
        "options": [{**option, "value_id": "app-domain-option-" + str(index)}
                    for index, option in enumerate(question["options"])]}
    receipt = "app-domain-decision-" + fingerprint(row)
    batch = "app-domain-batch-" + fingerprint(question["id"])
    if pending["state"] in {"pending", "bound"}:
        provisional_authority.bind_question_batch(session, batch_id=batch,
            call_id=batch, items=[item])
    result = provisional_authority.settle_question_answer(session, question={
        "question_id": batch, "answer_receipt": receipt, "items": [item],
        "answer": {"items": [{"question_id": question["id"], "skipped": False,
            "value_ids": [item["options"][0 if request["decision"] == "approve" else 1]["value_id"]]}]},
    }, reason="用户在App中确认公共资料差异")
    return {**result, "decision_receipt": receipt}
