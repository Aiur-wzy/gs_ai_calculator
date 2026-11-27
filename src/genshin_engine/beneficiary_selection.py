"""Finite recipient qualification; never converts a hit filter to a property."""
from __future__ import annotations

CONTRACT = "beneficiary-eligibility-v1"


def _qualified(rule, candidates):
    eligibility = rule["beneficiaries"].get("eligibility")
    if eligibility is None:
        return candidates
    whitelist = eligibility.get("characters")
    return [actor for actor in candidates
            if (whitelist is None or actor in whitelist)
            and not (eligibility.get("exclude_owner") and actor == rule["owner"])]


def possible_recipients(rule, roster):
    """Whole-execution upper bound, independent of current frontline/state."""
    mode = rule["beneficiaries"]["mode"]
    candidates = ([rule["owner"]] if mode == "self" else
                  rule["beneficiaries"]["characters"] if mode == "characters" else list(roster))
    return _qualified(rule, candidates)


def base_recipients(rule, roster, frontline):
    scope = rule["beneficiaries"]
    mode = scope["mode"]
    if mode == "self":
        return [rule["owner"]]
    if mode == "team" or mode == "frontline" and scope["scope"] == "current_frontline":
        return list(roster)
    if mode == "characters":
        return scope["characters"]
    return [frontline]


def select_recipients(rule, roster, frontline):
    """Select once at trigger; delayed activation keeps the returned list."""
    return _qualified(rule, base_recipients(rule, roster, frontline))


def effective_recipient(rule, instance, actor, snapshot_frontline):
    """Read stored membership; a locked instance survives a later switch."""
    return (actor in instance["beneficiaries"]
            and position_matches(rule, actor, snapshot_frontline))


def position_matches(rule, actor, frontline):
    scope = rule["beneficiaries"]["scope"]
    if scope == "current_frontline":
        return actor == frontline
    if scope == "current_offfield":
        return frontline is not None and actor != frontline
    return True


def selection_receipt(rule, roster, frontline, event_id, time):
    """Finite display evidence only; callers retain actual queue authority."""
    eligibility = rule["beneficiaries"]["eligibility"]
    base = base_recipients(rule, roster, frontline)
    recipients = _qualified(rule, base)
    excluded = []
    for actor in base:
        if actor in recipients:
            continue
        reason = ("excluded_owner" if eligibility.get("exclude_owner") and actor == rule["owner"]
                  else "not_in_whitelist")
        excluded.append({"character": actor, "reason": reason})
    return {"contract": CONTRACT, "event_id": event_id, "selection_t": time,
            "frontline": frontline, "base_candidates": list(base), "recipients": recipients,
            "excluded": excluded}


def qualification_errors(rule, roster=None):
    """Semantic checks after the closed schema; return stable code/message pairs."""
    scope = rule["beneficiaries"]
    eligibility = scope.get("eligibility")
    if eligibility is None:
        return []
    errors = []
    if "exclude_owner" in eligibility and eligibility["exclude_owner"] is not True:
        errors.append(("beneficiary_eligibility_value", "排除提供者必须为布尔 true。"))
    whitelist = eligibility.get("characters")
    if whitelist is not None and (not whitelist or len(whitelist) != len(set(whitelist))):
        errors.append(("beneficiary_eligibility_value", "资格白名单必须非空且无重复。"))
    if not (eligibility.get("exclude_owner") is True or "characters" in eligibility):
        errors.append(("beneficiary_eligibility_empty", "受益者资格至少需要一个有效条件。"))
    if (scope["mode"] == "next_switch" or rule.get("resources")
            or any(effect["domain"] == "enemy" for effect in rule["effects"])):
        errors.append(("beneficiary_eligibility_unsupported", "受益者资格尚不支持接棒、产能副作用或敌方收益组合。"))
    if roster is not None:
        allowed = set(roster)
        if not set(eligibility.get("characters", [])).issubset(allowed):
            errors.append(("beneficiary_eligibility_reference", "受益者资格中的角色不在当前队伍。"))
        elif not possible_recipients(rule, roster):
            errors.append(("beneficiary_eligibility_unreachable", "该规则的受益者资格在当前队伍中永远无人符合。"))
    return errors
