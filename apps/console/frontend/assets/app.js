const $ = (selector) => document.querySelector(selector);

let state = {devices: [], rovers: [], known_resources: [], events: [], trajectories: {}, maps: {}, paths: {}, camera_feeds: {}};
let streamConnected = false;
let selectedRoverId = localStorage.getItem('selectedRoverId') || '';
let activePage = localStorage.getItem('activePage') || 'devices';
let lastRevision = -1;
let reconnectTimer = null;
let cameraSignature = '';
let mapMode = localStorage.getItem('mapMode') === '3d' ? '3d' : '2d';
const mapCache = new Map();
const pageMeta = {
  devices: ['NETWORK', '设备连接'], overview: ['LIVE OVERVIEW', '运行总览'],
  map: ['SLAM & NAVIGATION', '地图导航'], perception: ['PERCEPTION', '目标感知'],
  mission: ['MISSION CONTROL', '任务控制'], diagnostics: ['SYSTEM', '系统诊断']
};

const modeLabels = {
  IDLE: '空闲待命', SEARCH: '接收任务', EXPLORE: '探索环境', TARGET_FOUND: '发现目标',
  NAVIGATE: '规划前往', COLLECTING: '抵近目标', DONE: '任务完成', RETREAT: '正在返航',
  PAUSED: '已暂停', ERROR: '任务异常', UNKNOWN: '状态未知'
};
const eventLabels = {
  ROVER_FIRST_SEEN: '小车开始发送遥测', ROVER_DISCOVERED_ON_LAN: '发现局域网小车',
  OPERATOR_CONNECTED: '操作员已连接', OPERATOR_DISCONNECTED: '操作员已断开', OPERATOR_COMMAND: '操作指令已提交',
  OPERATOR_DIRECTIVE_ACCEPTED: '小车已接收指令', OPERATOR_DIRECTIVE_REJECTED: '指令被拒绝',
  TARGET_FOUND: '发现指定目标', TARGET_NAVIGATION_STARTED: '开始前往目标', TARGET_REACHED: '已到达目标',
  RESOURCE_DISCOVERED: '发现目标', RESOURCE_UPDATED: '目标位置已更新', MISSION_COMPLETED: '探索任务完成',
  ESTOP: '紧急停车已触发', COLLISION_WARNING: '避障安全介入', SAFETY_STOP: '安全停车'
};

function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>'"]/g, (char) => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;'}[char]));
}
function num(value, digits = 2, suffix = '') {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? `${parsed.toFixed(digits)}${suffix}` : '—';
}
function timeText(timestamp) {
  return Number.isFinite(Number(timestamp)) ? new Date(Number(timestamp) * 1000).toLocaleTimeString('zh-CN', {hour12: false}) : '—';
}
function ageText(seconds) {
  const value = Number(seconds);
  if (!Number.isFinite(value)) return '—';
  if (value < 1) return `${Math.round(value * 1000)} ms`;
  return `${value.toFixed(1)} s`;
}
function toast(message) {
  const element = $('#toast');
  element.textContent = message;
  element.classList.add('show');
  clearTimeout(element._timer);
  element._timer = setTimeout(() => element.classList.remove('show'), 2400);
}
async function post(url, body = {}) {
  const response = await fetch(url, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)});
  const payload = await response.json();
  if (!response.ok || payload.ok === false) throw new Error(payload.error || '请求失败');
  return payload.result;
}
function selectedRover() {
  const rows = state.rovers || [];
  return rows.find((row) => row.rover_id === selectedRoverId) || null;
}
function selectedDevice() {
  return (state.devices || []).find((row) => row.rover_id === selectedRoverId) || null;
}
function selectedId() {
  return selectedRoverId;
}
function missionMode(rover) {
  return String(rover?.mode || 'UNKNOWN').trim().toUpperCase();
}
function currentTarget(rover) {
  const healthTarget = rover?.health?.target_class;
  if (healthTarget) return String(healthTarget);
  const match = String(rover?.mission || '').match(/target=([^·]+)/i);
  return match ? match[1].trim() : '';
}
function normalizeSafety(raw) {
  if (!raw || raw === 'UNKNOWN') return '等待安全状态';
  try {
    const parsed = JSON.parse(raw);
    return String(parsed.reason || parsed.status || parsed.state || raw);
  } catch (_) {
    return String(raw);
  }
}

function render() {
  if (!selectedRoverId && state.connected_rover_id) selectedRoverId = state.connected_rover_id;
  renderPages();
  renderDevices();
  renderConnection();
  renderOverview();
  renderMission();
  renderHealth();
  renderTargets();
  renderEvents();
  renderCamera();
  renderMapMode();
  loadMap();
  updateControls();
  renderDiagnostics();
}

