"""Generate DS2 infrastructure completeness and identity-resolution outputs.

This script is intentionally independent from the existing notebooks. It uses
saved silver parquet files when they exist. If they do not exist, it rebuilds
only the minimum silver-style cleaned views needed for the DS2 infrastructure
question, in memory, from the checked-in source files.

Outputs:
  outputs/completeness_summary.csv
  outputs/identity_resolution_summary.csv
  outputs/segment_reliability_summary.csv
  outputs/ds2_infrastructure_findings.md
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import pandas as pd


RESEARCH_QUESTION = (
    "What is the data completeness rate across Shopify and Recharge after "
    "silver cleaning, and which customer segments have incomplete identity "
    "resolution that limits analysis reliability?"
)

MISSING_STRINGS = {"", "nan", "none", "null", "<na>"}


SHOPIFY_ORDER_COLUMNS = [
    "ID",
    "Name",
    "Tags",
    "Cancelled At",
    "Cancel: Reason",
    "Processed At",
    "Currency",
    "Source",
    "Price: Total",
    "Price: Subtotal",
    "Price: Total Discount",
    "Price: Total Shipping",
    "Price: Total Refund",
    "Payment: Status",
    "Order Fulfillment Status",
    "Customer: ID",
    "Customer: Tags",
    "Customer: Email Marketing Status",
    "Customer: SMS Marketing Status",
    "Shipping: City",
    "Shipping: Country",
    "Shipping: Country Code",
    "Browser: UTM Source",
    "Browser: UTM Medium",
    "Browser: UTM Campaign",
    "Top Row",
    "Line: Type",
    "Line: ID",
    "Line: Product ID",
    "Line: Title",
    "Line: Name",
    "Line: Variant ID",
    "Line: Variant Title",
    "Line: Variant SKU",
    "Line: Quantity",
    "Line: Price",
    "Line: Discount",
    "Line: Total",
]


@dataclass
class DataContext:
    tables: dict[str, pd.DataFrame]
    used_saved_silver: bool
    silver_load_notes: list[str]


def project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def is_present(series: pd.Series) -> pd.Series:
    """Return True for non-null, non-empty values.

    Metric definition used in the assignment:
    field completeness = non-null, non-empty values / total rows.
    """
    present = series.notna()
    as_text = series.astype("string").str.strip().str.lower()
    present &= ~as_text.isin(MISSING_STRINGS).fillna(True)
    return present


def field_completeness(df: pd.DataFrame, column: str) -> float | pd.NA:
    """Completeness rate for a single field, or NA if the field is absent."""
    if column not in df.columns or len(df) == 0:
        return pd.NA
    return round(float(is_present(df[column]).mean()), 4)


def overall_completeness(df: pd.DataFrame, key_fields: Iterable[str]) -> float | pd.NA:
    """Overall table completeness as the average across available key fields."""
    rates = [
        field_completeness(df, column)
        for column in key_fields
        if column in df.columns
    ]
    numeric_rates = [rate for rate in rates if not pd.isna(rate)]
    if not numeric_rates:
        return pd.NA
    return round(sum(numeric_rates) / len(numeric_rates), 4)


def unique_present_values(df: pd.DataFrame, column: str) -> set[str]:
    if column not in df.columns:
        return set()
    values = df.loc[is_present(df[column]), column].astype(str).str.strip()
    return set(values)


def read_excel_selected(path: Path, columns: list[str] | None = None) -> pd.DataFrame:
    """Read an Excel file, selecting only columns that exist in the file."""
    if columns is None:
        return pd.read_excel(path, dtype=str)
    header = pd.read_excel(path, nrows=0)
    existing = [column for column in columns if column in header.columns]
    return pd.read_excel(path, dtype=str, usecols=existing)


def safe_to_numeric(df: pd.DataFrame, columns: list[str]) -> None:
    for column in columns:
        if column in df.columns:
            df[column] = pd.to_numeric(df[column], errors="coerce")


def safe_to_datetime(df: pd.DataFrame, columns: list[str], utc: bool = True) -> None:
    for column in columns:
        if column in df.columns:
            df[column] = pd.to_datetime(df[column], errors="coerce", utc=utc)


def clean_text_lower(df: pd.DataFrame, columns: list[str]) -> None:
    for column in columns:
        if column in df.columns:
            df[column] = df[column].astype("string").str.strip().str.lower()


def clean_text_upper(df: pd.DataFrame, columns: list[str]) -> None:
    for column in columns:
        if column in df.columns:
            df[column] = df[column].astype("string").str.strip().str.upper()


def load_saved_silver(base: Path) -> DataContext | None:
    """Load saved silver parquet if all required files are available."""
    silver_dir = base / "medallion" / "silver"
    required = {
        "silver_orders": "silver_orders.parquet",
        "silver_products": "silver_products.parquet",
        "silver_discounts": "silver_discounts.parquet",
        "silver_sessions": "silver_sessions.parquet",
        "silver_recharge_orders": "silver_recharge_orders.parquet",
        "silver_recharge_order_items": "silver_recharge_order_items.parquet",
        "silver_recharge_recurring": "silver_recharge_recurring.parquet",
        "silver_recharge_reactivated": "silver_recharge_reactivated.parquet",
        "silver_recharge_churned": "silver_recharge_churned.parquet",
    }
    if not silver_dir.exists():
        return None
    if not all((silver_dir / filename).exists() for filename in required.values()):
        return None

    try:
        tables = {
            name: pd.read_parquet(silver_dir / filename)
            for name, filename in required.items()
        }
    except Exception as exc:  # pragma: no cover - environment dependent
        return DataContext(
            tables={},
            used_saved_silver=False,
            silver_load_notes=[
                f"Saved silver parquet exists but could not be read: {type(exc).__name__}: {exc}",
            ],
        )

    order_rows = build_shopify_order_rows(tables["silver_orders"])
    tables["silver_shopify_order_rows"] = order_rows
    return DataContext(
        tables=tables,
        used_saved_silver=True,
        silver_load_notes=["Loaded existing medallion/silver parquet files."],
    )


def load_shopify_orders_raw(base: Path) -> pd.DataFrame:
    frames = []
    for path in sorted((base / "1.customer_transaction").glob("1_*.xlsx")):
        frame = read_excel_selected(path, SHOPIFY_ORDER_COLUMNS)
        frame["_source_file"] = path.name
        frames.append(frame)
    if not frames:
        raise FileNotFoundError("No Shopify order files found in 1.customer_transaction/")
    return pd.concat(frames, ignore_index=True)


def clean_silver_orders(raw_orders: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Rebuild the subset of silver_orders needed for DS2, in memory.

    The original silver notebook drops duplicate rows, preserves a discount
    lookup separately, and keeps product-relevant line types for silver_orders.
    """
    deduped = raw_orders.drop_duplicates().copy()
    keep_types = ["Line Item", "Fulfillment Line", "Refund Line"]
    silver_orders = deduped[deduped["Line: Type"].isin(keep_types)].copy()

    clean_text_upper(silver_orders, ["Line: Variant SKU"])
    clean_text_lower(
        silver_orders,
        [
            "Payment: Status",
            "Order Fulfillment Status",
            "Source",
            "Currency",
            "Shipping: Country",
            "Shipping: Country Code",
            "Browser: UTM Source",
            "Browser: UTM Medium",
            "Browser: UTM Campaign",
        ],
    )
    safe_to_datetime(silver_orders, ["Processed At", "Cancelled At"])
    safe_to_numeric(
        silver_orders,
        [
            "Line: Quantity",
            "Line: Price",
            "Line: Discount",
            "Line: Total",
            "Price: Total",
            "Price: Subtotal",
            "Price: Total Discount",
            "Price: Total Shipping",
            "Price: Total Refund",
        ],
    )
    return silver_orders, deduped


