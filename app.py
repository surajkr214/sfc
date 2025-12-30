import streamlit as st
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import holidays
import requests

# --- 0. CONFIGURATION ---
st.set_page_config(page_title="SFC Operations", page_icon="🍗", layout="wide")

# 🚨 LOCATION SETTINGS
LOCATION_CITY = "Salisbury"

# 🚨 BLENDED LOGIC WEIGHTS
WEIGHT_MANAGER = 0.60  
WEIGHT_SCIENCE = 0.40  

# 🚨 SUPPLY CHAIN PARAMETERS
SERVICE_LEVEL_Z = 1.65    # 95% Confidence
MANAGER_BUFFER = 0.20     # 20% Flat Buffer
MIN_SAFETY_FLOOR = 0.25   # Minimum safe floor

# 🚨 MULTIPLIERS
PAY_WEEK_BOOST = 1.15     # 15% Boost

WEATHER_MULTIPLIERS = {
    "Normal": 1.0,
    "Sunny": 1.15,        # +15% (Foot traffic)
    "Rainy": 1.05,        # +5% (Delivery)
    "Snow": 0.80,         # -20% (Shop closed/Empty)
    "Cloudy": 1.0
}

EVENT_MULTIPLIERS = {
    "Season: Christmas/New Year": 1.40,
    "Season: Summer Holidays": 1.25,
    "Bank Holiday": 1.30,
    "Easter": 1.25,
    "Halloween": 1.20,
    "Normal Day": 1.0
}

# 🚨 DEFAULTS
DEFAULT_ITEMS = ["9cuts", "fillets", "strips", "wings"]
DEFAULT_REASONS = ["Overcooked/Burned", "Left Over", "Dropped on floor", "Smell/Expired", "Dry", "Other"]
PIECES_PER_BOX = {'9cuts': 90, 'fillets': 40, 'strips': 60, 'wings': 80}

# --- STYLING ---
st.markdown("""
    <style>
    .main-header { font-size: 2rem; font-weight: bold; color: #b30000; }
    .ai-rec-box {
        background-color: #e3f2fd; border: 1px solid #90caf9; color: #1565c0;
        padding: 10px; border-radius: 8px; text-align: center; font-weight: 800; font-size: 24px;
    }
    .math-explainer { font-size: 11px; color: #666; font-style: italic; margin-top: 4px; line-height: 1.4; background: #111; padding: 5px; border-radius: 4px; }
    
    .badge-trend-up { background-color: #e8f5e9; color: #2e7d32; padding: 2px 8px; border-radius: 4px; font-size: 10px; font-weight: bold; border: 1px solid #c8e6c9; }
    .badge-trend-down { background-color: #ffebee; color: #c62828; padding: 2px 8px; border-radius: 4px; font-size: 10px; font-weight: bold; border: 1px solid #ffcdd2; }
    
    .status-safe { color: #2e7d32; font-weight: bold; font-size: 12px; }
    .status-danger { color: #c62828; font-weight: bold; font-size: 12px; }
    .status-warn { color: #f9a825; font-weight: bold; font-size: 12px; }

    .stNumberInput input { font-weight: bold; text-align: center; font-size: 16px; }
    div[data-testid="stRadio"] > label { display: none; }
    div[data-testid="stRadio"] > div { background-color: #161616; padding: 6px; border-radius: 12px; display: flex; justify-content: center; gap: 10px; }
    div[data-testid="stRadio"] div[role="radiogroup"] > label { background-color: transparent; border: 1px solid #333; border-radius: 8px; padding: 8px 20px; color: #aaa; transition: all 0.2s; text-align: center; flex-grow: 1; }
    div[data-testid="stRadio"] div[role="radiogroup"] > label[data-checked="true"] { background-color: #b30000; color: white; border-color: #b30000; font-weight: bold; }
    </style>
    """, unsafe_allow_html=True)

# ==========================================
# 1. AUTONOMOUS AGENTS
# ==========================================
@st.cache_data(ttl=3600)
def fetch_live_weather():
    try:
        url = f"https://wttr.in/{LOCATION_CITY}?format=%C"
        response = requests.get(url)
        if response.status_code == 200:
            condition = response.text.strip().lower()
            if "sun" in condition or "clear" in condition: return "Sunny"
            if "rain" in condition or "drizzle" in condition or "shower" in condition: return "Rainy"
            if "snow" in condition: return "Snow"
            if "cloud" in condition or "overcast" in condition: return "Cloudy"
    except: pass
    return "Normal"

