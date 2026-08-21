const WS_URL = 'ws://127.0.0.1:8000/ws';

const state = {
  latest: null,
  previous: null,
  interpolationStart: 0,
  ws: null,
  selectedDroneId: null,
  selectedSurvivorId: null,
  connectionStatus: 'CONNECTING',
  lastMessageTime: null,
};

const canvas = document.getElementById('mapCanvas');
const ctx = canvas.getContext('2d');

const elements = {
  connectionStatus: document.getElementById('connectionStatus'),
  simTime: document.getElementById('simTime'),
  activeDrones: document.getElementById('activeDrones'),
  mapBounds: document.getElementById('mapBounds'),
  lastUpdate: document.getElementById('lastUpdate'),
  droneList: document.getElementById('droneList'),
  detectionList: document.getElementById('detectionList'),
  taskList: document.getElementById('taskList'),
  searchCoverage: document.getElementById('searchCoverage'),
  confirmedSurvivors: document.getElementById('confirmedSurvivors'),
  openTasks: document.getElementById('openTasks'),
  wsStatus: document.getElementById('wsStatus'),
  healthStatus: document.getElementById('healthStatus'),
  engineMode: document.getElementById('engineMode'),
  restartSearch: document.getElementById('restartSearch'),
  detailPanel: document.getElementById('detailPanel'),
  detailTitle: document.getElementById('detailTitle'),
  detailBody: document.getElementById('detailBody'),
  closeDetail: document.getElementById('closeDetail'),
};

function clamp(value, min, max) {
  return Math.min(Math.max(value, min), max);
}

function lerp(a, b, t) {
  return a + (b - a) * t;
}

function formatTime(date) {
  const d = date instanceof Date ? date : new Date();
  return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false });
}

function toRadians(deg) {
  return (deg * Math.PI) / 180;
}

function isFiniteNumber(value) {
  return typeof value === 'number' && Number.isFinite(value);
}

function normalizeState(raw) {
  if (!raw || typeof raw !== 'object') {
    return {
      agents: [],
      map: { width: 100, height: 100, grid_width: 32, grid_height: 32, buildings: [], obstacles: [], terrain: [], known: [], prob: [] },
      open_tasks: [],
      detections: [],
      confirmed_survivors: [],
      survivors: [],
      trust_scores: {},
      coverage: null,
      mission_complete: false,
      bridge: null,
    };
  }

  const map = raw.map || {};
  const trustScores = raw.trust_scores && typeof raw.trust_scores === 'object' ? raw.trust_scores : {};
  const agents = Array.isArray(raw.agents) ? raw.agents.map((agent) => ({
    id: Number(agent?.id ?? 0),
    x: Number(agent?.x ?? 0),
    y: Number(agent?.y ?? 0),
    state: String(agent?.state ?? 'UNKNOWN'),
    battery: Number(agent?.battery ?? 0),
    heading: isFiniteNumber(agent?.heading) ? Number(agent.heading) : 0,
    trust: isFiniteNumber(agent?.trust) ? Number(agent.trust) : Number(trustScores[String(agent?.id)] ?? 1),
    quarantined: Boolean(agent?.quarantined),
    connected: agent?.connected !== false,
  })) : [];

  const openTasks = Array.isArray(raw.open_tasks) ? raw.open_tasks : [];
  const detections = Array.isArray(raw.detections) ? raw.detections : [];
  const confirmedSurvivors = Array.isArray(raw.confirmed_survivors) ? raw.confirmed_survivors : [];
  const survivors = Array.isArray(raw.survivors) ? raw.survivors : confirmedSurvivors.map((survivor, index) => ({
    ...survivor,
    id: survivor.id ?? index,
    status: survivor.status || 'confirmed',
  }));

  return {
    agents,
    map: {
      width: Number(map.width ?? 100),
      height: Number(map.height ?? 100),
      grid_width: Number(map.grid_width ?? 32),
      grid_height: Number(map.grid_height ?? 32),
      buildings: Array.isArray(map.buildings) ? map.buildings : [],
      obstacles: Array.isArray(map.obstacles) ? map.obstacles : [],
      terrain: Array.isArray(map.terrain) ? map.terrain : [],
      known: Array.isArray(map.known) ? map.known : [],
      prob: Array.isArray(map.prob) ? map.prob : [],
    },
    open_tasks: openTasks,
    detections,
    confirmed_survivors: confirmedSurvivors,
    survivors,
    trust_scores: trustScores,
    coverage: isFiniteNumber(raw.coverage) ? Number(raw.coverage) : null,
    mission_complete: Boolean(raw.mission_complete),
    bridge: raw.bridge && typeof raw.bridge === 'object' ? raw.bridge : null,
  };
}

