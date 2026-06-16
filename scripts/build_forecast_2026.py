"""
2026 Revenue Forecast — builds monthly scenario outputs for LushProtein.
Run from project root: python scripts/build_forecast_2026.py
"""
from __future__ import annotations

import json
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

BASE = Path(__file__).resolve().parent.parent
GOLD_DIR = BASE / "medallion" / "gold"
OUTPUT_DIR = BASE / "outputs"
OUTPUT_DIR.mkdir(exist_ok=True)

# ---------------------------------------------------------------------------
# Forecast parameter defaults.
#
# These were originally hardcoded constants. They are now loaded from:
# - configs/forecast_2026_params.json (tracked, editable)
# - outputs/ds6_metrics.json (generated from DS6 notebook logic)
#
# If those files are missing, we fall back to these defaults.
# ---------------------------------------------------------------------------
OVERALL_REPEAT_RATE = 0.2191
SUBSCRIBER_REPEAT_RATE = 0.85
NON_SUBSCRIBER_REPEAT_RATE = 0.195

DEFAULT_FORECAST_PARAMS = {
    "promo_gap_pp": 0.0411,
    "promo_lost_revenue_per_cohort": 2626.0,
    "lazada_winback_conservative": 9776.0,
    "lazada_winback_upside": 43652.79,
    # Proxy until the BG/NBD decay curve is wired in.
    "sub_monthly_survival": 0.95,
}


def _load_json_if_exists(path: Path) -> dict:
    if path.exists():
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def load_forecast_params() -> dict:
    """
    Load forecast parameters from config + generated DS6 outputs.

    Priority order (later overrides earlier):
    1) DEFAULT_FORECAST_PARAMS
    2) configs/forecast_2026_params.json
    3) outputs/ds6_metrics.json
    4) outputs/ds6_roopa_metrics.json
    5) outputs/clv_decay_metrics.json (Benny BG/NBD proxy)
    """
    cfg = _load_json_if_exists(BASE / "configs" / "forecast_2026_params.json")
    ds6 = _load_json_if_exists(OUTPUT_DIR / "ds6_metrics.json")
    roopa = _load_json_if_exists(OUTPUT_DIR / "ds6_roopa_metrics.json")
    clv = _load_json_if_exists(OUTPUT_DIR / "clv_decay_metrics.json")

    # Normalize optional nested payloads into top-level keys used by the forecast.
    clv_override = {}
    if "sub_monthly_survival_proxy" in clv and isinstance(clv["sub_monthly_survival_proxy"], dict):
        if "value" in clv["sub_monthly_survival_proxy"]:
            clv_override["sub_monthly_survival"] = clv["sub_monthly_survival_proxy"]["value"]

    roopa_override = {}
    if "repeat_month_multipliers" in roopa and isinstance(roopa["repeat_month_multipliers"], dict):
        roopa_override["repeat_month_multipliers"] = roopa["repeat_month_multipliers"]

    params = {**DEFAULT_FORECAST_PARAMS, **cfg, **ds6, **roopa_override, **clv_override}
    return params

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

# DS3-1 documented repeat rates (used when computed rates differ slightly)
DOCUMENTED_REPEAT = {
    "DTC": 0.2170,
    "Lazada": 0.2893,
    "Shopee": 0.1832,
    "Other": 0.1700,
}


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
    base = cp.merge(first_orders, on="customer_id", how="left")
    return base


def extract_parameters(base: pd.DataFrame, subs: pd.DataFrame) -> dict:
    channel_stats = (
        base.groupby("fc_channel")
        .agg(
            repeat_rate_90d=("repeat_purchase_90d", "mean"),
            first_order_aov=("first_order_aov", "mean"),
            customers=("customer_id", "count"),
        )
        .round(4)
    )

    repeat_rates = {}
    first_aov = {}
    for ch in FORECAST_CHANNELS:
        if ch in channel_stats.index:
            repeat_rates[ch] = float(
                DOCUMENTED_REPEAT.get(ch, channel_stats.loc[ch, "repeat_rate_90d"])
            )
            first_aov[ch] = float(channel_stats.loc[ch, "first_order_aov"])
        else:
            repeat_rates[ch] = 0.17
            first_aov[ch] = 60.0

    active_subs = int((~subs["is_churned"].fillna(True)).sum())
    sub_aov = float(subs["avg_order_value"].mean())

    return {
        "overall_repeat_rate": OVERALL_REPEAT_RATE,
        "subscriber_repeat_rate": SUBSCRIBER_REPEAT_RATE,
        "non_subscriber_repeat_rate": NON_SUBSCRIBER_REPEAT_RATE,
        "repeat_rates": repeat_rates,
        "first_aov": first_aov,
        "active_subscribers": active_subs,
        "sub_aov": sub_aov,
        "channel_stats": channel_stats,
    }


