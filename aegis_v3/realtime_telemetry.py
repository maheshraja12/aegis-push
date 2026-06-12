"""
==============================================================================
Aegis V3: Enterprise Autonomous Infrastructure Resilience Engine
aegis_v3/realtime_telemetry.py — FastAPI Real-Time Infrastructure Dashboard
==============================================================================

PURPOSE
-------
Executive-grade, real-time infrastructure monitoring portal. Every sub-system
event (cluster elections, proof completions, sandbox results, stage timings)
is streamed via WebSocket to a fully animated, Tailwind CSS dashboard.

BACKEND ARCHITECTURE
--------------------
  FastAPI application with:
    /              → Serves the HTML dashboard (inline Tailwind + JS)
    /api/status    → REST snapshot of current system state
    /api/pipeline/run  → POST to trigger a new Aegis V3 pipeline
    /ws/telemetry  → WebSocket: streams TelemetryEvent JSON frames
    /ws/metrics    → WebSocket: streams ResourceSnapshot every 1 second

  The pipeline runs as a background asyncio task. A broadcast queue
  distributes events to all connected WebSocket clients simultaneously.

FRONTEND DESIGN
---------------
  Single-page application using:
    - Tailwind CSS CDN for all styling (dark slate theme)
    - Vanilla JavaScript (no bundler, no React)
    - WebSocket client for live event streaming
    - CSS animations: pulsing LED status indicators, sliding bars

  Panels:
    [1] Cluster Health     — Real-time Raft node roles (Leader/Follower/Offline)
    [2] Pipeline Progress  — 8-stage progress tracker with microsecond timings
    [3] Formal Proofs      — Live proof tree updates (PROVED / REFUTED / UNKNOWN)
    [4] Wasm Sandbox       — Compile/execute timing + memory gauge
    [5] Resource Monitor   — CPU%, RAM%, active tasks, event queue depth
    [6] Event Feed         — Scrolling live event log (last 50 events)
    [7] Controls           — "Run Pipeline" + "Simulate Chaos" buttons

==============================================================================
"""
from __future__ import annotations

import asyncio
import datetime
import json
import logging
import os
import sys
import time
import uuid
from contextlib import asynccontextmanager
from typing import Any, Optional

import psutil
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware

from aegis_v3.schema_v3 import (
    ClusterStateLog,
    IncidentSeverity,
    NodeRole,
    PipelineResult,
    ResourceSnapshot,
    TelemetryEvent,
    TelemetryEventType,
)

logger = logging.getLogger("aegis.telemetry")

# ---------------------------------------------------------------------------
# Global state (singleton per process)
# ---------------------------------------------------------------------------

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Broadcast queue: all TelemetryEvents go here → pushed to all WS clients
_broadcast_q: asyncio.Queue[TelemetryEvent] = asyncio.Queue(maxsize=500)

# Active WebSocket connections (for both /ws/telemetry and /ws/metrics)
_telemetry_clients: set[WebSocket] = set()
_metrics_clients: set[WebSocket] = set()

# Latest pipeline result
_last_pipeline: Optional[PipelineResult] = None
_pipeline_running: bool = False
_start_time = time.monotonic()

# Cluster state cache
_cluster_snapshot: dict[str, Any] = {}


# ---------------------------------------------------------------------------
# Lifespan (startup / shutdown background tasks)
# ---------------------------------------------------------------------------

@asynccontextmanager
async def _lifespan(app: FastAPI):
    """Start background broadcast + metrics tasks on startup."""
    broadcast_task = asyncio.create_task(_broadcast_loop(), name="broadcast-loop")
    metrics_task   = asyncio.create_task(_metrics_loop(), name="metrics-loop")
    yield
    broadcast_task.cancel()
    metrics_task.cancel()
    await asyncio.gather(broadcast_task, metrics_task, return_exceptions=True)


# ---------------------------------------------------------------------------
# FastAPI App
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Aegis V3 — Infrastructure Resilience Engine",
    description="Real-time monitoring portal for the Autonomous Self-Healing Infrastructure",
    version="3.0.0",
    lifespan=_lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Background: broadcast TelemetryEvents to all connected WS clients
# ---------------------------------------------------------------------------

async def _broadcast_loop() -> None:
    """Drain the broadcast queue and push events to all telemetry WebSockets."""
    global _telemetry_clients
    while True:
        try:
            event: TelemetryEvent = await asyncio.wait_for(
                _broadcast_q.get(), timeout=1.0
            )
            payload = event.model_dump_json()
            dead: set[WebSocket] = set()
            for ws in list(_telemetry_clients):
                try:
                    await ws.send_text(payload)
                except Exception:
                    dead.add(ws)
            _telemetry_clients -= dead
        except asyncio.TimeoutError:
            pass
        except asyncio.CancelledError:
            return
        except Exception as exc:
            logger.error(f"Broadcast loop error: {exc}")


async def _metrics_loop() -> None:
    """Push ResourceSnapshot to all metrics WebSocket clients every 1 second."""
    global _metrics_clients
    proc = psutil.Process()
    while True:
        try:
            await asyncio.sleep(1.0)
            snap = ResourceSnapshot(
                cpu_percent=psutil.cpu_percent(interval=None),
                memory_mb=proc.memory_info().rss / 1_048_576,
                memory_percent=psutil.virtual_memory().percent,
                active_tasks=len([t for t in asyncio.all_tasks() if not t.done()]),
                event_queue_depth=_broadcast_q.qsize(),
                uptime_seconds=time.monotonic() - _start_time,
            )
            payload = snap.model_dump_json()
            dead: set[WebSocket] = set()
            for ws in list(_metrics_clients):
                try:
                    await ws.send_text(payload)
                except Exception:
                    dead.add(ws)
            _metrics_clients -= dead
        except asyncio.CancelledError:
            return
        except Exception as exc:
            logger.error(f"Metrics loop error: {exc}")


def _enqueue(event: TelemetryEvent) -> None:
    """Non-blocking enqueue of a TelemetryEvent."""
    try:
        _broadcast_q.put_nowait(event)
    except asyncio.QueueFull:
        pass  # Drop if queue is full (back-pressure)


# ---------------------------------------------------------------------------
# REST Endpoints
# ---------------------------------------------------------------------------

