#!/bin/bash
# 智能稍后阅读 - 报告生成脚本
# 用法: ./report.sh [daily|weekly|unread]

set -e

WORKSPACE="$HOME/.openclaw/workspace/read-later"
DB="$WORKSPACE/articles.db"

# 统计未读文章
stats() {
  echo "📊 **稍后阅读统计**"
  echo ""
  
  local total=$(sqlite3 "$DB" "SELECT COUNT(*) FROM articles;")
  local unread=$(sqlite3 "$DB" "SELECT COUNT(*) FROM articles WHERE is_read = 0;")
  local unreported=$(sqlite3 "$DB" "SELECT COUNT(*) FROM articles WHERE is_reported = 0;")
  
  echo "- 总收录: $total 篇"
  echo "- 未读: $unread 篇"
  echo "- 未报告: $unreported 篇"
  echo ""
  
  # 按分类统计
  echo "📂 **分类分布**"
  sqlite3 "$DB" "SELECT COALESCE(category, '未分类'), COUNT(*) FROM articles GROUP BY category ORDER BY COUNT(*) DESC;" | while read -r line; do
    echo "  - $line 篇"
  done
  
  # 按来源统计
  echo ""
  echo "🔗 **来源分布**"
  sqlite3 "$DB" "SELECT source_type, COUNT(*) FROM articles GROUP BY source_type ORDER BY COUNT(*) DESC;" | while read -r line; do
    echo "  - $line 篇"
  done
}

# 列出未报告的文章（用于 AI 生成报告）
list_unreported() {
  sqlite3 -json "$DB" "SELECT id, url, title, summary, category, tags, source_type, created_at FROM articles WHERE is_reported = 0 AND summary IS NOT NULL ORDER BY created_at DESC;"
}

# 列出未处理的文章（用于心跳检查）
list_unprocessed() {
  sqlite3 -json "$DB" "SELECT id, url, source_type, created_at FROM articles WHERE summary IS NULL ORDER BY created_at ASC LIMIT 5;"
}

# 标记为已报告
mark_reported() {
  local ids="$1"
  sqlite3 "$DB" "UPDATE articles SET is_reported = 1 WHERE id IN ($ids);"
}

# 标记为已读
mark_read() {
  local id="$1"
  sqlite3 "$DB" "UPDATE articles SET is_read = 1, read_at = datetime('now') WHERE id = $id;"
}

case "$1" in
  stats)
    stats
    ;;
  list)
    list_unreported
    ;;
  list-unprocessed)
    list_unprocessed
    ;;
  mark-reported)
    mark_reported "$2"
    echo "✅ 已标记为已报告"
    ;;
  mark-read)
    mark_read "$2"
    echo "✅ 已标记为已读"
    ;;
  *)
    stats
    ;;
esac
