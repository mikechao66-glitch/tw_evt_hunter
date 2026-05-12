import streamlit as st
import json
import time
import os
from datetime import datetime
from tw_evt_hunter import EventDatabase
from sentinel import run_hunter

st.set_page_config(page_title="台股事件獵人", layout="wide", page_icon="📈")

# Configuration loading and saving
CONFIG_FILE = "config.json"
KEYWORDS_FILE = "keywords.json"

def load_config():
    config = {"telegram": {"bot_token": "", "chat_id": ""}, "mops_keywords": {}, "news_keywords": {}}
    
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            try:
                loaded = json.load(f)
                config.update(loaded)
            except: pass
                
    if os.path.exists(KEYWORDS_FILE):
        with open(KEYWORDS_FILE, "r", encoding="utf-8") as f:
            try:
                kws = json.load(f)
                config['mops_keywords'] = kws.get('mops_keywords', {})
                config['news_keywords'] = kws.get('news_keywords', {})
            except: pass

    env_token = os.environ.get("TG_BOT_TOKEN")
    env_chat_id = os.environ.get("TG_CHAT_ID")
    if env_token: config['telegram']['bot_token'] = env_token
    if env_chat_id: config['telegram']['chat_id'] = env_chat_id
            
    if isinstance(config.get('mops_keywords'), list):
        config['mops_keywords'] = {kw: True for kw in config['mops_keywords']}
    if isinstance(config.get('news_keywords'), list):
        config['news_keywords'] = {kw: True for kw in config['news_keywords']}
    return config

def save_config(config):
    private_data = {"telegram": config['telegram']}
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(private_data, f, indent=4, ensure_ascii=False)
    
    public_keywords = {
        "mops_keywords": config['mops_keywords'],
        "news_keywords": config['news_keywords']
    }
    with open(KEYWORDS_FILE, "w", encoding="utf-8") as f:
        json.dump(public_keywords, f, indent=4, ensure_ascii=False)

if 'config' not in st.session_state:
    st.session_state['config'] = load_config()

# Initialization
db = EventDatabase()

# Helper for displaying events
def display_event(evt):
    tags_html = " ".join([f"<span style='background-color:#E1F5FE;color:#0277BD;padding:2px 6px;border-radius:4px;font-size:12px;margin-right:4px;'>#{t}</span>" for t in evt['tags'].split(",") if t])
    title_label = "公告主旨" if evt['type'] == '重大訊息' else "新聞標題"
    
    # 組合公司資訊 (針對重大訊息顯示代號與名稱)
    co_info_html = ""
    if evt.get('co_id') and evt.get('co_name'):
        co_info_html = f"<div style='font-size:13px; color:#666; margin-bottom:5px;'>🏢 {evt['co_id']} {evt['co_name']}</div>"
    
    # 使用不帶縮排的 HTML 字串，避免 Markdown 將其誤判為程式碼區塊
    html = f"""<div style='border:1px solid #ddd; padding:15px; border-radius:8px; margin-bottom:10px; background-color:#fff; color:#333;'>
{co_info_html}
<div style='font-size:16px; font-weight:bold; margin-bottom:5px;'>
{title_label}：{evt['title']}
</div>
<div style='font-size:12px; color:#888; margin-bottom:8px;'>
發佈時間：{evt['datetime_str']}
</div>
<div style='margin-bottom:10px;'>
{tags_html}
</div>
<div>
<a href="{evt['link']}" target="_blank" style='color:#1976D2; text-decoration:none; font-size:14px;'>🔗 前往連結</a>
</div>
</div>"""
    st.markdown(html, unsafe_allow_html=True)

