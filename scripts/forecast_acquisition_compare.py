"""
Compare acquisition forecasting methods for monthly new customers.

Methods:
  1. ewma_yoy      — same-month-last-year × EWMA growth (recent vs lag-12 window)
  2. holt_winters — Holt-Winters additive trend + seasonality (statsmodels)
  3. regression    — OLS: time trend + month dummies + promo months (Mar/Nov/Dec)
  4. v2_seasonal   — existing project_2026_monthly_seasonal (baseline)

Run from project root:
  python scripts/forecast_acquisition_compare.py
"""
from __future__ import annotations

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from acquisition_forecast import (
    allocate_by_channel,
    forecast_acquisition_total,
    forecast_regression,
    forecast_holt_winters,
    load_acq_params,
    total_series,
)
from build_forecast_2026 import (
    OUTPUT_DIR,
    historical_monthly_acquisitions,
    load_data,
    load_forecast_params,
    prepare_customer_base,
    project_2026_monthly_seasonal,
)


def _ewma_growth_factor(series, end_period, window, alpha):
    idx = series.index
    if end_period not in idx:
        end_period = idx[-1]
    end_loc = idx.get_loc(end_period)
    start_loc = max(0, end_loc - window + 1)
    recent = series.iloc[start_loc : end_loc + 1]
    ly_end = end_period - 12
    if ly_end not in idx:
        return 1.0
    ly_loc = idx.get_loc(ly_end)
    ly_start_loc = max(0, ly_loc - window + 1)
    ly_recent = series.iloc[ly_start_loc : ly_loc + 1]
    if len(recent) == 0 or len(ly_recent) == 0:
        return 1.0
    ewma_now = recent.ewm(alpha=alpha, adjust=False).mean().iloc[-1]
    ewma_ly = ly_recent.ewm(alpha=alpha, adjust=False).mean().iloc[-1]
    return float(ewma_now / ewma_ly) if ewma_ly > 0 else 1.0


def forecast_ewma_yoy(series, forecast_months, end_train, alpha=0.35, growth_window=6):
    train = series[series.index <= end_train]
    growth = _ewma_growth_factor(train, end_train, growth_window, alpha)
    out = {}
    for m in forecast_months:
        ly = m - 12
        if ly in train.index and train.loc[ly] > 0:
            out[m] = float(train.loc[ly] * growth)
        elif len(train) > 0:
            out[m] = float(train.ewm(alpha=alpha, adjust=False).mean().iloc[-1])
        else:
            out[m] = 0.0
    return pd.Series(out, name="ewma_yoy")


def forecast_v2_seasonal(hist, forecast_params):
    projected = project_2026_monthly_seasonal(hist, forecast_params)
    return projected.groupby("month")["new_customers"].sum().rename("v2_seasonal")


def _metrics(actual, forecast):
    aligned = pd.concat([actual.rename("actual"), forecast.rename("forecast")], axis=1).dropna()
    if aligned.empty:
        return {"mape": np.nan, "mae": np.nan, "bias": np.nan, "n_months": 0}
    err = aligned["forecast"] - aligned["actual"]
    mape = (err.abs() / aligned["actual"].replace(0, np.nan)).mean() * 100
    return {"mape": float(mape), "mae": float(err.abs().mean()), "bias": float(err.mean()), "n_months": int(len(aligned))}


