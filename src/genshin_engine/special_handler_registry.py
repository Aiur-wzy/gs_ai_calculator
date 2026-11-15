"""Version-pinned reaction handler metadata, independent of database knowledge.

Bindings select generic engine code, never a character, skill or database row.
Fingerprints describe normalized code and declared contracts without source
paths or line numbers, so source and frozen installations have the same ABI.
"""

from __future__ import annotations

import copy
import hashlib
import importlib
import json
import sys
from dataclasses import MISSING, asdict, dataclass, fields, is_dataclass
from functools import lru_cache
from types import CodeType, FunctionType
from typing import Any

from genshin_engine.handler_binding_contract import (
    BINDING_CONTRACT,
    BINDING_FIELDS,
    BINDING_SCHEMA_VERSION,
)
from genshin_engine.runtime_dependencies import (
    family_runtime_modules,
    validate_registered_runtime_dependencies,
)
from genshin_engine.special_replacement_handlers import (
    registered_replacement_bindings,
    replacement_family_binding,
)

REGISTRY_CONTRACT = "special-reaction-handler-registry-v1"


@dataclass(frozen=True, slots=True)
class ResolvedHandlerBinding:
    family: str
    contract_version: int
    implementation_version: int
    profile_schema_version: int
    capabilities: tuple[str, ...]
    handler_fingerprint: str
    registry_fingerprint: str
    schema_version: int = BINDING_SCHEMA_VERSION
    contract_id: str = BINDING_CONTRACT

    def as_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["capabilities"] = list(self.capabilities)
        return result


class HandlerBindingUnavailable(ValueError):
    def __init__(self, code: str, details: dict[str, Any]):
        self.code, self.details = code, copy.deepcopy(details)
        super().__init__(code)


def _fingerprint(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     allow_nan=False, separators=(",", ":")).encode("utf8")).hexdigest()


def _constant(value):
    if isinstance(value, CodeType):
        return _code_contract(value)
    if value is None or type(value) in (str, int, bool):
        return value
    if type(value) is float:
        return {"float": value.hex()}
    if type(value) is bytes:
        return {"bytes": value.hex()}
    if type(value) is tuple:
        return {"tuple": [_constant(item) for item in value]}
    if type(value) is list:
        return {"list": [_constant(item) for item in value]}
    if type(value) is dict:
        return {"dict": sorted(([_constant(key), _constant(item)] for key, item in value.items()),
                               key=lambda pair: _fingerprint(pair[0]))}
    if type(value) is set:
        return {"set": sorted((_constant(item) for item in value), key=_fingerprint)}
    if type(value) is frozenset:
        return {"frozenset": sorted((_constant(item) for item in value), key=_fingerprint)}
    if value is Ellipsis:
        return {"singleton": "Ellipsis"}
    if type(value) is complex:
        return {"complex": [value.real.hex(), value.imag.hex()]}
    raise TypeError(f"unsupported code constant: {type(value).__name__}")


def _code_contract(code: CodeType) -> dict:
    return {
        "bytecode": code.co_code.hex(), "constants": [_constant(value) for value in code.co_consts],
        "names": list(code.co_names), "varnames": list(code.co_varnames),
        "freevars": list(code.co_freevars), "cellvars": list(code.co_cellvars),
        "argcount": code.co_argcount, "posonlyargcount": code.co_posonlyargcount,
        "kwonlyargcount": code.co_kwonlyargcount, "flags": code.co_flags,
        "exceptiontable": code.co_exceptiontable.hex(),
    }


def _module_contract(name: str) -> dict:
    module = importlib.import_module(name)
    functions, records, classes, constants = {}, {}, {}, {}
    for key, value in sorted(vars(module).items()):
        if isinstance(value, FunctionType) and value.__module__ == name:
            functions[key] = _code_contract(value.__code__)
        elif (isinstance(getattr(value, "__wrapped__", None), FunctionType)
              and value.__wrapped__.__module__ == name
              and hasattr(value, "cache_parameters")):
            # Catalog caches are not mutable ABI state; their underlying
            # validation code and declared cache policy are executable inputs.
            functions[key] = {"wrapped": _code_contract(value.__wrapped__.__code__),
                              "cache_policy": value.cache_parameters()}
        elif isinstance(value, type) and value.__module__ == name and is_dataclass(value):
            records[key] = [{"name": item.name, "type": str(item.type),
                             "required": item.default is MISSING and item.default_factory is MISSING}
                            for item in fields(value)]
        elif isinstance(value, type) and value.__module__ == name:
            methods = {}
            for method_name, method in sorted(vars(value).items()):
                if isinstance(method, (classmethod, staticmethod)):
                    method = method.__func__
                if isinstance(method, FunctionType):
                    methods[method_name] = _code_contract(method.__code__)
                elif isinstance(method, property):
                    methods[method_name] = {
                        label: _code_contract(function.__code__) for label, function in (
                            ("get", method.fget), ("set", method.fset), ("delete", method.fdel))
                        if function is not None
                    }
            classes[key] = methods
        if key.isupper():
            try:
                constants[key] = _constant(value)
            except (TypeError, RecursionError):
                # Runtime objects/caches are not contract constants. Pure
                # finite rule tables and scalar constants remain included.
                pass
    return {"module": name, "functions": functions, "records": records,
            "classes": classes, "constants": constants}


