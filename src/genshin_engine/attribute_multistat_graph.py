"""Five-scalar immutable dependency graph reusing the qualified bounded walker."""
from __future__ import annotations

import copy

from genshin_engine import attribute_projection as projection
from genshin_engine.attribute_graph import AttributeGraphLedger
from genshin_engine.attribute_ledger import AttributeLedgerError, _issue
from genshin_engine.attribute_multistat_schema import CONTRIBUTION_V4, GRAPH_SCHEMA_V4
from genshin_engine.attribute_schema import ID
from genshin_engine.attribute_stat_graph import AttributeStatGraphLedger
from genshin_engine.attribute_stat_projection import AttributeStatProjectionLedger
from genshin_engine.attribute_stats import BASE_STATS, CONTRACT, DESCRIPTORS
from genshin_engine.rotation_schema import arr


class AttributeMultiStatGraphLedger(AttributeStatGraphLedger):
    contract = CONTRACT
    declaration_schema = GRAPH_SCHEMA_V4
    contribution_schema = CONTRIBUTION_V4

    def _query_support_issue(self, query):
        spec = DESCRIPTORS.get(query["stat"])
        if spec is None or query["basis"] not in spec["basis"]:
            return _issue("attribute_query_unsupported", "属性或读取基底不在v4能力范围", "query", status="pending")

    def _validate_node(self, node):
        AttributeGraphLedger._validate_node(self, node)
        percentage = node["application"]["kind"] == "recipient_base_fraction"
        expected = "ratio" if percentage else DESCRIPTORS[node["stat"]]["unit"]
        if node["value_unit"] != expected or percentage and node["stat"] not in BASE_STATS:
            raise AttributeLedgerError("attribute_unit_scope", "属性输出单位与基础比例应用不一致", node["id"])


PROJECTION_CONTRIBUTION = copy.deepcopy(CONTRIBUTION_V4)
PROJECTION_CONTRIBUTION["properties"].update(output_domain={"enum": ["self", "provider"]},
    sample_binding=projection.SAMPLE_BINDING, projection_of=projection.PROJECTION_OF, condition_ref=ID)
PROJECTION_SCHEMA = copy.deepcopy(GRAPH_SCHEMA_V4)
PROJECTION_SCHEMA["properties"].update(contributions=arr(PROJECTION_CONTRIBUTION), frames=arr(projection.GRAPH_FRAME))


class AttributeMultiStatProjectionLedger(AttributeMultiStatGraphLedger):
    declaration_schema = PROJECTION_SCHEMA
    contribution_schema = PROJECTION_CONTRIBUTION
    frame_schema = projection.GRAPH_FRAME
    _application_base = staticmethod(AttributeStatProjectionLedger._application_base)

    def dependency_footprint(self, actor, stat, basis, frame_id, *, purpose="ordinary"):
        return self._dependency_footprint(actor, stat, basis, frame_id, root_purpose=purpose)
