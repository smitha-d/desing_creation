"""
Thin REST adapter over IQGeo Network Manager Telecom (mywcom).

This is the "vendor API adapter" layer from the PoC blueprint: the LangGraph
agent never talks HTTP or feature-schema details directly, it calls the
generic methods below (get_features_in_polygon, create_feature, ...). If you
later port the same agent to Esri/Smallworld/QGIS, only this file changes.

CONFIRM AGAINST YOUR INSTANCE:
The exact REST paths differ by IQGeo version/deployment. Update
`_feature_url`, `_query_url` etc. below to match your local REST API
reference before pointing this at a real server. Everything else in this
package (nodes, rules, geometry) only depends on the method signatures here,
not on the URL strings.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import requests

from . import geometry
from .config import (
    IQGEO_BASE_URL, IQGEO_APP, IQGEO_USERNAME, IQGEO_PASSWORD,
    IQGEO_LAYER_FOR, IQGEO_QUERY_ZOOM,
)

log = logging.getLogger(__name__)


@dataclass
class Feature:
    feature_type: str
    id: str | None          # None until created; server assigns the real id
    fields: dict[str, Any]

    @property
    def ref(self) -> str:
        """The 'table/id' style reference used throughout the mywcom schema."""
        if self.id is None:
            raise ValueError(f"{self.feature_type} has not been created yet")
        return self.id


class IQGeoClient:
    """
    Minimal client covering the capability checklist needed by the FTTH
    agent: spatial query, feature CRUD, port info, network trace, design
    state transitions. Swap this class out entirely for a mock in tests
    (see mock_client.py) -- nodes only depend on this interface.
    """

    def __init__(
        self,
        base_url: str = IQGEO_BASE_URL,
        app: str = IQGEO_APP,
        username: str = IQGEO_USERNAME,
        password: str = IQGEO_PASSWORD,
        session: requests.Session | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.app = app
        self.session = session or requests.Session()
        self._login(username, password)

    # -- auth -----------------------------------------------------------

    def _login(self, username: str, password: str) -> None:
        # Placeholder: IQGeo instances commonly use session-cookie auth via
        # the standard login form, or a token issued by an auth endpoint.
        # Confirm the real login route/flow for your deployment.
        resp = self.session.post(
            f"{self.base_url}/auth",
            data={"user": username, "pass": password},
            timeout=15,
        )
        if resp.status_code >= 400:
            log.warning(
                "Login endpoint returned %s -- confirm the auth route for "
                "your instance before relying on this client.",
                resp.status_code,
            )

    # -- spatial query ----------------------------------------------------

    def get_features_in_polygon(
        self, feature_type: str, polygon_wkt: str, limit: int = 2000
    ) -> list[dict]:
        """
        Return raw field dicts for every feature of `feature_type` whose
        geometry intersects `polygon_wkt`, via the confirmed /select_within
        endpoint. Used for:
          - address ∩ design polygon  -> demand points
          - manhole/pole/cabinet ∩ (polygon buffered by search radius) -> tie-in candidates
          - oh_route/ug_route ∩ buffered polygon -> routing graph edges

        NOTE: the response shape below (features list, id/geometry/fields
        keys) is my best guess at a typical myWorld JSON response and is
        NOT yet confirmed against your instance -- run this once against
        TestDesign, inspect the real payload, and adjust
        `_parse_select_within_response` if the keys differ. Everything
        upstream (nodes.py) only depends on this method returning a list of
        flat dicts with an "id" and a "location" key, so a parsing fix here
        is the only thing that would need to change.
        """
        polygon = geometry.polygon_from_wkt(polygon_wkt)
        layer = IQGEO_LAYER_FOR.get(feature_type, IQGEO_LAYER_FOR["_default"])

        params = {
            "coords": geometry.polygon_to_flat_coords(polygon),
            "zoom": IQGEO_QUERY_ZOOM,
            "layers": layer,
            "types": feature_type,
            "limit": limit,
            "application": self.app,
        }
        resp = self.session.get(f"{self.base_url}/select_within", params=params, timeout=30)
        resp.raise_for_status()
        return _parse_select_within_response(resp.json(), feature_type)

    def get_feature(self, feature_type: str, feature_id: str) -> dict:
        url = f"{self.base_url}/{self.app}/rest/{feature_type}/{feature_id}"
        resp = self.session.get(url, timeout=15)
        resp.raise_for_status()
        return resp.json()

    # -- feature CRUD -----------------------------------------------------

    def create_feature(self, feature_type: str, fields: dict[str, Any]) -> str:
        """
        Create a feature and return the server-assigned id (e.g.
        'fiber_splitter/41'). design_id should already be set on `fields`
        by the caller so the object lands inside the right design/job.
        """
        url = f"{self.base_url}/{self.app}/rest/{feature_type}"
        resp = self.session.post(url, json=fields, timeout=30)
        resp.raise_for_status()
        body = resp.json()
        new_id = body.get("id") or f"{feature_type}/{body.get('id_value')}"
        log.info("created %s", new_id)
        return new_id

    def update_feature(self, feature_type: str, feature_id: str, fields: dict[str, Any]) -> None:
        url = f"{self.base_url}/{self.app}/rest/{feature_type}/{feature_id}"
        resp = self.session.put(url, json=fields, timeout=30)
        resp.raise_for_status()

    # -- port / capacity info ---------------------------------------------

    def get_port_info(self, feature_type: str, feature_id: str, side: str) -> dict:
        """side is typically 'in' or 'out'; matches *_ports_info JSON columns."""
        url = f"{self.base_url}/{self.app}/rest/{feature_type}/{feature_id}/ports/{side}"
        resp = self.session.get(url, timeout=15)
        resp.raise_for_status()
        return resp.json()

    # -- network trace / design lifecycle ----------------------------------

    def trace_connectivity(self, from_feature: str, to_feature: str | None = None) -> dict:
        """
        Runs IQGeo's native network trace so the compliance-audit node
        checks real connectivity rather than trusting the routing node.
        """
        url = f"{self.base_url}/{self.app}/rest/network/trace"
        resp = self.session.get(
            url, params={"from": from_feature, "to": to_feature}, timeout=30
        )
        resp.raise_for_status()
        return resp.json()

    def set_design_state(self, design_id: str, state: str) -> None:
        """e.g. 'Designing' -> 'Ready for Review' -> 'Approved'/'Build'."""
        url = f"{self.base_url}/{self.app}/rest/design/{design_id}/state"
        resp = self.session.put(url, json={"state": state}, timeout=15)
        resp.raise_for_status()


def _parse_select_within_response(payload: dict, feature_type: str) -> list[dict]:
    """
    Parse /select_within's JSON body -- confirmed shape (from a real
    Postman response) is a standard GeoJSON FeatureCollection:

        {"type": "FeatureCollection", "features": [
            {"type": "Feature", "myw": {...}, "bbox": [...], "id": 351,
             "geometry": {"type": "Point", "coordinates": [lon, lat]},
             "properties": {"id": 351, "name": None, "street_number": "134",
                            "street_name": "Ramsden Square", ...}},
            ...
        ]}

    Each raw feature is flattened via _flatten_geojson_feature() into the
    plain dict shape the rest of the pipeline expects (an "id" prefixed
    with feature_type, a "location" for Point geometries, and every
    properties key merged in). Falls back to an empty list with a loud log
    message for any other top-level shape rather than raising, so an
    unexpected response for a *different* endpoint/version doesn't crash
    the whole ingest step.
    """
    if isinstance(payload, dict) and "features" in payload:
        raw_features = payload["features"]
    elif isinstance(payload, list):
        raw_features = payload
    else:
        log.warning(
            "unrecognised /select_within response shape for %s -- got %s. "
            "Update _parse_select_within_response() in iqgeo_client.py.",
            feature_type,
            type(payload),
        )
        return []

    return [_flatten_geojson_feature(f, feature_type) for f in raw_features]


def _flatten_geojson_feature(feature: dict, feature_type: str) -> dict:
    """
    Turns one /select_within GeoJSON Feature into the flat dict shape
    ingest_node/geometry.parse_point expect: {"id": "address/351",
    "location": [lon, lat], <...properties>}. `id` is prefixed with
    feature_type to match the "table/id" convention used everywhere else
    in this schema (e.g. 'address/516' in ftth_circuit.address) -- the raw
    API response just gives a bare int.
    """
    props = dict(feature.get("properties") or {})
    props.pop("id", None)  # keep the prefixed top-level id as the one source of truth

    flat = {"id": f"{feature_type}/{feature['id']}", **props}

    geometry_obj = feature.get("geometry") or {}
    geom_type = geometry_obj.get("type")
    if geom_type == "Point":
        flat["location"] = geometry_obj.get("coordinates")
    elif geom_type in ("LineString", "MultiLineString"):
        # oh_route/ug_route etc. -- in_structure/out_structure/length are
        # expected to arrive via `properties` same as everything else above;
        # `path` is kept too in case a node ever needs the raw geometry.
        flat["path"] = geometry_obj.get("coordinates")

    return flat
