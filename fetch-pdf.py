#!/usr/bin/env python3
# 注意: pypdf 必须装在运行这个脚本的 python 环境的 site-packages 里。
# Hermes 环境默认 venv: ~/.hermes/hermes-agent/venv/bin/python3
# 如果 PATH 里 python3 是系统 Python 没有 pypdf,直接用绝对路径调用。
"""
PDF 内容抓取脚本
用法:
  python3 fetch-pdf.py <url>           # 下载并解析
  python3 fetch-pdf.py --local <path>  # 直接解析本地文件

处理链:
1. 下载/读取 PDF 文件
2. pypdf 提取每页文本
3. 提取 PDF metadata 作为 author/title 兜底
4. 若 metadata 没 title,从 content 首段启发式找标题候选
5. 若文本极少 → 标注 source='scan',由 AI 决定是否 OCR 兜底

输出 JSON: {title, author, page_count, content, source, file_path, source_url,
           file_size, avg_chars_per_page, total_chars, processing_hints, error}
"""
import argparse
import json
import os
import re
import statistics
import sys
import tempfile
import urllib.request
import urllib.error


CHUNK_SIZE = 64 * 1024  # 64KB
DOWNLOAD_TIMEOUT = 60
MAX_PDF_BYTES = 100 * 1024 * 1024  # 100MB 安全上限
SCAN_MEDIAN_THRESHOLD = 50  # 中位数 < 50 字符/页 → 判定为扫描版
SCAN_TOTAL_THRESHOLD = 200  # 且全文 < 200 字符 → 几乎纯图
MAX_TITLE_HEURISTIC_CHARS = 600  # 从 content 前 600 字符里找标题候选


def download_pdf(url, dest):
    """Download PDF from URL to dest. Returns (success, size_or_error_msg)."""
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=DOWNLOAD_TIMEOUT) as resp:
            content_length = resp.headers.get('Content-Length')
            if content_length and int(content_length) > MAX_PDF_BYTES:
                return False, f"PDF too large: {content_length} bytes (max {MAX_PDF_BYTES})"

            total = 0
            with open(dest, 'wb') as f:
                while True:
                    chunk = resp.read(CHUNK_SIZE)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > MAX_PDF_BYTES:
                        f.close()
                        os.remove(dest)
                        return False, f"PDF exceeded {MAX_PDF_BYTES} bytes during download"
                    f.write(chunk)
            return True, total
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError) as e:
        return False, str(e)


def heuristic_title_from_content(content):
    """PDF metadata 缺 title 时,从正文首段启发式找标题。

    策略:
    1. 跳过 boilerplate (arXiv permission notice 等)
    2. 取第一个明显的标题行:
       - 不以小写字母开头 (即不是句子延续)
       - 长度 5-120 字符
       - 不是纯数字 / 标点
    3. 如果前 600 字符里没找到,放弃,返回 None
    """
    if not content:
        return None

    # 取前 600 字符作为搜索范围
    head = content[:MAX_TITLE_HEURISTIC_CHARS]

    # 按行扫,跳过空行和 boilerplate
    skip_patterns = [
        r'^Provided proper attribution',  # arXiv permission
        r'^arXiv:',                        # arXiv stamp
        r'^\d{1,3}\s+Conference on',       # conference stamp
        r'^Copyright',                     # copyright
        r'^All rights reserved',
        r'^Preprint\.?$',
        r'^Under review',                  # submission boilerplate
        # IEEE/Springer/Elsevier 模板占位符
        r'^Noname manuscript',             # Springer: "Noname manuscript No."
        r'^\(will be inserted',            # Springer: "(will be inserted by the editor)"
        r'^Manuscript\s+(ID|draft)',       # IEEE Access: "Manuscript ID ..." / "Manuscript draft"
        r'^This\s+is\s+a\s+preprint',      # arxiv/elsevier preprint banner
        r'^Paper\s+\d+',                   # conference: "Paper 1234-5678"
    ]
    skip_re = [re.compile(p, re.IGNORECASE) for p in skip_patterns]

    for raw_line in head.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if any(p.match(line) for p in skip_re):
            continue
        # 长度过滤
        if not (5 <= len(line) <= 120):
            continue
        # 排除纯数字 / 标点
        if not re.search(r'[A-Za-z一-鿿]', line):
            continue
        # 排除明显的句子延续 (以小写字母开头)
        if line[0].islower():
            continue
        # 排除含句号结尾的长句 (更像段落而不是标题)
        if line.endswith('.') and len(line.split()) > 8:
            continue
        return line

    return None


