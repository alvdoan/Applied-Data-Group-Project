# README_2 - DS2 Infrastructure Outputs

## What Was Added

I created `scripts/generate_ds2_infrastructure_outputs.py` to answer the DS2 Data Architect infrastructure research question:

> What is the data completeness rate across Shopify and Recharge after silver cleaning, and which customer segments have incomplete identity resolution that limits analysis reliability?

The script does not run the old notebooks. It first looks for saved silver parquet files in `medallion/silver/`. Because the current checkout only has `medallion/bronze/`, it rebuilds the minimum needed silver-style cleaned views in memory from the source files, then writes final DS2 outputs to `outputs/`.

## How To Run

From the project root:

```powershell
conda run -n mlops_project python scripts/generate_ds2_infrastructure_outputs.py
```

The script creates or overwrites:

- `outputs/completeness_summary.csv`
- `outputs/identity_resolution_summary.csv`
- `outputs/segment_reliability_summary.csv`
- `outputs/ds2_infrastructure_findings.md`

## Main Outputs Created

### 1. Completeness Summary

`outputs/completeness_summary.csv` reports completeness for the relevant silver/logical tables.

Main numbers:

| Source | Table | Rows | Overall completeness |
|---|---:|---:|---:|
| Shopify | `silver_shopify_order_rows` | 28,054 | 98.45% |
| Shopify | `silver_orders` | 102,461 | 90.16% |
| Recharge | `silver_recharge_orders` | 1,215 | 100.00% |
| Recharge | `silver_recharge_order_items` | 1,094 | 97.23% |
| Recharge | `silver_recharge_recurring` | 650 | 98.13% |
| Recharge | `silver_recharge_churned` | 526 | 97.40% |

Important limitation: actual customer email and phone columns are not present in the checked-in Shopify or Recharge files, so email/phone completeness is marked unavailable.

### 2. Identity Resolution Summary

`outputs/identity_resolution_summary.csv` describes Shopify-Recharge identity matching.

Main numbers:

| Metric | Value |
|---|---:|
| Total unified customer identities | 13,908 |
| Shopify-only customers | 13,333 |
| Recharge-only unresolved customers | 23 |
| Matched Shopify-Recharge customers | 552 |
| Identity resolution rate | 96.00% |
| Unresolved identity rate | 4.00% |
| Direct Shopify/ReCharge customer ID overlap | 0 |

Identity matching uses:

```text
Recharge shopify_order_id -> Shopify ID -> Shopify Customer: ID
```

It does not use direct equality between Shopify `Customer: ID` and Recharge `customer_id`, because those IDs are different namespaces.

### 3. Segment Reliability Summary

`outputs/segment_reliability_summary.csv` flags which segments are reliable or limited for cross-platform analysis.

Key segment findings:

| Segment | Count | Identity resolution | Reliability |
|---|---:|---:|---|
| Matched Shopify-Recharge customers | 552 | 100.00% | High |
| Active subscribers | 255 | 91.37% | Medium |
| Cancelled subscribers | 320 | 99.69% | High |
| Recharge-only customers | 23 | 0.00% | Low |
| Guest checkout records | 153 | 0.00% | Low |
| Shopify-only customers | 13,333 | 0.00% | Low for cross-platform subscription analysis |
| One-time purchasers | 9,350 | 1.67% | Low for cross-platform subscription analysis |
| Repeat purchasers | 4,535 | 8.73% | Low for cross-platform subscription analysis |

Email/phone missing and duplicate segments are marked unavailable because actual email and phone identifiers are absent.

## Answer To The Research Question

After silver-style cleaning, the core order-level data is mostly complete:

- Shopify order-level completeness is 98.45%.
- Recharge order-level completeness is 100.00%.
- Recharge item-level and churn tables are also high completeness, around 97-98%.

The main infrastructure limitation is not basic order completeness. It is cross-platform customer identity resolution.

Recharge and Shopify customer IDs do not directly match: direct ID overlap is 0. The reliable bridge is through Recharge `shopify_order_id` back to Shopify `ID`, then to Shopify `Customer: ID`. Using that bridge, 552 of 575 Recharge customers resolve to Shopify customers, giving a Recharge-side identity resolution rate of 96.00%. The remaining 23 Recharge customers are unresolved, likely because the Recharge export extends to 2026-04-07 while the Shopify export appears to stop at 2026-03-31.

