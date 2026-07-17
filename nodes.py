"""
LangGraph node functions for the FTTH design agent.

Each function takes the current FTTHDesignState dict and returns a partial
dict of updates (standard LangGraph node convention). Nodes are kept free of
IQGeo REST/geometry mechanics where possible -- that lives in
iqgeo_client.py / geometry.py / rules.py -- so this file reads close to the
phased PoC plan: ingest -> route -> audit -> (retry | write) -> BOM -> review.
"""

from __future__ import annotations

import logging

import networkx as nx
from langgraph.types import interrupt

from . import geometry, rules
from .config import RULES, COSTS
from .iqgeo_client import IQGeoClient
from .state import FTTHDesignState

log = logging.getLogger(__name__)


def make_nodes(client: IQGeoClient, address_client=None):
    """
    Returns the node functions bound to a live IQGeoClient (or a mock with
    the same interface -- see mock_client.py). Binding this way keeps
    graph.py free of any client-construction concerns.

    `address_client` optionally overrides where "address" features come
    from -- e.g. db_client.DBAddressClient(conn) to query the database
    directly instead of the REST API for that one feature type. Defaults
    to `client` (the REST path) if not given. Everything else (structures,
    routes, OLTs, and all feature *writes*) always goes through `client`.
    """
    address_source = address_client or client

    # -- Phase 2: ingest ---------------------------------------------------

    def ingest_node(state: FTTHDesignState) -> dict:
        # polygon_wkt is copied straight out of NMT (Design boundary), e.g.
        # "POLYGON ((0.1364 52.2311, 0.1375 52.2317, ...))" -- same format
        # as package.metadata's `boundary` in a cdiff export.
        polygon = geometry.polygon_from_wkt(state["polygon_wkt"])
        polygon_wkt = polygon.wkt

        # Demand points: Address layer already loaded citywide (see PoC
        # notes) -- just spatially join against this design's polygon. NMT's
        # own "13 objects" address-filter grid is doing exactly this query
        # under the hood -- open your browser's Network tab while that grid
        # loads to confirm the real endpoint/params for iqgeo_client.py.
        addresses = address_source.get_features_in_polygon("address", polygon_wkt)
        demand_points = []
        for a in addresses:
            lon, lat = geometry.parse_point(a["location"])
            demand_points.append({
                "address_id": a.get("address_id") or a["id"],
                "lon": lon,
                "lat": lat,
                # carried through when the source provides them (e.g.
                # db_client.fetch_addresses_in_polygon) -- used for
                # naming/traceability in feature_writer_node, not required
                # for the routing/compliance logic itself.
                "name": a.get("name"),
                "street_number": a.get("street_number"),
                "street_name": a.get("street_name"),
                "city": a.get("city"),
                "postcode": a.get("postcode"),
            })

        # Search radius beyond the polygon itself so we can find the
        # nearest *existing* infrastructure to tie into (brownfield
        # extension is the common case -- greenfield civil design, i.e.
        # placing brand new poles/manholes, is out of scope for this PoC
        # and should raise a violation for human siting instead).
        search_area_wkt = polygon.buffer(0.01).wkt  # ~1km at these latitudes; tune per deployment

        manholes = client.get_features_in_polygon("manhole", search_area_wkt)
        poles = client.get_features_in_polygon("pole", search_area_wkt)
        cabinets = client.get_features_in_polygon("cabinet", search_area_wkt)
        oh_routes = client.get_features_in_polygon("oh_route", search_area_wkt)
        ug_routes = client.get_features_in_polygon("ug_route", search_area_wkt)
        buildings = client.get_features_in_polygon("building", search_area_wkt)
        olts_raw = client.get_features_in_polygon("fiber_olt", search_area_wkt)

        candidate_tie_ins = [s["id"] for s in (*manholes, *poles, *cabinets)]

        points = {
            s["id"]: geometry.parse_point(s["location"])
            for s in (*manholes, *poles, *cabinets, *buildings)
        }
        olt_candidates = [o for o in olts_raw if _has_available_port(o)]

        return {
            "demand_points": demand_points,
            "candidate_tie_ins": candidate_tie_ins,
            "olt_candidates": olt_candidates,
            # stashed on state for the route planner, which rebuilds the
            # routing graph from these; not part of the persisted design
            # schema, but declared in state.py so StateGraph actually
            # creates a channel for them.
            "_oh_routes": oh_routes,
            "_ug_routes": ug_routes,
            "_points": points,
        }

    # -- Phase 3: route planning -------------------------------------------

    def route_planner_node(state: FTTHDesignState) -> dict:
        demand_points = state["demand_points"]
        if not demand_points:
            return {"violations": ["no demand points (addresses) found inside the design polygon"]}

        route_graph = geometry.build_route_graph(
            state["_oh_routes"], state["_ug_routes"], state["_points"]
        )
        points = state["_points"]

        centroid = geometry.centroid([(d["lon"], d["lat"]) for d in demand_points])

        candidates = [c for c in state["candidate_tie_ins"] if c in route_graph.graph]
        if not candidates:
            return {"violations": ["no existing manhole/pole/cabinet near the polygon is on the routing graph"]}
        tie_in = geometry.nearest_structure(candidates, points, centroid)

        # pick the OLT with the shortest graph path to the tie-in point
        best = None
        for olt in state["olt_candidates"]:
            root = olt["root_housing"]  # e.g. 'building/1'
            if root not in route_graph.graph:
                continue
            try:
                path, length = route_graph.shortest_path(root, tie_in)
            except nx.NetworkXNoPath:
                continue
            if best is None or length < best["feeder_length_m"]:
                best = {"olt": olt, "feeder_path": path, "feeder_length_m": length}

        if best is None:
            return {"violations": ["no OLT with an available port is graph-connected to the tie-in point"]}

        splitter_tree = rules.plan_splitter_tree(len(demand_points), RULES)

        return {
            "olt": best["olt"],
            "tie_in_structure": tie_in,
            "tie_in_point": points[tie_in],
            "feeder_path": best["feeder_path"],
            "feeder_length_m": best["feeder_length_m"],
            "splitter_tree": splitter_tree,
            "violations": [],
        }

    # -- Phase 4: compliance audit ------------------------------------------

    def compliance_audit_node(state: FTTHDesignState) -> dict:
        violations = rules.audit(dict(state), RULES)
        return {
            "violations": violations,
            "retry_count": state.get("retry_count", 0) + (1 if violations else 0),
        }

    def route_or_write(state: FTTHDesignState) -> str:
        """Conditional edge used by graph.py."""
        if state.get("feeder_path") is None:
            # route_planner_node couldn't find any OLT<->tie-in path at all --
            # there's nothing for feature_writer_node to build (no
            # tie_in_structure/olt/feeder_path on state), and retrying won't
            # change that since the routing graph/candidates haven't changed.
            # Skip straight to human review with the violations.
            return "no_route"
        if state["violations"] and state.get("retry_count", 0) < RULES.max_retries:
            return "retry"
        return "proceed"  # either compliant, or out of retries -- either way,
        # a human sees the violations in the review step before build

    # -- Phase 5: write features back into NMT -----------------------------

    def feature_writer_node(state: FTTHDesignState) -> dict:
        design_id = state["design_id"]
        created: dict[str, list[str]] = {}
        # fields as sent, keyed by the id the server returned -- lets
        # cdiff_export.py build an offline-reviewable package identical in
        # shape to the sample you shared, as an alternative to writing
        # straight to the live instance.
        records: dict[str, dict[str, dict]] = {}

        def make(feature_type: str, fields: dict) -> str:
            # design_id is NOT sent as a field/property -- the server infers
            # it from the `delta` query param (confirmed via a captured
            # "Add Object" request; see iqgeo_client.create_feature). Still
            # stashed on the local `records` copy so cdiff_export.py's
            # offline package shape matches a real cdiff export.
            new_id = client.create_feature(feature_type, fields, design_id)
            created.setdefault(feature_type, []).append(new_id)
            records.setdefault(feature_type, {})[new_id] = {
                **fields, "design_id": design_id, "id": new_id,
            }
            return new_id

        tie_in = state["tie_in_structure"]
        tie_in_point = state["tie_in_point"]

        # one new splice closure at the tie-in structure to house the splitter(s)
        closure_id = make("splice_closure", {
            "name": f"AUTO-SC-{design_id}",
            "root_housing": tie_in,
            "housing": tie_in,
            "location": list(tie_in_point),
        })

        splitter_ids = []
        for i, stage in enumerate(state["splitter_tree"]):
            splitter_ids.append(make("fiber_splitter", {
                "name": f"AUTO-SPL-{design_id}-{i+1}",
                "n_fiber_in_ports": 1,
                "n_fiber_out_ports": stage["ratio"],
                "root_housing": tie_in,
                "housing": closure_id,
                "location": list(tie_in_point),
                "loss": RULES.splitter_loss_db[stage["ratio"]],
                "service_status": "Designing",
            }))

        # feeder cable: OLT building -> tie-in structure, one segment per
        # routing hop (mirrors mywcom_fiber_segment in the sample export --
        # housing should really be the specific conduit pulled through each
        # hop; selecting/verifying spare conduit capacity is a TODO left as
        # a stub so this doesn't silently assume capacity that isn't there).
        feeder_cable_id = make("fiber_cable", {
            "name": f"AUTO-FCB-{design_id}",
            "type": "External",
            "fiber_count": max(64, len(state["demand_points"]) * 2),
            "directed": True,
            "loss": RULES.fiber_cable_loss_db_per_km,
        })
        path = state["feeder_path"]
        segment_ids = []
        prev_segment = None
        for a, b in zip(path, path[1:]):
            seg_id = make("mywcom_fiber_segment", {
                "cable": feeder_cable_id,
                "in_structure": a,
                "out_structure": b,
                "in_segment": prev_segment,
                "directed": True,
                "forward": True,
            })
            if prev_segment is not None:
                make("mywcom_fiber_connection", {
                    "in_object": prev_segment,
                    "out_object": seg_id,
                    "in_side": "out",
                    "out_side": "in",
                    "splice": True,
                    "root_housing": a,
                    "housing": a,
                })
            segment_ids.append(seg_id)
            prev_segment = seg_id

        # one ONT + drop cable per demand point, wired through the last
        # splitter stage. `last_splitter` is the fan-out stage every drop
        # is physically spliced from; a real build would also check each
        # drop against that splitter's remaining port count, which needs
        # the same conduit/port-capacity wiring flagged in the README TODOs.
        last_splitter = splitter_ids[-1]
        for dp in state["demand_points"]:
            ont_id = make("fiber_ont", {
                "name": f"AUTO-ONT-{dp['address_id'].split('/')[-1]}",
                "n_fiber_in_ports": 4,
                "root_housing": tie_in,
                "housing": tie_in,  # TODO: replace with the demand point's own
                                    # wall_box/building once that lookup/creation
                                    # step is wired in -- left explicit rather
                                    # than silently wrong.
                "location": [dp["lon"], dp["lat"]],
                "service_status": "Designing",
            })

            # physical drop cable: splitter housing -> ONT (single segment;
            # both ends currently collapse to `tie_in` per the TODO above,
            # so treat the segment's `length` field, not its path geometry,
            # as the authoritative drop distance until ONT housing is real).
            drop_cable_id = make("fiber_cable", {
                "name": f"AUTO-DRP-{design_id}-{dp['address_id'].split('/')[-1]}",
                "type": "Drop",
                "fiber_count": 2,
                "directed": True,
                "loss": RULES.fiber_cable_loss_db_per_km,
            })
            make("mywcom_fiber_segment", {
                "cable": drop_cable_id,
                "in_structure": tie_in,
                "out_structure": tie_in,
                "in_equipment": last_splitter,
                "out_equipment": ont_id,
                "length": dp["drop_length_m"],
                "directed": True,
                "forward": True,
            })

            make("ftth_circuit", {
                "name": f"AUTO-FTTH-{design_id}-{dp['address_id'].split('/')[-1]}",
                "in_feature": state["olt"]["id"],
                "in_pins": f"out:{_first_available_port(state['olt'])}",
                "out_feature": ont_id,
                "out_pins": "in:1",
                "connected": False,   # flips True once actually built/spliced
                "status": "Designing",
                "address": dp["address_id"],
                "service_type": "Direct",
            })

        return {"created_features": created, "_records": records}

    # -- Phase 6: BOM --------------------------------------------------------

    def bom_node(state: FTTHDesignState) -> dict:
        return {"bom": rules.compute_bom(dict(state), COSTS)}

    # -- Phase 7: human review gate -------------------------------------------

    def human_review_node(state: FTTHDesignState) -> dict:
        """
        Pauses the graph (requires a checkpointer -- see graph.py) and
        surfaces the BOM + any outstanding violations for an engineer to
        approve, edit, or reject. Resume with:

            graph.invoke(Command(resume={"decision": "approved"}), config)

        See main.py for the full resume flow.
        """
        decision = interrupt({
            "message": "FTTH design ready for review",
            "violations": state.get("violations", []),
            "bom": state.get("bom"),
            "created_features": state.get("created_features"),
        })
        return {
            "review_status": decision.get("decision", "pending_review"),
            "review_notes": decision.get("notes", ""),
        }

    return {
        "ingest_node": ingest_node,
        "route_planner_node": route_planner_node,
        "compliance_audit_node": compliance_audit_node,
        "route_or_write": route_or_write,
        "feature_writer_node": feature_writer_node,
        "bom_node": bom_node,
        "human_review_node": human_review_node,
    }


# -- small helpers reading the *_ports_info JSON convention from the sample export --

def _has_available_port(olt_feature: dict) -> bool:
    ports_info = olt_feature.get("fiber_out_ports_info") or {}
    return any(v.get("status") == "Available" for v in ports_info.values())


def _first_available_port(olt_feature: dict) -> str:
    ports_info = olt_feature.get("fiber_out_ports_info") or {}
    for port, v in ports_info.items():
        if v.get("status") == "Available":
            return port
    raise ValueError(f"no available port on {olt_feature.get('id')}")
