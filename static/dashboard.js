// ============== State ==============
const API = '/api';
let agents = [];
let connections = [];
let _prevAgents = [];    // 上一帧 Agent 快照，用于位置保持
let ws = null;
let canvas, ctx;
let mouseX = -100, mouseY = -100;
let hoveredAgent = null;
let selectedAgent = null;
let animFrame = 0;
let simRunning = false;
// Zoom/Pan
let zoom = 1, panX = 0, panY = 0;
let targetZoom = 1, targetPanX = 0, targetPanY = 0;
let isPanning = false, panStartX = 0, panStartY = 0;
// Map state
let terrainMap = null;
let tickRunning = false;
let tickInterval = null;  // 推演定时器

// ============== Canvas Setup ==============
canvas = document.getElementById('agent-canvas');
ctx = canvas.getContext('2d');

function resizeCanvas() {
const panel = document.getElementById('canvas-panel');
const rect = panel.getBoundingClientRect();
canvas.width = rect.width * devicePixelRatio;
canvas.height = rect.height * devicePixelRatio;
canvas.style.width = rect.width + 'px';
canvas.style.height = rect.height + 'px';
}
resizeCanvas();
window.addEventListener('resize', resizeCanvas);

// ============== Colors ==============
const ROLE_COLORS = { scout: '#22c55e', commander: '#3b82f6', generic: '#94a3b8', analyst: '#a855f7', support: '#f59e0b' };
const ROLE_ICONS = { scout: 'S', commander: 'C', generic: 'A', analyst: 'Y', support: 'H' };

// ============== Layout ==============
function layoutAgents() {
const W = canvas.width / devicePixelRatio;
const H = canvas.height / devicePixelRatio;
const n = agents.length;
if (n === 0) return;
const r = Math.min(24, Math.max(6, Math.min(W, H) / (Math.sqrt(n) * 3 + 8)));
const cx = W / 2, cy = H / 2;
const cellW = (terrainMap ? W / (terrainMap.size || 1) : W);
const cellH = (terrainMap ? H / (terrainMap.size || 1) : H);
agents.forEach((a, i) => {
if (a.x !== undefined && a.y !== undefined && terrainMap) {
a._x = a.x * cellW + cellW / 2;
a._y = a.y * cellH + cellH / 2;
} else if (a._x === undefined) {
const angle = (2 * Math.PI * i) / n - Math.PI / 2;
const dist = Math.min(W, H) * 0.32;
a._x = cx + Math.cos(angle) * dist;
a._y = cy + Math.sin(angle) * dist;
if (n === 1) { a._x = cx; a._y = cy; }
}
a._r = r;
a._color = ROLE_COLORS[a.role] || ROLE_COLORS.generic;
a._icon = ROLE_ICONS[a.role] || 'A';
});
}

// ============== Render ==============
function render() {
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

// Terrain map background
drawTerrainMap(W, H);

// Smooth movement interpolation
agents.forEach(a => {
if (a._targetX !== undefined && a._targetY !== undefined) {
a._x += (a._targetX - a._x) * 0.1;
a._y += (a._targetY - a._y) * 0.1;
if (Math.abs(a._x - a._targetX) < 0.5 && Math.abs(a._y - a._targetY) < 0.5) {
a._x = a._targetX;
a._y = a._targetY;
delete a._targetX;
delete a._targetY;
}
}
});

// Connections
const N = agents.length;
if (N <= 200 && zoom > 0.25) {
connections.forEach(c => drawConnection(c));
}

// Agents (LOD)
if (N <= 200 && zoom > 0.25) {
agents.forEach(a => drawNode(a));
} else if (N <= 500 && zoom > 0.15) {
agents.forEach(a => drawNodeSimple(a));
} else {
drawDensityMap(W, H);
}

ctx.restore();
checkHover();
animFrame++;
requestAnimationFrame(render);
}

function drawNodeSimple(agent) {
const W = canvas.width / devicePixelRatio, H = canvas.height / devicePixelRatio;
let x = agent._x, y = agent._y;
if (x === undefined) return;
const r = Math.max(3, agent._r || 20);
ctx.beginPath(); ctx.arc(x, y, r, 0, Math.PI*2);
ctx.fillStyle = agent._color || '#3b82f6'; ctx.fill();
ctx.strokeStyle = 'rgba(255,255,255,0.2)'; ctx.lineWidth = 0.5; ctx.stroke();
}