Segments with lower reliability:

- Recharge-only customers: cannot be linked to Shopify customer history.
- Guest checkout records: no persistent Shopify customer ID.
- Shopify-only customers: usable for Shopify-only analysis, but not reliable for subscription analysis.
- One-time and repeat purchaser segments: strong for Shopify retention, weak for cross-platform subscription conclusions unless bridged.
- Active subscribers: medium reliability because 22 of 255 active Recharge customers do not resolve to Shopify.
- Email/phone-based segments: unavailable because actual email and phone identifiers are not included.

Slide-ready interpretation:

> The cleaned Shopify and Recharge datasets are strong at the order level, but cross-platform customer analysis depends on the Shopify order ID bridge. Recharge identity resolution is high at 96%, but unresolved Recharge-only customers, guest checkout records, and missing email/phone identifiers should be flagged as lower reliability for customer-level and subscription analysis.

---

## ## DS1 - Repeat Purchase Prediction (Sections 1–6) Outputs

In addition to the infrastructure metrics, we have fully implemented **`05_ds1_repeat_purchase_prediction.ipynb`** in the workspace root. This notebook builds the customer-level modeling features, performs exploratory data analysis, trains and compares machine learning models, and establishes key project findings.

### How To Run

You can execute the entire predictive pipeline in either of two ways:

1. **Jupyter Notebook Interface (Recommended)**:
   * Open **`05_ds1_repeat_purchase_prediction.ipynb`** in your Jupyter Notebook or Jupyter Lab editor.
   * Click **"Run All"**.
   * *Self-Healing Feature*: The notebook has a built-in pre-run cell that checks if you have the prerequisite files in `medallion/silver/` and `medallion/gold/`. If they are missing, it automatically compiles and runs the Medallion sequence locally to populate the databases!

2. **Programmatic Pipeline Script (Execute Everything with 1 Command)**:
   * Run the sequencing script directly from PowerShell using your Conda Python environment:
     ```powershell
     D:\Programs\Conda\Conda\python.exe scripts/run_notebooks.py
     ```
   * **What it does**: This single command executes all data cleaning, customer ID bridging, gold layer aggregations, product & discount feature engineering, exploratory data analysis, chart rendering, and machine learning training (Logistic Regression + Random Forest).
   * **Outputs**: All Parquet files, exploratory plots, model confusion matrices, and the feature importance chart are generated and saved to `outputs/` automatically. No additional notebook steps are required!

### Outputs Created

Running the notebook automatically generates the following deliverables inside your **`outputs/`** directory:

1. **Analytical Data Table**:
   * `outputs/05_features_repeat_purchase.parquet` - The complete, cleaned, B2B-filtered customer features table ($13,908$ retail customer rows).

2. **Exploratory Visualizations (Section 4)**:
   * `outputs/chart_4_1_channel_repeat.png` - 90-Day Repeat Rate by Customer Acquisition Channel (Bar chart with overall baseline line).
   * `outputs/chart_4_2_category_repeat.png` - 90-Day Repeat Rate by First Product Category ($N \ge 30$).
   * `outputs/chart_4_3_discount_tier_repeat.png` - 90-Day Repeat Rate by first order Discount Tier (ordered with customer count annotations).
   * `outputs/chart_4_4_subscription_repeat.png` - 90-Day Repeat Rate: Subscribers vs Non-Subscribers.
   * `outputs/chart_4_5_spend_density.png` - First Order Spend Distribution by Repeat Outcome (overlapping KDE plot).
   * `outputs/chart_4_6_product_formats_repeat.png` - Side-by-side repeat rate comparison for Bundles and Whey protein formats.

3. **Model Evaluations & Importances (Section 5)**:
   * `outputs/cm_logistic_regression.png` - Heatmap confusion matrix for the Logistic Regression model.
   * `outputs/cm_random_forest.png` - Heatmap confusion matrix for the Random Forest model.
   * `outputs/chart_5_4_feature_importances.png` - Horizontal bar chart showing the **Top 15 Feature Importances** from the champion Random Forest model.


