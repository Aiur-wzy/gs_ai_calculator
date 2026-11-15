"""Target existing CLI contracts without introducing a second schema owner."""
from __future__ import annotations

import copy

from genshin_app.equipment.schema import PlanError


def _tokens(pointer):
    if not isinstance(pointer, str) or len(pointer) > 4096 or pointer and not pointer.startswith("/"):
        raise PlanError("pointer必须为空或以/开头的JSON Pointer", "invalid_capability_query")
    result = []
    for token in pointer.split("/")[1:]:
        if "~" in token.replace("~0", "").replace("~1", ""):
            raise PlanError("pointer含非法转义", "invalid_capability_query")
        result.append(token.replace("~1", "/").replace("~0", "~"))
    return result


def _read(document, pointer):
    value = document
    try:
        for token in _tokens(pointer):
            if isinstance(value, list):
                if not token.isascii() or not token.isdecimal() or len(token) > 10 or token != str(int(token)):
                    raise KeyError(token)
                value = value[int(token)]
            else:
                value = value[token]
    except (KeyError, IndexError, TypeError):
        raise PlanError("能力字段不存在；请先读取其父位置", "capability_pointer_not_found", {"pointer": pointer}) from None
    return value


def _references(value, root):
    """Preserve full local $ref targets transitively, without inlining cycles."""
    pending, seen, references, external = [value], set(), {}, set()
    while pending:
        current = pending.pop()
        if not isinstance(current, (dict, list)) or id(current) in seen:
            continue
        seen.add(id(current))
        if isinstance(current, dict):
            reference = current.get("$ref")
            if isinstance(reference, str):
                if reference.startswith("#"):
                    if reference not in references:
                        try:
                            target = _read(root, reference[1:])
                        except PlanError:
                            # A nested schema may own its own root; do not make
                            # up a replacement. Return the unresolved identity.
                            external.add(reference)
                        else:
                            references[reference] = copy.deepcopy(target)
                            pending.append(target)
                else:
                    external.add(reference)
            pending.extend(current.values())
        else:
            pending.extend(current)
    return references, sorted(external)


def query_capabilities(document, *, command=None, section=None, pointer=None):
    if command is not None and section is not None:
        raise PlanError("command与section只能选择一个根", "invalid_capability_query")
    root, root_pointer = document, ""
    if command is not None:
        name = " ".join(command.split())
        matches = [(index, row) for index, row in enumerate(document["commands"]) if row["name"] == name]
        if len(matches) != 1:
            raise PlanError("命令不存在；名称应与capabilities中的完整命令一致", "capability_command_not_found",
                            {"command": name})
        index, root = matches[0]
        root_pointer = "/commands/" + str(index)
    elif section is not None:
        if not isinstance(section, str) or section not in document:
            raise PlanError("能力分区不存在", "capability_section_not_found", {"section": section})
        root = document[section]
        root_pointer = "/" + section.replace("~", "~0").replace("/", "~1")
    relative = "" if pointer is None else pointer
    value = _read(root, relative)
    reference_root, current = root, root
    for token in _tokens(relative):
        if isinstance(current, dict) and any(key in current for key in ("$id", "$schema", "$defs", "definitions")):
            reference_root = current
        current = current[int(token)] if isinstance(current, list) else current[token]
    if isinstance(current, dict) and any(key in current for key in ("$id", "$schema", "$defs", "definitions")):
        reference_root = current
    references, external = _references(value, reference_root)
    return {"contract": "genshin-cli-capability-selection-v1", "cli": document["cli"],
        "version": document["version"], "source_pointer": root_pointer + relative,
        "data": copy.deepcopy(value), "local_references": references,
        "unresolved_or_external_references": external, "complete": True,
        "authority": "description_only", "note": "不会取得当前原生会话的权限；实际工具目录请从该会话读取tool_contracts。"}
