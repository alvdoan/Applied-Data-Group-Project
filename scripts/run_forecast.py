"""
Run extractors + 2026 forecast pipeline.

From project root:
  python scripts/run_forecast.py
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
PY = sys.executable

STEPS = [
    [PY, str(BASE / "scripts" / "extract_ds6_metrics.py")],
    [PY, str(BASE / "scripts" / "extract_ds6_roopa_metrics.py")],
    [PY, str(BASE / "scripts" / "extract_benny_clv_decay_metrics.py")],
    [PY, str(BASE / "scripts" / "build_forecast_2026.py")],
    [PY, str(BASE / "scripts" / "forecast_backtest.py")],
    [PY, str(BASE / "scripts" / "forecast_acquisition_compare.py")],
]


def main():
    for cmd in STEPS:
        print(f"\n>>> {' '.join(cmd)}")
        subprocess.run(cmd, cwd=str(BASE), check=True)
    print("\nForecast pipeline complete.")


if __name__ == "__main__":
    main()
