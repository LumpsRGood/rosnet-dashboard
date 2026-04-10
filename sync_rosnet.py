import os
import time
import hashlib
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

target_store_raw = os.getenv("TARGET_STORE")
TARGET_STORE = int(target_store_raw) if target_store_raw and target_store_raw.strip() else None

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
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS employee_zero_guest_alerts (
                store_number bigint NOT NULL,
                business_date date NOT NULL,
                employee_id bigint NOT NULL,
                employee_name text,
                zero_guest_check_count integer NOT NULL DEFAULT 0,
                zero_guest_sales numeric NOT NULL DEFAULT 0,
                zero_guest_beverage_sales numeric NOT NULL DEFAULT 0,
                updated_at timestamptz DEFAULT now(),
                PRIMARY KEY (store_number, business_date, employee_id)
            );
            """
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_employee_zero_guest_alerts_date_store
            ON employee_zero_guest_alerts (business_date, store_number);
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
        if TARGET_STORE is not None:
            cur.execute(
                """
                SELECT store_number, store_name, last_synced_date
                FROM sync_progress
                WHERE store_number = %s
                  AND (
                      last_synced_date IS NULL
                      OR last_synced_date < %s
                  )
                ORDER BY
                    CASE WHEN last_synced_date IS NULL THEN 0 ELSE 1 END,
                    last_synced_date NULLS FIRST,
                    store_number
                LIMIT %s;
                """,
                (TARGET_STORE, target_date, max_stores),
            )
        else:
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

    order_type = df["orderType"].fillna("").astype(str).str.lower()

    return df[
        order_type.str.contains("dine") |
        order_type.str.contains("eat")
    ].copy()


def stable_employee_id(store_id, day_str, server_name):
    raw = f"{int(store_id)}|{day_str}|{str(server_name).strip()}".encode("utf-8")
    digest = hashlib.sha256(raw).digest()
    return int.from_bytes(digest[:4], "big") & 0x7FFFFFFF

def transform_checks(df, store_id, day_str):
    if df.empty:
        return pd.DataFrame()

    required = {
        "serverName",
        "checkNumber",
        "netSales",
        "beverageSales",
        "openTime",
        "closeTime",
        "guestCount",
    }
    missing = required - set(df.columns)
    if missing:
        print(f"    skipped, missing columns: {sorted(missing)}")
        return pd.DataFrame()

    df["guestCount"] = pd.to_numeric(df["guestCount"], errors="coerce").fillna(0)
    df["netSales"] = pd.to_numeric(df["netSales"], errors="coerce").fillna(0)

    # Calculate global PPA across ALL segments (Dine-In, Carry-Out, OLO, etc)
    global_grouped = (
        df.groupby("serverName", dropna=False)
        .agg(
            total_sales=("netSales", "sum"),
            total_guest_count=("guestCount", "sum"),
        ).reset_index()
    )
    global_grouped["ppa"] = global_grouped.apply(
        lambda r: (r["total_sales"] / r["total_guest_count"]) if r["total_guest_count"] > 0 else 0.0, axis=1
    )

    # Now filter down strictly to Dine-In for the rest of the metrics (Turn Time, Bev %, Dine-in Sales)
    df = filter_to_true_dine_in(df)
    
    if df.empty:
        return pd.DataFrame()

    df = df.dropna(
        subset=[
            "serverName",
            "checkNumber",
            "netSales",
            "beverageSales",
            "guestCount",
        ]
    ).copy()

    if df.empty:
        return pd.DataFrame()

    if "openTime" in df.columns and "closeTime" in df.columns:
        df["openTimeObj"] = pd.to_datetime(df["openTime"], format="%H:%M:%S", errors="coerce")
        df["closeTimeObj"] = pd.to_datetime(df["closeTime"], format="%H:%M:%S", errors="coerce")
        
        df["turn_time"] = (df["closeTimeObj"] - df["openTimeObj"]).dt.total_seconds() / 60
        df.loc[df["turn_time"] < 0, "turn_time"] += 24 * 60
        
        import numpy as np
        df["turn_time"] = df["turn_time"].apply(lambda x: x if pd.notna(x) and x > 0 else np.nan)
    else:
        df["turn_time"] = float('nan')

    grouped = (
        df.groupby("serverName", dropna=False)
        .agg(
            sales=("netSales", "sum"),
            beverage_sales=("beverageSales", "sum"),
            turn_time=("turn_time", "mean"),
            check_count=("checkNumber", "count"),
            guest_count=("guestCount", "sum"),
        )
        .reset_index()
    )

    if grouped.empty:
        return pd.DataFrame()


    grouped = pd.merge(grouped, global_grouped[["serverName", "ppa"]], on="serverName", how="left")
    grouped["ppa"] = grouped["ppa"].fillna(0.0)

    grouped["beverage_pct"] = (
        (grouped["beverage_sales"] / grouped["sales"])
        .replace([pd.NA, pd.NaT], 0)
        .fillna(0)
        * 100
    )

    grouped["employee_id"] = (
        grouped["serverName"]
        .fillna("")
        .apply(lambda x: stable_employee_id(store_id, day_str, x))
    )
    grouped["employee_name"] = grouped["serverName"]

    return grouped[
        [
            "employee_id",
            "employee_name",
            "ppa",
            "beverage_pct",
            "beverage_sales",
            "turn_time",
            "check_count",
            "guest_count",
            "sales",
        ]
    ]


def build_zero_guest_alerts(df, store_id, day_str):
    if df.empty:
        return pd.DataFrame()

    df = filter_to_true_dine_in(df)
    if df.empty:
        return pd.DataFrame()

    required = {
        "serverName",
        "checkNumber",
        "netSales",
        "beverageSales",
        "guestCount",
    }
    missing = required - set(df.columns)
    if missing:
        return pd.DataFrame()

    work_df = df.dropna(
        subset=[
            "serverName",
            "checkNumber",
            "netSales",
            "beverageSales",
            "guestCount",
        ]
    ).copy()
    if work_df.empty:
        return pd.DataFrame()

    work_df["guestCount"] = pd.to_numeric(work_df["guestCount"], errors="coerce").fillna(0)
    work_df["netSales"] = pd.to_numeric(work_df["netSales"], errors="coerce").fillna(0)
    work_df["beverageSales"] = pd.to_numeric(work_df["beverageSales"], errors="coerce").fillna(0)

    zero_guest_df = work_df[work_df["guestCount"] == 0].copy()
    if zero_guest_df.empty:
        return pd.DataFrame()

    grouped = (
        zero_guest_df.groupby("serverName", dropna=False)
        .agg(
            zero_guest_check_count=("checkNumber", "count"),
            zero_guest_sales=("netSales", "sum"),
            zero_guest_beverage_sales=("beverageSales", "sum"),
        )
        .reset_index()
    )

    grouped["employee_id"] = (
        grouped["serverName"]
        .fillna("")
        .apply(lambda x: stable_employee_id(store_id, day_str, x))
    )
    grouped["employee_name"] = grouped["serverName"]

    return grouped[
        [
            "employee_id",
            "employee_name",
            "zero_guest_check_count",
            "zero_guest_sales",
            "zero_guest_beverage_sales",
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
                    beverage_sales,
                    turn_time,
                    check_count,
                    guest_count,
                    sales,
                    updated_at
                )
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,now())
                ON CONFLICT (store_number, business_date, employee_id)
                DO UPDATE SET
                    employee_name = EXCLUDED.employee_name,
                    ppa = EXCLUDED.ppa,
                    beverage_pct = EXCLUDED.beverage_pct,
                    beverage_sales = EXCLUDED.beverage_sales,
                    turn_time = EXCLUDED.turn_time,
                    check_count = EXCLUDED.check_count,
                    guest_count = EXCLUDED.guest_count,
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
                    float(row["beverage_sales"]),
                    float(row["turn_time"]),
                    float(row["check_count"]),
                    float(row["guest_count"]),
                    float(row["sales"]),
                ),
            )
        conn.commit()
    finally:
        cur.close()


def upsert_zero_guest_rows(conn, alerts_df, store_id, business_date):
    cur = conn.cursor()
    try:
        cur.execute(
            """
            DELETE FROM employee_zero_guest_alerts
            WHERE store_number = %s
              AND business_date = %s;
            """,
            (int(store_id), business_date),
        )

        for _, row in alerts_df.iterrows():
            cur.execute(
                """
                INSERT INTO employee_zero_guest_alerts (
                    store_number,
                    business_date,
                    employee_id,
                    employee_name,
                    zero_guest_check_count,
                    zero_guest_sales,
                    zero_guest_beverage_sales,
                    updated_at
                )
                VALUES (%s,%s,%s,%s,%s,%s,%s,now())
                ON CONFLICT (store_number, business_date, employee_id)
                DO UPDATE SET
                    employee_name = EXCLUDED.employee_name,
                    zero_guest_check_count = EXCLUDED.zero_guest_check_count,
                    zero_guest_sales = EXCLUDED.zero_guest_sales,
                    zero_guest_beverage_sales = EXCLUDED.zero_guest_beverage_sales,
                    updated_at = now();
                """,
                (
                    int(store_id),
                    business_date,
                    int(row["employee_id"]),
                    row["employee_name"],
                    int(row["zero_guest_check_count"]),
                    float(row["zero_guest_sales"]),
                    float(row["zero_guest_beverage_sales"]),
                ),
            )

        conn.commit()
    finally:
        cur.close()


def main():
    print(f"Sync window: {start_date} to {end_date}")
    print(f"Max stores this run: {MAX_STORES_PER_RUN}")
    print(f"Target store: {TARGET_STORE if TARGET_STORE is not None else 'all eligible stores'}")
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
                zero_guest_alerts = build_zero_guest_alerts(df, store_id, day_str)

                if grouped.empty and zero_guest_alerts.empty:
                    print("    no usable grouped rows")
                    latest_synced_for_store = day
                    continue

                if not grouped.empty:
                    upsert_grouped_rows(conn, grouped, store_id, day)
                    inserted = len(grouped)
                    total_rows += inserted
                    print(f"    upserted {inserted} employee rows")
                else:
                    print("    no performance rows, zero-guest alerts only")

                upsert_zero_guest_rows(conn, zero_guest_alerts, store_id, day)
                total_store_days += 1
                latest_synced_for_store = day

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
