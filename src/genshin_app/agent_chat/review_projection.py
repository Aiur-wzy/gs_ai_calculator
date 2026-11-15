"""Session-owned historical review fields; never a reusable calculation approval."""
from __future__ import annotations

import hashlib
import os
import re
import tempfile
import uuid

from genshin_app.equipment.files import json_bytes, publish_files
from genshin_app.equipment.schema import PlanError
from genshin_app.review_batch_json import (
    BATCH_BYTES,
    PROJECTION_WORK,
    Budget,
    embed_json,
    encode,
    plain_json,
    prepay,
    read_json,
)

from .sessions import MAX_CHECKPOINT, safe_path, write_owned

CONTRACT = "agent-calculation-review-projection-v1"
NAME = re.compile(r"review-[0-9a-f]{32}")
IDENTITY_FIELDS = {"plan_id", "revision", "input_fingerprint", "source_fingerprint", "result_fingerprint"}
META_FIELDS = {"contract", "historical_projection", "session_id", "original_revision", "identity", "content_fingerprint"}
RECORD_FIELDS = META_FIELDS | {"file_name", "byte_length", "file_sha256"}


def _invalid():
    raise PlanError("历史审查投影的归属、合同、长度或内容与检查点不符", "agent_projection_invalid")


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _encoded(value, budget, maximum):
    return encode(value, budget, maximum=maximum)


def _metadata(document):
    return {key: document[key] for key in META_FIELDS}


def _scope(document):
    return {**_metadata(document),
            "pointer_scope": "历史行json_pointer只属于捕获时的原计算文档，不能对当前calculated解引用。",
            "original_document": {"readable": False, "reason": "original_snapshot_not_retained",
                                  "message": "本资源仅保留已选字段，原完整快照未随投影保存；当前候选不能代替原文。"}}


def validate_document(session, name, document, record, *, raw=None):
    """Check memory/disk against the immutable checkpoint manifest, without replay."""
    if (type(name) is not str or NAME.fullmatch(name) is None or type(record) is not dict
            or set(record) != RECORD_FIELDS or type(document) is not dict
            or set(document) != META_FIELDS | {"projection"}):
        _invalid()
    if (document.get("contract") != CONTRACT or document.get("historical_projection") is not True
            or document.get("session_id") != session.session_id
            or type(document.get("original_revision")) is not int
            or not 0 <= document["original_revision"] <= session.revision):
        _invalid()
    identity = document.get("identity")
    if (type(identity) is not dict or set(identity) != IDENTITY_FIELDS
            or type(identity["plan_id"]) is not str or not identity["plan_id"]
            or identity["plan_id"] != session.candidate["plan_id"]
            or type(identity["revision"]) is not int
            or not 0 <= identity["revision"] <= session.candidate["revision"]):
        _invalid()
    for key in ("input_fingerprint", "source_fingerprint", "result_fingerprint"):
        if type(identity[key]) is not str or re.fullmatch(r"[0-9a-f]{64}", identity[key]) is None:
            _invalid()
    projection = document["projection"]
    if (type(projection) is not dict or projection.get("contract") != "calculation-review-batch-v1"
            or projection.get("historical_projection") is not True
            or json_bytes(projection.get("identity")) != json_bytes(identity)
            or type(projection.get("pages")) is not list or not 1 <= len(projection["pages"]) <= 16
            or _sha(json_bytes(projection)) != document["content_fingerprint"]):
        _invalid()
    if json_bytes({key: record[key] for key in META_FIELDS}) != json_bytes(_metadata(document)):
        _invalid()
    if record["file_name"] != name + ".json" or type(record["byte_length"]) is not int:
        _invalid()
    encoded = json_bytes(document)
    if (not 0 < len(encoded) <= BATCH_BYTES or len(encoded) != record["byte_length"]
            or _sha(encoded) != record["file_sha256"]
            or raw is not None and raw != encoded):
        _invalid()
    return document


