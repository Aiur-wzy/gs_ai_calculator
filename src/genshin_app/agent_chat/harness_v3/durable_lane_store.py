"""Durable, atomic App admission storage for DSH runtime lanes.

DSH remains the sole owner of Agent, Session, Turn and activity lifecycle.
This store persists only the App facts needed to prevent two provider starts or
conflicting App resource holdings: an admitted lane identity, its current
resource leases, a per-lane CAS revision and an idempotent reservation id.

Every mutation is serialized by one cross-process lock.  Session binding is
re-read under its own binding lock while the admission lock is held, so a new
reservation can never be published from an unprotected binding snapshot.

The mandatory lock order is global admission lock, then per-conversation
binding lock.  Callers and future adapters must never acquire them in reverse
order.  The admission ledger does not participate in DSH Session/Turn state.
"""

from __future__ import annotations

import copy
import json
import re
import stat
import uuid
from collections.abc import Iterator, Mapping
from contextlib import AbstractContextManager, contextmanager
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from genshin_app.equipment.files import fingerprint, json_bytes
from genshin_app.equipment.schema import PlanError
from genshin_db.locking import file_lock

from ..native_dsh_session_binding import (
    ACTIVE,
    AUTHORITATIVE_DURABLE,
    DSH_SESSION_RESERVED,
    NativeDshSessionBindingStore,
)
from ..sessions import safe_path, write_owned
from .runtime_lanes import (
    PROVIDER_RESOURCE,
    DurableLaneAdmissionPort,
    LaneAdmissionOutcome,
    LaneAdmissionPolicy,
    LaneIdentity,
    LaneKey,
    LaneReservationReceipt,
    LeaseHolding,
    ResolvedLaneRequest,
    ResourceLease,
    RuntimeLane,
    SessionBindingSnapshot,
    evaluate_lane_admission,
    evaluate_lease_replacement,
    project_runtime_lanes,
)

CONTRACT = "native-harness-durable-lane-store-v2"
REGISTRY_DIRECTORY = ".native-harness-lane-admission-v2"
LEDGER_FILENAME = "admissions.json"
INITIALIZATION_FILENAME = "initialization.json"
LOCK_FILENAME = ".admissions.lock"
MAX_LEDGER_BYTES = 64 * 1024 * 1024
MAX_INITIALIZATION_BYTES = 4096
MAX_RESERVATIONS = 4096
MAX_SAFE_INTEGER = 2**53 - 1
_IDENTITY_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")

INITIALIZATION_CONTRACT = "native-harness-durable-lane-initialization-v1"
_ROOT_KEYS = {"contract", "epoch_id", "revision", "reservations"}
_INITIALIZATION_KEYS = {"contract", "store_contract", "epoch_id"}
_RESERVATION_KEYS = {
    "reservation_id",
    "admission_fingerprint",
    "lane",
    "held_leases",
}
_LANE_KEYS = {
    "conversation_id",
    "dsh_session_id",
    "generation",
    "turn_id",
    "operation",
    "binding_revision",
    "admission_revision",
}
_LEASE_KEYS = {"resource_kind", "resource_id", "units"}


def _fail(
    message: str,
    reason: str = "native_lane_store_invalid",
    details: Mapping[str, Any] | None = None,
) -> None:
    raise PlanError(message, reason, None if details is None else dict(details))


def _counter(value: object, field: str) -> int:
    if type(value) is not int or not 0 <= value <= MAX_SAFE_INTEGER:
        _fail(f"{field}必须是非负安全整数")
    return value


def _next_revision(value: object) -> int:
    revision = _counter(value, "revision")
    if revision == MAX_SAFE_INTEGER:
        _fail("准入账修订已耗尽", "native_lane_store_revision_exhausted")
    return revision + 1


def _identity(value: object, field: str) -> str:
    if not isinstance(value, str) or _IDENTITY_RE.fullmatch(value) is None:
        _fail(f"{field}不合法")
    return value


def _reservation_id(value: object) -> str:
    return _identity(value, "expected_reservation_id")


def _admission_fingerprint(request: ResolvedLaneRequest) -> str:
    """Hash immutable Host admission intent, excluding domain revision."""

    return fingerprint({
        "lane_key": request.key.public(),
        "initial_required_leases": [
            lease.public() for lease in request.required_leases
        ],
        "host_resolution": {
            "resolver_id": request.resolution.resolver_id,
            "policy_revision": request.resolution.policy_revision,
        },
    })


