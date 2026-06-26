#!/usr/bin/env python3
"""
将 my-knowledge-base 条目写入 Obsidian Vault
用法: python3 write-to-obsidian.py --title "标题" --url "链接" --source_type "youtube" \
      --author "作者" --category "tech" --tags "AI,Python" --summary "摘要" --content "正文"

输出: 写入的文件路径
"""

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime


VAULT = os.environ.get('OBSIDIAN_VAULT_PATH', 
                       os.path.expanduser('~/Documents/我的知识库'))
PROJECT_NAME = 'my-knowledge-base'

CATEGORY_MAP = {
    'tech': '技术',
    'business': '商业',
    'design': '设计',
    'life': '生活',
    'news': '新闻',
    'other': '其他',
}

# 视频类型归入视频文件夹
VIDEO_TYPES = {'youtube', 'bilibili', 'douyin'}

# 文档类型（PDF）归入文档文件夹
DOC_TYPES = {'pdf'}


def sanitize_filename(name):
    """清理文件名中的非法字符"""
    # Obsidian 允许大部分字符，但避免 / \ : * ? " < > |
    name = re.sub(r'[/\\:*?"<>|]', '-', name)
    name = name.strip('. ')
    if len(name) > 100:
        name = name[:100]
    return name or 'Untitled'


def note_has_source(filepath, url):
    """Return True when an existing note already represents this source URL."""
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            head = f.read(1200)
    except OSError:
        return False
    return f'source: "{url}"' in head or f"source: '{url}'" in head


def choose_note_path(folder, safe_title, url, date_str):
    """Choose an idempotent Obsidian note path for a source URL."""
    base = os.path.join(folder, f"{safe_title}.md")
    dated = os.path.join(folder, f"{safe_title} ({date_str}).md")

    for candidate in (base, dated):
        if os.path.exists(candidate) and note_has_source(candidate, url):
            return candidate

    if not os.path.exists(base):
        return base

    if not os.path.exists(dated):
        return dated

    counter = 2
    while True:
        candidate = os.path.join(folder, f"{safe_title} ({date_str}-{counter}).md")
        if not os.path.exists(candidate):
            return candidate
        counter += 1


def yaml_quote(value):
    """Return a YAML-safe JSON scalar."""
    return json.dumps('' if value is None else str(value), ensure_ascii=False)


def parse_tags(tags):
    if isinstance(tags, (list, tuple)):
        return [str(t).strip() for t in tags if str(t).strip()]
    text = str(tags or '').strip()
    if not text:
        return []
    try:
        parsed = json.loads(text)
        if isinstance(parsed, list):
            return [str(t).strip() for t in parsed if str(t).strip()]
    except json.JSONDecodeError:
        pass
    return [t.strip() for t in text.split(',') if t.strip()]


def format_tags_yaml(tags):
    return '[' + ', '.join(yaml_quote(tag) for tag in tags) + ']'


def write_note(title, url, source_type, author='', category='',
               tags='', summary='', content='', key_points=''):
    """Write a note to Obsidian vault."""
    
    # Determine folder
    if source_type in VIDEO_TYPES:
        folder = os.path.join(VAULT, '稍后阅读', '视频')
    elif source_type in DOC_TYPES:
        folder = os.path.join(VAULT, '稍后阅读', '文档')
    else:
        cat_folder = CATEGORY_MAP.get(category, '其他')
        folder = os.path.join(VAULT, '稍后阅读', cat_folder)
    
    os.makedirs(folder, exist_ok=True)
    
    # Build filename
    date_str = datetime.now().strftime('%Y-%m-%d')
    safe_title = sanitize_filename(title)
    filepath = choose_note_path(folder, safe_title, url, date_str)
    
    tag_list = parse_tags(tags)
    tags_yaml = format_tags_yaml(tag_list)
    
    # Build note content
    note = f"""---
title: {yaml_quote(title)}
source: {yaml_quote(url)}
source_type: {yaml_quote(source_type)}
author: {yaml_quote(author)}
category: {yaml_quote(category)}
tags: {tags_yaml}
date: {date_str}
status: unread
---

# {title}

"""

    if author:
        note += f"**作者**: {author}  \n"
    note += f"**来源**: [{source_type}]({url})  \n"
    note += f"**日期**: {date_str}  \n"
    
    if summary:
        note += f"\n## 摘要\n\n{summary}\n"
    
    if key_points:
        note += f"\n## 要点\n\n{key_points}\n"
    
    if content:
        note += f"\n## 正文\n\n{content}\n"
    
    note += f"\n---\n*由 {PROJECT_NAME} 自动收录于 {date_str}*\n"
    
    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(note)
    
    return filepath


def main():
    parser = argparse.ArgumentParser(description='Write my-knowledge-base article to Obsidian')
    parser.add_argument('--title', required=True, help='Article title')
    parser.add_argument('--url', required=True, help='Original URL')
    parser.add_argument('--source-type', default='web', help='Source type')
    parser.add_argument('--author', default='', help='Author')
    parser.add_argument('--category', default='other', help='Category')
    parser.add_argument('--tags', default='', help='Comma-separated tags')
    parser.add_argument('--summary', default='', help='Summary')
    parser.add_argument('--content', default='', help='Full content')
    parser.add_argument('--key-points', default='', help='Key points')
    
    args = parser.parse_args()
    
    filepath = write_note(
        title=args.title,
        url=args.url,
        source_type=args.source_type,
        author=args.author,
        category=args.category,
        tags=args.tags,
        summary=args.summary,
        content=args.content,
        key_points=args.key_points,
    )
    
    print(filepath)


if __name__ == '__main__':
    main()
