"""Conservative transitive ordinary-attribute footprints for a contact group.

No callback is executed here. Historical reads are immutable; current reads
follow the declared providers/recipients and source qualification, not IDs of
particular characters. The real attribute ledger still checks actual cycles.
"""
from __future__ import annotations

from genshin_engine.beneficiary_selection import effective_recipient, position_matches, select_recipients
from genshin_engine.registry import BENEFIT_TYPES


def output_stat(effect):
    if effect['benefit_type_id'] == 'benefit.energy_recharge.all':
        return 'energy_recharge'
    info = BENEFIT_TYPES.get(effect["benefit_type_id"], {})
    zone = info.get("zone")
    if zone == "em":
        return "em"
    if zone == "energy_recharge":
        return "energy_recharge"
    if zone in ("stat_add", "stat_pct"):
        return info.get("stat")
    return None


def _classification(runtime, rule, effect):
    graph = runtime.attribute_runtime
    if graph is None:
        return "unknown"
    row = graph.classifications.get(("rule", rule["id"], effect["id"]), {})
    if row.get("status") not in ("assumed", "confirmed", "approved"):
        return "unknown"
    return row.get("nonextra_inclusion", "unknown")


def _current_candidates(runtime, rule):
    frontline = getattr(runtime, 'frontline', None)
    recipients = select_recipients(rule, runtime.data['chars'], frontline)
    return {actor for actor in recipients if position_matches(rule, actor, frontline)}


def _active_recipients(runtime, rule):
    instances = getattr(runtime, 'state', {}).get(rule['id'], {}).get('instances', [])
    return {actor for instance in instances for actor in instance['beneficiaries']
            if effective_recipient(rule, instance, actor, getattr(runtime, 'frontline', None))}


def package_attribute_reads(runtime, actor, *, candidate_rule_ids=(), candidate_recipients=None):
    """Ordinary Swirl reads its trigger's EM, including current conversions."""
    result, todo = set(), [(actor, "em", "total")]
    while todo:
        node = todo.pop()
        if node in result:
            continue
        result.add(node)
        recipient, stat, basis = node
        if basis == "base":
            continue
        if runtime.attribute_runtime:
            runtime.attribute_runtime.ledger.reserve_capture_work(1 + len(runtime.rules))
        for rule in runtime.rules.values():
            recipients = _active_recipients(runtime, rule)
            if rule['id'] in candidate_rule_ids:
                recipients |= (set(candidate_recipients.get(rule['id'], ())) if candidate_recipients is not None
                               else _current_candidates(runtime, rule))
            if recipient not in recipients:
                continue
            if rule.get("quota") or rule.get("resource_gate"):
                continue
            for effect in rule["effects"]:
                if (effect["domain"] == "enemy" or effect.get("filter")
                        or output_stat(effect) != stat):
                    continue
                if basis == "nonextra" and _classification(runtime, rule, effect) == "excluded":
                    continue
                value = effect["value"]
                if not isinstance(value, dict) or value.get("kind") != "stat" or value["sample"] != "current":
                    continue
                provider = rule["owner"] if value["subject"] == "owner" else recipient
                source_stat = value["stat"]
                source_basis = value.get("basis", "base" if source_stat.startswith("base_") else "total")
                todo.append((provider, source_stat.removeprefix("base_"), source_basis))
                if BENEFIT_TYPES.get(effect["benefit_type_id"], {}).get("zone") == "stat_pct":
                    todo.append((recipient, stat, "base"))
    return result


def independent_attribute_write(runtime, rule, effect, reads, *, recipients=None):
    stat = output_stat(effect)
    if stat is None or effect["domain"] == "enemy":
        return False
    for actor in (_current_candidates(runtime, rule) if recipients is None else recipients):
        for reader, read_stat, basis in reads:
            if reader != actor or read_stat != stat or basis == "base":
                continue
            if basis == "nonextra" and _classification(runtime, rule, effect) == "excluded":
                continue
            return False
    return True
