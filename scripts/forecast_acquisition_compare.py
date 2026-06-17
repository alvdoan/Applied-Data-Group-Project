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

import json
import warnings
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import statsmodels.api as sm
import statsmodels.formula.api as smf
from statsmodels.tsa.holtwinters import ExponentialSmoothing

from build_forecast_2026 import (
    FORECAST_CHANNELS,
    OUTPUT_DIR,
    historical_monthly_acquisitions,
    load_data,
    load_forecast_params,
    prepare_customer_base,
    project_2026_monthly_seasonal,
)

warnings.filterwarnings("ignore", category=FutureWarning)
warnings.filterwarnings("ignore", category=UserWarning)

PROMO_MONTHS = {3, 11, 12}
DEFAULT_ACQ_PARAMS = {
    "hist_start": "2024-01",
    "end_train": "2025-12",
    "forecast_start": "2026-01",
    "forecast_end": "2026-12",
    "ewma_alpha": 0.35,
    "ewma_growth_window": 6,
    "mix_window": 3,
    "backtest_start": "2026-01",
    "backtest_end": "2026-03",
}


def _load_acq_params() -> dict:
    cfg_path = Path(__file__).resolve().parent.parent / "configs" / "forecast_2026_params.json"
    if cfg_path.exists():
        with cfg_path.open(encoding="utf-8") as f:
            cfg = json.load(f)
        acq = cfg.get("acquisition_forecast", {})
        return {**DEFAULT_ACQ_PARAMS, **acq}
    return dict(DEFAULT_ACQ_PARAMS)


def _total_series(hist: pd.DataFrame) -> pd.Series:
    s = hist.sum(axis=1).astype(float)
    s.index = pd.PeriodIndex(s.index, freq="M")
    return s.sort_index()


def _channel_mix(hist: pd.DataFrame, mix_window: int) -> pd.Series:
    recent = hist.tail(mix_window)
    mix = recent.sum()
    total = mix.sum()
    if total <= 0:
        mix = pd.Series(1.0 / len(FORECAST_CHANNELS), index=FORECAST_CHANNELS)
    else:
        mix = mix / total
    return mix.reindex(FORECAST_CHANNELS, fill_value=0.0)


def allocate_by_channel(
    total_forecast: pd.Series,
    hist: pd.DataFrame,
    mix_window: int,
) -> pd.DataFrame:
    mix = _channel_mix(hist, mix_window)
    rows = []
    for month, total in total_forecast.items():
        for ch in FORECAST_CHANNELS:
            rows.append(
                {"month": month, "fc_channel": ch, "new_customers": float(total) * float(mix[ch])}
            )
    return pd.DataFrame(rows)


def _ewma_growth_factor(
    series: pd.Series,
    end_period: pd.Period,
    window: int,
    alpha: float,
) -> float:
    """EWMA(recent window) / EWMA(same window one year earlier)."""
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
    if ewma_ly <= 0:
        return 1.0
    return float(ewma_now / ewma_ly)


def forecast_ewma_yoy(
    series: pd.Series,
    forecast_months: pd.PeriodIndex,
    end_train: pd.Period,
    alpha: float = 0.35,
    growth_window: int = 6,
) -> pd.Series:
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


def forecast_holt_winters(
    series: pd.Series,
    forecast_months: pd.PeriodIndex,
    end_train: pd.Period,
) -> pd.Series:
    train = series[series.index <= end_train].astype(float)
    if len(train) < 24:
        raise ValueError(f"Holt-Winters needs >=24 months; got {len(train)}")

    best = None
    for trend in ("add", None):
        for seasonal in ("add", "mul"):
            try:
                model = ExponentialSmoothing(
                    train.values,
                    trend=trend,
                    seasonal=seasonal,
                    seasonal_periods=12,
                    initialization_method="estimated",
                )
                fit = model.fit(optimized=True, use_brute=False)
                if best is None or fit.aic < best.aic:
                    best = fit
            except Exception:
                continue

    if best is None:
        model = ExponentialSmoothing(
            train.values,
            trend="add",
            seasonal="add",
            seasonal_periods=12,
            initialization_method="heuristic",
        )
        best = model.fit(optimized=True)

    n = len(forecast_months)
    fc = best.forecast(n)
    return pd.Series(fc, index=forecast_months, name="holt_winters")


