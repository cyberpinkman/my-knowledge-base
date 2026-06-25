#!/usr/bin/env python3
"""
灵感管理脚本 - 创建、搜索、关联
用法:
  python3 idea.py add --title "xxx" --description "xxx" [--motivation "xxx"]
  python3 idea.py list [--status 待探索|进行中|已搁置]
  python3 idea.py analyze --title "xxx"  # 搜索知识库关联内容
"""

import argparse
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime


VAULT = os.environ.get('OBSIDIAN_VAULT_PATH',
                       os.path.expanduser('~/Documents/我的知识库'))

IDEA_DIR = os.path.join(VAULT, '灵感')
STATUS_MAP = {
    '待探索': '待探索',
    '进行中': '进行中',
    '已搁置': '已搁置',
}


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str
    stderr: str


def default_command_runner(argv):
    completed = subprocess.run(
        argv,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        timeout=45,
    )
    return CommandResult(completed.returncode, completed.stdout, completed.stderr)


def sanitize_filename(name):
    name = re.sub(r'[/\\:*?"<>|]', '-', name)
    name = name.strip('. ')
    if len(name) > 80:
        name = name[:80]
    return name or 'Untitled'


def add_idea(title, description='', motivation='', tags=''):
    """Create a new idea note."""
    date_str = datetime.now().strftime('%Y-%m-%d')
    status_dir = os.path.join(IDEA_DIR, '待探索')
    os.makedirs(status_dir, exist_ok=True)

    safe_title = sanitize_filename(title)
    filepath = os.path.join(status_dir, f'{safe_title}.md')

    # Handle duplicate
    counter = 1
    while os.path.exists(filepath):
        filepath = os.path.join(status_dir, f'{safe_title} ({counter}).md')
        counter += 1

    tag_list = [t.strip() for t in tags.split(',') if t.strip()] if tags else ['灵感']
    tags_str = ', '.join(tag_list)

    # Search related knowledge
    related = search_related_knowledge(title + ' ' + description)
    related_section = ''
    if related:
        related_section = '\n'.join([f'- [[{r}]]' for r in related[:5]])
    else:
        related_section = '（暂未发现直接关联的知识库内容）'

    note = f"""---
title: "{title}"
date: {date_str}
status: 待探索
tags: [{tags_str}]
---

# 💡 {title}

## 灵感描述

{description or '（待补充）'}

## 为什么觉得有意思

{motivation or '（待补充）'}

## 关联的知识库内容

{related_section}

## 下一步

- [ ] （待规划）

---
*记录于 {date_str}*
"""

    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(note)

    # Return result
    result = {
        'filepath': filepath,
        'title': title,
        'status': '待探索',
        'related_knowledge': related[:5],
    }
    return result


def parse_gbrain_query_results(output):
    """Extract unique gbrain slugs from `gbrain query` text output."""
    slugs = []
    seen = set()
    for line in output.splitlines():
        match = re.match(r'^\[[^\]]+\]\s+(\S+)\s+--\s+', line.strip())
        if not match:
            continue
        slug = match.group(1)
        if slug not in seen:
            slugs.append(slug)
            seen.add(slug)
    return slugs


def search_gbrain_related(query, command_runner=default_command_runner,
                          gbrain_command='gbrain', limit=10):
    """Search gbrain first; return slugs, or [] when unavailable/no match."""
    if not query.strip():
        return []
    try:
        result = command_runner([gbrain_command, 'query', query])
    except Exception:
        return []
    if result.returncode != 0:
        return []
    return parse_gbrain_query_results(result.stdout)[:limit]


def search_vault_related_knowledge(query):
    """Search vault for related knowledge articles."""
    if not query.strip():
        return []

    related = []
    read_later_dir = os.path.join(VAULT, '稍后阅读')

    # Extract keywords from query (simple approach: split and filter short words)
    keywords = [w for w in re.split(r'[\s,，、；;]+', query) if len(w) >= 2]

    if not os.path.exists(read_later_dir):
        return []

    # Walk through all knowledge articles
    for root, dirs, files in os.walk(read_later_dir):
        for fname in files:
            if not fname.endswith('.md'):
                continue

            fpath = os.path.join(root, fname)
            try:
                with open(fpath, 'r', encoding='utf-8') as f:
                    content = f.read()
            except Exception:
                continue

            # Score by keyword matches
            score = 0
            for kw in keywords:
                if kw.lower() in content.lower():
                    score += 1

            if score > 0:
                # Extract title from filename
                title = fname.replace('.md', '')
                related.append((title, score, fpath))

    # Sort by relevance
    related.sort(key=lambda x: x[1], reverse=True)
    return [r[0] for r in related[:10]]


def search_related_knowledge(query, command_runner=default_command_runner,
                             prefer_gbrain=True):
    """Search gbrain first, then fall back to local vault keyword matching."""
    if prefer_gbrain:
        gbrain_results = search_gbrain_related(query, command_runner=command_runner)
        if gbrain_results:
            return gbrain_results
    return search_vault_related_knowledge(query)


def looks_like_gbrain_slug(value):
    return '/' in value and not value.endswith('.md')


def extract_summary_section(markdown):
    """Extract either Chinese or English summary section from markdown."""
    summary = ''
    in_summary = False
    for line in markdown.split('\n'):
        stripped = line.strip()
        if re.match(r'^##\s+(摘要|Summary)\s*$', stripped, re.IGNORECASE):
            in_summary = True
            continue
        if in_summary and stripped.startswith('## '):
            break
        if in_summary and stripped:
            summary += stripped + ' '
    return summary.strip()


