# 2026 Revenue Scenarios by Acquisition Method

Three scenarios (Status Quo, Second-Purchase Push, Lazada Win-back) with v2 repeat/subscription layers.

| Acquisition method | Status Quo | Second-Purchase Push | Lazada Win-back | SP uplift | LZ uplift |
|--------------------|------------|----------------------|-----------------|-----------|-----------|
| regression | SGD 644,610 | SGD 649,638 | SGD 678,048 | +5,028 | +33,438 |
| holt_winters | SGD 602,893 | SGD 607,906 | SGD 633,630 | +5,013 | +30,737 |

## Outputs

- `forecast_2026_scenarios_regression.png` — monthly revenue, 3 scenarios
- `forecast_2026_scenarios_holt_winters.png` — monthly revenue, 3 scenarios
- Matching delta and stacked charts per method