function setConnectionBadge(text, tone) {
  elements.connectionStatus.className = `status-pill ${tone}`;
  elements.connectionStatus.innerHTML = `<span class="dot"></span><span>${text}</span>`;
}

function updateStatusPanels() {
  const currentState = state.latest || normalizeState(null);
  const activeAgents = currentState.agents.filter((agent) => agent && Number.isFinite(agent.id));
  const droneCount = activeAgents.length;
  elements.activeDrones.textContent = `${droneCount}/${droneCount || 0}`;

  elements.mapBounds.textContent = `${Math.round(currentState.map.width)} x ${Math.round(currentState.map.height)}`;
  elements.confirmedSurvivors.textContent = String(currentState.confirmed_survivors.length || 0);
  elements.openTasks.textContent = String(currentState.open_tasks.length || 0);

  const coverage = isFiniteNumber(currentState.coverage)
    ? Math.round(currentState.coverage * 100)
    : computeSearchCoverage(currentState);
  elements.searchCoverage.textContent = `${coverage}%`;

  if (state.lastMessageTime) {
    elements.lastUpdate.textContent = formatTime(new Date(state.lastMessageTime));
  }

  elements.wsStatus.textContent = state.connectionStatus;
  elements.healthStatus.textContent = currentState.mission_complete ? 'MISSION COMPLETE' : state.latest ? 'OK' : 'N/A';

  // MODE reflects the real backend engine, not a hardcoded label. The hardware
  // bridge tags its payload with `bridge.source`; a bridge that has gone stale
  // (ESPs unplugged / feeder stopped) reads OFFLINE instead of pretending.
  if (elements.engineMode) {
    const bridge = currentState.bridge;
    if (bridge && typeof bridge === 'object') {
      elements.engineMode.textContent = bridge.online === false
        ? 'HARDWARE (no bridge)'
        : 'HARDWARE - 5x ESP32';
    } else {
      elements.engineMode.textContent = 'SIMULATION';
    }
  }
}

function computeSearchCoverage(simState) {
  const detections = Array.isArray(simState.detections) ? simState.detections : [];
  const agents = Array.isArray(simState.agents) ? simState.agents : [];
  const coveragePoints = [...agents.map((a) => ({ x: a.x, y: a.y, weight: 0.7 })), ...detections.map((d) => ({ x: d.x, y: d.y, weight: Number(d.confidence ?? 0.4) }))];

  if (!coveragePoints.length) return 0;

  let totalWeight = 0;
  for (const point of coveragePoints) {
    totalWeight += point.weight;
  }

  const normalized = (totalWeight / (coveragePoints.length * 1.4)) * 100;
  return Math.min(100, Math.round(normalized));
}

function getAgentStateClass(stateValue) {
  const normalized = String(stateValue || '').toUpperCase();
  if (normalized.includes('SEARCH')) return 'drone-searching';
  if (normalized.includes('REOBS')) return 'drone-reobserving';
  if (normalized.includes('RETURN')) return 'drone-returning';
  if (normalized.includes('IDLE')) return 'drone-idle';
  if (normalized.includes('BIDDING')) return 'drone-bidding';
  if (normalized.includes('SOLO')) return 'drone-solo';
  if (normalized.includes('QUARANTINED')) return 'drone-quarantined';
  if (normalized.includes('DEAD')) return 'drone-dead';
  return 'drone-lost';
}