function drawDensityMap(W, H) {
const gs = 16, cols = Math.ceil(W/gs), rows = Math.ceil(H/gs);
const grid = new Array(cols*rows).fill(0);
agents.forEach(a => {
let x = a._x, y = a._y;
if (x !== undefined) { const col = Math.floor(x/gs), row = Math.floor(y/gs); if (col>=0&&col<cols&&row>=0&&row<rows) grid[row*cols+col]++; }
});
const mx = Math.max(1, ...grid);
for (let r=0; r<rows; r++) for (let c=0; c<cols; c++) { const d = grid[r*cols+c]; if(d>0){ctx.fillStyle='rgba(59,130,246,'+(0.1+0.7*d/mx).toFixed(2)+')';ctx.fillRect(c*gs,r*gs,gs,gs);} }
ctx.fillStyle='#fff'; ctx.font='bold 20px Segoe UI'; ctx.textAlign='center'; ctx.fillText(agents.length+' agents (density)', W/2, H/2);
}

function drawNode(agent) {
const W = canvas.width / devicePixelRatio;
const H = canvas.height / devicePixelRatio;
let x = agent._x, y = agent._y;
if (x === undefined) return;
const r = agent._r, color = agent._color, status = agent.status;
const t = animFrame * 0.05;

// Glow
ctx.save();
ctx.beginPath(); ctx.arc(x, y, r + 6, 0, Math.PI * 2);
let glowAlpha = 0.15;
let glowColor = color;
if (status === 'running') { glowAlpha = 0.25 + Math.sin(t * 3) * 0.15; glowColor = '#ffffff'; }
else if (status === 'error') { glowAlpha = 0.3 + Math.sin(t * 5) * 0.2; glowColor = '#ef4444'; }
const grad = ctx.createRadialGradient(x, y, r, x, y, r + 12);
grad.addColorStop(0, glowColor + '80'); grad.addColorStop(1, 'transparent');
ctx.fillStyle = grad; ctx.fill();
ctx.restore();

// Selection ring
if (agent === selectedAgent) {
ctx.beginPath(); ctx.arc(x, y, r + 4, 0, Math.PI * 2);
ctx.strokeStyle = '#ffffff'; ctx.lineWidth = 2;
ctx.setLineDash([4, 4]); ctx.lineDashOffset = -t * 2; ctx.stroke();
ctx.setLineDash([]); ctx.lineWidth = 1;
}

// Body
const bodyGrad = ctx.createRadialGradient(x - r * 0.3, y - r * 0.3, r * 0.1, x, y, r);
bodyGrad.addColorStop(0, '#ffffff40'); bodyGrad.addColorStop(0.7, color + 'cc'); bodyGrad.addColorStop(1, color + '60');
ctx.beginPath(); ctx.arc(x, y, r, 0, Math.PI * 2);
ctx.fillStyle = bodyGrad; ctx.fill();
ctx.strokeStyle = color + '99'; ctx.lineWidth = 2; ctx.stroke();

// Icon
ctx.fillStyle = '#fff';
ctx.font = 'bold ' + (r * 0.6) + 'px "Segoe UI"';
ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
ctx.fillText(agent._icon, x, y + 1);

// Status dot
ctx.beginPath(); ctx.arc(x + r * 0.6, y - r * 0.6, r * 0.18, 0, Math.PI * 2);
const statusColors = { idle: '#22c55e', running: '#3b82f6', paused: '#f59e0b', stopped: '#ef4444', error: '#ef4444', created: '#94a3b8' };
ctx.fillStyle = statusColors[status] || '#94a3b8'; ctx.fill();
ctx.strokeStyle = 'rgba(0,0,0,0.4)'; ctx.lineWidth = 1; ctx.stroke();

// Label
ctx.fillStyle = '#cbd5e1'; ctx.font = '11px "Segoe UI"';
ctx.fillText(agent.name || agent.agent_id, x, y + r + 16);
ctx.fillStyle = '#64748b'; ctx.font = '9px "Segoe UI"';
ctx.fillText(agent.role, x, y + r + 29);
}

