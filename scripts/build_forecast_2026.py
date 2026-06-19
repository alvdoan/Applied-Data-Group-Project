"""
2026 Revenue Forecast — builds monthly scenario outputs for LushProtein.

Versions:
  v1 — flat acquisition run-rate, simple repeat spread, subscription decay proxy
  v2 — seasonal acquisition, cohort repeat (Roopa), BG/NBD subscription curve (Benny)

Run from project root:
  python scripts/build_forecast_2026.py

Environment:
  FORECAST_VERSION=v1|v2  (default: v2)
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

BASE = Path(__file__).resolve().parent.parent
GOLD_DIR = BASE / "medallion" / "gold"
OUTPUT_DIR = BASE / "outputs"
OUTPUT_DIR.mkdir(exist_ok=True)

FORECAST_VERSION = os.environ.get("FORECAST_VERSION", "v2")

OVERALL_REPEAT_RATE = 0.2191
SUBSCRIBER_REPEAT_RATE = 0.85
NON_SUBSCRIBER_REPEAT_RATE = 0.195

DEFAULT_FORECAST_PARAMS = {
    "forecast_version": "v2",
    "promo_gap_pp": 0.0411,
    "promo_lost_revenue_per_cohort": 2626.0,
    "lazada_winback_conservative": 9776.0,
    "lazada_winback_upside": 43652.79,
    "annual_recovery_pivot": 5253.0,
    "sub_monthly_survival": 0.95,
    "promo_rate_reduction_factor": 0.7,
    "lazada_mix_shift_pp": 0.07,
    "seasonal_hist_start": "2024-01",
    "trend_cap_low": 0.85,
    "trend_cap_high": 1.15,
}

CHANNEL_MAP = {
    "DTC": "DTC",
    "Lazada": "Lazada",
    "Shopee": "Shopee",
    "Marketplace": "Other",
    "Other Marketplace": "Other",
    "Draft Order": "Other",
    "Bulk Import": "Other",
    "POS": "Other",
    "Email": "DTC",
    "TikTok": "Other",
    "Shop App": "Other",
    "Affiliate": "Other",
}

FORECAST_CHANNELS = ["DTC", "Lazada", "Shopee", "Other"]

DOCUMENTED_REPEAT = {
    "DTC": 0.2170,
    "Lazada": 0.2893,
    "Shopee": 0.1832,
    "Other": 0.1700,
}


def _load_json_if_exists(path: Path) -> dict:
    if path.exists():
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def load_forecast_params() -> dict:
    cfg = _load_json_if_exists(BASE / "configs" / "forecast_2026_params.json")
    ds6 = _load_json_if_exists(OUTPUT_DIR / "ds6_metrics.json")
    roopa = _load_json_if_exists(OUTPUT_DIR / "ds6_roopa_metrics.json")
    clv = _load_json_if_exists(OUTPUT_DIR / "clv_decay_metrics.json")

    clv_override = {}
    if isinstance(clv.get("sub_monthly_survival_proxy"), dict) and "value" in clv["sub_monthly_survival_proxy"]:
        clv_override["sub_monthly_survival"] = clv["sub_monthly_survival_proxy"]["value"]

    roopa_override = {}
    if isinstance(roopa.get("repeat_month_multipliers"), dict):
        roopa_override["repeat_month_multipliers"] = roopa["repeat_month_multipliers"]
    sim = roopa.get("scenario_simulation") or {}
    if isinstance(sim, dict):
        if "annual_recovery_pivot" in sim:
            roopa_override["annual_recovery_pivot"] = sim["annual_recovery_pivot"]
        if "discount_acq_rate_status_quo" in sim:
            roopa_override["promo_acq_rate"] = sim["discount_acq_rate_status_quo"]
    elif "annual_recovery_pivot" in roopa:
        roopa_override["annual_recovery_pivot"] = roopa["annual_recovery_pivot"]

    if "subscription_forecast_monthly_2026" in clv:
        clv_override["subscription_forecast_monthly_2026"] = clv["subscription_forecast_monthly_2026"]

    return {**DEFAULT_FORECAST_PARAMS, **cfg, **ds6, **roopa_override, **clv_override}


def load_data():
    cp = pd.read_parquet(GOLD_DIR / "gold_customer_profiles.parquet")
    co = pd.read_parquet(GOLD_DIR / "gold_customer_orders.parquet")
    subs = pd.read_parquet(GOLD_DIR / "gold_subscription_behaviour.parquet")
    cohorts_ch = pd.read_parquet(GOLD_DIR / "gold_retention_cohorts_channel.parquet")
    return cp, co, subs, cohorts_ch


def prepare_customer_base(cp: pd.DataFrame, co: pd.DataFrame) -> pd.DataFrame:
    cp = cp.copy()
    cp["fc_channel"] = cp["acquisition_channel"].map(CHANNEL_MAP).fillna("Other")
    cp["first_month"] = pd.to_datetime(cp["first_order_date"]).dt.tz_localize(None).dt.to_period("M")
    first_orders = co[co["is_first_order"] == True][["customer_id", "price_total"]].rename(
        columns={"price_total": "first_order_aov"}
    )
    return cp.merge(first_orders, on="customer_id", how="left")


def extract_parameters(base: pd.DataFrame, subs: pd.DataFrame, forecast_params: dict | None = None) -> dict:
    channel_stats = (
        base.groupby("fc_channel")
        .agg(
            repeat_rate_90d=("repeat_purchase_90d", "mean"),
            first_order_aov=("first_order_aov", "mean"),
            customers=("customer_id", "count"),
        )
        .round(4)
    )
    repeat_rates, first_aov = {}, {}
    for ch in FORECAST_CHANNELS:
        if ch in channel_stats.index:
            repeat_rates[ch] = float(DOCUMENTED_REPEAT.get(ch, channel_stats.loc[ch, "repeat_rate_90d"]))
            first_aov[ch] = float(channel_stats.loc[ch, "first_order_aov"])
        else:
            repeat_rates[ch] = 0.17
            first_aov[ch] = 60.0

    fp = forecast_params or {}
    ds6_debug = fp.get("ds6_debug") or _load_json_if_exists(OUTPUT_DIR / "ds6_metrics.json").get("ds6_debug", {})
    full_price_rate = float(ds6_debug.get("full_price_rate", 0.2389))
    high_mag_rate = float(ds6_debug.get("high_mag_rate", 0.1978))
    promo_repeat_ratio = high_mag_rate / full_price_rate if full_price_rate else 0.83

    return {
        "overall_repeat_rate": OVERALL_REPEAT_RATE,
        "subscriber_repeat_rate": SUBSCRIBER_REPEAT_RATE,
        "non_subscriber_repeat_rate": NON_SUBSCRIBER_REPEAT_RATE,
        "repeat_rates": repeat_rates,
        "first_aov": first_aov,
        "active_subscribers": int((~subs["is_churned"].fillna(True)).sum()),
        "sub_aov": float(subs["avg_order_value"].mean()),
        "channel_stats": channel_stats,
        "promo_gap_pp": float(fp.get("promo_gap_pp", 0.0411)),
        "promo_lost_revenue_per_cohort": float(fp.get("promo_lost_revenue_per_cohort", 2626.0)),
        "lazada_winback_conservative": float(fp.get("lazada_winback_conservative", 9776.0)),
        "lazada_winback_upside": float(fp.get("lazada_winback_upside", 43652.79)),
        "sub_monthly_survival": float(fp.get("sub_monthly_survival", 0.95)),
        "annual_recovery_pivot": float(fp.get("annual_recovery_pivot", 5253.0)),
        "promo_acq_rate": float(fp.get("promo_acq_rate", 0.054)),
        "promo_repeat_ratio": promo_repeat_ratio,
        "promo_rate_reduction_factor": float(fp.get("promo_rate_reduction_factor", 0.7)),
        "lazada_mix_shift_pp": float(fp.get("lazada_mix_shift_pp", 0.07)),
        "forecast_version": fp.get("forecast_version", FORECAST_VERSION),
    }


def historical_monthly_acquisitions(base: pd.DataFrame, start: str = "2024-01", end: str | None = None) -> pd.DataFrame:
    hist = base[base["first_month"] >= start]
    if end is not None:
        hist = hist[hist["first_month"] <= end]
    hist = (
        hist.groupby(["first_month", "fc_channel"])
        .size()
        .unstack(fill_value=0)
        .reindex(columns=FORECAST_CHANNELS, fill_value=0)
    )
    return hist


# ---------------------------------------------------------------------------
# v1 acquisition
# ---------------------------------------------------------------------------

def project_2026_monthly(hist: pd.DataFrame) -> pd.DataFrame:
    months_2026 = pd.period_range("2026-01", "2026-12", freq="M")
    recent = hist.loc[hist.index >= "2025-10"] if "2025-10" in hist.index else hist.tail(6)
    avg_monthly_total = recent.sum(axis=1).mean()
    anchor = hist.loc["2026-03"] if "2026-03" in hist.index else recent.iloc[-1]
    mix = anchor / anchor.sum()
    rows = []
    for m in months_2026:
        for ch in FORECAST_CHANNELS:
            rows.append({"month": m, "fc_channel": ch, "new_customers": avg_monthly_total * mix[ch]})
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# v2 acquisition — seasonal + mild trend
# ---------------------------------------------------------------------------

def _seasonal_indices(hist: pd.DataFrame) -> dict[str, dict[int, float]]:
    seasonal: dict[str, dict[int, float]] = {}
    for ch in FORECAST_CHANNELS:
        series = hist[ch] if ch in hist.columns else pd.Series(0.0, index=hist.index)
        by_cal_month = series.groupby(series.index.month).mean()
        overall = float(series.mean()) if series.mean() > 0 else 1.0
        seasonal[ch] = {
            m: float(by_cal_month.get(m, overall) / overall) if overall else 1.0 for m in range(1, 13)
        }
    total = hist.sum(axis=1)
    by_total = total.groupby(total.index.month).mean()
    overall_t = float(total.mean()) if total.mean() > 0 else 1.0
    seasonal["_total"] = {
        m: float(by_total.get(m, overall_t) / overall_t) if overall_t else 1.0 for m in range(1, 13)
    }
    return seasonal


def project_2026_monthly_seasonal(
    hist: pd.DataFrame,
    forecast_params: dict,
    channel_mix: dict[str, float] | None = None,
) -> pd.DataFrame:
    hist_start = forecast_params.get("seasonal_hist_start", "2024-01")
    hist = hist.loc[hist.index >= hist_start] if len(hist) else hist
    months_2026 = pd.period_range("2026-01", "2026-12", freq="M")

    recent = hist.loc[hist.index >= "2025-10"] if "2025-10" in hist.index else hist.tail(6)
    base_run_rate = {ch: float(recent[ch].mean()) if ch in recent.columns else 0.0 for ch in FORECAST_CHANNELS}

    if len(hist) >= 6:
        recent3 = float(hist.tail(3).sum(axis=1).mean())
        prior3 = float(hist.iloc[-6:-3].sum(axis=1).mean())
        trend_factor = float(
            np.clip(recent3 / prior3 if prior3 else 1.0,
                    forecast_params.get("trend_cap_low", 0.85),
                    forecast_params.get("trend_cap_high", 1.15))
        )
    else:
        trend_factor = 1.0

    if channel_mix is None:
        anchor = hist.loc["2026-03"] if "2026-03" in hist.index else recent.sum()
        channel_mix = (anchor / anchor.sum()).to_dict()

    seasonal = _seasonal_indices(hist)
    rows = []
    for m in months_2026:
        cal_m = m.month
        total_seasonal = seasonal["_total"].get(cal_m, 1.0)
        month_total = sum(base_run_rate[ch] for ch in FORECAST_CHANNELS) * total_seasonal * trend_factor
        for ch in FORECAST_CHANNELS:
            ch_seasonal = seasonal[ch].get(cal_m, 1.0)
            raw = base_run_rate[ch] * ch_seasonal * trend_factor
            raw_sum = sum(base_run_rate[c] * seasonal[c].get(cal_m, 1.0) for c in FORECAST_CHANNELS) or 1.0
            mix_weight = channel_mix.get(ch, 0.25)
            n = month_total * mix_weight * (raw / raw_sum) if raw_sum else month_total * mix_weight
            rows.append({"month": m, "fc_channel": ch, "new_customers": n})
    return pd.DataFrame(rows)


def apply_lazada_mix_shift(projected: pd.DataFrame, shift_pp: float) -> pd.DataFrame:
    if shift_pp <= 0:
        return projected.copy()
    out = projected.copy()
    for month in out["month"].unique():
        mask = out["month"] == month
        sub = out.loc[mask].set_index("fc_channel")
        total = sub["new_customers"].sum()
        shift_n = total * shift_pp
        if "Shopee" in sub.index and "Lazada" in sub.index:
            sub.loc["Shopee", "new_customers"] = max(0.0, sub.loc["Shopee", "new_customers"] - shift_n)
            sub.loc["Lazada", "new_customers"] = sub.loc["Lazada", "new_customers"] + shift_n
        for ch in FORECAST_CHANNELS:
            out.loc[mask & (out["fc_channel"] == ch), "new_customers"] = sub.loc[ch, "new_customers"]
    return out


def build_new_acq_revenue(projected: pd.DataFrame, first_aov: dict) -> pd.DataFrame:
    projected = projected.copy()
    projected["new_acq_revenue"] = projected.apply(
        lambda r: r["new_customers"] * first_aov[r["fc_channel"]], axis=1
    )
    return projected.groupby("month").agg(
        new_customers=("new_customers", "sum"),
        new_acq_revenue=("new_acq_revenue", "sum"),
    )


# ---------------------------------------------------------------------------
# Repeat layers
# ---------------------------------------------------------------------------

def build_repeat_revenue(
    projected: pd.DataFrame,
    repeat_rates: dict,
    first_aov: dict,
    repeat_aov_factor: float = 0.95,
    repeat_month_multipliers: dict[str, float] | None = None,
) -> pd.Series:
    months = sorted(projected["month"].unique())
    proj_pivot = projected.pivot(index="month", columns="fc_channel", values="new_customers").fillna(0)
    repeat_by_month = {}
    for m in months:
        total_repeat = 0.0
        for lag in [1, 2, 3]:
            prior = m - lag
            if prior not in proj_pivot.index:
                continue
            for ch in FORECAST_CHANNELS:
                if ch not in proj_pivot.columns:
                    continue
                n = proj_pivot.loc[prior, ch]
                rate = repeat_rates[ch] / 3
                aov = first_aov[ch] * repeat_aov_factor
                total_repeat += n * rate * aov
        if repeat_month_multipliers:
            total_repeat *= float(repeat_month_multipliers.get(str(m), 1.0))
        repeat_by_month[m] = total_repeat
    return pd.Series(repeat_by_month, name="repeat_revenue")


def build_repeat_revenue_cohort(
    projected: pd.DataFrame,
    repeat_rates: dict,
    first_aov: dict,
    promo_acq_rate: float,
    promo_repeat_ratio: float,
    repeat_aov_factor: float = 0.95,
    repeat_month_multipliers: dict[str, float] | None = None,
) -> pd.Series:
    months = sorted(projected["month"].unique())
    proj_pivot = projected.pivot(index="month", columns="fc_channel", values="new_customers").fillna(0)
    repeat_by_month = {m: 0.0 for m in months}

    for t0 in months:
        for ch in FORECAST_CHANNELS:
            if ch not in proj_pivot.columns:
                continue
            n = float(proj_pivot.loc[t0, ch])
            n_promo = n * promo_acq_rate
            n_organic = n * (1.0 - promo_acq_rate)
            rate_org = repeat_rates[ch]
            rate_promo = repeat_rates[ch] * promo_repeat_ratio
            aov = first_aov[ch] * repeat_aov_factor
            for lag in [1, 2, 3]:
                t = t0 + lag
                if t not in repeat_by_month:
                    continue
                repeat_by_month[t] += (n_organic * rate_org / 3 + n_promo * rate_promo / 3) * aov

    if repeat_month_multipliers:
        for m in months:
            repeat_by_month[m] *= float(repeat_month_multipliers.get(str(m), 1.0))

    return pd.Series(repeat_by_month, name="repeat_revenue")


# ---------------------------------------------------------------------------
# Subscription layers
# ---------------------------------------------------------------------------

def build_subscription_revenue(params: dict, months: pd.PeriodIndex) -> pd.Series:
    active = params["active_subscribers"]
    sub_aov = params["sub_aov"]
    survival = params["sub_monthly_survival"]
    rev = {}
    current_active = active
    for m in months:
        rev[m] = current_active * sub_aov
        current_active *= survival
    return pd.Series(rev, name="subscription_revenue")


def build_subscription_revenue_v2(params: dict, months: pd.PeriodIndex, forecast_params: dict) -> pd.Series:
  monthly_json = forecast_params.get("subscription_forecast_monthly_2026")
  if isinstance(monthly_json, dict):
      rev = {}
      for m in months:
          rev[m] = float(monthly_json.get(str(m), monthly_json.get(m.strftime("%Y-%m"), 0.0)))
      if any(v > 0 for v in rev.values()):
          return pd.Series(rev, name="subscription_revenue")

  clv = _load_json_if_exists(OUTPUT_DIR / "clv_decay_metrics.json")
  portfolio = clv.get("portfolio", {})
  horizons = [30, 60, 90, 180, 365]
  rates = []
  for h in horizons:
      key = f"{h}d"
      rates.append(float(portfolio.get(key, {}).get("monthly_rate", 0.0)))
  if not any(rates):
      return build_subscription_revenue(params, months)

  base_jan = params["active_subscribers"] * params["sub_aov"]
  scale = base_jan / rates[0] if rates[0] else 1.0
  rev = {}
  for i, m in enumerate(months, start=1):
      days = min(i * 30, 365)
      rate = float(np.interp(days, horizons, rates))
      rev[m] = rate * scale
  return pd.Series(rev, name="subscription_revenue")


def assemble_scenario(
    months: pd.PeriodIndex,
    new_acq: pd.DataFrame,
    repeat_rev: pd.Series,
    sub_rev: pd.Series,
    scenario: str,
    repeat_multiplier: float = 1.0,
    repeat_monthly_addon: float = 0.0,
    winback_lump: float = 0.0,
    winback_month: str | None = None,
) -> pd.DataFrame:
    df = pd.DataFrame(index=months)
    df["scenario"] = scenario
    df["new_customers"] = new_acq["new_customers"]
    df["new_acq_revenue"] = new_acq["new_acq_revenue"]
    df["repeat_revenue"] = repeat_rev * repeat_multiplier + repeat_monthly_addon
    df["subscription_revenue"] = sub_rev
    df["winback_revenue"] = 0.0
    if winback_lump > 0 and winback_month:
        wm = pd.Period(winback_month, freq="M")
        if wm in df.index:
            df.loc[wm, "winback_revenue"] = winback_lump
    df["total_revenue"] = (
        df["new_acq_revenue"] + df["repeat_revenue"] + df["subscription_revenue"] + df["winback_revenue"]
    )
    return df.reset_index(names="month")


def historical_monthly_total_revenue(co: pd.DataFrame, start: str = "2024-01") -> pd.Series:
    orders = co.copy()
    orders["order_month"] = (
        pd.to_datetime(orders["processed_at"], utc=True, errors="coerce")
        .dt.tz_localize(None)
        .dt.to_period("M")
    )
    monthly = orders.dropna(subset=["order_month"]).groupby("order_month")["price_total"].sum().sort_index()
    return monthly.loc[monthly.index >= start]


# ---------------------------------------------------------------------------
# Core forecast builder (v1 / v2)
# ---------------------------------------------------------------------------

def build_forecast(
    version: str | None = None,
    end_train: str | None = None,
    cp: pd.DataFrame | None = None,
    co: pd.DataFrame | None = None,
    subs: pd.DataFrame | None = None,
    cohorts_ch: pd.DataFrame | None = None,
    base: pd.DataFrame | None = None,
    write_outputs: bool = True,
):
    forecast_params = load_forecast_params()
    version = version or forecast_params.get("forecast_version", FORECAST_VERSION)

    if cp is None:
        cp, co, subs, cohorts_ch = load_data()
    if base is None:
        base = prepare_customer_base(cp, co)

    params = extract_parameters(base, subs, forecast_params)
    hist = historical_monthly_acquisitions(
        base, start=forecast_params.get("seasonal_hist_start", "2024-01"),
        end=end_train,
    )
    months_2026 = pd.period_range("2026-01", "2026-12", freq="M")
    repeat_mult = forecast_params.get("repeat_month_multipliers")

    if version == "v2":
        projected_sq = project_2026_monthly_seasonal(hist, forecast_params)
        projected_lazada = apply_lazada_mix_shift(projected_sq, params["lazada_mix_shift_pp"])
        promo_sq = params["promo_acq_rate"]
        promo_sp = params["promo_acq_rate"] * params["promo_rate_reduction_factor"]

        repeat_sq = build_repeat_revenue_cohort(
            projected_sq, params["repeat_rates"], params["first_aov"],
            promo_acq_rate=promo_sq, promo_repeat_ratio=params["promo_repeat_ratio"],
            repeat_month_multipliers=repeat_mult,
        )
        repeat_lazada = build_repeat_revenue_cohort(
            projected_lazada, params["repeat_rates"], params["first_aov"],
            promo_acq_rate=promo_sq, promo_repeat_ratio=params["promo_repeat_ratio"],
            repeat_month_multipliers=repeat_mult,
        )
        sub_rev = build_subscription_revenue_v2(params, months_2026, forecast_params)
        projected = projected_sq
    else:
        projected_sq = project_2026_monthly(hist)
        projected_lazada = projected_sq
        repeat_sq = build_repeat_revenue(
            projected_sq, params["repeat_rates"], params["first_aov"],
            repeat_month_multipliers=repeat_mult,
        )
        repeat_lazada = repeat_sq
        sub_rev = build_subscription_revenue(params, months_2026)
        projected = projected_sq

    new_acq_sq = build_new_acq_revenue(projected_sq, params["first_aov"])
    new_acq_lazada = build_new_acq_revenue(projected_lazada, params["first_aov"])

    status_quo = assemble_scenario(months_2026, new_acq_sq, repeat_sq, sub_rev, "Status Quo")
    pivot_monthly = params["annual_recovery_pivot"] / 12.0
    if version == "v2":
        promo_sp = params["promo_acq_rate"] * params["promo_rate_reduction_factor"]
        repeat_sp = build_repeat_revenue_cohort(
            projected_sq, params["repeat_rates"], params["first_aov"],
            promo_acq_rate=promo_sp,
            promo_repeat_ratio=params["promo_repeat_ratio"],
            repeat_month_multipliers=repeat_mult,
        )
    else:
        repeat_sp = repeat_sq

    second_purchase = assemble_scenario(
        months_2026, new_acq_sq, repeat_sp, sub_rev,
        "Second-Purchase Push", repeat_monthly_addon=pivot_monthly,
    )
    lazada_winback = assemble_scenario(
        months_2026, new_acq_lazada, repeat_lazada, sub_rev,
        "Lazada Win-back",
        winback_lump=params["lazada_winback_conservative"],
        winback_month="2026-03",
    )
    all_scenarios = pd.concat([status_quo, second_purchase, lazada_winback], ignore_index=True)

    if write_outputs:
        _write_all_outputs(all_scenarios, params, hist, co, projected, forecast_params, version, cohorts_ch)

    return all_scenarios, params, hist, projected


# ---------------------------------------------------------------------------
# v3 — regression / Holt-Winters acquisition + v2 revenue layers
# ---------------------------------------------------------------------------

def build_forecast_with_acquisition(
    acquisition_method: str,
    end_train: str | None = None,
    cp: pd.DataFrame | None = None,
    co: pd.DataFrame | None = None,
    subs: pd.DataFrame | None = None,
    cohorts_ch: pd.DataFrame | None = None,
    base: pd.DataFrame | None = None,
    write_outputs: bool = True,
):
    from acquisition_forecast import load_acq_params, project_acquisition_channels

    if acquisition_method not in ("regression", "holt_winters"):
        raise ValueError(f"acquisition_method must be regression or holt_winters, got {acquisition_method!r}")

    forecast_params = load_forecast_params()
    acq_params = load_acq_params()
    end_train = end_train or acq_params["end_train"]

    if cp is None:
        cp, co, subs, cohorts_ch = load_data()
    if base is None:
        base = prepare_customer_base(cp, co)

    params = extract_parameters(base, subs, forecast_params)
    hist = historical_monthly_acquisitions(
        base, start=acq_params.get("hist_start", "2024-01"), end=end_train,
    )
    months_2026 = pd.period_range("2026-01", "2026-12", freq="M")
    repeat_mult = forecast_params.get("repeat_month_multipliers")

    projected_sq = project_acquisition_channels(
        acquisition_method, hist, months_2026, FORECAST_CHANNELS, acq_params,
    )
    projected_lazada = apply_lazada_mix_shift(projected_sq, params["lazada_mix_shift_pp"])
    promo_sq = params["promo_acq_rate"]
    promo_sp = params["promo_acq_rate"] * params["promo_rate_reduction_factor"]

    repeat_sq = build_repeat_revenue_cohort(
        projected_sq, params["repeat_rates"], params["first_aov"],
        promo_acq_rate=promo_sq, promo_repeat_ratio=params["promo_repeat_ratio"],
        repeat_month_multipliers=repeat_mult,
    )
    repeat_lazada = build_repeat_revenue_cohort(
        projected_lazada, params["repeat_rates"], params["first_aov"],
        promo_acq_rate=promo_sq, promo_repeat_ratio=params["promo_repeat_ratio"],
        repeat_month_multipliers=repeat_mult,
    )
    repeat_sp = build_repeat_revenue_cohort(
        projected_sq, params["repeat_rates"], params["first_aov"],
        promo_acq_rate=promo_sp, promo_repeat_ratio=params["promo_repeat_ratio"],
        repeat_month_multipliers=repeat_mult,
    )
    sub_rev = build_subscription_revenue_v2(params, months_2026, forecast_params)

    new_acq_sq = build_new_acq_revenue(projected_sq, params["first_aov"])
    new_acq_lazada = build_new_acq_revenue(projected_lazada, params["first_aov"])

    status_quo = assemble_scenario(months_2026, new_acq_sq, repeat_sq, sub_rev, "Status Quo")
    pivot_monthly = params["annual_recovery_pivot"] / 12.0
    second_purchase = assemble_scenario(
        months_2026, new_acq_sq, repeat_sp, sub_rev,
        "Second-Purchase Push", repeat_monthly_addon=pivot_monthly,
    )
    lazada_winback = assemble_scenario(
        months_2026, new_acq_lazada, repeat_lazada, sub_rev,
        "Lazada Win-back",
        winback_lump=params["lazada_winback_conservative"],
        winback_month="2026-03",
    )
    all_scenarios = pd.concat([status_quo, second_purchase, lazada_winback], ignore_index=True)

    version_label = f"v3 ({acquisition_method})"
    if write_outputs:
        _write_scenario_outputs_for_method(
            all_scenarios, params, hist, co, projected_sq, acquisition_method,
            forecast_params, cohorts_ch, version_label,
        )

    return all_scenarios, params, hist, projected_sq


def _write_scenario_outputs_for_method(
    all_scenarios,
    params,
    hist,
    co,
    projected,
    acquisition_method,
    forecast_params,
    cohorts_ch,
    version_label,
):
    suffix = acquisition_method
    all_scenarios.to_csv(OUTPUT_DIR / f"forecast_2026_monthly_{suffix}.csv", index=False)
    sq = all_scenarios[all_scenarios["scenario"] == "Status Quo"]
    sq.to_csv(OUTPUT_DIR / f"forecast_2026_monthly_baseline_{suffix}.csv", index=False)
    projected.to_csv(OUTPUT_DIR / f"forecast_2026_acquisition_by_channel_{suffix}.csv", index=False)
    actual_revenue = historical_monthly_total_revenue(co, start="2024-01")
    plot_scenarios(
        all_scenarios,
        OUTPUT_DIR / f"forecast_2026_scenarios_{suffix}.png",
        actual_revenue=actual_revenue,
        version=version_label,
    )
    annual = plot_delta(all_scenarios, OUTPUT_DIR / f"forecast_2026_delta_{suffix}.png")
    plot_stacked_status_quo(sq, OUTPUT_DIR / f"forecast_2026_stacked_status_quo_{suffix}.png")
    plot_acquisition_seasonality(hist, projected, OUTPUT_DIR / f"forecast_2026_acquisition_seasonality_{suffix}.png")
    checks = run_validation(all_scenarios, params, projected, hist, cohorts_ch, version_label)
    return annual, checks


def run_acquisition_scenario_forecasts(
    methods: tuple[str, ...] = ("regression", "holt_winters"),
    end_train: str | None = None,
) -> dict:
    """Build 3-scenario revenue forecasts for each acquisition method; write separate charts."""
    cp, co, subs, cohorts_ch = load_data()
    base = prepare_customer_base(cp, co)
    results = {}
    summary_lines = [
        "# 2026 Revenue Scenarios by Acquisition Method",
        "",
        "Three scenarios (Status Quo, Second-Purchase Push, Lazada Win-back) with v2 repeat/subscription layers.",
        "",
        "| Acquisition method | Status Quo | Second-Purchase Push | Lazada Win-back | SP uplift | LZ uplift |",
        "|--------------------|------------|----------------------|-----------------|-----------|-----------|",
    ]

    for method in methods:
        all_scenarios, params, hist, projected = build_forecast_with_acquisition(
            method,
            end_train=end_train,
            cp=cp, co=co, subs=subs, cohorts_ch=cohorts_ch, base=base,
            write_outputs=True,
        )
        totals = all_scenarios.groupby("scenario")["total_revenue"].sum()
        sq, sp, lz = totals["Status Quo"], totals["Second-Purchase Push"], totals["Lazada Win-back"]
        summary_lines.append(
            f"| {method} | SGD {sq:,.0f} | SGD {sp:,.0f} | SGD {lz:,.0f} "
            f"| +{sp - sq:,.0f} | +{lz - sq:,.0f} |"
        )
        checks = run_validation(all_scenarios, params, projected, hist, cohorts_ch, f"v3-{method}")
        results[method] = {"scenarios": all_scenarios, "totals": totals, "checks": checks}
        print(f"\n=== {method} acquisition — 2026 scenario totals ===")
        for s, t in totals.items():
            print(f"  {s}: SGD {t:,.0f}")
        print(f"  Second-Purchase Push uplift: SGD {sp - sq:,.0f}")
        print(f"  Lazada Win-back uplift: SGD {lz - sq:,.0f}")

    summary_lines += [
        "",
        "## Outputs",
        "",
        "- `forecast_2026_scenarios_regression.png` — monthly revenue, 3 scenarios",
        "- `forecast_2026_scenarios_holt_winters.png` — monthly revenue, 3 scenarios",
        "- Matching delta and stacked charts per method",
    ]
    (OUTPUT_DIR / "forecast_scenarios_by_acquisition.md").write_text("\n".join(summary_lines), encoding="utf-8")
    return results


def _write_all_outputs(all_scenarios, params, hist, co, projected, forecast_params, version, cohorts_ch):
    all_scenarios.to_csv(OUTPUT_DIR / "forecast_2026_monthly.csv", index=False)
    sq = all_scenarios[all_scenarios["scenario"] == "Status Quo"]
    sq.to_csv(OUTPUT_DIR / "forecast_2026_monthly_baseline.csv", index=False)
    projected.to_csv(OUTPUT_DIR / "forecast_2026_acquisition_by_channel.csv", index=False)
    write_assumptions_md(params, hist, forecast_params, version, OUTPUT_DIR / "forecast_assumptions.md")
    actual_revenue = historical_monthly_total_revenue(co, start="2024-01")
    plot_scenarios(all_scenarios, OUTPUT_DIR / "forecast_2026_scenarios.png", actual_revenue=actual_revenue, version=version)
    plot_delta(all_scenarios, OUTPUT_DIR / "forecast_2026_delta.png")
    plot_stacked_status_quo(sq, OUTPUT_DIR / "forecast_2026_stacked_status_quo.png")
    plot_acquisition_seasonality(hist, projected, OUTPUT_DIR / "forecast_2026_acquisition_seasonality.png")
    checks = run_validation(all_scenarios, params, projected, hist, cohorts_ch, version)
    return checks


def write_assumptions_md(params: dict, hist: pd.DataFrame, forecast_params: dict, version: str, path: Path):
    lines = [
        "# 2026 Revenue Forecast Assumptions",
        "",
        f"**Forecast version:** `{version}`",
        "",
        "Auto-generated by `09_revenue_forecast_2026.ipynb` / `scripts/build_forecast_2026.py`.",
        "",
        "## Core parameters",
        "",
        "| Parameter | Value | Source |",
        "|-----------|-------|--------|",
        f"| Overall 90-day repeat rate | {params['overall_repeat_rate']:.2%} | DS1 |",
        f"| Promo retention gap | {params['promo_gap_pp']:.2%} | Roopa DS6 |",
        f"| Promo acquisition rate (status quo) | {params['promo_acq_rate']:.2%} | Roopa scenario_simulation |",
        f"| DS6 Pivot annual recovery | SGD {params.get('annual_recovery_pivot', 0):,.0f} | Roopa DS6 |",
        f"| Lazada win-back (conservative) | SGD {params['lazada_winback_conservative']:,.0f} | DS3-1 |",
        f"| Subscription monthly survival (v1 proxy) | {params['sub_monthly_survival']:.0%} | Benny BG/NBD proxy |",
        f"| Active subscribers (start) | {params['active_subscribers']} | gold_subscription_behaviour |",
        f"| Subscription AOV | SGD {params['sub_aov']:.2f} | gold_subscription_behaviour |",
        "",
        "## Channel repeat rates (90-day)",
        "",
        "| Channel | Rate | First-order AOV (SGD) |",
        "|---------|------|------------------------|",
    ]
    for ch in FORECAST_CHANNELS:
        lines.append(f"| {ch} | {params['repeat_rates'][ch]:.2%} | {params['first_aov'][ch]:.2f} |")

    anchor = hist.loc["2026-03"] if "2026-03" in hist.index else hist.iloc[-1]
    mix = (anchor / anchor.sum() * 100).round(1)
    lines += ["", "## 2026 acquisition mix anchor", "", "| Channel | Share |", "|---------|-------|"]
    for ch in FORECAST_CHANNELS:
        lines.append(f"| {ch} | {mix.get(ch, 0):.1f}% |")

    if version == "v2":
        lines += [
            "",
            "## v2 modeling choices",
            "",
            "- **Acquisition**: seasonal index by calendar month (from 2024+) × mild trend × Mar 2026 mix.",
            "- **Repeat**: cohort engine with organic vs promo split (Roopa rates); Nov/Dec holiday multipliers.",
            "- **Subscription**: BG/NBD portfolio monthly-rate interpolation (Benny `clv_decay_metrics.json`).",
            "- **Second-Purchase Push**: lower promo acquisition rate + Pivot recovery on repeat layer.",
            f"- **Lazada Win-back**: +{params['lazada_mix_shift_pp']:.0%} Lazada mix shift + Mar lump sum.",
            "",
            "## Roopa DS6 integration",
            "",
            "- `outputs/ds6_metrics.json` — promo gap",
            "- `outputs/ds6_roopa_metrics.json` — holiday + Pivot scenario",
        ]
    else:
        lines += [
            "",
            "## v1 modeling choices",
            "",
            "- Flat acquisition run-rate; simple repeat spread; subscription decay proxy.",
        ]
    path.write_text("\n".join(lines), encoding="utf-8")


def plot_scenarios(all_scenarios, output_path, actual_revenue=None, forecast_start="2026-01", version="v2"):
    fig, ax = plt.subplots(figsize=(14, 6))
    if actual_revenue is not None and len(actual_revenue) > 0:
        hist = actual_revenue.sort_index()
        ax.plot(hist.index.astype(str), hist.values, label="Actual revenue", color="#374151",
                linewidth=2.5, linestyle="-", marker="o", markersize=5, zorder=3)
    sq_total = all_scenarios[all_scenarios["scenario"] == "Status Quo"]["total_revenue"].sum()
    sp_total = all_scenarios[all_scenarios["scenario"] == "Second-Purchase Push"]["total_revenue"].sum()
    delta = sp_total - sq_total
    for scenario, color in [("Status Quo", "#6366f1"), ("Second-Purchase Push", "#22c55e"), ("Lazada Win-back", "#f59e0b")]:
        sub = all_scenarios[all_scenarios["scenario"] == scenario].sort_values("month")
        ax.plot(sub["month"].astype(str), sub["total_revenue"], label=f"{scenario} (forecast)",
                color=color, linewidth=2, linestyle="--", marker="o", markersize=5,
                markerfacecolor="white", markeredgewidth=1.5, markeredgecolor=color, zorder=2)
    ax.axvline(x=forecast_start, color="#9ca3af", linestyle=":", linewidth=1.5, alpha=0.9)
    ymax = ax.get_ylim()[1]
    ax.text(forecast_start, ymax * 0.97 if ymax > 0 else 1, "  Forecast →", va="top", ha="left", fontsize=9, color="#6b7280")
    ax.set_title(
        f"Monthly Revenue ({version}): Actuals vs 2026 Scenarios\nSecond-Purchase Push uplift vs Status Quo: SGD {delta:,.0f}",
        fontsize=13, fontweight="bold",
    )
    ax.set_xlabel("Month")
    ax.set_ylabel("Monthly Revenue (SGD)")
    ax.legend(loc="upper right", fontsize=9)
    ax.grid(alpha=0.3)
    plt.xticks(rotation=45, ha="right")
    plt.tight_layout()
    plt.savefig(output_path, dpi=150, bbox_inches="tight")
    plt.close()


def plot_delta(all_scenarios, output_path):
    annual = (
        all_scenarios.groupby("scenario")
        .agg(new_acq=("new_acq_revenue", "sum"), repeat=("repeat_revenue", "sum"),
             subscription=("subscription_revenue", "sum"), winback=("winback_revenue", "sum"),
             total=("total_revenue", "sum"))
        .loc[["Status Quo", "Second-Purchase Push", "Lazada Win-back"]]
    )
    fig, axes = plt.subplots(1, 2, figsize=(14, 5))
    sq = annual.loc["Status Quo"]
    components = ["new_acq", "repeat", "subscription", "winback"]
    labels = ["New Acquisition", "Repeat", "Subscription", "Win-back"]
    colors = ["#6366f1", "#22c55e", "#a855f7", "#f59e0b"]
    axes[0].bar(labels, [sq[c] for c in components], color=colors)
    axes[0].set_title(f"Status Quo 2026 Revenue Breakdown\nTotal: SGD {sq['total']:,.0f}")
    axes[0].set_ylabel("SGD")
    for i, v in enumerate([sq[c] for c in components]):
        axes[0].text(i, v, f"{v:,.0f}", ha="center", va="bottom", fontsize=9)
    scenarios = annual.index.tolist()
    totals = annual["total"].values
    axes[1].bar(scenarios, totals, color=["#6366f1", "#22c55e", "#f59e0b"])
    axes[1].set_title("Annual 2026 Revenue by Scenario")
    axes[1].set_ylabel("SGD")
    for i, v in enumerate(totals):
        axes[1].text(i, v, f"{v:,.0f}", ha="center", va="bottom", fontsize=9)
    plt.tight_layout()
    plt.savefig(output_path, dpi=150, bbox_inches="tight")
    plt.close()
    return annual


def plot_stacked_status_quo(sq: pd.DataFrame, output_path: Path):
    fig, ax = plt.subplots(figsize=(12, 5))
    months = sq["month"].astype(str)
    bottom = np.zeros(len(sq))
    for col, label, color in [
        ("new_acq_revenue", "New Acquisition", "#6366f1"),
        ("repeat_revenue", "Repeat", "#22c55e"),
        ("subscription_revenue", "Subscription", "#a855f7"),
        ("winback_revenue", "Win-back", "#f59e0b"),
    ]:
        vals = sq[col].values
        ax.bar(months, vals, bottom=bottom, label=label, color=color)
        bottom += vals
    ax.set_title("Status Quo 2026 — Monthly Revenue by Layer")
    ax.set_ylabel("SGD")
    ax.legend(loc="upper right")
    plt.xticks(rotation=45, ha="right")
    plt.tight_layout()
    plt.savefig(output_path, dpi=150, bbox_inches="tight")
    plt.close()


def plot_acquisition_seasonality(hist: pd.DataFrame, projected: pd.DataFrame, output_path: Path):
    fig, ax = plt.subplots(figsize=(12, 5))
    hist_total = hist.sum(axis=1)
    ax.plot(hist_total.index.astype(str), hist_total.values, label="Actual new customers (monthly)", color="#374151", marker="o")
    proj_total = projected.groupby("month")["new_customers"].sum()
    ax.plot(proj_total.index.astype(str), proj_total.values, label="Forecast new customers (2026)", color="#6366f1", linestyle="--", marker="o")
    ax.set_title("New Customer Acquisition — History vs 2026 Forecast")
    ax.set_ylabel("New customers")
    ax.legend()
    plt.xticks(rotation=45, ha="right")
    plt.tight_layout()
    plt.savefig(output_path, dpi=150, bbox_inches="tight")
    plt.close()


def run_validation(all_scenarios, params, projected, hist, cohorts_ch, version="v2"):
    checks = []
    sq = all_scenarios[all_scenarios["scenario"] == "Status Quo"]
    diff = (
        sq["total_revenue"] - sq["new_acq_revenue"] - sq["repeat_revenue"]
        - sq["subscription_revenue"] - sq["winback_revenue"]
    ).abs().max()
    checks.append(f"Component reconciliation max diff: SGD {diff:.2f} {'PASS' if diff < 1 else 'FAIL'}")

    if projected is not None:
        mix = projected.groupby("fc_channel")["new_customers"].sum()
        mix = mix / mix.sum()
        blended = sum(mix.get(ch, 0) * params["repeat_rates"][ch] for ch in FORECAST_CHANNELS)
        gap_pp = abs(blended - OVERALL_REPEAT_RATE) * 100
        status = "PASS" if gap_pp <= 1.0 else ("WARN (Shopee mix)" if gap_pp <= 2.0 else "FAIL")
        checks.append(f"Blended repeat rate {blended:.2%} vs DS1 {OVERALL_REPEAT_RATE:.2%} (gap {gap_pp:.2f}pp): {status}")

    if version == "v2" and projected is not None:
        proj_monthly = projected.groupby("month")["new_customers"].sum()
        nov = float(proj_monthly.get(pd.Period("2026-11", freq="M"), 0))
        feb = float(proj_monthly.get(pd.Period("2026-02", freq="M"), 0))
        checks.append(f"Nov new customers > Feb: {'PASS' if nov > feb else 'WARN'} ({nov:.0f} vs {feb:.0f})")

    totals = all_scenarios.groupby("scenario")["total_revenue"].sum()
    checks.append(f"Second-Purchase Push >= Status Quo: {'PASS' if totals['Second-Purchase Push'] >= totals['Status Quo'] else 'FAIL'}")
    checks.append(f"Lazada Win-back >= Status Quo: {'PASS' if totals['Lazada Win-back'] >= totals['Status Quo'] else 'FAIL'}")
    return checks


def main():
    version = load_forecast_params().get("forecast_version", FORECAST_VERSION)
    cp, co, subs, cohorts_ch = load_data()
    base = prepare_customer_base(cp, co)
    all_scenarios, params, hist, projected = build_forecast(
        version=version, cp=cp, co=co, subs=subs, cohorts_ch=cohorts_ch, base=base, write_outputs=True,
    )
    checks = run_validation(all_scenarios, params, projected, hist, cohorts_ch, version)

    try:
        import sys
        sys.path.insert(0, str(BASE / "scripts"))
        from forecast_backtest import run_backtest
        run_backtest()
    except Exception as exc:
        print(f"Backtest skipped: {exc}")

    totals = all_scenarios.groupby("scenario")["total_revenue"].sum()
    print(f"=== 2026 Revenue Forecast Summary ({version}) ===")
    for s, t in totals.items():
        print(f"  {s}: SGD {t:,.0f}")
    print(f"\n  Second-Purchase Push uplift: SGD {totals['Second-Purchase Push'] - totals['Status Quo']:,.0f}")
    print("\n=== Validation ===")
    for c in checks:
        print(f"  {c}")
    print(f"\nOutputs written to {OUTPUT_DIR}")
    return all_scenarios, params, checks


if __name__ == "__main__":
    main()
