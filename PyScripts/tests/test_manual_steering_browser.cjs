// Optional: PYTHON=<Python with Flask> NODE_PATH=<Playwright modules> node this_file [screenshot.png]
// Render the real dashboard; every hardware response is simulated.
const { chromium } = require('playwright');
const { execFileSync } = require('child_process');
const fs = require('fs'), path = require('path');
const assert = require('node:assert/strict');
const root = path.resolve(__dirname, '..');
const html = execFileSync(process.env.PYTHON || 'python', ['-X', 'utf8', '-B', '-c',
    "from flask import Flask,render_template; from pathlib import Path; p=Path('PyScripts').resolve(); app=Flask('manual_test',template_folder=str(p/'templates'),static_folder=str(p/'static')); app.add_url_rule('/pressure_plot',endpoint='pressure_plot.page',view_func=lambda:''); ctx=app.test_request_context('/'); ctx.push(); print(render_template('index.html'))"],
    { encoding: 'utf8', cwd: path.resolve(root, '..') });

(async () => {
    const browser = await chromium.launch({ channel: 'msedge', headless: true });
    try {
        const browserContext = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
        const page = await browserContext.newPage();
        const errors = [], commands = [], throttle = [];
        page.on('pageerror', error => errors.push(error.message));
        const manual = { active: false, blocking: false, fresh: false, ready: false, precise: false,
            calibration_supported: true, center_min_us: 1300, center_max_us: 1700,
            probe_supported:true, calibration:{center_us:null,points:[],endpoints:{}},
            target_us: null, low_us: 1200, center_us: 1500, high_us: 1800, serial_open: true, request: { status: 'idle' } };
        let next = 100, rejectEntry = false, delayEntry = 0;
        const lineConfig={request:{status:'idle'},applied:null,probes:{fresh:true,valid:true,mask:1,center_channel:0,reason:'valid',side:'left'}};
        const configs=[];
        const lineActions=[];
        let steeringState={mode:'OFF',fresh:true,reason:'disabled'};
        const independent={phase:'off',blocking:false,source:null,request:{status:'idle'},firmware:{},fresh:false,jpeg_sequence:0,settings:{
            imu:{kp:4,kd:.5,deadband:1,max_us:80,slew_us_s:400,direction:1},
            camera:{kp:200,kd:.5,deadband:.03,max_us:80,slew_us_s:400,direction:1,confidence_min:.65,target_x:.5,roi_top:.35,lookahead:.4,threshold_mode:'otsu',threshold:80}}};
        const independentActions=[];
        function ack(target = manual.center_us) {
            const stopping = manual.request.action === 'stop';
            const calibrating = manual.request.action === 'calibrate';
            if (calibrating) Object.assign(manual, { center_us: target, low_us: target - 300, high_us: target + 300 });
            if(manual.request.action==='expand_low')manual.low_us=Math.max(1000,manual.low_us-25);
            if(manual.request.action==='expand_high')manual.high_us=Math.min(2000,manual.high_us+25);
            Object.assign(manual, { active: !stopping, blocking: !stopping, fresh: true,
                ready: true, precise: true, target_us: target });
            manual.request = { ...manual.request, status: 'confirmed', message: stopping ? '已归中并退出手动测试' : calibrating ? `已保存直行中位 ${target}μs，掉电保留` : `ESP32 已确认输出 ${target}μs` };
            if(manual.calibration.points.length===3)manual.calibration.estimated_angle_deg=(target-manual.center_us)/10;
        }
        const context = page.context(), external = [];
        context.on('request', request => {if(request.url().startsWith('https://'))external.push(request.url());});
        await context.route('https://**', route => route.abort()); // Dashboard must work offline.
        await context.route('http://manual.test/**', async route => {
            const url = new URL(route.request().url());
            if (url.pathname === '/') return route.fulfill({ contentType: 'text/html', body: html });
            if (url.pathname.startsWith('/static/')) return route.fulfill({ contentType: url.pathname.endsWith('.css') ? 'text/css' : 'text/javascript', body: fs.readFileSync(path.join(root, url.pathname.slice(1)), 'utf8') });
            if (url.pathname.startsWith('/video_feed/')) return route.fulfill({ status: 204 });
            if (url.pathname.startsWith('/camera/preview/')) return route.fulfill({ status: 204 });
            if (url.pathname === '/dashboard/status') return route.fulfill({ json: {
                sensor: { position: 0, speed: '0', mode: 'WEB', throttle: 1500, encoder_fresh: !manual.blocking,
                    steering: steeringState, manual_steering: manual, line_response:lineConfig,independent_steering:independent },
                fps: { left: '210.0', right: '0.0' }, record: { active: false }, speed_record: { active: false },
                sync: { phase: 'idle', active: false, pressure: {}, encoder: {}, encoder_fresh: !manual.blocking },
                conversion: { phase: 'idle' }, replay: { enabled: false, cameras: {}, export: {} } } });
            if(url.pathname==='/steering/line/config'){
                if(route.request().method()==='GET')return route.fulfill({json:lineConfig});
                const settings=route.request().postDataJSON();configs.push(settings);
                lineConfig.request={id:++next,sent_at:next,status:'pending',settings,message:'等待固件参数确认'};
                return route.fulfill({json:lineConfig});
            }
            if(url.pathname==='/steering/line'){
                const action=route.request().postDataJSON();lineActions.push(action);
                const id=++next;
                steeringState={mode:action.enabled?'LINE':'OFF',fresh:true,reason:action.enabled?'tracking':'disabled',algo:'PROP',dir:'-1',pwm:'1500',e:'0',request:{id,status:'confirmed',message:'固件已确认转向开关'}};
                return route.fulfill({json:{request_id:id,message:'等待固件确认'}});
            }
            if (url.pathname === '/steering/manual') {
                const body = route.request().postDataJSON(); commands.push(body);
                if (body.action === 'start' && rejectEntry) return route.fulfill({ status: 409, json: { error: '请先结束同步记录' } });
                manual.request = { id: ++next, action: body.action, target_us: body.pulse_us ?? null,
                    status: 'pending', sent_at: next, message: '命令已发送，等待 ESP32 确认（最多3秒）' };
                manual.blocking = true;
                if (body.action === 'start' && delayEntry) await new Promise(resolve => setTimeout(resolve, delayEntry));
                return route.fulfill({ json: { status: 'requested', request_id: next, state: manual } });
            }
            if(url.pathname==='/steering/independent'){
                const body=route.request().postDataJSON();independentActions.push(body);
                if(body.action==='save')independent.settings[body.source]=body.settings;
                if(body.action==='start')Object.assign(independent,{source:body.source,blocking:true,phase:'arming',request:{id:++next,status:'pending'}});
                if(body.action==='stop')Object.assign(independent,{phase:'stopping',blocking:true});
                return route.fulfill({json:independent});
            }
            if(url.pathname.startsWith('/steering/manual/calibration/')){
                const body=route.request().postDataJSON(),a=manual.calibration;
                if(body.pulse_us!==manual.target_us)return route.fulfill({status:409,json:{error:'输出已变化'}});
                a.center_us=manual.center_us;
                if(body.angle_deg!=null)a.points=[...a.points.filter(p=>p.pulse_us!==body.pulse_us),{pulse_us:body.pulse_us,angle_deg:body.angle_deg}];
                if(body.side)a.endpoints[body.side]={pulse_us:body.pulse_us,angle_deg:body.angle_deg};
                if(a.points.length===3)a.estimated_angle_deg=(manual.target_us-manual.center_us)/10;
                return route.fulfill({json:a});
            }
            if(url.pathname==='/vehicle-lab/actuator/preview')return route.fulfill({json:{pulse_us:1590,sent_to_hardware:false}});
            if (url.pathname === '/set_throttle') { throttle.push(url.searchParams.get('val')); return route.fulfill({ json: { status: 'success' } }); }
            return route.fulfill({ status: 404, body: '' });
        });
        await page.goto('http://manual.test/');
        await page.waitForFunction(() => !document.getElementById('manual-steering-start').disabled);
        const panel = page.locator('#manual-steering-panel');
        assert.equal(await panel.getAttribute('open'), null);
        assert.equal(await page.locator('.manual-steering-content').isVisible(), false);
        assert.ok(await panel.evaluate(node => document.querySelector('.control-panel').contains(node)));
        await panel.locator(':scope > summary').click();
        await page.locator('#manual-steering-start').click();
        await page.waitForFunction(() => document.getElementById('manual-steering-status').textContent.includes('等待 ESP32'));
        assert.ok(await page.locator('#manual-steering-plus').isDisabled());
        ack();
        await page.waitForFunction(() => !document.getElementById('manual-steering-plus').disabled);
        assert.ok(await page.locator('#throttle-slider').isDisabled());
        assert.ok(await page.locator('#line-steering-on').isDisabled());
        await page.locator('#manual-steering-plus').click();
        await page.waitForFunction(() => document.getElementById('manual-steering-plus').disabled);
        assert.deepEqual(commands.at(-1), { action: 'set', pulse_us: 1505 });
        assert.equal(await page.locator('#manual-steering-current').innerText(), '1500');
        ack(1505);
        await page.waitForFunction(() => document.getElementById('manual-steering-current').textContent === '1505');
        await page.locator('#manual-steering-slider').evaluate(node => {
            node.value = '1520'; node.dispatchEvent(new Event('input', { bubbles: true })); node.dispatchEvent(new Event('change', { bubbles: true }));
        });
        await page.waitForFunction(() => document.getElementById('manual-steering-plus').disabled);
        assert.deepEqual(commands.at(-1), { action: 'set', pulse_us: 1520 });
        ack(1520);
        await page.waitForFunction(() => !document.getElementById('manual-steering-apply').disabled);
        await page.locator('#manual-steering-input').fill('1810');
        const beforeInvalid = commands.length;
        await page.locator('#manual-steering-apply').click();
        assert.equal(commands.length, beforeInvalid);
        assert.ok((await page.locator('#manual-steering-status').innerText()).includes('1200～1800'));
        await page.locator('#manual-steering-input').fill('1540');
        await page.locator('#manual-steering-apply').click();
        await page.waitForFunction(() => document.getElementById('manual-steering-plus').disabled);
        assert.deepEqual(commands.at(-1), { action: 'set', pulse_us: 1540 });
        ack(1540);
        await page.waitForFunction(() => !document.getElementById('manual-steering-center').disabled);
        await page.locator('#manual-steering-save-center').click();
        await page.waitForFunction(() => document.getElementById('manual-steering-save-center').disabled);
        assert.equal(commands.at(-1).action, 'calibrate');
        assert.equal(await page.locator('#manual-steering-current').innerText(), '1540');
        ack(1540);
        await page.waitForFunction(() => document.getElementById('manual-steering-mid').textContent.includes('1540'));
        assert.equal(await page.locator('#manual-steering-slider').getAttribute('min'), '1240');
        assert.equal(await page.locator('#manual-steering-slider').getAttribute('max'), '1840');
        assert.ok((await page.locator('#manual-steering-status').innerText()).includes('掉电保留'));
        await page.locator('#manual-steering-center').click();
        await page.waitForFunction(() => document.getElementById('manual-steering-center').disabled);
        assert.equal(commands.at(-1).action, 'center');
        ack();
        await page.waitForFunction(() => !document.getElementById('manual-steering-center').disabled);
        await page.locator('#manual-wheel-angle').fill('0');
        const beforeRecord=commands.length;
        await page.locator('#manual-record-point').click();
        await page.waitForFunction(()=>document.getElementById('manual-calibration-points').children.length===1);
        assert.equal(commands.length,beforeRecord);
        for(const [pulse,angle,side] of [[1640,10,'left'],[1440,-10,'right']]){
            await page.locator('#manual-steering-input').fill(String(pulse));
            await page.locator('#manual-steering-apply').click();
            await page.waitForFunction(()=>document.getElementById('manual-steering-plus').disabled);
            ack(pulse);
            await page.waitForFunction(()=>!document.getElementById('manual-record-point').disabled);
            await page.locator('#manual-wheel-angle').fill(String(angle));
            await page.locator(`#manual-record-${side}`).click();
            await page.waitForFunction(side=>document.getElementById(`manual-${side}-endpoint`).textContent.includes('μs'),side);
        }
        await page.waitForFunction(()=>document.getElementById('manual-estimated-angle').textContent==='-10.0°');
        await page.locator('#manual-preview-angle').fill('5');
        const beforeQuery=commands.length;
        await page.getByRole('button',{name:'计算对应 PWM'}).click();
        await page.waitForFunction(()=>document.getElementById('manual-angle-preview').textContent.includes('1590'));
        assert.equal(commands.length,beforeQuery);
        await panel.locator('.manual-probe-range summary').click();
        await page.locator('#manual-expand-low').click();
        await page.waitForFunction(()=>document.getElementById('manual-expand-low').disabled);
        ack(1440);
        await page.waitForFunction(()=>document.getElementById('manual-steering-low').textContent==='1215');
        assert.equal(await page.locator('#manual-steering-current').innerText(),'1440');
        if (process.argv[2]) await panel.screenshot({ path: process.argv[2] });
        await panel.locator(':scope > summary').click();
        await page.waitForFunction(() => document.getElementById('manual-steering-badge').textContent.includes('等待'));
        assert.equal(commands.at(-1).action, 'stop');
        ack();
        await page.waitForFunction(() => !document.getElementById('throttle-slider').disabled);
        assert.equal(await panel.getAttribute('open'), null);

        // Entry errors stay visible across subsequent dashboard polls.
        await panel.locator(':scope > summary').click();
        rejectEntry = true;
        await page.locator('#manual-steering-start').click();
        await page.waitForFunction(() => document.getElementById('manual-steering-status').textContent.includes('先结束同步记录'));
        await page.waitForTimeout(300);
        assert.ok((await page.locator('#manual-steering-status').innerText()).includes('先结束同步记录'));
        rejectEntry = false;
        delayEntry = 200;
        await page.locator('#manual-steering-start').click();
        await panel.locator(':scope > summary').click();  // Closing during HTTP entry must queue an exit.
        await page.waitForFunction(() => document.getElementById('manual-steering-status').textContent.includes('等待 ESP32'));
        await page.waitForTimeout(300);
        assert.equal(commands.at(-1).action, 'stop');
        ack();
        await page.waitForFunction(() => !document.getElementById('throttle-slider').disabled);
        await page.setViewportSize({ width: 390, height: 844 });
        await panel.locator(':scope > summary').click();
        const overflow = await page.evaluate(() => ({ width: innerWidth, scroll: document.documentElement.scrollWidth,
            elements: [...document.querySelectorAll('body *')].map(node => ({ tag: node.tagName, id: node.id,
                cls: node.className, right: node.getBoundingClientRect().right, width: node.getBoundingClientRect().width }))
                .filter(node => node.width > 0 && node.right > innerWidth + 1).slice(0, 15) }));
        assert.ok(overflow.scroll <= overflow.width, JSON.stringify(overflow));
        await page.locator('#manual-steering-start').click();
        await page.waitForFunction(() => document.getElementById('manual-steering-status').textContent.includes('未收到固件确认'), {timeout:6000});
        ack();
        await page.waitForFunction(() => !document.getElementById('manual-steering-status').textContent.includes('未收到固件确认'));
        await page.locator('#manual-steering-stop').click();
        await page.waitForFunction(() => document.getElementById('manual-steering-status').textContent.includes('等待 ESP32'));
        ack();
        await page.waitForFunction(() => !document.getElementById('manual-steering-start').disabled);
        assert.deepEqual(throttle, []);  // Manual controls never command propulsion.
        assert.deepEqual(errors, []);
        await page.locator('#independent-imu-on').click();
        await page.waitForFunction(()=>document.getElementById('independent-status').textContent.includes('等待固件确认'));
        assert.equal(independentActions.at(-1).source,'imu');
        assert.ok(await page.locator('#independent-camera-on').isDisabled());
        Object.assign(independent,{phase:'calibrating',fresh:true,firmware:{pwm:1500,ready:'0',reason:'imu_calibrating'}});
        await page.waitForFunction(()=>document.getElementById('independent-status').textContent.includes('静止标定中'));
        Object.assign(independent,{phase:'running',observation:{source:'imu',relative_heading_deg:6,yaw_rate_deg_s:1},proposal:{correction:-.25},observation_age_ms:10,firmware:{pwm:1480,ready:'1',reason:'tracking'}});
        await page.waitForFunction(()=>document.getElementById('independent-observation').textContent.includes('相对航向 6.00'));
        await page.locator('#independent-off').click();
        await page.waitForFunction(()=>document.getElementById('independent-status').textContent.includes('等待归中退出'));
        Object.assign(independent,{phase:'off',blocking:false,firmware:{pwm:1500,reason:'disabled'}});
        await page.waitForFunction(()=>!document.getElementById('independent-camera-on').disabled);
        await page.locator('#independent-camera-on').click();
        await page.waitForFunction(()=>document.getElementById('independent-status').textContent.includes('等待固件确认'));
        Object.assign(independent,{phase:'running',observation:{source:'camera',image_offset_norm:.2,image_heading_deg:1,confidence:.9},proposal:{correction:-.5},observation_age_ms:20,firmware:{pwm:1460,ready:'1',reason:'tracking'}});
        await page.waitForFunction(()=>document.getElementById('independent-observation').textContent.includes('画面偏差 0.200'));
        Object.assign(independent,{phase:'fault',firmware:{pwm:1500,reason:'host_target_stale'}});
        await page.waitForFunction(()=>document.getElementById('independent-status').textContent.includes('已停止'));
        assert.ok(await page.locator('#independent-imu-on').isDisabled());
        await page.locator('#independent-off').click();
        await page.waitForFunction(()=>document.getElementById('independent-status').textContent.includes('等待归中退出'));
        Object.assign(independent,{phase:'off',blocking:false,firmware:{pwm:1500,reason:'disabled'}});
        await page.waitForFunction(()=>!document.getElementById('independent-imu-on').disabled);
        assert.equal(independentActions.filter(v=>v.action==='save').length,2);
        assert.deepEqual(throttle,[]);
        if(process.argv[3]){
            await page.setViewportSize({width:1440,height:1000});
            await page.locator('.independent-steering-panel details').evaluateAll(nodes=>nodes.forEach(n=>n.open=true));
            await page.locator('.independent-steering-panel').screenshot({path:process.argv[3]});
            await page.setViewportSize({width:390,height:844});
        }
        await page.locator('#line-response-form').evaluate(node=>node.closest('details').open=true);
        assert.equal(await page.locator('#line-live-probes span').count(),8);
        await page.locator('#line-response-form [name=direction]').selectOption('-1');
        await page.locator('#line-response-form button').click();
        await page.waitForFunction(()=>document.getElementById('line-response-status').textContent.includes('等待固件参数'));
        assert.equal(lineConfig.applied,null);
        assert.deepEqual(configs.at(-1),{left_us:400,right_us:400,deadband:.10,slew_us_s:600,direction:-1});
        assert.equal(await page.locator('#line-response-form [name=left_us]').getAttribute('max'),'500');
        assert.equal(await page.locator('#line-response-form [name=right_us]').getAttribute('max'),'500');
        lineConfig.applied=configs.at(-1);lineConfig.request.status='confirmed';lineConfig.request.message='固件已确认比例参数';
        await page.waitForFunction(()=>document.getElementById('line-response-status').textContent.includes('方向-1'));
        const formOverflow=await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth);
        assert.equal(formOverflow,false);
        const beforeAuto=configs.length;
        await page.locator('#line-steering-on').click();
        await page.waitForFunction(n=>document.getElementById('line-response-status').textContent.includes('等待固件参数'),beforeAuto);
        assert.equal(configs.length,beforeAuto+1);
        assert.equal(lineActions.length,0); // Filling/applying a value cannot arm before ACK.
        lineConfig.applied=configs.at(-1);lineConfig.request.status='confirmed';lineConfig.request.message='固件已确认比例参数';
        await page.waitForFunction(()=>document.getElementById('line-steering-status').textContent.includes('可调比例反向响应'));
        assert.deepEqual(lineActions,[{enabled:true}]);
        await page.locator('#line-steering-off').click();
        await page.waitForFunction(()=>!document.getElementById('line-steering-on').disabled);
        const beforeCancel=lineActions.length;
        await page.locator('#line-steering-on').click();
        await page.waitForFunction(()=>document.getElementById('line-response-status').textContent.includes('等待固件参数'));
        await page.locator('#line-steering-off').click();
        lineConfig.applied=configs.at(-1);lineConfig.request.status='confirmed';
        await page.waitForFunction(()=>document.getElementById('line-steering-message').textContent.includes('已取消开启'));
        assert.equal(lineActions.length,beforeCancel); // Late config ACK cannot restart cancelled entry.
        await page.close();
        for (let i=0;i<3;i++) {
            const reopened = await context.newPage();
            reopened.on('pageerror', e => errors.push(e.message));
            await reopened.goto('http://manual.test/', {waitUntil:'load', timeout:5000});
            await reopened.waitForFunction(() => !document.getElementById('manual-steering-start').disabled);
            assert.equal(await reopened.evaluate(() => Chart.version), '4.4.8');
            await reopened.close();
        }
        assert.deepEqual(external, []);assert.deepEqual(errors, []);
        console.log('Steering browser checks passed: manual calibration, independent IMU/camera lifecycle, fault/exit confirmation, mobile layout and offline charts.');
    } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