function renderDroneList() {
  const agents = (state.latest && state.latest.agents) || [];

  if (!agents.length) {
    elements.droneList.innerHTML = '<div class="small-item"><strong>No drones</strong></div>';
    return;
  }

  const ordered = [...agents].sort((a, b) => Number(a.id) - Number(b.id));

  elements.droneList.innerHTML = ordered
    .map((agent) => {
      const selectedClass = state.selectedDroneId === agent.id ? 'selected' : '';
      const stateClass = getAgentStateClass(agent.state);
      return `
        <div class="drone-row ${selectedClass}" data-drone-id="${agent.id}" title="Drone ${agent.id}">
          <div class="drone-id">D${String(agent.id).padStart(2, '0')}</div>
          <div class="drone-meta">
            <div class="drone-main">
              <span class="drone-state ${stateClass}">${agent.state || 'UNKNOWN'}</span>
              <span class="drone-battery">${Math.round(agent.battery ?? 0)}%</span>
            </div>
            <div class="drone-battery">${Number(agent.x ?? 0).toFixed(1)}, ${Number(agent.y ?? 0).toFixed(1)} · trust ${Math.round(Number(agent.trust ?? 1) * 100)}% · ${agent.connected === false ? 'NO LINK' : 'LINK'}</div>
          </div>
          <div class="drone-battery">${Math.round((agent.battery ?? 0) / 100 * 100)}%</div>
        </div>
      `;
    })
    .join('');

  elements.droneList.querySelectorAll('.drone-row').forEach((row) => {
    row.addEventListener('click', () => {
      const id = Number(row.dataset.droneId);
      state.selectedDroneId = id;
      state.selectedSurvivorId = null;
      renderDetailPanel();
      renderDroneList();
    });
  });
}

function renderDetections() {
  const detections = (state.latest && state.latest.detections) || [];

  if (!detections.length) {
    elements.detectionList.innerHTML = '<div class="small-item"><strong>No detections</strong></div>';
    return;
  }

  const latest = [...detections].slice(-5).reverse();
  elements.detectionList.innerHTML = latest
    .map((detection) => `
      <div class="small-item">
        <strong>Drone ${Number(detection.agent_id ?? 0)}</strong><br />
        ${Number(detection.x ?? 0).toFixed(1)}, ${Number(detection.y ?? 0).toFixed(1)}<br />
        confidence ${(Number(detection.confidence ?? 0) * 100).toFixed(0)}%
      </div>
    `)
    .join('');
}

function renderTasks() {
  const tasks = (state.latest && state.latest.open_tasks) || [];

  if (!tasks.length) {
    elements.taskList.innerHTML = '<div class="small-item"><strong>No tasks</strong></div>';
    return;
  }

  elements.taskList.innerHTML = tasks
    .slice(0, 6)
    .map((task) => `
      <div class="small-item">
        <strong>${task.type || 'TASK'}</strong><br />
        ${Number(task.x ?? 0).toFixed(1)}, ${Number(task.y ?? 0).toFixed(1)}<br />
        bids: ${Object.keys(task.bids || {}).length}
      </div>
    `)
    .join('');
}

function updateCanvasSize() {
  const bounds = canvas.getBoundingClientRect();
  const ratio = window.devicePixelRatio || 1;
  canvas.width = Math.floor(bounds.width * ratio);
  canvas.height = Math.floor(bounds.height * ratio);
  ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
}

