import json
import os
import sqlite3
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pipeline


LEGACY_ENV_PREFIX = "READ" + "_LATER"


def legacy_env(suffix):
    return f"{LEGACY_ENV_PREFIX}_{suffix}"


def make_db():
    tmp = tempfile.TemporaryDirectory()
    db_path = Path(tmp.name) / "articles.db"
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.executescript(Path("schema.sql").read_text())
    conn.commit()
    return tmp, db_path, conn


class MyKnowledgeBasePipelineTest(unittest.TestCase):
    def setUp(self):
        self._old_disable_llm = os.environ.get("MY_KNOWLEDGE_BASE_DISABLE_LLM")
        os.environ["MY_KNOWLEDGE_BASE_DISABLE_LLM"] = "1"

    def tearDown(self):
        _restore_env("MY_KNOWLEDGE_BASE_DISABLE_LLM", self._old_disable_llm)

    def test_env_get_supports_legacy_fallback_with_new_prefix_precedence(self):
        new_name = "MY_KNOWLEDGE_BASE_LLM_PROVIDER"
        legacy_name = legacy_env("LLM_PROVIDER")
        old_new = os.environ.pop(new_name, None)
        old_legacy = os.environ.get(legacy_name)
        self.addCleanup(lambda: _restore_env(new_name, old_new))
        self.addCleanup(lambda: _restore_env(legacy_name, old_legacy))

        os.environ[legacy_name] = "minimax"
        self.assertEqual(pipeline.env_get("LLM_PROVIDER"), "minimax")

        os.environ[new_name] = "openai"
        self.assertEqual(pipeline.env_get("LLM_PROVIDER"), "openai")

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
        self.assertIn("*由 my-knowledge-base 自动收录于", note)
        self.assertNotIn("智能" + "稍后阅读", note)

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
        old_command = os.environ.get("MY_KNOWLEDGE_BASE_LLM_COMMAND")
        old_disable = os.environ.pop("MY_KNOWLEDGE_BASE_DISABLE_LLM", None)
        old_legacy_disable = os.environ.pop(legacy_env("DISABLE_LLM"), None)
        os.environ["MY_KNOWLEDGE_BASE_LLM_COMMAND"] = f"python3 {command_path}"
        self.addCleanup(lambda: _restore_env("MY_KNOWLEDGE_BASE_LLM_COMMAND", old_command))
        self.addCleanup(lambda: _restore_env("MY_KNOWLEDGE_BASE_DISABLE_LLM", old_disable))
        self.addCleanup(lambda: _restore_env(legacy_env("DISABLE_LLM"), old_legacy_disable))

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
            "MY_KNOWLEDGE_BASE_LLM_PROVIDER": "minimax",
        }
        old_legacy_disable = os.environ.pop(legacy_env("DISABLE_LLM"), None)
        self.addCleanup(lambda: _restore_env(legacy_env("DISABLE_LLM"), old_legacy_disable))
        with patch.dict(os.environ, env_patch, clear=False), patch("pipeline.urllib.request.urlopen", fake_urlopen):
            os.environ.pop("MY_KNOWLEDGE_BASE_DISABLE_LLM", None)
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

    def test_sync_article_to_gbrain_passes_current_article_id(self):
        class Result:
            synced = 1

        with patch("sync_to_gbrain.sync_articles", return_value=Result()) as sync_articles:
            did_sync = pipeline.sync_article_to_gbrain(Path("/tmp/articles.db"), 42)

        self.assertTrue(did_sync)
        sync_articles.assert_called_once_with(
            db_path=Path("/tmp/articles.db"),
            article_id=42,
            limit=1,
            verbose=False,
        )

    def test_run_json_command_returns_fetch_result_on_timeout(self):
        with patch(
            "pipeline.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd=["node", "fetch.js"], timeout=3),
        ):
            result = pipeline.run_json_command(["node", "fetch.js"], timeout=3)

        self.assertIsInstance(result, pipeline.FetchResult)
        self.assertIn("timed out", result.error)
        self.assertIn("node fetch.js", result.error)

    def test_fetch_web_content_returns_fetch_result_on_os_error(self):
        with patch("pipeline.subprocess.run", side_effect=OSError("node missing")):
            result = pipeline.fetch_web_content("https://example.com/article")

        self.assertEqual(result.source, "web")
        self.assertEqual(result.url, "https://example.com/article")
        self.assertIn("node missing", result.error)

    def test_fetch_web_content_removes_temp_screenshot_unless_debug_retention_enabled(self):
        old_keep = os.environ.pop("MY_KNOWLEDGE_BASE_KEEP_SCREENSHOTS", None)
        old_legacy_keep = os.environ.pop(legacy_env("KEEP_SCREENSHOTS"), None)
        self.addCleanup(lambda: _restore_env("MY_KNOWLEDGE_BASE_KEEP_SCREENSHOTS", old_keep))
        self.addCleanup(lambda: _restore_env(legacy_env("KEEP_SCREENSHOTS"), old_legacy_keep))
        seen_paths = []

        def fake_run(argv, **kwargs):
            screenshot = Path(argv[3])
            screenshot.write_text("fake screenshot", encoding="utf-8")
            seen_paths.append(screenshot)
            return subprocess.CompletedProcess(
                argv,
                0,
                stdout="",
                stderr=(
                    "---TITLE---\nTitle\n---END TITLE---\n"
                    "---CONTENT---\nBody text\n---END CONTENT---\n"
                ),
            )

        with patch("pipeline.subprocess.run", fake_run):
            result = pipeline.fetch_web_content("https://example.com/article")

        self.assertEqual(result.content, "Body text")
        self.assertNotIn("screenshot", result.raw_metadata or {})
        self.assertFalse(seen_paths[0].exists())

        os.environ["MY_KNOWLEDGE_BASE_KEEP_SCREENSHOTS"] = "1"
        with patch("pipeline.subprocess.run", fake_run):
            retained = pipeline.fetch_web_content("https://example.com/article")

        retained_path = Path(retained.raw_metadata["screenshot"])
        self.assertTrue(retained_path.exists())
        self.addCleanup(lambda: retained_path.exists() and retained_path.unlink())

    def test_analysis_payload_builds_note_with_fact_boundaries(self):
        required = set(pipeline.analysis_json_schema()["required"])
        self.assertTrue(
            {
                "source_facts",
                "inferences",
                "external_context",
                "claims_to_verify",
            }.issubset(required)
        )

        result = pipeline.analysis_result_from_payload(
            {
                "summary": "这是一条结构化深度摘要。",
                "category": "tech",
                "tags": ["AI", "就业"],
                "source_facts": ["原文提到 AI 会造成三组脱钩。"],
                "inferences": ["普通人应优先选择需要承担责任的岗位。"],
                "external_context": ["AI 税与企业披露义务需要另行查证。"],
                "claims_to_verify": ["三组脱钩是否有原始研究或数据支撑？"],
            },
            source="minimax",
        )

        self.assertIn("## 原文事实", result.note_content)
        self.assertIn("原文提到 AI 会造成三组脱钩。", result.note_content)
        self.assertIn("## 模型推断", result.note_content)
        self.assertIn("## 外部背景（待查证）", result.note_content)
        self.assertIn("## 待查证", result.note_content)

    def test_obsidian_frontmatter_escapes_yaml_values(self):
        tmp, db_path, conn = make_db()
        self.addCleanup(tmp.cleanup)
        vault = Path(tmp.name) / "vault"
        conn.execute(
            "INSERT INTO articles (url, source_type) VALUES (?, ?)",
            ("https://example.com/yaml", "web"),
        )
        conn.commit()
        article_id = conn.execute("SELECT id FROM articles").fetchone()["id"]

        def fetcher(article):
            return pipeline.FetchResult(
                title='Agent "Quoted": Test',
                content="content about AI agents",
                source="fixture",
            )

        def analyzer(title, content, source_type, raw_metadata):
            return pipeline.AnalysisResult(
                summary="summary",
                category="tech",
                tags=["AI,Agent", "风险:测试"],
                key_points="- point",
                note_content="body",
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
        note = next(vault.rglob("*.md")).read_text()
        self.assertIn('title: "Agent \\"Quoted\\": Test"', note)
        self.assertIn('tags: ["AI,Agent", "风险:测试"]', note)

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
