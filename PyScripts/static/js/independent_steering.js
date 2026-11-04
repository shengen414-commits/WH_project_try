(function(global){
    const el=id=>document.getElementById(id);
    let state={},busy=false,stopQueued=false,generation=0,initialized=false,waitingExit=false,cameraBusy=false,cameraWanted=false;
    const labels={off:'已关闭',arming:'等待固件确认',calibrating:'IMU静止标定中',running:'运行中',stopping:'等待归中退出确认',fault:'已停止，请关闭后重试'};
    const reasons={imu_init_failed:'IMU初始化失败，请检查接线和I²C读取',imu_zero_timeout:'IMU标定超时，查看下方采样诊断',zero_timeout:'未在4秒内收集到连续稳定窗口',bias_out_of_range:'静止零偏超出允许范围',imu_zero_failed:'IMU标定失败，请保持静止后重试',imu_stale:'IMU读取超时',imu_gap:'IMU采样中断',
        imu_mount_or_tilt:'IMU方向或倾斜不符合X前Y右Z下的平面条件',host_target_stale:'后台目标超过250ms未更新',
        host_sensor_invalid:'后台检测失效，固件已归中停动力',reverse_not_supported:'独立转向只支持前进',
        control_gap:'固件控制循环超时',rc_takeover:'遥控器已接管',tracking:'正在响应偏差',waiting_target:'等待有效观测',imu_calibrating:'静止标定中'};
    const formats=(value,digits=2)=>typeof value==='number'?value.toFixed(digits):'—';
    const detectionReasons={no_line:'未找到黑胶带',low_confidence:'中线置信度不足',ambiguous_lines:'存在多条候选线',
        observation_stale:'观测超过150ms',roi_too_short:'当前图像的ROI高度不足以采样',no_consistent_path:'近端或前视区域缺少连续可靠的胶带路径',insufficient_straight_support:'有效直线点不足',poor_fit:'中线拟合不可靠',no_far_support:'前视区域没有有效中线'};
    const zeroReasons={idle:'等待IMU初始化',collecting:'收集稳定样本',complete:'标定完成',
        sample_gap:'采样间断，重新采集',gyro_out_of_range:'原始角速度过大，重新采集',
        accel_out_of_range:'加速度不接近1g，重新采集',gyro_unstable:'角速度波动过大，重新采集',
        accel_unstable:'加速度波动过大，重新采集',zero_timeout:'稳定样本不足，标定超时',
        bias_out_of_range:'零偏超出允许范围',canceled:'已取消'};
    const image=el('independent-camera-preview');
    const preview=global.createCameraPreview({url:'/steering/independent/image',intervalMs:150,clearOnError:true,
        fetch:global.fetch.bind(global),setTimeout:global.setTimeout.bind(global),clearTimeout:global.clearTimeout.bind(global),
        createObjectURL:global.URL.createObjectURL.bind(global.URL),revokeObjectURL:global.URL.revokeObjectURL.bind(global.URL),
        showImage:url=>{image.src=url;image.hidden=false;},clearImage:()=>{image.removeAttribute('src');image.hidden=true;}});
    const updatePreview=()=>preview.setEnabled(!!state.camera?.enabled&&!document.hidden);
    document.addEventListener('visibilitychange',updatePreview);
    global.addEventListener('pagehide',()=>preview.setEnabled(false));
    global.addEventListener('pageshow',updatePreview);
    function render(data){
        if(!data)return;state=data;
        if(!initialized && data.settings){
            for(const source of ['imu','camera'])for(const [key,value] of Object.entries(data.settings[source])){
                const field=el(`independent-${source}-form`).elements.namedItem(key);if(field)field.value=value;
            }
            initialized=true;
        }
        const blocked=!!data.blocking;
        for(const source of ['imu','camera']){
            el(`independent-${source}-on`).disabled=busy||blocked||cameraBusy;
            el(`independent-${source}-form`).querySelectorAll('input,select,button').forEach(c=>c.disabled=busy||blocked);
        }
        el('independent-off').disabled=false; // Stop remains available after an uncertain HTTP result.
        const camera=data.camera||{},firmware=data.firmware||{},observation=data.source==='camera'&&camera.enabled?camera.observation:data.observation,proposal=data.proposal;
        const age=data.source==='camera'&&camera.enabled?camera.observation_age_ms:data.observation_age_ms;
        let detail=data.phase==='fault'?(reasons[data.message]||data.message||reasons[firmware.reason]):(reasons[firmware.reason]||data.message);
        if(detail?.startsWith('观测无效：')){const code=detail.slice('观测无效：'.length);detail=`观测无效：${detectionReasons[code]||code}`;}
        el('independent-status').textContent=`独立转向：${data.source==='imu'?'IMU航向保持':data.source==='camera'?'相机中线':'未选择'} · ${labels[data.phase]||data.phase||'等待'} | 固件PWM ${firmware.pwm||'—'} μs | ${detail||''}`;
        el('independent-observation').textContent=observation?(data.phase==='running'?'最新观测：':data.source==='camera'&&camera.enabled?'检测预览（未控制）：':'历史观测（未控制）：')+(observation.source==='imu'?
            `相对航向 ${formats(observation.relative_heading_deg)}° | 左转正角速度 ${formats(observation.yaw_rate_deg_s)}°/s | 横向位置未知`:
            `画面偏差 ${formats(observation.image_offset_norm,3)} | 图像方向 ${formats(observation.image_heading_deg)}° | 置信度 ${formats(observation.confidence)}`)+` | 数据年龄 ${formats(age,0)} ms`:'观测：等待新鲜数据';
        el('independent-command').textContent=proposal?`独立候选修正 ${formats(proposal.correction,3)}（左正右负${data.phase==='running'?'':'；已暂停，不发送'}） | 单一输出通道 | ${data.fresh?'固件状态新鲜':'固件状态过期'}`:'候选输出：等待观测与固件确认';
        if(waitingExit && data.phase==='off'){waitingExit=false;el('independent-message').textContent=data.message||'已确认独立转向关闭';}
        else if(waitingExit && data.request?.status==='timeout')el('independent-message').textContent=data.message;
        if(blocked){
            el('line-steering-on').disabled=true;el('manual-steering-start').disabled=true;
        }
        const zero=data.imu_calibration||{};
        el('independent-imu-calibration').textContent=data.source==='imu'&&zero.reason?
            `标定诊断：${zeroReasons[zero.reason]||zero.reason} | ${zero.samples}/200样本 | 重采 ${zero.restarts} 次 | Z零偏 ${formats(zero.bias)}°/s | 三轴最大波动 ${formats(zero.sd,3)}°/s | 加速度 ${formats(zero.a,3)}g`:'';
        el('independent-camera-toggle').checked=cameraBusy?cameraWanted:!!camera.enabled;
        el('independent-camera-toggle').disabled=cameraBusy||busy;
        const detected=camera.observation;
        el('independent-camera-status').textContent=camera.enabled?
            `检测预览已开启（独立于顶部LeftCam） | ${camera.error|| (detected?detectionReasons[detected.reason]||detected.reason:'等待相机出图')} | 数据年龄 ${formats(camera.observation_age_ms,0)} ms | 检测耗时 ${formats(camera.processing_ms,0)} ms`:'检测预览已关闭；请先开启此开关，等待出图后再开启相机转向。';
        updatePreview();

    }
    async function post(body){
        const controller=new AbortController(),timeout=setTimeout(()=>controller.abort(),2000);
        try{
            const response=await fetch('/steering/independent',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body),signal:controller.signal});
            const data=await response.json();if(!response.ok)throw new Error(data.error||`HTTP ${response.status}`);
            render(data);return data;
        }finally{clearTimeout(timeout);}
    }
    function settings(source){
        const form=el(`independent-${source}-form`);if(!form.reportValidity())throw new Error('请检查参数范围');
        return Object.fromEntries([...new FormData(form)].map(([key,value])=>[key,key==='threshold_mode'?value:Number(value)]));
    }
    async function execute(source,start){
        if(busy)return;
        const version=++generation;
        try{
            const values=settings(source);
            busy=true;render(state);
            if(global.dashboardPolling)global.dashboardPolling.invalidate();
            await post({action:'save',source,settings:values});
            if(start&&version===generation){
                if(typeof global.parkThrottleForManualSteering==='function')global.parkThrottleForManualSteering();
                await post({action:'start',source});
            }
            el('independent-message').textContent=start?'已请求开启，实际状态以固件确认为准':'独立参数已保存；未开启转向';
        }catch(error){el('independent-message').textContent=error.name==='AbortError'?'请求超时，状态不确定，请点击关闭独立转向确认退出':error.message;}
        finally{
            busy=false;render(state);if(global.dashboardPolling)global.dashboardPolling.refresh();
            if(stopQueued){stopQueued=false;await stop();}
        }
    }
    async function stop(){
        generation++;
        if(busy){stopQueued=true;el('independent-message').textContent='已请求取消，当前请求结束后归中退出';return;}
        busy=true;waitingExit=true;render(state);
        try{const result=await post({action:'stop'});el('independent-message').textContent=result.phase==='off'?(result.message||'独立转向已关闭'):'等待固件归中退出确认';}
        catch(error){el('independent-message').textContent=error.message;}
        finally{busy=false;render(state);if(global.dashboardPolling)global.dashboardPolling.refresh();}
    }
    for(const source of ['imu','camera'])el(`independent-${source}-form`).addEventListener('submit',event=>{event.preventDefault();execute(source,false);});
    el('independent-camera-toggle').addEventListener('change',async event=>{
        const enabled=event.target.checked;cameraWanted=enabled;cameraBusy=true;render(state);
        const controller=new AbortController(),timeout=setTimeout(()=>controller.abort(),2000);
        try{
            const response=await fetch('/steering/independent/camera',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({enabled}),signal:controller.signal});
            const data=await response.json();if(!response.ok)throw new Error(data.error||`HTTP ${response.status}`);
            render(data);
        }catch(error){el('independent-message').textContent=error.name==='AbortError'?'检测开关请求超时，请查看实际状态':error.message;}
        finally{clearTimeout(timeout);cameraBusy=false;render(state);if(global.dashboardPolling)global.dashboardPolling.refresh();}
    });
    global.startIndependentSteering=source=>execute(source,true);
    global.stopIndependentSteering=stop;
    global.independentSteeringBlocking=()=>!!state.blocking;
    global.renderIndependentSteering=render;
})(typeof window!=='undefined'?window:globalThis);
