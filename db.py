#!/usr/bin/env python3
"""
read-later 共享 SQLite helper
替代 bash + sqlite3 命令行拼接,提供安全的参数化查询。

用法:
  python3 db.py exists <url>
  python3 db.py insert <url> <source_type>
  python3 db.py get-id <url>

返回:
  exists → "0" 或 "1" (stdout)
  insert → 新插入行的 id (stdout)
  get-id → 已存在行的 id (stdout)
  没找到 → 空字符串 + exit code 1
"""
import os
import sqlite3
import sys


DB_PATH = os.path.expanduser("~/.openclaw/workspace/read-later/articles.db")


def get_conn():
    """Open SQLite connection. Uses Row factory for dict-like access."""
    if not os.path.exists(DB_PATH):
        sys.stderr.write(f"DB not found: {DB_PATH}\n")
        sys.exit(2)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def cmd_exists(url):
    conn = get_conn()
    row = conn.execute("SELECT id FROM articles WHERE url = ?", (url,)).fetchone()
    conn.close()
    if row:
        print(row["id"])
        sys.exit(0)
    print("")
    sys.exit(1)


def cmd_insert(url, source_type):
    conn = get_conn()
    try:
        try:
            cur = conn.execute(
                "INSERT INTO articles (url, source_type) VALUES (?, ?)",
                (url, source_type),
            )
            conn.commit()
            print(cur.lastrowid)
            sys.exit(0)
        except sqlite3.IntegrityError:
            # UNIQUE 冲突 → URL 已存在
            row = conn.execute(
                "SELECT id FROM articles WHERE url = ?", (url,)
            ).fetchone()
            if row:
                print(f"DUPLICATE:{row['id']}")
                sys.exit(0)
            print("DUPLICATE")
            sys.exit(1)
    finally:
        conn.close()


def cmd_get_id(url):
    conn = get_conn()
    row = conn.execute("SELECT id FROM articles WHERE url = ?", (url,)).fetchone()
    conn.close()
    if row:
        print(row["id"])
        sys.exit(0)
    sys.exit(1)


def main():
    if len(sys.argv) < 2:
        sys.stderr.write("Usage: db.py <exists|insert|get-id> <args>\n")
        sys.exit(2)

    cmd = sys.argv[1]
    if cmd == "exists":
        cmd_exists(sys.argv[2])
    elif cmd == "insert":
        cmd_insert(sys.argv[2], sys.argv[3])
    elif cmd == "get-id":
        cmd_get_id(sys.argv[2])
    else:
        sys.stderr.write(f"Unknown command: {cmd}\n")
        sys.exit(2)


if __name__ == "__main__":
    main()