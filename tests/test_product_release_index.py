from __future__ import annotations

import copy
import hashlib
import io
import json
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from urllib.error import URLError

import pytest

from genshin_app import (
    database_bootstrap,
    database_release_reuse,
    paths,
    product_first_install,
    product_release_index,
    release_pair_feed,
    version,
)
from genshin_app import release_pair_coordinator as coordinator
from genshin_app import release_pair_helper as helper
from genshin_app import release_pair_transport as transport
from genshin_app import release_pair_update as update
from genshin_app.agent_chat import native_compatibility_identity as native_identity
from genshin_app.package_integrity import verify_package_artifact
from genshin_app.product_release_index import (
    INDEX_CONTRACT,
    LIVE_PAIR_PROOF_FILENAME,
    PER_USER_UPDATER_RELATIVE,
    canonical,
    discover_product_index,
    release_options,
    validate_product_release_index,
)
from genshin_app.release_pair_discovery import ReleasePairDiscoveryError
from genshin_app.release_pair_trust import ALGORITHM
from genshin_db import release_migration, release_profile
from genshin_db.locking import file_lock
from genshin_db.release_integrity import build_mutable_layer_fingerprint
from tests.test_database_release_migration import (
    _fake_identity,
    _fixture_database,
    _manifest,
)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _signature(payload):
    # Isolated signature fixture. The production PSS verifier has its own
    # independent external-signature interoperability test.
    return {"algorithm": ALGORITHM, "key_id": "fixture", "value": hashlib.sha256(canonical(payload)).hexdigest()}


def _verify(payload, signature):
    return signature == _signature(payload)


def _signed(payload):
    return {**payload, "signature": _signature(payload)}


def _identity(path, **kwargs):
    identity = _fake_identity(path, **kwargs)
    identity["mutable_layer_fingerprint"] = build_mutable_layer_fingerprint(path)
    return identity


def _install_facts(data, app, pair, manifest, document):
    data.mkdir(exist_ok=True)
    shutil.copyfile(manifest[0], data / "genshin.db")
    identity = _identity(data / "genshin.db")
    manifest = manifest[1]
    release_profile.write_database_release_manifest(data / update.LIVE_DATABASE_MANIFEST_FILENAME, manifest)
    release_profile.write_release_pair_manifest(data / update.LIVE_PAIR_MANIFEST_FILENAME, pair)
    (data / update.LIVE_DATABASE_RECEIPT_FILENAME).write_bytes(canonical(release_profile.create_database_live_receipt(
        manifest, identity, installed_at=datetime.now(UTC).isoformat(),
    )))
    (data / LIVE_PAIR_PROOF_FILENAME).write_bytes(canonical(document))
    return app / "app.bin"


@pytest.fixture
def releases(tmp_path, monkeypatch):
    monkeypatch.setattr(update, "build_trust_verifier", lambda _keys: _verify)
    for module in (release_profile, database_release_reuse, release_pair_feed, update):
        monkeypatch.setattr(module, "read_database_identity", lambda path, **_: _identity(path))
    monkeypatch.setattr(release_migration, "_validate_database", _fake_identity)
    monkeypatch.setattr(release_migration.shutil, "disk_usage", lambda _path: SimpleNamespace(free=100 * 1024**3))
    now = datetime.now(UTC)
    before = (now - timedelta(hours=1)).isoformat()
    after = (now + timedelta(hours=1)).isoformat()
    apps, databases = {}, {}
    for generation in (1, 2):
        root = tmp_path / f"app-source-{generation}"
        root.mkdir()
        executable = root / "app.bin"
        executable.write_bytes(f"app-artifact-{generation}".encode())
        updater = root / PER_USER_UPDATER_RELATIVE
        updater.parent.mkdir()
        updater.write_bytes(f"app-updater-{generation}".encode())
        inventory = root / "PACKAGE-FILES.sha256"
        inventory.write_bytes(f"{_sha(executable)}  app.bin\n{_sha(updater)}  {PER_USER_UPDATER_RELATIVE}\n".encode())
        apps[generation] = (root, _sha(inventory))
        database = tmp_path / f"db-source-{generation}.sqlite"
        _fixture_database(database, upstream_commit=str(generation) * 40,
                          annotation_prefix=f"version-{generation}", semantic_marker=f"version-{generation}")
        manifest = _manifest(database, logic_profile=version.DATABASE_LOGIC_PROFILE,
                             release_id=f"db-7.0.0-r{generation}", release_sequence=generation)
        databases[generation] = (database, manifest)

    documents = {}
    sources = {}
    for a, d, sequence in ((1, 1, 1), (2, 1, 2), (1, 2, 3), (2, 2, 4)):
        app, app_digest = apps[a]
        database, manifest = databases[d]
        pair = release_profile.create_release_pair_manifest(
            manifest, release_pair_id=f"pair-product-{a}-{d}", pair_sequence=sequence,
            pair_policy="exact", app_release_id=f"app-product-{a}", app_version=version.APP_VERSION,
            app_artifact_manifest_sha256=app_digest, released_at=before,
        )
        pair_path = tmp_path / f"pair-{a}-{d}.json"
        database_path = tmp_path / f"database-{d}.json"
        release_profile.write_release_pair_manifest(pair_path, pair)
        release_profile.write_database_release_manifest(database_path, manifest)
        output = tmp_path / f"artifacts-{a}-{d}"
        receipt = release_pair_feed.prepare_release_pair_feed(
            app_root=app, database=database, pair_manifest_path=pair_path,
            database_manifest_path=database_path, base_url=f"https://example.invalid/app-{a}",
            database_base_url=f"https://example.invalid/db-{d}", output_directory=output,
            independent_app=True,
        )
        unsigned = json.loads((output / release_pair_feed.UNSIGNED_DOCUMENT_FILENAME).read_bytes())
        unsigned.pop("signature")
        documents[a, d] = _signed(unsigned)
        for artifact in receipt["artifacts"].values():
            sources[artifact["url"]] = output / artifact["url"].split("/")[-1]

    payload = {"contract": INDEX_CONTRACT, "schema_version": 1, "channel": "stable", "index_sequence": 9,
               "released_at": before, "expires_at": after, "latest_app_release_id": "app-product-2",
               "latest_database_release_id": "db-7.0.0-r2",
               "apps": [{"app_release_id": f"app-product-{item}", "release_sequence": item,
                         "released_at": before} for item in apps], "pairs": list(documents.values())}
    return apps, databases, documents, sources, payload


