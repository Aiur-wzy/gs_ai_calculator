"""Pure constant EM ledger: source identity, immutable frames and lazy guards."""
import copy

import pytest

from genshin_engine.attribute_ledger import AttributeLedger, AttributeLedgerError
from genshin_engine.attribute_schema import attribute_ledger_capabilities


def component(effect="gain", recipient="a"):
    return {"source_fingerprint": "synthetic-source-v1", "provider": "b", "part_id": "em",
            "effect_id": effect, "recipient": recipient}


def contribution(ident="gain", value=40, inclusion="included", recipient="a", **changes):
    return {"id": ident, "component": component(ident, recipient), "instance_id": "instance",
            "layer_id": "layer0", "stat": "em", "value": value,
            "nonextra_inclusion": inclusion, "visibility": "attribute", "constraints": [], **changes}


def declaration():
    return {"contract": "attribute-ledger-v1",
            "initials": [{"actor": "a", "stat": "em", "total": 300, "nonextra": 100, "base": 0,
                          "included_components": [], "source": "Synthetic initial panel; not a game fact"}],
            "contributions": [],
            "frames": [{"id": "f0", "origin": 0, "run_id": "0:h0", "event_id": "h0", "phase": "reaction_created",
                        "state_epoch": 4, "t_us": 200000, "contribution_ids": []}]}


def attach(data, *nodes):
    data["contributions"].extend(nodes)
    data["frames"][0]["contribution_ids"].extend(node["id"] for node in nodes)
    return data


def read(ledger, basis="total", actor="a", frame="f0", **kw):
    return ledger.read_stat(actor, "em", basis, frame, **kw)


def test_baseline_static_explanations_and_dynamic_classification_stay_separate():
    data = declaration()
    data["initials"][0]["included_components"] = [
        {"component": component("already-included"), "value": 200, "nonextra_inclusion": "excluded"}]
    attach(data, contribution(), contribution("extra", 30, "excluded"))
    ledger = AttributeLedger(data)
    assert read(ledger)["value"] == 370
    assert read(ledger, "nonextra")["value"] == 140
    assert read(ledger, "base")["value"] == 0
    assert read(ledger)["baseline"] == 300
    assert any(row["decision"] == "already_in_baseline" for row in read(ledger)["contributions"])


@pytest.mark.parametrize("basis", ["nonextra", "base"])
def test_missing_basis_never_falls_back_to_total_or_zero(basis):
    data = declaration()
    del data["initials"][0][basis]
    ledger = AttributeLedger(data)
    receipt = read(ledger, basis)
    assert receipt["status"] == "pending" and "value" not in receipt
    assert receipt["issues"][0]["code"] == "attribute_basis_missing"
    assert read(ledger)["value"] == 300


def test_unknown_classification_only_blocks_related_nonextra_query_even_if_zero():
    data = attach(declaration(), contribution("unknown", 0, "unknown"))
    data["initials"].append({**copy.deepcopy(data["initials"][0]), "actor": "b"})
    ledger = AttributeLedger(data)
    assert read(ledger)["value"] == 300
    assert read(ledger, "base")["value"] == 0
    assert read(ledger, "nonextra")["issues"][0]["code"] == "attribute_classification_unknown"
    assert read(ledger, "nonextra", actor="b")["value"] == 100


def test_component_alias_cannot_reapply_an_included_baseline_effect():
    node = contribution("dynamic-alias", 200, "excluded")
    data = attach(declaration(), node)
    data["initials"][0]["included_components"] = [
        {"component": copy.deepcopy(node["component"]), "value": 200, "nonextra_inclusion": "excluded"}]
    receipt = read(AttributeLedger(data))
    assert receipt["status"] == "invalid" and "value" not in receipt
    assert receipt["issues"][0]["code"] == "attribute_duplicate_delivery"
    # Another effect from the same source/part remains a distinct component.
    data["contributions"][0]["component"]["effect_id"] = "another-effect"
    assert read(AttributeLedger(data))["value"] == 500


