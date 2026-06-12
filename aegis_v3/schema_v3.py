"""
==============================================================================
Aegis V3: Enterprise Autonomous Infrastructure Resilience Engine
aegis_v3/schema_v3.py — Complete V3 Typed Data Contracts
==============================================================================

All Pydantic v2 models for the V3 pipeline. Covers:
  - Wasm sandbox execution traces and compilation results
  - Distributed Raft cluster state (heartbeats, votes, logs)
  - Formal verification proof trees and constraint records
  - Orchestrator pipeline stage results and telemetry events
==============================================================================
"""
from __future__ import annotations

import datetime
import uuid
from enum import Enum
from typing import Any, Optional
from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Shared
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.datetime.utcnow().isoformat() + "Z"


def _new_id() -> str:
    return str(uuid.uuid4())[:12]


# ---------------------------------------------------------------------------
# Wasm Sandbox
# ---------------------------------------------------------------------------

class SandboxStatus(str, Enum):
    COMPILED    = "COMPILED"
    EXECUTED    = "EXECUTED"
    TIMED_OUT   = "TIMED_OUT"
    MEMORY_EXCEEDED = "MEMORY_EXCEEDED"
    DENIED      = "DENIED"    # Denylist violation
    FAULT       = "FAULT"     # Runtime exception inside sandbox


class SandboxConfig(BaseModel):
    """Execution boundary configuration for the Wasm isolation engine."""
    max_execution_us: int    = Field(default=5_000,   description="Max wall-clock time in microseconds.")
    max_memory_kb: int       = Field(default=2_048,   description="Max resident memory in kilobytes.")
    max_ast_nodes: int       = Field(default=500,     description="Max AST nodes (complexity guard).")
    max_output_bytes: int    = Field(default=4_096,   description="Max bytes written to sandbox stdout.")
    allow_imports: list[str] = Field(default_factory=list, description="Explicitly whitelisted stdlib imports.")
    deny_builtins: list[str] = Field(
        default_factory=lambda: [
            "eval", "exec", "compile", "__import__", "open",
            "breakpoint", "input", "memoryview", "globals", "locals",
        ],
        description="Builtins blocked inside the sandbox.",
    )


class CompilationResult(BaseModel):
    """Result of the Wasm-simulated compilation step."""
    patch_id: str           = Field(default_factory=_new_id)
    status: SandboxStatus
    bytecode_size_bytes: int = Field(default=0)
    ast_node_count: int      = Field(default=0)
    compilation_us: float    = Field(default=0.0, description="Compilation wall-clock time in microseconds.")
    violation_detail: Optional[str] = None
    timestamp: str           = Field(default_factory=_now_iso)


class ExecutionTrace(BaseModel):
    """Full execution trace from the isolated sandbox run."""
    patch_id: str
    status: SandboxStatus
    stdout_captured: str     = Field(default="")
    return_value: Optional[Any] = None
    peak_memory_kb: float    = Field(default=0.0)
    execution_us: float      = Field(default=0.0)
    fault_traceback: Optional[str] = None
    security_violations: list[str] = Field(default_factory=list)
    timestamp: str           = Field(default_factory=_now_iso)


# ---------------------------------------------------------------------------
# Distributed Consensus (Raft)
# ---------------------------------------------------------------------------

class NodeRole(str, Enum):
    FOLLOWER  = "FOLLOWER"
    CANDIDATE = "CANDIDATE"
    LEADER    = "LEADER"
    OFFLINE   = "OFFLINE"


class RaftLogEntry(BaseModel):
    """A single committed entry in the Raft distributed log."""
    index: int
    term: int
    command: str
    payload: dict[str, Any] = Field(default_factory=dict)
    committed_at: str        = Field(default_factory=_now_iso)


class HeartbeatMessage(BaseModel):
    """Periodic leader-to-follower liveness signal (AppendEntries RPC with no entries)."""
    term: int               = Field(..., description="Leader's current term.")
    leader_id: str          = Field(..., description="Node ID of the current leader.")
    prev_log_index: int     = Field(default=0)
    prev_log_term: int      = Field(default=0)
    leader_commit: int      = Field(default=0, description="Leader's commit index.")
    entries: list[RaftLogEntry] = Field(default_factory=list, description="Empty for heartbeat.")
    sent_at: str            = Field(default_factory=_now_iso)


class LeaderElectionVote(BaseModel):
    """RequestVote RPC — sent by a CANDIDATE to all peers."""
    term: int               = Field(..., description="Candidate's term.")
    candidate_id: str       = Field(..., description="Candidate node ID.")
    last_log_index: int     = Field(default=0)
    last_log_term: int      = Field(default=0)
    vote_granted: bool      = Field(default=False, description="Populated in the response.")
    voter_id: Optional[str] = Field(default=None, description="Responder node ID.")
    sent_at: str            = Field(default_factory=_now_iso)