def _strict_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            _fail("准入账JSON包含重复字段")
        result[key] = value
    return result


def _lease_from_public(value: object) -> ResourceLease:
    if not isinstance(value, Mapping) or set(value) != _LEASE_KEYS:
        _fail("准入账资源租约字段不合法")
    try:
        return ResourceLease(
            resource_kind=value["resource_kind"],
            resource_id=value["resource_id"],
            units=value["units"],
        )
    except (TypeError, ValueError) as exc:
        raise PlanError("准入账资源租约不合法", "native_lane_store_invalid") from exc


def _lane_from_public(value: object) -> RuntimeLane:
    if not isinstance(value, Mapping) or set(value) != _LANE_KEYS:
        _fail("准入账lane字段不合法")
    try:
        generation = _counter(value["generation"], "generation")
        binding_revision = _counter(
            value["binding_revision"], "binding_revision"
        )
        admission_revision = _counter(
            value["admission_revision"], "admission_revision"
        )
        if generation == 0 or admission_revision == 0:
            _fail("准入账lane代数与修订必须为正整数")
        identity = LaneIdentity(
            conversation_id=value["conversation_id"],
            dsh_session_id=value["dsh_session_id"],
            generation=generation,
        )
        key = LaneKey(
            identity=identity,
            turn_id=value["turn_id"],
            operation=value["operation"],
        )
        return RuntimeLane(
            key=key,
            binding_revision=binding_revision,
            admission_revision=admission_revision,
        )
    except (TypeError, ValueError) as exc:
        raise PlanError("准入账lane不合法", "native_lane_store_invalid") from exc


def _record_from_public(value: object) -> LaneReservationReceipt:
    if not isinstance(value, Mapping) or set(value) != _RESERVATION_KEYS:
        _fail("准入账reservation字段不合法")
    lane = _lane_from_public(value["lane"])
    raw_leases = value["held_leases"]
    if not isinstance(raw_leases, list):
        _fail("准入账held_leases必须是数组")
    leases = tuple(_lease_from_public(row) for row in raw_leases)
    try:
        return LaneReservationReceipt(
            reservation_id=value["reservation_id"],
            admission_fingerprint=value["admission_fingerprint"],
            lane=lane,
            held_leases=leases,
        )
    except (TypeError, ValueError) as exc:
        raise PlanError(
            "准入账reservation不合法", "native_lane_store_invalid"
        ) from exc


def _record_public(receipt: LaneReservationReceipt) -> dict[str, Any]:
    public = receipt.public()
    return {
        "reservation_id": public["reservation_id"],
        "admission_fingerprint": public["admission_fingerprint"],
        "lane": public["lane"],
        "held_leases": public["held_leases"],
    }


def _facts(
    reservations: tuple[LaneReservationReceipt, ...],
) -> tuple[tuple[RuntimeLane, ...], tuple[LeaseHolding, ...]]:
    lanes = tuple(receipt.lane for receipt in reservations)
    holdings = tuple(
        LeaseHolding(key=receipt.lane.key, lease=lease)
        for receipt in reservations
        for lease in receipt.held_leases
    )
    return lanes, holdings


def _normalize_ledger(
    value: object,
    *,
    policy: LaneAdmissionPolicy,
    expected_epoch_id: str,
) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != _ROOT_KEYS:
        _fail("准入账根字段不合法")
    if value.get("contract") != CONTRACT:
        _fail("准入账版本不受支持", "native_lane_store_version_incompatible")
    epoch_id = _identity(value.get("epoch_id"), "epoch_id")
    if epoch_id != expected_epoch_id:
        _fail("准入账初始化代数不匹配", "native_lane_store_epoch_mismatch")
    revision = _counter(value.get("revision"), "revision")
    raw_records = value.get("reservations")
    if not isinstance(raw_records, list):
        _fail("准入账reservations必须是数组")
    if len(raw_records) > MAX_RESERVATIONS:
        _fail(
            "准入账reservation数量超过上限",
            "native_lane_store_capacity_exhausted",
        )
    records = tuple(_record_from_public(row) for row in raw_records)
    reservation_ids = [row.reservation_id for row in records]
    if len(reservation_ids) != len(set(reservation_ids)):
        _fail("准入账包含重复reservation_id")
    if any(row.lane.admission_revision > revision for row in records):
        _fail("准入账lane修订超过全局修订")
    lanes, holdings = _facts(records)
    try:
        project_runtime_lanes(lanes, holdings, policy)
    except (TypeError, ValueError) as exc:
        raise PlanError("准入账事实相互冲突", "native_lane_store_invalid") from exc
    normalized = {
        "contract": CONTRACT,
        "epoch_id": epoch_id,
        "revision": revision,
        "reservations": [
            _record_public(row)
            for row in sorted(
                records,
                key=lambda item: (
                    item.lane.key.identity.dsh_session_id,
                    item.lane.key.turn_id,
                    item.lane.key.operation,
                ),
            )
        ],
    }
    try:
        if len(json_bytes(normalized)) > MAX_LEDGER_BYTES:
            _fail("准入账超过保存上限", "agent_size_limit")
    except (TypeError, ValueError, OverflowError, RecursionError) as exc:
        raise PlanError("准入账不是严格JSON", "native_lane_store_invalid") from exc
    return normalized