function drawConnection(conn) {
const from = conn.from || (conn.source ? agents.find(a => a.agent_id === conn.source) : null);
const to = conn.to || (conn.target ? agents.find(a => a.agent_id === conn.target) : null);
if (!from || !to) return;
let fx = from._x, fy = from._y, tx = to._x, ty = to._y;
if (fx === undefined || tx === undefined) return;
const msgs = conn.messages_sent || 0;
const t = animFrame * 0.02;

ctx.beginPath(); ctx.moveTo(fx, fy);
const midX = (fx + tx) / 2, midY = (fy + ty) / 2 - 20;
ctx.quadraticCurveTo(midX, midY, tx, ty);

const lw = 0.8 + Math.min(4, Math.log2((msgs||0) + 1) * 0.8);
ctx.strokeStyle = 'rgba(56,189,248,' + (0.2 + Math.min(0.5, (msgs||0)/50)).toFixed(2) + ')';
ctx.lineWidth = lw;
ctx.setLineDash([6, 8]); ctx.lineDashOffset = -t * 3; ctx.stroke(); ctx.setLineDash([]);

// Particle
const pt = t % 1;
const px = (1-pt)*(1-pt)*fx + 2*(1-pt)*pt*midX + pt*pt*tx;
const py = (1-pt)*(1-pt)*fy + 2*(1-pt)*pt*midY + pt*pt*ty;
ctx.beginPath(); ctx.arc(px, py, 3, 0, Math.PI*2);
ctx.fillStyle = '#38bdf8'; ctx.fill();
ctx.beginPath(); ctx.arc(px, py, 6, 0, Math.PI*2);
ctx.fillStyle = 'rgba(56,189,248,0.2)'; ctx.fill();
}

// ============== Hover & Tooltip ==============
function checkHover() {
const W = canvas.width / devicePixelRatio;
const H = canvas.height / devicePixelRatio;
const mx = mouseX, my = mouseY;

let found = null;
for (let i = agents.length - 1; i >= 0; i--) {
const a = agents[i];
if (a._x === undefined) continue;
const dx = mx - a._x, dy = my - a._y;
if (Math.sqrt(dx*dx + dy*dy) < (a._r || 20) + 4) { found = a; break; }
}

const tt = document.getElementById('tooltip');
if (found) {
canvas.style.cursor = 'pointer';
if (found !== hoveredAgent) {
hoveredAgent = found;
tt.innerHTML = '<div class=tt-name>' + (found.name || found.agent_id) + '</div>' +
'<div class=tt-role style=background:' + found._color + '33;color:' + found._color + '>' + found.role + '</div>' +
'<div class=tt-row><span class=lbl>ID</span><span class=val>' + found.agent_id + '</span></div>' +
'<div class=tt-row><span class=lbl>状态</span><span class=val>' + found.status + '</span></div>' +
	(found.x !== undefined && found.y !== undefined ? '<div class=tt-row><span class=lbl>位置</span><span class=val>' + found.x.toFixed(1) + ', ' + found.y.toFixed(1) + '</span></div>' : '') +
'<div class=tt-row><span class=lbl>已完成</span><span class=val>' + found.completed_tasks + '</span></div>' +
'<div class=tt-skills>' + (found.skills||[]).map(s => '<span class=tt-tag>' + s + '</span>').join('') + '</div>' +
'<div class=tt-skills>' + (found.tags||[]).map(t => '<span class=tt-tag style=background:#1e3a5f>#' + t + '</span>').join('') + '</div>';
}
tt.style.display = 'block';
tt.style.left = (mx + 16) + 'px';
tt.style.top = (my - 10) + 'px';
} else {
canvas.style.cursor = 'default';
hoveredAgent = null;
tt.style.display = 'none';
}
}

