"""Update dashboard HTML with topology controls + zoom/pan + LOD"""
import re

with open('server.py', 'r', encoding='utf-8', newline='') as f:
    content = f.read()

changes = 0

# 1. Add CSS for topology control bar
old_css = '.canvas-panel{flex:3;position:relative;background:var(--bg);cursor:crosshair}'
new_css = '''.canvas-panel{flex:3;position:relative;background:var(--bg);cursor:grab}
.canvas-panel.panning{cursor:grabbing}
.topo-bar{position:absolute;top:8px;left:50%;transform:translateX(-50%);display:flex;gap:6px;align-items:center;background:rgba(17,24,39,0.92);border:1px solid var(--border);border-radius:8px;padding:6px 12px;z-index:10;backdrop-filter:blur(8px);flex-wrap:wrap;justify-content:center}
.topo-bar .topo-btn{padding:3px 10px;border-radius:4px;border:1px solid var(--border);background:transparent;color:var(--text);cursor:pointer;font-size:0.7rem;transition:all 0.15s}
.topo-bar .topo-btn:hover{background:var(--border)}
.topo-bar .topo-btn.active{background:var(--accent);color:#000;border-color:var(--accent)}
.topo-bar input[type=range]{width:100px;accent-color:var(--accent)}
.topo-bar .topo-label{font-size:0.65rem;color:var(--gray);white-space:nowrap}
.zoom-indicator{position:absolute;bottom:8px;right:8px;background:rgba(17,24,39,0.8);color:var(--gray);font-size:0.65rem;padding:2px 8px;border-radius:4px;z-index:10}'''
if old_css in content:
    content = content.replace(old_css, new_css, 1)
    changes += 1
    print('1. CSS updated')
else:
    print('1. CSS NOT FOUND')

# 2. Add topology control bar HTML inside canvas-panel
old_panel = '<div class="canvas-panel" id="canvas-panel">\n<canvas id="agent-canvas"></canvas>\n<div class="tooltip" id="tooltip"></div>\n</div>'
new_panel = '''<div class="canvas-panel" id="canvas-panel">
<canvas id="agent-canvas"></canvas>
<div class="topo-bar" id="topo-bar">
<span class="topo-label">TOPOLOGY</span>
<button class="topo-btn active" onclick="setTopology('star')">Star</button>
<button class="topo-btn" onclick="setTopology('ring')">Ring</button>
<button class="topo-btn" onclick="setTopology('tree')">Tree</button>
<button class="topo-btn" onclick="setTopology('mesh')">Mesh</button>
<button class="topo-btn" onclick="setTopology('pubsub')">Pub/Sub</button>
<button class="topo-btn" onclick="setTopology('random')">Random</button>
<button class="topo-btn" onclick="setTopology('small_world')">SmallW</button>
<span class="topo-label">N:</span>
<input type="range" id="topo-count" min="10" max="500" value="100" step="10" oninput="document.getElementById('topo-count-val').textContent=this.value">
<span id="topo-count-val" style="font-size:0.7rem;color:#fff;min-width:30px">100</span>
<button class="topo-btn" onclick="generateTopology()" style="color:var(--green);border-color:var(--green)">Generate</button>
<button class="topo-btn" onclick="runTopologySim()" style="color:var(--accent);border-color:var(--accent)">Run Sim</button>
<button class="topo-btn" onclick="resetTopology()" style="color:var(--gray)">Reset</button>
</div>
<div class="zoom-indicator" id="zoom-indicator">100%</div>
<div class="tooltip" id="tooltip"></div>
</div>'''
if old_panel in content:
    content = content.replace(old_panel, new_panel, 1)
    changes += 1
    print('2. Panel HTML updated')
else:
    print('2. Panel HTML NOT FOUND')

# 3. Add zoom/pan/experiment state after canvas init
old_init = "let animFrame = 0;\nlet simRunning = false;"
new_init = '''let animFrame = 0;
let simRunning = false;
// Zoom/Pan state
let zoom = 1, panX = 0, panY = 0;
let targetZoom = 1, targetPanX = 0, targetPanY = 0;
let isPanning = false, panStartX = 0, panStartY = 0;
let topoMode = 'star';
let topoPositions = null;
let topoConnections = [];
let forceNodes = null;'''
if old_init in content:
    content = content.replace(old_init, new_init, 1)
    changes += 1
    print('3. Zoom/pan state added')