def build_shopify_order_rows(source_orders: pd.DataFrame) -> pd.DataFrame:
    """Build logical one-row-per-order Shopify view from Top Row markers."""
    if "Top Row" not in source_orders.columns:
        return pd.DataFrame()
    order_rows = source_orders[source_orders["Top Row"].notna()].copy()
    if "ID" in order_rows.columns:
        order_rows = order_rows.drop_duplicates(subset=["ID"], keep="first")
    return order_rows


def clean_silver_products(base: Path) -> pd.DataFrame:
    df = read_excel_selected(base / "2.product_master" / "2_1.products_master_20260505.xlsx")
    df = df.dropna(how="all").copy()
    if "Title" in df.columns:
        df["Title"] = df["Title"].ffill().astype("string").str.strip()
    if "Variant SKU" in df.columns:
        df = df[df["Variant SKU"].notna()].copy()
        clean_text_upper(df, ["Variant SKU"])
        df = df.drop_duplicates(subset=["Variant SKU"], keep="first")
    keep = [
        "Title",
        "Variant SKU",
        "Variant Grams",
        "Variant Price",
        "Variant Barcode",
        "Cost per item",
    ]
    df = df[[column for column in keep if column in df.columns]].copy()
    safe_to_numeric(df, ["Variant Grams", "Variant Price", "Cost per item"])
    return df