// ============== Mouse Events ==============
canvas.addEventListener('mousemove', (e) => {
const rect = canvas.getBoundingClientRect();
mouseX = (e.clientX - rect.left - panX) / zoom + (canvas.width/devicePixelRatio)/2 * (1 - 1/zoom);
mouseY = (e.clientY - rect.top - panY) / zoom + (canvas.height/devicePixelRatio)/2 * (1 - 1/zoom);
if (isPanning) { panX = e.clientX - panStartX; panY = e.clientY - panStartY; targetPanX = panX; targetPanY = panY; }
});
canvas.addEventListener('click', () => { selectedAgent = hoveredAgent; });
canvas.addEventListener('mousedown', (e) => {
if (e.button === 0 && !hoveredAgent) { isPanning = true; panStartX = e.clientX - panX; panStartY = e.clientY - panY; canvas.parentElement.classList.add('panning'); }
});
canvas.addEventListener('mouseup', () => { isPanning = false; canvas.parentElement.classList.remove('panning'); });
canvas.addEventListener('wheel', (e) => {
e.preventDefault();
targetZoom = Math.max(0.1, Math.min(5, targetZoom * (e.deltaY > 0 ? 0.85 : 1.18)));
document.getElementById('zoom-indicator').textContent = Math.round(targetZoom * 100) + '%';
}, {passive: false});
canvas.addEventListener('mouseleave', () => {
mouseX = -100; mouseY = -100; hoveredAgent = null;
document.getElementById('tooltip').style.display = 'none';
});

// ============== WebSocket ==============
function connectWS() {
const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
ws = new WebSocket(proto + '//' + location.host + '/ws');
ws.onopen = () => { ws.send('all'); logEntry('L1', 'WebSocket 已连接'); };
ws.onmessage = (e) => {
const msg = JSON.parse(e.data);
if (msg.type === 'status' || msg.type === 'all') {
agents = msg.data.agents || [];
// 从持久快照恢复渲染状态
		if (_prevAgents.length > 0) {
			agents.forEach(a => {
				const prev = _prevAgents.find(p => p.agent_id === a.agent_id);
				if (prev) { a._x = prev._x; a._y = prev._y; a._r = prev._r; a._color = prev._color; a._icon = prev._icon; }
			});
		}
// Process map data from server
if (msg.data.map && !terrainMap) {
terrainMap = msg.data.map;
renderLegend();
}
layoutAgents();
_prevAgents = agents.map(a => ({ agent_id: a.agent_id, _x: a._x, _y: a._y, _r: a._r, _color: a._color, _icon: a._icon }));
document.getElementById('stat-agents').textContent = msg.data.stats?.total_agents || agents.length;
}
if (msg.type === 'packets' && msg.data) {
			document.getElementById('stat-packets').textContent = msg.data.total || msg.data.stats?.total || 0;
		}
		if (msg.type === 'message') {
logEntry('L5', '消息包: ' + (msg.data.payload?.action || msg.data.type) + ' [' + msg.data.source + ' → ' + msg.data.target + ']');
}
};
ws.onclose = () => { setTimeout(connectWS, 3000); };
}
connectWS();

function updateConnections() {
fetch(API + '/packets').then(r => r.json()).then(data => {
const seen = new Set();
connections = [];
(data.records||[]).forEach(rec => {
const key = [rec.source_agent, rec.target_agent].sort().join('-');
if (!seen.has(key) && rec.source_agent && rec.target_agent) {
seen.add(key);
const from = agents.find(a => a.agent_id === rec.source_agent);
const to = agents.find(a => a.agent_id === rec.target_agent);
if (from && to) connections.push({ from, to });
}
});
document.getElementById('stat-packets').textContent = data.total || 0;
});
}

// ============== Log ==============
let logBuffer = [];
function logEntry(level, event) {
logBuffer.push({ timestamp: new Date().toISOString().slice(11, 23), level, event });
if (logBuffer.length > 500) logBuffer.shift();
renderLogs();
}
function renderLogs() {
const filter = document.getElementById('log-filter')?.value || '';
const container = document.getElementById('log-entries');
const autoscroll = document.getElementById('log-autoscroll')?.checked;
if (!container) return;
const wasAtBottom = container.scrollHeight - container.scrollTop - container.clientHeight < 30;
let entries = [...logBuffer];
if (filter) entries = entries.filter(e => e.level === filter);
document.getElementById('log-count') && (document.getElementById('log-count').textContent = entries.length);
container.innerHTML = entries.slice(-200).map(e =>
'<div class=log-entry><span class=ts>' + e.timestamp + '</span> <span class="lv lv-' + e.level + '">' + e.level + '</span> <span class=ev>' + e.event + '</span></div>'
).join('') || '<div class=log-entry><span class=ts>--</span> <span class=ev>无日志</span></div>';
if (autoscroll && wasAtBottom) container.scrollTop = container.scrollHeight;
}
function clearLogs() { logBuffer = []; renderLogs(); }

