const PPR = 12.0; // 编码器分辨率

// --- 摄像头切换逻辑 ---
function toggleRightCam() {
    const isChecked = document.getElementById('cam-toggle').checked;
    const rightBox = document.getElementById('box-right');
    const leftBox = document.getElementById('box-left');
    fetch(`/toggle_cam?state=${isChecked ? 'on' : 'off'}`);
    if (isChecked) {
        rightBox.style.display = 'block';
        leftBox.classList.remove('single-mode');
        document.getElementById('img-right').src = "/video_feed/right?" + new Date().getTime();
    } else {
        rightBox.style.display = 'none';
        leftBox.classList.add('single-mode');
        document.getElementById('fps-right').innerText = "STANDBY";
    }
}

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

// --- 后台数据轮询 ---
setInterval(() => {
    fetch('/fps_stats').then(r => r.json()).then(data => {
        document.getElementById('fps-left').innerText = "实时: " + data.left + " FPS";
        if (document.getElementById('cam-toggle').checked) {
            document.getElementById('fps-right').innerText = "实时: " + data.right + " FPS";
        }
    });
}, 1000);

setInterval(() => {
    fetch('/sensor_stats').then(r => r.json()).then(data => {
        const revolutions = (parseFloat(data.position) / PPR).toFixed(2);
        const rpm = ((parseFloat(data.speed) / PPR) * 60).toFixed(1);

        document.getElementById('sensor-pos').innerText = revolutions;
        document.getElementById('sensor-speed').innerText = rpm;

        posChart.data.labels.push(timeTicks);
        posChart.data.datasets[0].data.push(revolutions);
        speedChart.data.labels.push(timeTicks);
        speedChart.data.datasets[0].data.push(rpm);

        if (posChart.data.labels.length > maxDataPoints) {
            posChart.data.labels.shift(); posChart.data.datasets[0].data.shift();
            speedChart.data.labels.shift(); speedChart.data.datasets[0].data.shift();
        }
        posChart.update(); speedChart.update();
        timeTicks++;

        // 双向同步逻辑
        const serverMode = data.mode;
        const serverThrottle = parseInt(data.throttle);
        const rcWarning = document.getElementById('rc-warning');

        if (serverMode === 'RC') {
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
                    btn.innerText = "💾 正在写入硬盘和SD卡...";
                    setTimeout(() => {
                        btn.disabled = false;
                        btn.innerText = "🔴 记录多源融合数据 (3秒)";
                    }, 2500); 
                } else {
                    btn.innerText = `⏳ 正在抓取 (剩余 ${timeLeft} 秒)...`;
                }
            }, 1000);
        }
    });
}

// ==========================================
// 线控摇杆与定速巡航双模逻辑
// ==========================================
const knob = document.getElementById('joy-knob');
const slider = document.getElementById('throttle-slider');
const throttleDisplay = document.getElementById('throttle-val');

let isDragging = false;
let startY = 0;
let currentTop = 80; 
const maxTop = 160;  
const minTop = 0;    
let lastSendTime = 0;

// ==========================================
// 发送引擎：防堵塞 + 丢帧保最新
// ==========================================
let isSending = false;      // 网络锁：当前是否正在发数据？
let pendingThrottle = null; // 暂存区：记录最新的油门值

function sendThrottleCommand(val) {
    pendingThrottle = val; // 永远用最新值覆盖暂存区
    
    // 如果网络通道是空闲的，立刻开火！如果正在忙，就随它去，暂存区已经更新了
    if (!isSending) {
        flushThrottleQueue();
    }
}

function flushThrottleQueue() {
    if (pendingThrottle === null) return;
    
    // 把暂存区的值拿出来准备发送，并清空暂存区
    let valToSend = pendingThrottle;
    pendingThrottle = null; 
    
    isSending = true; // 上锁！
    
    // 发起 HTTP 请求
    fetch(`/set_throttle?val=${valToSend}`)
        .then(response => {
            // 请求完成，解锁！
            isSending = false; 
            
            // 🚀 核心：如果在我们发送的这零点几秒内，用户又拖了滑块（暂存区不为空）
            // 那就休息 100 毫秒后，再发一次最新的！
            if (pendingThrottle !== null) {
                setTimeout(flushThrottleQueue, 100);
            }
        })
        .catch(error => {
            console.error("指令下发失败:", error);
            isSending = false; // 报错也要解锁，防止死锁
        });
}

// ==========================================
// 🚨 紧急刹车专属逻辑
// ==========================================
function triggerEStop() {
    // 1. 强行清空节流阀的暂存区，掐断还没发出的网络请求
    pendingThrottle = null; 
    
    // 2. 将网页 UI 的滑块、数字、摇杆瞬间打回 1500 中位
    updateDriveState(1500, 'system');
    
    // 3. 呼叫后端的专属急停接口
    fetch('/e_stop').then(() => {
        console.log("🚨 急停指令已送达底层！");
    });
}

// 👑 核心状态机：同步滑块、摇杆和显示器
function updateDriveState(pwm, source) {
    // 1. 更新数字和颜色
    throttleDisplay.innerText = pwm;
    if(pwm > 1550) throttleDisplay.style.color = '#ff8800';
    else if(pwm < 1450) throttleDisplay.style.color = '#00aaff';
    else throttleDisplay.style.color = '#ffffff';

    // 2. 如果是摇杆在动，让滑块跟着动
    if (source === 'joystick' || source === 'system') {
        slider.value = pwm;
    }

    // 3. 如果是滑块在动，让物理摇杆跟着动
    if (source === 'slider' || source === 'system') {
        let y = Math.round((2000 - pwm) / 1000 * maxTop);
        knob.style.transition = 'top 0.3s cubic-bezier(0.175, 0.885, 0.32, 1.275)';
        knob.style.top = y + 'px';
        currentTop = y;
    }

    // 4. 下发底层指令
    sendThrottleCommand(pwm);
}

// === 监听：定速巡航滑块 ===
slider.addEventListener('input', function() {
    updateDriveState(this.value, 'slider');
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
    
    let pwm = Math.round(2000 - (y / maxTop) * 1000);
    updateDriveState(pwm, 'joystick');
}

function stopDrag() {
    if (isDragging) {
        isDragging = false;
        // 松开摇杆：解除一切巡航，瞬间归中！
        updateDriveState(1500, 'system'); 
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

// 👻 幽灵同步函数：专门负责让UI跟着底层跑，但不向Python发指令
function syncUIFromRemote(pwm) {
    throttleDisplay.innerText = pwm;
    if(pwm > 1550) throttleDisplay.style.color = '#ff8800';
    else if(pwm < 1450) throttleDisplay.style.color = '#00aaff';
    else throttleDisplay.style.color = '#ffffff';

    slider.value = pwm;

    let y = Math.round((2000 - pwm) / 1000 * maxTop);
    knob.style.transition = 'top 0.1s linear'; // 让同步看起来极其丝滑跟手
    knob.style.top = y + 'px';
    currentTop = y;
}