def historical_monthly_acquisitions(base: pd.DataFrame) -> pd.DataFrame:
    hist = (
        base[base["first_month"] >= "2024-01"]
        .groupby(["first_month", "fc_channel"])
        .size()
        .unstack(fill_value=0)
        .reindex(columns=FORECAST_CHANNELS, fill_value=0)
    )
    return hist


def project_2026_monthly(hist: pd.DataFrame) -> pd.DataFrame:
    """Project 2026 monthly new customers using trailing mix + volume trend."""
    months_2026 = pd.period_range("2026-01", "2026-12", freq="M")

    # Volume: average monthly new customers in last 6 months of actual data
    recent = hist.loc[hist.index >= "2025-10"]
    avg_monthly_total = recent.sum(axis=1).mean()

    # Channel mix: Mar 2026 actual shares (anchor per plan)
    anchor = hist.loc["2026-03"] if "2026-03" in hist.index else recent.iloc[-1]
    mix = anchor / anchor.sum()

    rows = []
    for m in months_2026:
        total = avg_monthly_total
        for ch in FORECAST_CHANNELS:
            rows.append({"month": m, "fc_channel": ch, "new_customers": total * mix[ch]})
    return pd.DataFrame(rows)


def build_new_acq_revenue(projected: pd.DataFrame, first_aov: dict) -> pd.DataFrame:
    projected = projected.copy()
    projected["new_acq_revenue"] = projected.apply(
        lambda r: r["new_customers"] * first_aov[r["fc_channel"]], axis=1
    )
    monthly = projected.groupby("month").agg(
        new_customers=("new_customers", "sum"),
        new_acq_revenue=("new_acq_revenue", "sum"),
    )
    return monthly


def build_repeat_revenue(
    projected: pd.DataFrame,
    repeat_rates: dict,
    first_aov: dict,
    repeat_aov_factor: float = 0.95,
    repeat_month_multipliers: dict[str, float] | None = None,
) -> pd.Series:
    """
    For each forecast month, sum expected repeat revenue from cohorts acquired
    in the prior 1-3 months (90-day repeat window).
    """
    months = sorted(projected["month"].unique())
    repeat_by_month = {}

    proj_pivot = projected.pivot(index="month", columns="fc_channel", values="new_customers").fillna(0)

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
                rate = repeat_rates[ch] / 3  # spread 90d repeat across 3 months
                aov = first_aov[ch] * repeat_aov_factor
                total_repeat += n * rate * aov

        # Optional month-specific multiplier (e.g., holiday churn trap adjustments).
        if repeat_month_multipliers:
            mult = float(repeat_month_multipliers.get(str(m), 1.0))
            total_repeat *= mult

        repeat_by_month[m] = total_repeat

    return pd.Series(repeat_by_month, name="repeat_revenue")


def build_subscription_revenue(
    params: dict,
    months: pd.PeriodIndex,
) -> pd.Series:
    """Flat monthly subscription revenue proxy (Benny plug-in point)."""
    active = params["active_subscribers"]
    sub_aov = params["sub_aov"]
    survival = params["sub_monthly_survival"]

    rev = {}
    current_active = active
    for m in months:
        monthly_rev = current_active * sub_aov
        rev[m] = monthly_rev
        current_active *= survival
    return pd.Series(rev, name="subscription_revenue")


def assemble_scenario(
    months: pd.PeriodIndex,
    new_acq: pd.DataFrame,
    repeat_rev: pd.Series,
    sub_rev: pd.Series,
    scenario: str,
    repeat_multiplier: float = 1.0,
    winback_lump: float = 0.0,
    winback_month: str | None = None,
) -> pd.DataFrame:
    df = pd.DataFrame(index=months)
    df["scenario"] = scenario
    df["new_customers"] = new_acq["new_customers"]
    df["new_acq_revenue"] = new_acq["new_acq_revenue"]
    df["repeat_revenue"] = repeat_rev * repeat_multiplier
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