// ============== API Actions ==============
async function runScript() {
const script = document.getElementById('script-input').value.trim();
if (!script) return;
simRunning = true;
logEntry('L1', '=== 执行脚本 ===');
logEntry('L1', '剧本: ' + script.slice(0, 80) + '...');
logEntry('L1', '解析剧本中...');
const r = await fetch(API + '/simulations/run', {
method: 'POST', headers: {'Content-Type': 'application/json'},
body: JSON.stringify({ scene: 'auto', script: script, name: 'ai-' + Date.now() })
});
const data = await r.json();
const parseMethod = data.llm_parsed ? 'AI(LLM)' : '模板匹配';
logEntry('L1', '[' + parseMethod + '] 场景: ' + (data.scene_definition?.scene_name || 'N/A'));
logEntry('L1', '仿真完成: ' + data.duration_seconds + 's | ' + data.agent_stats.total_agents + ' 个Agent');
logEntry('L1', '角色分布: ' + JSON.stringify(data.agent_stats.by_role));
if (data.scene_definition?.agents) {
data.scene_definition.agents.forEach(a => {
logEntry('L2', a.name + ' (' + a.role + ') | 任务: ' + (a.tasks||[]).join(', '));
});
}
document.getElementById('stat-sims').textContent = (parseInt(document.getElementById('stat-sims').textContent) || 0) + 1;
if (ws && ws.readyState === WebSocket.OPEN) ws.send('all');
updateConnections();
simRunning = false;
}

async function quickScene(name) {
simRunning = true;
logEntry('L1', '=== 运行 ' + name + ' 场景 ===');
const r = await fetch(API + '/simulations/run', {
method: 'POST', headers: {'Content-Type': 'application/json'},
body: JSON.stringify({ scene: name })
});
const data = await r.json();
logEntry('L1', '完成: ' + data.duration_seconds + 's | Agent: ' + data.agent_stats.total_agents + ' 个');
document.getElementById('stat-sims').textContent = (parseInt(document.getElementById('stat-sims').textContent) || 0) + 1;
if (ws && ws.readyState === WebSocket.OPEN) ws.send('all');
updateConnections();
simRunning = false;
}

async function clearAll() {
const agentList = await (await fetch(API + '/agents')).json();
for (const a of agentList) { await fetch(API + '/agents/' + a.agent_id, { method: 'DELETE' }); }
agents = []; _prevAgents = []; connections = []; selectedAgent = null; hoveredAgent = null; terrainMap = null;
	// 停止推演定时器
	if (tickRunning) {
		tickRunning = false;
		const btn = document.getElementById('tick-btn');
		if (btn) { btn.textContent = '开始推演'; btn.classList.remove('accent'); }
		if (tickInterval) { clearInterval(tickInterval); tickInterval = null; }
	}
	if (tickInterval) { clearInterval(tickInterval); tickInterval = null; }
document.getElementById('stat-agents').textContent = '0';
document.getElementById('stat-packets').textContent = '0';
logEntry('L1', '已清空全部 Agent');
}

function togglePanel(id) { document.getElementById(id).classList.toggle('minimized'); resizeCanvas(); }

