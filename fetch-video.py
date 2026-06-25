#!/usr/bin/env python3
"""
视频内容抓取脚本 v2
用法: python3 fetch-video.py <url> [output-dir]

处理链:
1. 字幕提取 (youtube-transcript-api / yt-dlp)
2. 无字幕 → 下载视频 → 提取音频 → Whisper 语音转文字
3. 下载失败 → 截帧分析
4. 全部失败 → 元数据兜底

输出 JSON: {title, author, description, duration, transcript, frames_dir, source}
"""

import json
import os
import re
import subprocess
import sys
import glob
import shutil
from pathlib import Path


def run(cmd, **kwargs):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=kwargs.pop('timeout', 120), **kwargs)


def get_video_id(url):
    m = re.search(r'(?:v=|youtu\.be/|shorts/|embed/|live/)([a-zA-Z0-9_-]{11})', url)
    return m.group(1) if m else None


def get_metadata(url):
    """Get video metadata via yt-dlp --dump-json."""
    # Try without cookies first
    try:
        r = run(['yt-dlp', '--dump-json', '--no-download', '--no-warnings', url], timeout=30)
        if r.returncode == 0:
            return json.loads(r.stdout)
    except Exception:
        pass
    
    # Try with Chrome cookies (all profiles)
    chrome_dir = os.path.expanduser("~/Library/Application Support/Google/Chrome")
    if os.path.exists(chrome_dir):
        for profile in sorted(os.listdir(chrome_dir)):
            if profile.startswith('Profile') or profile == 'Default':
                try:
                    r = run([
                        'yt-dlp', f'--cookies-from-browser=chrome:{profile}',
                        '--dump-json', '--no-download', '--no-warnings', url
                    ], timeout=15)
                    if r.returncode == 0:
                        return json.loads(r.stdout)
                except Exception:
                    continue
    return {}


def get_youtube_transcript(video_id):
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
        api = YouTubeTranscriptApi()
        for langs in [['zh-Hans', 'zh', 'en'], None]:
            try:
                result = api.fetch(video_id, languages=langs) if langs else api.fetch(video_id)
                return ' '.join(seg.text for seg in result)
            except Exception:
                continue
    except ImportError:
        pass
    return ''


def get_subtitle_via_ytdlp(url, outdir):
    try:
        run([
            'yt-dlp', '--write-auto-sub', '--write-sub',
            '--sub-format', 'srt/vtt',
            '--sub-lang', 'zh-Hans,zh,en',
            '--skip-download', '--no-warnings',
            '-o', os.path.join(outdir, 'subs'), url
        ], timeout=30)
        for ext in ('*.srt', '*.vtt'):
            for f in glob.glob(os.path.join(outdir, f'subs*{ext}')):
                text = parse_subtitle(f)
                if text:
                    return text
    except Exception:
        pass
    return ''


def parse_subtitle(filepath):
    lines = []
    with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
        for line in f:
            line = line.strip()
            if not line or re.match(r'^\d+$', line) or '-->' in line:
                continue
            if line.startswith(('WEBVTT', 'Kind:', 'Language:', 'NOTE')):
                continue
            line = re.sub(r'<[^>]*>', '', line)
            lines.append(line)
    if not lines:
        return ''
    deduped = [lines[0]]
    for line in lines[1:]:
        if line != deduped[-1]:
            deduped.append(line)
    return ' '.join(deduped)


def download_video(url, outdir):
    """Download video, trying multiple methods."""
    video_file = os.path.join(outdir, 'video.mp4')
    
    # Method 1: yt-dlp direct
    attempts = [
        ['yt-dlp', '-f', 'worstvideo+worstaudio/worst',
         '--merge-output-format', 'mp4', '--max-filesize', '200M',
         '--no-warnings', '-o', video_file, url],
    ]
    
    # Method 2: Try with Chrome cookies
    chrome_dir = os.path.expanduser("~/Library/Application Support/Google/Chrome")
    if os.path.exists(chrome_dir):
        for profile in sorted(os.listdir(chrome_dir)):
            if profile.startswith('Profile') or profile == 'Default':
                attempts.append([
                    'yt-dlp', f'--cookies-from-browser=chrome:{profile}',
                    '-f', 'worstvideo+worstaudio/worst',
                    '--merge-output-format', 'mp4', '--max-filesize', '200M',
                    '--no-warnings', '-o', video_file, url
                ])
    
    for cmd in attempts:
        try:
            r = run(cmd, timeout=180)
            if os.path.exists(video_file) and os.path.getsize(video_file) > 10000:
                return video_file
        except Exception:
            continue
    
    return None


