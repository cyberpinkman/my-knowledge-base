#!/usr/bin/env python3
"""End-to-end read-later processing pipeline.

The shell entrypoint owns input normalization and de-duplication. This module
owns the article lifecycle after a row exists in SQLite:
fetch content, derive metadata, update SQLite, write Obsidian, and optionally
sync eligible content to gbrain.
"""

from __future__ import annotations

import argparse
import dataclasses
import importlib.util
import json
import os
import re
import shlex
import sqlite3
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable, Mapping


WORKSPACE = Path(os.path.expanduser("~/.openclaw/workspace/read-later"))
DEFAULT_DB = WORKSPACE / "articles.db"
DEFAULT_VAULT = Path(os.environ.get("OBSIDIAN_VAULT_PATH", "~/Documents/我的知识库")).expanduser()
MINIMAX_DEFAULT_BASE_URL = "https://api.minimaxi.com/v1"
MINIMAX_DEFAULT_MODEL = "MiniMax-M3"
MINIMAX_DEFAULT_API_KEY_ENV = "MINIMAX_API_KEY"
MINIMAX_STRUCTURED_TOOL_NAME = "emit_structured_response"


@dataclasses.dataclass(frozen=True)
class FetchResult:
    title: str = ""
    author: str = ""
    content: str = ""
    source: str = ""
    url: str = ""
    published_date: str = ""
    raw_metadata: Mapping[str, object] | None = None
    error: str = ""


@dataclasses.dataclass(frozen=True)
class AnalysisResult:
    summary: str
    category: str
    tags: list[str]
    key_points: str = ""
    note_content: str = ""
    source: str = "deterministic"


@dataclasses.dataclass(frozen=True)
class ProcessResult:
    ok: bool
    article_id: int
    title: str = ""
    note_path: str = ""
    gbrain_synced: bool = False
    skipped: bool = False
    error: str = ""


