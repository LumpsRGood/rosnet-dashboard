import textwrap
from io import BytesIO

import streamlit as st
import plotly.graph_objects as go
import plotly.express as px
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle
import uuid

def style_metric_cards():
    """
    Injects custom CSS to make Streamlit's native metric cards look premium.
    Uses glassmorphism effects and modern borders.
    """
    st.markdown('''
        <style>
        div[data-testid="metric-container"] {
            background-color: var(--secondary-background-color);
            color: var(--text-color);
            border: 1px solid var(--faint-text-color);
            padding: 20px;
            border-radius: 15px;
            box-shadow: 0 4px 6px rgba(0, 0, 0, 0.2);
            transition: transform 0.2s ease, box-shadow 0.2s ease;
        }
        div[data-testid="metric-container"]:hover {
            transform: translateY(-5px);
            box-shadow: 0 8px 15px rgba(0, 0, 0, 0.1);
            border: 1px solid var(--faint-text-color);
        }
        /* Make the delta values pop */
        div[data-testid="stMetricDelta"] > div {
            font-weight: 700;
        }
        /* Larger, more prominent tabs */
        button[data-baseweb="tab"] {
            font-size: 18px !important;
            font-weight: 600 !important;
            padding: 14px 24px !important;
        }
        button[data-baseweb="tab"][aria-selected="true"] {
            font-weight: 700 !important;
        }
        </style>
    ''', unsafe_allow_html=True)

def render_sales_chart(df: pd.DataFrame, key: str = None):
    """
    Renders a premium line chart for Sales Overview.
    If df has columns: 'businessDate' and 'netSales', 'driveThruSales'
    """
    if df.empty:
        st.info("No sales data available for this date range.")
        return

    # Group by date to aggregate data across locations if needed
    daily_sales = df.groupby('businessDate').agg({
        'netSales': 'sum',
        'driveThruSales': 'sum'
    }).reset_index()

    fig = go.Figure()

    # Add Net Sales line (Gradient fill area)
    fig.add_trace(go.Scatter(
        x=daily_sales['businessDate'], 
        y=daily_sales['netSales'],
        mode='lines+markers',
        name='Total Net Sales',
        line=dict(color='#00ff88', width=3),
        marker=dict(size=8, color='#00ff88', line=dict(width=2, color='white')),
        fill='tozeroy',
        fillcolor='rgba(0, 255, 136, 0.1)'
    ))

    # Add Drive Thru line
    if 'driveThruSales' in daily_sales.columns and daily_sales['driveThruSales'].sum() > 0:
        fig.add_trace(go.Scatter(
            x=daily_sales['businessDate'],
            y=daily_sales['driveThruSales'],
            mode='lines',
            name='Drive-Thru Sales',
            line=dict(color='#00d2ff', width=3, dash='dot')
        ))

    fig.update_layout(
        title="Revenue Tracking",
        title_font=dict(size=22, family="Inter"),
        plot_bgcolor='rgba(0,0,0,0)',
        paper_bgcolor='rgba(0,0,0,0)',
        xaxis=dict(showgrid=False, title=""),
        yaxis=dict(showgrid=True, title="Sales ($)", tickprefix="$"),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1, font=dict()),
        margin=dict(l=20, r=20, t=60, b=20),
        hovermode="x unified"
    )

    unique_key = f"{key}_{uuid.uuid4().hex}" if key else uuid.uuid4().hex
    st.plotly_chart(fig, use_container_width=True, key=unique_key)


