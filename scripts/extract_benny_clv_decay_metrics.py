"""
Extract Benny BG/NBD forward-decay metrics into forecast-ready JSON.

This is a lightweight extractor aligned to 04_clv_decay_forecast.ipynb:
- fits BG/NBD + Gamma-Gamma (lifetimes)
- computes horizon monthly run-rate totals (30/60/90/180/365 days)
- outputs a simple proxy "sub_monthly_survival" based on 365d/30d monthly rate ratio

Outputs:
- outputs/clv_decay_metrics.json

Run (from project root):
  python scripts/extract_benny_clv_decay_metrics.py
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd


def resolve_base_dir() -> Path:
    return Path(__file__).resolve().parent.parent


def main() -> dict:
    base = resolve_base_dir()
    gold_dir = base / "medallion" / "gold"
    out_dir = base / "outputs"
    out_dir.mkdir(exist_ok=True)

    # Lazy import so the error is obvious if missing.
    from lifetimes import BetaGeoFitter, GammaGammaFitter  # type: ignore

    horizons = [30, 60, 90, 180, 365]

    co = pd.read_parquet(gold_dir / "gold_customer_orders.parquet")
    cp = pd.read_parquet(gold_dir / "gold_customer_profiles.parquet")
    da = pd.read_parquet(gold_dir / "gold_discount_analysis.parquet")

    # Paid orders only.
    co_paid = co[co["payment_status"] == "paid"].copy()
    co_paid["processed_at"] = pd.to_datetime(co_paid["processed_at"], errors="coerce", utc=True)
    co_paid["processed_at_naive"] = co_paid["processed_at"].dt.tz_localize(None)

    # First paid order per customer.
    first_orders = (
        co_paid.sort_values(["customer_id", "processed_at_naive"])
        .groupby("customer_id", as_index=False)
        .first()[["customer_id", "processed_at_naive", "price_total", "channel", "utm_source", "utm_medium"]]
        .rename(columns={"processed_at_naive": "first_order_date", "price_total": "first_order_spend"})
    )

    obs_date = co_paid["processed_at_naive"].max()

    # Build RFM-like order rollups for all customers.
    agg = (
        co_paid.groupby("customer_id")
        .agg(
            total_orders=("order_id", "nunique"),
            first_order_date=("processed_at_naive", "min"),
            last_order_date=("processed_at_naive", "max"),
            total_spend=("price_total", "sum"),
        )
        .reset_index()
    )
    agg["AOF"] = (agg["total_orders"] - 1).clip(lower=0)
    agg["recency_days"] = (agg["last_order_date"] - agg["first_order_date"]).dt.days
    agg["tenure_days"] = (obs_date - agg["first_order_date"]).dt.days

    # Exclude B2B / affiliate (align to notebook scoping).
    b2b = set(da[da["is_b2b_or_affiliate"] == True]["customer_id"].dropna())
    rfm = agg[~agg["customer_id"].isin(b2b)].copy()

    # Remove zero tenure.
    rfm = rfm[rfm["tenure_days"] > 0].copy()

    # AOV proxy for Gamma-Gamma: repeat spend / AOF (repeat orders only).
    # First-order spend is approximated via the first paid order.
    rfm = rfm.merge(first_orders[["customer_id", "first_order_spend"]], on="customer_id", how="left")
    rfm["repeat_spend"] = rfm["total_spend"] - rfm["first_order_spend"].fillna(0)
    rfm["AOV_repeat"] = np.where(rfm["AOF"] > 0, rfm["repeat_spend"] / rfm["AOF"], 0.0)

    # Fit models.
    bgf = BetaGeoFitter(penalizer_coef=0.0)
    bgf.fit(rfm["AOF"], rfm["recency_days"], rfm["tenure_days"])

    gg_df = rfm[(rfm["AOF"] > 0) & (rfm["AOV_repeat"] > 0)].copy()
    ggf = GammaGammaFitter(penalizer_coef=0.0)
    ggf.fit(gg_df["AOF"], gg_df["AOV_repeat"])

    # Expected AOV (repeat order value) for all customers.
    rfm["exp_aov"] = ggf.conditional_expected_average_profit(rfm["AOF"], rfm["AOV_repeat"])

    portfolio = {}
    monthly_rates = {}
    for t in horizons:
        label = f"{t}d"
        exp_pur = bgf.conditional_expected_number_of_purchases_up_to_time(
            t, rfm["AOF"], rfm["recency_days"], rfm["tenure_days"]
        )
        exp_rev = (exp_pur * rfm["exp_aov"]).sum()
        monthly_rate = float(exp_rev / t * 30)
        portfolio[label] = {"exp_revenue_total": float(exp_rev), "monthly_rate": monthly_rate}
        monthly_rates[t] = monthly_rate

    # Proxy “survival” (steady-state ratio).
    sub_monthly_survival = float(monthly_rates[365] / monthly_rates[30]) if monthly_rates[30] else 0.95

    payload = {
        "generated_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "source_notebook_alignment": "04_clv_decay_forecast.ipynb (approximate filters)",
        "observation_date": obs_date.date().isoformat() if pd.notna(obs_date) else None,
        "model_population_n": int(len(rfm)),
        "bgnbd_params": {k: float(v) for k, v in bgf.params_.items()},
        "gamma_gamma_params": {k: float(v) for k, v in ggf.params_.items()},
        "portfolio": portfolio,
        "sub_monthly_survival_proxy": {
            "value": sub_monthly_survival,
            "formula": "monthly_rate_365d / monthly_rate_30d",
        },
    }

    out_path = out_dir / "clv_decay_metrics.json"
    out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"Wrote {out_path}")
    return payload


if __name__ == "__main__":
    main()

