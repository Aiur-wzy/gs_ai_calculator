"""Reviewed child environment shared by the current DSH shell and historical runtimes."""

import os
from collections.abc import Mapping
from pathlib import Path

from .native_provider_proxy import validate_native_provider_proxy_environment

_SAFE_ENVIRONMENT_KEYS = frozenset(
    {
        "COMSPEC",
        # Normal executable/module discovery belongs to the complete DSH
        # runtime. These are paths and OS facts, never provider credentials.
        "PATH",
        "PROGRAMFILES",
        "PROGRAMFILES(X86)",
        "PROGRAMW6432",
        "COMMONPROGRAMFILES",
        "COMMONPROGRAMFILES(X86)",
        "COMMONPROGRAMW6432",
        "PSMODULEPATH",
        # Preserve the user's ordinary Windows directories. DSH storage still
        # uses the separately App-selected profile and derived DSH_HOME below.
        "USERPROFILE",
        "HOMEDRIVE",
        "HOMEPATH",
        "APPDATA",
        "LOCALAPPDATA",
        # App-owned, non-secret production profile root.  Production startup
        # overwrites this value from its reviewed sessions_root; ambient values
        # alone never become authoritative.  Node validates and derives every
        # DSH storage child below this one root.
        "GENSHIN_NATIVE_DSH_PROFILE_ROOT",
        "PATHEXT",
        "SYSTEMDRIVE",
        "SYSTEMROOT",
        "TEMP",
        "TMP",
        "WINDIR",
    }
)

def build_native_runtime_environment(
    source: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Retain explicit standard OS paths, without inheriting secrets/injection.

    Keys are canonicalized once to uppercase: Windows accepts mixed-case OS
    names, but child environment maps must never contain PATH/Path duplicates.
    This is an allowlist, not whole-process environment inheritance. Dedicated
    frozen QA still supplies its own clean, redirected environment.
    """
    values = os.environ if source is None else source
    output: dict[str, str] = {}
    for key, value in values.items():
        if key.upper() in _SAFE_ENVIRONMENT_KEYS and isinstance(value, str):
            output[key.upper()] = value
    # Ambient proxy variables are never inherited.  Only a caller-supplied,
    # explicitly marked and revalidated mapping reaches the runtime.
    if source is not None:
        output.update(validate_native_provider_proxy_environment(values))
        profile = output.get("GENSHIN_NATIVE_DSH_PROFILE_ROOT")
        if profile:
            root = Path(profile)
            if not root.is_absolute():
                raise ValueError("native_dsh_profile_root_must_be_absolute")
            # DSH's provider-owned anonymous id/file index use this global
            # upstream path independently of Session persistence options.
            # Never inherit ambient DSH_HOME or the user's actual home.
            output["DSH_HOME"] = str(root.resolve() / "dsh-home")
    output["DSH_TELEMETRY_DISABLED"] = "1"
    output["NO_COLOR"] = "1"
    return output