def open_db(db_path: Path | str = DEFAULT_DB) -> sqlite3.Connection:
    conn = sqlite3.connect(Path(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def process_article(
    article_id: int,
    db_path: Path | str = DEFAULT_DB,
    vault_path: Path | str = DEFAULT_VAULT,
    fetcher: Callable[[sqlite3.Row], FetchResult] | None = None,
    analyzer: Callable[[str, str, str, Mapping[str, object]], AnalysisResult] | None = None,
    sync_gbrain: bool = False,
    force: bool = False,
) -> ProcessResult:
    conn = open_db(db_path)
    try:
        article = conn.execute("SELECT * FROM articles WHERE id = ?", (article_id,)).fetchone()
        if not article:
            return ProcessResult(False, article_id, error=f"article not found: {article_id}")

        if not force and _has_processed_content(article):
            return ProcessResult(
                True,
                article_id,
                title=article["title"] or article["url"],
                skipped=True,
            )

        fetch = (fetcher or fetch_article_content)(article)
        if fetch.error or not fetch.content.strip():
            error = fetch.error or "empty fetched content"
            record_fetch_failure(conn, article, error)
            return ProcessResult(False, article_id, error=error)

        fetch = dataclasses.replace(
            fetch,
            author=fetch.author or article["author"] or "",
            published_date=fetch.published_date or article["published_date"] or "",
        )
        title = fetch.title.strip() or article["title"] or derive_title_from_url(article["url"])
        analysis = analyze_content(
            title,
            fetch.content,
            article["source_type"],
            fetch.raw_metadata or {},
            analyzer=analyzer,
        )

        update_article(conn, article_id, title, fetch, analysis)
        note_content = analysis.note_content or fetch.content
        note_path = write_obsidian_note(
            title=title,
            url=article["url"],
            source_type=article["source_type"],
            author=fetch.author,
            category=analysis.category,
            tags=analysis.tags,
            summary=analysis.summary,
            content=note_content,
            key_points=analysis.key_points,
            vault_path=Path(vault_path),
        )

        did_sync = False
        if sync_gbrain:
            did_sync = sync_article_to_gbrain(db_path)

        return ProcessResult(
            True,
            article_id,
            title=title,
            note_path=str(note_path),
            gbrain_synced=did_sync,
        )
    finally:
        conn.close()


def _has_processed_content(article: sqlite3.Row) -> bool:
    return bool((article["summary"] or "").strip() and (article["title"] or "").strip())


def fetch_article_content(article: sqlite3.Row) -> FetchResult:
    source_type = (article["source_type"] or "web").lower()
    url = article["url"]
    if source_type == "douyin":
        result = fetch_video_content(url)
        if result.content.strip():
            return result
        return fetch_douyin_page(url)
    if source_type in {"youtube", "bilibili"}:
        return fetch_video_content(url)
    if source_type == "pdf":
        return fetch_pdf_content(url)
    return fetch_web_content(url)


def fetch_video_content(url: str) -> FetchResult:
    with tempfile.TemporaryDirectory(prefix="read-later-video-") as outdir:
        result = run_json_command([sys.executable, str(WORKSPACE / "fetch-video.py"), url, outdir], timeout=240)
    if isinstance(result, FetchResult):
        return result
    return fetch_result_from_video_payload(url, result)


def fetch_douyin_page(url: str) -> FetchResult:
    result = run_json_command(["node", str(WORKSPACE / "fetch-douyin.js"), url], timeout=90)
    if isinstance(result, FetchResult):
        return result
    return fetch_result_from_douyin_payload(url, result)


def fetch_pdf_content(url: str) -> FetchResult:
    if url.startswith("file://"):
        argv = [sys.executable, str(WORKSPACE / "fetch-pdf.py"), "--local", url.removeprefix("file://")]
    else:
        argv = [sys.executable, str(WORKSPACE / "fetch-pdf.py"), url]
    result = run_json_command(argv, timeout=180)
    if isinstance(result, FetchResult):
        return result
    content = _clean(result.get("content", ""))
    error = result.get("error", "") if not content else ""
    return FetchResult(
        title=_clean(result.get("title", "")),
        author=_clean(result.get("author", "")),
        content=content,
        source=_clean(result.get("source", "pdf")),
        url=url,
        raw_metadata=result,
        error=error,
    )


def fetch_web_content(url: str) -> FetchResult:
    screenshot_path = tempfile.NamedTemporaryFile(prefix="read-later-page-", suffix=".png", delete=False)
    screenshot_path.close()
    completed = subprocess.run(
        ["node", str(WORKSPACE / "fetch-screenshot.js"), url, screenshot_path.name],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=90,
        check=False,
    )
    text = completed.stderr or completed.stdout
    if completed.returncode != 0:
        return FetchResult(url=url, source="web", error=(text or "web fetch failed").strip())
    title = extract_between(text, "---TITLE---", "---END TITLE---")
    content = extract_between(text, "---CONTENT---", "---END CONTENT---")
    if not content:
        return FetchResult(title=title, url=url, source="web", error="empty web content")
    return FetchResult(
        title=title,
        content=content,
        source="web",
        url=url,
        raw_metadata={"screenshot": screenshot_path.name},
    )


def run_json_command(argv: list[str], timeout: int) -> Mapping[str, object] | FetchResult:
    completed = subprocess.run(
        argv,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        check=False,
    )
    if completed.returncode != 0:
        error = (completed.stderr or completed.stdout or f"command failed: {' '.join(argv)}").strip()
        return FetchResult(error=error)
    try:
        return json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        return FetchResult(error=f"invalid JSON from {' '.join(argv)}: {exc}")


def fetch_result_from_video_payload(url: str, payload: Mapping[str, object]) -> FetchResult:
    source = str(payload.get("source") or "video")
    content = _clean(
        payload.get("transcript")
        or payload.get("chapter_summary")
        or payload.get("description")
        or ""
    )
    if payload.get("duration"):
        content = f"视频时长：{payload['duration']}\n\n{content}".strip()
    error = _clean(payload.get("error", "")) if not content else ""
    return FetchResult(
        title=_clean(payload.get("title", "")),
        author=_clean(payload.get("author", "")),
        content=content,
        source=source,
        url=url,
        published_date=_clean(payload.get("published_date", "")),
        raw_metadata=payload,
        error=error,
    )


def fetch_result_from_douyin_payload(url: str, payload: Mapping[str, object]) -> FetchResult:
    title = _clean(payload.get("title", ""))
    chapter_summary = _clean(payload.get("chapter_summary", ""))
    description = _clean(payload.get("description", ""))
    duration = _clean(payload.get("duration", ""))
    parts = []
    if duration:
        parts.append(f"视频时长：{duration}")
    if chapter_summary:
        parts.append(f"章节要点：{chapter_summary}")
    elif description and description != title:
        parts.append(description)
    elif title:
        parts.append(title)
    content = "\n\n".join(parts)
    return FetchResult(
        title=title,
        author=_clean(payload.get("author", "")),
        content=content,
        source="douyin_page",
        url=url,
        published_date=_clean(payload.get("published_date", "")),
        raw_metadata={k: json.dumps(v, ensure_ascii=False) if isinstance(v, (list, dict)) else v for k, v in payload.items()},
        error="" if content else "empty douyin page content",
    )


def analyze_content(
    title: str,
    content: str,
    source_type: str,
    raw_metadata: Mapping[str, object],
    analyzer: Callable[[str, str, str, Mapping[str, object]], AnalysisResult] | None = None,
) -> AnalysisResult:
    text = _clean(content)
    llm_result = None
    if analyzer:
        llm_result = analyzer(title, text, source_type, raw_metadata)
    else:
        llm_result = analyze_content_with_configured_llm(title, text, source_type, raw_metadata)
    if llm_result:
        return normalize_analysis_result(llm_result, title, text, source_type, raw_metadata)
    return deterministic_analysis(title, text, source_type, raw_metadata)


def deterministic_analysis(
    title: str,
    content: str,
    source_type: str,
    raw_metadata: Mapping[str, object],
) -> AnalysisResult:
    summary = make_summary(content)
    category = categorize(title, content, source_type)
    tags = extract_tags(title, content, raw_metadata)
    return AnalysisResult(
        summary=summary,
        category=category,
        tags=tags,
        note_content=content,
        source="deterministic",
    )


def analyze_content_with_configured_llm(
    title: str,
    content: str,
    source_type: str,
    raw_metadata: Mapping[str, object],
) -> AnalysisResult | None:
    if os.environ.get("READ_LATER_DISABLE_LLM") == "1":
        return None
    command = os.environ.get("READ_LATER_LLM_COMMAND")
    if command:
        return analyze_with_command(command, title, content, source_type, raw_metadata)
    provider = os.environ.get("READ_LATER_LLM_PROVIDER", "").strip().lower()
    if provider == "minimax" or (provider == "" and resolve_minimax_api_key()[0]):
        result = analyze_with_minimax(title, content, source_type, raw_metadata)
        if result:
            return result
    if provider in {"", "openai"} and os.environ.get("OPENAI_API_KEY"):
        return analyze_with_openai(title, content, source_type, raw_metadata)
    return None


def analysis_input(title: str, content: str, source_type: str, raw_metadata: Mapping[str, object]) -> dict[str, object]:
    return {
        "title": title,
        "source_type": source_type,
        "content": content[:24000],
        "raw_metadata": dict(raw_metadata),
        "required_output_schema": {
            "summary": "150-300 Chinese characters, explain the real idea instead of copying platform summaries",
            "category": "one of tech,business,design,life,news,other",
            "tags": "3-8 short Chinese tags",
            "key_points": "Markdown bullet list of the most important points",
            "note_content": "Markdown deep-learning note for Obsidian",
        },
    }


def analyze_with_command(
    command: str,
    title: str,
    content: str,
    source_type: str,
    raw_metadata: Mapping[str, object],
) -> AnalysisResult | None:
    payload = analysis_input(title, content, source_type, raw_metadata)
    try:
        completed = subprocess.run(
            shlex.split(command),
            input=json.dumps(payload, ensure_ascii=False),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=int(os.environ.get("READ_LATER_LLM_TIMEOUT", "120")),
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if completed.returncode != 0 or not completed.stdout.strip():
        return None
    parsed = parse_json_text(completed.stdout)
    return analysis_result_from_payload(parsed, source="llm_command") if parsed else None


def analyze_with_openai(
    title: str,
    content: str,
    source_type: str,
    raw_metadata: Mapping[str, object],
) -> AnalysisResult | None:
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        return None
    model = os.environ.get("READ_LATER_LLM_MODEL", "gpt-5.4-mini")
    url = os.environ.get("READ_LATER_LLM_BASE_URL", "https://api.openai.com/v1/responses")
    payload = {
        "model": model,
        "reasoning": {"effort": os.environ.get("READ_LATER_LLM_REASONING", "low")},
        "text": {"verbosity": "low"},
        "max_output_tokens": int(os.environ.get("READ_LATER_LLM_MAX_OUTPUT_TOKENS", "2200")),
        "input": [
            {
                "role": "system",
                "content": (
                    "你是个人知识库的深度阅读助理。任务是把抓取到的原始材料转成可学习、"
                    "可复用的 Obsidian 笔记。不要盲信平台自动摘要；如果材料明显很短或可能偏题，"
                    "要在 note_content 里说明局限。只输出 JSON。"
                ),
            },
            {
                "role": "user",
                "content": json.dumps(analysis_input(title, content, source_type, raw_metadata), ensure_ascii=False),
            },
        ],
    }
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=int(os.environ.get("READ_LATER_LLM_TIMEOUT", "120"))) as response:
            response_payload = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
        return None
    text = extract_openai_response_text(response_payload)
    parsed = parse_json_text(text)
    return analysis_result_from_payload(parsed, source="openai") if parsed else None


def analyze_with_minimax(
    title: str,
    content: str,
    source_type: str,
    raw_metadata: Mapping[str, object],
) -> AnalysisResult | None:
    api_key, _api_key_env = resolve_minimax_api_key()
    if not api_key:
        return None
    model = os.environ.get("READ_LATER_MINIMAX_MODEL") or os.environ.get("READ_LATER_LLM_MODEL") or MINIMAX_DEFAULT_MODEL
    base_url = (os.environ.get("READ_LATER_MINIMAX_BASE_URL") or MINIMAX_DEFAULT_BASE_URL).rstrip("/")
    url = f"{base_url}/chat/completions"
    schema = analysis_json_schema()
    payload = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": (
                    "你是个人知识库的深度阅读助理。把抓取到的原始材料转成可学习、"
                    "可复用的 Obsidian 笔记。不要盲信平台自动摘要；如果材料明显很短、偏题或证据不足，"
                    "必须在 note_content 里说明局限。"
                ),
            },
            {
                "role": "user",
                "content": json.dumps(analysis_input(title, content, source_type, raw_metadata), ensure_ascii=False),
            },
        ],
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": MINIMAX_STRUCTURED_TOOL_NAME,
                    "description": "Return the final structured JSON object for the read-later knowledge workflow.",
                    "parameters": schema,
                },
            }
        ],
        "tool_choice": {"type": "function", "function": {"name": MINIMAX_STRUCTURED_TOOL_NAME}},
        "thinking": {"type": "disabled"},
        "max_tokens": int(os.environ.get("READ_LATER_LLM_MAX_OUTPUT_TOKENS", "2200")),
    }
    if str(model).strip().lower() in {"minimax-m3", "minimax/minimax-m3"}:
        payload["reasoning_split"] = True
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=int(os.environ.get("READ_LATER_LLM_TIMEOUT", "120"))) as response:
            response_payload = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
        return None
    parsed = extract_minimax_tool_payload(response_payload)
    return analysis_result_from_payload(parsed, source="minimax") if parsed else None


