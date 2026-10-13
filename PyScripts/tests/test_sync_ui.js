const fs = require('fs');
const vm = require('vm');
const assert = require('assert');
const elements = new Map();
const element = id => {
    if (!elements.has(id)) elements.set(id, { innerText: '', disabled: false });
    return elements.get(id);
};
const configButtons = [{}, {}];
const context = vm.createContext({
    document: { getElementById: element, querySelectorAll: () => configButtons },
    setInterval: () => {},
    fetch: async () => ({ ok: true, json: async () => ({ phase: 'idle', encoder_fresh: false, pressure: {} }) }),
});
vm.runInContext(fs.readFileSync('PyScripts/static/js/sync_recording.js', 'utf8'), context);
const render = state => context.renderSyncState({ phase: 'idle', active: false, encoder_fresh: true, pressure: { connected: true }, ...state });
render({});
assert.strictEqual(element('sync-start').disabled, false);
render({ encoder_fresh: false });
assert.strictEqual(element('sync-start').disabled, true);
render({ pressure: { connected: true, reboot_required: true } });
assert.strictEqual(element('sync-start').disabled, true);
render({ phase: 'recording', active: true });
assert.strictEqual(element('sync-start').disabled, true);
assert.strictEqual(element('sync-stop').disabled, false);
assert.ok(configButtons.every(button => button.disabled));
render({ phase: 'stopping', active: true });
assert.strictEqual(element('sync-stop').disabled, true);
render({ phase: 'stop_failed', active: true, error: 'STOP timeout' });
assert.strictEqual(element('sync-stop').disabled, false);
assert.ok(element('sync-stop').innerText.includes('重试'));
assert.ok(element('sync-status').innerText.includes('STOP timeout'));
render({ phase: 'error', active: false });
assert.strictEqual(element('sync-start').disabled, false);
assert.strictEqual(element('sync-stop').disabled, true);
console.log('Sync UI state checks passed');
