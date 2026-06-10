import { useEffect, useState, useCallback } from 'react';

interface AgentInfo {
  agent_id: string; name: string; role: string; status: string;
  x: number; y: number;
  skills?: string[]; tags?: string[];
  pending_task_descs?: string[];
  extra_meta?: Record<string, any>;
}

interface Relationship {
  from: string; to: string;
  relation_type?: string;
  value?: number;
  can_direct_chat?: boolean;
}

const STATUS_COLORS: Record<string, string> = {
  idle: '#6B8A5E', running: '#5A7A9A', paused: '#B8783A',
  stopped: '#C0392B', error: '#C0392B', created: '#8B8475',
};

/** Persistent simulation state (survives re-renders) */
let _simState: Map<string, { x: number; y: number }> | null = null;
let _relRef: Relationship[] = [];

/** 导出: 用 _simState 检测哪个 Agent 在指定世界坐标下 (供拖动/悬停使用) */
export function findAgentAt(worldX: number, worldY: number, agents: AgentInfo[], radius: number = 18): AgentInfo | null {
  for (let i = agents.length - 1; i >= 0; i--) {
    const a = agents[i];
    if (a.x === undefined || a.y === undefined) continue;
    const sp = _simState?.get(a.agent_id);
    const ax = sp ? sp.x : a.x;
    const ay = sp ? sp.y : a.y;
    if (Math.abs(worldX - ax) < radius && Math.abs(worldY - ay) < radius) return a;
  }
  return null;
}

/** 导出: 直接更新 Agent 渲染位置 (拖动时调用) */
export function moveAgent(agentId: string, worldX: number, worldY: number) {
  if (!_simState) _simState = new Map();
  _simState.set(agentId, { x: worldX, y: worldY });
}

export function useAgentOverlay() {
  const [agents, setAgents] = useState<AgentInfo[]>([]);
  const [relationships, setRelationships] = useState<Relationship[]>([]);
  const [hovered, setHovered] = useState<AgentInfo | null>(null);
  const [selected, setSelected] = useState<AgentInfo | null>(null);
  const [mousePos, setMousePos] = useState<{x:number;y:number}|null>(null);
  const [draggingId, setDraggingId] = useState<string | null>(null);

  // Receive agent data from parent via postMessage
  useEffect(() => {
    const handler = (e: MessageEvent) => {
      if (e.data?.type === 'agents') {
        setAgents(e.data.data || []);
        setRelationships(e.data.relationships || []);
      }
    };
    window.addEventListener('message', handler);
    return () => window.removeEventListener('message', handler);
  }, []);

  const handleMouse = useCallback((x: number, y: number, wx: number, wy: number, zoom: number) => {
    setMousePos({x, y});
    let found: AgentInfo | null = null;
    const rPx = Math.min(12, 6 / zoom);
    for (let i = agents.length - 1; i >= 0; i--) {
      const a = agents[i];
      if (a.x === undefined || a.y === undefined) continue;
      const sp = _simState?.get(a.agent_id);
      const ax = sp ? sp.x : a.x;
      const ay = sp ? sp.y : a.y;
      const dx = wx - ax, dy = wy - ay;
      if (Math.sqrt(dx*dx + dy*dy) < rPx + 3/zoom) { found = a; break; }
    }
    setHovered(found);
  }, [agents]);

  const handleClick = useCallback(() => {
    setSelected(hovered);
  }, [hovered]);

  // ── 拖动 ──
  const handleDragStart = useCallback((worldX: number, worldY: number) => {
    const found = findAgentAt(worldX, worldY, agents, 20);
    if (found) { setDraggingId(found.agent_id); setSelected(found); }
  }, [agents]);

  const handleDragMove = useCallback((worldX: number, worldY: number) => {
    if (draggingId) moveAgent(draggingId, worldX, worldY);
  }, [draggingId]);

  const handleDragEnd = useCallback(() => {
    if (draggingId && _simState) {
      const pos = _simState.get(draggingId);
      if (pos) {
        window.parent.postMessage({ type: 'agent-move', agent_id: draggingId, x: Math.round(pos.x), y: Math.round(pos.y) }, '*');
      }
    }
    setDraggingId(null);
  }, [draggingId]);

  return { agents, relationships, hovered, selected, mousePos, draggingId, handleMouse, handleClick, handleDragStart, handleDragMove, handleDragEnd };
}

