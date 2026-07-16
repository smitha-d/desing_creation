"""
Optional exporter: write the agent's proposed features out as a cdiff
package -- the same folder/CSV/.fields structure as
design_20260716_170750.zip -- instead of (or in addition to) calling
IQGeoClient.create_feature() directly.

Why this exists: writing straight to a live design via REST is the fast
path, but a cdiff package can be handed to an engineer to import through
NMT's normal package-import UI after eyeballing it, which is a gentler
rollout for a first PoC than live writes. Point this at the `records` dict
returned by nodes.feature_writer_node (state["_records"]) after a run.

Column lists below are transcribed directly from the .fields files inside
your sample export -- if your instance's schema has custom fields added on
top, extend FIELDS_BY_TABLE accordingly.
"""

from __future__ import annotations

import csv
import os
from pathlib import Path

# table_name -> (folder, [ordered field names])   (matches package.metadata layout)
TABLE_LAYOUT = {
    "splice_closure": ("equipment", [
        "id", "name", "equipment", "root_housing", "housing", "specification",
        "labor_costs", "created_at", "create_user", "updated_at", "update_user",
        "location", "loss", "circuits", "sort_children", "design_id",
        "installation_date", "job_id", "isp_pos_total", "isp_pos_order",
        "isp_pos_in_housing", "isp_pos_occupancy", "isp_pos_side",
    ]),
    "fiber_splitter": ("equipment", [
        "id", "name", "n_fiber_in_ports", "n_fiber_out_ports", "root_housing",
        "housing", "specification", "labor_costs", "created_at", "create_user",
        "updated_at", "update_user", "location", "offset_geom", "circuits",
        "directed", "loss", "fiber_in_ports_info", "fiber_out_ports_info",
        "sort_children", "capacity_threshold", "design_id", "device_id",
        "installation_date", "job_id", "service_status", "stop_ripple",
        "isp_boundary", "myw_gwn_isp_boundary", "isp_location",
        "myw_orientation_isp_location", "myw_gwn_isp_location", "isp_x_length",
        "isp_y_length", "isp_pos_total", "isp_pos_order", "isp_pos_in_housing",
        "isp_pos_occupancy", "isp_pos_side",
    ]),
    "fiber_ont": ("equipment", [
        "id", "name", "n_fiber_in_ports", "root_housing", "housing",
        "specification", "labor_costs", "created_at", "create_user",
        "updated_at", "update_user", "location", "offset_geom", "circuits",
        "directed", "loss", "fiber_in_ports_info", "sort_children",
        "capacity_threshold", "design_id", "installation_date", "job_id",
        "service_status", "isp_boundary", "myw_gwn_isp_boundary",
        "isp_location", "myw_orientation_isp_location", "myw_gwn_isp_location",
        "isp_x_length", "isp_y_length",
    ]),
    "fiber_cable": ("cables", [
        "id", "name", "specification", "type", "fiber_count", "directed",
        "created_at", "create_user", "updated_at", "update_user", "path",
        "placement_path", "offset_geom", "tick_mark_spacing", "loss",
        "labor_costs", "design_id", "owner", "job_id", "installation_date",
        "diameter",
    ]),
    "mywcom_fiber_segment": ("cables", [
        "id", "length", "cable", "housing", "root_housing", "directed",
        "forward", "in_structure", "out_structure", "in_segment", "out_segment",
        "path", "strand_info", "in_tick", "out_tick", "circuits",
        "in_equipment", "out_equipment", "capacity_threshold", "design_id",
    ]),
    "mywcom_fiber_connection": ("connections", [
        "id", "in_object", "out_object", "in_side", "in_low", "in_high",
        "out_side", "out_low", "out_high", "splice", "root_housing", "housing",
        "location", "design_id",
    ]),
    "ftth_circuit": ("circuits", [
        "id", "name", "in_feature", "in_pins", "out_feature", "out_pins",
        "created_at", "create_user", "updated_at", "update_user", "path",
        "connected", "design_id", "status", "customer", "address",
        "service_type", "date",
    ]),
}


def write_cdiff_package(
    records_by_type: dict[str, dict[str, dict]],
    out_dir: str,
    boundary_wkb_hex: str = "",
    coord_system: int = 4326,
) -> None:
    """
    records_by_type: {"fiber_splitter": {"fiber_splitter/AUTO-1": {field: value, ...}}, ...}
    (this is exactly state["_records"] from feature_writer_node)
    """
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    with open(out / "package.metadata", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["property", "value"])
        w.writerow(["format", "cdif"])
        w.writerow(["coord_system", coord_system])
        w.writerow(["boundary", boundary_wkb_hex])

    for table, records in records_by_type.items():
        if table not in TABLE_LAYOUT:
            # unknown table -- write it anyway with whatever columns showed
            # up, so nothing is silently dropped, but flag it.
            print(f"warning: {table} has no known column layout, writing "
                  f"columns as encountered")
            folder, fields = "other", sorted({k for r in records.values() for k in r})
        else:
            folder, fields = TABLE_LAYOUT[table]

        table_dir = out / folder
        table_dir.mkdir(parents=True, exist_ok=True)

        with open(table_dir / f"{table}.fields", "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["name", "type", "unit"])
            for name in fields:
                w.writerow([name, "", ""])  # types omitted -- see the
                # original .fields files from your export for the real
                # type/unit metadata if your import step requires it

        with open(table_dir / f"{table}.csv", "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(fields)
            for record in records.values():
                w.writerow([_serialise(record.get(f, "")) for f in fields])

    print(f"wrote cdiff package to {out.resolve()}")


def _serialise(value) -> str:
    if isinstance(value, (list, tuple)):
        return ";".join(str(v) for v in value)
    if value is None:
        return ""
    return str(value)
