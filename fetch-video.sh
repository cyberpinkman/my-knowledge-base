#!/bin/bash
# 视频内容抓取脚本
# 用法: ./fetch-video.sh <url> <output-dir>
#
# 输出 JSON 到 stdout:
#   { "title": "...", "author": "...", "description": "...",
#     "duration": "...", "transcript": "...", "frames_dir": "...", "source": "subtitle|metadata|none" }

set -euo pipefail

URL="$1"
OUTDIR="${2:-/tmp/my-knowledge-base-video}"
mkdir -p "$OUTDIR"

RESULT_FILE="$OUTDIR/result.json"
METADATA_FILE="$OUTDIR/metadata.json"
TRANSCRIPT_FILE="$OUTDIR/transcript.txt"
FRAMES_DIR="$OUTDIR/frames"

# --- 第一步: 用 yt-dlp 获取元数据 ---
echo "[fetch-video] 获取元数据: $URL" >&2

yt-dlp --dump-json --no-download \
  --no-warnings \
  "$URL" > "$METADATA_FILE" 2>/dev/null || {
    # yt-dlp 失败，返回空结果
    echo '{"title":"","author":"","description":"","duration":"","transcript":"","frames_dir":"","source":"none"}'
    exit 0
  }

# 提取关键字段
TITLE=$(python3 -c "import json,sys; d=json.load(open('$METADATA_FILE')); print(d.get('title',''))" 2>/dev/null || echo "")
AUTHOR=$(python3 -c "import json,sys; d=json.load(open('$METADATA_FILE')); print(d.get('uploader') or d.get('channel',''))" 2>/dev/null || echo "")
DESCRIPTION=$(python3 -c "import json,sys; d=json.load(open('$METADATA_FILE')); print(d.get('description',''))" 2>/dev/null || echo "")
DURATION=$(python3 -c "import json,sys; d=json.load(open('$METADATA_FILE')); s=d.get('duration',0); print(f'{int(s)//60}:{int(s)%60:02d}')" 2>/dev/null || echo "")

echo "[fetch-video] 标题: $TITLE" >&2
echo "[fetch-video] 作者: $AUTHOR" >&2
echo "[fetch-video] 时长: $DURATION" >&2

# --- 第二步: 尝试获取字幕 ---
TRANSCRIPT=""
SOURCE="none"

# 方法 1: youtube-transcript-api (对 YouTube 最有效)
VIDEO_ID=""
if [[ "$URL" =~ (?:v=|youtu\.be/|shorts/|embed/)([a-zA-Z0-9_-]{11}) ]]; then
  VIDEO_ID="${BASH_REMATCH[1]}"
fi

if [[ -n "$VIDEO_ID" ]]; then
  echo "[fetch-video] 尝试 youtube-transcript-api..." >&2
  python3 -c "
from youtube_transcript_api import YouTubeTranscriptApi
api = YouTubeTranscriptApi()
try:
    result = api.fetch('$VIDEO_ID', languages=['zh-Hans','zh','en'])
    text = ' '.join(seg.text for seg in result)
    print(text)
except:
    try:
        result = api.fetch('$VIDEO_ID')
        text = ' '.join(seg.text for seg in result)
        print(text)
    except:
        pass
" > "$TRANSCRIPT_FILE" 2>/dev/null || true

  if [[ -s "$TRANSCRIPT_FILE" ]]; then
    TRANSCRIPT=$(cat "$TRANSCRIPT_FILE")
    SOURCE="subtitle"
    echo "[fetch-video] 字幕获取成功 ($(echo "$TRANSCRIPT" | wc -c | tr -d ' ') 字符)" >&2
  fi
fi

# 方法 2: yt-dlp 下载字幕文件 (对 B站等非 YouTube 平台)
if [[ -z "$TRANSCRIPT" ]]; then
  echo "[fetch-video] 尝试 yt-dlp 字幕下载..." >&2
  yt-dlp --write-auto-sub --write-sub --sub-format srt --sub-lang zh,en,zh-Hans \
    --skip-download --no-warnings \
    -o "$OUTDIR/subs" "$URL" 2>/dev/null || true

  # 找到字幕文件
  SUB_FILE=$(find "$OUTDIR" -name "subs*.srt" -o -name "subs*.vtt" 2>/dev/null | head -1)
  if [[ -n "$SUB_FILE" && -s "$SUB_FILE" ]]; then
    # 去掉时间戳，只留文本
    TRANSCRIPT=$(grep -v '^[0-9]' "$SUB_FILE" | grep -v '^$' | grep -v '\-\->' | grep -v 'WEBVTT' | grep -v 'Kind:' | grep -v 'Language:' | sort -u | tr '\n' ' ' | sed 's/<[^>]*>//g')
    SOURCE="subtitle"
    echo "[fetch-video] 字幕下载成功 ($(echo "$TRANSCRIPT" | wc -c | tr -d ' ') 字符)" >&2
  fi
fi

# --- 第三步: 没字幕则下载低画质视频截帧 ---
FRAMES_RESULT=""

if [[ -z "$TRANSCRIPT" ]]; then
  echo "[fetch-video] 无字幕，尝试下载视频截帧..." >&2

  VIDEO_FILE="$OUTDIR/video.mp4"
  yt-dlp -f "worst[ext=mp4]/worst" \
    --max-filesize 100M \
    --no-warnings \
    -o "$VIDEO_FILE" "$URL" 2>/dev/null || true

  if [[ -f "$VIDEO_FILE" ]]; then
    mkdir -p "$FRAMES_DIR"
    # 每30秒截一帧，最多10帧
    ffmpeg -i "$VIDEO_FILE" -vf "fps=1/30" -frames:v 10 \
      "$FRAMES_DIR/frame_%03d.jpg" -y -loglevel error 2>/dev/null || true

    FRAME_COUNT=$(find "$FRAMES_DIR" -name "frame_*.jpg" 2>/dev/null | wc -l | tr -d ' ')
    if [[ "$FRAME_COUNT" -gt 0 ]]; then
      FRAMES_RESULT="$FRAMES_DIR"
      SOURCE="frames"
      echo "[fetch-video] 截取了 $FRAME_COUNT 帧" >&2
    fi

    # 清理视频文件节省空间
    rm -f "$VIDEO_FILE"
  fi
fi

# 如果连截帧都没有，至少用元数据中的 description
if [[ -z "$TRANSCRIPT" && -z "$FRAMES_RESULT" ]]; then
  SOURCE="metadata"
  TRANSCRIPT="$DESCRIPTION"
  echo "[fetch-video] 无法获取字幕和截帧，使用描述信息" >&2
fi

# --- 输出 JSON 结果 ---
python3 -c "
import json
result = {
    'title': '''$TITLE'''.replace(\"'\", \"'\"), 
    'author': '''$AUTHOR'''.replace(\"'\", \"'\"),
    'duration': '$DURATION',
    'transcript': '''$TRANSCRIPT'''.replace(\"'\", \"'\"),
    'frames_dir': '$FRAMES_RESULT',
    'source': '$SOURCE'
}
# 用 json.dumps 保证安全
print(json.dumps(result, ensure_ascii=False))
" 2>/dev/null || echo '{"title":"","author":"","duration":"","transcript":"","frames_dir":"","source":"none"}'
