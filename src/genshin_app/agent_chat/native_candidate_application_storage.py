"""App-owned candidate NativeCandidateApplicationStorage; no Session or Turn state."""
from __future__ import annotations

import copy
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .native_candidate_transaction_contracts import (
    APPLICATION_INTENT_CONTRACT,
    APPLICATION_INTENT_FILE,
    APPLIED_PROOFS_CONTRACT,
    APPLIED_PROOFS_FILE,
    LAST_APPLIED_WHITEBOARD_PROJECTION_FILE,
    MAX_APPLICATION_INTENT_BYTES,
    MAX_APPLIED_PROOF_RECORDS,
    MAX_APPLIED_PROOFS_BYTES,
    MAX_LAST_APPLIED_WHITEBOARD_PROJECTION_BYTES,
    NativeServiceV2Error,
    _fail,
    _stamp,
    _unique_application_intent_pairs,
    _unique_json_pairs,
    _validate_application_intent,
    _validate_applied_proofs,
    _validate_last_applied_whiteboard_projection,
)
from .sessions import read_owned, safe_path, write_owned


class NativeCandidateApplicationStorage:
    @staticmethod
    def _application_timestamp() -> str:
        """Timestamp seam consumed by the application transaction port."""

        return _stamp()

    @staticmethod
    def _max_applied_proof_records() -> int:
        return MAX_APPLIED_PROOF_RECORDS

    @staticmethod
    def _read_applied_proofs(
        root: Path,
        conversation_id: str,
    ) -> dict[str, Any]:
        empty = {
            "contract": APPLIED_PROOFS_CONTRACT,
            "conversation_id": conversation_id,
            "records": [],
        }
        try:
            path = safe_path(root, APPLIED_PROOFS_FILE)
            if not path.exists():
                return empty
            if (
                not path.is_file()
                or path.is_symlink()
                or path.stat().st_size > MAX_APPLIED_PROOFS_BYTES
            ):
                _fail("native_applied_proof_invalid")
            value = json.loads(
                path.read_text(encoding="utf-8"),
                object_pairs_hook=_unique_json_pairs,
                parse_constant=lambda _value: _fail(
                    "native_applied_proof_invalid",
                ),
            )
            return _validate_applied_proofs(
                value,
                conversation_id=conversation_id,
            )
        except NativeServiceV2Error:
            raise
        except Exception:  # noqa: BLE001 - normalize all untrusted storage failures
            _fail("native_applied_proof_invalid")

    @staticmethod
    def _write_applied_proofs(root: Path, value: dict[str, Any]) -> None:
        try:
            checked = _validate_applied_proofs(
                value,
                conversation_id=value["conversation_id"],
            )
            write_owned(
                root,
                APPLIED_PROOFS_FILE,
                checked,
                MAX_APPLIED_PROOFS_BYTES,
            )
        except NativeServiceV2Error:
            raise
        except Exception:  # noqa: BLE001 - never trust or expose local storage details
            _fail("native_applied_proof_invalid")

    @staticmethod
    def _read_application_intent(
        root: Path,
        conversation_id: str,
    ) -> dict[str, Any]:
        empty = {
            "contract": APPLICATION_INTENT_CONTRACT,
            "conversation_id": conversation_id,
            "intent": None,
        }
        try:
            path = safe_path(root, APPLICATION_INTENT_FILE)
            if not path.exists():
                return empty
            if (
                not path.is_file()
                or path.is_symlink()
                or path.stat().st_size > MAX_APPLICATION_INTENT_BYTES
            ):
                _fail("native_application_intent_invalid")
            value = json.loads(
                path.read_text(encoding="utf-8"),
                object_pairs_hook=_unique_application_intent_pairs,
                parse_constant=lambda _value: _fail(
                    "native_application_intent_invalid",
                ),
            )
            return _validate_application_intent(
                value,
                conversation_id=conversation_id,
            )
        except NativeServiceV2Error:
            raise
        except Exception:  # noqa: BLE001 - normalize all untrusted storage failures
            _fail("native_application_intent_invalid")

    @staticmethod
    def _write_application_intent(root: Path, value: dict[str, Any]) -> None:
        try:
            checked = _validate_application_intent(
                value,
                conversation_id=value["conversation_id"],
            )
            write_owned(
                root,
                APPLICATION_INTENT_FILE,
                checked,
                MAX_APPLICATION_INTENT_BYTES,
            )
        except NativeServiceV2Error:
            raise
        except Exception:  # noqa: BLE001 - never expose local storage details
            _fail("native_application_intent_invalid")

    @staticmethod
    def _read_last_applied_whiteboard_projection(
        root: Path,
        conversation_id: str,
    ) -> dict[str, Any] | None:
        try:
            path = safe_path(root, LAST_APPLIED_WHITEBOARD_PROJECTION_FILE)
            if not path.exists():
                return None
            if (
                not path.is_file()
                or path.is_symlink()
                or path.stat().st_size
                > MAX_LAST_APPLIED_WHITEBOARD_PROJECTION_BYTES
            ):
                _fail("native_last_applied_whiteboard_projection_invalid")
            value = json.loads(
                path.read_text(encoding="utf-8"),
                object_pairs_hook=_unique_json_pairs,
                parse_constant=lambda _value: _fail(
                    "native_last_applied_whiteboard_projection_invalid",
                ),
            )
            return _validate_last_applied_whiteboard_projection(
                value,
                conversation_id=conversation_id,
            )
        except NativeServiceV2Error:
            raise
        except Exception:  # noqa: BLE001 - normalize untrusted storage failures
            _fail("native_last_applied_whiteboard_projection_invalid")

    @staticmethod
    def _write_last_applied_whiteboard_projection(
        root: Path,
        value: dict[str, Any],
    ) -> None:
        try:
            checked = _validate_last_applied_whiteboard_projection(
                value,
                conversation_id=value["conversation_id"],
            )
            write_owned(
                root,
                LAST_APPLIED_WHITEBOARD_PROJECTION_FILE,
                checked,
                MAX_LAST_APPLIED_WHITEBOARD_PROJECTION_BYTES,
            )
        except NativeServiceV2Error:
            raise
        except Exception:  # noqa: BLE001 - never expose local storage details
            _fail("native_last_applied_whiteboard_projection_invalid")

    @staticmethod
    def _write_immutable_projection_artifact(
        root: Path,
        name: str,
        value: Mapping[str, Any],
        maximum: int,
    ) -> None:
        try:
            path = safe_path(root, name)
            detached = copy.deepcopy(dict(value))
            if path.exists():
                if read_owned(root, name, maximum) != detached:
                    _fail("native_last_applied_whiteboard_projection_invalid")
                return
            write_owned(root, name, detached, maximum)
        except NativeServiceV2Error:
            raise
        except Exception:  # noqa: BLE001 - immutable Host artifact boundary
            _fail("native_last_applied_whiteboard_projection_invalid")