def clean_silver_discounts(base: Path) -> pd.DataFrame:
    df = pd.read_csv(base / "3.Discounts" / "3_1.discounts_export_20260505.csv", dtype=str)
    df = df.dropna(how="all").copy()
    keep = ["Name", "Value", "Value Type", "Times Used In Total", "Start", "End"]
    df = df[[column for column in keep if column in df.columns]].copy()
    if "Name" in df.columns:
        df = df.drop_duplicates(subset=["Name"], keep="first")
        clean_text_upper(df, ["Name"])
    safe_to_numeric(df, ["Value", "Times Used In Total"])
    if "Value" in df.columns:
        df["Value"] = df["Value"].abs()
    safe_to_datetime(df, ["Start", "End"])
    return df


def clean_silver_sessions(base: Path) -> pd.DataFrame:
    df = pd.read_csv(base / "4.Campaigns" / "4_1.Sessions by referrer_20260505.csv", dtype=str)
    df = df.dropna(how="all").copy()
    df = df.drop(columns=[column for column in ["Landing page URL", "Landing page path"] if column in df.columns])
    clean_text_lower(
        df,
        [
            "Referrer source",
            "Referrer name",
            "Session city",
            "UTM campaign",
            "UTM medium",
            "UTM source",
        ],
    )
    safe_to_numeric(df, ["Online store visitors", "Sessions"])
    return df


def clean_silver_recharge_orders(base: Path) -> pd.DataFrame:
    df = read_excel_selected(base / "5.Recharge_data" / "5_1.orders_combined_20260505.xlsx")
    df = df.dropna(how="all").copy()
    df = df.drop(columns=[column for column in ["order_tax", "order_shipping"] if column in df.columns])
    safe_to_datetime(df, ["metric_date"])
    clean_text_lower(df, ["order_type"])
    safe_to_numeric(df, ["order_total", "order_gross_revenue", "order_discounts"])
    if "order_discounts" in df.columns:
        df["order_discounts"] = df["order_discounts"].abs()
    return df


def clean_silver_recharge_items(path: Path) -> pd.DataFrame:
    df = read_excel_selected(path)
    df = df.dropna(how="all").copy()
    df = df.drop(columns=[column for column in ["line_item_tax"] if column in df.columns])
    if "product_sku" in df.columns:
        missing_sku = ~is_present(df["product_sku"])
        df.loc[missing_sku, "item_flag"] = "missing_sku"
        clean_text_upper(df, ["product_sku"])
    clean_text_lower(df, ["purchase_type"])
    safe_to_datetime(df, ["metric_date"])
    safe_to_numeric(df, ["order_item_quantity", "line_item_price", "line_item_discount"])
    if "line_item_discount" in df.columns:
        df["line_item_discount"] = df["line_item_discount"].abs()
    return df


def clean_silver_recharge_reactivated(base: Path) -> pd.DataFrame:
    df = read_excel_selected(base / "5.Recharge_data" / "5_3.subscribers_reactivated_20260505.xlsx")
    df = df.dropna(how="all").copy()
    safe_to_datetime(df, ["metric_date", "first_subscription_activation_date", "reactivated_date"])
    return df


def clean_silver_recharge_churned(base: Path) -> pd.DataFrame:
    df = read_excel_selected(base / "5.Recharge_data" / "5_4.subscriptions_churned_20260505.xlsx")
    df = df.dropna(how="all").copy()
    if "product_sku" in df.columns:
        missing_sku = ~is_present(df["product_sku"])
        df.loc[missing_sku, "item_flag"] = "missing_sku"
        clean_text_upper(df, ["product_sku"])
    if "cancellation_reason" in df.columns:
        df["cancellation_reason"] = df["cancellation_reason"].replace("(Unknown)", pd.NA)
    safe_to_datetime(df, ["metric_date", "subscription_activation_date", "subscription_churn_date"])
    return df


