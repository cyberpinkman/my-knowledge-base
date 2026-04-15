// 网页截图抓取脚本（改进版）
// 用法: node fetch-screenshot.js <url> <output-path>

const { chromium } = require('playwright');

async function main() {
  const url = process.argv[2];
  const outputPath = process.argv[3] || '/tmp/screenshot.png';
  
  console.error(`[fetch-screenshot] 开始抓取: ${url}`);
  
  const browser = await chromium.launch({
    headless: true,
    args: ['--no-sandbox', '--disable-setuid-sandbox']
  });
  
  const page = await browser.newPage();
  
  // 设置更大的视口
  await page.setViewportSize({ width: 1920, height: 10800 });
  
  try {
    console.error(`[fetch-screenshot] 正在访问页面...`);
    await page.goto(url, { waitUntil: 'networkidle', timeout: 30000 });
    
    // 等待页面加载
    await page.waitForTimeout(3000);
    
    // 滚动页面到底部以加载更多内容
    const scrollHeight = await page.evaluate(() => document.body.scrollHeight);
    console.error(`[fetch-screenshot] 页面总高度: ${scrollHeight}px`);
    
    // 滚动到页面底部
    await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight));
    await page.waitForTimeout(2000);
    
    // 截取整页
    await page.screenshot({ 
      path: outputPath,
      fullPage: true
    });
    
    console.error(`[fetch-screenshot] 截图已保存: ${outputPath}`);
    
    // 提取标题
    const title = await page.title();
    console.error(`[fetch-screenshot] 标题: ${title}`);
    
    // 提取所有文本内容（避免截断）
    const content = await page.evaluate(() => {
      // 移除脚本和样式标签
      const clone = document.body.cloneNode(true);
      const scripts = clone.querySelectorAll('script, style, noscript');
      scripts.forEach(el => el.remove());
      
      // 获取文本
      let text = clone.innerText || clone.textContent || '';
      
      // 清理空白
      text = text.replace(/\s+/g, ' ').replace(/\n\s*\n/g, '\n').trim();
      
      return text;
    });
    
    console.error('---TITLE---');
    console.error(title);
    console.error('---END TITLE---');
    
    console.error('---CONTENT---');
    // 分段输出以避免缓冲区问题
    const lines = content.split('\n');
    for (let i = 0; i < lines.length; i++) {
      console.error(lines[i]);
    }
    console.error('---END CONTENT---');
    
  } catch (error) {
    console.error(`❌ 错误: ${error.message}`);
    process.exit(1);
  } finally {
    await browser.close();
  }
}

main().catch(console.error);
