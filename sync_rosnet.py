import os
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
import psycopg2
from dotenv import load_dotenv

import api

load_dotenv()

# -----------------------------
# Environment / Config
# -----------------------------
DB_HOST = os.getenv("DB_HOST")
DB_NAME = os.getenv("DB_NAME", "postgres")
DB_USER = os.getenv("DB_USER")
DB_PASSWORD = os.getenv("DB_PASSWORD")

db_port_raw = os.getenv("DB_PORT")
DB_PORT = int(db_port_raw) if db_port_raw and db_port_raw.strip() else 6543

sync_lookback_raw = os.getenv("SYNC_LOOKBACK_DAYS")
SYNC_LOOKBACK_DAYS = int(sync_lookback_raw) if sync_lookback_raw and sync_lookback_raw.strip() else 1

max_stores_raw = os.getenv("MAX_STORES_PER_RUN")
MAX_STORES_PER_RUN = int(max_stores_raw) if max_stores_raw and max_stores_raw.strip() else 8

request_delay_raw = os.getenv("REQUEST_DELAY_SECONDS")
REQUEST_DELAY_SECONDS = float(request_delay_raw) if request_delay_raw and request_delay_raw.strip() else 3.0

max_rate_limit_events_raw = os.getenv("MAX_RATE_LIMIT_EVENTS")
MAX_RATE_LIMIT_EVENTS = int(max_rate_limit_events_raw) if max_rate_limit_events_raw and max_rate_limit_events_raw.strip() else 3

if not all([DB_HOST, DB_USER, DB_PASSWORD]):
    raise RuntimeError("Missing one or more required DB environment variables.")

tz = ZoneInfo("America/New_York")
today = datetime.now(tz).date()
end_date = today - timedelta(days=1)
start_date = end_date - timedelta(days=SYNC_LOOKBACK_DAYS - 1)


# -----------------------------
# Helpers
# -----------------------------
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


def ensure_tables(conn):
    cur = conn.cursor()
    try:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS sync_progress (
                store_number bigint PRIMARY KEY,
                store_name text,
                last_synced_date date,
                last_attempted_at timestamptz,
                last_status text,
                last_message text,
                updated_at timestamptz DEFAULT now()
            );
            """
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_employee_daily_metrics_date_store
            ON employee_daily_metrics (business_date, store_number);
            """
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


def seed_sync_progress(conn, loc_map):
    cur = conn.cursor()
    try:
        for store_id, store_name in loc_map.items():
            cur.execute(
                """
                INSERT INTO sync_progress (store_number, store_name, updated_at)
                VALUES (%s, %s, now())
                ON CONFLICT (store_number)
                DO UPDATE SET
                    store_name = EXCLUDED.store_name,
                    updated_at = now();
                """,
                (int(store_id), store_name),
            )
        conn.commit()
    finally:
        cur.close()


