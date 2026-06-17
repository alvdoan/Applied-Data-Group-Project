# Acquisition forecast method comparison

Train through **2025-12**. Backtest **2026-01**–**2026-03**.

## Backtest (new customers)

| Method | MAPE | MAE (customers) | Bias |
|--------|------|-----------------|------|
| regression | 9.4% | 29 | -23 |
| ewma_yoy | 45.6% | 131 | -131 |
| holt_winters | 62.0% | 178 | -178 |
| v2_seasonal | 85.2% | 252 | -252 |

## Methods

- **ewma_yoy**: same calendar month last year × EWMA growth (recent vs lag-12 window).
- **holt_winters**: additive Holt-Winters (`seasonal_periods=12`), AIC-selected trend/season spec.
- **regression**: OLS `customers ~ t + C(cal_month) + promo` (promo = Mar/Nov/Dec).
- **v2_seasonal**: existing `project_2026_monthly_seasonal` baseline.

EWMA α=0.35, growth window=6 months.