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

APP_VERSION = "v1.2.0"

# --- Sidebar UI ---
sidebar_col1, sidebar_col2, sidebar_col3 = st.sidebar.columns([1, 1.5, 1])
with sidebar_col2:
    st.image("logo.png", use_container_width=True)

st.sidebar.header("Filter Selections Below")

# --- Dates ---
tz = ZoneInfo("America/New_York")
today = datetime.now(tz).date()
yesterday = today - timedelta(days=1)

date_range = st.sidebar.date_input(
    "Date Range",
    value=(yesterday, yesterday)
)

if isinstance(date_range, tuple):
    start_date, end_date = date_range
else:
    start_date = end_date = date_range

# --- Locations (still using API for now) ---
raw_locations = api.get_locations()
loc_map = {loc.get("Id", loc.get("id")): loc.get("Name", loc.get("name")) for loc in raw_locations if loc}

selected_locations = st.sidebar.multiselect(
    "Choose Your Location(s)",
    options=list(loc_map.keys()),
    format_func=lambda x: f"{x} - {loc_map.get(x)}"
)

if not selected_locations:
    st.stop()

# --- DB LOAD ---
def get_data_from_db(start_date, end_date, locations):
    conn = psycopg2.connect(
        host=st.secrets["database"]["host"],
        port=st.secrets["database"]["port"],
        dbname=st.secrets["database"]["dbname"],
        user=st.secrets["database"]["user"],
        password=st.secrets["database"]["password"]
    )

    query = """
        SELECT *
        FROM employee_daily_metrics
        WHERE store_number = ANY(%s)
        AND business_date BETWEEN %s AND %s
    """

    df = pd.read_sql(query, conn, params=(locations, start_date, end_date))
    conn.close()
    return df

with st.spinner("Loading data..."):
    df = get_data_from_db(start_date, end_date, selected_locations)

if df.empty:
    st.warning("No data found")
    st.stop()

# --- 🔥 CRITICAL FIX: Normalize DB → App ---
df = df.rename(columns={
    "employee_name": "serverName",
    "sales": "netSales",
    "beverage_pct": "beverageSales",
    "turn_time": "turnTimeMinutes",
    "check_count": "checkNumber",
    "store_number": "locationId"
})

# Remove old filtering (DB is already processed)
filtered_df = df.copy()

# --- KPI ---
def render_kpi_row(df, prefix="Market"):
    kpi_cols = st.columns(3)

    avg_turn = df["turnTimeMinutes"].mean()
    bev_pct = df["beverageSales"].mean()
    ppa = df["netSales"].sum() / df["checkNumber"].sum()

    kpi_cols[0].metric(f"{prefix} Avg Turn Time", f"{avg_turn:.1f} min")
    kpi_cols[1].metric(f"{prefix} Bev %", f"{bev_pct:.1f}%")
    kpi_cols[2].metric(f"{prefix} PPA", f"${ppa:.2f}")

# --- Tabs ---
tab1, tab2 = st.tabs(["⏱️ Turns", "👨‍🍳 Performance"])

with tab1:
    st.markdown("### Market")
    render_kpi_row(filtered_df)

    render_table_turns(filtered_df, key="market")

    for loc in filtered_df["locationId"].unique():
        st.markdown(f"#### {loc_map.get(loc, loc)}")
        loc_df = filtered_df[filtered_df["locationId"] == loc]
        render_kpi_row(loc_df, "Store")
        render_table_turns(loc_df, key=f"{loc}")

with tab2:
    st.markdown("### Leaderboard")

    render_combined_leaderboard(
        filtered_df,
        key="leaderboard",
        title="Market",
        date_range_str=f"{start_date} - {end_date}"
    )

st.markdown("---")
st.caption(f"v{APP_VERSION} | Supabase Powered")