def extract_text(pdf_path):
    """Extract text + metadata from PDF using pypdf. Returns dict."""
    try:
        from pypdf import PdfReader
    except ImportError:
        return {'error': 'pypdf not installed. Run: pip install pypdf'}

    try:
        reader = PdfReader(pdf_path)
    except Exception as e:
        return {'error': f'pypdf failed to read PDF: {e}'}

    meta = reader.metadata or {}
    title = (meta.get('/Title') or '').strip()
    author = (meta.get('/Author') or '').strip()

    pages_text = []
    page_errors = []
    for i, page in enumerate(reader.pages):
        try:
            t = page.extract_text() or ''
        except Exception as e:
            # 失败时不计入字符 (避免影响 scan 判定), 错误单独收集
            t = ''
            page_errors.append({'page': i + 1, 'error': str(e)})
        pages_text.append(t)

    content = '\n\n'.join(pages_text)
    total_chars = sum(len(p) for p in pages_text)
    page_count = len(reader.pages)

    # Per-page char counts (for scan detection)
    per_page_chars = [len(p) for p in pages_text]

    # Median 比 avg 更鲁棒: 一页有页眉/页码不会拉低中位数
    if per_page_chars:
        median_chars = statistics.median(per_page_chars)
        avg_chars = total_chars / page_count
    else:
        median_chars = 0
        avg_chars = 0

    # Scan detection: 中位数极少 + 全文也少
    if median_chars < SCAN_MEDIAN_THRESHOLD and total_chars < SCAN_TOTAL_THRESHOLD:
        source = 'scan'
    else:
        source = 'text'

    # Title 启发式: metadata 没给就从 content 找
    if not title and content:
        title = heuristic_title_from_content(content) or ''

    return {
        'title': title,
        'author': author,
        'page_count': page_count,
        'content': content,
        'source': source,
        'avg_chars_per_page': round(avg_chars, 1),
        'median_chars_per_page': round(median_chars, 1),
        'total_chars': total_chars,
        'page_errors': page_errors,  # list of {page, error}; empty if all extracted ok
    }


def derive_title_from_url(url):
    """Fallback: derive title from URL filename."""
    # e.g. https://arxiv.org/pdf/2601.12345.pdf → 2601.12345
    m = re.search(r'/([^/?#]+)\.pdf', url, re.IGNORECASE)
    if m:
        return m.group(1)
    # arxiv abstract URL: https://arxiv.org/abs/2601.12345
    m = re.search(r'arxiv\.org/abs/([0-9.]+)', url)
    if m:
        return f'arxiv-{m.group(1)}'
    return 'untitled-pdf'


def build_processing_hints(result, url=None):
    """给 AI 摘要阶段提供操作建议。

    不是"必须照做",只是 hint。
    """
    hints = {}

    # 是否学术论文
    if url and 'arxiv.org' in url.lower():
        hints['is_academic'] = True
        hints['suggested_summary_length'] = '500-600'
    elif url and re.search(r'(researchgate|springer|ieee|acm)\.', url.lower()):
        hints['is_academic'] = True
        hints['suggested_summary_length'] = '500'
    else:
        hints['is_academic'] = False
        # 短文档用短摘要
        page_count = result.get('page_count', 0)
        if page_count <= 5:
            hints['suggested_summary_length'] = '200-400'
        elif page_count <= 20:
            hints['suggested_summary_length'] = '400-500'
        else:
            hints['suggested_summary_length'] = '500-600'

    # scan 版需要 OCR 兜底提示
    if result.get('source') == 'scan':
        hints['needs_ocr'] = True
        hints['fallback_tool'] = 'pdf skill (Hermes)'  # TODO: 真有复杂 PDF 时调研

    # 是否需要重新提取 title
    if not result.get('title'):
        hints['title_needs_llm_extraction'] = True

    return hints


def fetch_url(url):
    """Download PDF from URL, extract text. Returns result dict."""
    title_hint = derive_title_from_url(url)
    with tempfile.NamedTemporaryFile(suffix='.pdf', delete=False) as tmp:
        tmp_path = tmp.name

    try:
        ok, info = download_pdf(url, tmp_path)
        if not ok:
            return {
                'title': title_hint,
                'author': '',
                'page_count': 0,
                'content': '',
                'source': 'download_failed',
                'source_url': url,
                'processing_hints': build_processing_hints({'page_count': 0, 'source': 'download_failed'}, url),
                'error': info,
            }
        size = info
        result = extract_text(tmp_path)
        result['source_url'] = url
        result['file_size'] = size
        result['file_path'] = tmp_path
        if not result.get('title'):
            result['title'] = title_hint
        result['processing_hints'] = build_processing_hints(result, url)
        return result
    except Exception as e:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        return {
            'title': title_hint,
            'author': '',
            'page_count': 0,
            'content': '',
            'source': 'error',
            'source_url': url,
            'processing_hints': build_processing_hints({'page_count': 0, 'source': 'error'}, url),
            'error': str(e),
        }


def fetch_local(path):
    """Parse local PDF file. Returns result dict."""
    path = os.path.abspath(os.path.expanduser(path))
    if not os.path.exists(path):
        return {'error': f'File not found: {path}'}

    if not path.lower().endswith('.pdf'):
        return {'error': f'Not a .pdf file: {path}'}

    size = os.path.getsize(path)
    result = extract_text(path)
    result['file_path'] = path
    result['file_size'] = size

    if not result.get('title'):
        result['title'] = os.path.splitext(os.path.basename(path))[0]

    result['processing_hints'] = build_processing_hints(result)
    return result


def main():
    parser = argparse.ArgumentParser(description='Fetch & extract PDF content')
    parser.add_argument('url', nargs='?', help='PDF URL to download')
    parser.add_argument('--local', help='Local PDF file path')
    args = parser.parse_args()

    if args.local:
        result = fetch_local(args.local)
    elif args.url:
        result = fetch_url(args.url)
    else:
        parser.error('Provide either a URL or --local <path>')

    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()