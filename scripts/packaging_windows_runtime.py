"""Keep the packaged Python runtime independent of Windows OS-owned DLLs."""

import os


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
