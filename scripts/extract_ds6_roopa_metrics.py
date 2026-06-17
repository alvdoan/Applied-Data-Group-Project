"""
Extract Roopa DS6 (Acquisition Dynamics) add-ons into forecast-ready JSON.

Outputs:
- outputs/ds6_roopa_metrics.json

Includes:
- holiday churn-trap windows (11.11, BFCM, 12.12) with repeat-rate multipliers vs baseline
- scenario simulation (Status Quo leakage, Risk, Pivot recovery) from DS6 Part 4

Run (from project root):
  python scripts/extract_ds6_roopa_metrics.py
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd


BASE = Path(__file__).resolve().parent.parent
GOLD_DIR = BASE / "medallion" / "gold"
OUT_DIR = BASE / "outputs"

# Roopa notebook scenario assumptions (DS6 Part 4)
PIVOT_GAP_CLOSURE_PCT = 0.50
RISK_DISCOUNT_ACQ_RATE = 0.10
COHORT_CYCLES_PER_YEAR = 4


def load_first_orders_roopa_style(da: pd.DataFrame) -> pd.DataFrame:
    """First retail orders — same filters and promo_group rules as DS6 notebook."""
    first_orders = da[
        (da["is_first_order"] == True) & (da["is_b2b_or_affiliate"] == False)
    ].drop_duplicates(subset=["customer_id"])

    first_orders["promo_group"] = "Other/Small Discount"
    first_orders.loc[first_orders["discount_type"].isna(), "promo_group"] = "Full Price"
    first_orders.loc[first_orders["is_high_magnitude"] == True, "promo_group"] = "High Magnitude (30%+)"

    first_orders["repeat_purchase_90d"] = pd.to_numeric(
        first_orders["repeat_purchase_90d"], errors="coerce"
    )
    return first_orders


def _flag_holiday(ts: pd.Timestamp) -> str:
    if pd.isna(ts):
        return "Baseline"
    d = ts.to_pydatetime()
    if d.month == 11 and d.day == 11:
        return "11.11"
    if d.month == 12 and d.day == 12:
        return "12.12"
    if d.month == 11 and d.day >= 24:
        return "BFCM"
    return "Baseline"


def _build_scenario_simulation(first_orders: pd.DataFrame, gap: float, aov: float) -> dict:
    """
    Replicate Roopa DS6 Part 4 scenario simulation.

    Uses observed discount-acquisition rate on the full first-order base and
    Roopa's placeholder annual new-customer projection (13,715) for forward scenarios.
    """
    total_first_orders = int(first_orders["customer_id"].nunique())
    high_mag_customers = int(
        first_orders.loc[
            first_orders["promo_group"] == "High Magnitude (30%+)", "customer_id"
        ].nunique()
    )
    current_discount_rate = high_mag_customers / total_first_orders if total_first_orders else 0.0

    # Roopa notebook placeholder — conservative: same annual base as historical cohort
    projected_new_customers_2026 = total_first_orders

    sq_discount_cohort = int(round(projected_new_customers_2026 * current_discount_rate))
    sq_lost_repeats = int(round(sq_discount_cohort * gap))
    sq_lost_revenue = float(sq_lost_repeats * aov)
    sq_annual = float(sq_lost_revenue * COHORT_CYCLES_PER_YEAR)

    risk_discount_cohort = int(round(projected_new_customers_2026 * RISK_DISCOUNT_ACQ_RATE))
    risk_lost_repeats = int(round(risk_discount_cohort * gap))
    risk_lost_revenue = float(risk_lost_repeats * aov)
    risk_annual = float(risk_lost_revenue * COHORT_CYCLES_PER_YEAR)

    pivot_gap = gap * PIVOT_GAP_CLOSURE_PCT
    pivot_recovered_repeats = int(round(sq_discount_cohort * pivot_gap))
    pivot_recovered_revenue = float(pivot_recovered_repeats * aov)
    pivot_annual = float(pivot_recovered_revenue * COHORT_CYCLES_PER_YEAR)

    return {
        "aov": aov,
        "promo_gap_pp": gap,
        "projected_new_customers_2026": projected_new_customers_2026,
        "discount_acq_rate_status_quo": current_discount_rate,
        "discount_acq_rate_risk": RISK_DISCOUNT_ACQ_RATE,
        "pivot_gap_closure_pct": PIVOT_GAP_CLOSURE_PCT,
        "status_quo": {
            "discount_cohort_size": sq_discount_cohort,
            "lost_repeat_customers": sq_lost_repeats,
            "lost_revenue_per_cohort": sq_lost_revenue,
            "annual_leakage": sq_annual,
        },
        "risk": {
            "discount_cohort_size": risk_discount_cohort,
            "lost_repeat_customers": risk_lost_repeats,
            "lost_revenue_per_cohort": risk_lost_revenue,
            "annual_leakage": risk_annual,
        },
        "pivot": {
            "recovered_repeat_customers": pivot_recovered_repeats,
            "recovered_revenue_per_cohort": pivot_recovered_revenue,
            "annual_recovery": pivot_annual,
        },
        # Forecast wiring: spread Pivot recovery evenly across 2026 months.
        "annual_recovery_pivot": pivot_annual,
    }


def main() -> dict:
    OUT_DIR.mkdir(exist_ok=True)

    da = pd.read_parquet(GOLD_DIR / "gold_discount_analysis.parquet")
    first_orders = load_first_orders_roopa_style(da)

    aov_col = "price_total_sgd" if "price_total_sgd" in first_orders.columns else "price_total"
    aov = float(first_orders[aov_col].mean())

    full_price_rate = float(
        first_orders.loc[first_orders["promo_group"] == "Full Price", "repeat_purchase_90d"].mean()
    )
    high_mag_rate = float(
        first_orders.loc[
            first_orders["promo_group"] == "High Magnitude (30%+)", "repeat_purchase_90d"
        ].mean()
    )
    gap = full_price_rate - high_mag_rate

    first_orders = first_orders.copy()
    first_orders["processed_at"] = pd.to_datetime(
        first_orders["processed_at"], errors="coerce", utc=True
    ).dt.tz_localize(None)
    first_orders["repeat_purchase_90d"] = pd.to_numeric(
        first_orders["repeat_purchase_90d"], errors="coerce"
    )
    first_orders["one_and_done"] = (
        pd.to_numeric(first_orders["total_orders"], errors="coerce").fillna(0) <= 1
    )
    first_orders["holiday_window"] = first_orders["processed_at"].apply(_flag_holiday)

    grp = (
        first_orders.groupby("holiday_window")
        .agg(
            n=("customer_id", "nunique"),
            one_and_done_rate=("one_and_done", "mean"),
            repeat_rate_90d=("repeat_purchase_90d", "mean"),
        )
        .fillna(0)
    )

    baseline = (
        grp.loc["Baseline"]
        if "Baseline" in grp.index
        else pd.Series({"n": 0, "one_and_done_rate": 0, "repeat_rate_90d": 0})
    )

    windows: dict[str, dict] = {}
    for w in ["11.11", "BFCM", "12.12"]:
        if w not in grp.index:
            continue
        row = grp.loc[w]
        repeat_vs_baseline = (
            float(row["repeat_rate_90d"] / baseline["repeat_rate_90d"])
            if baseline["repeat_rate_90d"]
            else 1.0
        )
        windows[w] = {
            "n": int(row["n"]),
            "one_and_done_rate": float(row["one_and_done_rate"]),
            "repeat_rate_90d": float(row["repeat_rate_90d"]),
            "repeat_vs_baseline": repeat_vs_baseline,
            "delta_one_and_done_pp": float((row["one_and_done_rate"] - baseline["one_and_done_rate"]) * 100),
            "delta_repeat_pp": float((row["repeat_rate_90d"] - baseline["repeat_rate_90d"]) * 100),
        }

    scenario_simulation = _build_scenario_simulation(first_orders, gap, aov)

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "source_table": "gold_discount_analysis.parquet",
        "filters": {"is_first_order": True, "is_b2b_or_affiliate": False},
        "holiday_churn_trap": {
            "baseline": {
                "n": int(baseline["n"]),
                "one_and_done_rate": float(baseline["one_and_done_rate"]),
                "repeat_rate_90d": float(baseline["repeat_rate_90d"]),
            },
            "windows": windows,
            "forecast_month_map_2026": {"2026-11": ["11.11", "BFCM"], "2026-12": ["12.12"]},
        },
        "repeat_month_multipliers": {
            # Nov: BFCM multiplier (Roopa: more reliable sample than 11.11 alone).
            "2026-11": windows.get("BFCM", {}).get("repeat_vs_baseline", 1.0),
            "2026-12": windows.get("12.12", {}).get("repeat_vs_baseline", 1.0),
        },
        "scenario_simulation": scenario_simulation,
        "annual_recovery_pivot": scenario_simulation["annual_recovery_pivot"],
    }

    out_path = OUT_DIR / "ds6_roopa_metrics.json"
    out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"Wrote {out_path}")
    return payload


if __name__ == "__main__":
    main()
