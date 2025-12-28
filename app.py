import streamlit as st
import pandas as pd
import numpy as np
from sklearn.ensemble import RandomForestRegressor
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder
from sklearn.compose import ColumnTransformer
from datetime import datetime
import gspread
from oauth2client.service_account import ServiceAccountCredentials

# --- PAGE CONFIGURATION ---
st.set_page_config(page_title="SFC Inventory Manager", page_icon="🍗", layout="wide")

# --- PROFESSIONAL STYLING ---
st.markdown("""
    <style>
    .main-header { font-size: 2rem; font-weight: bold; color: #b30000; }
    .sub-header { font-size: 1.2rem; color: #333; margin-bottom: 20px; }
    .data-row { padding: 10px; border-bottom: 1px solid #eee; }
    .stButton button { width: 100%; background-color: #b30000; color: white; font-weight: bold; }
    div[data-testid="stMetricValue"] { font-size: 24px; }
    </style>
    """, unsafe_allow_html=True)

# ==========================================
# 1. GOOGLE SHEETS CONNECTION (WRITE ACCESS)
# ==========================================
def get_google_sheet_client():
    try:
        scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
        creds_dict = dict(st.secrets["gcp_service_account"])
        creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
        client = gspread.authorize(creds)
        return client
    except Exception as e:
        # If secrets are missing or wrong, we just return None and work in Read-Only mode
        return None

# ==========================================
# 2. DATA LOADING (THE HYBRID ENGINE)
# ==========================================
@st.cache_data(ttl=60)
def load_all_data():
    all_data = []

    # --- PART A: LOAD HISTORY (The "Block Format" Excel Sheet) ---
    try:
        # Get link from secrets or use a default if testing
        if "public_sheet_url" in st.secrets:
            link = st.secrets["public_sheet_url"]
            # Convert to CSV export for fast reading
            if "docs.google.com" in link:
                export_url = link.replace("/edit?usp=sharing", "/export?format=csv")
                export_url = export_url.replace("/edit", "/export?format=csv")
            else:
                export_url = link
            
            # PARSE THE BLOCKS
            df_raw = pd.read_csv(export_url, header=None)
            row1_vals = df_raw.iloc[1, :].astype(str).values
            block_starts = [i for i, val in enumerate(row1_vals) if "Last stock" in val]

            for start_col in block_starts:
                # Get Date
                date_val = df_raw.iloc[0, start_col]
                if pd.isna(date_val): date_val = df_raw.iloc[0, start_col+1]
                try:
                    current_date = pd.to_datetime(date_val, dayfirst=True)
                except:
                    continue

                # Find Columns
                headers = df_raw.iloc[1, start_col:start_col+6].astype(str).values
                try:
                    # We map "Today Stock" -> Stock_Level, "order" -> Ordered_Qty
                    idx_stock = np.where([("Today Stock" in h) for h in headers])[0][0]
                    idx_order = np.where([("order" in h and "ordered" not in h) for h in headers])[0][0]
                except:
                    continue

                items = df_raw.iloc[2:, 0].values 
                for i, item in enumerate(items):
                    if pd.isna(item): continue
                    row_idx = i + 2
                    try:
                        s_val = pd.to_numeric(df_raw.iloc[row_idx, start_col + idx_stock], errors='coerce')
                        o_val = pd.to_numeric(df_raw.iloc[row_idx, start_col + idx_order], errors='coerce')
                        
                        if pd.isna(s_val): s_val = 0
                        if pd.isna(o_val): continue # Needs a target to train

                        all_data.append({
                            "Date": current_date,
                            "Day_of_Week": current_date.day_name(),
                            "Item": str(item).lower(),
                            "Stock_Level": s_val,
                            "Ordered_Qty": o_val,
                            "Source": "History"
                        })
                    except:
                        continue
    except Exception as e:
        # If history fails, we just continue to New Data
        print(f"History Load Error: {e}")

    # --- PART B: LOAD NEW DB (The "Database_Log" Tab) ---
    try:
        client = get_google_sheet_client()
        if client:
            sheet = client.open_by_key(st.secrets["sheet_id"]).worksheet("Database_Log")
            new_records = sheet.get_all_records()
            
            for row in new_records:
                # Map the new DB columns to the Training Format
                # DB Cols: Date, Day_of_Week, Time, Item, Current_Stock, AI_Rec, Actual_Order
                try:
                    all_data.append({
                        "Date": pd.to_datetime(row['Date']),
                        "Day_of_Week": row['Day_of_Week'],
                        "Item": str(row['Item']).lower(),
                        "Stock_Level": float(row['Current_Stock']),
                        "Ordered_Qty": float(row['Actual_Order']), # We train on what was ACTUALLY ordered
                        "Source": "New_Log"
                    })
                except:
                    continue
    except:
        pass # If new DB is empty or fails, just use history

    # --- COMBINE & CLEAN ---
    if not all_data:
        return pd.DataFrame()
    
    df_final = pd.DataFrame(all_data)
    
    # Standardize Item Names
    valid_map = {
        '9cuts': '9cuts', 'halal 9-cut chicken 1.6kg': '9cuts',
        'fillets': 'fillets', 'halal chicken fillets 120 - 140g': 'fillets',
        'strips': 'strips', 'halal chicken strips': 'strips',
        'wings': 'wings', 'halal prime prime wings': 'wings'
    }
    df_final['Item_Clean'] = df_final['Item'].map(valid_map).fillna(df_final['Item'])
    
    return df_final

