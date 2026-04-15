#!/usr/bin/env python3
"""
视频内容抓取脚本
用法: python3 fetch-video.py <url> [output-dir]

输出 JSON 到 stdout:
  { "title", "author", "description", "duration", "transcript", "frames_dir", "source" }

source 取值:
  "subtitle"  - 获取到字幕/转录
  "frames"    - 无字幕，截取了视频帧
  "metadata"  - 只有元数据
  "none"      - 全部失败
"""

import json
import os
import re
import subprocess
import sys
import glob
import shutil


def run(cmd, **kwargs):
    """Run command, return CompletedProcess."""
    return subprocess.run(cmd, capture_output=True, text=True, timeout=kwargs.pop('timeout', 120), **kwargs)


def get_video_id(url):
    """Extract YouTube video ID."""
    m = re.search(r'(?:v=|youtu\.be/|shorts/|embed/|live/)([a-zA-Z0-9_-]{11})', url)
    return m.group(1) if m else None


def get_metadata(url):
    """Get video metadata via yt-dlp --dump-json."""
    try:
        r = run(['yt-dlp', '--dump-json', '--no-download', '--no-warnings', url], timeout=30)
        if r.returncode == 0:
            return json.loads(r.stdout)
    except Exception:
        pass
    return {}


def get_youtube_transcript(video_id):
    """Get transcript via youtube-transcript-api."""
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
        api = YouTubeTranscriptApi()
        # 优先中文
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
    """Download subtitle files via yt-dlp."""
    try:
        r = run([
            'yt-dlp', '--write-auto-sub', '--write-sub',
            '--sub-format', 'srt/vtt',
            '--sub-lang', 'zh-Hans,zh,en',
            '--skip-download', '--no-warnings',
            '-o', os.path.join(outdir, 'subs'),
            url
        ], timeout=30)
        
        # Find subtitle files
        for ext in ('*.srt', '*.vtt'):
            files = glob.glob(os.path.join(outdir, f'subs*{ext}'))
            for f in files:
                text = parse_subtitle(f)
                if text:
                    return text
    except Exception:
        pass
    return ''


def parse_subtitle(filepath):
    """Parse SRT/VTT file, extract text only."""
    lines = []
    with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
        for line in f:
            line = line.strip()
            # Skip timestamps, numbers, headers
            if not line:
                continue
            if re.match(r'^\d+$', line):
                continue
            if '-->' in line:
                continue
            if line.startswith(('WEBVTT', 'Kind:', 'Language:', 'NOTE')):
                continue
            # Remove HTML tags
            line = re.sub(r'<[^>]*>', '', line)
            lines.append(line)
    
    if not lines:
        return ''
    
    # Deduplicate consecutive lines
    deduped = [lines[0]]
    for line in lines[1:]:
        if line != deduped[-1]:
            deduped.append(line)
    
    return ' '.join(deduped)


def download_and_extract_frames(url, outdir):
    """Download low-quality video and extract frames with ffmpeg."""
    video_file = os.path.join(outdir, 'video.mp4')
    frames_dir = os.path.join(outdir, 'frames')
    
    try:
        # Download low quality (B站等平台音视频分离，需要合并)
        r = run([
            'yt-dlp', '-f', 'worstvideo+worstaudio/worst',
            '--merge-output-format', 'mp4',
            '--max-filesize', '100M',
            '--no-warnings',
            '-o', video_file, url
        ], timeout=180)
        
        if not os.path.exists(video_file):
            return ''
        
        os.makedirs(frames_dir, exist_ok=True)
        
        # Extract frames: one every 30 seconds, max 10
        run([
            'ffmpeg', '-i', video_file,
            '-vf', 'fps=1/30',
            '-frames:v', '10',
            os.path.join(frames_dir, 'frame_%03d.jpg'),
            '-y', '-loglevel', 'error'
        ], timeout=60)
        
        # Cleanup video
        os.remove(video_file)
        
        frames = glob.glob(os.path.join(frames_dir, 'frame_*.jpg'))
        if frames:
            return frames_dir
            
    except Exception as e:
        print(f"[fetch-video] 截帧失败: {e}", file=sys.stderr)
    
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
    
    # Step 2: Transcript (subtitle)
    transcript = ''
    source = 'none'
    
    # Method 1: youtube-transcript-api
    video_id = get_video_id(url)
    if video_id:
        print("[fetch-video] 尝试 youtube-transcript-api...", file=sys.stderr)
        transcript = get_youtube_transcript(video_id)
        if transcript:
            source = 'subtitle'
            print(f"[fetch-video] 字幕获取成功 ({len(transcript)} 字符)", file=sys.stderr)
    
    # Method 2: yt-dlp subtitle download (for Bilibili etc.)
    if not transcript:
        print("[fetch-video] 尝试 yt-dlp 字幕下载...", file=sys.stderr)
        transcript = get_subtitle_via_ytdlp(url, outdir)
        if transcript:
            source = 'subtitle'
            print(f"[fetch-video] 字幕下载成功 ({len(transcript)} 字符)", file=sys.stderr)
    
    # Step 3: Download & extract frames if no transcript
    frames_dir = ''
    if not transcript:
        print("[fetch-video] 无字幕，尝试下载视频截帧...", file=sys.stderr)
        frames_dir = download_and_extract_frames(url, outdir)
        if frames_dir:
            source = 'frames'
    
    # Fallback: use description
    if not transcript and not frames_dir:
        source = 'metadata'
        transcript = description
        print("[fetch-video] 无法获取字幕和截帧，使用描述信息", file=sys.stderr)
    
    result = {
        'title': title,
        'author': author,
        'description': description,
        'duration': duration,
        'transcript': transcript,
        'frames_dir': frames_dir,
        'source': source,
    }
    
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
