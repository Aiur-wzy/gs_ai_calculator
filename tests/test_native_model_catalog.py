from __future__ import annotations

import pytest

from genshin_app.agent_chat.native_model_catalog import (
    DEEPSEEK_PROVIDER_ID,
    DEFAULT_APP_MODEL_ALLOWLIST,
    DEFAULT_CONFIGURED_MODEL,
    ModelCatalogError,
    ModelSelection,
    check_locked_selection,
    lock_model_selection,
    resolve_model_catalog,
)


def installed(*, second=True):
    rows = [{
        "id": "deepseek-flash",
        "provider": DEEPSEEK_PROVIDER_ID,
        "name": "DeepSeek Flash",
        "contextWindow": 1_000_000,
        "maxTokens": 65_536,
        "reasoning": {
            "efforts": [{"id": value} for value in ("off", "low", "high", "max")],
            "defaultEffort": "high",
        },
    }]
    if second:
        rows.append({
            "id": "deepseek-v4-pro",
            "provider": DEEPSEEK_PROVIDER_ID,
            "name": "DeepSeek V4 Pro",
            "contextWindow": 1_000_000,
            "maxTokens": 65_536,
            "reasoning": {
                "efforts": [{"id": value} for value in ("low", "high", "max")],
                "defaultEffort": "max",
            },
        })
    return rows


def allowlist():
    return [
        {"model": "deepseek-flash", "reasoningEfforts": ["low", "high"],
         "defaultReasoningEffort": "high"},
        {"model": "deepseek-v4-pro", "reasoningEfforts": ["high", "max"]},
    ]


_UNSET = object()


def resolve(*, installed_rows=_UNSET, allowlist_rows=_UNSET, account_rows=_UNSET,
            default="deepseek-flash"):
    return resolve_model_catalog(
        installed_catalog=installed() if installed_rows is _UNSET else installed_rows,
        app_allowlist=allowlist() if allowlist_rows is _UNSET else allowlist_rows,
        account_models=([{"id": "deepseek-flash"}, {"id": "deepseek-v4-pro"}]
                        if account_rows is _UNSET else account_rows),
        configured_default_model=default,
    )


def test_three_layer_intersection_preserves_reviewed_efforts_and_explicit_default():
    catalog = resolve()
    assert [row.model_id for row in catalog.models] == [
        "deepseek-flash", "deepseek-v4-pro"]
    assert catalog.models[0].reasoning_efforts == ("low", "high")
    assert catalog.models[1].reasoning_efforts == ("high", "max")
    assert catalog.models[1].default_reasoning_effort == "max"
    assert catalog.default_status().public() == {
        "provider": DEEPSEEK_PROVIDER_ID,
        "model": "deepseek-flash",
        "reasoningEffort": "high",
        "available": True,
        "reason": None,
    }
    assert catalog.public()["models"][0]["accountVerified"] is True
    assert catalog.public()["models"][0]["contextWindow"] == 1_000_000
    assert catalog.public()["models"][0]["maxTokens"] == 65_536


def test_preconnection_catalog_is_visible_but_cannot_be_locked_or_sent():
    catalog = resolve(account_rows=None)
    assert [row.model_id for row in catalog.models] == [
        "deepseek-flash", "deepseek-v4-pro"]
    assert all(row.public()["accountVerified"] is False for row in catalog.models)
    assert catalog.default_status().public() == {
        "provider": DEEPSEEK_PROVIDER_ID,
        "model": "deepseek-flash",
        "reasoningEffort": "high",
        "available": False,
        "reason": "account_unverified",
    }
    with pytest.raises(ModelCatalogError, match="selected_model_account_unverified"):
        lock_model_selection(catalog)
    locked = ModelSelection(DEEPSEEK_PROVIDER_ID, "deepseek-flash", "high")
    assert check_locked_selection(catalog, locked).reason == "account_unverified"


def test_default_reviewed_policy_matches_current_pinned_dsh_catalog():
    catalog = resolve_model_catalog(
        installed_catalog=installed(),
        app_allowlist=DEFAULT_APP_MODEL_ALLOWLIST,
        account_models={"data": [
            {"id": "deepseek-flash"}, {"id": "deepseek-v4-pro"},
        ]},
        configured_default_model=DEFAULT_CONFIGURED_MODEL,
    )
    assert [row.model_id for row in catalog.models] == [
        "deepseek-flash", "deepseek-v4-pro"]
    assert catalog.default_status().available is True


def test_raw_provider_models_payload_is_accepted_locally_without_io():
    catalog = resolve_model_catalog(
        installed_catalog=installed(),
        app_allowlist=allowlist(),
        account_models={"data": [{"id": "deepseek-flash"}]},
        configured_default_model="deepseek-flash",
    )
    assert [row.model_id for row in catalog.models] == ["deepseek-flash"]


