"""Keep the packaged Python runtime independent of Windows OS-owned DLLs."""

import os
from pathlib import Path

_RUNTIME_RESOURCE_DIRECTORY = Path("genshin_runtime/resources")


def runtime_validation_datas(project_root: Path) -> list[tuple[str, str]]:
    directory = project_root / "src" / _RUNTIME_RESOURCE_DIRECTORY
    if not (directory / "profiles.json").is_file():
        raise ValueError("缺少数据库校验所需的运行配置归一化档案")
    return [(str(path), _RUNTIME_RESOURCE_DIRECTORY.as_posix()) for path in sorted(directory.glob("*.json"))]


def verify_runtime_validation_archive(executable: Path, project_root: Path) -> None:
    from PyInstaller.archive.readers import CArchiveReader

    archive = CArchiveReader(str(executable))
    entries = {name.replace("\\", "/"): name for name in archive.toc}
    for source, directory in runtime_validation_datas(project_root):
        path = Path(source)
        expected = f"{directory}/{path.name}"
        if expected not in entries or archive.extract(entries[expected]) != path.read_bytes():
            raise ValueError(f"发行程序缺少正确的数据库校验资源：{expected}")


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
