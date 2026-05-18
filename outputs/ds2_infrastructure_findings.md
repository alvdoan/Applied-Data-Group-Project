# DS2 Infrastructure Findings

## Research Question

What is the data completeness rate across Shopify and Recharge after silver cleaning, and which customer segments have incomplete identity resolution that limits analysis reliability?

## Data Sources Used

- Shopify yearly order exports from `1.customer_transaction/`
- Shopify product master from `2.product_master/`
- Shopify discount export from `3.Discounts/`
- Shopify sessions-by-referrer export from `4.Campaigns/`
- Recharge order, item, reactivation, and churn exports from `5.Recharge_data/`

Saved silver parquet files were not available; DS2 silver-style views were rebuilt in memory.

- No complete saved medallion/silver layer was available; rebuilt DS2 silver-style views in memory from source files without writing them.

## Silver Tables / Logical Views Inspected

- silver_orders
- silver_shopify_order_rows
- silver_products
- silver_discounts
- silver_sessions
- silver_recharge_orders
- silver_recharge_order_items
- silver_recharge_recurring
- silver_recharge_reactivated
- silver_recharge_churned

## Definitions

- Field completeness rate: non-null, non-empty values divided by total rows.
- Overall table completeness rate: average completeness across the key fields checked for that table.
- Identity resolution rate: Recharge customer identities matched to Shopify through `shopify_order_id -> Shopify ID -> Customer: ID`, divided by total Recharge customer identities.
- Unresolved identity: a customer or record that cannot be confidently linked across systems because key identifiers are missing, inconsistent, duplicated, or conflicting.
- Reliability rating: High when identity resolution rate is >= 95%, Medium when >= 80% and < 95%, Low when < 80%. Rows marked Unavailable are segments where required columns are absent.

## Key Numbers

- Shopify order-level completeness (`silver_shopify_order_rows`): 98.5% across 28,054 order rows.
- Shopify customer ID completeness: 99.5%.
- Recharge order completeness (`silver_recharge_orders`): 100.0% across 1,215 Recharge orders.
- Recharge customer ID completeness: 100.0%.
- Matched Shopify-Recharge customer identities: 552.
- Recharge-only unresolved identities: 23.
- Recharge-side identity resolution rate: 96.0%.
- Unresolved identity rate: 4.0%.
- Distinct bridge rows: 1,176.

## So What?

The core Shopify and Recharge operational IDs are mostly complete after silver-style cleaning, so order-level infrastructure is strong enough for customer insight work. The main reliability limitation is cross-platform identity resolution: Recharge and Shopify use different customer ID namespaces, and the available checked-in data does not include actual email or phone identifiers to support secondary matching. Therefore, subscription analyses should rely on the order-level `shopify_order_id` bridge and clearly flag Recharge customers that do not bridge back to Shopify.

## Segments Where Identity Resolution Limits Reliability

- Shopify-only customers: Low; Usable for Shopify-only order analysis, but not reliable for cross-platform subscription analysis.
- Recharge-only customers: Low; Recharge records lack a resolved Shopify customer identity in the available export window.
- Guest checkout records: Low; No persistent Shopify customer ID, so customer-level retention and subscription linkage are unreliable.
- Customers with missing email: Unavailable; Unavailable: email completeness cannot be computed from marketing-status columns.
- Customers with missing phone: Unavailable; Unavailable: phone completeness cannot be computed from SMS marketing-status columns.
- Customers with duplicate email: Unavailable; Unavailable: no email identifier is present.
- Customers with duplicate phone: Unavailable; Unavailable: no phone identifier is present.
- One-time purchasers: Low; Mostly Shopify-only; cross-platform subscription behavior is under-observed unless bridged.
- Repeat purchasers: Low; Strong for Shopify retention, but subscription conclusions depend on bridge coverage.

## Caveats

- No actual customer email or phone columns are present in the checked-in Shopify or Recharge files. Email/SMS marketing-status columns are not identity keys.
- Duplicate email and duplicate phone metrics are unavailable.
- The current checkout contains `medallion/bronze/`, but not saved `medallion/silver/` or `medallion/gold/` outputs.
- Recharge data extends to 2026-04-07, while the Shopify order export appears to stop at 2026-03-31. Some unmatched Recharge records may come from this export-window gap.
- Campaign/session data has no customer ID, order ID, or date key, so it cannot support identity resolution or conversion-rate analysis.
- Active subscriber status is approximated as Recharge customers absent from the churned-subscriptions file.
