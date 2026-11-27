"""Typed final-value explanation assembly and saved consistency checks.

This layer joins already executed evidence. It never reads SQLite, advances a
world, evaluates a character rule or solves a second damage formula.
"""
from __future__ import annotations

import copy
import json

from genshin_engine.damage_explanations import CONTRACT, TABLES, digest, encoded

RECEIPT_CONTRACT = "damage-explanation-receipt-v1"
SECTIONS = ("identity", "panel", "modifiers", "formula", "normalization", "outcome", "evidence")
MESSAGE = "逐击解释与当前命中、来源或计算回执不一致，请重新计算。"


def explanation_contract():
    return {"contract": CONTRACT, "result_field": "damage_explanations",
            "receipt_contract": RECEIPT_CONTRACT, "view": "damage_explanations",
            "sections": list(SECTIONS), "default_sections": ["identity", "outcome"],
            "scope": "captured_consumed_inputs_and_saved_consistency_not_game_truth",
            "legacy": "missing; never reconstruct from total damage",
            "sources": ["engine_formula", "fixed_override", "external"],
            "read_only": True}


def _intern(document, table, value):
    key = digest(value)
    document[table][key] = value
    return key


def _lineages(result, report):
    links = {}
    for row in result.get("mechanics_trace", []):
        if (row.get("type") in ("hit", "reaction_damage") and row.get("cycle", 0) == 0
                and isinstance(row.get("event_id"), str)):
            links[row["event_id"]] = {k: row[k] for k in ("action_id", "ability_id", "component_id",
                "run_id", "cycle", "target_id", "area_id") if row.get(k) is not None}
    for compiled in result.get("action_event_compilations", []):
        for row in compiled.get("generated", []):
            if row.get("hit_id"):
                links.setdefault(row["hit_id"], {}).update({k: row[k] for k in
                    ("action_id", "ability_id", "prototype_hit_id", "channel_id", "ordinal") if k in row})
    for hit in result.get("hits", []):
        if isinstance(hit.get("target_id"), str):
            links.setdefault(hit["id"], {})["target_id"] = hit["target_id"]
    template = report.get("plan", {}).get("resolved_template", {})
    for hit in template.get("hits", []):
        extension = hit.get("extensions", {})
        special = extension.get("special_reaction", {})
        binding = (special.get("ability_profile") or {}).get("ability_binding") or {}
        elemental = extension.get("elemental", {})
        link = links.setdefault(hit.get("id"), {})
        link.update({k: binding[k] for k in ("ability_id", "component_id", "variant_id", "effective_level") if k in binding})
        if special.get("component") is not None:
            link["component_id"] = special["component"]
        link.update({k: elemental[k] for k in ("action_id", "target_id", "area_id", "cycle") if elemental.get(k) is not None})
        if special.get("request_id") or elemental.get("special_reaction_request_id"):
            link["source_event_id"] = special.get("request_id") or elemental["special_reaction_request_id"]
        if elemental.get("parent_event_id") is not None:
            link["parent_event_id"] = elemental["parent_event_id"]
    rotation = result.get("rotation_check", {}).get("resolved_rotation_model") or report.get("compiled_rotation_model") or {}
    actions = {row["id"]: row for row in rotation.get("actions", [])}
    for link in links.values():
        action = actions.get(link.get("action_id"), {})
        if action.get("ability_id"):
            link.setdefault("ability_id", action["ability_id"])
    return links


def _runtime_evidence(result):
    effects, samples = {}, {}
    for index, row in enumerate(result.get("mechanics_trace", [])):
        if row.get("projected_effect_id") and row.get("cycle", 0) == 0:
            effects[(row.get("event_id"), row["projected_effect_id"])] = (index, row)
    for index, row in enumerate(result.get("sampling_trace", [])):
        if row.get("cycle", 0) == 0:
            if row.get("projected_effect_id"):
                samples[(row.get("event_id"), row["projected_effect_id"])] = (index, row)
            if row.get("rule_id") and row.get("effect_id"):
                samples[(row.get("event_id"), row["rule_id"], row["effect_id"])] = (index, row)
    return effects, samples


