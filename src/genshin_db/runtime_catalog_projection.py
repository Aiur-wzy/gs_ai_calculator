"""Paged projections of one immutable source catalog, without execution rights."""
from __future__ import annotations

import copy

VIEWS = ("all", "summary", "roots", "materials", "level_profiles", "issues")


def project_activation_catalog(value, *, view="all", page=1, page_size=20):
    if view not in VIEWS or type(page) is not int or page < 1 or type(page_size) is not int or not 1 <= page_size <= 50:
        raise ValueError("catalog_view须为all/summary/roots/materials/level_profiles/issues，page>=1，page_size为1..50")
    if view == "all":
        return copy.deepcopy(value)
    result = {key: copy.deepcopy(value[key]) for key in ("contract", "snapshot", "selection", "content_fingerprint",
              "root_directory_status", "activation_closure_status", "capacity_closure_status", "execution_authority") if key in value}
    field = {"roots": "root_occurrences", "materials": "materials", "level_profiles": "level_profiles", "issues": "issues"}.get(view)
    counts = {name: len(value.get(name, [])) for name in ("root_occurrences", "materials", "level_profiles", "issues")}
    result.update(catalog_view=view, counts=counts, available_views=list(VIEWS),
                  content_scope="content_fingerprint identifies the full same-snapshot catalog; items are a projection")
    if field:
        selected = value.get(field, [])
        # Some source profiles are keyed by a stable semantic identity.
        rows = ([{"key": key, "value": selected[key]} for key in sorted(selected)] if isinstance(selected, dict) else selected)
        start = (page - 1) * page_size
        result.update(items=copy.deepcopy(rows[start:start + page_size]), total=len(rows), page=page, page_size=page_size,
                      next_page=page + 1 if start + page_size < len(rows) else None, source_field=field)
    else:
        result.update(items=[], next_page=None,
                      next_options=[{"action": "action_activation_catalog", "catalog_view": name, "page": 1, "page_size": page_size,
                                     **value.get("selection", {}), "snapshot_id": value.get("snapshot", {}).get("snapshot_id")}
                                    for name in ("roots", "materials", "level_profiles", "issues")])
        for request in result["next_options"]:
            for key in list(request):
                if request[key] is None:
                    del request[key]
    return result
