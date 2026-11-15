"""Pinned Node launch argv shared by the DSH carrier and offline QA guard."""
from __future__ import annotations

from pathlib import Path

NODE_RUNTIME_FLAGS = ("--expose-internals",)


def native_runtime_command(node: Path, entry: Path, entry_args: tuple[str, ...] = ()) -> tuple[str, ...]:
    """Only code-owned flags are added; callers still validate paths and args."""
    return (str(node), *NODE_RUNTIME_FLAGS, str(entry), *entry_args)