def test_component_layer_alias_is_duplicate_but_independent_layer_is_additive():
    first = contribution()
    second = {**copy.deepcopy(first), "id": "alias"}
    data = attach(declaration(), first, second)
    assert read(AttributeLedger(data))["issues"][0]["code"] == "attribute_duplicate_delivery"
    data["contributions"][1]["layer_id"] = "layer1"
    assert read(AttributeLedger(data))["value"] == 380


def test_mutating_input_or_receipt_cannot_rewrite_existing_frame():
    data = attach(declaration(), contribution())
    ledger = AttributeLedger(data)
    first = read(ledger)
    data["contributions"][0]["value"] = 10000
    data["frames"][0]["contribution_ids"] = []
    first["value"] = -1
    first["contributions"][0]["value"] = -1
    assert read(ledger)["value"] == 340


def test_same_time_different_phase_and_origin_do_not_share_sample_cache():
    data = attach(declaration(), contribution("gain", 200))
    first = data["frames"][0]
    data["frames"].append({**copy.deepcopy(first), "id": "post", "phase": "post", "state_epoch": 5})
    data["frames"].append({**copy.deepcopy(first), "id": "next-origin", "origin": 1, "run_id": "1:h0"})
    first["contribution_ids"] = []
    ledger = AttributeLedger(data)
    assert read(ledger)["value"] == 300
    assert read(ledger, frame="post")["value"] == 500
    assert read(ledger, frame="next-origin")["query"]["frame"]["origin"] == 1
    assert read(ledger)["receipt_id"] != read(ledger, frame="post")["receipt_id"]


def test_unordered_contribution_storage_has_identical_receipt_identity():
    data = attach(declaration(), contribution("a", 1e16), contribution("b", .5), contribution("c", -1e16))
    changed = copy.deepcopy(data)
    changed["contributions"].reverse()
    changed["frames"][0]["contribution_ids"].reverse()
    assert read(AttributeLedger(data)) == read(AttributeLedger(changed))
    assert read(AttributeLedger(data))["value"] == 300.5


@pytest.mark.parametrize("constraint", ["filter", "quota", "resource_gate", "hit_match"])
def test_unimplemented_contextual_writer_is_never_silently_an_ordinary_value(constraint):
    data = attach(declaration(), contribution(constraints=[constraint]))
    receipt = read(AttributeLedger(data))
    assert receipt["status"] == "pending" and "value" not in receipt
    assert receipt["issues"][0]["code"] == "attribute_context_unsupported"
    assert read(AttributeLedger(data), "base")["value"] == 0


def test_damage_context_and_nonordinary_purpose_require_future_typed_context():
    data = attach(declaration(), contribution(visibility="damage_context"))
    assert read(AttributeLedger(data))["issues"][0]["code"] == "attribute_context_unsupported"
    result = read(AttributeLedger(declaration()), purpose="damage")
    assert result["status"] == "pending" and "value" not in result


def test_l1_expression_cycle_is_bounded_pending_and_basis_prunes_excluded_nodes():
    expression = {"kind": "stat", "subject": "owner", "stat": "em", "factor": .2, "sample": "current"}
    data = attach(declaration(), contribution(value=expression, inclusion="excluded"))
    data["initials"].append({**copy.deepcopy(data["initials"][0]), "actor": "b"})
    other = contribution("back", expression, "excluded", recipient="b")
    other["component"]["provider"] = "a"
    attach(data, other)
    ledger = AttributeLedger(data)
    assert read(ledger)["issues"][0]["code"] == "attribute_expression_unsupported"
    assert read(ledger, actor="b")["issues"][0]["code"] == "attribute_expression_unsupported"
    assert read(ledger, "nonextra")["value"] == 100
    assert read(ledger, "base")["value"] == 0


def test_unrelated_expression_does_not_force_eager_full_party_evaluation():
    data = declaration()
    expression = {"kind": "stat", "subject": "owner", "stat": "em", "factor": .2, "sample": "current"}
    attach(data, contribution(value=expression, recipient="b"))
    data["initials"].append({**copy.deepcopy(data["initials"][0]), "actor": "b"})
    assert read(AttributeLedger(data))["value"] == 300


def test_base_and_every_contribution_use_one_stable_sum():
    data = declaration()
    data["initials"][0]["total"] = 1e16
    attach(data, contribution("cancel", -1e16), contribution("tiny", 1))
    assert read(AttributeLedger(data))["value"] == 1


