"""
Example entry points.

- run_demo()            MockIQGeoClient for structures/routes/OLTs (no
                         server needed for those), but addresses come from
                         the real /select_within endpoint, same as
                         run_for_test_design() -- no synthetic/random
                         points anywhere in this file anymore.
- run_for_test_design()  the real path: your actual "TestDesign" polygon,
                         run against a live IQGeoClient. Addresses (and
                         everything else) go through the confirmed
                         /select_within endpoint by default -- fill in
                         IQGEO_LAYER_FOR in config.py with the real layer
                         code(s) first. Pass use_db_for_addresses=True to
                         fetch addresses straight from Postgres instead
                         (db_client.py) if you'd rather sidestep
                         /select_within's response shape for now.

Run `python -m ftth_design_agent.main` to execute run_demo() by default.
"""

from __future__ import annotations

from langgraph.types import Command

from .graph import build_graph
from .iqgeo_client import IQGeoClient
from .mock_client import MockIQGeoClient
from . import db_client

# The exact design polygon from NMT (Design: TestDesign), copied straight
# out of the map -- this is the same WKT format as package.metadata's
# `boundary` in your cdiff sample, so no conversion is needed.
TEST_DESIGN_POLYGON_WKT = (
    "POLYGON ((0.136392251965408 52.23111191954027, "
    "0.1374950180243864 52.23169602875947, "
    "0.1388046267826676 52.230875626989274, "
    "0.1377036495382974 52.230192023305165, "
    "0.136392251965408 52.23111191954027))"
)


def _run(client, design_id: str, polygon_wkt: str, thread_id: str, auto_approve: bool = True, address_client=None):
    graph = build_graph(client, address_client=address_client)
    config = {"configurable": {"thread_id": thread_id}}
    initial_state = {
        "design_id": design_id,
        "polygon_wkt": polygon_wkt,
        "retry_count": 0,
    }

    result = graph.invoke(initial_state, config=config)

    if "__interrupt__" in result:
        print("\n=== paused for human review ===")
        print(result["__interrupt__"])
        if auto_approve:
            # In NMT this is where the plugin UI shows the BOM + violations
            # and the engineer clicks Approve / Edit / Reject -- simulating
            # "approve" here so the script runs end to end unattended.
            result = graph.invoke(Command(resume={"decision": "approved"}), config=config)
        else:
            return result  # caller decides when/how to resume

    print("\n=== final state ===")
    print("violations:", result.get("violations"))
    print("bom:", result.get("bom"))
    print("created_features:", {k: len(v) for k, v in result.get("created_features", {}).items()})
    print("review_status:", result.get("review_status"))
    return result


# ---------------------------------------------------------------------------
# Demo: MockIQGeoClient for structures/routes/OLTs (bundled sample slice,
# no server needed for those), but addresses are fetched for real via
# /select_within against your live instance -- no random/synthetic demand
# points. Note the bundled sample slice covers the "Woodhead Hub" area from
# your original cdiff export; this bounding box was chosen to sit inside
# that same coverage so the mocked structures/routes/OLTs resolve, but the
# address fetch itself hits your actual server for whatever's really there.
# ---------------------------------------------------------------------------

def _demo_polygon_wkt(bounds: dict) -> str:
    lo, la, hi_lo, hi_la = bounds["lon_min"], bounds["lat_min"], bounds["lon_max"], bounds["lat_max"]
    ring = f"{lo} {la}, {hi_lo} {la}, {hi_lo} {hi_la}, {lo} {hi_la}, {lo} {la}"
    return f"POLYGON (({ring}))"


def run_demo():
    bounds = {"lon_min": 0.1420, "lon_max": 0.1447, "lat_min": 52.2255, "lat_max": 52.2271}
    client = MockIQGeoClient()
    address_client = IQGeoClient()  # /select_within -- real addresses, no fabrication
    return _run(
        client, "design/DEMO-1", _demo_polygon_wkt(bounds), "demo-design-1",
        address_client=address_client,
    )


# ---------------------------------------------------------------------------
# Real run: your actual TestDesign polygon, against a live instance.
# ---------------------------------------------------------------------------

def run_for_test_design(use_db_for_addresses: bool = False):
    """
    Requires iqgeo_client.py's endpoint paths (and config.py's
    IQGEO_BASE_URL / credentials) to actually match your instance first --
    see the README.

    Addresses default to going through the REST /select_within endpoint
    (the same call IQGeoClient uses for structures/routes/OLTs) now that
    it's confirmed to exist on this instance -- fill in IQGEO_LAYER_FOR in
    config.py with the real layer code(s) first. Pass
    use_db_for_addresses=True to fetch them straight from Postgres instead
    (db_client.fetch_addresses_in_polygon) if you'd rather not depend on
    /select_within's response shape being confirmed yet -- both paths
    filter out already-served addresses, never random/synthetic points.
    """
    client = IQGeoClient()

    if use_db_for_addresses:
        conn = db_client.connect()
        address_client = db_client.DBAddressClient(conn)
    else:
        conn = None
        address_client = None  # None -> ingest_node falls back to `client` (REST) for addresses too

    try:
        return _run(
            client,
            design_id="design/TestDesign",
            polygon_wkt=TEST_DESIGN_POLYGON_WKT,
            thread_id="test-design-1",
            auto_approve=False,  # a real design should get an actual human look
                                 # before "approved" -- resume manually once you've
                                 # reviewed the interrupt payload's BOM/violations:
                                 #   graph.invoke(Command(resume={"decision": "approved"}), config)
            address_client=address_client,
        )
    finally:
        if conn is not None:
            conn.close()


if __name__ == "__main__":
    run_demo()
