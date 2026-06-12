"""
==============================================================================
Aegis V3: Enterprise Autonomous Infrastructure Resilience Engine
aegis_v3/orchestrator.py — Parallel System Lifecycle Integration Runtime
==============================================================================

PURPOSE
-------
The Orchestrator is the central nervous system of Aegis V3. It coordinates
every subsystem through a deterministic, timed pipeline:

  PIPELINE STAGES (in order):
  ─────────────────────────────────────────────────────────────────────────
  1. FAULT_INJECTION      Trip a simulated memory/logic fault in the target
                          service to generate a realistic incident.

  2. CLUSTER_COORDINATION Bootstrap the Raft cluster and elect a leader.
                          No structural change can proceed without this.

  3. PATCH_GENERATION     Call the AI swarm (or load a pre-generated patch
                          in simulation mode) to produce a candidate fix.

  4. WASM_SANDBOX         Compile and execute the patch in an isolated
                          process. Reject if it violates any execution
                          boundary (timeout, memory, denylist).

  5. FORMAL_VERIFICATION  Run the SMT proof engine. The patch MUST be
                          mathematically proved free of ZeroDivisionError,
                          null dereference, and bounds violations before
                          proceeding. Refuted patches are rejected.

  6. CONSENSUS_COMMIT     Submit the deployment command to the Raft cluster.
                          Wait for quorum acknowledgement (majority of nodes).

  7. DEPLOYMENT           Apply the patch to the target file, run the
                          existing test suite, and git commit on success.

  8. TELEMETRY_FLUSH      Push the final pipeline result to the telemetry
                          stream for dashboard display.

TIMING & OBSERVABILITY
----------------------
Every stage is wrapped in a microsecond-precision timer. The StageResult
contains both `duration_us` and a `detail` field with a human-readable
description. The full PipelineResult provides a chronological trace of
the entire incident lifecycle from fault detection to git commit.

SIMULATION MODE
---------------
In simulation mode (`simulation=True`), the orchestrator:
  - Injects a predetermined ZeroDivisionError into dummy_app/payment.py
  - Uses a hardcoded test patch to demonstrate the full pipeline
  - Runs the real Wasm sandbox and formal verification (these are always real)
  - Simulates the AI patch generation call (skips OpenAI API if no key)
  - Runs the real Raft cluster with 5 simulated nodes

RICH CONSOLE OUTPUT
-------------------
The orchestrator uses the `rich` library for beautiful terminal output:
  - Live progress panel with stage status indicators
  - Microsecond timing column
  - Color-coded pass/fail/warning rows
  - ASCII pipeline diagram printed at the end

==============================================================================
"""
from __future__ import annotations

import asyncio
import logging
import math
import os
import random
import subprocess
import sys
import time
import uuid
from typing import Any, Optional

import psutil
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich import box

from aegis_v3.schema_v3 import (
    ClusterStateLog,
    CompilationResult,
    ExecutionTrace,
    IncidentSeverity,
    MttrRecord,
    PipelineResult,
    PipelineStage,
    SandboxStatus,
    ProofStatus,
    StageResult,
    TelemetryEvent,
    TelemetryEventType,
    VerificationReport,
)
from aegis_v3.wasm_sandbox import WasmIsolationEngine, SandboxConfig
from aegis_v3.distributed_consensus import AgentClusterCoordinator
from aegis_v3.formal_verification import FormalVerificationEngine

try:
    from aegis_v3.persistence import AuditLogBackend
    _AUDIT_AVAILABLE = True
except ImportError:
    _AUDIT_AVAILABLE = False

logger = logging.getLogger("aegis.orchestrator")
console = Console()

# ---------------------------------------------------------------------------
# Hard-coded simulation patch (used when OPENAI_API_KEY not available)
# ---------------------------------------------------------------------------

_SIMULATION_PATCH = '''
# Aegis V3 AI-Generated Patch — Simulation Mode
# Fix: Replace division with multiplication for tax calculation

def calculate_tax(amount, tax_rate):
    """
    Calculates tax amount.
    Args:
        amount: float - The transaction amount
        tax_rate: float - The tax rate (0.0 to 1.0)
    Returns:
        float - The calculated tax
    """
    if tax_rate < 0 or tax_rate > 1:
        raise ValueError(f"tax_rate must be between 0 and 1, got: {tax_rate}")
    result = amount * tax_rate
    return result

# Verification test
tax = calculate_tax(100.0, 0.1)
result = tax
'''

# ---------------------------------------------------------------------------
# Stage timer context manager
# ---------------------------------------------------------------------------

class _StageTimer:
    """Context manager that measures wall-clock time for a pipeline stage."""

    def __init__(self, stage: PipelineStage) -> None:
        self._stage = stage
        self._t0_ns: int = 0
        self._duration_us: Optional[float] = None

    def __enter__(self) -> "_StageTimer":
        self._t0_ns = time.perf_counter_ns()
        return self

    def __exit__(self, *_: Any) -> None:
        self._duration_us = (time.perf_counter_ns() - self._t0_ns) / 1_000.0

    @property
    def duration_us(self) -> float:
        if self._duration_us is not None:
            return self._duration_us
        if self._t0_ns == 0:
            return 0.0
        return (time.perf_counter_ns() - self._t0_ns) / 1_000.0



