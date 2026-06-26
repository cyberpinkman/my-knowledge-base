#!/bin/bash
# my-knowledge-base - 统一收录入口
# 用法:
#   ./process.sh "https://example.com/article"     # URL 模式
#   ./process.sh "/path/to/local/file.pdf"         # 本地文件模式 (自动识别 .pdf)
#
# 行为:
#   - URL: detect_source_type → 写入 SQLite → pipeline 抓取/总结/写 Obsidian
#   - 本地 .pdf 文件: 写入 file:// URL → pipeline 调用 fetch-pdf.py --local
#   - 同一 URL/file 已存在 → 复用原 ID, 未处理则继续补处理
#   - MY_KNOWLEDGE_BASE_INBOX_ONLY=1 可只收录不处理
set -e

WORKSPACE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DB="$WORKSPACE/articles.db"
LOG="$WORKSPACE/process.log"
DB_PY="$WORKSPACE/db.py"
PIPELINE_PY="$WORKSPACE/pipeline.py"

env_value() {
  local suffix="$1"
  local current="MY_KNOWLEDGE_BASE_${suffix}"
  local legacy="READ_""LATER_${suffix}"

  if [[ -n "${!current+x}" ]]; then
    printf '%s' "${!current}"
    return
  fi
  if [[ -n "${!legacy+x}" ]]; then
    printf '%s' "${!legacy}"
  fi
}

# 初始化数据库 (复用 init_db 逻辑)
init_db() {
  if [[ ! -f "$DB" ]]; then
    sqlite3 "$DB" < "$WORKSPACE/schema.sql"
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] 数据库初始化完成" >> "$LOG"
  fi
}

# 检测链接类型 (URL 模式)
detect_source_type() {
  local url="$1"

  if [[ "$url" =~ mp\.weixin\.qq\.com ]]; then
    echo "wechat"
  elif [[ "$url" =~ youtube\.com|youtu\.be ]]; then
    echo "youtube"
  elif [[ "$url" =~ bilibili\.com|b23\.tv ]]; then
    echo "bilibili"
  elif [[ "$url" =~ douyin\.com ]]; then
    echo "douyin"
  elif [[ "$url" =~ xiaohongshu\.com|xhslink\.com ]]; then
    echo "xiaohongshu"
  elif [[ "$url" =~ twitter\.com|x\.com ]]; then
    echo "twitter"
  elif [[ "$url" =~ \.pdf($|\?|#) ]] || [[ "$url" =~ arxiv\.org/pdf/ ]]; then
    echo "pdf"
  else
    echo "web"
  fi
}

# 判断输入是否为本地文件路径
# 返回 0 = 是本地文件 (文件存在)
# 返回 1 = 是 URL
# 返回 2 = 看起来像本地路径 (无协议头, 是绝对路径或 ~ 开头) 但文件不存在 → main 应报错
is_local_path() {
  local input="$1"
  # 有协议头 → URL
  [[ "$input" =~ ^[a-zA-Z][a-zA-Z0-9+.-]*:// ]] && return 1
  # 文件存在 → 本地
  [[ -f "$input" ]] && return 0
  # 没协议头 + 看起来像绝对路径或 ~/... → 本地意图但文件不存在
  if [[ "$input" =~ ^/ ]] || [[ "$input" =~ ^~ ]]; then
    return 2
  fi
  # 既无协议头也不像本地路径 (如 "arxiv.org/pdf/...") → 当 URL 处理
  return 1
}

# 解析本地 .pdf → 标准化 file:// URL
local_to_file_url() {
  local file="$1"
  local abs
  abs=$(cd "$(dirname "$file")" && pwd)/$(basename "$file")
  echo "file://$abs"
}

# 主流程
main() {
  local input="$1"

  if [[ -z "$input" ]]; then
    echo "用法: $0 <url-or-local-path>"
    echo "  URL  示例: $0 https://example.com/article"
    echo "  文件示例: $0 ~/Downloads/paper.pdf"
    exit 1
  fi

  init_db

  local url=""
  local source_type=""
  local input_kind=""

  # 判断一次,根据返回值分别处理
  # 用 || rc=$? 模式避免 set -e 把 is_local_path 的非零返回当作失败
  local rc=0
  is_local_path "$input" || rc=$?

  # 本地文件模式的 .pdf 扩展名检查 (在 rc=0 和 rc=2 分支都需要)
  # rc=0: 文件存在但不是 .pdf
  # rc=2: 文件不存在且不是 .pdf 扩展名 → 提示"非 PDF 格式"比"文件不存在"更准确
  local input_lower
  input_lower=$(echo "$input" | tr '[:upper:]' '[:lower:]')
  local is_pdf_ext=false
  [[ "$input_lower" =~ \.pdf$ ]] && is_pdf_ext=true

  if [[ $rc -eq 0 ]]; then
    # 本地文件 (文件存在)
    input_kind="local"
    if [[ "$is_pdf_ext" != "true" ]]; then
      echo "❌ 本地文件目前仅支持 .pdf (其他格式待加): $input"
      exit 1
    fi
    url=$(local_to_file_url "$input")
    source_type="pdf"
  elif [[ $rc -eq 2 ]]; then
    # 看起来像本地路径但文件不存在 → 明确报错 (不要 fallback 到 URL)
    # 区分: 是 .pdf 路径还是其他格式, 给最贴近的提示
    if [[ "$is_pdf_ext" == "true" ]]; then
      echo "❌ PDF 文件不存在: $input"
      echo "   检查路径/文件名拼写,或用绝对路径重试"
    else
      echo "❌ 本地文件目前仅支持 .pdf (其他格式待加): $input"
      echo "   (该路径也不存在,但即便存在也不支持)"
    fi
    exit 1
  else
    # rc == 1 → URL
    input_kind="url"
    url="$input"
    source_type=$(detect_source_type "$url")
  fi

  # 用 db.py 插入 (parameterized, 安全)
  local result
  result=$(python3 "$DB_PY" insert "$url" "$source_type" 2>&1) || {
    echo "❌ DB 写入失败: $result"
    exit 1
  }

  local article_id=""
  if [[ "$result" == DUPLICATE* ]]; then
    article_id="${result#DUPLICATE:}"
    echo "⚠️  已收录过: $url"
    echo "   ID:   $article_id"
    echo "   类型: $source_type"
  else
    article_id="$result"
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] 新增: $url ($input_kind, $source_type)" >> "$LOG"

    echo "✅ 已收录: $url"
    echo "   ID:   $article_id"
    echo "   类型: $source_type"
    echo "   模式: $input_kind"
  fi

  if [[ "$(env_value INBOX_ONLY)" == "1" ]]; then
    echo "   状态: 待处理（MY_KNOWLEDGE_BASE_INBOX_ONLY=1）"
    exit 0
  fi

  echo "   状态: 开始抓取、总结并写入 Obsidian..."

  local pipeline_args=(python3 "$PIPELINE_PY" --id "$article_id")
  if [[ "$(env_value SYNC_GBRAIN)" == "1" ]]; then
    pipeline_args+=(--sync-gbrain)
  fi
  if [[ "$(env_value FORCE)" == "1" ]]; then
    pipeline_args+=(--force)
  fi

  local pipeline_result
  pipeline_result=$("${pipeline_args[@]}" 2>&1) || {
    echo "❌ 处理失败: $pipeline_result"
    exit 1
  }
  echo "✅ 处理完成: $pipeline_result"
}

main "$@"