def _training_frame(series: pd.Series, end_train: pd.Period) -> pd.DataFrame:
    train = series[series.index <= end_train]
    df = pd.DataFrame({"customers": train.values}, index=train.index)
    df["t"] = np.arange(len(df))
    df["cal_month"] = df.index.month
    df["promo"] = df["cal_month"].isin(PROMO_MONTHS).astype(int)
    return df


def forecast_regression(
    series: pd.Series,
    forecast_months: pd.PeriodIndex,
    end_train: pd.Period,
) -> tuple[pd.Series, object]:
    train_df = _training_frame(series, end_train)
    if len(train_df) < 12:
        raise ValueError(f"Regression needs >=12 months; got {len(train_df)}")

    model = smf.ols("customers ~ t + C(cal_month) + promo", data=train_df).fit()

    last_t = int(train_df["t"].iloc[-1])
    pred_rows = []
    for i, m in enumerate(forecast_months, start=1):
        pred_rows.append(
            {
                "t": last_t + i,
                "cal_month": m.month,
                "promo": int(m.month in PROMO_MONTHS),
            }
        )
    pred_df = pd.DataFrame(pred_rows, index=forecast_months)
    pred = model.predict(pred_df)
    pred = pred.clip(lower=0.0)
    return pd.Series(pred.values, index=forecast_months, name="regression"), model


def forecast_v2_seasonal(hist: pd.DataFrame, forecast_params: dict) -> pd.Series:
    projected = project_2026_monthly_seasonal(hist, forecast_params)
    return projected.groupby("month")["new_customers"].sum().rename("v2_seasonal")


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


def run_comparison(
    end_train: str | None = None,
    write_outputs: bool = True,
) -> dict:
    params = _load_acq_params()
    fp = load_forecast_params()
    end_train = end_train or params["end_train"]
    end_train_p = pd.Period(end_train, freq="M")

    forecast_months = pd.period_range(params["forecast_start"], params["forecast_end"], freq="M")
    backtest_months = pd.period_range(params["backtest_start"], params["backtest_end"], freq="M")

    cp, co, _, _ = load_data()
    base = prepare_customer_base(cp, co)
    hist_full = historical_monthly_acquisitions(base, start=params["hist_start"])
    hist_train = historical_monthly_acquisitions(base, start=params["hist_start"], end=end_train)
    total_full = _total_series(hist_full)
    total_train = _total_series(hist_train)

    methods: dict[str, pd.Series] = {}
    models = {}

    methods["ewma_yoy"] = forecast_ewma_yoy(
        total_train,
        forecast_months,
        end_train_p,
        alpha=params["ewma_alpha"],
        growth_window=params["ewma_growth_window"],
    )
    methods["holt_winters"] = forecast_holt_winters(total_train, forecast_months, end_train_p)
    reg_fc, reg_model = forecast_regression(total_train, forecast_months, end_train_p)
    methods["regression"] = reg_fc
    models["regression"] = reg_model
    methods["v2_seasonal"] = forecast_v2_seasonal(hist_train, fp)

    actual_backtest = total_full.reindex(backtest_months)
    backtest_monthly = []
    backtest_summary = []
    for name, fc in methods.items():
        m = _metrics(actual_backtest, fc.reindex(backtest_months))
        backtest_summary.append({"method": name, "month": "SUMMARY", **m})
        for month in backtest_months:
            backtest_monthly.append(
                {
                    "method": name,
                    "month": str(month),
                    "actual": float(actual_backtest.get(month, np.nan)),
                    "forecast": float(fc.get(month, np.nan)),
                    "error": float(fc.get(month, np.nan) - actual_backtest.get(month, np.nan)),
                }
            )

    forecast_rows = []
    channel_frames = []
    for name, fc in methods.items():
        for month, val in fc.items():
            forecast_rows.append({"method": name, "month": str(month), "new_customers": float(val)})
        channel_frames.append(
            allocate_by_channel(fc, hist_train, params["mix_window"]).assign(method=name)
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
            "# Acquisition forecast method comparison",
            "",
            f"Train through **{end_train}**. Backtest **{params['backtest_start']}**–**{params['backtest_end']}**.",
            "",
            "## Backtest (new customers)",
            "",
            "| Method | MAPE | MAE (customers) | Bias |",
            "|--------|------|-----------------|------|",
        ]
        for _, row in summary_metrics.sort_values("mape").iterrows():
            lines.append(
                f"| {row['method']} | {row['mape']:.1f}% | {row['mae']:.0f} | {row['bias']:+.0f} |"
            )
        lines += [
            "",
            "## Methods",
            "",
            "- **ewma_yoy**: same calendar month last year × EWMA growth (recent vs lag-12 window).",
            "- **holt_winters**: additive Holt-Winters (`seasonal_periods=12`), AIC-selected trend/season spec.",
            "- **regression**: OLS `customers ~ t + C(cal_month) + promo` (promo = Mar/Nov/Dec).",
            "- **v2_seasonal**: existing `project_2026_monthly_seasonal` baseline.",
            "",
            f"EWMA α={params['ewma_alpha']}, growth window={params['ewma_growth_window']} months.",
        ]
        (OUTPUT_DIR / "acquisition_forecast_backtest.md").write_text("\n".join(lines), encoding="utf-8")

        if reg_model is not None:
            with (OUTPUT_DIR / "acquisition_regression_summary.txt").open("w", encoding="utf-8") as f:
                f.write(reg_model.summary().as_text())

        _plot_comparison(total_full, methods, end_train_p, forecast_months, params)

    return {
        "methods": methods,
        "forecast_df": forecast_df,
        "channel_df": channel_df,
        "backtest_df": backtest_df,
        "regression_model": models.get("regression"),
    }


