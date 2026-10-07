/* Reusable canvas plot. No CDN, hardware calls or changes to source CSV. */
(() => {
  'use strict';
  const colors = ['#59c7ff','#ffae69','#8bdb8b','#e897df','#aaabff','#67ded2',
                  '#ff8796','#dfce69','#95b7df','#deb995','#99c6ac','#bd9cc9'];
  const style = n => ({color: colors[(n-1)%colors.length], dash:[[],[7,3],[2,3],[8,3,2,3],[10,4,2,4]][Math.floor((n-1)/colors.length)]});
  const number = key => Number(key.slice(1));
  const label = v => Math.abs(v)>=100000 || (v!==0 && Math.abs(v)<0.001) ? v.toExponential(2) : Number(v.toPrecision(6)).toString();
  const lowerBound = (array,value) => {let lo=0,hi=array.length;while(lo<hi){const m=(lo+hi)>>1;if(array[m]<value)lo=m+1;else hi=m;}return lo;};

  class PressureTimeChart {
    constructor(canvas, {tooltip=null}={}) {
      this.canvas=canvas;this.ctx=canvas.getContext('2d');this.tooltip=tooltip;
      this.data=null;this.selected=[];this.range=null;this.geometry=null;
      this.base=document.createElement('canvas');
      this.resizeObserver=new ResizeObserver(()=>this.draw());this.resizeObserver.observe(canvas);
      this.pointerHandler=event=>this.showSample(event);
      this.leaveHandler=()=>this.clearHover();
      canvas.addEventListener('pointermove',this.pointerHandler);
      canvas.addEventListener('pointerleave',this.leaveHandler);
      canvas.addEventListener('click',this.pointerHandler);
      this.draw();
    }
    setData(data) {
      if(!data.times?.length || !data.channels || data.frames?.length!==data.times.length)throw new Error('绘图数据不完整');
      this.data=data;
      this.selected=Object.keys(data.channels).filter(key=>data.channels[key].some(v=>v!==null)).slice(0,4);
      this.range=[data.times[0],data.times.at(-1)];this.draw();
    }
    setChannels(keys) {
      if(!this.data)return;
      this.selected=[...new Set(keys)].filter(key=>this.data.channels[key]?.some(v=>v!==null)).sort((a,b)=>number(a)-number(b));this.draw();
    }
    setRange(start,end) {
      if(!this.data)return;
      if(!Number.isFinite(start)||!Number.isFinite(end)||start>=end)throw new Error('开始时间必须小于结束时间');
      if(end<this.data.times[0]||start>this.data.times.at(-1))throw new Error('选择的范围不包含数据');
      this.range=[start,end];this.draw();
    }
    draw() {
      const {canvas,ctx}=this,w=canvas.clientWidth || 600,h=canvas.clientHeight || 420,dpr=window.devicePixelRatio || 1;
      canvas.width=Math.round(w*dpr);canvas.height=Math.round(h*dpr);ctx.setTransform(dpr,0,0,dpr,0,0);
      ctx.fillStyle='#08131f';ctx.fillRect(0,0,w,h);ctx.font='13px system-ui';ctx.fillStyle='#e7f2f5';
      this.geometry=null;
      if(this.tooltip)this.tooltip.hidden=true;
      if(!this.data){ctx.fillText('请先加载 CSV 文件',30,50);return;}
      const times=this.data.times;
      const [start,end]=this.range;const first=lowerBound(times,start),last=Math.min(times.length,lowerBound(times,end)+ (times[lowerBound(times,end)]===end?1:0));
      let ymin=Infinity,ymax=-Infinity;
      this.selected.forEach(key=>{const values=this.data.channels[key];for(let i=first;i<last;i++){const v=values[i];if(v!==null){ymin=Math.min(ymin,v);ymax=Math.max(ymax,v);}}});
      if(!Number.isFinite(ymin)){ymin=0;ymax=1;}
      const pad=(ymax-ymin)*0.06 || Math.max(Math.abs(ymin)*0.01,0.1);ymin-=pad;ymax+=pad;
      const left=w<450?78:94,right=w-18,top=38,bottom=h-50;
      const xend=end>start?end:start+0.002;
      const sx=t=>left+(t-start)/(xend-start)*(right-left),sy=v=>bottom-(v-ymin)/(ymax-ymin)*(bottom-top);
      this.geometry={left,right,top,bottom,sx,sy,start,end:xend,first,last,w,h};
      ctx.strokeStyle='#29404f';ctx.lineWidth=1;ctx.strokeRect(left,top,right-left,bottom-top);
      const ticks=w<450?3:5;
      for(let i=0;i<=ticks;i++) {
        const x=left+(right-left)*i/ticks,t=start+(xend-start)*i/ticks;
        ctx.strokeStyle='#203442';ctx.beginPath();ctx.moveTo(x,top);ctx.lineTo(x,bottom);ctx.stroke();
        ctx.fillStyle='#c5d5de';ctx.textAlign=i===0?'left':i===ticks?'right':'center';ctx.fillText(label(t),x,bottom+20);
      }
      for(let i=0;i<=5;i++) {
        const y=bottom-(bottom-top)*i/5,v=ymin+(ymax-ymin)*i/5;
        ctx.strokeStyle='#203442';ctx.beginPath();ctx.moveTo(left,y);ctx.lineTo(right,y);ctx.stroke();
        ctx.fillStyle='#c5d5de';ctx.textAlign='right';ctx.fillText(label(v),left-9,y+4);
      }
      ctx.textAlign='center';ctx.fillStyle='#e7f2f5';ctx.fillText('设备帧时间（s）',(left+right)/2,h-8);
      ctx.save();ctx.translate(16,(top+bottom)/2);ctx.rotate(-Math.PI/2);ctx.fillText(`压力（${this.data.unit}）`,0,0);ctx.restore();
      ctx.textAlign='left';ctx.fillText(`${this.data.name} · ${this.selected.length} 路`,left,20,right-left);
      ctx.save();ctx.beginPath();ctx.rect(left,top,right-left,bottom-top);ctx.clip();
      this.selected.forEach(key=>{
        const values=this.data.channels[key],s=style(number(key));ctx.strokeStyle=s.color;ctx.setLineDash(s.dash);ctx.lineWidth=1.2;
        // Split at invalid readings and actual frame gaps before downsampling.
        let run=[];
        const flush=()=>{
          if(!run.length)return;
          const step=Math.max(1,Math.ceil(run.length/Math.max(1,right-left)));
          const points=[];
          for(let k=0;k<run.length;k+=step){const group=run.slice(k,k+step);let min=group[0],max=group[0];group.forEach(i=>{if(values[i]<values[min])min=i;if(values[i]>values[max])max=i;});points.push(...[...new Set([group[0],min,max,group.at(-1)])].sort((a,b)=>a-b));}
          ctx.beginPath();points.forEach((i,k)=>{if(k===0)ctx.moveTo(sx(times[i]),sy(values[i]));else ctx.lineTo(sx(times[i]),sy(values[i]));});ctx.stroke();
          if(points.length===1){ctx.fillStyle=s.color;ctx.beginPath();ctx.arc(sx(times[points[0]]),sy(values[points[0]]),2,0,Math.PI*2);ctx.fill();}
          run=[];
        };
        for(let i=first;i<last;i++){if(values[i]===null){flush();continue;}if(run.length && this.data.frames[i]!==this.data.frames[i-1]+1)flush();run.push(i);}flush();
      });ctx.restore();
      if(!this.selected.length){ctx.fillStyle='#c5d5de';ctx.textAlign='center';ctx.fillText('请选择需要显示的通道',(left+right)/2,(top+bottom)/2);}
      this.base.width=canvas.width;this.base.height=canvas.height;this.base.getContext('2d').drawImage(canvas,0,0);
    }
    clearHover() {
      this.ctx.save();this.ctx.setTransform(1,0,0,1,0,0);this.ctx.clearRect(0,0,this.canvas.width,this.canvas.height);this.ctx.drawImage(this.base,0,0);this.ctx.restore();
      if(this.tooltip)this.tooltip.hidden=true;
    }
    showSample(event) {
      const g=this.geometry;if(!g || !this.selected.length)return;
      const rect=this.canvas.getBoundingClientRect(),px=event.clientX-rect.left,py=event.clientY-rect.top;
      if(px<g.left || px>g.right || py<g.top || py>g.bottom){this.clearHover();return;}
      const target=g.start+(px-g.left)/(g.right-g.left)*(g.end-g.start),times=this.data.times;
      let i=lowerBound(times,target);if(i===times.length || (i>0 && Math.abs(times[i-1]-target)<Math.abs(times[i]-target)))i--;
      if(i<g.first||i>=g.last){this.clearHover();return;}
      this.clearHover();const x=g.sx(times[i]);this.ctx.strokeStyle='#a7becb';this.ctx.setLineDash([3,3]);this.ctx.beginPath();this.ctx.moveTo(x,g.top);this.ctx.lineTo(x,g.bottom);this.ctx.stroke();this.ctx.setLineDash([]);
      if(this.tooltip){const rows=this.selected.slice(0,12).map(key=>`${key}：${this.data.channels[key][i]===null?'无效':label(this.data.channels[key][i])+' '+this.data.unit}`);if(this.selected.length>12)rows.push(`另有 ${this.selected.length-12} 路，减少选择可查看读数`);this.tooltip.textContent=`帧 ${this.data.frames[i]} · ${label(times[i])} s\n${rows.join('\n')}`;this.tooltip.hidden=false;this.tooltip.style.left=Math.max(0,Math.min(px+14,g.w-this.tooltip.offsetWidth-4))+'px';this.tooltip.style.top=Math.max(0,Math.min(py+14,g.h-this.tooltip.offsetHeight))+'px';}
    }
    exportPNG() {
      if(!this.data)throw new Error('请先加载 CSV');this.clearHover();
      const dpr=window.devicePixelRatio || 1,w=this.canvas.clientWidth,h=this.canvas.clientHeight;
      const columns=Math.max(1,Math.floor(w/120)),legendHeight=30+Math.ceil(this.selected.length/columns)*26;
      const out=document.createElement('canvas');out.width=this.canvas.width;out.height=Math.round((h+legendHeight)*dpr);
      const ctx=out.getContext('2d');ctx.fillStyle='#08131f';ctx.fillRect(0,0,out.width,out.height);ctx.drawImage(this.base,0,0);ctx.scale(dpr,dpr);ctx.font='13px system-ui';
      this.selected.forEach((key,i)=>{const x=16+(i%columns)*120,y=h+22+Math.floor(i/columns)*26,s=style(number(key));ctx.strokeStyle=s.color;ctx.setLineDash(s.dash);ctx.beginPath();ctx.moveTo(x,y-4);ctx.lineTo(x+24,y-4);ctx.stroke();ctx.fillStyle='#e7f2f5';ctx.fillText(key,x+30,y);});
      const a=document.createElement('a');a.download=this.data.name.replace(/\.csv$/i,'')+'-pressure.png';a.href=out.toDataURL('image/png');a.click();
    }
    destroy() {this.resizeObserver.disconnect();this.canvas.removeEventListener('pointermove',this.pointerHandler);this.canvas.removeEventListener('pointerleave',this.leaveHandler);this.canvas.removeEventListener('click',this.pointerHandler);}
  }
  window.PressureTimeChart=PressureTimeChart;

  const root=document.getElementById('pressure-plot');if(!root)return;
  const el=id=>document.getElementById(id),message=el('plot-message');
  const chart=new PressureTimeChart(el('csv-pressure-chart'),{tooltip:el('plot-tooltip')});
  const inputs=el('csv-channels');let data=null,activeRequest=null;
  const notify=(text,error=false)=>{message.textContent=text;message.classList.toggle('error',error);};
  function syncSelection(){chart.setChannels([...inputs.querySelectorAll('input:checked')].map(input=>input.value));el('selection-count').textContent=`已选 ${chart.selected.length} / ${Object.keys(data.channels).length} 路`;}
  function loadData(value){
    chart.setData(value);data=value;inputs.replaceChildren();
    Object.keys(data.channels).forEach(key=>{
      const labelEl=document.createElement('label'),input=document.createElement('input'),swatch=document.createElement('span'),text=document.createElement('span');
      input.type='checkbox';input.value=key;input.checked=chart.selected.includes(key);input.disabled=data.invalid_counts[key]===data.frame_count;
      swatch.className='swatch';swatch.style.background=style(number(key)).color;
      text.textContent=key+(input.disabled?'（全部无效）':data.invalid_counts[key]?`（无效 ${data.invalid_counts[key]} 帧）`:'');
      if(input.disabled)labelEl.className='channel-invalid';labelEl.append(input,swatch,text);inputs.append(labelEl);input.addEventListener('change',syncSelection);
    });
    el('time-start').value=data.times[0];el('time-end').value=data.times.at(-1);syncSelection();
    notify(`${data.name} · ${data.source_format} · ${data.frame_count} 帧 · ${data.unit} · ${label(data.times[0])}～${label(data.times.at(-1))} s`);
  }
  async function requestData(url,options={}) {
    activeRequest?.abort();const controller=new AbortController();activeRequest=controller;notify('正在读取 CSV…');
    try{const response=await fetch(url,{...options,signal:controller.signal});const value=await response.json();if(!response.ok)throw new Error(value.error || `读取失败 ${response.status}`);loadData(value);}catch(error){if(error.name!=='AbortError')notify(error.message,true);}finally{if(activeRequest===controller)activeRequest=null;}
  }
  async function refresh(){try{const response=await fetch('/plot/api/files');if(!response.ok)throw new Error('文件列表读取失败');const value=await response.json();const select=el('csv-files'),previous=select.value;select.replaceChildren(new Option('选择 CSV 文件',''));value.files.forEach(name=>select.append(new Option(name,name)));select.value=previous;}catch(error){notify(error.message,true);}}
  el('refresh-files').addEventListener('click',refresh);
  el('load-file').addEventListener('click',()=>{const name=el('csv-files').value;if(!name){notify('请先选择文件',true);return;}requestData('/plot/api/data?filename='+encodeURIComponent(name));});
  el('csv-upload').addEventListener('change',()=>{const file=el('csv-upload').files[0];if(!file)return;const form=new FormData();form.append('file',file);requestData('/plot/api/upload',{method:'POST',body:form});});
  el('apply-range').addEventListener('click',()=>{try{if(!data)throw new Error('请先加载 CSV');if(!el('time-start').value||!el('time-end').value)throw new Error('请填写时间范围');chart.setRange(Number(el('time-start').value),Number(el('time-end').value));notify(`显示 ${el('time-start').value}～${el('time-end').value} s`);}catch(error){notify(error.message,true);}});
  el('reset-range').addEventListener('click',()=>{if(!data)return;el('time-start').value=data.times[0];el('time-end').value=data.times.at(-1);chart.range=[data.times[0],data.times.at(-1)];chart.draw();});
  el('select-all').addEventListener('click',()=>{if(!data)return;inputs.querySelectorAll('input:not(:disabled)').forEach(input=>input.checked=true);syncSelection();});
  el('select-none').addEventListener('click',()=>{if(!data)return;inputs.querySelectorAll('input').forEach(input=>input.checked=false);syncSelection();});
  el('save-image').addEventListener('click',()=>{try{chart.exportPNG();}catch(error){notify(error.message,true);}});
  refresh();if(root.dataset.initial==='true')requestData('/plot/api/data');
})();