def run_comparison(end_train=None, write_outputs=True):
    from build_forecast_2026 import FORECAST_CHANNELS

    params = load_acq_params()
    fp = load_forecast_params()
    end_train = end_train or params["end_train"]
    end_train_p = pd.Period(end_train, freq="M")
    forecast_months = pd.period_range(params["forecast_start"], params["forecast_end"], freq="M")
    backtest_months = pd.period_range(params["backtest_start"], params["backtest_end"], freq="M")

    cp, co, _, _ = load_data()
    base = prepare_customer_base(cp, co)
    hist_full = historical_monthly_acquisitions(base, start=params["hist_start"])
    hist_train = historical_monthly_acquisitions(base, start=params["hist_start"], end=end_train)
    total_full = total_series(hist_full)
    total_train = total_series(hist_train)

    methods = {
        "ewma_yoy": forecast_ewma_yoy(
            total_train, forecast_months, end_train_p,
            alpha=params["ewma_alpha"], growth_window=params["ewma_growth_window"],
        ),
        "holt_winters": forecast_holt_winters(total_train, forecast_months, end_train_p),
        "v2_seasonal": forecast_v2_seasonal(hist_train, fp),
    }
    reg_fc, reg_model = forecast_regression(total_train, forecast_months, end_train_p)
    methods["regression"] = reg_fc

    actual_backtest = total_full.reindex(backtest_months)
    backtest_monthly, backtest_summary = [], []
    for name, fc in methods.items():
        m = _metrics(actual_backtest, fc.reindex(backtest_months))
        backtest_summary.append({"method": name, "month": "SUMMARY", **m})
        for month in backtest_months:
            backtest_monthly.append({
                "method": name, "month": str(month),
                "actual": float(actual_backtest.get(month, np.nan)),
                "forecast": float(fc.get(month, np.nan)),
                "error": float(fc.get(month, np.nan) - actual_backtest.get(month, np.nan)),
            })

    forecast_rows, channel_frames = [], []
    for name, fc in methods.items():
        for month, val in fc.items():
            forecast_rows.append({"method": name, "month": str(month), "new_customers": float(val)})
        channel_frames.append(
            allocate_by_channel(fc, hist_train, params["mix_window"], FORECAST_CHANNELS).assign(method=name)
        )

    forecast_df = pd.DataFrame(forecast_rows)
    channel_df = pd.concat(channel_frames, ignore_index=True)
    backtest_df = pd.DataFrame(backtest_monthly + backtest_summary)

    if write_outputs:
        forecast_df.to_csv(OUTPUT_DIR / "acquisition_forecast_comparison.csv", index=False)
        channel_df.to_csv(OUTPUT_DIR / "acquisition_forecast_by_channel.csv", index=False)
        backtest_df.to_csv(OUTPUT_DIR / "acquisition_forecast_backtest.csv", index=False)
        summary_metrics = pd.DataFrame(backtest_summary)
        lines = [
            "# Acquisition forecast method comparison", "",
            f"Train through **{end_train}**. Backtest **{params['backtest_start']}**–**{params['backtest_end']}**.", "",
            "## Backtest (new customers)", "",
            "| Method | MAPE | MAE (customers) | Bias |",
            "|--------|------|-----------------|------|",
        ]
        for _, row in summary_metrics.sort_values("mape").iterrows():
            lines.append(f"| {row['method']} | {row['mape']:.1f}% | {row['mae']:.0f} | {row['bias']:+.0f} |")
        lines += [
            "", "## Methods", "",
            "- **ewma_yoy**: same calendar month last year × EWMA growth.",
            "- **holt_winters**: Holt-Winters (`seasonal_periods=12`).",
            "- **regression**: OLS `customers ~ t + C(cal_month) + promo`.",
            "- **v2_seasonal**: existing seasonal baseline.", "",
            f"EWMA α={params['ewma_alpha']}, growth window={params['ewma_growth_window']} months.",
        ]
        (OUTPUT_DIR / "acquisition_forecast_backtest.md").write_text("\n".join(lines), encoding="utf-8")
        if reg_model is not None:
            (OUTPUT_DIR / "acquisition_regression_summary.txt").write_text(reg_model.summary().as_text(), encoding="utf-8")
        _plot_comparison(total_full, methods, end_train_p, params)

    return {"methods": methods, "forecast_df": forecast_df, "backtest_df": backtest_df}


def _plot_comparison(total_full, methods, end_train, params):
    forecast_months = pd.period_range(params["forecast_start"], params["forecast_end"], freq="M")
    fig, axes = plt.subplots(2, 1, figsize=(14, 10), gridspec_kw={"height_ratios": [2, 1]})
    hist_plot = total_full[total_full.index <= forecast_months[-1]]
    axes[0].plot(hist_plot.index.astype(str), hist_plot.values, label="Actual new customers",
                 color="#374151", linewidth=2.5, marker="o", markersize=4, zorder=3)
    colors = {"ewma_yoy": "#2563eb", "holt_winters": "#16a34a", "regression": "#dc2626", "v2_seasonal": "#9333ea"}
    for name, fc in methods.items():
        axes[0].plot(fc.index.astype(str), fc.values, label=name, color=colors.get(name, "#6b7280"),
                     linestyle="--", linewidth=2, marker="o", markersize=4, markerfacecolor="white", markeredgewidth=1.2)
    axes[0].axvline(x=str(end_train), color="#9ca3af", linestyle=":", linewidth=1.5)
    axes[0].set_title("New Customer Acquisition — Method Comparison (2026)", fontsize=13, fontweight="bold")
    axes[0].set_ylabel("New customers / month")
    axes[0].legend(loc="upper left", fontsize=9)
    axes[0].grid(alpha=0.3)
    bt_months = pd.period_range(params["backtest_start"], params["backtest_end"], freq="M")
    actual_bt = total_full.reindex(bt_months)
    x = np.arange(len(bt_months))
    width = 0.2
    for i, (name, fc) in enumerate(methods.items()):
        axes[1].bar(x + i * width, fc.reindex(bt_months).values, width, label=name,
                    color=colors.get(name, "#6b7280"), alpha=0.85)
    axes[1].plot(x + width * 1.5, actual_bt.values, "ko-", label="Actual", linewidth=2, markersize=8)
    axes[1].set_xticks(x + width * 1.5)
    axes[1].set_xticklabels([str(m) for m in bt_months])
    axes[1].set_title("Q1 2026 backtest — forecast vs actual", fontsize=11)
    axes[1].legend(fontsize=8, ncol=3)
    axes[1].grid(alpha=0.3, axis="y")
    plt.xticks(rotation=45, ha="right")
    plt.tight_layout()
    plt.savefig(OUTPUT_DIR / "acquisition_forecast_comparison.png", dpi=150, bbox_inches="tight")
    plt.close()


def main():
    result = run_comparison()
    summary = result["backtest_df"]
    summary = summary[summary["month"] == "SUMMARY"]
    if not summary.empty:
        print("=== Q1 2026 acquisition backtest (new customers) ===")
        print(summary.sort_values("mape").to_string(index=False))
    print(f"\nWrote outputs to {OUTPUT_DIR}")


if __name__ == "__main__":
    main()
