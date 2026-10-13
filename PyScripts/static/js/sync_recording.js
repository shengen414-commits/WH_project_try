let syncRequestPending = false;
let syncPollPending = false;
let syncLastState = {};
let syncLastPressure = {};
let syncPressureHistory = [];
let syncLastPressureFrame = '';
let syncPressureSource = '';
const syncValue = id => document.getElementById(id).value.trim();
const syncPhases = { idle: '空闲', starting: '正在启动', waiting_for_data: '等待双方数据', recording: '正在记录', stopping: '正在停止', saving: '正在保存和匹配', saved: '保存完成', error: '记录失败', stop_failed: 'STOP 未确认，请重试停止' };

function syncStartBlockers(data, pressure, busy) {
    const reasons = [];
    if (busy) reasons.push('设备操作进行中，请等待完成');
    if (data.active) reasons.push('已有同步记录正在运行或保存');
    if (!data.encoder_fresh) {
        const encoder = data.encoder || {};
        if (encoder.serial_open === false) reasons.push(`ESP32 串口未连接（${encoder.port || '请检查后台日志'}）`);
        else if (encoder.error) reasons.push(`ESP32 串口读取失败：${encoder.error}`);
        else if (encoder.debug_mode === 'servo') reasons.push('ESP32 正在舵机调试，只上报 [SERVO]；请恢复正常上报（R）');
        else if (encoder.debug_mode === 'line') reasons.push('ESP32 正在巡线调试；请恢复正常上报（R）');
        else reasons.push('未收到新鲜编码器数据；请确认正常固件、串口连接及 R 模式');
    }
    if (!pressure.connected) reasons.push('扫描阀未连接');
    if (pressure.reboot_required) reasons.push('扫描阀需断电重启后重新连接');
    if (!data.active && pressure.scanning) reasons.push('压力单独采集中，请先 STOP 并关闭压力文件');
    else if (!data.active && pressure.file_open) reasons.push('已有压力文件未关闭，请先关闭压力文件');
    if (pressure.connected && !busy && (pressure.rate_hz == null || pressure.fps == null || !pressure.udp_target_ip))
        reasons.push('扫描参数不完整，请读取设备信息并确认 RATE、FPS 和 UDP 目标');
    return reasons;
}