function renderMapMode() {
  $('#map2dButton').classList.toggle('active', mapMode === '2d');
  $('#map3dButton').classList.toggle('active', mapMode === '3d');
}

function renderPages() {
  if (!pageMeta[activePage]) activePage = 'devices';
  document.querySelectorAll('[data-page-panel]').forEach((element) => element.classList.toggle('active', element.dataset.pagePanel === activePage));
  document.querySelectorAll('[data-page]').forEach((element) => element.classList.toggle('active', element.dataset.page === activePage));
  $('#pageEyebrow').textContent = pageMeta[activePage][0];
  $('#pageTitle').textContent = pageMeta[activePage][1];
  localStorage.setItem('activePage', activePage);
  if (activePage === 'map') requestAnimationFrame(drawMap);
}

function navigate(page) {
  activePage = pageMeta[page] ? page : 'devices';
  $('#sidebar').classList.remove('open');
  $('#sidebarBackdrop').classList.remove('show');
  renderPages();
}

function renderDevices() {
  const rows = state.devices || [];
  const onlineCount = rows.filter((row) => row.online).length;
  $('#deviceCount').textContent = `${onlineCount} 台在线`;
  if (!rows.length) {
    $('#deviceList').innerHTML = '<div class="device-empty"><div class="empty-orbit"></div><strong>正在寻找小车</strong><span>请确认小车和平台位于同一局域网，并已启动 ROS2 网关。</span></div>';
    return;
  }
  const capabilityLabels = {lidar: '激光雷达', camera: '相机', depth: '深度', imu: 'IMU', localization: '定位'};
  $('#deviceList').innerHTML = rows.map((device) => {
    const isCurrent = selectedRoverId === device.rover_id && state.connected_rover_id === device.rover_id;
    const capabilities = (device.capabilities || []).map((value) => `<span>${escapeHtml(capabilityLabels[value] || value)}</span>`).join('') || '<span>等待能力信息</span>';
    const buttonText = isCurrent ? '已连接' : device.online ? '连接' : '已离线';
    return `<article class="device-card${isCurrent ? ' connected' : ''}${device.online ? '' : ' offline'}">
      <div class="device-top"><div class="device-avatar">${escapeHtml(String(device.name || device.rover_id).slice(0, 1).toUpperCase())}</div><span class="online-label${device.online ? '' : ' offline'}">${device.online ? '在线' : '离线'}</span></div>
      <h3>${escapeHtml(device.name || device.rover_id)}</h3><span class="device-subtitle">${escapeHtml(device.ip_address || '地址待确认')} · ${escapeHtml(device.model || 'ROS2 Rover')}</span>
      <div class="capability-row">${capabilities}</div>
      <div class="device-footer"><small>${device.telemetry_online ? `遥测 ${ageText(device.telemetry_age_s)}` : '等待遥测数据'}</small><button class="connect-button${isCurrent ? ' current' : ''}" data-connect-rover="${escapeHtml(device.rover_id)}" ${!device.online || isCurrent ? 'disabled' : ''}>${buttonText}</button></div>
    </article>`;
  }).join('');
}

function renderConnection() {
  const rover = selectedRover();
  const device = selectedDevice();
  const element = $('#connectionState');
  $('#activeRoverName').textContent = device?.name || selectedRoverId || '尚未连接';
  if (!streamConnected) {
    element.className = 'connection waiting';
    element.innerHTML = '<i></i><span>平台连接中断</span>';
  } else if (!device || state.connected_rover_id !== selectedRoverId) {
    element.className = 'connection warning';
    element.innerHTML = '<i></i><span>等待选择小车</span>';
  } else if (!device.online) {
    element.className = 'connection warning';
    element.innerHTML = '<i></i><span>小车连接已中断</span>';
  } else if (!rover?.online) {
    element.className = 'connection warning';
    element.innerHTML = '<i></i><span>已连接，等待遥测</span>';
  } else {
    element.className = 'connection live';
    element.innerHTML = '<i></i><span>小车实时在线</span>';
  }
}

