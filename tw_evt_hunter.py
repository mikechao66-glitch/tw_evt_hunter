import os
import json
import sqlite3
import requests
import time
import random
import logging
from datetime import datetime, timedelta, timezone
from bs4 import BeautifulSoup
import urllib.parse
import hashlib
import re
from email.utils import parsedate_to_datetime

# Helper to get Taiwan time consistently
def get_tw_time():
    return datetime.now(timezone(timedelta(hours=8))).replace(tzinfo=None)

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class EventDatabase:
    def __init__(self, db_path="events.db"):
        self.db_path = db_path
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS events (
                    id TEXT PRIMARY KEY,
                    title TEXT,
                    source TEXT,
                    type TEXT,
                    datetime_str TEXT,
                    link TEXT,
                    tags TEXT,
                    deleted INTEGER DEFAULT 0,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                    co_id TEXT,
                    co_name TEXT
                )
            ''')
            # 確保欄位存在 (針對舊資料庫升級)
            try:
                cursor.execute('ALTER TABLE events ADD COLUMN co_id TEXT')
            except sqlite3.OperationalError:
                pass
            try:
                cursor.execute('ALTER TABLE events ADD COLUMN co_name TEXT')
            except sqlite3.OperationalError:
                pass

            # 檢查並遷移舊版 sent_events 的資料，標記為已刪除以避免再次推播
            try:
                cursor.execute('INSERT OR IGNORE INTO events (id, title, source, deleted) SELECT id, title, source, 1 FROM sent_events')
            except sqlite3.OperationalError:
                pass
            conn.commit()

    def is_sent(self, event_id):
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT 1 FROM events WHERE id = ?', (event_id,))
            return cursor.fetchone() is not None

    def insert_event(self, event_dict):
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT OR IGNORE INTO events 
                (id, title, source, type, datetime_str, link, tags, deleted, co_id, co_name)
                VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?, ?)
            ''', (
                event_dict['id'], 
                event_dict['title'], 
                event_dict['source'],
                event_dict['type'],
                event_dict['datetime_str'],
                event_dict.get('link', ''),
                ','.join(event_dict.get('tags', [])),
                event_dict.get('co_id', ''),
                event_dict.get('co_name', '')
            ))
            conn.commit()

    def get_active_events(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute('''
                SELECT * FROM events 
                WHERE deleted = 0 
                ORDER BY datetime_str DESC
            ''')
            return [dict(row) for row in cursor.fetchall()]

    def mark_all_deleted(self):
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('UPDATE events SET deleted = 1 WHERE deleted = 0')
            conn.commit()

    def cleanup_old_events(self, days=2):
        """清除超過指定天數的舊資料，避免資料庫無限膨脹"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            # 使用 SQLite 的 date 函數計算
            cursor.execute(f"DELETE FROM events WHERE timestamp < datetime('now', '-{days} days')")
            conn.commit()
            logger.info(f"已清理超過 {days} 天的舊資料庫紀錄")

class TelegramNotifier:
    def __init__(self, bot_token, chat_id):
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.api_url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"

    def send_message(self, text):
        try:
            payload = {
                "chat_id": self.chat_id,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True
            }
            response = requests.post(self.api_url, json=payload, timeout=10)
            response.raise_for_status()
            return True
        except Exception as e:
            logger.error(f"Telegram 發送失敗: {e}")
            return False

class MopsCrawler:
    def __init__(self, keywords):
        self.keywords = keywords
        self.base_url = "https://mopsov.twse.com.tw/mops/web/"
        self.ajax_url = "https://mopsov.twse.com.tw/mops/web/ajax_t05sr01_1"
        self.page_url = "https://mopsov.twse.com.tw/mops/web/t05sr01_1"
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-TW,zh;q=0.9,en-US;q=0.8",
        }

    def fetch_today_events(self):
        events_dict = {}
        two_days_ago = get_tw_time() - timedelta(days=2)
        error_msg = None
        
        targets = [
            (self.page_url, self.ajax_url, "當日", "today"),
            ("https://mopsov.twse.com.tw/mops/web/t05st02", "https://mopsov.twse.com.tw/mops/web/ajax_t05st02", "前一日", "prev")
        ]

        # 計算昨日日期 (用於前一日查詢參數)
        yesterday = get_tw_time() - timedelta(days=1)
        roc_year = yesterday.year - 1911
        roc_month = yesterday.strftime("%m")
        roc_day = yesterday.strftime("%d")

        try:
            session = requests.Session()
            session.headers.update(self.headers)

            for p_url, a_url, label, mode in targets:
                try:
                    logger.info(f"正在爬取 MOPS {label}重大訊息...")
                    session.get(p_url, timeout=15)
                    time.sleep(random.uniform(1.0, 2.0))
                    
                    # 根據頁面類型設定不同的 POST 參數
                    if mode == "today":
                        payload = {'TYPEK': 'all', 'step': '0'}
                    else:
                        payload = {
                            'encodeURIComponent': '1',
                            'step': '1', 
                            'step00': '0',
                            'firstin': '1', 
                            'off': '1',
                            'TYPEK': 'all', 
                            'year': str(roc_year), 
                            'month': roc_month, 
                            'day': roc_day
                        }

                    response = session.post(
                        a_url,
                        data=payload,
                        headers={'Referer': p_url},
                        timeout=15
                    )
                    
                    if response.status_code != 200:
                        error_msg = f"連線失敗 ({response.status_code})，可能已被暫時封鎖 IP"
                        continue

                    # 檢查是否被阻擋 (MOPS 阻擋時通常沒有 公司代號 欄位)
                    if "公司代號" not in response.text and "查無" not in response.text and "無符合" not in response.text:
                        error_msg = "偵測到存取受限，IP 可能已被封鎖或頁面格式錯誤"
                        continue

                    soup = BeautifulSoup(response.content, 'html.parser')
                    
                    tables = soup.find_all('table')
                    if not tables:
                        if len(response.content) < 5000:
                            error_msg = "偵測到存取受限，IP 可能已被封鎖"
                        continue

                    main_table = max(tables, key=lambda t: len(t.find_all('tr')))
                    rows = main_table.find_all('tr')
                    
                    for row in rows[1:]:  # 跳過表頭
                        cols = row.find_all('td')
                        if len(cols) >= 5:
                            # 根據頁面類型調整欄位讀取順序
                            if mode == "today":
                                co_id    = cols[0].get_text(strip=True)
                                co_name  = cols[1].get_text(strip=True)
                                date_str = cols[2].get_text(strip=True)
                                time_str = cols[3].get_text(strip=True)
                                title    = cols[4].get_text(strip=True)
                            else:
                                # 前一日 (t05st02) 頁面欄位順序不同
                                date_str = cols[0].get_text(strip=True)
                                time_str = cols[1].get_text(strip=True)
                                co_id    = cols[2].get_text(strip=True)
                                co_name  = cols[3].get_text(strip=True)
                                title    = cols[4].get_text(strip=True)
                            
                            if not co_id or not title or not date_str:
                                continue
                            
                            try:
                                # 處理日期轉換
                                year_part, month_part, day_part = date_str.split('/')
                                west_year = int(year_part) + 1911
                                dt_obj = datetime.strptime(f"{west_year}/{month_part}/{day_part} {time_str}", "%Y/%m/%d %H:%M:%S")
                            except Exception:
                                dt_obj = get_tw_time()
                            
                            if dt_obj < two_days_ago:
                                continue
                            
                            matched_tags = [kw for kw in self.keywords if all(sk in title or sk in co_name for sk in kw.split('+'))]
                            if matched_tags:
                                event_id = hashlib.md5(f"mops_{co_id}_{date_str}_{time_str}_{title}".encode('utf-8')).hexdigest()
                                
                                if event_id not in events_dict:
                                    formatted_time = dt_obj.strftime("%Y/%m/%d %H:%M")
                                    events_dict[event_id] = {
                                        'id': event_id,
                                        'source': '公開資訊觀測站',
                                        'type': '重大訊息',
                                        'co_id': co_id,
                                        'co_name': co_name,
                                        'datetime_obj': dt_obj,
                                        'datetime_str': formatted_time,
                                        'title': title,
                                        'link': p_url,
                                        'tags': matched_tags
                                    }
                                else:
                                    # 如果已存在，合併標籤
                                    existing_tags = events_dict[event_id]['tags']
                                    events_dict[event_id]['tags'] = list(set(existing_tags + matched_tags))
                                    
                except Exception as inner_e:
                    logger.error(f"MOPS {label}爬取失敗: {inner_e}")
                    if not error_msg:
                        error_msg = f"連線異常，IP 可能已被封鎖 ({type(inner_e).__name__})"
                    continue
                        
        except Exception as e:
            logger.error(f"MOPS 爬蟲 Session 建立失敗: {e}")
        
        all_events = list(events_dict.values())
        logger.info(f"MOPS 共抓取 {len(all_events)} 筆符合條件的重大訊息")
        return all_events, error_msg

class NewsCrawler:
    def __init__(self, keywords):
        self.keywords = keywords
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }
        # 限定抓取目標網站 (包含指定財經網站)
        self.site_query = "(site:cnyes.com OR site:money.udn.com OR site:ctee.com.tw OR site:chinatimes.com OR site:ltn.com.tw OR site:wantgoo.com OR site:cmoney.tw)"

    def fetch_news(self):
        two_days_ago = get_tw_time() - timedelta(days=2)
        error_msg = None
        title_dict = {}

        def get_priority(url):
            # 優先級：1 (原始媒體), 2 (轉載/聚合)
            original_sites = ['cnyes.com', 'money.udn.com', 'ctee.com.tw', 'chinatimes.com', 'ltn.com.tw']
            for site in original_sites:
                if site in url: return 1
            return 2

        for kw in self.keywords:
            try:
                # 使用 Google News RSS 搜尋
                query = f"{kw} {self.site_query}"
                encoded_query = urllib.parse.quote(query)
                rss_url = f"https://news.google.com/rss/search?q={encoded_query}&hl=zh-TW&gl=TW&ceid=TW:zh-Hant"
                
                time.sleep(random.uniform(1.5, 3.0)) # 稍微加快一點點
                response = requests.get(rss_url, headers=self.headers, timeout=15)
                soup = BeautifulSoup(response.content, 'xml')
                
                items = soup.find_all('item')
                for item in items:
                    title = item.title.text if item.title else ""
                    link = item.link.text if item.link else ""
                    pubDate_str = item.pubDate.text if item.pubDate else ""
                    
                    dt_obj_naive = get_tw_time()
                    try:
                        dt_obj = parsedate_to_datetime(pubDate_str)
                        dt_obj_tw = dt_obj.astimezone(timezone(timedelta(hours=8)))
                        dt_obj_naive = dt_obj_tw.replace(tzinfo=None)
                        if dt_obj_naive < two_days_ago:
                            continue
                    except Exception:
                        pass
                    
                    # 檢查該則新聞符合的所有關鍵字 (支援 A+B 複合格式)
                    current_matched = []
                    for k in self.keywords:
                        # 支援以 + 號連接的複合關鍵字，需同時滿足標題內包含所有子詞
                        sub_kws = k.split('+')
                        if all(sk in title for sk in sub_kws):
                            current_matched.append(k)
                    
                    if current_matched:
                        # 1. 標題正規化用於去重
                        # 去除 Google News 結尾的來源 (e.g. " - Yahoo奇摩股市")
                        clean_title = title.rsplit(' - ', 1)[0].strip()
                        # 進一步去除標題中的分類標籤 (e.g. "| 國際焦點 | 國際" 或 "｜ 財經")
                        clean_title = clean_title.split('|')[0].split('｜')[0].strip()
                        # 去除標題中常見的括號內容 (e.g. [速報], 【公告】, (2330))
                        clean_title = re.sub(r'[\[【\(（].*?[\]】\)）]', '', clean_title).strip()
                        
                        priority = get_priority(link)
                        news_id = hashlib.md5(f"news_{link}".encode('utf-8')).hexdigest()
                        formatted_time = dt_obj_naive.strftime("%Y/%m/%d %H:%M")
                        
                        item_data = {
                            'id': news_id,
                            'source': '財經新聞',
                            'type': '新聞',
                            'datetime_obj': dt_obj_naive,
                            'datetime_str': formatted_time,
                            'title': title,
                            'link': link,
                            'tags': current_matched,
                            'priority': priority
                        }

                        if clean_title not in title_dict:
                            title_dict[clean_title] = item_data
                        else:
                            existing = title_dict[clean_title]
                            # 合併並去重標籤
                            existing['tags'] = list(set(existing['tags'] + current_matched))
                            
                            # 檢查是否需要替換為更優先的來源
                            # 優先級數字越小越優先 (1 > 2)
                            if priority < existing['priority']:
                                item_data['tags'] = list(set(item_data['tags'] + existing['tags']))
                                title_dict[clean_title] = item_data
                            elif priority == existing['priority']:
                                # 同優先級，保留發布時間較早的 (通常是原始報導)
                                if dt_obj_naive < existing['datetime_obj']:
                                    item_data['tags'] = list(set(item_data['tags'] + existing['tags']))
                                    title_dict[clean_title] = item_data
                        
            except Exception as e:
                logger.error(f"新聞爬取失敗 (關鍵字={kw}): {e}")
                if "403" in str(e) or "429" in str(e):
                    error_msg = "新聞搜尋請求過於頻繁，可能已被暫時封鎖"
                elif not error_msg:
                    error_msg = f"新聞搜尋連線異常 ({type(e).__name__})"
                
        unique_news = list(title_dict.values())
        unique_news.sort(key=lambda x: (len(x['tags']), x['datetime_obj']), reverse=True)
        
        return unique_news, error_msg
