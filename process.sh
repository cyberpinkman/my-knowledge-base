#!/bin/bash
# 智能稍后阅读 - 链接处理脚本
# 用法: ./process.sh "<url>"

set -e

WORKSPACE="$HOME/.openclaw/workspace/read-later"
DB="$WORKSPACE/articles.db"
LOG="$WORKSPACE/process.log"

# 初始化数据库
init_db() {
  if [[ ! -f "$DB" ]]; then
    sqlite3 "$DB" < "$WORKSPACE/schema.sql"
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] 数据库初始化完成" >> "$LOG"
  fi
}

# 检测链接类型
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
  else
    echo "web"
  fi
}

# 添加文章到数据库（待处理状态）
add_article() {
  local url="$1"
  local source_type="$2"
  
  # 检查是否已存在
  local exists=$(sqlite3 "$DB" "SELECT COUNT(*) FROM articles WHERE url = '$url';")
  if [[ "$exists" -gt 0 ]]; then
    echo "文章已存在，跳过"
    return 0
  fi
  
  sqlite3 "$DB" "INSERT INTO articles (url, source_type) VALUES ('$url', '$source_type');"
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] 新增文章: $url (类型: $source_type)" >> "$LOG"
}

# 主流程
main() {
  local url="$1"
  
  if [[ -z "$url" ]]; then
    echo "用法: $0 <url>"
    exit 1
  fi
  
  init_db
  
  local source_type=$(detect_source_type "$url")
  add_article "$url" "$source_type"
  
  echo "✅ 链接已收录: $url"
  echo "   类型: $source_type"
  echo "   状态: 待处理（将由 AI 进行内容抓取和总结）"
}

main "$@"
