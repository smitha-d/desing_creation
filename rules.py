"""
Design-rule engine: splitter sizing, loss-budget / drop-length compliance,
and BOM cost rollup.

This is the stand-in for the RAG-backed "Knowledge-Infused Policy
Validation" node in the PoC blueprint. For the PoC it's a plain rule table
(config.RULES); production should swap `audit()` to retrieve the actual
numbers from your embedded SOP/spec corpus (LlamaIndex/pgvector) so the
agent picks up spec changes without a code change. The function signatures
below are written so that swap doesn't touch node.py at all.
"""

from __future__ import annotations

from .config import FTTHRules, CostTable
from .geometry import haversine_m


def plan_splitter_tree(n_demand: int, rules: FTTHRules) -> list[dict]:
    """
    Decide splitter tiering for n_demand ONTs. Single-stage if a ratio
    covers all of them; otherwise a 4-way primary feeding 32-way
    secondaries (the common two-stage GPON pattern). Extend this if your
    network standard uses a different split strategy.
    """
    for ratio in rules.splitter_ratios:
        if n_demand <= ratio:
            return [{"stage": 1, "ratio": ratio, "fanout": n_demand}]

    primary_ratio = 4
    secondary_ratio = 32
    n_secondaries = -(-n_demand // secondary_ratio)  # ceil division
    if n_secondaries > primary_ratio:
        raise ValueError(
            f"{n_demand} demand points exceed what a single OLT port can "
            f"serve at {primary_ratio}x{secondary_ratio} split -- split the "
            f"design polygon into multiple feeder runs / OLT ports."
        )
    return [{"stage": 1, "ratio": primary_ratio, "fanout": primary_ratio}] + [
        {"stage": 2, "ratio": secondary_ratio, "fanout": secondary_ratio}
        for _ in range(n_secondaries)
    ]


def estimate_loss_db(
    feeder_length_m: float,
    splitter_tree: list[dict],
    n_splices: int,
    rules: FTTHRules,
) -> float:
    feeder_km = feeder_length_m / 1000.0
    splitter_loss = sum(rules.splitter_loss_db[stage["ratio"]] for stage in splitter_tree)
    return (
        feeder_km * rules.fiber_cable_loss_db_per_km
        + splitter_loss
        + n_splices * rules.splice_loss_db
    )


def audit(state: dict, rules: FTTHRules) -> list[str]:
    """
    Returns a list of human-readable violation strings; empty list means
    the design passes. Called by nodes.compliance_audit_node.
    """
    violations: list[str] = []

    if state.get("feeder_path") is None:
        violations.append("no route path found between selected OLT and tie-in structure")
        return violations  # nothing else to check without a feeder path

    n_splices = max(len(state["feeder_path"]) - 1, 0)
    loss = estimate_loss_db(
        state["feeder_length_m"], state["splitter_tree"], n_splices, rules
    )
    state["estimated_loss_db"] = loss
    if loss > rules.gpon_loss_budget_db:
        violations.append(
            f"estimated end-to-end loss {loss:.2f} dB exceeds budget "
            f"{rules.gpon_loss_budget_db} dB"
        )

    tie_in_point = state["tie_in_point"]
    for dp in state["demand_points"]:
        # NOTE: mutates the demand-point dicts in place. compliance_audit_node
        # calls this with a shallow `dict(state)` copy, so `demand_points`
        # is the *same* list/dict objects as the real graph state -- this
        # is how `drop_length_m` ends up available to feature_writer_node
        # and bom_node later without route_planner/compliance_audit needing
        # to explicitly return the whole list back through LangGraph.
        d = haversine_m(tie_in_point, (dp["lon"], dp["lat"]))
        dp["drop_length_m"] = d
        if d > rules.max_drop_length_m:
            violations.append(
                f"{dp['address_id']} drop length {d:.1f} m exceeds max "
                f"{rules.max_drop_length_m} m"
            )

    total_fanout = sum(stage["fanout"] for stage in state["splitter_tree"] if stage["stage"] == max(
        s["stage"] for s in state["splitter_tree"]))
    if total_fanout < len(state["demand_points"]):
        violations.append(
            f"splitter plan covers {total_fanout} ports but there are "
            f"{len(state['demand_points'])} demand points"
        )

    return violations


def compute_bom(state: dict, costs: CostTable) -> dict:
    feeder_m = state["feeder_length_m"]
    drop_m = sum(dp["drop_length_m"] for dp in state["demand_points"])
    n_splitters = len(state["splitter_tree"])
    n_ont = len(state["demand_points"])
    n_splices = max(len(state["feeder_path"]) - 1, 0)

    cost = (
        (feeder_m + drop_m) * costs.fiber_cable_per_m
        + n_splitters * costs.splitter_unit
        + costs.splice_closure_unit  # one new closure at the tie-in point
        + n_ont * costs.ont_unit
        + n_splices * costs.labor_per_splice
    )

    return {
        "feeder_cable_m": round(feeder_m, 1),
        "drop_cable_m": round(drop_m, 1),
        "splitters": state["splitter_tree"],
        "splice_closures_new": 1,
        "onts": n_ont,
        "estimated_loss_db": state.get("estimated_loss_db"),
        "estimated_cost_usd": round(cost, 2),
    }
