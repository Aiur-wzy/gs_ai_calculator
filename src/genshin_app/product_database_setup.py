"""Install a signed database for an already verified independent App."""
from __future__ import annotations

import copy
import json
import os
import shutil
import sys
from pathlib import Path
from threading import Event, Lock
from uuid import uuid4

from genshin_app import version
from genshin_app.database_transaction import atomic_json, profile_writer
from genshin_app.installation_timing import InstallationTiming
from genshin_app.package_integrity import (
    PackageIntegrityError,
    verify_package_artifact,
    verify_package_manifest_descriptor,
    verify_signed_package_startup,
)
from genshin_app.paths import is_frozen
from genshin_app.product_install_contract import (
    FIRST_INSTALL_CONTRACT,
    REGISTRATION_FILENAME,
    FirstInstallError,
)
from genshin_app.product_release_index import (
    LIVE_PAIR_PROOF_FILENAME,
    PER_USER_UPDATER_RELATIVE,
    digest,
    discover_product_index,
)
from genshin_app.release_pair_discovery import (
    MAX_DOCUMENT_BYTES,
    validate_latest_document,
)
from genshin_app.release_pair_storage import preflight_before_download
from genshin_app.release_pair_transport import (
    MAX_DATABASE_BYTES,
    _download_https,
    _extract_database,
)
from genshin_app.release_pair_trust import build_trust_verifier
from genshin_app.release_pair_update import (
    LIVE_PAIR_MANIFEST_FILENAME,
    _sha256,
    qualified_database_from_signed_document,
)
from genshin_app.windows_known_folders import is_reparse_point, reject_reparse_chain
from genshin_db.release_migration import (
    abandon_unpublished_database_attempt,
    install_database_release,
)

PENDING_SETUP_FILENAME = "database-first-install-pending.json"


def _read_control(path):
    if is_reparse_point(path) or path.stat().st_size > MAX_DOCUMENT_BYTES:
        raise FirstInstallError("product_database_setup_registration_invalid")
    return json.loads(path.read_text(encoding="utf-8"))


def _signed_document(raw, trust_verifier):
    document, trusted = validate_latest_document(
        raw, expected_channel="stable", trust_verifier=trust_verifier, allow_unsigned_dev=False)
    if not trusted or document["artifacts"]["app"]["format"] != "app-zip":
        raise FirstInstallError("product_database_setup_app_source_invalid")
    return document


