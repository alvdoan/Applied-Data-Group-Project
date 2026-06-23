"""
2026 Strategic Revenue Forecast — Path 1 (Lean+Creatine DTC bundle) and Path 2 (Inverted funnel + 11.11).

Standalone from the legacy three-scenario forecast. Uses regression acquisition + cohort repeat + BG/NBD sub.

Run from project root:
  python scripts/build_strategic_forecast_2026.py
"""
from __future__ import annotations

import json
import warnings
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

warnings.filterwarnings("ignore", category=FutureWarning)
warnings.filterwarnings("ignore", category=UserWarning)

BASE = Path(__file__).resolve().parent.parent
GOLD_DIR = BASE / "medallion" / "gold"
OUTPUT_DIR = BASE / "outputs"
CONFIG_PATH = BASE / "configs" / "forecast_2026_strategic_params.json"
OUTPUT_DIR.mkdir(exist_ok=True)

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

SCENARIO_COLORS = {
    "Status Quo": "#6366f1",
    "Lean + Creatine DTC Bundle": "#22c55e",
    "Inverted Funnel + 11.11": "#f59e0b",
}


def _load_json_if_exists(path: Path) -> dict:
    if path.exists():
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def load_strategic_params(user_input_override: dict | None = None) -> dict:
    cfg = _load_json_if_exists(CONFIG_PATH)
    roopa = _load_json_if_exists(OUTPUT_DIR / "ds6_roopa_metrics.json")
    clv = _load_json_if_exists(OUTPUT_DIR / "clv_decay_metrics.json")
    ds6 = _load_json_if_exists(OUTPUT_DIR / "ds6_metrics.json")

    merged = {**cfg}
    if isinstance(roopa.get("repeat_month_multipliers"), dict):
        merged["repeat_month_multipliers"] = roopa["repeat_month_multipliers"]
    sim = roopa.get("scenario_simulation") or {}
    if isinstance(sim, dict):
        if "annual_recovery_pivot" in sim:
            merged.setdefault("data_defaults", {})["annual_recovery_pivot"] = sim["annual_recovery_pivot"]
        if "discount_acq_rate_status_quo" in sim:
            merged["promo_acq_rate"] = sim["discount_acq_rate_status_quo"]
    if "subscription_forecast_monthly_2026" in clv:
        merged["subscription_forecast_monthly_2026"] = clv["subscription_forecast_monthly_2026"]
    if isinstance(clv.get("sub_monthly_survival_proxy"), dict):
        merged["sub_monthly_survival"] = clv["sub_monthly_survival_proxy"].get(
            "value", merged.get("sub_monthly_survival", 0.95)
        )

    ds6_debug = ds6.get("ds6_debug", {})
    full_price_rate = float(ds6_debug.get("full_price_rate", 0.2389))
    high_mag_rate = float(ds6_debug.get("high_mag_rate", 0.1978))
    merged["promo_repeat_ratio"] = high_mag_rate / full_price_rate if full_price_rate else 0.83
    merged["ds6_debug"] = ds6_debug

    ui = dict(cfg.get("user_inputs", {}))
    if user_input_override:
        ui.update(user_input_override)
    merged["user_inputs"] = ui
    return merged


def load_data():
    cp = pd.read_parquet(GOLD_DIR / "gold_customer_profiles.parquet")
    co = pd.read_parquet(GOLD_DIR / "gold_customer_orders.parquet")
    subs = pd.read_parquet(GOLD_DIR / "gold_subscription_behaviour.parquet")
    return cp, co, subs


def prepare_customer_base(cp: pd.DataFrame, co: pd.DataFrame) -> pd.DataFrame:
    cp = cp.copy()
    cp["fc_channel"] = cp["acquisition_channel"].map(CHANNEL_MAP).fillna("Other")
    cp["first_month"] = pd.to_datetime(cp["first_order_date"]).dt.tz_localize(None).dt.to_period("M")
    first_orders = co[co["is_first_order"] == True][["customer_id", "price_total"]].rename(
        columns={"price_total": "first_order_aov"}
    )
    return cp.merge(first_orders, on="customer_id", how="left")


