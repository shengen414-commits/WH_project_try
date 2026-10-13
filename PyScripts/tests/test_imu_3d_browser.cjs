// Optional visual integration check: node test_imu_3d_browser.cjs [screenshot.png]
// Requires Playwright + an installed Edge. All HTTP data is simulated; no hardware.
const { chromium } = require('playwright');
const fs = require('fs');
const path = require('path');
const assert = require('node:assert/strict');
const root = path.resolve(__dirname, '..');
const {rotate} = require('../static/js/imu_3d.js');
assert.deepEqual(rotate([1,0,0,0],[1,2,3]),[1,2,3]);
const turned = rotate([Math.SQRT1_2,0,0,Math.SQRT1_2],[1,0,0]);
assert.ok(Math.abs(turned[0])<1e-12 && Math.abs(turned[1]-1)<1e-12);
(async()=> {
    const browser = await chromium.launch({channel:'msedge',headless:true});
    try {
        const page = await browser.newPage({viewport:{width:1280,height:960}});
        const errors = [], commands = [];
        page.on('pageerror',error=>errors.push(error.message));
        let fresh = true, angle = 0, inflight = 0, maxInflight = 0, requests = 0;
        const html = fs.readFileSync(path.join(root,'templates/imu.html'),'utf8')
            .replace(/{{.*?}}/g,match=>match.includes('imu.css')?'/static/css/imu.css':'/static/js/imu_3d.js');
        await page.route('http://imu.test/**',async route=> {
            const url = new URL(route.request().url());
            if (url.pathname === '/imu/status') {
                requests++; inflight++; maxInflight = Math.max(maxInflight,inflight);
                await new Promise(resolve=>setTimeout(resolve,80));
                inflight--;
                return route.fulfill({json:{fresh,serial_open:true,serial_error:null,age_ms:fresh?12:800,
                    quaternion:[Math.cos(angle/2),0,0,Math.sin(angle/2)],angles:[0,0,angle*180/Math.PI],
                    message:fresh?'三维数据接收中':'数据已断流',
                    count:100, diagnostics:{invalid_frames:1,consecutive_invalid:0,last_invalid_age_ms:2000,
                        last_invalid:{reason:'ax 不是数字',raw:'[IMU],100,0�,0,1,0,0,0,25,0,NO'}},
                    sample:{acceleration:[0,0,1],gyro:[0,0,20],corrected_z:20,temperature:27,calibration:'OK'}}});
            }
            if (url.pathname === '/imu/command') {
                commands.push(route.request().postDataJSON().action);
                return route.fulfill({status:409,json:{error:'请先结束记录再启动测试'}});
            }
            if (url.pathname === '/imu') return route.fulfill({contentType:'text/html',body:html});
            if (url.pathname.startsWith('/static/')) return route.fulfill({
                contentType:url.pathname.endsWith('.css')?'text/css':'text/javascript',
                body:fs.readFileSync(path.join(root,url.pathname.slice(1)),'utf8')});
            return route.fulfill({status:404,body:''});
        });
        await page.goto('http://imu.test/imu');
        await page.waitForFunction(()=>document.getElementById('connection').textContent==='实时姿态');
        assert.equal(await page.locator('#invalid-count').innerText(),'1');
        await page.locator('.imu-diagnostics summary').click();
        assert.ok((await page.locator('#invalid-reason').innerText()).includes('ax 不是数字'));
        assert.ok((await page.locator('#invalid-raw').innerText()).includes('0�'));
        const initial = await page.locator('canvas').evaluate(c=>c.toDataURL());
        angle = .7;
        await page.waitForFunction(()=>document.getElementById('yaw').textContent==='40.1');
        assert.notEqual(await page.locator('canvas').evaluate(c=>c.toDataURL()),initial);
        await page.locator('[data-action=start]').click();
        await page.waitForFunction(()=>document.getElementById('message').textContent.includes('请先结束记录'));
        await page.waitForTimeout(250);
        assert.ok((await page.locator('#message').innerText()).includes('请先结束记录'));
        assert.deepEqual(commands,['start']);
        if (process.argv[2]) await page.screenshot({path:process.argv[2],fullPage:true});
        fresh = false;
        await page.waitForFunction(()=>document.getElementById('roll').textContent==='—');
        assert.equal(await page.locator('#lamp').getAttribute('class'),'');
        const paused = await page.locator('canvas').evaluate(c=>c.toDataURL());
        angle = 2;
        await page.waitForTimeout(350);
        assert.equal(await page.locator('canvas').evaluate(c=>c.toDataURL()),paused);
        await page.setViewportSize({width:390,height:844});
        await page.waitForTimeout(150);
        assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
        assert.ok(requests>4 && maxInflight===1);
        assert.deepEqual(errors,[]);
        console.log('PASS: quaternion axes, live rotation, stale freeze, frame diagnostics, command errors, bounded polling, mobile layout');
    } finally {await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
