# Applied-Data-Group-Project

# DS 6 Analysis: Acquisition Dynamics & Promo Strategy

## ## Answer To The Research Question
Customers acquired through high-magnitude discounts (30%+) show a **statistically lower** 90-day repeat purchase rate compared to full-price customers.

### Key Metrics
- **Full Price Repeat Rate:** 23.89%
- **High-Magnitude (30%+) Repeat Rate:** 19.78%
- **The Loyalty Gap:** 4.11%
- **Estimated Revenue Loss:** SGD 4,742.80 per acquisition cohort

### Data Quality & Counter-measures
- **Discrepancy Found:** The `rfm_group` tags in the Gold layer were identified as 100% null.
- **Counter-measure Applied:** Implemented a **'One-and-Done'** behavioral proxy (customers with only 1 total order) to accurately measure churn risk for the Mid-Term report.
- **Finding:** High-magnitude discount customers are **9.69% more likely** to be 'One-and-Done' (72.76% vs 63.07% baseline).

### Limitations
- **Identity Resolution:** In alignment with DS 2's infrastructure notes, retention tracking for **Guest Checkout** segments remains low-reliability due to a lack of persistent IDs.