def rebuild_silver_like_tables(base: Path) -> DataContext:
    raw_orders = load_shopify_orders_raw(base)
    silver_orders, deduped_orders = clean_silver_orders(raw_orders)
    order_rows = build_shopify_order_rows(deduped_orders)
    if order_rows.empty:
        order_rows = build_shopify_order_rows(silver_orders)

    tables = {
        "silver_orders": silver_orders,
        "silver_shopify_order_rows": order_rows,
        "silver_products": clean_silver_products(base),
        "silver_discounts": clean_silver_discounts(base),
        "silver_sessions": clean_silver_sessions(base),
        "silver_recharge_orders": clean_silver_recharge_orders(base),
        "silver_recharge_order_items": clean_silver_recharge_items(
            base / "5.Recharge_data" / "5_2.order_items_checkout_20260505.xlsx"
        ),
        "silver_recharge_recurring": clean_silver_recharge_items(
            base / "5.Recharge_data" / "5_5.order_items_recurring_20260505.xlsx"
        ),
        "silver_recharge_reactivated": clean_silver_recharge_reactivated(base),
        "silver_recharge_churned": clean_silver_recharge_churned(base),
    }
    return DataContext(
        tables=tables,
        used_saved_silver=False,
        silver_load_notes=[
            "No complete saved medallion/silver layer was available; rebuilt DS2 "
            "silver-style views in memory from source files without writing them.",
        ],
    )


def load_data_context(base: Path) -> DataContext:
    saved = load_saved_silver(base)
    if saved and saved.tables:
        return saved
    rebuilt = rebuild_silver_like_tables(base)
    if saved and saved.silver_load_notes:
        rebuilt.silver_load_notes = saved.silver_load_notes + rebuilt.silver_load_notes
    return rebuilt


def missingness_notes(df: pd.DataFrame, key_fields: list[str], extra: str = "") -> str:
    notes = []
    unavailable = [column for column in key_fields if column not in df.columns]
    if unavailable:
        notes.append(f"Unavailable columns: {', '.join(unavailable)}")
    low_fields = []
    for column in key_fields:
        rate = field_completeness(df, column)
        if not pd.isna(rate) and rate < 0.95:
            low_fields.append(f"{column} {rate:.1%}")
    if low_fields:
        notes.append("Fields below 95% complete: " + "; ".join(low_fields))
    if extra:
        notes.append(extra)
    return " | ".join(notes) if notes else "No major missingness in checked key fields."


def completeness_row(
    source_system: str,
    table_name: str,
    df: pd.DataFrame,
    key_fields: list[str],
    customer_field: str | None = None,
    order_field: str | None = None,
    subscription_field: str | None = None,
    extra_notes: str = "",
) -> dict[str, object]:
    # Email and phone are requested metrics, but the checked-in files do not
    # contain actual email or phone identifiers. Marketing-status fields are not
    # treated as identity keys.
    return {
        "source_system": source_system,
        "table_name": table_name,
        "row_count": len(df),
        "key_fields_checked": ", ".join(key_fields),
        "completeness_rate_overall": overall_completeness(df, key_fields),
        "customer_id_completeness": field_completeness(df, customer_field) if customer_field else pd.NA,
        "email_completeness": pd.NA,
        "phone_completeness": pd.NA,
        "order_id_completeness": field_completeness(df, order_field) if order_field else pd.NA,
        "subscription_id_completeness": (
            field_completeness(df, subscription_field) if subscription_field else pd.NA
        ),
        "missingness_notes": missingness_notes(df, key_fields, extra_notes),
    }


