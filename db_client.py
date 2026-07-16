"""
Direct database access for addresses.

You already showed a working SQL row against the real schema:

    id | name | street_number | street_name | city | postcode | building |
    location | service_status | serving_equipment | serving_structure

so rather than routing the address lookup through the (still-unconfirmed)
REST API, this queries the IQGeo Platform database directly with PostGIS
and returns rows already shaped like the rest of the pipeline expects.

`fetch_addresses_in_polygon` is the function you asked for. `DBAddressClient`
is a thin wrapper around it that exposes the same `get_features_in_polygon`
method IQGeoClient/MockIQGeoClient use, so it drops straight into
`build_graph(client, address_client=DBAddressClient(conn))` (see main.py)
without touching nodes.py.
"""

from __future__ import annotations

import psycopg2
import psycopg2.extras

from .config import DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD, ADDRESS_TABLE


def connect():
    """Confirm DB_* in config.py against your instance before using this --
    same caveat as IQGEO_BASE_URL in iqgeo_client.py."""
    return psycopg2.connect(
        host=DB_HOST, port=DB_PORT, dbname=DB_NAME, user=DB_USER, password=DB_PASSWORD,
    )


def fetch_addresses_in_polygon(
    conn, polygon_wkt: str, unserved_only: bool = True
) -> list[dict]:
    """
    Fetch every address whose location falls inside polygon_wkt, straight
    from the database (PostGIS ST_Within) -- no synthetic/random points.

    unserved_only=True (the default) filters to addresses that don't
    already have serving_equipment/serving_structure set -- i.e. actual
    FTTH demand points, not premises that are already connected. Set it to
    False if you want every address in the polygon regardless of current
    service state (e.g. for a coverage audit rather than a new build).

    Returns dicts shaped for the rest of the pipeline:
        {"address_id": "address/216", "lon": ..., "lat": ...,
         "name": ..., "street_number": ..., "street_name": ..., "city": ...,
         "postcode": ..., "building": ..., "service_status": ...,
         "serving_equipment": ..., "serving_structure": ...}
    """
    query = f"""
        SELECT id, name, street_number, street_name, city, postcode,
               building, ST_AsText(location) AS location, service_status,
               serving_equipment, serving_structure
        FROM {ADDRESS_TABLE}
        WHERE ST_Within(location, ST_GeomFromText(%s, 4326))
    """
    if unserved_only:
        query += " AND serving_equipment IS NULL AND serving_structure IS NULL"

    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(query, (polygon_wkt,))
        rows = cur.fetchall()

    results = []
    for row in rows:
        row = dict(row)
        # location comes back as "POINT (lon lat)" text (ST_AsText) --
        # geometry.parse_point already handles that WKT string form, so
        # ingest_node doesn't need a separate code path for DB vs REST.
        loc = row.pop("location")
        row_id = row.pop("id")
        results.append({
            "address_id": f"address/{row_id}",
            "location": loc,
            **row,
        })
    return results


class DBAddressClient:
    """
    Adapter so a plain DB connection can stand in for the `address_client`
    argument to build_graph()/make_nodes() -- only `get_features_in_polygon`
    for feature_type="address" is implemented; anything else raises, since
    structures/routes/OLTs still come from IQGeoClient (REST) in this setup.
    """

    def __init__(self, conn, unserved_only: bool = True):
        self.conn = conn
        self.unserved_only = unserved_only

    def get_features_in_polygon(self, feature_type: str, polygon_wkt: str, limit: int = 2000) -> list[dict]:
        if feature_type != "address":
            raise NotImplementedError(
                f"DBAddressClient only serves 'address' -- got {feature_type!r}. "
                f"Pass the REST IQGeoClient as `client` for everything else."
            )
        return fetch_addresses_in_polygon(self.conn, polygon_wkt, self.unserved_only)