def _plot_comparison(
    total_full: pd.Series,
    methods: dict[str, pd.Series],
    end_train: pd.Period,
    forecast_months: pd.PeriodIndex,
    params: dict,
) -> None:
    fig, axes = plt.subplots(2, 1, figsize=(14, 10), gridspec_kw={"height_ratios": [2, 1]})

    hist_plot = total_full[total_full.index <= forecast_months[-1]]
    axes[0].plot(
        hist_plot.index.astype(str),
        hist_plot.values,
        label="Actual new customers",
        color="#374151",
        linewidth=2.5,
        marker="o",
        markersize=4,
        zorder=3,
    )

    colors = {
        "ewma_yoy": "#2563eb",
        "holt_winters": "#16a34a",
        "regression": "#dc2626",
        "v2_seasonal": "#9333ea",
    }
    for name, fc in methods.items():
        axes[0].plot(
            fc.index.astype(str),
            fc.values,
            label=name,
            color=colors.get(name, "#6b7280"),
            linestyle="--",
            linewidth=2,
            marker="o",
            markersize=4,
            markerfacecolor="white",
            markeredgewidth=1.2,
        )

    axes[0].axvline(x=str(end_train), color="#9ca3af", linestyle=":", linewidth=1.5)
    axes[0].set_title("New Customer Acquisition — Method Comparison (2026)", fontsize=13, fontweight="bold")
    axes[0].set_ylabel("New customers / month")
    axes[0].legend(loc="upper left", fontsize=9)
    axes[0].grid(alpha=0.3)

    # Backtest panel: Q1 2026 errors
    bt_months = pd.period_range(params["backtest_start"], params["backtest_end"], freq="M")
    actual_bt = total_full.reindex(bt_months)
    x = np.arange(len(bt_months))
    width = 0.2
    for i, (name, fc) in enumerate(methods.items()):
        vals = fc.reindex(bt_months).values
        axes[1].bar(x + i * width, vals, width, label=name, color=colors.get(name, "#6b7280"), alpha=0.85)
    axes[1].plot(x + width * 1.5, actual_bt.values, "ko-", label="Actual", linewidth=2, markersize=8)
    axes[1].set_xticks(x + width * 1.5)
    axes[1].set_xticklabels([str(m) for m in bt_months])
    axes[1].set_title("Q1 2026 backtest — forecast vs actual", fontsize=11)
    axes[1].set_ylabel("New customers")
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
    for method, fc in result["methods"].items():
        print(f"\n{method}: 2026 annual total = {fc.sum():.0f} new customers")
    print(f"\nWrote outputs to {OUTPUT_DIR}")
    print("  acquisition_forecast_comparison.csv")
    print("  acquisition_forecast_comparison.png")
    print("  acquisition_forecast_backtest.md")


if __name__ == "__main__":
    main()
