import streamlit as st
import pandas as pd
import numpy as np
from sklearn.ensemble import RandomForestRegressor
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder
from sklearn.compose import ColumnTransformer
import altair as alt

st.set_page_config(page_title="SFC Live Command", page_icon="🍗", layout="wide")

# ==========================================
# 1. SECURE LINK LOADING
# ==========================================
try:
    # This grabs the link from your private secrets file
    SHEET_LINK = st.secrets["sheet_url"]
except FileNotFoundError:
    st.error("⚠️ Secrets file not found! If running locally, make sure .streamlit/secrets.toml exists.")
    st.stop()
except KeyError:
    st.error("⚠️ 'sheet_url' not found in secrets file.")
    st.stop()
# ==========================================

# --- CUSTOM CSS ---
st.markdown("""
    <style>
    div[data-testid="metric-container"] {
        border: 1px solid rgba(128, 128, 128, 0.5);
        padding: 15px;
        border-radius: 10px;
        background-color: rgba(255, 255, 255, 0.05);
    }
    div[data-testid="stMetricValue"] {
        font-size: 28px !important;
        font-weight: bold;
    }
    </style>
    """, unsafe_allow_html=True)

# ==========================================
# 2. DATA PARSER
# ==========================================
@st.cache_data(ttl=60)
def load_and_parse_data(link):
    if not link: return pd.DataFrame()
        
    try:
        # Convert link to export format
        if "docs.google.com" in link:
            export_url = link.replace("/edit?usp=sharing", "/export?format=csv")
            export_url = export_url.replace("/edit", "/export?format=csv")
        else:
            export_url = link
        
        df_raw = pd.read_csv(export_url, header=None)
        
        data_points = []
        row1_vals = df_raw.iloc[1, :].astype(str).values
        block_starts = [i for i, val in enumerate(row1_vals) if "Last stock" in val]

        for start_col in block_starts:
            date_val = df_raw.iloc[0, start_col]
            if pd.isna(date_val): date_val = df_raw.iloc[0, start_col+1]
            
            try:
                current_date = pd.to_datetime(date_val, dayfirst=True)
            except:
                continue 

            headers = df_raw.iloc[1, start_col:start_col+6].astype(str).values
            try:
                idx_stock = np.where([("Today Stock" in h) for h in headers])[0][0]
                idx_sold  = np.where([("Sold" in h) for h in headers])[0][0]
                idx_order = np.where([("order" in h and "ordered" not in h) for h in headers])[0][0]
            except:
                continue

            items = df_raw.iloc[2:, 0].values 
            
            for i, item in enumerate(items):
                if pd.isna(item): continue
                row_idx = i + 2
                
                try:
                    s_val = pd.to_numeric(df_raw.iloc[row_idx, start_col + idx_stock], errors='coerce')
                    sold_val = pd.to_numeric(df_raw.iloc[row_idx, start_col + idx_sold], errors='coerce')
                    o_val = pd.to_numeric(df_raw.iloc[row_idx, start_col + idx_order], errors='coerce')
                    
                    if pd.isna(s_val): s_val = 0
                    if pd.isna(sold_val): sold_val = 0
                    if pd.isna(o_val): continue 

                    data_points.append({
                        "Date": current_date,
                        "Day_of_Week": current_date.day_name(),
                        "Item": str(item).lower(),
                        "Stock_Level": s_val,
                        "Sold_Qty": sold_val,
                        "Ordered_Qty": o_val
                    })
                except:
                    continue

        df_clean = pd.DataFrame(data_points)
        
        valid_map = {
            '9cuts': '9cuts', 'halal 9-cut chicken 1.6kg': '9cuts',
            'fillets': 'fillets', 'halal chicken fillets 120 - 140g': 'fillets',
            'strips': 'strips', 'halal chicken strips': 'strips',
            'wings': 'wings', 'halal prime prime wings': 'wings'
        }
        df_clean['Item_Clean'] = df_clean['Item'].map(valid_map).fillna(df_clean['Item'])
        
        return df_clean.sort_values("Date")
        
    except Exception as e:
        return pd.DataFrame()