def test_index_options_expiry_and_replay(tmp_path, monkeypatch, releases):
    _, _, documents, _, payload = releases
    current = documents[1, 1]
    signed = _signed(payload)
    index = validate_product_release_index(signed, channel="stable", trust_verifier=_verify)
    assert [option["mode"] for option in release_options(index, current["pair_manifest"], current["database_manifest"])] == [
        "app_only", "database_only", "both",
    ]
    with pytest.raises(ReleasePairDiscoveryError, match="latest_pair_missing"):
        validate_product_release_index(_signed({**payload, "pairs": list(documents.values())[:-1]}),
                                       channel="stable", trust_verifier=_verify)
    expired = datetime.now(UTC) + timedelta(hours=3)
    with pytest.raises(ReleasePairDiscoveryError, match="not_current"):
        validate_product_release_index(signed, channel="stable", trust_verifier=_verify, now=expired)

    import genshin_app.product_release_index as product

    response = [signed]
    monkeypatch.setattr(product, "_fetch_https", lambda *_args, **_kwargs: (200, canonical(response[0]), '"etag"'))
    args = ("https://example.invalid/latest.json", tmp_path / "cache" / "index.json")
    assert discover_product_index(*args, channel="stable", trust_verifier=_verify)["signed_index"] == signed
    response[0] = _signed({**payload, "index_sequence": 8})
    with pytest.raises(ReleasePairDiscoveryError, match="index_replay"):
        discover_product_index(*args, channel="stable", trust_verifier=_verify)
    response[0] = _signed({**payload, "pairs": list(reversed(payload["pairs"]))})
    with pytest.raises(ReleasePairDiscoveryError, match="sequence_conflict"):
        discover_product_index(*args, channel="stable", trust_verifier=_verify)


def test_public_site_publishes_the_exact_signed_index_and_offline_pair(tmp_path, monkeypatch, releases):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "scripts"))
    from scripts import build_release_pair_feed

    _, _, documents, _, payload = releases
    index_path = tmp_path / "unsigned.json"
    signature_path = tmp_path / "signature.json"
    index_path.write_bytes(canonical({**payload, "signature": None}))
    signature_path.write_bytes(canonical(_signature(payload)))
    monkeypatch.setattr(build_release_pair_feed, "build_trust_verifier", lambda _keys: _verify)
    site = tmp_path / "site"
    setup_asset = tmp_path / "setup.json"
    setup_asset.write_bytes(canonical({"url": "https://example.invalid/Setup.zip",
                                       "size_bytes": 17_000_000, "sha256": "d" * 64}))
    result = build_release_pair_feed.finalize_product_site(index_path, signature_path, site, (),
                                                          setup_asset_path=setup_asset)
    assert result["status"] == "finalized"
    assert (site / "latest.json").read_bytes() == canonical(_signed(payload))
    assert (site / "database-install-pair.json").read_bytes() == canonical(documents[2, 2])
    page = (site / "index.html").read_text(encoding="utf-8")
    assert all(locator["url"] in page and locator["sha256"] in page for locator in documents[2, 2]["artifacts"].values())
    assert "非官方工具" in page and "YuanShenResources" in page
    assert "https://example.invalid/Setup.zip" in page and "d" * 64 in page
    assert "GPT-6.1 Sol" in page and (site / ".nojekyll").is_file()


def test_first_index_requires_verified_first_stable_app_pair(tmp_path, monkeypatch, releases):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "scripts"))
    from scripts import build_release_pair_feed

    _, _, documents, _, payload = releases
    monkeypatch.setattr(build_release_pair_feed, "build_trust_verifier", lambda _keys: _verify)
    pair_path = tmp_path / "pair.json"
    index_path = tmp_path / "index.json"
    pair_path.write_bytes(canonical({**documents[1, 1], "signature": None}))
    with pytest.raises(build_release_pair_feed.ReleasePairFeedError, match="pair_signature_untrusted"):
        build_release_pair_feed.prepare_first_index(pair_path, payload["expires_at"], index_path, ())
    assert not index_path.exists()

    pair_path.write_bytes(canonical(documents[1, 1]))
    result = build_release_pair_feed.prepare_first_index(pair_path, payload["expires_at"], index_path, ())
    assert result["index_sequence"] == 1
    unsigned = json.loads(index_path.read_bytes())
    assert unsigned.pop("signature") is None
    verified = validate_product_release_index(_signed(unsigned), channel="stable", trust_verifier=_verify)
    assert verified["latest"] == ("app-product-1", "db-7.0.0-r1")


