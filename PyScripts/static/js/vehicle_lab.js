/* Single-flight observation polling; commands never imply hardware confirmation. */
(() => {
    const $ = id => document.getElementById(id);
    let state, initialized = false, busy = false, history = [], errorUntil = 0;
    function message(text) { $('lab-message').textContent = text; }
    async function api(path, body) {
        const controller = new AbortController();
        const timer = setTimeout(() => controller.abort(), 4000);
        try {
            const response = await fetch(path, {method: body ? 'POST' : 'GET', cache:'no-store', signal:controller.signal,
                headers: body ? {'Content-Type':'application/json'} : {}, body:body ? JSON.stringify(body) : undefined});
            const data = await response.json();
            if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
            return data;
        } finally { clearTimeout(timer); }
    }
    function number(value, scale=1, digits=1) { return Number.isFinite(value) ? (value*scale).toFixed(digits) : '—'; }
    function draw(s) {
        const now = s.time_s;
        history.push([now, s.valid ? s.lateral_error_m : null]);
        history = history.filter(p => p[0] > now-20).slice(-100);
        const canvas = $('state-chart'), c = canvas.getContext('2d'), w = canvas.width, h = canvas.height;
        c.clearRect(0,0,w,h); c.font='16px sans-serif'; c.fillStyle='#9badba';
        c.fillText('横向偏差 mm · 最近20秒 · 无效状态留空',20,25);
        const extent = Math.max(50,...history.map(p => p[1] === null ? 0 : Math.abs(p[1]*1000)*1.2));
        for (const value of [-extent,0,extent]) {
            const y=125-value/extent*75; c.strokeStyle='#344653';c.beginPath();c.moveTo(60,y);c.lineTo(w-10,y);c.stroke();c.fillText(value.toFixed(0),5,y+5);
        }
        c.strokeStyle='#75dbcf';c.lineWidth=2;c.beginPath();let open=false;
        for(const [t,y] of history){if(y===null){open=false;continue;}const x=60+(t-now+20)/20*(w-70),cy=125-y*1000/extent*75;if(open)c.lineTo(x,cy);else c.moveTo(x,cy);open=true;}c.stroke();
    }
    function render(data) {
        state=data;const s=data.state;
        if(!initialized&&data.channels)for(const [key,value]of Object.entries(data.channels))$('sensor-channels').elements[key].checked=value;
        $('lab-start').disabled=busy||data.blocking;
        $('lab-zero').disabled=busy||!data.fresh;
        $('lab-stop').disabled=busy||!data.blocking;
        $('geometry-form').querySelector('button').disabled=busy||data.blocking;
        if (!busy && Date.now() > errorUntil) message(data.request.status==='pending'?'命令已发送，等待固件确认':data.request.status==='timeout'?'未收到固件确认，请检查版本后退出重试':data.request.reason|| (data.fresh?'固件已确认联合观察，动力中位':'尚未启动联合观察'));
        $('lab-health').textContent=`观察：${data.fresh?'在线':'未就绪'} ｜ 时钟误差界限：${number(data.clock.uncertainty_ms)}ms ｜ 丢包 ${data.sequence_gaps} ｜ 无效帧 ${data.invalid_frames}`;
        const line=data.raw.line||{};
        $('line-probes').replaceChildren(...Array.from({length:8},(_,i)=>{const node=document.createElement('span');node.textContent=`CH${i+1}`;node.className=line.mask>=0&&(line.mask&(1<<i))?'hit':'';return node;}));
        const lineReasons={valid:'1～3个连续探头命中',waiting:'0个命中，等待胶带，暂无位置',lost:'未检测到胶带',all_active:'全部命中',multiple_segments:'多个分离线段',too_wide:'命中超过三个探头',disabled:'巡线调试已关闭',stale:'数据过期'};
        $('line-position').textContent=`探头命中：${lineReasons[line.reason]||'等待数据'} ｜ 中心通道 ${Number.isFinite(line.center_channel)?(line.center_channel+1).toFixed(1):'—'}，目标4.5。探头间距未标定也可以先核对通道顺序。`;
        $('value-y').textContent=number(s.lateral_error_m,1000);
        $('value-heading').textContent=number(s.heading_error_rad,180/Math.PI);
        $('value-yaw').textContent=number(s.yaw_rate_rad_s,180/Math.PI);
        $('value-speed').textContent=number(s.speed_m_s,1,3);
        $('state-quality').textContent=Number.isFinite(s.lateral_error_m)?`${s.valid?'状态有效（仍仅用于观察）':'状态未就绪/不确定度过大'} ｜ 横向σ ${number(s.sigma_y_m,1000)}mm，方向σ ${number(s.sigma_heading_rad,180/Math.PI)}° ｜ 延迟回放 ${s.delayed_replays} ｜ 异常观测拒绝 ${s.rejected_updates}`:'等待已标定的有效观测；当前不输出毫米位置或方向估计。';
        const labels={line_pitch_m:'探头间距',line_x_m:'探头前后位置',line_y_m:'探头左右位置',meters_per_count:'编码器距离比例',encoder_sign:'编码器前进方向',wheelbase_m:'轴距'};
        $('missing-calibration').textContent=data.profile_error||`未标定：${data.missing_geometry.map(key=>labels[key]||key).join('、')||'安装尺寸已填写'}。横向速度未估计；侧滑明显时需要升级模型。`;
        $('raw-state').textContent=JSON.stringify({raw:data.raw,clock:data.clock,state:s,latency:data.latency,record:data.record},null,2);
        if(data.latency)$('lab-health').textContent+=` ｜ 输入排队 ${number(data.latency.queue_delay_ms)}ms ｜ 处理 ${number(data.latency.processing_ms)}ms ｜ 积压丢弃 ${data.latency.input_drops+data.latency.stale_inputs} ｜ 写盘排队 ${data.record.queue_depth||0} ｜ 存储丢弃 ${data.record.drops}`;
        $('camera-status').textContent=`${data.vision.reason} ｜ 图像偏差 ${number(data.vision.pixel_error)}px ｜ 置信度 ${number(data.vision.confidence)} ｜ 画面年龄 ${number(data.vision.frame_age_ms)}ms`;
        const preview=data.preview_available&&data.active&&data.vision.frame_age_ms<250;
        $('camera-preview').hidden=!preview;
        if(preview)$('camera-preview').src=`/vehicle-lab/image?t=${Date.now()}`;
        $('actuator-ready').textContent=data.actuator_reason;
        $('actuator-points').replaceChildren(...data.profile.actuator.points.slice().sort((a,b)=>a.pulse_us-b.pulse_us).map(p=>{const tr=document.createElement('tr');for(const v of [p.pulse_us,p.angle_deg]){const td=document.createElement('td');td.textContent=v;tr.appendChild(td);}return tr;}));
        $('trace-download').hidden=!data.record.done||!data.record.path;
        if(!initialized){const f=$('geometry-form'),g=data.profile.geometry;for(const [key,value]of Object.entries(g)){if(!f.elements[key])continue;f.elements[key].value=value===null?'':Array.isArray(value)?value.join(','):key.endsWith('_m')||key==='meters_per_count'?value*1000:value;}initialized=true;}
        draw(s);
    }
    async function perform(path,body){if(busy)return null;busy=true;errorUntil=0;for(const button of document.querySelectorAll('button'))button.disabled=true;message('正在发送…');try{const data=await api(path,body);if(data.state)render(data);message('已提交，等待状态确认');return data;}catch(e){errorUntil=Date.now()+5000;message(e.message);return null;}finally{busy=false;for(const button of document.querySelectorAll('button'))button.disabled=false;}}
    for(const button of document.querySelectorAll('[data-action]'))button.addEventListener('click',()=>perform('/vehicle-lab/command',{action:button.dataset.action}));
    $('sensor-channels').addEventListener('submit',e=>{e.preventDefault();const form=e.target;perform('/vehicle-lab/channels',Object.fromEntries(['line','imu','camera'].map(key=>[key,form.elements[key].checked])));});
    $('geometry-form').addEventListener('submit',async e=>{e.preventDefault();const f=e.target,g={};for(const key of Object.keys(state.profile.geometry)){const text=f.elements[key].value.trim();g[key]=text===''?null:key==='imu_axes'?text.split(',').map(Number):key.endsWith('_m')||key==='meters_per_count'?Number(text)/1000:Number(text);}await perform('/vehicle-lab/profile',{geometry:g});});
    $('actuator-form').addEventListener('submit',e=>{e.preventDefault();perform('/vehicle-lab/actuator/point',{angle_deg:Number($('wheel-angle').value)});});
    $('clear-points').addEventListener('click',()=>perform('/vehicle-lab/actuator/clear',{}));
    $('preview-form').addEventListener('submit',async e=>{e.preventDefault();const data=await perform('/vehicle-lab/actuator/preview',{angle_deg:Number($('preview-angle').value)});if(data)$('preview-result').textContent=`预计脉宽 ${data.pulse_us}μs，仅计算，未发送到舵机。`;});
    $('camera-file').addEventListener('change',async e=>{const file=e.target.files[0];if(!file)return;try{if(file.size>32768)throw new Error('文件超过32KiB');await perform('/vehicle-lab/profile',{camera:JSON.parse(await file.text())});}catch(error){message(error.message);}});
    async function poll(){try{render(await api('/vehicle-lab/status'));}catch(e){message(`状态读取失败：${e.message}`);}finally{setTimeout(poll,500);}}
    poll();
})();