# -----------------
# 側邊欄 (Sidebar)
# -----------------
with st.sidebar:
    st.subheader("🔔 訂閱推播")
    
    with st.form("subscription_form", clear_on_submit=True, border=False):
        bot_token = st.text_input("Telegram Bot Token", type="password")
        chat_id = st.text_input("Telegram Chat ID", type="password")
        
        if st.form_submit_button("儲存訂閱資訊"):
            if bot_token and chat_id:
                st.session_state['config']['telegram']['bot_token'] = bot_token
                st.session_state['config']['telegram']['chat_id'] = chat_id
                save_config(st.session_state['config'])
                st.success("已完成儲存")
            
    st.caption("請輸入您的 Telegram Bot資訊。系統將於搜尋到符合之重大訊息及新聞時推送至您的行動裝置。")
    
    st.divider()
    
    st.subheader("🔑 關鍵字設定")
    
    # MOPS Keywords
    with st.expander("重大訊息關鍵字"):
        mops_kws = st.session_state['config']['mops_keywords']
        for kw, enabled in list(mops_kws.items()):
            col1, col2 = st.columns([0.8, 0.2])
            new_val = col1.checkbox(f"{kw}", value=enabled, key=f"mops_chk_{kw}")
            if new_val != enabled:
                st.session_state['config']['mops_keywords'][kw] = new_val
                save_config(st.session_state['config'])
                st.rerun()
                
            if col2.button("❌", key=f"del_mops_{kw}", help="刪除"):
                del st.session_state['config']['mops_keywords'][kw]
                save_config(st.session_state['config'])
                st.rerun()
                
        new_mops = st.text_input("新增重大訊息關鍵字")
        col_m1, col_m2 = st.columns([0.4, 0.6])
        if col_m1.button("新增", key="add_mops"):
            if new_mops and new_mops not in st.session_state['config']['mops_keywords']:
                st.session_state['config']['mops_keywords'][new_mops] = True
                st.session_state['last_keyword_added_ts'] = time.time()  # 記錄新增時間
                save_config(st.session_state['config'])
                st.rerun()
        col_m2.caption("💡 使用 `+` 號可進行複合搜尋")

    # News Keywords
    with st.expander("新聞關鍵字"):
        news_kws = st.session_state['config']['news_keywords']
        for kw, enabled in list(news_kws.items()):
            col1, col2 = st.columns([0.8, 0.2])
            new_val = col1.checkbox(f"{kw}", value=enabled, key=f"news_chk_{kw}")
            if new_val != enabled:
                st.session_state['config']['news_keywords'][kw] = new_val
                save_config(st.session_state['config'])
                st.rerun()
                
            if col2.button("❌", key=f"del_news_{kw}", help="刪除"):
                del st.session_state['config']['news_keywords'][kw]
                save_config(st.session_state['config'])
                st.rerun()
                
        new_news = st.text_input("新增新聞關鍵字")
        col_btn, col_note = st.columns([0.4, 0.6])
        if col_btn.button("新增", key="add_news"):
            if new_news and new_news not in st.session_state['config']['news_keywords']:
                st.session_state['config']['news_keywords'][new_news] = True
                st.session_state['last_keyword_added_ts'] = time.time()  # 記錄新增時間
                save_config(st.session_state['config'])
                st.rerun()
        col_note.caption("💡 使用 `+` 號可進行複合搜尋")
            
    st.divider()
    
    # Auto Update
    auto_update = st.checkbox("啟動自動更新", value=False)
    
    # State for auto update
    if 'last_update_time' not in st.session_state:
        st.session_state['last_update_time'] = "尚未執行"
    
    st.write(f"上次更新時間：{st.session_state['last_update_time']}")
    st.caption("啟用後每 30 分鐘會自動執行一次掃描。")

# -----------------
# 主畫面 (Main Content)
# -----------------
st.header("台股事件獵人 監測看板")

# 檢查自動更新是否觸發
trigger_auto_scan = False
if auto_update:
    now_ts = time.time()
    
    # 檢查是否在新增關鍵字的 15 分鐘冷卻期內
    is_in_cooldown = False
    if 'last_keyword_added_ts' in st.session_state:
        if now_ts - st.session_state['last_keyword_added_ts'] < 900:  # 900秒 = 15分鐘
            is_in_cooldown = True

    if 'last_auto_update_ts' not in st.session_state:
        # 如果是第一次啟動，且不在冷卻期內，才觸發
        if not is_in_cooldown:
            st.session_state['last_auto_update_ts'] = now_ts
            trigger_auto_scan = True
        else:
            # 在冷卻期內，只設定時間點但不觸發掃描
            st.session_state['last_auto_update_ts'] = now_ts
    else:
        elapsed = now_ts - st.session_state['last_auto_update_ts']
        if elapsed >= 1800:
            st.session_state['last_auto_update_ts'] = now_ts
            trigger_auto_scan = True
else:
    # 關閉自動更新時，清除紀錄
    if 'last_auto_update_ts' in st.session_state:
        del st.session_state['last_auto_update_ts']

col1, col2 = st.columns(2)
with col1:
    scan_clicked = st.button("🚀 啟動全市場掃描", use_container_width=True)
    scan_msg_placeholder = st.empty()

with col2:
    if st.button("🗑️ 刪除所有訊息", use_container_width=True):
        db.mark_all_deleted()
        st.success("已清空畫面！這些訊息未來不會再重複顯示或推播。")
        st.rerun()

# 統一執行掃描的邏輯
if scan_clicked or trigger_auto_scan:
    with scan_msg_placeholder.container():
        with st.spinner("正在掃描全市場..."):
            run_hunter()
            st.session_state['last_update_time'] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        st.success("掃描完成！")
        time.sleep(1.5)  # 讓使用者能看到完成訊息
    st.rerun()

# 撈取資料庫內容
all_active_events = db.get_active_events()
# 排序：優先比對標籤數量 (符合越多關鍵字越前面)，其次按日期
all_active_events.sort(key=lambda x: (len(x['tags'].split(',')) if x['tags'] else 0, x['datetime_str']), reverse=True)

mops_events = [e for e in all_active_events if e['type'] == '重大訊息']
news_events = [e for e in all_active_events if e['type'] == '新聞']

st.divider()

col_mops, col_news = st.columns(2)

with col_mops:
    st.subheader(f"📢 重大訊息 ({len(mops_events)})")
    if not mops_events:
        st.info("目前沒有重大訊息。")
    for evt in mops_events:
        display_event(evt)

with col_news:
    st.subheader(f"📰 財經新聞 ({len(news_events)})")
    if not news_events:
        st.info("目前沒有新聞。")
    for evt in news_events:
        display_event(evt)

# Auto update wait loop (維持定期重啟)
# 使用 60 秒短循環取代一次性長 sleep，避免 WebSocket 超時導致自動更新失效
if auto_update and not trigger_auto_scan:
    now_ts = time.time()
    elapsed = now_ts - st.session_state['last_auto_update_ts']
    if elapsed < 1800:
        time.sleep(60)
        st.rerun()

st.divider()
st.markdown("<div style='text-align: center; color: #888; font-size: 13px;'>© 2026 Built by 趙志軒</div>", unsafe_allow_html=True)