def download_video_playwright(url, outdir):
    """Legacy hook for direct video downloads.

    fetch-douyin.js extracts page metadata as JSON; it does not download an mp4.
    Keep this function as a no-op so callers do not mistake page scraping for
    video download.
    """
    return None


def fetch_douyin_page_json(url):
    """Fetch Douyin page metadata via Playwright JSON scraper."""
    if 'douyin.com' not in url and 'v.douyin.com' not in url:
        return {}
    script = os.path.join(os.path.dirname(__file__), 'fetch-douyin.js')
    
    if not os.path.exists(script):
        return {}
    
    try:
        r = run(['node', script, url], timeout=90)
        if r.returncode == 0 and r.stdout.strip():
            return json.loads(r.stdout)
    except Exception:
        pass
    return {}


def extract_audio(video_file, outdir):
    """Extract audio from video file using ffmpeg."""
    audio_file = os.path.join(outdir, 'audio.mp3')
    try:
        run([
            'ffmpeg', '-i', video_file,
            '-vn', '-acodec', 'libmp3lame', '-q:a', '4',
            audio_file, '-y', '-loglevel', 'error'
        ], timeout=60)
        if os.path.exists(audio_file) and os.path.getsize(audio_file) > 1000:
            return audio_file
    except Exception:
        pass
    
    # Fallback: try wav
    audio_wav = os.path.join(outdir, 'audio.wav')
    try:
        run([
            'ffmpeg', '-i', video_file,
            '-vn', '-acodec', 'pcm_s16le', '-ar', '16000', '-ac', '1',
            audio_wav, '-y', '-loglevel', 'error'
        ], timeout=60)
        if os.path.exists(audio_wav) and os.path.getsize(audio_wav) > 1000:
            return audio_wav
    except Exception:
        pass
    return None


def transcribe_audio(audio_file):
    """Transcribe audio using faster-whisper (fast) or openai-whisper (fallback)."""
    # Method 1: faster-whisper (much faster, less memory)
    try:
        from faster_whisper import WhisperModel
        model = WhisperModel("base", device="cpu", compute_type="int8")
        segments, info = model.transcribe(audio_file, language=None)
        text = ' '.join(seg.text.strip() for seg in segments)
        if text:
            return text
    except Exception as e:
        print(f"[fetch-video] faster-whisper failed: {e}", file=sys.stderr)
    
    # Method 2: openai-whisper
    try:
        import whisper
        model = whisper.load_model("base")
        result = model.transcribe(audio_file)
        text = result.get('text', '').strip()
        if text:
            return text
    except Exception as e:
        print(f"[fetch-video] whisper failed: {e}", file=sys.stderr)
    
    return ''


def extract_frames(video_file, outdir):
    """Extract frames from video."""
    frames_dir = os.path.join(outdir, 'frames')
    os.makedirs(frames_dir, exist_ok=True)
    
    try:
        # Get duration
        probe = run([
            'ffprobe', '-v', 'error', '-show_entries', 'format=duration',
            '-of', 'default=noprint_wrappers=1:nokey=1', video_file
        ], timeout=10)
        duration = float(probe.stdout.strip()) if probe.stdout.strip() else 0
        
        # Determine frame interval: aim for ~10 frames
        if duration > 0:
            interval = max(10, int(duration / 10))
        else:
            interval = 30
        
        run([
            'ffmpeg', '-i', video_file,
            '-vf', f'fps=1/{interval}',
            '-frames:v', '10',
            os.path.join(frames_dir, 'frame_%03d.jpg'),
            '-y', '-loglevel', 'error'
        ], timeout=60)
        
        frames = glob.glob(os.path.join(frames_dir, 'frame_*.jpg'))
        if frames:
            return frames_dir
    except Exception:
        pass
    return ''


