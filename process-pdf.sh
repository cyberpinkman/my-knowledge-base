#!/bin/bash
# 本地 PDF 快捷入口 — 已合并到 process.sh
# 保留此文件仅为向后兼容,实际逻辑调用 process.sh
set -e

SELF_DIR="$(cd "$(dirname "$0")" && pwd)"
exec "$SELF_DIR/process.sh" "$@"