def extract_parameters(base: pd.DataFrame, subs: pd.DataFrame, forecast_params: dict) -> dict:
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

    dd = forecast_params.get("data_defaults", {})
    ui = forecast_params.get("user_inputs", {})

    return {
        "repeat_rates": repeat_rates,
        "first_aov": first_aov,
        "active_subscribers": int((~subs["is_churned"].fillna(True)).sum()),
        "sub_aov": float(subs["avg_order_value"].mean()),
        "sub_monthly_survival": float(forecast_params.get("sub_monthly_survival", 0.95)),
        "promo_acq_rate": float(forecast_params.get("promo_acq_rate", 0.054)),
        "promo_repeat_ratio": float(forecast_params.get("promo_repeat_ratio", 0.83)),
        "annual_recovery_pivot": float(
            dd.get("annual_recovery_pivot", ui.get("annual_recovery_pivot", 4893.26))
        ),
        "bundle_repeat_rate_90d": float(dd.get("bundle_repeat_rate_90d", 0.3649)),
        "lean_standalone_repeat_rate_90d": float(dd.get("lean_standalone_repeat_rate_90d", 0.2344)),
        "channel_stats": channel_stats,
    }


def historical_monthly_acquisitions(base: pd.DataFrame, start: str = "2024-01", end: str | None = None) -> pd.DataFrame:
    hist = base[base["first_month"] >= start]
    if end is not None:
        hist = hist[hist["first_month"] <= end]
    return (
        hist.groupby(["first_month", "fc_channel"])
        .size()
        .unstack(fill_value=0)
        .reindex(columns=FORECAST_CHANNELS, fill_value=0)
    )


def historical_monthly_total_revenue(co: pd.DataFrame, start: str = "2024-01") -> pd.Series:
    orders = co.copy()
    orders["order_month"] = (
        pd.to_datetime(orders["processed_at"], utc=True, errors="coerce")
        .dt.tz_localize(None)
        .dt.to_period("M")
    )
    monthly = orders.dropna(subset=["order_month"]).groupby("order_month")["price_total"].sum().sort_index()
    return monthly.loc[monthly.index >= start]


def project_acquisition_regression(hist: pd.DataFrame, months_2026: pd.PeriodIndex, acq_params: dict) -> pd.DataFrame:
    from acquisition_forecast import project_acquisition_channels

    return project_acquisition_channels(
        "regression",
        hist,
        months_2026,
        FORECAST_CHANNELS,
        acq_params,
    )


def scale_projected_customers(projected: pd.DataFrame, multiplier: float) -> pd.DataFrame:
    if multiplier == 1.0:
        return projected.copy()
    out = projected.copy()
    out["new_customers"] = out["new_customers"] * multiplier
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


def build_new_acq_revenue_dtc_bundle(
    projected: pd.DataFrame,
    first_aov: dict,
    bundle_share_dtc: float,
    bundle_aov_multiplier: float,
    bundle_discount_pct: float,
) -> pd.DataFrame:
    projected = projected.copy()
    aov_dtc = first_aov["DTC"]
    aov_bundle = aov_dtc * bundle_aov_multiplier * (1.0 - bundle_discount_pct)
    aov_rest_dtc = aov_dtc

    def row_revenue(r):
        if r["fc_channel"] == "DTC":
            n = r["new_customers"]
            return n * bundle_share_dtc * aov_bundle + n * (1.0 - bundle_share_dtc) * aov_rest_dtc
        return r["new_customers"] * first_aov[r["fc_channel"]]

    projected["new_acq_revenue"] = projected.apply(row_revenue, axis=1)
    return projected.groupby("month").agg(
        new_customers=("new_customers", "sum"),
        new_acq_revenue=("new_acq_revenue", "sum"),
    )


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


def build_repeat_revenue_cohort_dtc_bundle(
    projected: pd.DataFrame,
    repeat_rates: dict,
    first_aov: dict,
    promo_acq_rate: float,
    promo_repeat_ratio: float,
    bundle_share_dtc: float,
    bundle_repeat_rate: float,
    dtc_rest_repeat_rate: float,
    repeat_aov_factor: float = 0.95,
    repeat_month_multipliers: dict[str, float] | None = None,
) -> pd.Series:
    """DTC split into bundle cohort (higher repeat) vs rest (standard promo split)."""
    months = sorted(projected["month"].unique())
    proj_pivot = projected.pivot(index="month", columns="fc_channel", values="new_customers").fillna(0)
    repeat_by_month = {m: 0.0 for m in months}

    for t0 in months:
        for ch in FORECAST_CHANNELS:
            if ch not in proj_pivot.columns:
                continue
            n = float(proj_pivot.loc[t0, ch])
            aov = first_aov[ch] * repeat_aov_factor

            if ch == "DTC":
                n_bundle = n * bundle_share_dtc
                n_rest = n * (1.0 - bundle_share_dtc)
                n_promo = n_rest * promo_acq_rate
                n_organic = n_rest * (1.0 - promo_acq_rate)
                rate_org = dtc_rest_repeat_rate
                rate_promo = dtc_rest_repeat_rate * promo_repeat_ratio
                for lag in [1, 2, 3]:
                    t = t0 + lag
                    if t not in repeat_by_month:
                        continue
                    repeat_by_month[t] += n_bundle * bundle_repeat_rate / 3 * aov
                    repeat_by_month[t] += (n_organic * rate_org / 3 + n_promo * rate_promo / 3) * aov
            else:
                n_promo = n * promo_acq_rate
                n_organic = n * (1.0 - promo_acq_rate)
                rate_org = repeat_rates[ch]
                rate_promo = repeat_rates[ch] * promo_repeat_ratio
                for lag in [1, 2, 3]:
                    t = t0 + lag
                    if t not in repeat_by_month:
                        continue
                    repeat_by_month[t] += (n_organic * rate_org / 3 + n_promo * rate_promo / 3) * aov

    if repeat_month_multipliers:
        for m in months:
            repeat_by_month[m] *= float(repeat_month_multipliers.get(str(m), 1.0))

    return pd.Series(repeat_by_month, name="repeat_revenue")