def _family_modules(binding) -> tuple[str, ...]:
    return family_runtime_modules(binding.family, handler_module=binding.handler.__module__)


@lru_cache(maxsize=1)
def _registry_snapshot():
    bindings = registered_replacement_bindings()
    validate_registered_runtime_dependencies(bindings)
    descriptors = []
    for binding in bindings:
        descriptor = {
            "family": binding.family, "contract_version": binding.contract_version,
            "implementation_version": binding.implementation_version,
            "profile_schema_version": binding.profile_schema_version,
            "capabilities": sorted(binding.capabilities),
            "implementation_abi": f"{sys.implementation.name}-{sys.version_info.major}.{sys.version_info.minor}",
            "implementation": [_module_contract(name) for name in _family_modules(binding)],
        }
        descriptor["handler_fingerprint"] = _fingerprint(descriptor)
        descriptors.append(descriptor)
    registry_fingerprint = _fingerprint({"contract_id": REGISTRY_CONTRACT, "handlers": descriptors})
    records = tuple(ResolvedHandlerBinding(
        row["family"], row["contract_version"], row["implementation_version"],
        row["profile_schema_version"], tuple(row["capabilities"]),
        row["handler_fingerprint"], registry_fingerprint,
    ) for row in descriptors)
    return registry_fingerprint, records


def special_handler_registry_capabilities() -> dict:
    fingerprint, records = _registry_snapshot()
    return {"contract_id": REGISTRY_CONTRACT, "schema_version": 1,
            "registry_fingerprint": fingerprint,
            "selection": "exact family/contract/implementation/profile/capabilities/fingerprints",
            "legacy_model_v1": "unsigned model-v1 syntax adapter to the current algorithm; not a signed registry binding",
            "handlers": [record.as_dict() for record in records]}


def validate_resolved_handler_binding(value: Any, *, family: str) -> list[dict]:
    if not isinstance(value, dict) or set(value) != BINDING_FIELDS:
        return [{"code": "special_handler_binding_fields", "message": "引擎绑定必须完整声明固定版本和指纹字段。"}]
    issues = []
    for key in ("schema_version", "contract_version", "implementation_version", "profile_schema_version"):
        if type(value[key]) is not int or value[key] < 1:
            issues.append({"code": "special_handler_binding_version", "message": "引擎绑定版本必须为正整数。", "field": key})
    if value["family"] != family or not isinstance(value["contract_id"], str) or not value["contract_id"]:
        issues.append({"code": "special_handler_binding_identity", "message": "引擎绑定必须准确标注反应族和合同。"})
    for key in ("handler_fingerprint", "registry_fingerprint"):
        digest = value[key]
        if not isinstance(digest, str) or len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
            issues.append({"code": "special_handler_binding_fingerprint", "message": "引擎绑定指纹必须为64位小写SHA-256。", "field": key})
    capabilities = value["capabilities"]
    if (not isinstance(capabilities, list) or not capabilities or len(capabilities) > 32
            or any(not isinstance(item, str) or not item for item in capabilities)
            or len(set(capabilities)) != len(capabilities)):
        issues.append({"code": "special_handler_binding_capabilities", "message": "引擎能力必须为唯一的非空名称数组。"})
    return issues


def resolved_handler_binding(family: str, *, contract_version: int = 1,
                             implementation_version: int = 2) -> dict:
    _, records = _registry_snapshot()
    for record in records:
        if (record.family, record.contract_version, record.implementation_version) == (
                family, contract_version, implementation_version):
            return record.as_dict()
    raise HandlerBindingUnavailable("special_handler_version_unavailable", {
        "family": family, "contract_version": contract_version,
        "implementation_version": implementation_version})


def resolve_exact_handler_binding(family: str, requested: dict, *, capability: str | None = None):
    issues = validate_resolved_handler_binding(requested, family=family)
    if issues:
        raise HandlerBindingUnavailable("special_handler_binding_invalid", {"issues": issues})
    expected = resolved_handler_binding(family, contract_version=requested["contract_version"],
                                        implementation_version=requested["implementation_version"])
    if requested != expected:
        raise HandlerBindingUnavailable("special_handler_binding_stale", {
            "family": family, "requested": requested, "expected": expected,
            "changed_fields": sorted(key for key in expected if requested.get(key) != expected[key]),
        })
    if capability is not None and capability not in expected["capabilities"]:
        raise HandlerBindingUnavailable("special_handler_capability_unavailable", {
            "family": family, "capability": capability})
    return replacement_family_binding(family, contract_version=requested["contract_version"],
                                      implementation_version=requested["implementation_version"])


def freeze_special_reaction_handler_bindings(model: dict) -> dict:
    """Materialize explicit generic engine bindings; never enable a replacement."""
    result = copy.deepcopy(model)
    if result.get("schema_version") != 1 or not isinstance(result.get("replacements"), dict):
        return result
    active = [family for family, row in result["replacements"].items()
              if isinstance(row, dict) and row.get("enabled") is True]
    if not active:
        return result
    known = {binding.family for binding in registered_replacement_bindings()}
    if any(family not in known for family in active):
        return result
    result["schema_version"] = 2
    result["handler_bindings"] = {family: resolved_handler_binding(family) for family in sorted(active)}
    return result
