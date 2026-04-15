// Twitter 抓取脚本 - 使用保存的登录状态
// 用法: node fetch-twitter.js <url>

const { chromium } = require('playwright');
const path = require('path');

const PROFILE_DIR = path.join(__dirname, 'twitter-profile');

async function main() {
  const url = process.argv[2];
  
  if (!url) {
    console.error('用法: node fetch-twitter.js <twitter-url>');
    process.exit(1);
  }
  
  console.error(`[fetch-twitter] 正在抓取: ${url}`);
  
  const browser = await chromium.launchPersistentContext(PROFILE_DIR, {
    headless: false,  // 必须非 headless，否则 Twitter 会检测
    channel: 'chrome',
    args: ['--disable-blink-features=AutomationControlled'],
    viewport: { width: 1280, height: 800 }
  });
  
  const page = browser.pages()[0] || await browser.newPage();
  
  try {
    await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 20000 });
    // 等待页面加载完成
    await page.waitForLoadState('load', { timeout: 15000 }).catch(() => {});
    await page.waitForTimeout(3000);
    
    // 等待推文内容加载
    await page.waitForSelector('[data-testid="tweet"]', { timeout: 10000 });
    
    // 提取标题
    const title = await page.title();
    console.error('---TITLE---');
    console.error(title);
    console.error('---END TITLE---');
    
    // 提取推文内容
    const content = await page.evaluate(() => {
      // 获取推文文本
      const tweet = document.querySelector('[data-testid="tweetText"]');
      if (tweet) return tweet.innerText;
      
      // 备用：获取整个 body 的文本
      return document.body.innerText;
    });
    
    console.error('---CONTENT---');
    console.error(content);
    console.error('---END CONTENT---');
    
  } catch (error) {
    console.error(`❌ 错误: ${error.message}`);
    process.exit(1);
  } finally {
    await browser.close();
  }
}

main().catch(console.error);