// ============== Settings ==============
async function loadSettings() {
try {
const r = await fetch(API + '/settings'); const s = await r.json();
if (s.has_key) document.getElementById('cfg-apikey').placeholder = s.api_key;
document.getElementById('cfg-provider').value = s.provider || 'auto';
document.getElementById('cfg-apibase').placeholder = s.api_base || 'https://api.deepseek.com/v1';
document.getElementById('cfg-model').placeholder = s.model || 'deepseek-chat';
const statusEl = document.getElementById('cfg-status');
if (statusEl) { statusEl.textContent = s.has_key ? '已配置' : '未配置 API Key'; statusEl.style.color = s.has_key ? 'var(--green)' : 'var(--red)'; }
if (s.api_base) document.getElementById('cfg-apibase').value = s.api_base;
} catch(e) { console.error('loadSettings', e); }
}
function onProviderChange() {
const p = document.getElementById('cfg-provider').value;
const bases = { anthropic: '', openai: 'https://api.openai.com/v1', deepseek: 'https://api.deepseek.com/v1', auto: '' };
const models = { anthropic: 'claude-sonnet-4-6', openai: 'gpt-4o', deepseek: 'deepseek-chat', auto: '' };
document.getElementById('cfg-apibase').placeholder = bases[p] || '';
document.getElementById('cfg-apibase').value = bases[p] || '';
document.getElementById('cfg-model').placeholder = models[p] || '';
}
function toggleSettings() {
const panel = document.getElementById('settings-panel');
panel.style.display = panel.style.display === 'none' ? 'block' : 'none';
if (panel.style.display === 'block') loadSettings();
}
async function saveSettings() {
const apiKey = document.getElementById('cfg-apikey').value.trim();
const provider = document.getElementById('cfg-provider').value;
const apiBase = document.getElementById('cfg-apibase').value.trim();
const model = document.getElementById('cfg-model').value.trim();
if (!apiKey) { const el = document.getElementById('cfg-status'); el.textContent = '请输入 API Key'; return; }
const el = document.getElementById('cfg-status'); el.textContent = '保存中...'; el.style.color = 'var(--gray)';
try {
const body = { api_key: apiKey, provider: provider };
if (apiBase) body.api_base = apiBase;
if (model) body.model = model;
const r = await fetch(API + '/settings', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body) });
const s = await r.json();
el.textContent = s.has_key ? '已保存' : '保存失败'; el.style.color = s.has_key ? 'var(--green)' : 'var(--red)';
document.getElementById('cfg-apikey').value = '';
document.getElementById('cfg-apikey').placeholder = s.api_key;
if (s.api_base) document.getElementById('cfg-apibase').value = s.api_base;
document.getElementById('cfg-apibase').placeholder = s.api_base || 'https://api.deepseek.com/v1';
document.getElementById('cfg-model').placeholder = s.model || 'deepseek-chat';
} catch(e) { el.textContent = '保存失败: ' + e.message; el.style.color = 'var(--red)'; }
}
async function testSettings() {
const script = document.getElementById('script-input').value.trim();
if (!script) { logEntry('L1', '请先在剧本编辑器中输入内容'); return; }
const el = document.getElementById('cfg-status'); el.textContent = '解析中...'; el.style.color = 'var(--gray)';
try {
const r = await fetch(API + '/scripts/parse', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({script, use_llm:true}) });
const data = await r.json();
const status = data.llm_used ? 'LLM解析成功' : '模板匹配(未用LLM)';
el.textContent = status; el.style.color = data.llm_used ? 'var(--green)' : 'var(--orange)';
logEntry('L1', '[解析预览] 场景: ' + data.scene_name + ' | Agent: ' + data.agents.length + '个 | 方式: ' + status);
data.agents.forEach(a => logEntry('L2', a.name + ' (' + a.role + ') | ' + (a.tasks||[]).join(', ')));
} catch(e) { el.textContent = '解析失败'; el.style.color = 'var(--red)'; logEntry('L1', '[解析失败] ' + e.message); }
}

