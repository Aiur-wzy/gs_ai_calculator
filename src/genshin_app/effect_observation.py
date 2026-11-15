"""Sparse, read-only links from declarations to formula/credit evidence.

Presence of an identifier in a log is not proof of application. Only typed
execution rows and the numeric pipeline's captured modifiers establish edges.
"""
from __future__ import annotations

from collections import Counter


def _segment(value):
    return str(value or "").replace("~", "~0").replace("/", "~1")


def _component_key(value):
    fields = ("source_fingerprint", "provider", "part_id", "effect_id")
    if not isinstance(value, dict) or not all(isinstance(value.get(k), str) for k in fields):
        return None
    return tuple(value[k] for k in fields)


class _AttributeLinks:
    """Follow saved consumption edges, never evaluate expressions or infer links."""

    def __init__(self, result, record):
        self.record = record
        self.receipts, self.components, self.samples = {}, {}, {}
        self.unresolved, self.links = {}, set()
        ledger = result.get("attribute_ledger") or {}
        self.available = bool(ledger)
        self.conditions = ledger.get("condition_sources") or {}
        for index, receipt in enumerate(ledger.get("receipts", [])):
            identity = receipt.get("receipt_id")
            if identity:
                # Duplicate identities are ambiguous, even when their values agree.
                self.receipts[identity] = (index, receipt) if identity not in self.receipts else None
        for declaration in (ledger.get("declarations") or {}).get("effect_classifications", []):
            key = _component_key(declaration.get("component"))
            ref = declaration.get("effect_ref") or {}
            if ref.get("kind") == "rule":
                target = ("rule", ref.get("rule_id"), ref.get("effect_id"))
            elif ref.get("kind") == "manual_buff":
                target = ("buff", ref.get("buff_id"), ref.get("effect_id"))
            else:
                continue
            if key is not None:
                self.components.setdefault(key, set()).add(target)
        for index, item in enumerate(result.get("sampling_trace", [])):
            if item.get("cycle", 0) == 0 and item.get("projected_effect_id"):
                key = (item.get("event_id"), item["projected_effect_id"])
                self.samples[key] = (index, item) if key not in self.samples else None

    def issue(self, reason, reference, source_ref):
        key = (reason, reference, source_ref)
        self.unresolved.setdefault(key, {"reason": reason, "reference": reference,
                                        "resource": "result", "source_ref": source_ref})

    def modifier_roots(self, event, modifier, modifier_ref):
        """The actual consumed modifier establishes authority for these reads."""
        roots = []
        identity = (event.get("event_id"), modifier.get("effect_id"))
        saved = self.samples.get(identity)
        evidence = [(modifier.get("sampling") or {}, modifier_ref + "/sampling"),
                    (modifier.get("execution") or {}, modifier_ref + "/execution")]
        if saved:
            index, value = saved
            evidence.append((value, f"/sampling_trace/{index}"))
        elif identity in self.samples:
            self.issue("ambiguous_sampling_identity", str(identity), modifier_ref)
        for value, source in evidence:
            for key in ("projection_receipt_id", "attribute_receipt_id"):
                if value.get(key):
                    roots.append((value[key], source + "/" + key))
        return roots

    def panel_roots(self, event, panel):
        """A list of panel reads alone is not evidence that every read was used."""
        inputs = panel.get("inputs") or {}
        stats = ({panel.get("scaling_stat")} if "scaling" in inputs else set())
        if "em" in inputs:
            stats.add("em")
        roots = []
        for reference in panel.get("attribute_receipt_refs", []):
            prefix = "/attribute_ledger/receipts/"
            if not isinstance(reference, str) or not reference.startswith(prefix):
                continue
            # Resolve via the unique ID index, not an inferred actor/time join.
            suffix = reference[len(prefix):]
            if not suffix.isascii() or not suffix.isdecimal():
                continue
            item = self.by_index.get(int(suffix))
            if item is None:
                self.issue("missing_panel_receipt", reference, reference)
                continue
            query = item.get("query") or {}
            frame = query.get("frame") or {}
            if (query.get("purpose") == "damage_projection"
                    and query.get("actor") == event.get("character")
                    and query.get("stat") in stats
                    and frame.get("event_id") == event.get("event_id")
                    and frame.get("origin", 0) == 0):
                roots.append((item.get("receipt_id"), reference))
        return roots

    @property
    def by_index(self):
        if not hasattr(self, "_by_index"):
            self._by_index = {index: receipt for item in self.receipts.values()
                              if item is not None for index, receipt in [item]}
        return self._by_index

    def walk(self, roots, visited):
        pending = list(roots)
        while pending:
            identity, source_ref = pending.pop()
            if identity in visited:
                continue
            visited.add(identity)
            entry = self.receipts.get(identity)
            if entry is None:
                reason = "ambiguous_receipt" if identity in self.receipts else "missing_receipt"
                self.issue(reason, identity, source_ref)
                continue
            index, receipt = entry
            reference = f"/attribute_ledger/receipts/{index}"
            if receipt.get("status") != "known":
                self.issue("receipt_not_known", identity, reference)
                continue
            for ci, contribution in enumerate(receipt.get("contributions", [])):
                if contribution.get("decision") != "included":
                    continue
                path = reference + f"/contributions/{ci}"
                targets = self.components.get(_component_key(contribution.get("component")), set())
                if contribution.get("condition_ref"):
                    condition = self.conditions.get(contribution["condition_ref"]) or {}
                    component = contribution.get("component") or {}
                    frame = (receipt.get("query") or {}).get("frame") or {}
                    if (condition.get("fixed_source_id") and condition.get("effect_id")
                            and condition.get("source_fingerprint") == component.get("source_fingerprint")
                            and condition.get("source_id") == component.get("part_id")
                            and condition.get("effect_id") == component.get("effect_id")
                            and condition.get("actor") == component.get("recipient")
                            and condition.get("actual_event_run_id") == frame.get("run_id")):
                        targets = {("fixed", condition["fixed_source_id"], condition["effect_id"])}
                if len(targets) == 1:
                    record = self.record(*next(iter(targets)))
                    self.links.add((identity, ci))
                    yield record, {"receipt_ref": reference, "contribution_ref": path,
                                   "receipt_id": identity, "stat": contribution.get("stat"),
                                   "evaluated_value": contribution.get("evaluated_value"),
                                   "consumption_source_ref": source_ref}
                else:
                    self.issue("ambiguous_component" if targets else "unlinked_component", identity, path)
                for key in ("input_receipt_id", "application_base_receipt_id"):
                    if contribution.get(key):
                        pending.append((contribution[key], path + "/" + key))

    def summary(self):
        return {"status": ("partial" if self.unresolved else "complete") if self.available else "unavailable",
                "scope": "exact_saved_formula_receipt_dependencies_only",
                "contribution_link_count": len(self.links), "unresolved_count": len(self.unresolved),
                "samples": list(self.unresolved.values())[:8]}