def select_stores_for_run(conn, target_date, max_stores):
    cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT store_number, store_name, last_synced_date
            FROM sync_progress
            WHERE last_synced_date IS NULL
               OR last_synced_date < %s
            ORDER BY
                CASE WHEN last_synced_date IS NULL THEN 0 ELSE 1 END,
                last_synced_date NULLS FIRST,
                store_number
            LIMIT %s;
            """,
            (target_date, max_stores),
        )
        return cur.fetchall()
    finally:
        cur.close()


def mark_progress(conn, store_id, status, message="", synced_date=None):
    cur = conn.cursor()
    try:
        if synced_date:
            cur.execute(
                """
                UPDATE sync_progress
                SET last_synced_date = %s,
                    last_attempted_at = now(),
                    last_status = %s,
                    last_message = %s,
                    updated_at = now()
                WHERE store_number = %s;
                """,
                (synced_date, status, message[:1000], int(store_id)),
            )
        else:
            cur.execute(
                """
                UPDATE sync_progress
                SET last_attempted_at = now(),
                    last_status = %s,
                    last_message = %s,
                    updated_at = now()
                WHERE store_number = %s;
                """,
                (status, message[:1000], int(store_id)),
            )
        conn.commit()
    finally:
        cur.close()


def fetch_store_day(store_id, day_str, emp_map, bev_ids):
    checks = api.get_checks(
        day_str,
        day_str,
        store_id,
        emp_map=emp_map,
        bev_cat_ids=bev_ids,
    )
    return pd.DataFrame(checks)


def filter_to_true_dine_in(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df.copy()

    if "orderType" not in df.columns:
        return pd.DataFrame()

    order_type = df["orderType"].fillna("").astype(str).str.strip().str.lower()

    allowed = {
        "eat in",
        "eat-in",
        "dine in",
        "dine-in",
    }

    excluded_contains = [
        "staff",
        "olo",
        "online",
        "to go",
        "togo",
        "to-go",
        "delivery",
        "pickup",
        "carryout",
        "carry out",
        "curbside",
        "3rd party",
        "third party",
    ]

    dine_mask = order_type.isin(allowed)
    for bad in excluded_contains:
        dine_mask &= ~order_type.str.contains(bad, na=False)

    return df[dine_mask].copy()


def transform_checks(df, store_id, day_str):
    if df.empty:
        return pd.DataFrame()

    df = filter_to_true_dine_in(df)
    if df.empty:
        return pd.DataFrame()

    required = {"serverName", "checkNumber", "netSales", "beverageSales", "openTime", "closeTime"}
    missing = required - set(df.columns)
    if missing:
        print(f"    skipped, missing columns: {sorted(missing)}")
        return pd.DataFrame()

    df = df.dropna(
        subset=["serverName", "checkNumber", "netSales", "beverageSales", "openTime", "closeTime"]
    ).copy()

    if df.empty:
        return pd.DataFrame()

    df["openTime"] = pd.to_datetime(df["openTime"], format="%H:%M:%S", errors="coerce")
    df["closeTime"] = pd.to_datetime(df["closeTime"], format="%H:%M:%S", errors="coerce")

    df["turn_time"] = (df["closeTime"] - df["openTime"]).dt.total_seconds() / 60
    df.loc[df["turn_time"] < 0, "turn_time"] += 24 * 60
    df = df[df["turn_time"] > 0].copy()

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

    grouped["employee_id"] = (
        grouped["serverName"]
        .fillna("")
        .apply(lambda x: abs(hash((int(store_id), day_str, x))) % 2147483647)
    )
    grouped["employee_name"] = grouped["serverName"]

    return grouped[
        [
            "employee_id",
            "employee_name",
            "ppa",
            "beverage_pct",
            "turn_time",
            "check_count",
            "sales",
        ]
    ]


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


def main():
    print(f"Sync window: {start_date} to {end_date}")
    print(f"Max stores this run: {MAX_STORES_PER_RUN}")
    print(f"Request delay: {REQUEST_DELAY_SECONDS}s")

    conn = get_conn()
    ensure_tables(conn)

    loc_map = build_location_map()
    if not loc_map:
        raise RuntimeError("No locations returned from Rosnet.")

    seed_sync_progress(conn, loc_map)

    stores_to_sync = select_stores_for_run(conn, end_date, MAX_STORES_PER_RUN)
    if not stores_to_sync:
        print("No stores need syncing for the target date. Exiting.")
        conn.close()
        return

    print(f"Stores selected this run: {len(stores_to_sync)}")

    bev_ids = api.get_beverage_category_ids()

    total_rows = 0
    total_store_days = 0
    rate_limit_events = 0

    try:
        for store_id, store_name, last_synced_date in stores_to_sync:
            print(f"\nSyncing {store_id} - {store_name} (last synced: {last_synced_date})")

            try:
                emp_map = api.get_employees_map(int(store_id))
            except api.RateLimitExceeded as e:
                wait_for = max(getattr(e, "retry_after", 30), 30)
                rate_limit_events += 1
                print(f"  rate limited getting employee map, sleeping {wait_for}s")
                time.sleep(wait_for)
                mark_progress(conn, store_id, "rate_limited", f"employee map retry_after={wait_for}")
                if rate_limit_events >= MAX_RATE_LIMIT_EVENTS:
                    print("Too many rate limit events. Exiting early.")
                    break
                continue
            except Exception as e:
                print(f"  failed employee map: {e}")
                mark_progress(conn, store_id, "error", f"employee map error: {e}")
                continue

            store_success = True
            latest_synced_for_store = None

            for day in daterange(start_date, end_date):
                day_str = day.strftime("%Y-%m-%d")
                print(f"  {day_str}")

                try:
                    df = fetch_store_day(int(store_id), day_str, emp_map, bev_ids)
                except api.RateLimitExceeded as e:
                    wait_for = max(getattr(e, "retry_after", 30), 30)
                    rate_limit_events += 1
                    print(f"    rate limited, sleeping {wait_for}s and skipping this store-day")
                    time.sleep(wait_for)
                    mark_progress(conn, store_id, "rate_limited", f"store-day retry_after={wait_for}")
                    store_success = False
                    if rate_limit_events >= MAX_RATE_LIMIT_EVENTS:
                        print("Too many rate limit events. Exiting early.")
                        return
                    continue
                except Exception as e:
                    print(f"    fetch error: {e}")
                    mark_progress(conn, store_id, "error", f"fetch error: {e}")
                    store_success = False
                    continue

                time.sleep(REQUEST_DELAY_SECONDS)

                grouped = transform_checks(df, store_id, day_str)
                if grouped.empty:
                    print("    no usable grouped rows")
                    latest_synced_for_store = day
                    continue

                upsert_grouped_rows(conn, grouped, store_id, day)
                inserted = len(grouped)
                total_rows += inserted
                total_store_days += 1
                latest_synced_for_store = day

                print(f"    upserted {inserted} employee rows")

            if latest_synced_for_store:
                mark_progress(
                    conn,
                    store_id,
                    "success" if store_success else "partial",
                    f"synced through {latest_synced_for_store}",
                    synced_date=latest_synced_for_store,
                )
            else:
                mark_progress(
                    conn,
                    store_id,
                    "no_data" if store_success else "partial",
                    "no rows synced",
                )

    finally:
        conn.close()

    print("\nDone.")
    print(f"Store-days synced: {total_store_days}")
    print(f"Employee rows upserted: {total_rows}")


if __name__ == "__main__":
    main()
