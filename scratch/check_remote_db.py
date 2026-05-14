import requests
import sqlite3
import os

def check_remote_db():
    url = "https://raw.githubusercontent.com/mikechao66-glitch/tw_evt_hunter/main/events.db"
    r = requests.get(url)
    with open("remote_events.db", "wb") as f:
        f.write(r.content)
    
    conn = sqlite3.connect("remote_events.db")
    cursor = conn.cursor()
    
    try:
        cursor.execute("SELECT COUNT(*) FROM events")
        total = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM events WHERE deleted = 0")
        active = cursor.fetchone()[0]
        
        cursor.execute("SELECT title, datetime_str, timestamp FROM events WHERE deleted = 0 ORDER BY timestamp DESC LIMIT 5")
        rows = cursor.fetchall()
        
        print(f"Remote DB - Total events: {total}")
        print(f"Remote DB - Active events (deleted=0): {active}")
        print("\nRecent active events in Remote DB:")
        for row in rows:
            print(row)
    except Exception as e:
        print(f"Error reading remote DB: {e}")
    finally:
        conn.close()
        if os.path.exists("remote_events.db"):
            os.remove("remote_events.db")

if __name__ == "__main__":
    check_remote_db()