class ClusterStateLog(BaseModel):
    """Snapshot of the entire cluster state at a point in time."""
    snapshot_id: str        = Field(default_factory=_new_id)
    current_term: int       = Field(default=0)
    leader_id: Optional[str] = None
    committed_index: int    = Field(default=0)
    nodes: dict[str, NodeRole] = Field(default_factory=dict)
    log_entries: list[RaftLogEntry] = Field(default_factory=list)
    quorum_size: int        = Field(default=0)
    consensus_reached: bool = Field(default=False)
    captured_at: str        = Field(default_factory=_now_iso)


class NodeConfig(BaseModel):
    """Configuration for a single Raft cluster node."""
    node_id: str
    election_timeout_ms: float = Field(default=150.0, description="Randomized election timeout.")
    heartbeat_interval_ms: float = Field(default=50.0)
    simulate_failure: bool   = Field(default=False, description="If True, node randomly drops messages.")
    failure_probability: float = Field(default=0.0, ge=0.0, le=1.0)


# ---------------------------------------------------------------------------
# Formal Verification
# ---------------------------------------------------------------------------

class ConstraintType(str, Enum):
    DIVISION_SAFETY  = "DIVISION_SAFETY"   # denominator != 0
    NULL_SAFETY      = "NULL_SAFETY"       # value is not None
    BOUNDS_SAFETY    = "BOUNDS_SAFETY"     # index within range
    OVERFLOW_SAFETY  = "OVERFLOW_SAFETY"   # no integer overflow
    TYPE_INVARIANT   = "TYPE_INVARIANT"    # type coherence
    POSTCONDITION    = "POSTCONDITION"     # function result invariant
    PRECONDITION     = "PRECONDITION"      # caller must ensure


class ProofStatus(str, Enum):
    PROVED   = "PROVED"    # Constraint satisfied under all inputs
    REFUTED  = "REFUTED"   # Counter-example found
    UNKNOWN  = "UNKNOWN"   # Solver timed out or inconclusive


class Constraint(BaseModel):
    """A single logical constraint extracted from an AI-generated patch."""
    constraint_id: str      = Field(default_factory=_new_id)
    constraint_type: ConstraintType
    description: str
    expression: str         = Field(..., description="Python expression representing the constraint.")
    source_line: int        = Field(default=0)
    source_function: str    = Field(default="<module>")
    # Interval arithmetic bounds
    lower_bound: Optional[float] = None
    upper_bound: Optional[float] = None


class ProofNode(BaseModel):
    """A node in the formal proof tree (goal decomposition)."""
    node_id: str            = Field(default_factory=_new_id)
    goal: str               = Field(..., description="Logical goal to be proved at this node.")
    status: ProofStatus     = ProofStatus.UNKNOWN
    tactic: str             = Field(default="", description="Proof tactic applied (e.g., 'interval_arithmetic').")
    sub_goals: list["ProofNode"] = Field(default_factory=list)
    counter_example: Optional[dict[str, Any]] = None
    proof_time_us: float    = Field(default=0.0)
    depth: int              = Field(default=0)


class ProofTree(BaseModel):
    """The complete proof tree for a patch's safety properties."""
    tree_id: str            = Field(default_factory=_new_id)
    patch_description: str
    root: Optional[ProofNode] = None
    total_nodes: int        = Field(default=0)
    proved_nodes: int       = Field(default=0)
    refuted_nodes: int      = Field(default=0)
    total_proof_time_us: float = Field(default=0.0)
    verdict: ProofStatus    = ProofStatus.UNKNOWN


class VerificationReport(BaseModel):
    """Complete formal verification report for an AI-generated patch."""
    report_id: str          = Field(default_factory=_new_id)
    patch_summary: str
    constraints_checked: list[Constraint] = Field(default_factory=list)
    proof_tree: Optional[ProofTree] = None
    overall_verdict: ProofStatus = ProofStatus.UNKNOWN
    is_division_safe: bool  = False
    is_null_safe: bool      = False
    is_bounds_safe: bool    = False
    is_overflow_safe: bool  = False
    critical_failures: list[str] = Field(default_factory=list)
    verification_time_us: float = Field(default=0.0)
    generated_at: str       = Field(default_factory=_now_iso)


# ---------------------------------------------------------------------------
# Orchestrator Pipeline
# ---------------------------------------------------------------------------

class PipelineStage(str, Enum):
    FAULT_INJECTION        = "FAULT_INJECTION"
    CLUSTER_COORDINATION   = "CLUSTER_COORDINATION"
    PATCH_GENERATION       = "PATCH_GENERATION"
    WASM_SANDBOX           = "WASM_SANDBOX"
    FORMAL_VERIFICATION    = "FORMAL_VERIFICATION"
    CONSENSUS_COMMIT       = "CONSENSUS_COMMIT"
    DEPLOYMENT             = "DEPLOYMENT"
    TELEMETRY_FLUSH        = "TELEMETRY_FLUSH"


class IncidentSeverity(str, Enum):
    P0 = "P0"   # Total outage
    P1 = "P1"   # Severe degradation
    P2 = "P2"   # Partial impact
    P3 = "P3"   # Minor anomaly


