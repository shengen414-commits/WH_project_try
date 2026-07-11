const PPR = 12.0; // 编码器分辨率
const KMH_PER_RPM = 0.007173; // 时速换算系数：km/h = abs(RPM) * KMH_PER_RPM
let isRcMode = false;
let maxKmh = 0.0;

// --- 摄像头切换逻辑 ---
function syncCameraLayout() {
    const leftOn = document.getElementById('cam-toggle-left').checked;
    const rightOn = document.getElementById('cam-toggle-right').checked;
    const videoContainer = document.getElementById('video-container');
    const leftBox = document.getElementById('box-left');
    const rightBox = document.getElementById('box-right');
    const leftImg = document.getElementById('img-left');
    const rightImg = document.getElementById('img-right');
    const visibleCount = (leftOn ? 1 : 0) + (rightOn ? 1 : 0);

    videoContainer.style.display = visibleCount > 0 ? 'flex' : 'none';
    leftBox.style.display = leftOn ? 'block' : 'none';
    rightBox.style.display = rightOn ? 'block' : 'none';

    if (leftOn && !leftImg.getAttribute('src')) {
        leftImg.src = `/video_feed/left?${Date.now()}`;
    } else if (!leftOn) {
        leftImg.removeAttribute('src');
        document.getElementById('fps-left').innerText = "STANDBY";
    }

    if (rightOn && !rightImg.getAttribute('src')) {
        rightImg.src = `/video_feed/right?${Date.now()}`;
    } else if (!rightOn) {
        rightImg.removeAttribute('src');
        document.getElementById('fps-right').innerText = "STANDBY";
    }

    leftBox.classList.toggle('single-mode', visibleCount === 1 && leftOn);
    rightBox.classList.toggle('single-mode', visibleCount === 1 && rightOn);
}

function toggleCamera(camera) {
    const isLeft = camera === 'left';
    const checkbox = document.getElementById(isLeft ? 'cam-toggle-left' : 'cam-toggle-right');
    const isChecked = checkbox.checked;
    const img = document.getElementById(isLeft ? 'img-left' : 'img-right');
    const fps = document.getElementById(isLeft ? 'fps-left' : 'fps-right');
    const feedPath = isLeft ? '/video_feed/left' : '/video_feed/right';

    fetch(`/toggle_cam?camera=${camera}&state=${isChecked ? 'on' : 'off'}`);

    if (isChecked) {
        img.src = `${feedPath}?${Date.now()}`;
    } else {
        img.removeAttribute('src');
        fps.innerText = "STANDBY";
    }

    syncCameraLayout();
}

function toggleRightCam() {
    toggleCamera('right');
}

syncCameraLayout();

// --- 图表初始化逻辑 ---
const maxDataPoints = 50;
let timeTicks = 0;
const commonOptions = {
    responsive: true, maintainAspectRatio: false, animation: false,
    scales: { x: { display: false }, y: { grid: { color: '#333' }, ticks: { color: '#888' } } },
    plugins: { legend: { display: false } }
};

const posChart = new Chart(document.getElementById('posChart').getContext('2d'), {
    type: 'line',
    data: { labels: [], datasets: [{ label: 'Revolutions', data: [], borderColor: '#00aaff', borderWidth: 2, pointRadius: 0, tension: 0.1 }] },
    options: { ...commonOptions, plugins: { title: { display: true, text: 'Position (Revolutions)', color: '#00aaff' } } }
});

const speedChart = new Chart(document.getElementById('speedChart').getContext('2d'), {
    type: 'line',
    data: { labels: [], datasets: [{ label:'RPM', data: [], borderColor: '#ff8800', borderWidth: 2, pointRadius: 0, tension: 0.1 }] },
    options: { ...commonOptions, plugins: { title: { display: true, text: 'Speed (RPM)', color: '#ff8800' } } }
});

const kmhChart = new Chart(document.getElementById('kmhChart').getContext('2d'), {
    type: 'line',
    data: { labels: [], datasets: [{ label:'km/h', data: [], borderColor: '#00ff88', borderWidth: 2, pointRadius: 0, tension: 0.1 }] },
    options: { ...commonOptions, plugins: { title: { display: true, text: 'Vehicle Speed (km/h)', color: '#00ff88' } } }
});

const chartVisibilityStorageKey = 'whDashboardChartVisibility';
const chartByTarget = {
    pos: posChart,
    rpm: speedChart,
    kmh: kmhChart,
};