def write_assumptions_md(params: dict, hist: pd.DataFrame, assumptions_path: Path):
  lines = [
    "# 2026 Revenue Forecast Assumptions",
    "",
    "Auto-generated by `09_revenue_forecast_2026.ipynb` / `scripts/build_forecast_2026.py`.",
    "",
    "## Core parameters",
    "",
    "| Parameter | Value | Source |",
    "|-----------|-------|--------|",
    f"| Overall 90-day repeat rate | {params['overall_repeat_rate']:.2%} | DS1 `05_ds1_repeat_purchase_prediction.ipynb` |",
    f"| Promo retention gap | {params['promo_gap_pp']:.2%} | DS6 `04_analysis_ds6_acquisition_dynamics.ipynb` |",
    f"| Lost repeat revenue per cohort cycle | SGD {params['promo_lost_revenue_per_cohort']:,.0f} | DS6 |",
    f"| Subscriber repeat rate | {params['subscriber_repeat_rate']:.1%} | DS1 Section 6 |",
    f"| Non-subscriber repeat rate | {params['non_subscriber_repeat_rate']:.1%} | DS1 Section 6 |",
    f"| Lazada win-back (conservative) | SGD {params['lazada_winback_conservative']:,.0f} | DS3-1 Section 6 |",
    f"| Lazada win-back (upside) | SGD {params['lazada_winback_upside']:,.0f} | DS3-1 Section 6 |",
    f"| Subscription monthly survival (proxy) | {params['sub_monthly_survival']:.0%} | Proxy until Benny BG/NBD decay |",
    f"| Active subscribers (start) | {params['active_subscribers']} | `gold_subscription_behaviour.parquet` |",
    f"| Subscription AOV | SGD {params['sub_aov']:.2f} | `gold_subscription_behaviour.parquet` |",
    "",
    "## Channel repeat rates (90-day)",
    "",
    "| Channel | Rate | First-order AOV (SGD) |",
    "|---------|------|------------------------|",
  ]
  for ch in FORECAST_CHANNELS:
    lines.append(
      f"| {ch} | {params['repeat_rates'][ch]:.2%} | {params['first_aov'][ch]:.2f} |"
    )

  anchor = hist.loc["2026-03"] if "2026-03" in hist.index else hist.iloc[-1]
  mix = (anchor / anchor.sum() * 100).round(1)
  lines += [
    "",
    "## 2026 acquisition mix anchor (Mar 2026)",
    "",
    "| Channel | Share |",
    "|---------|-------|",
  ]
  for ch in FORECAST_CHANNELS:
    lines.append(f"| {ch} | {mix[ch]:.1f}% |")

  lines += [
    "",
    "## Modeling choices",
    "",
    "- **Volume**: average monthly new customers from Oct 2025 - Mar 2026 held flat through 2026.",
    "- **Mix**: Mar 2026 channel shares held flat (Patrick MoM shift anchor).",
    "- **Repeat**: channel-specific 90d rate spread evenly across months t+1, t+2, t+3.",
    "- **Repeat AOV**: 95% of first-order AOV.",
    "- **Second-Purchase Push**: +4.11pp proportional uplift on repeat revenue layer.",
    "- **Lazada Win-back**: lump-sum in March 2026 (conservative ROI).",
    "",
    "## Future plug-ins",
    "",
    "- Benny: BG/NBD subscription decay curve (replace flat 95% survival).",
    "- Roopa: holiday churn adjustment (11.11, 12.12, BFCM).",
    "- Roopa: quarterly organic vs promo cohort decay.",
  ]

  assumptions_path.write_text("\n".join(lines), encoding="utf-8")


def plot_scenarios(all_scenarios: pd.DataFrame, output_path: Path):
    fig, ax = plt.subplots(figsize=(12, 6))
    months_str = all_scenarios["month"].astype(str)

    for scenario, color in [
        ("Status Quo", "#6366f1"),
        ("Second-Purchase Push", "#22c55e"),
        ("Lazada Win-back", "#f59e0b"),
    ]:
        sub = all_scenarios[all_scenarios["scenario"] == scenario].copy()
        sub = sub.sort_values("month")
        cum = sub["total_revenue"].cumsum()
        ax.plot(
            sub["month"].astype(str),
            cum,
            label=scenario,
            color=color,
            linewidth=2.5,
            marker="o",
            markersize=4,
        )

    sq_total = all_scenarios[all_scenarios["scenario"] == "Status Quo"]["total_revenue"].sum()
    sp_total = all_scenarios[all_scenarios["scenario"] == "Second-Purchase Push"]["total_revenue"].sum()
    delta = sp_total - sq_total
    ax.set_title(
        f"2026 Cumulative Revenue Forecast by Scenario\n"
        f"Second-Purchase Push uplift vs Status Quo: SGD {delta:,.0f}",
        fontsize=13,
        fontweight="bold",
    )
    ax.set_xlabel("Month")
    ax.set_ylabel("Cumulative Revenue (SGD)")
    ax.legend()
    ax.grid(alpha=0.3)
    plt.xticks(rotation=45)
    plt.tight_layout()
    plt.savefig(output_path, dpi=150, bbox_inches="tight")
    plt.close()


