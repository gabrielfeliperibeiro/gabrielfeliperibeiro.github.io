#!/usr/bin/env python3
"""
BEES Dashboard Data Fetcher

This script fetches live order data from Databricks and saves it as static JSON files.
Used by GitHub Actions for automated data updates.
"""

import json
import os
from datetime import datetime, timezone
from pathlib import Path

try:
    from databricks import sql
except ImportError:
    print("Error: databricks-sql-connector not installed")
    print("Run: pip install databricks-sql-connector")
    exit(1)


# Configuration from environment variables
DATABRICKS_HOST = os.environ.get("DATABRICKS_HOST", "adb-1825183661408911.11.azuredatabricks.net")
DATABRICKS_TOKEN = os.environ.get("DATABRICKS_TOKEN")
DATABRICKS_HTTP_PATH = os.environ.get("DATABRICKS_HTTP_PATH", "/sql/protocolv1/o/1825183661408911/0523-172047-4vu5f6v7")

# Output directory
SCRIPT_DIR = Path(__file__).parent
DATA_DIR = SCRIPT_DIR.parent / "data"


def get_connection():
    """Create Databricks SQL connection."""
    if not DATABRICKS_TOKEN:
        raise ValueError("DATABRICKS_TOKEN environment variable is required")

    return sql.connect(
        server_hostname=DATABRICKS_HOST,
        http_path=DATABRICKS_HTTP_PATH,
        access_token=DATABRICKS_TOKEN
    )


def fetch_orders():
    """Fetch today's orders from Databricks."""
    query = """
    SELECT
        country,
        placement_date,
        vendor_id,
        order_number,
        order_status,
        order_gmv,
        order_gmv_usd,
        account_id,
        vendor_account_id,
        channel,
        source,
        minute_aging,
        is_last_minute,
        is_last_30_minutes,
        is_last_hour,
        is_today
    FROM wh_am.sandbox.orders_live_tracking
    WHERE country = 'PH'
      AND TO_DATE(DATE_TRUNC('DAY', placement_date)) = CURRENT_DATE()
    ORDER BY placement_date DESC
    """

    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(query)
            columns = [desc[0] for desc in cursor.description]
            rows = cursor.fetchall()

    # Convert to list of dictionaries
    orders = []
    for row in rows:
        order = {}
        for i, col in enumerate(columns):
            value = row[i]
            # Convert datetime to ISO string
            if isinstance(value, datetime):
                value = value.isoformat()
            order[col] = value
        orders.append(order)

    return orders


def calculate_summary(orders):
    """Calculate summary statistics from orders."""
    if not orders:
        return {
            "total_orders": 0,
            "total_gmv": 0,
            "total_gmv_usd": 0,
            "active_vendors": 0,
            "avg_order_value": 0,
            "status_breakdown": {},
            "channel_breakdown": {},
            "hourly_distribution": {}
        }

    # Basic counts
    total_orders = len(orders)
    total_gmv = sum(o.get("order_gmv") or 0 for o in orders)
    total_gmv_usd = sum(o.get("order_gmv_usd") or 0 for o in orders)
    unique_vendors = set(o.get("vendor_id") for o in orders if o.get("vendor_id"))

    # Status breakdown
    status_breakdown = {}
    for order in orders:
        status = order.get("order_status", "UNKNOWN")
        status_breakdown[status] = status_breakdown.get(status, 0) + 1

    # Channel breakdown
    channel_breakdown = {}
    for order in orders:
        channel = order.get("channel", "UNKNOWN")
        if channel not in channel_breakdown:
            channel_breakdown[channel] = {"count": 0, "gmv": 0}
        channel_breakdown[channel]["count"] += 1
        channel_breakdown[channel]["gmv"] += order.get("order_gmv") or 0

    # Hourly distribution
    hourly_distribution = {}
    for order in orders:
        placement_date = order.get("placement_date", "")
        if placement_date:
            try:
                # Parse ISO format datetime
                dt = datetime.fromisoformat(placement_date.replace("Z", "+00:00"))
                hour = dt.hour
                hourly_distribution[hour] = hourly_distribution.get(hour, 0) + 1
            except (ValueError, AttributeError):
                pass

    return {
        "total_orders": total_orders,
        "total_gmv": round(total_gmv, 2),
        "total_gmv_usd": round(total_gmv_usd, 2),
        "active_vendors": len(unique_vendors),
        "avg_order_value": round(total_gmv / total_orders, 2) if total_orders > 0 else 0,
        "status_breakdown": status_breakdown,
        "channel_breakdown": channel_breakdown,
        "hourly_distribution": hourly_distribution
    }


def save_data(orders, summary):
    """Save data to JSON files."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now(timezone.utc).isoformat()

    # Save orders
    orders_data = {
        "updated_at": timestamp,
        "count": len(orders),
        "orders": orders
    }
    orders_file = DATA_DIR / "orders.json"
    with open(orders_file, "w") as f:
        json.dump(orders_data, f, indent=2)
    print(f"Saved {len(orders)} orders to {orders_file}")

    # Save summary
    summary_data = {
        "updated_at": timestamp,
        **summary
    }
    summary_file = DATA_DIR / "summary.json"
    with open(summary_file, "w") as f:
        json.dump(summary_data, f, indent=2)
    print(f"Saved summary to {summary_file}")

    # Save metadata
    metadata = {
        "last_updated": timestamp,
        "orders_count": len(orders),
        "data_source": "databricks",
        "country": "PH"
    }
    metadata_file = DATA_DIR / "metadata.json"
    with open(metadata_file, "w") as f:
        json.dump(metadata, f, indent=2)
    print(f"Saved metadata to {metadata_file}")


def main():
    """Main entry point."""
    print(f"BEES Dashboard Data Fetcher")
    print(f"=" * 40)
    print(f"Timestamp: {datetime.now(timezone.utc).isoformat()}")
    print(f"Databricks Host: {DATABRICKS_HOST}")
    print()

    try:
        print("Fetching orders from Databricks...")
        orders = fetch_orders()
        print(f"Fetched {len(orders)} orders")

        print("Calculating summary statistics...")
        summary = calculate_summary(orders)
        print(f"Summary: {summary['total_orders']} orders, ${summary['total_gmv_usd']:.2f} GMV")

        print("Saving data files...")
        save_data(orders, summary)

        print()
        print("Data update completed successfully!")

    except Exception as e:
        print(f"Error: {e}")
        raise


if __name__ == "__main__":
    main()
