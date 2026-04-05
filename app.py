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

APP_VERSION = "v1.2.2"


@st.dialog("Data Availability")
def show_realtime_warning():
    st.warning("Real-time data is not available.")
    st.write(
        "This data is historical only. Please change your date selection in the sidebar to a range ending yesterday or earlier."
    )


# --- Sidebar Filters ---
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


# --- Date logic ---
try:
    tz = ZoneInfo("America/New_York")
    today = datetime.now(tz).date()
except Exception:
    today = datetime.now().date()

yesterday = today - timedelta(days=1)

date_method = st.sidebar.radio(
    "Choose Your Timeframe",
    ["Quick Select", "Custom Range"],
    horizontal=True,
)

if date_method == "Quick Select":
    quick_choice = st.sidebar.selectbox(
        "Range",
        ["Yesterday", "Week to Date", "Last Week", "Last Month"]
    )

    if quick_choice == "Yesterday":
        start_date = end_date = yesterday

    elif quick_choice == "Week to Date":
        start_date = today - timedelta(days=today.weekday())
        end_date = yesterday
        if start_date > end_date:
            start_date = end_date

    elif quick_choice == "Last Week":
        start_date = yesterday - timedelta(days=yesterday.weekday() + 7)
        end_date = start_date + timedelta(days=6)

    elif quick_choice == "Last Month":
        first_day_this_month = today.replace(day=1)
        end_date = first_day_this_month - timedelta(days=1)
        start_date = end_date.replace(day=1)

    st.sidebar.info(
        f"Selected: **{start_date.strftime('%b %d, %Y')}** to **{end_date.strftime('%b %d, %Y')}**"
    )

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


# --- Dynamic locations from Rosnet only ---
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

st.title("*Almost* Live Rosnet Turn and Beverage Data 📈")
st.warning(
    "🚧 **Under Development:** This dashboard is currently in active testing. Errors may occasionally occur. Please contact **Chad** with any issues, feedback, or UI suggestions."
)

if len(selected_locations) == 0:
    st.info("👋 **Welcome to Rosnet Insights!**\n\nPlease select one or more locations from the sidebar to begin your analysis.")
    st.stop()


# --- Database helpers ---
def get_db_connection():
    return psycopg2.connect(
        host=st.secrets["database"]["host"],
        port=st.secrets["database"]["port"],
        dbname=st.secrets["database"]["dbname"],
        user=st.secrets["database"]["user"],
        password=st.secrets["database"]["password"],
    )


@st.cache_data(ttl=300)
def get_data_from_db(start_date, end_date, locations):
    conn = get_db_connection()
    locations = [int(x) for x in locations]

    query = """
        SELECT *
        FROM employee_daily_metrics
        WHERE store_number = ANY(%s::bigint[])
          AND business_date BETWEEN %s AND %s
        ORDER BY business_date, store_number, employee_name
    """

    try:
        df = pd.read_sql(query, conn, params=(locations, start_date, end_date))
    finally:
        conn.close()

    return df


with st.spinner("Loading stored Rosnet data..."):
    try:
        checks_df = get_data_from_db(start_date, end_date, selected_locations)
    except Exception as e:
        st.error(f"Error fetching data from database: {e}")
        st.stop()

if checks_df.empty:
    st.warning(
        f"No data found for {start_date.strftime('%b %d, %Y')} to {end_date.strftime('%b %d, %Y')} "
        f"for the selected location(s). Make sure those dates and locations have been synced into Supabase."
    )
    with st.expander("Debug details"):
        st.write("Selected locations:", selected_locations)
        st.write("Available location labels:", {k: loc_map[k] for k in selected_locations if k in loc_map})
        st.write("Date range:", start_date, "to", end_date)
    st.stop()


