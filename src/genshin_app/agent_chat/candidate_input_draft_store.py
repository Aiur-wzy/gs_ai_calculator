"""App-owned candidate snapshot publication; paths never enter model tools."""
from __future__ import annotations

import copy
import hashlib
from pathlib import Path

from genshin_app.equipment.files import fingerprint, json_bytes
from genshin_app.equipment.schema import PlanError
from genshin_app.query_cache import protect_inputs
from genshin_app.version import APP_RELEASE_ID, APP_VERSION

from .candidate_input_draft import (
    MAX_BYTES,
    SUFFIX,
    build_draft,
    decode_draft,
    decode_draft_for_preview,
    preview_draft,
    validate_draft,
)
from .native_compatibility_identity import active_database_release_id
from .sessions import read_owned, safe_path, write_owned

_PROVENANCE = "candidate-input-provenance.json"


def historical_provenance(session):
    """Read-only provenance, deliberately outside trusted query descriptors."""
    if not safe_path(session.root, _PROVENANCE).exists():
        return {"descriptors": {}, "unresolved_references": []}
    saved = read_owned(session.root, _PROVENANCE, MAX_BYTES)
    if (type(saved) is not dict or set(saved) != {"contract", "descriptors", "unresolved_references"}
            or saved["contract"] != "candidate-input-historical-provenance-v1"
            or type(saved["descriptors"]) is not dict or type(saved["unresolved_references"]) is not list):
        raise PlanError("导入草稿的历史资料索引不合法", "candidate_input_draft_provenance_invalid")
    return saved


def historical_resource_descriptors(session):
    return historical_provenance(session)["descriptors"]


class CandidateInputDraftStore:
    def __init__(self, session, identity_reader):
        self.session = session
        self.identity_reader = identity_reader

    def _build(self):
        return build_draft(self.session, workspace_identity=self.identity_reader(),
                           database_release_id=active_database_release_id(Path(self.session.db_path).parent),
                           producer={"app_version": APP_VERSION, "app_release_id": APP_RELEASE_ID})

    def capture(self, expected_revision=None):
        with self.session._lock, self.session._read_db():
            if expected_revision is not None:
                self.session._expect(expected_revision)
            draft = self._build()
            name = "candidate-input-draft-" + draft["artifact_id"] + ".json"
            write_owned(self.session.root, name, draft, MAX_BYTES)
            return self.target(draft)

    def target(self, draft):
        return {"kind": "candidate_input_draft", "workspace_id": self.session.session_id,
                "artifact_id": draft["artifact_id"],
                "candidate_revision": draft["source"]["candidate_revision"],
                "stage_revision": draft["source"]["stage_revision"],
                "candidate_fingerprint": draft["source"]["candidate_fingerprint"],
                "input_snapshot_fingerprint": draft["fingerprints"]["input_snapshot"],
                "compatibility_fingerprint": fingerprint(draft["compatibility"]["workspace_identity"]),
                "database_fingerprint": draft["compatibility"]["database_fingerprint"]}

    def read_target(self, target, *, check_current=True):
        import uuid

        try:
            identity = str(uuid.UUID(target["artifact_id"]))
        except (KeyError, ValueError, TypeError) as exc:
            raise PlanError("候选草稿快照身份不合法", "candidate_input_draft_target_invalid") from exc
        draft = read_owned(self.session.root, "candidate-input-draft-" + identity + ".json", MAX_BYTES)
        validate_draft(draft)
        if self.target(draft) != target:
            raise PlanError("候选草稿保存目标不一致", "candidate_input_draft_target_invalid")
        if check_current:
            with self.session._lock, self.session._read_db():
                current = self._build()
                current_target = self.target(current)
                current_target["artifact_id"] = target["artifact_id"]
                if current_target != target:
                    raise PlanError("候选已更新，请保存当前版本", "candidate_input_draft_stale")
        return draft

    def publish(self, target, output_path, *, expected_file_sha=None, overwrite=False):
        return self._publish(target, output_path, expected_file_sha=expected_file_sha, overwrite=overwrite, check_current=True)

    def publish_snapshot(self, target, output_path, *, expected_file_sha=None, overwrite=False):
        """Publish an exact captured target after its user claim was verified.

        It remains a draft of that revision; no approval or calculation authority
        is created. The controller, not this byte publisher, owns user claims.
        """
        return self._publish(target, output_path, expected_file_sha=expected_file_sha, overwrite=overwrite, check_current=False)

    def _publish(self, target, output_path, *, expected_file_sha, overwrite, check_current):
        # Called only after the trusted user has claimed the exact file card.
        with self.session._lock, self.session._read_db():
            draft = self.read_target(target, check_current=check_current)
            path = Path(output_path).absolute()
            if not path.name.lower().endswith(SUFFIX):
                raise PlanError("请选择候选输入草稿文件名（.gencdraft.json）", "candidate_input_draft_filename")
            try:
                protect_inputs([path], [self.session.db_path])
                if path.exists():
                    # A known SHA never grants permission to replace another file type.
                    load_candidate_input_draft(path)
            except (OSError, PlanError) as exc:
                raise PlanError("所选位置或已有文件不能用于保存这份草稿，请另选文件名。",
                                "candidate_input_draft_destination_invalid", {"publication_state": "not_written"}) from exc
            raw = json_bytes(draft)
            # overwrite is a user decision, never a waiver of file CAS.
            # None means the user selected a new, still-nonexistent path.
            expected = {path: expected_file_sha}
            from genshin_app.output_policy import publish_ordinary_files
            report = publish_ordinary_files([(path, raw)], self.session.db_path, overwrite=overwrite, expected_shas=expected,
                                   input_paths=[self.session.db_path])
            try:
                loaded = load_candidate_input_draft(path)
                actual_sha = report["file_shas"][str(path.resolve())]
                if loaded["file_sha"] != actual_sha or loaded["draft"] != draft:
                    raise ValueError("草稿内容在写入后发生变化")
            except (OSError, ValueError, KeyError) as exc:
                raise PlanError("文件已写入，但重新读取时未能确认内容，请核对所选文件。",
                                "candidate_input_draft_write_unconfirmed",
                                {"publication_state": "written_unverified", "path": str(path)}) from exc
            return {"kind": draft["kind"], "artifact_id": draft["artifact_id"],
                    "input_snapshot_fingerprint": draft["fingerprints"]["input_snapshot"],
                    "file_sha": actual_sha, "bytes": len(raw), "saved": True, "path": str(path)}