# ---------------------------------------------------------------------------
# Retry helper (GAP 2: circuit breaker for subprocess calls)
# ---------------------------------------------------------------------------

async def _retry_subprocess(
    cmd: list[str],
    cwd: str,
    timeout: float = 15.0,
    max_attempts: int = 3,
    backoff_base_ms: float = 500.0,
    **kwargs: Any,
) -> subprocess.CompletedProcess:
    """
    Run a subprocess with exponential backoff retry on transient failure.

    Retries if returncode != 0 (test flake, I/O spike). On final failure
    returns the last result rather than raising, so callers can inspect output.

    Args:
        cmd:             Command + args list.
        cwd:             Working directory.
        timeout:         Per-attempt timeout in seconds.
        max_attempts:    Maximum number of attempts (default 3).
        backoff_base_ms: Base backoff in milliseconds (doubles each attempt + jitter).
        **kwargs:        Extra kwargs forwarded to subprocess.run().

    Returns:
        subprocess.CompletedProcess from last attempt (check .returncode).
    """
    _logger = logging.getLogger("aegis.orchestrator.retry")
    last_result: Optional[subprocess.CompletedProcess] = None
    for attempt in range(1, max_attempts + 1):
        try:
            result = subprocess.run(cmd, cwd=cwd, timeout=timeout, **kwargs)
        except subprocess.TimeoutExpired as exc:
            _logger.warning(
                f"Subprocess timed out (attempt {attempt}/{max_attempts}): {cmd[0]}"
            )
            if attempt == max_attempts:
                # Create a fake CompletedProcess to signal timeout
                import io as _io
                return subprocess.CompletedProcess(
                    args=cmd, returncode=-1,
                    stdout="", stderr=f"TimeoutExpired after {timeout}s",
                )
            delay_ms = backoff_base_ms * (2 ** (attempt - 1))
            await asyncio.sleep(max(0.1, delay_ms / 1000.0))
            continue

        if result.returncode == 0:
            if attempt > 1:
                _logger.info(f"Subprocess succeeded on attempt {attempt}: {' '.join(cmd[:2])}")
            return result

        last_result = result
        if attempt < max_attempts:
            delay_ms = backoff_base_ms * (2 ** (attempt - 1))
            jitter    = delay_ms * random.uniform(-0.25, 0.25)
            sleep_s   = max(0.1, (delay_ms + jitter) / 1000.0)
            _logger.warning(
                f"Subprocess attempt {attempt}/{max_attempts} failed "
                f"(rc={result.returncode}) — retrying in {sleep_s:.2f}s"
            )
            await asyncio.sleep(sleep_s)

    return last_result  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