function loadChartVisibility() {
    try {
        const savedVisibility = JSON.parse(localStorage.getItem(chartVisibilityStorageKey) || '{}');
        document.querySelectorAll('.chart-toggle-input').forEach(input => {
            const target = input.dataset.chartTarget;
            if (typeof savedVisibility[target] === 'boolean') {
                input.checked = savedVisibility[target];
            }
        });
    } catch (error) {
        console.warn('图表显示设置读取失败:', error);
    }
}

function syncChartVisibility() {
    const chartContainer = document.getElementById('charts-container');
    let visibleCount = 0;
    const visibleTargets = [];
    const visibilityState = {};

    document.querySelectorAll('.chart-toggle-input').forEach(input => {
        const target = input.dataset.chartTarget;
        const chartBox = document.querySelector(`[data-chart-box="${target}"]`);
        if (!chartBox) return;

        visibilityState[target] = input.checked;
        chartBox.classList.toggle('chart-hidden', !input.checked);
        if (input.checked) {
            visibleCount += 1;
            visibleTargets.push(target);
        }
    });

    try {
        localStorage.setItem(chartVisibilityStorageKey, JSON.stringify(visibilityState));
    } catch (error) {
        console.warn('图表显示设置保存失败:', error);
    }
    chartContainer.classList.toggle('charts-collapsed', visibleCount === 0);
    requestAnimationFrame(() => {
        visibleTargets.forEach(target => {
            chartByTarget[target].resize();
            chartByTarget[target].update('none');
        });
    });
}

document.querySelectorAll('.chart-toggle-input').forEach(input => {
    input.addEventListener('change', syncChartVisibility);
});
loadChartVisibility();
syncChartVisibility();

// --- 后台数据轮询 ---
setInterval(() => {
    fetch('/fps_stats').then(r => r.json()).then(data => {
        if (document.getElementById('cam-toggle-left').checked) {
            document.getElementById('fps-left').innerText = "实时: " + data.left + " FPS";
        }
        if (document.getElementById('cam-toggle-right').checked) {
            document.getElementById('fps-right').innerText = "实时: " + data.right + " FPS";
        }
    });
}, 1000);

setInterval(() => {
    fetch('/sensor_stats').then(r => r.json()).then(data => {
        const revolutions = (parseFloat(data.position) / PPR).toFixed(2);
        const rpmValue = (parseFloat(data.speed) / PPR) * 60;
        const rpm = rpmValue.toFixed(1);
        const kmhValue = Math.abs(rpmValue) * KMH_PER_RPM;
        const kmh = kmhValue.toFixed(2);
        maxKmh = Math.max(maxKmh, kmhValue);

        document.getElementById('sensor-pos').innerText = revolutions;
        document.getElementById('sensor-speed').innerText = rpm;
        document.getElementById('sensor-kmh').innerText = kmh;
        document.getElementById('sensor-max-kmh').innerText = maxKmh.toFixed(2);

        posChart.data.labels.push(timeTicks);
        posChart.data.datasets[0].data.push(revolutions);
        speedChart.data.labels.push(timeTicks);
        speedChart.data.datasets[0].data.push(rpm);
        kmhChart.data.labels.push(timeTicks);
        kmhChart.data.datasets[0].data.push(kmh);

        if (posChart.data.labels.length > maxDataPoints) {
            posChart.data.labels.shift(); posChart.data.datasets[0].data.shift();
            speedChart.data.labels.shift(); speedChart.data.datasets[0].data.shift();
            kmhChart.data.labels.shift(); kmhChart.data.datasets[0].data.shift();
        }
        posChart.update(); speedChart.update(); kmhChart.update();
        timeTicks++;

        // 双向同步逻辑
        const serverMode = data.mode;
        const serverThrottle = parseInt(data.throttle);
        const rcWarning = document.getElementById('rc-warning');
        isRcMode = (serverMode === 'RC');

        if (isRcMode) {
            // 遥控器夺权了！显示红条警告
            rcWarning.style.display = 'block';
            
            // 只要你的手没有正在按着网页摇杆，UI就会像被鬼附身一样跟着遥控器动！
            if (!isDragging) {
                syncUIFromRemote(serverThrottle);
            }
        } else {
            // 网页控制中，隐藏红条
            rcWarning.style.display = 'none';
        }
    });
}, 100);

