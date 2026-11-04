const fs = require('fs'), vm = require('vm'), assert = require('node:assert/strict');
const context = vm.createContext({AbortController});
vm.runInContext(fs.readFileSync('PyScripts/static/js/camera_preview.js', 'utf8'), context);
(async () => {
    const timers = new Map(), requests = [], shown = [], revoked = [];
    let next = 0, cleared=0;
    const preview = context.createCameraPreview({url: '/camera/preview/left', clearOnError:true,
        setTimeout: (fn, ms) => {timers.set(++next, {fn, ms});return next;}, clearTimeout: id => timers.delete(id),
        fetch: (url, options) => new Promise((resolve, reject) => {
            requests.push({url, resolve, signal:options.signal});
            options.signal.addEventListener('abort', () => reject(Object.assign(new Error(), {name:'AbortError'})));
        }),
        showImage: url => shown.push(url), clearImage() {cleared++;},
        createObjectURL: () => `blob:${++next}`, revokeObjectURL: url => revoked.push(url)});
    const settle = () => new Promise(resolve => setImmediate(resolve));
    preview.setEnabled(true);
    for(let i=0;i<100;i++)preview.setEnabled(true);
    assert.equal(requests.length, 1);
    requests[0].resolve({ok:true, status:200, blob:async()=>({})});await settle();
    assert.equal(shown.length, 1);
    preview.setEnabled(false);
    assert.deepEqual(revoked, shown);
    assert.equal(timers.size, 0);
    preview.setEnabled(true);
    assert.equal(requests.length, 2);
    preview.setEnabled(false);
    assert.ok(requests[1].signal.aborted);await settle();
    assert.equal(timers.size, 0);
    preview.setEnabled(true);
    let fired = [...timers.entries()].find(([,t])=>t.ms===1500);timers.delete(fired[0]);fired[1].fn();await settle();
    assert.ok(requests[2].signal.aborted);
    assert.ok(cleared>=3); // Disable and timeout clear stale images in detection preview.
    fired = [...timers.entries()].find(([,t])=>t.ms===80);timers.delete(fired[0]);fired[1].fn();
    requests[3].resolve({ok:true,status:204});await settle();
    preview.setEnabled(false);
    assert.equal(timers.size, 0);
    let clock=0, clears=0, code=200;
    const retry=[];
    const tolerant=context.createCameraPreview({url:'/test',emptyGraceMs:600,clearOnError:true,now:()=>clock,
        setTimeout:(fn,ms)=>{if(ms===80)retry.push(fn);return 1;},clearTimeout:()=>{},
        fetch:async()=>({ok:true,status:code,blob:async()=>({})}),
        createObjectURL:()=> 'blob:test',revokeObjectURL:()=>{},showImage:()=>{},clearImage:()=>clears++});
    tolerant.setEnabled(true);await settle();
    code=204;clock=200;retry.shift()();await settle();assert.equal(clears,0);
    clock=601;retry.shift()();await settle();assert.equal(clears,1);
    tolerant.setEnabled(false);
    console.log('Camera preview: single flight, finite timeout, close cancellation, reopen, no-frame retry and blob cleanup passed');
})().catch(error=>{console.error(error);process.exitCode=1;});