def plot_delta(all_scenarios: pd.DataFrame, output_path: Path):
    annual = (
        all_scenarios.groupby("scenario")
        .agg(
            new_acq=("new_acq_revenue", "sum"),
            repeat=("repeat_revenue", "sum"),
            subscription=("subscription_revenue", "sum"),
            winback=("winback_revenue", "sum"),
            total=("total_revenue", "sum"),
        )
        .loc[["Status Quo", "Second-Purchase Push", "Lazada Win-back"]]
    )

    fig, axes = plt.subplots(1, 2, figsize=(14, 5))

    # Stacked components for Status Quo
    sq = annual.loc["Status Quo"]
    components = ["new_acq", "repeat", "subscription", "winback"]
    labels = ["New Acquisition", "Repeat", "Subscription", "Win-back"]
    colors = ["#6366f1", "#22c55e", "#a855f7", "#f59e0b"]
    axes[0].bar(labels, [sq[c] for c in components], color=colors)
    axes[0].set_title(f"Status Quo 2026 Revenue Breakdown\nTotal: SGD {sq['total']:,.0f}")
    axes[0].set_ylabel("SGD")
    for i, v in enumerate([sq[c] for c in components]):
        axes[0].text(i, v, f"{v:,.0f}", ha="center", va="bottom", fontsize=9)

    # Scenario totals comparison
    scenarios = annual.index.tolist()
    totals = annual["total"].values
    bar_colors = ["#6366f1", "#22c55e", "#f59e0b"]
    axes[1].bar(scenarios, totals, color=bar_colors)
    axes[1].set_title("Annual 2026 Revenue by Scenario")
    axes[1].set_ylabel("SGD")
    for i, v in enumerate(totals):
        axes[1].text(i, v, f"{v:,.0f}", ha="center", va="bottom", fontsize=9)

    plt.tight_layout()
    plt.savefig(output_path, dpi=150, bbox_inches="tight")
    plt.close()

    return annual


def run_validation(
    all_scenarios: pd.DataFrame,
    params: dict,
    projected: pd.DataFrame | None = None,
    hist: pd.DataFrame | None = None,
    cohorts_ch: pd.DataFrame | None = None,
) -> list[str]:
    checks = []
    sq = all_scenarios[all_scenarios["scenario"] == "Status Quo"]

    # Component reconciliation
    diff = (
        sq["total_revenue"]
        - sq["new_acq_revenue"]
        - sq["repeat_revenue"]
        - sq["subscription_revenue"]
        - sq["winback_revenue"]
    ).abs().max()
    checks.append(f"Component reconciliation max diff: SGD {diff:.2f} {'PASS' if diff < 1 else 'FAIL'}")

    # Blended repeat rate vs DS1 baseline
    if projected is not None:
        mix = projected.groupby("fc_channel")["new_customers"].sum()
        mix = mix / mix.sum()
        blended = sum(mix.get(ch, 0) * params["repeat_rates"][ch] for ch in FORECAST_CHANNELS)
        gap_pp = abs(blended - OVERALL_REPEAT_RATE) * 100
        status = "PASS" if gap_pp <= 1.0 else ("WARN (Shopee mix)" if gap_pp <= 2.0 else "FAIL")
        checks.append(
            f"Blended repeat rate {blended:.2%} vs DS1 {OVERALL_REPEAT_RATE:.2%} "
            f"(gap {gap_pp:.2f}pp): {status}"
        )

    # 2026 projected mix vs Mar 2026 anchor
    if hist is not None and projected is not None:
        anchor = hist.loc["2026-03"] if "2026-03" in hist.index else hist.iloc[-1]
        anchor_mix = anchor / anchor.sum()
        proj_mix = projected.groupby("fc_channel")["new_customers"].sum()
        proj_mix = proj_mix / proj_mix.sum()
        max_diff_pp = (anchor_mix - proj_mix).abs().max() * 100
        checks.append(
            f"2026 mix vs Mar 2026 anchor max diff: {max_diff_pp:.2f}pp "
            f"{'PASS' if max_diff_pp < 0.1 else 'FAIL'}"
        )

    # Retention cohort decay sanity (period 0 vs period 1)
    if cohorts_ch is not None:
        recent = cohorts_ch[cohorts_ch["cohort_quarter"] >= "2024Q1"]
        decay = (
            recent[recent["period_number"].isin([0, 1])]
            .pivot_table(
                index="acquisition_channel",
                columns="period_number",
                values="retention_rate",
                aggfunc="mean",
            )
        )
        if 0 in decay.columns and 1 in decay.columns:
            valid = decay[[0, 1]].dropna(how="any")
            decay_ok = (valid[1] <= valid[0]).all() if len(valid) > 0 else True
            checks.append(f"Cohort period-1 retention <= period-0: {'PASS' if decay_ok else 'FAIL'}")

    # Scenario ordering
    totals = all_scenarios.groupby("scenario")["total_revenue"].sum()
    sp_ok = totals["Second-Purchase Push"] >= totals["Status Quo"]
    lw_ok = totals["Lazada Win-back"] >= totals["Status Quo"]
    checks.append(f"Second-Purchase Push >= Status Quo: {'PASS' if sp_ok else 'FAIL'}")
    checks.append(f"Lazada Win-back >= Status Quo: {'PASS' if lw_ok else 'FAIL'}")

    return checks


