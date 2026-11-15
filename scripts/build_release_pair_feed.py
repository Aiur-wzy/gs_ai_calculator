"""Build or finalize an offline App/database release-pair feed."""

from __future__ import annotations

import argparse
import base64
import html
import json
import os
import shutil
import sys
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from release_supply_chain import SupplyChainError, write_package_file_manifest

if __package__:
    from .product_site_publication import (
        OFFLINE_PAIR_CATALOG,
        OFFLINE_PAIR_DIRECTORY,
        offline_pair_catalog,
        site_downloads,
        validate_setup_locator,
    )
else:
    from product_site_publication import (
        OFFLINE_PAIR_CATALOG,
        OFFLINE_PAIR_DIRECTORY,
        offline_pair_catalog,
        site_downloads,
        validate_setup_locator,
    )

from genshin_app import version
from genshin_app.package_integrity import (
    PackageIntegrityError,
    _read_package_manifest,
    verify_package_artifact,
)
from genshin_app.product_release_index import (
    INDEX_CONTRACT,
    PER_USER_UPDATER_RELATIVE,
    _check_index_progress,
    _stamp,
    canonical,
    validate_product_release_index,
)
from genshin_app.release_pair_coordinator import (
    ReleasePairCoordinatorError,
    _read_updater_receipt,
    _sha256,
)
from genshin_app.release_pair_discovery import (
    ReleasePairDiscoveryError,
    validate_latest_document,
)
from genshin_app.release_pair_feed import (
    ReleasePairFeedError,
    _read_json,
    _safe_members,
    finalize_release_pair_feed,
    prepare_release_pair_feed,
)
from genshin_app.release_pair_trust import ALGORITHM, build_trust_verifier
from genshin_app.release_update_projection import PRODUCT_RELEASE_NOTES
from genshin_db.release_profile import (
    create_release_pair_manifest,
    load_database_release_manifest,
    load_release_pair_manifest,
    write_release_pair_manifest,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="生成待外部签名或已经验签的App/数据库联合更新feed",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    prepare = commands.add_parser("prepare", help="生成制品和canonical签名载荷")
    prepare.add_argument("--app-root", type=Path, required=True)
    prepare.add_argument("--database", type=Path, required=True)
    prepare.add_argument("--pair-manifest", type=Path, required=True)
    prepare.add_argument("--database-manifest", type=Path, required=True)
    prepare.add_argument("--base-url", required=True)
    prepare.add_argument("--database-base-url")
    prepare.add_argument("--independent-app", action="store_true")
    prepare.add_argument("--accepted-database-source-pair", type=Path,
                         help="原数据库压缩包对应的已签名配对，沿用原下载地址")
    prepare.add_argument("--accepted-database-archive", type=Path,
                         help="原已发布 SQLite gzip，复用原始字节")
    prepare.add_argument("--output", type=Path, required=True)
    finalize = commands.add_parser("finalize", help="验证外部签名并生成latest.json")
    finalize.add_argument("--feed", type=Path, required=True)
    finalize.add_argument("--signature", type=Path, required=True)
    initial = commands.add_parser("index-first", help="从已验签配对生成首个正式索引的待签文件")
    initial.add_argument("--pair-document", type=Path, required=True)
    initial.add_argument("--expires-at", required=True)
    initial.add_argument("--output", type=Path, required=True)
    following = commands.add_parser("index-next", help="从已签索引和新增已签配对生成下一版待签索引")
    following.add_argument("--previous-index", type=Path, required=True)
    changes = following.add_mutually_exclusive_group(required=True)
    changes.add_argument("--pair-document", type=Path, action="append", help="新增已签配对，可重复指定")
    changes.add_argument("--renew", action="store_true", help="仅延长索引有效期，保留现有版本和配对")
    following.add_argument("--released-at", required=True)
    following.add_argument("--expires-at", required=True)
    following.add_argument("--output", type=Path, required=True)
    app = commands.add_parser("prepare-app", help="从已核验构建生成无数据库的独立 App 工件目录")
    app.add_argument("--source", type=Path, required=True)
    app.add_argument("--updater", type=Path, required=True)
    app.add_argument("--output", type=Path, required=True)
    pair = commands.add_parser("prepare-pair", help="为独立 App 工件与 DB 清单建立精确配对")
    pair.add_argument("--app-root", type=Path, required=True)
    pair.add_argument("--app-release-id", required=True)
    pair.add_argument("--app-version", required=True)
    pair.add_argument("--database-manifest", type=Path, required=True)
    pair.add_argument("--release-pair-id", required=True)
    pair.add_argument("--sequence", type=int, required=True)
    pair.add_argument("--released-at", required=True)
    pair.add_argument("--output", type=Path, required=True)
    index = commands.add_parser("index-payload", help="生成供外部签名的索引字节")
    index.add_argument("--index", type=Path, required=True)
    index.add_argument("--output", type=Path, required=True)
    site = commands.add_parser("index-finalize", help="验签后生成静态 Pages 目录")
    site.add_argument("--index", type=Path, required=True)
    site.add_argument("--signature", type=Path, required=True)
    site.add_argument("--output", type=Path, required=True)
    site.add_argument("--setup-asset", type=Path, help="安装器 ZIP 的 HTTPS 地址、字节数和 SHA-256 JSON")
    signature = commands.add_parser("signature-json", help="将离线签名原始字节转为 Base64URL JSON")
    signature.add_argument("--raw", required=True, type=Path)
    signature.add_argument("--key-id", required=True)
    signature.add_argument("--output", required=True, type=Path)
    return parser


def prepare_independent_app(source: Path, output: Path, *, updater: Path) -> dict:
    source, output = source.resolve(strict=True), output.resolve()
    if output.exists() or source in output.parents:
        raise ReleasePairFeedError("release_pair_feed_output_exists")
    before = verify_package_artifact(source)
    _, _, source_inventory = _read_package_manifest(source)
    pair = load_release_pair_manifest(source / "_internal" / "bundled" / "release-pair-manifest.json")
    updater_receipt = _read_updater_receipt(updater.with_name("updater-artifact.json"))
    updater_provenance = updater_receipt["build_provenance"].get("app")
    updater_identity = updater_receipt["updater"]
    if (before["manifest_sha256"] != pair["app"]["artifact_manifest"]["sha256"]
            or updater.is_symlink() or not updater.is_file() or updater.name != updater_identity["filename"]
            or PER_USER_UPDATER_RELATIVE in source_inventory
            or updater.stat().st_size != updater_identity["size_bytes"]
            or _sha256(updater) != updater_identity["sha256"]
            or updater_provenance != {"version": pair["app"]["app_version"],
                                      "release_id": pair["app"]["app_release_id"],
                                      "release_pair_id": pair["release_pair_id"]}):
        raise ReleasePairFeedError("product_release_updater_build_mismatch")
    with TemporaryDirectory(dir=output.parent, prefix=".product-app-") as staging:
        target = Path(staging) / "app"
        target.mkdir()
        for relative, original in _safe_members(source):
            if relative == "PACKAGE-FILES.sha256" or relative.startswith("_internal/bundled/"):
                continue
            destination = target / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(original, destination)
        bundled_updater = target / PER_USER_UPDATER_RELATIVE
        bundled_updater.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(updater, bundled_updater)
        receipt = write_package_file_manifest(target)
        _, _, target_inventory = _read_package_manifest(target)
        expected_inventory = {name: sha for name, sha in source_inventory.items()
                              if not name.startswith("_internal/bundled/")}
        expected_inventory[PER_USER_UPDATER_RELATIVE] = updater_identity["sha256"]
        if target_inventory != expected_inventory:
            raise ReleasePairFeedError("release_pair_feed_app_copy_mismatch")
        if verify_package_artifact(source) != before:
            raise ReleasePairFeedError("release_pair_feed_source_changed")
        verify_package_artifact(target)
        os.replace(target, output)
    return receipt


def _index_payload(path: Path) -> dict:
    source = _read_json(path, "product_release_index_invalid")
    if "signature" not in source or source.pop("signature") is not None:
        raise ReleasePairFeedError("product_release_unsigned_index_expected")
    return source


def prepare_first_index(pair_document_path: Path, expires_at: str, output: Path, trust_keys) -> dict:
    source = _read_json(pair_document_path, "product_release_index_pair_invalid")
    document, trusted = validate_latest_document(
        source, expected_channel="stable", trust_verifier=build_trust_verifier(trust_keys),
        allow_unsigned_dev=False,
    )
    if not trusted:
        raise ReleasePairFeedError("product_release_pair_signature_untrusted")
    pair = document["pair_manifest"]
    expiry = datetime.fromisoformat(expires_at)
    if (pair["pair_sequence"] != 1 or document["artifacts"]["app"]["format"] != "app-zip"
            or expiry.utcoffset() is None or expiry <= datetime.fromisoformat(pair["released_at"])):
        raise ReleasePairFeedError("product_release_first_index_inputs_invalid")
    app_id = pair["app"]["app_release_id"]
    payload = {
        "contract": INDEX_CONTRACT, "schema_version": 1, "channel": "stable", "index_sequence": 1,
        "released_at": pair["released_at"], "expires_at": expires_at,
        "latest_app_release_id": app_id,
        "latest_database_release_id": document["database_manifest"]["database_release_id"],
        "apps": [{"app_release_id": app_id, "release_sequence": 1, "released_at": pair["released_at"]}],
        "pairs": [document], "signature": None,
    }
    with output.open("xb") as stream:
        stream.write(canonical(payload))
    return {"status": "prepared", "index_sequence": 1, "release_pair_id": pair["release_pair_id"]}


def _qualify_unsigned_index(payload, *, previous, trust_keys, trust_verifier, released):
    # The local outer envelope is still unsigned. Reserve enough bytes for any
    # pinned signing key and send every real signature to the pinned verifier.
    signatures = [previous["signed_index"]["signature"]]
    for key in trust_keys:
        size = (int(key["modulus_hex"], 16).bit_length() + 7) // 8
        signatures.append({"algorithm": ALGORITHM, "key_id": key["key_id"],
                           "value": base64.urlsafe_b64encode(bytes(size)).decode("ascii")})
    placeholder = dict(max(signatures, key=lambda signature: len(canonical(signature))))

    def verify_candidate(data, signature):
        if signature is placeholder:
            return data == payload
        return trust_verifier(data, signature)

    index = validate_product_release_index({**payload, "signature": placeholder}, channel="stable",
                                           trust_verifier=verify_candidate, now=released)
    _check_index_progress(index, previous)
    return index


def prepare_next_index(previous_path: Path, pair_paths: Sequence[Path], released_at: str, expires_at: str,
                       output: Path, trust_keys, *, renew: bool = False) -> dict:
    """Append explicitly verified compatible pairs, or renew only the publication envelope."""
    if bool(pair_paths) == renew:
        raise ReleasePairFeedError("product_release_next_index_inputs_invalid")
    verifier = build_trust_verifier(trust_keys)
    past = _read_json(previous_path, "product_release_index_invalid")
    previous = validate_product_release_index(past, channel="stable", trust_verifier=verifier, for_install=False)
    released, expires = _stamp(released_at), _stamp(expires_at)
    if (released <= _stamp(past["released_at"]) or expires <= released
            or (renew and expires <= _stamp(past["expires_at"]))):
        raise ReleasePairFeedError("product_release_next_index_time_invalid")

    app_sequences = dict(previous["app_sequences"])
    database_sequences = dict(previous["database_sequences"])
    app_sequence = max(app_sequences.values())
    latest_db_sequence = max(database_sequences.values())
    latest_pair_sequence = max(entry["pair_manifest"]["pair_sequence"] for entry in previous["pairs"].values())
    apps = list(past["apps"])
    pairs = list(past["pairs"])
    additions = []
    for path in pair_paths:
        original = _read_json(path, "product_release_index_pair_invalid")
        document, trusted = validate_latest_document(original, expected_channel="stable", trust_verifier=verifier,
                                                      allow_unsigned_dev=False)
        if not trusted:
            raise ReleasePairFeedError("product_release_pair_signature_untrusted")
        pair, database = document["pair_manifest"], document["database_manifest"]
        if (pair["pair_policy"] != "exact" or document["artifacts"]["app"]["format"] != "app-zip"
                or _stamp(pair["released_at"]) > released or _stamp(database["released_at"]) > released):
            raise ReleasePairFeedError("product_release_next_index_pair_invalid")
        if pair["pair_sequence"] <= latest_pair_sequence:
            raise ReleasePairFeedError("product_release_next_index_pair_sequence_invalid")
        if (database["database_release_id"] not in previous["database_sequences"]
                and database["release_sequence"] <= latest_db_sequence):
            raise ReleasePairFeedError("product_release_next_index_database_sequence_invalid")
        additions.append((pair["pair_sequence"], original, pair, database))

    for _sequence, original, pair, database in sorted(additions, key=lambda row: row[0]):
        app_id, db_id = pair["app"]["app_release_id"], database["database_release_id"]
        if app_id not in app_sequences:
            app_sequence += 1
            app_sequences[app_id] = app_sequence
            apps.append({"app_release_id": app_id, "release_sequence": app_sequence, "released_at": pair["released_at"]})
        database_sequences[db_id] = database["release_sequence"]
        pairs.append(original)

    payload = {key: value for key, value in past.items() if key != "signature"}
    payload.update(index_sequence=past["index_sequence"] + 1, released_at=released_at, expires_at=expires_at,
                   latest_app_release_id=max(app_sequences, key=app_sequences.get),
                   latest_database_release_id=max(database_sequences, key=database_sequences.get),
                   apps=apps, pairs=pairs)
    index = _qualify_unsigned_index(payload, previous=previous, trust_keys=trust_keys, trust_verifier=verifier,
                                    released=released)
    with output.open("xb") as stream:
        stream.write(canonical({**payload, "signature": None}))
    return {"status": "prepared", "index_sequence": payload["index_sequence"],
            "release_pair_id": index["combinations"][index["latest"]], "added_pairs": len(additions), "renewed": renew}


def finalize_product_site(index_path: Path, signature_path: Path, output: Path, trust_keys, *,
                          setup_asset_path: Path | None = None) -> dict:
    output = output.resolve()
    if output.exists():
        raise ReleasePairFeedError("release_pair_feed_output_exists")
    payload = _index_payload(index_path)
    signature = _read_json(signature_path, "product_release_index_signature_invalid", maximum=64 * 1024)
    signed = {**payload, "signature": signature}
    validated = validate_product_release_index(signed, channel="stable", trust_verifier=build_trust_verifier(trust_keys))
    latest = validated["pairs"][validated["combinations"][validated["latest"]]]
    artifacts = latest["artifacts"]
    application, database = validated["latest"]
    app_version = latest["pair_manifest"]["app"]["app_version"]
    data_version = latest["database_manifest"]["database"]["game_version"]
    links = "".join(f'<li><a href="{html.escape(artifacts[kind]["url"], quote=True)}">{label}</a> '
                    f'{html.escape(release_id)}（{artifacts[kind]["size_bytes"] / 1024**2:.1f} MiB）'
                    f'<details><summary>SHA-256</summary><code>{artifacts[kind]["sha256"]}</code></details></li>'
                    for kind, label, release_id in (("app", "免安装程序", application), ("database", "数据库包", database)))
    setup = None
    if setup_asset_path is not None:
        try:
            setup = validate_setup_locator(_read_json(setup_asset_path, "product_release_setup_asset_invalid",
                                                      maximum=64 * 1024))
        except ValueError as error:
            raise ReleasePairFeedError("product_release_setup_asset_invalid") from error
        links = (f'<li><a href="{html.escape(setup["url"], quote=True)}">Windows 小安装器</a> '
                 f'（{setup["size_bytes"] / 1024**2:.1f} MiB）'
                 f'<details><summary>SHA-256</summary><code>{setup["sha256"]}</code></details></li>') + links
    site_downloads(validated, latest, setup)
    catalog = offline_pair_catalog(validated)
    catalog_links = []
    for row in catalog["pairs"]:
        selected = validated["pairs"][row["release_pair_id"]]["artifacts"]
        app_url, database_url = (html.escape(selected[kind]["url"], quote=True) for kind in ("app", "database"))
        label = html.escape(f'{row["app_release_id"]} / {row["database_release_id"]}')
        current = "（当前推荐）" if row["release_pair_id"] == catalog["current_pair_id"] else ""
        catalog_links.append(f'<li>{label}{current} · <a href="{row["document"]}">签名补装清单</a> · '
                             f'<a href="{app_url}">程序包</a> · <a href="{database_url}">数据库包</a></li>')
    archive = ('<section><h2>兼容组合与离线补装</h2><p>按已安装的程序版本选择签名补装清单。'
               f'<a href="{OFFLINE_PAIR_CATALOG}">查看组合清单</a>。</p>'
               f'<ul>{"".join(catalog_links)}</ul></section>')
    with TemporaryDirectory(dir=output.parent, prefix=".product-site-") as staging:
        target = Path(staging) / "site"
        target.mkdir()
        (target / "latest.json").write_bytes(canonical(signed))
        (target / "database-install-pair.json").write_bytes(canonical(latest))
        (target / OFFLINE_PAIR_CATALOG).write_bytes(canonical(catalog))
        (target / OFFLINE_PAIR_DIRECTORY).mkdir()
        for row in catalog["pairs"]:
            (target / row["document"]).write_bytes(canonical(validated["pairs"][row["release_pair_id"]]))
        (target / ".nojekyll").touch()
        (target / "index.html").write_text(
            '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" '
            'content="width=device-width,initial-scale=1"><title>原神拉表下载</title><main>'
            f'<h1>原神拉表</h1><p>当前兼容组合为应用 {html.escape(app_version)} '
            f'和游戏数据 {html.escape(data_version)}。</p><ul>{links}</ul>'
            f'<p><a href="{PRODUCT_RELEASE_NOTES["app"]}">安装器及应用发行说明</a> · '
            f'<a href="{PRODUCT_RELEASE_NOTES["database"]}">数据库发行说明</a></p>'
            '<p>Windows 工件未做代码签名。本页提供 SHA-256 校验值。</p>'
            '<p>建议由 GPT-6.1 Sol 或能力更强的模型通过 CLI 查询、编排和计算。GUI 适合看图、审核和寻找问题。'
            '应用内原生 Agent 与 DeepSeek 的复杂任务表现仍不稳定。</p>'
            '<p>免安装版解压后直接打开。可以在应用内联网下载数据库，也可以选择本页的数据库包和'
            '<a href="database-install-pair.json">本地补装签名清单</a>。'
            '使用安装器作整套离线安装时，请同时下载应用包、数据库包和<a href="latest.json">签名更新索引</a>。</p>'
            f'{archive}'
            '<p>本项目为非官方工具，仅供学习参考。角色、武器和套装资料可能尚未正式上线。'
            '基础游戏数据整理自 <a href="https://gitlab.com/GuraFoundation/YuanShenResources">YuanShenResources</a>。'
            '使用相关资料前请核对其状态。</p></main></html>',
            encoding="utf-8",
        )
        os.replace(target, output)
    return {"status": "finalized", "index_sha256": validated["index_sha256"],
            "offline_pair_documents": len(catalog["pairs"])}


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "prepare":
            result = prepare_release_pair_feed(
                app_root=args.app_root,
                database=args.database,
                pair_manifest_path=args.pair_manifest,
                database_manifest_path=args.database_manifest,
                base_url=args.base_url,
                output_directory=args.output,
                independent_app=args.independent_app,
                database_base_url=args.database_base_url,
                accepted_database_source_pair=args.accepted_database_source_pair,
                accepted_database_archive=args.accepted_database_archive,
                trust_keys=version.DATABASE_RELEASE_TRUST_KEYS,
            )
        elif args.command == "finalize":
            result = finalize_release_pair_feed(
                feed_directory=args.feed,
                signature_path=args.signature,
                trust_keys=version.DATABASE_RELEASE_TRUST_KEYS,
            )
        elif args.command == "index-first":
            result = prepare_first_index(args.pair_document, args.expires_at, args.output, version.DATABASE_RELEASE_TRUST_KEYS)
        elif args.command == "index-next":
            result = prepare_next_index(args.previous_index, args.pair_document or (), args.released_at,
                                        args.expires_at, args.output, version.DATABASE_RELEASE_TRUST_KEYS, renew=args.renew)
        elif args.command == "prepare-app":
            result = prepare_independent_app(args.source, args.output, updater=args.updater)
        elif args.command == "prepare-pair":
            if args.output.exists() or (args.app_root / "_internal" / "bundled").exists():
                raise ReleasePairFeedError("release_pair_feed_input_invalid")
            manifest = load_database_release_manifest(args.database_manifest)
            result = create_release_pair_manifest(manifest, release_pair_id=args.release_pair_id,
                pair_sequence=args.sequence, pair_policy="exact" if manifest["channel"] == "stable" else "current_only",
                app_release_id=args.app_release_id, app_version=args.app_version,
                app_artifact_manifest_sha256=verify_package_artifact(args.app_root)["manifest_sha256"],
                released_at=args.released_at)
            write_release_pair_manifest(args.output, result)
        elif args.command == "index-payload":
            raw = canonical(_index_payload(args.index))
            with args.output.open("xb") as stream:
                stream.write(raw)
            result = {"status": "prepared", "bytes": len(raw)}
        elif args.command == "signature-json":
            if not 256 <= args.raw.stat().st_size <= 2048 or not 1 <= len(args.key_id) <= 256:
                raise ReleasePairFeedError("release_pair_feed_signature_invalid")
            encoded = base64.urlsafe_b64encode(args.raw.read_bytes()).rstrip(b"=").decode("ascii")
            with args.output.open("xb") as stream:
                stream.write(canonical({"algorithm": ALGORITHM, "key_id": args.key_id, "value": encoded}))
            result = {"status": "prepared", "key_id": args.key_id}
        else:
            result = finalize_product_site(args.index, args.signature, args.output, version.DATABASE_RELEASE_TRUST_KEYS,
                                           setup_asset_path=args.setup_asset)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0
    except (ReleasePairFeedError, ReleasePairDiscoveryError, ReleasePairCoordinatorError,
            PackageIntegrityError, SupplyChainError, OSError, TypeError, ValueError) as error:
        print(
            json.dumps(
                {"status": "failed", "reason": getattr(error, "reason", getattr(error, "code", type(error).__name__))},
                ensure_ascii=False,
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