@pytest.mark.parametrize("value", [-301, 1e308])
def test_negative_effective_or_overflow_has_no_consumable_value(value):
    data = attach(declaration(), contribution(value=value))
    if value > 0:
        data["initials"][0]["total"] = 1e308
    result = read(AttributeLedger(data))
    assert result["status"] == "invalid" and "value" not in result
    assert result["issues"][0]["code"] == "attribute_value_invalid"


@pytest.mark.parametrize("value", [float("inf"), float("nan"), True])
def test_nonfinite_or_boolean_stat_input_is_not_accepted(value):
    data = declaration()
    data["initials"][0]["total"] = value
    with pytest.raises(AttributeLedgerError):
        AttributeLedger(data)


@pytest.mark.parametrize("change", ["duplicate_frame", "wrong_origin", "unknown_contribution", "unknown_field"])
def test_ambiguous_or_unbound_frame_declarations_are_rejected(change):
    data = declaration()
    if change == "duplicate_frame":
        data["frames"].append({**copy.deepcopy(data["frames"][0]), "id": "alias"})
    elif change == "wrong_origin":
        data["frames"][0]["run_id"] = "1:h0"
    elif change == "unknown_contribution":
        data["frames"][0]["contribution_ids"] = ["missing"]
    else:
        data["frames"][0]["trust_me"] = True
    with pytest.raises(AttributeLedgerError):
        AttributeLedger(data)


def test_missing_frame_or_actor_returns_explicit_query_issue():
    ledger = AttributeLedger(declaration())
    assert read(ledger, frame="future")["issues"][0]["code"] == "attribute_frame_missing"
    assert read(ledger, actor="missing")["issues"][0]["code"] == "attribute_basis_missing"


def test_receipt_and_work_limits_refuse_finite_excess_without_a_value(monkeypatch):
    limits = {'query_work': 1}
    ledger = AttributeLedger(attach(declaration(), contribution()), limits=limits)
    receipt = read(ledger)
    assert receipt["status"] == "pending" and "value" not in receipt
    assert receipt["issues"][0]["code"] == "attribute_work_limit"
    limits['query_work'] = 40000
    limits['receipt_bytes'] = 100
    receipt = read(AttributeLedger(declaration(), limits=limits))
    assert receipt["status"] == "pending" and "value" not in receipt
    assert receipt["issues"][0]["code"] == "attribute_receipt_limit"


def test_cached_reads_do_not_repeat_work_but_distinct_queries_share_budget(monkeypatch):
    limits = {'query_work': 2}
    ledger = AttributeLedger(attach(declaration(), contribution()), limits=limits)
    original = read(ledger)
    assert original["value"] == 340
    assert read(ledger) == original
    assert read(ledger, "nonextra")["issues"][0]["code"] == "attribute_work_limit"


@pytest.mark.parametrize("limit", ["initials", "contributions", "frames", "declaration_bytes"])
def test_constructor_limits_are_enforced_before_unbounded_accounting(monkeypatch, limit):
    limits = {limit: 0}
    with pytest.raises(AttributeLedgerError):
        AttributeLedger(attach(declaration(), contribution()), limits=limits)


def test_unqualified_context_on_an_inactive_frame_does_not_block_current_basis():
    data = declaration()
    data["contributions"] = [contribution(constraints=["quota"], inclusion="unknown")]
    assert read(AttributeLedger(data), "nonextra")["value"] == 100


def test_legal_negative_extra_can_make_nonextra_greater_than_total():
    data = declaration()
    data["initials"][0].update(total=100, nonextra=200)
    attach(data, contribution("reduction", -40, "excluded"))
    ledger = AttributeLedger(data)
    assert read(ledger)["value"] == 60
    assert read(ledger, "nonextra")["value"] == 200


def test_capabilities_do_not_claim_game_source_or_scheduler_approval():
    capabilities = attribute_ledger_capabilities()
    assert capabilities["contract"] == "attribute-ledger-v1"
    assert capabilities["expressions"] == "unsupported_in_l1"
    assert capabilities["frame_authority"] == "caller_supplied_frame"
