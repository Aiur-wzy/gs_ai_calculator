"""App-issued read-only projection of unchanged plan-local public facts.

This is not a Session and cannot publish an approval or usage receipt. The
original authority service resolves every real reference, while only explicit
panel-stat variations may be compared under the same plan identity.
"""
from __future__ import annotations

import copy
import math
from pathlib import Path

from genshin_app.equipment.files import fingerprint, input_view
from genshin_app.equipment.provisional_entities import (
    FIELD,
    authorization_basis_fingerprint,
    build_usage_receipt,
    declared_provisional_usages,
)
from genshin_app.equipment.schema import STATS, PlanError

_SEAL = object()
CONTRACT = "provisional-public-fact-readonly-projection-v1"
FIELD_NAME = "advisory_public_fact_projection"


def _error(message, reason="energy_trial_authorization_scope_changed"):
    raise PlanError(message, reason, {"category": "request_error", "authority": "none",
        "recovery": {"action": "correct_trial_scope", "retry_same_request": False}})


class ReadOnlyProvisionalProjection:
    """Opaque in-process access to a fixed original authority, never a writer."""

    __slots__ = ("_authority", "_calculated_fingerprint", "_database", "_db_path", "_evidence", "_revision", "_seal", "_session", "_source", "_stage_revision", "_trial")

    def __init__(self, session, source, authority, trial, *, _seal=None):
        if _seal is not _SEAL:
            _error("只读临时事实上下文只能由原App权限服务创建")
        self._session, self._source, self._authority = session, copy.deepcopy(source), copy.deepcopy(authority)
        self._revision = session.revision
        self._stage_revision = session.stage_state.get("stage_revision", 0)
        self._database, self._db_path = fingerprint(session.database_snapshot), Path(session.db_path).resolve()
        self._trial, self._seal = fingerprint(input_view(trial)), _SEAL
        self._evidence, self._calculated_fingerprint = None, None

    def _assert_current(self):
        current = self._session
        with current._lock, current._read_db():
            if (getattr(current, "closed", False) or self._seal is not _SEAL or current.revision != self._revision
                    or current.stage_state.get("stage_revision", 0) != self._stage_revision
                    or fingerprint(current.database_snapshot) != self._database
                    or input_view(current.candidate) != input_view(self._source)
                    or current.provisional_authority != self._authority):
                _error("原候选、数据库或临时资料授权已变化，不能继续沿用只读试算上下文",
                       "energy_trial_authorization_stale")

    def validate_input(self, plan, db_path):
        self._assert_current()
        if Path(db_path).resolve() != self._db_path or fingerprint(input_view(plan)) != self._trial:
            _error("只读试算上下文不属于此输入或数据库")

    def resolve_provisional_entity_ref(self, *, ref, consumer):
        self._assert_current()
        # Reuse the original resolver and exact live ledgers. This neither
        # copies an old approval onto the trial nor mints a new one.
        with self._session._lock:
            return self._session.resolve_provisional_entity_ref(ref=ref, consumer=consumer)

    def execution_evidence(self, plan, resolved_uses):
        self._assert_current()
        if (plan.get("plan_id") != self._source.get("plan_id")
                or plan.get(FIELD) != self._source.get(FIELD)
                or declared_provisional_usages(plan) != declared_provisional_usages(self._source)):
            _error("组合后的临时事实或实际引用范围与原授权不一致")
        # The pure validator checks sealed resolver outputs, exact multiplicity
        # and original authorization. Its formal-shaped intermediate never
        # escapes, is never published, and grants no trial/export authority.
        checked = build_usage_receipt(self._source, resolved_uses,
            candidate_revision=self._revision, stage_revision=self._stage_revision,
            database_snapshot_fingerprint=self._database)
        evidence = {"contract": CONTRACT, "advisory_only": True, "formal_result_authority": False,
            "source_plan_id": self._source["plan_id"], "source_revision": self._revision,
            "source_stage_revision": self._stage_revision, "database_snapshot_fingerprint": self._database,
            "source_input_fingerprint": fingerprint(input_view(self._source)),
            "source_authorization_basis_fingerprint": authorization_basis_fingerprint(self._source),
            "trial_input_fingerprint": self._trial,
            "uses": [{key: row[key] for key in ("entity_type", "provisional_id", "consumer", "authority",
                "payload_fingerprint", "lookup_receipt_id", "attestation_id", "approval_id")} for row in checked["entities"]]}
        self._evidence = copy.deepcopy(evidence)
        return evidence


    def bind_fresh_result(self, calculated):
        """Bind the actual fresh producer output in memory, never in an authority ledger."""
        self._assert_current()
        if self._evidence is None or any(
                not isinstance(calculated.get(owner), dict)
                or calculated[owner].get(FIELD_NAME) != self._evidence
                for owner in ("result", "result_receipt", "report")):
            _error("本次只读计算缺少真实采用证据", "energy_trial_result_untrusted")
        self._calculated_fingerprint = fingerprint(calculated)

    def validate_fresh_result(self, calculated):
        self._assert_current()
        if (self._calculated_fingerprint is None or fingerprint(calculated) != self._calculated_fingerprint
                or calculated.get("result", {}).get(FIELD_NAME) != self._evidence):
            _error("只读核验必须使用本次真实且未改变的计算结果", "energy_trial_result_untrusted")
        return copy.deepcopy(self._evidence)


def _prepare_readonly_projection(session, trial, changes):
    """Called by the original authority owner; only explicit panel stats differ."""
    from genshin_app.agent_chat.provisional_authority import validate_state

    with session._lock, session._read_db():
        source = copy.deepcopy(session.candidate)
        authority = validate_state(session.provisional_authority, session_id=session.session_id,
            database_snapshot_fingerprint=fingerprint(session.database_snapshot), candidate=source)
        expected = copy.deepcopy(source)
        actors = {row["id"]: row for row in expected.get("manual_template", {}).get("characters", [])}
        if not isinstance(changes, dict) or any(not isinstance(stats, dict) for stats in changes.values()):
            _error("只读试算必须列出明确角色面板变化")
        for actor, stats in changes.items():
            if (actor not in actors or set(stats) - set(STATS)
                    or any(type(value) not in (int, float) or not math.isfinite(value) for value in stats.values())):
                _error("只读试算只接受已存在角色的有限面板数值")
            gear = expected.get("equipment_inputs", {}).get(actor)
            if gear is not None and gear.get("mode") != "panel":
                _error("此试算角色并非明确面板输入，不能仅修改显示面板")
            actors[actor]["stats"].update(stats)
        if input_view(expected) != input_view(trial):
            _error("只读试算除明确面板变化外不能改变实体、来源、动作、绑定、版本或批准范围")
        return ReadOnlyProvisionalProjection(session, source, authority, trial, _seal=_SEAL)
