"""Verify a frozen Setup or updater archive against its build source without executing it."""
from __future__ import annotations

import argparse
import hashlib
import json
import marshal
import struct
import zlib
from pathlib import Path
from types import CodeType

if __package__:
    from .engine_runtime_release import _code_contract
    from .packaging_windows_runtime import (
        RUNTIME_NORMALIZATION_REGISTRY,
        verify_runtime_validation_archive,
    )
else:
    from engine_runtime_release import _code_contract
    from packaging_windows_runtime import (
        RUNTIME_NORMALIZATION_REGISTRY,
        verify_runtime_validation_archive,
    )

ROOT = Path(__file__).resolve().parents[1]
COMMON_MODULES = (
    "genshin_app.database_release_reuse",
    "genshin_app.database_transaction",
    "genshin_app.installation_location",
    "genshin_app.installation_inventory",
    "genshin_app.installation_runtime",
    "genshin_app.installation_timing",
    "genshin_app.product_release_index",
    "genshin_app.release_pair_helper",
    "genshin_app.release_pair_trust",
    "genshin_app.release_pair_update",
    "genshin_db.release_migration",
    "genshin_db.release_profile",
    "genshin_db.release_qualification",
    "genshin_db.runtime_store",
    "genshin_db.semantic_store",
    "genshin_runtime.normalization",
)
ENTRY_MODULES = {
    "GenshinCalcSetup.exe": ("genshin_app.product_first_install", "genshin_app.product_database_setup", "genshin_app.release_pair_transport",
                             "genshin_app.setup_frozen_qualification", "genshin_app.managed_installation",
                             "genshin_app.installation_migration", "genshin_app.windows_product_registration",
                             "genshin_app.windows_shell_identity", "genshin_app.shortcuts", "genshin_app.paths"),
    "GenshinCalcUpdater.exe": (),
}
ENTRY_SCRIPTS = {"GenshinCalcSetup.exe": "installer_entry", "GenshinCalcUpdater.exe": "updater_entry"}


def verify_archive(executable: Path, project_root: Path = ROOT) -> dict:
    from PyInstaller.archive.readers import (
        ArchiveReadError,
        CArchiveReader,
        NotAnArchiveError,
    )

    path = Path(executable).resolve(strict=True)
    expected_modules = ENTRY_MODULES.get(path.name)
    if expected_modules is None or not path.is_file():
        raise ValueError("setup_archive_executable_invalid")
    project = Path(project_root).resolve(strict=True)
    try:
        return _verify_archive(CArchiveReader(str(path)), path, project, expected_modules)
    except (ArchiveReadError, NotAnArchiveError, EOFError, struct.error, zlib.error) as error:
        raise ValueError(f"setup_archive_unreadable:{path.name}") from error


def _verify_archive(archive, path: Path, project: Path, expected_modules) -> dict:
    source = project / "src"
    resource_hashes = verify_runtime_validation_archive(path, project, archive=archive)
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
    try:
        entry_code = marshal.loads(archive.extract(entry))
    except ValueError as error:
        raise ValueError(f"setup_archive_entry_invalid:{path.name}") from error
    require_current_source(entry_code, project / f"{entry}.py", entry)
    with path.open("rb") as stream:
        executable_sha256 = hashlib.file_digest(stream, "sha256").hexdigest()
    return {
        "contract": "genshin-setup-archive-verification-v1", "status": "verified",
        "executable": path.name, "executable_sha256": executable_sha256, "modules": modules, "entry": entry,
        "normalization_registry_sha256": resource_hashes[RUNTIME_NORMALIZATION_REGISTRY],
        "runtime_validation_resource_sha256": resource_hashes,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--exe", type=Path, required=True)
    parser.add_argument("--project-root", type=Path, default=ROOT)
    args = parser.parse_args(argv)
    print(json.dumps(verify_archive(args.exe, args.project_root), ensure_ascii=False))


if __name__ == "__main__":
    main()
