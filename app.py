import io
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import matplotlib.pyplot as plt
import pandas as pd
import psycopg2
import streamlit as st

import api

st.set_page_config(
    page_title="Rosnet Insights Dashboard",
    page_icon="🍔",
    layout="wide",
    initial_sidebar_state="expanded",
)

APP_VERSION = "v1.5.0"


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
        return pd.DataFrame(columns=["employee_name", "turn_time", "beverage_pct", "ppa", "check_count", "sales"])

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

    grouped = grouped[["employee_name", "turn_time", "beverage_pct", "ppa", "check_count", "sales"]]
    return grouped.sort_values("ppa", ascending=False).reset_index(drop=True)


def build_location_summary(df: pd.DataFrame, loc_map: dict) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame(columns=["Location", "Turn Time", "Bev %", "PPA", "Checks", "Sales"])

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
            "check_count": "Checks",
            "sales": "Sales",
        }
    )[["Location", "Turn Time", "Bev %", "PPA", "Checks", "Sales"]]

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
            return "background-color: #16351f; color: #d1fae5;"
        if v <= 45:
            return "background-color: #3a3112; color: #fef3c7;"
        return "background-color: #3b1a1a; color: #fecaca;"

    def color_bev(v):
        if pd.isna(v):
            return ""
        if v >= 19:
            return "background-color: #16351f; color: #d1fae5;"
        if v >= 18:
            return "background-color: #3a3112; color: #fef3c7;"
        return "background-color: #3b1a1a; color: #fecaca;"

    def color_ppa(v):
        if pd.isna(v):
            return ""
        if v >= 21:
            return "background-color: #16351f; color: #d1fae5;"
        if v >= 20:
            return "background-color: #3a3112; color: #fef3c7;"
        return "background-color: #3b1a1a; color: #fecaca;"

    return (
        df.style
        .format(
            {
                "Turn Time": "{:.1f}",
                "Bev %": "{:.1f}%",
                "PPA": "${:.2f}",
                "Checks": "{:.0f}",
                "Sales": "${:,.2f}",
            }
        )
        .applymap(color_turn, subset=["Turn Time"])
        .applymap(color_bev, subset=["Bev %"])
        .applymap(color_ppa, subset=["PPA"])
    )