def load_document(session, name, record):
    if type(record) is not dict or record.get("file_name") != name + ".json":
        _invalid()
    path = safe_path(session.root, record["file_name"])
    with path.open("rb") as stream:
        raw = stream.read(BATCH_BYTES + 1)
    if (len(raw) > BATCH_BYTES or len(raw) != record.get("byte_length")
            or _sha(raw) != record.get("file_sha256")):
        _invalid()
    # The bounded JSON reader rejects duplicate keys and nonfinite values too.
    document = read_json(path, BATCH_BYTES)
    return validate_document(session, name, document, record, raw=raw)


def read_page(session, name, args, page):
    from .tools import MAX_RESPONSE

    records = getattr(session, "_review_projection_records", {})
    if name not in records or name not in session.resources:
        _invalid()
    document = validate_document(session, name, session.resources[name], records[name])
    result = page(document, name, args.get("pointer", ""), args.get("offset", 0), args.get("limit", 50))
    # This is a separate read operation, never an extension of batch/replay
    # budgets. The legacy pager aliases children; only this new endpoint detaches
    # its entire bounded response before enriching references or returning it.
    budget = Budget(PROJECTION_WORK, "historical_read")
    try:
        result, _ = plain_json({**result, **_scope(document)}, budget, maximum=MAX_RESPONSE)
        metadata = {key: result[key] for key in META_FIELDS | {"pointer_scope", "original_document"}}
        for row in result.get("items", []):
            item = row["value"]
            if type(item) is dict and "reference" in item:
                item["reference"] = {**item["reference"], **metadata}
        prepay({"code": 0, "data": result}, budget, maximum=MAX_RESPONSE)
        return result
    finally:
        budget.close()


def _register(session, name, document, record):
    """One injection seam for the in-memory half of publication."""
    session.resources[name] = document
    session._review_projection_records[name] = record


def _prepare_checkpoint(session, name, record, budget):
    path = safe_path(session.root, "checkpoint.json")
    checkpoint = read_json(path, MAX_CHECKPOINT)
    before = _encoded(checkpoint, budget, MAX_CHECKPOINT)
    # This operation only adds a projection to an already-saved calculation.
    # Do not silently update a stale checkpoint or redo full artifact hashes.
    receipt = _encoded(session.calculated["result_receipt"], budget, BATCH_BYTES)
    saved_records = _encoded(checkpoint.get("review_projections", {}), budget, MAX_CHECKPOINT)
    memory_records = _encoded(getattr(session, "_review_projection_records", {}), budget, MAX_CHECKPOINT)
    if (type(checkpoint.get("version")) is not int or checkpoint["version"] != 1
            or checkpoint.get("session_id") != session.session_id
            or type(checkpoint.get("revision")) is not int or checkpoint["revision"] != session.revision
            or checkpoint.get("kind") != session.kind
            or checkpoint.get("document_fingerprint") != session.document_fingerprint
            or checkpoint.get("receipt_fingerprint") != _sha(receipt)
            or record["identity"]["plan_id"] != session.candidate["plan_id"]
            or type(record["original_revision"]) is not int or record["original_revision"] != session.revision
            or type(record["identity"]["revision"]) is not int
            or record["identity"]["revision"] != session.candidate["revision"]
            or checkpoint.get("resources") != list(session.resources)
            or saved_records != memory_records):
        raise PlanError("历史审查不能登记到过期或身份不符的检查点", "agent_checkpoint_invalid")
    checkpoint["resources"] = [*checkpoint["resources"], name]
    checkpoint["review_projections"] = {**checkpoint.get("review_projections", {}), name: record}
    return before, _encoded(checkpoint, budget, MAX_CHECKPOINT)


def _commit_checkpoint(session, before, raw):
    """Atomic terminal commit: no hash, encode, stat or cleanup after replace.

    Legacy save/write_owned hashes published files after replacing them; that
    tail can fail despite a committed checkpoint. The new transaction instead
    finishes every fallible preparation before this one replacement.
    """
    target = safe_path(session.root, "checkpoint.json")
    fd, name = tempfile.mkstemp(prefix=".review-checkpoint-", suffix=".tmp", dir=session.root)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        with target.open("rb") as stream:
            if stream.read(MAX_CHECKPOINT + 1) != before:
                raise PlanError("历史审查发布前检查点已变化", "agent_checkpoint_invalid")
        os.replace(name, target)
    except BaseException:
        try:
            os.unlink(name)
        except OSError:
            pass
        raise