else:
    print('3. Init NOT FOUND')

# 4. Replace render() with zoom/pan/LOD version
old_render = '''function render() {
const W = canvas.width / devicePixelRatio;
const H = canvas.height / devicePixelRatio;
ctx.clearRect(0, 0, W, H);

// Background grid
ctx.strokeStyle = 'rgba(30,58,95,0.15)';
ctx.lineWidth = 1;
const gs = 40;
for (let x = gs; x < W; x += gs) { ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, H); ctx.stroke(); }
for (let y = gs; y < H; y += gs) { ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(W, y); ctx.stroke(); }

// Connections
connections.forEach(c => drawConnection(c));

// Agents
agents.forEach(a => drawNode(a));

// Hover detection
checkHover();

animFrame++;
requestAnimationFrame(render);
}'''

new_render = '''function render() {
const W = canvas.width / devicePixelRatio;
const H = canvas.height / devicePixelRatio;
ctx.clearRect(0, 0, W, H);

// Smooth zoom/pan easing
zoom += (targetZoom - zoom) * 0.15;
panX += (targetPanX - panX) * 0.15;
panY += (targetPanY - panY) * 0.15;

ctx.save();
ctx.translate(panX + W/2, panY + H/2);
ctx.scale(zoom, zoom);
ctx.translate(-W/2, -H/2);

// Background grid (scale-aware)
const gs = 40 / zoom;
ctx.strokeStyle = 'rgba(30,58,95,0.12)';
ctx.lineWidth = 1 / zoom;
const startX = -((panX/zoom) % gs + gs) % gs;
const startY = -((panY/zoom) % gs + gs) % gs;
for (let x = startX; x < W + gs; x += gs) { ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, H); ctx.stroke(); }
for (let y = startY; y < H + gs; y += gs) { ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(W, y); ctx.stroke(); }

// Draw connections
const allConns = topoConnections.length > 0 ? topoConnections : connections;
if (allConns.length < 500 || zoom > 0.5) {
  allConns.forEach(c => drawConnection(c));
} else {
  // LOD: skip thin connections at low zoom
  const threshold = allConns.length > 2000 ? 5 : (allConns.length > 500 ? 2 : 0);
  allConns.forEach(c => { if ((c.messages_sent||0) >= threshold || (c.weight||0) > 0.5) drawConnection(c); });
}

// Draw agents (LOD)
const nodeCount = agents.length;
if (nodeCount <= 200 || zoom > 0.4) {
  agents.forEach(a => drawNode(a));
} else if (nodeCount <= 500) {
  // Simplified: small dots with no labels
  agents.forEach(a => drawNodeSimple(a));
} else {
  // Heat map density
  drawDensityMap();
}

ctx.restore();

// Hover (screen-space)
checkHover();

animFrame++;
requestAnimationFrame(render);
}

function drawNodeSimple(agent) {
const W = canvas.width / devicePixelRatio;
const H = canvas.height / devicePixelRatio;
let x, y;
if (agent._x !== undefined) { x = agent._x; y = agent._y; }
else if (topoPositions && agent._topoIdx !== undefined) {
  const p = topoPositions[agent._topoIdx];
  if (p) { x = p[0] * W; y = p[1] * H; }
} else { return; }
const r = Math.max(3, agent._r || 20);
ctx.beginPath();
ctx.arc(x, y, r, 0, Math.PI*2);
ctx.fillStyle = agent._color || '#3b82f6';
ctx.fill();
ctx.strokeStyle = 'rgba(255,255,255,0.3)';
ctx.lineWidth = 0.5;
ctx.stroke();
}

function drawDensityMap() {
// Heat map: aggregate agent density into a grid
const W = canvas.width / devicePixelRatio;
const H = canvas.height / devicePixelRatio;
const gridSize = 20;
const cols = Math.ceil(W / gridSize), rows = Math.ceil(H / gridSize);
const grid = new Array(cols * rows).fill(0);
agents.forEach(a => {
  let x = a._x, y = a._y;
  if (x === undefined && topoPositions && a._topoIdx !== undefined) {
    const p = topoPositions[a._topoIdx];
    if (p) { x = p[0] * W; y = p[1] * H; }
  }
  if (x !== undefined) {
    const col = Math.floor(x / gridSize), row = Math.floor(y / gridSize);
    if (col >= 0 && col < cols && row >= 0 && row < rows) grid[row * cols + col]++;
  }
});
const maxDensity = Math.max(1, ...grid);
for (let row = 0; row < rows; row++) {
  for (let col = 0; col < cols; col++) {
    const d = grid[row * cols + col];
    if (d > 0) {
      const alpha = 0.1 + 0.7 * (d / maxDensity);
      ctx.fillStyle = 'rgba(59,130,246,' + alpha.toFixed(2) + ')';
      ctx.fillRect(col * gridSize, row * gridSize, gridSize, gridSize);
    }
  }
}
// Agent count label
ctx.fillStyle = '#fff';
ctx.font = 'bold 24px "Segoe UI"';
ctx.textAlign = 'center';
ctx.fillText(agents.length + ' agents (density view)', W/2, H/2);
ctx.font = '12px "Segoe UI"';
ctx.fillStyle = '#94a3b8';
ctx.fillText('zoom in for detail', W/2, H/2 + 28);
}'''