@app.get("/api/status", response_class=JSONResponse)
async def get_status() -> dict:
    """Return current system status snapshot."""
    proc = psutil.Process()
    return {
        "system": "Aegis V3 — Prometheus",
        "status": "RUNNING_PIPELINE" if _pipeline_running else "IDLE",
        "uptime_seconds": round(time.monotonic() - _start_time, 2),
        "cpu_percent": psutil.cpu_percent(interval=None),
        "memory_mb": round(proc.memory_info().rss / 1_048_576, 2),
        "active_ws_telemetry": len(_telemetry_clients),
        "active_ws_metrics": len(_metrics_clients),
        "queue_depth": _broadcast_q.qsize(),
        "last_pipeline": _last_pipeline.model_dump() if _last_pipeline else None,
        "cluster": _cluster_snapshot,
        "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
    }


@app.get("/health", response_class=JSONResponse, tags=["ops"])
async def health_check() -> dict:
    """
    Liveness probe — returns 200 OK if the server is running.

    Compatible with: Kubernetes liveness probes, nginx health checks,
    AWS ALB target group health checks, Datadog synthetics.
    """
    proc = psutil.Process()
    return {
        "status": "ok",
        "service": "aegis-v3",
        "uptime_seconds": round(time.monotonic() - _start_time, 2),
        "pipeline_running": _pipeline_running,
        "cpu_percent": round(psutil.cpu_percent(interval=None), 1),
        "memory_mb": round(proc.memory_info().rss / 1_048_576, 1),
        "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
    }


@app.get("/ready", response_class=JSONResponse, tags=["ops"])
async def readiness_check() -> dict:
    """
    Readiness probe — returns 200 if all subsystems are importable and ready.

    Returns 503 if any critical import fails.
    """
    checks: dict[str, str] = {}
    all_ok = True

    critical_modules = [
        ("aegis_v3.wasm_sandbox",          "wasm_sandbox"),
        ("aegis_v3.distributed_consensus", "raft_consensus"),
        ("aegis_v3.formal_verification",   "formal_verification"),
        ("aegis_v3.persistence",           "audit_persistence"),
        ("aegis_v3.chaos_monkey",          "chaos_engine"),
    ]
    for module_path, check_name in critical_modules:
        try:
            __import__(module_path)
            checks[check_name] = "ok"
        except Exception as exc:
            checks[check_name] = f"FAIL: {exc}"
            all_ok = False

    status_code = 200 if all_ok else 503
    from fastapi.responses import JSONResponse as _JSONResponse
    return _JSONResponse(
        status_code=status_code,
        content={
            "status": "ready" if all_ok else "degraded",
            "checks": checks,
            "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
        },
    )


@app.get("/api/history", response_class=JSONResponse, tags=["pipeline"])
async def get_pipeline_history(limit: int = 50) -> dict:
    """
    Fetch the last N pipeline run records from the persistent audit log.

    Returns runs in reverse chronological order (newest first).
    This endpoint survives server restarts because records are stored in SQLite.
    """
    try:
        from aegis_v3.persistence import AuditLogBackend
        backend = AuditLogBackend()
        await backend.initialize()
        runs = await backend.get_recent_runs(limit=max(1, min(limit, 500)))
        return {
            "count": len(runs),
            "limit": limit,
            "runs": runs,
            "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
        }
    except Exception as exc:
        logger.error(f"/api/history error: {exc}")
        return {
            "count": 0,
            "limit": limit,
            "runs": [],
            "error": str(exc),
            "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
        }


@app.get("/api/mttr/stats", response_class=JSONResponse, tags=["pipeline"])
async def get_mttr_stats() -> dict:
    """
    Return aggregate MTTR (Mean Time To Recovery) statistics across all
    chaos-injected incidents that were resolved by Aegis V3.
    """
    try:
        from aegis_v3.persistence import AuditLogBackend
        backend = AuditLogBackend()
        await backend.initialize()
        stats = await backend.get_mttr_stats()
        return {**stats, "timestamp": datetime.datetime.utcnow().isoformat() + "Z"}
    except Exception as exc:
        logger.error(f"/api/mttr/stats error: {exc}")
        return {"error": str(exc), "timestamp": datetime.datetime.utcnow().isoformat() + "Z"}


@app.post("/api/pipeline/run", response_class=JSONResponse)
async def trigger_pipeline(
    target_file: str = "dummy_app/payment.py",
    severity: str = "P1",
    simulation: bool = True,
    chaos: bool = False,
) -> dict:
    """Trigger a new Aegis V3 self-healing pipeline asynchronously."""
    global _pipeline_running

    if _pipeline_running:
        raise HTTPException(status_code=409, detail="A pipeline is already running.")

    sev = IncidentSeverity(severity) if severity in IncidentSeverity.__members__ else IncidentSeverity.P1
    run_id = str(uuid.uuid4())[:8].upper()

    _enqueue(TelemetryEvent(
        event_type=TelemetryEventType.PIPELINE_START,
        source="api",
        title="PIPELINE_START",
        detail=f"Run {run_id} triggered via API | target={target_file} | sev={sev.value} | chaos={chaos}",
        severity="INFO",
        data={"run_id": run_id, "target_file": target_file, "severity": sev.value, "chaos": chaos},
    ))

    asyncio.create_task(
        _run_pipeline_background(run_id, target_file, sev, simulation, chaos),
        name=f"pipeline-{run_id}",
    )

    return {"status": "triggered", "run_id": run_id, "severity": sev.value}


@app.post("/api/chaos/inject", response_class=JSONResponse)
async def inject_chaos() -> dict:
    """Inject a random chaos bug and trigger an immediate pipeline run."""
    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        raise HTTPException(
            status_code=400,
            detail="Chaos injection mode requires the OPENAI_API_KEY environment variable to be set for AI patch generation."
        )
    return await trigger_pipeline(
        target_file="dummy_app/payment.py",
        severity="P1",
        simulation=False,
        chaos=True,
    )


