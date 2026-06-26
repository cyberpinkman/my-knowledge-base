import importlib.util
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


def load_fetch_video():
    module_path = Path(__file__).with_name("fetch-video.py")
    spec = importlib.util.spec_from_file_location("fetch_video", module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FetchVideoTest(unittest.TestCase):
    def test_short_douyin_summary_is_not_usable_for_analysis(self):
        fetch_video = load_fetch_video()
        old_threshold = os.environ.get("MY_KNOWLEDGE_BASE_DOUYIN_MIN_CHAPTER_SUMMARY_CHARS")
        os.environ["MY_KNOWLEDGE_BASE_DOUYIN_MIN_CHAPTER_SUMMARY_CHARS"] = "300"
        self.addCleanup(lambda: _restore_env("MY_KNOWLEDGE_BASE_DOUYIN_MIN_CHAPTER_SUMMARY_CHARS", old_threshold))

        self.assertFalse(fetch_video.is_usable_douyin_summary("太短的摘要"))
        self.assertTrue(fetch_video.is_usable_douyin_summary("长摘要" * 150))

    def test_download_douyin_media_prefers_audio_url(self):
        fetch_video = load_fetch_video()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        calls = []

        def fake_download(url, output_path, min_size=10000):
            calls.append((url, Path(output_path).name, min_size))
            Path(output_path).write_text("media", encoding="utf-8")
            return True

        payload = {
            "audio_url": "https://example.com/audio.mp4",
            "video_url": "https://example.com/video.mp4",
        }
        with patch.object(fetch_video, "download_url", fake_download):
            result = fetch_video.download_douyin_media(payload, tmp.name)

        self.assertEqual(Path(result).name, "douyin_audio.mp4")
        self.assertEqual(calls, [("https://example.com/audio.mp4", "douyin_audio.mp4", 10000)])

    def test_transcribe_audio_uses_whisper_cli_fallback(self):
        fetch_video = load_fetch_video()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        audio = Path(tmp.name) / "douyin_audio.mp4"
        audio.write_bytes(b"fake audio")

        def fake_run(cmd, **kwargs):
            self.assertEqual(Path(cmd[0]).name, "whisper")
            self.assertIn("--model", cmd)
            (Path(tmp.name) / "douyin_audio.txt").write_text("转写文本", encoding="utf-8")
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        with patch.object(fetch_video.shutil, "which", return_value="/usr/local/bin/whisper"):
            with patch.object(fetch_video, "run", fake_run):
                text = fetch_video.transcribe_audio(str(audio))

        self.assertEqual(text, "转写文本")


def _restore_env(name, value):
    if value is None:
        os.environ.pop(name, None)
    else:
        os.environ[name] = value


if __name__ == "__main__":
    unittest.main()
