"""Prepare isolated QA signing inputs; never accept or read a production private key.

No command builds or launches an EXE, writes Windows registration, or publishes.
Generated trust is exclusively for a disposable QA source/build directory.
"""
from __future__ import annotations

import argparse
import ast
import base64
import hashlib
import json
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

from build_release_pair_feed import prepare_next_index
from export_release_public_key import export_public_key

from genshin_app.installation_location import checked_path
from genshin_app.product_release_index import canonical, validate_product_release_index
from genshin_app.release_pair_feed import (
    finalize_release_pair_feed,
    prepare_release_pair_feed,
)
from genshin_app.release_pair_trust import ALGORITHM, build_trust_verifier
from genshin_db.release_profile import (
    create_release_pair_manifest,
    write_release_pair_manifest,
)

CONTRACT = "genshin-installation-qa-signing-v1"
MARKER = "QA_ONLY.json"
PRIVATE_NAME = "qa-private.pem"


def _write(path, value):
    with checked_path(path).open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def _root(path):
    root = checked_path(path)
    row = json.loads(checked_path(root / MARKER).read_text("utf-8"))
    if row.get("contract") != CONTRACT or row.get("production_authority") is not False:
        raise ValueError("qa_root_marker_invalid")
    keys = row["trust_keys"]
    if row["test_key_id"] not in {key["key_id"] for key in keys}:
        raise ValueError("qa_root_key_invalid")
    build_trust_verifier(tuple(keys))
    return root, row


def _inside(root, path):
    path = checked_path(path)
    if path == root or not path.is_relative_to(root):
        raise ValueError("qa_output_must_stay_inside_qa_root")
    return path


def initialize(root, openssl, production_public_key=None):
    root = checked_path(root)
    if root.exists():
        raise ValueError("qa_root_must_be_new")
    root.mkdir(parents=True)
    # Only this freshly created path can hold a signer; the CLI has no arbitrary
    # --private-key parameter and never prints PEM or opens a user signer.
    subprocess.run([openssl, "genpkey", "-algorithm", "RSA", "-pkeyopt", "rsa_keygen_bits:3072",
                    "-out", str(root / PRIVATE_NAME)], check=True, capture_output=True, timeout=60)
    subprocess.run([openssl, "pkey", "-in", str(root / PRIVATE_NAME), "-pubout", "-out", str(root / "qa-public.pem")],
                   check=True, capture_output=True, timeout=20)
    key = export_public_key(root / "qa-public.pem", openssl=openssl)
    keys = [key]
    if production_public_key:
        # Public verification record only. It permits the original r20 signed
        # predecessor to remain unchanged inside an isolated successor index.
        public = json.loads(checked_path(production_public_key).read_text("utf-8"))
        build_trust_verifier((public,))
        keys.append(public)
    row = {"contract": CONTRACT, "production_authority": False, "test_key_id": key["key_id"],
           "trust_keys": keys, "created_at": datetime.now(UTC).isoformat(),
           "scope": "isolated_qa_builds_only", "network_allowed": False}
    _write(root / MARKER, row)
    _write(root / "qa-public-key.json", key)
    return {"status": "prepared", "qa_root": str(root), "test_key_id": key["key_id"],
            "production_authority": False}


def _sign(root, row, payload, openssl, stem):
    source, signature = _inside(root, root / f"{stem}.canonical.json"), _inside(root, root / f"{stem}.sig.bin")
    if source.exists() or signature.exists():
        raise ValueError("qa_signature_output_exists")
    source.write_bytes(canonical(payload))
    key = checked_path(root / PRIVATE_NAME)
    if key.parent != root or not key.is_file():
        raise ValueError("qa_signer_missing")
    subprocess.run([openssl, "dgst", "-sha256", "-sign", str(key), "-sigopt", "rsa_padding_mode:pss",
                    "-sigopt", "rsa_pss_saltlen:32", "-sigopt", "rsa_mgf1_md:sha256", "-out", str(signature),
                    str(source)], check=True, capture_output=True, timeout=30)
    result = {"algorithm": ALGORITHM, "key_id": row["test_key_id"],
              "value": base64.urlsafe_b64encode(signature.read_bytes()).decode("ascii").rstrip("=")}
    if not build_trust_verifier(tuple(row["trust_keys"]))(payload, result):
        raise ValueError("qa_signature_does_not_match_test_key")
    return result


