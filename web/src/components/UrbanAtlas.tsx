import { useEffect, useRef, useCallback, useState } from 'react';
import type { CityWorld, CityStats } from '../types/urban';
import { UrbanRenderer } from '../rendering/UrbanRenderer';
import { useMapControls } from '../hooks/useMapControls';
import { useAnimationLoop } from '../hooks/useAnimationLoop';
import { useAgentOverlay, drawAgents, drawRelationships, findAgentAt, moveAgent } from './AgentLayer';

interface UrbanAtlasProps {
  world: CityWorld;
  onStats?: (stats: CityStats) => void;
  showMap?: boolean;
}

export function UrbanAtlas({ world, onStats, showMap = true }: UrbanAtlasProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const rendererRef = useRef<UrbanRenderer>(new UrbanRenderer());
  const renderer = rendererRef.current;
  const [, setCursorWorld] = useState<{ x: number; y: number } | null>(null);

  const {
    viewport, updateViewportSize,
    handleWheel, handleMouseDown, handleMouseMove, handleMouseUp, handleDoubleClick, fitToMap,
  } = useMapControls({ canvasWidth: 1200, canvasHeight: 800 });

  // Agent overlay (same canvas)
  const { agents, relationships, hovered, selected, draggingId, handleMouse, handleClick, handleDragStart, handleDragMove, handleDragEnd } = useAgentOverlay();

  // Send hovered agent info + mouse position to parent for tooltip
  const lastMouseScreen = useRef({ x: 0, y: 0 });
  useEffect(() => {
    if (window.parent !== window) {
      window.parent.postMessage({
        type: 'agent-hover',
        data: hovered,
        mx: lastMouseScreen.current.x,
        my: lastMouseScreen.current.y,
      }, '*');
    }
  }, [hovered]);

  // Initialize renderer
  useEffect(() => { renderer.initialize(world); }, [world, renderer]);

  // Push stats to parent
  useEffect(() => { onStats?.(world.stats); }, [world.stats, onStats]);

  // Fit on load
  useEffect(() => { fitToMap(); }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // Attach wheel listener with { passive: false }
  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const onWheel = (e: WheelEvent) => { handleWheel(e as unknown as React.WheelEvent); };
    el.addEventListener('wheel', onWheel, { passive: false });
    return () => el.removeEventListener('wheel', onWheel);
  }, [handleWheel]);

  // Resize
  const resize = useCallback(() => {
    const container = containerRef.current;
    const canvas = canvasRef.current;
    if (!container || !canvas) return;
    const rect = container.getBoundingClientRect();
    const dpr = window.devicePixelRatio || 1;
    canvas.width = rect.width * dpr;
    canvas.height = rect.height * dpr;
    canvas.style.width = rect.width + 'px';
    canvas.style.height = rect.height + 'px';
    updateViewportSize(rect.width, rect.height);
  }, [updateViewportSize]);

  useEffect(() => {
    resize();
    window.addEventListener('resize', resize);
    return () => window.removeEventListener('resize', resize);
  }, [resize]);

  // Render loop: map → agents (on same canvas, same coordinate system)
  const render = useCallback(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;
    const dpr = window.devicePixelRatio || 1;
    const W = canvas.width / dpr;
    const H = canvas.height / dpr;

    ctx.save();
    ctx.scale(dpr, dpr);

    // Step 1: render map (skip if toggled off)
    if (showMap) {
      renderer.render(ctx, world, viewport.zoom, viewport.panX, viewport.panY, W, H);
    } else {
      ctx.fillStyle = '#ECE8DF';
      ctx.fillRect(0, 0, W, H);
    }

    // Step 2: render agents in world coordinates
    // Atlas renders world 0..400 into screen via: screenX = wx/WORLD * zoom * canvasW + canvasW/2 + panX - zoom*canvasW/2
    // We apply the same transform so agents are drawn in world space
    const atlasScale = viewport.zoom * (W / 1024);  // match UrbanRenderer ATLAS_RES=1024
    const screenW = 1024 * atlasScale;
    const screenH = 1024 * atlasScale;
    const screenX = W / 2 + viewport.panX - screenW / 2;
    const screenY = H / 2 + viewport.panY - screenH / 2;

    ctx.save();
    ctx.beginPath();
    ctx.rect(screenX, screenY, screenW, screenH);
    ctx.clip();  // clip to atlas bounds
    ctx.translate(screenX, screenY);
    ctx.scale(screenW / world.width, screenH / world.height);
    drawAgents(ctx, agents, selected, world.width, screenW, screenH, draggingId);
    drawRelationships(ctx, relationships, agents);
    ctx.restore();

    ctx.restore();
  }, [world, viewport, renderer, agents, relationships, selected, showMap]);

  useAnimationLoop(render, true);

  const toWorld = useCallback((mx: number, my: number, rect: DOMRect) => {
    const W = rect.width;
    const H = rect.height;
    const atlasScale = viewport.zoom * (W / 1024);
    const screenW = 1024 * atlasScale;
    const screenH = 1024 * atlasScale;
    const sx = W / 2 + viewport.panX - screenW / 2;
    const sy = H / 2 + viewport.panY - screenH / 2;
    return { wx: (mx - sx) / screenW * world.width, wy: (my - sy) / screenH * world.height };
  }, [viewport, world]);

  const handleMouseMoveUrban = useCallback((e: React.MouseEvent) => {
    const rect = canvasRef.current?.getBoundingClientRect();
    if (!rect) return;
    const mx = e.clientX - rect.left;
    const my = e.clientY - rect.top;
    lastMouseScreen.current = { x: e.clientX, y: e.clientY };
    const { wx, wy } = toWorld(mx, my, rect);
    setCursorWorld({ x: wx, y: wy });
    if (draggingId) {
      handleDragMove(wx, wy);
    } else {
      handleMouseMove(e);
      handleMouse(mx, my, wx, wy, viewport.zoom);
    }
  }, [handleMouseMove, viewport, world, handleMouse, draggingId, handleDragMove, toWorld]);

  const handleMouseDownUrban = useCallback((e: React.MouseEvent) => {
    if (e.button !== 0) return handleMouseDown(e);
    const rect = canvasRef.current?.getBoundingClientRect();
    if (!rect) return;
    const mx = e.clientX - rect.left;
    const my = e.clientY - rect.top;
    const { wx, wy } = toWorld(mx, my, rect);
    if (findAgentAt(wx, wy, agents, 20)) {
      handleDragStart(wx, wy);
    } else {
      handleMouseDown(e);
    }
  }, [agents, handleMouseDown, handleDragStart, toWorld]);

  const handleMouseUpUrban = useCallback((e: React.MouseEvent) => {
    if (draggingId) handleDragEnd();
    handleMouseUp(e);
  }, [draggingId, handleDragEnd, handleMouseUp]);

  return (
    <div
      ref={containerRef}
      className="flex-1 relative overflow-hidden cursor-grab active:cursor-grabbing"
      onMouseDown={handleMouseDownUrban}
      onMouseMove={handleMouseMoveUrban}
      onMouseUp={handleMouseUpUrban}
      onMouseLeave={handleMouseUpUrban}
      onDoubleClick={handleDoubleClick}
      onClick={() => agents.length > 0 && handleClick()}
    >
      <canvas ref={canvasRef} className="absolute inset-0" />
    </div>
  );
}
