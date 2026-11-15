# PyInstaller 打包规格（G5）：one-dir，捆绑前端、数据库和原生Agent闭包。
# 正式入口：py -3.12 scripts/build_dist.py --native-node <exact-node.exe>
import hashlib
import json
import PyInstaller.utils.hooks as hooks
import os
import runpy
import sys
from pathlib import Path

project_root = Path(SPECPATH).resolve()
for source_path in (project_root, project_root / "src"):
    if str(source_path) not in sys.path:
        sys.path.insert(0, str(source_path))
from genshin_db.release_profile import validate_database_release_manifest
from scripts.native_agent_release import validate_staged_native_agent_runtime
from scripts.app_runtime_release import app_runtime_module_closure
from scripts.packaging_windows_runtime import omit_windows_system_crt
from genshin_engine.runtime_dependencies import CONTRACT as ENGINE_DEPENDENCY_CONTRACT
from genshin_engine.runtime_dependencies import FROZEN_MANIFEST, engine_runtime_module_closure
engine_runtime_modules = sorted(engine_runtime_module_closure(project_root / "src"))
app_runtime_modules = sorted(app_runtime_module_closure(project_root / "src"))
engine_dependency_manifest = project_root / "build" / FROZEN_MANIFEST
engine_dependency_manifest.parent.mkdir(parents=True, exist_ok=True)
engine_dependency_manifest.write_text(json.dumps({"contract": ENGINE_DEPENDENCY_CONTRACT,
                                                 "modules": engine_runtime_modules}, sort_keys=True) + "\n", encoding="utf-8")
bundled_database = Path(os.environ.get("GENSHIN_BUNDLED_DATABASE", str(project_root / "data/genshin.db"))).resolve()
if project_root not in bundled_database.parents or bundled_database.name != "genshin.db" or not bundled_database.is_file():
    raise ValueError("预置数据库必须是当前工作区内已存在的 genshin.db 文件")
for cleaned in ((Path(DISTPATH) / "GenshinCalc").resolve(), Path(workpath).resolve()):
    if cleaned == bundled_database or cleaned in bundled_database.parents:
        raise ValueError("预置数据库不能位于本次构建会清理的输出或工作目录")
database_manifest_value = os.environ.get("GENSHIN_DATABASE_RELEASE_MANIFEST", "")
if not database_manifest_value:
    raise ValueError("必须通过build_dist.py提供数据库发行清单")
database_manifest_path = Path(database_manifest_value).resolve()
allowed_manifest_root = (project_root / "build-py").resolve()
if (
    database_manifest_path.name != "database-release-manifest.json"
    or allowed_manifest_root not in database_manifest_path.parents
    or not database_manifest_path.is_file()
):
    raise ValueError("数据库发行清单必须位于受控构建目录")
database_manifest = validate_database_release_manifest(json.loads(database_manifest_path.read_text(encoding="utf-8")))
with bundled_database.open("rb") as database_handle:
    database_sha256 = hashlib.file_digest(database_handle, "sha256").hexdigest()
if (
    database_manifest.get("database", {}).get("sha256") != database_sha256
    or database_manifest.get("database", {}).get("size_bytes") != bundled_database.stat().st_size
):
    raise ValueError("数据库发行清单与预置数据库不一致")
pair_manifest_value = os.environ.get("GENSHIN_RELEASE_PAIR_MANIFEST", "")
if not pair_manifest_value:
    raise ValueError("必须通过build_dist.py提供App/数据库版本对清单")
pair_manifest_path = Path(pair_manifest_value).resolve()
if (
    pair_manifest_path.name != "release-pair-manifest.json"
    or allowed_manifest_root not in pair_manifest_path.parents
    or not pair_manifest_path.is_file()
):
    raise ValueError("版本对清单必须位于受控构建目录")
