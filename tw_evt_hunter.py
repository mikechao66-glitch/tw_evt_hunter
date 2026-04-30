import os
import json
import sqlite3
import requests
import time
import random
import logging
from datetime import datetime, timedelta
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
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            ''')
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
                (id, title, source, type, datetime_str, link, tags, deleted)
                VALUES (?, ?, ?, ?, ?, ?, ?, 0)
            ''', (
                event_dict['id'], 
                event_dict['title'], 
                event_dict['source'],
                event_dict['type'],
                event_dict['datetime_str'],
                event_dict.get('link', ''),
                ','.join(event_dict.get('tags', []))
            ))
            conn.commit()

    def get_active_events(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute('''
                SELECT * FROM events 
                WHERE deleted = 0 
                ORDER BY timestamp DESC
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
        self.url = "https://mops.twse.com.tw/mops/web/ajax_t05sr01_1"
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Referer": "https://mops.twse.com.tw/mops/web/t05sr01_1",
            "Content-Type": "application/x-www-form-urlencoded"
        }

    def fetch_today_events(self):
        all_events = []
        types = ['sii', 'otc', 'rotc', 'pub'] # 上市, 上櫃, 興櫃, 公開發行
        
        for t in types:
            try:
                payload = {
                    "encodeURIComponent": "1",
                    "step": "1",
                    "firstin": "1",
                    "TYPEK": t,
                    "co_id": ""
                }
                
                # 加入隨機延遲避免被擋
                time.sleep(random.uniform(1.5, 3.5))
                
                response = requests.post(self.url, headers=self.headers, data=payload, timeout=15)
                response.encoding = 'utf8'
                soup = BeautifulSoup(response.text, 'html.parser')
                
                # 找尋表格
                tables = soup.find_all('table', {'class': 'hasBorder'})
                if not tables:
                    continue
                
                rows = tables[0].find_all('tr')
                for row in rows[1:]: # Skip header
                    cols = row.find_all('td')
                    if len(cols) >= 6:
                        # 0:公司代號, 1:公司名稱, 2:發言日期, 3:發言時間, 4:主旨, 5:符合條款
                        co_id = cols[0].text.strip()
                        co_name = cols[1].text.strip()
                        date_str = cols[2].text.strip() # 民國年 e.g. 113/04/29
                        time_str = cols[3].text.strip()
                        title = cols[4].text.strip()
                        
                        # 檢查關鍵字
                        matched_tags = [kw for kw in self.keywords if kw in title]
                        if matched_tags:
                            event_id = hashlib.md5(f"mops_{co_id}_{date_str}_{time_str}_{title}".encode('utf-8')).hexdigest()
                            
                            # 轉換民國年為西元年以便排序
                            try:
                                year, month, day = date_str.split('/')
                                west_year = int(year) + 1911
                                dt_obj = datetime.strptime(f"{west_year}/{month}/{day} {time_str}", "%Y/%m/%d %H:%M:%S")
                            except:
                                dt_obj = datetime.now() # 如果解析失敗，用現在時間
                                
                            # 依照需求格式化發布時間 YYYY/MM/DD HH:MM
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
                                'link': "https://mops.twse.com.tw/mops/web/t05sr01_1",
                                'tags': matched_tags
                            })
                            
            except Exception as e:
                logger.error(f"MOPS 爬取失敗 (TYPEK={t}): {e}")
                
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
        # 近三日
        three_days_ago = datetime.now() - timedelta(days=3)
        
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
                        # Remove timezone info for comparison
                        dt_obj_naive = dt_obj.replace(tzinfo=None)
                        if dt_obj_naive < three_days_ago:
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