def resolve_minimax_api_key() -> tuple[str, str]:
    api_key_env = os.environ.get("READ_LATER_MINIMAX_API_KEY_ENV", MINIMAX_DEFAULT_API_KEY_ENV)
    candidates = [api_key_env]
    if api_key_env == MINIMAX_DEFAULT_API_KEY_ENV:
        candidates.extend(["MINIMAX_CN_API_KEY", "MINIMAX_SUBSCRIPTION_KEY"])
    for name in candidates:
        value = os.environ.get(name)
        if value:
            return value, name
    return "", api_key_env


def analysis_json_schema() -> dict[str, object]:
    return {
        "type": "object",
        "required": ["summary", "category", "tags", "key_points", "note_content"],
        "properties": {
            "summary": {"type": "string"},
            "category": {"type": "string", "enum": ["tech", "business", "design", "life", "news", "other"]},
            "tags": {"type": "array", "items": {"type": "string"}},
            "key_points": {"type": "string"},
            "note_content": {"type": "string"},
        },
        "additionalProperties": False,
    }


def extract_openai_response_text(payload: Mapping[str, object]) -> str:
    if payload.get("output_text"):
        return str(payload["output_text"])
    texts = []
    for item in payload.get("output", []) or []:
        if not isinstance(item, dict):
            continue
        for content_item in item.get("content", []) or []:
            if not isinstance(content_item, dict):
                continue
            if content_item.get("text"):
                texts.append(str(content_item["text"]))
    return "\n".join(texts)


