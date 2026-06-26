import os
import tempfile
import unittest
from pathlib import Path

import idea


class IdeaGbrainSearchTest(unittest.TestCase):
    def test_parse_gbrain_query_results_extracts_unique_slugs(self):
        output = """
[0.8721] media/articles/wechat-3-dc5fc29c -- Agent Team and observability
[0.4210] media/articles/web-5-4f449d82 -- MCP server notes
[0.3210] media/articles/wechat-3-dc5fc29c -- duplicate chunk
warning: ignored line
"""

        self.assertEqual(
            idea.parse_gbrain_query_results(output),
            ["media/articles/wechat-3-dc5fc29c", "media/articles/web-5-4f449d82"],
        )

    def test_search_related_knowledge_prefers_gbrain_results(self):
        calls = []

        def runner(argv):
            calls.append(argv)
            return idea.CommandResult(
                returncode=0,
                stdout="[0.9000] media/articles/twitter-6-307aa2d3 -- Claude Architect\n",
                stderr="",
            )

        result = idea.search_related_knowledge("Claude Architect", command_runner=runner)

        self.assertEqual(result, ["media/articles/twitter-6-307aa2d3"])
        self.assertEqual(calls[0][:2], ["gbrain", "query"])

    def test_search_related_knowledge_falls_back_to_vault_when_gbrain_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            old_vault = idea.VAULT
            try:
                idea.VAULT = tmp
                articles = Path(tmp) / "稍后阅读" / "技术"
                articles.mkdir(parents=True)
                (articles / "Agent组织方式.md").write_text(
                    "# Agent组织方式\n\nAgent Team 可观测 回滚", encoding="utf-8"
                )

                def runner(_argv):
                    return idea.CommandResult(returncode=1, stdout="", stderr="locked")

                result = idea.search_related_knowledge("Agent Team", command_runner=runner)
            finally:
                idea.VAULT = old_vault

        self.assertEqual(result, ["Agent组织方式"])

    def test_build_related_details_fetches_gbrain_summary_for_slugs(self):
        calls = []

        def runner(argv):
            calls.append(argv)
            return idea.CommandResult(
                returncode=0,
                stdout=(
                    "---\n"
                    "title: Agent Article\n"
                    "---\n\n"
                    "# Agent Article\n\n"
                    "## Summary\n\n"
                    "Agents become useful when organized as observable teams.\n\n"
                    "## Original Content\n\n"
                    "Long body"
                ),
                stderr="",
            )

        details = idea.build_related_details(
            ["media/articles/wechat-3-dc5fc29c"],
            command_runner=runner,
        )

        self.assertEqual(calls[0], ["gbrain", "get", "media/articles/wechat-3-dc5fc29c"])
        self.assertEqual(
            details,
            [
                {
                    "title": "media/articles/wechat-3-dc5fc29c",
                    "summary": "Agents become useful when organized as observable teams.",
                    "source": "gbrain",
                }
            ],
        )

    def test_analyze_missing_idea_includes_gbrain_related_details(self):
        with tempfile.TemporaryDirectory() as tmp:
            old_vault = idea.VAULT
            old_idea_dir = idea.IDEA_DIR
            try:
                idea.VAULT = tmp
                idea.IDEA_DIR = os.path.join(tmp, "灵感")

                def runner(argv):
                    if argv[:2] == ["gbrain", "query"]:
                        return idea.CommandResult(
                            returncode=0,
                            stdout="[0.9000] media/articles/twitter-6-307aa2d3 -- Claude Architect\n",
                            stderr="",
                        )
                    if argv[:2] == ["gbrain", "get"]:
                        return idea.CommandResult(
                            returncode=0,
                            stdout="# Page\n\n## Summary\n\nArchitect course summary.",
                            stderr="",
                        )
                    raise AssertionError(argv)

                result = idea.analyze_idea("Claude Architect", command_runner=runner)
            finally:
                idea.VAULT = old_vault
                idea.IDEA_DIR = old_idea_dir

        self.assertFalse(result["found"])
        self.assertEqual(
            result["related_details"],
            [
                {
                    "title": "media/articles/twitter-6-307aa2d3",
                    "summary": "Architect course summary.",
                    "source": "gbrain",
                }
            ],
        )


if __name__ == "__main__":
    unittest.main()