def read_input_file_bytes(path):
    """Use the largest existing input format budget before identifying kind."""
    path = Path(path)
    from genshin_app.equipment.files import input_file_errors, read_input_bytes
    with input_file_errors():
        if path.stat().st_size > MAX_BYTES:
            raise PlanError("输入文件超过 128 MiB 容量限制", "candidate_input_draft_too_large")
        raw = read_input_bytes(path, maximum_bytes=MAX_BYTES)
    if len(raw) > MAX_BYTES:  # A file can grow after stat; never read it unbounded.
        raise PlanError("输入文件超过 128 MiB 容量限制", "candidate_input_draft_too_large")
    return raw


def load_candidate_input_draft(path):
    path = Path(path).resolve()
    raw = read_input_file_bytes(path)
    return {"draft": decode_draft(raw), "file_sha": hashlib.sha256(raw).hexdigest(), "path": str(path)}


def load_candidate_input_draft_preview(path):
    """Read-only loader; unknown metadata is never accepted by the strict store."""
    path = Path(path).resolve()
    raw = read_input_file_bytes(path)
    return {**decode_draft_for_preview(raw), "file_sha": hashlib.sha256(raw).hexdigest(), "path": str(path)}


def draft_preview(path, *, workspace_identity, database_fingerprint):
    loaded = load_candidate_input_draft(path)
    return {**preview_draft(loaded["draft"], workspace_identity=workspace_identity,
                           database_fingerprint=database_fingerprint), "file_sha": loaded["file_sha"]}


def restore_candidate_input(session, draft):
    """Populate a *new* isolated workspace. Never restore a live Session's authority."""
    validate_draft(draft)
    marker = "draft_import:" + draft["artifact_id"] + ":" + draft["fingerprints"]["input_snapshot"]
    candidate = copy.deepcopy(draft["input"]["candidate"])
    if candidate["representation"] == "native":
        candidate["payload"].update(resolution={"state": "pending"}, resolved_template=None)
        from genshin_app.native_input_projection import synchronize_native_projection
        synchronize_native_projection(candidate["payload"])
    if (session.revision == 1 and any(marker in row.get("fields", []) for row in session.changes)
            and session.calculated is None and
                session.candidate == candidate["payload"]):
        return session.summary()  # crash after checkpoint commit, before import marker
    if session.revision != 0 or session.calculated is not None or session.findings or session.resources:
        raise PlanError("只能在新的候选工作副本中打开输入草稿", "candidate_input_draft_workspace_not_empty")
    from .native_dsh_workspace_compatibility import assert_workspace_compatible

    assert_workspace_compatible(draft["compatibility"]["workspace_identity"], Path(session.db_path).parent)
    if draft["compatibility"]["database_fingerprint"] != fingerprint(session.database_snapshot):
        raise PlanError("候选输入草稿来自其他资料库版本，只能查看", "candidate_input_draft_incompatible")
    with session._lock:
        session.candidate = candidate["payload"]
        session.baseline = copy.deepcopy(session.candidate)
        session.revision = 1
        session.calculated, session.report = None, None
        session.changes = [{"revision": 1, "message": "打开候选输入草稿；旧证明和许可未恢复", "fields": ["draft_import", marker]}]
        state = session.stage_state
        state.update(phase=draft["input"]["work_focus"], representation=candidate["representation"],
                     energy_intent=draft["input"]["energy_intent"], calculation_basis=None)
        for row in draft["evidence"]["resources"]:
            name = row["resource_id"]
            write_owned(session.root, name + ".json", row["data"], 4*1024*1024)
            session.resources[name] = copy.deepcopy(row["data"])
            # Imported provenance is historical; it is not a new database proof.
            state["resource_stages"][name] = state["phase"]
        write_owned(session.root, _PROVENANCE, {"contract": "candidate-input-historical-provenance-v1",
            "descriptors": {row["resource_id"]: row["descriptor"] for row in draft["evidence"]["resources"]},
            "unresolved_references": draft["evidence"]["unresolved_references"]}, MAX_BYTES)
        session.findings = [copy.deepcopy(row["record"]) for row in draft["evidence"]["findings"]]
        state["finding_stages"] = [row["work_focus"] for row in draft["evidence"]["findings"]]
        from .provisional_authority import new_state

        session.provisional_authority = new_state(session.session_id, fingerprint(session.database_snapshot), session.candidate)
        session.save()
    return session.summary()
