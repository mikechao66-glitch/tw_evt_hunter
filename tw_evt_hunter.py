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
from email.utils import parsedate_to_datetime

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
        all_events = []
        # 計算近二日的日期範圍供過濾
        two_days_ago = datetime.now() - timedelta(days=2)
        
        try:
            # 建立 Session ，先 GET 頁面取得 Cookie
            session = requests.Session()
            session.headers.update(self.headers)
            session.get(self.page_url, timeout=15)
            time.sleep(random.uniform(1.0, 2.0))
            
            # 用正確參數 POST （TYPEK=all, step=0）取得全市場即時重大訊息
            response = session.post(
                self.ajax_url,
                data={'TYPEK': 'all', 'step': '0'},
                headers={'Referer': self.page_url},
                timeout=15
            )
            soup = BeautifulSoup(response.content, 'html.parser')
            
            tables = soup.find_all('table')
            if not tables:
                logger.warning("MOPS: 未找到表格資料")
                return []

            # 找包含重大訊息資料的最大表格
            main_table = max(tables, key=lambda t: len(t.find_all('tr')))
            rows = main_table.find_all('tr')
            
            for row in rows[1:]:  # 跳過表頭
                cols = row.find_all('td')
                if len(cols) >= 5:
                    co_id    = cols[0].get_text(strip=True)
                    co_name  = cols[1].get_text(strip=True)
                    date_str = cols[2].get_text(strip=True)  # 民國年 e.g. 115/04/30
                    time_str = cols[3].get_text(strip=True)
                    title    = cols[4].get_text(strip=True)
                    
                    # 跳過空白或非法資料
                    if not co_id or not title or not date_str:
                        continue
                    
                    # 轉換民國年為西元年
                    try:
                        year, month, day = date_str.split('/')
                        west_year = int(year) + 1911
                        dt_obj = datetime.strptime(f"{west_year}/{month}/{day} {time_str}", "%Y/%m/%d %H:%M:%S")
                    except Exception:
                        dt_obj = datetime.now()
                    
                    # 过濾超過二日的訊息
                    if dt_obj < two_days_ago:
                        continue
                    
                    # 檢查關鍵字 (同時搜尋主旨與公司名稱)
                    matched_tags = [kw for kw in self.keywords if kw in title or kw in co_name]
                    if matched_tags:
                        event_id = hashlib.md5(f"mops_{co_id}_{date_str}_{time_str}_{title}".encode('utf-8')).hexdigest()
                        formatted_time = dt_obj.strftime("%Y/%m/%d %H:%M")
                        
                        all_events.append({
                            'id': event_id,
                            'source': '公開資訊觀測站',
                            'type': '重大訊息',
                            'co_id': co_id,
                            'co_name': co_name,
                            'datetime_obj': dt_obj,
                            'datetime_str': formatted_time,
                            'title': title,
                            'link': self.page_url,
                            'tags': matched_tags
                        })
                        
        except Exception as e:
            logger.error(f"MOPS 爬取失敗: {e}")
        
        logger.info(f"MOPS 共抓取 {len(all_events)} 筆符合條件的重大訊息")
        return all_events

class NewsCrawler:
    def __init__(self, keywords):
        self.keywords = keywords
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }
        # 限定抓取目標網站 (包含指定財經網站)
        self.site_query = "(site:yahoo.com.tw/finance OR site:cnyes.com OR site:money.udn.com OR site:ctee.com.tw OR site:chinatimes.com OR site:ltn.com.tw)"

    def fetch_news(self):
        all_news = []
        # 近二日
        two_days_ago = datetime.now() - timedelta(days=2)
        
        news_dict = {}
        for kw in self.keywords:
            try:
                # 使用 Google News RSS 搜尋
                query = f"{kw} {self.site_query}"
                encoded_query = urllib.parse.quote(query)
                rss_url = f"https://news.google.com/rss/search?q={encoded_query}&hl=zh-TW&gl=TW&ceid=TW:zh-Hant"
                
                time.sleep(random.uniform(2.0, 4.0))
                response = requests.get(rss_url, headers=self.headers, timeout=15)
                soup = BeautifulSoup(response.content, 'xml')
                
                items = soup.find_all('item')
                for item in items:
                    title = item.title.text if item.title else ""
                    link = item.link.text if item.link else ""
                    pubDate_str = item.pubDate.text if item.pubDate else ""
                    
                    dt_obj = datetime.now()
                    # 檢查日期是否在三天內
                    try:
                        dt_obj = parsedate_to_datetime(pubDate_str)
                        # 轉換為台灣時間 (UTC+8)
                        dt_obj_tw = dt_obj.astimezone(timezone(timedelta(hours=8)))
                        dt_obj_naive = dt_obj_tw.replace(tzinfo=None)
                        if dt_obj_naive < two_days_ago:
                            continue # 太舊的新聞跳過

                    except Exception as e:
                        logger.warning(f"無法解析新聞日期: {pubDate_str}")
                        dt_obj_naive = dt_obj
                        pass # 若解析失敗，保守起見先保留
                    
                    # 雙重確認標題是否包含關鍵字
                    if kw in title:
                        news_id = hashlib.md5(f"news_{link}".encode('utf-8')).hexdigest()
                        
                        if link in news_dict:
                            if kw not in news_dict[link]['tags']:
                                news_dict[link]['tags'].append(kw)
                        else:
                            formatted_time = dt_obj_naive.strftime("%Y/%m/%d %H:%M")
                            news_dict[link] = {
                                'id': news_id,
                                'source': '財經新聞',
                                'type': '新聞',
                                'datetime_obj': dt_obj_naive,
                                'datetime_str': formatted_time,
                                'title': title,
                                'link': link,
                                'tags': [kw]
                            }
                        
            except Exception as e:
                logger.error(f"新聞爬取失敗 (關鍵字={kw}): {e}")
                
        # 轉換回 list
        unique_news = list(news_dict.values())
        
        # 依照時間由新到舊排序
        unique_news.sort(key=lambda x: x['datetime_obj'], reverse=True)
        
        # 只取最新的 10 則
        return unique_news[:10]
