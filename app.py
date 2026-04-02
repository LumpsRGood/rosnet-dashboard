import streamlit as st
import pandas as pd
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import api
from components import style_metric_cards, render_table_turns, render_server_leaderboard
st.set_page_config(
    page_title="Rosnet Insights Dashboard",
    page_icon="🍔",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Inject metric card styles
style_metric_cards()

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
st.sidebar.image("https://plus.unsplash.com/premium_photo-1661882196621-3e4cdb58dcf4?auto=format&fit=crop&q=80&w=300", 
                 caption="Rosnet Insights", use_container_width=True)
st.sidebar.header("Dashboard Filters")

# Filter logic
try:
    # Anchor to Eastern Time to prevent UTC servers from rolling over 'today' prematurely
    tz = ZoneInfo("America/New_York")
    today = datetime.now(tz).date()
except Exception:
    today = datetime.now().date()
    
yesterday = today - timedelta(days=1)

date_method = st.sidebar.radio("Date Selection Method", ["Quick Select", "Custom Range"], horizontal=True)

if date_method == "Quick Select":
    quick_choice = st.sidebar.selectbox("Range", ["Yesterday", "Last 7 Days", "Last Week", "Last Month"])
    
    if quick_choice == "Yesterday":
        start_date = end_date = yesterday
    elif quick_choice == "Last 7 Days":
        start_date = yesterday - timedelta(days=6)
        end_date = yesterday
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
        max_value=yesterday
    )
    start_date = end_date = yesterday
    if isinstance(date_range, tuple) and len(date_range) == 2:
        start_date, end_date = date_range
    elif isinstance(date_range, tuple) and len(date_range) == 1:
        start_date = end_date = date_range[0]
    else:
        start_date = end_date = date_range

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
    "Select Locations (Area Config)",
    options=list(loc_map.keys()),
    format_func=lambda x: f"{x} - {loc_map.get(x, 'Unknown')}",
    default=[] # Do not autoselect sites to prevent unintentional API polling
)

# Dates are already fully processed by the date_method selection above

# --- Dashboard Input Finalization ---
st.sidebar.markdown("---")
st.sidebar.subheader("Local Data Overlay")
uploaded_files = st.sidebar.file_uploader("Upload POS CSV Override", type=["csv"], accept_multiple_files=True)

# --- Main Content ---
st.title("Area Director Pulse 📈")
st.warning("🚧 **Under Development:** This dashboard is currently in active testing. Errors may occasionally occur. Please contact **Chad** with any issues, feedback, or UI suggestions.")

if api.MOCK_MODE:
    st.warning("⚠️ Running in Mock Mode. Please add credentials to .env to pull real Rosnet data.")

if len(selected_locations) == 0 and not uploaded_files:
    st.info("👋 **Welcome to Rosnet Insights!**\n\nPlease select one or more locations from the sidebar, or drop a POS CSV into the overlay to begin your analysis.")
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
    all_checks = []
    for loc_id in locs:
        emp_map = api.get_employees_map(loc_id)
        checks_data = api.get_checks(sd_str, ed_str, loc_id, emp_map=emp_map)
        if checks_data:
            all_checks.extend(checks_data)
    return _normalize_df(all_checks)

ALIASES = {
    "Opened": ["opened", "open", "order start", "start time", "opened at"],
    "Closed": ["closed", "close", "order end", "end time", "closed at"],
    "Service": ["service", "service type", "order type"],
    "Created By": ["created by", "server", "server name", "employee", "cashier"],
    "Site": ["site", "location", "store", "restaurant"]
}

def pick_col(df: pd.DataFrame, candidates):
    for c in df.columns:
        lc = c.strip().lower()
        for a in candidates:
            if lc == a.lower() or a.lower() in lc:
                return c
    return None

# (CSV Input Uploader Sidebar Definition moved to top level logic gate)

checks_dfs = []
if uploaded_files:
    for upl in uploaded_files:
        try:
            df = pd.read_csv(upl)
            col_open = pick_col(df, ALIASES["Opened"])
            col_close = pick_col(df, ALIASES["Closed"])
            col_service = pick_col(df, ALIASES["Service"])
            col_server = pick_col(df, ALIASES["Created By"])
            col_site = pick_col(df, ALIASES["Site"])
            
            # Bridge to Rosnet Dashboard column mapping
            mapped = pd.DataFrame()
            if col_open: mapped['openTime'] = df[col_open]
            if col_close: mapped['closeTime'] = df[col_close]
            if col_service: mapped['orderType'] = df[col_service]
            if col_server: mapped['serverName'] = df[col_server]
            if col_site: mapped['locationId'] = df[col_site]
            
            # Since CSV doesn't usually list payments, assume Credit Card to pass the Dashboard filter
            mapped['paymentType'] = 'Credit Card' 
            # Bypassing business date for simplicity since Time often contains dates
            mapped['businessDate'] = pd.to_datetime(df[col_open], errors='coerce').dt.strftime('%Y-%m-%d')
            
            checks_dfs.append(mapped)
        except Exception as e:
            st.sidebar.error(f"Error parsing {upl.name}: {e}")

if checks_dfs:
    checks_df = pd.concat(checks_dfs, ignore_index=True)
    st.info(f"Loaded {len(checks_df)} records from {len(uploaded_files)} local CSV file(s).")
else:
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
        total_qualifying_tickets = len(df)
        avg_turn_time = df['turnTimeMinutes'].mean()
        delta_goal = round(avg_turn_time - 45, 1)
    else:
        total_qualifying_tickets = 0
        avg_turn_time = 0.0
        delta_goal = 0.0

    kpi_cols[0].metric(f"{prefix} Avg Turn Time", f"{avg_turn_time:.1f} min", f"{delta_goal:+.1f} min vs 45m Goal", delta_color="inverse")
    kpi_cols[1].metric(f"{prefix} Qualifying Tickets", f"{total_qualifying_tickets:,}")
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
    st.markdown("### 🏢 Market Total Leaderboard")
    render_kpi_row(filtered_df, prefix="Market")
    render_server_leaderboard(filtered_df, key="market_total_leaderboard")
    
    for i, loc in enumerate(unique_locs):
        st.markdown("---")
        st.markdown(f"#### 📍 {store_names[i]}")
        loc_df = filtered_df[filtered_df['locationId'] == loc].copy()
        if not loc_df.empty:
            render_kpi_row(loc_df, prefix="Store")
            render_server_leaderboard(loc_df, key=f"store_leaderboard_{loc}")
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