// ============== Terrain Map ==============
function drawTerrainMap(W, H) {
	if (!terrainMap || !terrainMap.grid) return;
	const size = terrainMap.size || terrainMap.grid.length;

	// 离屏 Canvas: 在网格分辨率下绘制高程颜色
	const offCanvas = document.createElement('canvas');
	offCanvas.width = size;
	offCanvas.height = size;
	const offCtx = offCanvas.getContext('2d');

	// 逐格填充
	for (let row = 0; row < size; row++) {
		for (let col = 0; col < size; col++) {
			const cell = terrainMap.grid[row][col];
			offCtx.fillStyle = cell.color || elevationToColor(cell.elevation || 0);
			offCtx.fillRect(col, row, 1, 1);
		}
	}

	// 放大到画布尺寸 (imageSmoothingEnabled 提供双线性插值)
	const cellW = W / size;
	const cellH = H / size;
	ctx.save();
	ctx.imageSmoothingEnabled = true;
	ctx.drawImage(offCanvas, 0, 0, W, H);

	// 等高线 (50m 间隔)
	ctx.strokeStyle = 'rgba(0,0,0,0.18)';
	ctx.lineWidth = 0.5;
	const interval = 50;
	for (let row = 0; row < size; row++) {
		for (let col = 0; col < size; col++) {
			const elev = terrainMap.grid[row][col].elevation || 0;
			const x = col * cellW, y = row * cellH;
			// 右边
			if (col + 1 < size) {
				const e2 = terrainMap.grid[row][col + 1].elevation || 0;
				const lo = Math.min(elev, e2), hi = Math.max(elev, e2);
				const loB = Math.floor(lo / interval), hiB = Math.floor(hi / interval);
				for (let b = loB + 1; b <= hiB; b++) {
					const t = (b * interval - lo) / Math.max(hi - lo, 1);
					const cx = x + (elev < e2 ? t : 1 - t) * cellW;
					ctx.beginPath(); ctx.moveTo(cx, y); ctx.lineTo(cx, y + cellH); ctx.stroke();
				}
			}
			// 下边
			if (row + 1 < size) {
				const e2 = terrainMap.grid[row + 1][col].elevation || 0;
				const lo = Math.min(elev, e2), hi = Math.max(elev, e2);
				const loB = Math.floor(lo / interval), hiB = Math.floor(hi / interval);
				for (let b = loB + 1; b <= hiB; b++) {
					const t = (b * interval - lo) / Math.max(hi - lo, 1);
					const cy = y + (elev < e2 ? t : 1 - t) * cellH;
					ctx.beginPath(); ctx.moveTo(x, cy); ctx.lineTo(x + cellW, cy); ctx.stroke();
				}
			}
		}
	}

	// 主等高线 (200m 间隔, 更粗)
	ctx.strokeStyle = 'rgba(0,0,0,0.32)';
	ctx.lineWidth = 1.0;
	const majorInterval = 200;
	for (let row = 0; row < size; row++) {
		for (let col = 0; col < size; col++) {
			const elev = terrainMap.grid[row][col].elevation || 0;
			const x = col * cellW, y = row * cellH;
			if (col + 1 < size) {
				const e2 = terrainMap.grid[row][col + 1].elevation || 0;
				const lo = Math.min(elev, e2), hi = Math.max(elev, e2);
				const loB = Math.floor(lo / majorInterval), hiB = Math.floor(hi / majorInterval);
				for (let b = loB + 1; b <= hiB; b++) {
					const t = (b * majorInterval - lo) / Math.max(hi - lo, 1);
					const cx = x + (elev < e2 ? t : 1 - t) * cellW;
					ctx.beginPath(); ctx.moveTo(cx, y); ctx.lineTo(cx, y + cellH); ctx.stroke();
				}
			}
			if (row + 1 < size) {
				const e2 = terrainMap.grid[row + 1][col].elevation || 0;
				const lo = Math.min(elev, e2), hi = Math.max(elev, e2);
				const loB = Math.floor(lo / majorInterval), hiB = Math.floor(hi / majorInterval);
				for (let b = loB + 1; b <= hiB; b++) {
					const t = (b * majorInterval - lo) / Math.max(hi - lo, 1);
					const cy = y + (elev < e2 ? t : 1 - t) * cellH;
					ctx.beginPath(); ctx.moveTo(x, cy); ctx.lineTo(x + cellW, cy); ctx.stroke();
				}
			}
		}
	}

	// 不可通行标记
	for (let row = 0; row < size; row++) {
		for (let col = 0; col < size; col++) {
			const cell = terrainMap.grid[row][col];
			if (cell.passable === false) {
				const x = col * cellW, y = row * cellH;
				ctx.fillStyle = 'rgba(255,40,40,0.18)';
				ctx.fillRect(x, y, cellW, cellH);
				ctx.strokeStyle = 'rgba(255,80,80,0.4)';
				ctx.lineWidth = 0.8;
				ctx.beginPath();
				ctx.moveTo(x + cellW*0.15, y + cellH*0.15);
				ctx.lineTo(x + cellW*0.85, y + cellH*0.85);
				ctx.moveTo(x + cellW*0.85, y + cellH*0.15);
				ctx.lineTo(x + cellW*0.15, y + cellH*0.85);
				ctx.stroke();
			}
		}
	}
	ctx.restore();
}

