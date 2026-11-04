// The fold is presentation only; explicit entry starts the firmware's neutral-power test mode.
(function (global) {
    const el = id => document.getElementById(id);
    const panel = el('manual-steering-panel');
    if (!panel) return;
    let state = { active: false, blocking: false, serial_open: false, request: {},
        low_us: 1200, center_us: 1500, high_us: 1800 };
    let busy = false, actionInFlight = '', error = '', exitQueued = false, confirmationTimer = null;
    let confirmationErrorId = null;
    let calibrationBusy = false;
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
        if (confirmationErrorId === incoming.id && ['confirmed', 'rejected'].includes(incoming.status)) {
            error = ''; confirmationErrorId = null;
        }
        state = data;
        const pending = incoming.status === 'pending';
        if (!pending && confirmationTimer !== null) { clearTimeout(confirmationTimer); confirmationTimer = null; }
        const blocked = blocking();
        const adjust = data.active && data.fresh && data.ready && data.precise && !pending && !busy && !calibrationBusy;
        el('manual-steering-start').disabled = busy || pending || blocked || !data.serial_open;
        el('manual-steering-stop').disabled = busy || !blocked;
        document.querySelectorAll('[data-manual-adjust]').forEach(control => control.disabled = !adjust);
        el('manual-steering-minus').disabled = !adjust || data.target_us <= data.low_us;
        el('manual-steering-plus').disabled = !adjust || data.target_us >= data.high_us;
        el('manual-expand-low').disabled = !adjust || !data.probe_supported || data.low_us <= 1000;
        el('manual-expand-high').disabled = !adjust || !data.probe_supported || data.high_us >= 2000;
        el('manual-probe-hint').textContent = data.probe_supported ? '仅本次手动测试有效，记录端点不自动应用为控制限位。' : '扩大探测范围需要重新烧录新版 esp32dev；原范围内可正常记录。';
        document.querySelectorAll('[data-calibration-save]').forEach(control => control.disabled = !adjust || incoming.status !== 'confirmed' || calibrationBusy);
        const centerLow = data.center_min_us ?? 1300, centerHigh = data.center_max_us ?? 1700;
        el('manual-steering-save-center').disabled = !adjust || !data.calibration_supported ||
            data.target_us < centerLow || data.target_us > centerHigh;
        el('manual-steering-calibration-hint').textContent = !data.calibration_supported
            ? '保存中位需要本次更新的固件。默认1500μs不一定对应车轮直行。'
            : `先微调至车轮直行，再保存当前输出；掉电后仍有效。可标定中位 ${centerLow}～${centerHigh}μs，保存后范围为新中位±300μs。`;
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
        renderCalibration(data.calibration || {});
    }

    function renderCalibration(calibration) {
        const endpoints = calibration.endpoints || {};
        for (const side of ['left', 'right']) {
            const p = endpoints[side];
            el(`manual-${side}-endpoint`).textContent = p ? `${p.pulse_us} μs${p.angle_deg == null ? '（角度未测）' : ` / ${p.angle_deg}°`}` : '未记录';
        }
        el('manual-estimated-angle').textContent = calibration.estimated_angle_deg == null ? '—' : `${calibration.estimated_angle_deg.toFixed(1)}°`;
        el('manual-angle-hint').textContent = calibration.interpolation_error || '由实测点分段插值；不是实时角度反馈';
        const points = new Map((calibration.points || []).map(p => [p.pulse_us, {...p}]));
        for (const [side,p] of Object.entries(endpoints)) points.set(p.pulse_us, {...(points.get(p.pulse_us) || p), side});
        const sorted = [...points.values()].sort((a,b)=>a.pulse_us-b.pulse_us);
        el('manual-calibration-points').replaceChildren(...sorted.map(p => {
            const row = document.createElement('tr');
            for (const value of [p.pulse_us, p.angle_deg ?? '未测', p.side === 'left' ? '最左' : p.side === 'right' ? '最右' : p.pulse_us === calibration.center_us ? '直行中位' : '测量点']) {
                const cell = document.createElement('td'); cell.textContent = value; row.appendChild(cell);
            }
            const cell = document.createElement('td'), button = document.createElement('button');
            button.textContent = '删除'; button.disabled = calibrationBusy;
            button.addEventListener('click',()=>calibrationRequest('remove',{pulse_us:p.pulse_us}));
            cell.appendChild(button);row.appendChild(cell);return row;
        }));
        drawCalibration((calibration.points || []).slice().sort((a,b)=>a.pulse_us-b.pulse_us));
    }
    function drawCalibration(points) {
        const canvas=el('manual-calibration-chart'), width=canvas.clientWidth;
        if (!width) return;
        const height=200, ratio=global.devicePixelRatio||1;
        canvas.width=width*ratio;canvas.height=height*ratio;
        const ctx=canvas.getContext('2d');ctx.setTransform(ratio,0,0,ratio,0,0);
        ctx.clearRect(0,0,width,height);ctx.font='11px system-ui';
        const minX=Math.min(1200,...points.map(p=>p.pulse_us)),maxX=Math.max(1800,...points.map(p=>p.pulse_us));
        const maxY=Math.max(10,...points.map(p=>Math.abs(p.angle_deg)))*1.15;
        const x=v=>42+(width-58)*(v-minX)/(maxX-minX), y=v=>15+(height-50)*(maxY-v)/(2*maxY);
        ctx.strokeStyle='#3b596b';ctx.fillStyle='#a0b4c0';ctx.lineWidth=1;
        for (const value of [-maxY,0,maxY]) {ctx.beginPath();ctx.moveTo(42,y(value));ctx.lineTo(width-16,y(value));ctx.stroke();ctx.fillText(`${value.toFixed(0)}°`,3,y(value)+4);}
        ctx.fillText(`${minX}`,42,height-14);ctx.fillText(`${maxX} μs`,width-70,height-14);
        const differences=points.slice(1).map((p,i)=>p.angle_deg-points[i].angle_deg);
        if (points.length>1 && (differences.every(d=>d>.1)||differences.every(d=>d<-.1))) {
            ctx.beginPath();points.forEach((p,i)=>i?ctx.lineTo(x(p.pulse_us),y(p.angle_deg)):ctx.moveTo(x(p.pulse_us),y(p.angle_deg)));
            ctx.strokeStyle='#66bdf5';ctx.lineWidth=2;ctx.stroke();
        }
        ctx.fillStyle='#ffd875';for (const p of points){ctx.beginPath();ctx.arc(x(p.pulse_us),y(p.angle_deg),4,0,2*Math.PI);ctx.fill();}
        if (!points.length) {ctx.fillStyle='#a0b4c0';ctx.fillText('记录人工测量点后显示曲线',55,75);}
    }
    async function calibrationRequest(operation, body) {
        if (calibrationBusy || busy) return null;
        calibrationBusy=true; renderManualSteering(state);
        const controller=new AbortController(), timeout=setTimeout(()=>controller.abort(),3000);
        try {
            const url=operation==='preview'?'/vehicle-lab/actuator/preview':`/steering/manual/calibration/${operation}`;
            const response=await global.fetch(url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body),signal:controller.signal});
            const data=await response.json();if(!response.ok)throw new Error(data.error||`HTTP ${response.status}`);
            el('manual-calibration-message').textContent=operation==='preview'?'查询完成，没有发送舵机命令。':'标定记录已保存，没有移动舵机。';
            if (data.points) state={...state,calibration:data};
            return data;
        } catch(failure){el('manual-calibration-message').textContent=failure.name==='AbortError'?'保存请求超时，请检查记录是否已保存后再重试':failure.message;return null;}
        finally {clearTimeout(timeout);calibrationBusy=false;renderManualSteering(state);if(global.dashboardPolling)global.dashboardPolling.refresh();}
    }
    global.recordManualCalibration=kind=>{
        const text=el('manual-wheel-angle').value.trim(), angle=text===''?null:Number(text);
        if ((kind==='point'&&angle===null)|| (angle!==null&&(!Number.isFinite(angle)||Math.abs(angle)>45))) {
            el('manual-calibration-message').textContent='请填写实测前轮角度（±45°内，左正右负）；仅记录端点时可留空。';return;
        }
        const observed={pulse_us:state.target_us,request_id:state.request?.id};
        return calibrationRequest(kind==='point'?'point':'endpoint',kind==='point'?{angle_deg:angle,...observed}:{side:kind,angle_deg:angle,...observed});
    };
    global.previewManualAngle=async()=>{
        const text=el('manual-preview-angle').value.trim(), angle=Number(text);
        if(!text||!Number.isFinite(angle)){el('manual-angle-preview').textContent='请填写要查询的角度';return;}
        const result=await calibrationRequest('preview',{angle_deg:angle});
        el('manual-angle-preview').textContent=result?`${angle}° ≈ ${result.pulse_us} μs（实测表插值，未发送）`:'查询失败，请查看下方原因';
    };

    async function manualSteeringAction(action, pulse) {
        if (busy) {
            if (action === 'stop') exitQueued = true;
            return;
        }
        busy = true;
        actionInFlight = action;
        error = '';
        confirmationErrorId = null;
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
                        confirmationErrorId = requestId;
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