if old_render in content:
    content = content.replace(old_render, new_render, 1)
    changes += 1
    print('4. Render updated with zoom/pan/LOD')
else:
    print('4. Render NOT FOUND')

# 5. Replace drawNode to use topo positions
old_drawNode = '''function drawNode(agent) {
const { _x: x, _y: y, _r: r, _color: color, status } = agent;'''
new_drawNode = '''function drawNode(agent) {
const W = canvas.width / devicePixelRatio;
const H = canvas.height / devicePixelRatio;
// Use topology positions if available
let x = agent._x, y = agent._y;
if ((x === undefined || x === 0) && topoPositions && agent._topoIdx !== undefined) {
  const p = topoPositions[agent._topoIdx];
  if (p) { x = p[0] * W; y = p[1] * H; agent._x = x; agent._y = y; }
}
if (x === undefined) return;
const r = agent._r, color = agent._color, status = agent.status;'''

if old_drawNode in content:
    content = content.replace(old_drawNode, new_drawNode, 1)
    changes += 1
    print('5. drawNode updated')
else:
    print('5. drawNode NOT FOUND')

# 6. Replace drawConnection to handle topo connections
old_drawConn = '''function drawConnection(conn) {
const from = conn.from, to = conn.to;
const t = animFrame * 0.02;'''
new_drawConn = '''function drawConnection(conn) {
const from = conn.from || (conn.source ? agents.find(a => a.agent_id === conn.source) : null);
const to = conn.to || (conn.target ? agents.find(a => a.agent_id === conn.target) : null);
if (!from || !to) return;
const W = canvas.width / devicePixelRatio;
const H = canvas.height / devicePixelRatio;
// Get positions
let fx = from._x, fy = from._y, tx = to._x, ty = to._y;
if ((fx === undefined) && topoPositions && from._topoIdx !== undefined) { const p = topoPositions[from._topoIdx]; if (p) { fx = p[0]*W; fy = p[1]*H; } }
if ((tx === undefined) && topoPositions && to._topoIdx !== undefined) { const p = topoPositions[to._topoIdx]; if (p) { tx = p[0]*W; ty = p[1]*H; } }
if (fx === undefined || tx === undefined) return;
const weight = conn.weight || 0.5;
const msgs = conn.messages_sent || 0;
const t = animFrame * 0.02;'''

if old_drawConn in content:
    content = content.replace(old_drawConn, new_drawConn, 1)
    changes += 1
    print('6. drawConnection updated')
else:
    print('6. drawConnection NOT FOUND')

