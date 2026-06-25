#!/usr/bin/env python3
"""
Sync selected read-later articles into gbrain as long-term memory pages.

Default behavior is intentionally conservative: only articles with summaries in
high-value categories are synced, and the sync is one-way into gbrain.
"""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable


WORKSPACE = Path(os.path.expanduser("~/.openclaw/workspace/read-later"))
DEFAULT_DB = WORKSPACE / "articles.db"
DEFAULT_CATEGORIES = ("tech", "business", "design")


@dataclasses.dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str
    stderr: str


@dataclasses.dataclass(frozen=True)
class SyncResult:
    scanned: int = 0
    synced: int = 0
    failed: int = 0
    skipped: int = 0


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def open_db(db_path: Path | str) -> sqlite3.Connection:
    conn = sqlite3.connect(Path(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def ensure_schema(conn: sqlite3.Connection) -> None:
    existing = {row[1] for row in conn.execute("PRAGMA table_info(articles)")}
    additions = {
        "gbrain_slug": "TEXT",
        "gbrain_synced_at": "TEXT",
        "gbrain_sync_status": "TEXT",
        "gbrain_sync_error": "TEXT",
        "long_term_value": "INTEGER DEFAULT 0",
        "gbrain_sync_mode": "TEXT",
    }
    for column, definition in additions.items():
        if column not in existing:
            try:
                conn.execute(f"ALTER TABLE articles ADD COLUMN {column} {definition}")
            except sqlite3.OperationalError as exc:
                if "duplicate column name" not in str(exc).lower():
                    raise
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_articles_gbrain_sync_status "
        "ON articles(gbrain_sync_status)"
    )
    conn.commit()


def select_eligible_articles(
    conn: sqlite3.Connection,
    categories: Iterable[str] = DEFAULT_CATEGORIES,
    limit: int | None = None,
    retry_failed: bool = False,
    only_marked: bool = False,
) -> list[sqlite3.Row]:
    category_values = tuple(c.strip() for c in categories if c.strip())
    if not category_values and not only_marked:
        raise ValueError("At least one category is required")

    status_filter = (
        "(gbrain_sync_status IS NULL OR gbrain_sync_status = '' OR gbrain_sync_status = 'failed')"
        if retry_failed
        else "(gbrain_sync_status IS NULL OR gbrain_sync_status = '')"
    )
    filters = [
        "summary IS NOT NULL",
        "TRIM(summary) != ''",
        status_filter,
    ]
    params: list[object] = []
    if only_marked:
        filters.append("long_term_value = 1")
    else:
        placeholders = ",".join("?" for _ in category_values)
        filters.append(f"category IN ({placeholders})")
        params.extend(category_values)

    sql = f"""
        SELECT *
        FROM articles
        WHERE {' AND '.join(filters)}
        ORDER BY created_at ASC, id ASC
    """
    if limit is not None:
        sql += " LIMIT ?"
        params.append(int(limit))
    return list(conn.execute(sql, params))


def make_slug(article: sqlite3.Row) -> str:
    article_id = int(article["id"])
    source_type = _slug_token(article["source_type"] or "web")
    digest = hashlib.sha256((article["url"] or "").encode("utf-8")).hexdigest()[:8]
    return f"media/articles/{source_type}-{article_id}-{digest}"


def _slug_token(value: str) -> str:
    token = "".join(ch.lower() if ch.isalnum() else "-" for ch in value)
    token = "-".join(part for part in token.split("-") if part)
    return token or "web"


def parse_tags(value: str | None) -> list[str]:
    if not value:
        return []
    text = value.strip()
    if not text:
        return []
    try:
        parsed = json.loads(text)
        if isinstance(parsed, list):
            return [str(item).strip() for item in parsed if str(item).strip()]
    except json.JSONDecodeError:
        pass
    return [part.strip() for part in text.split(",") if part.strip()]


def yaml_quote(value: object) -> str:
    return json.dumps("" if value is None else str(value), ensure_ascii=False)


def build_markdown(article: sqlite3.Row, slug: str) -> str:
    title = article["title"] or article["url"] or f"Read-later article {article['id']}"
    tags = ["read-later", *(parse_tags(article["tags"]))]
    tag_yaml = "[" + ", ".join(yaml_quote(tag) for tag in tags) + "]"
    frontmatter = [
        "---",
        "type: media",
        f"title: {yaml_quote(title)}",
        f"slug: {yaml_quote(slug)}",
        f"source_url: {yaml_quote(article['url'])}",
        f"source_type: {yaml_quote(article['source_type'])}",
        f"category: {yaml_quote(article['category'] or '')}",
        f"author: {yaml_quote(article['author'] or '')}",
        f"published_date: {yaml_quote(article['published_date'] or '')}",
        f"read_later_id: {article['id']}",
        f"tags: {tag_yaml}",
        "---",
        "",
    ]

    sections = [
        f"# {title}",
        "",
        f"Source: [{article['source_type'] or 'web'}]({article['url']})",
        "",
        "## Summary",
        "",
        article["summary"].strip(),
    ]
    original_content = (article["original_content"] or "").strip()
    if original_content:
        sections.extend(["", "## Original Content", "", original_content])
    sections.append("")
    return "\n".join(frontmatter + sections)


def default_command_runner(argv: list[str], input_text: str) -> CommandResult:
    completed = subprocess.run(
        argv,
        input=input_text,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    return CommandResult(completed.returncode, completed.stdout, completed.stderr)


def mark_synced(conn: sqlite3.Connection, article_id: int, slug: str) -> None:
    conn.execute(
        """
        UPDATE articles
        SET gbrain_slug = ?,
            gbrain_synced_at = ?,
            gbrain_sync_status = 'synced',
            gbrain_sync_error = NULL
        WHERE id = ?
        """,
        (slug, _utc_now(), article_id),
    )
    conn.commit()


def mark_failed(conn: sqlite3.Connection, article_id: int, slug: str, error: str) -> None:
    conn.execute(
        """
        UPDATE articles
        SET gbrain_slug = ?,
            gbrain_sync_status = 'failed',
            gbrain_sync_error = ?
        WHERE id = ?
        """,
        (slug, error[:2000], article_id),
    )
    conn.commit()


def sync_articles(
    db_path: Path | str = DEFAULT_DB,
    categories: Iterable[str] = DEFAULT_CATEGORIES,
    limit: int | None = None,
    dry_run: bool = False,
    retry_failed: bool = False,
    only_marked: bool = False,
    gbrain_command: str = "gbrain",
    command_runner: Callable[[list[str], str], CommandResult] = default_command_runner,
    verbose: bool = True,
) -> SyncResult:
    conn = open_db(db_path)
    try:
        ensure_schema(conn)
        articles = select_eligible_articles(conn, categories, limit, retry_failed, only_marked)
        synced = failed = skipped = 0
        for article in articles:
            slug = make_slug(article)
            markdown = build_markdown(article, slug)
            if dry_run:
                if verbose:
                    print(f"[dry-run] would sync article {article['id']} -> {slug}")
                skipped += 1
                continue
            result = command_runner([gbrain_command, "put", slug], markdown)
            if result.returncode == 0:
                mark_synced(conn, int(article["id"]), slug)
                synced += 1
                if verbose:
                    print(f"[synced] article {article['id']} -> {slug}")
            else:
                error = (result.stderr or result.stdout or "gbrain put failed").strip()
                mark_failed(conn, int(article["id"]), slug, error)
                failed += 1
                if verbose:
                    print(f"[failed] article {article['id']} -> {slug}: {error}", file=sys.stderr)
        return SyncResult(scanned=len(articles), synced=synced, failed=failed, skipped=skipped)
    finally:
        conn.close()


def parse_categories(value: str) -> tuple[str, ...]:
    return tuple(part.strip() for part in value.split(",") if part.strip())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Sync read-later articles into gbrain")
    parser.add_argument("--db", default=str(DEFAULT_DB), help="Path to articles.db")
    parser.add_argument(
        "--categories",
        default=",".join(DEFAULT_CATEGORIES),
        help="Comma-separated categories to sync",
    )
    parser.add_argument("--limit", type=int, default=None, help="Maximum articles to sync")
    parser.add_argument("--dry-run", action="store_true", help="Show planned writes without calling gbrain")
    parser.add_argument("--retry-failed", action="store_true", help="Retry articles previously marked failed")
    parser.add_argument("--only-marked", action="store_true", help="Only sync articles marked as long-term value")
    parser.add_argument("--gbrain-command", default="gbrain", help="gbrain executable path")
    args = parser.parse_args(argv)

    result = sync_articles(
        db_path=args.db,
        categories=parse_categories(args.categories),
        limit=args.limit,
        dry_run=args.dry_run,
        retry_failed=args.retry_failed,
        only_marked=args.only_marked,
        gbrain_command=args.gbrain_command,
    )
    print(
        f"scanned={result.scanned} synced={result.synced} "
        f"failed={result.failed} skipped={result.skipped}"
    )
    return 1 if result.failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