def test_prepare_app_binds_updater_to_the_same_reviewed_portable_build(tmp_path, releases):
    apps, databases, documents, _, _ = releases
    source = tmp_path / "portable"
    shutil.copytree(apps[1][0], source)
    bundled = source / "_internal" / "bundled"
    bundled.mkdir(parents=True)
    (bundled / "genshin.db").write_bytes(b"portable database marker")
    original_pair = documents[1, 1]["pair_manifest"]
    updater_dir = tmp_path / "updater-artifact"
    updater_dir.mkdir()
    updater = updater_dir / "GenshinCalcUpdater.exe"
    (source / PER_USER_UPDATER_RELATIVE).rename(updater)
    inventory = source / "PACKAGE-FILES.sha256"
    files = ["_internal/bundled/genshin.db", "app.bin"]
    inventory.write_bytes("".join(f"{_sha(source / filename)}  {filename}\n" for filename in files).encode())
    pair = release_profile.create_release_pair_manifest(
        databases[1][1], release_pair_id=original_pair["release_pair_id"], pair_sequence=original_pair["pair_sequence"],
        pair_policy="exact", app_release_id=original_pair["app"]["app_release_id"], app_version=version.APP_VERSION,
        app_artifact_manifest_sha256=_sha(inventory), released_at=original_pair["released_at"],
    )
    release_profile.write_release_pair_manifest(bundled / "release-pair-manifest.json", pair)
    receipt = {
        "contract": coordinator.UPDATER_ARTIFACT_CONTRACT,
        "schema_version": 1,
        "build_provenance": {"app": {"version": pair["app"]["app_version"], "release_id": pair["app"]["app_release_id"],
                                     "release_pair_id": pair["release_pair_id"]}},
        "updater": {"filename": updater.name, "helper_contract": helper.HELPER_RESULT_CONTRACT,
                    "request_schema_version": coordinator.UPDATER_REQUEST_SCHEMA_VERSION,
                    "sha256": _sha(updater), "size_bytes": updater.stat().st_size},
    }
    (updater_dir / "updater-artifact.json").write_bytes(canonical(receipt))
    script = Path(__file__).resolve().parents[1] / "scripts" / "build_release_pair_feed.py"
    command = [sys.executable, str(script), "prepare-app", "--source", str(source), "--updater", str(updater)]
    target = tmp_path / "prepared-app"
    result = subprocess.run([*command, "--output", str(target)], capture_output=True, check=False)
    assert result.returncode == 0, result.stderr.decode()
    assert verify_package_artifact(target)["status"] == "verified"
    assert _sha(target / PER_USER_UPDATER_RELATIVE) == _sha(updater)
    assert _sha(target / "app.bin") == _sha(source / "app.bin")
    assert not (target / "_internal" / "bundled").exists()
    updater.write_bytes(b"tampered bytes")
    rejected = tmp_path / "rejected-app"
    failed = subprocess.run([*command, "--output", str(rejected)], capture_output=True, check=False)
    assert failed.returncode == 1 and b"product_release_updater_build_mismatch" in failed.stderr
    assert not rejected.exists()


@pytest.mark.parametrize("first,second", [("app_only", "database_only"), ("database_only", "app_only")])
def test_product_upgrade_preserves_untouched_bytes_and_binds_offline_startup(
    tmp_path, monkeypatch, releases, first, second,
):
    apps, databases, documents, sources, payload = releases
    active = tmp_path / "installed-app"
    data = tmp_path / "installed-data"
    workspace = tmp_path / "work"
    shutil.copytree(apps[1][0], active)
    document = documents[1, 1]
    app_member = _install_facts(data, active, document["pair_manifest"], databases[1], document)
    import genshin_app.product_release_index as product

    monkeypatch.setattr(product, "_fetch_https", lambda *_args, **_kwargs: (200, canonical(_signed(payload)), None))
    downloads = []

    def download(artifact, destination, **_kwargs):
        source = sources[artifact["url"]]
        downloads.append(artifact["format"])
        shutil.copyfile(source, destination)
        assert _sha(destination) == artifact["sha256"]
        return {"verified": True}

    monkeypatch.setattr(transport, "_download_https", download)
    monkeypatch.setattr(helper, "sys", SimpleNamespace(frozen=True, executable=__file__))
    launched = []
    service = coordinator.ReleasePairCoordinator(
        data_dir=data, active_app_root=active, workspace=workspace, channel="stable",
        discovery_url="https://example.invalid/latest.json", trust_verifier=_verify,
        updater_locator=lambda: {"executable": tmp_path / "updater.exe"},
        launcher=lambda _exe, request: launched.append(request) or {"accepted": True}, restart_relative="app.bin",
    )
    for mode in (first, second):
        before_app = _sha(app_member)
        before_db = _sha(data / "genshin.db")
        downloads.clear()
        discovered = service.discover()
        selected = next(item for item in discovered["update_options"] if item["mode"] == mode)
        handoff = service.prepare_and_launch(discovered, confirmed_document_sha256=selected["confirmation_digest"],
                                             user_confirmed=True)
        assert helper.execute_external_pair_update(workspace, active, data, operation_id=handoff["operation_id"])["status"] == "complete"
        if mode == "app_only":
            assert downloads == ["app-zip"] and _sha(data / "genshin.db") == before_db
            assert _sha(app_member) != before_app
        else:
            assert downloads == ["sqlite-gzip"] and _sha(app_member) == before_app
            assert _sha(data / "genshin.db") != before_db

    assert len(launched) == 2
    monkeypatch.setattr(database_bootstrap, "DATABASE_RELEASE_CHANNEL", "stable")
    monkeypatch.setattr(database_bootstrap, "APP_RELEASE_ID", "app-product-2")
    gate = database_bootstrap.prepare_database_startup(data, resource_root=active / "_internal",
                                                        app_package_root=active, pair_policy="exact")
    assert gate["can_start"] and gate["release_pair_id"] == "pair-product-2-2"
    proof_path = data / LIVE_PAIR_PROOF_FILENAME
    original = json.loads(proof_path.read_bytes())
    altered = copy.deepcopy(original)
    altered["artifacts"]["app"]["url"] = "https://example.invalid/forged"
    proof_path.write_bytes(canonical(altered))
    with pytest.raises(database_bootstrap.DatabaseStartupError, match="product_release_live_pair_proof_invalid"):
        database_bootstrap.prepare_database_startup(data, resource_root=active / "_internal",
                                                    app_package_root=active, pair_policy="exact")