def test_unrelated_colon_model_id_does_not_break_reviewed_intersection():
    catalog = resolve_model_catalog(
        installed_catalog=installed(),
        app_allowlist=allowlist(),
        account_models={"data": [
            {"id": "future:model"},
            {"id": "deepseek-flash"},
        ]},
        configured_default_model="deepseek-flash",
    )
    assert [row.model_id for row in catalog.models] == ["deepseek-flash"]


def test_selection_locks_concrete_default_and_never_tracks_later_default():
    original = resolve()
    locked = lock_model_selection(original)
    assert locked == ModelSelection(
        DEEPSEEK_PROVIDER_ID, "deepseek-flash", "high")

    changed_default = resolve(default="deepseek-v4-pro")
    status = check_locked_selection(changed_default, locked)
    assert status.available is True
    assert status.selection == locked


def test_explicit_model_and_effort_are_validated_and_locked():
    catalog = resolve()
    locked = lock_model_selection(
        catalog, model_id="deepseek-v4-pro", reasoning_effort="high")
    assert locked.public() == {
        "provider": DEEPSEEK_PROVIDER_ID,
        "model": "deepseek-v4-pro",
        "reasoningEffort": "high",
    }
    with pytest.raises(ModelCatalogError, match="selected_reasoning_effort_unavailable"):
        lock_model_selection(catalog, model_id="deepseek-flash", reasoning_effort="max")
    with pytest.raises(ModelCatalogError, match="reasoning_effort_unknown"):
        lock_model_selection(catalog, reasoning_effort="turbo")


def test_model_without_reasoning_tiers_locks_none_without_inventing_effort():
    no_reasoning = [{
        "id": "deepseek-basic",
        "provider": DEEPSEEK_PROVIDER_ID,
        "name": "DeepSeek Basic",
        "reasoning": None,
    }]
    catalog = resolve_model_catalog(
        installed_catalog=no_reasoning,
        app_allowlist=[{"model": "deepseek-basic", "reasoningEfforts": []}],
        account_models={"data": [{"id": "deepseek-basic"}]},
        configured_default_model="deepseek-basic",
    )
    assert catalog.models[0].reasoning_efforts == ()
    assert catalog.models[0].default_reasoning_effort is None
    locked = lock_model_selection(catalog)
    assert locked.reasoning_effort is None
    assert check_locked_selection(catalog, locked).available is True
    with pytest.raises(ModelCatalogError, match="selected_reasoning_effort_unavailable"):
        lock_model_selection(catalog, reasoning_effort="off")


def test_model_offline_is_explicit_even_when_another_model_remains():
    locked = lock_model_selection(
        resolve(), model_id="deepseek-v4-pro", reasoning_effort="max")
    refreshed = resolve(account_rows=[{"id": "deepseek-flash"}])
    status = check_locked_selection(refreshed, locked)
    assert status.available is False
    assert status.reason == "model_unavailable"
    assert status.selection.model_id == "deepseek-v4-pro"
    assert refreshed.default_status().available is True


def test_removed_effort_pauses_locked_session_without_mapping_or_fallback():
    locked = lock_model_selection(
        resolve(), model_id="deepseek-v4-pro", reasoning_effort="max")
    restricted = allowlist()
    restricted[1] = {
        "model": "deepseek-v4-pro",
        "reasoningEfforts": ["high"],
        "defaultReasoningEffort": "high",
    }
    status = check_locked_selection(resolve(allowlist_rows=restricted), locked)
    assert status.available is False
    assert status.reason == "reasoning_effort_unavailable"
    assert status.selection.reasoning_effort == "max"


def test_missing_configured_default_is_reported_without_choosing_available_model():
    catalog = resolve(
        account_rows=[{"id": "deepseek-v4-pro"}],
        default="deepseek-flash",
    )
    assert [model.model_id for model in catalog.models] == ["deepseek-v4-pro"]
    status = catalog.default_status()
    assert status.available is False
    assert status.reason == "configured_default_unavailable"
    assert status.selection.model_id == "deepseek-flash"
    assert status.selection.reasoning_effort is None
    with pytest.raises(ModelCatalogError, match="selected_model_unavailable"):
        lock_model_selection(catalog)


def test_empty_intersection_is_valid_but_has_no_implicit_selection():
    catalog = resolve(account_rows=[{"id": "other-model"}])
    assert catalog.models == ()
    assert catalog.default_status().available is False
    with pytest.raises(ModelCatalogError, match="selected_model_unavailable"):
        lock_model_selection(catalog)