function renderOverview() {
  const rover = selectedRover();
  const resources = (state.known_resources || []).filter((row) => !rover || row.discovered_by === rover.rover_id);
  const mode = missionMode(rover);
  $('#modeValue').textContent = rover ? (modeLabels[mode] || mode) : '等待数据';
  $('#missionValue').textContent = rover?.mission || (rover ? '等待任务' : '尚未连接小车');
  $('#speedValue').textContent = rover ? num(rover.linear_mps, 2, ' m/s') : '—';
  $('#turnValue').textContent = `角速度 ${rover ? num(rover.angular_rps, 2, ' rad/s') : '—'}`;
  $('#batteryValue').textContent = rover ? num(rover.battery_pct, 0, '%') : '—';
  const overall = String(rover?.health?.overall || 'UNKNOWN').toUpperCase();
  $('#healthValue').textContent = overall === 'OK' ? '设备工作正常' : overall === 'FAULT' ? '设备存在异常' : '设备状态未知';
  const target = currentTarget(rover);
  $('#targetValue').textContent = target || '未指定';
  $('#targetCountValue').textContent = `已发现 ${resources.length} 个目标`;
  $('#poseValue').textContent = rover ? `(${num(rover.pose?.x)}, ${num(rover.pose?.y)})` : '—';
  $('#headingValue').textContent = `航向 ${rover ? num(rover.pose?.yaw_deg, 1, '°') : '—'}`;
  $('#distanceValue').textContent = rover ? num(rover.distance_m, 1, ' m') : '—';
  $('#ageValue').textContent = `数据龄 ${rover ? ageText(rover.age_s) : '—'}`;
  $('#commandQueue').textContent = `${Number(state.pending_command_count || 0)} 条待发`;
  $('#serverClock').textContent = new Date().toLocaleTimeString('zh-CN', {hour12: false});
  const modeText = rover ? (modeLabels[mode] || mode) : '等待连接小车';
  $('#overviewMissionTitle').textContent = modeText;
  $('#overviewMissionText').textContent = rover?.mission || (rover ? '小车在线，尚未开始任务。' : '连接后可查看自主探索、路径规划和避障状态。');
  $('#statusRingValue').textContent = rover ? (rover.online ? '在线' : '超时') : '—';
  $('#overviewMissionBadge').textContent = rover ? (modeLabels[mode] || mode) : '空闲';
  $('#overviewMissionBadge').className = `status-pill${!rover || !rover.online ? ' muted' : mode === 'ERROR' ? ' fault' : ''}`;
}

function renderMission() {
  const rover = selectedRover();
  const mode = missionMode(rover);
  const stages = ['SEARCH', 'EXPLORE', 'TARGET_FOUND', 'NAVIGATE', 'DONE'];
  const activeIndex = stages.indexOf(mode);
  document.querySelectorAll('#missionSteps [data-stage]').forEach((element, index) => {
    element.classList.toggle('active', index === activeIndex);
    element.classList.toggle('done', activeIndex >= 0 && index < activeIndex || mode === 'DONE');
  });
  document.querySelectorAll('#missionSteps > b').forEach((element, index) => {
    element.classList.toggle('done', activeIndex > index || mode === 'DONE');
  });
  const badge = $('#missionBadge');
  badge.textContent = rover ? (modeLabels[mode] || mode) : '离线';
  badge.className = `status-pill${mode === 'ERROR' ? ' fault' : (!rover || !rover.online ? ' muted' : '')}`;
  const exploration = rover?.health?.exploration;
  $('#explorationDetail').textContent = exploration && exploration !== 'IDLE'
    ? `探索器：${exploration}${currentTarget(rover) ? ` · 当前目标：${currentTarget(rover)}` : ''}`
    : (rover?.mission || '等待平台下发搜索任务');
}

function renderHealth() {
  const health = selectedRover()?.health || {};
  const sensors = [['lidar', '激光雷达'], ['camera', '彩色相机'], ['depth', '深度数据'], ['imu', 'IMU'], ['localization', '定位'], ['cpu_pct', 'CPU']];
  $('#healthGrid').innerHTML = sensors.map(([key, label]) => {
    if (key === 'cpu_pct') return `<div class="health-item"><span>${label}</span><strong class="${health[key] == null ? 'unknown' : ''}">${health[key] == null ? '未知' : `${num(health[key], 0, '%')}`}</strong></div>`;
    const value = String(health[key] || 'UNKNOWN').toUpperCase();
    const className = value === 'OK' ? '' : value === 'FAULT' ? 'fault' : 'unknown';
    const text = value === 'OK' ? '正常' : value === 'FAULT' ? '异常' : '未知';
    return `<div class="health-item"><span>${label}</span><strong class="${className}">${text}</strong></div>`;
  }).join('');
  const overall = String(health.overall || 'UNKNOWN').toUpperCase();
  const badge = $('#overallHealth');
  badge.textContent = overall === 'OK' ? '全部正常' : overall === 'FAULT' ? '存在异常' : '状态未知';
  badge.className = `status-pill${overall === 'FAULT' ? ' fault' : overall === 'OK' ? '' : ' muted'}`;
  const safety = normalizeSafety(health.safety);
  $('#safetyValue').textContent = safety;
  $('#safetyValue').style.color = /STOP|FAULT|COLLISION|EMERGENCY|TIMEOUT/i.test(safety) ? 'var(--red)' : 'var(--green)';
}