class StageResult(BaseModel):
    """Result of a single orchestrator pipeline stage."""
    stage: PipelineStage
    success: bool
    duration_us: float
    detail: str             = Field(default="")
    data: dict[str, Any]    = Field(default_factory=dict)
    error: Optional[str]    = None


class PipelineResult(BaseModel):
    """Full end-to-end pipeline execution result."""
    run_id: str             = Field(default_factory=_new_id)
    incident_id: str        = Field(default="")
    severity: IncidentSeverity = IncidentSeverity.P1
    total_duration_us: float = Field(default=0.0)
    stages: list[StageResult] = Field(default_factory=list)
    success: bool           = False
    deployed_branch: Optional[str] = None
    summary: str            = Field(default="")
    started_at: str         = Field(default_factory=_now_iso)
    completed_at: Optional[str] = None


# ---------------------------------------------------------------------------
# Real-Time Telemetry
# ---------------------------------------------------------------------------

class TelemetryEventType(str, Enum):
    CLUSTER_STATE     = "CLUSTER_STATE"
    SANDBOX_RESULT    = "SANDBOX_RESULT"
    PROOF_UPDATE      = "PROOF_UPDATE"
    STAGE_COMPLETE    = "STAGE_COMPLETE"
    RESOURCE_SNAPSHOT = "RESOURCE_SNAPSHOT"
    PIPELINE_START    = "PIPELINE_START"
    PIPELINE_END      = "PIPELINE_END"
    NODE_ELECTION     = "NODE_ELECTION"
    ALERT             = "ALERT"


class ResourceSnapshot(BaseModel):
    """Real-time compute resource usage snapshot."""
    cpu_percent: float      = Field(default=0.0)
    memory_mb: float        = Field(default=0.0)
    memory_percent: float   = Field(default=0.0)
    active_tasks: int       = Field(default=0)
    event_queue_depth: int  = Field(default=0)
    uptime_seconds: float   = Field(default=0.0)


class TelemetryEvent(BaseModel):
    """A single real-time telemetry event broadcast to the dashboard."""
    event_id: str           = Field(default_factory=_new_id)
    event_type: TelemetryEventType
    source: str             = Field(default="SYSTEM")
    title: str
    detail: str             = Field(default="")
    data: dict[str, Any]    = Field(default_factory=dict)
    severity: str           = Field(default="INFO")  # INFO | WARN | CRITICAL | SUCCESS
    duration_us: Optional[float] = None
    timestamp: str          = Field(default_factory=_now_iso)


# ---------------------------------------------------------------------------
# MTTR (Mean Time To Recovery) Record
# ---------------------------------------------------------------------------

class MttrRecord(BaseModel):
    """Captures the full lifecycle of a chaos-injected incident for MTTR measurement."""
    record_id: str            = Field(default_factory=_new_id)
    chaos_id: str             = Field(..., description="ChaosEvent chaos_id that triggered this incident.")
    bug_type: str             = Field(..., description="BugType enum value.")
    target_file: str
    injected_at: str          = Field(..., description="ISO timestamp when bug was injected.")
    resolved_at: Optional[str] = Field(default=None, description="ISO timestamp when pipeline succeeded.")
    mttr_seconds: Optional[float] = Field(default=None, description="Computed MTTR in seconds.")
    pipeline_run_id: Optional[str] = Field(default=None)
    resolved: bool            = Field(default=False)

    def compute_mttr(self) -> None:
        """Compute and store MTTR from injected_at and resolved_at timestamps."""
        if self.resolved_at and self.injected_at:
            import datetime
            fmt = "%Y-%m-%dT%H:%M:%S.%f"
            def _parse(s: str) -> datetime.datetime:
                s = s.rstrip("Z")
                try:
                    return datetime.datetime.fromisoformat(s)
                except ValueError:
                    return datetime.datetime.strptime(s[:26], fmt)
            try:
                delta = _parse(self.resolved_at) - _parse(self.injected_at)
                self.mttr_seconds = max(0.0, delta.total_seconds())
            except Exception:
                self.mttr_seconds = None


# ---------------------------------------------------------------------------
# Pipeline Run Record (for SQLite audit log)
# ---------------------------------------------------------------------------

class PipelineRunRecord(BaseModel):
    """SQLite-serializable snapshot of a completed pipeline run."""
    run_id: str
    incident_id: str          = Field(default="")
    severity: str             = Field(default="P1")
    success: bool             = False
    total_duration_us: float  = Field(default=0.0)
    stage_count: int          = Field(default=0)
    deployed_branch: Optional[str] = None
    summary: str              = Field(default="")
    started_at: str           = Field(default_factory=_now_iso)
    completed_at: Optional[str] = None
    stages_json: str          = Field(default="[]", description="JSON-serialized list of StageResult dicts.")
    mttr_seconds: Optional[float] = None
    chaos_bug_type: Optional[str] = None


# Rebuild ProofNode for self-referential model
ProofNode.model_rebuild()