class DatabaseSetup:
    """Initialize a verified independent App's local database."""

    def __init__(self, data_dir, app_root, *, url, trust_verifier, app_release_id, app_version,
                 logic_profile=None, data_contract_version=None, portable=False):
        self.data_dir = Path(data_dir).absolute()
        self.app_root = Path(app_root).absolute()
        self.url = url
        self.trust_verifier = trust_verifier
        self.app_release_id = app_release_id
        self.app_version = app_version
        self.logic_profile = logic_profile or version.DATABASE_LOGIC_PROFILE
        self.data_contract_version = (version.DATABASE_DATA_CONTRACT_VERSION
                                      if data_contract_version is None else data_contract_version)
        self.registration = self.data_dir.parent / REGISTRATION_FILENAME
        self.pending = self.data_dir / PENDING_SETUP_FILENAME
        self.cache = self.data_dir / "cache"
        self._operation = Lock()
        self._cancel = Event()
        self._progress = None
        self._portable = portable

    def _check_app(self, document, *, startup=False):
        pair = document["pair_manifest"]
        app = pair["app"]
        if (app["app_release_id"], app["app_version"], app["logic_profile"], app["data_contract_version"]) != (
                self.app_release_id, self.app_version, self.logic_profile, self.data_contract_version):
            raise FirstInstallError("product_database_setup_app_identity_mismatch")
        expected = app["artifact_manifest"]["sha256"]
        if ((self.app_root / "_internal" / "bundled").exists()
                or not (self.app_root / PER_USER_UPDATER_RELATIVE).is_file()):
            raise FirstInstallError("product_database_setup_app_identity_mismatch")
        try:
            if startup:
                verify_signed_package_startup(self.app_root, expected)
            elif verify_package_manifest_descriptor(self.app_root)["manifest_sha256"] != expected:
                raise FirstInstallError("release_pair_app_artifact_mismatch")
        except PackageIntegrityError as error:
            raise FirstInstallError(error.code) from error

    def registered(self):
        if self.app_root != self.data_dir.parent / "app" or self.data_dir.name != "data":
            return False
        if not self.registration.is_file():
            return False
        row = _read_control(self.registration)
        return (isinstance(row, dict) and row.get("contract") == FIRST_INSTALL_CONTRACT
                and row.get("scope") == "current_user" and row.get("database_state") == "database_not_installed"
                and row.get("app_release_id") == self.app_release_id)

    def available(self):
        if not (self.registered() or self._portable):
            return False
        if not self.pending.is_file() and any((self.data_dir / name).exists() for name in (
                "database-live-manifest.json", "database-live-receipt.json", LIVE_PAIR_PROOF_FILENAME)):
            return False
        if (self.data_dir / LIVE_PAIR_MANIFEST_FILENAME).exists():
            return self.pending.is_file()
        return not (self.data_dir / "genshin.db").exists() or self.pending.is_file()

    def validate_app_only(self, *, startup=False):
        if not self.available():
            raise FirstInstallError("product_database_setup_app_only_required")
        reject_reparse_chain(self.app_root, reason="product_database_setup_app_path_invalid")
        ancestor = next(path for path in (self.data_dir, *self.data_dir.parents) if os.path.lexists(path))
        reject_reparse_chain(ancestor, reason="product_database_setup_data_path_invalid")
        if self.registered():
            self._check_app(_signed_document(_read_control(self.registration).get("signed_app_source"),
                                             self.trust_verifier), startup=startup)
        elif startup:
            try:
                verify_package_artifact(self.app_root)
            except PackageIntegrityError as error:
                raise FirstInstallError(error.code) from error
        if self.pending.is_file():
            document = _signed_document(_read_control(self.pending), self.trust_verifier)
            self._check_app(document)
            database = self.data_dir / "genshin.db"
            if database.exists() and (is_reparse_point(database)
                    or _sha256(database) != document["database_manifest"]["database"]["sha256"]):
                raise FirstInstallError("product_database_setup_unexpected_live_database")

    def _require_direct_cache(self):
        for path in (self.cache, self.cache / "product-release-index.json",
                     self.cache / "product-release-index.lock", self.cache / "product-release-index.json.tmp"):
            if os.path.lexists(path) and is_reparse_point(path):
                raise FirstInstallError("product_database_setup_cache_path_invalid")

    def _selection(self, local_document=None):
        self.validate_app_only()
        if self.pending.is_file():
            document = _signed_document(_read_control(self.pending), self.trust_verifier)
            self._check_app(document)
            if local_document is not None and _read_control(local_document) != document:
                raise FirstInstallError("product_database_setup_state_changed")
            return document, digest({"mode": "database_setup_recovery", "document": document})
        if local_document is not None:
            document = _signed_document(_read_control(local_document), self.trust_verifier)
            self._check_app(document)
            return document, digest({"mode": "database_setup_local", "document": document})
        self._require_direct_cache()
        index = discover_product_index(self.url, self.cache / "product-release-index.json",
                                       channel="stable", trust_verifier=self.trust_verifier)
        choices = [(index["database_sequences"][database], pair_id)
                   for (app, database), pair_id in index["combinations"].items()
                   if app == self.app_release_id]
        if not choices:
            raise FirstInstallError("product_database_setup_no_compatible_release")
        pair_id = max(choices)[1]
        document = index["pairs"][pair_id]
        self._check_app(document)
        return document, digest({"mode": "database_setup", "index_sha256": index["index_sha256"],
                                 "pair_id": pair_id})

    def preview(self, *, local_document=None):
        from .release_update_projection import product_release_details

        document, confirmation = self._selection(local_document)
        return {"confirmation_digest": confirmation, "mode": "database_setup",
                "app_release_id": self.app_release_id,
                "database_release_id": document["database_manifest"]["database_release_id"],
                "app_bytes": 0, "local_package": local_document is not None,
                "database_bytes": (0 if local_document is not None else
                                   document["artifacts"]["database"]["size_bytes"]),
                **product_release_details(document)}

    def progress(self):
        return copy.deepcopy(self._progress)

    def cancel(self):
        if self._progress and not self._progress["cancellable"]:
            return {"cancel_requested": False, "reason": "installation_finishing"}
        self._cancel.set()
        return {"cancel_requested": True}

    def _record_progress(self, value):
        self._progress = value

    def install(self, confirmation_digest, *, user_confirmed, local_database=None, local_document=None):
        if user_confirmed is not True:
            raise FirstInstallError("product_first_install_confirmation_required")
        if not self._operation.acquire(blocking=False):
            raise FirstInstallError("product_first_install_busy")
        self._cancel.clear()
        self._progress = {"phase": "preparing", "cancellable": True}
        timing = InstallationTiming(self._record_progress)
        work = None
        try:
            if (local_database is None) != (local_document is None):
                raise FirstInstallError("product_database_setup_local_package_required")
            document, expected = self._selection(local_document)
            if confirmation_digest != expected:
                raise FirstInstallError("product_first_install_confirmation_mismatch")
            pair, manifest = document["pair_manifest"], document["database_manifest"]
            qualification = qualified_database_from_signed_document(document)
            artifact = document["artifacts"]["database"]
            if local_database is None:
                self._require_direct_cache()
                preflight_before_download(
                    workspace=self.cache, active_app_root=self.app_root, data_dir=self.data_dir,
                    app_archive_bytes=0, app_unpacked_bytes=0, app_unpacked_files=0,
                    target_database_bytes=manifest["database"]["size_bytes"], current_database_bytes=0,
                    reuse_installed_app=True, download_cache=self.cache / "artifacts",
                    database_archive_bytes=artifact["size_bytes"] if artifact["format"] == "sqlite-gzip" else 0)
                work = self.cache / f"database-setup-{uuid4().hex}"
                work.mkdir(parents=True)
                package = work / "genshin.db"
                compressed = artifact["format"] == "sqlite-gzip"
                archive = work / "database.gz" if compressed else package
                timing({"phase": "download", "cancellable": True})
                _download_https(artifact, archive, timeout=30, maximum_bytes=MAX_DATABASE_BYTES,
                                cancel_check=self._cancel.is_set, cache_dir=self.cache / "artifacts",
                                progress=timing)
                if compressed:
                    timing({"phase": "extract_database", "cancellable": True})
                    _extract_database(archive, package, artifact, self._cancel.is_set)
            else:
                timing({"phase": "verifying_archives", "cancellable": True})
                selected = reject_reparse_chain(Path(local_database), reason="product_database_setup_local_path_invalid")
                identity = manifest["database"]
                if selected.stat().st_size == identity["size_bytes"] and _sha256(selected) == identity["sha256"]:
                    package = selected
                elif (artifact["format"] == "sqlite-gzip" and selected.stat().st_size == artifact["size_bytes"]
                      and _sha256(selected) == artifact["sha256"]):
                    self._require_direct_cache()
                    preflight_before_download(
                        workspace=self.cache, active_app_root=self.app_root, data_dir=self.data_dir,
                        app_archive_bytes=0, app_unpacked_bytes=0, app_unpacked_files=0,
                        target_database_bytes=identity["size_bytes"], current_database_bytes=0,
                        reuse_installed_app=True)
                    work = self.cache / f"database-setup-{uuid4().hex}"
                    work.mkdir(parents=True)
                    package = work / "genshin.db"
                    timing({"phase": "extract_database", "cancellable": True})
                    _extract_database(selected, package, artifact, self._cancel.is_set)
                else:
                    raise FirstInstallError("product_database_setup_package_mismatch")
            with profile_writer(self.data_dir, timeout=0):
                from genshin_app.database_legacy_processes import (
                    packaged_peer_processes,
                )
                if packaged_peer_processes():
                    raise FirstInstallError("database_legacy_instance_running")
                if self._cancel.is_set():
                    raise FirstInstallError("product_first_install_cancelled")
                if not self.available() or (self.pending.is_file() and _read_control(self.pending) != document):
                    raise FirstInstallError("product_database_setup_state_changed")
                timing({"phase": "qualifying", "cancellable": True})
                self._check_app(document, startup=True)
                if self._cancel.is_set():
                    raise FirstInstallError("product_first_install_cancelled")
                timing({"phase": "installing", "cancellable": False})
                if self.pending.is_file():
                    abandon_unpublished_database_attempt(self.data_dir, manifest, expected_old_sha256=None)
                # Persist exact authorization so a restart can recover the same DB transaction.
                atomic_json(self.pending, document)
                app = pair["app"]
                install_database_release(
                    self.data_dir, package, manifest, logic_profile=app["logic_profile"],
                    data_contract_version=app["data_contract_version"], user_confirmed=True, production=True,
                    app_release_id=app["app_release_id"], release_pair_id=pair["release_pair_id"],
                    required_database_release_id=app["required_database_release_id"],
                    pair_policy=pair["pair_policy"], app_version=app["app_version"],
                    app_artifact_manifest_sha256=app["artifact_manifest"]["sha256"], pair_manifest=pair,
                    **({"qualification": qualification} if qualification is not None else {}))
                atomic_json(self.data_dir / LIVE_PAIR_PROOF_FILENAME, document)
                atomic_json(self.data_dir / LIVE_PAIR_MANIFEST_FILENAME, pair)
                self.pending.unlink()
                if self.registration.is_file():
                    from .installation_inventory import save_maintenance_proof
                    save_maintenance_proof(self.data_dir.parent, document)
                    row = _read_control(self.registration)
                    row.update(database_state="installed", app_release_id=pair["app"]["app_release_id"])
                    atomic_json(self.registration, row)
            timing({"phase": "cleanup", "cancellable": False})
            if work is not None:
                shutil.rmtree(work, ignore_errors=True)
                work = None
            return {"status": "complete", "requires_restart": True,
                    "database_release_id": manifest["database_release_id"], "timing": timing.finish()}
        finally:
            if work is not None:
                shutil.rmtree(work, ignore_errors=True)
            self._progress = None
            self._operation.release()


def build_database_setup(data_dir):
    if not is_frozen() or version.DATABASE_RELEASE_CHANNEL != "stable":
        return None
    trust = build_trust_verifier(version.DATABASE_RELEASE_TRUST_KEYS)
    if trust is None:
        return None
    app_root = Path(sys.executable).absolute().parent
    independent = ((app_root / PER_USER_UPDATER_RELATIVE).is_file()
                   and not (app_root / "_internal" / "bundled").exists())
    setup = DatabaseSetup(data_dir, app_root,
                          url=version.DATABASE_RELEASE_MANIFEST_URL, trust_verifier=trust,
                          app_release_id=version.APP_RELEASE_ID, app_version=version.APP_VERSION, portable=independent)
    return setup if setup.available() else None
