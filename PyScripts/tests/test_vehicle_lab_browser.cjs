// Optional browser QA. Every hardware reply is simulated; no real ports/cameras.
const { chromium } = require('playwright');
const { execFileSync } = require('child_process');
const fs = require('fs'), path = require('path'), assert = require('node:assert/strict');
const root = path.resolve(__dirname,'..');
const fixture = JSON.parse(execFileSync(process.env.PYTHON || 'python',['-X','utf8','-B','-c',
    "import json,tempfile;from pathlib import Path;from flask import Flask,render_template;from PyScripts.vehicle_state.profile import ProfileStore;from PyScripts.vehicle_state.service import VehicleLab;p=Path('PyScripts').resolve();a=Flask('lab',template_folder=str(p/'templates'),static_folder=str(p/'static'));c=a.test_request_context('/');c.push();lab=VehicleLab(ProfileStore(Path(tempfile.gettempdir())/'uncreated-lab-fixture-profile.json'),p,lambda _:None);print(json.dumps({'html':render_template('vehicle_lab.html'),'state':lab.status()}))"],
    {encoding:'utf8',cwd:path.resolve(root,'..')}));
(async()=>{
    const browser=await chromium.launch({channel:'msedge',headless:true});
    try{
        const page=await browser.newPage({viewport:{width:1440,height:1100}}),errors=[],commands=[];
        const state=fixture.state;
        page.on('pageerror',e=>errors.push(e.message));
        await page.route('http://lab.test/**',async route=>{
            const url=new URL(route.request().url());
            if(url.pathname==='/')return route.fulfill({contentType:'text/html',body:fixture.html});
            if(url.pathname.startsWith('/static/'))return route.fulfill({contentType:url.pathname.endsWith('.css')?'text/css':'text/javascript',body:fs.readFileSync(path.join(root,url.pathname.slice(1)),'utf8')});
            if(url.pathname==='/vehicle-lab/status')return route.fulfill({json:state});
            const data=route.request().postDataJSON();commands.push({path:url.pathname,data});
            if(url.pathname==='/vehicle-lab/command'){
                state.request={status:'pending',action:data.action};state.blocking=true;
                return route.fulfill({json:state});
            }
            if(url.pathname==='/vehicle-lab/profile'){
                Object.assign(state.profile,data);return route.fulfill({json:state});
            }
            if(url.pathname==='/vehicle-lab/actuator/point')return route.fulfill({status:409,json:{error:'请先进入手动测试，等待固件确认'}});
            return route.fulfill({status:404});
        });
        await page.goto('http://lab.test/');
        await page.waitForFunction(()=>document.getElementById('lab-health').textContent.includes('时钟'));
        assert.equal(await page.locator('[name=imu_yaw_sign]').inputValue(),'-1');
        assert.equal(await page.locator('[name=line_pitch_m]').inputValue(),'');
        assert.equal(await page.locator('#value-y').innerText(),'—');
        await page.locator('[name=line_pitch_m]').fill('8');
        await page.waitForTimeout(600);
        assert.equal(await page.locator('[name=line_pitch_m]').inputValue(),'8'); // Polls preserve edits.
        await page.locator('#geometry-form button').click();
        await page.waitForTimeout(200);
        const saved=commands.find(c=>c.path==='/vehicle-lab/profile').data.geometry;
        assert.equal(saved.line_pitch_m,.008);assert.equal(saved.line_x_m,null);assert.equal(saved.imu_axes,null);
        await page.locator('#lab-start').click();
        await page.waitForFunction(()=>document.getElementById('lab-message').textContent.includes('等待固件'));
        assert.ok(await page.locator('#lab-zero').isDisabled());
        state.active=state.fresh=true;state.request={status:'confirmed'};
        state.raw={line:{mask:24,raw_mask:24,center_channel:3.5,reason:'valid'},imu_ready:true};
        await page.waitForFunction(()=>!document.getElementById('lab-zero').disabled);
        assert.equal(await page.locator('#line-probes .hit').count(),2);
        assert.ok((await page.locator('#line-position').innerText()).includes('4.5'));
        assert.ok(await page.locator('#geometry-form button').isDisabled());
        await page.locator('#lab-stop').click();
        state.active=state.fresh=state.blocking=false;state.request={status:'confirmed'};
        await page.waitForFunction(()=>!document.getElementById('lab-start').disabled);
        await page.locator('#wheel-angle').fill('-5');await page.locator('#actuator-form button').click();
        await page.waitForFunction(()=>document.getElementById('lab-message').textContent.includes('请先进入手动'));
        await page.waitForTimeout(600);
        assert.ok((await page.locator('#lab-message').innerText()).includes('请先进入手动'));
        assert.equal(commands.filter(c=>c.path==='/vehicle-lab/actuator/point').at(-1).data.angle_deg,-5);
        await page.evaluate(()=>scrollTo(0,0));
        if(process.argv[2])await page.screenshot({path:process.argv[2],fullPage:true});
        await page.setViewportSize({width:390,height:844});
        assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
        assert.deepEqual(errors,[]);
        console.log('Vehicle lab browser checks passed.');
    }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
