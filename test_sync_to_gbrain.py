import sqlite3
import tempfile
import unittest
from pathlib import Path

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
    conn.commit()
    return tmp, db_path, conn


class SyncToGbrainTest(unittest.TestCase):
    def test_ensure_schema_adds_sync_columns_idempotently(self):
        tmp, _db_path, conn = make_db()
        self.addCleanup(tmp.cleanup)

        sync_to_gbrain.ensure_schema(conn)
        sync_to_gbrain.ensure_schema(conn)

        columns = {row[1] for row in conn.execute("PRAGMA table_info(articles)")}
        self.assertIn("gbrain_slug", columns)
        self.assertIn("gbrain_synced_at", columns)
        self.assertIn("gbrain_sync_status", columns)
        self.assertIn("gbrain_sync_error", columns)
        self.assertIn("long_term_value", columns)
        self.assertIn("gbrain_sync_mode", columns)

    def test_ensure_schema_ignores_duplicate_column_race(self):
        tmp, _db_path, conn = make_db()
        self.addCleanup(tmp.cleanup)
        class RaceConnection:
            def __init__(self, wrapped):
                self.wrapped = wrapped
                self.injected = False

            def execute(self, sql, params=()):
                if "ADD COLUMN long_term_value" in sql and not self.injected:
                    self.injected = True
                    self.wrapped.execute("ALTER TABLE articles ADD COLUMN long_term_value INTEGER DEFAULT 0")
                return self.wrapped.execute(sql, params)

            def commit(self):
                return self.wrapped.commit()

        sync_to_gbrain.ensure_schema(RaceConnection(conn))

        columns = {row[1] for row in conn.execute("PRAGMA table_info(articles)")}
        self.assertIn("long_term_value", columns)

    def test_select_eligible_articles_requires_summary_and_high_value_category(self):
        tmp, _db_path, conn = make_db()
        self.addCleanup(tmp.cleanup)
        sync_to_gbrain.ensure_schema(conn)
        conn.executemany(
            """
            INSERT INTO articles (url, source_type, title, summary, category, tags, gbrain_sync_status)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            [
                ("https://example.com/tech", "web", "Tech Article", "real summary", "tech", '["AI"]', None),
                ("https://example.com/life", "web", "Life Article", "real summary", "life", '["travel"]', None),
                ("https://example.com/empty", "web", "Empty", "", "tech", "[]", None),
                ("https://example.com/synced", "web", "Synced", "real summary", "tech", "[]", "synced"),
            ],
        )
        conn.commit()

        rows = sync_to_gbrain.select_eligible_articles(conn)

        self.assertEqual([row["url"] for row in rows], ["https://example.com/tech"])

    def test_select_eligible_articles_can_require_long_term_value_mark(self):
        tmp, _db_path, conn = make_db()
        self.addCleanup(tmp.cleanup)
        sync_to_gbrain.ensure_schema(conn)
        conn.executemany(
            """
            INSERT INTO articles (
              url, source_type, title, summary, category, tags, long_term_value, gbrain_sync_status
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                ("https://example.com/marked", "web", "Marked", "real summary", "life", "[]", 1, None),
                ("https://example.com/unmarked", "web", "Unmarked", "real summary", "tech", "[]", 0, None),
            ],
        )
        conn.commit()

        rows = sync_to_gbrain.select_eligible_articles(conn, only_marked=True)

        self.assertEqual([row["url"] for row in rows], ["https://example.com/marked"])

    def test_sync_articles_puts_markdown_and_marks_success(self):
        tmp, db_path, conn = make_db()
        self.addCleanup(tmp.cleanup)
        sync_to_gbrain.ensure_schema(conn)
        conn.execute(
            """
            INSERT INTO articles (
              url, source_type, title, original_content, summary, category, tags, author, published_date
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "https://example.com/ai-agents",
                "web",
                "AI Agents in Practice",
                "Full original body.",
                "A concise summary.",
                "tech",
                '["AI", "Agents"]',
                "Jane Doe",
                "2026-05-10",
            ),
        )
        conn.commit()
        calls = []

        def runner(argv, input_text):
            calls.append((argv, input_text))
            return sync_to_gbrain.CommandResult(returncode=0, stdout='{"status":"created_or_updated"}\n', stderr="")

        result = sync_to_gbrain.sync_articles(
            db_path=db_path,
            command_runner=runner,
            verbose=False,
        )

        self.assertEqual(result.synced, 1)
        self.assertEqual(calls[0][0][:2], ["gbrain", "put"])
        self.assertEqual(calls[0][0][2], "media/articles/web-1-2cb9bcf9")
        self.assertIn('source_url: "https://example.com/ai-agents"', calls[0][1])
        self.assertIn("## Summary\n\nA concise summary.", calls[0][1])
        self.assertIn("## Original Content\n\nFull original body.", calls[0][1])

        row = conn.execute(
            "SELECT gbrain_slug, gbrain_sync_status, gbrain_sync_error FROM articles WHERE id = 1"
        ).fetchone()
        self.assertEqual(row[0], "media/articles/web-1-2cb9bcf9")
        self.assertEqual(row[1], "synced")
        self.assertIsNone(row[2])


if __name__ == "__main__":
    unittest.main()