@pytest.fixture
def first_install(tmp_path, monkeypatch, releases):
    _, _, _, sources, payload = releases
    local = tmp_path / "current-user"
    local.mkdir()
    index = [canonical(_signed(payload))]
    monkeypatch.setattr(product_first_install, "_windows_is_elevated", lambda: False)
    monkeypatch.setattr(product_first_install, "known_folder_path", lambda _id: local)
    monkeypatch.setattr(product_first_install, "build_trust_verifier", lambda _keys: _verify)
    monkeypatch.setattr(version, "DATABASE_RELEASE_CHANNEL", "stable")
    monkeypatch.setattr(version, "DATABASE_RELEASE_MANIFEST_URL", "https://example.invalid/latest.json")
    monkeypatch.setattr(product_release_index, "_fetch_https", lambda *_a, **_kw: (200, index[0], None))
    monkeypatch.setattr(helper, "sys", SimpleNamespace(frozen=True, executable=__file__))
    downloads = []

    class Response(io.BytesIO):
        def __init__(self, url):
            body = sources[url].read_bytes()
            super().__init__(body)
            self.url = url
            self.headers = {"Content-Length": str(len(body))}

        def geturl(self):
            return self.url

    def open_artifact(url, **_kwargs):
        downloads.append(url)
        return Response(url)

    monkeypatch.setattr(transport, "_open_https", open_artifact)
    return product_first_install.build_first_install_coordinator(), local, index, downloads


def test_first_install_boots_signed_pair_offline_and_keeps_existing_profile(monkeypatch, releases, first_install):
    apps, databases, documents, _, _ = releases
    setup, local, _, downloads = first_install
    profile = local / version.APP_DIR_NAME
    preview = setup.preview()
    assert preview["status"] == "confirmation_required"
    assert preview["release_pair_id"] == "pair-product-2-2"
    assert preview["database_release_id"] == "db-7.0.0-r2"
    assert preview["install_directory"] == str(profile)
    assert all(value > 0 for value in preview["download_bytes"].values())
    assert not downloads and not profile.exists()
    receipt = setup.install(user_confirmed=True, confirmation_digest=preview["confirmation_digest"])
    assert receipt["status"] == "complete"
    assert downloads == [artifact["url"] for artifact in documents[2, 2]["artifacts"].values()]
    registration = json.loads((profile / product_first_install.REGISTRATION_FILENAME).read_bytes())
    assert registration["scope"] == "current_user"
    assert registration["operation_id"] == receipt["operation_id"]
    app = profile / registration["app_directory"]
    data = profile / registration["data_directory"]
    assert _sha(app / "app.bin") == _sha(apps[2][0] / "app.bin")
    assert _sha(data / "genshin.db") == _sha(databases[2][0])

    def offline(*_args, **_kwargs):
        raise AssertionError("startup and existing-profile refusal must be offline")

    monkeypatch.setattr(product_release_index, "_fetch_https", offline)
    monkeypatch.setattr(database_bootstrap, "DATABASE_RELEASE_CHANNEL", "stable")
    monkeypatch.setattr(database_bootstrap, "APP_RELEASE_ID", "app-product-2")
    gate = database_bootstrap.prepare_database_startup(data, resource_root=app / "_internal",
                                                       app_package_root=app, pair_policy="exact")
    assert gate["can_start"] and gate["release_pair_id"] == preview["release_pair_id"]
    with pytest.raises(product_first_install.FirstInstallError, match="existing_profile"):
        setup.install(user_confirmed=True, confirmation_digest=preview["confirmation_digest"])
    assert _sha(data / "genshin.db") == _sha(databases[2][0])


def test_offline_first_install_uses_same_signed_pair_transaction(monkeypatch, releases, first_install):
    _, databases, documents, sources, payload = releases
    installer, local, _, downloads = first_install
    index = local / "latest.json"
    index.write_bytes(canonical(_signed(payload)))
    document = documents[2, 2]
    assets = {kind: sources[artifact["url"]] for kind, artifact in document["artifacts"].items()}

    def offline(*_args, **_kwargs):
        raise AssertionError("local installation must not use network")

    monkeypatch.setattr(product_release_index, "_fetch_https", offline)
    monkeypatch.setattr(transport, "_open_https", offline)
    option = installer.preview(local_index=index)
    assert option["mode"] == "local_pair"
    invalid = local / "invalid.zip"
    app_archive = assets["app"].read_bytes()
    invalid.write_bytes(bytes([app_archive[0] ^ 1]) + app_archive[1:])
    with pytest.raises(transport.ReleasePairTransportError, match="sha256_mismatch"):
        installer.install(user_confirmed=True, confirmation_digest=option["confirmation_digest"],
                          local_index=index, local_artifacts={**assets, "app": invalid})
    assert not (local / version.APP_DIR_NAME).exists()
    receipt = installer.install(user_confirmed=True, confirmation_digest=option["confirmation_digest"],
                                local_index=index, local_artifacts=assets)
    data = local / version.APP_DIR_NAME / "data"
    assert receipt["status"] == "complete" and not downloads
    assert _sha(data / "genshin.db") == _sha(databases[2][0])


