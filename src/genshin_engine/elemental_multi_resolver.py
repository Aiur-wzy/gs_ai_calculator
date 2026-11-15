"""Ordered v4 resolver dispatch; kernels keep the one target state authority."""

from __future__ import annotations

from genshin_engine.elemental_multi_contact import MultiAuraContact
from genshin_engine.elemental_resolver_amplify import resolve_amplify_or_refresh
from genshin_engine.elemental_resolver_coexistence import (
    resolve_cryo_electro,
    resolve_hydro_cryo,
    resolve_hydro_dendro,
    resolve_hydro_electro,
)
from genshin_engine.elemental_resolver_crystallize import resolve_geo
from genshin_engine.elemental_resolver_freeze import (
    resolve_freezable_contact,
    resolve_frozen_anemo,
)
from genshin_engine.elemental_resolver_swirl import resolve_anemo


def apply_multi_contact(contact: MultiAuraContact):
    contact.validate()
    state, element = contact.state, contact.element
    freeze_child, non_freeze_auras = contact.freeze_child, contact.non_freeze_auras
    if (state.freezable and freeze_child and element in ("hydro", "cryo")
            and not non_freeze_auras):
        return resolve_freezable_contact(contact)
    if (element == "anemo" and freeze_child
            and (freeze_child.aura or freeze_child.frozen) and not non_freeze_auras):
        return resolve_frozen_anemo(contact)
    if element == "anemo":
        return resolve_anemo(contact)
    if element == "geo":
        return resolve_geo(contact)
    opposite = {"hydro": "cryo", "cryo": "hydro"}.get(element)
    if opposite and opposite in state.auras:
        return resolve_hydro_cryo(contact, opposite)
    opposite = {"hydro": "electro", "electro": "hydro"}.get(element)
    if opposite and opposite in state.auras:
        return resolve_hydro_electro(contact, opposite)
    opposite = {"cryo": "electro", "electro": "cryo"}.get(element)
    if opposite and opposite in state.auras:
        return resolve_cryo_electro(contact, opposite)
    opposite = {"hydro": "dendro", "dendro": "hydro"}.get(element)
    if opposite and opposite in state.auras:
        return resolve_hydro_dendro(contact, opposite)
    return resolve_amplify_or_refresh(contact)
