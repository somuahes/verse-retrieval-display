import sqlite3
import json
import os

# ════════════════════════════════════════════════════════════
#  PATHS
# ════════════════════════════════════════════════════════════
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "bible.db")
DATA_DIR = os.path.join(BASE_DIR, "..", "..", "data")


# ════════════════════════════════════════════════════════════
#  VERSION REGISTRY
# ════════════════════════════════════════════════════════════
VERSIONS = [
    {
        "code":      "KJV",
        "full_name": "King James Version",
        "file":      "en_kjv.json"
    },
    {
        "code":      "BBE",
        "full_name": "Bible in Basic English",
        "file":      "en_bbe.json"
    },
    {
        "code":      "TWI",
        "full_name": "Asante Twi Bible",
        "file":      "tw_asante.json"
    },
]

DEFAULT_VERSION = "KJV"


# ════════════════════════════════════════════════════════════
#  CREATE TABLES
# ════════════════════════════════════════════════════════════
def create_tables(cursor):
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS versions (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            code      TEXT UNIQUE NOT NULL,
            full_name TEXT NOT NULL
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS verses (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            version_id INTEGER NOT NULL,
            book       TEXT NOT NULL,
            chapter    INTEGER NOT NULL,
            verse      INTEGER NOT NULL,
            text       TEXT NOT NULL,
            FOREIGN KEY (version_id) REFERENCES versions(id)
        )
    """)

    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_lookup
        ON verses (version_id, book, chapter, verse)
    """)

    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_text
        ON verses (version_id, text)
    """)


# ════════════════════════════════════════════════════════════
#  IMPORT ONE VERSION
# ════════════════════════════════════════════════════════════
def import_version(cursor, version_info):
    code = version_info["code"]
    full_name = version_info["full_name"]
    filepath = os.path.join(DATA_DIR, version_info["file"])

    if not os.path.exists(filepath):
        print(f"  ⚠️  File not found, skipping: {version_info['file']}")
        return 0

    cursor.execute("SELECT id FROM versions WHERE code = ?", (code,))
    if cursor.fetchone():
        print(f"  ✅ {code} already imported. Skipping.")
        return 0

    cursor.execute(
        "INSERT INTO versions (code, full_name) VALUES (?, ?)",
        (code, full_name)
    )
    version_id = cursor.lastrowid

    # utf-8-sig handles the BOM character in downloaded files
    with open(filepath, "r", encoding="utf-8-sig") as f:
        bible_data = json.load(f)

    total = 0
    for book_data in bible_data:
        book_name = book_data["name"]
        for chapter_num, chapter_verses in enumerate(book_data["chapters"], start=1):
            for verse_num, verse_text in enumerate(chapter_verses, start=1):
                cursor.execute(
                    """INSERT INTO verses
                       (version_id, book, chapter, verse, text)
                       VALUES (?, ?, ?, ?, ?)""",
                    (version_id, book_name, chapter_num, verse_num, verse_text)
                )
                total += 1

    print(f"  ✅ {code} — {total:,} verses imported")
    return total


# ════════════════════════════════════════════════════════════
#  BUILD THE DATABASE
# ════════════════════════════════════════════════════════════
def build_database():
    print("=" * 50)
    print("  BIBLE DATABASE BUILDER")
    print("=" * 50)

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    create_tables(cursor)

    grand_total = 0
    for v in VERSIONS:
        print(f"\n📖 Importing {v['code']} ({v['full_name']})...")
        grand_total += import_version(cursor, v)

    conn.commit()
    conn.close()

    print("\n" + "=" * 50)
    print(f"  ✅ DATABASE READY")
    print(f"  📊 {grand_total:,} total verses imported")
    print(f"  📁 Saved to: {DB_PATH}")
    print("=" * 50)


# ════════════════════════════════════════════════════════════
#  GET ALL VERSES — used by SemanticEngine (app/retrieval/semantic.py)
#  to build its FAISS index. The only reader query still used from this
#  module — hybrid.py implements its own version-code/book-name-aware
#  lookups (db_get_verse, db_get_next_verse, db_list_versions, etc.)
#  independently and is what every live caller (main_ui.py, the
#  evaluation pipeline) actually uses.
#
#  Usage:
#    verses = get_all_verses()
#    verses = get_all_verses("BBE")
# ════════════════════════════════════════════════════════════
def get_all_verses(version=DEFAULT_VERSION):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute("SELECT id FROM versions WHERE code = ?", (version.upper(),))
    row = cursor.fetchone()
    if not row:
        conn.close()
        raise ValueError(f"Version '{version}' not found in database.")
    vid = row[0]

    cursor.execute(
        """SELECT book, chapter, verse, text FROM verses
           WHERE version_id=? ORDER BY id""",
        (vid,)
    )
    rows = cursor.fetchall()
    conn.close()

    return [{"version": version, "book": r[0], "chapter": r[1],
             "verse": r[2], "text": r[3]} for r in rows]


# ════════════════════════════════════════════════════════════
#  RUN THIS FILE TO (RE)BUILD THE DATABASE FROM data/en_*.json
# ════════════════════════════════════════════════════════════
if __name__ == "__main__":
    build_database()

    print("\n📖 Sanity check — John 3:16 in both versions:")
    for v in get_all_verses("KJV"):
        if v["book"] == "John" and v["chapter"] == 3 and v["verse"] == 16:
            print(f"   KJV — {v['text']}")
            break
    for v in get_all_verses("BBE"):
        if v["book"] == "John" and v["chapter"] == 3 and v["verse"] == 16:
            print(f"   BBE — {v['text']}")
            break
