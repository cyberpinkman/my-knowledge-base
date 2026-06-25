# My Knowledge Base | 我的知识库

AI 驱动的个人知识管理系统：收录链接、视频和 PDF，抓取内容后用 LLM 做结构化分析，写入 Obsidian，并把长期价值内容同步到 gbrain。

## 功能

- **内容收录** — 通过 `process.sh` 收录 URL 或本地 PDF，抓取、分析、分类并写入知识库
  - 普通网页、微信公众号等网页：Playwright 抓取正文
  - YouTube、B站、抖音：优先提取字幕/转录/平台元数据
  - 本地 PDF 或 PDF URL：提取文本后分析
  - Twitter/X、小红书等强反爬平台：目前走通用网页抓取，可能失败或需要人工补充内容
- **灵感管理** — 随手记录 idea，自动搜索知识库关联内容，评估可行性
- **知识关联** — 构建 idea ↔ 知识的双向链接
- **Obsidian 集成** — 所有内容以 Markdown 写入 Obsidian Vault

当前不支持图片内容解析；图片/OCR 是后续扩展方向。

## 架构

```
用户 → 发送链接/灵感
  ↓
process.sh → 检测类型、收录数据库
  ↓
fetch-video.py / fetch-pdf.py / fetch-screenshot.js → 抓取内容
  ↓
LLM 结构化分析：摘要 / 原文事实 / 模型推断 / 外部背景 / 待查证
  ↓
write-to-obsidian.py → 写入 Obsidian Vault
sync_to_gbrain.py → 筛选高价值条目并同步到 gbrain
idea.py → 创建灵感 / 分析关联
  ↓
Obsidian Vault (~/Documents/我的知识库/)
  ├── 稍后阅读/{技术,商业,设计,生活,新闻,视频,其他}/
  ├── 灵感/{待探索,进行中,已搁置}/
  └── 关联图谱/
```

## 依赖

- **yt-dlp** — 视频/音频下载（YouTube/B站/抖音等 1800+ 平台）
- **youtube-transcript-api** — YouTube 字幕提取
- **ffmpeg** — 视频截帧、音频处理
- **Playwright** — 网页截图抓取（微信公众号等）
- **SQLite** — 文章数据库
- **Obsidian** — 知识库前端（本地 Markdown）

## 安装

```bash
pip3 install yt-dlp youtube-transcript-api
brew install ffmpeg
npm install playwright
```

## 使用

### 配置 LLM

默认优先级：

1. `READ_LATER_LLM_COMMAND`
2. MiniMax：`READ_LATER_LLM_PROVIDER=minimax` 或检测到 MiniMax key
3. OpenAI：检测到 `OPENAI_API_KEY`
4. 无可用 LLM 时回退到本地规则摘要

MiniMax 示例：

```bash
export READ_LATER_LLM_PROVIDER=minimax
export MINIMAX_CN_API_KEY="your_minimax_key_here"
```

不要把真实 API key 写进 README、提交到 git，或放进公开日志。这里的 `your_minimax_key_here` 只是占位符。

常用环境变量：

```bash
export OBSIDIAN_VAULT_PATH="$HOME/Documents/我的知识库"
export READ_LATER_FORCE=1              # 已处理条目也重新抓取/分析
export READ_LATER_INBOX_ONLY=1         # 只收录，不立即处理
export READ_LATER_SYNC_GBRAIN=1        # 当前条目处理后尝试同步到 gbrain
export READ_LATER_KEEP_SCREENSHOTS=1   # 调试网页抓取时保留临时截图；默认会删除
export READ_LATER_LLM_TIMEOUT=120
export READ_LATER_LLM_MAX_OUTPUT_TOKENS=2200
```

可选 MiniMax 变量：

```bash
export READ_LATER_MINIMAX_MODEL="MiniMax-M3"
export READ_LATER_MINIMAX_BASE_URL="https://api.minimaxi.com/v1"
export READ_LATER_MINIMAX_API_KEY_ENV="MINIMAX_CN_API_KEY"
```

### 收录链接
```bash
./process.sh "https://example.com/article"
./process.sh ~/Downloads/paper.pdf
python3 fetch-video.py "https://youtube.com/watch?v=xxx" /tmp/output
```

### 写入知识库
```bash
python3 write-to-obsidian.py \
  --title "文章标题" --url "https://..." \
  --source-type web --category tech \
  --tags "AI,Python" --summary "摘要内容"
```

### 同步到 gbrain
```bash
# 预览将同步哪些条目
python3 sync_to_gbrain.py --dry-run

# 默认只同步 tech,business,design 中已有摘要且未同步的条目
python3 sync_to_gbrain.py

# 精确同步某一条 articles.id
python3 sync_to_gbrain.py --article-id 67

# 人工标记长期价值内容，只同步标记过的条目
python3 mark.py value 12 18 23
python3 sync_to_gbrain.py --only-marked --limit 10

# 重试失败条目，或指定分类
python3 sync_to_gbrain.py --retry-failed
python3 sync_to_gbrain.py --categories tech,business,design,news
```

### 灵感管理
```bash
python3 idea.py add --title "我的idea" --description "描述"
python3 idea.py list
python3 idea.py analyze --title "我的idea"
```

灵感关联会优先调用 `gbrain query` 检索长期脑库；如果 gbrain 不可用或没有结果，会自动回退到 Obsidian `稍后阅读/` 目录的本地关键词搜索。

## License

MIT