def render_labor_breakdown(df: pd.DataFrame, key: str = None):
    """
    Renders a bar chart for Labor Costs by Job Code.
    Requires columns: 'jobCode' and 'regularPay'
    """
    if df.empty:
        st.info("No labor data available.")
        return

    labor_costs = df.groupby('jobCode')['regularPay'].sum().reset_index()
    labor_costs = labor_costs.sort_values(by='regularPay', ascending=True)

    fig = px.bar(
        labor_costs, 
        x='regularPay', 
        y='jobCode', 
        orientation='h',
        color='regularPay',
        color_continuous_scale=['#00d2ff', '#3a7bd5'], # Premium blue gradient
        text_auto='.2s',
        labels={'regularPay': 'Total Labor Cost ($)', 'jobCode': ''}
    )

    fig.update_layout(
        title="Labor Cost by Job Code",
        title_font=dict(size=20, family="Inter"),
        plot_bgcolor='rgba(0,0,0,0)',
        paper_bgcolor='rgba(0,0,0,0)',
        xaxis=dict(showgrid=True),
        yaxis=dict(showgrid=False),
        coloraxis_showscale=False,
        margin=dict(l=20, r=40, t=60, b=20)
    )
    
    # Custom hover template
    fig.update_traces(
        textfont_size=12, textangle=0, textposition="outside", cliponaxis=False,
        hovertemplate="<b>%{y}</b><br>Cost: $%{x:.2f}<extra></extra>"
    )

    unique_key = f"{key}_{uuid.uuid4().hex}" if key else uuid.uuid4().hex
    st.plotly_chart(fig, use_container_width=True, key=unique_key)


def render_inventory_treemap(df: pd.DataFrame, key: str = None):
    """
    Renders a treemap of inventory items based on on-hand value.
    Requires columns: 'category', 'productName', 'totalCost'
    """
    if df.empty:
        st.info("No inventory data available.")
        return
        
    fig = px.treemap(
        df,
        path=[px.Constant("All Inventory"), 'category', 'productName'],
        values='totalCost',
        color='totalCost',
        color_continuous_scale='Purpor',
    )
    
    fig.update_layout(
        title="Inventory Value Top Contributors",
        title_font=dict(size=20, family="Inter"),
        plot_bgcolor='rgba(0,0,0,0)',
        paper_bgcolor='rgba(0,0,0,0)',
        margin=dict(t=50, l=10, r=10, b=10)
    )
    
    fig.update_traces(
        hovertemplate="<b>%{label}</b><br>Value: $%{value:.2f}<extra></extra>"
    )

    unique_key = f"{key}_{uuid.uuid4().hex}" if key else uuid.uuid4().hex
    st.plotly_chart(fig, use_container_width=True, key=unique_key)

def render_table_turns(df: pd.DataFrame, key: str = None):
    """
    Renders an area chart showing average table turn time (minutes) per day.
    """
    if df.empty or 'turnTimeMinutes' not in df.columns:
        st.info("No check data matching these filters.")
        return
        
    # Average turns across all tickets for each day
    avg_turns = df.groupby('businessDate')['turnTimeMinutes'].mean().reset_index()
    
    fig = px.area(
        avg_turns,
        x='businessDate',
        y='turnTimeMinutes',
        color_discrete_sequence=['#3b82f6'], # Tailwind Blue 500
        markers=True
    )
    
    # Add a target goal line at 45 minutes
    fig.add_hline(y=45, line_dash="dot", line_color="#ef4444", annotation_text="45m Goal", annotation_position="top right")
    
    fig.update_layout(
        title="Average Daily Turn Time (Open to Close)",
        title_font=dict(size=22, family="Inter"),
        plot_bgcolor='rgba(0,0,0,0)',
        paper_bgcolor='rgba(0,0,0,0)',
        xaxis=dict(showgrid=False, title=""),
        yaxis=dict(showgrid=True, title="Avg Time (Minutes)"),
        margin=dict(l=20, r=20, t=60, b=20),
        hovermode="x unified"
    )
    
    fig.update_traces(
        fillcolor="rgba(59, 130, 246, 0.2)",
        line=dict(width=3),
        hovertemplate="<b>%{x}</b><br>Avg Turn Time: %{y:.1f} mins<extra></extra>"
    )

    unique_key = f"{key}_{uuid.uuid4().hex}" if key else uuid.uuid4().hex
    st.plotly_chart(fig, use_container_width=True, key=unique_key)