// --- 录制功能 ---
function startRecord() {
    const btn = document.getElementById('rec-btn');
    btn.disabled = true;
    btn.innerText = "⏳ 正在抓取 (剩余 3 秒)...";
    fetch('/start_record').then(r => r.json()).then(data => {
        if(data.status === 'started') {
            let timeLeft = 3;
            let timer = setInterval(() => {
                timeLeft -= 1;
                if(timeLeft <= 0) {
                    clearInterval(timer);
                    btn.innerText = "💾 等待停稳并复制SD数据...";
                    setTimeout(() => {
                        btn.disabled = false;
                        btn.innerText = "🔴 记录多源融合数据 (3秒)";
                    }, 2500); 
                } else {
                    btn.innerText = `⏳ 正在抓取 (剩余 ${timeLeft} 秒)...`;
                }
            }, 1000);
        } else {
            btn.disabled = false;
            btn.innerText = "🔴 记录多源融合数据 (3秒)";
        }
    }).catch(error => {
        console.error("记录启动失败:", error);
        btn.disabled = false;
        btn.innerText = "🔴 记录多源融合数据 (3秒)";
    });
}

let speedRecordActive = false;

function formatDuration(seconds) {
    const totalSeconds = Math.max(0, Math.floor(seconds || 0));
    const minutes = Math.floor(totalSeconds / 60).toString().padStart(2, '0');
    const secs = (totalSeconds % 60).toString().padStart(2, '0');
    return `${minutes}:${secs}`;
}

function updateSpeedRecordUI(data) {
    const btn = document.getElementById('speed-rec-btn');
    const status = document.getElementById('speed-record-status');
    if (!btn || !status) return;

    speedRecordActive = !!data.active;
    btn.classList.toggle('recording', speedRecordActive);
    btn.innerText = speedRecordActive ? "⏹ 停止速度记录" : "📈 开始速度记录";

    const elapsed = formatDuration(data.elapsed_sec || 0);
    const maxDuration = formatDuration(data.max_duration_sec || 600);
    const samples = data.sample_count || 0;

    if (speedRecordActive) {
        status.innerText = `速度记录: 记录中 ${elapsed} / ${maxDuration} | ${samples} samples`;
    } else if (data.file_path) {
        status.innerText = `速度记录: 已停止 ${elapsed} | ${samples} samples | ${data.stop_reason || 'stopped'}`;
    } else {
        status.innerText = `速度记录: 空闲 | 上限 ${maxDuration}`;
    }
}

function refreshSpeedRecordStatus() {
    fetch('/speed_record/status', { cache: 'no-store' })
        .then(r => r.json())
        .then(updateSpeedRecordUI)
        .catch(error => console.error("速度记录状态读取失败:", error));
}

function toggleSpeedRecord() {
    const btn = document.getElementById('speed-rec-btn');
    const endpoint = speedRecordActive ? '/speed_record/stop' : '/speed_record/start';
    btn.disabled = true;

    fetch(endpoint, { method: 'POST', cache: 'no-store' })
        .then(r => r.json())
        .then(updateSpeedRecordUI)
        .catch(error => {
            console.error("速度记录切换失败:", error);
            refreshSpeedRecordStatus();
        })
        .finally(() => {
            btn.disabled = false;
        });
}

setInterval(refreshSpeedRecordStatus, 1000);
refreshSpeedRecordStatus();

// ==========================================
// 线控摇杆与定速巡航双模逻辑
// ==========================================
const knob = document.getElementById('joy-knob');
const slider = document.getElementById('throttle-slider');
const throttleDisplay = document.getElementById('throttle-val');
const throttleInput = document.getElementById('throttle-input');
const numericOutputToggle = document.getElementById('numeric-output-toggle');

let isDragging = false;
let startY = 0;
let currentTop = 80; 
const maxTop = 160;  
const minTop = 0;    
let lastSendTime = 0;
const THROTTLE_MIN = 1000;
const THROTTLE_MAX = 2000;
const THROTTLE_NEUTRAL = 1500;

// ==========================================
// 发送引擎：防堵塞 + 丢帧保最新
// ==========================================
let isSending = false;      // 网络锁：当前是否正在发数据？
let pendingThrottle = null; // 暂存区：记录最新的油门值
let activeThrottleController = null;
let lastHeartbeatTime = 0;
let throttleRequestStartedAt = 0;
const THROTTLE_FETCH_TIMEOUT_MS = 900;
const THROTTLE_SEND_RECOVER_MS = 1200;
const THROTTLE_HEARTBEAT_MS = 250;

function fetchWithTimeout(url, options = {}, timeoutMs = THROTTLE_FETCH_TIMEOUT_MS) {
    const controller = options.controller || new AbortController();
    const { controller: _controller, ...fetchOptions } = options;
    const timeoutId = setTimeout(() => controller.abort(), timeoutMs);

    return fetch(url, {
        cache: 'no-store',
        ...fetchOptions,
        signal: controller.signal,
    }).finally(() => clearTimeout(timeoutId));
}

