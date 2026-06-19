"""
Acquisition forecasting methods for monthly new customers.

Shared by forecast_acquisition_compare.py and build_forecast_2026.py (v3 scenarios).
"""
from __future__ import annotations

import json
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
import statsmodels.formula.api as smf
from statsmodels.tsa.holtwinters import ExponentialSmoothing

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


def load_acq_params() -> dict:
    cfg_path = Path(__file__).resolve().parent.parent / "configs" / "forecast_2026_params.json"
    if cfg_path.exists():
        with cfg_path.open(encoding="utf-8") as f:
            cfg = json.load(f)
        acq = cfg.get("acquisition_forecast", {})
        return {**DEFAULT_ACQ_PARAMS, **acq}
    return dict(DEFAULT_ACQ_PARAMS)


def total_series(hist: pd.DataFrame) -> pd.Series:
    s = hist.sum(axis=1).astype(float)
    s.index = pd.PeriodIndex(s.index, freq="M")
    return s.sort_index()


def channel_mix(hist: pd.DataFrame, mix_window: int, channels: list[str]) -> pd.Series:
    recent = hist.tail(mix_window)
    mix = recent.sum()
    total = mix.sum()
    if total <= 0:
        mix = pd.Series(1.0 / len(channels), index=channels)
    else:
        mix = mix / total
    return mix.reindex(channels, fill_value=0.0)


def allocate_by_channel(
    total_forecast: pd.Series,
    hist: pd.DataFrame,
    mix_window: int,
    channels: list[str],
) -> pd.DataFrame:
    mix = channel_mix(hist, mix_window, channels)
    rows = []
    for month, total in total_forecast.items():
        for ch in channels:
            rows.append(
                {"month": month, "fc_channel": ch, "new_customers": float(total) * float(mix[ch])}
            )
    return pd.DataFrame(rows)


def forecast_regression(
    series: pd.Series,
    forecast_months: pd.PeriodIndex,
    end_train: pd.Period,
):
    train = series[series.index <= end_train]
    train_df = pd.DataFrame({"customers": train.values}, index=train.index)
    train_df["t"] = np.arange(len(train_df))
    train_df["cal_month"] = train_df.index.month
    train_df["promo"] = train_df["cal_month"].isin(PROMO_MONTHS).astype(int)
    if len(train_df) < 12:
        raise ValueError(f"Regression needs >=12 months; got {len(train_df)}")

    model = smf.ols("customers ~ t + C(cal_month) + promo", data=train_df).fit()
    last_t = int(train_df["t"].iloc[-1])
    pred_rows = []
    for i, m in enumerate(forecast_months, start=1):
        pred_rows.append(
            {"t": last_t + i, "cal_month": m.month, "promo": int(m.month in PROMO_MONTHS)}
        )
    pred_df = pd.DataFrame(pred_rows, index=forecast_months)
    pred = model.predict(pred_df).clip(lower=0.0)
    return pd.Series(pred.values, index=forecast_months, name="regression"), model


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

    fc = best.forecast(len(forecast_months))
    return pd.Series(fc, index=forecast_months, name="holt_winters")


def forecast_acquisition_total(
    method: str,
    hist_train: pd.DataFrame,
    months_2026: pd.PeriodIndex,
    end_train: pd.Period,
    ewma_alpha: float = 0.35,
    ewma_growth_window: int = 6,
) -> pd.Series:
    """Return monthly total new-customer forecast for the given method name."""
    total = total_series(hist_train)
    if method == "regression":
        fc, _ = forecast_regression(total, months_2026, end_train)
        return fc
    if method == "holt_winters":
        return forecast_holt_winters(total, months_2026, end_train)
    raise ValueError(f"Unknown acquisition method: {method}")


def project_acquisition_channels(
    method: str,
    hist_train: pd.DataFrame,
    months_2026: pd.PeriodIndex,
    channels: list[str],
    acq_params: dict | None = None,
) -> pd.DataFrame:
    """Forecast total new customers and split across channels."""
    acq_params = acq_params or load_acq_params()
    end_train = pd.Period(acq_params["end_train"], freq="M")
    total_fc = forecast_acquisition_total(
        method,
        hist_train,
        months_2026,
        end_train,
        ewma_alpha=acq_params.get("ewma_alpha", 0.35),
        ewma_growth_window=acq_params.get("ewma_growth_window", 6),
    )
    return allocate_by_channel(
        total_fc,
        hist_train,
        acq_params.get("mix_window", 3),
        channels,
    )
