const fs = require('fs');
const vm = require('vm');
const assert = require('assert');
const context = vm.createContext({ AbortController });
vm.runInContext(fs.readFileSync('PyScripts/static/js/dashboard_polling.js', 'utf8'), context);

async function test() {
    const timers = new Map();
    let timerId = 0;
    const requests = [], updates = [];
    const poller = context.createDashboardPoller({
        setTimeout: (fn, ms) => { timers.set(++timerId, { fn, ms }); return timerId; },
        clearTimeout: id => timers.delete(id),
        fetch: (url, options) => new Promise((resolve, reject) => {
            assert.strictEqual(url, '/dashboard/status');
            requests.push({ resolve, signal: options.signal });
            options.signal.addEventListener('abort', () => {
                const error = new Error('aborted'); error.name = 'AbortError'; reject(error);
            });
        }),
        onData: state => updates.push(state),
    });
    const first = poller.start();
    for (let n = 0; n < 100; n++) assert.strictEqual(poller.refresh(), first);
    assert.strictEqual(requests.length, 1);  // 100 refresh attempts cannot build a browser request queue.
    let finishJson;
    requests[0].resolve({ ok: true, json: () => new Promise(resolve => finishJson = resolve) });
    await new Promise(resolve => setImmediate(resolve));
    poller.refresh();
    assert.strictEqual(requests.length, 1);  // Parsing a slow response still occupies the single slot.
    finishJson({ id: 'old' });
    await first;
    assert.strictEqual(updates.length, 1);

    const second = poller.refresh();
    poller.invalidate();  // A control command cancels telemetry instead of queuing behind it.
    await second;
    assert.ok(requests[1].signal.aborted);
    assert.strictEqual(updates.length, 1);

    const third = poller.refresh();
    const timeout = [...timers.values()].find(timer => timer.ms === 1500);
    timeout.fn();
    await third;
    assert.ok(requests[2].signal.aborted);
    const fourth = poller.refresh();
    requests[3].resolve({ ok: true, json: async () => ({ id: 'new' }) });
    await fourth;
    assert.strictEqual(updates.at(-1).id, 'new');
    poller.stop();
    assert.strictEqual(timers.size, 0);
    console.log('Dashboard polling: one in-flight request, cancellation and timeout recovery passed');
}
test().catch(error => { console.error(error); process.exitCode = 1; });
