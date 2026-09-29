"use strict";

const $ = (id) => document.getElementById(id);
const chart = $("pressure-chart");
const ctx = chart.getContext("2d");
const colors = ["#3cc7db", "#e4ad63", "#96d38c", "#e986a3", "#a6a0ed", "#d4cb71"];
let history = [];
let lastFrameKey = "";
let latestStatus = null;

function showMessage(message, error = false) {
  const box = $("message");
  box.textContent = message;
  box.classList.toggle("error", error);
}

async function api(path, payload = null) {
  const response = await fetch(path, {
    method: payload === null ? "GET" : "POST",
    headers: payload === null ? {} : { "Content-Type": "application/json" },
    body: payload === null ? undefined : JSON.stringify(payload),
    cache: "no-store",
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
  return data;
}

function onClick(id, action, successText) {
  $(id).addEventListener("click", async () => {
    const button = $(id);
    button.disabled = true;
    try {
      const result = await action();
      if (result === false) return;
      showMessage(successText || "操作已完成");
      await refreshStatus();
      return result;
    } catch (error) {
      showMessage(error.message, true);
    } finally {
      button.disabled = false;
    }
  });
}

const ip = () => $("device-ip").value.trim();
const port = () => Number($("control-port").value);
const udpPort = () => Number($("udp-port").value);

onClick("connect-btn", () => api("/api/connect", { device_ip: ip(), control_port: port() }), "设备已连接，可读取信息。");
onClick("reconnect-btn", () => api("/api/connect", { device_ip: ip(), control_port: port(), confirm_reboot: true }), "已按重启后的设备重新连接；请读取设备信息核对 UDP 设置。");
onClick("disconnect-btn", () => api("/api/disconnect", {}), "设备已断开，采集文件已安全关闭。");
onClick("info-btn", async () => {
  const data = await api("/api/device-info", {});
  $("device-info").textContent = Object.entries(data.info).map(([key, value]) => `>>> ${key}\n${value}`).join("\n\n");
  const match = data.info["LIST UDP"].match(/SET IPUDP\s+([\d.]+)\s+(\d+)/i);
  if (match) { $("host-ip").value = match[1]; $("udp-port").value = match[2]; }
  return data;
}, "设备信息已读取。请核对阀位 PX、UDP 目标和扫描设置。");

onClick("configure-btn", async () => {
  if (!confirm("将设置设备的 UDP 二进制输出并执行 SAVE。确认 Orange Pi 网口 IP 和端口正确，且当前没有其他采集任务？")) return false;
  return api("/api/configure-udp", { host_ip: $("host-ip").value.trim(), udp_port: udpPort() });
}, "UDP 配置已保存。请给 MPS4264 断电重启，再点击“已重启，重新连接”。");

onClick("set-btn", () => api("/api/parameters", {
  rate: Number($("rate").value), fps: Number($("fps").value),
  fast_group: Number($("fast-group").value), read_mode: Number($("read-mode").value),
  subset_size: Number($("subset-size").value),
}), "SET 参数已发送；如需断电后保留，请点击 SAVE。");
onClick("save-btn", () => api("/api/save", {}), "SAVE 已完成，设备已返回提示符。");
onClick("new-file-btn", async () => {
  const name = $("file-name").value.trim() || `mps_${new Date().toISOString().replace(/[-:T.Z]/g, "").slice(0, 14)}`;
  $("file-name").value = name;
  const result = await api("/api/new-file", { name, udp_port: udpPort() });
  await refreshFiles();
  history = []; lastFrameKey = ""; drawChart();
  return result;
}, "已新建文件并监听 UDP；现在可以 SCAN。");
onClick("scan-btn", () => api("/api/scan", {}), "已发送 SCAN；正在接收 UDP 数据。");
onClick("stop-btn", () => api("/api/stop", {}), "已发送 STOP；可继续扫描或关闭文件。");
onClick("close-btn", async () => {
  const result = await api("/api/close-file", {});
  await refreshFiles();
  return result;
}, "采集文件已关闭；可以转换 CSV。");
onClick("refresh-files-btn", refreshFiles, "文件列表已刷新。");
onClick("convert-btn", async () => {
  const filename = $("convert-file").value;
  if (!filename) throw new Error("请先选择 .dat 文件");
  const result = await api("/api/convert", {
    filename, fast_group: $("convert-group").value || null,
  });
  $("convert-status").textContent = "正在后台转换，原始 .dat 保持不变…";
  return result;
}, "转换任务已启动，请查看下方进度。");
onClick("calz-btn", async () => {
  if (!confirm("确认当前满足校零条件：CAL/REF 等压，或 PX 状态下无风、测点与参考端均无外加压差？")) return false;
  return api("/api/calz", { zero_pressure_confirmed: true });
}, "CALZ 命令完成；请核查设备返回信息与后续零位。");

function channels() {
  const values = $("chart-channels").value.split(",").map((v) => Number(v.trim()));
  return [...new Set(values)].filter((n) => Number.isInteger(n) && n >= 1 && n <= 64).slice(0, 6);
}

function drawChart() {
  const width = chart.width, height = chart.height;
  ctx.fillStyle = "#0d1c2b"; ctx.fillRect(0, 0, width, height);
  const selected = channels();
  const valid = history.flatMap((item) => selected.map((n) => item[n - 1]).filter(Number.isFinite));
  if (!valid.length) {
    ctx.fillStyle = "#7894a9"; ctx.font = "19px sans-serif";
    ctx.fillText("等待压力数据…", 38, height / 2);
    return;
  }
  let min = Math.min(...valid), max = Math.max(...valid);
  const pad = Math.max((max - min) * 0.12, 0.001);
  min -= pad; max += pad;
  const left = 80, right = width - 24, top = 38, bottom = height - 38;
  ctx.strokeStyle = "#254052"; ctx.lineWidth = 1;
  ctx.fillStyle = "#8da9b8"; ctx.font = "14px sans-serif";
  for (let i = 0; i <= 4; i++) {
    const y = top + (bottom - top) * i / 4;
    ctx.beginPath(); ctx.moveTo(left, y); ctx.lineTo(right, y); ctx.stroke();
    ctx.fillText((max - (max - min) * i / 4).toFixed(2), 12, y + 4);
  }
  selected.forEach((number, index) => {
    ctx.strokeStyle = colors[index]; ctx.lineWidth = 2; ctx.beginPath();
    let active = false;
    history.forEach((item, offset) => {
      const value = item[number - 1];
      if (!Number.isFinite(value)) { active = false; return; }
      const x = left + (right - left) * offset / Math.max(1, history.length - 1);
      const y = bottom - (value - min) / (max - min) * (bottom - top);
      if (!active) { ctx.moveTo(x, y); active = true; } else ctx.lineTo(x, y);
    });
    ctx.stroke();
    ctx.fillStyle = colors[index]; ctx.fillText(`P${String(number).padStart(2, "0")}`, left + 82 * index, 22);
  });
}

function renderChannels(frame) {
  const grid = $("channel-grid");
  grid.replaceChildren();
  frame.pressures.forEach((value, index) => {
    const item = document.createElement("div");
    item.className = "channel";
    item.textContent = `P${String(index + 1).padStart(2, "0")}  ${value === null ? "—" : Number(value).toFixed(3)}`;
    grid.append(item);
  });
}

async function refreshStatus() {
  const state = await api("/api/status");
  latestStatus = state;
  $("connection-light").classList.toggle("online", state.connected);
  $("connection-text").textContent = state.connected ? `已连接 ${state.device_ip}` : "未连接";
  $("scan-state").textContent = state.scanning ? "扫描中" : state.file_open ? "文件待命" : "待机";
  $("frame-count").textContent = state.frames_received.toLocaleString();
  $("gap-count").textContent = state.frame_gaps.toLocaleString();
  $("bad-count").textContent = state.bad_datagrams.toLocaleString();
  $("last-frame-number").textContent = state.last_frame ? state.last_frame.number : "—";
  $("units").textContent = state.last_frame ? state.last_frame.unit_label : "—";
  $("chart-note").textContent = state.last_frame
    ? `${state.file_name || "未存盘"} · ${state.last_frame.rate_hz.toFixed(1)} Hz · ${state.last_frame.fast_group_unknown ? "快速组未知，48 路可能无效" : "预览约 5 次/秒"}`
    : state.file_name || "等待数据";
  if (state.last_frame) {
    const key = `${state.file_name}/${state.last_frame.number}/${state.last_receive_unix_ns}`;
    if (key !== lastFrameKey) {
      lastFrameKey = key;
      history.push(state.last_frame.pressures);
      if (history.length > 180) history.shift();
      drawChart();
      renderChannels(state.last_frame);
    }
  }
  const events = $("events");
  events.replaceChildren();
  state.events.slice().reverse().forEach((event) => {
    const row = document.createElement("div");
    row.textContent = `[${event.time}] ${event.message}`;
    events.append(row);
  });
  if (state.last_error) showMessage(state.last_error, true);
  else if (state.no_udp_warning) showMessage("SCAN 已发出，但 3 秒内没有收到压力帧。请核对 LIST UDP 的目标 IP/端口、设备重启状态和防火墙。", true);
}

async function refreshFiles() {
  const data = await api("/api/files");
  const select = $("convert-file");
  const selected = select.value;
  select.replaceChildren(new Option("选择 .dat 文件", ""));
  data.files.forEach((item) => select.add(new Option(`${item.name} (${(item.bytes / 1024).toFixed(1)} KiB)`, item.name)));
  select.value = selected;
}

async function refreshConversion() {
  const job = await api("/api/convert-status");
  $("convert-status").textContent = job.state === "running" ? "正在后台转换…"
    : job.state === "done" ? `已完成：${job.result.frames} 帧 → ${job.result.csv_file}`
    : job.state === "error" ? `转换失败：${job.error}` : "尚无转换任务。";
}

$("chart-channels").addEventListener("change", drawChart);
setInterval(() => { $("clock").textContent = new Date().toLocaleTimeString(); }, 1000);
setInterval(() => refreshStatus().catch((error) => showMessage(`状态读取失败：${error.message}`, true)), 200);
setInterval(() => refreshConversion().catch(() => {}), 1000);
refreshStatus().catch(() => {});
refreshFiles().catch(() => {});
refreshConversion().catch(() => {});
drawChart();
