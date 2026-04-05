import os
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
import psycopg2
from dotenv import load_dotenv

import api

load_dotenv()

DB_HOST = os.getenv("DB_HOST")
DB_PORT = int(os.getenv("DB_PORT", "6543"))
DB_NAME = os.getenv("DB_NAME", "postgres")
DB_USER = os.getenv("DB_USER")
DB_PASSWORD = os.getenv("DB_PASSWORD")

SYNC_LOOKBACK_DAYS = int(os.getenv("SYNC_LOOKBACK_DAYS", "2"))

if not all([DB_HOST, DB_USER, DB_PASSWORD]):
    raise RuntimeError("Missing DB environment variables.")

# Stay below 60 calls/minute.
# 1.2 sec between store-day pulls is a safe throttle.
REQUEST_DELAY_SECONDS = 1.2

tz = ZoneInfo("America/New_York")
today = datetime.now(tz).date()
end_date = today - timedelta(days=1)
start_date = end_date - timedelta(days=SYNC_LOOKBACK_DAYS - 1)


def daterange(start, end):
    current = start
    while current <= end:
        yield current
        current += timedelta(days=1)


def get_conn():
    return psycopg2.connect(
        host=DB_HOST,
        port=DB_PORT,
        dbname=DB_NAME,
        user=DB_USER,
        password=DB_PASSWORD,
    )


def upsert_grouped_rows(conn, grouped_df, store_id, business_date):
    cur = conn.cursor()
    try:
        for _, row in grouped_df.iterrows():
            cur.execute(
                """
                INSERT INTO employee_daily_metrics (
                    store_number,
                    business_date,
                    employee_id,
                    employee_name,
                    ppa,
                    beverage_pct,
                    turn_time,
                    check_count,
                    sales,
                    updated_at
                )
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,now())
                ON CONFLICT (store_number, business_date, employee_id)
                DO UPDATE SET
                    employee_name = EXCLUDED.employee_name,
                    ppa = EXCLUDED.ppa,
                    beverage_pct = EXCLUDED.beverage_pct,
                    turn_time = EXCLUDED.turn_time,
                    check_count = EXCLUDED.check_count,
                    sales = EXCLUDED.sales,
                    updated_at = now();
                """,
                (
                    int(store_id),
                    business_date,
                    int(row["employee_id"]),
                    row["employee_name"],
                    float(row["ppa"]),
                    float(row["beverage_pct"]),
                    float(row["turn_time"]),
                    float(row["check_count"]),
                    float(row["sales"]),
                ),
            )
        conn.commit()
    finally:
        cur.close()


def build_location_map():
    raw_locations = api.get_locations()
    loc_map = {}
    for loc in raw_locations:
        if not loc:
            continue
        l_id = loc.get("Id", loc.get("id"))
        l_name = loc.get("Name", loc.get("name", "Unknown"))
        if l_id is not None:
            loc_map[int(l_id)] = l_name
    return loc_map


def fetch_store_day(store_id, day_str, emp_map, bev_ids):
    checks = api.get_checks(
        day_str,
        day_str,
        store_id,
        emp_map=emp_map,
        bev_cat_ids=bev_ids,
    )
    return pd.DataFrame(checks)


def transform_checks(df, store_id, day_str):
    if df.empty:
        return pd.DataFrame()

    required = {"serverName", "checkNumber", "netSales", "beverageSales", "turn_time"}
    missing = required - set(df.columns)
    if missing:
        print(f"    skipped, missing columns: {sorted(missing)}")
        return pd.DataFrame()

    df = df.dropna(
        subset=["serverName", "checkNumber", "netSales", "beverageSales", "turn_time"]
    ).copy()

    if df.empty:
        return pd.DataFrame()

    grouped = (
        df.groupby("serverName", dropna=False)
        .agg(
            sales=("netSales", "sum"),
            beverage_sales=("beverageSales", "sum"),
            turn_time=("turn_time", "mean"),
            check_count=("checkNumber", "count"),
        )
        .reset_index()
    )

    grouped["ppa"] = grouped["sales"] / grouped["check_count"]
    grouped["beverage_pct"] = (
        (grouped["beverage_sales"] / grouped["sales"])
        .replace([pd.NA, pd.NaT], 0)
        .fillna(0)
        * 100
    )

    # Stable placeholder employee ID.
    # Later, if your Rosnet payload includes a true employee ID, swap this out.
    grouped["employee_id"] = (
        grouped["serverName"]
        .fillna("")
        .apply(lambda x: abs(hash((int(store_id), day_str, x))) % 2147483647)
    )
    grouped["employee_name"] = grouped["serverName"]

    return grouped[[
        "employee_id",
        "employee_name",
        "ppa",
        "beverage_pct",
        "turn_time",
        "check_count",
        "sales",
    ]]


def main():
    print(f"Sync window: {start_date} to {end_date}")
    print(f"DB host: {DB_HOST}")

    loc_map = build_location_map()
    if not loc_map:
        raise RuntimeError("No locations returned from Rosnet.")

    print(f"Locations found: {len(loc_map)}")

    bev_ids = api.get_beverage_category_ids()
    conn = get_conn()

    total_rows = 0
    total_store_days = 0

    try:
        for store_id, store_name in loc_map.items():
            print(f"\nSyncing {store_id} - {store_name}")
            emp_map = api.get_employees_map(store_id)

            for day in daterange(start_date, end_date):
                day_str = day.strftime("%Y-%m-%d")
                print(f"  {day_str}")

                try:
                    df = fetch_store_day(store_id, day_str, emp_map, bev_ids)
                except api.RateLimitExceeded as e:
                    wait_for = max(getattr(e, "retry_after", 60), 60)
                    print(f"    rate limited, sleeping {wait_for}s")
                    time.sleep(wait_for)
                    df = fetch_store_day(store_id, day_str, emp_map, bev_ids)

                time.sleep(REQUEST_DELAY_SECONDS)

                if df.empty:
                    print("    no rows")
                    continue

                grouped = transform_checks(df, store_id, day_str)
                if grouped.empty:
                    print("    no usable grouped rows")
                    continue

                upsert_grouped_rows(conn, grouped, store_id, day)
                inserted = len(grouped)
                total_rows += inserted
                total_store_days += 1
                print(f"    upserted {inserted} employee rows")

    finally:
        conn.close()

    print(f"\nDone.")
    print(f"Store-days synced: {total_store_days}")
    print(f"Employee rows upserted: {total_rows}")


if __name__ == "__main__":
    main()
