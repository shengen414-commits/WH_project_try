(function(global){
    const form=document.getElementById('line-response-form');if(!form)return;
    let sentAt=-1, busy=false, pending=false, epoch=null, errorUntil=0, initialized=false, edited=false;
    const keys=['left_us','right_us','deadband','slew_us_s','direction'];
    form.addEventListener('input',()=>{edited=true;});
    form.addEventListener('change',()=>{edited=true;});
    const status=document.getElementById('line-response-status');
    global.renderLineResponse=function(data){
        if(!data)return;
        if(!initialized){
            if(data.applied&&!edited)for(const key of keys)form.elements[key].value=data.applied[key];
            initialized=true;
        }
        if(data.epoch&&data.epoch!==epoch){epoch=data.epoch;sentAt=-1;}
        const p=data.probes||{}, box=document.getElementById('line-live-probes');
        box.replaceChildren(...Array.from({length:8},(_,i)=>{
            const node=document.createElement('span');node.textContent=`CH${i+1}`;
            node.style.cssText=`padding:6px;border-radius:4px;background:${p.fresh&&p.mask!==null&&(p.mask&(1<<i))?'#176b56':'#263541'};color:white`;
            return node;
        }));
        const reasons={valid:'有效：单个或相邻两个探头命中',lost:'丢线',all_active:'八路全命中，无效',too_wide:'超过两个探头命中，无效',multiple_segments:'分离命中，无效'};
        document.getElementById('line-live-quality').textContent=p.fresh?
            `${reasons[p.reason]||p.reason}｜中心 ${Number.isFinite(p.center_channel)?(p.center_channel+1).toFixed(1):'—'}｜${{left:'胶带偏左',right:'胶带偏右',center:'居中'}[p.side]||'不输出有效位置'}`:'等待新鲜巡线数据；退出联合观察和手动测试后查看';
        const request=data.request||{};
        if((request.sent_at??-1)<sentAt)return;
        sentAt=request.sent_at??-1;
        pending=request.status==='pending';
        const applied=data.applied;
        if(Date.now()>errorUntil){
            status.textContent=request.message||'默认左右各400μs，可设0～500μs；设置尚未确认。';
            if(applied)status.textContent+=`｜已确认：左${applied.left_us} / 右${applied.right_us}μs，方向${applied.direction}，变化速度${applied.slew_us_s}μs/s`;
        }
        form.querySelector('button').disabled=busy||pending;
    };
    async function requestJson(method,body){
        const controller=new AbortController(), timer=setTimeout(()=>controller.abort(),1800);
        try{
            const response=await fetch('/steering/line/config',{method,cache:'no-store',headers:{'Content-Type':'application/json'},signal:controller.signal,...(body?{body:JSON.stringify(body)}:{})});
            const data=await response.json();if(!response.ok)throw new Error(data.error||`HTTP ${response.status}`);
            return data;
        }finally{clearTimeout(timer);}
    }
    global.ensureLineResponseApplied=async function(){
        if(busy)throw new Error('参数请求正在执行，请稍后再开启');
        if(!form.checkValidity()){form.closest('details').open=true;form.reportValidity();throw new Error('请先填写有效的巡线参数');}
        const settings=Object.fromEntries(keys.map(key=>[key,Number(form.elements[key].value)]));
        const matches=data=>data.applied&&keys.every(key=>data.applied[key]===settings[key]);
        busy=true;errorUntil=0;form.querySelector('button').disabled=true;
        try{
            let data=await requestJson('GET');
            const waiting=data.request?.status==='pending';
            // Reapply even identical saved values: this also selects PROP in firmware.
            if(!waiting)data=await requestJson('POST',settings);
            const id=data.request?.id, until=Date.now()+3500;
            while(true){
                global.renderLineResponse(data);
                if(data.request?.id!==id)throw new Error('巡线参数被其他请求修改，请重新开启');
                if(data.request.status==='confirmed'){
                    if(!matches(data))throw new Error('已确认参数与当前输入不同，请重新应用');
                    return;
                }
                if(data.request.status!=='pending'||Date.now()>=until)throw new Error(data.request.message||'参数未确认，未开启巡线');
                await new Promise(resolve=>setTimeout(resolve,80));
                data=await requestJson('GET');
            }
        }finally{busy=false;form.querySelector('button').disabled=pending;}
    };
    form.addEventListener('submit',async event=>{
        event.preventDefault();if(busy||pending)return;
        try{await global.ensureLineResponseApplied();}
        catch(error){errorUntil=Date.now()+4000;status.textContent=error.name==='AbortError'?'参数请求超时，未开启巡线':error.message;}
        finally{if(global.dashboardPolling)global.dashboardPolling.refresh();}
    });
})(window);
