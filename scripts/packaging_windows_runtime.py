"""Shared frozen resource inventory and Windows runtime packaging rules."""

import hashlib
import os
from pathlib import Path

_RUNTIME_RESOURCE_DIRECTORY = Path("genshin_runtime/resources")
RUNTIME_NORMALIZATION_REGISTRY = "genshin_runtime/resources/profiles.json"


class RuntimeValidationPackageError(ValueError):
    def __init__(self, reason: str, resource: str | None = None):
        self.reason = reason
        self.resource = resource
        super().__init__(f"{reason}:{resource}" if resource else reason)


def _ordinary(path: Path) -> bool:
    return not path.is_symlink() and not path.is_junction()


def _source_resources(project_root: Path) -> dict[str, Path]:
    directory = project_root / "src" / _RUNTIME_RESOURCE_DIRECTORY
    if not (directory / "profiles.json").is_file():
        raise RuntimeValidationPackageError("runtime_validation_registry_missing", "profiles.json")
    for parent in (project_root / "src", directory.parent, directory):
        if not _ordinary(parent):
            raise RuntimeValidationPackageError("runtime_validation_resource_path_invalid", parent.name)
    sources = {}
    names = set()
    for path in sorted(directory.glob("*.json")):
        if not path.is_file() or not _ordinary(path) or path.name.casefold() in names:
            raise RuntimeValidationPackageError("runtime_validation_resource_invalid", path.name)
        names.add(path.name.casefold())
        sources[(_RUNTIME_RESOURCE_DIRECTORY / path.name).as_posix()] = path
    return sources


def runtime_validation_datas(project_root: Path) -> list[tuple[str, str]]:
    return [(str(path), _RUNTIME_RESOURCE_DIRECTORY.as_posix()) for path in _source_resources(project_root).values()]


def _verify_resource_bytes(sources, read_resource) -> dict[str, str]:
    hashes = {}
    for relative, path in sources.items():
        packaged = read_resource(relative)
        if packaged != path.read_bytes():
            raise RuntimeValidationPackageError("runtime_validation_resource_mismatch", path.name)
        hashes[relative] = hashlib.sha256(packaged).hexdigest()
    return hashes


def verify_runtime_validation_directory(internal_dir: Path, project_root: Path) -> dict[str, str]:
    """Check the actual one-dir files before accepting the App inventory."""
    directory = internal_dir / _RUNTIME_RESOURCE_DIRECTORY
    for path in (internal_dir, directory.parent, directory):
        if not path.is_dir() or not _ordinary(path):
            raise RuntimeValidationPackageError("runtime_validation_resource_directory_invalid", path.name)
    sources = _source_resources(project_root)

    def read_resource(relative):
        path = internal_dir / relative
        if not path.is_file() or not _ordinary(path):
            raise RuntimeValidationPackageError("runtime_validation_resource_missing", path.name)
        return path.read_bytes()

    hashes = _verify_resource_bytes(sources, read_resource)
    if {path.name for path in directory.iterdir()} != {path.name for path in sources.values()}:
        raise RuntimeValidationPackageError("runtime_validation_resource_inventory_mismatch")
    return hashes


def verify_runtime_validation_archive(executable: Path, project_root: Path, *, archive=None) -> dict[str, str]:
    """Check the regular data entries extracted by the one-file bootloader."""
    if archive is None:
        from PyInstaller.archive.readers import CArchiveReader

        archive = CArchiveReader(str(executable))
    sources = _source_resources(project_root)
    prefix = _RUNTIME_RESOURCE_DIRECTORY.as_posix() + "/"
    entries = {}
    for name, row in archive.toc.items():
        relative = name.replace("\\", "/")
        if not relative.casefold().startswith(prefix):
            continue
        if relative not in sources or relative in entries or row[-1] not in {"b", "x"}:
            raise RuntimeValidationPackageError("runtime_validation_resource_inventory_mismatch")
        entries[relative] = name

    def read_resource(relative):
        if relative not in entries:
            raise RuntimeValidationPackageError("runtime_validation_resource_missing", Path(relative).name)
        return archive.extract(entries[relative])

    return _verify_resource_bytes(sources, read_resource)


def omit_windows_system_crt(binaries: list[tuple[str, str, str]]) -> list[tuple[str, str, str]]:
    """Drop root UCRT and API-set DLLs resolved by PyInstaller on the build host."""
    if os.name != "nt":
        return binaries

    # The product's Windows 10+ target supplies these APIs. Compare the whole
    # destination name so nested resources such as native Agent dependencies
    # retain the original release inventory. VC140 is a separate runtime.
    def supplied_by_windows(destination: str) -> bool:
        name = destination.lower()
        return name == "ucrtbase.dll" or (
            "/" not in name and "\\" not in name
            and name.startswith("api-ms-win-") and name.endswith(".dll")
        )

    return [entry for entry in binaries if not supplied_by_windows(entry[0])]