function elevationToColor(elev) {
	if (elev <= 60)  return '#3b7fc4';
	if (elev <= 120) return '#7ec850';
	if (elev <= 200) return '#8bc34a';
	if (elev <= 300) return '#c5a843';
	if (elev <= 450) return '#b8954a';
	if (elev <= 650) return '#8b6b45';
	return '#8a8a8a';
}

async function generateMap() {
	const size = parseInt(document.getElementById('map-size').value);
	const useLLM = document.getElementById('map-use-llm').checked;
	logEntry('L1', '=== 生成地形地图: ' + size + 'x' + size + (useLLM ? ' (AI)' : ' (程序化)') + ' ===');
	try {
		const r = await fetch(API + '/map/generate', {
			method: 'POST',
			headers: {'Content-Type': 'application/json'},
			body: JSON.stringify({size: size, use_llm: useLLM})
		});
		const data = await r.json();
		terrainMap = data;
		// 缩放到地图适配视口
		const mapFitW = (canvas.width / devicePixelRatio) / (data.size || 16);
		const mapFitH = (canvas.height / devicePixelRatio) / (data.size || 16);
		targetZoom = Math.max(0.15, Math.min(mapFitW, mapFitH) * 0.88);
		targetPanX = 0; targetPanY = 0;
		logEntry('L1', '地形图已生成: ' + size + 'x' + size + ', ' + (data.total_cells || size*size) + ' 个单元格');
		renderLegend();
		if (agents.length > 0) { await placeAgentsRandomly(); }
	} catch(e) {
		logEntry('L1', '地图生成失败: ' + e.message);
	}
}

function renderLegend() {
	const el = document.getElementById('map-legend');
	if (!terrainMap) { el.innerHTML = ''; return; }
	const samples = [
		{label:'低洼', color:'#3b7fc4'}, {label:'平原', color:'#7ec850'},
		{label:'浅绿', color:'#8bc34a'}, {label:'丘陵', color:'#c5a843'},
		{label:'高地', color:'#b8954a'}, {label:'山地', color:'#8b6b45'},
		{label:'高峰', color:'#8a8a8a'}
	];
	el.innerHTML = samples.map(s =>
		'<span class="legend-swatch" style="background:' + s.color + '" title="' + s.label + '"></span>' + s.label
	).join(' ');
}


async function placeAgentsRandomly() {
	if (!terrainMap) return;
	try {
		const r = await fetch(API + '/map/agents/place-random', {
			method: 'POST',
			headers: {'Content-Type': 'application/json'},
			body: JSON.stringify({agent_ids: agents.map(a => a.agent_id)})
		});
		const data = await r.json();
		logEntry('L1', '已部署 ' + data.placed + ' 个 Agent 到地图');
		if (ws && ws.readyState === WebSocket.OPEN) ws.send('all');
	} catch(e) {
		logEntry('L1', '部署失败: ' + e.message);
	}
}

async function toggleSimulationTick() {
	tickRunning = !tickRunning;
	const btn = document.getElementById('tick-btn');
	btn.textContent = tickRunning ? '停止推演' : '开始推演';
	btn.classList.toggle('accent', tickRunning);
	if (!tickRunning) return;
	
	async function tick() {
		if (!tickRunning) return;
		try {
			const r = await fetch(API + '/map/tick', {method: 'POST'});
			const data = await r.json();
			if (data.agents && terrainMap) {
				const W = canvas.width / devicePixelRatio;
				const H = canvas.height / devicePixelRatio;
				const cellW = W / (terrainMap.size || 1);
				const cellH = H / (terrainMap.size || 1);
				data.agents.forEach(as => {
					const local = agents.find(a => a.agent_id === as.agent_id);
					if (local) {
						local.x = as.x;
						local.y = as.y;
						local._targetX = as.x * cellW + cellW / 2;
						local._targetY = as.y * cellH + cellH / 2;
					}
				});
				logEntry('L3', 'Tick: ' + data.agents.length + ' agents updated');
			}
		} catch(e) { /* ignore tick errors */ }
		setTimeout(tick, 1500);
	}
	tick();
}

// ============== Start ==============
render();
logEntry('L1', '控制台就绪');
loadSettings();
setTimeout(() => { if (ws && ws.readyState === WebSocket.OPEN) ws.send('all'); }, 500);