df = load_and_parse_data(SHEET_LINK)

# ==========================================
# 3. DASHBOARD
# ==========================================
st.title("🍗 SFC Winterton Command Center")

if df.empty:
    st.warning("waiting for connection...")
    st.stop()

latest_date = df['Date'].max().strftime('%d %b %Y')
st.caption(f"🟢 Connected Live | Last Data: {latest_date} | Records: {len(df)}")

tab1, tab2 = st.tabs(["🔮 ORDER PREDICTOR", "📊 SALES TRENDS"])

with tab1:
    X = df[['Day_of_Week', 'Item_Clean', 'Stock_Level']]
    y = df['Ordered_Qty']
    
    preprocessor = ColumnTransformer(transformers=[
        ('num', 'passthrough', ['Stock_Level']),
        ('cat', OneHotEncoder(handle_unknown='ignore'), ['Day_of_Week', 'Item_Clean'])
    ])
    
    model = Pipeline(steps=[
        ('preprocessor', preprocessor),
        ('model', RandomForestRegressor(n_estimators=100, random_state=42))
    ])
    
    model.fit(X, y)
    
    st.markdown("### 🛒 Enter Tonight's Stock")
    
    with st.form("order_calc"):
        col_day, col_blank = st.columns([1, 2])
        day = col_day.selectbox("Ordering For Which Night?", ["Monday", "Wednesday", "Friday"])
        
        c1, c2, c3, c4 = st.columns(4)
        s_9cuts = c1.number_input("9-Cuts", value=1.0, step=0.5)
        s_fillets = c2.number_input("Fillets", value=1.0, step=0.5)
        s_strips = c3.number_input("Strips", value=1.0, step=0.5)
        s_wings = c4.number_input("Wings", value=1.0, step=0.5)
        
        submitted = st.form_submit_button("CALCULATE ORDER", type="primary")

    if submitted:
        st.markdown("---")
        st.markdown(f"#### ✅ Recommended Order for {day} Night:")
        
        items = [("9cuts", s_9cuts), ("fillets", s_fillets), ("strips", s_strips), ("wings", s_wings)]
        res_cols = st.columns(4)
        
        for i, (name, stock) in enumerate(items):
            input_data = pd.DataFrame({'Day_of_Week': [day], 'Item_Clean': [name], 'Stock_Level': [stock]})
            pred = model.predict(input_data)[0]
            qty = max(0, int(round(pred)))
            
            with res_cols[i]:
                st.metric(label=name.upper(), value=f"{qty} Cases", delta=f"Stock: {stock}")

with tab2:
    st.markdown("### 📈 Chicken Usage Trends")
    
    avg_sold = df.groupby(['Day_of_Week', 'Item_Clean'])['Sold_Qty'].mean().reset_index()
    
    chart_bar = alt.Chart(avg_sold).mark_bar().encode(
        x=alt.X('Day_of_Week', sort=['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']),
        y='Sold_Qty',
        color='Item_Clean',
        tooltip=['Day_of_Week', 'Item_Clean', 'Sold_Qty']
    ).properties(title="Average Cases Sold per Day").interactive()
    
    st.altair_chart(chart_bar, use_container_width=True)
    
    total_vol = df.groupby('Item_Clean')['Sold_Qty'].sum().reset_index()
    chart_pie = alt.Chart(total_vol).mark_arc(innerRadius=50).encode(
        theta=alt.Theta(field="Sold_Qty", type="quantitative"),
        color=alt.Color(field="Item_Clean", type="nominal"),
        tooltip=['Item_Clean', 'Sold_Qty']
    ).properties(title="Total Consumption Share")
    
    st.altair_chart(chart_pie, use_container_width=True)

st.markdown("---")
st.caption("System linked to SFC Live Google Sheet. Updates automatically.")