def observe_effects(plan, result, report=None):
    records, projection = {}, {}

    def row(kind, parent, effect_id, **fields):
        key = (kind, parent, effect_id)
        if key not in records:
            records[key] = {"kind": kind, "parent_id": parent, "effect_id": effect_id,
                "applied_count": 0, "rejected_count": 0, "formula_consumed_count": 0,
                "direct_formula_consumed_count": 0, "attribute_dependency_consumed_count": 0,
                "outside_report_count": 0,
                "samples": [], "rejection_reasons": {}, **fields}
        return records[key]

    report = report or {}
    mechanics = report.get("mechanics_model") or plan.get("mechanics_model") or {}
    mechanic_resource = "report" if report.get("mechanics_model") else "plan"
    mechanic_path = "/mechanics_model"
    for index, rule in enumerate(mechanics.get("rules", [])):
        for ei, effect in enumerate(rule.get("effects", [])):
            row("rule", rule.get("id"), effect.get("id"), source_id=rule.get("source_id"), owner=rule.get("owner"),
                input_resource=mechanic_resource,
                input_ref=f"{mechanic_path}/rules/{index}/effects/{ei}", benefit_type_id=effect.get("benefit_type_id"),
                source_status=rule.get("status"), beneficiaries=rule.get("beneficiaries"),
                condition_refs={"trigger": f"{mechanic_path}/rules/{index}/trigger", "effect_filter": f"{mechanic_path}/rules/{index}/effects/{ei}/filter"})
    for index, buff in enumerate((plan.get("manual_template") or {}).get("buffs", [])):
        for ei, effect in enumerate(buff.get("effects", [])):
            row("buff", buff.get("id"), effect.get("id"), source=buff.get("source"),
                input_resource="plan", input_ref=f"/manual_template/buffs/{index}/effects/{ei}", benefit_type_id=effect.get("benefit_type_id"))
    for index, source in enumerate(result.get("fixed_effect_sources", mechanics.get("fixed_effect_sources", []))):
        for ei, effect in enumerate(source.get("effects", [])):
            row("fixed", source.get("id"), effect.get("id"), source=source.get("source"),
                input_resource="result" if "fixed_effect_sources" in result else mechanic_resource,
                input_ref=("" if "fixed_effect_sources" in result else mechanic_path) +
                    f"/fixed_effect_sources/{index}/effects/{ei}", benefit_type_id=effect.get("benefit_type_id"))

    def sample(record, value):
        if len(record["samples"]) < 3:
            record["samples"].append({"resource": "result", **value})
        elif value.get("stage") not in {item.get("stage") for item in record["samples"]}:
            # Keep a bounded sample of each conclusion, not the first three
            # rejections hiding the later actual formula/dependency receipt.
            stages = [item.get("stage") for item in record["samples"]]
            replace = next((i for i in reversed(range(len(stages))) if stages.count(stages[i]) > 1), None)
            if replace is not None:
                record["samples"][replace] = {"resource": "result", **value}

    audit_occurrences = {(event.get("event_id"), event.get("t"))
                         for event in result.get("mechanics_trace", [])
                         if event.get("type") in ("hit", "reaction_damage") and event.get("cycle", 0) != 0}
    dispositions = {(event.get("event_id"), event.get("t")): event.get("report_disposition")
                    for event in result.get("mechanics_trace", [])
                    if event.get("type") in ("hit", "reaction_damage") and event.get("cycle", 0) == 0}
    for i, event in enumerate(result.get("mechanics_trace", [])):
        if (event.get("cycle", 0) != 0 or "cycle" not in event
                and (event.get("event_id"), event.get("t")) in audit_occurrences):
            continue
        kind = event.get("type")
        if kind not in {"effect_applied", "manual_effect_applied", "effect_rejected", "special_reaction_effect_applied"}:
            continue
        if kind == "special_reaction_effect_applied":
            record = row("special_reaction", event.get("area_id"), event.get("effect_id"),
                         reaction_type=event.get("reaction_type"), benefit_type_id=event.get("benefit_type_id"))
            projection[(event.get("event_id"), event.get("effect_id"))] = record
        else:
            record = row("rule" if event.get("rule_id") else "buff", event.get("rule_id", event.get("buff_id")), event.get("effect_id"))
        if kind == "effect_rejected":
            record["rejected_count"] += 1
            reason = event.get("reason", "unspecified")
            record["rejection_reasons"][reason] = record["rejection_reasons"].get(reason, 0) + 1
            sample(record, {"event_id": event.get("event_id"), "stage": "rejected", "reason": reason,
                            "ref": f"/mechanics_trace/{i}"})
        else:
            record["applied_count"] += 1
            disposition = dispositions.get((event.get("event_id"), event.get("t")))
            if disposition == "outside_input_window":
                record["outside_report_count"] += 1
            sample(record, {"event_id": event.get("event_id"), "stage": "applied", "ref": f"/mechanics_trace/{i}",
                            "report_disposition": disposition})
        if event.get("projected_effect_id"):
            projection[(event.get("event_id"), event["projected_effect_id"])] = record

    for i, event in enumerate(result.get("target_condition_trace", [])):
        if event.get("cycle", 0) != 0:
            continue
        record = row("fixed", event.get("fixed_source_id"), event.get("effect_id"))
        applied = event.get("applied") is True
        record["applied_count" if applied else "rejected_count"] += 1
        if not applied:
            reason = event.get("reason", "unspecified")
            record["rejection_reasons"][reason] = record["rejection_reasons"].get(reason, 0) + 1
        sample(record, {"event_id": event.get("event_id"), "stage": "applied" if applied else "rejected",
                        "reason": event.get("reason"), "ref": f"/target_condition_trace/{i}",
                        "target_sample_ref": event.get("target_sample_ref")})
        if event.get("projected_effect_id"):
            projection[(event.get("event_id"), event["projected_effect_id"])] = record

    explanation = result.get("damage_explanations") or {}
    modifiers = explanation.get("modifiers") or {}
    attributes = _AttributeLinks(result, row)
    for key, event in (explanation.get("events") or {}).items():
        origin = (event.get("outcome") or {}).get("origin")
        direct, indirect, visited = set(), {}, set()
        panel = (explanation.get("panels") or {}).get(event.get("panel_ref"), {})
        roots = []
        for ref in event.get("modifier_refs", []):
            modifier = modifiers.get(ref)
            if not isinstance(modifier, dict):
                continue
            source = modifier.get("execution") or {}
            record = projection.get((event.get("event_id"), modifier.get("effect_id")))
            if record is None:
                record = row("rule" if source.get("rule_id") else "buff",
                             source.get("rule_id", modifier.get("buff_id")), source.get("effect_id", modifier.get("effect_id")))
            if origin != "engine_formula":
                sample(record, {"event_id": event.get("event_id"), "stage": "automatic_baseline_only",
                                "ref": "/damage_explanations/events/" + _segment(key)})
                continue
            record_key = (record["kind"], record["parent_id"], record["effect_id"])
            if record_key not in direct:
                record["direct_formula_consumed_count"] += 1
                direct.add(record_key)
            modifier_ref = "/damage_explanations/modifiers/" + _segment(ref)
            roots.extend(attributes.modifier_roots(event, modifier, modifier_ref))
            capped = (modifier.get("zone") == "crit_rate" and (panel.get("inputs") or {}).get("crit_rate_raw", 0) >= 1)
            sample(record, {"event_id": event.get("event_id"), "stage": "formula_consumed",
                            "ref": "/damage_explanations/events/" + _segment(key),
                            "modifier_ref": modifier_ref,
                            "source_ref": "/damage_explanations/sources/" + _segment(event.get("source_ref")),
                            "panel_ref": "/damage_explanations/panels/" + _segment(event.get("panel_ref")),
                            "source_id": source.get("source_id"), "provider": source.get("provider"),
                            "beneficiary": event.get("character"),
                            "crit_rate_at_cap": capped, "marginal_gain": "not_measured"})
        if origin == "engine_formula":
            roots.extend(attributes.panel_roots(event, panel))
            for record, evidence in attributes.walk(roots, visited):
                record_key = (record["kind"], record["parent_id"], record["effect_id"])
                indirect.setdefault(record_key, evidence)
            for record_key in indirect.keys() - direct:
                record = records[record_key]
                record["attribute_dependency_consumed_count"] += 1
                sample(record, {"event_id": event.get("event_id"), "stage": "attribute_dependency_consumed",
                                "ref": "/damage_explanations/events/" + _segment(key),
                                "beneficiary": event.get("character"), **indirect[record_key]})
            for record_key in direct | indirect.keys():
                records[record_key]["formula_consumed_count"] += 1
    complete = explanation.get("status") == "complete"
    for record in records.values():
        record["state"] = ("formula_consumed" if record["formula_consumed_count"] else
                           "outside_report_window" if record["outside_report_count"] and
                           record["outside_report_count"] == record["applied_count"] else
                           "applied_no_formula_evidence" if record["applied_count"] else
                           "rejected" if record["rejected_count"] else
                           "no_saved_consumption" if complete else "unknown")
        record["evidence_scope"] = "declared_input_and_saved_execution; game_facts_not_certified"
        record["counting_unit"] = "formula: reported_damage_event; application/rejection: cycle_0_attempt"
    resources = []
    on_field = None
    for i, event in enumerate((result.get("rotation_check") or {}).get("event_log", [])):
        if event.get("type") == "action" and event.get("status") == "passed":
            on_field = event.get("frontline_after", on_field)
        if not isinstance(event.get("energy"), list):
            continue
        for credit in event["energy"]:
            resources.append({"event_id": event.get("id"), "kind": event.get("type"), "t": event.get("t"),
                "recipient": credit.get("character"), "gain": credit.get("gain"), "before": credit.get("before"),
                "after": credit.get("after"), "overflow": credit.get("overflow"),
                "on_field": credit.get("on_field"),
                "declared_active_character": event.get("frontline_before", on_field),
                "cause": event.get("resource_cause"), "resource": "result", "ref": f"/rotation_check/event_log/{i}",
                "state": "credit_recorded", "source_legality": "not_independently_certified"})
    externals = [{"id": row.get("id"), "event_id": row.get("event_id"), "action_id": row.get("action_id"),
                  "state": "external_declared", "formula_consumption": "not_engine_calculated",
                  "input_resource": "plan", "input_ref": f"/external_damage_supplements/{i}"}
                 for i, row in enumerate(plan.get("external_damage_supplements", []))]
    return {"effects": list(records.values()), "resource_credits": resources, "external": externals,
            "attribute_dependencies": attributes.summary(),
            "summary": {"effect_count": len(records), "states": dict(Counter(r["state"] for r in records.values())),
                        "attribute_dependency_effect_count": sum(bool(r["attribute_dependency_consumed_count"]) for r in records.values()),
                        "attribute_dependency_observation_status": attributes.summary()["status"],
                        "attribute_dependency_unresolved_count": len(attributes.unresolved),
                        "resource_credit_count": len(resources), "external_count": len(externals)},
            "interpretation": "触发、应用和进入公式分别计数；同一伤害的直接与属性依赖贡献去重。封顶或溢出不等于未生效；不据记录缺失证明游戏中没有该效果。"}