pair_manifest = json.loads(pair_manifest_path.read_text(encoding="utf-8"))
# A database release manifest is immutable and can retain the App/pair that
# first published it.  Later App versions bind that accepted release through
# their own pair manifest, whose database-manifest hash and release-core hash
# are verified below; never require rewriting the accepted database manifest.
database_manifest_sha256 = hashlib.sha256(
    json.dumps(
        database_manifest,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
).hexdigest()
database_pair = pair_manifest.get("database", {})
if (
    pair_manifest.get("contract") != "genshin-release-pair-manifest-v2"
    or pair_manifest.get("schema_version") != 2
    or database_pair.get("database_release_id")
    != database_manifest.get("database_release_id")
    or database_pair.get("manifest_sha256") != database_manifest_sha256
    or database_pair.get("release_core_sha256")
    != database_manifest.get("database", {}).get("release_core", {}).get("root_sha256")
):
    raise ValueError("App/数据库版本对清单不精确或与数据库发行清单不一致")

native_stage_value = os.environ.get("GENSHIN_NATIVE_AGENT_STAGE", "")
if not native_stage_value:
    raise ValueError("必须通过build_dist.py提供已验证的原生Agent staging")
native_stage = Path(native_stage_value).resolve()
allowed_stage_root = (project_root / "build" / "native-agent-v2").resolve()
if (
    native_stage.is_symlink()
    or not native_stage.is_dir()
    or allowed_stage_root not in native_stage.parents
):
    raise ValueError("原生Agent staging必须位于受控构建目录")
native_runtime = native_stage / "agent-runtime"
native_release = native_stage / "native-agent-release"
native_assets = native_stage / "agent-client-assets"
native_manifest = native_runtime / "dsh-client-shell-runtime-manifest.json"
native_node = native_runtime / "node.exe"
native_dependencies = native_runtime / "node_modules"
native_identity_file = native_release / "release-identity.json"
native_plan_file = native_release / "native-agent-build-plan.json"
if (
    not native_manifest.is_file()
    or not native_node.is_file()
    or not native_dependencies.is_dir()
    or not native_assets.is_dir()
    or not native_identity_file.is_file()
    or not native_plan_file.is_file()
):
    raise ValueError("原生Agent staging不完整")
version_values = runpy.run_path(str(project_root / "src/genshin_app/version.py"))
pair_app = pair_manifest.get("app", {})
if (
    pair_app.get("app_version") != version_values["APP_VERSION"]
    or pair_app.get("app_release_id") != version_values["APP_RELEASE_ID"]
    or pair_app.get("required_database_release_id")
    != database_manifest.get("database_release_id")
    or pair_app.get("logic_profile") != version_values["DATABASE_LOGIC_PROFILE"]
    or pair_app.get("data_contract_version")
    != version_values["DATABASE_DATA_CONTRACT_VERSION"]
    or pair_manifest.get("release_pair_id") != version_values["RELEASE_PAIR_ID"]
    or pair_manifest.get("pair_sequence") != version_values["RELEASE_PAIR_SEQUENCE"]
    or pair_manifest.get("pair_policy") != version_values["DATABASE_PAIR_POLICY"]
    or pair_manifest.get("channel") != version_values["DATABASE_RELEASE_CHANNEL"]
    or database_manifest.get("database_release_id")
    != version_values["DATABASE_RELEASE_ID"]
    or database_manifest.get("release_sequence")
    != version_values["DATABASE_RELEASE_SEQUENCE"]
    or database_manifest.get("channel")
    != version_values["DATABASE_RELEASE_CHANNEL"]
):
    raise ValueError("版本对清单中的App合同与当前构建不一致")
# Shared with build_dist and the real production GUI factory: authenticate
# runtime + frontend roots, exact reviewed cohort and the full physical graph.
# There is no Phase 5B fallback or independent, stale spec-only verifier.
validate_staged_native_agent_runtime(native_stage)

webview_datas, webview_bins, webview_hidden = hooks.collect_all("webview")

a = Analysis(
    ["app_entry.py"],
    pathex=[],
    binaries=webview_bins,
    datas=[
        ("src/genshin_app/gui/web", "genshin_app/gui/web"),
        ("skill", "skill"),
        ("examples", "examples"),
        (str(bundled_database), "bundled"),  # 候选可先打包验收；正式库随后发布
        (str(database_manifest_path), "bundled"),
        (str(pair_manifest_path), "bundled"),
        ("src/genshin_db/display_alias_reference.json", "genshin_db"),
        ("src/genshin_db/resources/mechanics", "genshin_db/resources/mechanics"),
        ("src/genshin_db/resources/mechanisms", "genshin_db/resources/mechanisms"),
        ("src/genshin_runtime/resources/*.json", "genshin_runtime/resources"),
        (str(engine_dependency_manifest), "genshin_engine"),
        (str(native_runtime), "agent-runtime"),
        (str(native_release), "native-agent-release"),
        (str(native_assets), "agent-client-assets"),
        *webview_datas,
    ],
    hiddenimports=[
        "webview.platforms.winforms", "clr_loader",
        # Public compatibility projection/ACK API required in both frozen
        # entrypoints. The default DSH owner has no static import edge to it;
        # preserve importability without reviving an old execution path.
        "genshin_app.agent_chat.native_app_tool_bridge",
        *webview_hidden, *engine_runtime_modules, *app_runtime_modules,
    ],
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
a.binaries = omit_windows_system_crt(a.binaries)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="GenshinCalc",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,  # 保留控制台：Agent/Skill 通道依赖 exe 的 stdout JSON
    icon=None,
)
gui_exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="GenshinCalcGUI",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    icon=None,
)
coll = COLLECT(
    exe,
    gui_exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="GenshinCalc",
)