def test_app_only_and_later_signed_database_setup(monkeypatch, releases, first_install):
    from genshin_app.product_database_setup import DatabaseSetup

    apps, databases, documents, _, _ = releases
    installer, local, _, downloads = first_install
    profile = local / version.APP_DIR_NAME
    preview = installer.preview(app_only=True)
    assert preview["download_bytes"]["database"] == 0
    with pytest.raises(product_first_install.FirstInstallError, match="confirmation_mismatch"):
        installer.install(user_confirmed=True, confirmation_digest=preview["confirmation_digest"])
    receipt = installer.install(user_confirmed=True, confirmation_digest=preview["confirmation_digest"], app_only=True)
    app, data = profile / "app", profile / "data"
    assert receipt["database_state"] == "database_not_installed"
    assert downloads == [documents[2, 2]["artifacts"]["app"]["url"]]
    assert not (data / "genshin.db").exists()
    assert not (data / LIVE_PAIR_PROOF_FILENAME).exists()
    before = _sha(app / "app.bin")
    setup = DatabaseSetup(data, app, url=version.DATABASE_RELEASE_MANIFEST_URL,
                          trust_verifier=_verify, app_release_id="app-product-2", app_version=version.APP_VERSION)
    setup.validate_app_only()
    choice = setup.preview()
    installed = setup.install(choice["confirmation_digest"], user_confirmed=True)
    assert installed["status"] == "complete"
    assert not setup.available()
    assert downloads == [locator["url"] for locator in documents[2, 2]["artifacts"].values()]
    assert _sha(app / "app.bin") == before == _sha(apps[2][0] / "app.bin")
    assert _sha(data / "genshin.db") == _sha(databases[2][0])
    monkeypatch.setattr(database_bootstrap, "DATABASE_RELEASE_CHANNEL", "stable")
    monkeypatch.setattr(database_bootstrap, "APP_RELEASE_ID", "app-product-2")
    gate = database_bootstrap.prepare_database_startup(data, resource_root=app / "_internal",
                                                       app_package_root=app, pair_policy="exact")
    assert gate["can_start"] and gate["release_pair_id"] == "pair-product-2-2"


def test_local_database_setup_resumes_after_sidecar_publication_failure(monkeypatch, releases, first_install):
    from genshin_app import product_database_setup as database_setup
    from genshin_app.gui.api import GuiApi

    _, databases, documents, _, _ = releases
    installer, local, _, downloads = first_install
    preview = installer.preview(app_only=True)
    installer.install(user_confirmed=True, confirmation_digest=preview["confirmation_digest"], app_only=True)
    profile = local / version.APP_DIR_NAME
    app, data = profile / "app", profile / "data"
    document_path = local / "signed.json"
    document_path.write_bytes(canonical(documents[2, 2]))

    def offline(*_args, **_kwargs):
        raise URLError("offline")

    monkeypatch.setattr(product_release_index, "_fetch_https", offline)
    setup = database_setup.DatabaseSetup(data, app, url=version.DATABASE_RELEASE_MANIFEST_URL,
        trust_verifier=_verify, app_release_id="app-product-2", app_version=version.APP_VERSION)
    api = GuiApi(data, database_setup=setup, dsh_shell_factory=lambda _path: SimpleNamespace(dsh_client_boot=lambda: {"ready": True}))
    assert api.dsh_client_boot() == {"ready": True}
    assert api.dsh_client_domain_binding({"dsh_session_id": "session-fixture"}) == {
        "binding": None, "reason": "database_not_installed"}
    assert api.db_overview()["reason"] == "database_not_installed"
    assert api.whiteboard_new()["code"] == 0
    update_status = api.check_database_update()["data"]
    assert update_status["local_database_setup_available"] is True
    assert update_status["update_action_available"] is False
    choice = api._release_port.preview_local_database(databases[2][0], document_path)["data"]
    assert choice["local_package"] is True
    assert choice["database_released_at"] == documents[2, 2]["database_manifest"]["released_at"]
    assert choice["release_notes"]["database"].endswith("/gs_ai_calculator_db/releases")
    original = database_setup.atomic_json

    def interrupt_proof(path, value):
        if path.name == LIVE_PAIR_PROOF_FILENAME:
            raise OSError("fixture: interrupted after database swap")
        original(path, value)

    with monkeypatch.context() as patch:
        patch.setattr(database_setup, "atomic_json", interrupt_proof)
        assert api.update_database(choice["confirmation_digest"], True)["code"] == 1
    assert (data / database_setup.PENDING_SETUP_FILENAME).is_file()
    assert _sha(data / "genshin.db") == _sha(databases[2][0])
    retry = api._release_port.preview_local_database(databases[2][0], document_path)["data"]
    assert api.update_database(retry["confirmation_digest"], True)["data"]["status"] == "complete"
    assert downloads == [documents[2, 2]["artifacts"]["app"]["url"]]
    assert not setup.available()


