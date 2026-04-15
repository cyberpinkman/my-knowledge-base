// Twitter 登录脚本 - 运行一次保存登录状态
// 用法: node twitter-login.js

const { chromium } = require('playwright');
const path = require('path');

const PROFILE_DIR = path.join(__dirname, 'twitter-profile');

async function main() {
  console.log('启动浏览器...');
  console.log('请在浏览器中登录 Twitter/X');
  console.log('登录完成后，关闭浏览器窗口即可\n');
  
  const browser = await chromium.launchPersistentContext(PROFILE_DIR, {
    headless: false,
    channel: 'chrome',
    args: ['--disable-blink-features=AutomationControlled'],
    viewport: null
  });
  
  const page = browser.pages()[0] || await browser.newPage();
  
  // 打开 Twitter
  await page.goto('https://x.com');
  
  console.log('等待你登录并关闭浏览器...');
  
  // 等待浏览器关闭
  await new Promise(resolve => {
    browser.on('close', resolve);
  });
  
  console.log('\n✅ 登录状态已保存!');
  console.log('现在可以用 node fetch-twitter.js 抓取推文了');
}

main().catch(console.error);
