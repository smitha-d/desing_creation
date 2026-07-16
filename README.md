# FTTH Design Agent (LangGraph + IQGeo NMT)

Turns a design polygon drawn in Network Manager Telecom (Comms) into a
complete, compliance-checked FTTH design: splice closure, splitter(s),
feeder + drop fiber cable, ONTs, and end-to-end FTTH circuits, plus a BOM
and cost estimate -- gated by a human review step before anything moves to
"Build".

## Files

- `config.py` -- connection settings + design rules (splitter ratios, loss
  budget, max drop length, cost table). Start here.
- `iqgeo_client.py` -- REST adapter over NMT. Spatial queries now use the
  confirmed `/select_within` endpoint (coords/zoom/layers/types params, per
  your instance's own REST API reference) -- fill in `IQGEO_LAYER_FOR` in
  `config.py` with the real layer code(s) and check the response-shape
  parser (`_parse_select_within_response`) once you've seen a real payload.
  Feature *create/update* paths are still placeholders -- confirm those
  separately (see the module docstring).
- `geometry.py` -- shapely/networkx helpers: WKT polygon parsing, point
  normalisation, route graph, shortest path, distance.
- `rules.py` -- splitter-tree sizing, loss-budget/drop-length compliance
  audit, BOM cost rollup. This is the stand-in for the RAG-backed
  compliance node described in the original PoC blueprint -- same function
  signatures, swap the internals for a vector-store lookup later.
- `state.py` -- the LangGraph state schema (`FTTHDesignState`).
- `nodes.py` -- the node functions: ingest, route planner, compliance
  audit, feature writer, BOM, human review.
- `graph.py` -- wires the nodes into the state graph with the bounded
  retry loop and the human-review interrupt.
- `db_client.py` -- fetches real addresses straight from the database
  (PostGIS `ST_Within`) instead of guessing at a REST endpoint for them --
  `fetch_addresses_in_polygon(conn, polygon_wkt)` is the function you
  asked for, and `DBAddressClient` wraps it to drop into `build_graph()`
  as `address_client`. Filters to unserved addresses by default (real
  demand points, not premises already connected).
- `mock_client.py` + `sample_data/sample_network.json` -- an offline
  IQGeoClient stand-in backed by a real (trimmed) slice of the topology
  from your `design_20260716_170750.zip` sample, so you can run the whole
  pipeline with no server before pointing it at your instance.
- `cdiff_export.py` -- optional: write the agent's proposed features out
  as a cdiff package in the same shape as your sample export, for a human
  to review/import through NMT's normal package-import flow instead of
  live REST writes.
- `main.py` -- runnable examples (`run_demo()` and `run_for_test_design()`).

## Try it offline first

```
pip install -r requirements.txt
python -m ftth_design_agent.main
```

This runs `main.run_demo()`: structures/routes/OLTs come from the bundled
sample network (no server needed for those), but addresses are fetched for
real via `/select_within` against your live instance -- there are no
synthetic/random demand points anywhere in this codebase. It prints the
review-gate payload (BOM + violations), "approves" it, and prints the
final state. You do need `IQGEO_BASE_URL` reachable for the address fetch
even in this "demo" -- it's here to sanity-check the routing/compliance/BOM
logic against real demand points before running the full thing against
TestDesign.

## Running it against your actual TestDesign polygon

The graph's input is now just `polygon_wkt` -- the design boundary copied
straight out of NMT, same format as `boundary` in your cdiff sample's
`package.metadata`. `main.py` has `TEST_DESIGN_POLYGON_WKT` set to your
actual TestDesign polygon and a `run_for_test_design()` entry point wired
to the real `IQGeoClient` (not the mock -- the bundled sample topology is
from a different part of the map and doesn't cover this polygon).

Addresses default to going through `/select_within` -- the same documented
endpoint (`GET /select_within?coords=...&zoom=...&layers=...&types=...`)
used for structures/routes/OLTs -- now that it's confirmed to exist on
this instance. `db_client.py` (direct PostGIS query, matching the raw SQL
row you shared: id, name, street_number, street_name, city, postcode,
building, location, service_status, serving_equipment, serving_structure)
remains available as an alternative if you'd rather not depend on
`/select_within`'s exact response shape yet -- pass
`run_for_test_design(use_db_for_addresses=True)`.

Before running it:

1. Fill in `IQGEO_LAYER_FOR` in `config.py` with the real layer code(s) for
   your instance (check the Layers panel in NMT), and confirm
   `IQGEO_QUERY_ZOOM` is a sensible value for your deployment.
2. Update `IQGEO_BASE_URL` / credentials in `config.py`, and confirm the
   feature create/update paths in `iqgeo_client.py` separately (those are
   still placeholders -- `/select_within` only covers reads).
3. Run once and inspect the actual `/select_within` JSON payload -- adjust
   `_parse_select_within_response()` in `iqgeo_client.py` if the keys don't
   match its best-effort guess.
4. `python -c "from ftth_design_agent.main import run_for_test_design; run_for_test_design()"`
   (or `run_for_test_design(use_db_for_addresses=True)` to source addresses
   from Postgres instead while you're still confirming step 3).

Either way, `db_client.py`'s `DB_*` settings in `config.py` only need
filling in if you actually use `use_db_for_addresses=True`.

This does *not* auto-approve at the review gate (unlike `run_demo()`) --
it prints the BOM and any violations and waits. Resume it with:

```python
from langgraph.types import Command
graph.invoke(Command(resume={"decision": "approved"}), config)
```

using the same `config` (`thread_id="test-design-1"`) `_run()` used.

## Known simplifications / TODOs before production use

- **Endpoint paths** in `iqgeo_client.py` are placeholders -- confirm
  against your instance.
- **Greenfield siting is out of scope.** The route planner only ties into
  *existing* manholes/poles/cabinets already on the routing graph. If no
  existing structure is near the new demand cluster, it raises a
  violation for a human to site a new structure, rather than guessing
  coordinates.
- **Conduit capacity isn't checked.** `mywcom_fiber_segment.housing` should
  reference a specific conduit with spare capacity in each hop; the code
  currently houses each segment directly on the route. Wire in a
  `select_conduit_for_route()` capacity check before writing to a live
  design.
- **ONT housing** currently reuses the tie-in structure as a placeholder;
  it should resolve (or create) the demand point's own wall_box/building --
  left as an explicit TODO in `nodes.py` rather than silently wrong.
- **Fiber strand/tick allocation** (`strand_info`, `in_tick`/`out_tick` in
  the sample export) isn't populated -- add an allocator if you need
  strand-level accuracy rather than just topological connectivity.
- The **loss budget and splitter loss table** are reasonable GPON defaults,
  not your actual spec sheet numbers -- update `config.py`.
