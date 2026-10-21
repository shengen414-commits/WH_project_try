(function(global){
    const form=document.getElementById('line-response-form');if(!form)return;
    let sentAt=-1, busy=false, pending=false, epoch=null, errorUntil=0;
    const status=document.getElementById('line-response-status');
    global.renderLineResponse=function(data){
        if(!data)return;
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
    form.addEventListener('submit',async event=>{
        event.preventDefault();if(busy||pending)return;busy=true;errorUntil=0;form.querySelector('button').disabled=true;
        const controller=new AbortController(), timer=setTimeout(()=>controller.abort(),1800);
        try{
            const settings=Object.fromEntries(['left_us','right_us','deadband','slew_us_s','direction'].map(key=>[key,Number(form.elements[key].value)]));
            const response=await fetch('/steering/line/config',{method:'POST',headers:{'Content-Type':'application/json'},signal:controller.signal,body:JSON.stringify(settings)});
            const data=await response.json();if(!response.ok)throw new Error(data.error||`HTTP ${response.status}`);
            global.renderLineResponse(data);
        }catch(error){errorUntil=Date.now()+4000;status.textContent=error.name==='AbortError'?'参数请求超时，等待状态确认后重试':error.message;}
        finally{clearTimeout(timer);busy=false;form.querySelector('button').disabled=pending;if(global.dashboardPolling)global.dashboardPolling.refresh();}
    });
})(window);
