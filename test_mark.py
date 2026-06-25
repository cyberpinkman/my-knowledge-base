import sqlite3
import tempfile
import unittest
from pathlib import Path

import mark
import sync_to_gbrain


def make_db():
    tmp = tempfile.TemporaryDirectory()
    db_path = Path(tmp.name) / "articles.db"
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE articles (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          url TEXT NOT NULL UNIQUE,
          source_type TEXT NOT NULL,
          title TEXT,
          original_content TEXT,
          summary TEXT,
          category TEXT,
          tags TEXT,
          author TEXT,
          published_date TEXT,
          word_count INTEGER,
          is_read INTEGER DEFAULT 0,
          is_reported INTEGER DEFAULT 0,
          created_at TEXT DEFAULT CURRENT_TIMESTAMP,
          read_at TEXT,
          raw_metadata TEXT
        )
        """
    )
    conn.executemany(
        """
        INSERT INTO articles (url, source_type, title, summary, category)
        VALUES (?, ?, ?, ?, ?)
        """,
        [
            ("https://example.com/a", "web", "Article A", "summary", "tech"),
            ("https://example.com/b", "web", "Article B", "summary", "life"),
        ],
    )
    conn.commit()
    sync_to_gbrain.ensure_schema(conn)
    return tmp, db_path, conn


class MarkLongTermValueTest(unittest.TestCase):
    def test_mark_value_sets_long_term_flag_and_manual_mode(self):
        tmp, _db_path, conn = make_db()
        self.addCleanup(tmp.cleanup)

        changed = mark.mark_value(conn, [1, 2])

        self.assertEqual(changed, 2)
        rows = conn.execute(
            "SELECT id, long_term_value, gbrain_sync_mode FROM articles ORDER BY id"
        ).fetchall()
        self.assertEqual([(r["id"], r["long_term_value"], r["gbrain_sync_mode"]) for r in rows], [
            (1, 1, "manual"),
            (2, 1, "manual"),
        ])

    def test_clear_value_removes_manual_sync_gate(self):
        tmp, _db_path, conn = make_db()
        self.addCleanup(tmp.cleanup)
        mark.mark_value(conn, [1])

        changed = mark.clear_value(conn, [1])

        self.assertEqual(changed, 1)
        row = conn.execute(
            "SELECT long_term_value, gbrain_sync_mode FROM articles WHERE id = 1"
        ).fetchone()
        self.assertEqual(row["long_term_value"], 0)
        self.assertIsNone(row["gbrain_sync_mode"])

    def test_list_articles_can_filter_marked(self):
        tmp, _db_path, conn = make_db()
        self.addCleanup(tmp.cleanup)
        mark.mark_value(conn, [2])

        rows = mark.list_articles(conn, marked=True)

        self.assertEqual([(row["id"], row["title"]) for row in rows], [(2, "Article B")])


if __name__ == "__main__":
    unittest.main()
