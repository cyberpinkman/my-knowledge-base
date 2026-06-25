#!/usr/bin/env python3
"""Mark read-later articles for long-term gbrain sync."""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

import sync_to_gbrain


def mark_value(conn: sqlite3.Connection, article_ids: list[int]) -> int:
    sync_to_gbrain.ensure_schema(conn)
    changed = 0
    for article_id in article_ids:
        cursor = conn.execute(
            """
            UPDATE articles
            SET long_term_value = 1,
                gbrain_sync_mode = 'manual'
            WHERE id = ?
            """,
            (article_id,),
        )
        changed += cursor.rowcount
    conn.commit()
    return changed


def clear_value(conn: sqlite3.Connection, article_ids: list[int]) -> int:
    sync_to_gbrain.ensure_schema(conn)
    changed = 0
    for article_id in article_ids:
        cursor = conn.execute(
            """
            UPDATE articles
            SET long_term_value = 0,
                gbrain_sync_mode = NULL
            WHERE id = ?
            """,
            (article_id,),
        )
        changed += cursor.rowcount
    conn.commit()
    return changed


def list_articles(
    conn: sqlite3.Connection,
    marked: bool | None = None,
    limit: int = 20,
) -> list[sqlite3.Row]:
    sync_to_gbrain.ensure_schema(conn)
    filters = []
    if marked is True:
        filters.append("long_term_value = 1")
    elif marked is False:
        filters.append("(long_term_value IS NULL OR long_term_value = 0)")
    where = f"WHERE {' AND '.join(filters)}" if filters else ""
    return list(
        conn.execute(
            f"""
            SELECT id, title, url, category, summary, long_term_value,
                   gbrain_sync_status, gbrain_slug
            FROM articles
            {where}
            ORDER BY created_at DESC, id DESC
            LIMIT ?
            """,
            (limit,),
        )
    )


def parse_ids(values: list[str]) -> list[int]:
    ids = []
    for value in values:
        for part in value.split(","):
            part = part.strip()
            if part:
                ids.append(int(part))
    if not ids:
        raise ValueError("At least one article id is required")
    return ids


def open_db(db_path: str | Path) -> sqlite3.Connection:
    return sync_to_gbrain.open_db(db_path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Mark read-later articles for long-term value")
    parser.add_argument("--db", default=str(sync_to_gbrain.DEFAULT_DB), help="Path to articles.db")
    sub = parser.add_subparsers(dest="command", required=True)

    p_value = sub.add_parser("value", help="Mark articles as long-term value")
    p_value.add_argument("ids", nargs="+", help="Article ids, comma-separated or space-separated")

    p_clear = sub.add_parser("clear", help="Clear long-term value mark")
    p_clear.add_argument("ids", nargs="+", help="Article ids, comma-separated or space-separated")

    p_list = sub.add_parser("list", help="List articles and mark status")
    p_list.add_argument("--marked", action="store_true", help="Only show marked articles")
    p_list.add_argument("--unmarked", action="store_true", help="Only show unmarked articles")
    p_list.add_argument("--limit", type=int, default=20)

    args = parser.parse_args(argv)
    conn = open_db(args.db)
    try:
        if args.command == "value":
            changed = mark_value(conn, parse_ids(args.ids))
            print(json.dumps({"changed": changed}, ensure_ascii=False))
        elif args.command == "clear":
            changed = clear_value(conn, parse_ids(args.ids))
            print(json.dumps({"changed": changed}, ensure_ascii=False))
        elif args.command == "list":
            marked = True if args.marked else False if args.unmarked else None
            rows = list_articles(conn, marked=marked, limit=args.limit)
            print(json.dumps([dict(row) for row in rows], ensure_ascii=False, indent=2))
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
