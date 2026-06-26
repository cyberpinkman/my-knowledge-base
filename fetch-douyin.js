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

function collectUrls(obj, out = []) {
  if (!obj || typeof obj !== 'object') return out;
  if (Array.isArray(obj)) {
    obj.forEach(item => collectUrls(item, out));
    return out;
  }
  Object.values(obj).forEach(value => {
    if (typeof value === 'string' && /^https?:\/\//.test(value)) {
      out.push(value);
    } else if (value && typeof value === 'object') {
      collectUrls(value, out);
    }
  });
  return out;
}

function extractMediaUrls(awemeDetail) {
  const urls = collectUrls(awemeDetail?.video || {});
  const audioUrl = urls.find(url => url.includes('media-audio') || url.includes('/audio/')) || '';
  const videoUrl = urls.find(url =>
    url.includes('mime_type=video_mp4') &&
    !url.includes('/play/dash/') &&
    !url.includes('media-audio')
  ) || '';
  return { audioUrl, videoUrl };
}

function extractNoteFromText(bodyText, author) {
  const stopPattern = /^(?:@|评论\s*\d*|查看更多评论|相关推荐|去抖音|说点什么|打开抖音|滑动去抖音|全部评论)/;
  const lines = String(bodyText || '')
    .split('\n')
    .map(line => line.trim())
    .filter(Boolean);
  const candidates = [];

  for (let i = 0; i < lines.length; i++) {
    if (author && lines[i] !== author) continue;
    const chunk = [];
    for (const line of lines.slice(i + 1)) {
      if (stopPattern.test(line) || /^\d{4}[-/年]\d{1,2}[-/月]\d{1,2}/.test(line)) break;
      if (/^\d+\+?$/.test(line)) continue;
      if (/^打开抖音/.test(line)) continue;
      chunk.push(line);
    }
    const text = chunk.join('\n').trim();
    if (text.length > 40) candidates.push(text);
  }

  const fallback = lines
    .slice(lines.findIndex(line => /Vibe Coding|Harness|Skill|AI|vibecoding/i.test(line)))
    .filter(line => !stopPattern.test(line))
    .join('\n')
    .trim();
  const text = candidates.sort((a, b) => b.length - a.length)[0] || fallback;
  if (!text) return { title: '', description: '', tags: [] };

  const contentLines = text.split('\n').map(line => line.trim()).filter(Boolean);
  const title = contentLines[0] || '';
  const tags = [...new Set((text.match(/#[^\s#]+/g) || []).map(tag => tag.replace(/^#/, '')))];
  return { title, description: text, tags };
}

(async () => {
  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({
    userAgent: 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    locale: 'zh-CN',
  });
  const page = await context.newPage();
  let awemeDetail = null;

  page.on('response', async response => {
    if (!response.url().includes('/aweme/v1/web/aweme/detail/')) return;
    try {
      const payload = await response.json();
      awemeDetail = payload.aweme_detail || payload.aweme_details?.[0] || awemeDetail;
    } catch {
      // Some duplicate detail responses stream empty bodies; keep the last good one.
    }
  });

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
      function extractNoteFromPageText(bodyText, author, preferredTitle) {
        const stopPattern = /^(?:@|展开|发布时间|评论\s*\d*|查看更多评论|相关推荐|去抖音|说点什么|打开抖音|滑动去抖音|全部评论)/;
        const lines = String(bodyText || '')
          .split('\n')
          .map(line => line.trim())
          .filter(Boolean);
        const candidates = [];
        const titleTokens = String(preferredTitle || '')
          .replace(/\s*-\s*抖音$/, '')
          .split(/[｜|\s]/)
          .map(token => token.trim())
          .filter(token => token.length >= 4);

        for (let i = 0; i < lines.length; i++) {
          if (author && lines[i] !== author) continue;
          const chunk = [];
          for (const line of lines.slice(i + 1)) {
            if (stopPattern.test(line) || /^\d{4}[-/年]\d{1,2}[-/月]\d{1,2}/.test(line)) break;
            if (/^\d+\+?$/.test(line)) continue;
            if (/^打开抖音/.test(line)) continue;
            chunk.push(line);
          }
          const text = chunk.join('\n').trim();
          if (text.length > 40) candidates.push(text);
        }

        const firstRelevant = lines.findIndex(line =>
          titleTokens.some(token => line.includes(token)) || /Vibe Coding|Harness starter/i.test(line)
        );
        const fallback = firstRelevant >= 0
          ? lines.slice(firstRelevant).filter(line => !stopPattern.test(line)).join('\n').trim()
          : '';
        const score = text => {
          const firstLine = text.split('\n').map(line => line.trim()).filter(Boolean)[0] || '';
          let value = Math.min(text.length, 1200);
          if (/^\d{1,2}:\d{2}$/.test(firstLine)) value -= 5000;
          for (const token of titleTokens) {
            if (firstLine.includes(token)) value += 5000;
            if (text.includes(token)) value += 2000;
          }
          if (/Vibe Coding|Harness starter/i.test(firstLine)) value += 5000;
          if (/Vibe Coding|Harness starter/i.test(text)) value += 2000;
          return value;
        };
        const text = candidates.sort((a, b) => score(b) - score(a))[0] || fallback;
        if (!text) return { title: '', description: '', tags: [] };

        let contentLines = text.split('\n').map(line => line.trim()).filter(Boolean);
        const startAt = contentLines.findIndex(line =>
          titleTokens.some(token => line.includes(token)) || /Vibe Coding|Harness starter/i.test(line)
        );
        if (startAt > 0) contentLines = contentLines.slice(startAt);
        const firstLine = contentLines[0] || '';
        const title = firstLine.length > 80
          ? (firstLine.match(/^.{1,80}?[。！？!?]/)?.[0] || firstLine.slice(0, 80)).trim()
          : firstLine;
        const tags = [...new Set((text.match(/#[^\s#]+/g) || []).map(tag => tag.replace(/^#/, '')))];
        return { title, description: contentLines.join('\n'), tags };
      }

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

      const isNotePage = location.pathname.includes('/note/') || location.pathname.includes('/share/note/');
      if (isNotePage) {
        const note = extractNoteFromPageText(bodyText, result.author, document.title);
        if (!result.title && note.title) result.title = note.title;
        if ((!result.description || result.description === result.title) && note.description) {
          result.description = note.description;
        }
        if (!result.tags.length && note.tags.length) result.tags = note.tags;
      }

      return result;
    });

    const mediaUrls = extractMediaUrls(awemeDetail);
    const durationMs = awemeDetail?.video?.duration || 0;
    const durationFromDetail = durationMs
      ? `${Math.floor(durationMs / 60000)}:${String(Math.floor((durationMs % 60000) / 1000)).padStart(2, '0')}`
      : '';

    const output = {
      title: data.title,
      author: data.author,
      description: data.description,
      duration: data.duration || durationFromDetail,
      chapter_summary: data.chapter_summary,
      stats: data.stats,
      tags: data.tags,
      published_date: data.published_date,
      audio_url: mediaUrls.audioUrl,
      video_url: mediaUrls.videoUrl,
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
