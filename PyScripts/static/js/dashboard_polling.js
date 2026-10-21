// Keep at most one state request in flight. MJPEG, PWM and device commands
// share the browser's origin connection pool, so slow polls must not accumulate.
(function (global) {
    function createPoller(options) {
        const fetcher = options.fetch;
        const later = options.setTimeout;
        const cancel = options.clearTimeout;
        let current = null, pending = null, scheduled = null, stopped = true, generation = 0;
        function refresh() {
            if (pending) return pending;
            if (scheduled !== null) { cancel(scheduled); scheduled = null; }
            const controller = new AbortController();
            const version = generation;
            current = controller;
            const timeout = later(() => controller.abort(), options.timeoutMs || 1500);
            pending = (async () => {
                try {
                    const response = await fetcher('/dashboard/status', { cache: 'no-store', signal: controller.signal });
                    if (!response.ok) throw new Error(`HTTP ${response.status}`);
                    const state = await response.json();
                    if (version === generation && !controller.signal.aborted) options.onData(state);
                } catch (error) {
                    if (error.name !== 'AbortError' && options.onError) options.onError(error);
                } finally {
                    cancel(timeout);
                    current = null;
                    pending = null;
                    if (!stopped) scheduled = later(refresh, options.intervalMs || 200);
                }
            })();
            return pending;
        }
        return {
            refresh,
            invalidate() { generation++; if (current) current.abort(); },
            start() { stopped = false; return refresh(); },
            stop() { stopped = true; generation++; if (scheduled !== null) cancel(scheduled); if (current) current.abort(); },
        };
    }
    global.createDashboardPoller = createPoller;
    if (typeof document !== 'undefined') {
        global.dashboardPolling = createPoller({
            fetch: global.fetch.bind(global), setTimeout: global.setTimeout.bind(global), clearTimeout: global.clearTimeout.bind(global),
            onData(data) {
                for (const [render, state] of [[global.renderFpsStats, data.fps], [global.renderSensorStats, data.sensor],
                    [global.renderRecordStatus, data.record], [global.updateSpeedRecordUI, data.speed_record],
                    [global.renderSyncState, data.sync], [global.renderSyncConversion, data.conversion],
                    [global.renderCameraReplay, data.replay],
                    [global.renderLineResponse, data.sensor && data.sensor.line_response],
                    [global.renderManualSteering, data.sensor && data.sensor.manual_steering]]) {
                    try { if (typeof render === 'function') render(state); }
                    catch (error) { console.warn('Dashboard render:', render.name, error.message); }
                }
            },
            onError: error => console.warn('Dashboard status:', error.message),
        });
        global.dashboardPolling.start();
        global.addEventListener('pagehide', () => global.dashboardPolling.stop());
        global.addEventListener('pageshow', () => global.dashboardPolling.start());
    }
})(typeof window !== 'undefined' ? window : globalThis);
