# scripts/add_category_color.py
import os
import sqlite3
import sys

# Percorso DB: aggiorna se il tuo file è altrove
DB_PATH = os.environ.get("DB_PATH", "app/db.sqlite3")

def column_exists(conn, table, column):
    cur = conn.execute(f"PRAGMA table_info({table});")
    cols = [r[1].lower() for r in cur.fetchall()]
    return column.lower() in cols

def main():
    if not os.path.exists(DB_PATH):
        print(f"[ERR] DB non trovato: {DB_PATH}")
        sys.exit(1)

    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute("BEGIN")
        if not column_exists(conn, "category", "color_hex"):
            print("[MIGRATE] Aggiungo category.color_hex ...")
            conn.execute("ALTER TABLE category ADD COLUMN color_hex TEXT NULL;")
            # opzionale: imposta un default vuoto
            # conn.execute("UPDATE category SET color_hex = NULL WHERE color_hex IS NULL;")
        else:
            print("[SKIP] category.color_hex esiste già, niente da fare.")
        conn.commit()
    except Exception as e:
        conn.rollback()
        print("[ERR] Migrazione fallita:", e)
        sys.exit(2)
    finally:
        conn.close()

    # Verifica
    conn = sqlite3.connect(DB_PATH)
    try:
        cur = conn.execute("PRAGMA table_info(category);")
        print("[OK] Colonne category:", [r[1] for r in cur.fetchall()])
    finally:
        conn.close()

if __name__ == "__main__":
    main()