@pytest.mark.parametrize("layer", ["installed", "allowlist", "account"])
def test_duplicate_models_are_rejected_in_every_layer(layer):
    installed_rows, allowlist_rows = installed(), allowlist()
    account_rows = [{"id": "deepseek-flash"}]
    if layer == "installed":
        installed_rows.append(dict(installed_rows[0]))
    elif layer == "allowlist":
        allowlist_rows.append(dict(allowlist_rows[0]))
    else:
        account_rows.append({"id": "deepseek-flash"})
    with pytest.raises(ModelCatalogError, match=f"{layer}_model_duplicate"):
        resolve(installed_rows=installed_rows, allowlist_rows=allowlist_rows,
                account_rows=account_rows)


def test_same_model_id_from_another_installed_provider_does_not_collide():
    rows = installed()
    other = dict(rows[0])
    other["provider"] = "another-provider"
    rows.insert(0, other)
    catalog = resolve(installed_rows=rows)
    assert catalog.by_id()["deepseek-flash"].display_name == "DeepSeek Flash"


@pytest.mark.parametrize("layer", ["installed", "allowlist", "account"])
@pytest.mark.parametrize("bad", [None, "deepseek-flash", [None]])
def test_malformed_catalogs_are_rejected(layer, bad):
    if layer == "account" and bad is None:
        assert all(not row.account_verified for row in resolve(account_rows=None).models)
        return
    kwargs = {f"{layer}_rows": bad}
    with pytest.raises(ModelCatalogError, match=f"{layer}_catalog_invalid"):
        resolve(**kwargs)


def test_raw_account_payload_requires_data_array():
    for value in ({}, {"data": None}, {"data": "deepseek-flash"}):
        with pytest.raises(ModelCatalogError, match="account_catalog_invalid"):
            resolve(account_rows=value)


@pytest.mark.parametrize("layer", ["installed", "allowlist"])
def test_unknown_reasoning_effort_is_rejected_even_before_intersection(layer):
    installed_rows, allowlist_rows = installed(), allowlist()
    if layer == "installed":
        installed_rows[0]["reasoning"]["efforts"] = [{"id": "high"}, {"id": "turbo"}]
    else:
        allowlist_rows[0]["reasoningEfforts"] = ["high", "turbo"]
    with pytest.raises(ModelCatalogError, match="reasoning_effort_unknown"):
        resolve(installed_rows=installed_rows, allowlist_rows=allowlist_rows)


def test_bad_default_effort_and_bad_provider_identity_fail_closed():
    rows = installed()
    rows[0]["reasoning"]["defaultEffort"] = "max"
    rows[0]["reasoning"]["efforts"] = [{"id": "high"}]
    with pytest.raises(ModelCatalogError, match="installed_default_effort_unsupported"):
        resolve(installed_rows=rows)
    with pytest.raises(ModelCatalogError, match="provider_invalid"):
        resolve_model_catalog(
            installed_catalog=[], app_allowlist=[], account_models=[],
            configured_default_model="deepseek-flash", provider_id="bad provider")


@pytest.mark.parametrize(
    ("field", "value", "reason"),
    [
        ("contextWindow", True, "installed_context_window_invalid"),
        ("contextWindow", 0, "installed_context_window_invalid"),
        ("maxTokens", -1, "installed_max_tokens_invalid"),
    ],
)
def test_invalid_installed_capacity_is_rejected(field, value, reason):
    rows = installed()
    rows[0][field] = value
    with pytest.raises(ModelCatalogError, match=reason):
        resolve(installed_rows=rows)


def test_duplicate_dsh_effort_and_allowlist_default_mismatch_fail_closed():
    rows = installed()
    rows[0]["reasoning"]["efforts"] = [{"id": "high"}, {"id": "high"}]
    with pytest.raises(ModelCatalogError, match="installed_reasoning_efforts_duplicate"):
        resolve(installed_rows=rows)

    policy = allowlist()
    policy[0]["defaultReasoningEffort"] = "max"
    with pytest.raises(ModelCatalogError, match="allowlist_default_effort_unsupported"):
        resolve(allowlist_rows=policy)


def test_provider_change_marks_lock_unavailable_without_replacement():
    locked = lock_model_selection(resolve())
    other_catalog = resolve_model_catalog(
        installed_catalog=[{
            **installed()[0],
            "provider": "other-provider",
        }],
        app_allowlist=allowlist(),
        account_models=[{"id": "deepseek-flash"}],
        configured_default_model="deepseek-flash",
        provider_id="other-provider",
    )
    status = check_locked_selection(other_catalog, locked)
    assert status.available is False
    assert status.reason == "provider_unavailable"
    assert status.selection == locked
