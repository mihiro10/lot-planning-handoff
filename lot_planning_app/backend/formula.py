from datetime import date, timedelta
from typing import Dict, List, Optional, Tuple

RawValues = Dict[Tuple[int, str], Optional[float]]  # (row_type_id, date_iso) -> value


def compute_product_grid(
    dates: List[date],
    starting_inventory: float,
    lot_size: float,
    lead_time: int,
    raw_values: RawValues,
    row_type_roles: Dict[int, str],  # row_type_id -> role
) -> Dict[Tuple[int, str], float]:
    """
    Compute derived rows (入庫予定数, 最終) for one product over the given dates.
    Returns computed values keyed by (row_type_id, date_iso).
    Raw manual values are not included — only calculated ones.
    """
    plan_rtid        = _find_role(row_type_roles, "plan")
    yotei_rtid       = _find_role(row_type_roles, "inbound_planned")
    actual_in_rtid   = _find_role(row_type_roles, "inbound_actual")
    forecast_rtid    = _find_role(row_type_roles, "demand_forecast")
    adjustment_rtid  = _find_role(row_type_roles, "adjustment")
    final_rtid       = _find_role(row_type_roles, "final")
    demand_rtids     = [k for k, v in row_type_roles.items() if v == "demand_actual"]

    computed: Dict[Tuple[int, str], float] = {}
    prev_final = starting_inventory

    for d in dates:
        ds = d.isoformat()

        # 入庫予定数: look up 計画（倍） on (date - lead_time) * lot_size
        plan_date = d - timedelta(days=lead_time)
        plan_val = raw_values.get((plan_rtid, plan_date.isoformat())) if plan_rtid else None
        inbound_planned = (plan_val * lot_size) if plan_val else 0.0
        if yotei_rtid is not None:
            computed[(yotei_rtid, ds)] = inbound_planned

        # 入庫: actual overrides planned
        inbound_actual = raw_values.get((actual_in_rtid, ds)) if actual_in_rtid else None
        inbound = inbound_actual if inbound_actual is not None else inbound_planned

        # demand: sum demand_actual rows if any have values, else use forecast
        demand_vals = [
            v for rtid in demand_rtids
            if (v := raw_values.get((rtid, ds))) is not None
        ]
        if demand_vals:
            demand = sum(demand_vals)
        else:
            demand = (raw_values.get((forecast_rtid, ds)) or 0.0) if forecast_rtid else 0.0

        adjustment = (raw_values.get((adjustment_rtid, ds)) or 0.0) if adjustment_rtid else 0.0

        final_val = prev_final + inbound - demand + adjustment
        if final_rtid is not None:
            computed[(final_rtid, ds)] = final_val

        prev_final = final_val

    return computed


def _find_role(roles: Dict[int, str], role: str) -> Optional[int]:
    return next((k for k, v in roles.items() if v == role), None)
