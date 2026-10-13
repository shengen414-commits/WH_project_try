/* Run: node mps4264_app/tests/test_pressure_plot.js
 * Standalone renderer regression; no browser or third-party packages needed.
 */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

function fakeCanvas() {
  const dashCalls=[],textCalls=[];
  const context = new Proxy({
    fillText(text) { textCalls.push(text); },
    setLineDash(value) {
      // Match the browser failure that used to occur for channels 61-64.
      if (!Array.isArray(value)) throw new TypeError('Invalid Canvas line dash');
      assert.ok(value.every(v=>Number.isFinite(v) && v>=0));
      dashCalls.push(value.slice());
    }
  }, {get(target,key){return key in target ? target[key] : ()=>{};}});
  return {width:1000,height:420,clientWidth:1000,clientHeight:420,
          getContext:()=>context,addEventListener(){},removeEventListener(){},
          getBoundingClientRect:()=>({left:0,top:0}),dashCalls,textCalls};
}

const sandbox = {
  window:{devicePixelRatio:1},
  document:{getElementById:()=>null,createElement:()=>fakeCanvas()},
  ResizeObserver:class {observe(){} disconnect(){}}
};
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(path.join(__dirname,'../static/pressure_plot.js'),'utf8'),sandbox);
const canvas = fakeCanvas();
const chart = new sandbox.window.PressureTimeChart(canvas);
const channels = Object.fromEntries(Array.from({length:64},(_,i)=>[
  `P${String(i+1).padStart(2,'0')}`, [i+1,i+2,i+1]
]));
chart.setData({name:'regression.csv',unit:'Pa',times:[0.0004,0.0008,0.0012],frames:[1,2,3],channels});
for(let n=1;n<=64;n++) {
  const keys = Object.keys(channels).slice(0,n);
  assert.doesNotThrow(()=>chart.setChannels(keys), `Selecting ${n} channels failed`);
  assert.equal(chart.selected.length,n);
  assert.doesNotThrow(()=>chart.showSample({clientX:500,clientY:100}));
  assert.doesNotThrow(()=>chart.clearHover());
}
// High-numbered fast group 1 channels used to trigger the same error.
assert.doesNotThrow(()=>chart.setChannels(['P01','P05','P52','P56','P60','P64']));
assert.equal(chart.selected.length,6);
chart.setChannels([]);
assert.doesNotThrow(()=>chart.clearHover());
chart.setChannels(Object.keys(channels));
assert.equal(chart.selected.length,64);
const tooltip={hidden:true,style:{},offsetWidth:150,offsetHeight:90};
const syncCanvas=fakeCanvas();
const syncChart=new sandbox.window.PressureTimeChart(syncCanvas,{tooltip});
syncChart.setData({name:'synchronized.csv',unit:'Pa',times:[0.1,0.2,0.3],frames:[1,2,3],
  channels:{P01:[1000,2000,3000]},speed:[0,null,12],speed_unit:'km/h',time_label:'同步记录时间（s）'});
assert.deepEqual(Array.from(syncChart.selected),['P01','speed']);
assert.ok(syncCanvas.textCalls.includes('速度（km/h）'));
assert.ok(syncCanvas.textCalls.includes('同步记录时间（s）'));
assert.notEqual(syncChart.geometry.sy(12),syncChart.geometry.speedY(12));
syncChart.showSample({clientX:syncChart.geometry.sx(0.3),clientY:100});
assert.ok(tooltip.textContent.includes('速度：12 km/h'));
syncChart.showSample({clientX:syncChart.geometry.sx(0.2),clientY:100});
assert.ok(tooltip.textContent.includes('速度：无效'));
syncChart.setChannels(['speed']);
assert.deepEqual(Array.from(syncChart.selected),['speed']);
syncChart.setChannels(['P01']);
assert.ok(syncChart.geometry.right>800); // Right speed axis is removed when unchecked.
syncChart.setData({name:'pressure.csv',unit:'Pa',times:[1,2],frames:[1,2],channels:{P01:[1,2]}});
syncChart.setChannels(['P01','speed']);
assert.deepEqual(Array.from(syncChart.selected),['P01']);
syncChart.setData({name:'invalid-speed.csv',unit:'Pa',times:[1,2,2],frames:[1,2,2],channels:{P01:[1,2,2]},speed:[null,null,null]});
assert.deepEqual(Array.from(syncChart.selected),['P01']);
assert.equal(syncChart.geometry.last,3); // Include all rows at the selected end time.
syncChart.destroy();
chart.destroy();
console.log('PASS: all 64 channel selections, mouse reading, fast-group P64 and reselection');