def check_pay_week_status(check_date):
    """Checks if date is near last Friday of month"""
    next_month = check_date.replace(day=28) + timedelta(days=4)
    last_day_of_month = next_month - timedelta(days=next_month.day)
    days_left = (last_day_of_month - check_date).days
    return days_left <= 7 and days_left >= 0

# ==========================================
# 2. CORE FUNCTIONS
# ==========================================
def get_client():
    try:
        scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
        creds_dict = dict(st.secrets["gcp_service_account"])
        creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
        return gspread.authorize(creds)
    except: return None

@st.cache_data(ttl=60)
def load_config():
    client = get_client()
    items, reasons = DEFAULT_ITEMS, DEFAULT_REASONS
    if client:
        try:
            sheet = client.open_by_key(st.secrets["sheet_id"]).worksheet("Settings")
            data = pd.DataFrame(sheet.get_all_records())
            if 'Items' in data.columns:
                found = [x for x in data['Items'].dropna().unique() if x != ""]
                if found: items = found
            if 'Wastage_Reasons' in data.columns:
                found = [x for x in data['Wastage_Reasons'].dropna().unique() if x != ""]
                if found: reasons = found
        except: pass 
    return items, reasons

def smart_append(sheet_name, data_dict):
    client = get_client()
    if not client: return False, "Connection Failed"
    try:
        sheet = client.open_by_key(st.secrets["sheet_id"]).worksheet(sheet_name)
        headers = sheet.row_values(1)
        # Create map of Column Name -> Index
        col_map = {name.strip(): i for i, name in enumerate(headers)}
        
        # Prepare row with empty strings
        row_to_write = [''] * len(headers)
        
        # Fill in data where keys match headers
        for key, value in data_dict.items():
            if key in col_map:
                row_to_write[col_map[key]] = value
                
        sheet.append_row(row_to_write)
        return True, "Success"
    except Exception as e: return False, str(e)

# ==========================================
# 3. HYBRID ENGINE
# ==========================================
@st.cache_data(ttl=60, show_spinner=False)
def calculate_inventory_stats():
    all_usage = []

    # A. Legacy Data
    if "public_sheet_url" in st.secrets:
        try:
            link = st.secrets["public_sheet_url"]
            url = link.replace("/edit?usp=sharing", "/export?format=csv").replace("/edit", "/export?format=csv") if "docs.google.com" in link else link
            df_raw = pd.read_csv(url, header=None)
            row1 = df_raw.iloc[1, :].astype(str).values
            block_starts = [i for i, val in enumerate(row1) if "Last stock" in val]
            for start in block_starts:
                try: 
                    d_val = df_raw.iloc[0, start] if not pd.isna(df_raw.iloc[0, start]) else df_raw.iloc[0, start+1]
                    dt = pd.to_datetime(d_val, dayfirst=True)
                    day_name = dt.day_name()
                    est_cover = 3 if day_name == "Friday" else 2
                except: continue
                headers = df_raw.iloc[1, start:start+6].astype(str).values
                try: idx_order = np.where([("order" in h and "ordered" not in h) for h in headers])[0][0]
                except: continue
                items = df_raw.iloc[2:, 0].values 
                for i, item in enumerate(items):
                    if pd.isna(item): continue
                    try:
                        qty = pd.to_numeric(df_raw.iloc[i+2, start + idx_order], errors='coerce')
                        if pd.isna(qty) or qty <= 0: continue
                        all_usage.append({ "Item": str(item).lower(), "Day_of_Week": day_name, "Daily_Burn": qty / est_cover, "Date": dt })
                    except: continue
        except: pass

    # B. Live Data
    try:
        client = get_client()
        if client:
            sheet = client.open_by_key(st.secrets["sheet_id"]).worksheet("Database_Log")
            for row in sheet.get_all_records():
                try:
                    qty = float(row['Actual_Order'])
                    cov = float(row['Order_Coverage']) if row['Order_Coverage'] else 1.0
                    dt = pd.to_datetime(row['Date'])
                    if qty > 0:
                        all_usage.append({ "Item": str(row['Items']).lower(), "Day_of_Week": row['Day_of_Week'], "Daily_Burn": qty / max(1, cov), "Date": dt })
                except: continue
    except: pass

    if not all_usage: return pd.DataFrame(), pd.DataFrame()
    
    df = pd.DataFrame(all_usage)
    valid_map = {'9cuts':'9cuts', 'fillets':'fillets', 'strips':'strips', 'wings':'wings'}
    df['Item_Clean'] = df['Item'].map(valid_map).fillna(df['Item'])
    
    volatility_stats = df.groupby(['Item_Clean', 'Day_of_Week'])['Daily_Burn'].agg(['mean', 'std', 'count']).reset_index()
    
    recent_cutoff = datetime.now() - timedelta(days=14)
    df_recent = df[df['Date'] >= recent_cutoff]
    trend_avg = df_recent.groupby(['Item_Clean'])['Daily_Burn'].mean().reset_index().rename(columns={'Daily_Burn': 'Recent_Avg'})
    long_avg = df.groupby(['Item_Clean'])['Daily_Burn'].mean().reset_index().rename(columns={'Daily_Burn': 'Long_Avg'})
    trend_df = pd.merge(long_avg, trend_avg, on='Item_Clean', how='left')
    
    return volatility_stats, trend_df