def _empty_ledger(epoch_id: str) -> dict[str, Any]:
    return {
        "contract": CONTRACT,
        "epoch_id": epoch_id,
        "revision": 0,
        "reservations": [],
    }


@runtime_checkable
class TransactionalSessionBindingRegistry(Protocol):
    """Binding seam whose read remains stable until its context exits.

    The caller must already hold the global admission lock before entering
    this context.  Implementations must never acquire the admission lock while
    holding a binding lock; the fixed order is admission -> binding.
    """

    def locked_current_binding(
        self, conversation_id: str
    ) -> AbstractContextManager[SessionBindingSnapshot]:
        """Hold the binding writer lock while yielding the current identity."""


class NativeDshLaneBindingRegistry:
    """Adapt the existing never-reused DSH binding registry to lane identity."""

    def __init__(self, sessions_root: str | Path) -> None:
        self.sessions_root = Path(sessions_root).absolute()

    @staticmethod
    def _snapshot(record: Mapping[str, Any]) -> SessionBindingSnapshot:
        if record.get("lifecycle_state") != ACTIVE:
            _fail("当前DSH绑定不是活动状态", "stale_context")
        if record.get("history_state") not in {
            DSH_SESSION_RESERVED,
            AUTHORITATIVE_DURABLE,
        }:
            _fail("当前DSH绑定没有可运行的历史身份", "stale_context")
        # NativeDshSessionBindingStore mints a new random dsh_session_id and
        # never reuses an old id.  The id itself is therefore the ABA fence;
        # generation 1 is the stable generation inside that unique identity.
        return SessionBindingSnapshot(
            identity=LaneIdentity(
                conversation_id=record["conversation_id"],
                dsh_session_id=record["dsh_session_id"],
                generation=1,
            ),
            binding_revision=record["revision"],
        )

    def read_current_binding(self, conversation_id: str) -> SessionBindingSnapshot:
        store = NativeDshSessionBindingStore(
            self.sessions_root, conversation_id=conversation_id
        )
        return self._snapshot(store.cold_read())

    @contextmanager
    def locked_current_binding(
        self, conversation_id: str
    ) -> Iterator[SessionBindingSnapshot]:
        store = NativeDshSessionBindingStore(
            self.sessions_root, conversation_id=conversation_id
        )
        # All existing binding mutations take this same per-binding lock.
        with file_lock(store.lock_path):
            yield self._snapshot(store.cold_read())