function recoverStaleThrottleRequest() {
    if (!isSending || throttleRequestStartedAt === 0) return;
    if (Date.now() - throttleRequestStartedAt <= THROTTLE_SEND_RECOVER_MS) return;

    console.warn("Throttle request watchdog recovered a stuck send.");
    if (activeThrottleController) {
        activeThrottleController.abort();
    }
    activeThrottleController = null;
    isSending = false;
    throttleRequestStartedAt = 0;
}

function sendThrottleCommand(val) {
    pendingThrottle = val; // 永远用最新值覆盖暂存区
    recoverStaleThrottleRequest();
    
    // 如果网络通道是空闲的，立刻开火！如果正在忙，就随它去，暂存区已经更新了
    if (!isSending) {
        flushThrottleQueue();
    }
}

function flushThrottleQueue() {
    recoverStaleThrottleRequest();
    if (isSending) return;
    if (pendingThrottle === null) return;
    
    // 把暂存区的值拿出来准备发送，并清空暂存区
    let valToSend = pendingThrottle;
    pendingThrottle = null; 
    
    isSending = true; // 上锁！
    activeThrottleController = new AbortController();
    throttleRequestStartedAt = Date.now();
    
    // 发起 HTTP 请求
    fetchWithTimeout(`/set_throttle?val=${valToSend}`, {
        method: 'POST',
        controller: activeThrottleController,
    }, 700)
        .then(response => {
            // 请求完成，解锁！
            isSending = false; 
            activeThrottleController = null;
            throttleRequestStartedAt = 0;
            
            // 🚀 核心：如果在我们发送的这零点几秒内，用户又拖了滑块（暂存区不为空）
            // 那就休息 100 毫秒后，再发一次最新的！
            if (pendingThrottle !== null) {
                setTimeout(flushThrottleQueue, 100);
            }
        })
        .catch(error => {
            console.error("指令下发失败:", error);
            isSending = false; // 报错也要解锁，防止死锁
            activeThrottleController = null;
            throttleRequestStartedAt = 0;
            if (pendingThrottle !== null) {
                setTimeout(flushThrottleQueue, 100);
            }
        });
}

// ==========================================
// 🚨 紧急刹车专属逻辑
// ==========================================
function triggerEStop() {
    // 1. 强行清空节流阀的暂存区，掐断还没发出的网络请求
    pendingThrottle = null; 
    if (activeThrottleController) {
        activeThrottleController.abort();
        activeThrottleController = null;
    }
    isSending = false;
    throttleRequestStartedAt = 0;
    
    // 2. 将网页 UI 的滑块、数字、摇杆瞬间打回 1500 中位
    updateDriveState(THROTTLE_NEUTRAL, 'system', false);
    
    // 3. 呼叫后端的专属急停接口
    fetchWithTimeout('/e_stop', { method: 'POST' }, THROTTLE_FETCH_TIMEOUT_MS).then(() => {
        console.log("🚨 急停指令已送达底层！");
    }).catch(error => {
        console.error("急停指令发送失败:", error);
    });
}

function clampThrottleValue(value, fallback = THROTTLE_NEUTRAL) {
    const parsed = parseInt(value, 10);
    if (Number.isNaN(parsed)) return fallback;
    return Math.max(THROTTLE_MIN, Math.min(THROTTLE_MAX, parsed));
}

function readNumericThrottleValue() {
    const pwm = clampThrottleValue(throttleInput.value, THROTTLE_NEUTRAL);
    throttleInput.value = pwm;
    return pwm;
}

function toggleNumericThrottleOutput() {
    if (numericOutputToggle.checked) {
        updateDriveState(readNumericThrottleValue(), 'numeric');
    } else {
        updateDriveState(THROTTLE_NEUTRAL, 'system');
    }
}