def build_subscription_revenue(params: dict, months: pd.PeriodIndex, forecast_params: dict) -> pd.Series:
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
    rates = [float(portfolio.get(f"{h}d", {}).get("monthly_rate", 0.0)) for h in horizons]
    if not any(rates):
        active = params["active_subscribers"]
        sub_aov = params["sub_aov"]
        survival = params["sub_monthly_survival"]
        rev, current = {}, active
        for m in months:
            rev[m] = current * sub_aov
            current *= survival
        return pd.Series(rev, name="subscription_revenue")

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
    repeat_monthly_addon: float = 0.0,
    flash_revenue_by_month: dict | None = None,
) -> pd.DataFrame:
    df = pd.DataFrame(index=months)
    df["scenario"] = scenario
    df["new_customers"] = new_acq["new_customers"]
    df["new_acq_revenue"] = new_acq["new_acq_revenue"]
    df["repeat_revenue"] = repeat_rev + repeat_monthly_addon
    df["subscription_revenue"] = sub_rev
    df["flash_revenue"] = 0.0
    if flash_revenue_by_month:
        for m_str, val in flash_revenue_by_month.items():
            wm = pd.Period(m_str, freq="M")
            if wm in df.index:
                df.loc[wm, "flash_revenue"] = float(val)
    df["total_revenue"] = (
        df["new_acq_revenue"] + df["repeat_revenue"] + df["subscription_revenue"] + df["flash_revenue"]
    )
    return df.reset_index(names="month")


def acq_volume_multiplier(promo_sq: float, promo_pivot: float, penalty_per_pp: float) -> float:
    promo_cut_pp = max(0.0, (promo_sq - promo_pivot) * 100.0)
    return max(0.5, 1.0 - promo_cut_pp * penalty_per_pp)