def test_default_uninstall_removes_only_registered_app_and_keeps_data(monkeypatch, first_install):
    from genshin_app import database_legacy_processes

    installer, local, _, _ = first_install
    preview = installer.preview()
    installer.install(user_confirmed=True, confirmation_digest=preview["confirmation_digest"])
    profile = local / version.APP_DIR_NAME
    user_file = profile / "data" / "plans" / "saved.json"
    user_file.parent.mkdir(exist_ok=True)
    user_file.write_text("keep", encoding="utf-8")
    digest = _sha(profile / "data" / "genshin.db")
    selection = installer.uninstall_preview()
    with pytest.raises(product_first_install.FirstInstallError, match="cancelled"):
        installer.uninstall(selection["confirmation_digest"], user_confirmed=True, cancel_check=lambda: True)
    assert (profile / "app").exists()
    with pytest.raises(product_first_install.FirstInstallError, match="confirmation_mismatch"):
        installer.uninstall("wrong", user_confirmed=True)
    with monkeypatch.context() as patch:
        patch.setattr(database_legacy_processes, "packaged_peer_processes", lambda: [{"name": "GenshinCalcGUI.exe"}])
        with pytest.raises(product_first_install.FirstInstallError, match="instance_running"):
            installer.uninstall(selection["confirmation_digest"], user_confirmed=True)
    assert installer.uninstall(selection["confirmation_digest"], user_confirmed=True)["status"] == "uninstalled"
    assert not (profile / "app").exists()
    assert user_file.read_text(encoding="utf-8") == "keep"
    assert _sha(profile / "data" / "genshin.db") == digest


def test_reinstall_uses_retained_signed_pair_and_preserves_database_and_user_files(monkeypatch, first_install):
    from genshin_app import database_legacy_processes

    installer, local, _, downloads = first_install
    monkeypatch.setattr(database_legacy_processes, "packaged_peer_processes", list)
    choice = installer.preview()
    installer.install(user_confirmed=True, confirmation_digest=choice["confirmation_digest"])
    profile = local / version.APP_DIR_NAME
    data = profile / "data"
    user_file = data / "notes.json"
    user_file.write_bytes(b'{"keep":true}')
    original_db = _sha(data / "genshin.db")
    selected = installer.uninstall_preview()
    phases = []
    installer.uninstall(selected["confirmation_digest"], user_confirmed=True, progress=phases.append)
    assert phases == [{"phase": "committing"}]
    assert not (profile / "app").exists()

    def no_index(*_args, **_kwargs):
        raise AssertionError("the retained signed pair determines reinstall")

    monkeypatch.setattr(product_release_index, "_fetch_https", no_index)
    downloads.clear()
    reinstall = installer.preview()
    assert reinstall["mode"] == "reinstall" and reinstall["download_bytes"]["database"] == 0
    assert reinstall["database_release_id"] == choice["database_release_id"]
    phases.clear()
    assert installer.install(user_confirmed=True, confirmation_digest=reinstall["confirmation_digest"],
                             progress=phases.append)["status"] == "complete"
    assert phases[-1] == {"phase": "committing"}
    assert len(downloads) == 1 and downloads[0].endswith(".zip")
    assert _sha(data / "genshin.db") == original_db
    assert user_file.read_bytes() == b'{"keep":true}'
    monkeypatch.setattr(database_bootstrap, "DATABASE_RELEASE_CHANNEL", "stable")
    monkeypatch.setattr(database_bootstrap, "APP_RELEASE_ID", "app-product-2")
    gate = database_bootstrap.prepare_database_startup(data, resource_root=profile / "app" / "_internal",
                                                       app_package_root=profile / "app", pair_policy="exact")
    assert gate["can_start"]


def test_current_user_shortcut_registers_exact_bytes_and_never_removes_user_replacement(monkeypatch, first_install):
    from genshin_app import shortcuts

    installer, local, _, _ = first_install
    menu = local / "Programs"
    menu.mkdir()
    installer._programs_folder = lambda: menu
    app = local / version.APP_DIR_NAME / "app"
    app.mkdir(parents=True)
    executable = app / "GenshinCalcGUI.exe"
    executable.touch()
    calls = []
    monkeypatch.setattr(shortcuts, "_windows_link", lambda spec: calls.append(spec) or b"fixture-link")
    registration = {"app_directory": "app", "data_directory": "data"}
    link = menu / product_first_install._START_MENU_SHORTCUT
    assert installer._write_shortcut(registration) == "created"
    assert calls[0]["target"] == str(executable)
    registered = json.loads((app.parent / product_first_install.REGISTRATION_FILENAME).read_bytes())
    assert registered["start_menu_shortcut_sha256"] == _sha(link)
    assert installer._remove_shortcut(registered) == "removed" and not link.exists()
    link.write_bytes(b"user-owned replacement")
    assert installer._write_shortcut(registration) == "preserved_existing"
    assert installer._remove_shortcut(registered) == "preserved_modified"
    assert link.read_bytes() == b"user-owned replacement"