def main():
    if len(sys.argv) < 2:
        print("用法: python3 fetch-video.py <url> [output-dir]", file=sys.stderr)
        sys.exit(1)
    
    url = sys.argv[1]
    outdir = sys.argv[2] if len(sys.argv) > 2 else '/tmp/read-later-video'
    os.makedirs(outdir, exist_ok=True)
    
    # Step 1: Metadata
    print(f"[fetch-video] 获取元数据: {url}", file=sys.stderr)
    meta = get_metadata(url)
    
    title = meta.get('title', '') or ''
    author = meta.get('uploader', '') or meta.get('channel', '') or ''
    description = meta.get('description', '') or ''
    duration_sec = meta.get('duration', 0) or 0
    duration = f"{int(duration_sec)//60}:{int(duration_sec)%60:02d}" if duration_sec else ''
    
    print(f"[fetch-video] 标题: {title}", file=sys.stderr)
    print(f"[fetch-video] 作者: {author}", file=sys.stderr)
    print(f"[fetch-video] 时长: {duration}", file=sys.stderr)
    
    transcript = ''
    frames_dir = ''
    source = 'none'
    tags = []
    published_date = ''

    # Douyin short links often need fresh cookies for yt-dlp, but the page
    # itself can expose reliable title/author/chapter summary.
    douyin_page = fetch_douyin_page_json(url)
    if douyin_page:
        title = douyin_page.get('title') or title
        author = douyin_page.get('author') or author
        description = douyin_page.get('description') or description
        duration = douyin_page.get('duration') or duration
        tags = douyin_page.get('tags') or []
        published_date = douyin_page.get('published_date') or ''
        if douyin_page.get('chapter_summary'):
            transcript = douyin_page['chapter_summary']
            source = 'douyin_page'
            print(f"[fetch-video] 抖音页面摘要获取成功 ({len(transcript)} 字符)", file=sys.stderr)

    # Step 2: Try subtitles first
    
    video_id = get_video_id(url)
    if not transcript and video_id:
        print("[fetch-video] 尝试 youtube-transcript-api...", file=sys.stderr)
        transcript = get_youtube_transcript(video_id)
        if transcript:
            source = 'subtitle'
            print(f"[fetch-video] 字幕获取成功 ({len(transcript)} 字符)", file=sys.stderr)
    
    if not transcript:
        print("[fetch-video] 尝试 yt-dlp 字幕下载...", file=sys.stderr)
        transcript = get_subtitle_via_ytdlp(url, outdir)
        if transcript:
            source = 'subtitle'
            print(f"[fetch-video] 字幕下载成功 ({len(transcript)} 字符)", file=sys.stderr)
    
    # Step 3: No subtitle → download video → extract audio → Whisper
    video_file = None
    if not transcript:
        print("[fetch-video] 无字幕，尝试下载视频...", file=sys.stderr)
        video_file = download_video(url, outdir)
        
        if not video_file:
            # Try Playwright for Douyin
            print("[fetch-video] 尝试 Playwright 下载...", file=sys.stderr)
            video_file = download_video_playwright(url, outdir)
        
        if video_file:
            print(f"[fetch-video] 视频下载成功，提取音频...", file=sys.stderr)
            audio_file = extract_audio(video_file, outdir)
            
            if audio_file:
                print(f"[fetch-video] 音频提取成功，Whisper 转录中...", file=sys.stderr)
                transcript = transcribe_audio(audio_file)
                if transcript:
                    source = 'whisper'
                    print(f"[fetch-video] Whisper 转录成功 ({len(transcript)} 字符)", file=sys.stderr)
                else:
                    print("[fetch-video] Whisper 转录失败", file=sys.stderr)
                
                # Clean up audio
                os.remove(audio_file)
            
            # If still no transcript, try frames
            if not transcript:
                print("[fetch-video] 尝试截帧...", file=sys.stderr)
                frames_dir = extract_frames(video_file, outdir)
                if frames_dir:
                    source = 'frames'
            
            # Clean up video
            if os.path.exists(video_file):
                os.remove(video_file)
    
    # Step 4: Fallback to metadata
    if not transcript and source == 'none':
        source = 'metadata'
        transcript = description
        print("[fetch-video] 使用描述信息兜底", file=sys.stderr)
    
    result = {
        'title': title,
        'author': author,
        'description': description,
        'duration': duration,
        'transcript': transcript,
        'frames_dir': frames_dir if source == 'frames' else '',
        'source': source,
        'tags': tags,
        'published_date': published_date,
    }
    
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