def parse_json_text(text: str) -> dict[str, object] | None:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?\s*", "", stripped)
        stripped = re.sub(r"\s*```$", "", stripped)
    try:
        parsed = json.loads(stripped)
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        match = re.search(r"\{[\s\S]*\}", stripped)
        if not match:
            return None
        try:
            parsed = json.loads(match.group(0))
            return parsed if isinstance(parsed, dict) else None
        except json.JSONDecodeError:
            return None


def extract_minimax_tool_payload(payload: Mapping[str, object]) -> dict[str, object] | None:
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        return None
    message = _mapping_value(choices[0], "message")
    tool_calls = _mapping_value(message, "tool_calls")
    if isinstance(tool_calls, list):
        for tool_call in tool_calls:
            function = _mapping_value(tool_call, "function")
            if _mapping_value(function, "name") != MINIMAX_STRUCTURED_TOOL_NAME:
                continue
            arguments = _mapping_value(function, "arguments")
            if isinstance(arguments, str) and arguments.strip():
                return load_minimax_json_object(arguments)
    content = _mapping_value(message, "content")
    if isinstance(content, str) and content.strip():
        return load_minimax_json_object(json_object_candidate(strip_think_tags(content)))
    return None


def load_minimax_json_object(text: str) -> dict[str, object] | None:
    value = load_minimax_json_value(text)
    if isinstance(value, str):
        value = load_minimax_json_value(value)
    return value if isinstance(value, dict) else None


