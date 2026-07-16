"""
Builds the LangGraph state machine described in the phased PoC plan:

    ingest -> route_planner -> compliance_audit --(fail, retries left)--> route_planner
                                            |
                             (pass, or retries exhausted) --> feature_writer -> bom -+
                                            |                                        |
                                    (no route found at all) ------------------------>+--> human_review -> END

The retry edge is bounded by RULES.max_retries (see rules.route_or_write /
config.py) so a design that can't be made compliant surfaces to the human
reviewer with its violations listed, instead of looping forever. A design
with no viable OLT<->tie-in path at all skips feature_writer/bom entirely
(there's nothing to build) and goes straight to human_review with its
violations.

Requires: langgraph>=0.2 (for `interrupt`/`Command`-based human-in-the-loop).
Confirm against your installed version -- the interrupt API has moved
around across LangGraph releases.
"""

from __future__ import annotations

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, StateGraph

from .iqgeo_client import IQGeoClient
from .nodes import make_nodes
from .state import FTTHDesignState


def build_graph(client: IQGeoClient, address_client=None, checkpointer=None):
    nodes = make_nodes(client, address_client=address_client)

    workflow = StateGraph(FTTHDesignState)
    workflow.add_node("ingest", nodes["ingest_node"])
    workflow.add_node("route_planner", nodes["route_planner_node"])
    workflow.add_node("compliance_audit", nodes["compliance_audit_node"])
    workflow.add_node("feature_writer", nodes["feature_writer_node"])
    workflow.add_node("bom", nodes["bom_node"])
    workflow.add_node("human_review", nodes["human_review_node"])

    workflow.set_entry_point("ingest")
    workflow.add_edge("ingest", "route_planner")
    workflow.add_edge("route_planner", "compliance_audit")
    workflow.add_conditional_edges(
        "compliance_audit",
        nodes["route_or_write"],
        {
            "retry": "route_planner",
            "proceed": "feature_writer",
            "no_route": "human_review",
        },
    )
    workflow.add_edge("feature_writer", "bom")
    workflow.add_edge("bom", "human_review")
    workflow.add_edge("human_review", END)

    return workflow.compile(checkpointer=checkpointer or MemorySaver())