def render_server_leaderboard(df: pd.DataFrame, key: str = None):
    """
    Renders the custom styled server performance leaderboard (Green/Yellow/Red).
    """
    if df.empty or 'serverName' not in df.columns or 'turnTimeMinutes' not in df.columns:
        st.info("No server breakdown available yet.")
        return

    # Aggregate server data
    server_df = df.groupby('serverName')['turnTimeMinutes'].mean().reset_index()
    store_avg = df['turnTimeMinutes'].mean()
    
    # Append store average as a mock 'server'
    avg_row = pd.DataFrame([{"serverName": "STORE AVERAGE", "turnTimeMinutes": store_avg}])
    server_df = pd.concat([server_df, avg_row], ignore_index=True)
    
    server_df = server_df.sort_values(by='turnTimeMinutes', ascending=False)
    
    def get_color(val, name):
        if name == "STORE AVERAGE":
            return '#94a3b8' # Slate 400 for average
        if val < 40:
            return '#22c55e' # Green 500
        elif 40 <= val <= 45:
            return '#eab308' # Yellow 500
        else:
            return '#ef4444' # Red 500
    server_df['color'] = server_df.apply(lambda row: get_color(row['turnTimeMinutes'], row['serverName']), axis=1)

    # Force Plotly array ordering by generating the exact list physically bottom-to-top
    y_axis_order = server_df.sort_values(by='turnTimeMinutes', ascending=True)['serverName'].tolist()

    dynamic_height = max(500, len(server_df) * 45) # Expanded height multiplier completely protects from vertical compression

    fig = px.bar(
        server_df,
        x='turnTimeMinutes',
        y='serverName',
        orientation='h',
        color='color',
        color_discrete_map='identity',
        height=dynamic_height
    )
    
    fig.update_traces(
        hovertemplate="<b>%{y}</b><br>Avg Turn Time: %{x:.1f} mins<extra></extra>"
    )
    
    # Loop for absolutely bulletproof Left Justification!
    for _, row in server_df.iterrows():
        fig.add_annotation(
            x=0.01, xref='paper', # Pin exactly 1% inside the left margin
            y=row['serverName'], yref='y',
            text=f"<b>{row['serverName']}  |  {row['turnTimeMinutes']:.1f} mins</b>",
            xanchor='left',
            yanchor='middle',
            showarrow=False,
            font=dict(size=15, color='#ffffff'), # Sharp white contrast against the red/green bars
            align='left' # Guarantees strict left formatting
        )
    
    # V-lines for goals
    fig.add_vline(x=40, line_dash="dash", line_color="#22c55e", line_width=2)
    fig.add_vline(x=45, line_dash="dash", line_color="#ef4444", line_width=2)
    
    # Place text directly above the plotting area to stop overlap with topmost bar
    fig.add_annotation(
        x=40, y=1.01, yref="paper",
        text="<b>Peachtree Goal</b>", 
        showarrow=False, 
        xanchor="right", yanchor="bottom",
        font=dict(size=20, color="#15803d")
    )
    fig.add_annotation(
        x=45, y=1.01, yref="paper",
        text="<b>IHOP goal</b>", 
        showarrow=False, 
        xanchor="left", yanchor="bottom",
        font=dict(size=20, color="#b91c1c")
    )
    
    fig.update_layout(
        title="Eat-In Turn Time Leaderboard (Goal: <40m)",
        title_font=dict(size=22, family="Inter"),
        plot_bgcolor='rgba(0,0,0,0)',
        paper_bgcolor='rgba(0,0,0,0)',
        xaxis=dict(
            showgrid=True, 
            title="Avg Turn Time (Minutes)",
            tickfont=dict(size=14),
            title_font=dict(size=16)
        ),
        yaxis=dict(
            type='category',     # Absolute Fix: Force strict category handling
            categoryorder='array', 
            categoryarray=y_axis_order, # Absolute Fix: Plotly is explicitly told what each Y-tick is called, preventing arbitrary consolidation of identical integers.
            showgrid=False, 
            showticklabels=False, 
            title="", 
        ),
        margin=dict(l=10, r=60, t=100, b=20) # Collapsed left padding
    )

    unique_key = f"{key}_{uuid.uuid4().hex}" if key else uuid.uuid4().hex
    st.plotly_chart(
        fig, 
        use_container_width=True,
        key=unique_key,
        config={
            'displayModeBar': True, # Keep it visible
            'toImageButtonOptions': {
                'format': 'png',
                'filename': 'Server_Performance_Leaderboard',
                'scale': 2 # Sharp resolution
            }
        }
    )