def load_minimax_json_value(text: str) -> object | None:
    candidates = []
    stripped = strip_markdown_json_fence(strip_think_tags(text))
    for candidate in (text, stripped, json_object_candidate(stripped)):
        cleaned = candidate.strip()
        if cleaned and cleaned not in candidates:
            candidates.append(cleaned)
    for candidate in candidates:
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            continue
    return None


def strip_think_tags(text: str) -> str:
    return re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()


def strip_markdown_json_fence(text: str) -> str:
    stripped = text.strip()
    match = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", stripped, flags=re.DOTALL | re.IGNORECASE)
    return match.group(1).strip() if match else stripped


def json_object_candidate(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("{") and stripped.endswith("}"):
        return stripped
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start >= 0 and end > start:
        return stripped[start : end + 1]
    return stripped


def _mapping_value(container: object, key: str) -> object:
    return container.get(key) if isinstance(container, Mapping) else None


def analysis_result_from_payload(payload: Mapping[str, object], source: str) -> AnalysisResult:
    raw_tags = payload.get("tags") or []
    if isinstance(raw_tags, str):
        raw_tags = [part.strip() for part in re.split(r"[,，、]", raw_tags) if part.strip()]
    tags = [str(tag).strip().strip("#") for tag in raw_tags if str(tag).strip()]
    return AnalysisResult(
        summary=_clean(payload.get("summary", "")),
        category=_clean(payload.get("category", "")),
        tags=tags,
        key_points=str(payload.get("key_points", "") or "").strip(),
        note_content=str(payload.get("note_content", "") or "").strip(),
        source=source,
    )


def normalize_analysis_result(
    result: AnalysisResult,
    title: str,
    content: str,
    source_type: str,
    raw_metadata: Mapping[str, object],
) -> AnalysisResult:
    fallback = deterministic_analysis(title, content, source_type, raw_metadata)
    category = result.category if result.category in {"tech", "business", "design", "life", "news", "other"} else fallback.category
    tags = result.tags or fallback.tags
    return AnalysisResult(
        summary=result.summary or fallback.summary,
        category=category,
        tags=tags[:8],
        key_points=result.key_points,
        note_content=result.note_content or fallback.note_content,
        source=result.source,
    )


def make_summary(content: str, limit: int = 500) -> str:
    text = _clean(content)
    if len(text) <= limit:
        return text
    sentences = re.split(r"(?<=[。！？.!?])\s+", text)
    summary = ""
    for sentence in sentences:
        if not sentence:
            continue
        if len(summary) + len(sentence) > limit:
            break
        summary = f"{summary}{sentence}"
    return summary or text[:limit].rstrip()


def categorize(title: str, content: str, source_type: str) -> str:
    text = f"{title}\n{content}".lower()
    if source_type in {"youtube", "bilibili", "douyin", "pdf"} and any(k in text for k in TECH_KEYWORDS):
        return "tech"
    scores = {
        "tech": sum(1 for keyword in TECH_KEYWORDS if keyword in text),
        "business": sum(1 for keyword in BUSINESS_KEYWORDS if keyword in text),
        "design": sum(1 for keyword in DESIGN_KEYWORDS if keyword in text),
        "news": sum(1 for keyword in NEWS_KEYWORDS if keyword in text),
        "life": sum(1 for keyword in LIFE_KEYWORDS if keyword in text),
    }
    category, score = max(scores.items(), key=lambda item: item[1])
    return category if score > 0 else "other"


TECH_KEYWORDS = {
    "ai",
    "agent",
    "gpt",
    "llm",
    "大模型",
    "人工智能",
    "知识库",
    "编程",
    "代码",
    "自动化",
    "模型",
    "rag",
    "obsidian",
}
BUSINESS_KEYWORDS = {"创业", "商业", "增长", "产品", "销售", "市场", "公司", "融资", "变现"}
DESIGN_KEYWORDS = {"设计", "ux", "ui", "视觉", "动效", "排版", "用户体验"}
NEWS_KEYWORDS = {"发布", "新闻", "最新", "政策", "监管", "宣布", "报道"}
LIFE_KEYWORDS = {"生活", "旅行", "健康", "美食", "家庭", "学习方法", "效率"}


def extract_tags(title: str, content: str, raw_metadata: Mapping[str, object], limit: int = 8) -> list[str]:
    tags = []
    raw_tags = raw_metadata.get("tags")
    if isinstance(raw_tags, str):
        try:
            raw_tags = json.loads(raw_tags)
        except json.JSONDecodeError:
            raw_tags = [raw_tags]
    if isinstance(raw_tags, list):
        tags.extend(str(tag).strip() for tag in raw_tags if str(tag).strip())

    text = f"{title}\n{content}".lower()
    keyword_tags = [
        ("AI", "ai"),
        ("Agent", "agent"),
        ("LLM", "llm"),
        ("知识库", "知识库"),
        ("Obsidian", "obsidian"),
        ("自动化", "自动化"),
        ("RAG", "rag"),
        ("学习", "学习"),
        ("产品", "产品"),
        ("设计", "设计"),
    ]
    for label, needle in keyword_tags:
        if needle in text:
            tags.append(label)

    deduped = []
    seen = set()
    for tag in tags:
        normalized = tag.strip().strip("#")
        key = normalized.lower()
        if normalized and key not in seen:
            deduped.append(normalized)
            seen.add(key)
        if len(deduped) >= limit:
            break
    return deduped


def update_article(
    conn: sqlite3.Connection,
    article_id: int,
    title: str,
    fetch: FetchResult,
    analysis: AnalysisResult,
) -> None:
    conn.execute(
        """
        UPDATE articles
        SET title = ?,
            original_content = ?,
            summary = ?,
            category = ?,
            tags = ?,
            author = ?,
            published_date = ?,
            word_count = ?,
            raw_metadata = ?
        WHERE id = ?
        """,
        (
            title,
            fetch.content,
            analysis.summary,
            analysis.category,
            json.dumps(analysis.tags, ensure_ascii=False),
            fetch.author,
            fetch.published_date,
            len(fetch.content),
            json.dumps(
                {
                    **dict(fetch.raw_metadata or {}),
                    "analysis_source": analysis.source,
                },
                ensure_ascii=False,
            ),
            article_id,
        ),
    )
    conn.commit()


def record_fetch_failure(conn: sqlite3.Connection, article: sqlite3.Row, error: str) -> None:
    conn.execute(
        """
        INSERT INTO fetch_failures (url, source_type, error_message)
        VALUES (?, ?, ?)
        """,
        (article["url"], article["source_type"], error[:2000]),
    )
    conn.commit()


def write_obsidian_note(
    title: str,
    url: str,
    source_type: str,
    author: str,
    category: str,
    tags: list[str],
    summary: str,
    content: str,
    key_points: str,
    vault_path: Path,
) -> str:
    module_path = WORKSPACE / "write-to-obsidian.py"
    spec = importlib.util.spec_from_file_location("write_to_obsidian", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {module_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.VAULT = str(vault_path)
    return module.write_note(
        title=title,
        url=url,
        source_type=source_type,
        author=author,
        category=category,
        tags=",".join(tags),
        summary=summary,
        content=content,
        key_points=key_points,
    )


def sync_article_to_gbrain(db_path: Path | str) -> bool:
    import sync_to_gbrain

    result = sync_to_gbrain.sync_articles(db_path=db_path, limit=1, verbose=False)
    return result.synced > 0


def derive_title_from_url(url: str) -> str:
    cleaned = url.rstrip("/")
    tail = cleaned.rsplit("/", 1)[-1] if "/" in cleaned else cleaned
    return tail or cleaned or "Untitled"


def extract_between(text: str, start: str, end: str) -> str:
    try:
        after = text.split(start, 1)[1]
        return _clean(after.split(end, 1)[0])
    except IndexError:
        return ""


def _clean(value: object) -> str:
    text = "" if value is None else str(value)
    return re.sub(r"\s+", " ", text).strip()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Process one read-later article end-to-end")
    parser.add_argument("--id", type=int, required=True, help="articles.id to process")
    parser.add_argument("--db", default=str(DEFAULT_DB), help="Path to articles.db")
    parser.add_argument("--vault", default=str(DEFAULT_VAULT), help="Obsidian vault path")
    parser.add_argument("--sync-gbrain", action="store_true", help="Sync one eligible article to gbrain")
    parser.add_argument("--force", action="store_true", help="Re-process even if summary already exists")
    args = parser.parse_args(argv)

    result = process_article(
        args.id,
        db_path=args.db,
        vault_path=Path(args.vault).expanduser(),
        sync_gbrain=args.sync_gbrain,
        force=args.force,
    )
    print(json.dumps(dataclasses.asdict(result), ensure_ascii=False))
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