def test_paired_portable_offers_the_transition_before_database_only(tmp_path, monkeypatch, releases):
    apps, databases, documents, sources, payload = releases
    active = tmp_path / "portable"
    data = tmp_path / "installed-data"
    work = tmp_path / "work"
    shutil.copytree(apps[1][0], active)
    bundled = active / "_internal" / "bundled"
    bundled.mkdir(parents=True)
    manifest = databases[1][1]
    bundled_manifest = bundled / "database-release-manifest.json"
    release_profile.write_database_release_manifest(bundled_manifest, manifest)
    inventory = active / "PACKAGE-FILES.sha256"
    inventory.write_bytes(f"{_sha(bundled_manifest)}  _internal/bundled/database-release-manifest.json\n".encode()
                          + inventory.read_bytes())
    original = documents[1, 1]["pair_manifest"]
    pair = release_profile.create_release_pair_manifest(
        manifest, release_pair_id=original["release_pair_id"], pair_sequence=original["pair_sequence"],
        pair_policy="exact", app_release_id=original["app"]["app_release_id"], app_version=version.APP_VERSION,
        app_artifact_manifest_sha256=_sha(inventory), released_at=original["released_at"],
    )
    release_profile.write_release_pair_manifest(bundled / "release-pair-manifest.json", pair)
    _install_facts(data, active, pair, databases[1], documents[1, 1])
    (data / LIVE_PAIR_PROOF_FILENAME).unlink()
    monkeypatch.setattr(product_release_index, "_fetch_https", lambda *_a, **_kw: (200, canonical(_signed(payload)), None))
    monkeypatch.setattr(transport, "_download_https", lambda artifact, destination, **_kw:
                        shutil.copyfile(sources[artifact["url"]], destination) and {"verified": True})
    monkeypatch.setattr(helper, "sys", SimpleNamespace(frozen=True, executable=__file__))
    service = coordinator.ReleasePairCoordinator(
        data_dir=data, active_app_root=active, workspace=work, channel="stable",
        discovery_url="https://example.invalid/latest.json", trust_verifier=_verify,
        updater_locator=lambda: {"executable": tmp_path / "updater.exe"},
        launcher=lambda *_args: {"accepted": True}, restart_relative="app.bin",
    )
    discovered = service.discover()
    assert [option["mode"] for option in discovered["update_options"]] == ["app_only", "both"]
    option = discovered["update_options"][0]
    handoff = service.prepare_and_launch(discovered, confirmed_document_sha256=option["confirmation_digest"],
                                         user_confirmed=True)
    assert helper.execute_external_pair_update(work, active, data, operation_id=handoff["operation_id"])["status"] == "complete"
    assert _sha(data / "genshin.db") == _sha(databases[1][0])
    assert not bundled.exists()
    assert [option["mode"] for option in service.discover()["update_options"]] == ["database_only"]


def test_current_user_install_cold_boot_and_repeat_independent_updates(monkeypatch, releases, first_install):
    apps, databases, documents, _, payload = releases
    setup, local, index, downloads = first_install
    initial = {**payload, "latest_app_release_id": "app-product-1", "latest_database_release_id": "db-7.0.0-r1",
               "apps": payload["apps"][:1], "pairs": [documents[1, 1]], "index_sequence": payload["index_sequence"] - 1}
    index[0] = canonical(_signed(initial))
    preview = setup.preview()
    setup.install(user_confirmed=True, confirmation_digest=preview["confirmation_digest"])
    profile = local / version.APP_DIR_NAME
    app, data, workspace = profile / "app", profile / "data", profile / "update-workspace"
    executable = app / "app.bin"
    calls = []

    def launch(argv, **options):
        calls.append((argv, options))
        assert not options["shell"] and argv[1] == "--current-user"
        return SimpleNamespace(pid=123)

    monkeypatch.setattr(paths, "is_frozen", lambda: True)
    monkeypatch.setattr(coordinator, "known_folder_path", lambda _id: local)
    monkeypatch.setattr(coordinator, "is_windows_elevated", lambda: False)
    monkeypatch.setattr(coordinator, "build_trust_verifier", lambda _keys: _verify)
    monkeypatch.setattr(coordinator, "sys", SimpleNamespace(executable=str(executable)))
    monkeypatch.setattr(coordinator.subprocess, "Popen", launch)
    monkeypatch.setattr(helper, "known_folder_path", lambda _id: local)
    monkeypatch.setattr(helper, "is_windows_elevated", lambda: False)
    monkeypatch.setattr(helper, "_wait_for_parent_exit", lambda *_args: None)
    monkeypatch.setattr(helper, "_launch_replacement", lambda *_args: 321)
    monkeypatch.setattr(database_bootstrap, "is_frozen", lambda: True)
    monkeypatch.setattr(database_bootstrap, "resource_dir", lambda: app / "_internal")
    monkeypatch.setattr(database_bootstrap, "sys", SimpleNamespace(executable=str(executable)))
    monkeypatch.setattr(database_bootstrap, "DATABASE_RELEASE_CHANNEL", "stable")
    monkeypatch.setattr(database_bootstrap, "DATABASE_PAIR_POLICY", "exact")
    monkeypatch.setattr(native_identity, "is_frozen", lambda: True)
    monkeypatch.setattr(native_identity, "resource_dir", lambda: app / "_internal")
    monkeypatch.setattr(native_identity, "DATABASE_RELEASE_CHANNEL", "stable")

    def cold_boot(app_generation, database_generation):
        release = f"app-product-{app_generation}"
        monkeypatch.setattr(database_bootstrap, "APP_RELEASE_ID", release)
        monkeypatch.setattr(native_identity, "APP_RELEASE_ID", release)
        gate = database_bootstrap.prepare_database_startup(data)
        assert gate["can_start"] and gate["release_pair_id"] == f"pair-product-{app_generation}-{database_generation}"
        assert native_identity.active_database_release_id(data) == f"db-7.0.0-r{database_generation}"
        assert _sha(executable) == _sha(apps[app_generation][0] / "app.bin")
        assert _sha(data / "genshin.db") == _sha(databases[database_generation][0])
        return native_identity.release_pair_identity_hash(data)

    identities = [cold_boot(1, 1)]
    index[0] = canonical(_signed(payload))
    service = coordinator.build_default_release_pair_coordinator(data)
    assert service.availability()["available"]
    for mode, expected in (("app_only", (2, 1)), ("database_only", (2, 2))):
        discovered = service.discover()
        option = next(item for item in discovered["update_options"] if item["mode"] == mode)
        downloads.clear()
        prepared = service.prepare_and_launch(discovered, confirmed_document_sha256=option["confirmation_digest"],
                                              user_confirmed=True)
        assert prepared["launcher"]["method"] == "windows_current_user"
        argv, _ = calls[-1]
        installed_helper = Path(argv[0])
        assert installed_helper.parent == workspace and installed_helper.read_bytes() == (app / PER_USER_UPDATER_RELATIVE).read_bytes()
        monkeypatch.setattr(helper, "sys", SimpleNamespace(frozen=True, executable=str(installed_helper), stderr=sys.stderr))
        if mode == "database_only":
            def fail_postcheck(*_a, **_kw):
                raise update.ReleasePairUpdateError("fixture_update_postcheck_failed")

            with monkeypatch.context() as fault:
                fault.setattr(update, "postcheck_pair_update", fail_postcheck)
                assert helper.main(argv[1:]) == 1
            assert update.read_pair_update_journal(workspace)["state"] == "cleanup_complete"
            assert cold_boot(2, 1) == identities[-1]
            assert downloads == [documents[expected]["artifacts"]["database"]["url"]]
            downloads.clear()
            service.prepare_and_launch(discovered, confirmed_document_sha256=option["confirmation_digest"],
                                       user_confirmed=True)
            argv, _ = calls[-1]
        assert helper.main(argv[1:]) == 0
        identities.append(cold_boot(*expected))
        key = "app" if mode == "app_only" else "database"
        assert downloads == ([documents[expected]["artifacts"][key]["url"]] if mode == "app_only" else [])
    assert len(set(identities)) == 3
    assert service.discover()["update_options"] == []
    (app / PER_USER_UPDATER_RELATIVE).write_bytes(b"changed helper")
    assert not service.availability()["available"]
    assert len(calls) == 3


