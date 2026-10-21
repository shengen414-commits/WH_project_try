// Finite JPEG requests release HTTP connections between frames and on page exit.
(function (global) {
    function createCameraPreview(options) {
        let enabled = false, generation = 0, timer = null, pending = null, objectUrl = null;
        const later = options.setTimeout, cancel = options.clearTimeout;
        function clearImage() {
            if (objectUrl !== null) options.revokeObjectURL(objectUrl);
            objectUrl = null;
            options.clearImage();
        }
        async function tick() {
            if (!enabled || pending) return;
            const controller = new AbortController(), version = generation;
            pending = controller;
            const timeout = later(() => controller.abort(), options.timeoutMs || 1500);
            try {
                const response = await options.fetch(options.url, {cache: 'no-store', signal: controller.signal});
                if (response.status === 204) {
                    if (version === generation) clearImage();
                } else {
                    if (!response.ok) throw new Error(`HTTP ${response.status}`);
                    const blob = await response.blob();
                    if (enabled && version === generation && !controller.signal.aborted) {
                        const next = options.createObjectURL(blob), previous = objectUrl;
                        objectUrl = next; options.showImage(next);
                        if (previous !== null) options.revokeObjectURL(previous);
                    }
                }
            } catch (_) {
                // Retry without piling requests behind a camera/network failure.
            } finally {
                cancel(timeout); pending = null;
                if (enabled) timer = later(tick, options.intervalMs || 80);
            }
        }
        return {
            setEnabled(value) {
                value = !!value;
                if (value === enabled) return;
                enabled = value; generation++;
                if (timer !== null) cancel(timer);
                timer = null;
                if (pending) pending.abort();
                if (!enabled) clearImage();
                else tick();
            }
        };
    }
    global.createCameraPreview = createCameraPreview;
    if (typeof document === 'undefined') return;
    let suspended = document.hidden;
    const wanted = {}, previews = {};
    for (const camera of ['left', 'right']) {
        const img = document.getElementById(`img-${camera}`);
        previews[camera] = createCameraPreview({url: `/camera/preview/${camera}`,
            fetch: global.fetch.bind(global), setTimeout: global.setTimeout.bind(global), clearTimeout: global.clearTimeout.bind(global),
            createObjectURL: global.URL.createObjectURL.bind(global.URL), revokeObjectURL: global.URL.revokeObjectURL.bind(global.URL),
            showImage: url => { img.src = url; }, clearImage: () => img.removeAttribute('src')});
    }
    function apply() {
        for (const camera of ['left', 'right']) previews[camera].setEnabled(wanted[camera] && !suspended);
    }
    global.cameraPreview = {setEnabled(camera, value) { wanted[camera] = !!value; apply(); }};
    global.addEventListener('pagehide', () => { suspended = true; apply(); });
    global.addEventListener('pageshow', () => { suspended = document.hidden; apply(); });
    document.addEventListener('visibilitychange', () => { suspended = document.hidden; apply(); });
})(typeof window !== 'undefined' ? window : globalThis);