def get_gbrain_page_summary(slug, command_runner=default_command_runner,
                            gbrain_command='gbrain'):
    try:
        result = command_runner([gbrain_command, 'get', slug])
    except Exception:
        return ''
    if result.returncode != 0:
        return ''
    return extract_summary_section(result.stdout)[:200]


def build_related_details(related, command_runner=default_command_runner):
    """Build summary details for related gbrain slugs or vault note titles."""
    related_details = []
    read_later_dir = os.path.join(VAULT, '稍后阅读')

    for item in related[:5]:
        if looks_like_gbrain_slug(item):
            summary = get_gbrain_page_summary(item, command_runner=command_runner)
            related_details.append({
                'title': item,
                'summary': summary,
                'source': 'gbrain',
            })
            continue

        if not os.path.exists(read_later_dir):
            continue
        for root, dirs, files in os.walk(read_later_dir):
            for fname in files:
                if fname.replace('.md', '') != item:
                    continue
                fpath = os.path.join(root, fname)
                with open(fpath, 'r', encoding='utf-8') as f:
                    content = f.read(3000)
                related_details.append({
                    'title': item,
                    'summary': extract_summary_section(content)[:200],
                    'source': 'vault',
                })
                break

    return related_details


def list_ideas(status_filter=None):
    """List all ideas, optionally filtered by status."""
    ideas = []

    for status_dir_name in STATUS_MAP:
        if status_filter and status_filter != status_dir_name:
            continue

        status_dir = os.path.join(IDEA_DIR, status_dir_name)
        if not os.path.exists(status_dir):
            continue

        for fname in os.listdir(status_dir):
            if not fname.endswith('.md'):
                continue

            fpath = os.path.join(status_dir, fname)
            try:
                with open(fpath, 'r', encoding='utf-8') as f:
                    content = f.read(2000)
            except Exception:
                continue

            title = fname.replace('.md', '')
            # Extract tags from frontmatter
            tags_match = re.search(r'tags:\s*\[(.*?)\]', content)
            tags = tags_match.group(1) if tags_match else ''
            # Extract date
            date_match = re.search(r'date:\s*(\S+)', content)
            date = date_match.group(1) if date_match else ''

            # Get first non-frontmatter line as preview
            lines = content.split('\n')
            preview = ''
            in_body = False
            for line in lines:
                if in_body and line.strip() and not line.startswith('#') and not line.startswith('---'):
                    preview = line.strip()[:100]
                    break
                if line.strip() == '---' and in_body:
                    in_body = False
                elif line.strip() == '---':
                    in_body = True

            ideas.append({
                'title': title,
                'status': status_dir_name,
                'date': date,
                'tags': tags,
                'preview': preview,
                'filepath': fpath,
            })

    return ideas


def analyze_idea(title, command_runner=default_command_runner):
    """Deep analysis: search knowledge base and generate feasibility assessment."""
    # First find the idea file
    idea_file = None
    idea_content = ''

    for status_dir_name in STATUS_MAP:
        status_dir = os.path.join(IDEA_DIR, status_dir_name)
        if not os.path.exists(status_dir):
            continue
        safe_title = sanitize_filename(title)
        for fname in os.listdir(status_dir):
            if fname.replace('.md', '') == safe_title or title in fname:
                idea_file = os.path.join(status_dir, fname)
                break
        if idea_file:
            break

    if not idea_file:
        # Treat as ad-hoc query
        related = search_related_knowledge(title, command_runner=command_runner)
        return {
            'title': title,
            'found': False,
            'related_knowledge': related,
            'related_details': build_related_details(related, command_runner=command_runner),
            'message': '灵感笔记未找到，以下是知识库中的相关内容',
        }

    with open(idea_file, 'r', encoding='utf-8') as f:
        idea_content = f.read()

    related = search_related_knowledge(title, command_runner=command_runner)

    related_details = build_related_details(related, command_runner=command_runner)

    return {
        'title': title,
        'found': True,
        'filepath': idea_file,
        'related_knowledge': related,
        'related_details': related_details,
    }


def main():
    parser = argparse.ArgumentParser(description='Idea management')
    sub = parser.add_subparsers(dest='command')

    # add
    p_add = sub.add_parser('add')
    p_add.add_argument('--title', required=True)
    p_add.add_argument('--description', default='')
    p_add.add_argument('--motivation', default='')
    p_add.add_argument('--tags', default='')

    # list
    p_list = sub.add_parser('list')
    p_list.add_argument('--status', default=None)

    # analyze
    p_analyze = sub.add_parser('analyze')
    p_analyze.add_argument('--title', required=True)

    args = parser.parse_args()

    if args.command == 'add':
        result = add_idea(args.title, args.description, args.motivation, args.tags)
        print(json.dumps(result, ensure_ascii=False, indent=2))

    elif args.command == 'list':
        ideas = list_ideas(args.status)
        print(json.dumps(ideas, ensure_ascii=False, indent=2))

    elif args.command == 'analyze':
        result = analyze_idea(args.title)
        print(json.dumps(result, ensure_ascii=False, indent=2))

    else:
        parser.print_help()


if __name__ == '__main__':
    main()