function drawBackground(simState) {
  const width = canvas.clientWidth || 600;
  const height = canvas.clientHeight || 600;

  ctx.clearRect(0, 0, width, height);

  const gradient = ctx.createLinearGradient(0, 0, width, height);
  gradient.addColorStop(0, '#152026');
  gradient.addColorStop(1, '#0f171b');
  ctx.fillStyle = gradient;
  ctx.fillRect(0, 0, width, height);

  const mapWidth = Number(simState.map.width || 100);
  const mapHeight = Number(simState.map.height || 100);
  const padding = 30;

  const scale = Math.min((width - padding * 2) / mapWidth, (height - padding * 2) / mapHeight);
  const originX = (width - mapWidth * scale) / 2;
  const originY = (height - mapHeight * scale) / 2;

  ctx.save();
  ctx.translate(originX, originY);

  ctx.fillStyle = '#17262d';
  ctx.fillRect(0, 0, mapWidth * scale, mapHeight * scale);

  ctx.strokeStyle = 'rgba(255,255,255,0.06)';
  ctx.lineWidth = 1;
  for (let x = 0; x <= mapWidth; x += 10) {
    const px = x * scale;
    ctx.beginPath();
    ctx.moveTo(px, 0);
    ctx.lineTo(px, mapHeight * scale);
    ctx.stroke();
  }
  for (let y = 0; y <= mapHeight; y += 10) {
    const py = y * scale;
    ctx.beginPath();
    ctx.moveTo(0, py);
    ctx.lineTo(mapWidth * scale, py);
    ctx.stroke();
  }

  for (const building of simState.map.buildings || []) {
    const x = Number(building.x || 0) * scale;
    const y = Number(building.y || 0) * scale;
    const w = Number(building.width || 0) * scale;
    const h = Number(building.height || 0) * scale;
    ctx.fillStyle = '#2a3b43';
    ctx.fillRect(x, y, w, h);
    ctx.strokeStyle = 'rgba(255,255,255,0.08)';
    ctx.strokeRect(x, y, w, h);
  }

  for (const obstacle of simState.map.obstacles || []) {
    const x = Number(obstacle.x || 0) * scale;
    const y = Number(obstacle.y || 0) * scale;
    const radius = Number(obstacle.radius || 0) * scale;
    ctx.beginPath();
    ctx.fillStyle = 'rgba(119, 167, 188, 0.23)';
    ctx.arc(x, y, radius, 0, Math.PI * 2);
    ctx.fill();
  }

  ctx.restore();
  return { scale, originX, originY, mapWidth, mapHeight };
}

function drawHeatmap(simState, settings) {
  const { scale, originX, originY, mapWidth, mapHeight } = settings;
  const probabilities = simState.map.prob || [];
  const gridWidth = Number(simState.map.grid_width || probabilities[0]?.length || 0);
  const gridHeight = Number(simState.map.grid_height || probabilities.length || 0);

  if (gridWidth && gridHeight && probabilities.length) {
    const cellWidth = (mapWidth * scale) / gridWidth;
    const cellHeight = (mapHeight * scale) / gridHeight;
    for (let gy = 0; gy < gridHeight; gy += 1) {
      const row = probabilities[gy] || [];
      for (let gx = 0; gx < gridWidth; gx += 1) {
        const probability = clamp(Number(row[gx] ?? 0), 0, 1);
        if (probability <= 0) continue;
        ctx.fillStyle = `rgba(239, 145, 68, ${0.04 + probability * 0.28})`;
        ctx.fillRect(originX + gx * cellWidth, originY + gy * cellHeight, cellWidth + 0.5, cellHeight + 0.5);
      }
    }
    return;
  }

  const detections = simState.detections || [];
  if (!detections.length) return;

  for (const detection of detections) {
    const x = Number(detection.x ?? 0) * scale + originX;
    const y = Number(detection.y ?? 0) * scale + originY;
    const radius = 36 + Number(detection.confidence ?? 0.5) * 48;
    const gradient = ctx.createRadialGradient(x, y, 0, x, y, radius);
    gradient.addColorStop(0, 'rgba(255, 185, 64, 0.22)');
    gradient.addColorStop(1, 'rgba(255, 185, 64, 0)');
    ctx.fillStyle = gradient;
    ctx.beginPath();
    ctx.arc(x, y, radius, 0, Math.PI * 2);
    ctx.fill();
  }
}