def main():
    forecast_params = load_forecast_params()
    cp, co, subs, cohorts_ch = load_data()
    base = prepare_customer_base(cp, co)
    params = extract_parameters(base, subs)
    hist = historical_monthly_acquisitions(base)
    projected = project_2026_monthly(hist)

    months_2026 = pd.period_range("2026-01", "2026-12", freq="M")
    new_acq = build_new_acq_revenue(projected, params["first_aov"])
    repeat_rev = build_repeat_revenue(
        projected,
        params["repeat_rates"],
        params["first_aov"],
        repeat_month_multipliers=forecast_params.get("repeat_month_multipliers"),
    )
    params["promo_gap_pp"] = float(forecast_params["promo_gap_pp"])
    params["promo_lost_revenue_per_cohort"] = float(forecast_params["promo_lost_revenue_per_cohort"])
    params["lazada_winback_conservative"] = float(forecast_params["lazada_winback_conservative"])
    params["lazada_winback_upside"] = float(forecast_params["lazada_winback_upside"])
    params["sub_monthly_survival"] = float(forecast_params["sub_monthly_survival"])

    sub_rev = build_subscription_revenue(params, months_2026)

    # Scenarios
    status_quo = assemble_scenario(months_2026, new_acq, repeat_rev, sub_rev, "Status Quo")

    # +4.11pp uplift on repeat layer => multiplier = (baseline + gap) / baseline on repeat only
    baseline_repeat_annual = status_quo["repeat_revenue"].sum()
    uplift_multiplier = 1 + (params["promo_gap_pp"] / OVERALL_REPEAT_RATE)
    second_purchase = assemble_scenario(
        months_2026, new_acq, repeat_rev, sub_rev,
        "Second-Purchase Push", repeat_multiplier=uplift_multiplier,
    )

    lazada_winback = assemble_scenario(
        months_2026, new_acq, repeat_rev, sub_rev,
        "Lazada Win-back",
        winback_lump=params["lazada_winback_conservative"],
        winback_month="2026-03",
    )

    all_scenarios = pd.concat([status_quo, second_purchase, lazada_winback], ignore_index=True)

    # Export CSV
    csv_path = OUTPUT_DIR / "forecast_2026_monthly.csv"
    all_scenarios.to_csv(csv_path, index=False)

    baseline_path = OUTPUT_DIR / "forecast_2026_monthly_baseline.csv"
    status_quo.to_csv(baseline_path, index=False)

    # Assumptions
    write_assumptions_md(params, hist, OUTPUT_DIR / "forecast_assumptions.md")

    # Charts
    plot_scenarios(all_scenarios, OUTPUT_DIR / "forecast_2026_scenarios.png")
    annual = plot_delta(all_scenarios, OUTPUT_DIR / "forecast_2026_delta.png")

    # Validation
    checks = run_validation(all_scenarios, params, projected, hist, cohorts_ch)

    # Summary
    totals = all_scenarios.groupby("scenario")["total_revenue"].sum()
    print("=== 2026 Revenue Forecast Summary ===")
    for s, t in totals.items():
        print(f"  {s}: SGD {t:,.0f}")
    print(f"\n  Second-Purchase Push uplift: SGD {totals['Second-Purchase Push'] - totals['Status Quo']:,.0f}")
    print("\n=== Validation ===")
    for c in checks:
        print(f"  {c}")
    print(f"\nOutputs written to {OUTPUT_DIR}")

    return all_scenarios, params, annual, checks


if __name__ == "__main__":
    main()
