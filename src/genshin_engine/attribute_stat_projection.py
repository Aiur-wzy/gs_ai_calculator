"""V3 graph with explicitly qualified projection facts.

This is not a native runtime, and it does not authorize any source or queue fact.
Ordinary AttributeStatGraphLedger remains unchanged and rejects these fields.
"""
from __future__ import annotations

import copy

from genshin_engine import attribute_projection as projection
from genshin_engine.attribute_stat_graph import AttributeStatGraphLedger
from genshin_engine.attribute_stat_schema import STAT_CONTRIBUTION, STAT_GRAPH_SCHEMA
from genshin_engine.rotation_schema import arr

PROJECTION_CONTRIBUTION = copy.deepcopy(STAT_CONTRIBUTION)
PROJECTION_CONTRIBUTION['properties'].update(
    output_domain={'enum': ['self', 'provider']}, sample_binding=projection.SAMPLE_BINDING,
    projection_of=projection.PROJECTION_OF)
PROJECTION_SCHEMA = copy.deepcopy(STAT_GRAPH_SCHEMA)
PROJECTION_SCHEMA['properties'].update(contributions=arr(PROJECTION_CONTRIBUTION),
                                      frames=arr(projection.GRAPH_FRAME))


class AttributeStatProjectionLedger(AttributeStatGraphLedger):
    declaration_schema = PROJECTION_SCHEMA
    contribution_schema = PROJECTION_CONTRIBUTION
    frame_schema = projection.GRAPH_FRAME

    def dependency_footprint(self, actor, stat, basis, frame_id, *, purpose='ordinary'):
        return self._dependency_footprint(actor, stat, basis, frame_id, root_purpose=purpose)

    @staticmethod
    def _application_base(node, query):
        # Both numeric and footprint routes call this only after the same full
        # qualification walker has validated projection_of identity/membership.
        # A root-only projection can never be consumed as an ordinary BASE.
        reference = node.get('projection_of')
        frame_id = reference['frame_id'] if reference is not None else query['frame']['id']
        return {'actor': node['component']['recipient'], 'stat': node['stat'],
                'basis': 'base', 'frame_id': frame_id}