def build_strategic_forecasts(
    user_input_override: dict | None = None,
    write_outputs: bool = True,
) -> tuple[pd.DataFrame, dict, pd.Series]:
    forecast_params = load_strategic_params(user_input_override)
    ui = forecast_params["user_inputs"]
    acq_cfg = {**forecast_params.get("acquisition_forecast", {})}

    cp, co, subs = load_data()
    base = prepare_customer_base(cp, co)
    params = extract_parameters(base, subs, forecast_params)

    hist = historical_monthly_acquisitions(
        base, start=acq_cfg.get("hist_start", "2024-01"), end=acq_cfg.get("end_train", "2025-12")
    )
    months_2026 = pd.period_range("2026-01", "2026-12", freq="M")
    repeat_mult = forecast_params.get("repeat_month_multipliers")

    projected = project_acquisition_regression(hist, months_2026, acq_cfg)
    sub_rev = build_subscription_revenue(params, months_2026, forecast_params)

    promo_sq = params["promo_acq_rate"]
    promo_pivot = float(ui.get("promo_acq_rate_pivot", 0.03))

    # --- Status Quo ---
    new_acq_sq = build_new_acq_revenue(projected, params["first_aov"])
    repeat_sq = build_repeat_revenue_cohort(
        projected,
        params["repeat_rates"],
        params["first_aov"],
        promo_acq_rate=promo_sq,
        promo_repeat_ratio=params["promo_repeat_ratio"],
        repeat_month_multipliers=repeat_mult,
    )
    status_quo = assemble_scenario(months_2026, new_acq_sq, repeat_sq, sub_rev, "Status Quo")

    scenarios = [status_quo]

    # --- Path 1: Lean + Creatine DTC Bundle ---
    if forecast_params.get("scenarios", {}).get("lean_creatine_dtc_bundle", {}).get("enabled", True):
        bundle_share = float(ui.get("bundle_adoption_share_dtc", 0.25))
        bundle_discount = float(ui.get("bundle_discount_pct", 0.10))
        bundle_aov_mult = float(ui.get("bundle_aov_multiplier_vs_dtc", 1.30))

        new_acq_bundle = build_new_acq_revenue_dtc_bundle(
            projected, params["first_aov"], bundle_share, bundle_aov_mult, bundle_discount
        )
        repeat_bundle = build_repeat_revenue_cohort_dtc_bundle(
            projected,
            params["repeat_rates"],
            params["first_aov"],
            promo_acq_rate=promo_sq,
            promo_repeat_ratio=params["promo_repeat_ratio"],
            bundle_share_dtc=bundle_share,
            bundle_repeat_rate=params["bundle_repeat_rate_90d"],
            dtc_rest_repeat_rate=params["lean_standalone_repeat_rate_90d"],
            repeat_month_multipliers=repeat_mult,
        )
        label = forecast_params["scenarios"]["lean_creatine_dtc_bundle"].get(
            "label", "Lean + Creatine DTC Bundle"
        )
        scenarios.append(assemble_scenario(months_2026, new_acq_bundle, repeat_bundle, sub_rev, label))

    # --- Path 2: Inverted Funnel + 11.11 ---
    if forecast_params.get("scenarios", {}).get("inverted_funnel_1111", {}).get("enabled", True):
        acq_mult = acq_volume_multiplier(
            promo_sq, promo_pivot, float(ui.get("acq_volume_penalty_per_promo_pp", 0.015))
        )
        projected_pivot = scale_projected_customers(projected, acq_mult)

        pivot_monthly = params["annual_recovery_pivot"] / 12.0
        crm_extra = float(ui.get("day30_60_crm_extra_recovery_annual", 0.0)) / 12.0
        repeat_addon = pivot_monthly + crm_extra

        new_acq_pivot = build_new_acq_revenue(projected_pivot, params["first_aov"])
        repeat_pivot = build_repeat_revenue_cohort(
            projected_pivot,
            params["repeat_rates"],
            params["first_aov"],
            promo_acq_rate=promo_pivot,
            promo_repeat_ratio=params["promo_repeat_ratio"],
            repeat_month_multipliers=repeat_mult,
        )

        nov_customers = float(ui.get("nov_111_reactivated_customers", 120))
        nov_aov = float(ui.get("nov_111_flash_order_aov", 72.0))
        flash = {"2026-11": nov_customers * nov_aov}

        label = forecast_params["scenarios"]["inverted_funnel_1111"].get(
            "label", "Inverted Funnel + 11.11"
        )
        scenarios.append(
            assemble_scenario(
                months_2026,
                new_acq_pivot,
                repeat_pivot,
                sub_rev,
                label,
                repeat_monthly_addon=repeat_addon,
                flash_revenue_by_month=flash,
            )
        )

    all_scenarios = pd.concat(scenarios, ignore_index=True)
    actual_revenue = historical_monthly_total_revenue(co, start="2024-01")

    if write_outputs:
        all_scenarios.to_csv(OUTPUT_DIR / "forecast_2026_strategic_monthly.csv", index=False)
        plot_strategic_scenarios(
            all_scenarios,
            OUTPUT_DIR / "forecast_2026_strategic_scenarios.png",
            actual_revenue=actual_revenue,
        )
        write_summary_md(all_scenarios, forecast_params, OUTPUT_DIR / "forecast_2026_strategic_summary.md")

    meta = {
        "params": params,
        "user_inputs": ui,
        "projected": projected,
        "acq_volume_multiplier_pivot": acq_volume_multiplier(
            promo_sq, promo_pivot, float(ui.get("acq_volume_penalty_per_promo_pp", 0.015))
        ),
    }
    return all_scenarios, meta, actual_revenue


