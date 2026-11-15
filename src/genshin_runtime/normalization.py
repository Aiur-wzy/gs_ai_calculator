"""Commit-bound JSON normalization, without database or execution semantics.

Field identifiers and ``$type`` values occupy separate namespaces. Named maps
retain their keys, including names which happen to resemble an encoded field.
The original input is never modified; pointers always resolve against that input.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from functools import lru_cache
from importlib import resources
from types import MappingProxyType

NORMALIZATION_VERSION = 2
KIND_TYPE_SCOPES = frozenset({"ability", "ability_source", "talent", "talent_source",
                              "talent_config", "modifier", "operation"})
KIND_FIELD_SCOPES = MappingProxyType({
    **{kind: None for kind in KIND_TYPE_SCOPES},
    "avatar_config": ("abilities", "abilityName"),
    "gadget_config": ("abilities", "abilityName"),
})
SUPPORTED_KINDS = frozenset(KIND_FIELD_SCOPES)
_COMMIT = re.compile(r"[0-9a-f]{40}\Z")
_INDEX = re.compile(r"(?:0|[1-9][0-9]*)\Z")
_BAD_ESCAPE = re.compile(r"~(?![01])")
_NAMED_FIELDS = frozenset({"modifiers", "abilitySpecials", "properties", "globalValues"})


def _mapping(value, name):
    if not isinstance(value, Mapping) or any(
        not isinstance(k, str) or not k or not isinstance(v, str) or not v
        for k, v in value.items()
    ):
        raise ValueError(f"{name} must map nonempty strings to nonempty strings")
    if name == "fields" and any(
        (k == "$type" or v == "$type") and k != v for k, v in value.items()
    ):
        raise ValueError("$type is a reserved field; use the separate types mapping")
    return MappingProxyType(dict(value))


def _mode(value, kind):
    if not isinstance(kind, str) or not kind:
        raise ValueError("kind must be a nonempty string")
    if kind not in SUPPORTED_KINDS:
        return "opaque"
    if kind == "ability_source" or kind == "ability" and isinstance(value, list):
        return "abilities"
    if kind in {"talent", "talent_source", "gadget_config"} or kind == "talent_config" and isinstance(value, dict):
        return "named"
    return "record"


def _child_mode(mode, key):
    if mode == "opaque":
        return "opaque"
    if mode == "variants" or mode == "named":
        return "record"
    return "named" if key in _NAMED_FIELDS else "record"


def _escape(token):
    return token.replace("~", "~0").replace("/", "~1")


@dataclass(frozen=True)
class Profile:
    """A reviewed mapping for exact commits; no prefix/version matching."""

    id: str
    commits: tuple[str, ...]
    fields: Mapping[str, str]
    types: Mapping[str, str]
    mapping_fingerprint: str = field(init=False)

    def __post_init__(self):
        if not isinstance(self.id, str) or not self.id.strip():
            raise ValueError("profile id must be a nonempty string")
        if isinstance(self.commits, str):
            raise ValueError("commits must be a collection of full commit hashes")  # noqa: TRY004 - validation API
        try:
            commits = tuple(self.commits)
        except TypeError as exc:
            raise ValueError("commits must be a collection of full commit hashes") from exc
        if not commits or any(not isinstance(c, str) or not _COMMIT.fullmatch(c) for c in commits):
            raise ValueError("profile requires full lowercase commit hashes")
        if len(set(commits)) != len(commits):
            raise ValueError("duplicate profile commit")
        object.__setattr__(self, "commits", tuple(sorted(commits)))
        object.__setattr__(self, "fields", _mapping(self.fields, "fields"))
        object.__setattr__(self, "types", _mapping(self.types, "types"))
        scopes = {kind: {"fields": sorted(fields) if fields is not None else "*",
                         "translate_types": kind in KIND_TYPE_SCOPES}
                  for kind, fields in KIND_FIELD_SCOPES.items()}
        payload = {"normalization_version": NORMALIZATION_VERSION, "kind_scopes": scopes,
                   "named_fields": sorted(_NAMED_FIELDS),
                   "fields": dict(self.fields), "types": dict(self.types)}
        serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        object.__setattr__(self, "mapping_fingerprint", hashlib.sha256(serialized.encode()).hexdigest())

    def _entries(self, value, mode, kind):
        entries = {}
        allowed = KIND_FIELD_SCOPES.get(kind, ())
        for raw, item in value.items():
            if not isinstance(raw, str):
                raise ValueError("JSON object keys must be strings")  # noqa: TRY004 - validation API
            canonical = raw if mode in {"named", "variants", "opaque"} else self.fields.get(raw, raw)
            if allowed is not None and canonical not in allowed:
                canonical = raw
            if canonical in entries:
                raise ValueError(f"normalized key collision: {canonical!r}")
            entries[canonical] = (raw, item)
        return entries

    def _next_mode(self, mode, key, kind):
        # Recognize a reviewed named-map boundary without renaming its field in
        # a limited scope. Its dynamic keys must never become property names.
        if KIND_FIELD_SCOPES.get(kind, ()) is not None:
            key = self.fields.get(key, key)
        return _child_mode(mode, key)

    def _normalize(self, value, mode, kind):
        if isinstance(value, dict):
            result = {}
            for key, (_, item) in self._entries(value, mode, kind).items():
                if (kind in KIND_TYPE_SCOPES and mode not in {"named", "variants", "opaque"}
                        and key == "$type" and isinstance(item, str)):
                    result[key] = self.types.get(item, item)
                else:
                    result[key] = self._normalize(item, self._next_mode(mode, key, kind), kind)
            return result
        if isinstance(value, list):
            child = "variants" if mode == "abilities" else "opaque" if mode == "opaque" else "record"
            return [self._normalize(item, child, kind) for item in value]
        if value is None or isinstance(value, (str, bool, int, float)):
            return value
        raise ValueError("normalization accepts JSON values only")

    def normalize(self, value, *, kind="ability"):
        """Return a detached canonical JSON value; unknown names remain intact."""
        return self._normalize(value, _mode(value, kind), kind)

    def raw_pointer(self, value, canonical_relative_pointer, *, kind="ability"):
        """Translate an existing RFC6901 pointer through the same structural walk.

        Missing nodes, ambiguous keys, invalid escapes and noncanonical array
        indices fail. Dynamic dictionary names are not reverse-mapped.
        """
        pointer = canonical_relative_pointer
        if not isinstance(pointer, str) or pointer and not pointer.startswith("/"):
            raise ValueError("JSON pointer must be empty or start with /")
        mode, raw_tokens = _mode(value, kind), []
        for encoded in pointer.split("/")[1:]:
            if _BAD_ESCAPE.search(encoded):
                raise ValueError("invalid JSON pointer escape")
            token = encoded.replace("~1", "/").replace("~0", "~")
            if isinstance(value, dict):
                if mode in {"named", "variants", "opaque"}:
                    # These maps retain literal keys, so no sibling can collide
                    # through renaming. Avoid rescanning large named gadget maps
                    # for every reference; normalize validates the complete JSON.
                    if token not in value:
                        raise ValueError(f"canonical JSON pointer key does not exist: {token!r}")
                    raw, value = token, value[token]
                else:
                    entries = self._entries(value, mode, kind)
                    if token not in entries:
                        raise ValueError(f"canonical JSON pointer key does not exist: {token!r}")
                    raw, value = entries[token]
                raw_tokens.append(_escape(raw))
                mode = self._next_mode(mode, token, kind)
            elif isinstance(value, list):
                if not _INDEX.fullmatch(token) or int(token) >= len(value):
                    raise ValueError("invalid or missing JSON array index")
                raw_tokens.append(token)
                value = value[int(token)]
                mode = "variants" if mode == "abilities" else "opaque" if mode == "opaque" else "record"
            else:
                raise ValueError("JSON pointer traverses a scalar")  # noqa: TRY004 - validation API
        return "/" + "/".join(raw_tokens) if raw_tokens else ""

    def receipt(self, commit):
        if commit not in self.commits:
            raise ValueError("commit is not covered by this normalization profile")
        return {"normalization_version": NORMALIZATION_VERSION, "profile_id": self.id,
                "mapping_fingerprint": self.mapping_fingerprint, "upstream_commit": commit}

    def verify_receipt(self, receipt, *, commit):
        if (not isinstance(receipt, dict)
                or type(receipt.get("normalization_version")) is not int
                or receipt != self.receipt(commit)):
            raise ValueError("unknown or changed normalization receipt")
        return self


def normalize(value, *, kind="ability", profile):
    return profile.normalize(value, kind=kind)


def raw_pointer(value, canonical_relative_pointer, *, kind="ability", profile):
    return profile.raw_pointer(value, canonical_relative_pointer, kind=kind)


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key in normalization registry: {key!r}")
        result[key] = value
    return result


@lru_cache(maxsize=1)
def _load_profiles():
    """Package resources are immutable for this process; tests may clear cache."""
    source = resources.files("genshin_runtime").joinpath("resources", "profiles.json")
    try:
        document = json.loads(source.read_text(encoding="utf-8"), object_pairs_hook=_unique_object)
    except FileNotFoundError:
        return ()
    if (not isinstance(document, dict) or type(document.get("schema_version")) is not int
            or document.get("schema_version") != 1):
        raise ValueError("unsupported normalization registry schema")
    entries = document.get("profiles")
    if not isinstance(entries, list):
        raise ValueError("normalization registry profiles must be a list")  # noqa: TRY004 - validation API
    profiles, ids, commits = [], set(), set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("invalid normalization profile entry")  # noqa: TRY004 - validation API
        try:
            profile = Profile(entry["id"], entry["commits"], entry["fields"], entry["types"])
        except (KeyError, TypeError) as exc:
            raise ValueError("invalid normalization profile entry") from exc
        if profile.id in ids or commits.intersection(profile.commits):
            raise ValueError("duplicate normalization profile id or commit")
        ids.add(profile.id)
        commits.update(profile.commits)
        profiles.append(profile)
    return tuple(profiles)


def for_commit(commit):
    """Load a shipped exact-commit profile, or return None for unknown commits."""
    if not isinstance(commit, str) or not _COMMIT.fullmatch(commit):
        return None
    return next((p for p in _load_profiles() if commit in p.commits), None)


def verify_receipt(receipt, *, commit):
    """Verify against the installed registry, never against receipt-supplied maps."""
    profile = for_commit(commit)
    if profile is None:
        raise ValueError("unknown normalization commit")
    return profile.verify_receipt(receipt, commit=commit)
