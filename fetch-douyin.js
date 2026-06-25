#!/usr/bin/env node
/**
 * 抖音视频内容抓取脚本 (v4 - 稳定版)
 * 用法: node fetch-douyin.js <url>
 *
 * 输出 JSON 到 stdout:
 *   { title, author, description, duration, chapter_summary,
 *     stats: {likes, comments, collects, shares},
 *     tags: [], published_date, video_id, url, fetched_at }
 *
 * 策略：只提取可靠字段，不追求完美
 */

const { chromium } = require('playwright');

const URL = process.argv[2];
if (!URL) {
  console.error('用法: node fetch-douyin.js <url>');
  process.exit(1);
}

(async () => {
  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({
    userAgent: 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    locale: 'zh-CN',
  });
  const page = await context.newPage();

  try {
    console.error(`[douyin] 正在访问: ${URL}`);
    await page.goto(URL, { waitUntil: 'domcontentloaded', timeout: 30000 });
    const finalUrl = page.url();
    console.error(`[douyin] 最终URL: ${finalUrl}`);

    const videoIdMatch = finalUrl.match(/douyin\.com\/video\/(\d+)/);
    const videoId = videoIdMatch ? videoIdMatch[1] : '';

    // 等待 h1 出现，最多 20 秒
    await page.waitForSelector('h1', { timeout: 20000 }).catch(() => {
      console.error('[douyin] h1 未出现，可能需要登录');
    });
    // 额外等待动态内容
    await page.waitForTimeout(5000);

    // 用 console 注入提取，只取可靠数据
    const data = await page.evaluate(() => {
      const result = {
        title: '',
        author: '',
        description: '',
        duration: '',
        chapter_summary: '',
        stats: { likes: '', comments: '', collects: '', shares: '' },
        tags: [],
        published_date: '',
      };

      // ===== 标题 =====
      const h1 = document.querySelector('h1');
      if (h1) {
        // 取 h1 全部文本，去掉标签链接的 # 前缀
        const raw = h1.innerText || h1.textContent || '';
        result.title = raw.replace(/\s+/g, ' ').trim();
      }
      result.description = result.title;

      // ===== 作者 =====
      // 方式1: 带"作者"标记的链接
      const links = [...document.querySelectorAll('a[href*="/user/"]')];
      for (const link of links) {
        // 检查父级文本是否含"作者"
        let el = link;
        for (let i = 0; i < 3; i++) {
          if (el.parentElement) {
            if (el.parentElement.textContent.includes('作者')) {
              result.author = link.textContent.trim().replace(/作者$/, '').trim();
              break;
            }
            el = el.parentElement;
          }
        }
        if (result.author) break;
      }
      // 方式2: 找评论区的作者标记
      if (!result.author) {
        for (const link of links) {
          const next = link.nextElementSibling || link.parentElement?.nextElementSibling;
          if (next && next.textContent.trim() === '作者') {
            result.author = link.textContent.trim();
            break;
          }
        }
      }

      // ===== 时长 =====
      // 播放器上的 "00:00 / 04:08" 格式
      const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
      let node;
      while (node = walker.nextNode()) {
        const text = node.textContent.trim();
        if (/^\d{1,2}:\d{2}\s*\/\s*\d{1,2}:\d{2}$/.test(text)) {
          result.duration = text.split('/').pop().trim();
          break;
        }
      }

      // ===== 章节要点（AI 摘要）=====
      const bodyText = document.body.innerText;
      const chapterMatch = bodyText.match(/章节要点\n([\s\S]{10,300}?)(?=\n\d{2}:\d{2}|内容由AI生成)/);
      if (chapterMatch) {
        result.chapter_summary = chapterMatch[1].trim();
      }

      // ===== 标签 =====
      const tags = new Set();
      if (h1) {
        h1.querySelectorAll('a[href*="/search/"]').forEach(a => {
          const t = a.textContent.trim().replace(/^#/, '');
          if (t && t.length < 30) tags.add(t);
        });
      }
      result.tags = [...tags];

      // ===== 发布时间 =====
      const dateMatch = bodyText.match(/发布时间[：:]\s*(\d{4}[-/]\d{2}[-/]\d{2}\s*\d{0,2}:?\d{0,2})/);
      if (dateMatch) result.published_date = dateMatch[1].trim();

      // ===== 互动数据 =====
      // 策略：从 body text 中找 "1498 28 1706 243 举报" 这种模式
      // 数字紧跟在标题内容后面、举报前面
      const reportIdx = bodyText.indexOf('举报');
      if (reportIdx > -1) {
        // 取举报前面 100 字符
        const chunk = bodyText.substring(Math.max(0, reportIdx - 100), reportIdx).trim();
        // 找最后出现的 4 个短数字（1-6位，不含年份/备案号）
        const nums = chunk.match(/\b(\d{1,6})\b/g) || [];
        const validNums = nums.filter(n => {
          const v = parseInt(n);
          return v > 0 && v < 999999 && n.length <= 6;
        });
        if (validNums.length >= 4) {
          const last4 = validNums.slice(-4);
          result.stats.likes = last4[0];
          result.stats.comments = last4[1];
          result.stats.collects = last4[2];
          result.stats.shares = last4[3];
        }
      }

      return result;
    });

    const output = {
      title: data.title,
      author: data.author,
      description: data.description,
      duration: data.duration,
      chapter_summary: data.chapter_summary,
      stats: data.stats,
      tags: data.tags,
      published_date: data.published_date,
      video_id: videoId,
      url: finalUrl,
      fetched_at: new Date().toISOString(),
    };

    console.log(JSON.stringify(output, null, 2));

  } catch (err) {
    console.error(`[douyin] 错误: ${err.message}`);
    process.exit(1);
  } finally {
    await browser.close();
  }
})();