def render_combined_leaderboard(df: pd.DataFrame, key: str = None):
    """
    Renders combined server performance leaderboard with Turn Time and Dine In Bev %.
    Design: Option C KPI cards (3 across, dynamically colored) + Option A table (solid cell colors).
    """
    if df.empty or 'serverName' not in df.columns or 'turnTimeMinutes' not in df.columns:
        st.info("No server breakdown available yet.")
        return

    work_df = df.copy()
    if 'beverageSales' not in work_df.columns:
        work_df['beverageSales'] = 0
    if 'netSales' not in work_df.columns:
        work_df['netSales'] = 0

    # Aggregate per server
    server_stats = work_df.groupby('serverName').agg(
        avgTurn=('turnTimeMinutes', 'mean'),
        totalBevSales=('beverageSales', 'sum'),
        totalNetSales=('netSales', 'sum'),
    ).reset_index()

    server_stats['bevPct'] = server_stats.apply(
        lambda r: (r['totalBevSales'] / r['totalNetSales'] * 100) if r['totalNetSales'] > 0 else 0, axis=1
    )

    # Overall averages
    overall_turn = work_df['turnTimeMinutes'].mean()
    total_net = work_df['netSales'].sum()
    overall_bev = (work_df['beverageSales'].sum() / total_net * 100) if total_net > 0 else 0

    # Best / Worst
    best_turn = server_stats.loc[server_stats['avgTurn'].idxmin()]
    worst_turn = server_stats.loc[server_stats['avgTurn'].idxmax()]
    best_bev = server_stats.loc[server_stats['bevPct'].idxmax()]
    worst_bev = server_stats.loc[server_stats['bevPct'].idxmin()]

    # Scoring
    def greens_count(row):
        count = 0
        if row['avgTurn'] <= 40: count += 1
        if row['bevPct'] >= 19: count += 1
        return count

    server_stats['greens'] = server_stats.apply(greens_count, axis=1)
    all_green = int((server_stats['greens'] == 2).sum())
    total_servers = len(server_stats)
    server_stats = server_stats.sort_values(by=['greens', 'avgTurn', 'bevPct'], ascending=[False, True, False])

    # --- Dynamic KPI card colors ---
    def turn_card_color(val):
        if val <= 40: return ('#22c55e', 'rgba(34,197,94,0.15)', 'rgba(34,197,94,0.4)')
        elif val <= 45: return ('#eab308', 'rgba(234,179,8,0.15)', 'rgba(234,179,8,0.4)')
        else: return ('#ef4444', 'rgba(239,68,68,0.15)', 'rgba(239,68,68,0.4)')

    def bev_card_color(val):
        if val >= 19: return ('#22c55e', 'rgba(34,197,94,0.15)', 'rgba(34,197,94,0.4)')
        elif val >= 18: return ('#eab308', 'rgba(234,179,8,0.15)', 'rgba(234,179,8,0.4)')
        else: return ('#ef4444', 'rgba(239,68,68,0.15)', 'rgba(239,68,68,0.4)')

    tc, tbg, tbr = turn_card_color(overall_turn)
    bc, bbg, bbr = bev_card_color(overall_bev)
    # All-Green card: always purple (informational, not a metric)
    gc, gbg, gbr = '#a855f7', 'rgba(168,85,247,0.15)', 'rgba(168,85,247,0.4)'

    # --- KPI Cards (Option C style — 3 across, dynamically colored) ---
    kpi_html = (
        '<div style="display: flex; gap: 12px; margin-bottom: 20px;">'
        f'<div style="flex: 1; background: {tbg}; border: 2px solid {tbr}; border-radius: 12px; padding: 16px;">'
        f'<div style="font-size: 13px; font-weight: 600; text-transform: uppercase; color: {tc}; letter-spacing: 0.05em;">Avg Turn Time</div>'
        f'<div style="font-size: 32px; font-weight: 800; margin: 4px 0;">{overall_turn:.1f} min</div>'
        f'<div style="font-size: 13px; margin-top: 6px;">Best: <strong>{best_turn["serverName"]}</strong></div>'
        f'<div style="font-size: 13px;">Slowest: <strong>{worst_turn["serverName"]}</strong></div>'
        '</div>'
        f'<div style="flex: 1; background: {bbg}; border: 2px solid {bbr}; border-radius: 12px; padding: 16px;">'
        f'<div style="font-size: 13px; font-weight: 600; text-transform: uppercase; color: {bc}; letter-spacing: 0.05em;">Avg Dine In Bev %</div>'
        f'<div style="font-size: 32px; font-weight: 800; margin: 4px 0;">{overall_bev:.1f}%</div>'
        f'<div style="font-size: 13px; margin-top: 6px;">Top: <strong>{best_bev["serverName"]}</strong></div>'
        f'<div style="font-size: 13px;">Bottom: <strong>{worst_bev["serverName"]}</strong></div>'
        '</div>'
        f'<div style="flex: 1; background: {gbg}; border: 2px solid {gbr}; border-radius: 12px; padding: 16px;">'
        f'<div style="font-size: 13px; font-weight: 600; text-transform: uppercase; color: {gc}; letter-spacing: 0.05em;">All-Green Servers</div>'
        f'<div style="font-size: 32px; font-weight: 800; margin: 4px 0;">{all_green} of {total_servers}</div>'
        f'<div style="font-size: 13px; margin-top: 6px; color: #94a3b8;">Turn ≤40m &amp; Bev ≥19%</div>'
        '</div>'
        '</div>'
    )
    st.markdown(kpi_html, unsafe_allow_html=True)

    # --- Color helpers (solid fills for table cells) ---
    def turn_cell_bg(val):
        if val <= 40: return '#6fdc8c'
        elif val <= 45: return '#ffe066'
        else: return '#ff6b6b'

    def bev_cell_bg(val):
        if val >= 19: return '#6fdc8c'
        elif val >= 18: return '#ffe066'
        else: return '#ff6b6b'

    def cell_text_color(bg):
        return '#ffffff' if bg == '#ff6b6b' else '#222222'

    # --- Build Option A style table (solid colored cells, high contrast) ---
    rows_html = ""
    for _, row in server_stats.iterrows():
        t_bg = turn_cell_bg(row['avgTurn'])
        b_bg = bev_cell_bg(row['bevPct'])
        t_tc = cell_text_color(t_bg)
        b_tc = cell_text_color(b_bg)
        rows_html += (
            '<tr>'
            f'<td style="padding: 10px 16px; font-weight: 600; font-size: 15px; border-bottom: 1px solid rgba(128,128,128,0.15);">{row["serverName"]}</td>'
            f'<td style="padding: 10px 16px; text-align: center; background: {t_bg}; color: {t_tc}; font-weight: 700; font-size: 15px; border-bottom: 1px solid rgba(255,255,255,0.2);">{row["avgTurn"]:.2f}</td>'
            f'<td style="padding: 10px 16px; text-align: center; background: {b_bg}; color: {b_tc}; font-weight: 700; font-size: 15px; border-bottom: 1px solid rgba(255,255,255,0.2);">{row["bevPct"]:.2f}%</td>'
            '</tr>'
        )

    table_html = (
        '<div style="overflow-x: auto; margin-top: 4px;">'
        '<table style="width: 100%; border-collapse: collapse;">'
        '<thead><tr style="background: rgba(128,128,128,0.15);">'
        '<th style="padding: 12px 16px; text-align: left; font-size: 14px; font-weight: 700;">Server</th>'
        '<th style="padding: 12px 16px; text-align: center; font-size: 14px; font-weight: 700;">Turn Time</th>'
        '<th style="padding: 12px 16px; text-align: center; font-size: 14px; font-weight: 700;">Dine In Bev %</th>'
        '</tr></thead>'
        f'<tbody>{rows_html}</tbody>'
        '</table></div>'
        '<div style="margin-top: 10px; display: flex; gap: 16px; font-size: 12px; color: #94a3b8;">'
        '<span>🟢 Turn ≤40m / Bev ≥19%</span>'
        '<span>🟡 Turn 40-45m / Bev 18-19%</span>'
        '<span>🔴 Turn &gt;45m / Bev &lt;18%</span>'
        '</div>'
    )
    st.markdown(table_html, unsafe_allow_html=True)

    # --- WhatsApp Card Download (matplotlib PNG) ---
    card_label = key or 'scorecard'
    fig = _create_whatsapp_card(
        card_label, server_stats, overall_turn, overall_bev,
        best_turn, worst_turn, best_bev, worst_bev,
    )
    buf = BytesIO()
    fig.savefig(buf, format='png', dpi=200, bbox_inches='tight', facecolor='white')
    buf.seek(0)
    plt.close(fig)
    safe_label = card_label.replace(' ', '_').replace('-', '_')
    st.download_button(
        label=f"📲 Download WhatsApp Card",
        data=buf,
        file_name=f"{safe_label}_scorecard.png",
        mime="image/png",
        key=f"dl_{safe_label}_{uuid.uuid4().hex[:6]}",
    )