// Exercise page loading and the actual channel checkbox list.
class FakeElement {
  constructor(tag='div') { this.tag=tag;this.children=[];this.listeners={};this.dataset={};this.style={};this.classList={toggle(){}}; }
  append(...items) { this.children.push(...items); }
  replaceChildren(...items) { this.children=items; }
  addEventListener(event,handler) { this.listeners[event]=handler; }
  querySelectorAll(selector) {
    const inputs=this.children.flatMap(label=>label.children || []).filter(item=>item.tag==='input');
    return inputs.filter(item=>selector==='input:checked'?item.checked:selector==='input:not(:disabled)'?!item.disabled:true);
  }
}
(async()=>{
  const elements=new Map();
  const get=id=>{if(!elements.has(id))elements.set(id,id==='csv-pressure-chart'?fakeCanvas():new FakeElement());return elements.get(id);};
  get('pressure-plot').dataset.initial='true';
  let payload={name:'sync.csv',source_format:'Synchronized',unit:'Pa',times:[1,2],frames:[1,2],channels:{P01:[10,20],P02:[30,40]},
               speed:[0,5],speed_unit:'km/h',frame_count:2,invalid_counts:{P01:0,P02:0}};
  const page={window:{devicePixelRatio:1},document:{getElementById:get,createElement:tag=>tag==='canvas'?fakeCanvas():new FakeElement(tag)},
    ResizeObserver:class {observe(){} disconnect(){}},AbortController,Option:class extends FakeElement {},
    fetch:async url=>({ok:true,json:async()=>url==='/plot/api/files'?{files:['sync.csv']} : payload})};
  vm.createContext(page);vm.runInContext(fs.readFileSync(path.join(__dirname,'../static/pressure_plot.js'),'utf8'),page);
  await new Promise(resolve=>setImmediate(resolve));
  let inputs=get('csv-channels').querySelectorAll('input');
  assert.deepEqual(inputs.map(input=>input.value),['P01','P02','speed']);
  assert.equal(inputs.at(-1).checked,true);
  assert.ok(get('csv-channels').children.at(-1).children.at(-1).textContent.includes('km/h'));
  inputs.at(-1).checked=false;inputs.at(-1).listeners.change();
  assert.ok(get('selection-count').textContent.includes('速度未选'));
  get('select-all').listeners.click();
  assert.equal(inputs.at(-1).checked,true);
  get('select-none').listeners.click();
  assert.ok(inputs.every(input=>!input.checked));
  payload={...payload,name:'ordinary.csv',source_format:'Linux',speed:null};
  get('csv-files').value='ordinary.csv';get('load-file').listeners.click();
  await new Promise(resolve=>setImmediate(resolve));
  assert.deepEqual(get('csv-channels').querySelectorAll('input').map(input=>input.value),['P01','P02']);
  payload={...payload,speed:[null,null]};
  get('load-file').listeners.click();await new Promise(resolve=>setImmediate(resolve));
  inputs=get('csv-channels').querySelectorAll('input');
  assert.equal(inputs.at(-1).value,'speed');
  assert.equal(inputs.at(-1).disabled,true);
  console.log('PASS: synchronized speed checkbox last, default checked, select all/none, ordinary CSV reset and invalid speed');
})().catch(error=>{console.error(error);process.exitCode=1;});