function drawSearchCoverage(simState, settings) {
  const { scale, originX, originY, mapWidth, mapHeight } = settings;
  const known = simState.map.known || [];
  const gridWidth = Number(simState.map.grid_width || known[0]?.length || 0);
  const gridHeight = Number(simState.map.grid_height || known.length || 0);

  if (gridWidth && gridHeight && known.length) {
    const cellWidth = (mapWidth * scale) / gridWidth;
    const cellHeight = (mapHeight * scale) / gridHeight;
    for (let gy = 0; gy < gridHeight; gy += 1) {
      const row = known[gy] || [];
      for (let gx = 0; gx < gridWidth; gx += 1) {
        if (Number(row[gx] ?? 0) <= 0.5) continue;
        ctx.fillStyle = 'rgba(93, 174, 151, 0.14)';
        ctx.fillRect(originX + gx * cellWidth, originY + gy * cellHeight, cellWidth + 0.5, cellHeight + 0.5);
      }
    }
    return;
  }

  const agents = simState.agents || [];
  const detections = simState.detections || [];
  const points = [...agents, ...detections.map((d) => ({ x: d.x, y: d.y, confidence: d.confidence }))];

  for (const point of points) {
    const x = Number(point.x ?? 0) * scale + originX;
    const y = Number(point.y ?? 0) * scale + originY;
    const radius = 18 + (Number(point.confidence ?? 0.4) * 24 || 12);
    ctx.fillStyle = 'rgba(239, 199, 107, 0.10)';
    ctx.beginPath();
    ctx.arc(x, y, radius, 0, Math.PI * 2);
    ctx.fill();
  }
}

function drawTasks(simState, settings) {
  const { scale, originX, originY } = settings;
  const tasks = simState.open_tasks || [];

  for (const task of tasks) {
    const x = Number(task.x ?? 0) * scale + originX;
    const y = Number(task.y ?? 0) * scale + originY;
    ctx.strokeStyle = task.type === 'FRONTIER' ? '#f0c47d' : '#7db7ff';
    ctx.lineWidth = 1.5;
    ctx.beginPath();
    ctx.moveTo(x - 7, y - 7);
    ctx.lineTo(x + 7, y + 7);
    ctx.moveTo(x + 7, y - 7);
    ctx.lineTo(x - 7, y + 7);
    ctx.stroke();
  }
}

function drawSurvivors(simState, settings) {
  const { scale, originX, originY } = settings;
  const survivors = simState.survivors || simState.confirmed_survivors || [];

  for (const survivor of survivors) {
    const x = Number(survivor.x ?? 0) * scale + originX;
    const y = Number(survivor.y ?? 0) * scale + originY;
    const confirmed = String(survivor.status || 'confirmed').toLowerCase() === 'confirmed';
    const color = confirmed ? '#70d39a' : '#f0c76d';

    ctx.strokeStyle = color;
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.arc(x, y, 8, 0, Math.PI * 2);
    ctx.stroke();

    ctx.beginPath();
    ctx.moveTo(x, y - 15);
    ctx.lineTo(x, y + 15);
    ctx.moveTo(x - 15, y);
    ctx.lineTo(x + 15, y);
    ctx.stroke();

    ctx.beginPath();
    ctx.arc(x, y, 3, 0, Math.PI * 2);
    ctx.fillStyle = color;
    ctx.fill();

    if (!confirmed) {
      ctx.setLineDash([3, 3]);
      ctx.beginPath();
      ctx.arc(x, y, 13, 0, Math.PI * 2);
      ctx.stroke();
      ctx.setLineDash([]);
    }
  }
}

function getInterpolatedAgent(agent) {
  if (!state.previous || !state.previous.agents) {
    return { ...agent };
  }

  const prevAgent = state.previous.agents.find((candidate) => Number(candidate.id) === Number(agent.id));
  if (!prevAgent) {
    return { ...agent };
  }

  const elapsed = performance.now() - state.interpolationStart;
  const t = clamp(elapsed / 220, 0, 1);

  return {
    ...agent,
    x: lerp(Number(prevAgent.x ?? agent.x), Number(agent.x ?? prevAgent.x), t),
    y: lerp(Number(prevAgent.y ?? agent.y), Number(agent.y ?? prevAgent.y), t),
    heading: isFiniteNumber(agent.heading) ? agent.heading : (isFiniteNumber(prevAgent.heading) ? prevAgent.heading : 0),
  };
}