# 7. Also fix the drawConnection body to use fx/fy/tx/ty instead of from._x etc.
old_body1 = 'ctx.moveTo(from._x, from._y);'
if old_body1 in content:
    content = content.replace(old_body1, 'ctx.moveTo(fx, fy);')
    changes += 1
old_body2 = 'const midX = (from._x + to._x) / 2;'
if old_body2 in content:
    content = content.replace(old_body2, 'const midX = (fx + tx) / 2;')
old_body3 = 'const midY = (from._y + to._y) / 2 - 20;'
if old_body3 in content:
    content = content.replace(old_body3, 'const midY = (fy + ty) / 2 - 20;')
old_body4 = 'const px = (1 - pt) * (1 - pt) * from._x + 2 * (1 - pt) * pt * midX + pt * pt * to._x;'
if old_body4 in content:
    content = content.replace(old_body4, 'const px = (1 - pt) * (1 - pt) * fx + 2 * (1 - pt) * pt * midX + pt * pt * tx;')
old_body5 = 'const py = (1 - pt) * (1 - pt) * from._y + 2 * (1 - pt) * pt * midY + pt * pt * to._y;'
if old_body5 in content:
    content = content.replace(old_body5, 'const py = (1 - pt) * (1 - pt) * fy + 2 * (1 - pt) * pt * midY + pt * pt * ty;')

# Line width based on traffic
old_stroke = "ctx.strokeStyle = 'rgba(56,189,248,0.3)';\nctx.lineWidth = 1.5;"
if old_stroke in content:
    new_stroke = "const lw = 0.8 + Math.min(4, Math.log2((msgs||0) + 1) * 0.8);\nctx.strokeStyle = 'rgba(56,189,248,' + (0.2 + Math.min(0.5, (msgs||0)/50)).toFixed(2) + ')';\nctx.lineWidth = lw;"
    content = content.replace(old_stroke, new_stroke)
    changes += 1

print('6b. Connection body fixed')

# 8. Add mouse wheel zoom + drag pan handlers
old_mouseleave = "canvas.addEventListener('mouseleave', () => {\nmouseX = -100; mouseY = -100;\nhoveredAgent = null;\ndocument.getElementById('tooltip').style.display = 'none';\n});"
new_mouse = '''canvas.addEventListener('mousedown', (e) => {
if (e.button === 0 && !hoveredAgent) { isPanning = true; panStartX = e.clientX - panX; panStartY = e.clientY - panY; canvas.parentElement.classList.add('panning'); }
});
canvas.addEventListener('mousemove', (e) => {
const rect = canvas.getBoundingClientRect();
mouseX = (e.clientX - rect.left - panX) / zoom + (canvas.width/devicePixelRatio)/2 * (1 - 1/zoom);
mouseY = (e.clientY - rect.top - panY) / zoom + (canvas.height/devicePixelRatio)/2 * (1 - 1/zoom);
if (isPanning) { targetPanX = e.clientX - panStartX; targetPanY = e.clientY - panStartY; panX = targetPanX; panY = targetPanY; }
});
canvas.addEventListener('mouseup', () => { isPanning = false; canvas.parentElement.classList.remove('panning'); });
canvas.addEventListener('wheel', (e) => {
e.preventDefault();
const delta = e.deltaY > 0 ? 0.85 : 1.18;
targetZoom = Math.max(0.1, Math.min(5, targetZoom * delta));
document.getElementById('zoom-indicator').textContent = Math.round(targetZoom * 100) + '%';
}, {passive: false});
canvas.addEventListener('mouseleave', () => {
mouseX = -100; mouseY = -100;
hoveredAgent = null;
document.getElementById('tooltip').style.display = 'none';
});'''
if old_mouseleave in content:
    content = content.replace(old_mouseleave, new_mouse, 1)
    changes += 1
    print('8. Mouse zoom/pan added')
else:
    print('8. Mouseleave NOT FOUND')