function renderDiagnostics() {
  const device = selectedDevice();
  $('#detailRoverName').textContent = device?.name || device?.rover_id || '—';
  $('#detailRoverIp').textContent = device?.ip_address || '—';
  $('#detailRosDomain').textContent = device?.ros_domain_id ?? '—';
  $('#detailSource').textContent = device?.source === 'lan-broadcast' ? '局域网自动发现' : device?.source || '—';
  $('#detailLatency').textContent = device?.telemetry_age_s == null ? '—' : ageText(device.telemetry_age_s);
  $('#disconnectRover').disabled = !selectedRoverId || state.connected_rover_id !== selectedRoverId;
}

function renderTargets() {
  const rover = selectedRover();
  const rows = (state.known_resources || []).filter((row) => !rover || row.discovered_by === rover.rover_id);
  $('#targetBadge').textContent = rows.length;
  $('#targetList').innerHTML = rows.length ? rows.slice().reverse().map((row) => `
    <div class="target-row">
      <div class="target-row-head"><strong>${escapeHtml(row.resource_type || '未知目标')}</strong><span class="confidence">${row.confidence == null ? '—' : `${Math.round(Number(row.confidence) * 100)}%`}</span></div>
      <div class="row-meta"><span>位置 (${num(row.x)}, ${num(row.y)})</span><span>${escapeHtml(row.status || 'DISCOVERED')}</span></div>
    </div>`).join('') : '<div class="list-empty">尚未发现目标</div>';
}

function eventDetail(event) {
  const payload = event.payload || {};
  const parts = [];
  if (payload.action) parts.push(String(payload.action));
  if (payload.target_class) parts.push(`目标 ${payload.target_class}`);
  if (payload.reason) parts.push(String(payload.reason));
  if (payload.status) parts.push(String(payload.status));
  return parts.join(' · ') || event.rover_id || '系统事件';
}
function renderEvents() {
  const rover = selectedRover();
  const rows = (state.events || []).filter((event) => !rover || !event.rover_id || event.rover_id === rover.rover_id).slice(0, 12);
  $('#eventList').innerHTML = rows.length ? rows.map((event) => `
    <div class="event-row"><div class="event-row-head"><strong>${escapeHtml(eventLabels[event.event_type] || event.event_type)}</strong><time>${timeText(event.timestamp)}</time></div><p>${escapeHtml(eventDetail(event))}</p></div>`).join('') : '<div class="list-empty">暂无任务动态</div>';
}

function renderCamera() {
  const roverId = selectedId();
  const feed = roverId ? state.camera_feeds?.[roverId] : null;
  const stage = document.querySelector('.camera-stage');
  const image = $('#cameraFrame');
  const badge = $('#cameraState');
  if (!feed) {
    cameraSignature = '';
    stage.classList.remove('has-frame');
    image.removeAttribute('src');
    badge.textContent = '等待画面';
    badge.className = 'status-pill muted';
    $('#cameraMeta').textContent = '等待 /perception/debug_image';
    return;
  }
  const signature = `${roverId}:${feed.received_at}`;
  if (signature !== cameraSignature) {
    image.src = `/api/camera/${encodeURIComponent(roverId)}?t=${encodeURIComponent(feed.received_at)}`;
    cameraSignature = signature;
  }
  const fresh = Number(feed.age_s) <= 3;
  stage.classList.add('has-frame');
  badge.textContent = fresh ? '实时' : '画面超时';
  badge.className = `status-pill${fresh ? '' : ' fault'}`;
  $('#cameraMeta').textContent = `${roverId} · 更新于 ${ageText(feed.age_s)} 前`;
}