def _wrap_names(text, width=22):
    if not text or text == "No data":
        return text
    return "\n".join(textwrap.wrap(text, width=width, break_long_words=False))


def _create_whatsapp_card(label, server_stats, overall_turn, overall_bev,
                           best_turn, worst_turn, best_bev, worst_bev):
    """Generates a matplotlib figure matching the user's existing WhatsApp card style."""
    # Prepare export data
    export_df = server_stats[['serverName', 'avgTurn', 'bevPct']].copy()
    export_df.columns = ['Server', 'Turn Time', 'Dine In Bev %']
    export_df['Turn Time'] = export_df['Turn Time'].apply(lambda x: f"{x:.2f}")
    export_df['Dine In Bev %'] = export_df['Dine In Bev %'].apply(lambda x: f"{x:.2f}%")

    row_count = len(export_df)
    fig_height = max(8.0, 4.8 + (row_count * 0.48))
    fig, ax = plt.subplots(figsize=(7.5, fig_height))
    fig.patch.set_facecolor('white')
    ax.set_axis_off()

    # Outer border
    ax.add_patch(Rectangle(
        (0.01, 0.01), 0.98, 0.98,
        transform=ax.transAxes, facecolor='white',
        edgecolor='#d7dee8', linewidth=1.2, zorder=0
    ))

    # Header bar (IHOP blue)
    ax.add_patch(Rectangle(
        (0.01, 0.91), 0.98, 0.08,
        transform=ax.transAxes, facecolor='#1d4f91',
        edgecolor='#1d4f91', zorder=1
    ))
    ax.text(
        0.03, 0.95, label,
        transform=ax.transAxes, fontsize=17, fontweight='bold',
        color='white', va='center', zorder=2
    )

    # --- KPI Lanes ---
    lane_y = 0.68
    lane_h = 0.19
    lane_w = 0.45
    lane_gap = 0.04
    lane_xs = [0.03, 0.03 + lane_w + lane_gap]

    def turn_box_color(x):
        if pd.isna(x): return '#f5f8fc'
        if x <= 40: return '#6fdc8c'
        elif x <= 45: return '#ffe066'
        return '#ff6b6b'

    def bev_box_color(x):
        if pd.isna(x): return '#f5f8fc'
        if x >= 19: return '#6fdc8c'
        elif x >= 18: return '#ffe066'
        return '#ff6b6b'

    def box_text_color(fill):
        return 'white' if fill in ['#ff6b6b', '#1d4f91'] else '#222222'

    lane_data = [
        (
            'TURN', 'Avg Turn',
            f'{overall_turn:.2f}',
            'Best', _wrap_names(best_turn['serverName']),
            'Slowest', _wrap_names(worst_turn['serverName']),
            turn_box_color(overall_turn),
        ),
        (
            'BEVERAGE', 'Avg Dine In Bev %',
            f'{overall_bev:.2f}%',
            'Top', _wrap_names(best_bev['serverName']),
            'Bottom', _wrap_names(worst_bev['serverName']),
            bev_box_color(overall_bev),
        ),
    ]

    for lane_x, lane in zip(lane_xs, lane_data):
        title, avg_label, avg_value, label1, value1, label2, value2, fill_color = lane
        text_color = box_text_color(fill_color)

        ax.add_patch(Rectangle(
            (lane_x, lane_y), lane_w, lane_h,
            transform=ax.transAxes, facecolor=fill_color,
            edgecolor='#cfd9e6', linewidth=1, zorder=1
        ))
        ax.text(lane_x + 0.015, lane_y + lane_h - 0.025, title,
                transform=ax.transAxes, fontsize=11, fontweight='bold',
                color=text_color, va='top', zorder=2)
        ax.text(lane_x + 0.015, lane_y + lane_h - 0.055, avg_label,
                transform=ax.transAxes, fontsize=8.8, color=text_color,
                va='top', zorder=2)
        ax.text(lane_x + 0.015, lane_y + lane_h - 0.082, avg_value,
                transform=ax.transAxes, fontsize=12, fontweight='bold',
                color=text_color, va='top', zorder=2)
        ax.text(lane_x + 0.015, lane_y + lane_h - 0.118, f"{label1}: {value1}",
                transform=ax.transAxes, fontsize=8.4, color=text_color,
                va='top', zorder=2)
        ax.text(lane_x + 0.015, lane_y + lane_h - 0.162, f"{label2}: {value2}",
                transform=ax.transAxes, fontsize=8.4, color=text_color,
                va='top', zorder=2)

    # --- Data Table ---
    table_bbox = [0.03, 0.04, 0.94, 0.60]
    table = ax.table(
        cellText=export_df.values,
        colLabels=export_df.columns,
        cellLoc='left',
        loc='center',
        bbox=table_bbox
    )
    table.auto_set_font_size(False)
    table.set_fontsize(9.8)
    table.scale(1, 1.45)

    ncols = len(export_df.columns)

    # Style header row
    for col_idx in range(ncols):
        hdr = table[0, col_idx]
        hdr.set_text_props(weight='bold', color='white')
        hdr.set_facecolor('#2d6cb5')
        hdr.set_edgecolor('#d7dee8')

    # Color data cells
    for row_idx in range(1, row_count + 1):
        original = server_stats.iloc[row_idx - 1]
        for col_idx in range(ncols):
            cell = table[row_idx, col_idx]
            cell.set_edgecolor('#dfe5ec')
            if row_idx % 2 == 0:
                cell.set_facecolor('#fbfcfe')
            else:
                cell.set_facecolor('white')

        # Turn Time cell (col 1)
        t_val = original['avgTurn']
        t_fill = turn_box_color(t_val)
        t_text = box_text_color(t_fill)
        table[row_idx, 1].set_facecolor(t_fill)
        table[row_idx, 1].set_text_props(weight='bold', color=t_text)

        # Bev % cell (col 2)
        b_val = original['bevPct']
        b_fill = bev_box_color(b_val)
        b_text = box_text_color(b_fill)
        table[row_idx, 2].set_facecolor(b_fill)
        table[row_idx, 2].set_text_props(weight='bold', color=b_text)

    return fig