def build_completeness_summary(tables: dict[str, pd.DataFrame]) -> pd.DataFrame:
    rows = [
        completeness_row(
            "Shopify",
            "silver_orders",
            tables["silver_orders"],
            [
                "ID",
                "Customer: ID",
                "Processed At",
                "Source",
                "Line: Type",
                "Shipping: Country Code",
                "Line: Variant SKU",
            ],
            customer_field="Customer: ID",
            order_field="ID",
            extra_notes="Line-level cleaned Shopify table; SKU missingness limits product-level reliability.",
        ),
        completeness_row(
            "Shopify",
            "silver_shopify_order_rows",
            tables["silver_shopify_order_rows"],
            ["ID", "Customer: ID", "Processed At", "Source", "Shipping: Country Code", "Shipping: City"],
            customer_field="Customer: ID",
            order_field="ID",
            extra_notes="Logical one-row-per-order view from Top Row markers after duplicate removal.",
        ),
        completeness_row(
            "Shopify",
            "silver_products",
            tables["silver_products"],
            ["Variant SKU", "Title", "Variant Price"],
            extra_notes="Product master has no customer identity fields.",
        ),
        completeness_row(
            "Shopify",
            "silver_discounts",
            tables["silver_discounts"],
            ["Name", "Value", "Value Type", "Times Used In Total"],
            extra_notes="Discount table has no customer identity fields.",
        ),
        completeness_row(
            "Shopify",
            "silver_sessions",
            tables["silver_sessions"],
            ["Referrer source", "Session city", "Online store visitors", "Sessions"],
            extra_notes="Campaign/session table has no customer, order, or date key.",
        ),
        completeness_row(
            "Recharge",
            "silver_recharge_orders",
            tables["silver_recharge_orders"],
            ["recharge_order_id", "shopify_order_id", "customer_id", "metric_date", "order_type", "order_total"],
            customer_field="customer_id",
            order_field="recharge_order_id",
            extra_notes="Recharge customer_id is not the same namespace as Shopify Customer: ID.",
        ),
        completeness_row(
            "Recharge",
            "silver_recharge_order_items",
            tables["silver_recharge_order_items"],
            [
                "recharge_order_id",
                "shopify_order_id",
                "customer_id",
                "product_sku",
                "metric_date",
                "order_item_quantity",
            ],
            customer_field="customer_id",
            order_field="recharge_order_id",
            extra_notes="Missing product_sku rows cannot support SKU-level joins.",
        ),
        completeness_row(
            "Recharge",
            "silver_recharge_recurring",
            tables["silver_recharge_recurring"],
            [
                "recharge_order_id",
                "shopify_order_id",
                "customer_id",
                "product_sku",
                "metric_date",
                "order_item_quantity",
            ],
            customer_field="customer_id",
            order_field="recharge_order_id",
            extra_notes="Missing product_sku rows are likely discontinued/deleted variants.",
        ),
        completeness_row(
            "Recharge",
            "silver_recharge_reactivated",
            tables["silver_recharge_reactivated"],
            ["customer_id", "first_subscription_activation_date", "reactivated_date"],
            customer_field="customer_id",
            extra_notes="Reactivation table has no Shopify customer ID, email, or phone.",
        ),
        completeness_row(
            "Recharge",
            "silver_recharge_churned",
            tables["silver_recharge_churned"],
            [
                "subscription_id",
                "customer_id",
                "product_sku",
                "subscription_activation_date",
                "subscription_churn_date",
                "churn_type",
            ],
            customer_field="customer_id",
            subscription_field="subscription_id",
            extra_notes="Churn table has subscription_id but no Shopify customer ID, email, or phone.",
        ),
    ]
    return pd.DataFrame(rows)


def build_identity_bridge(
    shopify_order_rows: pd.DataFrame,
    recharge_orders: pd.DataFrame,
) -> pd.DataFrame:
    """Resolve identities through shopify_order_id -> Shopify order ID.

    Direct customer ID equality is not used because Shopify and Recharge use
    different customer ID namespaces.
    """
    required_shopify = {"ID", "Customer: ID"}
    required_recharge = {"shopify_order_id", "customer_id"}
    if not required_shopify.issubset(shopify_order_rows.columns) or not required_recharge.issubset(
        recharge_orders.columns
    ):
        return pd.DataFrame(columns=["shopify_order_id", "shopify_customer_id", "customer_id"])

    shopify_map = shopify_order_rows[["ID", "Customer: ID"]].copy()
    shopify_map = shopify_map.rename(
        columns={"ID": "shopify_order_id", "Customer: ID": "shopify_customer_id"}
    )
    recharge_map = recharge_orders[["shopify_order_id", "customer_id"]].copy()

    bridge = recharge_map.merge(shopify_map, on="shopify_order_id", how="inner")
    bridge = bridge[
        is_present(bridge["customer_id"])
        & is_present(bridge["shopify_customer_id"])
        & is_present(bridge["shopify_order_id"])
    ].copy()
    return bridge[["shopify_order_id", "shopify_customer_id", "customer_id"]].drop_duplicates()


