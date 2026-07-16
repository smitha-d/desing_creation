"""
Configuration for the FTTH design agent.

Everything an operator is likely to want to tune (endpoint paths, design
rules, cost table) lives here rather than scattered through the node code.

IMPORTANT - REST endpoint paths:
IQGeo's public docs confirm the *capabilities* (feature CRUD, spatial query,
port info GET/POST, network trace, design impact report) but not a single
fixed path across every deployment/version. The paths below are placeholders
in the common IQGeo Anywhere / myWorld REST convention
(``/{module}/rest/{feature_type}``). Before running this against your
instance, open your local server's REST API reference (Help Center for your
installed version, or a ``/doc`` route on your own host) and correct
``IQGEO_BASE_URL`` and the path templates in ``iqgeo_client.py`` to match.
Do not trust the defaults here as verified truth for your install.
"""

from dataclasses import dataclass, field


# ---------------------------------------------------------------------------
# IQGeo connection
# ---------------------------------------------------------------------------

IQGEO_BASE_URL = "http://localhost:8096"          # confirm for your instance
IQGEO_APP = "mywcom"                              # the Comms application name
IQGEO_USERNAME = "admin"
IQGEO_PASSWORD = "_mywWorld_"                      # prefer an env var / secret store in production

# /select_within is a confirmed real endpoint on this instance (per its own
# REST API reference): GET /select_within?coords=...&zoom=...&layers=...
# &types=...&limit=... -- see iqgeo_client.get_features_in_polygon.
#
# `layers` takes layer codes (a layer can bundle several feature types --
# e.g. one "Comms Structures" layer might hold manhole+pole+cabinet+building
# together), and `types` narrows the result to specific feature type(s)
# within those layers. IQGEO_LAYER_FOR maps each feature_type this agent
# queries to the layer code it lives in -- check the Layers panel in NMT
# (left sidebar) for the real codes and fill these in; "_default" is a
# fallback for any feature_type not listed explicitly.
IQGEO_LAYER_FOR = {
    "address": "a,dn",          # confirmed working for addresses (Postman-tested)
    "_default": "a,dn",         # only confirmed for addresses so far -- spot-check
                                 # this actually returns manhole/pole/cabinet/route/
                                 # fiber_olt features too before trusting it for those
}

# zoom is a required param on /select_within -- it's the map zoom level to
# evaluate scale-dependent layer/feature visibility at. 21 returned an empty
# FeatureCollection for addresses even though they exist in the polygon --
# turned out to be too high (features weren't configured visible at that
# scale). 16 is confirmed working for addresses; spot-check it for other
# feature types too, since scale-dependent visibility can differ per layer.
IQGEO_QUERY_ZOOM = 16


# ---------------------------------------------------------------------------
# FTTH design rules (stand-in for the RAG-backed compliance store described
# in the PoC blueprint -- swap `rules.audit()` to query pgvector/LlamaIndex
# once the SOP corpus is loaded; the interface stays the same).
# ---------------------------------------------------------------------------

@dataclass
class FTTHRules:
    # PON splitter ratios available, in ascending order
    splitter_ratios: tuple = (4, 8, 16, 32, 64)

    # Typical two-stage insertion loss per ratio (dB) -- replace with your
    # actual specification sheet values (fiber_splitter_spec in your schema).
    splitter_loss_db: dict = field(default_factory=lambda: {
        4: 7.25, 8: 10.5, 16: 13.7, 32: 17.5, 64: 21.0,
    })

    fiber_cable_loss_db_per_km: float = 0.35   # matches fiber_cable.loss in sample export
    splice_loss_db: float = 0.30               # per splice/connector, conservative estimate
    gpon_loss_budget_db: float = 28.0          # class B+ typical; confirm against your spec
    max_drop_length_m: float = 250.0           # confirm against your construction standards
    max_retries: int = 3                       # bounded retry for the compliance loop


@dataclass
class CostTable:
    fiber_cable_per_m: float = 1.80
    splitter_unit: float = 220.0
    splice_closure_unit: float = 340.0
    ont_unit: float = 95.0
    labor_per_splice: float = 45.0


RULES = FTTHRules()
COSTS = CostTable()


# ---------------------------------------------------------------------------
# Direct database access (PostGIS) -- used for address lookups instead of
# going through the REST API. IQGeo Platform's default backend is
# PostgreSQL/PostGIS; confirm host/port/dbname/schema for your install
# (a local Comms install commonly runs Postgres alongside the app server on
# the same box). ADDRESS_TABLE assumes a flat "address" table/view exposing
# the columns you get back from a plain SQL query -- id, name,
# street_number, street_name, city, postcode, building, location,
# service_status, serving_equipment, serving_structure -- adjust the schema
# prefix (e.g. "public.address" or "myworld.address") if yours differs.
# ---------------------------------------------------------------------------

DB_HOST = "localhost"
DB_PORT = 5432
DB_NAME = "myworld"                # confirm for your instance
DB_USER = "myworld"
DB_PASSWORD = "CHANGE_ME"          # prefer an env var / secret store in production
ADDRESS_TABLE = "address"          # confirm table/view name and schema prefix
