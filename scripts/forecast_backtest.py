"""
Backtest v1 vs v2 forecast against actual monthly revenue.

Run from project root:
  python scripts/forecast_backtest.py
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from build_forecast_2026 import (
    OUTPUT_DIR,
    build_forecast,
    historical_monthly_total_revenue,
    load_data,
    prepare_customer_base,
)


def _metrics(actual: pd.Series, forecast: pd.Series) -> dict:
    aligned = pd.concat([actual.rename("actual"), forecast.rename("forecast")], axis=1).dropna()
    if aligned.empty:
        return {"mape": np.nan, "mae": np.nan, "bias": np.nan, "n_months": 0}
    err = aligned["forecast"] - aligned["actual"]
    mape = (err.abs() / aligned["actual"].replace(0, np.nan)).mean() * 100
    return {
        "mape": float(mape),
        "mae": float(err.abs().mean()),
        "bias": float(err.mean()),
        "n_months": int(len(aligned)),
    }


def run_backtest(end_train: str = "2025-12", eval_start: str = "2026-01", eval_end: str = "2026-03") -> pd.DataFrame:
    cp, co, subs, cohorts_ch = load_data()
    base = prepare_customer_base(cp, co)
    actual = historical_monthly_total_revenue(co, start=eval_start)
    actual = actual[(actual.index >= eval_start) & (actual.index <= eval_end)]

    rows = []
    for version in ("v1", "v2"):
        all_scenarios, _, _, _ = build_forecast(
            version=version,
            end_train=end_train,
            cp=cp,
            co=co,
            subs=subs,
            cohorts_ch=cohorts_ch,
            base=base,
            write_outputs=False,
        )
        sq = all_scenarios[all_scenarios["scenario"] == "Status Quo"].copy()
        sq["month"] = pd.PeriodIndex(sq["month"], freq="M")
        fc = sq.set_index("month")["total_revenue"]
        fc = fc[(fc.index >= eval_start) & (fc.index <= eval_end)]
        m = _metrics(actual, fc)
        for month in fc.index:
            rows.append(
                {
                    "version": version,
                    "month": str(month),
                    "actual": float(actual.get(month, np.nan)),
                    "forecast": float(fc.get(month, np.nan)),
                    "error": float(fc.get(month, np.nan) - actual.get(month, np.nan)),
                }
            )
        rows.append({"version": version, "month": "SUMMARY", **m})

    df = pd.DataFrame(rows)
    df.to_csv(OUTPUT_DIR / "forecast_backtest_comparison.csv", index=False)

    lines = [
        "# Forecast backtest (Status Quo vs actuals)",
        "",
        f"Train acquisitions through **{end_train}**. Evaluate **{eval_start}** to **{eval_end}**.",
        "",
        "| Version | MAPE | MAE (SGD) | Bias (SGD) |",
        "|---------|------|-----------|------------|",
    ]
    for version in ("v1", "v2"):
        s = df[(df["version"] == version) & (df["month"] == "SUMMARY")].iloc[0]
        lines.append(
            f"| {version} | {s['mape']:.1f}% | {s['mae']:,.0f} | {s['bias']:+,.0f} |"
        )
    (OUTPUT_DIR / "forecast_backtest_summary.md").write_text("\n".join(lines), encoding="utf-8")
    return df


if __name__ == "__main__":
    df = run_backtest()
    print(df[df["month"] == "SUMMARY"].to_string(index=False))
    print(f"\nWrote {OUTPUT_DIR / 'forecast_backtest_comparison.csv'}")