function drawDrones(simState, settings) {
  const { scale, originX, originY } = settings;
  const displayedAgents = (simState.agents || []).map(getInterpolatedAgent);

  for (const agent of displayedAgents) {
    const x = Number(agent.x ?? 0) * scale + originX;
    const y = Number(agent.y ?? 0) * scale + originY;
    const heading = Number(agent.heading ?? 0);
    const color = getDroneColor(agent.state);
    const size = 12;

    ctx.save();
    ctx.translate(x, y);
    ctx.rotate(toRadians(heading));
    ctx.beginPath();
    ctx.moveTo(size, 0);
    ctx.lineTo(-size * 0.7, -size * 0.7);
    ctx.lineTo(-size * 0.7, size * 0.7);
    ctx.closePath();
    ctx.fillStyle = color;
    ctx.fill();
    ctx.lineWidth = 1.2;
    ctx.strokeStyle = 'rgba(0, 0, 0, 0.35)';
    ctx.stroke();

    if (state.selectedDroneId === agent.id) {
      ctx.beginPath();
      ctx.arc(0, 0, size + 8, 0, Math.PI * 2);
      ctx.strokeStyle = '#f2f5f7';
      ctx.lineWidth = 1.5;
      ctx.stroke();
    }

    if (agent.quarantined || String(agent.state).toUpperCase().includes('QUARANTINED')) {
      ctx.setLineDash([3, 3]);
      ctx.beginPath();
      ctx.arc(0, 0, size + 5, 0, Math.PI * 2);
      ctx.strokeStyle = '#c58cff';
      ctx.lineWidth = 1.5;
      ctx.stroke();
      ctx.setLineDash([]);
    } else if (String(agent.state).toUpperCase().includes('SOLO')) {
      ctx.beginPath();
      ctx.arc(0, 0, size + 5, 0, Math.PI * 2);
      ctx.strokeStyle = '#73cbd1';
      ctx.lineWidth = 1.2;
      ctx.stroke();
    } else if (String(agent.state).toUpperCase().includes('DEAD')) {
      ctx.strokeStyle = '#d95d5d';
      ctx.lineWidth = 2;
      ctx.beginPath();
      ctx.moveTo(-size, -size);
      ctx.lineTo(size, size);
      ctx.moveTo(size, -size);
      ctx.lineTo(-size, size);
      ctx.stroke();
    }

    ctx.restore();
  }
}

function getDroneColor(stateValue) {
  const normalized = String(stateValue || '').toUpperCase();
  if (normalized.includes('SEARCH')) return '#efc76b';
  if (normalized.includes('REOBS')) return '#7db7ff';
  if (normalized.includes('RETURN')) return '#f39b63';
  if (normalized.includes('IDLE')) return '#8d9aa5';
  if (normalized.includes('BIDDING')) return '#d6dce1';
  if (normalized.includes('SOLO')) return '#73cbd1';
  if (normalized.includes('QUARANTINED')) return '#c58cff';
  if (normalized.includes('DEAD')) return '#69737b';
  return '#d95d5d';
}

function drawSelectionHover(simState, settings) {
  if (!state.selectedDroneId && !state.selectedSurvivorId) return;

  const selectedDrone = simState.agents.find((agent) => Number(agent.id) === Number(state.selectedDroneId));
  if (selectedDrone) {
    const { scale, originX, originY } = settings;
    const x = Number(selectedDrone.x ?? 0) * scale + originX;
    const y = Number(selectedDrone.y ?? 0) * scale + originY;
    ctx.beginPath();
    ctx.arc(x, y, 18, 0, Math.PI * 2);
    ctx.strokeStyle = 'rgba(255,255,255,0.75)';
    ctx.lineWidth = 1.5;
    ctx.stroke();
  }
}