/** Draw relationship links between agents */
export function drawRelationships(
  ctx: CanvasRenderingContext2D,
  relationships: Relationship[],
  agents: AgentInfo[],
) {
  _relRef = relationships;
  if (!relationships.length || !agents.length) return;

  const agentMap = new Map(agents.map(a => [a.agent_id, a]));
  const getPos = (id: string): {x:number;y:number}|null => {
    const sp = _simState?.get(id);
    if (sp) return sp;
    const a = agentMap.get(id);
    return (a && a.x != null) ? { x: a.x, y: a.y } : null;
  };

  ctx.save();
  ctx.lineCap = 'round';

  for (const rel of relationships) {
    const from = getPos(rel.from.toLowerCase());
    const to = getPos(rel.to.toLowerCase());
    if (!from || !to) continue;

    const isCooperative = (rel.value || 0) > 0;
    const alpha = Math.min(1, Math.abs(rel.value || 50) / 100 + 0.15);
    const color = isCooperative
      ? `rgba(107,138,94,${alpha.toFixed(2)})`
      : `rgba(192,57,43,${alpha.toFixed(2)})`;

    ctx.beginPath();
    ctx.moveTo(from.x, from.y);
    ctx.lineTo(to.x, to.y);

    if (rel.can_direct_chat) {
      ctx.setLineDash([4, 3]);
      ctx.lineWidth = 1.0;
    } else {
      ctx.setLineDash([3, 4]);
      ctx.lineWidth = 0.6;
    }
    ctx.strokeStyle = color;
    ctx.stroke();

    if (rel.relation_type) {
      const mx = (from.x + to.x) / 2;
      const my = (from.y + to.y) / 2;
      ctx.fillStyle = '#88847F';
      ctx.font = '5px Inter,IBM Plex Sans,system-ui,sans-serif';
      ctx.textAlign = 'center';
      ctx.fillText(rel.relation_type, mx, my - 5);
      ctx.textAlign = 'start';
    }
  }
  ctx.setLineDash([]);
  ctx.restore();
}

/** Draw agents onto the shared canvas context (in world coordinates).
 *  Called inside a ctx.translate(screenX,screenY) + ctx.scale(screenW/worldW,...) transform.
 */
export function drawAgents(
  ctx: CanvasRenderingContext2D,
  agents: AgentInfo[],
  selected: AgentInfo | null,
  worldWidth: number,
  _canvasW: number, _canvasH: number,
  draggingId: string | null = null,
) {
  if (!agents.length) return;
  const r = Math.max(2, Math.min(10, worldWidth / 50));

  // ── Force simulation (persistent across frames) ──
  if (!_simState) _simState = new Map<string, { x: number; y: number }>();
  const pos = _simState;

  // Init new agents at server position
  for (const a of agents) {
    if (a.x === undefined || a.y === undefined) continue;
    if (!pos.has(a.agent_id)) {
      pos.set(a.agent_id, { x: a.x, y: a.y });
    }
  }
  // Remove stale agents
  const activeIds = new Set(agents.map(a => a.agent_id));
  for (const id of pos.keys()) { if (!activeIds.has(id)) pos.delete(id); }

  // Build adjacency map from relationship links
  const adj = new Map<string, Set<string>>();
  for (const rel of _relRef) {
    const f = rel.from.toLowerCase();
    const t = rel.to.toLowerCase();
    if (!adj.has(f)) adj.set(f, new Set());
    if (!adj.has(t)) adj.set(t, new Set());
    adj.get(f)!.add(t); adj.get(t)!.add(f);
  }

  const margin = r * 2;
  const entries = Array.from(pos.entries());
  const n = entries.length;
  const minDist = r * 10;
  const damping = 0.12;

  for (let i = 0; i < n; i++) {
    const [id, pi] = entries[i];
    // 拖动中的 agent 不参与力模拟
    if (id === draggingId) continue;
    let fx = 0, fy = 0;
    const neighbors = adj.get(id);

    for (let j = 0; j < n; j++) {
      if (i === j) continue;
      const [jid, pj] = entries[j];
      const dx = pi.x - pj.x;
      const dy = pi.y - pj.y;
      const d = Math.sqrt(dx * dx + dy * dy) || 0.01;
      const isLinked = neighbors?.has(jid);

      if (d < minDist) {
        const force = (minDist - d) / minDist * (isLinked ? 2 : 1);
        fx += (dx / d) * force;
        fy += (dy / d) * force;
      } else if (isLinked && d < minDist * 4) {
        fx -= dx * 0.02;
        fy -= dy * 0.02;
      }
    }

    pi.x += fx * damping;
    pi.y += fy * damping;
    pi.x = Math.max(margin, Math.min(worldWidth - margin, pi.x));
    pi.y = Math.max(margin, Math.min(worldWidth - margin, pi.y));
  }

  for (let i = 0; i < agents.length; i++) {
    const a = agents[i];
    if (a.x === undefined || a.y === undefined) continue;
    const p = pos.get(a.agent_id);
    if (!p) continue;
    const color = STATUS_COLORS[a.status] || '#8B8475';

    const sx = p.x;
    const sy = p.y;

    // Selection ring
    if (selected?.agent_id === a.agent_id) {
      ctx.beginPath(); ctx.arc(sx, sy, r + 2, 0, Math.PI * 2);
      ctx.strokeStyle = color; ctx.lineWidth = 1.5 / (ctx as any)._scale_hint || 1;
      ctx.setLineDash([2, 2]); ctx.stroke(); ctx.setLineDash([]);
    }

    // Body
    ctx.beginPath(); ctx.arc(sx, sy, r, 0, Math.PI * 2);
    ctx.fillStyle = color + 'cc'; ctx.fill();
    ctx.strokeStyle = color; ctx.lineWidth = 0.8; ctx.stroke();

    // Label
    ctx.fillStyle = '#2A2A2A';
    const fontSize = Math.max(4, Math.min(5.5, r * 0.55));
    ctx.font = `${fontSize}px Inter,IBM Plex Sans,system-ui,sans-serif`;
    ctx.textAlign = 'center';
    ctx.fillText(a.name || a.agent_id, sx, sy - r - 4);
    ctx.textAlign = 'start';
  }
}
