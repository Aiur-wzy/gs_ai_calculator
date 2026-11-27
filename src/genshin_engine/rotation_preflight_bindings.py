"""Read-only binding view for declared rotation checks of native v3 plans."""

from genshin_engine.mechanics_projection import project_action_hits, project_action_switches
from genshin_engine.rotation_port import rotation_issue


def prepare_mechanics_bindings(template, model, mechanics, issues):
    if template.get("version") != 2:
        issues.append(rotation_issue("unsupported", "机制命中检查需要稳定的 v2 命中 ID。", "template.version"))
        return template, model, []
    referenced = {item["action_id"] for field in ("hit_events", "hit_patterns")
                  for item in mechanics[field]}
    if any(action.get("start") is None and action["id"] in referenced for action in model["actions"]):
        issues.append(rotation_issue("missing_information", "来源动作开始时点待填，暂未检查机制命中关联。",
                                     "rotation_model.actions", severity="pending"))
        return template, model, []
    try:
        projected, events, _, prototypes = project_action_hits(
            template, model, mechanics, issues, require_complete=False)
    except ValueError as exc:
        issues.append(rotation_issue("time_precision", str(exc), "mechanics_model.hit_events"))
        return template, model, []

    hit_paths = {row["hit_id"]: f"mechanics_model.hit_events[{index}]"
                 for index, row in enumerate(mechanics["hit_events"])}
    patterns = {row["id"]: f"mechanics_model.hit_patterns[{index}]"
                for index, row in enumerate(mechanics["hit_patterns"])}
    bindings = {}
    prototype_bindings = {}
    for event in events:
        descriptor = event["data"]["descriptor"]
        row = {"hit_id": event["id"], "action_id": descriptor["action_id"], "order": event["order"]}
        if "pattern_occurrence" in descriptor:
            row["source_path"] = f"{patterns[descriptor['id']]}.offsets[{descriptor['pattern_occurrence'] - 1}]"
            prototype_bindings.setdefault(descriptor["prototype_hit_id"], []).append(row)
        else:
            row["source_path"] = hit_paths[event["id"]]
        bindings[event["id"]] = row

    for index, legacy in enumerate(model.get("action_hit_links", [])):
        hid, aid = legacy["hit_id"], legacy["action_id"]
        row = {**legacy, "source_path": f"rotation_model.action_hit_links[{index}]"}
        others = ([bindings[hid]] if hid in bindings else prototype_bindings.get(hid, []))
        conflicts = {}
        for other in others:
            if other["action_id"] != aid:
                conflicts.setdefault(other["action_id"], other)
        if conflicts:
            issues.append(rotation_issue(
                "action_hit_link_conflict", "同一命中指向了不同动作，请核对两处来源。",
                row["source_path"], hit_id=hid, conflicting_links=[row, *conflicts.values()]))
        elif not others:
            bindings[hid] = row
        elif any(other["source_path"].startswith("rotation_model.") for other in others):
            issues.append(rotation_issue(
                "action_hit_link_duplicate", "旧式命中关联重复，请保留同一次绑定。",
                row["source_path"], hit_id=hid, conflicting_links=[row, *others]))

    projected.extend(hit for hit in template["hits"]
                     if hit["id"] not in prototypes and hit["id"] not in hit_paths)
    scoped_template = {**template, "hits": projected}
    if all(action.get("start") is not None for action in model["actions"]):
        scoped_template["switches"] = project_action_switches(model)
    scoped_model = {**model, "action_hit_links": [
        {"hit_id": row["hit_id"], "action_id": row["action_id"]} for row in bindings.values()
    ]}
    return scoped_template, scoped_model, list(bindings.values())