function renderDetailPanel() {
  if (!state.latest) return;

  const drone = state.latest.agents.find((agent) => Number(agent.id) === Number(state.selectedDroneId));
  if (drone) {
    elements.detailPanel.classList.remove('hidden');
    elements.detailPanel.classList.add('visible');
    elements.detailTitle.textContent = `Drone D${String(drone.id).padStart(2, '0')}`;
    elements.detailBody.innerHTML = `
      <div class="row"><span>State</span><strong>${drone.state || 'UNKNOWN'}</strong></div>
      <div class="row"><span>Position</span><strong>${Number(drone.x ?? 0).toFixed(1)}, ${Number(drone.y ?? 0).toFixed(1)}</strong></div>
      <div class="row"><span>Battery</span><strong>${Math.round(drone.battery ?? 0)}%</strong></div>
      <div class="row"><span>Trust</span><strong>${Math.round(Number(drone.trust ?? 1) * 100)}%</strong></div>
      <div class="row"><span>Connection</span><strong>${drone.connected === false ? 'NO LINK' : 'LINK'}</strong></div>
      <div class="row"><span>Heading</span><strong>${Number(drone.heading ?? 0).toFixed(0)}°</strong></div>
      <div class="row"><span>Task</span><strong>${findActiveTask(drone.id)}</strong></div>
      <div class="row"><span>Last update</span><strong>${formatTime(new Date(state.lastMessageTime || Date.now()))}</strong></div>
    `;
    return;
  }

  const selectedSurvivor = (state.latest.survivors || state.latest.confirmed_survivors || [])[state.selectedSurvivorId];
  if (selectedSurvivor) {
    elements.detailPanel.classList.remove('hidden');
    elements.detailPanel.classList.add('visible');
    elements.detailTitle.textContent = 'Survivor';
    elements.detailBody.innerHTML = `
      <div class="row"><span>Status</span><strong>${selectedSurvivor.status || 'confirmed'}</strong></div>
      <div class="row"><span>Position</span><strong>${Number(selectedSurvivor.x ?? 0).toFixed(1)}, ${Number(selectedSurvivor.y ?? 0).toFixed(1)}</strong></div>
      <div class="row"><span>Confidence</span><strong>${Math.round(Number(selectedSurvivor.confidence ?? 0) * 100)}%</strong></div>
      <div class="row"><span>Views</span><strong>${Number(selectedSurvivor.n_views ?? 0)}</strong></div>
    `;
    return;
  }

  elements.detailPanel.classList.add('hidden');
  elements.detailPanel.classList.remove('visible');
}

function findActiveTask(agentId) {
  const tasks = state.latest?.open_tasks || [];
  for (const task of tasks) {
    if (task.bids && Object.prototype.hasOwnProperty.call(task.bids, String(agentId))) {
      return task.type || 'TASK';
    }
  }
  return 'N/A';
}

function renderFrame() {
  if (!state.latest) {
    requestAnimationFrame(renderFrame);
    return;
  }

  const simState = state.latest;
  const settings = drawBackground(simState);
  drawHeatmap(simState, settings);
  drawSearchCoverage(simState, settings);
  drawTasks(simState, settings);
  drawSurvivors(simState, settings);
  drawDrones(simState, settings);
  drawSelectionHover(simState, settings);

  const now = new Date();
  elements.simTime.textContent = formatTime(now);
  updateStatusPanels();
  renderDroneList();
  renderDetections();
  renderTasks();
  renderDetailPanel();

  requestAnimationFrame(renderFrame);
}

function handleIncomingMessage(event) {
  try {
    const payload = JSON.parse(event.data);
    if (!payload || typeof payload !== 'object') {
      return;
    }

    const normalized = normalizeState(payload);
    state.previous = state.latest ? state.latest : null;
    state.latest = normalized;
    state.interpolationStart = performance.now();
    state.lastMessageTime = Date.now();
    state.connectionStatus = 'CONNECTED';
    setConnectionBadge('CONNECTED', 'status-live');
  } catch (error) {
    console.error('Malformed SimState payload', error);
  }
}

