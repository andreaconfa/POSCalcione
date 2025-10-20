import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DB = ROOT / "app.db"
con = sqlite3.connect(DB)
cur = con.cursor()

cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='product';")
if not cur.fetchone():
    print("product table missing. Run the app once to create tables.")
    raise SystemExit(1)

cur.execute("PRAGMA table_info(product)")
cols = [r[1] for r in cur.fetchall()]
if "image_url" not in cols:
    cur.execute("ALTER TABLE product ADD COLUMN image_url TEXT NULL")
    con.commit()
    print("image_url added")
else:
    print("image_url already present")
con.close()