"""
LangGraph state schema for the FTTH design agent.

One state object flows through every node in graph.py. Keeping it a
TypedDict (rather than scattering values across node return values) is what
lets the compliance-audit -> route-planner retry loop and the human-review
interrupt both "just work" -- LangGraph persists this whole dict at every
step via the checkpointer.
"""

from __future__ import annotations

from typing import TypedDict


class DemandPoint(TypedDict):
    address_id: str
    lon: float
    lat: float
    drop_length_m: float  # filled in by rules.audit()


class FTTHDesignState(TypedDict, total=False):
    # -- input, set once at invoke() time --
    design_id: str
    polygon_wkt: str  # e.g. "POLYGON ((0.1364 52.2311, ...))" -- copied
                       # straight out of NMT, same format as package.metadata
                       # `boundary` in a cdiff export

    # -- ingest_node output --
    demand_points: list[DemandPoint]
    candidate_tie_ins: list[str]          # structure ids (manhole/pole/cabinet)
    olt_candidates: list[dict]            # fiber_olt rows with available ports

    # -- ingest_node internals, consumed by route_planner_node --
    # Must be declared here even though they're not part of the persisted
    # design schema: StateGraph(FTTHDesignState) only creates a channel for
    # keys present in this TypedDict's annotations, so any key a node
    # returns that isn't declared here is silently dropped instead of
    # merged into state.
    #
    # Kept as plain feature dicts / tuples rather than a built
    # geometry.RouteGraph -- the checkpointer msgpack-serializes state on
    # every step, and RouteGraph wraps an nx.Graph, which isn't
    # serializable. route_planner_node rebuilds the graph from these (cheap)
    # each time it runs instead.
    _oh_routes: list[dict]
    _ug_routes: list[dict]
    _points: dict          # structure id -> (lon, lat)

    # -- route_planner_node output --
    olt: dict | None
    tie_in_structure: str | None
    tie_in_point: tuple  # (lon, lat)
    splitter_tree: list[dict]
    feeder_path: list[str] | None         # structure ids, OLT building -> tie-in
    feeder_length_m: float

    # -- compliance_audit_node output --
    violations: list[str]
    estimated_loss_db: float
    retry_count: int

    # -- feature_writer_node output --
    created_features: dict[str, list[str]]  # table name -> list of new ids

    # -- bom_node output --
    bom: dict

    # -- human_review_node output --
    review_status: str  # "pending_review" | "approved" | "rejected"
    review_notes: str