async def _run_pipeline_background(
    run_id: str,
    target_file: str,
    severity: IncidentSeverity,
    simulation: bool,
    chaos: bool = False,
) -> None:
    """Run the full Aegis V3 pipeline as a background task."""
    global _pipeline_running, _last_pipeline, _cluster_snapshot
    _pipeline_running = True

    try:
        from aegis_v3.orchestrator import AegisV3Orchestrator

        api_key = os.environ.get("OPENAI_API_KEY", "")
        orchestrator = AegisV3Orchestrator(
            repo_root=_REPO_ROOT,
            openai_api_key=api_key or None,
            simulation=simulation,
            node_count=5,
            telemetry_queue=_broadcast_q,
        )

        result = await orchestrator.run_full_pipeline(
            target_file=target_file,
            severity=severity,
            chaos=chaos,
        )
        _last_pipeline = result

        # Update cluster snapshot from CLUSTER_COORDINATION stage
        for stage in result.stages:
            if stage.stage.value == "CLUSTER_COORDINATION" and "nodes" in stage.data:
                _cluster_snapshot = stage.data

        _enqueue(TelemetryEvent(
            event_type=TelemetryEventType.PIPELINE_END,
            source=f"orchestrator/{run_id}",
            title="PIPELINE_END",
            detail=(
                f"{'SUCCESS' if result.success else 'FAILED'} | "
                f"total={result.total_duration_us:,.0f}us | "
                f"{result.summary}"
            ),
            severity="SUCCESS" if result.success else "CRITICAL",
            data={
                "run_id": result.run_id,
                "success": result.success,
                "total_us": result.total_duration_us,
                "branch": result.deployed_branch,
                "summary": result.summary,
                "stages": [
                    {
                        "stage": s.stage.value,
                        "success": s.success,
                        "duration_us": s.duration_us,
                    }
                    for s in result.stages
                ],
            },
        ))

    except Exception as exc:
        logger.error(f"Background pipeline [{run_id}] failed: {exc}", exc_info=True)
        _enqueue(TelemetryEvent(
            event_type=TelemetryEventType.ALERT,
            source=f"orchestrator/{run_id}",
            title="PIPELINE_ERROR",
            detail=str(exc)[:300],
            severity="CRITICAL",
        ))
    finally:
        _pipeline_running = False


# ---------------------------------------------------------------------------
# WebSocket Endpoints
# ---------------------------------------------------------------------------

@app.websocket("/ws/telemetry")
async def ws_telemetry(websocket: WebSocket) -> None:
    """Stream TelemetryEvent frames to the client."""
    await websocket.accept()
    _telemetry_clients.add(websocket)
    logger.info(f"Telemetry WS client connected ({len(_telemetry_clients)} total)")

    # Send current status immediately on connect
    await websocket.send_text(TelemetryEvent(
        event_type=TelemetryEventType.ALERT,
        source="server",
        title="CONNECTED",
        detail=f"Aegis V3 Telemetry Stream active | clients={len(_telemetry_clients)}",
        severity="INFO",
    ).model_dump_json())

    try:
        while True:
            # Keep connection alive — data comes via broadcast loop
            await asyncio.sleep(30)
            await websocket.send_text('{"ping":true}')
    except (WebSocketDisconnect, Exception):
        pass
    finally:
        _telemetry_clients.discard(websocket)
        logger.info(f"Telemetry WS client disconnected ({len(_telemetry_clients)} remaining)")


@app.websocket("/ws/metrics")
async def ws_metrics(websocket: WebSocket) -> None:
    """Stream ResourceSnapshot frames every second."""
    await websocket.accept()
    _metrics_clients.add(websocket)
    try:
        while True:
            await asyncio.sleep(60)  # heartbeat — real data from _metrics_loop
    except (WebSocketDisconnect, Exception):
        pass
    finally:
        _metrics_clients.discard(websocket)


# ---------------------------------------------------------------------------
# HTML Dashboard (inline — served at /)
# ---------------------------------------------------------------------------

DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Aegis V3 — Mission Control</title>
  <script src="https://cdn.tailwindcss.com"></script>
  <style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap');

    :root {
      --aegis-cyan: #22d3ee;
      --aegis-blue: #3b82f6;
      --aegis-green: #10b981;
      --aegis-red: #ef4444;
      --aegis-yellow: #f59e0b;
      --aegis-purple: #8b5cf6;
    }

    * { box-sizing: border-box; }
    body {
      font-family: 'Inter', sans-serif;
      background: #0a0e1a;
      color: #e2e8f0;
      min-height: 100vh;
    }

    .mono { font-family: 'JetBrains Mono', monospace; }

    /* Animated gradient header */
    .header-bg {
      background: linear-gradient(135deg, #0f172a 0%, #1e293b 40%, #0f2a3d 100%);
      border-bottom: 1px solid rgba(34,211,238,0.2);
    }

    /* Panel card */
    .panel {
      background: rgba(15,23,42,0.8);
      border: 1px solid rgba(51,65,85,0.6);
      border-radius: 12px;
      backdrop-filter: blur(12px);
      transition: border-color 0.3s;
    }
    .panel:hover { border-color: rgba(34,211,238,0.3); }
    .panel-title {
      font-size: 0.7rem;
      font-weight: 700;
      letter-spacing: 0.12em;
      text-transform: uppercase;
      color: var(--aegis-cyan);
    }

    /* LED status indicator */
    .led {
      width: 8px; height: 8px;
      border-radius: 50%;
      display: inline-block;
      margin-right: 6px;
    }
    .led-green  { background: var(--aegis-green);  box-shadow: 0 0 6px var(--aegis-green); animation: pulse-green 2s infinite; }
    .led-red    { background: var(--aegis-red);    box-shadow: 0 0 6px var(--aegis-red);   animation: pulse-red 1s infinite; }
    .led-yellow { background: var(--aegis-yellow); box-shadow: 0 0 6px var(--aegis-yellow); animation: pulse-yellow 1.5s infinite; }
    .led-cyan   { background: var(--aegis-cyan);   box-shadow: 0 0 6px var(--aegis-cyan);  animation: pulse-cyan 2s infinite; }
    .led-dim    { background: #475569; }

    @keyframes pulse-green  { 0%,100%{opacity:1} 50%{opacity:0.4} }
    @keyframes pulse-red    { 0%,100%{opacity:1} 50%{opacity:0.3} }
    @keyframes pulse-yellow { 0%,100%{opacity:1} 50%{opacity:0.5} }
    @keyframes pulse-cyan   { 0%,100%{opacity:1} 50%{opacity:0.4} }

    /* Progress bar */
    .bar-track {
      height: 6px; border-radius: 3px;
      background: rgba(51,65,85,0.8);
      overflow: hidden;
    }
    .bar-fill {
      height: 100%; border-radius: 3px;
      transition: width 0.5s ease;
    }

    /* Event log */
    #event-log {
      max-height: 280px;
      overflow-y: auto;
      scrollbar-width: thin;
      scrollbar-color: #334155 transparent;
    }
    .event-item {
      padding: 6px 10px;
      border-left: 2px solid transparent;
      margin-bottom: 3px;
      border-radius: 0 6px 6px 0;
      font-size: 0.72rem;
      transition: background 0.2s;
      animation: slideIn 0.3s ease;
    }
    .event-item:hover { background: rgba(51,65,85,0.3); }
    .event-SUCCESS  { border-left-color: var(--aegis-green); background: rgba(16,185,129,0.05); }
    .event-CRITICAL { border-left-color: var(--aegis-red);   background: rgba(239,68,68,0.05); }
    .event-INFO     { border-left-color: var(--aegis-cyan);  background: rgba(34,211,238,0.03); }
    .event-WARN     { border-left-color: var(--aegis-yellow); background: rgba(245,158,11,0.05); }
    @keyframes slideIn { from{opacity:0;transform:translateX(-8px)} to{opacity:1;transform:translateX(0)} }

    /* Stage status */
    .stage-row { display: flex; align-items: center; padding: 5px 0; border-bottom: 1px solid rgba(51,65,85,0.3); }
    .stage-name { font-size:0.73rem; color:#94a3b8; width:180px; flex-shrink:0; }
    .stage-time { font-size:0.7rem; color:#64748b; width:90px; text-align:right; flex-shrink:0; mono; }
    .stage-badge {
      font-size: 0.65rem; font-weight: 700; letter-spacing: 0.05em;
      padding: 1px 7px; border-radius: 10px; margin-left: 8px;
    }
    .badge-pass    { background: rgba(16,185,129,0.15); color: var(--aegis-green); border: 1px solid rgba(16,185,129,0.3); }
    .badge-fail    { background: rgba(239,68,68,0.15);  color: var(--aegis-red);   border: 1px solid rgba(239,68,68,0.3);  }
    .badge-pending { background: rgba(100,116,139,0.15);color: #64748b;            border: 1px solid rgba(100,116,139,0.3); }
    .badge-running { background: rgba(34,211,238,0.15); color: var(--aegis-cyan);  border: 1px solid rgba(34,211,238,0.3); animation: pulse-cyan 1s infinite; }

    /* Proof tree */
    .proof-node {
      font-size: 0.7rem; padding: 4px 8px;
      border-radius: 6px; margin-bottom: 4px;
      display: flex; align-items: center; gap: 8px;
    }
    .proof-PROVED  { background: rgba(16,185,129,0.1); border: 1px solid rgba(16,185,129,0.2); }
    .proof-REFUTED { background: rgba(239,68,68,0.1);  border: 1px solid rgba(239,68,68,0.2);  }
    .proof-UNKNOWN { background: rgba(245,158,11,0.1); border: 1px solid rgba(245,158,11,0.2); }

    /* Node chip */
    .node-chip {
      display: inline-flex; align-items: center;
      padding: 4px 10px; border-radius: 20px;
      font-size: 0.68rem; font-weight: 600; margin: 3px;
      transition: all 0.3s;
    }
    .node-LEADER   { background: rgba(34,211,238,0.15); border: 1px solid rgba(34,211,238,0.4); color: var(--aegis-cyan); }
    .node-FOLLOWER { background: rgba(100,116,139,0.1); border: 1px solid rgba(100,116,139,0.3); color: #94a3b8; }
    .node-CANDIDATE{ background: rgba(245,158,11,0.15); border: 1px solid rgba(245,158,11,0.4); color: var(--aegis-yellow); }
    .node-OFFLINE  { background: rgba(239,68,68,0.1);   border: 1px solid rgba(239,68,68,0.3);  color: #f87171; opacity:0.6; }

    /* Button */
    .btn-primary {
      background: linear-gradient(135deg, #0891b2, #0e7490);
      border: 1px solid rgba(34,211,238,0.4);
      color: white; border-radius: 8px;
      padding: 10px 24px; font-weight: 600; font-size: 0.85rem;
      cursor: pointer; transition: all 0.2s;
      box-shadow: 0 0 20px rgba(34,211,238,0.15);
    }
    .btn-primary:hover {
      background: linear-gradient(135deg, #0e7490, #155e75);
      box-shadow: 0 0 30px rgba(34,211,238,0.3);
      transform: translateY(-1px);
    }
    .btn-primary:active { transform: translateY(0); }
    .btn-primary:disabled { opacity: 0.4; cursor: not-allowed; transform: none; }
    .btn-danger {
      background: linear-gradient(135deg, #991b1b, #7f1d1d);
      border: 1px solid rgba(239,68,68,0.4);
      color: white; border-radius: 8px;
      padding: 10px 24px; font-weight: 600; font-size: 0.85rem;
      cursor: pointer; transition: all 0.2s;
    }
    .btn-danger:hover { transform: translateY(-1px); box-shadow: 0 0 20px rgba(239,68,68,0.3); }
    .btn-danger:disabled { opacity: 0.4; cursor: not-allowed; transform: none; }

    /* Metric card */
    .metric-card {
      background: rgba(15,23,42,0.6); border: 1px solid rgba(51,65,85,0.5);
      border-radius: 10px; padding: 14px;
    }
    .metric-value { font-size: 1.6rem; font-weight: 700; line-height: 1.1; }
    .metric-label { font-size: 0.65rem; color: #64748b; text-transform: uppercase; letter-spacing: 0.1em; margin-top: 2px; }

    /* Wasm gauge */
    .gauge-ring {
      width: 80px; height: 80px;
      border-radius: 50%;
      background: conic-gradient(var(--aegis-cyan) var(--pct, 0%), rgba(51,65,85,0.5) var(--pct, 0%));
      display: flex; align-items: center; justify-content: center;
      position: relative;
    }
    .gauge-ring::before {
      content: '';
      width: 60px; height: 60px;
      border-radius: 50%;
      background: #0a0e1a;
      position: absolute;
    }
    .gauge-val { position: relative; z-index: 1; font-size: 0.7rem; font-weight: 700; color: var(--aegis-cyan); text-align: center; }

    /* Scrollbar */
    ::-webkit-scrollbar { width: 5px; }
    ::-webkit-scrollbar-track { background: transparent; }
    ::-webkit-scrollbar-thumb { background: #334155; border-radius: 3px; }
  </style>
</head>
<body>

<!-- ═══════ HEADER ═══════════════════════════════════════════════════════ -->
<header class="header-bg px-6 py-4 flex items-center justify-between sticky top-0 z-50">
  <div class="flex items-center gap-4">
    <div class="w-8 h-8 rounded-lg bg-cyan-500/20 border border-cyan-500/40 flex items-center justify-center">
      <svg class="w-4 h-4 text-cyan-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z"/>
      </svg>
    </div>
    <div>
      <h1 class="text-sm font-bold text-white tracking-wide">AEGIS V3 <span class="text-cyan-400">PROMETHEUS</span></h1>
      <p class="text-xs text-slate-500">Autonomous Infrastructure Resilience Engine — Mission Control</p>
    </div>
  </div>
  <div class="flex items-center gap-6">
    <div class="flex items-center gap-2">
      <span class="led led-green" id="ws-led"></span>
      <span class="text-xs text-slate-400 mono" id="ws-status">Connecting...</span>
    </div>
    <div class="text-xs text-slate-500 mono" id="system-clock"></div>
  </div>
</header>

<!-- ═══════ MAIN GRID ════════════════════════════════════════════════════ -->
<main class="max-w-screen-2xl mx-auto px-4 py-5 grid grid-cols-12 gap-4">

  <!-- ─── Row 1: Metrics ─────────────────────────────────────────────── -->
  <div class="col-span-12 grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-6 gap-3">
    <div class="metric-card">
      <div class="metric-value text-cyan-400 mono" id="metric-cpu">--</div>
      <div class="metric-label">CPU Usage</div>
    </div>
    <div class="metric-card">
      <div class="metric-value text-purple-400 mono" id="metric-ram">--</div>
      <div class="metric-label">RAM (MB)</div>
    </div>
    <div class="metric-card">
      <div class="metric-value text-green-400 mono" id="metric-tasks">--</div>
      <div class="metric-label">Active Tasks</div>
    </div>
    <div class="metric-card">
      <div class="metric-value text-yellow-400 mono" id="metric-queue">--</div>
      <div class="metric-label">Event Queue</div>
    </div>
    <div class="metric-card">
      <div class="metric-value text-blue-400 mono" id="metric-uptime">--</div>
      <div class="metric-label">Uptime (s)</div>
    </div>
    <div class="metric-card">
      <div class="metric-value mono" id="metric-status"
           style="font-size:1rem;padding-top:4px">
        <span class="led led-dim" id="pipeline-led"></span>
        <span id="pipeline-status-text" class="text-slate-400">IDLE</span>
      </div>
      <div class="metric-label">Pipeline Status</div>
    </div>
  </div>

  <!-- ─── Row 2: Cluster + Pipeline + Controls ────────────────────────── -->

  <!-- Cluster Health -->
  <div class="col-span-12 lg:col-span-4 panel p-4">
    <div class="panel-title mb-3">Raft Cluster Health</div>
    <div id="cluster-nodes" class="flex flex-wrap gap-1 mb-3">
      <span class="text-slate-600 text-xs italic">Awaiting cluster data...</span>
    </div>
    <div class="grid grid-cols-3 gap-2 mt-3">
      <div class="text-center">
        <div class="text-lg font-bold text-cyan-400 mono" id="cluster-term">--</div>
        <div class="text-xs text-slate-500">Term</div>
      </div>
      <div class="text-center">
        <div class="text-lg font-bold text-green-400 mono" id="cluster-leader">--</div>
        <div class="text-xs text-slate-500">Leader</div>
      </div>
      <div class="text-center">
        <div class="text-lg font-bold text-yellow-400 mono" id="cluster-commit">--</div>
        <div class="text-xs text-slate-500">Commit Idx</div>
      </div>
    </div>
  </div>

  <!-- Pipeline Progress -->
  <div class="col-span-12 lg:col-span-5 panel p-4">
    <div class="panel-title mb-3">Pipeline Execution</div>
    <div id="pipeline-stages">
      <!-- Populated by JS -->
    </div>
    <div class="mt-3 pt-2 border-t border-slate-700/50 flex justify-between text-xs">
      <span class="text-slate-500">Total Time</span>
      <span class="text-cyan-400 mono font-bold" id="pipeline-total-us">-- us</span>
    </div>
  </div>

  <!-- Controls -->
  <div class="col-span-12 lg:col-span-3 panel p-4 flex flex-col gap-3">
    <div class="panel-title mb-1">Mission Controls</div>
    <button class="btn-primary" id="btn-run" onclick="triggerPipeline()">
      ▶ Run Healing Pipeline
    </button>
    <button class="btn-danger" id="btn-chaos" onclick="injectChaos()">
      ⚡ Inject Chaos Bug
    </button>
    <div class="mt-2 p-3 rounded-lg bg-slate-900/50 border border-slate-700/50">
      <div class="text-xs text-slate-500 mb-1">Last Run ID</div>
      <div class="mono text-xs text-cyan-300" id="last-run-id">--</div>
      <div class="text-xs text-slate-500 mt-2 mb-1">Branch</div>
      <div class="mono text-xs text-green-300" id="last-branch">--</div>
    </div>
  </div>

  <!-- ─── Row 3: Formal Verification + Wasm Sandbox ────────────────────── -->

  <!-- Formal Verification Proof Tree -->
  <div class="col-span-12 lg:col-span-6 panel p-4">
    <div class="panel-title mb-3">Formal Verification — Proof Tree</div>
    <div class="grid grid-cols-4 gap-2 mb-3" id="proof-properties">
      <div class="proof-node proof-UNKNOWN" id="prop-div">
        <span class="text-yellow-400 font-bold">?</span>
        <span class="text-slate-400">Division Safety</span>
      </div>
      <div class="proof-node proof-UNKNOWN" id="prop-null">
        <span class="text-yellow-400 font-bold">?</span>
        <span class="text-slate-400">Null Safety</span>
      </div>
      <div class="proof-node proof-UNKNOWN" id="prop-bounds">
        <span class="text-yellow-400 font-bold">?</span>
        <span class="text-slate-400">Bounds Safety</span>
      </div>
      <div class="proof-node proof-UNKNOWN" id="prop-overflow">
        <span class="text-yellow-400 font-bold">?</span>
        <span class="text-slate-400">Overflow Safety</span>
      </div>
    </div>
    <div id="proof-tree-nodes" class="space-y-1">
      <div class="text-xs text-slate-600 italic">No proof data yet. Run a pipeline to populate.</div>
    </div>
    <div class="mt-3 pt-2 border-t border-slate-700/50 flex justify-between text-xs">
      <span class="text-slate-500">Proof Time</span>
      <span class="text-purple-400 mono font-bold" id="proof-time">-- us</span>
    </div>
  </div>

  <!-- Wasm Sandbox -->
  <div class="col-span-12 lg:col-span-6 panel p-4">
    <div class="panel-title mb-3">Wasm Isolation Sandbox</div>
    <div class="grid grid-cols-2 gap-4">
      <div>
        <div class="text-xs text-slate-500 mb-2">Compile Time</div>
        <div class="text-2xl font-bold text-cyan-400 mono" id="wasm-compile-us">--</div>
        <div class="text-xs text-slate-600">microseconds</div>
        <div class="bar-track mt-2">
          <div class="bar-fill bg-cyan-500" id="wasm-compile-bar" style="width:0%"></div>
        </div>
      </div>
      <div>
        <div class="text-xs text-slate-500 mb-2">Execute Time</div>
        <div class="text-2xl font-bold text-green-400 mono" id="wasm-exec-us">--</div>
        <div class="text-xs text-slate-600">microseconds</div>
        <div class="bar-track mt-2">
          <div class="bar-fill bg-green-500" id="wasm-exec-bar" style="width:0%"></div>
        </div>
      </div>
      <div>
        <div class="text-xs text-slate-500 mb-2">Peak Memory</div>
        <div class="text-2xl font-bold text-yellow-400 mono" id="wasm-mem">--</div>
        <div class="text-xs text-slate-600">kilobytes</div>
        <div class="bar-track mt-2">
          <div class="bar-fill bg-yellow-500" id="wasm-mem-bar" style="width:0%"></div>
        </div>
      </div>
      <div>
        <div class="text-xs text-slate-500 mb-2">AST Nodes</div>
        <div class="text-2xl font-bold text-purple-400 mono" id="wasm-nodes">--</div>
        <div class="text-xs text-slate-600">instructions</div>
        <div class="bar-track mt-2">
          <div class="bar-fill bg-purple-500" id="wasm-nodes-bar" style="width:0%"></div>
        </div>
      </div>
    </div>
    <div class="mt-3 grid grid-cols-2 gap-3">
      <div class="p-2 rounded-lg bg-slate-900/50 text-center">
        <div class="text-xs text-slate-500">Compile Status</div>
        <div class="text-sm font-bold mono" id="wasm-compile-status">--</div>
      </div>
      <div class="p-2 rounded-lg bg-slate-900/50 text-center">
        <div class="text-xs text-slate-500">Execute Status</div>
        <div class="text-sm font-bold mono" id="wasm-exec-status">--</div>
      </div>
    </div>
  </div>

  <!-- ─── Row 4: Event Log ────────────────────────────────────────────── -->
  <div class="col-span-12 panel p-4">
    <div class="flex items-center justify-between mb-3">
      <div class="panel-title">Live Event Feed</div>
      <button onclick="clearLog()" class="text-xs text-slate-600 hover:text-slate-400 transition">Clear</button>
    </div>
    <div id="event-log" class="mono"></div>
  </div>

</main>

<!-- ═══════ FOOTER ═══════════════════════════════════════════════════════ -->
<footer class="text-center py-4 text-xs text-slate-700 border-t border-slate-800/50">
  Aegis V3 Prometheus — Enterprise Autonomous Infrastructure Resilience Engine &nbsp;|&nbsp;
  Wasm Sandbox · Raft Consensus · Formal Verification · AI Swarm
</footer>

<script>
// ═══════════════════════════════════════════════════════════════════════════
// Aegis V3 Dashboard — Vanilla JS
// ═══════════════════════════════════════════════════════════════════════════

const STAGES = [
  "FAULT_INJECTION", "CLUSTER_COORDINATION", "PATCH_GENERATION",
  "WASM_SANDBOX", "FORMAL_VERIFICATION", "CONSENSUS_COMMIT",
  "DEPLOYMENT", "TELEMETRY_FLUSH"
];
const STAGE_LABELS = {
  FAULT_INJECTION: "Fault Injection",
  CLUSTER_COORDINATION: "Raft Cluster Election",
  PATCH_GENERATION: "AI Patch Generation",
  WASM_SANDBOX: "Wasm Sandbox",
  FORMAL_VERIFICATION: "Formal Verification",
  CONSENSUS_COMMIT: "Consensus Commit",
  DEPLOYMENT: "Deployment",
  TELEMETRY_FLUSH: "Telemetry Flush",
};

let pipelineRunning = false;
let telemetryWs = null;
let metricsWs = null;

// ──────────────────────────── Clock ────────────────────────────────────────
function updateClock() {
  document.getElementById('system-clock').textContent = new Date().toISOString().replace('T',' ').slice(0,19) + ' UTC';
}
setInterval(updateClock, 1000);
updateClock();

// ──────────────────────────── Pipeline Stages ──────────────────────────────
function initPipelineStages() {
  const container = document.getElementById('pipeline-stages');
  container.innerHTML = STAGES.map(s => `
    <div class="stage-row" id="stage-${s}">
      <span class="stage-name">${STAGE_LABELS[s] || s}</span>
      <span class="stage-badge badge-pending" id="badge-${s}">PENDING</span>
      <span class="stage-time mono" id="time-${s}">-- us</span>
    </div>
  `).join('');
}
initPipelineStages();

function updateStage(stageName, success, durationUs) {
  const badge = document.getElementById('badge-' + stageName);
  const time  = document.getElementById('time-' + stageName);
  if (badge) {
    badge.textContent = success ? 'PASS' : 'FAIL';
    badge.className = 'stage-badge ' + (success ? 'badge-pass' : 'badge-fail');
  }
  if (time && durationUs != null) {
    time.textContent = durationUs.toFixed(1) + ' us';
  }
}

function resetPipelineStages() {
  STAGES.forEach(s => {
    const b = document.getElementById('badge-' + s);
    const t = document.getElementById('time-' + s);
    if (b) { b.textContent = 'PENDING'; b.className = 'stage-badge badge-pending'; }
    if (t) t.textContent = '-- us';
  });
  document.getElementById('pipeline-total-us').textContent = '-- us';
}

// ──────────────────────────── Pipeline Status ───────────────────────────────
function setPipelineRunning(running) {
  pipelineRunning = running;
  const led  = document.getElementById('pipeline-led');
  const txt  = document.getElementById('pipeline-status-text');
  const bRun = document.getElementById('btn-run');
  const bCha = document.getElementById('btn-chaos');
  if (running) {
    led.className = 'led led-yellow';
    txt.textContent = 'RUNNING'; txt.style.color = '#f59e0b';
    bRun.disabled = true; bCha.disabled = true;
  } else {
    led.className = 'led led-green';
    txt.textContent = 'IDLE'; txt.style.color = '#10b981';
    bRun.disabled = false; bCha.disabled = false;
  }
}

// ──────────────────────────── Event Log ─────────────────────────────────────
let eventCount = 0;
function addEvent(ev) {
  const log = document.getElementById('event-log');
  const sev = ev.severity || 'INFO';
  const ts  = (ev.timestamp || '').slice(11,23);
  const div = document.createElement('div');
  div.className = 'event-item event-' + sev;
  div.innerHTML = `
    <span style="color:#475569">${ts}</span>
    <span class="ml-2 font-semibold" style="color:${sevColor(sev)}">${ev.title || ev.event_type || ''}</span>
    <span class="ml-2 text-slate-400">${(ev.detail || '').slice(0,120)}</span>
    ${ev.duration_us != null ? `<span class="ml-2 text-slate-600">${ev.duration_us.toFixed(1)}us</span>` : ''}
  `;
  log.prepend(div);
  // Keep only last 80 events
  while (log.children.length > 80) log.removeChild(log.lastChild);
}
function sevColor(s) {
  const m = {SUCCESS:'#10b981', CRITICAL:'#ef4444', INFO:'#22d3ee', WARN:'#f59e0b'};
  return m[s] || '#94a3b8';
}
function clearLog() { document.getElementById('event-log').innerHTML = ''; }

// ──────────────────────────── Cluster Panel ─────────────────────────────────
function updateCluster(data) {
  const nodes = data.nodes || {};
  const container = document.getElementById('cluster-nodes');
  container.innerHTML = Object.entries(nodes).map(([id, role]) => `
    <span class="node-chip node-${role}">
      <span class="led ${roleLed(role)}"></span>${id} <span class="opacity-60 ml-1">${role}</span>
    </span>
  `).join('');
  if (data.term != null)          document.getElementById('cluster-term').textContent = data.term;
  if (data.leader_id != null)     document.getElementById('cluster-leader').textContent = data.leader_id ? data.leader_id.slice(0,8) : '--';
  if (data.committed_index != null) document.getElementById('cluster-commit').textContent = data.committed_index;
}
function roleLed(r) {
  return {LEADER:'led-cyan', FOLLOWER:'led-green', CANDIDATE:'led-yellow', OFFLINE:'led-dim'}[r] || 'led-dim';
}

// ──────────────────────────── Proof Panel ───────────────────────────────────
function updateProof(data) {
  const props = ['div','null','bounds','overflow'];
  const keys  = ['is_division_safe','is_null_safe','is_bounds_safe','is_overflow_safe'];
  const labels = ['Division Safety','Null Safety','Bounds Safety','Overflow Safety'];
  props.forEach((p, i) => {
    const el = document.getElementById('prop-' + p);
    const ok = data[keys[i]];
    if (el && ok !== undefined) {
      el.className = 'proof-node proof-' + (ok ? 'PROVED' : 'REFUTED');
      el.innerHTML = `<span style="color:${ok?'#10b981':'#ef4444'};font-weight:700">${ok?'✓':'✗'}</span><span class="text-slate-400">${labels[i]}</span>`;
    }
  });
  if (data.proof_time_us != null) {
    document.getElementById('proof-time').textContent = data.proof_time_us.toFixed(1) + ' us';
  }
  // Proof tree nodes
  const treeEl = document.getElementById('proof-tree-nodes');
  const v = data.verdict || 'UNKNOWN';
  const proved = data.proved_nodes || 0;
  const total  = data.total_nodes  || 0;
  treeEl.innerHTML = `
    <div class="proof-node proof-${v} flex justify-between">
      <span style="color:${v==='PROVED'?'#10b981':v==='REFUTED'?'#ef4444':'#f59e0b'};font-weight:700">${v}</span>
      <span class="text-slate-400">Global Safety Proof — ${proved}/${total} nodes proved</span>
    </div>
    ${(data.critical_failures||[]).map(f=>`
      <div class="proof-node proof-REFUTED text-xs text-red-300">${f.slice(0,80)}</div>
    `).join('')}
    <div class="proof-node proof-UNKNOWN">
      <span class="text-slate-500">Constraints checked: ${data.constraints_checked || 0}</span>
    </div>
  `;
}

// ──────────────────────────── Wasm Panel ────────────────────────────────────
function updateWasm(data) {
  const setBar = (id, val, max) => {
    const el = document.getElementById(id);
    if (el) el.style.width = Math.min(100, (val/max)*100).toFixed(1) + '%';
  };
  if (data.compile_us != null) {
    document.getElementById('wasm-compile-us').textContent = data.compile_us.toFixed(1);
    setBar('wasm-compile-bar', data.compile_us, 5000);
  }
  if (data.execute_us != null) {
    document.getElementById('wasm-exec-us').textContent = data.execute_us.toFixed(1);
    setBar('wasm-exec-bar', data.execute_us, 10000);
  }
  if (data.peak_memory_kb != null) {
    document.getElementById('wasm-mem').textContent = data.peak_memory_kb.toFixed(1);
    setBar('wasm-mem-bar', data.peak_memory_kb, 4096);
  }
  if (data.ast_nodes != null) {
    document.getElementById('wasm-nodes').textContent = data.ast_nodes;
    setBar('wasm-nodes-bar', data.ast_nodes, 800);
  }
  const statusColor = s => s === 'EXECUTED' || s === 'COMPILED' ? '#10b981' : '#ef4444';
  if (data.compilation_status) {
    const el = document.getElementById('wasm-compile-status');
    el.textContent = data.compilation_status;
    el.style.color = statusColor(data.compilation_status);
  }
  if (data.execution_status) {
    const el = document.getElementById('wasm-exec-status');
    el.textContent = data.execution_status;
    el.style.color = statusColor(data.execution_status);
  }
}

// ──────────────────────────── Metrics Panel ─────────────────────────────────
function updateMetrics(snap) {
  if (snap.cpu_percent    != null) document.getElementById('metric-cpu').textContent    = snap.cpu_percent.toFixed(1) + '%';
  if (snap.memory_mb      != null) document.getElementById('metric-ram').textContent    = snap.memory_mb.toFixed(1);
  if (snap.active_tasks   != null) document.getElementById('metric-tasks').textContent  = snap.active_tasks;
  if (snap.event_queue_depth != null) document.getElementById('metric-queue').textContent = snap.event_queue_depth;
  if (snap.uptime_seconds != null) document.getElementById('metric-uptime').textContent = Math.floor(snap.uptime_seconds);
}

// ──────────────────────────── Event Router ──────────────────────────────────
function handleTelemetryEvent(ev) {
  addEvent(ev);

  if (ev.event_type === 'PIPELINE_START') {
    resetPipelineStages();
    setPipelineRunning(true);
    document.getElementById('last-run-id').textContent = ev.data?.run_id || '--';
    STAGES.forEach(s => {
      const b = document.getElementById('badge-' + s);
      if(b) { b.textContent = 'PENDING'; b.className = 'stage-badge badge-pending'; }
    });
  }

  if (ev.event_type === 'STAGE_COMPLETE' || ev.event_type === 'CLUSTER_STATE' ||
      ev.event_type === 'SANDBOX_RESULT' || ev.event_type === 'PROOF_UPDATE') {
    const stage = ev.title;
    if (STAGES.includes(stage)) {
      updateStage(stage, ev.severity === 'SUCCESS', ev.duration_us);
    }
    // Route to specific panels
    if (ev.event_type === 'CLUSTER_STATE' && ev.data?.nodes) updateCluster(ev.data);
    if (ev.event_type === 'SANDBOX_RESULT') updateWasm(ev.data || {});
    if (ev.event_type === 'PROOF_UPDATE') updateProof(ev.data || {});
  }

  if (ev.event_type === 'PIPELINE_END') {
    setPipelineRunning(false);
    const d = ev.data || {};
    if (d.total_us) document.getElementById('pipeline-total-us').textContent = Math.round(d.total_us).toLocaleString() + ' us';
    if (d.branch)   document.getElementById('last-branch').textContent = d.branch;
    if (d.run_id)   document.getElementById('last-run-id').textContent = d.run_id;
    // Update all stages from summary
    (d.stages || []).forEach(s => updateStage(s.stage, s.success, s.duration_us));
  }
}

// ──────────────────────────── WebSocket Setup ────────────────────────────────
function connectTelemetry() {
  const proto = location.protocol === 'https:' ? 'wss' : 'ws';
  telemetryWs = new WebSocket(`${proto}://${location.host}/ws/telemetry`);
  telemetryWs.onopen = () => {
    document.getElementById('ws-led').className = 'led led-green';
    document.getElementById('ws-status').textContent = 'CONNECTED';
  };
  telemetryWs.onmessage = e => {
    try {
      const msg = JSON.parse(e.data);
      if (msg.ping) return;
      handleTelemetryEvent(msg);
    } catch(err) {}
  };
  telemetryWs.onclose = () => {
    document.getElementById('ws-led').className = 'led led-red';
    document.getElementById('ws-status').textContent = 'RECONNECTING...';
    setTimeout(connectTelemetry, 3000);
  };
  telemetryWs.onerror = () => telemetryWs.close();
}

function connectMetrics() {
  const proto = location.protocol === 'https:' ? 'wss' : 'ws';
  metricsWs = new WebSocket(`${proto}://${location.host}/ws/metrics`);
  metricsWs.onmessage = e => {
    try { updateMetrics(JSON.parse(e.data)); } catch(err) {}
  };
  metricsWs.onclose = () => setTimeout(connectMetrics, 3000);
  metricsWs.onerror = () => metricsWs.close();
}

// ──────────────────────────── Control Actions ────────────────────────────────
async function triggerPipeline() {
  if (pipelineRunning) return;
  try {
    const r = await fetch('/api/pipeline/run?simulation=true', { method: 'POST' });
    const d = await r.json();
    if (!r.ok) { addEvent({event_type:'ALERT',title:'API_ERROR',detail:d.detail||'Error',severity:'CRITICAL'}); return; }
    addEvent({event_type:'PIPELINE_START',title:'PIPELINE_START',detail:`Run ${d.run_id} triggered`,severity:'INFO',data:d});
  } catch(e) {
    addEvent({event_type:'ALERT',title:'API_ERROR',detail:String(e),severity:'CRITICAL'});
  }
}

async function injectChaos() {
  if (pipelineRunning) return;
  try {
    const r = await fetch('/api/chaos/inject', { method: 'POST' });
    const d = await r.json();
    addEvent({event_type:'ALERT',title:'CHAOS_INJECTED',detail:`Chaos run ${d.run_id} started`,severity:'WARN',data:d});
  } catch(e) {
    addEvent({event_type:'ALERT',title:'API_ERROR',detail:String(e),severity:'CRITICAL'});
  }
}

// ──────────────────────────── Init ──────────────────────────────────────────
connectTelemetry();
connectMetrics();

// Load initial status
fetch('/api/status').then(r=>r.json()).then(d => {
  if (d.cluster?.nodes) updateCluster(d.cluster);
  if (d.status === 'RUNNING_PIPELINE') setPipelineRunning(true);
}).catch(()=>{});
</script>
</body>
</html>"""


@app.get("/", response_class=HTMLResponse)
async def dashboard() -> HTMLResponse:
    """Serve the Mission Control dashboard."""
    return HTMLResponse(content=DASHBOARD_HTML)