def assemble_damage_explanations(plan, result, report, *, maximum_bytes=None):
    """Called on a fresh result after fixed-value overrides, before any seal."""
    document = result.get("damage_explanations")
    if not isinstance(document, dict) or document.get("contract") != CONTRACT:
        raise ValueError(MESSAGE)
    links = _lineages(result, report)
    effects, samples = _runtime_evidence(result)
    rules = {row["id"]: row for row in (report.get("mechanics_model") or {}).get("rules", [])}
    attribute_receipts = result.get("attribute_ledger", {}).get("receipts", [])
    receipts_by_event = {}
    for index, item in enumerate(attribute_receipts):
        query = item.get("query", {})
        event_id = query.get("frame", {}).get("event_id")
        if event_id:
            receipts_by_event.setdefault(event_id, []).append((index, item))
    hits = {hit.get("explanation_ref"): hit for hit in result.get("hits", []) if hit.get("explanation_ref")}
    for key, event in document["events"].items():
        hit = hits[key]
        source = copy.deepcopy(document["sources"][event["source_ref"]])
        source.update(links.get(hit.get("id"), {}))
        if source.get("action_id"):
            source["lineage_status"] = "execution_bound"
        event["source_ref"] = _intern(document, "sources", source)
        panel = copy.deepcopy(document["panels"][event["panel_ref"]])
        if result.get("mechanics_trace"):
            panel["sampling"] = "materialized_runtime_inputs; see per-domain receipts"
            panel["evaluation_t"] = panel["sample_t"]
            panel["sample_t"] = None
        panel["attribute_receipt_refs"] = [f"/attribute_ledger/receipts/{index}"
            for index, item in receipts_by_event.get(hit.get("id"), [])
            if item.get("query", {}).get("actor") == hit["character"]
            and item.get("query", {}).get("frame", {}).get("t_us", 0) <= round(hit["t"] * 1_000_000)]
        event["panel_ref"] = _intern(document, "panels", panel)
        modifier_refs = []
        for modifier_id in event["modifier_refs"]:
            modifier = copy.deepcopy(document["modifiers"][modifier_id])
            identity = (hit.get("id"), modifier.get("effect_id"))
            trace = effects.get(identity)
            sample = samples.get(identity)
            if sample is None and trace:
                value = trace[1]
                sample = samples.get((hit.get("id"), value.get("rule_id"), value.get("effect_id")))
            if trace:
                index, value = trace
                modifier["execution"] = {k: value[k] for k in ("rule_id", "effect_id", "source_id", "instance_id",
                    "domain", "sample_t", "layers", "attribute_receipt_id", "attribute_node_id") if k in value}
                modifier["execution"]["trace_ref"] = f"/mechanics_trace/{index}"
                provider = rules.get(value.get("rule_id"), {}).get("owner")
                if provider is not None:
                    modifier["execution"]["provider"] = provider
                name = rules.get(value.get("rule_id"), {}).get("name_zh")
                if name:
                    modifier["name_zh"] = name
            if sample:
                index, value = sample
                modifier["origin"] = "sampled_expression"
                modifier["sampling"] = {k: value[k] for k in ("character", "stat", "domain", "sample_t", "expression",
                    "base_value", "source_value", "value", "attribute_receipt_id", "projection_receipt_id") if k in value}
                modifier["sampling"]["trace_ref"] = f"/sampling_trace/{index}"
            modifier_refs.append(_intern(document, "modifiers", modifier))
        event["modifier_refs"] = modifier_refs
        if hit.get("calculation_override"):
            override = hit["calculation_override"]
            event["outcome"] = {"origin": "fixed_override", "expected": hit["expected"], "crit": None,
                "count": hit.get("count", 1), "result_weight": hit.get("result_weight", 1),
                "automatic_expected": override["automatic_expected"], "override_id": override["override_id"],
                "precision": copy.deepcopy(override.get("precision")), "state_effects": "none"}
            event["evidence"]["override_ref"] = "/calculation_computed_value_overrides"
            event["normalization"] = {"status": "not_applicable", "reason": "fixed_value_not_derived_from_effective_panel"}
            event["formula_scope"] = "automatic_baseline_only"
    for item in plan.get("external_damage_supplements", []):
        key = "external:" + item["id"]
        source = {"character": item["character"], "skill": item["skill"], "action_id": item.get("action_id"),
                  "ability_id": None, "component_id": None, "lineage_status": "external_declared"}
        document["events"][key] = {"event_id": item["event_id"], "t": item["t"], "character": item["character"],
            "skill": item["skill"], "source_ref": _intern(document, "sources", source), "panel_ref": None,
            "modifier_refs": [], "formula_ref": None, "checkpoints": {}, "zones": {},
            "normalization": {"status": "not_applicable", "reason": "externally_calculated"},
            "outcome": {"origin": "external", "expected": item["expected"], "crit": None,
                        "count": 1, "result_weight": 1, "precision": copy.deepcopy(item.get("precision"))},
            "evidence": {k: copy.deepcopy(item[k]) for k in ("id", "source_refs", "reason", "calculation_note", "non_overlap_note")}}
        document["coverage"]["eligible"] += 1
        document["coverage"]["explained"] += 1
    result["damage_explanation_contract"] = CONTRACT
    report["damage_explanation_contract"] = CONTRACT
    # A root execution identity binds all deduplicated tables without cloning
    # either the compiled model or the full attribute/sampling ledger per hit.
    document["execution_binding"] = {k: report[k] for k in
        ("input_fingerprint", "output_fingerprint", "source_fingerprint")}
    document["execution_scope"] = {"mechanics_status": result.get("mechanics_status"),
        "complete_window": result.get("mechanics", {}).get("complete_first_cycle"),
        "damage_interpretation": result.get("rotation_check", {}).get("damage_interpretation", {}).get("kind"),
        "note": "解释完整仅针对本结果已报告数值，不证明动作或角色机制完整。"}
    _bound_evidence(document, result, maximum_bytes=maximum_bytes)
    result["damage_explanation_fingerprint"] = digest(document)


