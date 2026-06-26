-- my-knowledge-base 数据库结构
-- 路径: 仓库目录/articles.db

CREATE TABLE IF NOT EXISTS articles (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  
  -- 来源信息
  url TEXT NOT NULL UNIQUE,
  source_type TEXT NOT NULL,  -- 'wechat' | 'twitter' | 'xiaohongshu' | 'web' | 'youtube' | 'bilibili' | 'douyin' | 'other'
  
  -- 内容
  title TEXT,
  original_content TEXT,       -- 原始抓取内容
  summary TEXT,                -- AI 生成的摘要
  
  -- 分类
  category TEXT,               -- AI 自动分类: 'tech' | 'business' | 'life' | 'design' | 'news' | 'other'
  tags TEXT,                   -- JSON 数组: ["AI", "产品", "设计"]
  
  -- 元数据
  author TEXT,
  published_date TEXT,
  word_count INTEGER,
  
  -- 状态
  is_read INTEGER DEFAULT 0,   -- 0=未读, 1=已读
  is_reported INTEGER DEFAULT 0, -- 是否已在报告中输出过
  gbrain_slug TEXT,              -- 同步到 gbrain 后的页面 slug
  gbrain_synced_at TEXT,         -- 最近一次成功同步时间
  gbrain_sync_status TEXT,       -- NULL/''=未同步, synced=成功, failed=失败
  gbrain_sync_error TEXT,        -- 最近一次同步失败原因
  long_term_value INTEGER DEFAULT 0, -- 是否值得进入长期脑库
  gbrain_sync_mode TEXT,         -- manual=人工标记, heuristic=规则筛选
  
  -- 时间戳
  created_at TEXT DEFAULT CURRENT_TIMESTAMP,
  read_at TEXT,
  
  -- 原始信息
  raw_metadata TEXT            -- JSON: 其他可能有用原始信息
);

-- 抓取失败记录（便于后续重试）
CREATE TABLE IF NOT EXISTS fetch_failures (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  url TEXT NOT NULL,
  source_type TEXT,
  error_message TEXT,
  retry_count INTEGER DEFAULT 0,
  created_at TEXT DEFAULT CURRENT_TIMESTAMP,
  last_retry_at TEXT
);

-- 报告历史
CREATE TABLE IF NOT EXISTS reports (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  report_type TEXT NOT NULL,   -- 'daily' | 'weekly' | 'on-demand'
  article_count INTEGER,
  category_breakdown TEXT,     -- JSON: 各分类文章数
  created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

-- 索引
CREATE INDEX IF NOT EXISTS idx_articles_category ON articles(category);
CREATE INDEX IF NOT EXISTS idx_articles_is_read ON articles(is_read);
CREATE INDEX IF NOT EXISTS idx_articles_is_reported ON articles(is_reported);
CREATE INDEX IF NOT EXISTS idx_articles_created_at ON articles(created_at);
CREATE INDEX IF NOT EXISTS idx_articles_source_type ON articles(source_type);
CREATE INDEX IF NOT EXISTS idx_articles_gbrain_sync_status ON articles(gbrain_sync_status);
