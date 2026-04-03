import streamlit as st
import pandas as pd
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

# Inject metric card styles
style_metric_cards()

@st.dialog("Data Availability")
def show_realtime_warning():
    st.warning("Real-time data is not available.")
    st.write("This data is historical only. Please change your date selection in the sidebar to a range ending yesterday or earlier.")

def handle_rate_limit(e):
    hours, remainder = divmod(e.retry_after, 3600)
    minutes, _ = divmod(remainder, 60)
    parts = []
    if hours > 0:
        parts.append(f"{hours} hour{'s' if hours != 1 else ''}")
    if minutes > 0:
        parts.append(f"{minutes} minute{'s' if minutes != 1 else ''}")
    if not parts:
        parts.append(f"{e.retry_after} seconds")
    time_str = " and ".join(parts)
    
    st.error(f"**Oops!** The Rosnet rate limit has been exceeded. Go touch some tables and come back in {time_str}.")
    
    # Attempt to load the user's provided picture
    import os
    if os.path.exists("rate_limit_exceeded.png"):
        st.image("rate_limit_exceeded.png")
    elif os.path.exists("rate_limit_exceeded.jpg"):
        st.image("rate_limit_exceeded.jpg")
    elif os.path.exists("rate_limit_exceeded.jpeg"):
        st.image("rate_limit_exceeded.jpeg")
        
    st.stop()

# --- Sidebar Filters ---
st.sidebar.image("logo.png", width=140)
st.sidebar.caption("Peachtree Partners Data Analysis")
st.sidebar.header("Filter Selections Below")

# Filter logic
try:
    # Anchor to Eastern Time to prevent UTC servers from rolling over 'today' prematurely
    tz = ZoneInfo("America/New_York")
    today = datetime.now(tz).date()
except Exception:
    today = datetime.now().date()
    
yesterday = today - timedelta(days=1)

date_method = st.sidebar.radio("Choose Your Timeframe", ["Quick Select", "Custom Range"], horizontal=True)

if date_method == "Quick Select":
    quick_choice = st.sidebar.selectbox("Range", ["Yesterday", "Week to Date", "Last Week", "Last Month"])
    
    if quick_choice == "Yesterday":
        start_date = end_date = yesterday
    elif quick_choice == "Week to Date":
        start_date = today - timedelta(days=today.weekday())
        end_date = yesterday
        if start_date > end_date:
            start_date = end_date
    elif quick_choice == "Last Week":
        # Monday to Sunday of previous week
        start_date = yesterday - timedelta(days=yesterday.weekday() + 7)
        end_date = start_date + timedelta(days=6)
    elif quick_choice == "Last Month":
        # First to last day of previous month
        first_day_this_month = today.replace(day=1)
        end_date = first_day_this_month - timedelta(days=1)
        start_date = end_date.replace(day=1)
        
    st.sidebar.info(f"Selected: **{start_date.strftime('%b %d, %Y')}** to **{end_date.strftime('%b %d, %Y')}**")

else:
    date_range = st.sidebar.date_input(
        "Custom Date Range",
        value=(yesterday - timedelta(days=6), yesterday),
        max_value=today
    )
    start_date = end_date = yesterday
    if isinstance(date_range, tuple) and len(date_range) == 2:
        start_date, end_date = date_range
    elif isinstance(date_range, tuple) and len(date_range) == 1:
        start_date = end_date = date_range[0]
    else:
        start_date = end_date = date_range

    if start_date == today or end_date == today:
        show_realtime_warning()
        st.error("Real-time data is unavailable. Please adjust the Custom Date Range in the sidebar.")
        st.stop()


# Locations dropdown
with st.spinner("Loading Locations..."):
    try:
        raw_locations = api.get_locations()
        # Create a dict of location ID to Names for easy mapping, handling both PascalCase and camelCase
        loc_map = {}
        if isinstance(raw_locations, list):
            for loc in raw_locations:
                if loc:
                    l_id = loc.get('Id', loc.get('id'))
                    l_name = loc.get('Name', loc.get('name', 'Unknown'))
                    if l_id is not None:
                        loc_map[l_id] = l_name
        if not loc_map:
            loc_map = {101: "Default Location (Mock)"}
    except api.RateLimitExceeded as e:
        handle_rate_limit(e)
    except Exception as e:
        st.sidebar.error("Could not fetch locations.")
        loc_map = {101: "Default Location (Mock)"}


selected_locations = st.sidebar.multiselect(
    "Choose Your Location(s)",
    options=list(loc_map.keys()),
    format_func=lambda x: f"{x} - {loc_map.get(x, 'Unknown')}",
    default=[] # Do not autoselect sites to prevent unintentional API polling
)

# Dates are already fully processed by the date_method selection above

# Dates are finalized by the date selection above

# --- Main Content ---
st.title("*Almost* Live Rosnet Turn and Beverage Data 📈")
st.warning("🚧 **Under Development:** This dashboard is currently in active testing. Errors may occasionally occur. Please contact **Chad** with any issues, feedback, or UI suggestions.")

if api.MOCK_MODE:
    st.warning("⚠️ Running in Mock Mode. Please add credentials to .env to pull real Rosnet data.")

if len(selected_locations) == 0:
    st.info("👋 **Welcome to Rosnet Insights!**\n\nPlease select one or more locations from the sidebar to begin your analysis.")
    st.stop()

# Load Data
def _normalize_df(data):
    if not data or data == [None]:
        return pd.DataFrame()
    df = pd.DataFrame(data)
    if not df.empty:
        df.columns = [c[0].lower() + c[1:] for c in df.columns]
    return df

