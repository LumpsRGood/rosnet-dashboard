import io
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import matplotlib.pyplot as plt
import pandas as pd
import psycopg2
import streamlit as st
from matplotlib.patches import Rectangle

APP_VERSION = "v1.8.2"

st.set_page_config(
    page_title="Peachtree Performance Dashboard",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown(
    """
    <style>
    [data-testid="stSidebar"] [data-testid="stSidebarContent"] {
        padding-top: 0.15rem;
    }
    [data-testid="stSidebar"] .block-container {
        padding-top: 0;
    }
    [data-testid="stSidebar"] [data-testid="stImage"] {
        margin-top: -2.1rem;
        margin-bottom: 0.15rem;
        text-align: center;
    }
    [data-testid="stSidebar"] [data-testid="stImage"] img {
        margin-left: auto;
        margin-right: auto;
    }
    </style>
    """,
    unsafe_allow_html=True,
)


# -----------------------------
# DB
# -----------------------------
def get_db_connection():
    return psycopg2.connect(
        host=st.secrets["database"]["host"],
        port=st.secrets["database"]["port"],
        dbname=st.secrets["database"]["dbname"],
        user=st.secrets["database"]["user"],
        password=st.secrets["database"]["password"],
    )


@st.cache_data(ttl=300)
def get_location_map_from_db():
    conn = get_db_connection()
    try:
        df = pd.read_sql(
            """
            SELECT store_number, store_name
            FROM sync_progress
            WHERE store_number IS NOT NULL
              AND store_name IS NOT NULL
            ORDER BY store_number
            """,
            conn,
        )
    finally:
        conn.close()

    if df.empty:
        return {}

    return {
        int(row["store_number"]): row["store_name"]
        for _, row in df.iterrows()
    }


@st.cache_data(ttl=120)
def get_sync_status_summary(target_date):
    conn = get_db_connection()
    try:
        df = pd.read_sql(
            """
            SELECT
                COUNT(*) AS total_stores,
                COUNT(*) FILTER (
                    WHERE last_synced_date >= %s
                ) AS current_stores,
                COUNT(*) FILTER (
                    WHERE COALESCE(last_status, '') NOT IN ('success', 'partial')
                ) AS failed_stores,
                MAX(last_attempted_at) AS last_attempted_at,
                MAX(last_synced_date) FILTER (
                    WHERE COALESCE(last_status, '') IN ('success', 'partial')
                ) AS last_good_business_date
            FROM sync_progress
            """,
            conn,
            params=(target_date,),
        )
    finally:
        conn.close()

    if df.empty:
        return {
            "total": 0,
            "current": 0,
            "behind": 0,
            "failed": 0,
            "last_sync": None,
            "last_good_business_date": None,
        }

    row = df.iloc[0]
    total = int(row["total_stores"] or 0)
    current = int(row["current_stores"] or 0)
    failed = int(row["failed_stores"] or 0)
    behind = max(total - current - failed, 0)

    return {
        "total": total,
        "current": current,
        "behind": behind,
        "failed": failed,
        "last_sync": row["last_attempted_at"],
        "last_good_business_date": row["last_good_business_date"],
    }


@st.cache_data(ttl=120)
def get_sync_status():
    conn = get_db_connection()
    try:
        df = pd.read_sql(
            """
            SELECT
                COUNT(*) AS total_stores,
                COUNT(last_synced_date) AS synced_stores,
                MAX(last_attempted_at) AS last_attempted_at
            FROM sync_progress
            """,
            conn,
        )
    finally:
        conn.close()

    if df.empty:
        return 0, 0, None

    row = df.iloc[0]
    return (
        int(row["total_stores"] or 0),
        int(row["synced_stores"] or 0),
        row["last_attempted_at"],
    )


@st.cache_data(ttl=300)
def get_data_from_db(start_date, end_date, locations=None):
    conn = get_db_connection()
    try:
        if locations:
            locations = [int(x) for x in locations]
            query = """
                SELECT *
                FROM employee_daily_metrics
                WHERE store_number = ANY(%s::bigint[])
                  AND business_date BETWEEN %s AND %s
                ORDER BY business_date, store_number, employee_name
            """
            df = pd.read_sql(query, conn, params=(locations, start_date, end_date))
        else:
            query = """
                SELECT *
                FROM employee_daily_metrics
                WHERE business_date BETWEEN %s AND %s
                ORDER BY business_date, store_number, employee_name
            """
            df = pd.read_sql(query, conn, params=(start_date, end_date))
    finally:
        conn.close()

    return df


@st.cache_data(ttl=300)
def get_zero_guest_alerts_from_db(start_date, end_date, locations=None):
    conn = get_db_connection()
    try:
        if locations:
            locations = [int(x) for x in locations]
            query = """
                SELECT *
                FROM employee_zero_guest_alerts
                WHERE store_number = ANY(%s::bigint[])
                  AND business_date BETWEEN %s AND %s
                ORDER BY business_date, store_number, employee_name
            """
            df = pd.read_sql(query, conn, params=(locations, start_date, end_date))
        else:
            query = """
                SELECT *
                FROM employee_zero_guest_alerts
                WHERE business_date BETWEEN %s AND %s
                ORDER BY business_date, store_number, employee_name
            """
            df = pd.read_sql(query, conn, params=(start_date, end_date))
    except Exception:
        return pd.DataFrame()
    finally:
        conn.close()

    return df


# -----------------------------
# SIDEBAR FRESHNESS
# -----------------------------
def render_freshness_sidebar(target_date):
    summary = get_sync_status_summary(target_date)
    st.sidebar.markdown("---")
    st.sidebar.markdown("### Location Sync")

    if not summary["last_sync"]:
        st.sidebar.info("No sync data yet.")
        return

    try:
        local_tz = ZoneInfo("America/Chicago")
        last_sync_local = pd.to_datetime(summary["last_sync"], utc=True).tz_convert(local_tz)
    except Exception:
        last_sync_local = pd.to_datetime(summary["last_sync"])

    total = summary["total"]
    current = summary["current"]
    behind = summary["behind"]
    failed = summary["failed"]
    last_good_business_date = summary["last_good_business_date"]

    last_good_label = (
        pd.to_datetime(last_good_business_date).strftime("%b %d, %Y")
        if pd.notna(last_good_business_date)
        else "No successful sync yet"
    )

    def render_status_row(label, value):
        left, mid, right = st.sidebar.columns([1.7, 1.8, 0.9])
        left.markdown(f"**{label}**")
        fraction = 0.0 if total <= 0 else float(value) / float(total)
        mid.progress(fraction)
        right.markdown(f"**{value}/{total}**")

    render_status_row("Current", current)
    render_status_row("Behind", behind)
    render_status_row("Failed", failed)

    st.sidebar.markdown("---")
    st.sidebar.caption("LAST GOOD BUSINESS DATE")
    st.sidebar.markdown(f"**{last_good_label}**")
    st.sidebar.caption(f"Last sync attempt: {last_sync_local.strftime('%I:%M %p %Z')}")

# -----------------------------
# DATA PREP
# -----------------------------
def prepare_display_df(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df.copy()

    out = df.copy()

    numeric_cols = [
        "store_number",
        "ppa",
        "beverage_pct",
        "turn_time",
        "check_count",
        "guest_count",
        "sales",
    ]
    for col in numeric_cols:
        if col in out.columns:
            out[col] = pd.to_numeric(out[col], errors="coerce")

    if "business_date" in out.columns:
        out["business_date"] = pd.to_datetime(out["business_date"], errors="coerce").dt.date

    out = out.dropna(
        subset=[
            "store_number",
            "business_date",
            "employee_name",
            "ppa",
            "beverage_pct",
            "turn_time",
            "check_count",
            "guest_count",
            "sales",
        ]
    ).copy()

    out["store_number"] = out["store_number"].astype(int)
    return out


def prepare_zero_guest_alerts_df(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df.copy()

    out = df.copy()

    numeric_cols = [
        "store_number",
        "zero_guest_check_count",
        "zero_guest_sales",
        "zero_guest_beverage_sales",
    ]
    for col in numeric_cols:
        if col in out.columns:
            out[col] = pd.to_numeric(out[col], errors="coerce")

    if "business_date" in out.columns:
        out["business_date"] = pd.to_datetime(out["business_date"], errors="coerce").dt.date

    out = out.dropna(
        subset=[
            "store_number",
            "business_date",
            "employee_name",
            "zero_guest_check_count",
            "zero_guest_sales",
            "zero_guest_beverage_sales",
        ]
    ).copy()

    out["store_number"] = out["store_number"].astype(int)
    out = out[out["zero_guest_check_count"] > 0].copy()
    return out


def weighted_bev_pct(df: pd.DataFrame) -> float:
    if df.empty:
        return 0.0

    total_sales = df["sales"].sum()
    total_bev_sales = df["beverage_sales"].sum()

    return (total_bev_sales / total_sales * 100.0) if total_sales > 0 else 0.0


def aggregate_store_day_ppa(df: pd.DataFrame) -> float:
    if df.empty:
        return 0.0
    store_day = (
        df.groupby(["store_number", "business_date"], as_index=False)
        .agg(ppa=("ppa", "first"))
    )
    return float(store_day["ppa"].mean()) if not store_day.empty else 0.0


def build_server_summary(df: pd.DataFrame, zg_df: pd.DataFrame = None) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame(columns=["employee_name", "turn_time", "beverage_pct", "ppa", "zero_checks"])

    grouped = (
        df.groupby("employee_name", dropna=False)
        .agg(
            turn_time=("turn_time", "mean"),
            sales=("sales", "sum"),
            ppa=("ppa", "mean"),
        )
        .reset_index()
    )

    bev_source = (
        df.groupby("employee_name", dropna=False)
        .agg(beverage_sales=("beverage_sales", "sum"))
        .reset_index()
    )

    grouped = grouped.merge(bev_source, on="employee_name", how="left")
    grouped["beverage_pct"] = grouped.apply(
        lambda r: (r["beverage_sales"] / r["sales"] * 100.0) if r["sales"] > 0 else 0.0,
        axis=1,
    )

    grouped = grouped[["employee_name", "turn_time", "beverage_pct", "ppa"]]
    
    if zg_df is not None and not zg_df.empty:
        zg_agg = zg_df.groupby("employee_name", dropna=False).agg(zero_checks=("zero_guest_check_count", "sum")).reset_index()
        grouped = grouped.merge(zg_agg, on="employee_name", how="left")
        grouped["zero_checks"] = grouped["zero_checks"].fillna(0).astype(int)
    else:
        grouped["zero_checks"] = 0

    return grouped.sort_values("turn_time", ascending=True).reset_index(drop=True)


def build_zero_guest_alert_summary(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame(
            columns=[
                "employee_name",
                "zero_guest_check_count",
                "zero_guest_sales",
                "zero_guest_beverage_sales",
                "flagged_days",
                "date_range",
                "trend",
            ]
        )

    grouped = (
        df.groupby("employee_name", dropna=False)
        .agg(
            zero_guest_check_count=("zero_guest_check_count", "sum"),
            zero_guest_sales=("zero_guest_sales", "sum"),
            zero_guest_beverage_sales=("zero_guest_beverage_sales", "sum"),
            flagged_days=("business_date", "nunique"),
            first_flagged_date=("business_date", "min"),
            last_flagged_date=("business_date", "max"),
        )
        .reset_index()
    )

    def format_date_range(row):
        first_date = row["first_flagged_date"]
        last_date = row["last_flagged_date"]
        if pd.isna(first_date) or pd.isna(last_date):
            return ""
        if first_date == last_date:
            return pd.to_datetime(first_date).strftime("%b %d, %Y")
        return (
            f"{pd.to_datetime(first_date).strftime('%b %d, %Y')} - "
            f"{pd.to_datetime(last_date).strftime('%b %d, %Y')}"
        )

    def format_trend(row):
        flagged_days = int(row["flagged_days"])
        if flagged_days <= 1:
            return "Single day"
        return f"Repeated over {flagged_days} days"

    grouped["date_range"] = grouped.apply(format_date_range, axis=1)
    grouped["trend"] = grouped.apply(format_trend, axis=1)

    grouped = grouped.sort_values(
        ["flagged_days", "zero_guest_check_count", "zero_guest_sales", "employee_name"],
        ascending=[False, False, False, True],
    ).reset_index(drop=True)

    return grouped


def build_location_summary(df: pd.DataFrame, loc_map: dict) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame(columns=["Location", "Turn Time", "Bev %", "PPA"])

    grouped = (
        df.groupby("store_number", dropna=False)
        .agg(
            turn_time=("turn_time", "mean"),
            sales=("sales", "sum"),
        )
        .reset_index()
    )

    bev_source = (
        df.groupby("store_number", dropna=False)
        .agg(beverage_sales=("beverage_sales", "sum"))
        .reset_index()
    )

    ppa_source = (
        df.groupby(["store_number", "business_date"], dropna=False)
        .agg(ppa=("ppa", "first"))
        .reset_index()
        .groupby("store_number", dropna=False)
        .agg(ppa=("ppa", "mean"))
        .reset_index()
    )

    grouped = grouped.merge(bev_source, on="store_number", how="left")
    grouped = grouped.merge(ppa_source, on="store_number", how="left")

    grouped["beverage_pct"] = grouped.apply(
        lambda r: (r["beverage_sales"] / r["sales"] * 100.0) if r["sales"] > 0 else 0.0,
        axis=1,
    )
    grouped["Location"] = grouped["store_number"].apply(
        lambda x: f"{x} - {loc_map.get(int(x), 'Unknown')}"
    )

    out = grouped.rename(
        columns={
            "turn_time": "Turn Time",
            "beverage_pct": "Bev %",
            "ppa": "PPA",
        }
    )[["Location", "Turn Time", "Bev %", "PPA"]]

    return out.sort_values("PPA", ascending=False).reset_index(drop=True)


# -----------------------------
# STYLING
# -----------------------------
def format_ppa_status(ppa: float):
    if ppa >= 21:
        return "#21c55e", "#13281c"
    if ppa >= 20:
        return "#eab308", "#2e270f"
    return "#ef4444", "#321717"


def style_location_summary(df: pd.DataFrame):
    def color_turn(v):
        if pd.isna(v):
            return ""
        if v <= 40:
            return "background-color: #6fd08c; color: #111827;"
        if v <= 45:
            return "background-color: #f0d766; color: #111827;"
        return "background-color: #f8696b; color: white;"

    def color_bev(v):
        if pd.isna(v):
            return ""
        if v >= 19:
            return "background-color: #6fd08c; color: #111827;"
        if v >= 18:
            return "background-color: #f0d766; color: #111827;"
        return "background-color: #f8696b; color: white;"

    def color_ppa(v):
        if pd.isna(v):
            return ""
        if v >= 21:
            return "background-color: #6fd08c; color: #111827;"
        if v >= 20:
            return "background-color: #f0d766; color: #111827;"
        return "background-color: #f8696b; color: white;"

    styler = df.style.format(
        {
            "Turn Time": "{:.2f}",
            "Bev %": "{:.2f}%",
            "PPA": "${:.2f}",
        }
    )
    styler = styler.map(color_turn, subset=["Turn Time"])
    styler = styler.map(color_bev, subset=["Bev %"])
    styler = styler.map(color_ppa, subset=["PPA"])
    return styler


def style_server_summary(df: pd.DataFrame):
    def color_turn(v):
        if pd.isna(v):
            return ""
        if v <= 40:
            return "background-color: #6fd08c; color: #111827;"
        if v <= 45:
            return "background-color: #f0d766; color: #111827;"
        return "background-color: #f8696b; color: white;"

    def color_bev(v):
        if pd.isna(v):
            return ""
        if v >= 19:
            return "background-color: #6fd08c; color: #111827;"
        if v >= 18:
            return "background-color: #f0d766; color: #111827;"
        return "background-color: #f8696b; color: white;"

    def color_ppa(v):
        if pd.isna(v):
            return ""
        if v >= 21:
            return "background-color: #6fd08c; color: #111827;"
        if v >= 20:
            return "background-color: #f0d766; color: #111827;"
        return "background-color: #f8696b; color: white;"

    def color_zero_checks(v):
        if pd.isna(v) or v == 0:
            return "color: #4b5563;"
        return "background-color: #f8696b; color: white; font-weight: bold;"

    display_df = df.rename(
        columns={
            "employee_name": "Server",
            "turn_time": "Turn Time",
            "beverage_pct": "Dine In Bev %",
            "ppa": "PPA",
            "zero_checks": "Ghost Checks"
        }
    )

    styler = display_df.style.format(
        {
            "Turn Time": "{:.2f}",
            "Dine In Bev %": "{:.2f}%",
            "PPA": "${:.2f}",
            "Ghost Checks": "{:.0f}"
        }
    )
    styler = styler.map(color_turn, subset=["Turn Time"])
    styler = styler.map(color_bev, subset=["Dine In Bev %"])
    styler = styler.map(color_ppa, subset=["PPA"])
    styler = styler.map(color_zero_checks, subset=["Ghost Checks"])
    return styler


def style_zero_guest_alert_summary(df: pd.DataFrame):
    display_df = df.rename(
        columns={
            "employee_name": "Server",
            "date_range": "Occurrence Date(s)",
            "flagged_days": "Days Flagged",
            "trend": "Trend",
            "zero_guest_check_count": "Zero-Guest Checks",
            "zero_guest_sales": "Sales Attached",
            "zero_guest_beverage_sales": "Bev Sales Attached",
        }
    )

    styler = display_df.style.format(
        {
            "Occurrence Date(s)": "{}",
            "Days Flagged": "{:.0f}",
            "Trend": "{}",
            "Zero-Guest Checks": "{:.0f}",
            "Sales Attached": "${:.2f}",
            "Bev Sales Attached": "${:.2f}",
        }
    )
    return styler


def render_zero_guest_alert_box(df: pd.DataFrame):
    if df.empty:
        return

    total_checks = int(df["zero_guest_check_count"].sum())
    total_sales = float(df["zero_guest_sales"].sum())
    total_bev = float(df["zero_guest_beverage_sales"].sum())

    st.warning(
        f"Zero-guest checks detected: {total_checks} check(s) with ${total_sales:.2f} in sales "
        f"and ${total_bev:.2f} in beverage sales. Review for possible missed cover entry."
    )
    st.dataframe(
        style_zero_guest_alert_summary(df),
        use_container_width=True,
        height=min(280, 45 + len(df) * 35),
    )


# -----------------------------
# KPI CARDS
# -----------------------------
def render_kpi_cards(df: pd.DataFrame, header_label: str):
    st.markdown(f"## {header_label}")

    avg_turn = df["turn_time"].mean() if not df.empty else 0.0
    avg_bev = weighted_bev_pct(df)
    ppa = aggregate_store_day_ppa(df)

    server_summary = build_server_summary(df)
    total_servers = len(server_summary)

    all_green = server_summary[
        (server_summary["turn_time"] <= 40) &
        (server_summary["beverage_pct"] >= 19)
    ]
    all_green_count = len(all_green)

    best_turn = server_summary.loc[server_summary["turn_time"].idxmin(), "employee_name"] if total_servers else "N/A"
    slowest_turn = server_summary.loc[server_summary["turn_time"].idxmax(), "employee_name"] if total_servers else "N/A"
    top_bev = server_summary.loc[server_summary["beverage_pct"].idxmax(), "employee_name"] if total_servers else "N/A"
    bottom_bev = server_summary.loc[server_summary["beverage_pct"].idxmin(), "employee_name"] if total_servers else "N/A"
    top_ppa = server_summary.loc[server_summary["ppa"].idxmax(), "employee_name"] if total_servers else "N/A"
    bottom_ppa = server_summary.loc[server_summary["ppa"].idxmin(), "employee_name"] if total_servers else "N/A"

    def safe_name(text, max_len=24):
        text = str(text)
        return text if len(text) <= max_len else text[: max_len - 3] + "..."

    best_turn = safe_name(best_turn)
    slowest_turn = safe_name(slowest_turn)
    top_bev = safe_name(top_bev)
    bottom_bev = safe_name(bottom_bev)
    top_ppa = safe_name(top_ppa)
    bottom_ppa = safe_name(bottom_ppa)

    ppa_border, ppa_bg = format_ppa_status(ppa)

    cols = st.columns(4)

    card_style_base = """
        border-radius:18px;
        padding:24px;
        min-height:250px;
        height:250px;
        display:flex;
        flex-direction:column;
        justify-content:space-between;
        overflow:hidden;
    """
    label_style = "font-size:14px; font-weight:700; letter-spacing:1px;"
    value_style = "font-size:46px; font-weight:800; color:white; line-height:1.0; margin-top:10px;"
    detail_style = "font-size:16px; color:white; line-height:1.35; min-height:58px;"

    with cols[0]:
        st.markdown(
            f"""
            <div style="border:1px solid #8a6d1f; background:#2f2918; {card_style_base}">
                <div style="color:#f0b90b; {label_style}">TURN (DINE-IN)</div>
                <div style="{value_style}">{avg_turn:.2f}</div>
                <div style="{detail_style}">
                    <div>Best: <b>{best_turn}</b></div>
                    <div>Slow: <b>{slowest_turn}</b></div>
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    with cols[1]:
        st.markdown(
            f"""
            <div style="border:1px solid #8d2b2b; background:#35191d; {card_style_base}">
                <div style="color:#ff4b4b; {label_style}">BEV % (DINE-IN)</div>
                <div style="{value_style}">{avg_bev:.2f}%</div>
                <div style="{detail_style}">
                    <div>Top: <b>{top_bev}</b></div>
                    <div>Bot: <b>{bottom_bev}</b></div>
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    with cols[2]:
        st.markdown(
            f"""
            <div style="border:1px solid {ppa_border}; background:{ppa_bg}; {card_style_base}">
                <div style="color:{ppa_border}; {label_style}">PPA (ALL)</div>
                <div style="{value_style}">${ppa:.2f}</div>
                <div style="{detail_style}">
                    <div>Top: <b>{top_ppa}</b></div>
                    <div>Bot: <b>{bottom_ppa}</b></div>
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    with cols[3]:
        st.markdown(
            f"""
            <div style="border:1px solid #7c3aed; background:#24163d; {card_style_base}">
                <div style="color:#a855f7; {label_style}">ALL-GREEN (DINE-IN)</div>
                <div style="{value_style}">{all_green_count} of {total_servers}</div>
                <div style="{detail_style}">
                    <div>Turn ≤40m & Bev ≥19%</div>
                    <div>&nbsp;</div>
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )


# -----------------------------
# WHATSAPP EXPORT
# -----------------------------
def build_whatsapp_png(title: str, subtitle: str, raw_df: pd.DataFrame) -> bytes:
    server_df = build_server_summary(raw_df).copy()

    avg_turn = raw_df["turn_time"].mean() if not raw_df.empty else 0.0
    avg_bev = weighted_bev_pct(raw_df)
    avg_ppa = aggregate_store_day_ppa(raw_df)

    all_green = server_df[
        (server_df["turn_time"] <= 40) &
        (server_df["beverage_pct"] >= 19)
    ]
    all_green_count = len(all_green)
    total_servers = len(server_df)

    best_turn = server_df.loc[server_df["turn_time"].idxmin(), "employee_name"] if total_servers else "N/A"
    slowest_turn = server_df.loc[server_df["turn_time"].idxmax(), "employee_name"] if total_servers else "N/A"
    top_bev = server_df.loc[server_df["beverage_pct"].idxmax(), "employee_name"] if total_servers else "N/A"
    bottom_bev = server_df.loc[server_df["beverage_pct"].idxmin(), "employee_name"] if total_servers else "N/A"
    top_ppa = server_df.loc[server_df["ppa"].idxmax(), "employee_name"] if total_servers else "N/A"
    bottom_ppa = server_df.loc[server_df["ppa"].idxmin(), "employee_name"] if total_servers else "N/A"

    display_df = server_df.rename(
        columns={
            "employee_name": "Server",
            "turn_time": "Turn Time",
            "beverage_pct": "Dine In Bev %",
            "ppa": "PPA",
        }
    ).copy()

    rows = len(display_df)
    fig_height = max(11, 4.0 + rows * 0.38)

    fig, ax = plt.subplots(figsize=(8.3, fig_height), dpi=200)
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")
    fig.patch.set_facecolor("#f3f4f6")

    ax.add_patch(Rectangle((0.015, 0.015), 0.97, 0.97, facecolor="#f3f4f6", edgecolor="#d1d5db", linewidth=1.2))

    ax.add_patch(Rectangle((0.02, 0.88), 0.96, 0.1, facecolor="#2c5aa0", edgecolor="#2c5aa0"))
    ax.text(0.04, 0.94, title, fontsize=22, fontweight="bold", color="white", va="center")
    ax.text(0.04, 0.905, subtitle, fontsize=13, color="#dbeafe", va="center", style="italic")

    card_y = 0.62
    card_h = 0.18
    card_w = 0.22
    x_positions = [0.04, 0.28, 0.52, 0.76]

    ppa_fill = "#6fd08c" if avg_ppa >= 21 else "#f0d766" if avg_ppa >= 20 else "#f8696b"

    card_specs = [
        ("TURN (DINE-IN)", f"{avg_turn:.2f}", f"Best: {best_turn}", f"Slow: {slowest_turn}", "#6fd08c"),
        ("BEV % (DINE-IN)", f"{avg_bev:.2f}%", f"Top: {top_bev}", f"Bot: {bottom_bev}", "#f0d766"),
        ("PPA (ALL)", f"${avg_ppa:.2f}", f"Top: {top_ppa}", f"Bot: {bottom_ppa}", ppa_fill),
        ("ALL-GREEN (DINE-IN)", f"{all_green_count} of {total_servers}", "Turn ≤40m & Bev ≥19%", "", "#b160f0"),
    ]

    for i, (label, value, line1, line2, color) in enumerate(card_specs):
        ax.add_patch(Rectangle((x_positions[i], card_y), card_w, card_h, facecolor=color, edgecolor="#cbd5e1", linewidth=1.2))
        ax.text(x_positions[i] + 0.012, card_y + 0.15, label, fontsize=10, fontweight="bold", color="#111827", va="center")
        ax.text(x_positions[i] + 0.012, card_y + 0.10, value, fontsize=21, fontweight="bold", color="#111827", va="center")
        ax.text(x_positions[i] + 0.012, card_y + 0.055, line1, fontsize=9.5, color="#111827", va="center")
        if line2:
            ax.text(x_positions[i] + 0.012, card_y + 0.02, line2, fontsize=9.5, color="#111827", va="center")

    display_df["Turn Time"] = display_df["Turn Time"].map(lambda x: f"{x:.2f}")
    display_df["Dine In Bev %"] = display_df["Dine In Bev %"].map(lambda x: f"{x:.2f}%")
    display_df["PPA"] = display_df["PPA"].map(lambda x: f"${x:.2f}")

    table_top = 0.58
    table_left = 0.04
    table_width = 0.92
    row_h = 0.033
    header_h = 0.04

    cols = ["Server", "Turn Time", "Dine In Bev %", "PPA"]
    col_widths = [0.42, 0.18, 0.22, 0.10]

    y = table_top
    x = table_left
    for col, w in zip(cols, col_widths):
        ax.add_patch(Rectangle((x, y - header_h), w * table_width, header_h, facecolor="#3b73b9", edgecolor="white", linewidth=1.0))
        ax.text(x + (w * table_width) / 2, y - header_h / 2, col, fontsize=11, fontweight="bold", color="white", ha="center", va="center")
        x += w * table_width

    def turn_fill(v):
        v = float(v)
        if v <= 40:
            return "#6fd08c", "#111827"
        if v <= 45:
            return "#f0d766", "#111827"
        return "#f8696b", "white"

    def bev_fill(v):
        v = float(str(v).replace("%", ""))
        if v >= 19:
            return "#6fd08c", "#111827"
        if v >= 18:
            return "#f0d766", "#111827"
        return "#f8696b", "white"

    def ppa_fill_func(v):
        v = float(str(v).replace("$", ""))
        if v >= 21:
            return "#6fd08c", "#111827"
        if v >= 20:
            return "#f0d766", "#111827"
        return "#f8696b", "white"

    y = table_top - header_h
    for _, row in display_df.iterrows():
        y -= row_h
        x = table_left

        ax.add_patch(Rectangle((x, y), col_widths[0] * table_width, row_h, facecolor="#f9fafb", edgecolor="#d1d5db", linewidth=1.0))
        ax.text(x + 0.01, y + row_h / 2, row["Server"], fontsize=10, color="#111827", ha="left", va="center")
        x += col_widths[0] * table_width

        fill, text_color = turn_fill(row["Turn Time"])
        ax.add_patch(Rectangle((x, y), col_widths[1] * table_width, row_h, facecolor=fill, edgecolor="#d1d5db", linewidth=1.0))
        ax.text(x + 0.01, y + row_h / 2, row["Turn Time"], fontsize=10, fontweight="bold", color=text_color, ha="left", va="center")
        x += col_widths[1] * table_width

        fill, text_color = bev_fill(row["Dine In Bev %"])
        ax.add_patch(Rectangle((x, y), col_widths[2] * table_width, row_h, facecolor=fill, edgecolor="#d1d5db", linewidth=1.0))
        ax.text(x + 0.01, y + row_h / 2, row["Dine In Bev %"], fontsize=10, fontweight="bold", color=text_color, ha="left", va="center")
        x += col_widths[2] * table_width

        fill, text_color = ppa_fill_func(row["PPA"])
        ax.add_patch(Rectangle((x, y), col_widths[3] * table_width, row_h, facecolor=fill, edgecolor="#d1d5db", linewidth=1.0))
        ax.text(x + 0.01, y + row_h / 2, row["PPA"], fontsize=10, fontweight="bold", color=text_color, ha="left", va="center")

    buf = io.BytesIO()
    plt.tight_layout()
    plt.savefig(buf, format="png", bbox_inches="tight", facecolor=fig.get_facecolor())
    plt.close(fig)
    buf.seek(0)
    return buf.getvalue()


# -----------------------------
# SIDEBAR
# -----------------------------
logo_left, logo_mid, logo_right = st.sidebar.columns([1, 1.6, 1])
with logo_mid:
    try:
        st.image("logo.png", width=120)
    except Exception:
        pass

st.sidebar.caption("Peachtree Partners Data Analysis")
st.sidebar.caption(APP_VERSION)

try:
    tz = ZoneInfo("America/New_York")
    today = datetime.now(tz).date()
except Exception:
    today = datetime.now().date()

yesterday = today - timedelta(days=1)

with st.spinner("Loading Locations..."):
    try:
        loc_map = get_location_map_from_db()
    except Exception as e:
        st.sidebar.error(f"Could not load locations from database: {e}")
        st.stop()

location_options = list(loc_map.keys()) if loc_map else []

selected_locations = st.sidebar.multiselect(
    "Choose Your Location(s)",
    options=location_options,
    format_func=lambda x: f"{x} - {loc_map.get(x, 'Unknown')}",
    default=[],
)

date_method = st.sidebar.radio(
    "Choose Your Timeframe",
    ["Yesterday", "WTD", "MTD", "Custom"],
    horizontal=True,
)

if date_method == "Yesterday":
    start_date = end_date = yesterday
elif date_method == "WTD":
    start_date = today - timedelta(days=today.weekday())
    end_date = yesterday
    if start_date > end_date:
        start_date = end_date
elif date_method == "MTD":
    start_date = today.replace(day=1)
    end_date = yesterday
    if start_date > end_date:
        start_date = end_date
else:
    date_range = st.sidebar.date_input(
        "Custom Date Range",
        value=(yesterday - timedelta(days=6), yesterday),
        max_value=yesterday,
    )

    if isinstance(date_range, tuple) and len(date_range) == 2:
        start_date, end_date = date_range
    elif isinstance(date_range, tuple) and len(date_range) == 1:
        start_date = end_date = date_range[0]
    else:
        start_date = end_date = date_range

    if start_date >= today or end_date >= today:
        st.sidebar.error("Real-time data is unavailable. Please adjust the Custom Date Range.")
        st.stop()

st.sidebar.caption(
    f"Selected: {start_date.strftime('%b %d, %Y')} to {end_date.strftime('%b %d, %Y')}"
)

render_freshness_sidebar(end_date)


# -----------------------------
# MAIN DATA LOAD
# -----------------------------
st.title("*Almost* Live Rosnet Turn and Beverage Data 📈")
st.warning(
    "🚧 **Under Development:** This dashboard is currently in active testing. Errors may occasionally occur. Please contact **Chad** with any issues, feedback, or UI suggestions."
)

active_locations = selected_locations if selected_locations else None
header_label = "MARKET TOTAL" if selected_locations else "COMPANY TOTAL"

with st.spinner("Loading stored Rosnet data..."):
    try:
        raw_df = get_data_from_db(start_date, end_date, active_locations)
    except Exception as e:
        st.error(f"Error fetching data from database: {e}")
        st.stop()

    zero_guest_raw_df = get_zero_guest_alerts_from_db(start_date, end_date, active_locations)

if raw_df.empty:
    if selected_locations:
        st.warning(
            f"No data found for {start_date.strftime('%b %d, %Y')} to {end_date.strftime('%b %d, %Y')} "
            f"for the selected location(s)."
        )
    else:
        st.info(
            "No company data is loaded yet for the selected timeframe. "
            "Run the sync to populate historical data, then the COMPANY TOTAL view will appear automatically."
        )
    st.stop()

df = prepare_display_df(raw_df)
zero_guest_df = prepare_zero_guest_alerts_df(zero_guest_raw_df)

if df.empty:
    st.warning("Data was returned, but none of it matched the fields required by the dashboard.")
    st.stop()


# -----------------------------
# TABS
# -----------------------------
tab1, tab2, tab3, tab4, tab5, tab6 = st.tabs([
    "📊 Overview",
    "👨‍🍳 Server Performance",
    "⚠️ Audit & Compliance",
    "🚀 Coming Attractions",
    "🆘 Known Gremlins",
    "🧾 Dataset"
])

with tab1:
    render_kpi_cards(df, header_label=header_label)

    st.markdown("---")
    st.markdown("### Location Breakdown")

    summary_df = build_location_summary(df, loc_map)
    st.dataframe(
        style_location_summary(summary_df),
        use_container_width=True,
        height=500,
    )

with tab2:
    if not selected_locations:
        st.info("Select one or more locations from the main page to view server performance.")
    else:
        render_kpi_cards(df, header_label="MARKET TOTAL")

        unique_locs = sorted(df["store_number"].dropna().astype(int).unique())

        for loc in unique_locs:
            loc_df = df[df["store_number"] == int(loc)].copy()
            if loc_df.empty:
                continue

            store_name = loc_map.get(int(loc), str(loc))

            st.markdown("---")
            st.markdown(f"### 📍 {store_name}")

            render_kpi_cards(loc_df, header_label="STORE TOTAL")

            server_df = build_server_summary(loc_df, zero_guest_df[zero_guest_df["store_number"] == int(loc)])
            loc_zero_guest_df = build_zero_guest_alert_summary(
                zero_guest_df[zero_guest_df["store_number"] == int(loc)].copy()
            )

            if not loc_zero_guest_df.empty:
                render_zero_guest_alert_box(loc_zero_guest_df)

            st.dataframe(
                style_server_summary(server_df),
                use_container_width=True,
                height=min(500, 45 + len(server_df) * 35),
            )

            png_bytes = build_whatsapp_png(
                store_name,
                f"{start_date.strftime('%b %d, %Y')} to {end_date.strftime('%b %d, %Y')}",
                loc_df,
            )
            st.download_button(
                f"Download {store_name} WhatsApp Image",
                data=png_bytes,
                file_name=f"{store_name.lower().replace(' ', '_')}_whatsapp.png",
                mime="image/png",
                key=f"png_{loc}",
            )

with tab3:
    st.markdown("## ⚠️ Audit & Compliance")
    st.markdown("The Manager's Coaching Sandbox. Track adherence and catch bad habits before they ruin your data.")
    
    st.markdown("### Coming Soon to this Tab:")
    
    def coming_soon_card(title, description):
        return f"""
        <div style="border:1px solid #475569; border-radius:12px; padding:20px; background:rgba(255,255,255,0.03); margin-bottom:15px;">
            <div style="font-size:16px; font-weight:700; color:#f8fafc; margin-bottom:6px;">{title}</div>
            <div style="font-size:14px; color:#94a3b8; line-height:1.5;">{description}</div>
        </div>
        """
    
    st.markdown(coming_soon_card("📈 30-Day Ghost Check Trends", "A line graph showing exactly how many 0-cover checks your team is ringing up over the month so you can spot failing habits visually."), unsafe_allow_html=True)
    st.markdown(coming_soon_card("🚨 Repeat Offender Leaderboard", "A dedicated ranking of which servers are artificially inflating their PPA the most, measuring the exact dollar amount of 'invisible PPA' they are causing."), unsafe_allow_html=True)
    st.markdown(coming_soon_card("⏱️ 'Campers' & Phantom Tables", "Alerts for checks that stay open suspiciously long (or short) to catch unclosed tickets messing up your Turn Time metric."), unsafe_allow_html=True)

with tab4:
    st.markdown("## 🚀 Coming Attractions")
    st.markdown("What’s cooking behind the scenes...")

    def status_tag(label):
        colors = {
            "LIVE": "#22c55e",
            "IN PROGRESS": "#f59e0b",
            "PLANNED": "#64748b",
        }
        color = colors.get(label, "#64748b")
        return f"""
        <span style="
            background:{color};
            color:white;
            padding:3px 10px;
            border-radius:999px;
            font-size:11px;
            font-weight:600;
            margin-left:8px;
        ">
            {label}
        </span>
        """

    def roadmap_card(title, items, color):
        rows = ""
        for text, status, live_note in items:
            extra = ""
            if status == "LIVE" and live_note:
                extra = f'<div style="color:#22c55e; font-size:12px; margin-top:2px;">{live_note}</div>'
            rows += f"<li style='margin-bottom:10px;'>{text} {status_tag(status)}{extra}</li>"

        return f"""
        <div style="
            border:1px solid {color};
            border-radius:18px;
            padding:22px;
            background:rgba(255,255,255,0.02);
            min-height:230px;
        ">
            <div style="color:{color}; font-size:16px; font-weight:700; margin-bottom:12px;">
                {title}
            </div>
            <ul style="margin-left:18px; line-height:1.7;">
                {rows}
            </ul>
        </div>
        """

    col1, col2 = st.columns(2)

    with col1:
        st.markdown(
            roadmap_card(
                "🔥 Next Up",
                [
                    ("+/- vs Previous Period", "IN PROGRESS", ""),
                    ("Trend Indicators (↑ ↓)", "PLANNED", ""),
                    ("Top & Bottom Movers", "PLANNED", ""),
                    ("Enhanced WhatsApp Exports", "LIVE", "Now includes visual scorecards (v1.8.2)"),
                ],
                "#22c55e",
            ),
            unsafe_allow_html=True,
        )

    with col2:
        st.markdown(
            roadmap_card(
                "🧠 Smarter Insights",
                [
                    ("Ghost Check Tracking", "LIVE", "Deployed in Audit Sandbox (v1.8.2)"),
                    ("Coaching Callouts", "PLANNED", ""),
                    ("Highlight Underperformers", "PLANNED", ""),
                    ("Server Search & Filters", "PLANNED", ""),
                    ("Minimum Check Threshold", "PLANNED", ""),
                ],
                "#f59e0b",
            ),
            unsafe_allow_html=True,
        )

    st.markdown("")

    col3, col4 = st.columns(2)

    with col3:
        st.markdown(
            roadmap_card(
                "📈 Data Evolution",
                [
                    ("PPA True Accuracy", "LIVE", "All segments tracked correctly (v1.8.2)"),
                    ("WTD / MTD Comparisons", "PLANNED", ""),
                    ("Store Rank Movement", "PLANNED", ""),
                    ("Historical Trends", "PLANNED", ""),
                    ("LY Comparisons", "PLANNED", ""),
                ],
                "#3b82f6",
            ),
            unsafe_allow_html=True,
        )

    with col4:
        st.markdown(
            roadmap_card(
                "⚙️ System & Backend",
                [
                    ("Sync Freshness Indicator", "LIVE", "Live as of v1.7.0"),
                    ("Store Sync Coverage", "PLANNED", ""),
                    ("Admin Diagnostics View", "PLANNED", ""),
                    ("Data Quality Safeguards", "PLANNED", ""),
                ],
                "#a855f7",
            ),
            unsafe_allow_html=True,
        )

    st.markdown("---")
    st.markdown(
        "<center><i>This dashboard is evolving fast. Every update is built to drive One More Visit.</i></center>",
        unsafe_allow_html=True,
    )

with tab5:
    st.markdown("## 🆘 Known Gremlins")
    st.markdown("Tracking known issues, accuracy gaps, and active fixes in real time.")

    def status_tag(label):
        colors = {
            "OPEN": "#ef4444",
            "IN PROGRESS": "#f59e0b",
            "MITIGATED": "#3b82f6",
            "RESOLVED": "#22c55e",
        }
        color = colors.get(label, "#64748b")
        return f"""
        <span style="
            background:{color};
            color:white;
            padding:3px 10px;
            border-radius:999px;
            font-size:11px;
            font-weight:600;
            margin-left:8px;
        ">
            {label}
        </span>
        """

    def issue_card(title, items, color):
        rows = ""
        for item, status, note in items:
            note_html = f"<div style='font-size:12px; color:#9ca3af; margin-top:4px;'>{note}</div>" if note else ""
            rows += f"<li style='margin-bottom:12px;'>{item} {status_tag(status)}{note_html}</li>"

        return f"""
        <div style="
            border:1px solid {color};
            border-radius:18px;
            padding:22px;
            background:rgba(255,255,255,0.02);
            min-height:260px;
        ">
            <div style="color:{color}; font-size:16px; font-weight:700; margin-bottom:12px;">
                {title}
            </div>
            <ul style="margin-left:18px; line-height:1.7;">
                {rows}
            </ul>
        </div>
        """

    col1, col2 = st.columns(2)

    with col1:
        st.markdown(
            issue_card(
                "📡 Data Integrity",
                [
                    (
                        "Timezone-based sync misalignment",
                        "IN PROGRESS",
                        "Stores sync before local 4AM closeout in some regions"
                    ),
                    (
                        "Dine-in Beverage % accuracy",
                        "RESOLVED",
                        "Now isolated strictly to dine-in receipts (v1.8.2)"
                    ),
                    (
                        "PPA calculation consistency",
                        "RESOLVED",
                        "True global PPA across all checks now live (v1.8.2)"
                    ),
                ],
                "#ef4444",
            ),
            unsafe_allow_html=True,
        )

    with col2:
        st.markdown(
            issue_card(
                "⚙️ System Behavior",
                [
                    (
                        "API rate limit pressure",
                        "MITIGATED",
                        "Location calls moved to DB instead of Rosnet API"
                    ),
                    (
                        "Sync completeness visibility",
                        "IN PROGRESS",
                        "Freshness indicator added, expanding to store-level diagnostics"
                    ),
                    (
                        "Cross-day aggregation edge cases",
                        "OPEN",
                        "Multi-day averaging may distort performance signals"
                    ),
                ],
                "#f59e0b",
            ),
            unsafe_allow_html=True,
        )

    st.markdown("")

    col3, col4 = st.columns(2)

    with col3:
        st.markdown(
            issue_card(
                "📊 Reporting Gaps",
                [
                    (
                        "No +/- previous period comparison",
                        "OPEN",
                        "Limits coaching context and trend visibility"
                    ),
                    (
                        "No trend direction indicators",
                        "OPEN",
                        "No quick visual for improving vs declining performance"
                    ),
                    (
                        "Limited filtering (server/store thresholds)",
                        "PLANNED",
                        "Will allow better focus on actionable data"
                    ),
                ],
                "#3b82f6",
            ),
            unsafe_allow_html=True,
        )

    with col4:
        st.markdown(
            issue_card(
                "🧠 Coaching & Insights",
                [
                    (
                        "No automated coaching callouts",
                        "PLANNED",
                        "System does not yet identify top opportunities or risks"
                    ),
                    (
                        "All-green logic not fully leveraged",
                        "PLANNED",
                        "Opportunity to highlight elite performers more clearly"
                    ),
                    (
                        "No anomaly detection",
                        "PLANNED",
                        "Spikes/drops not flagged automatically"
                    ),
                ],
                "#a855f7",
            ),
            unsafe_allow_html=True,
        )

    st.markdown("---")
    st.markdown(
        "<center><i>We don’t hide issues. We surface them, own them, and fix them.</i></center>",
        unsafe_allow_html=True,
    )

with tab6:
    st.markdown("### Combined Stored Dataset")
    st.markdown("Raw diagnostic data view. This tab will be removed in a future update.")
    st.dataframe(df, use_container_width=True, height=600)

st.markdown(
    f"<br><hr><center><small>Powered by Rosnet Sync + Supabase | {APP_VERSION}</small></center>",
    unsafe_allow_html=True,
)