def analyze_stock_logic(vol_stats, trend_stats, item, day, current_stock, days_cover, event, weather, is_pay_week):
    long_term_burn = 0.5
    recent_burn = 0.5
    volatility = 0.2
    trend_status = "Stable"
    
    if not trend_stats.empty:
        t_row = trend_stats[trend_stats['Item_Clean'] == item]
        if not t_row.empty:
            long_term_burn = t_row['Long_Avg'].values[0]
            if not np.isnan(t_row['Recent_Avg'].values[0]):
                recent_burn = t_row['Recent_Avg'].values[0]

    if recent_burn > (long_term_burn * 1.15): trend_status = "Trending Up"
    elif recent_burn < (long_term_burn * 0.85): trend_status = "Trending Down"

    if not vol_stats.empty:
        v_row = vol_stats[(vol_stats['Item_Clean'] == item) & (vol_stats['Day_of_Week'] == day)]
        if not v_row.empty:
            volatility = v_row['std'].values[0] if v_row['count'].values[0] > 2 else long_term_burn * 0.3
        else:
            volatility = long_term_burn * 0.3

    evt_mult = EVENT_MULTIPLIERS.get(event, 1.0)
    if event not in EVENT_MULTIPLIERS:
        if "Season" in event or "Holiday" in event: evt_mult = 1.25
        
    pay_mult = PAY_WEEK_BOOST if is_pay_week else 1.0
    wthr_mult = WEATHER_MULTIPLIERS.get(weather, 1.0)
    
    mgr_cycle = recent_burn * days_cover * evt_mult * pay_mult * wthr_mult
    mgr_buffer = mgr_cycle * MANAGER_BUFFER
    mgr_total = mgr_cycle + mgr_buffer

    sci_cycle = long_term_burn * days_cover * evt_mult
    sci_buffer = max(SERVICE_LEVEL_Z * volatility * np.sqrt(days_cover), long_term_burn * MIN_SAFETY_FLOOR)
    sci_total = sci_cycle + sci_buffer

    blended_need = (mgr_total * WEIGHT_MANAGER) + (sci_total * WEIGHT_SCIENCE)
    suggestion = max(0, blended_need - current_stock)
    final_rec = int(np.ceil(suggestion))

    explainer = f"Trend: {mgr_total:.1f}<br>"
    explainer += f"Stats: {sci_total:.1f}<br>"
    
    factors = []
    if is_pay_week: factors.append("💰 PayWeek")
    if wthr_mult != 1.0: factors.append(f"Weather ({weather})")
    if evt_mult != 1.0: factors.append(f"Event")
    
    if factors: explainer += f"Fact: {', '.join(factors)}<br>"
    explainer += f"Need {blended_need:.1f} - Stock {current_stock}"

    status_msg = "Safe"
    status_css = "status-safe"
    if current_stock < (blended_need * 0.5):
        status_msg = "⚠️ CRITICAL"
        status_css = "status-danger"
    elif current_stock < blended_need:
        status_msg = "⚠️ Low"
        status_css = "status-warn"

    return final_rec, explainer, status_msg, status_css, trend_status

# ==========================================
# 4. HELPERS
# ==========================================
def get_smart_defaults(date_obj):
    is_stock_night = date_obj.weekday() in [0, 2, 4] and datetime.now().hour >= 21
    view_idx = 0 if is_stock_night else 1
    cov = 3 if date_obj.weekday() == 4 else 2 if date_obj.weekday() in [0, 2] else 1
    
    year = date_obj.year
    uk_hol = holidays.UK(years=[year, year+1])
    event = "Normal Day"
    if date_obj in uk_hol: event = f"Bank Holiday: {uk_hol.get(date_obj)}"
    elif date_obj.month == 10 and date_obj.day == 31: event = "Halloween"
    elif (date_obj.month == 12 and date_obj.day >= 20) or (date_obj.month == 1 and date_obj.day <= 2):
        event = "Season: Christmas/New Year"
    elif 7 <= date_obj.month <= 8: event = "Season: Summer Holidays"
        
    return view_idx, cov, event

