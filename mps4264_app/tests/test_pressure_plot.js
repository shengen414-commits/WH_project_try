/* Run: node mps4264_app/tests/test_pressure_plot.js
 * Standalone renderer regression; no browser or third-party packages needed.
 */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

function fakeCanvas() {
  const dashCalls=[];
  const context = new Proxy({
    setLineDash(value) {
      // Match the browser failure that used to occur for channels 61-64.
      if (!Array.isArray(value)) throw new TypeError('Invalid Canvas line dash');
      assert.ok(value.every(v=>Number.isFinite(v) && v>=0));
      dashCalls.push(value.slice());
    }
  }, {get(target,key){return key in target ? target[key] : ()=>{};}});
  return {width:1000,height:420,clientWidth:1000,clientHeight:420,
          getContext:()=>context,addEventListener(){},removeEventListener(){},
          getBoundingClientRect:()=>({left:0,top:0}),dashCalls};
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
chart.destroy();
console.log('PASS: all 64 channel selections, mouse reading, fast-group P64 and reselection');
