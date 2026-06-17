"""
Extract DS6 (Acquisition Dynamics) key metrics into machine-readable outputs.

Replicates Roopa's `04_analysis_ds6_acquisition_dynamics.ipynb` logic:
- promo_gap_pp: Full Price repeat_rate_90d minus High Magnitude (30%+) repeat_rate_90d
- promo_lost_revenue_per_cohort: round(n_high_mag * gap) * AOV

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
    return Path(__file__).resolve().parent.parent


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


def main() -> dict:
    base = resolve_base_dir()
    gold_dir = base / "medallion" / "gold"
    out_dir = base / "outputs"
    out_dir.mkdir(exist_ok=True)

    da = pd.read_parquet(gold_dir / "gold_discount_analysis.parquet")
    first_orders = load_first_orders_roopa_style(da)

    full_price_rate = float(
        first_orders.loc[first_orders["promo_group"] == "Full Price", "repeat_purchase_90d"].mean()
    )
    high_mag_rate = float(
        first_orders.loc[
            first_orders["promo_group"] == "High Magnitude (30%+)", "repeat_purchase_90d"
        ].mean()
    )
    gap = full_price_rate - high_mag_rate

    num_high_mag = int(
        first_orders.loc[
            first_orders["promo_group"] == "High Magnitude (30%+)", "customer_id"
        ].nunique()
    )

    # Roopa notebook uses FX-normalised price_total_sgd when present; else price_total.
    aov_col = "price_total_sgd" if "price_total_sgd" in first_orders.columns else "price_total"
    avg_order_val = float(first_orders[aov_col].mean())

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
            "lost_repeat_customers": lost_repeat_customers,
            "source": "gold_discount_analysis.parquet (Roopa DS6 notebook alignment)",
        },
    }

    out_path = out_dir / "ds6_metrics.json"
    out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"Wrote {out_path}")
    return payload


if __name__ == "__main__":
    main()
