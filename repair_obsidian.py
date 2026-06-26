#!/usr/bin/env python3
"""Repair Obsidian notes from processed SQLite rows without refetching.

This script is for historical repair only. It reads already processed rows from
articles.db and writes missing Obsidian notes using the same writer as the main
pipeline. It does not fetch source URLs or call an LLM.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import re
import sqlite3
from pathlib import Path
from typing import Iterable

import pipeline


LEGACY_FOOTER = "由智能稍后阅读服务自动收录于"
CURRENT_FOOTER = "由 my-knowledge-base 自动收录于"
READ_LATER_DIR = "稍后阅读"


@dataclasses.dataclass
class RepairReport:
    scanned: int = 0
    already_present: int = 0
    written: int = 0
    would_write: int = 0
    footer_migrated: int = 0
    footer_would_migrate: int = 0
    skipped_unprocessed: int = 0
    paths: list[str] = dataclasses.field(default_factory=list)


def select_processed_articles(
    conn: sqlite3.Connection,
    *,
    article_id: int | None = None,
    limit: int | None = None,
) -> list[sqlite3.Row]:
    where = [
        "TRIM(COALESCE(title, '')) != ''",
        "TRIM(COALESCE(summary, '')) != ''",
    ]
    params: list[object] = []
    if article_id is not None:
        where.append("id = ?")
        params.append(article_id)

    sql = f"""
        SELECT *
        FROM articles
        WHERE {' AND '.join(where)}
        ORDER BY id
    """
    if limit is not None:
        sql += " LIMIT ?"
        params.append(limit)
    return conn.execute(sql, params).fetchall()


def select_article_by_id(conn: sqlite3.Connection, article_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM articles WHERE id = ?", (article_id,)).fetchone()


def repair_obsidian_notes(
    *,
    db_path: Path | str = pipeline.DEFAULT_DB,
    vault_path: Path | str = pipeline.DEFAULT_VAULT,
    all_missing: bool = False,
    article_id: int | None = None,
    limit: int | None = None,
    dry_run: bool = False,
    all_vault: bool = False,
) -> RepairReport:
    if not all_missing and article_id is None:
        raise ValueError("choose --all-missing or --article-id")

    vault = Path(vault_path).expanduser()
    source_index = build_source_index(note_scan_root(vault, all_vault=all_vault))
    report = RepairReport()

    conn = pipeline.open_db(db_path)
    try:
        if article_id is not None and select_article_by_id(conn, article_id) is None:
            raise ValueError(f"article not found: {article_id}")
        rows = select_processed_articles(conn, article_id=article_id, limit=limit)
        if article_id is not None and not rows:
            report.skipped_unprocessed = 1
            return report

        for row in rows:
            report.scanned += 1
            url = row_text(row, "url")
            existing = source_index.get(url)
            if existing:
                report.already_present += 1
                report.paths.append(str(existing))
                continue

            if dry_run:
                report.would_write += 1
                report.paths.append(expected_note_hint(row, vault))
                continue

            note_path = pipeline.write_obsidian_note(
                title=row_text(row, "title"),
                url=url,
                source_type=row_text(row, "source_type") or "web",
                author=row_text(row, "author"),
                category=row_text(row, "category") or "other",
                tags=parse_tags(row["tags"]),
                summary=row_text(row, "summary"),
                content=row_text(row, "original_content"),
                key_points="",
                vault_path=vault,
            )
            report.written += 1
            report.paths.append(str(note_path))
            source_index[url] = Path(note_path)
    finally:
        conn.close()

    return report


def note_scan_root(vault_path: Path, *, all_vault: bool = False) -> Path:
    vault = vault_path.expanduser()
    return vault if all_vault else vault / READ_LATER_DIR


def build_source_index(root_path: Path) -> dict[str, Path]:
    root = root_path.expanduser()
    index: dict[str, Path] = {}
    if not root.exists():
        return index
    for note in sorted(root.rglob("*.md")):
        source = extract_source(note)
        if source and source not in index:
            index[source] = note
    return index


def extract_source(note_path: Path) -> str:
    try:
        text = note_path.read_text(encoding="utf-8")[:4096]
    except OSError:
        return ""
    frontmatter = text
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            frontmatter = parts[1]
    match = re.search(r"(?m)^source:\s*(.+?)\s*$", frontmatter)
    if not match:
        return ""
    raw = match.group(1).strip()
    if not raw:
        return ""
    if raw[0] in {"'", '"'}:
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return raw.strip("'\"")
    return raw


def migrate_legacy_footers(
    *,
    vault_path: Path | str = pipeline.DEFAULT_VAULT,
    dry_run: bool = False,
    all_vault: bool = False,
) -> RepairReport:
    vault = Path(vault_path).expanduser()
    root = note_scan_root(vault, all_vault=all_vault)
    report = RepairReport()
    if not root.exists():
        return report
    for note in sorted(root.rglob("*.md")):
        try:
            text = note.read_text(encoding="utf-8")
        except OSError:
            continue
        if LEGACY_FOOTER not in text:
            continue
        report.paths.append(str(note))
        if dry_run:
            report.footer_would_migrate += 1
            continue
        note.write_text(text.replace(LEGACY_FOOTER, CURRENT_FOOTER), encoding="utf-8")
        report.footer_migrated += 1
    return report


def row_text(row: sqlite3.Row, column: str) -> str:
    value = row[column]
    return "" if value is None else str(value).strip()


def parse_tags(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(tag).strip() for tag in value if str(tag).strip()]
    text = str(value).strip()
    if not text:
        return []
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        parsed = None
    if isinstance(parsed, list):
        return [str(tag).strip() for tag in parsed if str(tag).strip()]
    return [tag.strip() for tag in text.split(",") if tag.strip()]


def expected_note_hint(row: sqlite3.Row, vault_path: Path) -> str:
    source_type = row_text(row, "source_type") or "web"
    category = row_text(row, "category") or "other"
    title = row_text(row, "title") or "Untitled"
    if source_type in {"youtube", "bilibili", "douyin"}:
        folder = vault_path / "稍后阅读" / "视频"
    elif source_type == "pdf":
        folder = vault_path / "稍后阅读" / "文档"
    else:
        category_map = {
            "tech": "技术",
            "business": "商业",
            "design": "设计",
            "life": "生活",
            "news": "新闻",
            "other": "其他",
        }
        folder = vault_path / "稍后阅读" / category_map.get(category, "其他")
    return str(folder / f"{sanitize_filename(title)}.md")


def sanitize_filename(name: str) -> str:
    cleaned = re.sub(r'[/\\:*?"<>|]', "-", name).strip(". ")
    return cleaned[:100] or "Untitled"


def combine_reports(reports: Iterable[RepairReport]) -> RepairReport:
    combined = RepairReport()
    for report in reports:
        combined.scanned += report.scanned
        combined.already_present += report.already_present
        combined.written += report.written
        combined.would_write += report.would_write
        combined.footer_migrated += report.footer_migrated
        combined.footer_would_migrate += report.footer_would_migrate
        combined.skipped_unprocessed += report.skipped_unprocessed
        combined.paths.extend(report.paths)
    return combined


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Repair missing Obsidian notes from processed my-knowledge-base DB rows."
    )
    parser.add_argument("--db", default=str(pipeline.DEFAULT_DB), help="Path to articles.db")
    parser.add_argument("--vault", default=str(pipeline.DEFAULT_VAULT), help="Obsidian vault path")
    parser.add_argument("--all-missing", action="store_true", help="Backfill every processed DB row missing an Obsidian note")
    parser.add_argument("--article-id", type=int, help="Backfill one processed articles.id if its note is missing")
    parser.add_argument("--limit", type=int, help="Limit rows selected for note backfill")
    parser.add_argument("--dry-run", action="store_true", help="Show what would change without writing files")
    parser.add_argument("--migrate-footer", action="store_true", help="Replace legacy Obsidian footer text in existing notes")
    parser.add_argument("--all-vault", action="store_true", help="Scan the entire vault instead of only 稍后阅读")
    args = parser.parse_args(argv)

    if not args.all_missing and args.article_id is None and not args.migrate_footer:
        parser.error("choose --all-missing, --article-id, or --migrate-footer")

    reports: list[RepairReport] = []
    if args.all_missing or args.article_id is not None:
        reports.append(
            repair_obsidian_notes(
                db_path=Path(args.db).expanduser(),
                vault_path=Path(args.vault).expanduser(),
                all_missing=args.all_missing,
                article_id=args.article_id,
                limit=args.limit,
                dry_run=args.dry_run,
                all_vault=args.all_vault,
            )
        )
    if args.migrate_footer:
        reports.append(
            migrate_legacy_footers(
                vault_path=Path(args.vault).expanduser(),
                dry_run=args.dry_run,
                all_vault=args.all_vault,
            )
        )

    print(json.dumps(dataclasses.asdict(combine_reports(reports)), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
