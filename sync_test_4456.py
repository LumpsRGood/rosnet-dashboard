from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import psycopg2
import pandas as pd
import api
import os

# DB CONFIG
conn = psycopg2.connect(
    host=os.getenv("DB_HOST"),
    port=os.getenv("DB_PORT"),
    dbname=os.getenv("DB_NAME"),
    user=os.getenv("DB_USER"),
    password=os.getenv("DB_PASSWORD"),
)

STORE_ID = 4456

tz = ZoneInfo("America/New_York")
today = datetime.now(tz).date()
target_date = today - timedelta(days=1)
day_str = target_date.strftime("%Y-%m-%d")

print(f"Testing store {STORE_ID} for {day_str}")

# Get data
emp_map = api.get_employees_map(STORE_ID)
bev_ids = api.get_beverage_category_ids()

checks = api.get_checks(
    day_str,
    day_str,
    STORE_ID,
    emp_map=emp_map,
    bev_cat_ids=bev_ids,
)

df = pd.DataFrame(checks)

if df.empty:
    print("No data returned")
    exit()

# SIMPLE DINE-IN FILTER (NEW LOGIC)
order_type = df["orderType"].fillna("").astype(str).str.lower()
df = df[
    order_type.str.contains("dine") |
    order_type.str.contains("eat")
].copy()
df = df[
    pd.to_numeric(df["guestCount"], errors="coerce").fillna(0) > 0
].copy()

print(f"Rows after filter: {len(df)}")
print("\n=== ORDER TYPE VALUES AFTER FILTER ===")
print(df["orderType"].value_counts(dropna=False))

print("\n=== BEVERAGE SALES ROWS ===")
bev_rows = df[pd.to_numeric(df["beverageSales"], errors="coerce").fillna(0) > 0].copy()

cols_to_show = [
    c for c in [
        "serverName",
        "checkNumber",
        "orderType",
        "guestCount",
        "netSales",
        "beverageSales",
        "openTime",
        "closeTime",
    ] if c in bev_rows.columns
]

print(bev_rows[cols_to_show].sort_values(["serverName", "checkNumber"]).to_string(index=False))
suspect_servers = [
    "SMITH, ALYSON",
    "Lusk, Taylor",
    "Helsel, Colten",
    "Benefield, Shayla",
]

suspect_checks = [
    279372625,  # Benefield, 4.69
    279458643,  # Helsel, 3.49 maybe suspect
    279465347,  # Lusk, 3.49 maybe suspect
    279360062,  # Alyson, 8.18
    279381627,  # Alyson, 13.06
]

detail_cols = [
    c for c in [
        "serverName",
        "checkNumber",
        "itemName",
        "categoryName",
        "groupName",
        "quantity",
        "price",
        "netSales",
        "beverageSales",
        "orderType",
    ] if c in df.columns
]

print("\n=== SUSPECT CHECK DETAIL ===")
print(
    df[df["checkNumber"].isin(suspect_checks)][detail_cols]
    .sort_values(["checkNumber", "itemName"] if "itemName" in detail_cols else ["checkNumber"])
    .to_string(index=False)
)

print("\n=== SUSPECT SERVER BEVERAGE CHECKS ===")
suspect_rows = bev_rows[bev_rows["serverName"].isin(suspect_servers)].copy()

cols_to_show = [
    c for c in [
        "serverName",
        "checkNumber",
        "guestCount",
        "netSales",
        "beverageSales",
        "orderType",
        "openTime",
        "closeTime",
    ] if c in suspect_rows.columns
]

print(
    suspect_rows[cols_to_show]
    .sort_values(["serverName", "beverageSales", "checkNumber"], ascending=[True, False, True])
    .to_string(index=False)
)

# TRANSFORM
df["openTime"] = pd.to_datetime(df["openTime"], format="%H:%M:%S", errors="coerce")
df["closeTime"] = pd.to_datetime(df["closeTime"], format="%H:%M:%S", errors="coerce")

df["turn_time"] = (df["closeTime"] - df["openTime"]).dt.total_seconds() / 60
df = df[df["turn_time"] > 0]

grouped = (
    df.groupby("serverName")
    .agg(
        sales=("netSales", "sum"),
        beverage_sales=("beverageSales", "sum"),
        turn_time=("turn_time", "mean"),
        check_count=("checkNumber", "count"),
    )
    .reset_index()
)
print("\n=== SERVER BEVERAGE TOTALS ===")
print(
    grouped[["serverName", "sales", "beverage_sales"]]
    .sort_values("serverName")
    .to_string(index=False)
)

grouped["ppa"] = grouped["sales"] / grouped["check_count"]
grouped["beverage_pct"] = grouped["beverage_sales"] / grouped["sales"] * 100

print("\n=== RESULTS ===")
print(grouped)

print("\n=== TOTALS ===")
print("Sales:", round(grouped["sales"].sum(), 2))
print("Bev Sales:", round(grouped["beverage_sales"].sum(), 2))
print("Bev %:", round(grouped["beverage_sales"].sum() / grouped["sales"].sum() * 100, 2))
