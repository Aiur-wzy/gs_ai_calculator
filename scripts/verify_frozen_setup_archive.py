"""Verify a frozen Setup or updater archive against its build source without executing it."""
from __future__ import annotations

import argparse
import hashlib
import json
import marshal
from pathlib import Path
from types import CodeType

if __package__:
    from .engine_runtime_release import _code_contract
else:
    from engine_runtime_release import _code_contract

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = "genshin_runtime/resources/profiles.json"
COMMON_MODULES = ("genshin_db.release_migration", "genshin_runtime.normalization")
ENTRY_MODULES = {
    "GenshinCalcSetup.exe": ("genshin_app.product_first_install", "genshin_app.setup_frozen_qualification"),
    "GenshinCalcUpdater.exe": ("genshin_app.release_pair_helper",),
}
ENTRY_SCRIPTS = {"GenshinCalcSetup.exe": "installer_entry", "GenshinCalcUpdater.exe": "updater_entry"}


def verify_archive(executable: Path, project_root: Path = ROOT) -> dict:
    from PyInstaller.archive.readers import CArchiveReader

    path = Path(executable).resolve(strict=True)
    expected_modules = ENTRY_MODULES.get(path.name)
    if expected_modules is None or not path.is_file():
        raise ValueError("setup_archive_executable_invalid")
    archive = CArchiveReader(str(path))
    entries = [name for name in archive.toc if name.replace("\\", "/") == REGISTRY]
    if len(entries) != 1:
        raise ValueError(f"setup_archive_normalization_registry_missing:{path.name}")
    project = Path(project_root).resolve(strict=True)
    source = project / "src"
    bundled_registry = archive.extract(entries[0])
    if bundled_registry != (source / REGISTRY).read_bytes():
        raise ValueError(f"setup_archive_normalization_registry_mismatch:{path.name}")
    python_archives = [name for name, row in archive.toc.items() if row[-1] == "z"]
    if len(python_archives) != 1:
        raise ValueError(f"setup_archive_python_inventory_invalid:{path.name}")
    python = archive.open_embedded_archive(python_archives[0])
    modules = sorted(set(COMMON_MODULES + expected_modules))

    def require_current_source(packaged, original, entry):
        expected = compile(original.read_text(encoding="utf-8-sig"), str(original), "exec", dont_inherit=True)
        if not isinstance(packaged, CodeType) or _code_contract(packaged) != _code_contract(expected):
            raise ValueError(f"setup_archive_module_mismatch:{path.name}:{entry}")

    for module in modules:
        if module not in python.toc:
            raise ValueError(f"setup_archive_module_missing:{path.name}:{module}")
        original = source / Path(*module.split(".")).with_suffix(".py")
        require_current_source(python.extract(module), original, module)
    entry = ENTRY_SCRIPTS[path.name]
    if entry not in archive.toc or archive.toc[entry][-1] != "s":
        raise ValueError(f"setup_archive_entry_missing:{path.name}")
    require_current_source(marshal.loads(archive.extract(entry)), project / f"{entry}.py", entry)
    with path.open("rb") as stream:
        executable_sha256 = hashlib.file_digest(stream, "sha256").hexdigest()
    return {
        "contract": "genshin-setup-archive-verification-v1", "status": "verified",
        "executable": path.name, "executable_sha256": executable_sha256, "modules": modules, "entry": entry,
        "normalization_registry_sha256": hashlib.sha256(bundled_registry).hexdigest(),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--exe", type=Path, required=True)
    parser.add_argument("--project-root", type=Path, default=ROOT)
    args = parser.parse_args(argv)
    print(json.dumps(verify_archive(args.exe, args.project_root), ensure_ascii=False))


if __name__ == "__main__":
    main()