async function loadMap() {
  const roverId = selectedId();
  const metadata = roverId ? state.maps?.[roverId] : null;
  if (!metadata) {
    drawMap();
    return;
  }
  const cached = mapCache.get(roverId);
  if (cached?.received_at === metadata.received_at) {
    drawMap();
    return;
  }
  try {
    const response = await fetch(`/api/map/${encodeURIComponent(roverId)}`, {cache: 'no-store'});
    if (!response.ok) throw new Error('map unavailable');
    mapCache.set(roverId, await response.json());
  } catch (_) {
    mapCache.delete(roverId);
  }
  drawMap();
}
function decodeMap(row) {
  if (row?._data) return row._data;
  if (!row || !Number.isInteger(row.width) || !Number.isInteger(row.height)) return null;
  const data = new Int16Array(row.width * row.height);
  let index = 0;
  for (const run of row.runs || []) {
    data.fill(Number(run[0]), index, index + Number(run[1]));
    index += Number(run[1]);
  }
  if (index !== data.length) return null;
  row._data = data;
  return data;
}
function mapBitmap(row) {
  if (row._bitmap) return row._bitmap;
  const data = decodeMap(row);
  if (!data) return null;
  const canvas = document.createElement('canvas');
  canvas.width = row.width;
  canvas.height = row.height;
  const context = canvas.getContext('2d');
  const image = context.createImageData(row.width, row.height);
  for (let y = 0; y < row.height; y += 1) {
    for (let x = 0; x < row.width; x += 1) {
      const value = data[y * row.width + x];
      const offset = ((row.height - 1 - y) * row.width + x) * 4;
      const shade = value < 0 ? 191 : value === 0 ? 242 : Math.max(35, 220 - Math.round(value * 1.85));
      image.data[offset] = shade;
      image.data[offset + 1] = value < 0 ? shade + 5 : shade;
      image.data[offset + 2] = value < 0 ? shade + 8 : shade;
      image.data[offset + 3] = 255;
    }
  }
  context.putImageData(image, 0, 0);
  row._bitmap = canvas;
  return canvas;
}
function obstacleBlocks(row) {
  if (!row) return [];
  if (row._obstacleBlocks) return row._obstacleBlocks;
  const data = decodeMap(row);
  if (!data) return [];
  const stride = Math.max(1, Math.ceil(Math.sqrt((row.width * row.height) / 4200)));
  const blocks = [];
  for (let y = 0; y < row.height; y += stride) {
    for (let x = 0; x < row.width; x += stride) {
      let occupied = 0;
      const endY = Math.min(row.height, y + stride);
      const endX = Math.min(row.width, x + stride);
      for (let sampleY = y; sampleY < endY && occupied < 65; sampleY += 1) {
        for (let sampleX = x; sampleX < endX; sampleX += 1) {
          occupied = Math.max(occupied, data[sampleY * row.width + sampleX]);
        }
      }
      if (occupied >= 65) {
        blocks.push({
          x: Number(row.origin.x) + x * row.resolution,
          y: Number(row.origin.y) + y * row.resolution,
          size: stride * row.resolution,
          height: .16 + .30 * Math.min(1, occupied / 100)
        });
      }
    }
  }
  blocks.sort((a, b) => (a.x + a.y) - (b.x + b.y));
  row._obstacleBlocks = blocks;
  return blocks;
}
function fillPolygon(context, points, fill, stroke = null) {
  context.beginPath();
  points.forEach((point, index) => index ? context.lineTo(point.x, point.y) : context.moveTo(point.x, point.y));
  context.closePath();
  context.fillStyle = fill;
  context.fill();
  if (stroke) { context.strokeStyle = stroke; context.lineWidth = 1; context.stroke(); }
}
function drawMap3d(context, width, height, scene) {
  const {grid, track, path, targets, rover, minX, maxX, minY, maxY} = scene;
  const spanX = Math.max(2, maxX - minX);
  const spanY = Math.max(2, maxY - minY);
  const centerX = (minX + maxX) / 2;
  const centerY = (minY + maxY) / 2;
  const scale = Math.min(width / Math.max(3, (spanX + spanY) * .9), height / Math.max(3, (spanX + spanY) * .52 + 1.6));
  const project = (x, y, z = 0) => ({
    x: width * .5 + ((Number(x) - centerX) - (Number(y) - centerY)) * scale * .72,
    y: height * .68 + ((Number(x) - centerX) + (Number(y) - centerY)) * scale * .34 - Number(z) * scale
  });
  const gradient = context.createLinearGradient(0, 0, 0, height);
  gradient.addColorStop(0, '#f6f7f3'); gradient.addColorStop(1, '#dfe7e1');
  context.fillStyle = gradient; context.fillRect(0, 0, width, height);
  const corners = [project(minX, minY), project(maxX, minY), project(maxX, maxY), project(minX, maxY)];
  fillPolygon(context, corners, '#e9eee9', '#a8b8ad');
  context.strokeStyle = 'rgba(80,105,88,.16)'; context.lineWidth = 1;
  const divisions = 10;
  for (let index = 1; index < divisions; index += 1) {
    const x = minX + spanX * index / divisions;
    const y = minY + spanY * index / divisions;
    let a = project(x, minY), b = project(x, maxY); context.beginPath(); context.moveTo(a.x, a.y); context.lineTo(b.x, b.y); context.stroke();
    a = project(minX, y); b = project(maxX, y); context.beginPath(); context.moveTo(a.x, a.y); context.lineTo(b.x, b.y); context.stroke();
  }
  obstacleBlocks(grid).forEach((block) => {
    const {x, y, size, height: blockHeight} = block;
    const base = [project(x, y), project(x + size, y), project(x + size, y + size), project(x, y + size)];
    const top = [project(x, y, blockHeight), project(x + size, y, blockHeight), project(x + size, y + size, blockHeight), project(x, y + size, blockHeight)];
    fillPolygon(context, [base[1], base[2], top[2], top[1]], '#7d8a81');
    fillPolygon(context, [base[2], base[3], top[3], top[2]], '#69766e');
    fillPolygon(context, top, '#a7b1aa', 'rgba(74,94,80,.25)');
  });
  const drawRaisedLine = (points, color, dash, z, lineWidth) => {
    if (points.length < 2) return;
    context.save(); context.strokeStyle = color; context.lineWidth = lineWidth; context.setLineDash(dash); context.lineJoin = 'round'; context.beginPath();
    points.forEach((point, index) => { const projected = project(point.x, point.y, z); index ? context.lineTo(projected.x, projected.y) : context.moveTo(projected.x, projected.y); });
    context.stroke(); context.restore();
  };
  drawRaisedLine(path, '#3f7e9d', [7, 5], .10, 2.5);
  drawRaisedLine(track, '#2fa274', [], .07, 2.2);
  targets.forEach((target) => {
    const base = project(target.x, target.y, 0), top = project(target.x, target.y, .45);
    context.strokeStyle = '#d98038'; context.lineWidth = 2; context.beginPath(); context.moveTo(base.x, base.y); context.lineTo(top.x, top.y); context.stroke();
    fillPolygon(context, [{x: top.x, y: top.y - 6}, {x: top.x + 6, y: top.y}, {x: top.x, y: top.y + 6}, {x: top.x - 6, y: top.y}], '#d98038');
    context.fillStyle = '#27352c'; context.font = '600 10px system-ui'; context.fillText(String(target.resource_type || '目标'), top.x + 9, top.y + 3);
  });
  if (rover?.pose) {
    const point = project(rover.pose.x, rover.pose.y, .16);
    context.save(); context.translate(point.x, point.y); context.rotate(-Number(rover.pose.yaw_deg || 0) * Math.PI / 180 - Math.PI / 4); context.fillStyle = '#2f6d55';
    context.beginPath(); context.moveTo(10, 0); context.lineTo(-7, -6); context.lineTo(-4, 0); context.lineTo(-7, 6); context.closePath(); context.fill(); context.restore();
    context.fillStyle = '#27352c'; context.font = '600 10px system-ui'; context.fillText(rover.rover_id, point.x + 10, point.y - 9);
  }
  $('#mapStatus').textContent = grid ? `立体占用图 · ${grid.width} × ${grid.height} · ${num(grid.resolution, 3, ' m/格')}` : '立体轨迹视图 · 等待 /map';
  $('#mapUpdated').textContent = grid ? `2D SLAM 立体化 · ${ageText(Date.now() / 1000 - Number(grid.received_at))} 前更新` : '数据源为 2D SLAM';
}
function drawMap() {
  const canvas = $('#mapCanvas');
  const bounds = canvas.parentElement.getBoundingClientRect();
  if (!bounds.width || !bounds.height) return;
  const dpr = window.devicePixelRatio || 1;
  canvas.width = Math.round(bounds.width * dpr);
  canvas.height = Math.round(bounds.height * dpr);
  const context = canvas.getContext('2d');
  context.scale(dpr, dpr);
  const width = bounds.width;
  const height = bounds.height;
  context.clearRect(0, 0, width, height);
  context.fillStyle = '#cfd5d8';
  context.fillRect(0, 0, width, height);

  const rover = selectedRover();
  const roverId = rover?.rover_id || '';
  const grid = roverId ? mapCache.get(roverId) : null;
  const track = roverId ? (state.trajectories?.[roverId] || []) : [];
  const path = roverId ? (state.paths?.[roverId]?.points || []) : [];
  const targets = (state.known_resources || []).filter((row) => !roverId || row.discovered_by === roverId);
  const points = [...track, ...path, ...targets];
  if (rover?.pose) points.push(rover.pose);

  let minX, maxX, minY, maxY;
  if (grid) {
    minX = Number(grid.origin.x); minY = Number(grid.origin.y);
    maxX = minX + Number(grid.width) * Number(grid.resolution);
    maxY = minY + Number(grid.height) * Number(grid.resolution);
  } else if (points.length) {
    minX = Math.min(...points.map((point) => Number(point.x)));
    maxX = Math.max(...points.map((point) => Number(point.x)));
    minY = Math.min(...points.map((point) => Number(point.y)));
    maxY = Math.max(...points.map((point) => Number(point.y)));
  } else {
    $('#mapEmpty').style.display = 'flex';
    $('#mapStatus').textContent = '等待 /map';
    $('#mapUpdated').textContent = '尚未更新';
    return;
  }
  $('#mapEmpty').style.display = 'none';
  let spanX = Math.max(2, maxX - minX);
  let spanY = Math.max(2, maxY - minY);
  minX -= spanX * .06; maxX += spanX * .06; minY -= spanY * .06; maxY += spanY * .06;
  spanX = maxX - minX; spanY = maxY - minY;
  const scale = Math.min((width - 42) / spanX, (height - 42) / spanY);
  const offsetX = (width - spanX * scale) / 2;
  const offsetY = (height - spanY * scale) / 2;
  const px = (value) => offsetX + (Number(value) - minX) * scale;
  const py = (value) => height - offsetY - (Number(value) - minY) * scale;

  if (mapMode === '3d') {
    drawMap3d(context, width, height, {grid, track, path, targets, rover, minX, maxX, minY, maxY});
    return;
  }

  if (grid) {
    const bitmap = mapBitmap(grid);
    if (bitmap) {
      context.imageSmoothingEnabled = false;
      context.drawImage(bitmap, px(grid.origin.x), py(Number(grid.origin.y) + grid.height * grid.resolution), grid.width * grid.resolution * scale, grid.height * grid.resolution * scale);
    }
    $('#mapStatus').textContent = `${grid.width} × ${grid.height} · ${num(grid.resolution, 3, ' m/格')}`;
    $('#mapUpdated').textContent = `地图更新于 ${ageText(Date.now() / 1000 - Number(grid.received_at))} 前`;
  } else {
    context.strokeStyle = 'rgba(57,76,86,.12)';
    context.lineWidth = 1;
    for (let x = 0; x < width; x += 35) { context.beginPath(); context.moveTo(x, 0); context.lineTo(x, height); context.stroke(); }
    for (let y = 0; y < height; y += 35) { context.beginPath(); context.moveTo(0, y); context.lineTo(width, y); context.stroke(); }
    $('#mapStatus').textContent = '等待 /map，暂时显示里程计轨迹';
    $('#mapUpdated').textContent = '';
  }
  if (track.length > 1) {
    context.strokeStyle = '#28a978'; context.lineWidth = 2.4; context.lineJoin = 'round'; context.beginPath();
    track.forEach((point, index) => index ? context.lineTo(px(point.x), py(point.y)) : context.moveTo(px(point.x), py(point.y)));
    context.stroke();
  }
  if (path.length > 1) {
    context.save(); context.strokeStyle = '#00a9cf'; context.lineWidth = 3; context.setLineDash([8, 6]); context.beginPath();
    path.forEach((point, index) => index ? context.lineTo(px(point.x), py(point.y)) : context.moveTo(px(point.x), py(point.y)));
    context.stroke(); context.restore();
  }
  context.font = '600 10px system-ui';
  targets.forEach((target) => {
    const x = px(target.x), y = py(target.y);
    context.save(); context.translate(x, y); context.rotate(Math.PI / 4); context.fillStyle = '#d99b2b'; context.fillRect(-6, -6, 12, 12); context.restore();
    context.fillStyle = '#2b3b45'; context.fillText(String(target.resource_type || '目标'), x + 10, y + 4);
  });
  if (rover?.pose) {
    const x = px(rover.pose.x), y = py(rover.pose.y), angle = -Number(rover.pose.yaw_deg || 0) * Math.PI / 180;
    context.save(); context.translate(x, y); context.rotate(angle); context.fillStyle = rover.online ? '#183f52' : '#788690'; context.strokeStyle = '#40d3e3'; context.lineWidth = 2;
    context.beginPath(); context.moveTo(13, 0); context.lineTo(-8, -8); context.lineTo(-5, 0); context.lineTo(-8, 8); context.closePath(); context.fill(); context.stroke(); context.restore();
    context.fillStyle = '#203640'; context.fillText(rover.rover_id, x + 12, y - 11);
  }
}

