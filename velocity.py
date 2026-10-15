import argparse
import math
import sqlite3
from contextlib import closing
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd


DEFAULT_DATABASE_PATH = Path(__file__).with_name("pantry.db")


def analyze_inventory(
    database_path=DEFAULT_DATABASE_PATH,
    low_stock_days=7,
    as_of_date=None,
):
    """Return item stock, average daily consumption, and restock status.

    Average consumption is total logged consumption divided by the number of
    calendar days from the item's first log through ``as_of_date``.
    """
    if not math.isfinite(low_stock_days) or low_stock_days < 0:
        raise ValueError("low_stock_days must be a finite, non-negative number")

    as_of_date = as_of_date or date.today()
    if not isinstance(as_of_date, date) or isinstance(as_of_date, datetime):
        raise TypeError("as_of_date must be a datetime.date")

    with closing(sqlite3.connect(database_path)) as connection:
        items = pd.read_sql_query(
            """
            SELECT id AS item_id, name, quantity, category
            FROM items
            ORDER BY id
            """,
            connection,
        )
        logs = pd.read_sql_query(
            """
            SELECT item_id, consumed_amount, logged_date
            FROM consumption_logs
            """,
            connection,
        )

    if items.empty:
        items["average_daily_consumption"] = pd.Series(dtype="float64")
        items["days_until_empty"] = pd.Series(dtype="float64")
        items["predicted_stockout_date"] = pd.Series(dtype="object")
        items["stock_status"] = pd.Series(dtype="object")
        return items

    items["quantity"] = pd.to_numeric(items["quantity"], errors="raise")
    if (items["quantity"] < 0).any():
        raise ValueError("Item quantities cannot be negative")

    logs["consumed_amount"] = pd.to_numeric(logs["consumed_amount"], errors="raise")
    if not logs.empty:
        if (logs["consumed_amount"] <= 0).any():
            raise ValueError("Logged consumption amounts must be positive")
        logs["logged_date"] = pd.to_datetime(
            logs["logged_date"], errors="raise"
        ).dt.date
        logs = logs[logs["logged_date"] <= as_of_date]

    consumption_by_item = {}
    if not logs.empty:
        for item_id, item_logs in logs.groupby("item_id"):
            first_log_date = item_logs["logged_date"].min()
            observed_days = (as_of_date - first_log_date).days + 1
            total_consumed = item_logs["consumed_amount"].sum()
            consumption_by_item[item_id] = total_consumed / observed_days

    items["average_daily_consumption"] = (
        items["item_id"].map(consumption_by_item).fillna(0.0)
    )
    items["days_until_empty"] = items.apply(
        lambda item: (
            0.0
            if item["quantity"] == 0
            else (
                item["quantity"] / item["average_daily_consumption"]
                if item["average_daily_consumption"] > 0
                else float("nan")
            )
        ),
        axis=1,
    )
    items["predicted_stockout_date"] = items["days_until_empty"].map(
        lambda days: (
            as_of_date + timedelta(days=math.ceil(days))
            if pd.notna(days)
            else None
        )
    )
    items["stock_status"] = items.apply(
        lambda item: (
            "out_of_stock"
            if item["quantity"] == 0
            else (
                "low_stock"
                if pd.notna(item["days_until_empty"])
                and item["days_until_empty"] <= low_stock_days
                else "in_stock"
            )
        ),
        axis=1,
    )
    return items


def generate_shopping_list(inventory):
    """Return items that are out of stock or forecast to run low."""
    return inventory[inventory["stock_status"].isin(["low_stock", "out_of_stock"])].copy()


def main():
    parser = argparse.ArgumentParser(
        description="Forecast pantry stock and print the items to restock."
    )
    parser.add_argument(
        "--database",
        type=Path,
        default=DEFAULT_DATABASE_PATH,
        help=f"SQLite database path (default: {DEFAULT_DATABASE_PATH})",
    )
    parser.add_argument(
        "--low-stock-days",
        type=float,
        default=7,
        help="Add items with this many days of stock remaining or less (default: 7)",
    )
    args = parser.parse_args()

    inventory = analyze_inventory(args.database, args.low_stock_days)
    shopping_list = generate_shopping_list(inventory)
    print("Inventory forecast:")
    print(inventory.to_string(index=False))
    print("\nShopping list:")
    if shopping_list.empty:
        print("No items need restocking.")
    else:
        print(
            shopping_list[
                [
                    "name",
                    "quantity",
                    "average_daily_consumption",
                    "days_until_empty",
                    "predicted_stockout_date",
                ]
            ].to_string(index=False)
        )


if __name__ == "__main__":
    main()