"""Qualify declared Python engine dependencies in both real EXE archives.

Archive inspection only; never launches an executable. The frozen execution
qualification is a separate layer and cannot be inferred from this result.
"""
from __future__ import annotations

import hashlib
import subprocess
import sys
from pathlib import Path
from types import CodeType

ROOT = Path(__file__).resolve().parents[1]
# Direct build_dist.py imports this helper with only scripts/ on sys.path.
# Its qualified scripts.* imports require the same fixed repository root.
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from genshin_engine.runtime_dependencies import (
    CONTRACT,
    FROZEN_MANIFEST,
    engine_runtime_dependency_descriptor,
    engine_runtime_module_closure,
    module_source,
)
from scripts.app_runtime_release import (
    NON_PORTABLE_RELEASE_ENTRIES,
    app_runtime_module_closure,
)


class EngineRuntimePackageError(ValueError):
    def __init__(self, reason, **details):
        self.reason, self.details = reason, details
        super().__init__(reason)


def _constant(value):
    if isinstance(value, CodeType):
        return _code_contract(value)
    if isinstance(value, tuple):
        return tuple(_constant(item) for item in value)
    if isinstance(value, frozenset):
        return ("frozenset", tuple(sorted((_constant(item) for item in value), key=repr)))
    return value


def _code_contract(code):
    """Ignore paths/line tables only; retain executable constants and ABI."""
    return (code.co_code, tuple(_constant(item) for item in code.co_consts),
            code.co_names, code.co_varnames, code.co_freevars, code.co_cellvars,
            code.co_argcount, code.co_posonlyargcount, code.co_kwonlyargcount,
            code.co_flags, code.co_exceptiontable, code.co_stacksize)


def _digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def changed_project_modules(source_root: Path) -> set[str]:
    """Include working/staged and newly added source modules, if in a checkout."""
    project = source_root.parent
    try:
        changed = subprocess.check_output(
            ["git", "diff", "HEAD", "--name-only", "--", "src"], cwd=project,
            text=True, stderr=subprocess.PIPE).splitlines()
        changed += subprocess.check_output(
            ["git", "ls-files", "--others", "--exclude-standard", "--", "src"],
            cwd=project, text=True, stderr=subprocess.PIPE).splitlines()
    except (OSError, subprocess.CalledProcessError):
        return set()
    modules = set()
    for name in changed:
        path = Path(name)
        if path.suffix == ".py" and path.parts[0] == "src" and (project / path).is_file():
            parts = list(path.with_suffix("").parts[1:])
            if parts[-1] == "__init__":
                parts.pop()
            modules.add(".".join(parts))
    return modules


def validate_engine_package_archive(package_root: Path, *, source_root: Path | None = None) -> dict:
    """Both PYZs must ship the closure and match actual source semantics.

    Inspect every shipped project module plus the dependency closure and
    changed source set. This prevents replacing the older changed-code check
    with a narrower dependency-only check.
    """
    from PyInstaller.archive.readers import CArchiveReader

    source_root = (source_root or ROOT / "src").resolve()
    descriptor = engine_runtime_dependency_descriptor()
    closure = engine_runtime_module_closure(source_root)
    app_closure = app_runtime_module_closure(source_root)
    # A real import dependency always wins over the explicit release-only
    # ownership list. Keep verifying any of these modules if an App ships it.
    release_only = NON_PORTABLE_RELEASE_ENTRIES - set(app_closure) - set(closure)
    changed = changed_project_modules(source_root) - release_only
    records = []
    for filename in ("GenshinCalc.exe", "GenshinCalcGUI.exe"):
        path = package_root / filename
        if not path.is_file():
            raise EngineRuntimePackageError("engine_package_entry_missing", entry=filename)
        outer = CArchiveReader(str(path))
        archives = [name for name, entry in outer.toc.items() if entry[-1] == "z"]
        if len(archives) != 1:
            raise EngineRuntimePackageError("engine_package_pyz_invalid", entry=filename)
        pyz = outer.open_embedded_archive(archives[0])
        inventory = set(pyz.toc)
        missing = sorted(set(closure) - inventory)
        if missing:
            raise EngineRuntimePackageError("engine_dynamic_dependency_missing", entry=filename, modules=missing)
        missing_app = sorted(set(app_closure) - inventory)
        if missing_app:
            raise EngineRuntimePackageError("app_runtime_dependency_missing", entry=filename, modules=missing_app)
        missing_changed = sorted(changed - inventory)
        if missing_changed:
            raise EngineRuntimePackageError("engine_changed_source_missing", entry=filename, modules=missing_changed)
        project_modules = {name for name in inventory if name.startswith(
            ("genshin_engine", "genshin_app", "genshin_db", "genshin_runtime"))}
        for name in sorted(project_modules | set(closure) | set(app_closure) | changed):
            source = module_source(source_root, name)
            if source is None:
                raise EngineRuntimePackageError("engine_package_stale_module", entry=filename, module=name)
            actual = pyz.extract(name)
            expected = compile(source.read_text(encoding="utf-8-sig"), str(source), "exec", dont_inherit=True)
            if not isinstance(actual, CodeType) or _code_contract(actual) != _code_contract(expected):
                raise EngineRuntimePackageError("engine_package_source_mismatch", entry=filename, module=name)
        records.append({"entry": filename, "sha256": _digest(path),
                        "declared_closure_modules": len(closure),
                        "declared_app_closure_modules": len(app_closure),
                        "semantic_source_modules": len(project_modules | set(closure) | set(app_closure) | changed)})
    import json
    manifest_path = package_root / "_internal" / "genshin_engine" / FROZEN_MANIFEST
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise EngineRuntimePackageError("engine_dependency_manifest_unavailable") from error
    if manifest != {"contract": CONTRACT, "modules": sorted(closure)}:
        raise EngineRuntimePackageError("engine_dependency_manifest_mismatch")
    data_records = []
    for relative in descriptor["package_data"]:
        source, shipped = source_root / relative, package_root / "_internal" / relative
        if not shipped.is_file() or _digest(shipped) != _digest(source):
            raise EngineRuntimePackageError("engine_package_data_mismatch", resource=relative)
        data_records.append({"path": relative, "sha256": _digest(shipped)})
    for extension in descriptor["native_extensions"]:
        if extension["platform"] == sys.platform:
            suffix = ".pyd" if sys.platform == "win32" else ".so"
            if not any((package_root / "_internal").rglob(extension["module"] + "*" + suffix)):
                raise EngineRuntimePackageError("engine_package_native_extension_missing", module=extension["module"])
    return {"contract": "genshin-engine-package-archive-qualification-v1",
            "layer": "archive_inspection", "descriptor_contract": descriptor["contract"],
            "families": sorted(descriptor["families"]), "entries": records,
            "package_data": data_records, "execution_verified": False}
