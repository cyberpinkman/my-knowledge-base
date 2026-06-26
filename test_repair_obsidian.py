import sqlite3
import tempfile
import unittest
from pathlib import Path

import repair_obsidian


def make_db():
    tmp = tempfile.TemporaryDirectory()
    db_path = Path(tmp.name) / "articles.db"
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.executescript(Path("schema.sql").read_text())
    conn.commit()
    return tmp, db_path, conn


class RepairObsidianTest(unittest.TestCase):
    def test_backfills_missing_notes_without_refetching_or_reanalyzing(self):
        tmp, db_path, conn = make_db()
        self.addCleanup(tmp.cleanup)
        self.addCleanup(conn.close)
        vault = Path(tmp.name) / "vault"
        existing_dir = vault / "稍后阅读" / "技术"
        existing_dir.mkdir(parents=True)
        (existing_dir / "Existing.md").write_text(
            """---
title: "Existing"
source: "https://example.com/existing"
---

old note
""",
            encoding="utf-8",
        )
        conn.executemany(
            """
            INSERT INTO articles (url, source_type, title, original_content, summary, category, tags, author)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    "https://example.com/existing",
                    "web",
                    "Existing",
                    "Existing body.",
                    "Existing summary.",
                    "tech",
                    '["AI"]',
                    "Alice",
                ),
                (
                    "https://example.com/missing",
                    "web",
                    "Missing",
                    "Missing body.",
                    "Missing summary.",
                    "business",
                    '["产品"]',
                    "Bob",
                ),
                (
                    "https://example.com/unprocessed",
                    "web",
                    "Unprocessed",
                    "Unprocessed body.",
                    "",
                    "business",
                    "[]",
                    "",
                ),
            ],
        )
        conn.commit()

        report = repair_obsidian.repair_obsidian_notes(
            db_path=db_path,
            vault_path=vault,
            all_missing=True,
        )

        self.assertEqual(report.scanned, 2)
        self.assertEqual(report.already_present, 1)
        self.assertEqual(report.written, 1)
        notes = sorted(vault.rglob("*.md"))
        self.assertEqual(len(notes), 2)
        written = next(path for path in notes if path.name == "Missing.md")
        text = written.read_text(encoding="utf-8")
        self.assertIn('source: "https://example.com/missing"', text)
        self.assertIn("Missing summary.", text)
        self.assertIn("Missing body.", text)
        self.assertIn("*由 my-knowledge-base 自动收录于", text)

    def test_dry_run_reports_missing_notes_without_writing(self):
        tmp, db_path, conn = make_db()
        self.addCleanup(tmp.cleanup)
        self.addCleanup(conn.close)
        vault = Path(tmp.name) / "vault"
        conn.execute(
            """
            INSERT INTO articles (url, source_type, title, original_content, summary, category, tags)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "https://example.com/missing",
                "pdf",
                "Missing PDF",
                "PDF body.",
                "PDF summary.",
                "tech",
                "AI,PDF",
            ),
        )
        conn.commit()

        report = repair_obsidian.repair_obsidian_notes(
            db_path=db_path,
            vault_path=vault,
            all_missing=True,
            dry_run=True,
        )

        self.assertEqual(report.would_write, 1)
        self.assertEqual(list(vault.rglob("*.md")), [])

    def test_source_in_idea_note_does_not_suppress_read_later_backfill(self):
        tmp, db_path, conn = make_db()
        self.addCleanup(tmp.cleanup)
        self.addCleanup(conn.close)
        vault = Path(tmp.name) / "vault"
        idea_dir = vault / "灵感" / "待探索"
        idea_dir.mkdir(parents=True)
        (idea_dir / "Copied Source.md").write_text(
            """---
title: "Copied Source"
source: "https://example.com/collision"
---
""",
            encoding="utf-8",
        )
        conn.execute(
            """
            INSERT INTO articles (url, source_type, title, original_content, summary, category, tags)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "https://example.com/collision",
                "web",
                "Collision",
                "Collision body.",
                "Collision summary.",
                "tech",
                "[]",
            ),
        )
        conn.commit()

        report = repair_obsidian.repair_obsidian_notes(
            db_path=db_path,
            vault_path=vault,
            all_missing=True,
            dry_run=True,
        )

        self.assertEqual(report.scanned, 1)
        self.assertEqual(report.already_present, 0)
        self.assertEqual(report.would_write, 1)
        self.assertIn("稍后阅读", report.paths[0])

    def test_all_vault_can_treat_non_read_later_note_as_existing(self):
        tmp, db_path, conn = make_db()
        self.addCleanup(tmp.cleanup)
        self.addCleanup(conn.close)
        vault = Path(tmp.name) / "vault"
        idea_dir = vault / "灵感" / "待探索"
        idea_dir.mkdir(parents=True)
        copied = idea_dir / "Copied Source.md"
        copied.write_text(
            """---
title: "Copied Source"
source: "https://example.com/collision"
---
""",
            encoding="utf-8",
        )
        conn.execute(
            """
            INSERT INTO articles (url, source_type, title, original_content, summary, category, tags)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "https://example.com/collision",
                "web",
                "Collision",
                "Collision body.",
                "Collision summary.",
                "tech",
                "[]",
            ),
        )
        conn.commit()

        report = repair_obsidian.repair_obsidian_notes(
            db_path=db_path,
            vault_path=vault,
            all_missing=True,
            dry_run=True,
            all_vault=True,
        )

        self.assertEqual(report.already_present, 1)
        self.assertEqual(report.would_write, 0)
        self.assertEqual(report.paths, [str(copied)])

    def test_migrates_legacy_footer_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp) / "vault"
            note = vault / "稍后阅读" / "视频" / "Old.md"
            note.parent.mkdir(parents=True)
            note.write_text(
                "*由智能稍后阅读服务自动收录于 2026-06-25*",
                encoding="utf-8",
            )

            report = repair_obsidian.migrate_legacy_footers(vault_path=vault)

            self.assertEqual(report.footer_migrated, 1)
            text = note.read_text(encoding="utf-8")
            self.assertIn("*由 my-knowledge-base 自动收录于 2026-06-25*", text)
            self.assertNotIn("智能稍后阅读服务", text)

    def test_migrates_legacy_footer_only_under_read_later_by_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp) / "vault"
            read_later_note = vault / "稍后阅读" / "视频" / "Old.md"
            idea_note = vault / "灵感" / "待探索" / "Copied.md"
            read_later_note.parent.mkdir(parents=True)
            idea_note.parent.mkdir(parents=True)
            legacy = "*由智能稍后阅读服务自动收录于 2026-06-25*"
            read_later_note.write_text(legacy, encoding="utf-8")
            idea_note.write_text(legacy, encoding="utf-8")

            report = repair_obsidian.migrate_legacy_footers(vault_path=vault)

            self.assertEqual(report.footer_migrated, 1)
            self.assertNotIn("智能稍后阅读服务", read_later_note.read_text(encoding="utf-8"))
            self.assertIn("智能稍后阅读服务", idea_note.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