def build_identity_resolution_summary(
    tables: dict[str, pd.DataFrame],
    bridge: pd.DataFrame,
) -> pd.DataFrame:
    shopify_customers = unique_present_values(tables["silver_shopify_order_rows"], "Customer: ID")
    recharge_customers = unique_present_values(tables["silver_recharge_orders"], "customer_id")

    matched_recharge = unique_present_values(bridge, "customer_id")
    matched_shopify = unique_present_values(bridge, "shopify_customer_id")
    direct_id_overlap = len(shopify_customers & recharge_customers)

    shopify_only_count = len(shopify_customers - matched_shopify)
    recharge_only_count = len(recharge_customers - matched_recharge)
    matched_count = len(matched_recharge)

    # The identity resolution rate is measured from the Recharge side because
    # every Recharge order should carry a Shopify order ID that can resolve back
    # to Shopify when the extract windows overlap.
    identity_resolution_rate = (
        round(matched_count / len(recharge_customers), 4) if recharge_customers else pd.NA
    )
    unresolved_identity_rate = (
        round(recharge_only_count / len(recharge_customers), 4) if recharge_customers else pd.NA
    )

    total_unified = shopify_only_count + matched_count + recharge_only_count
    notes = (
        "Join used: Recharge shopify_order_id -> Shopify ID -> Shopify Customer: ID; "
        "Recharge customer_id and Shopify Customer: ID are treated as different namespaces; "
        f"direct customer ID overlap was {direct_id_overlap}; "
        f"bridge contains {matched_count} distinct Recharge-to-Shopify customer mappings "
        f"and {len(bridge)} distinct order/customer mapping rows; "
        "actual email and phone identifiers are absent, so duplicate email/phone metrics are unavailable."
    )

    return pd.DataFrame(
        [
            {
                "total_unique_customers_or_identities": total_unified,
                "shopify_only_count": shopify_only_count,
                "recharge_only_count": recharge_only_count,
                "matched_shopify_recharge_count": matched_count,
                "unresolved_identity_count": recharge_only_count,
                "duplicate_email_count": pd.NA,
                "duplicate_phone_count": pd.NA,
                "identity_resolution_rate": identity_resolution_rate,
                "unresolved_identity_rate": unresolved_identity_rate,
                "notes": notes,
            }
        ]
    )


def reliability_rating(rate: float | pd.NA) -> str:
    if pd.isna(rate):
        return "Unavailable"
    if rate >= 0.95:
        return "High"
    if rate >= 0.80:
        return "Medium"
    return "Low"


def segment_row(
    segment_name: str,
    segment_definition: str,
    customer_ids: set[str] | None,
    matched_ids: set[str],
    reason: str,
) -> dict[str, object]:
    if customer_ids is None:
        return {
            "segment_name": segment_name,
            "segment_definition": segment_definition,
            "customer_count": pd.NA,
            "matched_identity_count": pd.NA,
            "unresolved_identity_count": pd.NA,
            "identity_resolution_rate": pd.NA,
            "reliability_rating": "Unavailable",
            "reason_analysis_is_limited": reason,
        }

    customer_count = len(customer_ids)
    matched_count = len(customer_ids & matched_ids)
    unresolved_count = customer_count - matched_count
    rate = round(matched_count / customer_count, 4) if customer_count else pd.NA
    return {
        "segment_name": segment_name,
        "segment_definition": segment_definition,
        "customer_count": customer_count,
        "matched_identity_count": matched_count,
        "unresolved_identity_count": unresolved_count,
        "identity_resolution_rate": rate,
        "reliability_rating": reliability_rating(rate),
        "reason_analysis_is_limited": reason,
    }


