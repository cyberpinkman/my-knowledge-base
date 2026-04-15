# My Knowledge Base | 我的知识库

AI 驱动的个人知识管理系统：自动收录链接/图片/文档，记录灵感，构建知识关联。

## 功能

- **链接收录** — 发送 URL 自动抓取、总结、分类存入知识库
  - 微信公众号、普通网页（Playwright 抓取）
  - YouTube、B站、抖音（字幕提取 + 视频截帧）
  - Twitter/X、小红书
- **灵感管理** — 随手记录 idea，自动搜索知识库关联内容，评估可行性
- **知识关联** — 构建 idea ↔ 知识的双向链接
- **Obsidian 集成** — 所有内容以 Markdown 写入 Obsidian Vault

## 架构

```
用户 → 发送链接/灵感
  ↓
process.sh → 检测类型、收录数据库
  ↓
fetch-video.py / fetch-screenshot.js → 抓取内容
  ↓
AI 生成摘要 + 分类 + 标签
  ↓
write-to-obsidian.py → 写入 Obsidian Vault
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

### 收录链接
```bash
./process.sh "https://example.com/article"
python3 fetch-video.py "https://youtube.com/watch?v=xxx" /tmp/output
```

### 写入知识库
```bash
python3 write-to-obsidian.py \
  --title "文章标题" --url "https://..." \
  --source-type web --category tech \
  --tags "AI,Python" --summary "摘要内容"
```

### 灵感管理
```bash
python3 idea.py add --title "我的idea" --description "描述"
python3 idea.py list
python3 idea.py analyze --title "我的idea"
```

## License

MIT
