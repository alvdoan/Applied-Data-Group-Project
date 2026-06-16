"""
Extract Roopa DS6 (Acquisition Dynamics) add-ons into forecast-ready JSON.

Outputs:
- outputs/ds6_roopa_metrics.json

This focuses on the parts that can directly plug into the 2026 forecast:
- holiday churn-trap windows (11.11, BFCM, 12.12) with repeat-rate multipliers vs baseline

Run (from project root):
  python scripts/extract_ds6_roopa_metrics.py
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd


BASE = Path(__file__).resolve().parent.parent
GOLD_DIR = BASE / "medallion" / "gold"
OUT_DIR = BASE / "outputs"


@dataclass
class HolidayWindowStats:
    n: int
    one_and_done_rate: float
    repeat_rate_90d: float


def _flag_holiday(ts: pd.Timestamp) -> str:
    if pd.isna(ts):
        return "Baseline"
    # processed_at in gold_discount_analysis is timestamp-like; coerce to naive date.
    d = ts.to_pydatetime()
    if d.month == 11 and d.day == 11:
        return "11.11"
    if d.month == 12 and d.day == 12:
        return "12.12"
    # BFCM: Nov 24–30 (as in Roopa notebook)
    if d.month == 11 and d.day >= 24:
        return "BFCM"
    return "Baseline"


def main() -> dict:
    OUT_DIR.mkdir(exist_ok=True)

    da = pd.read_parquet(GOLD_DIR / "gold_discount_analysis.parquet")

    first_orders = da[(da["is_first_order"] == True) & (da["is_b2b_or_affiliate"] == False)].copy()

    # Coerce required columns
    first_orders["processed_at"] = pd.to_datetime(first_orders["processed_at"], errors="coerce", utc=True).dt.tz_localize(
        None
    )
    first_orders["repeat_purchase_90d"] = pd.to_numeric(first_orders["repeat_purchase_90d"], errors="coerce")
    first_orders["total_orders"] = pd.to_numeric(first_orders["total_orders"], errors="coerce")

    first_orders["one_and_done"] = first_orders["total_orders"].fillna(0).astype(float) <= 1
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

    baseline = grp.loc["Baseline"] if "Baseline" in grp.index else pd.Series({"n": 0, "one_and_done_rate": 0, "repeat_rate_90d": 0})

    windows: dict[str, dict] = {}
    for w in ["11.11", "BFCM", "12.12"]:
        if w not in grp.index:
            continue
        row = grp.loc[w]
        repeat_vs_baseline = float(row["repeat_rate_90d"] / baseline["repeat_rate_90d"]) if baseline["repeat_rate_90d"] else 1.0
        windows[w] = {
            "n": int(row["n"]),
            "one_and_done_rate": float(row["one_and_done_rate"]),
            "repeat_rate_90d": float(row["repeat_rate_90d"]),
            "repeat_vs_baseline": repeat_vs_baseline,
            "delta_one_and_done_pp": float((row["one_and_done_rate"] - baseline["one_and_done_rate"]) * 100),
            "delta_repeat_pp": float((row["repeat_rate_90d"] - baseline["repeat_rate_90d"]) * 100),
        }

    payload = {
        "generated_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
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
        # Forecast wiring suggestion: apply to repeat revenue by month.
        "repeat_month_multipliers": {
            # Conservative: apply BFCM multiplier for all of Nov and 12.12 for Dec.
            "2026-11": windows.get("BFCM", {}).get("repeat_vs_baseline", 1.0),
            "2026-12": windows.get("12.12", {}).get("repeat_vs_baseline", 1.0),
        },
    }

    out_path = OUT_DIR / "ds6_roopa_metrics.json"
    out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"Wrote {out_path}")
    return payload


if __name__ == "__main__":
    main()

