// Optional browser check: NODE_PATH=<Playwright modules> node this_file [screenshot.png]
// All camera/status responses are simulated; this never opens hardware.
const { chromium } = require('playwright');
const fs = require('fs');
const path = require('path');
const assert = require('node:assert/strict');
const root = path.resolve(__dirname, '..');

(async () => {
    const browser = await chromium.launch({ channel: 'msedge', headless: true });
    try {
        const page = await browser.newPage({ viewport: { width: 1200, height: 720 } });
        const errors = [], commands = [];
        page.on('pageerror', error => errors.push(error.message));
        const panel = fs.readFileSync(path.join(root, 'templates/index.html'), 'utf8')
            .match(/<section class="replay-panel"[\s\S]*?<\/section>/)[0];
        const html = `<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
            <link rel="stylesheet" href="/static/css/style.css"></head><body>${panel}
            <script src="/static/js/camera_replay.js"></script><script src="/static/js/dashboard_polling.js"></script></body></html>`;
        const cam = { enabled: false, buffered_sec: 0, cached_frames: 0, target_fps: 60, cache_bytes: 0, queue_drops: 0 };
        const state = { enabled: false, cameras: { Left: cam }, export: { phase: 'idle', active: false } };
        let failSave = false, polls = 0, inflight = 0, maxInflight = 0;
        await page.route('http://replay.test/**', async route => {
            const url = new URL(route.request().url());
            if (url.pathname === '/') return route.fulfill({ contentType: 'text/html', body: html });
            if (url.pathname.startsWith('/static/')) return route.fulfill({
                contentType: url.pathname.endsWith('.css') ? 'text/css' : 'text/javascript',
                body: fs.readFileSync(path.join(root, url.pathname.slice(1)), 'utf8') });
            if (url.pathname === '/dashboard/status') {
                inflight++; polls++; maxInflight = Math.max(maxInflight, inflight);
                await new Promise(resolve => setTimeout(resolve, 30));
                inflight--;
                return route.fulfill({ json: { replay: state } });
            }
            if (url.pathname === '/camera/replay/config') {
                const data = route.request().postDataJSON(); commands.push(data);
                state.enabled = cam.enabled = data.enabled;
                cam.target_fps = data.fps;
                return route.fulfill({ json: state });
            }
            if (url.pathname === '/camera/replay/save') {
                commands.push('save');
                if (failSave) return route.fulfill({ status: 409, json: { error: '磁盘空间不足' } });
                state.export = { phase: 'saving', active: true, message: '正在保存最近两分钟；回放采集继续运行' };
                return route.fulfill({ status: 202, json: state.export });
            }
            return route.fulfill({ status: 404 });
        });
        await page.goto('http://replay.test/');
        const save = page.locator('#replay-save'), toggle = page.locator('#replay-toggle');
        assert.ok(await save.isDisabled());
        await page.locator('#replay-fps').fill('120');
        await toggle.click();
        await page.waitForFunction(() => document.getElementById('replay-toggle').textContent.includes('停止'));
        assert.deepEqual(commands[0], { enabled: true, fps: 120 });
        cam.cached_frames = 10800; cam.buffered_sec = 90; cam.cache_bytes = 230 * 1048576;
        await page.waitForFunction(() => !document.getElementById('replay-save').disabled);
        await save.click();
        await page.waitForFunction(() => document.getElementById('replay-job').textContent.includes('采集继续'));
        assert.ok(await save.isDisabled());
        assert.equal(cam.enabled, true);
        state.export = { phase: 'saved', active: false, message: '回放已保存',
            files: ['session/Left.avi', 'session/Left.index.csv', 'session/manifest.json'],
            cameras: { Left: { coverage_sec: 90, frames: 10800 } } };
        await page.waitForFunction(() => document.querySelectorAll('#replay-files a').length === 3);
        assert.ok((await page.locator('#replay-job').innerText()).includes('90.0秒'));
        assert.equal(await page.locator('#replay-files a').first().getAttribute('href'), '/camera/replay/download/session/Left.avi');
        if (process.argv[2]) await page.locator('.replay-panel').screenshot({ path: process.argv[2] });
        await toggle.click();
        await page.waitForFunction(() => !document.getElementById('replay-fps').disabled);
        assert.equal(cam.enabled, false);
        failSave = true;
        await save.click();
        await page.waitForFunction(() => document.getElementById('replay-job').textContent.includes('磁盘空间不足'));
        await page.waitForTimeout(450);
        assert.ok((await page.locator('#replay-job').innerText()).includes('磁盘空间不足'));
        await page.setViewportSize({ width: 390, height: 844 });
        assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
        // An aborted browser poll may still be finishing inside this mock server.
        // Client request serialization/cancellation is checked in test_dashboard_polling.js.
        assert.ok(polls >= 3 && maxInflight <= 2, `polls=${polls}, mock-server concurrency=${maxInflight}`);
        assert.deepEqual(errors, []);
        console.log('Camera replay browser checks passed (controls, saving, download links, errors, mobile, shared polling).');
    } finally {
        await browser.close();
    }
})().catch(error => { console.error(error); process.exitCode = 1; });
