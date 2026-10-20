// The fold is presentation only; explicit entry starts the firmware's neutral-power test mode.
(function (global) {
    const el = id => document.getElementById(id);
    const panel = el('manual-steering-panel');
    if (!panel) return;
    let state = { active: false, blocking: false, serial_open: false, request: {},
        low_us: 1450, center_us: 1500, high_us: 1550 };
    let busy = false, actionInFlight = '', error = '', exitQueued = false, confirmationTimer = null;
    const blocking = () => !!state.blocking || (busy && actionInFlight === 'start');

    function preview(value) {
        el('manual-steering-slider').value = value;
        el('manual-steering-input').value = value;
    }
    function renderManualSteering(data) {
        if (!data) return;
        const incoming = data.request || {}, previous = state.request || {};
        if ((incoming.sent_at || 0) < (previous.sent_at || 0)) return;
        if (incoming.id && incoming.id === previous.id && previous.status !== 'pending' && incoming.status === 'pending') return;
        state = data;
        const pending = incoming.status === 'pending';
        if (!pending && confirmationTimer !== null) { clearTimeout(confirmationTimer); confirmationTimer = null; }
        const blocked = blocking();
        const adjust = data.active && data.fresh && data.ready && data.precise && !pending && !busy;
        el('manual-steering-start').disabled = busy || pending || blocked || !data.serial_open;
        el('manual-steering-stop').disabled = busy || !blocked;
        document.querySelectorAll('[data-manual-adjust]').forEach(control => control.disabled = !adjust);
        el('manual-steering-minus').disabled = !adjust || data.target_us <= data.low_us;
        el('manual-steering-plus').disabled = !adjust || data.target_us >= data.high_us;
        for (const id of ['manual-steering-slider', 'manual-steering-input']) {
            el(id).min = data.low_us;
            el(id).max = data.high_us;
        }
        el('manual-steering-low').textContent = data.low_us;
        el('manual-steering-mid').textContent = `中位 ${data.center_us}`;
        el('manual-steering-high').textContent = data.high_us;
        el('manual-steering-range').textContent = `范围 ${data.low_us}～${data.high_us} μs`;
        el('manual-steering-current').textContent = data.target_us ?? '—';
        if (!busy && !pending && !['manual-steering-slider', 'manual-steering-input'].includes(document.activeElement?.id)) {
            preview(data.target_us ?? data.center_us);
        }
        let message = error || incoming.message || '先归中油门，再进入手动测试。';
        if (!data.serial_open) message = data.serial_error || 'ESP32 串口未连接';
        else if (data.active && !data.fresh) message += ' | 舵机状态已过期，请检查连接或归中退出。';
        else if (data.active && !data.precise) message += ' | 旧固件不支持精确脉宽，请烧录新版 esp32dev。';
        else if (data.active && !data.ready) message += ' | 舵机 PWM 未就绪。';
        el('manual-steering-status').textContent = message;
        const failed = !!error || ['error', 'timeout', 'rejected'].includes(incoming.status);
        panel.dataset.error = String(failed);
        el('manual-steering-badge').textContent = failed ? '请检查状态' : pending || busy ? '等待确认' : blocked ? '手动测试中' : '未进入';
        document.body.classList.toggle('manual-steering-active', blocked);
        for (const id of ['throttle-slider', 'throttle-input', 'numeric-output-toggle', 'numeric-boost-toggle']) {
            const control = el(id);
            if (control) control.disabled = blocked;
        }
        if (blocked && el('line-steering-on')) el('line-steering-on').disabled = true;
    }

    async function manualSteeringAction(action, pulse) {
        if (busy) {
            if (action === 'stop') exitQueued = true;
            return;
        }
        busy = true;
        actionInFlight = action;
        error = '';
        if (action === 'start' && typeof global.parkThrottleForManualSteering === 'function') {
            try { global.parkThrottleForManualSteering(); }
            catch (failure) { console.warn('Throttle UI:', failure.message); }
        }
        if (global.dashboardPolling) global.dashboardPolling.invalidate();
        renderManualSteering(state);
        const controller = new AbortController();
        const timeout = setTimeout(() => controller.abort(), 1800);
        try {
            const response = await global.fetch('/steering/manual', { method: 'POST',
                headers: { 'Content-Type': 'application/json' }, signal: controller.signal,
                body: JSON.stringify({ action, ...(pulse === undefined ? {} : { pulse_us: pulse }) }) });
            const data = await response.json();
            if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
            renderManualSteering(data.state);
            if (confirmationTimer !== null) clearTimeout(confirmationTimer);
            if (state.request?.status === 'pending') {
                const requestId = state.request.id;
                confirmationTimer = setTimeout(() => {
                    if (state.request?.id === requestId && state.request.status === 'pending') {
                        error = '未收到固件确认，请检查串口和新版固件；可点击归中退出。';
                        renderManualSteering(state);
                    }
                }, 3500);
            }
        } catch (failure) {
            error = failure.name === 'AbortError' ? '舵机请求超时，不能确认是否执行，请归中退出后重试。' : failure.message;
        } finally {
            clearTimeout(timeout);
            busy = false;
            actionInFlight = '';
            renderManualSteering(state);
            if (global.dashboardPolling) global.dashboardPolling.refresh();
            if (exitQueued) { exitQueued = false; manualSteeringAction('stop'); }
        }
    }
    global.renderManualSteering = renderManualSteering;
    global.manualSteeringBlocking = blocking;
    global.manualSteeringAction = manualSteeringAction;
    global.stepManualSteering = delta => manualSteeringAction('set', Math.max(state.low_us,
        Math.min(state.high_us, (state.target_us ?? state.center_us) + delta)));
    global.applyManualSteering = () => {
        const value = Number(el('manual-steering-input').value);
        if (!Number.isInteger(value) || value < state.low_us || value > state.high_us) {
            error = `请输入 ${state.low_us}～${state.high_us}μs 的整数`;
            renderManualSteering(state);
            return;
        }
        return manualSteeringAction('set', value);
    };
    el('manual-steering-slider').addEventListener('input', event => { el('manual-steering-input').value = event.target.value; });
    el('manual-steering-slider').addEventListener('change', event => manualSteeringAction('set', Number(event.target.value)));
    el('manual-steering-input').addEventListener('keydown', event => {
        if (event.key === 'Enter') { event.preventDefault(); global.applyManualSteering(); }
    });
    panel.addEventListener('toggle', () => {
        if (!panel.open && (blocking() || busy)) manualSteeringAction('stop');
    });
})(typeof window !== 'undefined' ? window : globalThis);