def plot_strategic_scenarios(
    all_scenarios: pd.DataFrame,
    output_path: Path,
    actual_revenue: pd.Series | None = None,
    forecast_start: str = "2026-01",
):
    fig, ax = plt.subplots(figsize=(14, 6))
    if actual_revenue is not None and len(actual_revenue) > 0:
        hist = actual_revenue.sort_index()
        ax.plot(
            hist.index.astype(str),
            hist.values,
            label="Actual revenue",
            color="#374151",
            linewidth=2.5,
            linestyle="-",
            marker="o",
            markersize=5,
            zorder=3,
        )

    sq_label = "Status Quo"
    sq_total = all_scenarios[all_scenarios["scenario"] == sq_label]["total_revenue"].sum()
    scenario_names = [s for s in all_scenarios["scenario"].unique() if s != sq_label]

    for scenario in [sq_label] + scenario_names:
        sub = all_scenarios[all_scenarios["scenario"] == scenario].sort_values("month")
        color = SCENARIO_COLORS.get(scenario, "#64748b")
        total = sub["total_revenue"].sum()
        uplift = total - sq_total
        suffix = f" (SGD {total:,.0f}" + (f", +{uplift:,.0f})" if scenario != sq_label else ")")
        ax.plot(
            sub["month"].astype(str),
            sub["total_revenue"],
            label=f"{scenario}{suffix}",
            color=color,
            linewidth=2,
            linestyle="--" if scenario != sq_label else "-",
            marker="o",
            markersize=5,
            markerfacecolor="white",
            markeredgewidth=1.5,
            markeredgecolor=color,
            zorder=2,
        )

    ax.axvline(x=forecast_start, color="#9ca3af", linestyle=":", linewidth=1.5, alpha=0.9)
    ymax = ax.get_ylim()[1]
    ax.text(
        forecast_start,
        ymax * 0.97 if ymax > 0 else 1,
        "  Forecast →",
        va="top",
        ha="left",
        fontsize=9,
        color="#6b7280",
    )
    ax.set_title(
        "2026 Strategic Scenarios (Regression Acquisition)\n"
        "Status Quo vs Lean+Creatine DTC Bundle vs Inverted Funnel + 11.11",
        fontsize=13,
        fontweight="bold",
    )
    ax.set_xlabel("Month")
    ax.set_ylabel("Monthly Revenue (SGD)")
    ax.legend(loc="upper right", fontsize=8)
    ax.grid(alpha=0.3)
    plt.xticks(rotation=45, ha="right")
    plt.tight_layout()
    plt.savefig(output_path, dpi=150, bbox_inches="tight")
    plt.close()


def write_summary_md(all_scenarios: pd.DataFrame, forecast_params: dict, path: Path):
    totals = all_scenarios.groupby("scenario").agg(
        total_revenue=("total_revenue", "sum"),
        new_acq=("new_acq_revenue", "sum"),
        repeat=("repeat_revenue", "sum"),
        subscription=("subscription_revenue", "sum"),
        flash=("flash_revenue", "sum"),
    )
    sq = totals.loc["Status Quo", "total_revenue"]
    ui = forecast_params.get("user_inputs", {})

    lines = [
        "# 2026 Strategic Forecast Summary",
        "",
        "| Scenario | Total 2026 | vs Status Quo | New acq | Repeat | Subscription | Flash |",
        "|----------|------------|---------------|---------|--------|--------------|-------|",
    ]
    for scenario, row in totals.iterrows():
        uplift = row["total_revenue"] - sq
        uplift_str = "—" if scenario == "Status Quo" else f"+SGD {uplift:,.0f}"
        lines.append(
            f"| {scenario} | SGD {row['total_revenue']:,.0f} | {uplift_str} "
            f"| SGD {row['new_acq']:,.0f} | SGD {row['repeat']:,.0f} "
            f"| SGD {row['subscription']:,.0f} | SGD {row['flash']:,.0f} |"
        )

    lines += [
        "",
        "## Team assumptions (impute in notebook Cell 1 or JSON `user_inputs`)",
        "",
        f"- `bundle_adoption_share_dtc`: {ui.get('bundle_adoption_share_dtc')}",
        f"- `bundle_aov_multiplier_vs_dtc`: {ui.get('bundle_aov_multiplier_vs_dtc')}",
        f"- `promo_acq_rate_pivot`: {ui.get('promo_acq_rate_pivot')}",
        f"- `acq_volume_penalty_per_promo_pp`: {ui.get('acq_volume_penalty_per_promo_pp')}",
        f"- `nov_111_reactivated_customers`: {ui.get('nov_111_reactivated_customers')}",
        f"- `nov_111_flash_order_aov`: {ui.get('nov_111_flash_order_aov')}",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    import sys

    sys.path.insert(0, str(BASE / "scripts"))
    all_scenarios, meta, _ = build_strategic_forecasts(write_outputs=True)
    totals = all_scenarios.groupby("scenario")["total_revenue"].sum()
    print("\n=== 2026 Strategic Scenario Totals ===")
    for name, total in totals.items():
        print(f"  {name}: SGD {total:,.0f}")
    sq = totals["Status Quo"]
    for name, total in totals.items():
        if name != "Status Quo":
            print(f"  {name} uplift vs Status Quo: SGD {total - sq:,.0f}")
    print(f"\nWrote {OUTPUT_DIR / 'forecast_2026_strategic_scenarios.png'}")
