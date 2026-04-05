import streamlit as st
import pandas as pd
import psycopg2
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import api
from components import style_metric_cards, render_table_turns, render_combined_leaderboard

st.set_page_config(
    page_title="Rosnet Insights Dashboard",
    page_icon="🍔",
    layout="wide",
    initial_sidebar_state="expanded",
)

style_metric_cards()

APP_VERSION = "v1.3.0"


@st.dialog("Data Availability")
def show_realtime_warning():
    st.warning("Real-time data is not available.")
    st.write(
        "This data is historical only. Please change your date selection in the sidebar to a range ending yesterday or earlier."
    )


# -----------------------------
# Helpers
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


def prepare_display_df(df):
    if df.empty:
        return df.copy()

    out = df.rename(
        columns={
            "employee_name": "serverName",
            "sales": "netSales",
            "beverage_pct": "beverageSales",
            "turn_time": "turnTimeMinutes",
            "check_count": "checkNumber",
            "store_number": "locationId",
            "business_date": "businessDate",
        }
    ).copy()

    out["locationId"] = pd.to_numeric(out["locationId"], errors="coerce").astype("Int64")
    out["businessDate"] = pd.to_datetime(out["businessDate"], errors="coerce").dt.date
    out["turnTimeMinutes"] = pd.to_numeric(out["turnTimeMinutes"], errors="coerce")
    out["beverageSales"] = pd.to_numeric(out["beverageSales"], errors="coerce")
    out["netSales"] = pd.to_numeric(out["netSales"], errors="coerce")
    out["checkNumber"] = pd.to_numeric(out["checkNumber"], errors="coerce")

    out = out.dropna(
        subset=["locationId", "businessDate", "serverName", "turnTimeMinutes", "beverageSales", "netSales", "checkNumber"]
    ).copy()

    return out


def ppa_status(ppa_value: float) -> str:
    if ppa_value >= 21:
        return "green"
    if ppa_value >= 20:
        return "yellow"
    return "red"


def render_kpi_cards(df, title_label="COMPANY TOTAL", include_ppa=True):
    st.markdown(f"### {title_label}")

    avg_turn = df["turnTimeMinutes"].mean() if not df.empty else 0.0
    avg_bev = df["beverageSales"].mean() if not df.empty else 0.0

    total_sales = df["netSales"].sum() if not df.empty else 0.0
    total_checks = df["checkNumber"].sum() if not df.empty else 0.0
    ppa = (total_sales / total_checks) if total_checks > 0 else 0.0

    turn_delta = round(avg_turn - 45, 1)
    bev_delta = round(avg_bev - 19, 1)

    if include_ppa:
        cols = st.columns(4)
    else:
        cols = st.columns(3)

    cols[0].metric(
        "Avg Turn Time",
        f"{avg_turn:.1f} min",
        f"{turn_delta:+.1f} min vs 45m Goal",
        delta_color="inverse",
    )

    cols[1].metric(
        "Dine In Bev %",
        f"{avg_bev:.1f}%",
        f"{bev_delta:+.1f}% vs 19% Goal",
    )

    cols[2].metric("Turn Time Goal", "45 min")

    if include_ppa:
        ppa_color = ppa_status(ppa)
        if ppa_color == "green":
            ppa_label = "🟢"
        elif ppa_color == "yellow":
            ppa_label = "🟡"
        else:
            ppa_label = "🔴"

        cols[3].metric(
            "PPA",
            f"${ppa:.2f}",
            f"{ppa_label} vs $21.00 Goal",
        )


def build_location_summary(df, loc_map):
    if df.empty:
        return pd.DataFrame()

    summary = (
        df.groupby("locationId", dropna=False)
        .agg(
            turnTimeMinutes=("turnTimeMinutes", "mean"),
            beverageSales=("beverageSales", "mean"),
            netSales=("netSales", "sum"),
            checkNumber=("checkNumber", "sum"),
        )
        .reset_index()
    )

    summary["PPA"] = summary.apply(
        lambda r: (r["netSales"] / r["checkNumber"]) if r["checkNumber"] > 0 else 0.0,
        axis=1
    )
    summary["Location"] = summary["locationId"].apply(lambda x: f"{int(x)} - {loc_map.get(int(x), 'Unknown')}")
    summary = summary.rename(
        columns={
            "turnTimeMinutes": "Turn Time",
            "beverageSales": "Bev %",
            "netSales": "Sales",
            "checkNumber": "Checks",
        }
    )

    return summary[["Location", "Turn Time", "Bev %", "PPA", "Checks", "Sales"]].sort_values("PPA", ascending=False)


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

# Dynamic locations only
with st.spinner("Loading Locations..."):
    try:
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
# Main Data Load
# -----------------------------
st.title("*Almost* Live Rosnet Turn and Beverage Data 📈")
st.warning(
    "🚧 **Under Development:** This dashboard is currently in active testing. Errors may occasionally occur. Please contact **Chad** with any issues, feedback, or UI suggestions."
)

# No selection = company total
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

filtered_df = prepare_display_df(raw_df)

if filtered_df.empty:
    st.warning("Data was returned, but it did not match the required dashboard fields.")
    st.stop()

# -----------------------------
# Tabs
# -----------------------------
tab1, tab2, tab3 = st.tabs(["📊 Overview", "👨‍🍳 Server Performance", "Raw Dataset Summary"])

with tab1:
    render_kpi_cards(filtered_df, title_label=header_label, include_ppa=True)

    st.markdown("---")
    st.markdown("### Location Breakdown")

    summary_df = build_location_summary(filtered_df, loc_map)
    st.dataframe(summary_df, use_container_width=True, height=500)

with tab2:
    if not selected_locations:
        st.info("Select one or more locations from the main page to view server performance.")
    else:
        render_kpi_cards(filtered_df, title_label="MARKET TOTAL", include_ppa=True)

        if start_date == end_date:
            _date_str = start_date.strftime("%b %d, %Y")
        else:
            _date_str = f"{start_date.strftime('%b %d')} – {end_date.strftime('%b %d, %Y')}"

        st.markdown("---")
        st.markdown("### MARKET TOTAL")
        render_combined_leaderboard(
            filtered_df,
            key="market_total_leaderboard",
            title="MARKET TOTAL",
            date_range_str=_date_str,
        )

        unique_locs = filtered_df["locationId"].dropna().astype(int).unique()

        for loc in unique_locs:
            loc_df = filtered_df[filtered_df["locationId"].astype(int) == int(loc)].copy()
            if loc_df.empty:
                continue

            st.markdown("---")
            st.markdown(f"#### 📍 {loc_map.get(int(loc), str(loc))}")
            render_kpi_cards(loc_df, title_label="MARKET TOTAL", include_ppa=True)
            render_combined_leaderboard(
                loc_df,
                key=f"store_leaderboard_{loc}",
                title=loc_map.get(int(loc), str(loc)),
                date_range_str=_date_str,
            )

with tab3:
    st.markdown("### Combined Stored Dataset")
    st.dataframe(filtered_df, use_container_width=True, height=600)

st.markdown(
    f"<br><hr><center><small>Powered by Rosnet Sync + Supabase | {APP_VERSION}</small></center>",
    unsafe_allow_html=True,
)
