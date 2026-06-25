import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pipeline


def make_db():
    tmp = tempfile.TemporaryDirectory()
    db_path = Path(tmp.name) / "articles.db"
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.executescript(Path("schema.sql").read_text())
    conn.commit()
    return tmp, db_path, conn


class ReadLaterPipelineTest(unittest.TestCase):
    def setUp(self):
        self._old_disable_llm = os.environ.get("READ_LATER_DISABLE_LLM")
        os.environ["READ_LATER_DISABLE_LLM"] = "1"

    def tearDown(self):
        _restore_env("READ_LATER_DISABLE_LLM", self._old_disable_llm)

    def test_process_article_updates_database_and_writes_obsidian_note(self):
        tmp, db_path, conn = make_db()
        self.addCleanup(tmp.cleanup)
        vault = Path(tmp.name) / "vault"
        conn.execute(
            "INSERT INTO articles (url, source_type) VALUES (?, ?)",
            ("https://example.com/article", "web"),
        )
        conn.commit()
        article_id = conn.execute("SELECT id FROM articles").fetchone()["id"]

        def fetcher(article):
            return pipeline.FetchResult(
                title="Agent Learning Workflow",
                author="Pink",
                content="收藏不是学习。Agent 可以先抓取内容，再总结、分类、写入知识库。",
                source="fixture",
                raw_metadata={"source": "unit-test"},
            )

        result = pipeline.process_article(
            article_id,
            db_path=db_path,
            vault_path=vault,
            fetcher=fetcher,
            sync_gbrain=False,
        )

        self.assertTrue(result.ok)
        row = conn.execute(
            "SELECT title, summary, category, tags, original_content, author FROM articles WHERE id = ?",
            (article_id,),
        ).fetchone()
        self.assertEqual(row["title"], "Agent Learning Workflow")
        self.assertIn("Agent 可以先抓取内容", row["summary"])
        self.assertEqual(row["category"], "tech")
        self.assertIn("知识库", row["tags"])
        self.assertEqual(row["author"], "Pink")
        self.assertIn("收藏不是学习", row["original_content"])

        notes = list(vault.rglob("*.md"))
        self.assertEqual(len(notes), 1)
        note = notes[0].read_text()
        self.assertIn("# Agent Learning Workflow", note)
        self.assertIn("## 摘要", note)
        self.assertIn("## 正文", note)

    def test_process_article_records_fetch_failure_and_skips_obsidian(self):
        tmp, db_path, conn = make_db()
        self.addCleanup(tmp.cleanup)
        vault = Path(tmp.name) / "vault"
        conn.execute(
            "INSERT INTO articles (url, source_type) VALUES (?, ?)",
            ("https://example.com/broken", "web"),
        )
        conn.commit()
        article_id = conn.execute("SELECT id FROM articles").fetchone()["id"]

        def fetcher(article):
            return pipeline.FetchResult(
                title="",
                content="",
                source="fixture",
                error="empty content",
            )

        result = pipeline.process_article(
            article_id,
            db_path=db_path,
            vault_path=vault,
            fetcher=fetcher,
            sync_gbrain=False,
        )

        self.assertFalse(result.ok)
        self.assertEqual(result.error, "empty content")
        failures = conn.execute(
            "SELECT url, source_type, error_message FROM fetch_failures"
        ).fetchall()
        self.assertEqual(len(failures), 1)
        self.assertEqual(failures[0]["url"], "https://example.com/broken")
        self.assertIn("empty content", failures[0]["error_message"])
        self.assertFalse(list(vault.rglob("*.md")))

    def test_reprocessing_same_source_updates_existing_obsidian_note(self):
        tmp, db_path, conn = make_db()
        self.addCleanup(tmp.cleanup)
        vault = Path(tmp.name) / "vault"
        conn.execute(
            "INSERT INTO articles (url, source_type) VALUES (?, ?)",
            ("https://example.com/reprocess", "web"),
        )
        conn.commit()
        article_id = conn.execute("SELECT id FROM articles").fetchone()["id"]

        contents = iter(["first content about Agent", "second content about Agent"])

        def fetcher(article):
            return pipeline.FetchResult(
                title="Stable Title",
                content=next(contents),
                source="fixture",
            )

        first = pipeline.process_article(
            article_id,
            db_path=db_path,
            vault_path=vault,
            fetcher=fetcher,
            sync_gbrain=False,
            force=True,
        )
        second = pipeline.process_article(
            article_id,
            db_path=db_path,
            vault_path=vault,
            fetcher=fetcher,
            sync_gbrain=False,
            force=True,
        )

        notes = list(vault.rglob("*.md"))
        self.assertEqual(len(notes), 1)
        self.assertEqual(first.note_path, second.note_path)
        self.assertIn("second content", notes[0].read_text())

    def test_process_article_prefers_llm_deep_analysis_over_platform_summary(self):
        tmp, db_path, conn = make_db()
        self.addCleanup(tmp.cleanup)
        vault = Path(tmp.name) / "vault"
        conn.execute(
            "INSERT INTO articles (url, source_type) VALUES (?, ?)",
            ("https://v.douyin.com/example/", "douyin"),
        )
        conn.commit()
        article_id = conn.execute("SELECT id FROM articles").fetchone()["id"]

        def fetcher(article):
            return pipeline.FetchResult(
                title="AI 会对现代社会造成什么影响",
                author="Anson安叔",
                content="章节要点：平台自动摘要很短，甚至可能不完整。",
                source="douyin_page",
                raw_metadata={"tags": ["AI"]},
            )

        def analyzer(title, content, source_type, raw_metadata):
            return pipeline.AnalysisResult(
                summary="LLM 深度分析：这条内容真正讨论的是 AI 对就业、生产率和个人职业选择的结构性影响。",
                category="tech",
                tags=["AI", "就业", "学习"],
                key_points="- AI 会改变岗位结构\n- 普通人要靠真实责任和跨领域能力避开同质化",
                note_content=(
                    "## 核心观点\n\n"
                    "这不是一条普通 AI 新闻，而是在讨论技术进步、就业结构和个人策略之间的关系。\n\n"
                    "## 行动建议\n\n"
                    "把 AI 当成能力放大器，选择需要判断、责任和真实互动的方向。"
                ),
            )

        result = pipeline.process_article(
            article_id,
            db_path=db_path,
            vault_path=vault,
            fetcher=fetcher,
            analyzer=analyzer,
            sync_gbrain=False,
        )

        self.assertTrue(result.ok)
        row = conn.execute(
            "SELECT summary, category, tags, original_content FROM articles WHERE id = ?",
            (article_id,),
        ).fetchone()
        self.assertIn("LLM 深度分析", row["summary"])
        self.assertEqual(row["category"], "tech")
        self.assertIn("就业", row["tags"])
        self.assertIn("平台自动摘要很短", row["original_content"])

        note = next(vault.rglob("*.md")).read_text()
        self.assertIn("LLM 深度分析", note)
        self.assertIn("## 要点", note)
        self.assertIn("## 核心观点", note)
        self.assertNotIn("## 正文\n\n章节要点：平台自动摘要很短", note)

    def test_reprocessing_preserves_existing_author_when_fetch_lacks_author(self):
        tmp, db_path, conn = make_db()
        self.addCleanup(tmp.cleanup)
        vault = Path(tmp.name) / "vault"
        conn.execute(
            """
            INSERT INTO articles (url, source_type, author, title, summary, category)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                "https://v.douyin.com/author/",
                "douyin",
                "Anson安叔",
                "Old title",
                "old summary",
                "tech",
            ),
        )
        conn.commit()
        article_id = conn.execute("SELECT id FROM articles").fetchone()["id"]

        def fetcher(article):
            return pipeline.FetchResult(
                title="New title",
                author="",
                content="new AI content",
                source="fixture",
            )

        result = pipeline.process_article(
            article_id,
            db_path=db_path,
            vault_path=vault,
            fetcher=fetcher,
            sync_gbrain=False,
            force=True,
        )

        self.assertTrue(result.ok)
        row = conn.execute("SELECT author FROM articles WHERE id = ?", (article_id,)).fetchone()
        self.assertEqual(row["author"], "Anson安叔")

    def test_configured_llm_command_returns_structured_analysis(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        command_path = Path(tmp.name) / "fake_llm.py"
        command_path.write_text(
            "import json, sys\n"
            "payload = json.loads(sys.stdin.read())\n"
            "assert payload['title'] == 'Test Title'\n"
            "print(json.dumps({\n"
            "  'summary': '命令行 LLM 生成的深度摘要',\n"
            "  'category': 'tech',\n"
            "  'tags': ['LLM', '知识库'],\n"
            "  'key_points': '- point',\n"
            "  'note_content': '## 深度分析\\n\\ncommand output'\n"
            "}, ensure_ascii=False))\n",
            encoding="utf-8",
        )
        old_command = os.environ.get("READ_LATER_LLM_COMMAND")
        old_disable = os.environ.pop("READ_LATER_DISABLE_LLM", None)
        os.environ["READ_LATER_LLM_COMMAND"] = f"python3 {command_path}"
        self.addCleanup(lambda: _restore_env("READ_LATER_LLM_COMMAND", old_command))
        self.addCleanup(lambda: _restore_env("READ_LATER_DISABLE_LLM", old_disable))

        result = pipeline.analyze_content_with_configured_llm(
            "Test Title",
            "raw content",
            "web",
            {},
        )

        self.assertIsNotNone(result)
        self.assertEqual(result.summary, "命令行 LLM 生成的深度摘要")
        self.assertEqual(result.source, "llm_command")

    def test_minimax_analysis_uses_forced_tool_call_and_cn_key_fallback(self):
        seen = {}

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self):
                return json.dumps(
                    {
                        "choices": [
                            {
                                "message": {
                                    "tool_calls": [
                                        {
                                            "function": {
                                                "name": "emit_structured_response",
                                                "arguments": json.dumps(
                                                    {
                                                        "summary": "MiniMax 深度摘要",
                                                        "category": "tech",
                                                        "tags": ["AI", "知识库"],
                                                        "key_points": "- point",
                                                        "note_content": "## 深度分析\n\nMiniMax output",
                                                    },
                                                    ensure_ascii=False,
                                                ),
                                            }
                                        }
                                    ]
                                }
                            }
                        ]
                    },
                    ensure_ascii=False,
                ).encode("utf-8")

        def fake_urlopen(request, timeout):
            seen["url"] = request.full_url
            seen["timeout"] = timeout
            seen["headers"] = dict(request.header_items())
            seen["payload"] = json.loads(request.data.decode("utf-8"))
            return FakeResponse()

        env_patch = {
            "MINIMAX_CN_API_KEY": "test-key",
            "READ_LATER_LLM_PROVIDER": "minimax",
        }
        with patch.dict(os.environ, env_patch, clear=False), patch("pipeline.urllib.request.urlopen", fake_urlopen):
            os.environ.pop("READ_LATER_DISABLE_LLM", None)
            result = pipeline.analyze_content_with_configured_llm(
                "Test Title",
                "raw content",
                "douyin",
                {"tags": ["AI"]},
            )

        self.assertIsNotNone(result)
        self.assertEqual(result.summary, "MiniMax 深度摘要")
        self.assertEqual(result.source, "minimax")
        self.assertEqual(seen["url"], "https://api.minimaxi.com/v1/chat/completions")
        self.assertEqual(seen["payload"]["model"], "MiniMax-M3")
        self.assertEqual(seen["payload"]["tool_choice"], {"type": "function", "function": {"name": "emit_structured_response"}})
        self.assertEqual(seen["payload"]["tools"][0]["function"]["name"], "emit_structured_response")
        self.assertEqual(seen["payload"]["thinking"], {"type": "disabled"})
        self.assertTrue(seen["payload"]["reasoning_split"])
        self.assertIn("Authorization", seen["headers"])

    def test_douyin_page_payload_becomes_fetch_result(self):
        payload = {
            "title": "AI 会对现代社会造成什么影响",
            "author": "Anson安叔",
            "duration": "01:23",
            "chapter_summary": "普通人应该把 AI 当成能力放大器，而不是单纯恐慌。",
            "tags": ["AI", "学习"],
            "published_date": "2026-06-25",
        }

        result = pipeline.fetch_result_from_douyin_payload("https://v.douyin.com/x/", payload)

        self.assertEqual(result.title, "AI 会对现代社会造成什么影响")
        self.assertEqual(result.author, "Anson安叔")
        self.assertIn("能力放大器", result.content)
        self.assertEqual(result.source, "douyin_page")
        self.assertEqual(json.loads(result.raw_metadata["tags"])[0], "AI")


def _restore_env(name, value):
    if value is None:
        os.environ.pop(name, None)
    else:
        os.environ[name] = value


if __name__ == "__main__":
    unittest.main()