def build_segment_reliability_summary(
    tables: dict[str, pd.DataFrame],
    bridge: pd.DataFrame,
) -> pd.DataFrame:
    shopify_order_rows = tables["silver_shopify_order_rows"]
    recharge_orders = tables["silver_recharge_orders"]
    churned = tables["silver_recharge_churned"]

    shopify_customers = unique_present_values(shopify_order_rows, "Customer: ID")
    recharge_customers = unique_present_values(recharge_orders, "customer_id")
    matched_recharge = unique_present_values(bridge, "customer_id")
    matched_shopify = unique_present_values(bridge, "shopify_customer_id")

    order_counts = (
        shopify_order_rows.loc[is_present(shopify_order_rows["Customer: ID"])]
        .groupby("Customer: ID")["ID"]
        .nunique()
    )
    one_time_shopify = set(order_counts[order_counts == 1].index.astype(str))
    repeat_shopify = set(order_counts[order_counts > 1].index.astype(str))
    churned_recharge_all = unique_present_values(churned, "customer_id")
    # Approved DS2 definition: cancelled subscribers are Recharge customers
    # present in the churned-subscriptions file. Churned-only customer IDs that
    # do not appear in Recharge orders are excluded from this segment.
    cancelled_recharge = recharge_customers & churned_recharge_all
    active_recharge = recharge_customers - cancelled_recharge

    guest_records_count = 0
    if "Customer: ID" in shopify_order_rows.columns:
        guest_records_count = int((~is_present(shopify_order_rows["Customer: ID"])).sum())
    guest_record_ids = {f"guest_record_{idx}" for idx in range(guest_records_count)}

    rows = [
        segment_row(
            "Shopify-only customers",
            "Shopify Customer: ID exists but does not appear in the Shopify-Recharge bridge.",
            shopify_customers - matched_shopify,
            matched_shopify,
            "Usable for Shopify-only order analysis, but not reliable for cross-platform subscription analysis.",
        ),
        segment_row(
            "Recharge-only customers",
            "Recharge customer_id exists but could not be bridged to a Shopify Customer: ID.",
            recharge_customers - matched_recharge,
            matched_recharge,
            "Recharge records lack a resolved Shopify customer identity in the available export window.",
        ),
        segment_row(
            "Matched Shopify-Recharge customers",
            "Recharge customer_id successfully linked to Shopify Customer: ID through shopify_order_id.",
            matched_recharge,
            matched_recharge,
            "Cross-platform identity is resolved through order-level bridge.",
        ),
        segment_row(
            "Guest checkout records",
            "Shopify order-level rows where Customer: ID is missing.",
            guest_record_ids,
            set(),
            "No persistent Shopify customer ID, so customer-level retention and subscription linkage are unreliable.",
        ),
        segment_row(
            "Customers with missing email",
            "Actual email identifier is absent from the checked-in Shopify and Recharge files.",
            None,
            set(),
            "Unavailable: email completeness cannot be computed from marketing-status columns.",
        ),
        segment_row(
            "Customers with missing phone",
            "Actual phone identifier is absent from the checked-in Shopify and Recharge files.",
            None,
            set(),
            "Unavailable: phone completeness cannot be computed from SMS marketing-status columns.",
        ),
        segment_row(
            "Customers with duplicate email",
            "Duplicate email check requires an actual email identifier.",
            None,
            set(),
            "Unavailable: no email identifier is present.",
        ),
        segment_row(
            "Customers with duplicate phone",
            "Duplicate phone check requires an actual phone identifier.",
            None,
            set(),
            "Unavailable: no phone identifier is present.",
        ),
        segment_row(
            "Active subscribers",
            "Recharge customers not present in the churned-subscriptions file.",
            active_recharge,
            matched_recharge,
            "Active status is approximated from absence in churned subscriptions; unresolved records limit Shopify linkage.",
        ),
        segment_row(
            "Cancelled subscribers",
            "Recharge customers present in the churned-subscriptions file.",
            cancelled_recharge,
            matched_recharge,
            "Churn analysis is reliable only where Recharge customers bridge back to Shopify customers.",
        ),
        segment_row(
            "One-time purchasers",
            "Shopify customers with exactly one Shopify order.",
            one_time_shopify,
            matched_shopify,
            "Mostly Shopify-only; cross-platform subscription behavior is under-observed unless bridged.",
        ),
        segment_row(
            "Repeat purchasers",
            "Shopify customers with more than one Shopify order.",
            repeat_shopify,
            matched_shopify,
            "Strong for Shopify retention, but subscription conclusions depend on bridge coverage.",
        ),
    ]
    return pd.DataFrame(rows)


def pct(value: object) -> str:
    if pd.isna(value):
        return "unavailable"
    return f"{float(value):.1%}"


def int_fmt(value: object) -> str:
    if pd.isna(value):
        return "unavailable"
    return f"{int(value):,}"


