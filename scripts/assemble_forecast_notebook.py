"""One-off assembler: build self-contained 09_revenue_forecast_2026.ipynb."""
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NB_PATH = ROOT / "09_revenue_forecast_2026.ipynb"


def read_py(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def adapt(code: str) -> str:
    code = re.sub(r'^""".*?"""\s*\n', "", code, count=1, flags=re.DOTALL)
    code = code.replace("from __future__ import annotations\n\n", "")
    code = code.replace("Path(__file__).resolve().parent.parent", "BASE")
    code = re.sub(r"\nif __name__ == [\"']__main__[\"']:\s*\n.*", "", code, flags=re.DOTALL)
    code = code.replace(
        "    from acquisition_forecast import load_acq_params, project_acquisition_channels\n\n",
        "",
    )
    return code.strip() + "\n"


def cell_md(text: str) -> dict:
    return {"cell_type": "markdown", "metadata": {}, "source": [text if text.endswith("\n") else text + "\n"]}


def cell_code(text: str) -> dict:
    return {
        "cell_type": "code",
        "metadata": {},
        "source": [text if text.endswith("\n") else text + "\n"],
        "outputs": [],
        "execution_count": None,
    }


def main():
    bf = read_py("scripts/build_forecast_2026.py")
    acq = read_py("scripts/acquisition_forecast.py")
    ds6 = read_py("scripts/extract_ds6_metrics.py")
    roopa = read_py("scripts/extract_ds6_roopa_metrics.py")
    benny = read_py("scripts/extract_benny_clv_decay_metrics.py")
    backtest = read_py("scripts/forecast_backtest.py")

    # Split build_forecast_2026 at section markers
    bf = adapt(bf)
    bf = bf.replace('BASE = BASE\n', 'BASE = Path(".").resolve()\n', 1)
    if "BASE = Path" not in bf.split("GOLD_DIR")[0]:
        bf = bf.replace(
            "import pandas as pd\n\nBASE = BASE",
            'import pandas as pd\n\nBASE = Path(".").resolve()',
            1,
        )

    # Extractors: rename main -> extract_* and use BASE
    ds6 = adapt(ds6).replace("def main()", "def extract_ds6_metrics()")
    ds6 = ds6.replace("def resolve_base_dir() -> Path:\n    return BASE\n\n\n", "")
    ds6 = ds6.replace("base = resolve_base_dir()", "base = BASE")

    roopa = adapt(roopa).replace("BASE = BASE", "BASE = Path(\".\").resolve()")
    roopa = roopa.replace("def main()", "def extract_ds6_roopa_metrics()")

    benny = adapt(benny).replace("def main()", "def extract_benny_clv_metrics()")
    benny = benny.replace("def resolve_base_dir() -> Path:\n    return BASE\n\n\n", "")
    benny = benny.replace("base = resolve_base_dir()", "base = BASE")

    acq = adapt(acq)
    acq = acq.replace(
        'cfg_path = BASE / "configs" / "forecast_2026_params.json"',
        'cfg_path = BASE / "configs" / "forecast_2026_params.json"',
    )

    backtest = adapt(backtest)
    backtest = backtest.replace("from build_forecast_2026 import (\n    OUTPUT_DIR,\n    build_forecast,\n    historical_monthly_total_revenue,\n    load_data,\n    prepare_customer_base,\n)\n\n", "")

    # build_forecast: constants + engine functions (BASE set in Family A)
    engine_start = bf.find("def _load_json_if_exists")
    engine_code = bf[engine_start:]
    core_split = engine_code.split("# ---------------------------------------------------------------------------\n# Core forecast builder")
    scenario_code = core_split[1] if len(core_split) > 1 else ""
    scenario_code = re.sub(
        r"^.*?(?=def build_forecast)",
        "",
        scenario_code,
        count=1,
        flags=re.DOTALL,
    )
    scenario_code = scenario_code.split("def main():")[0].strip()

    intro = """# 2026 Revenue Forecast (v2 + v3)

**Objective:** Monthly Jan–Dec 2026 revenue with three scenarios (Status Quo, Second-Purchase Push, Lazada Win-back).

**Data:** Gold parquets + teammate JSON (Roopa DS6, Benny BG/NBD, DS1/DS3 parameters).

**Models:**
- **v2** — seasonal acquisition baseline (repeat cohort + BG/NBD subscription)
- **v3** — regression acquisition (**recommended**) + Holt-Winters comparison

**Prerequisites:** `pandas`, `matplotlib`, `numpy`, `statsmodels`, `lifetimes` (Benny extractor).

Run cells top-to-bottom. Code families are grouped below; logic mirrors `scripts/build_forecast_2026.py`.
"""

    cells = [
        cell_md(intro),
        cell_md("## Family A — Setup & configuration\n\nPaths, imports, constants, and gold table loaders. Defines channel mapping and default forecast parameters merged with `configs/forecast_2026_params.json`."),
        cell_code(
            """import json
import os
import warnings
from datetime import datetime, timezone
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import statsmodels.formula.api as smf
from statsmodels.tsa.holtwinters import ExponentialSmoothing

warnings.filterwarnings("ignore", category=FutureWarning)
warnings.filterwarnings("ignore", category=UserWarning)

BASE = Path(".").resolve()
GOLD_DIR = BASE / "medallion" / "gold"
OUTPUT_DIR = BASE / "outputs"
OUTPUT_DIR.mkdir(exist_ok=True)

FORECAST_VERSION = os.environ.get("FORECAST_VERSION", "v2")
PROMO_MONTHS = {3, 11, 12}

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
    "DTC": "DTC", "Lazada": "Lazada", "Shopee": "Shopee",
    "Marketplace": "Other", "Other Marketplace": "Other", "Draft Order": "Other",
    "Bulk Import": "Other", "POS": "Other", "Email": "DTC", "TikTok": "Other",
    "Shop App": "Other", "Affiliate": "Other",
}
FORECAST_CHANNELS = ["DTC", "Lazada", "Shopee", "Other"]
DOCUMENTED_REPEAT = {"DTC": 0.2170, "Lazada": 0.2893, "Shopee": 0.1832, "Other": 0.1700}

print("Project root:", BASE)
print("Gold dir exists:", GOLD_DIR.exists())
"""
        ),
        cell_md("## Family B — Teammate metric extractors\n\nReplicates Roopa DS6 and Benny CLV notebook logic without rerunning their `.ipynb` files. Writes JSON snapshots consumed by the forecast engine.\n\nSet `RUN_EXTRACTORS = False` to skip if JSON already exists in `outputs/`."),
        cell_code(
            f"RUN_EXTRACTORS = True\n\n"
            + ds6
            + "\n\n"
            + roopa.replace("BASE = Path(\".\").resolve()\n", "")
            + "\n\n"
            + benny
            + """

if RUN_EXTRACTORS:
    extract_ds6_metrics()
    extract_ds6_roopa_metrics()
    extract_benny_clv_metrics()
    print("Extractor outputs refreshed in", OUTPUT_DIR)
else:
    print("Skipping extractors — using existing JSON in", OUTPUT_DIR)
"""
        ),
        cell_md("## Family C — Parameter extraction\n\nMerges config + JSON into `params`: repeat rates, AOVs, Lazada win-back, promo rates, subscription base."),
        cell_code(engine_code.split("# ---------------------------------------------------------------------------\n# v1 acquisition")[0].strip()),
        cell_md("## Family D — Revenue layer engine\n\n`Monthly revenue = New acquisition + Repeat + Subscription (+ Win-back)`.\n\n- **D1** Acquisition projections (v1 flat, v2 seasonal)\n- **D2** Repeat cohort engine (Roopa promo split + holiday multipliers)\n- **D3** Subscription (Benny BG/NBD curve) and scenario assembly"),
        cell_code(
            "# D1–D3: acquisition, repeat, subscription, assembly\n"
            + engine_code.split("# ---------------------------------------------------------------------------\n# v1 acquisition")[1]
            .split("# ---------------------------------------------------------------------------\n# Core forecast builder")[0]
            .strip()
        ),
        cell_md("## Family E — Acquisition forecasting (v3)\n\nRegression (OLS: time + month + promo dummies) and Holt-Winters. Regression won Q1 2026 backtest on new customers (~9% MAPE)."),
        cell_code(acq.replace("DEFAULT_ACQ_PARAMS", "DEFAULT_ACQ_PARAMS_NB").replace("def load_acq_params", "def load_acq_params")),
        cell_md("## Family F — Scenario builders, charts & validation\n\nAssembles three scenarios on top of v2 or v3 acquisition. Includes plotting and validation helpers."),
        cell_code(
            "# Scenario builders, charts, validation\n" + scenario_code
        ),
        cell_md("## Family G — Revenue backtest (v1 vs v2)\n\nTrains through Dec 2025; evaluates Q1 2026 actual revenue vs Status Quo forecast."),
        cell_code(backtest.strip()),
        cell_md("## Section 10 — v2 baseline (optional)\n\nSeasonal acquisition model kept for comparison."),
        cell_code(
            """cp, co, subs, cohorts_ch = load_data()
base = prepare_customer_base(cp, co)

all_scenarios_v2, params_v2, hist_v2, projected_v2 = build_forecast(
    version="v2",
    cp=cp, co=co, subs=subs, cohorts_ch=cohorts_ch, base=base,
    write_outputs=True,
)
totals_v2 = all_scenarios_v2.groupby("scenario")["total_revenue"].sum()
print("=== v2 annual totals ===")
print(totals_v2.to_string())
"""
        ),
        cell_md("## Section 11 — Acquisition method comparison\n\nCompare regression, Holt-Winters, and v2 seasonal on **new customers** (Q1 2026)."),
        cell_code(
            """acq_params = load_acq_params()
end_train_p = pd.Period(acq_params["end_train"], freq="M")
forecast_months = pd.period_range("2026-01", "2026-12", freq="M")
backtest_months = pd.period_range(acq_params["backtest_start"], acq_params["backtest_end"], freq="M")

hist_train = historical_monthly_acquisitions(base, start=acq_params["hist_start"], end=acq_params["end_train"])
hist_full = historical_monthly_acquisitions(base, start=acq_params["hist_start"])
total_train = total_series(hist_train)
actual_bt = total_series(hist_full).reindex(backtest_months)

methods = {
    "regression": forecast_regression(total_train, forecast_months, end_train_p)[0],
    "holt_winters": forecast_holt_winters(total_train, forecast_months, end_train_p),
    "v2_seasonal": project_2026_monthly_seasonal(hist_train, load_forecast_params()).groupby("month")["new_customers"].sum(),
}

rows = []
for name, fc in methods.items():
    aligned = pd.concat([actual_bt.rename("actual"), fc.reindex(backtest_months).rename("forecast")], axis=1).dropna()
    mape = ((aligned["forecast"] - aligned["actual"]).abs() / aligned["actual"]).mean() * 100
    rows.append({"method": name, "Q1_MAPE_pct": round(mape, 1)})
pd.DataFrame(rows).sort_values("Q1_MAPE_pct")
"""
        ),
        cell_md("## Section 12 — v3 scenarios (main result)\n\n**Regression** (recommended) and **Holt-Winters** acquisition with three revenue scenarios each."),
        cell_code(
            """v3_results = run_acquisition_scenario_forecasts(methods=("regression", "holt_winters"))

summary_path = OUTPUT_DIR / "forecast_scenarios_by_acquisition.md"
if summary_path.exists():
    print(summary_path.read_text(encoding="utf-8"))
"""
        ),
        cell_md("## Section 13 — Charts, assumptions & slide summary"),
        cell_code(
            """from IPython.display import Image, display

for name in [
    "forecast_2026_scenarios_regression.png",
    "forecast_2026_scenarios_holt_winters.png",
    "forecast_2026_delta_regression.png",
    "acquisition_forecast_comparison.png",
]:
    p = OUTPUT_DIR / name
    if p.exists():
        print(name)
        display(Image(filename=str(p)))

assumptions_path = OUTPUT_DIR / "forecast_assumptions.md"
if assumptions_path.exists():
    print("\\n--- Assumptions (excerpt) ---")
    print(assumptions_path.read_text(encoding="utf-8")[:900])

reg = v3_results["regression"]["totals"]
sq, sp, lz = reg["Status Quo"], reg["Second-Purchase Push"], reg["Lazada Win-back"]
print(f"\\n=== Slide bullets (regression acquisition) ===")
print(f"Status Quo 2026: SGD {sq:,.0f}")
print(f"Second-Purchase Push: SGD {sp:,.0f} (+SGD {sp - sq:,.0f})")
print(f"Lazada Win-back: SGD {lz:,.0f} (+SGD {lz - sq:,.0f})")

backtest_df = run_backtest()
print("\\n=== Revenue backtest (v1 vs v2 Status Quo) ===")
print(backtest_df[backtest_df["month"] == "SUMMARY"].to_string(index=False))
"""
        ),
    ]

    nb = {
        "nbformat": 4,
        "nbformat_minor": 5,
        "metadata": {
            "kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
            "language_info": {"name": "python", "pygments_lexer": "ipython3"},
        },
        "cells": cells,
    }
    NB_PATH.write_text(json.dumps(nb, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Wrote {NB_PATH} ({len(cells)} cells)")


if __name__ == "__main__":
    main()