@st.cache_data(ttl=600) # Cache for 10 minutes to prevent API spam
def load_check_data(sd, ed, locs):
    sd_str = sd.strftime("%Y-%m-%d")
    ed_str = ed.strftime("%Y-%m-%d")
    bev_cat_ids = api.get_beverage_category_ids()
    all_checks = []
    for loc_id in locs:
        emp_map = api.get_employees_map(loc_id)
        checks_data = api.get_checks(sd_str, ed_str, loc_id, emp_map=emp_map, bev_cat_ids=bev_cat_ids)
        if checks_data:
            all_checks.extend(checks_data)
    return _normalize_df(all_checks)

# Load check data from Rosnet API
col_load = st.empty()
with col_load:
    with st.spinner("Crunching table checks from Rosnet..."):
        try:
            checks_df = load_check_data(start_date, end_date, selected_locations)
        except api.RateLimitExceeded as e:
            handle_rate_limit(e)
        except Exception as e:
            st.error(f"Error fetching data: {e}")
            st.stop()


# --- Table Turns Focus ---
st.markdown("### Specific Focus: Table Turns")
st.caption("Filtered exclusively for **Eat-In** tickets closed to **Credit Cards**.")

if not checks_df.empty:
    # Filter Data
    filtered_df = checks_df[
        (checks_df['orderType'] == 'Eat In') & 
        (checks_df['paymentType'] == 'Credit Card')
    ].copy()
else:
    filtered_df = pd.DataFrame()

if not filtered_df.empty and 'openTime' in filtered_df.columns and 'closeTime' in filtered_df.columns:
    # Pre-calculate Table Turn Time in minutes globally
    filtered_df['openTimeObj'] = pd.to_datetime(filtered_df['openTime'], format='%H:%M:%S', errors='coerce')
    filtered_df['closeTimeObj'] = pd.to_datetime(filtered_df['closeTime'], format='%H:%M:%S', errors='coerce')
    filtered_df['turnTimeMinutes'] = (filtered_df['closeTimeObj'] - filtered_df['openTimeObj']).dt.total_seconds() / 60.0
    # Handle overnight logic where closeTime is smaller than openTime
    filtered_df.loc[filtered_df['turnTimeMinutes'] < 0, 'turnTimeMinutes'] += 24 * 60

def render_kpi_row(df, prefix="Market"):
    kpi_cols = st.columns(3)
    if not df.empty and 'turnTimeMinutes' in df.columns:
        avg_turn_time = df['turnTimeMinutes'].mean()
        delta_goal = round(avg_turn_time - 45, 1)
    else:
        avg_turn_time = 0.0
        delta_goal = 0.0

    # Calculate beverage %
    if not df.empty and 'beverageSales' in df.columns and 'netSales' in df.columns:
        total_bev = df['beverageSales'].sum()
        total_net = df['netSales'].sum()
        bev_pct = (total_bev / total_net * 100) if total_net > 0 else 0
        bev_delta = round(bev_pct - 19, 1)
    else:
        bev_pct = 0.0
        bev_delta = 0.0

    kpi_cols[0].metric(f"{prefix} Avg Turn Time", f"{avg_turn_time:.1f} min", f"{delta_goal:+.1f} min vs 45m Goal", delta_color="inverse")
    kpi_cols[1].metric(f"{prefix} Dine In Bev %", f"{bev_pct:.1f}%", f"{bev_delta:+.1f}% vs 19% Goal")
    kpi_cols[2].metric("Turn Time Goal", "45 min")

st.markdown("---")

# --- Multi-Store Dynamic Resolution ---
store_names = []
unique_locs = []

if not filtered_df.empty and 'locationId' in filtered_df.columns:
    unique_locs = filtered_df['locationId'].dropna().unique()
    for loc in unique_locs:
        # Fallback for integer locations matching typical api maps
        if loc in loc_map:
            store_names.append(f"{loc} - {loc_map[loc]}")
        # Allow string Site names from CSV overlays
        elif isinstance(loc, str) and loc.isdigit() and int(loc) in loc_map:
            store_names.append(f"{loc} - {loc_map[int(loc)]}")
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
        loc_df = filtered_df[filtered_df['locationId'] == loc].copy()
        if not loc_df.empty:
            render_kpi_row(loc_df, prefix="Store")
            render_table_turns(loc_df, key=f"store_turns_{loc}")
        else:
            st.info("No qualitative check data available for this timeline.")

with tab2:
    # Build a human-readable date range for the WhatsApp card header
    if start_date == end_date:
        _date_str = start_date.strftime("%b %d, %Y")
    else:
        _date_str = f"{start_date.strftime('%b %d')} – {end_date.strftime('%b %d, %Y')}"

    st.markdown("### 🏢 Market Total Leaderboard")
    _market_title = "Market Total" if len(unique_locs) > 1 else (store_names[0] if store_names else "Market Total")
    render_combined_leaderboard(filtered_df, key="market_total_leaderboard",
                                title=_market_title, date_range_str=_date_str)
    
    for i, loc in enumerate(unique_locs):
        st.markdown("---")
        st.markdown(f"#### 📍 {store_names[i]}")
        loc_df = filtered_df[filtered_df['locationId'] == loc].copy()
        if not loc_df.empty:
            render_combined_leaderboard(loc_df, key=f"store_leaderboard_{loc}",
                                        title=store_names[i], date_range_str=_date_str)
        else:
            st.info("No server data available for this timeline.")

with tab3:
    st.markdown("### Combined Raw Ticket Log")
    if not filtered_df.empty:
        st.dataframe(filtered_df.drop(columns=['openTimeObj', 'closeTimeObj'], errors='ignore'), use_container_width=True, height=600)
    else:
        st.info("No raw data available to summarize.")
    
# Footer
st.markdown("<br><hr><center><small>Powered by Rosnet API | Data retrieved automatically</small></center>", unsafe_allow_html=True)