# --- Translate DB schema to component expectations ---
filtered_df = checks_df.rename(
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

# Normalize types
filtered_df["locationId"] = pd.to_numeric(filtered_df["locationId"], errors="coerce").astype("Int64")
filtered_df["businessDate"] = pd.to_datetime(filtered_df["businessDate"], errors="coerce").dt.date
filtered_df["turnTimeMinutes"] = pd.to_numeric(filtered_df["turnTimeMinutes"], errors="coerce")
filtered_df["beverageSales"] = pd.to_numeric(filtered_df["beverageSales"], errors="coerce")
filtered_df["netSales"] = pd.to_numeric(filtered_df["netSales"], errors="coerce")
filtered_df["checkNumber"] = pd.to_numeric(filtered_df["checkNumber"], errors="coerce")

# Drop rows missing critical fields
filtered_df = filtered_df.dropna(
    subset=["locationId", "businessDate", "serverName", "turnTimeMinutes", "beverageSales", "netSales", "checkNumber"]
).copy()

if filtered_df.empty:
    st.warning("Data was returned from the database, but none of it matched the fields required by the dashboard.")
    with st.expander("Returned columns"):
        st.write(list(checks_df.columns))
    st.stop()


def render_kpi_row(df, prefix="Market"):
    kpi_cols = st.columns(3)

    if not df.empty and "turnTimeMinutes" in df.columns:
        avg_turn_time = df["turnTimeMinutes"].mean()
        delta_goal = round(avg_turn_time - 45, 1)
    else:
        avg_turn_time = 0.0
        delta_goal = 0.0

    if not df.empty and "beverageSales" in df.columns:
        bev_pct = df["beverageSales"].mean()
        bev_delta = round(bev_pct - 19, 1)
    else:
        bev_pct = 0.0
        bev_delta = 0.0

    kpi_cols[0].metric(
        f"{prefix} Avg Turn Time",
        f"{avg_turn_time:.1f} min",
        f"{delta_goal:+.1f} min vs 45m Goal",
        delta_color="inverse",
    )
    kpi_cols[1].metric(
        f"{prefix} Dine In Bev %",
        f"{bev_pct:.1f}%",
        f"{bev_delta:+.1f}% vs 19% Goal",
    )
    kpi_cols[2].metric("Turn Time Goal", "45 min")


st.markdown("### Specific Focus: Table Turns")
st.caption("Using stored historical data from Supabase.")
st.markdown("---")

# --- Multi-store resolution ---
store_names = []
unique_locs = []

if not filtered_df.empty and "locationId" in filtered_df.columns:
    unique_locs = filtered_df["locationId"].dropna().astype(int).unique()
    for loc in unique_locs:
        if loc in loc_map:
            store_names.append(f"{loc} - {loc_map[loc]}")
        else:
            store_names.append(str(loc))

tab1, tab2, tab3 = st.tabs(["⏱️ Daily Turn Times", "👨‍🍳 Server Performance", "Raw Dataset Summary"])

with tab1:
    st.markdown("### 🏢 Market Total")
    render_kpi_row(filtered_df, prefix="Market")
    render_table_turns(filtered_df, key="market_total_turns")

    for i, loc in enumerate(unique_locs):
        st.markdown("---")
        st.markdown(f"#### 📍 {store_names[i]}")
        loc_df = filtered_df[filtered_df["locationId"].astype(int) == int(loc)].copy()
        if not loc_df.empty:
            render_kpi_row(loc_df, prefix="Store")
            render_table_turns(loc_df, key=f"store_turns_{loc}")
        else:
            st.info("No data available for this timeline.")

with tab2:
    if start_date == end_date:
        _date_str = start_date.strftime("%b %d, %Y")
    else:
        _date_str = f"{start_date.strftime('%b %d')} – {end_date.strftime('%b %d, %Y')}"

    st.markdown("### 🏢 Market Total Leaderboard")
    _market_title = "Market Total" if len(unique_locs) > 1 else (store_names[0] if store_names else "Market Total")
    render_combined_leaderboard(
        filtered_df,
        key="market_total_leaderboard",
        title=_market_title,
        date_range_str=_date_str,
    )

    for i, loc in enumerate(unique_locs):
        st.markdown("---")
        st.markdown(f"#### 📍 {store_names[i]}")
        loc_df = filtered_df[filtered_df["locationId"].astype(int) == int(loc)].copy()
        if not loc_df.empty:
            render_combined_leaderboard(
                loc_df,
                key=f"store_leaderboard_{loc}",
                title=store_names[i],
                date_range_str=_date_str,
            )
        else:
            st.info("No server data available for this timeline.")

with tab3:
    st.markdown("### Combined Stored Dataset")
    st.dataframe(filtered_df, use_container_width=True, height=600)

st.markdown(
    f"<br><hr><center><small>Powered by Rosnet Sync + Supabase | {APP_VERSION}</small></center>",
    unsafe_allow_html=True,
)