def _bound_evidence(document, result, *, maximum_bytes=None):
    """Drop only optional evidence, explicitly and before result/proof sealing."""
    maximum = document["budget"]["maximum_bytes"]
    envelope_limited = maximum_bytes is not None
    if envelope_limited:
        maximum = min(maximum, maximum_bytes)
        document["budget"].update(envelope_document_target_bytes=maximum_bytes,
                                  minimum_metadata_bytes=0, optional_payload_bytes=0)
    def collect_tables():
        referenced = {name: set() for name in TABLES}
        for event in document["events"].values():
            for key, table in (("source_ref", "sources"), ("panel_ref", "panels"), ("formula_ref", "formulas")):
                if event[key] is not None:
                    referenced[table].add(event[key])
            referenced["modifiers"].update(event["modifier_refs"])
        for table in TABLES:
            document[table] = {key: value for key, value in document[table].items() if key in referenced[table]}
    collect_tables()
    size = len(encoded(document))
    if size > maximum and document["events"]:
        document["status"] = "partial"
        reason = "calculated_envelope_capacity" if envelope_limited else "explanation_capacity"
        document["coverage"]["reasons"] = sorted(set(document["coverage"]["reasons"]) | {reason})
        events, tables = document["events"], {name: document[name] for name in TABLES}
        retained = {name: {} for name in TABLES}
        document.update(retained, events={})
        document["coverage"].update(explained=0, omitted=document["coverage"]["eligible"])
        document["budget"]["accounted_bytes"] = 0
        used = len(encoded(document))
        # Exact JSON-entry deltas, plus a proven decimal-width allowance for
        # the two scalar counters that may grow from zero. No arbitrary 4 KiB
        # reserve, full table charge or per-event whole-document encoding.
        scalar_growth = (len(str(document["coverage"]["eligible"])) - 1
                         + (len(str(max(size, 1))) - 1) * (3 if envelope_limited else 1))
        costs = {name: {} for name in TABLES}
        for key, event in events.items():
            refs = {name: set() for name in TABLES}
            for field, table in (("source_ref", "sources"), ("panel_ref", "panels"), ("formula_ref", "formulas")):
                if event[field] is not None:
                    refs[table].add(event[field])
            refs["modifiers"].update(event["modifier_refs"])
            additions = {name: refs[name] - retained[name].keys() for name in TABLES}
            cost = len(encoded({key: event})) - 2 + bool(document["events"])
            for name in TABLES:
                for ref in additions[name]:
                    if ref not in costs[name]:
                        costs[name][ref] = len(encoded({ref: tables[name][ref]})) - 2
                    cost += costs[name][ref]
                cost += max(0, len(additions[name]) - (not retained[name]))
            if used + cost + scalar_growth <= maximum:
                document["events"][key] = event
                for name in TABLES:
                    retained[name].update((ref, tables[name][ref]) for ref in additions[name])
                used += cost
        for hit in result.get("hits", []):
            if hit.get("explanation_ref") not in document["events"]:
                hit.pop("explanation_ref", None)
    document["coverage"]["explained"] = len(document["events"])
    document["coverage"]["omitted"] = document["coverage"]["eligible"] - len(document["events"])
    if envelope_limited:
        minimal = {**document, **{name: {} for name in TABLES}, "events": {},
                   "coverage": {**document["coverage"], "explained": 0,
                                "omitted": document["coverage"]["eligible"]},
                   "budget": {**document["budget"], "accounted_bytes": 0,
                              "minimum_metadata_bytes": 0, "optional_payload_bytes": 0}}
        for _ in range(4):
            size = len(encoded(minimal))
            if minimal["budget"]["minimum_metadata_bytes"] == size:
                break
            minimal["budget"].update(minimum_metadata_bytes=size, accounted_bytes=size)
        document["budget"]["minimum_metadata_bytes"] = minimal["budget"]["minimum_metadata_bytes"]
    # At most two decimal-width adjustments; this never iterates over events.
    for _ in range(4):
        size = len(encoded(document))
        payload = max(0, size - document["budget"]["minimum_metadata_bytes"]) if envelope_limited else None
        if (document["budget"]["accounted_bytes"] == size
                and (not envelope_limited or document["budget"]["optional_payload_bytes"] == payload)):
            break
        document["budget"]["accounted_bytes"] = size
        if envelope_limited:
            document["budget"]["optional_payload_bytes"] = payload