# 9. Add experiment JS functions before '// ============== Start =============='
old_start = '// ============== Start =============='
new_start = '''// ============== Topology Experiment ==============
let currentTopology = 'star';
let topoCount = 100;

async function setTopology(type) {
currentTopology = type;
document.querySelectorAll('.topo-btn').forEach(b => b.classList.remove('active'));
event.target.classList.add('active');
document.getElementById('topo-count-val').textContent = topoCount;
}
async function generateTopology() {
topoCount = parseInt(document.getElementById('topo-count').value);
document.getElementById('topo-count-val').textContent = topoCount;
logEntry('L1', '=== Generate: ' + topoCount + ' agents | ' + currentTopology + ' topology ===');
try {
const r = await fetch(API + '/experiments/generate', {
method:'POST', headers:{'Content-Type':'application/json'},
body: JSON.stringify({count: topoCount, topology: currentTopology, roles:'scout:70,commander:10,analyst:20'})
});
const data = await r.json();
logEntry('L1', 'Generated: ' + data.agents_count + ' agents | ' + data.connections_count + ' connections');
logEntry('L2', 'Roles: ' + JSON.stringify(data.role_distribution));
logEntry('L2', 'Max degree: ' + data.max_degree + ' | Bottleneck: ' + (data.bottleneck_agents||[]).slice(0,3).join(', '));
// Load into canvas
topoPositions = data.positions;
topoConnections = data.connections;
agents = [];
// Fetch agents from registry
const ar = await fetch(API + '/agents');
agents = await ar.json();
// Map topology positions to agents
agents.forEach((a, i) => { a._topoIdx = i; a._r = Math.max(6, Math.min(30, 300 / Math.sqrt(topoCount))); });
targetZoom = Math.min(1.5, Math.max(0.3, 1.0));
targetPanX = 0; targetPanY = 0;
zoom = targetZoom; panX = 0; panY = 0;
document.getElementById('stat-agents').textContent = data.agents_count;
document.getElementById('stat-packets').textContent = data.connections_count;
} catch(e) { logEntry('L1', 'Generate failed: ' + e.message); }
}
async function runTopologySim() {
if (topoConnections.length === 0) { logEntry('L1', 'Please generate topology first'); return; }
logEntry('L1', '=== Running communication simulation ===');
try {
const r = await fetch(API + '/experiments/run', {
method:'POST', headers:{'Content-Type':'application/json'},
body: JSON.stringify({rounds: 10, msg_rate: 0.3})
});
const data = await r.json();
const m = data.metrics;
logEntry('L1', 'Messages: ' + m.total_messages + ' | Latency p50=' + m.latency_p50_ms + 'ms p99=' + m.latency_p99_ms + 'ms');
logEntry('L2', 'Avg degree: ' + m.avg_degree + ' | Diameter: ' + m.topology_diameter + ' | Bandwidth: ' + m.total_bandwidth_kb.toFixed(0) + 'KB');
logEntry('L2', 'Bottleneck agents: ' + (m.bottleneck_agents||[]).slice(0,5).join(', '));
// Update connections with traffic data
topoConnections = data.connections || topoConnections;
// Show per-round stats
logEntry('L3', 'Messages/round: ' + (m.messages_per_round||[]).join(', '));
} catch(e) { logEntry('L1', 'Simulation failed: ' + e.message); }
}
async function resetTopology() {
agents = []; topoConnections = []; topoPositions = null;
connections = [];
document.getElementById('stat-agents').textContent = '0';
document.getElementById('stat-packets').textContent = '0';
targetZoom = 1; targetPanX = 0; targetPanY = 0;
zoom = 1; panX = 0; panY = 0;
// Clear agents from registry
const agentList = await (await fetch(API + '/agents')).json();
for (const a of agentList) { await fetch(API + '/agents/' + a.agent_id, {method:'DELETE'}); }
logEntry('L1', 'Topology reset');
}
// Initial generation on load
setTimeout(() => generateTopology(), 800);

// ============== Start =============='''
if old_start in content:
    content = content.replace(old_start, new_start, 1)
    changes += 1
    print('9. Experiment JS added')
else:
    print('9. Start marker NOT FOUND')

# Write result
with open('server.py', 'w', encoding='utf-8', newline='') as f:
    f.write(content)

print(f'\nTotal changes: {changes}')
