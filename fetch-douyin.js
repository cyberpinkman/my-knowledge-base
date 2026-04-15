// 抖音视频下载脚本 - 通过 Playwright 获取视频直链
// 用法: node fetch-douyin.js <url> <output-path>

const { chromium } = require('playwright');
const https = require('https');
const http = require('http');
const fs = require('fs');
const path = require('path');

async function main() {
    const url = process.argv[2];
    const outputPath = process.argv[3] || '/tmp/douyin-video.mp4';

    if (!url) {
        console.error('Usage: node fetch-douyin.js <url> <output-path>');
        process.exit(1);
    }

    console.error(`[fetch-douyin] 开始处理: ${url}`);

    const browser = await chromium.launch({
        headless: true,
        args: ['--no-sandbox', '--disable-setuid-sandbox']
    });

    const context = await browser.newContext({
        userAgent: 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
        viewport: { width: 1920, height: 1080 },
    });

    const page = await context.newPage();

    try {
        // Navigate - use domcontentloaded instead of networkidle for faster load
        await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 15000 });
        await page.waitForTimeout(5000);

        // Try to find video URL from page source
        const videoInfo = await page.evaluate(() => {
            // Method 1: Check <video> element src
            const videoEl = document.querySelector('video');
            if (videoEl) {
                const src = videoEl.src || videoEl.getAttribute('src');
                if (src && src.startsWith('http')) {
                    return { url: src, method: 'video-element' };
                }
                // Check <source> children
                const sources = videoEl.querySelectorAll('source');
                for (const s of sources) {
                    const sSrc = s.src || s.getAttribute('src');
                    if (sSrc && sSrc.startsWith('http')) {
                        return { url: sSrc, method: 'source-element' };
                    }
                }
            }

            // Method 2: Search page scripts for video URL
            const pageText = document.documentElement.innerHTML;
            const patterns = [
                /"playAddr"\s*:\s*\[?\s*\{[^}]*"src"\s*:\s*"([^"]+)"/,
                /"play_addr"[^}]*"url_list"\s*:\s*\["([^"]+)"/,
                /playApi["']?\s*:\s*["']([^"']+)/,
                /"download_addr"[^}]*"url_list"\s*:\s*\["([^"]+)"/,
                /https?:\/\/[^"'\s]+douyinvod[^"'\s]+/,
                /https?:\/\/v[0-9]*-[^"'\s]+\.douyinvod\.[^"'\s]+/,
            ];

            for (const pat of patterns) {
                const m = pageText.match(pat);
                if (m) {
                    return { url: m[1] || m[0], method: 'regex-' + pat.source.substring(0, 20) };
                }
            }

            return null;
        });

        if (videoInfo && videoInfo.url) {
            let videoUrl = videoInfo.url;
            // Unescape unicode
            videoUrl = videoUrl.replace(/\\u002F/g, '/').replace(/\\u0026/g, '&');
            console.error(`[fetch-douyin] Found video URL via ${videoInfo.method}`);

            // Download the video
            await downloadFile(videoUrl, outputPath);
            console.error(`[fetch-douyin] Downloaded to ${outputPath}`);
            console.log(outputPath);
        } else {
            console.error('[fetch-douyin] No video URL found in page');

            // Fallback: intercept network requests for video
            const videoUrls = [];
            page.on('response', async (response) => {
                const contentType = response.headers()['content-type'] || '';
                const url = response.url();
                if (contentType.includes('video') || url.includes('douyinvod') || url.includes('douyin.com/aweme/v1/play')) {
                    videoUrls.push(url);
                }
            });

            // Reload to capture requests
            await page.reload({ waitUntil: 'domcontentloaded', timeout: 15000 });
            await page.waitForTimeout(3000);

            if (videoUrls.length > 0) {
                console.error(`[fetch-douyin] Intercepted ${videoUrls.length} video URLs`);
                await downloadFile(videoUrls[0], outputPath);
                console.error(`[fetch-douyin] Downloaded to ${outputPath}`);
                console.log(outputPath);
            } else {
                console.error('[fetch-douyin] No video URLs intercepted');
                process.exit(1);
            }
        }

    } catch (error) {
        console.error(`[fetch-douyin] Error: ${error.message}`);
        process.exit(1);
    } finally {
        await browser.close();
    }
}

function downloadFile(url, dest) {
    return new Promise((resolve, reject) => {
        const file = fs.createWriteStream(dest);
        const client = url.startsWith('https') ? https : http;

        const request = (currentUrl) => {
            client.get(currentUrl, { 
                headers: { 
                    'Referer': 'https://www.douyin.com/',
                    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'
                }
            }, (response) => {
                // Follow redirects
                if (response.statusCode >= 300 && response.statusCode < 400 && response.headers.location) {
                    request(response.headers.location);
                    return;
                }
                if (response.statusCode !== 200) {
                    reject(new Error(`HTTP ${response.statusCode}`));
                    return;
                }
                response.pipe(file);
                file.on('finish', () => {
                    file.close();
                    const size = fs.statSync(dest).size;
                    if (size < 10000) {
                        reject(new Error(`File too small: ${size} bytes`));
                    } else {
                        resolve();
                    }
                });
            }).on('error', reject);
        };

        request(url);
    });
}

main().catch(e => {
    console.error(`[fetch-douyin] Fatal: ${e.message}`);
    process.exit(1);
});
