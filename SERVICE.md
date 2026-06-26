# my-knowledge-base 服务

## 概述

通过微信、命令行或其他入口收录想学习的链接/视频/PDF，由抓取器提取内容，LLM 做结构化分析后写入 Obsidian；长期价值内容可同步到 gbrain。

## 数据库位置

`~/.openclaw/workspace/my-knowledge-base/articles.db`

## 处理流程

### 1. 收到链接时

当收到 URL 或本地 PDF 时：

```bash
# 唯一正常入口：入库、抓取、LLM 分析、写入 Obsidian 都由它继续调度
~/.openclaw/workspace/my-knowledge-base/process.sh "<url-or-pdf-path>"

# 高价值内容可手动或批量同步到 gbrain
```

不要为了处理普通链接而手动运行抓取脚本、手动生成摘要、再手写 SQLite `UPDATE`。底层脚本只用于调试单个阶段。

### 2. 链接类型处理

| 类型 | 链接特征 | 抓取方式 |
|------|---------|---------|
| 普通网页 | 其他 URL | Playwright 抓取正文，默认删除临时截图 |
| 微信公众号 | mp.weixin.qq.com | 走通用网页抓取，可能受登录/反爬影响 |
| YouTube/B站/抖音 | youtube.com, bilibili.com, douyin.com 等 | 优先字幕/转录；抖音无章节摘要时尝试下载音频并用 Whisper 转写，最后才用平台元数据兜底 |
| PDF | .pdf URL、本地 .pdf 文件 | 提取文本后分析 |
| Twitter/X | twitter.com, x.com | 当前统一入口走通用网页抓取，可能失败 |
| 小红书 | xiaohongshu.com, xhslink.com | 当前统一入口走通用网页抓取，强反爬时需要人工补充 |

当前不支持图片/OCR 自动解析。

### 3. LLM 配置

不要在文档、git 提交或日志里写真实 API key。只配置本机环境变量。

MiniMax 示例：

```bash
export MY_KNOWLEDGE_BASE_LLM_PROVIDER=minimax
export MINIMAX_CN_API_KEY="your_minimax_key_here"
```

读取优先级：

1. `MY_KNOWLEDGE_BASE_LLM_COMMAND`
2. MiniMax：`MY_KNOWLEDGE_BASE_LLM_PROVIDER=minimax` 或检测到 `MINIMAX_API_KEY` / `MINIMAX_CN_API_KEY` / `MINIMAX_SUBSCRIPTION_KEY`
3. OpenAI：检测到 `OPENAI_API_KEY`
4. 本地规则摘要

常用开关：

```bash
export MY_KNOWLEDGE_BASE_FORCE=1              # 强制重新处理已分析条目
export MY_KNOWLEDGE_BASE_INBOX_ONLY=1         # 只入库，不抓取/分析
export MY_KNOWLEDGE_BASE_SYNC_GBRAIN=1        # 当前处理条目尝试同步到 gbrain
export MY_KNOWLEDGE_BASE_KEEP_SCREENSHOTS=1   # 调试网页抓取时保留截图；默认删除
export MY_KNOWLEDGE_BASE_VIDEO_FETCH_TIMEOUT=1800
export MY_KNOWLEDGE_BASE_WHISPER_MODEL=tiny   # 视频无字幕时的本地转写模型；可改为 base 提高质量
export MY_KNOWLEDGE_BASE_WHISPER_TIMEOUT=900
export MY_KNOWLEDGE_BASE_DOUYIN_MIN_CHAPTER_SUMMARY_CHARS=300
```

改名迁移期，脚本会把历史环境变量作为 fallback 读取；新配置只使用 `MY_KNOWLEDGE_BASE_*`。

### 4. 历史 Obsidian 修复

早期版本可能已经把内容写入 `articles.db`，但缺少对应 Obsidian Markdown。修复时只从 DB 读取已有 `title` / `summary` / `original_content`，不重新抓取原链接，也不重新调用 LLM：

