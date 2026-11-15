"""Read explicit param tokens; never infer gameplay meaning from text order."""
from __future__ import annotations

import math
import re

PARAMETER_TOKEN = re.compile(r"\{param([1-9][0-9]*)(?::([^{}\r\n]+))?\}")
_CANDIDATE_TOKEN = re.compile(r"\{param[^{}\r\n]*\}")
_DISPLAY_FORMAT = re.compile(r"F([0-6])(P)?\Z")


def parameter_indices(text, *, maximum=64):
    """Same bounded, exact token semantics used by runtime parameter queries."""
    return sorted({int(match[1]) - 1 for match in PARAMETER_TOKEN.finditer(text)
                   if len(match[1]) <= len(str(maximum)) and int(match[1]) <= maximum})


def project_parameter_descriptions(descriptions, values, *, level=None, pointer=""):
    """Add a reading view while keeping raw coefficients and complete sentences."""
    if not isinstance(descriptions, list):
        return {"status": "unavailable", "reason": "description_list_missing", "items": [],
                "execution_authority": "none"}
    items = []
    for position, text in enumerate(descriptions):
        row = {"text": text, "description_pointer": pointer + f"/param_desc/{position}",
               "level": {"table_level": level, "effective_level_status": "not_requested"},
               "references": [], "unresolved_tokens": [],
               "gameplay_interpretation": "agent_required", "execution_authority": "none"}
        if not isinstance(text, str):
            row["status"] = "description_text_missing"
            items.append(row)
            continue
        for candidate in _CANDIDATE_TOKEN.finditer(text):
            token = candidate[0]
            match = PARAMETER_TOKEN.fullmatch(token)
            if match is None or len(match[1]) > 6:
                row["unresolved_tokens"].append({"token": token, "reason": "unsupported_token"})
                continue
            index = int(match[1]) - 1
            reference = {"token": "param" + match[1], "index": index, "format": match[2],
                         "parameter_pointer": pointer + f"/param_list/{index}"}
            if not isinstance(values, list) or index >= len(values):
                reference.update(status="parameter_missing", raw_value=None, display=None)
                row["unresolved_tokens"].append({"token": token, "reason": "parameter_index_missing"})
            else:
                value = values[index]
                reference["raw_value"] = value
                try:
                    finite = type(value) in (int, float) and math.isfinite(value)
                except OverflowError:
                    finite = False
                display_format = _DISPLAY_FORMAT.fullmatch(match[2] or "")
                if not finite:
                    reference.update(status="non_scalar_parameter", display=None)
                    row["unresolved_tokens"].append({"token": token, "reason": "non_scalar_parameter"})
                elif match[2] is None:
                    reference.update(status="ready", display=str(value), display_unit="unspecified")
                elif display_format:
                    percent = bool(display_format[2])
                    display = value * 100 if percent else value
                    reference.update(status="ready", display=f"{display:.{display_format[1]}f}" + ("%" if percent else ""),
                                     display_unit="percent" if percent else "unspecified")
                else:
                    reference.update(status="format_unresolved", display=None)
                    row["unresolved_tokens"].append({"token": token, "reason": "unsupported_format"})
            row["references"].append(reference)
        row["status"] = "partial" if row["unresolved_tokens"] else "ready" if row["references"] else "no_parameter_references"
        items.append(row)
    return {"status": "partial" if any(row["unresolved_tokens"] or row["status"] == "description_text_missing" for row in items) else "ready",
            "items": items, "execution_authority": "none",
            "note": "参数按原文param编号关联，描述行号不是参数序号；数值用途与有效等级仍须核对。"}


def add_context_parameter_references(detail, kind):
    """Only enrich the parameter sections that survived the caller's selection."""
    groups = ("skills", "extra_info") if kind == "character" else ("items",) if kind == "skill" else ()
    for group in groups:
        for index, item in enumerate(detail.get(group, [])):
            for level, values in item.get("params", {}).items():
                values["parameter_references"] = project_parameter_descriptions(
                    values.get("param_desc"), values.get("param_list"),
                    level=int(level), pointer=f"/{group}/{index}/params/{level}")
    return detail
