"""Frozen v1 readiness admission, independent of actual handler registry lookup."""

from __future__ import annotations

import copy
from types import FunctionType
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from genshin_engine.special_reactions import (
        SpecialReactionPending,  # noqa: TC004 - injected canonical globals
    )


def _canonical_require_ready_v1(self, reaction_type: str, event_id: str) -> dict:
        row = self._config(reaction_type)
        if row is None:
            raise ValueError("内部错误：未开启的替换不能进入执行。")
        qualification = row["qualification"]
        mechanics = row["mechanics"]
        if qualification["status"] == "pending":
            raise SpecialReactionPending(
                "special_qualification_pending", "星月反应资格尚未核实。", event_id,
                {"reaction_type": reaction_type,
                 "provider_ids": copy.deepcopy(qualification["provider_ids"]),
                 "source": qualification["source"]})
        if self.party_member_ids is None:
            raise SpecialReactionPending(
                "special_party_unbound", "尚未绑定实际队伍，不能仅凭资格字符串开启替换。", event_id,
                {"reaction_type": reaction_type,
                 "provider_ids": copy.deepcopy(qualification["provider_ids"])})
        present = sorted(set(qualification["provider_ids"]) & set(self.party_member_ids))
        if not present:
            raise SpecialReactionPending(
                "special_provider_absent", "声明的星月资格提供者不在实际队伍中。", event_id,
                {"reaction_type": reaction_type,
                 "provider_ids": copy.deepcopy(qualification["provider_ids"]),
                 "party_member_ids": list(self.party_member_ids)})
        capability_tag = qualification["capability_tag"]
        proven = [
            provider_id for provider_id in present
            if capability_tag in self.party_capability_tags.get(provider_id, ())
        ]
        if not proven:
            raise SpecialReactionPending(
                "special_capability_unproven",
                "队伍成员存在，但数据库能力标签没有证明其具备该星月反应资格。",
                event_id,
                {"reaction_type": reaction_type, "provider_ids": present,
                 "required_capability_tag": capability_tag,
                 "party_capability_tags": {
                     provider_id: list(self.party_capability_tags.get(provider_id, ()))
                     for provider_id in present
                 }},
            )
        if mechanics["status"] == "pending":
            raise SpecialReactionPending(
                "special_mechanics_pending", "星月反应机制档案尚未核实。", event_id,
                {"reaction_type": reaction_type, "source": mechanics["source"]})
        return row


def build_reference_readiness_v1(namespace: dict[str, Any]) -> FunctionType:
    source = _canonical_require_ready_v1
    result = FunctionType(source.__code__, namespace, "_require_ready", source.__defaults__, source.__closure__)
    result.__kwdefaults__ = copy.deepcopy(source.__kwdefaults__)
    result.__annotations__ = copy.deepcopy(source.__annotations__)
    return result