def _hit_projection(result):
    return [{k: row[k] for k in ("id", "t", "character", "skill", "expected", "crit", "explanation_ref") if k in row}
            for row in result.get("hits", [])]


def _explanation_receipt(result, document_digest):
    return {"contract": RECEIPT_CONTRACT, "scope": "saved_damage_explanation_consistency_no_game_truth",
            "document_fingerprint": document_digest,
            "hits_fingerprint": digest(_hit_projection(result)),
            "external_fingerprint": digest(result.get("external_damage", {}).get("items", []))}


def make_explanation_receipt(result):
    return _explanation_receipt(result, digest(result["damage_explanations"]))


def _saved_ref(result, reference, prefix):
    if not isinstance(reference, str) or not reference.startswith(prefix + "/"):
        raise ValueError(MESSAGE)
    suffix = reference[len(prefix) + 1:]
    if not suffix.isascii() or not suffix.isdecimal():
        raise ValueError(MESSAGE)
    selected = result
    for part in prefix.strip("/").split("/"):
        selected = selected[part]
    return selected[int(suffix)]


def _validate_evidence(result, document, event):
    for reference in event["modifier_refs"]:
        modifier = document["modifiers"][reference]
        for field, prefix in (("execution", "/mechanics_trace"), ("sampling", "/sampling_trace")):
            evidence = modifier.get(field)
            if evidence is None:
                continue
            original = _saved_ref(result, evidence["trace_ref"], prefix)
            if original.get("event_id") != event["event_id"] or original.get("cycle", 0) != 0:
                raise ValueError(MESSAGE)
            if any(value != original.get(key) for key, value in evidence.items()
                   if key not in ("trace_ref", "provider")):
                raise ValueError(MESSAGE)
            if field == "execution" and original.get("value") != modifier["value"]:
                raise ValueError(MESSAGE)
    if event["panel_ref"] is not None:
        panel = document["panels"][event["panel_ref"]]
        for ref in panel.get("attribute_receipt_refs", []):
            item = _saved_ref(result, ref, "/attribute_ledger/receipts")
            if item.get("query", {}).get("actor") != event["character"]:
                raise ValueError(MESSAGE)