# ==========================================
# 3. AI ENGINE
# ==========================================
def train_model(df):
    if df.empty or len(df) < 5: return None
    
    # Train on: Day, Item, Stock --> Predict: Order Qty
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
    return model

# ==========================================
# 4. APP INTERFACE
# ==========================================
def main():
    # HEADER
    col1, col2 = st.columns([1, 5])
    with col1:
        # Professional Logo
        st.image("https://southernfriedchicken.com/wp-content/uploads/2018/04/SouthernFriedChicken-White.png", width=100)
    with col2:
        st.markdown('<div class="main-header">SFC WINTERTON INVENTORY</div>', unsafe_allow_html=True)
        st.markdown('<div class="sub-header">Manager Control Terminal</div>', unsafe_allow_html=True)

    # LOAD DATA & TRAIN
    df_combined = load_all_data()
    model = train_model(df_combined)
    
    # Status Bar
    if model:
        rec_count = len(df_combined)
        last_date = df_combined['Date'].max().strftime('%d %b')
        st.success(f"✅ AI Online | Trained on {rec_count} records (History + New) | Latest Data: {last_date}")
    else:
        st.warning("⚠️ AI Initializing... (Please ensure 'public_sheet_url' is correct in Secrets)")

    st.markdown("---")

    # CONTROLS
    with st.expander("📅 Settings (Date/Time)", expanded=True):
        c1, c2, c3 = st.columns(3)
        now = datetime.now()
        selected_date = c1.date_input("Ordering Date", now)
        selected_time = c2.time_input("Time", now)
        day_name = selected_date.strftime("%A")
        c3.text_input("Day", value=day_name, disabled=True)

    # SESSION STATE INIT
    if 'inventory' not in st.session_state:
        st.session_state.inventory = {
            '9cuts': {'stock': 0.0, 'rec': 0, 'actual': 0},
            'fillets': {'stock': 0.0, 'rec': 0, 'actual': 0},
            'strips': {'stock': 0.0, 'rec': 0, 'actual': 0},
            'wings': {'stock': 0.0, 'rec': 0, 'actual': 0}
        }

    # --- THE INPUT GRID ---
    st.markdown("### 📋 Enter Stock & Confirm Order")
    
    # Header Row
    cols = st.columns([2, 2, 2, 2])
    cols[0].markdown("**ITEM**")
    cols[1].markdown("**STOCK (Fridge)**")
    cols[2].markdown("**AI SUGGESTION**")
    cols[3].markdown("**FINAL ORDER**")
    
    items_list = ["9cuts", "fillets", "strips", "wings"]
    
    for item in items_list:
        c1, c2, c3, c4 = st.columns([2, 2, 2, 2])
        
        # 1. Name
        c1.markdown(f"##### {item.upper()}")
        
        # 2. Stock Input
        val = c2.number_input(f"s_{item}", min_value=0.0, step=0.5, key=f"stock_{item}", label_visibility="collapsed")
        
        # 3. AI Prediction
        prediction = 0
        if model:
            # Create a 1-row dataframe for prediction
            input_data = pd.DataFrame({
                'Day_of_Week': [day_name], 
                'Item_Clean': [item], 
                'Stock_Level': [val]
            })
            try:
                pred_raw = model.predict(input_data)[0]
                prediction = int(round(max(0, pred_raw)))
            except:
                prediction = 0
        
        c3.info(f"{prediction} Boxes")
        
        # 4. Actual Order Input (Default = Prediction)
        # We set value=prediction only if user hasn't typed yet, or we can just leave it 0
        # Better UX: User sees AI suggestion, then types final decision.
        actual = c4.number_input(f"o_{item}", min_value=0, value=prediction, step=1, key=f"order_{item}", label_visibility="collapsed")
        
        # Save to state
        st.session_state.inventory[item] = {'stock': val, 'rec': prediction, 'actual': actual}

    st.markdown("---")

    # SUBMIT BUTTON
    if st.button("💾 SAVE TO DATABASE"):
        client = get_google_sheet_client()
        if not client:
            st.error("❌ Database Connection Failed. Check Secrets.")
            st.stop()
            
        try:
            sheet = client.open_by_key(st.secrets["sheet_id"]).worksheet("Database_Log")
            
            rows = []
            for item in items_list:
                data = st.session_state.inventory[item]
                # [Date, Day, Time, Item, Current_Stock, AI_Rec, Actual_Order]
                rows.append([
                    selected_date.strftime("%Y-%m-%d"),
                    day_name,
                    selected_time.strftime("%H:%M:%S"),
                    item,
                    data['stock'],
                    data['rec'],
                    data['actual']
                ])
            
            sheet.append_rows(rows)
            st.balloons()
            st.success("✅ Order Saved! The AI has learned from this decision.")
            
        except Exception as e:
            st.error(f"Save Error: {e}")
            st.info("Ensure you created a tab named 'Database_Log' in your Google Sheet.")

if __name__ == "__main__":
    main()