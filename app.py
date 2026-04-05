import io
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import matplotlib.pyplot as plt
import pandas as pd
import psycopg2
import streamlit as st
from matplotlib.patches import Rectangle

import api

st.set_page_config(
    page_title="Rosnet Insights Dashboard",
    page_icon="🍔",
    layout="wide",
    initial_sidebar_state="expanded",
)

APP_VERSION = "v1.6.0"


@st.dialog("Data Availability")
def show_realtime_warning():
    st.warning("Real-time data is not available.")
    st.write(
        "This data is historical only. Please change your date selection in the sidebar to a range ending yesterday or earlier."
    )


# -----------------------------
# DB helpers
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


@st.cache_data(ttl=3600)
def get_location_map_from_api():
    raw_locations = api.get_locations()
    loc_map = {}
    if isinstance(raw_locations, list):
        for loc in raw_locations:
            if not loc:
                continue
            l_id = loc.get("Id", loc.get("id"))
            l_name = loc.get("Name", loc.get("name", "Unknown"))
            if l_id is not None:
                loc_map[int(l_id)] = l_name
    return loc_map


def prepare_display_df(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df.copy()

    out = df.copy()

    numeric_cols = ["store_number", "ppa", "beverage_pct", "turn_time", "check_count", "sales"]
    for col in numeric_cols:
        if col in out.columns:
            out[col] = pd.to_numeric(out[col], errors="coerce")

    if "business_date" in out.columns:
        out["business_date"] = pd.to_datetime(out["business_date"], errors="coerce").dt.date

    out = out.dropna(
        subset=["store_number", "business_date", "employee_name", "ppa", "beverage_pct", "turn_time", "check_count", "sales"]
    ).copy()

    out["store_number"] = out["store_number"].astype(int)
    return out


# -----------------------------
# Metric helpers
# -----------------------------
def weighted_bev_pct(df: pd.DataFrame) -> float:
    if df.empty:
        return 0.0
    total_sales = df["sales"].sum()
    if total_sales <= 0:
        return 0.0
    bev_dollars = (df["sales"] * (df["beverage_pct"] / 100.0)).sum()
    return (bev_dollars / total_sales) * 100.0


def build_server_summary(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame(columns=["employee_name", "turn_time", "beverage_pct", "ppa"])

    grouped = (
        df.groupby("employee_name", dropna=False)
        .agg(
            turn_time=("turn_time", "mean"),
            sales=("sales", "sum"),
            check_count=("check_count", "sum"),
        )
        .reset_index()
    )

    bev_source = (
        df.assign(beverage_dollars=df["sales"] * (df["beverage_pct"] / 100.0))
        .groupby("employee_name", dropna=False)
        .agg(beverage_dollars=("beverage_dollars", "sum"))
        .reset_index()
    )

    grouped = grouped.merge(bev_source, on="employee_name", how="left")
    grouped["beverage_pct"] = grouped.apply(
        lambda r: (r["beverage_dollars"] / r["sales"] * 100.0) if r["sales"] > 0 else 0.0,
        axis=1,
    )
    grouped["ppa"] = grouped.apply(
        lambda r: (r["sales"] / r["check_count"]) if r["check_count"] > 0 else 0.0,
        axis=1,
    )

    grouped = grouped[["employee_name", "turn_time", "beverage_pct", "ppa"]]
    return grouped.sort_values("turn_time", ascending=True).reset_index(drop=True)


def build_location_summary(df: pd.DataFrame, loc_map: dict) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame(columns=["Location", "Turn Time", "Bev %", "PPA"])

    grouped = (
        df.groupby("store_number", dropna=False)
        .agg(
            turn_time=("turn_time", "mean"),
            sales=("sales", "sum"),
            check_count=("check_count", "sum"),
        )
        .reset_index()
    )

    bev_source = (
        df.assign(beverage_dollars=df["sales"] * (df["beverage_pct"] / 100.0))
        .groupby("store_number", dropna=False)
        .agg(beverage_dollars=("beverage_dollars", "sum"))
        .reset_index()
    )

    grouped = grouped.merge(bev_source, on="store_number", how="left")
    grouped["beverage_pct"] = grouped.apply(
        lambda r: (r["beverage_dollars"] / r["sales"] * 100.0) if r["sales"] > 0 else 0.0,
        axis=1,
    )
    grouped["ppa"] = grouped.apply(
        lambda r: (r["sales"] / r["check_count"]) if r["check_count"] > 0 else 0.0,
        axis=1,
    )
    grouped["Location"] = grouped["store_number"].apply(lambda x: f"{x} - {loc_map.get(int(x), 'Unknown')}")

    out = grouped.rename(
        columns={
            "turn_time": "Turn Time",
            "beverage_pct": "Bev %",
            "ppa": "PPA",
        }
    )[["Location", "Turn Time", "Bev %", "PPA"]]

    return out.sort_values("PPA", ascending=False).reset_index(drop=True)


def format_ppa_status(ppa: float):
    if ppa >= 21:
        return "#21c55d", "#13281c"
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

    display_df = df.rename(
        columns={
            "employee_name": "Server",
            "turn_time": "Turn Time",
            "beverage_pct": "Dine In Bev %",
            "ppa": "PPA",
        }
    )

    styler = display_df.style.format(
        {
            "Turn Time": "{:.2f}",
            "Dine In Bev %": "{:.2f}%",
            "PPA": "${:.2f}",
        }
    )

    styler = styler.map(color_turn, subset=["Turn Time"])
    styler = styler.map(color_bev, subset=["Dine In Bev %"])
    styler = styler.map(color_ppa, subset=["PPA"])

    return styler


# -----------------------------
# KPI cards
# -----------------------------
def render_kpi_cards(df: pd.DataFrame, header_label: str):
    st.markdown(f"## {header_label}")

    avg_turn = df["turn_time"].mean() if not df.empty else 0.0
    avg_bev = weighted_bev_pct(df)
    total_sales = df["sales"].sum() if not df.empty else 0.0
    total_checks = df["check_count"].sum() if not df.empty else 0.0
    ppa = (total_sales / total_checks) if total_checks > 0 else 0.0

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

    ppa_border, ppa_bg = format_ppa_status(ppa)

    cols = st.columns(4)

    with cols[0]:
        st.markdown(
            f"""
            <div style="border:1px solid #8a6d1f; border-radius:18px; padding:24px; background:#2f2918; min-height:220px;">
                <div style="color:#f0b90b; font-size:14px; font-weight:700; letter-spacing:1px;">AVG TURN TIME</div>
                <div style="font-size:46px; font-weight:800; margin-top:16px; color:white;">{avg_turn:.2f}</div>
                <div style="margin-top:20px; font-size:16px; color:white;">Best: <b>{best_turn}</b></div>
                <div style="font-size:16px; color:white;">Slow: <b>{slowest_turn}</b></div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    with cols[1]:
        st.markdown(
            f"""
            <div style="border:1px solid #8d2b2b; border-radius:18px; padding:24px; background:#35191d; min-height:220px;">
                <div style="color:#ff4b4b; font-size:14px; font-weight:700; letter-spacing:1px;">AVG DINE IN BEV %</div>
                <div style="font-size:46px; font-weight:800; margin-top:16px; color:white;">{avg_bev:.2f}%</div>
                <div style="margin-top:20px; font-size:16px; color:white;">Top: <b>{top_bev}</b></div>
                <div style="font-size:16px; color:white;">Bot: <b>{bottom_bev}</b></div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    with cols[2]:
        st.markdown(
            f"""
            <div style="border:1px solid {ppa_border}; border-radius:18px; padding:24px; background:{ppa_bg}; min-height:220px;">
                <div style="color:{ppa_border}; font-size:14px; font-weight:700; letter-spacing:1px;">PPA</div>
                <div style="font-size:46px; font-weight:800; margin-top:16px; color:white;">${ppa:.2f}</div>
                <div style="margin-top:20px; font-size:16px; color:white;">Top: <b>{top_ppa}</b></div>
                <div style="font-size:16px; color:white;">Bot: <b>{bottom_ppa}</b></div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    with cols[3]:
        st.markdown(
            f"""
            <div style="border:1px solid #7c3aed; border-radius:18px; padding:24px; background:#24163d; min-height:220px;">
                <div style="color:#a855f7; font-size:14px; font-weight:700; letter-spacing:1px;">ALL-GREEN</div>
                <div style="font-size:46px; font-weight:800; margin-top:16px; color:white;">{all_green_count} of {total_servers}</div>
                <div style="margin-top:20px; font-size:16px; color:white;">Turn ≤40m & Bev ≥19%</div>
            </div>
            """,
            unsafe_allow_html=True,
        )


# -----------------------------
# WhatsApp image export
# -----------------------------
def build_whatsapp_png(title: str, subtitle: str, raw_df: pd.DataFrame) -> bytes:
    server_df = build_server_summary(raw_df).copy()

    avg_turn = raw_df["turn_time"].mean() if not raw_df.empty else 0.0
    avg_bev = weighted_bev_pct(raw_df)
    total_sales = raw_df["sales"].sum() if not raw_df.empty else 0.0
    total_checks = raw_df["check_count"].sum() if not raw_df.empty else 0.0
    avg_ppa = (total_sales / total_checks) if total_checks > 0 else 0.0

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

    # Header
    ax.add_patch(Rectangle((0.02, 0.88), 0.96, 0.1, facecolor="#2c5aa0", edgecolor="#2c5aa0"))
    ax.text(0.04, 0.94, title, fontsize=22, fontweight="bold", color="white", va="center")
    ax.text(0.04, 0.905, subtitle, fontsize=13, color="#dbeafe", va="center", style="italic")

    # KPI cards
    card_y = 0.62
    card_h = 0.18
    card_w = 0.22
    x_positions = [0.04, 0.28, 0.52, 0.76]

    ppa_fill = "#6fd08c" if avg_ppa >= 21 else "#f0d766" if avg_ppa >= 20 else "#f8696b"

    card_specs = [
        ("TURN", f"{avg_turn:.2f}", f"Best: {best_turn}", f"Slow: {slowest_turn}", "#6fd08c"),
        ("BEVERAGE", f"{avg_bev:.2f}%", f"Top: {top_bev}", f"Bot: {bottom_bev}", "#f0d766"),
        ("PPA", f"${avg_ppa:.2f}", f"Top: {top_ppa}", f"Bot: {bottom_ppa}", ppa_fill),
        ("ALL-GREEN", f"{all_green_count} of {total_servers}", "Turn ≤40m & Bev ≥19%", "", "#b160f0"),
    ]

    for i, (label, value, line1, line2, color) in enumerate(card_specs):
        ax.add_patch(Rectangle((x_positions[i], card_y), card_w, card_h, facecolor=color, edgecolor="#cbd5e1", linewidth=1.2))
        ax.text(x_positions[i] + 0.012, card_y + 0.15, label, fontsize=11, fontweight="bold", color="#111827", va="center")
        ax.text(x_positions[i] + 0.012, card_y + 0.10, value, fontsize=21, fontweight="bold", color="#111827", va="center")
        ax.text(x_positions[i] + 0.012, card_y + 0.055, line1, fontsize=9.5, color="#111827", va="center")
        if line2:
            ax.text(x_positions[i] + 0.012, card_y + 0.02, line2, fontsize=9.5, color="#111827", va="center")

    # Table
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
# Sidebar
# -----------------------------
sidebar_col1, sidebar_col2, sidebar_col3 = st.sidebar.columns([1, 1.5, 1])
with sidebar_col2:
    st.image("logo.png", use_container_width=True)

st.sidebar.markdown(
    "<p style='text-align: center; color: gray; font-size: 0.9em; margin-top: -10px;'>Peachtree Partners Data Analysis</p>",
    unsafe_allow_html=True,
)
st.sidebar.markdown(
    f"<p style='text-align: center; color: gray; font-size: 0.7em; margin-top: -15px;'>{APP_VERSION}</p>",
    unsafe_allow_html=True,
)
st.sidebar.header("Filter Selections Below")

try:
    tz = ZoneInfo("America/New_York")
    today = datetime.now(tz).date()
except Exception:
    today = datetime.now().date()

yesterday = today - timedelta(days=1)

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
        show_realtime_warning()
        st.error("Real-time data is unavailable. Please adjust the Custom Date Range in the sidebar.")
        st.stop()

st.sidebar.info(
    f"Selected: **{start_date.strftime('%b %d, %Y')}** to **{end_date.strftime('%b %d, %Y')}**"
)

with st.spinner("Loading Locations..."):
    try:
        loc_map = get_location_map_from_api()
        if not loc_map:
            st.sidebar.error("Could not load locations from Rosnet.")
            st.stop()
    except Exception as e:
        st.sidebar.error(f"Could not load locations from Rosnet: {e}")
        st.stop()

selected_locations = st.sidebar.multiselect(
    "Choose Your Location(s)",
    options=list(loc_map.keys()),
    format_func=lambda x: f"{x} - {loc_map.get(x, 'Unknown')}",
    default=[],
)

# -----------------------------
# Main
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

if df.empty:
    st.warning("Data was returned, but none of it matched the fields required by the dashboard.")
    st.stop()

tab1, tab2, tab3 = st.tabs(["📊 Overview", "👨‍🍳 Server Performance", "Raw Dataset Summary"])

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

        market_png = build_whatsapp_png(
            "MARKET TOTAL",
            f"{start_date.strftime('%b %d, %Y')} to {end_date.strftime('%b %d, %Y')}",
            df,
        )
        st.download_button(
            "Download MARKET TOTAL WhatsApp Image",
            data=market_png,
            file_name="market_total_whatsapp.png",
            mime="image/png",
            key="market_total_png",
        )

        unique_locs = sorted(df["store_number"].dropna().astype(int).unique())

        for loc in unique_locs:
            loc_df = df[df["store_number"] == int(loc)].copy()
            if loc_df.empty:
                continue

            store_name = loc_map.get(int(loc), str(loc))

            st.markdown("---")
            st.markdown(f"### 📍 {store_name}")

            render_kpi_cards(loc_df, header_label="STORE TOTAL")

            server_df = build_server_summary(loc_df)

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

            st.dataframe(
                style_server_summary(server_df),
                use_container_width=True,
                height=min(500, 45 + len(server_df) * 35),
            )

with tab3:
    st.markdown("### Combined Stored Dataset")
    st.dataframe(df, use_container_width=True, height=600)

st.markdown(
    f"<br><hr><center><small>Powered by Rosnet Sync + Supabase | {APP_VERSION}</small></center>",
    unsafe_allow_html=True,
)
