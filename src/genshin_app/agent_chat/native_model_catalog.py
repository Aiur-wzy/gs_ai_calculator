"""Pure local model-catalog intersection and immutable session selection.

The account layer is the already parsed result of the fixed provider
``/models`` probe.  This module performs no I/O and never retries or selects a
fallback model.
"""
from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass

DEEPSEEK_PROVIDER_ID = "deepseek-official"
KNOWN_REASONING_EFFORTS = ("off", "low", "high", "max")
DEFAULT_CONFIGURED_MODEL = "deepseek-flash"
DEFAULT_APP_MODEL_ALLOWLIST = (
    {
        "model": "deepseek-flash",
        "reasoningEfforts": ["off", "low", "high", "max"],
        "defaultReasoningEffort": "high",
    },
    {
        "model": "deepseek-v4-pro",
        "reasoningEfforts": ["off", "low", "high", "max"],
        "defaultReasoningEffort": "high",
    },
)
_MODEL_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")


class ModelCatalogError(ValueError):
    """A sanitized local catalog or selection failure."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _identifier(value: object, name: str) -> str:
    if not isinstance(value, str) or _MODEL_ID.fullmatch(value) is None:
        raise ModelCatalogError(f"{name}_invalid")
    return value


def _effort(value: object) -> str:
    if not isinstance(value, str) or value not in KNOWN_REASONING_EFFORTS:
        raise ModelCatalogError("reasoning_effort_unknown")
    return value


def _efforts(value: object, name: str, *, allow_empty: bool = False) -> tuple[str, ...]:
    if (not isinstance(value, (list, tuple)) or isinstance(value, (str, bytes))
            or (not value and not allow_empty)):
        raise ModelCatalogError(f"{name}_invalid")
    parsed = tuple(_effort(item) for item in value)
    if len(set(parsed)) != len(parsed):
        raise ModelCatalogError(f"{name}_duplicate")
    return parsed


def _dsh_reasoning(value: object) -> tuple[tuple[str, ...], str | None]:
    if value is None:
        return (), None
    if not isinstance(value, Mapping):
        raise ModelCatalogError("installed_reasoning_invalid")
    raw_efforts = value.get("efforts")
    if (not isinstance(raw_efforts, (list, tuple))
            or isinstance(raw_efforts, (str, bytes)) or not raw_efforts):
        raise ModelCatalogError("installed_reasoning_efforts_invalid")
    effort_ids = []
    for row in raw_efforts:
        if not isinstance(row, Mapping):
            raise ModelCatalogError("installed_reasoning_efforts_invalid")
        effort_ids.append(_effort(row.get("id")))
    efforts = tuple(effort_ids)
    if len(set(efforts)) != len(efforts):
        raise ModelCatalogError("installed_reasoning_efforts_duplicate")
    default = _effort(value.get("defaultEffort"))
    if default not in efforts:
        raise ModelCatalogError("installed_default_effort_unsupported")
    return efforts, default


@dataclass(frozen=True)
class AvailableModel:
    model_id: str
    display_name: str
    reasoning_efforts: tuple[str, ...]
    default_reasoning_effort: str | None
    account_verified: bool = True
    context_window: int | None = None
    max_output_tokens: int | None = None

    def public(self) -> dict:
        return {
            "model": self.model_id,
            "displayName": self.display_name,
            "supportedReasoningEfforts": [
                {"reasoningEffort": value} for value in self.reasoning_efforts
            ],
            "defaultReasoningEffort": self.default_reasoning_effort,
            "accountVerified": self.account_verified,
            "reviewed": True,
            **({"contextWindow": self.context_window}
               if self.context_window is not None else {}),
            **({"maxTokens": self.max_output_tokens}
               if self.max_output_tokens is not None else {}),
        }


@dataclass(frozen=True)
class ModelSelection:
    provider_id: str
    model_id: str
    reasoning_effort: str | None

    def public(self) -> dict:
        return {
            "provider": self.provider_id,
            "model": self.model_id,
            "reasoningEffort": self.reasoning_effort,
        }


@dataclass(frozen=True)
class SelectionStatus:
    selection: ModelSelection
    available: bool
    reason: str | None

    def public(self) -> dict:
        return {
            **self.selection.public(),
            "available": self.available,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class ResolvedModelCatalog:
    provider_id: str
    models: tuple[AvailableModel, ...]
    configured_default_model: str

    def by_id(self) -> dict[str, AvailableModel]:
        return {row.model_id: row for row in self.models}

    def default_status(self) -> SelectionStatus:
        model = self.by_id().get(self.configured_default_model)
        effort = model.default_reasoning_effort if model is not None else None
        selection = ModelSelection(self.provider_id, self.configured_default_model, effort)
        if model is not None and not model.account_verified:
            return SelectionStatus(
                selection=selection,
                available=False,
                reason="account_unverified",
            )
        return SelectionStatus(
            selection=selection,
            available=model is not None,
            reason=None if model is not None else "configured_default_unavailable",
        )

    def public(self) -> dict:
        return {
            "provider": self.provider_id,
            "models": [row.public() for row in self.models],
            "default": self.default_status().public(),
        }


def _unique_rows(rows: object, *, layer: str, id_keys: tuple[str, ...]) -> dict[str, Mapping]:
    if (not isinstance(rows, (list, tuple)) or isinstance(rows, (str, bytes))):
        raise ModelCatalogError(f"{layer}_catalog_invalid")
    result: dict[str, Mapping] = {}
    for row in rows:
        if not isinstance(row, Mapping):
            raise ModelCatalogError(f"{layer}_catalog_invalid")
        value = None
        for key in id_keys:
            if key in row:
                value = row[key]
                break
        model_id = _identifier(value, f"{layer}_model")
        if model_id in result:
            raise ModelCatalogError(f"{layer}_model_duplicate")
        result[model_id] = row
    return result


def _installed(rows: object, *, provider_id: str) -> dict[str, AvailableModel]:
    if (not isinstance(rows, (list, tuple)) or isinstance(rows, (str, bytes))):
        raise ModelCatalogError("installed_catalog_invalid")
    result = {}
    for row in rows:
        if not isinstance(row, Mapping):
            raise ModelCatalogError("installed_catalog_invalid")
        model_id = _identifier(row.get("id", row.get("model")), "installed_model")
        row_provider = _identifier(row.get("provider"), "installed_provider")
        if row_provider != provider_id:
            continue
        if model_id in result:
            raise ModelCatalogError("installed_model_duplicate")
        efforts, default = _dsh_reasoning(row.get("reasoning"))
        display_name = row.get("name", row.get("displayName", model_id))
        if not isinstance(display_name, str) or not display_name.strip() or len(display_name) > 128:
            raise ModelCatalogError("installed_display_name_invalid")
        context_window = row.get("contextWindow")
        if context_window is not None and (
            type(context_window) is not int or not 1 <= context_window <= 2**53 - 1
        ):
            raise ModelCatalogError("installed_context_window_invalid")
        max_output_tokens = row.get("maxTokens")
        if max_output_tokens is not None and (
            type(max_output_tokens) is not int
            or not 1 <= max_output_tokens <= 2**53 - 1
        ):
            raise ModelCatalogError("installed_max_tokens_invalid")
        result[model_id] = AvailableModel(
            model_id=model_id,
            display_name=display_name.strip(),
            reasoning_efforts=efforts,
            default_reasoning_effort=default,
            context_window=context_window,
            max_output_tokens=max_output_tokens,
        )
    return result


def _allowlist(rows: object) -> dict[str, tuple[tuple[str, ...], str | None]]:
    parsed = _unique_rows(rows, layer="allowlist", id_keys=("model", "id"))
    result = {}
    for model_id, row in parsed.items():
        efforts = _efforts(
            row.get("reasoningEfforts"),
            "allowlist_reasoning_efforts",
            allow_empty=True,
        )
        preferred = row.get("defaultReasoningEffort")
        if preferred is not None:
            preferred = _effort(preferred)
            if preferred not in efforts:
                raise ModelCatalogError("allowlist_default_effort_unsupported")
        result[model_id] = (efforts, preferred)
    return result


def resolve_model_catalog(
    *,
    installed_catalog: object,
    app_allowlist: object,
    account_models: object,
    configured_default_model: str,
    provider_id: str = DEEPSEEK_PROVIDER_ID,
) -> ResolvedModelCatalog:
    """Resolve reviewed installed models, optionally verified by ``/models``.

    ``account_models=None`` is the pre-connection UI projection. Its rows are
    visible as unverified candidates but cannot be locked for a conversation.
    Any supplied account payload is treated as a completed probe and therefore
    participates in the strict three-way intersection.
    """
    provider = _identifier(provider_id, "provider")
    configured_default = _identifier(configured_default_model, "configured_default_model")
    installed = _installed(installed_catalog, provider_id=provider)
    allowlist = _allowlist(app_allowlist)
    account_verified = account_models is not None
    if account_models is None:
        account = {model_id: {} for model_id in installed.keys() & allowlist.keys()}
    else:
        if isinstance(account_models, Mapping):
            account_models = account_models.get("data")
        account = _unique_rows(account_models, layer="account", id_keys=("id", "model"))
    models = []
    for model_id in sorted(installed.keys() & allowlist.keys() & account.keys()):
        source = installed[model_id]
        allowed_efforts, preferred = allowlist[model_id]
        efforts = tuple(item for item in source.reasoning_efforts if item in allowed_efforts)
        if bool(source.reasoning_efforts) != bool(allowed_efforts) or (
                source.reasoning_efforts and not efforts):
            continue
        default = None if not efforts else preferred or source.default_reasoning_effort
        if efforts and default not in efforts:
            raise ModelCatalogError("resolved_default_effort_unsupported")
        models.append(AvailableModel(
            model_id=model_id,
            display_name=source.display_name,
            reasoning_efforts=efforts,
            default_reasoning_effort=default,
            account_verified=account_verified,
            context_window=source.context_window,
            max_output_tokens=source.max_output_tokens,
        ))
    return ResolvedModelCatalog(
        provider_id=provider,
        models=tuple(models),
        configured_default_model=configured_default,
    )


def lock_model_selection(
    catalog: ResolvedModelCatalog,
    *,
    model_id: str | None = None,
    reasoning_effort: str | None = None,
) -> ModelSelection:
    """Resolve defaults once and lock the concrete IDs into a conversation."""
    selected_model = catalog.configured_default_model if model_id is None else _identifier(
        model_id, "selected_model")
    model = catalog.by_id().get(selected_model)
    if model is None:
        raise ModelCatalogError("selected_model_unavailable")
    if not model.account_verified:
        raise ModelCatalogError("selected_model_account_unverified")
    selected_effort = (model.default_reasoning_effort if reasoning_effort is None
                       else _effort(reasoning_effort))
    if (model.reasoning_efforts and selected_effort not in model.reasoning_efforts) or (
            not model.reasoning_efforts and selected_effort is not None):
        raise ModelCatalogError("selected_reasoning_effort_unavailable")
    return ModelSelection(catalog.provider_id, selected_model, selected_effort)


def check_locked_selection(
    catalog: ResolvedModelCatalog,
    selection: ModelSelection,
) -> SelectionStatus:
    """Check a session lock without altering or falling back from it."""
    if selection.provider_id != catalog.provider_id:
        return SelectionStatus(selection, False, "provider_unavailable")
    model = catalog.by_id().get(selection.model_id)
    if model is None:
        return SelectionStatus(selection, False, "model_unavailable")
    if not model.account_verified:
        return SelectionStatus(selection, False, "account_unverified")
    if not model.reasoning_efforts:
        if selection.reasoning_effort is None:
            return SelectionStatus(selection, True, None)
        return SelectionStatus(selection, False, "reasoning_effort_unavailable")
    if selection.reasoning_effort is None:
        return SelectionStatus(selection, False, "reasoning_effort_unavailable")
    if selection.reasoning_effort not in model.reasoning_efforts:
        return SelectionStatus(selection, False, "reasoning_effort_unavailable")
    return SelectionStatus(selection, True, None)


__all__ = [
    "DEEPSEEK_PROVIDER_ID",
    "DEFAULT_APP_MODEL_ALLOWLIST",
    "DEFAULT_CONFIGURED_MODEL",
    "KNOWN_REASONING_EFFORTS",
    "AvailableModel",
    "ModelCatalogError",
    "ModelSelection",
    "ResolvedModelCatalog",
    "SelectionStatus",
    "check_locked_selection",
    "lock_model_selection",
    "resolve_model_catalog",
]
