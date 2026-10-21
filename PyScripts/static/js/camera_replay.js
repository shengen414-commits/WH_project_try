// Rendered by the shared dashboard poller; recording runs independently of this page.
(function (global) {
    let state = { enabled: false, cameras: {}, export: {} }, busy = false, error = '', filesKey = '';
    const element = id => document.getElementById(id);

    function renderCameraReplay(data) {
        if (!data || !element('replay-toggle')) return;
        state = data;
        const job = data.export || {}, cameras = Object.entries(data.cameras || {});
        element('replay-toggle').disabled = busy;
        element('replay-toggle').textContent = data.enabled ? '停止回放缓存' : '开启一分钟回放';
        element('replay-fps').disabled = busy || data.enabled;
        const recording = cameras.find(([, cam]) => cam.enabled);
        if (recording && !busy) element('replay-fps').value = recording[1].target_fps;
        element('replay-save').disabled = busy || !!job.active || !cameras.some(([, cam]) => cam.cached_frames > 0);
        element('replay-cameras').textContent = cameras.map(([name, cam]) => {
            const drops = (cam.queue_drops || 0) + (cam.busy_drops || 0);
            const status = cam.error ? `错误：${cam.error}` : (cam.enabled ? '缓存中' : '未开启');
            return `${name}：${status} | 已缓存 ${(cam.buffered_sec || 0).toFixed(1)} / ${cam.history_sec || data.history_sec || 60} 秒` +
                ` | 目标 ${cam.target_fps || 200} fps | 缓存 ${((cam.cache_bytes || 0) / 1048576).toFixed(1)} MiB` +
                ` | 丢帧 ${drops}` + (cam.limited_by_bytes ? ' | 容量限制，历史可能不足一分钟' : '');
        }).join('\n');
        const coverage = Object.entries(job.cameras || {}).map(([name, cam]) => `${name} ${(cam.coverage_sec || 0).toFixed(1)}秒 / ${cam.frames}帧`).join('；');
        const failures = Object.entries(job.errors || {}).map(([name, message]) => `${name}：${message}`).join('；');
        element('replay-job').textContent = error || [job.message, coverage, failures].filter(Boolean).join(' | ');
        const files = job.files || [], key = JSON.stringify(files);
        if (key !== filesKey) {
            filesKey = key;
            element('replay-files').replaceChildren();
            for (const filename of files) {
                const link = document.createElement('a');
                link.href = '/camera/replay/download/' + filename.split('/').map(encodeURIComponent).join('/');
                link.textContent = filename.split('/').pop();
                link.download = '';
                element('replay-files').appendChild(link);
            }
        }
    }

    async function action(path, body) {
        if (busy) return;
        busy = true;
        error = '';
        if (global.dashboardPolling) global.dashboardPolling.invalidate();
        renderCameraReplay(state);
        try {
            const response = await global.fetch(path, { method: 'POST',
                headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body || {}) });
            const result = await response.json();
            if (!response.ok) throw new Error(result.error || '回放操作失败');
            if (path.endsWith('/config')) state = result;
            else state = { ...state, export: result };
        } catch (failure) {
            error = failure.message;
        } finally {
            busy = false;
            renderCameraReplay(state);
            if (global.dashboardPolling) global.dashboardPolling.refresh();
        }
    }
    global.renderCameraReplay = renderCameraReplay;
    global.toggleCameraReplay = () => action('/camera/replay/config', {
        enabled: !state.enabled, fps: Number(element('replay-fps').value) });
    global.saveCameraReplay = () => action('/camera/replay/save');
})(typeof window !== 'undefined' ? window : globalThis);
