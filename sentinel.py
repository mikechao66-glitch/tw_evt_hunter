import os
import json
import logging
from tw_evt_hunter import EventDatabase, TelegramNotifier, MopsCrawler, NewsCrawler

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def load_config(config_path="config.json"):
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"無法讀取設定檔 {config_path}: {e}")
        return None

def run_hunter():
    logger.info("開始執行台股事件獵人...")
    config = load_config()
    if not config:
        logger.error("缺少設定檔，終止執行。")
        return

    # 初始化模組
    db = EventDatabase()
    notifier = TelegramNotifier(
        bot_token=config['telegram']['bot_token'],
        chat_id=config['telegram']['chat_id']
    )
    
    mops_crawler = MopsCrawler(keywords=config.get('mops_keywords', []))
    news_crawler = NewsCrawler(keywords=config.get('news_keywords', []))

    all_events = []

    # 1. 抓取 MOPS 重大訊息
    logger.info("開始抓取公開資訊觀測站重大訊息...")
    mops_events = mops_crawler.fetch_today_events()
    logger.info(f"抓取到 {len(mops_events)} 筆相關重大訊息")
    all_events.extend(mops_events)

    # 2. 抓取新聞
    logger.info("開始抓取財經新聞...")
    news_events = news_crawler.fetch_news()
    logger.info(f"抓取到 {len(news_events)} 筆相關新聞")
    all_events.extend(news_events)

    # 3. 排序 (由新到舊: descending)
    all_events.sort(key=lambda x: x['datetime_obj'], reverse=True)

    # 4. 存檔與發送推播
    sent_count = 0
    for evt in all_events:
        if not db.is_sent(evt['id']):
            # 存入資料庫 (包含完整資訊與標籤)
            db.insert_event(evt)
            
            # 準備推播訊息格式 (HTML)
            tags_str = " ".join([f"#{t}" for t in evt.get('tags', [])])
            if evt['type'] == '重大訊息':
                msg_text = (
                    f"🚨 <b>【重大訊息】</b>\n"
                    f"🏢 {evt['co_id']} {evt['co_name']}\n"
                    f"🕒 {evt['datetime_str']}\n"
                    f"🏷️ {tags_str}\n"
                    f"📌 <b>{evt['title']}</b>\n"
                    f"🔗 <a href='{evt['link']}'>前往公開資訊觀測站查詢</a>"
                )
            else:
                msg_text = (
                    f"📰 <b>【財經新聞】</b>\n"
                    f"🕒 {evt['datetime_str']}\n"
                    f"🏷️ {tags_str}\n"
                    f"📌 <b>{evt['title']}</b>\n"
                    f"🔗 <a href='{evt['link']}'>閱讀全文</a>"
                )

            # 發送 Telegram
            success = notifier.send_message(msg_text)
            if success:
                sent_count += 1
                logger.info(f"已發送推播: {evt['title']}")
            
    logger.info(f"執行完畢，本次共發送 {sent_count} 則新訊息。")

if __name__ == "__main__":
    # 第一階段：可直接執行此檔案進行單次抓取與發送
    # 若未來要在 Streamlit 介面使用手動更新，只要 import run_hunter 即可
    # 若要定時更新，則可在 Streamlit 背景以 schedule 模組定期呼叫 run_hunter()
    run_hunter()
