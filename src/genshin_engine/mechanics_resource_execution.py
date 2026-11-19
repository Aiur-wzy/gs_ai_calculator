"""Rule-owned resource emissions use the original causal event emitter."""

from __future__ import annotations

from genshin_engine.mechanics_execution_port import MechanicsExecutionPort
from genshin_engine.particle_quantities import PARTICLE_MODE_FIELDS
from genshin_engine.resource_source_identity import (
    benefit_keys,
    causal_envelope,
    owns_trigger,
)


def activate_rule_and_resources(runtime: MechanicsExecutionPort, rule, event, beneficiaries, snapshot, parent, selection=None):
    # Whole threshold crossings are discrete triggers; otherwise a fractional
    # expected resource change carries its count into linear resource benefits.
    weight = 1 if rule['trigger'].get('resource_every') else event.get('resource_weight',1)
    if rule.get('resource_damage'):
        from genshin_engine.resource_damage_emission import emit_resource_damage
        emit_resource_damage(runtime, rule, event, parent)
    if event.get('expected_effect_activation') == 0:
        pass  # Fractional count remains available for linear benefits below.
    elif rule["timing"]["delay"]:
        runtime.emit_event(parent, "mechanics_activate", rule["timing"]["delay"],
                      {"rule_id": rule["id"], "event": event, "beneficiaries": beneficiaries, "snapshot": snapshot, **({"selection": selection} if selection is not None else {})}, rule["id"]+":activate")
    else:
        runtime.activate(rule, event, beneficiaries, snapshot, selection)
    for resource in sorted(rule.get("resources", []), key=lambda r: r["id"]):
        if not any(owns_trigger(o, rule, resource, event) for o in runtime.model.get("resource_ownership", [])):
            raise ValueError(f"{rule['id']} 的资源 {resource['source_part']} 未声明动作 {event.get('action_id')} 的执行所有权。")
        cause = causal_envelope(rule, resource, event, parent)
        keys = benefit_keys(cause, resource)
        delivered = getattr(runtime, '_resource_delivered_benefits', set())
        if delivered & keys:
            raise ValueError('The same actual resource benefit already has an executor')
        # Emission must succeed before marking ownership; a failed prefix is
        # retained by the queue and cannot be retried as a fresh occurrence.
        common = {"source": rule["source"], "source_rule_id": rule["id"],
                  "source_part": resource["source_part"], "resource_cause": cause,
                  **({"action_id": event['action_id']} if event.get('action_id') is not None else {})}
        if resource["kind"] == "energy_direct":
            amounts = resource['amounts'] if weight == 1 else {k:v*weight for k,v in resource['amounts'].items()}
            runtime.emit_event(parent, "energy_direct", resource["delay"], {**common, "amounts": amounts}, rule["id"]+":"+resource["id"])
        else:
            spawn = runtime.emit_event(parent, "energy_spawn", resource["delay"],
                {**common, "count": resource["count"]*weight, "lost_count": 0, "element": resource["element"], "particle_type": resource["particle_type"],
                 **({key: resource[key] for key in PARTICLE_MODE_FIELDS if key in resource} if weight==1 else {'count_mode':'average'})},
                rule["id"]+":"+resource["id"]+":spawn")
            runtime.data["events"][spawn["id"]] = spawn["data"]
            runtime.emit_event(spawn, "energy_credit", resource["travel_time"],
                          {**common, "spawn_id": spawn["id"], "count": resource["count"]*weight}, "credit")
        runtime._resource_delivered_benefits = delivered | keys

