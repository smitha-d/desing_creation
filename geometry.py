"""
Spatial helpers -- the "open-source computational core" from the PoC
blueprint (GeoPandas/Shapely/NetworkX), kept independent of IQGeo so the
same functions work if this agent is later pointed at Esri/Smallworld/QGIS.

Requires: shapely, networkx (see requirements.txt).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import networkx as nx
from shapely import wkt as shapely_wkt
from shapely.geometry import Point, Polygon, shape
from shapely.ops import transform
import pyproj


@dataclass
class RouteGraph:
    """Wraps a NetworkX graph of Structures connected by Route/Conduit
    features, plus a lookup of structure_id -> (lon, lat)."""

    graph: nx.Graph
    points: dict  # structure_id -> (lon, lat)

    def shortest_path(self, src: str, dst: str) -> tuple[list[str], float]:
        """Returns (path_of_structure_ids, total_length_m). Raises
        networkx.NetworkXNoPath if the two structures aren't connected in
        the fetched subgraph -- treat that as a compliance violation
        ("no existing route between OLT and tie-in point"), not a crash."""
        path = nx.shortest_path(self.graph, src, dst, weight="length")
        length = nx.shortest_path_length(self.graph, src, dst, weight="length")
        return path, length


def build_route_graph(oh_routes: list[dict], ug_routes: list[dict], points: dict) -> RouteGraph:
    """
    Build a routing graph from oh_route / ug_route feature dicts (each with
    in_structure, out_structure, length) as returned by
    IQGeoClient.get_features_in_polygon(). `points` maps every structure id
    referenced by those routes to a (lon, lat) tuple, used later for
    distance-based tie-in / OLT selection.
    """
    g = nx.Graph()
    for route in (*oh_routes, *ug_routes):
        a, b = route["in_structure"], route["out_structure"]
        length = float(route.get("length") or 10.0)
        g.add_edge(a, b, length=length, route_id=route["id"])
    return RouteGraph(graph=g, points=points)


# ---------------------------------------------------------------------------
# Point-in-polygon / distance helpers (EPSG:4326 in, local metric projection
# for distance math -- avoids the "degrees are not meters" trap).
# ---------------------------------------------------------------------------

def polygon_from_geojson(geojson: dict) -> Polygon:
    return shape(geojson)


def polygon_from_wkt(wkt_str: str) -> Polygon:
    """
    Parse the polygon exactly as copied out of NMT, e.g.:
        POLYGON ((0.1364 52.2311, 0.1375 52.2317, ...))
    This is the normal path now -- a design's boundary in NMT is already
    stored/exportable as WKT (see package.metadata `boundary` in your
    cdiff sample), so there's no need to round-trip through GeoJSON.
    """
    return shapely_wkt.loads(wkt_str)


def parse_point(value) -> tuple[float, float]:
    """
    Normalises whatever shape a feature's geometry field comes back as into
    a plain (lon, lat) tuple. Confirmed shape for your instance's REST API
    is still open (see iqgeo_client.py) -- this accepts the three formats
    an IQGeo REST response is realistically going to use, so ingest_node
    doesn't have to guess:
      - [lon, lat] / (lon, lat)
      - {"type": "Point", "coordinates": [lon, lat]}  (GeoJSON)
      - "POINT (lon lat)"                              (WKT)
    """
    if isinstance(value, (list, tuple)):
        return float(value[0]), float(value[1])
    if isinstance(value, dict):
        lon, lat = value["coordinates"]
        return float(lon), float(lat)
    if isinstance(value, str):
        pt = shapely_wkt.loads(value)
        return pt.x, pt.y
    raise ValueError(f"unrecognised point geometry: {value!r}")


def _metric_transformer(centroid_lat: float):
    # Local azimuthal equidistant projection centred on the design polygon --
    # good enough for distances across a single FTTH design footprint.
    proj = pyproj.Transformer.from_crs(
        "EPSG:4326",
        f"+proj=aeqd +lat_0={centroid_lat} +lon_0=0 +units=m",
        always_xy=True,
    )
    return proj


def haversine_m(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Quick great-circle distance in metres, good enough for drop-length
    checks (sub-kilometre scale)."""
    lon1, lat1 = a
    lon2, lat2 = b
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    h = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlambda / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


def nearest_structure(candidates: list[str], points: dict, target: tuple[float, float]) -> str:
    return min(candidates, key=lambda sid: haversine_m(points[sid], target))


def polygon_to_flat_coords(polygon: Polygon) -> str:
    """
    Flatten a polygon's exterior ring into the "lon,lat,lon,lat,..." string
    /select_within expects for its `coords` param (confirmed against your
    instance's actual REST API reference -- see iqgeo_client.py).
    """
    return ",".join(f"{x},{y}" for x, y in polygon.exterior.coords)


def centroid(coords: list[tuple[float, float]]) -> tuple[float, float]:
    lon = sum(c[0] for c in coords) / len(coords)
    lat = sum(c[1] for c in coords) / len(coords)
    return lon, lat
