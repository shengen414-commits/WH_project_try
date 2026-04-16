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

// --- 线控摇杆逻辑 ---
const knob = document.getElementById('joy-knob');
const throttleDisplay = document.getElementById('throttle-val');
let isDragging = false;
let startY = 0;
let currentTop = 80; 
const maxTop = 160;  
const minTop = 0;    
let lastSendTime = 0;

function sendThrottleCommand(val) {
    const now = Date.now();
    if (now - lastSendTime > 50) { 
        fetch(`/set_throttle?val=${val}`);
        lastSendTime = now;
    }
}

function updateThrottleFromY(yPosition) {
    let pwm = Math.round(2000 - (yPosition / maxTop) * 1000);
    throttleDisplay.innerText = pwm;
    if(pwm > 1550) throttleDisplay.style.color = '#ff8800';
    else if(pwm < 1450) throttleDisplay.style.color = '#00aaff';
    else throttleDisplay.style.color = '#ffffff';
    sendThrottleCommand(pwm);
}

function resetJoystick() {
    currentTop = 80;
    knob.style.transition = 'top 0.3s cubic-bezier(0.175, 0.885, 0.32, 1.275)';
    knob.style.top = currentTop + 'px';
    throttleDisplay.innerText = 1500;
    throttleDisplay.style.color = '#ffffff';
    fetch(`/set_throttle?val=1500`); 
}

function startDrag(clientY) {
    isDragging = true;
    startY = clientY - currentTop;
    knob.style.transition = 'none'; 
}

function onDrag(clientY) {
    if (!isDragging) return;
    let y = clientY - startY;
    y = Math.max(minTop, Math.min(y, maxTop));
    currentTop = y;
    knob.style.top = y + 'px';
    updateThrottleFromY(y);
}

function stopDrag() {
    if (isDragging) {
        isDragging = false;
        resetJoystick();
    }
}

knob.addEventListener('mousedown', (e) => startDrag(e.clientY));
document.addEventListener('mousemove', (e) => onDrag(e.clientY));
document.addEventListener('mouseup', stopDrag);

knob.addEventListener('touchstart', (e) => startDrag(e.touches[0].clientY), {passive: false});
document.addEventListener('touchmove', (e) => { e.preventDefault(); onDrag(e.touches[0].clientY); }, {passive: false});
document.addEventListener('touchend', stopDrag);