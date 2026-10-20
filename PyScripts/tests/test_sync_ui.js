const fs = require('fs');
const vm = require('vm');
const assert = require('assert');
const elements = new Map();
const element = id => {
    if (!elements.has(id)) elements.set(id, { innerText: '', disabled: false, dataset: {}, open: false, addEventListener: () => {} });
    return elements.get(id);
};
const configButtons = [{}, {}];
const context = vm.createContext({
    URL,
    location: { href: 'http://localhost/' },
    performance: { getEntriesByName: () => [{ requestStart: 2000, fetchStart: 0 }] },
    document: { getElementById: element, querySelectorAll: selector => selector === '[data-sync-config]' ? configButtons : [] },
    setInterval: () => {},
    setTimeout: () => {},
    fetch: async () => ({ ok: true, json: async () => ({ phase: 'idle', encoder_fresh: false, pressure: {} }) }),
});
vm.runInContext(fs.readFileSync('PyScripts/static/js/sync_recording.js', 'utf8'), context);
const readyPressure = { connected: true, rate_hz: 100, fps: 0, udp_target_ip: '191.30.90.82' };
const render = state => context.renderSyncState({ phase: 'idle', active: false, encoder_fresh: true, pressure: readyPressure, ...state });
render({});
assert.strictEqual(element('sync-start').disabled, false);
assert.strictEqual(element('sync-connection-light').dataset.state, 'on');
assert.ok(element('sync-readiness').innerText.includes('可以开始'));
assert.ok(element('sync-stop').title.includes('尚未开始'));
render({ encoder_fresh: false });
assert.strictEqual(element('sync-start').disabled, true);
assert.ok(element('sync-readiness').innerText.includes('新鲜编码器'));
render({ encoder_fresh: false, encoder: { serial_open: true, debug_mode: 'servo' } });
assert.strictEqual(element('sync-encoder-normal').hidden, false);
assert.strictEqual(element('sync-encoder-normal').disabled, false);
assert.ok(element('sync-readiness').innerText.includes('[SERVO]'));
assert.ok(element('sync-readiness').innerText.includes('重新允许正常油门控制'));
render({ encoder_fresh: false, encoder: { serial_open: true, debug_mode: 'imu' } });
assert.strictEqual(element('sync-encoder-normal').hidden, false);
assert.ok(element('sync-readiness').innerText.includes('MPU6050'));
render({ encoder_fresh: false, encoder: { serial_open: false, port: '/dev/ttyUSB0' } });
assert.strictEqual(element('sync-encoder-normal').hidden, true);
assert.ok(element('sync-start').title.includes('串口未连接'));
render({ encoder_fresh: true, encoder: { serial_open: true } });
assert.strictEqual(element('sync-start').disabled, false);
assert.strictEqual(element('sync-encoder-normal').hidden, true);
render({ pressure: { connected: true } });
assert.strictEqual(element('sync-start').disabled, true);
assert.ok(element('sync-readiness').innerText.includes('读取设备信息'));
render({ pressure: { connected: true, reboot_required: true } });
assert.strictEqual(element('sync-start').disabled, true);
render({ phase: 'recording', active: true });
assert.strictEqual(element('sync-start').disabled, true);
assert.strictEqual(element('sync-stop').disabled, false);
assert.ok(configButtons.every(button => button.disabled));
render({ phase: 'recording', active: true, speed: { sample_count: 1000, saved_hz: 99.8 } });
assert.ok(element('sync-status').innerText.includes('车速保存 99.8 Hz'));
render({ phase: 'stopping', active: true });
assert.strictEqual(element('sync-stop').disabled, true);
render({ phase: 'stop_failed', active: true, error: 'STOP timeout' });
assert.strictEqual(element('sync-stop').disabled, false);
assert.ok(element('sync-stop').innerText.includes('重试'));
assert.ok(element('sync-status').innerText.includes('STOP timeout'));
render({ pressure: { connected: true, file_open: true, scanning: true, last_receive_unix_ns: Date.now() * 1e6 } });
assert.strictEqual(element('sync-start').disabled, true);
assert.strictEqual(element('sync-data-light').dataset.state, 'on');
assert.strictEqual(element('sync-pressure-stop').disabled, false);
assert.ok(element('sync-start').title.includes('先 STOP'));
render({ phase: 'error', active: false });
assert.strictEqual(element('sync-start').disabled, false);
assert.strictEqual(element('sync-stop').disabled, true);
context.appendSyncTiming('/sync_record/device/command', 10, { server_ms: 1, commands: [] }, 'test');
assert.ok(!element('sync-terminal-output').innerText.includes('2000.0 ms'));
console.log('Sync UI state checks passed');
assert.equal(context.syncPressureValue(999999, 23), null);
assert.equal(context.syncPressureValue(-999999, 23), null);
assert.equal(context.syncPressureValue(999999, 27), 999999);
assert.equal(context.syncPressureValue(-999999, 27), -999999);
assert.equal(context.syncPressureValue(1200, 23), 1200);