def version_overlay(root, source, database_manifest, predecessor_pair, app_release_id, pair_id, pair_sequence):
    root, row = _root(root)
    if "-qa" not in app_release_id or "-qa" not in pair_id or pair_sequence < 1:
        raise ValueError("qa_identity_required")
    source, database_manifest, predecessor_pair = map(checked_path, (source, database_manifest, predecessor_pair))
    manifest = json.loads(database_manifest.read_text("utf-8"))
    if manifest["channel"] != "stable":
        raise ValueError("qa_database_must_use_accepted_stable_manifest")
    replacements = {"APP_RELEASE_ID": app_release_id, "RELEASE_PAIR_ID": pair_id,
                    "RELEASE_PAIR_SEQUENCE": pair_sequence, "DATABASE_PAIR_POLICY": "exact",
                    "DATABASE_RELEASE_ID": manifest["database_release_id"],
                    "DATABASE_RELEASE_SEQUENCE": manifest["release_sequence"], "DATABASE_RELEASE_CHANNEL": "stable",
                    "DATABASE_RELEASE_MANIFEST_URL": "https://qa.invalid/latest.json",
                    "DATABASE_RELEASE_TRUST_KEYS": tuple(row["trust_keys"]), "APP_DIR_NAME": "GenshinCalcQA",
                    "ACCEPTED_PREDECESSOR_PAIR_MANIFEST_SHA256": hashlib.sha256(predecessor_pair.read_bytes()).hexdigest()}
    text = source.read_text("utf-8-sig")
    lines, edits = text.splitlines(keepends=True), []
    for statement in ast.parse(text).body:
        targets = statement.targets if isinstance(statement, ast.Assign) else [statement.target] if isinstance(statement, ast.AnnAssign) else []
        for target in targets:
            if isinstance(target, ast.Name) and target.id in replacements:
                edits.append((statement.lineno - 1, statement.end_lineno, f"{target.id} = {replacements[target.id]!r}\n"))
    if len(edits) != len(replacements):
        raise ValueError("qa_version_source_assignments_changed")
    for start, end, replacement in sorted(edits, reverse=True):
        lines[start:end] = [replacement]
    output = root / "qa-version.py"
    with output.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write("# QA ONLY: never publish this test trust configuration.\n" + "".join(lines)
                     + f"\nINSTALLATION_QA_RUN_ID = {hashlib.sha256(row['test_key_id'].encode()).hexdigest()[:32]!r}\n")
    _write(root / "version-overlay.json", {"production_authority": False, "source": str(source),
            "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(), "output": str(output),
            "output_sha256": hashlib.sha256(output.read_bytes()).hexdigest(), "fields": sorted(replacements)})
    return {"status": "prepared", "version_overlay": str(output), "production_authority": False}


def sign_feed(root, feed, previous_index, openssl):
    root, row = _root(root)
    feed = _inside(root, feed)
    unsigned_path = checked_path(feed / "latest.unsigned.json")
    document = json.loads(unsigned_path.read_text("utf-8"))
    if "-qa" not in document["pair_manifest"]["app"]["app_release_id"]:
        raise ValueError("qa_identity_required")
    if not document["artifacts"]["app"]["url"].startswith("https://qa.invalid/"):
        raise ValueError("qa_app_url_must_be_nonproduction")
    payload = {key: value for key, value in document.items() if key != "signature"}
    if (feed / "release-pair-signing-payload.canonical.json").read_bytes() != canonical(payload):
        raise ValueError("qa_feed_payload_mismatch")
    signature = _sign(root, row, payload, openssl, "pair")
    signature_path = root / "pair-signature.json"
    _write(signature_path, signature)
    finalize_release_pair_feed(feed_directory=feed, signature_path=signature_path, trust_keys=tuple(row["trust_keys"]))
    # Preserve real predecessor signatures. Only the new QA pair and outer QA
    # index use the isolated signer; no altered document claims production trust.
    now = datetime.now(UTC)
    unsigned_index = root / "index.unsigned.json"
    prepare_next_index(checked_path(previous_index), [feed / "latest.json"], now.isoformat(),
                       (now + timedelta(days=7)).isoformat(), unsigned_index, tuple(row["trust_keys"]))
    index = json.loads(unsigned_index.read_text("utf-8"))
    payload = {key: value for key, value in index.items() if key != "signature"}
    index = {**payload, "signature": _sign(root, row, payload, openssl, "index")}
    validated = validate_product_release_index(index, channel="stable", trust_verifier=build_trust_verifier(tuple(row["trust_keys"])))
    output = root / "qa-index.signed.json"
    _write(output, index)
    receipt = {"contract": "genshin-installation-qa-fixture-v1", "production_authority": False,
               "execution_layer": "fixture_preparation_only", "latest": list(validated["latest"]),
               "index": str(output), "index_sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
               "app_archive": str(feed / document["artifacts"]["app"]["url"].split("/")[-1]),
               "artifact_facts": document["artifacts"], "test_key_id": row["test_key_id"]}
    _write(root / "fixture.json", receipt)
    return receipt