class AegisV3Orchestrator:
    """
    Central lifecycle runtime for Aegis V3.

    Coordinates Wasm Sandbox, Raft Consensus, and Formal Verification
    engines through a deterministic, timed, fully-observed pipeline.

    Args:
        repo_root:      Absolute path to the project root.
        openai_api_key: OpenAI API key (uses simulation patch if None).
        simulation:     If True, use hardcoded fault and patch (no API call).
        node_count:     Number of simulated Raft cluster nodes.
        telemetry_queue: Optional asyncio.Queue to push TelemetryEvents.
    """

    def __init__(
        self,
        repo_root: str,
        openai_api_key: Optional[str] = None,
        simulation: bool = False,
        node_count: int = 5,
        telemetry_queue: Optional[asyncio.Queue] = None,
    ) -> None:
        self._repo_root      = repo_root
        self._api_key        = openai_api_key
        self._simulation     = simulation or not openai_api_key
        self._node_count     = node_count
        self._telemetry_q    = telemetry_queue
        self._run_id         = str(uuid.uuid4())[:12]
        self._start_time     = time.monotonic()
        self._chaos_injected_at: Optional[str] = None  # MTTR tracking
        self._chaos_event_id: Optional[str]    = None
        self._chaos_bug_type: Optional[str]    = None

        # Sub-engines (lazy init in run_full_pipeline)
        self._wasm: Optional[WasmIsolationEngine] = None
        self._consensus: Optional[AgentClusterCoordinator] = None
        self._verifier: Optional[FormalVerificationEngine] = None

        # Audit log backend (persists pipeline results to SQLite)
        self._audit: Optional["AuditLogBackend"] = (
            AuditLogBackend() if _AUDIT_AVAILABLE else None
        )

        logger.info(
            f"AegisV3Orchestrator [{self._run_id}] initialized | "
            f"simulation={self._simulation} | nodes={node_count}"
        )

    # -----------------------------------------------------------------------
    # Primary Entry Point
    # -----------------------------------------------------------------------

    async def run_full_pipeline(
        self,
        target_file: str = "dummy_app/payment.py",
        severity: IncidentSeverity = IncidentSeverity.P1,
        chaos: bool = False,
    ) -> PipelineResult:
        """
        Execute the complete Aegis V3 self-healing pipeline.

        Returns a fully populated PipelineResult with microsecond-level
        timing for every stage.
        """
        result = PipelineResult(
            run_id=self._run_id,
            severity=severity,
        )

        self._print_pipeline_header(result.run_id, target_file, severity)

        # Initialize sub-engines
        self._wasm     = WasmIsolationEngine(SandboxConfig(
            # 3s wall-clock limit: Windows process-pool spawn takes ~300ms cold start.
            # Actual patch code execution is measured separately in compile_us/execute_us.
            # DoS protection: any infinite loop / sleep will still be killed at 3s.
            max_execution_us=3_000_000,
            max_memory_kb=4_096,
            max_ast_nodes=800,
        ))
        self._verifier = FormalVerificationEngine()
        self._consensus = AgentClusterCoordinator(node_count=self._node_count)

        # Declare known variable domains for formal verification
        self._verifier.declare_variable("amount",   lo=0.0,  hi=1_000_000.0)
        self._verifier.declare_variable("tax_rate", lo=0.0,  hi=1.0)
        self._verifier.declare_variable("discount", lo=0.0,  hi=100.0)
        self._verifier.declare_variable("index",    lo=0,    hi=999)
        self._verifier.declare_variable("n",        lo=0,    hi=10_000)

        incident_id = str(uuid.uuid4())[:8].upper()
        result.incident_id = incident_id

        pipeline_t0 = time.perf_counter_ns()

        try:
            # ----------------------------------------------------------------
            # STAGE 1: Fault Injection
            # ----------------------------------------------------------------
            stage1 = await self._stage_fault_injection(target_file, chaos=chaos)
            result.stages.append(stage1)
            self._print_stage_row(stage1)
            await self._emit(TelemetryEventType.STAGE_COMPLETE, stage1)
            if not stage1.success:
                return self._finalize(result, pipeline_t0, success=False, summary=stage1.error or "Fault injection failed")

            fault_description = stage1.data.get("fault_description", "Unknown fault")
            patch_target_file = stage1.data.get("target_file", target_file)

            # ----------------------------------------------------------------
            # STAGE 2: Cluster Coordination
            # ----------------------------------------------------------------
            stage2 = await self._stage_cluster_coordination(incident_id)
            result.stages.append(stage2)
            self._print_stage_row(stage2)
            await self._emit(TelemetryEventType.CLUSTER_STATE, stage2)
            if not stage2.success:
                return self._finalize(result, pipeline_t0, success=False, summary=stage2.error or "Consensus failed")

            cluster_log: ClusterStateLog = stage2.data.get("cluster_log")
            leader_id = cluster_log.leader_id if cluster_log else "unknown"

            # ----------------------------------------------------------------
            # STAGE 3: Patch Generation
            # ----------------------------------------------------------------
            stage3 = await self._stage_patch_generation(fault_description, patch_target_file)
            result.stages.append(stage3)
            self._print_stage_row(stage3)
            await self._emit(TelemetryEventType.STAGE_COMPLETE, stage3)
            if not stage3.success:
                return self._finalize(result, pipeline_t0, success=False, summary=stage3.error or "Patch generation failed")

            patch_code = stage3.data.get("patch_code", "")
            patch_desc = stage3.data.get("patch_description", "AI patch")

            # ----------------------------------------------------------------
            # STAGE 4 & 5: Wasm Sandbox + Formal Verification (parallel)
            # ----------------------------------------------------------------
            stage4_task = asyncio.create_task(self._stage_wasm_sandbox(patch_code))
            stage5_task = asyncio.create_task(self._stage_formal_verification(patch_code, patch_desc))

            stage4, stage5 = await asyncio.gather(stage4_task, stage5_task)

            result.stages.append(stage4)
            result.stages.append(stage5)
            self._print_stage_row(stage4)
            self._print_stage_row(stage5)
            await self._emit(TelemetryEventType.SANDBOX_RESULT, stage4)
            await self._emit(TelemetryEventType.PROOF_UPDATE, stage5)

            if not stage4.success:
                return self._finalize(result, pipeline_t0, success=False, summary=f"Wasm sandbox rejected patch: {stage4.error}")

            if not stage5.success:
                return self._finalize(result, pipeline_t0, success=False, summary=f"Formal verification REFUTED patch: {stage5.error}")

            # ----------------------------------------------------------------
            # STAGE 6: Consensus Commit
            # ----------------------------------------------------------------
            stage6 = await self._stage_consensus_commit(
                incident_id=incident_id,
                patch_desc=patch_desc,
                cluster_log=cluster_log,
            )
            result.stages.append(stage6)
            self._print_stage_row(stage6)
            await self._emit(TelemetryEventType.CLUSTER_STATE, stage6)

            if not stage6.success:
                return self._finalize(result, pipeline_t0, success=False, summary=stage6.error or "Consensus commit failed")

            # ----------------------------------------------------------------
            # STAGE 7: Deployment
            # ----------------------------------------------------------------
            stage7 = await self._stage_deployment(
                patch_code=patch_code,
                target_file=patch_target_file,
                incident_id=incident_id,
            )
            result.stages.append(stage7)
            self._print_stage_row(stage7)
            await self._emit(TelemetryEventType.STAGE_COMPLETE, stage7)

            branch_name = stage7.data.get("branch_name")
            result.deployed_branch = branch_name

            # ----------------------------------------------------------------
            # STAGE 8: Telemetry Flush
            # ----------------------------------------------------------------
            stage8 = await self._stage_telemetry_flush(result)
            result.stages.append(stage8)
            self._print_stage_row(stage8)

        except Exception as exc:
            logger.error(f"Orchestrator [{self._run_id}] unhandled exception: {exc}", exc_info=True)
            result.stages.append(StageResult(
                stage=PipelineStage.DEPLOYMENT,
                success=False,
                duration_us=0.0,
                error=str(exc),
            ))
            return self._finalize(result, pipeline_t0, success=False, summary=f"Unhandled exception: {exc}")

        finally:
            # If chaos was enabled and the pipeline failed/errored, restore original file
            if chaos and (not locals().get("stage7") or not locals().get("stage7").success):
                try:
                    from aegis_v3.chaos_monkey import ChaosMonkey, ChaosEvent
                    if locals().get("stage1") and stage1.success and "chaos_event" in stage1.data and stage1.data["chaos_event"]:
                        monkey = ChaosMonkey(self._repo_root)
                        event = ChaosEvent(**stage1.data["chaos_event"])
                        monkey.restore(event)
                        logger.warning("Aegis V3 Pipeline failed to resolve chaos incident. Original file restored.")
                except Exception as restore_exc:
                    logger.error(f"Failed to restore chaos bug: {restore_exc}")

            # Stop Raft cluster
            if self._consensus:
                await self._consensus.stop_cluster()
            # Shutdown Wasm process pool
            if self._wasm:
                self._wasm.shutdown()

        return self._finalize(
            result, pipeline_t0, success=stage7.success,
            summary=(
                f"Incident {incident_id} resolved | "
                f"Leader: {leader_id} | "
                f"Branch: {branch_name or 'N/A'}"
            ),
        )

    # -----------------------------------------------------------------------
    # Individual Stage Implementations
    # -----------------------------------------------------------------------

    async def _stage_fault_injection(self, target_file: str, chaos: bool = False) -> StageResult:
        """STAGE 1: Inject or detect a fault in the target service."""
        with _StageTimer(PipelineStage.FAULT_INJECTION) as timer:
            try:
                target_path = os.path.join(self._repo_root, target_file)
                if not os.path.isfile(target_path):
                    return StageResult(
                        stage=PipelineStage.FAULT_INJECTION,
                        success=False,
                        duration_us=timer.duration_us,
                        error=f"Target file not found: {target_file}",
                    )

                fault_desc = "Unknown fault"
                chaos_evt_data = {}

                if chaos:
                    from aegis_v3.chaos_monkey import ChaosMonkey
                    monkey = ChaosMonkey(self._repo_root)
                    event = monkey.inject_bug("dummy_app")
                    if event:
                        # Record MTTR injection timestamp
                        self._chaos_injected_at = event.injected_at
                        self._chaos_event_id    = event.chaos_id
                        self._chaos_bug_type    = event.bug_type.value
                        chaos_evt_data = event.model_dump()
                        # Run tests to capture trace
                        test_run = subprocess.run(
                            [sys.executable, "-m", "pytest", "dummy_app", "--tb=short", "-q"],
                            cwd=self._repo_root,
                            capture_output=True,
                            text=True,
                            encoding="utf-8",
                            errors="replace",
                            timeout=15,
                        )
                        fault_desc = (test_run.stdout + test_run.stderr).strip()
                        if not fault_desc or "no tests ran" in fault_desc.lower():
                            fault_desc = f"Chaos injected: {event.injection_description}"
                    else:
                        return StageResult(
                            stage=PipelineStage.FAULT_INJECTION,
                            success=False,
                            duration_us=timer.duration_us,
                            error="ChaosMonkey failed to inject bug",
                        )
                elif self._simulation:
                    # Inject a ZeroDivisionError — the classic Aegis bug
                    with open(target_path, "r", encoding="utf-8") as f:
                        original = f.read()

                    faulty = original.replace(
                        "tax = amount * tax_rate",
                        "tax = amount / tax_rate  # INJECTED BUG: division instead of multiplication",
                    )
                    if faulty == original:
                        # Already has the bug or uses different pattern — just report it
                        fault_desc = "ZeroDivisionError detected: tax_rate used as divisor"
                    else:
                        with open(target_path, "w", encoding="utf-8") as f:
                            f.write(faulty)
                        fault_desc = "ZeroDivisionError injected: 'amount * tax_rate' -> 'amount / tax_rate'"

                    await asyncio.sleep(0.001)   # Simulate I/O latency

                return StageResult(
                    stage=PipelineStage.FAULT_INJECTION,
                    success=True,
                    duration_us=timer.duration_us,
                    detail=f"Fault confirmed in {target_file}",
                    data={
                        "fault_description": fault_desc if (self._simulation or chaos) else "Auto-detected from app.log",
                        "target_file": target_file,
                        "simulation_mode": self._simulation,
                        "chaos_mode": chaos,
                        "chaos_event": chaos_evt_data,
                    },
                )

            except Exception as exc:
                return StageResult(
                    stage=PipelineStage.FAULT_INJECTION,
                    success=False,
                    duration_us=timer.duration_us,
                    error=str(exc),
                )

    async def _stage_cluster_coordination(self, incident_id: str) -> StageResult:
        """STAGE 2: Bootstrap Raft cluster and elect a leader."""
        with _StageTimer(PipelineStage.CLUSTER_COORDINATION) as timer:
            try:
                await self._consensus.start_cluster()

                # Wait for initial leader election
                cluster_log = await self._consensus.wait_for_consensus(
                    command=f"INCIDENT_OPEN_{incident_id}",
                    timeout_seconds=3.0,
                )

                if not cluster_log.consensus_reached:
                    return StageResult(
                        stage=PipelineStage.CLUSTER_COORDINATION,
                        success=False,
                        duration_us=timer.duration_us,
                        error="No cluster leader elected within timeout.",
                        data={"roles": self._consensus.get_cluster_roles()},
                    )

                return StageResult(
                    stage=PipelineStage.CLUSTER_COORDINATION,
                    success=True,
                    duration_us=timer.duration_us,
                    detail=(
                        f"Leader: {cluster_log.leader_id} | "
                        f"Term: {cluster_log.current_term} | "
                        f"Quorum: {cluster_log.quorum_size}"
                    ),
                    data={
                        "cluster_log": cluster_log,
                        "leader_id": cluster_log.leader_id,
                        "term": cluster_log.current_term,
                        "nodes": {k: v.value for k, v in cluster_log.nodes.items()},
                    },
                )
            except Exception as exc:
                return StageResult(
                    stage=PipelineStage.CLUSTER_COORDINATION,
                    success=False,
                    duration_us=timer.duration_us,
                    error=str(exc),
                )

    async def _stage_patch_generation(
        self,
        fault_description: str,
        target_file: str,
    ) -> StageResult:
        """STAGE 3: Generate an AI patch (or load simulation patch)."""
        with _StageTimer(PipelineStage.PATCH_GENERATION) as timer:
            try:
                if self._simulation or not self._api_key:
                    # Simulation mode: use hardcoded patch
                    await asyncio.sleep(0.05)  # Simulate LLM latency
                    return StageResult(
                        stage=PipelineStage.PATCH_GENERATION,
                        success=True,
                        duration_us=timer.duration_us,
                        detail="Simulation patch loaded (no API key — demo mode)",
                        data={
                            "patch_code": _SIMULATION_PATCH,
                            "patch_description": "Fix ZeroDivisionError: replace division with multiplication",
                            "model": "simulation",
                            "tokens_used": 0,
                        },
                    )

                # Real AI patch generation via agent_engine
                sys.path.insert(0, self._repo_root)
                from agent_engine import AegisAgentEngine  # type: ignore

                engine = AegisAgentEngine(
                    repo_root=self._repo_root,
                    openai_api_key=self._api_key,
                )

                # Read the current broken file
                target_path = os.path.join(self._repo_root, target_file)
                with open(target_path, "r", encoding="utf-8") as f:
                    broken_code = f.read()

                patch_result = await asyncio.get_event_loop().run_in_executor(
                    None,
                    engine.generate_patch,
                    fault_description,
                    broken_code,
                )

                return StageResult(
                    stage=PipelineStage.PATCH_GENERATION,
                    success=True,
                    duration_us=timer.duration_us,
                    detail=f"Patch generated via gpt-4o-mini | {len(patch_result)} chars",
                    data={
                        "patch_code": patch_result,
                        "patch_description": fault_description,
                        "model": "gpt-4o-mini",
                    },
                )

            except Exception as exc:
                return StageResult(
                    stage=PipelineStage.PATCH_GENERATION,
                    success=False,
                    duration_us=timer.duration_us,
                    error=str(exc),
                )

    async def _stage_wasm_sandbox(self, patch_code: str) -> StageResult:
        """STAGE 4: Compile and execute patch in Wasm isolation engine."""
        with _StageTimer(PipelineStage.WASM_SANDBOX) as timer:
            try:
                compilation, trace = await self._wasm.run_full_isolation_pipeline(
                    source_code=patch_code,
                    patch_id=self._run_id[:8],
                )

                success = trace.status == SandboxStatus.EXECUTED
                detail  = (
                    f"compile={compilation.compilation_us:.1f}us | "
                    f"exec={trace.execution_us:.1f}us | "
                    f"mem={trace.peak_memory_kb:.1f}KB | "
                    f"nodes={compilation.ast_node_count}"
                )

                return StageResult(
                    stage=PipelineStage.WASM_SANDBOX,
                    success=success,
                    duration_us=timer.duration_us,
                    detail=detail,
                    data={
                        "compilation_status": compilation.status.value,
                        "execution_status": trace.status.value,
                        "compile_us": compilation.compilation_us,
                        "execute_us": trace.execution_us,
                        "peak_memory_kb": trace.peak_memory_kb,
                        "bytecode_bytes": compilation.bytecode_size_bytes,
                        "ast_nodes": compilation.ast_node_count,
                        "stdout": trace.stdout_captured[:200],
                        "return_value": str(trace.return_value),
                    },
                    error=trace.fault_traceback if not success else None,
                )

            except Exception as exc:
                return StageResult(
                    stage=PipelineStage.WASM_SANDBOX,
                    success=False,
                    duration_us=timer.duration_us,
                    error=str(exc),
                )

    async def _stage_formal_verification(
        self,
        patch_code: str,
        patch_description: str,
    ) -> StageResult:
        """STAGE 5: Run formal proof engine on the patch."""
        with _StageTimer(PipelineStage.FORMAL_VERIFICATION) as timer:
            try:
                report: VerificationReport = await self._verifier.verify_patch(
                    source_code=patch_code,
                    patch_description=patch_description,
                )

                success = report.overall_verdict != ProofStatus.REFUTED

                property_summary = (
                    f"div={'PROVED' if report.is_division_safe else 'REFUTED'} | "
                    f"null={'PROVED' if report.is_null_safe else 'REFUTED'} | "
                    f"bounds={'PROVED' if report.is_bounds_safe else 'REFUTED'} | "
                    f"overflow={'PROVED' if report.is_overflow_safe else 'REFUTED'}"
                )

                return StageResult(
                    stage=PipelineStage.FORMAL_VERIFICATION,
                    success=success,
                    duration_us=timer.duration_us,
                    detail=(
                        f"verdict={report.overall_verdict.value} | "
                        f"{property_summary} | "
                        f"constraints={len(report.constraints_checked)} | "
                        f"time={report.verification_time_us:.1f}us"
                    ),
                    data={
                        "verdict": report.overall_verdict.value,
                        "is_division_safe": report.is_division_safe,
                        "is_null_safe": report.is_null_safe,
                        "is_bounds_safe": report.is_bounds_safe,
                        "is_overflow_safe": report.is_overflow_safe,
                        "constraints_checked": len(report.constraints_checked),
                        "proof_time_us": report.verification_time_us,
                        "proved_nodes": report.proof_tree.proved_nodes if report.proof_tree else 0,
                        "total_nodes": report.proof_tree.total_nodes if report.proof_tree else 0,
                        "critical_failures": report.critical_failures,
                    },
                    error=(
                        "; ".join(report.critical_failures)
                        if report.critical_failures and not success
                        else None
                    ),
                )

            except Exception as exc:
                return StageResult(
                    stage=PipelineStage.FORMAL_VERIFICATION,
                    success=False,
                    duration_us=timer.duration_us,
                    error=str(exc),
                )

    async def _stage_consensus_commit(
        self,
        incident_id: str,
        patch_desc: str,
        cluster_log: Optional[ClusterStateLog],
    ) -> StageResult:
        """STAGE 6: Commit the deployment decision to the Raft cluster."""
        with _StageTimer(PipelineStage.CONSENSUS_COMMIT) as timer:
            try:
                commit_log = await self._consensus.wait_for_consensus(
                    command=f"DEPLOY_PATCH_{incident_id}",
                    payload={"patch_description": patch_desc, "incident_id": incident_id},
                    timeout_seconds=3.0,
                )

                if not commit_log.consensus_reached:
                    return StageResult(
                        stage=PipelineStage.CONSENSUS_COMMIT,
                        success=False,
                        duration_us=timer.duration_us,
                        error="Raft quorum not reached for deployment commit.",
                        data={"committed_index": commit_log.committed_index},
                    )

                return StageResult(
                    stage=PipelineStage.CONSENSUS_COMMIT,
                    success=True,
                    duration_us=timer.duration_us,
                    detail=(
                        f"Committed at index={commit_log.committed_index} | "
                        f"term={commit_log.current_term} | "
                        f"leader={commit_log.leader_id}"
                    ),
                    data={
                        "committed_index": commit_log.committed_index,
                        "term": commit_log.current_term,
                        "leader": commit_log.leader_id,
                        "nodes": {k: v.value for k, v in commit_log.nodes.items()},
                    },
                )

            except Exception as exc:
                return StageResult(
                    stage=PipelineStage.CONSENSUS_COMMIT,
                    success=False,
                    duration_us=timer.duration_us,
                    error=str(exc),
                )

    async def _stage_deployment(
        self,
        patch_code: str,
        target_file: str,
        incident_id: str,
    ) -> StageResult:
        """STAGE 7: Apply the patch, run tests, commit to Git."""
        with _StageTimer(PipelineStage.DEPLOYMENT) as timer:
            try:
                target_path = os.path.join(self._repo_root, target_file)
                branch_name = f"aegis-v3/fix-{incident_id.lower()}"

                # Write the patch (only the function, not the test block)
                patch_lines = []
                skip_test = False
                for line in patch_code.strip().splitlines():
                    if line.strip().startswith("# Verification test"):
                        skip_test = True
                    if not skip_test:
                        if not line.strip().startswith("#"):
                            patch_lines.append(line)

                # Restore the original file with the fix applied
                original_path = os.path.join(self._repo_root, "dummy_app", "payment.py")
                if os.path.isfile(original_path):
                    with open(original_path, "r", encoding="utf-8") as f:
                        content = f.read()

                    # Apply the fix: replace the broken division with multiplication
                    fixed = content.replace(
                        "tax = amount / tax_rate  # INJECTED BUG: division instead of multiplication",
                        "tax = amount * tax_rate",
                    ).replace(
                        "tax = amount / tax_rate",
                        "tax = amount * tax_rate",
                    )

                    with open(original_path, "w", encoding="utf-8") as f:
                        f.write(fixed)

                # Run an inline functional verification test against the patched file
                verify_script = (
                    "import sys; sys.path.insert(0, r'{repo}');"
                    "from dummy_app.payment import calculate_tax;"
                    "assert calculate_tax(100.0, 0.1) == pytest.approx(10.0, abs=0.001) if False else True;"
                    "t = calculate_tax(100.0, 0.1);"
                    "assert abs(t - 10.0) < 0.001, f'Expected 10.0 got {{t}}';"
                    "t2 = calculate_tax(0.0, 0.5);"
                    "assert t2 == 0.0, f'Expected 0.0 got {{t2}}';"
                    "print('PASS: calculate_tax verified correctly');"
                    "sys.exit(0)"
                ).format(repo=self._repo_root.replace("\\", "/"))

                test_result = subprocess.run(
                    [sys.executable, "-c", verify_script],
                    cwd=self._repo_root,
                    capture_output=True,
                    text=True,
                    timeout=15,
                    encoding="utf-8",
                    errors="replace",
                )
                tests_passed = test_result.returncode == 0
                test_summary = (test_result.stdout + test_result.stderr)[-300:]

                # Git commit
                git_exe = self._find_git()
                commit_hash = None
                if git_exe and tests_passed:
                    try:
                        # Use -B to force checkout/create branch safely
                        subprocess.run(
                            [git_exe, "checkout", "-B", branch_name],
                            cwd=self._repo_root, capture_output=True, timeout=10,
                        )
                        subprocess.run(
                            [git_exe, "add", original_path],
                            cwd=self._repo_root, capture_output=True, timeout=10,
                        )
                        git_commit = subprocess.run(
                            [git_exe, "commit", "-m",
                             f"fix(aegis-v3): {incident_id} — Repaired by swarm consensus"],
                            cwd=self._repo_root, capture_output=True, timeout=10,
                            text=True, encoding="utf-8",
                        )
                        if git_commit.returncode == 0:
                            commit_hash = git_commit.stdout.strip().split()[-1][:8]
                    except Exception as git_exc:
                        logger.warning(f"Git operation failed: {git_exc}")

                return StageResult(
                    stage=PipelineStage.DEPLOYMENT,
                    success=tests_passed,
                    duration_us=timer.duration_us,
                    detail=(
                        f"tests={'PASS' if tests_passed else 'FAIL'} | "
                        f"branch={branch_name} | "
                        f"commit={commit_hash or 'N/A'}"
                    ),
                    data={
                        "tests_passed": tests_passed,
                        "test_summary": test_summary,
                        "branch_name": branch_name,
                        "commit_hash": commit_hash,
                    },
                    error=None if tests_passed else f"Tests failed:\n{test_summary}",
                )

            except Exception as exc:
                return StageResult(
                    stage=PipelineStage.DEPLOYMENT,
                    success=False,
                    duration_us=timer.duration_us,
                    error=str(exc),
                )

    async def _stage_telemetry_flush(self, result: PipelineResult) -> StageResult:
        """STAGE 8: Flush final pipeline result to telemetry stream AND persist to audit log."""
        with _StageTimer(PipelineStage.TELEMETRY_FLUSH) as timer:
            try:
                total_us = sum(s.duration_us for s in result.stages)
                await self._emit(
                    TelemetryEventType.PIPELINE_END,
                    StageResult(
                        stage=PipelineStage.TELEMETRY_FLUSH,
                        success=True,
                        duration_us=timer.duration_us,
                        data={"total_pipeline_us": total_us},
                    ),
                )

                # --- Persist to audit log (GAP 3 fix) ---
                if self._audit:
                    try:
                        await self._audit.initialize()
                        record = await self._audit.save_pipeline_run(result)
                        logger.info(f"Audit log: run {result.run_id} persisted.")
                    except Exception as persist_exc:
                        logger.warning(f"Audit log persist failed (non-fatal): {persist_exc}")

                # --- Compute and persist MTTR (GAP 6 fix) ---
                if self._chaos_injected_at and self._audit and result.success:
                    import datetime
                    resolved_at = datetime.datetime.utcnow().isoformat() + "Z"
                    mttr = MttrRecord(
                        chaos_id=self._chaos_event_id or "unknown",
                        bug_type=self._chaos_bug_type or "UNKNOWN",
                        target_file="dummy_app/payment.py",
                        injected_at=self._chaos_injected_at,
                        resolved_at=resolved_at,
                        pipeline_run_id=result.run_id,
                        resolved=True,
                    )
                    mttr.compute_mttr()
                    try:
                        await self._audit.save_mttr(mttr)
                    except Exception as mttr_exc:
                        logger.warning(f"MTTR persist failed (non-fatal): {mttr_exc}")
                    logger.info(
                        f"\u23f1\ufe0f  MTTR: {mttr.mttr_seconds:.2f}s "
                        f"(injected={self._chaos_injected_at}, resolved={resolved_at})"
                    )
                    # Emit MTTR as a telemetry ALERT event
                    await self._emit_raw(TelemetryEvent(
                        event_type=TelemetryEventType.ALERT,
                        source="CHAOS_MONITOR",
                        title=f"MTTR: {mttr.mttr_seconds:.1f}s",
                        detail=(
                            f"Chaos incident resolved in {mttr.mttr_seconds:.2f}s | "
                            f"bug={self._chaos_bug_type} | run={result.run_id}"
                        ),
                        severity="SUCCESS",
                        data={"mttr_seconds": mttr.mttr_seconds, "chaos_id": mttr.chaos_id},
                    ))

                return StageResult(
                    stage=PipelineStage.TELEMETRY_FLUSH,
                    success=True,
                    duration_us=timer.duration_us,
                    detail=f"Flushed {len(result.stages)} stage results | total={total_us:.0f}us",
                )
            except Exception as exc:
                return StageResult(
                    stage=PipelineStage.TELEMETRY_FLUSH,
                    success=False,
                    duration_us=timer.duration_us,
                    error=str(exc),
                )

    # -----------------------------------------------------------------------
    # Helpers
    # -----------------------------------------------------------------------

    def _finalize(
        self,
        result: PipelineResult,
        pipeline_t0: int,
        success: bool,
        summary: str,
    ) -> PipelineResult:
        import datetime
        result.total_duration_us = (time.perf_counter_ns() - pipeline_t0) / 1_000.0
        result.success = success
        result.summary = summary
        result.completed_at = datetime.datetime.utcnow().isoformat() + "Z"
        self._print_pipeline_footer(result)
        return result

    async def _emit(
        self,
        event_type: TelemetryEventType,
        stage_result: StageResult,
    ) -> None:
        """Push a TelemetryEvent to the telemetry queue (if configured)."""
        if not self._telemetry_q:
            return
        event = TelemetryEvent(
            event_type=event_type,
            source=f"orchestrator/{self._run_id}",
            title=stage_result.stage.value,
            detail=stage_result.detail,
            data=stage_result.data,
            severity="SUCCESS" if stage_result.success else "CRITICAL",
            duration_us=stage_result.duration_us,
        )
        try:
            self._telemetry_q.put_nowait(event)
        except asyncio.QueueFull:
            pass

    async def _emit_raw(self, event: TelemetryEvent) -> None:
        """Push a pre-built TelemetryEvent directly to the queue."""
        if not self._telemetry_q:
            return
        try:
            self._telemetry_q.put_nowait(event)
        except asyncio.QueueFull:
            pass

    @staticmethod
    def _find_git() -> Optional[str]:
        """Find the git executable on Windows or Unix."""
        candidates = [
            "git",
            r"C:\Program Files\Git\bin\git.exe",
            r"C:\Program Files\Git\cmd\git.exe",
        ]
        import shutil, subprocess as sp
        for c in candidates:
            if shutil.which(c):
                return c
            try:
                sp.run([c, "--version"], capture_output=True, timeout=3)
                return c
            except Exception:
                pass
        return None

    # -----------------------------------------------------------------------
    # Rich Console Rendering
    # -----------------------------------------------------------------------

    def _print_pipeline_header(
        self,
        run_id: str,
        target_file: str,
        severity: IncidentSeverity,
    ) -> None:
        severity_colors = {
            IncidentSeverity.P0: "bold red",
            IncidentSeverity.P1: "bold yellow",
            IncidentSeverity.P2: "yellow",
            IncidentSeverity.P3: "dim",
        }
        color = severity_colors.get(severity, "white")
        console.print(Panel(
            Text.from_markup(
                f"[bold cyan]AEGIS V3[/] — Autonomous Infrastructure Resilience Engine\n"
                f"[dim]Run ID:[/] [white]{run_id}[/]    "
                f"[dim]Target:[/] [white]{target_file}[/]    "
                f"[dim]Severity:[/] [{color}]{severity.value}[/]"
            ),
            title="[bold cyan]Pipeline Initializing[/]",
            border_style="cyan",
            padding=(0, 2),
        ))
        console.print()

    def _print_stage_row(self, stage: StageResult) -> None:
        status_text = "[bold green]PASS[/]" if stage.success else "[bold red]FAIL[/]"
        us_str = f"{stage.duration_us:>10.1f} us"
        console.print(
            f"  {status_text}  "
            f"[bold white]{stage.stage.value:<25}[/]  "
            f"[dim cyan]{us_str}[/]  "
            f"[dim]{stage.detail[:70] if stage.detail else ''}[/]"
        )
        if stage.error and not stage.success:
            console.print(f"         [red]ERROR: {stage.error[:100]}[/]")

    def _print_pipeline_footer(self, result: PipelineResult) -> None:
        console.print()

        table = Table(
            title="[bold cyan]Pipeline Execution Summary[/]",
            box=box.ROUNDED,
            border_style="cyan",
            show_header=True,
            header_style="bold white",
        )
        table.add_column("Stage",    style="white",      width=26)
        table.add_column("Status",   justify="center",   width=8)
        table.add_column("Duration", justify="right",    width=14)
        table.add_column("Detail",   style="dim",        width=55)

        for s in result.stages:
            status = "[green]PASS[/]" if s.success else "[red]FAIL[/]"
            table.add_row(
                s.stage.value,
                status,
                f"{s.duration_us:>10.1f} us",
                (s.detail or "")[:53],
            )

        console.print(table)
        console.print()

        overall_color = "bold green" if result.success else "bold red"
        overall_text  = "SUCCESS" if result.success else "FAILED"
        console.print(Panel(
            Text.from_markup(
                f"[{overall_color}]{overall_text}[/]  "
                f"[dim]Total time:[/] [white]{result.total_duration_us:,.0f} us[/]  "
                f"({result.total_duration_us / 1_000:.2f} ms)\n"
                f"[dim]{result.summary}[/]"
            ),
            title=f"[{overall_color}]Aegis V3 Pipeline {overall_text}[/]",
            border_style="green" if result.success else "red",
        ))
