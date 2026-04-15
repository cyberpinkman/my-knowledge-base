# 智能稍后阅读服务

## 概述

小粉通过微信发送感兴趣的链接，由 AI 阅读总结后分类存储，按需生成报告。

## 数据库位置

`~/.openclaw/workspace/read-later/articles.db`

## 处理流程

### 1. 收到链接时

当收到微信消息包含 URL 时：

```bash
# 1. 先用 process.sh 收录链接
~/.openclaw/workspace/read-later/process.sh "<url>"

# 2. 尝试抓取内容（不同平台不同处理）
# 3. 生成摘要
# 4. 更新数据库：title, summary, category, tags
```

### 2. 链接类型处理

| 类型 | 链接特征 | 抓取方式 |
|------|---------|---------|
| 微信公众号 | mp.weixin.qq.com | web_fetch 可能失败，尝试提取标题后告知用户 |
| Twitter/X | twitter.com, x.com | web_fetch 抓取 |
| 小红书 | xiaohongshu.com, xhslink.com | 通常有反爬，可能需要用户手动粘贴内容 |
| 普通网页 | 其他 | web_fetch 抓取 |

### 3. 分类标准

- `tech` - 技术相关（编程、AI、工具）
- `business` - 商业、产品、创业
- `design` - 设计、视觉、用户体验
- `life` - 生活、旅行、美食
- `news` - 时事、行业动态
- `other` - 其他

### 4. 生成报告

当用户说 "给我报告" 或类似指令时：

```bash
# 获取未报告的文章列表
~/.openclaw/workspace/read-later/report.sh list

# AI 处理后，标记为已报告
~/.openclaw/workspace/read-later/report.sh mark-reported "1,2,3"
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

## 未来扩展

- [ ] 迁移到 Notion 数据库
- [ ] 定时自动报告（每周日晚上）
- [ ] 阅读时长估算
- [ ] 相关文章推荐
