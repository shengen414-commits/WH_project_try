/* Local perspective renderer: no CDN, no second serial connection. */
(function (root) {
    'use strict';
    function rotate(q, p) {
        const [w,x,y,z] = q, [a,b,c] = p;
        return [(1-2*y*y-2*z*z)*a+(2*x*y-2*w*z)*b+(2*x*z+2*w*y)*c,
                (2*x*y+2*w*z)*a+(1-2*x*x-2*z*z)*b+(2*y*z-2*w*x)*c,
                (2*x*z-2*w*y)*a+(2*y*z+2*w*x)*b+(1-2*x*x-2*y*y)*c];
    }
    if (typeof module !== 'undefined') module.exports = {rotate};
    if (!root.document) return;
    const $ = id => document.getElementById(id), canvas = $('imu-canvas'), ctx = canvas.getContext('2d');
    let q = [1,0,0,0], fresh = false, azimuth = -.8, elevation = .55, dragging = null;
    function draw() {
        const width = canvas.clientWidth, height = canvas.clientHeight, ratio = root.devicePixelRatio || 1;
        canvas.width = width * ratio; canvas.height = height * ratio;
        ctx.setTransform(ratio,0,0,ratio,0,0); ctx.clearRect(0,0,width,height);
        const ca = Math.cos(azimuth), sa = Math.sin(azimuth), ce = Math.cos(elevation), se = Math.sin(elevation);
        const scale = Math.min(width,height)*.22;
        function project(p) {
            const [x,y,z] = p, depth = ce*ca*x+ce*sa*y+se*z, f = 6/(6-depth);
            return [width/2+scale*f*(-sa*x+ca*y), height/2-scale*f*(-se*ca*x-se*sa*y+ce*z), depth];
        }
        function line(a,b,color,lineWidth=1) {
            const p = project(a), r = project(b);
            ctx.strokeStyle = color; ctx.lineWidth = lineWidth;
            ctx.beginPath(); ctx.moveTo(p[0],p[1]); ctx.lineTo(r[0],r[1]); ctx.stroke();
        }
        for (let i=-3;i<=3;i++) {
            line([i,-3,-.5],[i,3,-.5],'#1c3042');
            line([-3,i,-.5],[3,i,-.5],'#1c3042');
        }
        const faces = [];
        function box(center, sizes, colors) {
            const points = [];
            for (const z of [-1,1]) for (const y of [-1,1]) for (const x of [-1,1])
                points.push(project(rotate(q, center.map((v,i)=>v+[x,y,z][i]*sizes[i]))));
            [[0,1,3,2],[4,6,7,5],[0,4,5,1],[2,3,7,6],[0,2,6,4],[1,5,7,3]].forEach((indices,i)=> {
                const vertices = indices.map(j=>points[j]);
                faces.push({vertices, color:colors[i%colors.length], depth:vertices.reduce((s,p)=>s+p[2],0)/4});
            });
        }
        box([0,0,0],[1.25,.72,.075],fresh?['#126a55','#278b6b','#175544']:['#3b4f5d']);
        box([0,0,.17],[.38,.33,.095],['#152633','#263f4d']);
        for (let i=0;i<8;i++) box([-.97,-.55+i*.155,.15],[.045,.045,.08],['#c3b478','#8c815f']);
        faces.sort((a,b)=>a.depth-b.depth).forEach(face=> {
            ctx.fillStyle = face.color; ctx.strokeStyle = '#83aec144'; ctx.lineWidth = 1;
            ctx.beginPath(); face.vertices.forEach((p,i)=>i?ctx.lineTo(p[0],p[1]):ctx.moveTo(p[0],p[1]));
            ctx.closePath(); ctx.fill(); ctx.stroke();
        });
        [['X',[1.9,0,0],'#ff7777'],['Y',[0,1.6,0],'#71dfaa'],['Z',[0,0,1.4],'#72b9ff']].forEach(([name,p,color])=> {
            const endpoint = rotate(q,p); line([0,0,0],endpoint,color,2);
            const v = project(endpoint); ctx.fillStyle = color;
            ctx.beginPath();ctx.arc(v[0],v[1],3,0,Math.PI*2);ctx.fill();
            ctx.font = 'bold 15px system-ui'; ctx.fillText(name,v[0]+9,v[1]-4);
        });
        ctx.font = '12px system-ui'; ctx.fillStyle = '#8ba4b9';
        ctx.fillText(fresh?'MPU6050 · 传感器姿态':'等待新鲜数据 · 模型暂停',18,height-15);
    }
    canvas.addEventListener('pointerdown',e=>{dragging=[e.clientX,e.clientY];canvas.setPointerCapture(e.pointerId);});
    canvas.addEventListener('pointermove',e=> {
        if (!dragging) return;
        azimuth -= (e.clientX-dragging[0])*.008;
        elevation = Math.max(-1.2,Math.min(1.2,elevation+(e.clientY-dragging[1])*.008));
        dragging=[e.clientX,e.clientY]; draw();
    });
    for (const event of ['pointerup','pointercancel','lostpointercapture']) canvas.addEventListener(event,()=>dragging=null);
    new ResizeObserver(draw).observe(canvas);
    function render(state) {
        fresh = state.fresh === true;
        if (fresh && Array.isArray(state.quaternion) && state.quaternion.length===4) q = state.quaternion;
        $('lamp').classList.toggle('live',fresh);
        $('connection').textContent = fresh?'实时姿态':!state.serial_open?'串口未连接':'无新鲜三维数据';
        $('connection').title = state.serial_error || state.message;
        for (const [i,id] of ['roll','pitch','yaw'].entries()) $(id).textContent = fresh?state.angles[i].toFixed(1):'—';
        const sample = state.sample;
        const triple = v => v.map(n=>n.toFixed(2)).join(' / ');
        $('acceleration').textContent = fresh?triple(sample.acceleration):'—';
        $('gyro').textContent = fresh?triple(sample.gyro):'—';
        $('corrected-z').textContent = fresh?sample.corrected_z.toFixed(2):'—';
        $('temperature').textContent = fresh?sample.temperature.toFixed(1):'—';
        $('calibration').textContent = fresh?({NO:'未标定',RUN:'静止标定中…',OK:'Z 零偏已标定'}[sample.calibration]):'—';
        $('age').textContent = state.age_ms===null?'—':`${state.age_ms} ms`;
        $('data-message').textContent = state.serial_error || state.message;
        const diagnostics = state.diagnostics || {}, last = diagnostics.last_invalid;
        $('invalid-count').textContent = diagnostics.invalid_frames || 0;
        const ago = diagnostics.last_invalid_age_ms == null ? '' : `（${(diagnostics.last_invalid_age_ms/1000).toFixed(1)} 秒前）`;
        $('invalid-reason').textContent = last ? `最近原因${ago}：${last.reason}；连续无效 ${diagnostics.consecutive_invalid || 0} 帧；累计有效 ${state.count || 0} 帧` : '尚未检测到无效帧';
        $('invalid-raw').textContent = last ? last.raw : '';
        draw();
    }
    let timer = null, polling = false, commandBusy = false;
    async function poll() {
        if (polling || document.hidden) return;
        polling = true;
        const controller = new AbortController(), timeout = setTimeout(()=>controller.abort(),1000);
        try {
            const response = await fetch('/imu/status',{cache:'no-store',signal:controller.signal});
            if (!response.ok) throw new Error('状态请求失败');
            render(await response.json());
        } catch(e) {
            fresh = false; $('lamp').classList.remove('live'); $('connection').textContent = '连接中断';
            for (const id of ['roll','pitch','yaw','acceleration','gyro','corrected-z','temperature','calibration']) $(id).textContent='—';
            $('data-message').textContent = '无法读取后台状态，请检查 Dashboard 连接'; draw();
        } finally {
            clearTimeout(timeout); polling = false;
            if (!document.hidden) timer = setTimeout(poll,60); // One request in flight, no queue.
        }
    }
    document.addEventListener('visibilitychange',()=>{clearTimeout(timer);if (!document.hidden) poll();});
    for (const button of document.querySelectorAll('[data-action]')) button.addEventListener('click',async()=> {
        if (commandBusy) return;
        commandBusy = true;
        const buttons = document.querySelectorAll('[data-action]');buttons.forEach(b=>b.disabled=true);
        const controller = new AbortController(), timeout = setTimeout(()=>controller.abort(),3000);
        try {
            const response = await fetch('/imu/command',{method:'POST',headers:{'Content-Type':'application/json'},
                body:JSON.stringify({action:button.dataset.action}),signal:controller.signal});
            const data = await response.json();
            $('message').textContent = data.error || data.message;
        } catch(e) { $('message').textContent = '命令请求失败，请检查连接；是否已执行需以实际读数确认'; }
        finally {clearTimeout(timeout);commandBusy=false;buttons.forEach(b=>b.disabled=false);}
    });
    draw(); poll();
})(typeof window !== 'undefined'?window:globalThis);