def _orphan(session, name, exc):
    path = safe_path(session.root, name + ".json")
    if not path.exists():
        return
    entry = {"contract": "agent-unpublished-review-v1", "session_id": session.session_id,
             "file_name": path.name, "published": False, "failure": type(exc).__name__}
    # This list is deliberately separate from ready resources and checkpoints.
    session._review_projection_orphans = [*getattr(session, "_review_projection_orphans", []), entry]
    try:
        write_owned(session.root, "unpublished-" + name + ".json", entry, 4096)
    except (OSError, PlanError) as evidence_error:
        entry["evidence_error"] = type(evidence_error).__name__


def finish(session, output, budget, *, maximum_response, maximum_resources, maximum_total):
    """Prebuild the entire final envelope, then publish at most once under RLock."""
    content = _encoded(output, budget, BATCH_BYTES)
    document = {"contract": CONTRACT, "historical_projection": True,
                "session_id": session.session_id, "original_revision": session.revision,
                "identity": output["identity"], "content_fingerprint": _sha(content), "projection": output}
    # An already oversized content body cannot fit inside the final envelope.
    # Skip constructing/scanning a doomed direct response; all resource bytes
    # and its final reference response still use this same remaining budget.
    if len(content) <= maximum_response:
        direct = {"code": 0, "data": {**output, **_scope(document), "contract": output["contract"]}}
        if prepay(direct, budget) <= maximum_response:
            _encoded(direct, budget, maximum_response)
            return direct
    raw = embed_json(_metadata(document), 'projection', content, budget, maximum=BATCH_BYTES)
    if len(session.resources) >= maximum_resources:
        raise PlanError("本会话资料已达128份，请复用资料或开始新会话", "agent_resource_limit")
    total = len(raw)
    if total > maximum_total:
        raise PlanError("本会话资料超过64MiB", "agent_resource_limit")
    for value in session.resources.values():
        total += prepay(value, budget)
        if total > maximum_total:
            raise PlanError("本会话资料超过64MiB", "agent_resource_limit")
    name = "review-" + uuid.uuid4().hex
    record = {**_metadata(document), "file_name": name + ".json", "byte_length": len(raw),
              "file_sha256": _sha(raw)}
    reference = {"resource": name, "pointer": "", **_scope(document)}
    response = {"code": 0, "data": {**_scope(document), "storage": "session_resource",
                                     "reference": reference, "bytes": len(raw), "page_count": len(output["pages"])}}
    # The initial reference is public too: do not expose the registered identity
    # dictionary through it, even before any later historical page is read.
    response, _ = plain_json(response, budget, maximum=maximum_response)
    _encoded(response, budget, maximum_response)
    # Account for new registration/checkpoint metadata before touching the disk.
    prepay(record, budget, maximum=BATCH_BYTES)
    checkpoint_before, checkpoint_raw = _prepare_checkpoint(session, name, record, budget)
    target = safe_path(session.root, record["file_name"])
    if target.exists() or name in session.resources:
        raise PlanError("历史投影UUID目标已存在；未覆盖或重试", "agent_projection_unpublished")
    safe_path(session.root, "." + target.name + ".plan.lock")
    previous_resources = session.resources.copy()
    previous_records = getattr(session, "_review_projection_records", {}).copy()
    session._review_projection_records = previous_records.copy()
    try:
        publish_files([(target, raw)], overwrite=False)
        _register(session, name, document, record)
        _commit_checkpoint(session, checkpoint_before, checkpoint_raw)
    except Exception as exc:
        session.resources = previous_resources
        session._review_projection_records = previous_records
        _orphan(session, name, exc)
        raise PlanError("历史审查投影未发布；未重试计算或发布", "agent_projection_unpublished") from exc
    # Nothing which can encode or fail packaging remains after checkpoint commit.
    return response