class DurableLaneAdmissionStore(DurableLaneAdmissionPort):
    """One durable, globally serialized App admission ledger.

    All methods use admission -> binding lock order.  This class must not be
    called from inside a binding-store transaction.
    """

    def __init__(
        self,
        sessions_root: str | Path,
        *,
        policy: LaneAdmissionPolicy,
        binding_registry: TransactionalSessionBindingRegistry | None = None,
    ) -> None:
        if not isinstance(policy, LaneAdmissionPolicy):
            raise TypeError("native_lane_policy_invalid")
        self.sessions_root = Path(sessions_root).absolute()
        self.policy = policy
        self.binding_registry = binding_registry or NativeDshLaneBindingRegistry(
            self.sessions_root
        )
        if not isinstance(self.binding_registry, TransactionalSessionBindingRegistry):
            raise TypeError("native_lane_binding_registry_invalid")
        self.registry_root = safe_path(self.sessions_root, REGISTRY_DIRECTORY)

    @property
    def path(self) -> Path:
        return safe_path(self.registry_root, LEDGER_FILENAME)

    @property
    def initialization_path(self) -> Path:
        return safe_path(self.registry_root, INITIALIZATION_FILENAME)

    @property
    def lock_path(self) -> Path:
        return safe_path(self.registry_root, LOCK_FILENAME)

    def _ensure_registry(self) -> None:
        self.sessions_root.mkdir(parents=True, exist_ok=True)
        self.registry_root.mkdir(exist_ok=True)
        info = self.registry_root.lstat()
        if (
            not self.registry_root.is_dir()
            or self.registry_root.is_symlink()
            or getattr(info, "st_file_attributes", 0)
            & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 1024)
        ):
            _fail("准入账目录不安全", "agent_path_rejected")

    def _read_initialization_unlocked(self) -> str | None:
        if not self.initialization_path.exists():
            return None
        try:
            with self.initialization_path.open("rb") as stream:
                raw = stream.read(MAX_INITIALIZATION_BYTES + 1)
        except OSError as exc:
            raise PlanError(
                "准入账初始化标记无法读取",
                "native_lane_store_unreadable",
            ) from exc
        if len(raw) > MAX_INITIALIZATION_BYTES:
            _fail("准入账初始化标记超过读取上限", "agent_size_limit")
        try:
            value = json.loads(
                raw.decode("utf-8", errors="strict"),
                object_pairs_hook=_strict_object,
                parse_constant=lambda _value: _fail(
                    "准入账初始化标记包含非有限数值"
                ),
            )
        except PlanError:
            raise
        except (UnicodeError, ValueError, TypeError, RecursionError) as exc:
            raise PlanError(
                "准入账初始化标记不是严格JSON",
                "native_lane_store_invalid",
            ) from exc
        if not isinstance(value, Mapping) or set(value) != _INITIALIZATION_KEYS:
            _fail("准入账初始化标记字段不合法")
        if (
            value.get("contract") != INITIALIZATION_CONTRACT
            or value.get("store_contract") != CONTRACT
        ):
            _fail(
                "准入账初始化标记版本不受支持",
                "native_lane_store_version_incompatible",
            )
        return _identity(value.get("epoch_id"), "epoch_id")

    def _read_unlocked(self) -> dict[str, Any] | None:
        epoch_id = self._read_initialization_unlocked()
        if not self.path.exists():
            if epoch_id is not None:
                _fail(
                    "已初始化的准入账文件缺失",
                    "native_lane_store_ledger_missing",
                )
            return None
        if epoch_id is None:
            _fail(
                "准入账缺少初始化标记",
                "native_lane_store_initialization_missing",
            )
        try:
            with self.path.open("rb") as stream:
                raw = stream.read(MAX_LEDGER_BYTES + 1)
        except OSError as exc:
            raise PlanError("准入账无法读取", "native_lane_store_unreadable") from exc
        if len(raw) > MAX_LEDGER_BYTES:
            _fail("准入账超过读取上限", "agent_size_limit")
        try:
            value = json.loads(
                raw.decode("utf-8", errors="strict"),
                object_pairs_hook=_strict_object,
                parse_constant=lambda _value: _fail("准入账包含非有限数值"),
            )
        except PlanError:
            raise
        except (UnicodeError, ValueError, TypeError, RecursionError) as exc:
            raise PlanError(
                "准入账不是严格JSON", "native_lane_store_invalid"
            ) from exc
        return _normalize_ledger(
            value,
            policy=self.policy,
            expected_epoch_id=epoch_id,
        )

    def _initialize_unlocked(self) -> dict[str, Any]:
        if self.path.exists() or self.initialization_path.exists():
            existing = self._read_unlocked()
            if existing is None:
                _fail("准入账初始化状态不一致")
            return existing
        epoch_id = f"lane-epoch-{uuid.uuid4()}"
        write_owned(
            self.registry_root,
            INITIALIZATION_FILENAME,
            {
                "contract": INITIALIZATION_CONTRACT,
                "store_contract": CONTRACT,
                "epoch_id": epoch_id,
            },
            MAX_INITIALIZATION_BYTES,
        )
        # Marker-first is intentional: a crash here freezes recovery rather
        # than silently treating a previously initialized store as empty.
        return self._write_unlocked(_empty_ledger(epoch_id))

    def _write_unlocked(self, value: Mapping[str, Any]) -> dict[str, Any]:
        epoch_id = self._read_initialization_unlocked()
        if epoch_id is None:
            _fail(
                "准入账缺少初始化标记",
                "native_lane_store_initialization_missing",
            )
        checked = _normalize_ledger(
            value,
            policy=self.policy,
            expected_epoch_id=epoch_id,
        )
        write_owned(
            self.registry_root,
            LEDGER_FILENAME,
            checked,
            MAX_LEDGER_BYTES,
        )
        return copy.deepcopy(checked)

    @staticmethod
    def _receipts(ledger: Mapping[str, Any]) -> tuple[LaneReservationReceipt, ...]:
        return tuple(_record_from_public(row) for row in ledger["reservations"])

    @staticmethod
    def _same_identity(
        left: SessionBindingSnapshot,
        right: SessionBindingSnapshot,
    ) -> bool:
        return left.identity == right.identity

    @staticmethod
    def _find(
        receipts: tuple[LaneReservationReceipt, ...], key: LaneKey
    ) -> LaneReservationReceipt | None:
        return next((row for row in receipts if row.lane.key == key), None)

    @staticmethod
    def _replace_record(
        receipts: tuple[LaneReservationReceipt, ...],
        replacement: LaneReservationReceipt,
    ) -> tuple[LaneReservationReceipt, ...]:
        return tuple(
            replacement if row.lane.key == replacement.lane.key else row
            for row in receipts
        )

    def cold_snapshot(self) -> dict[str, Any]:
        """Read a deterministic projection without creating runtime work."""

        if not self.registry_root.exists():
            ledger = None
        else:
            with file_lock(self.lock_path):
                ledger = self._read_unlocked()
        receipts = () if ledger is None else self._receipts(ledger)
        lanes, holdings = _facts(receipts)
        return {
            "contract": CONTRACT,
            "epoch_id": None if ledger is None else ledger["epoch_id"],
            "ledger_revision": 0 if ledger is None else ledger["revision"],
            "reservations": [row.public() for row in receipts],
            "projection": project_runtime_lanes(lanes, holdings, self.policy),
        }

    def evaluate_and_reserve(
        self,
        request: ResolvedLaneRequest,
        *,
        expected_admission_revision: int,
    ) -> LaneAdmissionOutcome:
        if not isinstance(request, ResolvedLaneRequest):
            raise TypeError("native_lane_request_not_host_resolved")
        expected = _counter(
            expected_admission_revision, "expected_admission_revision"
        )
        self._ensure_registry()
        with (
            file_lock(self.lock_path),
            self.binding_registry.locked_current_binding(
                request.public_request.conversation_id
            ) as current_binding,
        ):
            if not self._same_identity(current_binding, request.binding):
                _fail("准入请求的DSH绑定已经变化", "stale_context")
            ledger = self._read_unlocked()
            receipts = () if ledger is None else self._receipts(ledger)
            existing = self._find(receipts, request.key)
            request_fingerprint = _admission_fingerprint(request)
            if existing is not None:
                # Lost-receipt replay attaches to the existing lane.  It
                # never changes leases and never authorizes a provider start.
                if expected != 0:
                    _fail("准入重放必须从修订0开始", "stale_context")
                if existing.admission_fingerprint != request_fingerprint:
                    _fail(
                        "相同lane key对应了不同的Host准入请求",
                        "lane_request_replay_conflict",
                    )
                return LaneAdmissionOutcome(
                    disposition="attached",
                    receipt=copy.deepcopy(existing),
                    provider_start_authorized=False,
                )
            if current_binding.binding_revision != request.binding.binding_revision:
                _fail("准入请求的DSH绑定修订已经变化", "stale_context")
            if expected != 0:
                _fail("新准入必须从修订0开始", "stale_context")
            if len(receipts) >= MAX_RESERVATIONS:
                _fail(
                    "准入账reservation数量已达上限",
                    "native_lane_store_capacity_exhausted",
                )
            lanes, holdings = _facts(receipts)
            decision = evaluate_lane_admission(
                lanes, holdings, request, self.policy
            )
            if decision["decision"] != "admit":
                _fail(
                    "当前资源不能接纳新的Agent轮次",
                    decision["reason_codes"][0],
                    {"decision": decision},
                )
            if ledger is None:
                ledger = self._initialize_unlocked()
            next_revision = _next_revision(ledger["revision"])
            lane = RuntimeLane(
                key=request.key,
                binding_revision=current_binding.binding_revision,
                admission_revision=next_revision,
            )
            receipt = LaneReservationReceipt(
                reservation_id=f"lane-reservation-{uuid.uuid4()}",
                admission_fingerprint=request_fingerprint,
                lane=lane,
                held_leases=request.required_leases,
            )
            proposed = {
                "contract": CONTRACT,
                "epoch_id": ledger["epoch_id"],
                "revision": next_revision,
                "reservations": [
                    _record_public(row) for row in (*receipts, receipt)
                ],
            }
            self._write_unlocked(proposed)
            return LaneAdmissionOutcome(
                disposition="created",
                receipt=copy.deepcopy(receipt),
                provider_start_authorized=any(
                    lease.resource_kind == PROVIDER_RESOURCE
                    for lease in request.required_leases
                ),
            )

    def replace_lease_holdings(
        self,
        key: LaneKey,
        desired_leases: tuple[ResourceLease, ...],
        *,
        expected_reservation_id: str,
        expected_admission_revision: int,
    ) -> LaneReservationReceipt:
        if not isinstance(key, LaneKey):
            raise TypeError("native_lane_key_invalid")
        expected = _counter(
            expected_admission_revision, "expected_admission_revision"
        )
        expected_id = _reservation_id(expected_reservation_id)
        desired = tuple(desired_leases)
        self._ensure_registry()
        with (
            file_lock(self.lock_path),
            self.binding_registry.locked_current_binding(
                key.identity.conversation_id
            ) as current_binding,
        ):
            if current_binding.identity != key.identity:
                _fail("lane对应的DSH绑定已经变化", "stale_context")
            ledger = self._read_unlocked()
            receipts = () if ledger is None else self._receipts(ledger)
            current = self._find(receipts, key)
            if current is None:
                _fail("lane reservation不存在", "stale_context")
            if current.reservation_id != expected_id:
                _fail("lane reservation身份已经变化", "stale_context")
            if current.lane.admission_revision != expected:
                _fail("lane准入修订已经变化", "stale_context")
            lanes, holdings = _facts(receipts)
            decision = evaluate_lease_replacement(
                lanes, holdings, key, desired, self.policy
            )
            if decision["decision"] != "replace":
                _fail(
                    "当前资源不能替换lane租约",
                    decision["reason_codes"][0],
                    {"decision": decision},
                )
            next_revision = _next_revision(ledger["revision"])
            lane = RuntimeLane(
                key=key,
                binding_revision=current_binding.binding_revision,
                admission_revision=next_revision,
            )
            replacement = LaneReservationReceipt(
                reservation_id=current.reservation_id,
                admission_fingerprint=current.admission_fingerprint,
                lane=lane,
                held_leases=desired,
            )
            proposed = {
                "contract": CONTRACT,
                "epoch_id": ledger["epoch_id"],
                "revision": next_revision,
                "reservations": [
                    _record_public(row)
                    for row in self._replace_record(receipts, replacement)
                ],
            }
            self._write_unlocked(proposed)
            return copy.deepcopy(replacement)

    def release_lane(
        self,
        key: LaneKey,
        *,
        expected_reservation_id: str,
        expected_admission_revision: int,
    ) -> None:
        if not isinstance(key, LaneKey):
            raise TypeError("native_lane_key_invalid")
        expected = _counter(
            expected_admission_revision, "expected_admission_revision"
        )
        expected_id = _reservation_id(expected_reservation_id)
        self._ensure_registry()
        with file_lock(self.lock_path):
            ledger = self._read_unlocked()
            receipts = () if ledger is None else self._receipts(ledger)
            current = self._find(receipts, key)
            if current is None:
                return
            if current.reservation_id != expected_id:
                _fail("lane reservation身份已经变化", "stale_context")
            if current.lane.admission_revision != expected:
                _fail("lane准入修订已经变化", "stale_context")
            next_revision = _next_revision(ledger["revision"])
            proposed = {
                "contract": CONTRACT,
                "epoch_id": ledger["epoch_id"],
                "revision": next_revision,
                "reservations": [
                    _record_public(row) for row in receipts if row.lane.key != key
                ],
            }
            self._write_unlocked(proposed)


__all__ = [
    "CONTRACT",
    "MAX_LEDGER_BYTES",
    "MAX_RESERVATIONS",
    "DurableLaneAdmissionStore",
    "NativeDshLaneBindingRegistry",
    "TransactionalSessionBindingRegistry",
]
