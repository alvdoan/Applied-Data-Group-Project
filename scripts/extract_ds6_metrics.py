"""
Extract DS6 (Acquisition Dynamics) key metrics into machine-readable outputs.

This script replicates the DS6 notebook's core calculations:
- promo_gap_pp: Full Price repeat_rate_90d minus High Magnitude (30%+) repeat_rate_90d
- promo_lost_revenue_per_cohort: round(n_high_mag * gap) * first_order_AOV

Outputs:
- outputs/ds6_metrics.json

Run (from project root):
  python scripts/extract_ds6_metrics.py
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd


def resolve_base_dir() -> Path:
    base = Path(__file__).resolve().parent.parent
    return base


def main() -> dict:
    base = resolve_base_dir()
    gold_dir = base / "medallion" / "gold"
    out_dir = base / "outputs"
    out_dir.mkdir(exist_ok=True)

    cf = pd.read_parquet(gold_dir / "gold_churn_features.parquet")
    da = pd.read_parquet(gold_dir / "gold_discount_analysis.parquet")

    # First orders only, retail / non-affiliate only (DS6)
    first_orders_da = da[(da["is_first_order"] == True) & (da["is_b2b_or_affiliate"] == False)].drop_duplicates(
        subset=["customer_id"]
    )

    # Start from CF base (aligned with churn analysis), left join discount info.
    first_orders = cf[["customer_id"]].merge(first_orders_da, on="customer_id", how="left")

    # Grouping logic from DS6 notebook
    first_orders["promo_group"] = "Other/Small Discount"
    first_orders.loc[first_orders["discount_type"].isna(), "promo_group"] = "Full Price"
    first_orders.loc[first_orders["is_high_magnitude"] == True, "promo_group"] = "High Magnitude (30%+)"

    first_orders["repeat_purchase_90d"] = first_orders["repeat_purchase_90d"].astype(float)

    full_price_rate = float(
        first_orders[first_orders["promo_group"] == "Full Price"]["repeat_purchase_90d"].mean()
    )
    high_mag_rate = float(
        first_orders[first_orders["promo_group"] == "High Magnitude (30%+)"]["repeat_purchase_90d"].mean()
    )
    gap = full_price_rate - high_mag_rate

    num_high_mag = int(
        first_orders[first_orders["promo_group"] == "High Magnitude (30%+)"]["customer_id"].nunique()
    )

    # AOV proxy used in DS6: mean first order total (already FX-normalised in upstream layers)
    avg_order_val = float(first_orders["price_total"].mean())

    lost_repeat_customers = int(round(num_high_mag * gap))
    lost_revenue = float(lost_repeat_customers * avg_order_val)

    payload = {
        "promo_gap_pp": gap,
        "promo_lost_revenue_per_cohort": lost_revenue,
        "ds6_debug": {
            "full_price_rate": full_price_rate,
            "high_mag_rate": high_mag_rate,
            "n_high_mag_customers": num_high_mag,
            "avg_order_val": avg_order_val,
            "lost_repeat_customers": lost_repeat_customers
        },
    }

    out_path = out_dir / "ds6_metrics.json"
    out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"Wrote {out_path}")
    return payload


if __name__ == "__main__":
    main()

