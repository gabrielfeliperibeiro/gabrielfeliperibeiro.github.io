#!/usr/bin/env python3
"""
Fetch orders data from Databricks SQL warehouse and save to JSON.
"""
import json
import os
from datetime import datetime, timezone

from databricks import sql


def get_databricks_connection():
    """Create connection to Databricks SQL warehouse."""
    host = os.environ.get("DATABRICKS_HOST", "adb-1825183661408911.11.azuredatabricks.net")
    http_path = os.environ.get("DATABRICKS_HTTP_PATH", "/sql/protocolv1/o/1825183661408911/0523-172047-4vu5f6v7")
    token = os.environ.get("DATABRICKS_TOKEN")

    if not token:
        raise ValueError("DATABRICKS_TOKEN environment variable is required")

    return sql.connect(
        server_hostname=host,
        http_path=http_path,
        access_token=token
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

    debug = os.environ.get("DEBUG", "false").lower() == "true"

    if debug:
        print(f"Connecting to Databricks...")
        print(f"Host: {os.environ.get('DATABRICKS_HOST', 'default')}")

    with get_databricks_connection() as conn:
        with conn.cursor() as cursor:
            if debug:
                print("Executing query...")
            cursor.execute(query)
            columns = [desc[0] for desc in cursor.description]
            rows = cursor.fetchall()

            if debug:
                print(f"Fetched {len(rows)} rows")

            orders = []
            for row in rows:
                order = dict(zip(columns, row))
                # Convert datetime to ISO format string
                if order.get("placement_date"):
                    order["placement_date"] = order["placement_date"].isoformat()
                orders.append(order)

            return orders


def save_orders(orders):
    """Save orders to JSON file."""
    data = {
        "lastUpdated": datetime.now(timezone.utc).isoformat(),
        "orders": orders
    }

    output_path = os.path.join(os.path.dirname(__file__), "..", "data", "orders.json")
    output_path = os.path.abspath(output_path)

    with open(output_path, "w") as f:
        json.dump(data, f, indent=2, default=str)

    print(f"Saved {len(orders)} orders to {output_path}")
    print(f"Last updated: {data['lastUpdated']}")


def main():
    """Main entry point."""
    try:
        print("Fetching orders from Databricks...")
        orders = fetch_orders()
        save_orders(orders)
        print("Data update complete!")
    except Exception as e:
        print(f"Error fetching data: {e}")
        raise


if __name__ == "__main__":
    main()
