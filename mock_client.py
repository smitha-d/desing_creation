"""
Offline stand-in for IQGeoClient, backed by a real (anonymised) slice of
network topology pulled from the design_20260716_170750.zip cdiff sample --
structures, routes, and OLT port occupancy -- so you can run the whole
LangGraph pipeline end to end and see a sane design before pointing
main.py at a live IQGeo instance.

It does NOT include an Address layer (that table wasn't part of the design
export -- it lives in your base map, per the earlier discussion), so
demand points are passed in directly for the demo instead of being
resolved through get_features_in_polygon("address", ...).

Swap real_client = IQGeoClient(...) for mock_client = MockIQGeoClient(...)
in main.py to flip between the two -- both satisfy the same interface, so
nothing else changes.
"""

from __future__ import annotations

import json
from pathlib import Path

from shapely.geometry import Point

from . import geometry

_DATA_PATH = Path(__file__).parent / "sample_data" / "sample_network.json"


class MockIQGeoClient:
    def __init__(self, demand_points: list[dict] | None = None):
        with open(_DATA_PATH) as f:
            self._data = json.load(f)
        # demand points aren't in the export (see module docstring) --
        # supply your own test set, e.g. new addresses inside the polygon
        # you're about to pass to the graph.
        self._demand_points = demand_points or []
        self._next_id = {}

    def get_features_in_polygon(self, feature_type: str, polygon_wkt: str, limit: int = 2000) -> list[dict]:
        poly = geometry.polygon_from_wkt(polygon_wkt)

        if feature_type == "address":
            return [
                dp for dp in self._demand_points
                if poly.contains(Point(dp["lon"], dp["lat"]))
            ]

        rows = self._data.get(feature_type, [])
        if feature_type in ("oh_route", "ug_route"):
            # routes don't carry their own point geometry in this slice --
            # always include them, the route graph builder only needs
            # in_structure/out_structure/length.
            return rows

        if feature_type == "fiber_olt":
            # OLTs don't carry their own location in this slice either --
            # they sit inside root_housing (a building/manhole/pole/cabinet),
            # so filter by that structure's location instead.
            out = []
            for r in rows:
                loc = self._structure_location(r.get("root_housing"))
                if loc and poly.contains(Point(*loc)):
                    out.append(r)
            return out

        out = []
        for r in rows:
            loc = r.get("location")
            if loc and poly.contains(Point(*geometry.parse_point(loc))):
                out.append(r)
        return out

    def _structure_location(self, structure_id: str | None) -> tuple[float, float] | None:
        if not structure_id:
            return None
        for feature_type in ("manhole", "pole", "cabinet", "building"):
            for r in self._data.get(feature_type, []):
                if r["id"] == structure_id:
                    loc = r.get("location")
                    return geometry.parse_point(loc) if loc else None
        return None

    def get_feature(self, feature_type: str, feature_id: str) -> dict:
        for r in self._data.get(feature_type, []):
            if r["id"] == feature_id:
                return r
        raise KeyError(feature_id)

    def create_feature(self, feature_type: str, fields: dict, design_id: str) -> str:
        # signature matches IQGeoClient.create_feature -- design_id is the
        # `delta` target on the real client, just logged here since the
        # mock has no actual design/version concept.
        self._next_id.setdefault(feature_type, 0)
        self._next_id[feature_type] += 1
        new_id = f"{feature_type}/AUTO-{self._next_id[feature_type]}"
        print(f"[mock create] {new_id} (design={design_id})  {fields}")
        return new_id

    def update_feature(self, feature_type: str, feature_id: str, fields: dict, design_id: str) -> None:
        print(f"[mock update] {feature_id} (design={design_id})  {fields}")

    def get_port_info(self, feature_type: str, feature_id: str, side: str) -> dict:
        f = self.get_feature(feature_type, feature_id)
        return f.get(f"fiber_{side}_ports_info", {})

    def trace_connectivity(self, from_feature: str, to_feature: str | None = None) -> dict:
        return {"connected": True, "note": "mock client does not simulate real trace"}

    def set_design_state(self, design_id: str, state: str) -> None:
        print(f"[mock] design {design_id} -> {state}")