def test_first_install_requires_fresh_consent_and_signed_metadata(first_install, releases):
    setup, local, index, downloads = first_install
    *_, payload = releases
    preview = setup.preview()
    with pytest.raises(product_first_install.FirstInstallError, match="confirmation_required"):
        setup.install(user_confirmed=False, confirmation_digest=preview["confirmation_digest"])
    index[0] = canonical(_signed({**payload, "index_sequence": payload["index_sequence"] + 1}))
    with pytest.raises(product_first_install.FirstInstallError, match="confirmation_mismatch"):
        setup.install(user_confirmed=True, confirmation_digest=preview["confirmation_digest"])
    forged = json.loads(index[0])
    forged["latest_app_release_id"] = "forged-app"
    index[0] = canonical(forged)
    with pytest.raises(ReleasePairDiscoveryError, match="signature_untrusted"):
        setup.preview()
    assert not downloads and not (local / version.APP_DIR_NAME).exists()


def test_first_install_rejects_other_account_elevation(monkeypatch):
    monkeypatch.setattr(product_first_install, "_windows_is_elevated", lambda: True)
    with pytest.raises(product_first_install.FirstInstallError, match="elevation_unsupported"):
        product_first_install.build_first_install_coordinator()


def test_first_install_recovers_interrupted_private_work_and_obeys_install_lock(first_install):
    setup, local, _, downloads = first_install
    preview = setup.preview()
    options = {"user_confirmed": True, "confirmation_digest": preview["confirmation_digest"]}
    cache = local / f".{version.APP_DIR_NAME}-install-cache"
    interrupted = cache / "pending-install"
    interrupted.mkdir()
    (interrupted / "partial").write_bytes(b"old incomplete download")
    with (
        file_lock(cache / "first-install.lock", timeout=0),
        ThreadPoolExecutor(1) as executor,
        pytest.raises(product_first_install.FirstInstallError, match="first_install_busy"),
    ):
        executor.submit(setup.install, **options).result(timeout=10)
    assert interrupted.exists() and not downloads
    assert setup.install(**options)["status"] == "complete"
    assert not interrupted.exists()


@pytest.mark.parametrize("fault", ["discovery_offline", "download_offline", "cancel", "postcheck", "destination_race"])
def test_first_install_failure_is_retryable_without_overwriting(monkeypatch, first_install, fault):
    setup, local, _, _ = first_install
    profile = local / version.APP_DIR_NAME
    options = {"user_confirmed": True, "confirmation_digest": setup.preview()["confirmation_digest"]}

    def disconnected(*_a, **_kw):
        raise URLError("offline fixture")

    def fail_postcheck(*_a, **_kw):
        raise update.ReleasePairUpdateError("fixture_postcheck_failed")

    def create_competing_profile(*_a, **_kw):
        profile.mkdir()
        (profile / "preexisting.txt").write_text("keep", encoding="utf-8")

    with monkeypatch.context() as change:
        if fault == "discovery_offline":
            change.setattr(product_release_index, "_fetch_https", disconnected)
            expected = (ReleasePairDiscoveryError, "network_unavailable")
        elif fault == "download_offline":
            change.setattr(transport, "_open_https", disconnected)
            expected = (transport.ReleasePairTransportError, "download_failed")
        elif fault == "cancel":
            checks = iter([False, False, False, True])
            options["cancel_check"] = lambda: next(checks, True)
            expected = (transport.ReleasePairTransportError, "cancelled")
        elif fault == "postcheck":
            change.setattr(update, "postcheck_pair_update", fail_postcheck)
            expected = (helper.ReleasePairHelperError, "first_install_recovery_required")
        else:
            change.setattr(product_first_install, "atomic_json", create_competing_profile)
            expected = (product_first_install.FirstInstallError, "existing_profile")
        with pytest.raises(expected[0], match=expected[1]):
            setup.install(**options)

    assert not (local / f".{version.APP_DIR_NAME}-install-cache" / "pending-install").exists()
    assert not (profile / "app").exists()
    assert not (profile / "data").exists()
    if fault == "destination_race":
        assert (profile / "preexisting.txt").read_text(encoding="utf-8") == "keep"
    else:
        assert not profile.exists()
        options.pop("cancel_check", None)
        assert setup.install(**options)["status"] == "complete"