```bash
~/.openclaw/workspace/my-knowledge-base/repair_obsidian.py --all-missing --dry-run
~/.openclaw/workspace/my-knowledge-base/repair_obsidian.py --all-missing

# 迁移旧笔记尾注里的历史项目名
~/.openclaw/workspace/my-knowledge-base/repair_obsidian.py --migrate-footer

# 默认只扫描 Obsidian/稍后阅读；确认需要全 vault 扫描时再加
~/.openclaw/workspace/my-knowledge-base/repair_obsidian.py --migrate-footer --all-vault
```

### 5. 分类标准

- `tech` - 技术相关（编程、AI、工具）
- `business` - 商业、产品、创业
- `design` - 设计、视觉、用户体验
- `life` - 生活、旅行、美食
- `news` - 时事、行业动态
- `other` - 其他

### 6. 生成报告

当用户说 "给我报告" 或类似指令时：

```bash
# 获取未报告的文章列表
~/.openclaw/workspace/my-knowledge-base/report.sh list

# AI 处理后，标记为已报告
~/.openclaw/workspace/my-knowledge-base/report.sh mark-reported "1,2,3"
```

## 触发词

- 收到链接 → 收录并处理
- "给我报告" / "整理一下" / "summary" → 输出未读内容汇总
- "已读 X" → 标记某篇文章已读

## 注意事项

1. **微信文章**：可能无法直接抓取，需要告诉用户
2. **小红书**：有反爬，可能需要用户粘贴内容
3. **去重**：同一链接不重复收录
4. **失败处理**：抓取失败的链接记录到 fetch_failures 表
5. **临时截图**：网页抓取截图默认删除；只有 `MY_KNOWLEDGE_BASE_KEEP_SCREENSHOTS=1` 时保留到临时目录

## 未来扩展

- [ ] 迁移到 Notion 数据库
- [ ] 定时自动报告（每周日晚上）
- [ ] 阅读时长估算
- [ ] 相关文章推荐

## gbrain 同步

`sync_to_gbrain.py` 会给现有 `articles.db` 自动补充同步字段，并把已摘要、未同步、且属于 `tech,business,design` 的条目写入 gbrain：

```bash
~/.openclaw/workspace/my-knowledge-base/sync_to_gbrain.py --dry-run
~/.openclaw/workspace/my-knowledge-base/sync_to_gbrain.py
~/.openclaw/workspace/my-knowledge-base/sync_to_gbrain.py --article-id 67
```

同步页面写到 `media/articles/<source_type>-<article_id>-<url_hash>`。这是单向同步，my-knowledge-base 继续保留 inbox/read 状态，gbrain 只接收长期知识页。

项目改名后，已同步过的 gbrain 页面可用当前 frontmatter 和标签重写，避免历史页和新页元数据分裂：

```bash
~/.openclaw/workspace/my-knowledge-base/sync_to_gbrain.py --migrate-synced-metadata --dry-run
~/.openclaw/workspace/my-knowledge-base/sync_to_gbrain.py --migrate-synced-metadata
```

人工闸门：

```bash
~/.openclaw/workspace/my-knowledge-base/mark.py list --unmarked --limit 20
~/.openclaw/workspace/my-knowledge-base/mark.py value 12 18 23
~/.openclaw/workspace/my-knowledge-base/sync_to_gbrain.py --only-marked --limit 10
~/.openclaw/workspace/my-knowledge-base/mark.py clear 18
```

`--only-marked` 会忽略分类，只同步 `long_term_value = 1` 且已有摘要、未同步的条目。默认同步仍保留 `tech,business,design` 分类规则，适合批量引导；长期使用建议优先走人工标记。

`idea.py analyze` 和新增灵感时的关联搜索会优先调用 `gbrain query`。如果 gbrain 被锁、不可用或没有结果，会回退到 Obsidian `稍后阅读/` 的关键词扫描，避免灵感记录流程被 gbrain 状态阻塞。