function connectWebSocket() {
  state.connectionStatus = 'CONNECTING';
  setConnectionBadge('CONNECTING...', 'status-connecting');

  const socket = new WebSocket(WS_URL);
  state.ws = socket;

  socket.addEventListener('open', () => {
    state.connectionStatus = 'CONNECTED';
    setConnectionBadge('CONNECTED', 'status-live');
  });

  socket.addEventListener('message', handleIncomingMessage);

  socket.addEventListener('close', () => {
    state.connectionStatus = 'RECONNECTING';
    setConnectionBadge('RECONNECTING...', 'status-disconnected');
    setTimeout(connectWebSocket, 1800);
  });

  socket.addEventListener('error', () => {
    state.connectionStatus = 'RECONNECTING';
    setConnectionBadge('RECONNECTING...', 'status-disconnected');
  });
}

function attachCanvasInteraction() {
  canvas.addEventListener('click', (event) => {
    if (!state.latest) return;

    const rect = canvas.getBoundingClientRect();
    const x = event.clientX - rect.left;
    const y = event.clientY - rect.top;
    const map = state.latest.map || {};
    const width = canvas.clientWidth || 600;
    const height = canvas.clientHeight || 600;
    const padding = 30;
    const scale = Math.min((width - padding * 2) / (Number(map.width || 100)), (height - padding * 2) / (Number(map.height || 100)));
    const originX = (width - (Number(map.width || 100) * scale)) / 2;
    const originY = (height - (Number(map.height || 100) * scale)) / 2;

    let closestDrone = null;
    let smallestDistance = Number.POSITIVE_INFINITY;

    for (const agent of state.latest.agents || []) {
      const px = Number(agent.x || 0) * scale + originX;
      const py = Number(agent.y || 0) * scale + originY;
      const distance = Math.hypot(x - px, y - py);
      if (distance < 22 && distance < smallestDistance) {
        smallestDistance = distance;
        closestDrone = agent;
      }
    }

    if (closestDrone) {
      state.selectedDroneId = Number(closestDrone.id);
      state.selectedSurvivorId = null;
      renderDetailPanel();
      renderDroneList();
      return;
    }

    const survivors = state.latest.survivors || state.latest.confirmed_survivors || [];
    for (let i = 0; i < survivors.length; i += 1) {
      const survivor = survivors[i];
      const px = Number(survivor.x || 0) * scale + originX;
      const py = Number(survivor.y || 0) * scale + originY;
      const distance = Math.hypot(x - px, y - py);
      if (distance < 18) {
        state.selectedSurvivorId = i;
        state.selectedDroneId = null;
        renderDetailPanel();
        return;
      }
    }

    state.selectedDroneId = null;
    state.selectedSurvivorId = null;
    renderDetailPanel();
    renderDroneList();
  });
}

function bindControls() {
  elements.restartSearch.addEventListener('click', async () => {
    if (elements.restartSearch.disabled) return;

    elements.restartSearch.disabled = true;
    elements.restartSearch.textContent = 'RESTARTING...';
    try {
      const response = await fetch('http://127.0.0.1:8000/restart', { method: 'POST' });
      if (!response.ok) throw new Error(`Restart failed (${response.status})`);
      elements.restartSearch.textContent = 'RESTART SEARCH';
    } catch (error) {
      console.error(error);
      elements.restartSearch.textContent = 'RESTART FAILED';
      setTimeout(() => {
        elements.restartSearch.textContent = 'RESTART SEARCH';
      }, 1800);
    } finally {
      elements.restartSearch.disabled = false;
    }
  });

  elements.closeDetail.addEventListener('click', () => {
    state.selectedDroneId = null;
    state.selectedSurvivorId = null;
    renderDetailPanel();
    renderDroneList();
  });

  window.addEventListener('resize', () => {
    updateCanvasSize();
  });
}

function init() {
  updateCanvasSize();
  bindControls();
  attachCanvasInteraction();
  setConnectionBadge('CONNECTING...', 'status-connecting');
  connectWebSocket();
  requestAnimationFrame(renderFrame);
}

init();