function renderSyncState(data) {
    syncLastState = data;
    const incomingPressure = data.pressure || {};
    if (!incomingPressure.busy) syncLastPressure = incomingPressure;
    const pressure = incomingPressure.busy ? syncLastPressure : incomingPressure;
    const speed = data.speed || {};
    const busy = syncRequestPending || data.device_busy || incomingPressure.busy;
    document.querySelectorAll('[data-sync-config]').forEach(btn => btn.disabled = !!(busy || data.active || pressure.file_open || pressure.scanning));
    document.querySelectorAll('[data-sync-command]').forEach(btn => btn.disabled = !!(busy || data.active || pressure.scanning || !pressure.connected));
    const startReasons = syncStartBlockers(data, pressure, busy);
    document.getElementById('sync-start').disabled = startReasons.length > 0;
    document.getElementById('sync-start').title = startReasons.join('；');
    document.getElementById('sync-stop').disabled = syncRequestPending || !data.active || ['stopping', 'saving'].includes(data.phase);
    document.getElementById('sync-stop').innerText = data.phase === 'stop_failed' ? '重试停止并保存' : '停止并保存';
    document.getElementById('sync-stop').title = !data.active ? '尚未开始同步记录，无需停止' : ['stopping', 'saving'].includes(data.phase) ? '正在停止或保存，请等待完成' : '';
    document.getElementById('sync-readiness').innerText = data.active ? `同步记录${syncPhases[data.phase] || data.phase}；停止后会保存双方数据。` : startReasons.length ? `暂不能开始：${startReasons.join('；')}。停止并保存将在同步记录开始后可用。` : '车速和扫描阀已就绪，可以开始同步记录；停止并保存将在开始后可用。';
    const recoverEncoder = document.getElementById('sync-encoder-normal');
    recoverEncoder.hidden = !!(data.active || data.encoder_fresh || !data.encoder?.serial_open);
    recoverEncoder.disabled = !!busy;
    recoverEncoder.title = '发送 R 退出舵机/巡线调试；恢复后重新允许正常油门控制';
    if (!recoverEncoder.hidden) document.getElementById('sync-readiness').innerText += ' 恢复 R 后将重新允许正常油门控制。';
    syncLight('sync-connection-light', busy ? 'busy' : pressure.connected ? 'on' : 'off');
    document.getElementById('sync-connection-text').innerText = busy ? '设备操作中' : pressure.connected ? '已连接' : '未连接';
    const freshUDP = pressure.scanning && pressure.last_receive_unix_ns && (Date.now() - pressure.last_receive_unix_ns / 1e6) < 1500;
    syncLight('sync-data-light', freshUDP ? 'on' : pressure.scanning ? 'busy' : 'off');
    document.getElementById('sync-data-text').innerText = freshUDP ? 'UDP 接收中' : pressure.scanning ? '等待 UDP' : 'UDP 待机';
    document.getElementById('sync-terminal-send').disabled = !!(syncRequestPending || !pressure.connected);
    const owned = data.active || busy;
    document.getElementById('sync-pressure-new').disabled = !!(owned || pressure.file_open || !pressure.connected);
    document.getElementById('sync-pressure-scan').disabled = !!(owned || !pressure.file_open || pressure.scanning || pressure.reboot_required);
    document.getElementById('sync-pressure-stop').disabled = !!(syncRequestPending || data.active || !pressure.scanning);
    document.getElementById('sync-pressure-close').disabled = !!(owned || !pressure.file_open);
    document.getElementById('sync-convert').disabled = !!(owned || pressure.file_open);
    const device = pressure.busy ? '设备操作中' : pressure.connected ? `已连接 ${pressure.device_ip}` : '未连接';
    const phaseLabel = !data.active && pressure.file_open ? (pressure.scanning ? '压力单独采集中' : '压力文件已打开') : syncPhases[data.phase] || data.phase;
    let text = `${phaseLabel} | 扫描阀：${device} | 编码器：${data.encoder_fresh ? '就绪' : '无新鲜数据'} | 车速 ${speed.sample_count || 0} 条 | 压力 ${pressure.frames_received || 0} 帧`;
    text += ` | 车速缺样 ${speed.sequence_gaps || 0} / 写盘丢样 ${speed.queue_drops || 0} | 压力缺帧 ${pressure.frame_gaps || 0}`;
    if (pressure.reboot_required) text += ' | 请等待保存完成，给扫描阀断电重启后重新连接';
    if (pressure.udp_reported_port < 0) text += ` | UDP 端口回显 ${pressure.udp_reported_port}，按 16 位还原为 ${pressure.udp_port}`;
    if (data.folder) text += ` | 目录：${data.folder}`;
    if (data.alignment) text += ` | 成功匹配 ${data.alignment.matched_rows} / ${data.alignment.pressure_rows}`;
    if (data.error) text += ` | ${data.error}`;
    if (data.manifest_error) text += ` | 实验信息保存失败：${data.manifest_error}`;
    document.getElementById('sync-status').innerText = text;
    const network = data.network;
    document.getElementById('sync-network-status').innerText = network?.error ? `网口配置失败：${network.error}` : network?.skipped ? `网口配置已跳过：${network.reason}` : network ? `网口 ${network.interface} · ${network.cidr} · ${network.added ? '已添加地址' : '地址已存在'}` : '尚未检查网口地址';
    updateSyncPressure(pressure);
}

async function refreshSyncStatus() {
    if (window.dashboardPolling) return window.dashboardPolling.refresh();
    if (syncPollPending) return;
    syncPollPending = true;
    try {
        const response = await fetch('/sync_record/status', { cache: 'no-store' });
        const data = await response.json();
        if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
        renderSyncState(data);
    } catch (error) {
        syncLight('sync-connection-light', 'error');
        document.getElementById('sync-connection-text').innerText = '状态不可用';
        document.getElementById('sync-status').innerText = `状态读取失败：${error.message}`;
    } finally {
        syncPollPending = false;
    }
}