def prepare_feed(root, app_root, database, database_manifest, previous_index, database_archive):
    """Package a QA App while retaining the accepted database bytes and signature."""
    root, row = _root(root)
    paths = [checked_path(path) for path in (app_root, database, database_manifest, previous_index, database_archive)]
    app_root, database, database_manifest, previous_index, database_archive = paths
    verifier = build_trust_verifier(tuple(row["trust_keys"]))
    previous = validate_product_release_index(json.loads(previous_index.read_text("utf-8")),
                                             channel="stable", trust_verifier=verifier)
    old = previous["pairs"][previous["combinations"][previous["latest"]]]
    manifest = json.loads(database_manifest.read_text("utf-8"))
    if manifest != old["database_manifest"]:
        raise ValueError("qa_fixture_requires_unchanged_accepted_database")
    constants = {}
    for node in ast.parse((root / "qa-version.py").read_text("utf-8")).body:
        if (isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name)
                and node.targets[0].id in {"APP_RELEASE_ID", "APP_VERSION", "RELEASE_PAIR_ID", "RELEASE_PAIR_SEQUENCE",
                                         "ACCEPTED_PREDECESSOR_PAIR_MANIFEST_SHA256"}):
            constants[node.targets[0].id] = ast.literal_eval(node.value)
    if "-qa" not in constants["APP_RELEASE_ID"] or "-qa" not in constants["RELEASE_PAIR_ID"]:
        raise ValueError("qa_identity_required")
    inventory = checked_path(app_root / "PACKAGE-FILES.sha256")
    pair = create_release_pair_manifest(manifest, release_pair_id=constants["RELEASE_PAIR_ID"],
        pair_sequence=constants["RELEASE_PAIR_SEQUENCE"], pair_policy="exact",
        app_release_id=constants["APP_RELEASE_ID"], app_version=constants["APP_VERSION"],
        app_artifact_manifest_sha256=hashlib.sha256(inventory.read_bytes()).hexdigest(),
        released_at=datetime.now(UTC).isoformat())
    pair_path, old_path = root / "qa-pair.json", root / "predecessor-signed-pair.json"
    if pair_path.exists() or old_path.exists():
        raise ValueError("qa_feed_preparation_output_exists")
    write_release_pair_manifest(pair_path, pair)
    _write(old_path, old)
    receipt = prepare_release_pair_feed(app_root=app_root, database=database, pair_manifest_path=pair_path,
        database_manifest_path=database_manifest, base_url="https://qa.invalid/app", output_directory=root / "feed",
        independent_app=True, accepted_database_source_pair=old_path, accepted_database_archive=database_archive,
        trust_keys=tuple(row["trust_keys"]))
    return {"status": "prepared", "production_authority": False, "feed": str(root / "feed"),
            "app_release_id": constants["APP_RELEASE_ID"], "artifacts": receipt["artifacts"]}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init")
    init.add_argument("--root", type=Path, required=True)
    init.add_argument("--openssl", required=True)
    init.add_argument("--production-public-key", type=Path)
    overlay = commands.add_parser("version-overlay")
    for name in ("root", "source", "database-manifest", "predecessor-pair"):
        overlay.add_argument("--" + name, type=Path, required=True)
    for name in ("app-release-id", "pair-id"):
        overlay.add_argument("--" + name, required=True)
    overlay.add_argument("--pair-sequence", type=int, required=True)
    sign = commands.add_parser("sign-feed")
    for name in ("root", "feed", "previous-index"):
        sign.add_argument("--" + name, type=Path, required=True)
    sign.add_argument("--openssl", required=True)
    prepare = commands.add_parser("prepare-feed")
    for name in ("root", "app-root", "database", "database-manifest", "previous-index", "database-archive"):
        prepare.add_argument("--" + name, type=Path, required=True)
    args = vars(parser.parse_args(argv))
    command = args.pop("command")
    try:
        result = {"init": initialize, "version-overlay": version_overlay, "prepare-feed": prepare_feed,
                  "sign-feed": sign_feed}[command](**args)
    except (OSError, ValueError, KeyError, RuntimeError, subprocess.SubprocessError) as error:
        # subprocess errors can include a signer path; report only stable type.
        print(json.dumps({"status": "failed", "error_type": type(error).__name__,
                          "production_authority": False}), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