function updateControls() {
  const rover = selectedRover();
  const disabled = !rover || !rover.online || state.connected_rover_id !== rover.rover_id;
  ['startSearch', 'pauseCommand', 'resumeCommand', 'returnCommand', 'estopCommand'].forEach((id) => { $(`#${id}`).disabled = disabled; });
}
async function sendCommand(action, payload = {}, label = action) {
  const rover = selectedRover();
  if (!rover) return toast('小车尚未上线');
  try {
    $('#commandFeedback').textContent = `正在发送：${label}…`;
    await post('/api/operator', {rover_id: rover.rover_id, action, payload});
    $('#commandFeedback').textContent = `${label}已进入队列，等待小车确认。`;
    toast(`${label}已发送`);
    await fetchSnapshot();
  } catch (error) {
    $('#commandFeedback').textContent = `发送失败：${error.message}`;
    toast(error.message);
  }
}

async function connectRover(roverId) {
  try {
    await post('/api/connection', {action: 'connect', rover_id: roverId});
    selectedRoverId = roverId;
    localStorage.setItem('selectedRoverId', roverId);
    cameraSignature = '';
    toast(`已连接 ${roverId}`);
    await fetchSnapshot();
    navigate('overview');
  } catch (error) {
    toast(`连接失败：${error.message}`);
  }
}

