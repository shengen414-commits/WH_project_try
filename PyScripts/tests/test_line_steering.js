const fs = require('fs');
const vm = require('vm');
const assert = require('assert');
const code = fs.readFileSync('PyScripts/static/js/main.js', 'utf8');
const controls = code.slice(code.indexOf('let lineSteeringPending = false;'), code.indexOf('// --- 录制功能 ---'));
const elements = new Map();
const element = id => { if (!elements.has(id)) elements.set(id, {innerText: '', disabled: false}); return elements.get(id); };
let resolveRequest, fail = false, updates = 0;
const requests = [];
const timers = new Map(); let timerId = 0;
const context = vm.createContext({
    setTimeout: callback => {timers.set(++timerId, callback); return timerId;},
    clearTimeout: id => timers.delete(id),
    document: {getElementById: element}, isRcMode: false,
    numericOutputToggle: {checked: true}, pendingThrottle: {value: 1600},
    updateDriveState: value => {assert.equal(value, 1500); ++updates;},
    fetchWithTimeout: async (url, options) => {
        requests.push({url, enabled: JSON.parse(options.body).enabled});
        if (fail) return {ok: false, json: async () => ({error: '串口未连接'})};
        return new Promise(resolve => {resolveRequest = resolve;});
    },
});
vm.runInContext(controls, context);
(async () => {
    context.renderLineSteering({mode: 'FAULT', fresh: true, reason: 'line_lost'});
    assert.ok(element('line-steering-status').innerText.includes('动力已锁定'));
    assert.ok(element('line-steering-status').innerText.includes('未检测到线'));
    const start = context.requestLineSteering(true);
    await context.requestLineSteering(true);
    assert.equal(requests.length, 1);
    assert.equal(element('line-steering-on').disabled, true);
    resolveRequest({ok: true, json: async () => ({message: '等待固件确认', request_id: 11})});
    await start;
    assert.ok(element('line-steering-message').innerText.includes('等待固件确认'));
    assert.ok(element('line-steering-status').innerText.includes('动力已锁定')); // No guessed success.
    assert.equal(element('line-steering-on').disabled, true); // HTTP is not firmware confirmation.
    context.renderLineSteering({request: {id: 10, status: 'confirmed', message: 'OLD ACK'}});
    assert.ok(!element('line-steering-message').innerText.includes('OLD ACK'));
    context.renderLineSteering({mode: 'LINE', fresh: true, reason: 'tracking', pwm: '1504', e: '0.143', algo: 'BASIC', dir: '1',
        request: {id: 11, status: 'confirmed', message: 'ESP32 已确认开启'}});
    assert.ok(element('line-steering-status').innerText.includes('巡线已开启'));
    assert.ok(element('line-steering-status').innerText.includes('基础反向响应'));
    assert.ok(element('line-steering-status').innerText.includes('方向 1'));
    assert.equal(element('line-steering-on').disabled, true);
    assert.equal(timers.size, 0);
    assert.ok(element('line-steering-message').innerText.includes('已确认开启'));
    const stop = context.requestLineSteering(false);
    resolveRequest({ok: true, json: async () => ({message: '等待固件确认', request_id: 12})});
    await stop;
    assert.equal(context.numericOutputToggle.checked, false);
    assert.equal(updates, 1);
    context.renderLineSteering({mode: 'OFF', fresh: true, reason: 'disabled',
        request: {id: 12, status: 'confirmed', message: 'ESP32 已确认关闭'}});
    fail = true;
    await context.requestLineSteering(true);
    assert.ok(element('line-steering-message').innerText.includes('串口未连接'));
    assert.equal(element('line-steering-on').disabled, false);
    fail = false;
    const retry = context.requestLineSteering(true);
    resolveRequest({ok: true, json: async () => ({message: '等待固件确认', request_id: 13})});
    await retry;
    context.renderLineSteering({mode: 'OFF', fresh: true, reason: 'line_lost',
        request: {id: 13, status: 'rejected', message: 'ESP32 拒绝开启：未检测到黑胶带'}});
    assert.ok(element('line-steering-message').innerText.includes('拒绝开启'));
    assert.equal(element('line-steering-on').disabled, false);
    const timeout = context.requestLineSteering(true);
    resolveRequest({ok: true, json: async () => ({message: '等待固件确认', request_id: 14})});
    await timeout;
    const callback = Array.from(timers.values())[0]; callback();
    assert.ok(element('line-steering-message').innerText.includes('未收到 ESP32'));
    assert.equal(element('line-steering-on').disabled, false);
    console.log('PASS: line steering waits for firmware, explains faults, stops numeric output, bounds requests and recovers errors');
})().catch(error => {console.error(error); process.exitCode = 1;});
