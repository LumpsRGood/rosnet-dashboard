import streamlit as st
import plotly.graph_objects as go
import plotly.express as px
import pandas as pd
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
    
    fig.add_vline(
        x=40, line_dash="dash", line_color="#22c55e", line_width=2,
        annotation_text="<b>Peach Tree goal</b>", 
        annotation_position="top left",
        annotation_font=dict(size=20, color="#15803d") # Deep green, pronounced
    )
    fig.add_vline(
        x=45, line_dash="dash", line_color="#ef4444", line_width=2,
        annotation_text="<b>IHOP goal</b>", 
        annotation_position="top right",
        annotation_font=dict(size=20, color="#b91c1c") # Deep red, pronounced
    )
    
    fig.update_layout(
        title="Eat-In Turn Time Leaderboard (Goal: <40m)",
        title_font=dict(size=22, family="Inter"),
        plot_bgcolor='rgba(0,0,0,0)',
        paper_bgcolor='rgba(0,0,0,0)',
        xaxis=dict(
            showgrid=True, 
            gridcolor='rgba(0,0,0,0.1)', 
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