async function syncPost(endpoint, data) {
    if (syncRequestPending) return;
    syncRequestPending = true;
    if (window.dashboardPolling) window.dashboardPolling.invalidate();
    renderSyncState(syncLastState);
    document.getElementById('sync-message').innerText = '操作进行中，请等待设备返回。';
    const command = data.command;
    const started = performance.now();
    const traceId = `car-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
    const url = `${endpoint}?_trace=${traceId}`;
    if (command) appendSyncTerminal(`> ${command}`);
    try {
        const response = await fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-MPS-Trace-ID': traceId }, body: JSON.stringify(data) });
        const result = await response.json();
        const totalMs = performance.now() - started;
        if (command) appendSyncTerminal(result.result?.response || result.error || JSON.stringify(result.result || result));
        appendSyncTiming(url, totalMs, result._timing, traceId);
        if (!response.ok) throw new Error(result.error || `HTTP ${response.status}`);
        document.getElementById('sync-message').innerText = result.message || '操作完成';
        if (result.result) document.getElementById('sync-device-info').innerText = JSON.stringify(result.result, null, 2);
        if (result.status) renderSyncState(result.status);
        if (endpoint.endsWith('/close-file')) await refreshSyncFiles();
        return result;
    } catch (error) {
        document.getElementById('sync-message').innerText = error.message;
        if (command) appendSyncTerminal(`错误：${error.message}`);
    } finally {
        syncRequestPending = false;
        await refreshSyncStatus();
    }
}

function syncDevice(action) {
    const reconnect = action === 'reconnect';
    let data = {};
    if (action === 'connect' || reconnect) data = { device_ip: syncValue('sync-device-ip'), control_port: Number(syncValue('sync-control-port')), interface: syncValue('sync-interface'), local_cidr: syncValue('sync-local-cidr'), init_network: document.getElementById('sync-init-network').checked, confirm_reboot: reconnect };
    if (action === 'udp') data = { host_ip: syncValue('sync-local-cidr').split('/')[0], udp_port: Number(syncValue('sync-udp-port')) };
    if (action === 'parameters') data = { rate: Number(syncValue('sync-rate')), fps: Number(syncValue('sync-fps')), fast_group: Number(syncValue('sync-group')), read_mode: Number(syncValue('sync-read-mode')), subset_size: Number(syncValue('sync-subset')) };
    if (action === 'calz') data = { zero_pressure_confirmed: document.getElementById('sync-zero-confirmed').checked };
    if (action === 'new-file') data = { name: syncValue('sync-pressure-name') };
    return syncPost(`/sync_record/device/${reconnect ? 'connect' : action}`, data);
}
function startSyncRecording() {
    return syncPost('/sync_record/start', { duration_sec: Number(syncValue('sync-duration')), max_delta_ms: Number(syncValue('sync-delta')) });
}
function stopSyncRecording() { return syncPost('/sync_record/stop', {}); }
function restoreSyncEncoder() { return syncPost('/sync_record/encoder/normal', {}); }
function syncLight(id, state) { document.getElementById(id).dataset.state = state; }
function appendSyncTerminal(text) {
    const output = document.getElementById('sync-terminal-output');
    output.innerText = `${output.innerText}\n${text}`.slice(-30000);
    output.scrollTop = output.scrollHeight;
}
function appendSyncTiming(url, totalMs, timing, traceId) {
    const ms = value => value === null || value === undefined ? '未取得' : `${Number(value).toFixed(1)} ms`;
    const entries = performance.getEntriesByName(new URL(url, location.href).href);
    const entry = entries.at(-1);
    const rawBeforeSend = entry && entry.requestStart > 0 ? entry.requestStart - entry.fetchStart : null;
    // Headless virtual time or inconsistent browser clocks can produce an
    // impossible stage duration. Do not report it as real request queuing.
    const beforeSend = rawBeforeSend !== null && rawBeforeSend >= 0 && rawBeforeSend <= totalMs + 5 ? rawBeforeSend : null;
    const outsideServer = timing ? Math.max(0, totalMs - timing.server_ms) : null;
    let message = `[${timing?.trace_id || traceId}] 总 ${ms(totalMs)} | 后台 ${ms(timing?.server_ms)} | 浏览器发出前 ${ms(beforeSend)} | 网络/排队/前端 ${ms(outsideServer)}`;
    message += `\n  协调锁 ${ms(timing?.coordinator_lock_wait_ms)} | 设备锁 ${ms(timing?.controller_lock_wait_ms)} | TCP 锁 ${ms(timing?.tcp_lock_wait_ms)}`;
    for (const item of timing?.commands || []) message += `\n  ${item.command}: 发送 ${ms(item.send_ms)} | 首字节 ${ms(item.first_tcp_byte_ms)} | 完整回复 ${ms(item.response_ms)} | ${item.outcome}`;
    appendSyncTerminal(message);
    console.info('MPS command timing', { total_ms: totalMs, before_send_ms: beforeSend, before_send_raw_ms: rawBeforeSend, outside_server_ms: outsideServer, server: timing });
}
function sendSyncCommand(command) {
    const line = command || syncValue('sync-terminal-input');
    if (!line) return;
    return syncPost('/sync_record/device/command', { command: line, duration_sec: Number(syncValue('sync-duration')), max_delta_ms: Number(syncValue('sync-delta')), zero_pressure_confirmed: document.getElementById('sync-zero-confirmed').checked });
}
function updateSyncPressure(pressure) {
    const source = `${pressure.status_epoch || ''}:${pressure.file_name}`;
    if (pressure.file_open && source !== syncPressureSource) {
        syncPressureSource = source;
        syncPressureHistory = [];
        syncLastPressureFrame = '';
    }
    const frame = pressure.last_frame;
    const key = `${pressure.status_epoch || ''}:${pressure.file_name}:${frame?.number}:${pressure.last_receive_unix_ns}`;
    if (frame && key !== syncLastPressureFrame) {
        syncLastPressureFrame = key;
        syncPressureHistory.push({ time: pressure.last_receive_unix_ns / 1e9, values: frame.pressures });
        if (syncPressureHistory.length > 250) syncPressureHistory.shift();
    }
    if (!document.getElementById('sync-advanced').open) return;
    document.getElementById('sync-pressure-metrics').innerText = `UDP ${pressure.bytes_received || 0} B | 有效帧 ${pressure.bytes_saved || 0} B | 最新帧 ${frame?.number ?? '—'} | 无效包 ${pressure.bad_datagrams || 0} | 单位 ${frame?.unit_label || '—'}`;
    document.getElementById('sync-events').innerText = (pressure.events || []).map(item => `${item.time} ${item.message}`).join('\n') || '暂无设备事件';
    const grid = document.getElementById('sync-channel-grid');
    grid.replaceChildren();
    (frame?.pressures || []).forEach((value, index) => {
        const cell = document.createElement('div');
        cell.innerText = `P${String(index + 1).padStart(2, '0')} ${value === null || !Number.isFinite(value) || value === -999999 ? '—' : Number(value).toFixed(3)}`;
        grid.append(cell);
    });
    drawSyncPressure();
}
function drawSyncPressure() {
    const canvas = document.getElementById('sync-pressure-chart');
    const ctx = canvas.getContext('2d');
    const channels = [...new Set(syncValue('sync-preview-channels').split(',').map(Number).filter(number => Number.isInteger(number) && number >= 1 && number <= 64))].slice(0, 6);
    const width = canvas.width, height = canvas.height;
    ctx.clearRect(0, 0, width, height);
    ctx.fillStyle = '#9aaebe'; ctx.font = '16px sans-serif';
    const points = syncPressureHistory.flatMap(point => channels.map(number => point.values[number - 1])).filter(value => Number.isFinite(value) && value !== -999999);
    if (!points.length) { ctx.fillText('等待有效压力数据', 25, 45); return; }
    const low = Math.min(...points), high = Math.max(...points), margin = Math.max((high - low) * .1, .001);
    const minimum = low - margin, maximum = high + margin;
    const colors = ['#62d4e4', '#ffa85b', '#77dca2', '#e898d0', '#aaa0ff', '#ebd968'];
    for (let n = 0; n <= 4; n++) {
        const y = 35 + n * (height - 65) / 4;
        ctx.strokeStyle = '#263d4b'; ctx.beginPath(); ctx.moveTo(78, y); ctx.lineTo(width - 15, y); ctx.stroke();
        ctx.fillStyle = '#9aaebe'; ctx.fillText((maximum - n * (maximum - minimum) / 4).toFixed(2), 5, y + 5);
    }
    const firstTime = syncPressureHistory[0].time;
    const span = Math.max(syncPressureHistory.at(-1).time - firstTime, .001);
    channels.forEach((channel, index) => {
        ctx.strokeStyle = colors[index]; ctx.beginPath(); let drawing = false;
        syncPressureHistory.forEach(point => {
            const value = point.values[channel - 1];
            if (!Number.isFinite(value) || value === -999999) { drawing = false; return; }
            const x = 78 + (width - 93) * (point.time - firstTime) / span;
            const y = height - 30 - (height - 65) * (value - minimum) / (maximum - minimum);
            if (drawing) ctx.lineTo(x, y); else ctx.moveTo(x, y);
            drawing = true;
        });
        ctx.stroke(); ctx.fillStyle = colors[index]; ctx.fillText(`P${channel}`, 80 + index * 90, 20);
    });
}
async function refreshSyncFiles() {
    try {
        const response = await fetch('/sync_record/files', { cache: 'no-store' });
        const data = await response.json();
        if (!response.ok) throw new Error(data.error);
        const select = document.getElementById('sync-files'); const selected = select.value;
        select.replaceChildren();
        for (const item of data.files) {
            const option = document.createElement('option'); option.value = item.name;
            option.innerText = `${item.name} (${item.bytes} B${item.csv_exists ? ' · 已有 CSV' : ''})`;
            select.append(option);
        }
        if (data.files.some(item => item.name === selected)) select.value = selected;
    } catch (error) { document.getElementById('sync-convert-status').innerText = error.message; }
}
function convertSyncFile() {
    return syncPost('/sync_record/convert', { filename: syncValue('sync-files'), fast_group: syncValue('sync-convert-group'), overwrite: document.getElementById('sync-convert-overwrite').checked });
}
function downloadSyncFile(csv) {
    let filename = syncValue('sync-files'); if (!filename) return;
    if (csv) filename = filename.replace(/\.dat$/i, '.csv');
    window.location.assign(`/sync_record/download?filename=${encodeURIComponent(filename)}`);
}
async function refreshSyncConversion() {
    if (window.dashboardPolling) return window.dashboardPolling.refresh();
    if (!document.getElementById('sync-advanced').open) return;
    try {
        const response = await fetch('/sync_record/convert/status', { cache: 'no-store' });
        const job = await response.json();
        if (!response.ok) throw new Error(job.error);
        renderSyncConversion(job);
    } catch (error) { document.getElementById('sync-convert-status').innerText = error.message; }
}
function renderSyncConversion(job) {
    document.getElementById('sync-convert-status').innerText = job.phase === 'running' ? '正在转换…' : job.phase === 'done' ? `已转换 ${job.result.frames} 帧 → ${job.result.csv_file}` : job.phase === 'error' ? `转换失败：${job.error}` : '尚无转换任务';
}
document.getElementById('sync-advanced').addEventListener('toggle', () => {
    if (document.getElementById('sync-advanced').open) {
        updateSyncPressure(syncLastPressure); refreshSyncFiles(); refreshSyncConversion();
    }
});
// Periodic state/conversion refresh is owned by dashboard_polling.js.