def validate_damage_explanations(result, receipt=None, *, report=None):
    document = result.get("damage_explanations")
    receipt_signal = isinstance(receipt, dict) and "damage_explanations" in receipt
    required = (result.get("damage_explanation_contract") == CONTRACT
                or isinstance(report, dict) and report.get("damage_explanation_contract") == CONTRACT
                or receipt_signal)
    family_signal = required or any(key in result for key in (
        "damage_explanations", "damage_explanation_fingerprint", "damage_explanation_contract"))
    if document is None:
        if family_signal:
            raise ValueError(MESSAGE)
        return "missing"
    try:
        if not isinstance(document, dict) or document.get("contract") != CONTRACT or document.get("schema_version") != 1:
            raise ValueError(MESSAGE)
        document_digest = digest(document)
        if result.get("damage_explanation_fingerprint") != document_digest:
            raise ValueError(MESSAGE)
        for table in TABLES:
            if not isinstance(document.get(table), dict):
                raise TypeError(MESSAGE)
            for key, value in document[table].items():
                if key != digest(value):
                    raise ValueError(MESSAGE)
        events = document["events"]
        if not isinstance(events, dict):
            raise TypeError(MESSAGE)
        if document["status"] not in ("complete", "partial"):
            raise ValueError(MESSAGE)
        hits = {h.get("explanation_ref"): h for h in result.get("hits", []) if h.get("explanation_ref")}
        if len(hits) != sum(bool(h.get("explanation_ref")) for h in result.get("hits", [])):
            raise ValueError(MESSAGE)
        external = {"external:" + item["id"]: item for item in result.get("external_damage", {}).get("items", [])}
        for key, event in events.items():
            for field, table in (("source_ref", "sources"), ("panel_ref", "panels"), ("formula_ref", "formulas")):
                if event.get(field) is not None and event[field] not in document[table]:
                    raise ValueError(MESSAGE)
            if any(ref not in document["modifiers"] for ref in event["modifier_refs"]):
                raise ValueError(MESSAGE)
            _validate_evidence(result, document, event)
            origin = event["outcome"]["origin"]
            row = external.get(key) if origin == "external" else hits.get(key)
            if row is None or any(event.get(k) != row.get(k) for k in ("t", "character", "skill")):
                raise ValueError(MESSAGE)
            if event["event_id"] != row.get("event_id" if origin == "external" else "id"):
                raise ValueError(MESSAGE)
            if event["outcome"]["expected"] != row["expected"] or event["outcome"]["crit"] != row.get("crit"):
                raise ValueError(MESSAGE)
            if origin not in ("external", "engine_formula", "fixed_override"):
                raise ValueError(MESSAGE)
            if origin == "fixed_override" and event["outcome"]["override_id"] != row["calculation_override"]["override_id"]:
                raise ValueError(MESSAGE)
            if origin != "external":
                if event["outcome"].get("count") != row.get("count", 1) or event["outcome"].get("result_weight") != row.get("result_weight", 1):
                    raise ValueError(MESSAGE)
                panel = document["panels"][event["panel_ref"]]
                if panel.get("character") != row["character"]:
                    raise ValueError(MESSAGE)
                actual = row.get("attribute_inputs", {})
                if any(panel["inputs"].get(key) != actual[key] for key in ("scaling", "em") if key in actual and key in panel["inputs"]):
                    raise ValueError(MESSAGE)
                baseline = (event["checkpoints"]["expected_per_count"] * row.get("count", 1)
                            * row.get("result_weight", 1))
                actual_baseline = (row["calculation_override"]["automatic_expected"]
                                   if origin == "fixed_override" else row["expected"])
                if baseline != actual_baseline:
                    raise ValueError(MESSAGE)
        coverage = document["coverage"]
        if (coverage["eligible"] != len(result.get("hits", [])) + len(external)
                or coverage["explained"] != len(events) or coverage["omitted"] != coverage["eligible"] - len(events)
                or set(hits) - set(events)):
            raise ValueError(MESSAGE)
        if document["status"] == "complete" and (coverage["omitted"] or coverage["reasons"]):
            raise ValueError(MESSAGE)
        if document["status"] == "partial" and not coverage["reasons"]:
            raise ValueError(MESSAGE)
        if required:
            if (not isinstance(receipt, dict)
                    or receipt.get("damage_explanations") != _explanation_receipt(result, document_digest)):
                raise ValueError(MESSAGE)
            for field, value in document["execution_binding"].items():
                if receipt.get(field) != value or report is not None and report.get(field) != value:
                    raise ValueError(MESSAGE)
        return document["status"]
    except (KeyError, TypeError, AttributeError, IndexError, OverflowError, RecursionError) as exc:
        raise ValueError(MESSAGE) from exc


def explanation_sections(document, event, sections, *, budget=None, maximum_bytes=256 * 1024):
    """Resolve one event only; callers retain the original result fingerprint."""
    selected = {}
    used = 0
    def detached(value):
        nonlocal used
        from genshin_app.equipment.schema import PlanError
        remaining = maximum_bytes - used
        if remaining <= 0:
            raise PlanError("逐击解释超过响应限额，请缩小sections或limit", "review_size_limit")
        if budget is not None:
            from genshin_app.review_batch_json import prepay
            size = prepay(value, budget, maximum=remaining)
        else:
            size = 0
            for chunk in json.JSONEncoder(ensure_ascii=False, allow_nan=False).iterencode(value):
                size += len(chunk.encode("utf8"))
                if size > remaining:
                    raise PlanError("逐击解释超过响应限额，请缩小sections或limit", "review_size_limit")
        used += size + 16
        return copy.deepcopy(value)
    if "identity" in sections:
        selected["identity"] = detached(document["sources"][event["source_ref"]])
    if "panel" in sections:
        selected["panel"] = detached(document["panels"].get(event["panel_ref"]))
    if "modifiers" in sections:
        selected["modifiers"] = [detached(document["modifiers"][ref]) for ref in event["modifier_refs"]]
    if "formula" in sections:
        selected["formula"] = {"recipe": detached(document["formulas"].get(event["formula_ref"])),
            "checkpoints": detached(event["checkpoints"]), "zones": detached(event["zones"]),
            "scope": event.get("formula_scope", "effective_engine_formula")}
    for key in ("normalization", "outcome", "evidence"):
        if key in sections:
            selected[key] = detached(event[key])
    return selected
