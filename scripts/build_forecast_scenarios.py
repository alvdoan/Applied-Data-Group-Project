"""
Build 2026 revenue scenarios with regression or Holt-Winters acquisition.

Writes separate scenario charts per acquisition method:
  outputs/forecast_2026_scenarios_regression.png
  outputs/forecast_2026_scenarios_holt_winters.png

Run from project root:
  python scripts/build_forecast_scenarios.py
"""
from __future__ import annotations

from build_forecast_2026 import OUTPUT_DIR, run_acquisition_scenario_forecasts


def main():
    results = run_acquisition_scenario_forecasts(methods=("regression", "holt_winters"))
    print(f"\nWrote scenario charts to {OUTPUT_DIR}")
    print("  forecast_2026_scenarios_regression.png")
    print("  forecast_2026_scenarios_holt_winters.png")
    print("  forecast_scenarios_by_acquisition.md")
    return results


if __name__ == "__main__":
    main()
