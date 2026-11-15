"""Actual installed production payload identity, without dependency parsing."""
from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path

DEPENDENCY_METADATA_POLICY = "pnpm-release-metadata-v2"
INSTALL_ONLY_MODULE_FIELDS = frozenset({"storeDir", "virtualStoreDir", "prunedAt"})
_SHIM_TARGET_MARKER = b"# cmd-shim-target="
_SHIM_TARGET_PATH = re.compile(rb"(?:[A-Za-z]:[/\\]|/|\\\\)[^\x00\r\n]+(?:\r?\n)?\Z")


class DependencyPayloadError(RuntimeError):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


def project_dependency_shim(name, path):
    """Return the release filename and projected bytes for pnpm's .bin wrappers."""
    if Path(name).parent.name != ".bin":
        return name, None
    if name[-4:].lower() == ".cmd":
        return name[:-4] + ".cmd", None
    if name.endswith(".ps1"):
        return name, None
    raw = path.read_bytes()
    if _SHIM_TARGET_MARKER not in raw:
        return name, None
    offset = raw.index(_SHIM_TARGET_MARKER)
    prefix = raw[:offset]
    target = raw[offset + len(_SHIM_TARGET_MARKER):]
    if (raw.count(_SHIM_TARGET_MARKER) != 1 or not prefix.startswith((b"#!/bin/sh\n", b"#!/bin/sh\r\n"))
            or not prefix.endswith(b"\n") or not _SHIM_TARGET_PATH.fullmatch(target)):
        raise DependencyPayloadError("dsh_dependency_shim_marker_invalid")
    # This final cmd-shim comment is never consulted by the shell wrapper.
    return name, prefix


def dependency_payload_inventory(runtime_root):
    root = Path(runtime_root).absolute() / "node_modules"
    if not root.is_dir() or root.is_symlink() or root.is_junction():
        raise DependencyPayloadError("dsh_dependency_payload_root")
    files, aliases, directories = {}, {}, {""}
    total_bytes = 0

    def walk(prefix):
        nonlocal total_bytes
        folder = root.joinpath(*prefix.split("/")) if prefix else root
        for item in sorted(os.scandir(folder), key=lambda entry: entry.name):
            name = f"{prefix}/{item.name}" if prefix else item.name
            if name == ".pnpm-workspace-state-v1.json":
                if not item.is_file(follow_symlinks=False):
                    raise DependencyPayloadError("dsh_dependency_metadata_type")
                continue
            path = Path(item.path)
            if item.is_symlink() or path.is_junction():
                try:
                    aliases[name] = path.resolve(strict=True).relative_to(root).as_posix()
                except ValueError:
                    raise DependencyPayloadError("dsh_dependency_alias_escape") from None
            elif item.is_dir(follow_symlinks=False):
                directories.add(name)
                walk(name)
            elif item.is_file(follow_symlinks=False):
                name, shim_bytes = project_dependency_shim(name, path)
                if name in files:
                    raise DependencyPayloadError("dsh_dependency_shim_name_collision")
                if name == ".modules.yaml":
                    value = json.loads(path.read_bytes())
                    if not isinstance(value, dict):
                        raise DependencyPayloadError("dsh_dependency_metadata_type")
                    for field in INSTALL_ONLY_MODULE_FIELDS:
                        value.pop(field, None)
                    raw = (json.dumps(value, ensure_ascii=False, allow_nan=False,
                                      sort_keys=True, separators=(",", ":")) + "\n").encode()
                    files[name] = hashlib.sha256(raw).hexdigest()
                    total_bytes += len(raw)
                elif shim_bytes is not None:
                    files[name] = hashlib.sha256(shim_bytes).hexdigest()
                    total_bytes += len(shim_bytes)
                else:
                    digest = hashlib.sha256()
                    with path.open("rb") as stream:
                        while chunk := stream.read(1024 * 1024):
                            digest.update(chunk)
                    files[name] = digest.hexdigest()
                    total_bytes += path.stat().st_size
            else:
                raise DependencyPayloadError("dsh_dependency_payload_type")

    walk("")
    if ".modules.yaml" not in files:
        raise DependencyPayloadError("dsh_dependency_metadata_missing")
    if any(target not in files and target not in directories for target in aliases.values()):
        raise DependencyPayloadError("dsh_dependency_alias_uninventoried")
    return {"contract": "genshin-node-dependency-payloads-v1", "metadata_policy": DEPENDENCY_METADATA_POLICY,
            "files": dict(sorted(files.items())), "aliases": dict(sorted(aliases.items())), "total_bytes": total_bytes}
