"""Compact, lossless audit storage separate from the reader-facing source index."""
from __future__ import annotations

import hashlib
import json
import re


def source_chunks(value, *, max_utf16=30000):
    text = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    parts, start, used = [], 0, 0
    for index, char in enumerate(text):
        width = 2 if ord(char) > 0xffff else 1
        if used + width > max_utf16:
            parts.append(text[start:index])
            start, used = index, 0
        used += width
    parts.append(text[start:])
    encoded = text.encode("utf-8")
    return {"encoding": "canonical-json-utf8", "bytes": len(encoded),
            "sha256": hashlib.sha256(encoded).hexdigest(), "chunks": parts}


def local_numeric_edit(path, *, resolved_weighting=False):
    """Recognize pure damage inputs, never prove their dependency independence.

    A caller may supply a validated, complete resolved-weighting context. Its
    contribution-group owner must still reject members and matching effects;
    dynamic sampling, unknown provenance and ER are not local damage inputs.
    """
    ordinary = (path == "enemy.level" or path.startswith("enemy.resistance.")
            or path in ("enemy.defense_reduction", "enemy.defense_ignore")
            or path.startswith("hits[") and path.endswith(".multiplier"))
    if ordinary or not resolved_weighting:
        return ordinary
    return bool(re.fullmatch(
        r"characters\[\d+\]\.stats\.(?:atk|hp|def|base_atk|base_hp|base_def|em|crit_rate|crit_dmg"
        r"|dmg_bonus\.(?:all|physical|pyro|hydro|electro|cryo|anemo|geo|dendro))", path)
        or re.fullmatch(r"buffs\[\d+\]\.effects\[\d+\]\.value", path))