def get_wastage_default():
    return "Left Over" if 0 <= datetime.now().hour < 1 else "Overcooked/Burned"

# ==========================================
# 5. MAIN APP
# ==========================================
def main():
    col1, col2 = st.columns([1, 5])
    with col1: st.image("https://southernfriedchicken.com/wp-content/uploads/2018/04/SouthernFriedChicken-White.png", width=80)
    with col2: st.markdown('<div class="main-header">SFC OPERATIONS</div>', unsafe_allow_html=True)

    ITEMS_LIST, REASONS_LIST = load_config()
    
    with st.spinner("🚀 Booting AI & Fetching Weather..."):
        vol_stats, trend_stats = calculate_inventory_stats()
        live_weather = fetch_live_weather()

    if 'view_init' not in st.session_state:
        def_idx, _, _ = get_smart_defaults(datetime.now())
        st.session_state.view_default = def_idx
        st.session_state.view_init = True

    mode = st.radio("Navigation", ["📦 Stock & Order", "🗑️ Wastage Entry", "🤖 Manager Assistant"], 
                    index=st.session_state.view_default, horizontal=True)
    st.write("")

    # --- VIEW: STOCK & ORDER ---
    if mode == "📦 Stock & Order":
        with st.container(border=True):
            c1, c2, c3, c4 = st.columns(4)
            ord_date = c1.date_input("Date", datetime.now())
            _, def_cov, def_evt = get_smart_defaults(ord_date)
            auto_pay_week = check_pay_week_status(pd.to_datetime(ord_date))
            
            c2.text_input("Day", ord_date.strftime("%A"), disabled=True)
            days_cover = c3.number_input("Days to Cover", 1, 7, def_cov)
            
            c4.markdown("**Manager Context**")
            col_evt, col_wthr = c4.columns(2)
            
            opts = list(EVENT_MULTIPLIERS.keys())
            if def_evt not in opts and "Normal" not in def_evt: opts.insert(0, def_evt)
            evt_idx = opts.index(def_evt) if def_evt in opts else opts.index("Normal Day")
            event = col_evt.selectbox("Event", opts, index=evt_idx, label_visibility="collapsed")
            
            w_opts = list(WEATHER_MULTIPLIERS.keys())
            w_idx = w_opts.index(live_weather) if live_weather in w_opts else 0
            weather = col_wthr.selectbox("Weather", w_opts, index=w_idx, label_visibility="collapsed")
            
            is_pay_week = c4.checkbox("💰 Pay Week?", value=auto_pay_week)

        st.markdown("---")
        h1, h2, h3, h4 = st.columns([2, 2, 2, 2])
        h1.markdown("**ITEM**"); h2.markdown("**STOCK CHECK**"); h3.markdown("**HYBRID ORDER**"); h4.markdown("**FINAL DECISION**")

        if 'orders' not in st.session_state: st.session_state.orders = {}

        for item in ITEMS_LIST:
            with st.container():
                c1, c2, c3, c4 = st.columns([2, 2, 2, 2], vertical_alignment="top")
                
                rec, explanation, buff_stat, buff_color, trend = analyze_stock_logic(
                    vol_stats, trend_stats, item, ord_date.strftime("%A"), 0, days_cover, event, weather, is_pay_week
                )
                
                badge_html = ""
                if trend == "Trending Up": badge_html = "<span class='badge-trend-up'>📈 Busy Trend</span>"
                elif trend == "Trending Down": badge_html = "<span class='badge-trend-down'>📉 Quiet Trend</span>"
                
                c1.markdown(f"#### {item.upper()} {badge_html}", unsafe_allow_html=True)
                
                curr_stock = c2.number_input(f"s_{item}", 0.0, step=0.5, key=f"s_{item}", label_visibility="collapsed")
                
                rec, explanation, buff_stat, buff_color, _ = analyze_stock_logic(
                    vol_stats, trend_stats, item, ord_date.strftime("%A"), curr_stock, days_cover, event, weather, is_pay_week
                )
                
                c2.markdown(f"<div class='{buff_color}'>{buff_stat}</div>", unsafe_allow_html=True)
                c3.markdown(f"<div class='ai-rec-box'>{rec}</div>", unsafe_allow_html=True)
                c3.markdown(f"<div class='math-explainer'>{explanation}</div>", unsafe_allow_html=True)

                final_order = c4.number_input(f"o_{item}", 0, step=1, key=f"o_{item}", label_visibility="collapsed")
                
                reason = ""
                if final_order != rec and final_order != 0:
                    reason = c4.text_input(f"Reason", placeholder="Why change?", key=f"r_{item}", label_visibility="collapsed")
                
                st.session_state.orders[item] = {"Stock": curr_stock, "Rec": rec, "Order": final_order, "Reason": reason, "Buff": buff_stat}

        st.markdown("---")
        if st.button("CONFIRM ORDER", type="primary", use_container_width=True):
            success = 0
            for item in ITEMS_LIST:
                d = st.session_state.orders[item]
                data = {
                    "Date": ord_date.strftime("%Y-%m-%d"),
                    "Day_of_Week": ord_date.strftime("%A"),
                    "Time": datetime.now().strftime("%H:%M:%S"),
                    "Items": item,
                    "Current_Stock": d['Stock'],
                    "Order_Coverage": days_cover,
                    "Event_Type": event,
                    "Weather": weather,
                    "Is_Pay_Week": "Yes" if is_pay_week else "No",
                    "AI_Rec": d['Rec'],
                    "Actual_Order": d['Order'],
                    "Remarks": f"{d['Reason']} | {d['Buff']}"
                }
                ok, _ = smart_append("Database_Log", data)
                if ok: success += 1
            if success: 
                st.success(f"✅ Saved {success} items. Hybrid Engine Updated.")
                st.cache_data.clear()
            else: st.error("Save Failed.")

    # --- VIEW: WASTAGE ---
    elif mode == "🗑️ Wastage Entry":
        st.markdown("### Daily Wastage Log")
        c1, c2, c3 = st.columns(3)
        w_date = c1.date_input("Date", datetime.now())
        w_time = c2.time_input("Time", datetime.now())
        def_r = get_wastage_default()
        r_opts = REASONS_LIST.copy()
        if def_r in r_opts: r_opts.insert(0, r_opts.pop(r_opts.index(def_r)))
        reason = c3.selectbox("Reason", r_opts)

        st.markdown("---")
        w_data = {}
        with st.form("waste"):
            cols = st.columns(2)
            for i, item in enumerate(ITEMS_LIST):
                with cols[i % 2]:
                    st.markdown(f"**{item.upper()}**")
                    wc1, wc2 = st.columns(2)
                    q = wc1.number_input("Qty", 0.0, step=1.0, key=f"wq_{item}")
                    u = wc2.radio("U", ["Pieces", "Boxes"], horizontal=True, key=f"wu_{item}", label_visibility="collapsed")
                    w_data[item] = (q, u)
            note = st.text_input("Details")
            if st.form_submit_button("LOG WASTAGE", type="primary"):
                ok_cnt = 0
                for item, (qty, unit) in w_data.items():
                    if qty > 0:
                        pcs = qty * PIECES_PER_BOX.get(item, 80) if unit == "Boxes" else qty
                        row = { 
                            "Date": w_date.strftime("%Y-%m-%d"), 
                            "Day_of_Week": w_date.strftime("%A"), 
                            "Time": w_time.strftime("%H:%M:%S"), 
                            "Items": item, 
                            "Wastage_Qty": qty, 
                            "Wastage_Reason": reason, 
                            "Remarks": f"{unit} (Calc: {pcs} pcs) - {note}" 
                        }
                        ok, _ = smart_append("Database_Log", row)
                        if ok: ok_cnt += 1
                if ok_cnt: st.success("✅ Logged")

    elif mode == "🤖 Manager Assistant":
        st.markdown("### 💬 System Config")
        st.info("Example: 'Add item Popcorn Chicken'")
        if "chat" not in st.session_state: st.session_state.chat = []
        for m in st.session_state.chat:
            with st.chat_message(m["role"]): st.write(m["content"])
        if p := st.chat_input():
            st.session_state.chat.append({"role": "user", "content": p})
            with st.chat_message("user"): st.write(p)
            resp = "I didn't understand."
            if "add item" in p.lower():
                val = p[8:].strip()
                ok, msg = smart_append("Settings", {"Items": val})
                resp = f"✅ Added Item: {val}" if ok else f"❌ {msg}"
                st.cache_data.clear()
            elif "add reason" in p.lower():
                val = p[10:].strip()
                ok, msg = smart_append("Settings", {"Wastage_Reasons": val})
                resp = f"✅ Added Reason: {val}" if ok else f"❌ {msg}"
                st.cache_data.clear()
            with st.chat_message("assistant"): st.write(resp)
            st.session_state.chat.append({"role": "assistant", "content": resp})

if __name__ == "__main__":
    main()