async function disconnectRover() {
  if (!selectedRoverId) return;
  try {
    await post('/api/connection', {action: 'disconnect', rover_id: selectedRoverId});
    selectedRoverId = '';
    localStorage.removeItem('selectedRoverId');
    cameraSignature = '';
    await fetchSnapshot();
    navigate('devices');
    toast('已断开小车');
  } catch (error) {
    toast(`断开失败：${error.message}`);
  }
}

document.querySelectorAll('[data-page], [data-page-link]').forEach((element) => {
  element.addEventListener('click', () => navigate(element.dataset.page || element.dataset.pageLink));
});
$('#deviceList').addEventListener('click', (event) => {
  const button = event.target.closest('[data-connect-rover]');
  if (button && !button.disabled) connectRover(button.dataset.connectRover);
});
$('#refreshDevices').addEventListener('click', async () => {
  $('#refreshDevices').disabled = true;
  $('#refreshDevices').querySelector('.refresh-icon').style.display = 'inline-block';
  await fetchSnapshot();
  setTimeout(() => { $('#refreshDevices').disabled = false; }, 500);
  toast('已刷新局域网设备');
});
$('#disconnectRover').addEventListener('click', disconnectRover);
$('#menuButton').addEventListener('click', () => { $('#sidebar').classList.add('open'); $('#sidebarBackdrop').classList.add('show'); });
$('#sidebarBackdrop').addEventListener('click', () => { $('#sidebar').classList.remove('open'); $('#sidebarBackdrop').classList.remove('show'); });
$('#map2dButton').addEventListener('click', () => { mapMode = '2d'; localStorage.setItem('mapMode', mapMode); renderMapMode(); drawMap(); });
$('#map3dButton').addEventListener('click', () => { mapMode = '3d'; localStorage.setItem('mapMode', mapMode); renderMapMode(); drawMap(); });
$('#startSearch').addEventListener('click', () => {
  const targetClass = $('#targetClass').value.trim().toLowerCase();
  if (!targetClass) return toast('请输入要寻找的目标');
  sendCommand('SEARCH_TARGET', {target_class: targetClass}, `寻找 ${targetClass}`);
});
$('#targetClass').addEventListener('keydown', (event) => { if (event.key === 'Enter') $('#startSearch').click(); });
$('#pauseCommand').addEventListener('click', () => sendCommand('HOLD', {}, '暂停任务'));
$('#resumeCommand').addEventListener('click', () => sendCommand('RESUME', {}, '继续任务'));
$('#returnCommand').addEventListener('click', () => sendCommand('RETURN', {}, '返航'));
$('#estopCommand').addEventListener('click', () => {
  if (window.confirm('确认立即停止小车？')) sendCommand('EMERGENCY_STOP', {}, '紧急停车');
});
$('#cameraFrame').addEventListener('error', () => document.querySelector('.camera-stage').classList.remove('has-frame'));
window.addEventListener('resize', drawMap);
setInterval(() => { $('#serverClock').textContent = new Date().toLocaleTimeString('zh-CN', {hour12: false}); }, 1000);

async function fetchSnapshot() {
  try {
    const response = await fetch('/api/status', {cache: 'no-store'});
    if (!response.ok) throw new Error('status unavailable');
    state = await response.json();
    lastRevision = Number(state.revision ?? lastRevision);
    render();
  } catch (_) {
    streamConnected = false;
    renderConnection();
  }
}
function connectStream() {
  clearTimeout(reconnectTimer);
  const stream = new EventSource(`/api/stream?after=${encodeURIComponent(lastRevision)}`);
  stream.onopen = () => { streamConnected = true; renderConnection(); };
  stream.addEventListener('snapshot', (event) => {
    try {
      state = JSON.parse(event.data);
      lastRevision = Number(state.revision ?? lastRevision);
      streamConnected = true;
      render();
    } catch (_) { /* wait for the next complete event */ }
  });
  stream.onerror = () => {
    streamConnected = false;
    renderConnection();
    stream.close();
    reconnectTimer = setTimeout(connectStream, 1800);
  };
}

fetchSnapshot().finally(connectStream);