def write_markdown_report(
    output_path: Path,
    completeness: pd.DataFrame,
    identity: pd.DataFrame,
    segments: pd.DataFrame,
    context: DataContext,
    bridge: pd.DataFrame,
) -> None:
    main_shopify = completeness[completeness["table_name"] == "silver_shopify_order_rows"].iloc[0]
    main_recharge = completeness[completeness["table_name"] == "silver_recharge_orders"].iloc[0]
    identity_row = identity.iloc[0]

    low_segments = segments[segments["reliability_rating"].isin(["Low", "Unavailable"])]
    low_segment_lines = "\n".join(
        f"- {row.segment_name}: {row.reliability_rating}; {row.reason_analysis_is_limited}"
        for row in low_segments.itertuples()
    )

    tables_inspected = "\n".join(f"- {name}" for name in completeness["table_name"])
    silver_mode = (
        "Saved silver parquet files were used."
        if context.used_saved_silver
        else "Saved silver parquet files were not available; DS2 silver-style views were rebuilt in memory."
    )
    silver_notes = "\n".join(f"- {note}" for note in context.silver_load_notes)

    report = f"""# DS2 Infrastructure Findings

## Research Question

{RESEARCH_QUESTION}

## Data Sources Used

- Shopify yearly order exports from `1.customer_transaction/`
- Shopify product master from `2.product_master/`
- Shopify discount export from `3.Discounts/`
- Shopify sessions-by-referrer export from `4.Campaigns/`
- Recharge order, item, reactivation, and churn exports from `5.Recharge_data/`

{silver_mode}

{silver_notes}

## Silver Tables / Logical Views Inspected

{tables_inspected}

## Definitions

- Field completeness rate: non-null, non-empty values divided by total rows.
- Overall table completeness rate: average completeness across the key fields checked for that table.
- Identity resolution rate: Recharge customer identities matched to Shopify through `shopify_order_id -> Shopify ID -> Customer: ID`, divided by total Recharge customer identities.
- Unresolved identity: a customer or record that cannot be confidently linked across systems because key identifiers are missing, inconsistent, duplicated, or conflicting.
- Reliability rating: High when identity resolution rate is >= 95%, Medium when >= 80% and < 95%, Low when < 80%. Rows marked Unavailable are segments where required columns are absent.

## Key Numbers

- Shopify order-level completeness (`silver_shopify_order_rows`): {pct(main_shopify["completeness_rate_overall"])} across {int_fmt(main_shopify["row_count"])} order rows.
- Shopify customer ID completeness: {pct(main_shopify["customer_id_completeness"])}.
- Recharge order completeness (`silver_recharge_orders`): {pct(main_recharge["completeness_rate_overall"])} across {int_fmt(main_recharge["row_count"])} Recharge orders.
- Recharge customer ID completeness: {pct(main_recharge["customer_id_completeness"])}.
- Matched Shopify-Recharge customer identities: {int_fmt(identity_row["matched_shopify_recharge_count"])}.
- Recharge-only unresolved identities: {int_fmt(identity_row["recharge_only_count"])}.
- Recharge-side identity resolution rate: {pct(identity_row["identity_resolution_rate"])}.
- Unresolved identity rate: {pct(identity_row["unresolved_identity_rate"])}.
- Distinct bridge rows: {int_fmt(len(bridge))}.

## So What?

The core Shopify and Recharge operational IDs are mostly complete after silver-style cleaning, so order-level infrastructure is strong enough for customer insight work. The main reliability limitation is cross-platform identity resolution: Recharge and Shopify use different customer ID namespaces, and the available checked-in data does not include actual email or phone identifiers to support secondary matching. Therefore, subscription analyses should rely on the order-level `shopify_order_id` bridge and clearly flag Recharge customers that do not bridge back to Shopify.

## Segments Where Identity Resolution Limits Reliability

{low_segment_lines}

## Caveats

- No actual customer email or phone columns are present in the checked-in Shopify or Recharge files. Email/SMS marketing-status columns are not identity keys.
- Duplicate email and duplicate phone metrics are unavailable.
- The current checkout contains `medallion/bronze/`, but not saved `medallion/silver/` or `medallion/gold/` outputs.
- Recharge data extends to 2026-04-07, while the Shopify order export appears to stop at 2026-03-31. Some unmatched Recharge records may come from this export-window gap.
- Campaign/session data has no customer ID, order ID, or date key, so it cannot support identity resolution or conversion-rate analysis.
- Active subscriber status is approximated as Recharge customers absent from the churned-subscriptions file.
"""
    output_path.write_text(report, encoding="utf-8")


def write_outputs(base: Path, context: DataContext) -> None:
    output_dir = base / "outputs"
    output_dir.mkdir(exist_ok=True)

    tables = context.tables
    bridge = build_identity_bridge(
        tables["silver_shopify_order_rows"],
        tables["silver_recharge_orders"],
    )
    completeness = build_completeness_summary(tables)
    identity = build_identity_resolution_summary(tables, bridge)
    segments = build_segment_reliability_summary(tables, bridge)

    completeness.to_csv(output_dir / "completeness_summary.csv", index=False)
    identity.to_csv(output_dir / "identity_resolution_summary.csv", index=False)
    segments.to_csv(output_dir / "segment_reliability_summary.csv", index=False)
    write_markdown_report(
        output_dir / "ds2_infrastructure_findings.md",
        completeness,
        identity,
        segments,
        context,
        bridge,
    )


def main() -> None:
    base = project_root()
    context = load_data_context(base)
    write_outputs(base, context)
    print("Generated DS2 infrastructure outputs in outputs/.")


if __name__ == "__main__":
    main()
