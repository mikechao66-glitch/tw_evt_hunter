import sqlite3

def check_db():
    conn = sqlite3.connect("events.db")
    cursor = conn.cursor()
    
    cursor.execute("SELECT COUNT(*) FROM events")
    total = cursor.fetchone()[0]
    
    cursor.execute("SELECT COUNT(*) FROM events WHERE deleted = 0")
    active = cursor.fetchone()[0]
    
    cursor.execute("SELECT * FROM events WHERE deleted = 0 ORDER BY timestamp DESC LIMIT 5")
    rows = cursor.fetchall()
    
    print(f"Total events: {total}")
    print(f"Active events (deleted=0): {active}")
    print("\nRecent active events:")
    for row in rows:
        print(row)
    
    conn.close()

if __name__ == "__main__":
    check_db()
