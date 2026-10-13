let syncRequestPending = false;
let syncPollPending = false;
let syncLastState = {};
const syncValue = id => document.getElementById(id).value.trim();
const syncPhases = { idle: '空闲', starting: '正在启动', waiting_for_data: '等待双方数据', recording: '正在记录', stopping: '正在停止', saving: '正在保存和匹配', saved: '保存完成', error: '记录失败', stop_failed: 'STOP 未确认，请重试停止' };

function renderSyncState(data) {
    syncLastState = data;
    const pressure = data.pressure || {};
    const speed = data.speed || {};
    const busy = syncRequestPending || data.device_busy || pressure.busy;
    document.querySelectorAll('[data-sync-config]').forEach(btn => btn.disabled = !!(busy || data.active));
    document.getElementById('sync-start').disabled = !!(busy || data.active || !data.encoder_fresh || !pressure.connected || pressure.reboot_required);
    document.getElementById('sync-stop').disabled = syncRequestPending || !data.active || ['stopping', 'saving'].includes(data.phase);
    document.getElementById('sync-stop').innerText = data.phase === 'stop_failed' ? '重试停止并保存' : '停止并保存';
    const device = pressure.busy ? '设备操作中' : pressure.connected ? `已连接 ${pressure.device_ip}` : '未连接';
    let text = `${syncPhases[data.phase] || data.phase} | 扫描阀：${device} | 编码器：${data.encoder_fresh ? '就绪' : '无新鲜数据'} | 车速 ${speed.sample_count || 0} 条 | 压力 ${pressure.frames_received || 0} 帧`;
    text += ` | 车速缺样 ${speed.sequence_gaps || 0} / 写盘丢样 ${speed.queue_drops || 0} | 压力缺帧 ${pressure.frame_gaps || 0}`;
    if (pressure.reboot_required) text += ' | 请等待保存完成，给扫描阀断电重启后重新连接';
    if (data.folder) text += ` | 目录：${data.folder}`;
    if (data.alignment) text += ` | 成功匹配 ${data.alignment.matched_rows} / ${data.alignment.pressure_rows}`;
    if (data.error) text += ` | ${data.error}`;
    if (data.manifest_error) text += ` | 实验信息保存失败：${data.manifest_error}`;
    document.getElementById('sync-status').innerText = text;
}

async function refreshSyncStatus() {
    if (syncPollPending) return;
    syncPollPending = true;
    try {
        const response = await fetch('/sync_record/status', { cache: 'no-store' });
        const data = await response.json();
        if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
        renderSyncState(data);
    } catch (error) {
        document.getElementById('sync-status').innerText = `状态读取失败：${error.message}`;
    } finally {
        syncPollPending = false;
    }
}

async function syncPost(endpoint, data) {
    if (syncRequestPending) return;
    syncRequestPending = true;
    renderSyncState(syncLastState);
    document.getElementById('sync-message').innerText = '操作进行中；设置 UDP 保存可能需要较长时间，请等待返回后再给设备断电。';
    try {
        const response = await fetch(endpoint, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data) });
        const result = await response.json();
        if (!response.ok) throw new Error(result.error || `HTTP ${response.status}`);
        document.getElementById('sync-message').innerText = '操作完成';
        if (result.result) document.getElementById('sync-device-info').innerText = JSON.stringify(result.result, null, 2);
    } catch (error) {
        document.getElementById('sync-message').innerText = error.message;
    } finally {
        syncRequestPending = false;
        await refreshSyncStatus();
    }
}

function syncDevice(action) {
    const reconnect = action === 'reconnect';
    let data = {};
    if (action === 'connect' || reconnect) data = { device_ip: syncValue('sync-device-ip'), interface: syncValue('sync-interface'), local_cidr: syncValue('sync-local-cidr'), init_network: document.getElementById('sync-init-network').checked, confirm_reboot: reconnect };
    if (action === 'udp') data = { host_ip: syncValue('sync-local-cidr').split('/')[0], udp_port: Number(syncValue('sync-udp-port')) };
    if (action === 'parameters') data = { rate: Number(syncValue('sync-rate')), fps: Number(syncValue('sync-fps')), fast_group: Number(syncValue('sync-group')) };
    return syncPost(`/sync_record/device/${reconnect ? 'connect' : action}`, data);
}
function startSyncRecording() {
    return syncPost('/sync_record/start', { duration_sec: Number(syncValue('sync-duration')), max_delta_ms: Number(syncValue('sync-delta')) });
}
function stopSyncRecording() { return syncPost('/sync_record/stop', {}); }
setInterval(refreshSyncStatus, 1000);
refreshSyncStatus();
