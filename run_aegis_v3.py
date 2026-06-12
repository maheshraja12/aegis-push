"""
==============================================================================
Aegis V3 — run_aegis_v3.py — Master Entry Point
==============================================================================
Modes:
  python run_aegis_v3.py --mode dashboard   # Start FastAPI UI (default)
  python run_aegis_v3.py --mode pipeline    # Run full pipeline (console)
  python run_aegis_v3.py --mode verify      # Verify a patch file standalone
  python run_aegis_v3.py --mode consensus   # Test Raft cluster standalone
  python run_aegis_v3.py --mode chaos       # Chaos engineering mode

Environment variables:
  LOG_FORMAT=json     Enable structured JSON logging (default: plain text)
  LOG_LEVEL=DEBUG     Set log level (default: INFO)
  OPENAI_API_KEY=...  Enable real AI patch generation
==============================================================================
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys

# Force UTF-8 stdout on Windows
import io
if hasattr(sys.stdout, 'buffer') and sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, REPO_ROOT)

# ---------------------------------------------------------------------------
# Structured logging (GAP 7 fix) — must be configured before any aegis import
# ---------------------------------------------------------------------------
try:
    from aegis_v3.logging_config import configure_logging
    _log_format_json = os.environ.get("LOG_FORMAT", "plain").lower() == "json"
    _log_level       = os.environ.get("LOG_LEVEL", "INFO").upper()
    configure_logging(
        level=_log_level,
        json_format=_log_format_json,
        log_file=os.environ.get("LOG_FILE"),  # optional: set LOG_FILE=/path/aegis.log
    )
except Exception:
    # Fallback: use basic config if logging_config import fails
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-8s | %(name)-28s | %(message)s",
        datefmt="%H:%M:%S",
    )

for noisy in ["httpcore", "httpx", "openai", "chromadb", "urllib3", "watchfiles"]:
    logging.getLogger(noisy).setLevel(logging.WARNING)

logger = logging.getLogger("aegis.v3.main")



# ---------------------------------------------------------------------------
# MODE: dashboard
# ---------------------------------------------------------------------------

def run_dashboard(host: str = "0.0.0.0", port: int = 8001) -> None:
    print(f"\n  ╔══════════════════════════════════════════════════════╗")
    print(f"  ║       AEGIS V3 — MISSION CONTROL DASHBOARD           ║")
    print(f"  ╠══════════════════════════════════════════════════════╣")
    print(f"  ║  Dashboard:       http://localhost:{port}              ║")
    print(f"  ║  WebSocket (evt): ws://localhost:{port}/ws/telemetry   ║")
    print(f"  ║  WebSocket (res): ws://localhost:{port}/ws/metrics     ║")
    print(f"  ╠══════════════════════════════════════════════════════╣")
    print(f"  ║  Health probe:    http://localhost:{port}/health        ║")
    print(f"  ║  Ready probe:     http://localhost:{port}/ready         ║")
    print(f"  ║  API status:      http://localhost:{port}/api/status    ║")
    print(f"  ║  Run history:     http://localhost:{port}/api/history   ║")
    print(f"  ║  MTTR stats:      http://localhost:{port}/api/mttr/stats║")
    print(f"  ║  OpenAPI docs:    http://localhost:{port}/docs          ║")
    print(f"  ╚══════════════════════════════════════════════════════╝")
    print(f"  Press Ctrl+C to stop.\n")
    import uvicorn
    uvicorn.run(
        "aegis_v3.realtime_telemetry:app",
        host=host, port=port,
        reload=False, log_level="warning",
    )


# ---------------------------------------------------------------------------
# MODE: pipeline / chaos
# ---------------------------------------------------------------------------

async def run_pipeline_async(simulation: bool = True, chaos: bool = False) -> None:
    from aegis_v3.orchestrator import AegisV3Orchestrator
    from aegis_v3.schema_v3 import IncidentSeverity

    api_key = os.environ.get("OPENAI_API_KEY", "")
    if chaos and not api_key:
        print("[!] Chaos mode requires an OPENAI_API_KEY environment variable to generate dynamic patches.")
        sys.exit(1)

    orchestrator = AegisV3Orchestrator(
        repo_root=REPO_ROOT,
        openai_api_key=api_key or None,
        simulation=simulation,
        node_count=5,
    )
    result = await orchestrator.run_full_pipeline(
        target_file="dummy_app/payment.py",
        severity=IncidentSeverity.P1,
        chaos=chaos,
    )
    sys.exit(0 if result.success else 1)


def run_pipeline(simulation: bool = True, chaos: bool = False) -> None:
    asyncio.run(run_pipeline_async(simulation, chaos))


# ---------------------------------------------------------------------------
# MODE: verify (standalone formal verification)
# ---------------------------------------------------------------------------

async def run_verify_async(patch_file: str) -> None:
    from aegis_v3.formal_verification import FormalVerificationEngine

    if not os.path.isfile(patch_file):
        print(f"[!] File not found: {patch_file}")
        sys.exit(1)

    with open(patch_file, "r", encoding="utf-8") as f:
        source = f.read()

    engine = FormalVerificationEngine()
    engine.declare_variable("amount",   lo=0.0, hi=1_000_000.0)
    engine.declare_variable("tax_rate", lo=0.0, hi=1.0)
    engine.declare_variable("index",    lo=0,   hi=999)

    report = await engine.verify_patch(source, os.path.basename(patch_file))
    print(engine.summary_table(report))
    sys.exit(0 if report.overall_verdict != "REFUTED" else 1)


def run_verify(patch_file: str) -> None:
    asyncio.run(run_verify_async(patch_file))


# ---------------------------------------------------------------------------
# MODE: consensus (standalone Raft cluster test)
# ---------------------------------------------------------------------------

async def run_consensus_async(node_count: int = 5) -> None:
    from aegis_v3.distributed_consensus import AgentClusterCoordinator

    print(f"\n  Starting {node_count}-node Raft cluster (with 1 fault-injected node)...")
    coord = AgentClusterCoordinator(node_count=node_count)
    await coord.start_cluster()

    print("  Waiting for leader election...")
    log = await coord.wait_for_consensus(
        command="TEST_CONSENSUS_PROBE",
        timeout_seconds=5.0,
    )

    print(f"\n  Consensus reached: {log.consensus_reached}")
    print(f"  Leader:            {log.leader_id}")
    print(f"  Term:              {log.current_term}")
    print(f"  Committed index:   {log.committed_index}")
    print(f"  Node roles:")
    for nid, role in log.nodes.items():
        print(f"    {nid}: {role.value}")

    await coord.stop_cluster()
    sys.exit(0 if log.consensus_reached else 1)


def run_consensus(node_count: int = 5) -> None:
    asyncio.run(run_consensus_async(node_count))


# ---------------------------------------------------------------------------
# Argument Parser
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="run_aegis_v3",
        description="Aegis V3: Autonomous Infrastructure Resilience Engine",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Modes:
  dashboard  Start the FastAPI web UI on http://localhost:8001 (default)
  pipeline   Run the full 8-stage healing pipeline (console output)
  verify     Run formal verification on a patch file
  consensus  Test the Raft cluster election standalone

Examples:
  python run_aegis_v3.py --mode dashboard
  python run_aegis_v3.py --mode pipeline
  python run_aegis_v3.py --mode pipeline --no-simulation
  python run_aegis_v3.py --mode verify --patch dummy_app/payment.py
  python run_aegis_v3.py --mode consensus --nodes 7
        """,
    )
    p.add_argument("--mode",       choices=["dashboard","pipeline","verify","consensus","chaos"],
                   default="dashboard")
    p.add_argument("--host",       default="0.0.0.0")
    p.add_argument("--port",       type=int, default=8001)
    p.add_argument("--no-simulation", action="store_true",
                   help="Use real OpenAI API instead of simulation patch")
    p.add_argument("--patch",      default="dummy_app/payment.py",
                   help="Patch file to verify (verify mode)")
    p.add_argument("--nodes",      type=int, default=5,
                   help="Number of Raft cluster nodes (consensus mode)")
    return p.parse_args()


def main() -> None:
    args = parse_args()

    print("""
  ================================================================
  AEGIS V3 PROMETHEUS -- Autonomous Infrastructure Resilience
  Wasm Sandbox | Raft Consensus | Formal Verification | AI Swarm
  ================================================================""")
    print(f"  Mode: {args.mode.upper()}\n")

    if args.mode == "dashboard":
        run_dashboard(host=args.host, port=args.port)
    elif args.mode == "pipeline":
        run_pipeline(simulation=not args.no_simulation, chaos=False)
    elif args.mode == "chaos":
        run_pipeline(simulation=False, chaos=True)
    elif args.mode == "verify":
        run_verify(patch_file=args.patch)
    elif args.mode == "consensus":
        run_consensus(node_count=args.nodes)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[!] Interrupted. Shutting down cleanly.")
        sys.exit(0)
    except SystemExit:
        raise
    except Exception as exc:
        print(f"\n[ERROR] {exc}")
        import traceback; traceback.print_exc()
        sys.exit(1)