def style_server_summary(df: pd.DataFrame):
    def color_turn(v):
        if pd.isna(v):
            return ""
        if v <= 40:
            return "background-color: #16351f; color: #d1fae5;"
        if v <= 45:
            return "background-color: #3a3112; color: #fef3c7;"
        return "background-color: #3b1a1a; color: #fecaca;"

    def color_bev(v):
        if pd.isna(v):
            return ""
        if v >= 19:
            return "background-color: #16351f; color: #d1fae5;"
        if v >= 18:
            return "background-color: #3a3112; color: #fef3c7;"
        return "background-color: #3b1a1a; color: #fecaca;"

    def color_ppa(v):
        if pd.isna(v):
            return ""
        if v >= 21:
            return "background-color: #16351f; color: #d1fae5;"
        if v >= 20:
            return "background-color: #3a3112; color: #fef3c7;"
        return "background-color: #3b1a1a; color: #fecaca;"

    return (
        df.style
        .format(
            {
                "turn_time": "{:.1f}",
                "beverage_pct": "{:.1f}%",
                "ppa": "${:.2f}",
                "check_count": "{:.0f}",
                "sales": "${:,.2f}",
            }
        )
        .applymap(color_turn, subset=["turn_time"])
        .applymap(color_bev, subset=["beverage_pct"])
        .applymap(color_ppa, subset=["ppa"])
    )


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
                <div style="font-size:46px; font-weight:800; margin-top:16px; color:white;">{avg_turn:.1f} min</div>
                <div style="margin-top:20px; font-size:16px; color:white;">Best: <b>{best_turn}</b></div>
                <div style="font-size:16px; color:white;">Slowest: <b>{slowest_turn}</b></div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    with cols[1]:
        st.markdown(
            f"""
            <div style="border:1px solid #8d2b2b; border-radius:18px; padding:24px; background:#35191d; min-height:220px;">
                <div style="color:#ff4b4b; font-size:14px; font-weight:700; letter-spacing:1px;">AVG DINE IN BEV %</div>
                <div style="font-size:46px; font-weight:800; margin-top:16px; color:white;">{avg_bev:.1f}%</div>
                <div style="margin-top:20px; font-size:16px; color:white;">Top: <b>{top_bev}</b></div>
                <div style="font-size:16px; color:white;">Bottom: <b>{bottom_bev}</b></div>
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
                <div style="font-size:16px; color:white;">Bottom: <b>{bottom_ppa}</b></div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    with cols[3]:
        st.markdown(
            f"""
            <div style="border:1px solid #7c3aed; border-radius:18px; padding:24px; background:#24163d; min-height:220px;">
                <div style="color:#a855f7; font-size:14px; font-weight:700; letter-spacing:1px;">ALL-GREEN SERVERS</div>
                <div style="font-size:46px; font-weight:800; margin-top:16px; color:white;">{all_green_count} of {total_servers}</div>
                <div style="margin-top:20px; font-size:16px; color:white;">Turn ≤40m & Bev ≥19%</div>
            </div>
            """,
            unsafe_allow_html=True,
        )


# -----------------------------
# WhatsApp export
# -----------------------------
def build_whatsapp_png(title: str, subtitle: str, df: pd.DataFrame) -> bytes:
    display_df = df.copy()
    display_df["Turn"] = display_df["turn_time"].map(lambda x: f"{x:.1f}")
    display_df["Bev %"] = display_df["beverage_pct"].map(lambda x: f"{x:.1f}%")
    display_df["PPA"] = display_df["ppa"].map(lambda x: f"${x:.2f}")
    display_df["Checks"] = display_df["check_count"].map(lambda x: f"{x:.0f}")
    display_df["Sales"] = display_df["sales"].map(lambda x: f"${x:,.0f}")

    display_df = display_df.rename(columns={"employee_name": "Employee"})
    display_df = display_df[["Employee", "Turn", "Bev %", "PPA", "Checks", "Sales"]].head(18)

    rows = len(display_df)
    fig_height = max(6, 1.8 + rows * 0.35)

    fig, ax = plt.subplots(figsize=(10, fig_height), dpi=200)
    fig.patch.set_facecolor("#020817")
    ax.set_facecolor("#020817")
    ax.axis("off")

    ax.text(0.01, 1.06, title, fontsize=20, fontweight="bold", color="white", transform=ax.transAxes)
    ax.text(0.01, 1.01, subtitle, fontsize=10, color="#94a3b8", transform=ax.transAxes)

    table = ax.table(
        cellText=display_df.values,
        colLabels=display_df.columns,
        loc="upper left",
        cellLoc="left",
        colLoc="left",
        bbox=[0, 0, 1, 0.95],
    )

    table.auto_set_font_size(False)
    table.set_fontsize(9)

    for (r, c), cell in table.get_celld().items():
        cell.set_edgecolor("#1f2937")
        if r == 0:
            cell.set_facecolor("#111827")
            cell.get_text().set_color("white")
            cell.get_text().set_fontweight("bold")
        else:
            cell.set_facecolor("#0f172a")
            cell.get_text().set_color("white")

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
    st.warning(
        f"No data found for {start_date.strftime('%b %d, %Y')} to {end_date.strftime('%b %d, %Y')} "
        f"for the current selection."
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

        market_server_df = build_server_summary(df)

        market_png = build_whatsapp_png(
            "MARKET TOTAL",
            f"{start_date.strftime('%b %d, %Y')} to {end_date.strftime('%b %d, %Y')}",
            market_server_df,
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
                server_df,
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
