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

def load_config():
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"telegram": {"bot_token": "", "chat_id": ""}, "mops_keywords": [], "news_keywords": []}

def save_config(config):
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=4, ensure_ascii=False)

if 'config' not in st.session_state:
    st.session_state['config'] = load_config()

# Initialization
db = EventDatabase()

# Helper for displaying events
def display_event(evt):
    tags_html = " ".join([f"<span style='background-color:#E1F5FE;color:#0277BD;padding:2px 6px;border-radius:4px;font-size:12px;margin-right:4px;'>#{t}</span>" for t in evt['tags'].split(",") if t])
    title_label = "公告主旨" if evt['type'] == '重大訊息' else "新聞標題"
    
    html = f"""
    <div style='border:1px solid #ddd; padding:15px; border-radius:8px; margin-bottom:10px; background-color:#fff; color:#333;'>
        <div style='font-size:12px; color:#888; margin-bottom:5px;'>
            發佈時間：{evt['datetime_str']}
        </div>
        <div style='font-size:16px; font-weight:bold; margin-bottom:8px;'>
            {title_label}：{evt['title']}
        </div>
        <div style='margin-bottom:10px;'>
            {tags_html}
        </div>
        <div>
            <a href="{evt['link']}" target="_blank" style='color:#1976D2; text-decoration:none; font-size:14px;'>🔗 前往連結</a>
        </div>
    </div>
    """
    st.markdown(html, unsafe_allow_html=True)

# -----------------
# 側邊欄 (Sidebar)
# -----------------
with st.sidebar:
    st.title("🔔 訂閱推播")
    
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
    
    st.title("🔑 關鍵字設定")
    
    # MOPS Keywords
    st.subheader("重大訊息關鍵字")
    mops_kws = st.session_state['config']['mops_keywords']
    for kw in list(mops_kws):
        col1, col2 = st.columns([0.8, 0.2])
        col1.write(f"- {kw}")
        if col2.button("❌", key=f"del_mops_{kw}", help="刪除"):
            st.session_state['config']['mops_keywords'].remove(kw)
            save_config(st.session_state['config'])
            st.rerun()
            
    new_mops = st.text_input("新增重大訊息關鍵字")
    if st.button("新增", key="add_mops"):
        if new_mops and new_mops not in st.session_state['config']['mops_keywords']:
            st.session_state['config']['mops_keywords'].append(new_mops)
            save_config(st.session_state['config'])
            st.rerun()

    st.divider()
    
    # News Keywords
    st.subheader("新聞關鍵字")
    news_kws = st.session_state['config']['news_keywords']
    for kw in list(news_kws):
        col1, col2 = st.columns([0.8, 0.2])
        col1.write(f"- {kw}")
        if col2.button("❌", key=f"del_news_{kw}", help="刪除"):
            st.session_state['config']['news_keywords'].remove(kw)
            save_config(st.session_state['config'])
            st.rerun()
            
    new_news = st.text_input("新增新聞關鍵字")
    if st.button("新增", key="add_news"):
        if new_news and new_news not in st.session_state['config']['news_keywords']:
            st.session_state['config']['news_keywords'].append(new_news)
            save_config(st.session_state['config'])
            st.rerun()
            
    st.divider()
    
    # Auto Update
    st.title("⏱️ 自動更新設定")
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

col1, col2 = st.columns(2)
with col1:
    if st.button("🚀 啟動全市場掃描", use_container_width=True):
        with st.spinner("正在掃描全市場..."):
            run_hunter()
            st.session_state['last_update_time'] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        st.success("掃描完成！")
        # Do not rerun immediately so user sees the success message, but we might want to refresh DB data
        st.rerun()

with col2:
    if st.button("🗑️ 刪除所有訊息", use_container_width=True):
        db.mark_all_deleted()
        st.success("已清空畫面！這些訊息未來不會再重複顯示或推播。")
        st.rerun()

# 撈取資料庫內容
all_active_events = db.get_active_events()
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

# Auto update loop
if auto_update:
    # Check if we should run it right away upon checking the box
    if 'auto_run_initialized' not in st.session_state or not st.session_state['auto_run_initialized']:
        st.session_state['auto_run_initialized'] = True
        with st.spinner("自動掃描初始化中..."):
            run_hunter()
            st.session_state['last_update_time'] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        st.rerun()
        
    # Wait 30 minutes and rerun
    time.sleep(1800)
    
    with st.spinner("定期自動更新中..."):
        run_hunter()
        st.session_state['last_update_time'] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    st.rerun()
else:
    st.session_state['auto_run_initialized'] = False