// 👑 核心状态机：同步滑块、摇杆和显示器
function updateDriveState(pwm, source, shouldSend = true) {
    pwm = clampThrottleValue(pwm, THROTTLE_NEUTRAL);

    // 1. 更新数字和颜色
    throttleDisplay.innerText = pwm;
    if(pwm > 1550) throttleDisplay.style.color = '#ff8800';
    else if(pwm < 1450) throttleDisplay.style.color = '#00aaff';
    else throttleDisplay.style.color = '#ffffff';

    throttleInput.value = pwm;

    // 2. 如果不是滑块自己在动，让滑块跟着动
    if (source !== 'slider') {
        slider.value = pwm;
    }

    // 3. 如果不是摇杆自己在动，让物理摇杆跟着动
    if (source !== 'joystick') {
        let y = Math.round((THROTTLE_MAX - pwm) / (THROTTLE_MAX - THROTTLE_MIN) * maxTop);
        knob.style.transition = 'top 0.3s cubic-bezier(0.175, 0.885, 0.32, 1.275)';
        knob.style.top = y + 'px';
        currentTop = y;
    }

    // 4. 下发底层指令
    if (shouldSend) {
        sendThrottleCommand(pwm);
    }
}

// === 监听：定速巡航滑块 ===
slider.addEventListener('input', function() {
    updateDriveState(this.value, 'slider');
});

throttleInput.addEventListener('input', function() {
    const rawValue = this.value.trim();
    if (rawValue === '') {
        if (numericOutputToggle.checked) {
            updateDriveState(THROTTLE_NEUTRAL, 'numeric');
        }
        return;
    }

    const parsed = parseInt(rawValue, 10);
    if (Number.isNaN(parsed)) return;

    if (parsed > THROTTLE_MAX) {
        this.value = THROTTLE_MAX;
    } else if (rawValue.length >= 4 && parsed < THROTTLE_MIN) {
        this.value = THROTTLE_MIN;
    }

    if (numericOutputToggle.checked && this.value.length >= 4) {
        updateDriveState(clampThrottleValue(this.value), 'numeric');
    }
});

throttleInput.addEventListener('change', function() {
    const pwm = readNumericThrottleValue();
    if (numericOutputToggle.checked) {
        updateDriveState(pwm, 'numeric');
    }
});

// === 监听：物理摇杆 ===
function startDrag(clientY) {
    isDragging = true;
    startY = clientY - currentTop;
    knob.style.transition = 'none'; // 关闭弹簧动画，实现零延迟跟手
}

function onDrag(clientY) {
    if (!isDragging) return;
    let y = clientY - startY;
    y = Math.max(minTop, Math.min(y, maxTop));
    currentTop = y;
    knob.style.top = y + 'px'; // 实时改变物理位置
    
    let pwm = Math.round(THROTTLE_MAX - (y / maxTop) * (THROTTLE_MAX - THROTTLE_MIN));
    updateDriveState(pwm, 'joystick');
}

function stopDrag() {
    if (isDragging) {
        isDragging = false;
        // 松开摇杆：解除一切巡航，瞬间归中！
        updateDriveState(THROTTLE_NEUTRAL, 'system'); 
    }
}

// 绑定电脑鼠标
knob.addEventListener('mousedown', (e) => startDrag(e.clientY));
document.addEventListener('mousemove', (e) => onDrag(e.clientY));
document.addEventListener('mouseup', stopDrag);

// 绑定手机触摸屏
knob.addEventListener('touchstart', (e) => startDrag(e.touches[0].clientY), {passive: false});
document.addEventListener('touchmove', (e) => { e.preventDefault(); onDrag(e.touches[0].clientY); }, {passive: false});
document.addEventListener('touchend', stopDrag);

// === 监听：面板按钮 ===
function setThrottle(val) {
    updateDriveState(val, 'system');
}

setInterval(() => {
    const pwm = parseInt(slider.value, 10);
    recoverStaleThrottleRequest();
    if (!document.hidden && !isRcMode && pwm !== THROTTLE_NEUTRAL && Date.now() - lastHeartbeatTime > THROTTLE_HEARTBEAT_MS) {
        lastHeartbeatTime = Date.now();
        sendThrottleCommand(pwm);
    }
}, 300);

// 👻 幽灵同步函数：专门负责让UI跟着底层跑，但不向Python发指令
function syncUIFromRemote(pwm) {
    pwm = clampThrottleValue(pwm, THROTTLE_NEUTRAL);

    throttleDisplay.innerText = pwm;
    if(pwm > 1550) throttleDisplay.style.color = '#ff8800';
    else if(pwm < 1450) throttleDisplay.style.color = '#00aaff';
    else throttleDisplay.style.color = '#ffffff';

    slider.value = pwm;
    throttleInput.value = pwm;

    let y = Math.round((THROTTLE_MAX - pwm) / (THROTTLE_MAX - THROTTLE_MIN) * maxTop);
    knob.style.transition = 'top 0.1s linear'; // 让同步看起来极其丝滑跟手
    knob.style.top = y + 'px';
    currentTop = y;
}
