"""Small existing periodic DB input; no database copies or gameplay timing claims."""
import copy

from test_periodic_actions import periodic_plan


def assembly_inputs(db):
    manual = periodic_plan(db)
    profile, source = manual["action_event_profiles"][0], manual["periodic_attack_sources"][0]
    mappings = {c["channel_id"]: c for c in source["channels"]}
    request = {"schema_version": 1, "profile_id": "assembled.q", "ability_ids": ["e"],
               **{k: copy.deepcopy(source[k]) for k in ("version_line", "selection", "origin_reference_id", "status", "source")},
               "damage_type_ids": ["damage.elemental_burst"], "scaling_stat": "atk",
               "lifecycle": copy.deepcopy(profile["lifecycle"]), "channels": []}
    for channel in profile["channels"]:
        mapping = mappings[channel["channel_id"]]
        request["channels"].append({"candidate_id": mapping["candidate_id"], "counter_tag": mapping["counter_tag"],
            "application_mode": mapping["application_mode"], "verification": copy.deepcopy(mapping["verification"]),
            **{k: copy.deepcopy(channel[k]) for k in ("schedule", "contact_profile_id", "default_landed", "self_sampling")}})
    plan = copy.deepcopy(manual)
    plan["manual_template"]["hits"] = []
    for key in ("action_event_profiles", "action_event_bindings", "action_event_overrides", "periodic_attack_sources"):
        plan.pop(key)
    return plan, request, manual
