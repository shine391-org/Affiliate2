import os
import glob
import sqlite3
import shutil

user_data = r"C:\Users\shine\AppData\Local\Google\Chrome\User Data"
profiles = ["Default"] + [f"Profile {i}" for i in range(1, 20)]

found = False
for p in profiles:
    p_path = os.path.join(user_data, p)
    cookie_db = os.path.join(p_path, "Network", "Cookies")
    if not os.path.exists(cookie_db):
        cookie_db = os.path.join(p_path, "Cookies")
    if os.path.exists(cookie_db):
        temp_db = os.path.join(os.environ.get("TEMP", "."), f"test_cookie_{p.replace(' ', '_')}.db")
        try:
            shutil.copyfile(cookie_db, temp_db)
            conn = sqlite3.connect(temp_db)
            cur = conn.cursor()
            cur.execute("SELECT host_key, count(1) FROM cookies WHERE host_key LIKE '%douyin%' GROUP BY host_key")
            rows = cur.fetchall()
            if rows:
                print(f"Found Douyin cookies in {p}: {rows}")
                found = True
                cur.execute("SELECT name, host_key FROM cookies WHERE host_key LIKE '%douyin%'")
                cookie_names = [r[0] for r in cur.fetchall()]
                print(f"Cookie names in {p}: {cookie_names}")

            conn.close()
            if os.path.exists(temp_db):
                os.remove(temp_db)
        except Exception as e:
            pass

if not found:
    print("No Douyin cookies found in any Chrome profile.")

# Also check Edge
edge_data = r"C:\Users\shine\AppData\Local\Microsoft\Edge\User Data"
for p in ["Default"] + [f"Profile {i}" for i in range(1, 10)]:
    p_path = os.path.join(edge_data, p)
    cookie_db = os.path.join(p_path, "Network", "Cookies")
    if os.path.exists(cookie_db):
        temp_db = os.path.join(os.environ.get("TEMP", "."), f"test_edge_cookie_{p.replace(' ', '_')}.db")
        try:
            shutil.copyfile(cookie_db, temp_db)
            conn = sqlite3.connect(temp_db)
            cur = conn.cursor()
            cur.execute("SELECT host_key, count(1) FROM cookies WHERE host_key LIKE '%douyin%' GROUP BY host_key")
            rows = cur.fetchall()
            if rows:
                print(f"Found Douyin cookies in Edge {p}: {rows}")
            conn.close()
            if os.path.exists(temp_db):
                os.remove(temp_db)
        except Exception as e:
            pass
