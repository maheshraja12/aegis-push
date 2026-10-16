# 🛡️ Aegis V3 — Autonomous Infrastructure Resilience Engine

<div align="center">

<img src="https://img.shields.io/badge/Python-3.10%2B-3776AB?style=for-the-badge&logo=python&logoColor=white"/>
<img src="https://img.shields.io/badge/FastAPI-0.111-009688?style=for-the-badge&logo=fastapi&logoColor=white"/>
<img src="https://img.shields.io/badge/OpenAI-GPT--4o-412991?style=for-the-badge&logo=openai&logoColor=white"/>
<img src="https://img.shields.io/badge/Pydantic-v2-E92063?style=for-the-badge&logo=pydantic&logoColor=white"/>
<a href="https://github.com/maheshraja12/aegis-push/actions/workflows/ci.yml"><img src="https://github.com/maheshraja12/aegis-push/actions/workflows/ci.yml/badge.svg?branch=main" alt="AEGIS CI status"/></a>
<img src="https://img.shields.io/badge/License-MIT-FFC107?style=for-the-badge"/>
<img src="https://img.shields.io/badge/Status-Experimental-orange?style=for-the-badge"/>

<br/><br/>

> **An experimental Python project exploring AI-assisted repair workflows, sandbox checks, verification heuristics, simulated consensus, and telemetry. It is not yet validated for autonomous production remediation.**

<br/>

[🚀 Quick Start](#-quick-start) •
[🧠 How It Works](#-how-it-works) •
[📁 Project Structure](#-project-structure) •
[🖥️ Local Dashboard](#️-local-dashboard) •
[📡 API Reference](#-api-reference) •
[🧪 Testing](#-testing) •
[⚙️ Configuration](#️-configuration) •
[🗺️ Roadmap](#️-roadmap)

</div>

---

## 📌 Table of Contents

1. [What is Aegis V3?](#-what-is-aegis-v3)
2. [The Problem It Solves](#-the-problem-it-solves)
3. [Key Features](#-key-features)
4. [System Architecture](#-system-architecture)
5. [The 8-Stage Pipeline](#-the-8-stage-pipeline)
6. [Core Modules](#-core-modules)
7. [Quick Start](#-quick-start)
8. [Running Modes](#-running-modes)
9. [Local Dashboard](#️-local-dashboard)
10. [API Reference](#-api-reference)
11. [WebSocket Streams](#-websocket-streams)
12. [Testing](#-testing)
13. [Configuration](#️-configuration)
14. [Security Design](#-security-design)
15. [Performance Measurements](#-performance-measurements)
16. [Project Structure](#-project-structure)
17. [Dependencies](#-dependencies)
18. [Roadmap](#️-roadmap)
19. [Contributing](#-contributing)
20. [License](#-license)

---

## 🎯 What is Aegis V3?

**AEGIS V3 is an experimental Python research prototype** for exploring AI-assisted incident repair workflows. It coordinates patch generation or demo patches, static checks, heuristic verification, simulated consensus, guarded deployment logic, and telemetry.

Current implementation boundaries:

- **Patch generation:** optional OpenAI integration; a hard-coded demo patch is used in simulation mode.
- **Execution checks:** Python AST/bytecode checks and a child process with timeout handling. This is **not a WebAssembly runtime or a secure sandbox**.
- **Verification:** interval/rule-based checks are heuristics, not a sound formal proof of program correctness.
- **Consensus:** a simulated/custom coordinator used for experimentation; it is not independently certified as a production Raft implementation.
- **Deployment:** demo deployment is disabled by default and must remain limited to a disposable test checkout.
- **Observability:** stage results and telemetry help inspect the prototype; they do not establish reliability or an SLA.

**Safety status:** Do not connect AEGIS to production or enable unattended remediation. CI only reports whether the configured automated checks passed. It does not prove sandbox escape resistance, verifier soundness, rollback correctness, or production readiness.

## 💀 The Problem It Explores

Automated repair systems aim to shorten incident diagnosis and recovery. This repository explores pieces of that workflow in a controlled demonstration; it does not claim measured production downtime or financial savings.

### Demonstration only

The pipeline can simulate a fault in the included dummy application, process a candidate patch, record stage outcomes, and exercise deployment guards. Any timings shown by a local run are environment-dependent measurements, not service-level guarantees. The project must not be used to inject faults into production systems.

## ✨ Key Features

### 🤖 AI-Powered Patch Generation
- Uses the optional OpenAI API to propose candidate patches for controlled demo workflows
- Falls back to **simulation mode** with hardcoded demo patches when no API key is present
- Supports context-aware patch generation using the full error trace

### 🧪 Python Patch-Execution Checks

- Performs AST and bytecode checks before attempting execution.
- Uses a dedicated child process and terminates it when the configured wall-clock timeout is exceeded.
- Captures output and records execution traces for debugging.
- Uses tracemalloc to observe Python allocations; this is **not a hard memory limit**.
- **Important:** this is defense-in-depth for a demo, not a security boundary. It does not provide comprehensive OS/container isolation or guaranteed filesystem/network restrictions. Do not execute untrusted patches outside a disposable environment.

### 🔎 Heuristic Verification Engine

- Applies interval/rule-based checks to selected classes of risky expressions.
- Can return PROVED, REFUTED, or UNKNOWN according to the implementation's checks.
- These results are **not a formal proof of arbitrary program correctness**. Soundness and completeness have not been established, and the checks must not be treated as a substitute for tests, review, or a mature verification tool.

### 🗳️ Experimental Consensus Coordinator

- Exercises leader-election, voting, and persistence concepts in a simulated cluster.
- Useful for experimenting with recovery workflows and telemetry.
- Not independently validated as a production-grade Raft implementation; do not use its result as authorization to deploy a patch to live infrastructure.

### 🐒 Chaos Engineering (Chaos Monkey)
- **AST-based fault injection** — modifies Python source code at the syntax tree level
- Supports 5 bug types:
  - `ARITHMETIC` — flips `*` to `/` (or `+` to `-`)
  - `OFF_BY_ONE` — shifts loop bounds by ±1
  - `ZERO_DIVISION` — introduces unguarded division
  - `NULL_DEREFERENCE` — removes None guards
  - `INDEX_OUT_OF_BOUNDS` — shifts index access
- Full **restore capability** — original file content preserved for exact rollback
- Records `injected_at` timestamp for **MTTR measurement**

### 📊 Real-Time Telemetry Dashboard
- **FastAPI** backend with async WebSocket streaming
- Beautiful **glassmorphism UI** with live event feed
- Two WebSocket channels:
  - `/ws/telemetry` — pipeline events (stage start/end, alerts)
  - `/ws/metrics` — system resources (CPU, memory, uptime) every 1 second
- REST API for triggering pipelines, checking health, querying history

### 💾 Persistent Audit Log
- Every `PipelineResult` saved to **SQLite** via `aiosqlite` (fully async)
- Survives process restarts — full history available at `/api/history`
- **MTTR records** stored separately — queryable aggregate stats at `/api/mttr/stats`
- Raft node state persisted — `currentTerm`, `votedFor`, log entries

### ⏱️ MTTR Tracking (Mean Time To Recovery)
- Automatically measures time from **bug injection → successful deployment**
- `MttrRecord` stores full incident lifecycle
- `compute_mttr()` calculates elapsed time from the recorded incident and resolution timestamps; accuracy depends on those event definitions.
- Emitted as a live telemetry `ALERT` event on resolution
- Aggregate stats: min/max/avg MTTR across all resolved incidents

### 📋 Structured JSON Logging
- **Datadog**, **Grafana Loki**, **AWS CloudWatch**, **ELK Stack** compatible
- One JSON object per log line with fields: `timestamp`, `level`, `logger`, `message`, `run_id`, `module`, `line`
- Rotating file handler (10MB per file, 5 backups)
- Toggle with `LOG_FORMAT=json` environment variable

### 🏥 Health & Readiness Probes
- `GET /health` — Kubernetes liveness probe (returns 200 if server alive)
- `GET /ready` — Kubernetes readiness probe (checks all 5 subsystems, returns 503 if any fail)
- Compatible with: nginx, AWS ALB, GCP Load Balancer, Kubernetes, Datadog Synthetics

### 🔁 Circuit Breaker with Retry
- `_retry_subprocess()` wraps all critical subprocess calls
- **Exponential backoff** with ±25% jitter
- Default: 3 attempts, 500ms base delay
- Prevents single pytest flake from killing the entire deployment pipeline

---

## 🏗️ System Architecture

```
╔══════════════════════════════════════════════════════════════════════════════╗
║                        AEGIS V3 — SYSTEM ARCHITECTURE                       ║
╠══════════════════════════════════════════════════════════════════════════════╣
║                                                                              ║
║   ┌─────────────────────────────────────────────────────────────────────┐   ║
║   │                    run_aegis_v3.py (Entry Point)                    │   ║
║   │  --mode pipeline | --mode dashboard | --mode chaos | --mode verify  │   ║
║   └────────────────────────────┬────────────────────────────────────────┘   ║
║                                │                                            ║
║   ┌────────────────────────────▼────────────────────────────────────────┐   ║
║   │                    AegisV3Orchestrator                              │   ║
║   │              (aegis_v3/orchestrator.py)                             │   ║
║   │                                                                     │   ║
║   │  ┌──────────────┐  ┌──────────────┐  ┌──────────────────────────┐  │   ║
║   │  │   Stage 1    │  │   Stage 2    │  │        Stage 3           │  │   ║
║   │  │    FAULT     │  │   CLUSTER    │  │   PATCH_GENERATION       │  │   ║
║   │  │  INJECTION   │  │    COORD     │  │   (OpenAI GPT-4o or      │  │   ║
║   │  │              │  │  (5-node     │  │    simulation patch)     │  │   ║
║   │  │ ChaosMonkey  │  │    Raft)     │  │                          │  │   ║
║   │  └──────┬───────┘  └──────┬───────┘  └───────────┬──────────────┘  │   ║
║   │         │                 │                       │                 │   ║
║   │  ┌──────▼───────┐  ┌──────▼───────┐  ┌───────────▼──────────────┐  │   ║
║   │  │   Stage 4    │  │   Stage 5    │  │        Stage 6           │  │   ║
║   │  │   PYTHON     │  │  HEURISTIC   │  │   SIMULATED CONSENSUS    │  │   ║
║   │  │   CHECKS     │◄─►  CHECKS     │  │   (experimental quorum) │  │   ║
║   │  │  (parallel)  │  │  (parallel)  │  │                          │  │   ║
║   │  └──────┬───────┘  └──────┬───────┘  └───────────┬──────────────┘  │   ║
║   │         │                 │                       │                 │   ║
║   │  ┌──────▼───────────────────────────────────────▼──────────────┐  │   ║
║   │  │                       Stage 7                                │  │   ║
║   │  │                     DEPLOYMENT                               │  │   ║
║   │  │     (apply patch → retry subprocess → run tests → git)       │  │   ║
║   │  └─────────────────────────────┬────────────────────────────────┘  │   ║
║   │                                │                                    │   ║
║   │  ┌─────────────────────────────▼────────────────────────────────┐  │   ║
║   │  │                       Stage 8                                │  │   ║
║   │  │                  TELEMETRY_FLUSH                             │  │   ║
║   │  │     (AuditLogBackend.save() + MTTR compute + WS emit)        │  │   ║
║   │  └──────────────────────────────────────────────────────────────┘  │   ║
║   └─────────────────────────────────────────────────────────────────────┘   ║
║                                                                              ║
║   ┌─────────────────────┐    ┌─────────────────────────────────────────┐    ║
║   │   persistence.py    │    │         realtime_telemetry.py           │    ║
║   │                     │    │                                         │    ║
║   │ • AuditLogBackend   │    │  GET  /health      (liveness probe)     │    ║
║   │   pipeline_runs     │    │  GET  /ready       (readiness probe)    │    ║
║   │   mttr_records      │    │  GET  /api/status  (live snapshot)      │    ║
║   │                     │    │  GET  /api/history (run history)        │    ║
║   │ • RaftPersistence   │    │  GET  /api/mttr/stats                   │    ║
║   │   currentTerm       │    │  POST /api/pipeline/run                 │    ║
║   │   votedFor          │    │  POST /api/chaos/inject                 │    ║
║   │   log entries       │    │  WS   /ws/telemetry                     │    ║
║   │                     │    │  WS   /ws/metrics                       │    ║
║   │  aegis_v3_audit.db  │    │  GET  /docs  (Swagger UI)               │    ║
║   └─────────────────────┘    └─────────────────────────────────────────┘    ║
╚══════════════════════════════════════════════════════════════════════════════╝
```

---

## 🔄 The 8-Stage Pipeline

In simulation mode, the prototype can exercise an **8-stage workflow**. Stage names describe the experimental pipeline, not a production-validated self-healing guarantee:

### Stage 1: FAULT_INJECTION 🐒
```python
# ChaosMonkey injects an AST-level bug into the target file
monkey = ChaosMonkey(repo_root)
event = monkey.inject_bug("dummy_app")
# Records injected_at timestamp for MTTR measurement
```
**What happens:** The Chaos Monkey opens the target Python file, parses it into an AST, selects a mutation (e.g. replace `*` with `/`), applies it, and writes the mutated file back. The original content is preserved for restore.

**Output:** `ChaosEvent` with `bug_type`, `injection_description`, `injected_at`, `original_content`

---

### Stage 2: CLUSTER_COORDINATION 🗳️
```python
# 5-node Raft cluster starts up and elects a leader
coordinator = AgentClusterCoordinator(node_count=5)
await coordinator.start_cluster()
cluster_log = await coordinator.wait_for_consensus(
    command="INCIDENT_OPEN_<id>", timeout_seconds=5.0
)
```
**What happens:** 5 asyncio tasks start simultaneously, each simulating a Raft node. Randomized election timeouts (150-300ms scaled) trigger a leader election. The elected leader broadcasts heartbeats. All nodes restore their `currentTerm` and `votedFor` from SQLite on startup.

**Output:** `ClusterStateLog` with `leader_id`, `current_term`, `consensus_reached`, `committed_index`

---

### Stage 3: PATCH_GENERATION 🤖
```python
# AI generates a fix for the detected fault
if api_key:
    patch = await generate_with_openai(fault_description, broken_code)
else:
    patch = SIMULATION_PATCH  # hardcoded demo fix
```
**What happens:** The broken code + error trace is sent to GPT-4o with a structured prompt asking for a safe Python fix. The response is parsed and validated. In simulation mode, a hardcoded patch (correct `tax = amount * tax_rate`) is used.

**Output:** Python source code string of the fix

---

### Stage 4: WASM_SANDBOX 🧪 (legacy stage name; Python checks, not WebAssembly)
```python
engine = WasmIsolationEngine(SandboxConfig(...))
compilation = await engine.compile_patch(patch_code, patch_id)
if compilation.status == SandboxStatus.COMPILED:
    trace = await engine.execute_isolated(patch_code, compilation)
```
**What happens:**
1. **AST Parse** — catches syntax errors immediately
2. **Security Visitor** — walks AST for blocked nodes (imports, eval, exec, dunder access)
3. **Bytecode Compile** — compiles to Python bytecode
4. **Opcode Audit** — scans bytecode for `IMPORT_NAME`/`IMPORT_FROM` opcodes
5. **Isolated Execution** — runs in a child process with restricted `__builtins__`
6. **Memory Tracking** — `tracemalloc` measures peak allocation
7. **Timeout Enforcement** — kills child process if execution exceeds limit

**Output:** `CompilationResult` + `ExecutionTrace` with timing, memory, return value

---

### Stage 5: HEURISTIC_VERIFICATION 🔬 (legacy runtime stage name; runs in parallel with Stage 4)
```python
engine = FormalVerificationEngine()
engine.declare_variable("amount",   lo=0.0, hi=1_000_000.0)
engine.declare_variable("tax_rate", lo=0.0, hi=1.0)
report = await engine.verify_patch(patch_code, description)
```
**What happens:**
1. **Variable domain declaration** — establishes valid input ranges
2. **Constraint extraction** — scans AST for division ops, comparisons, subscripts, large literals
3. **Interval arithmetic solving** — evaluates each constraint over the declared domains
4. **Heuristic verdict computation** — returns `PROVED`, `REFUTED`, or `UNKNOWN` according to implemented rules; these labels are not a formal correctness proof

**Output:** `VerificationReport` with `overall_verdict`, `is_division_safe`, `constraints_checked`, `critical_failures`

---

### Stage 6: CONSENSUS_COMMIT 🗳️
```python
commit_log = await coordinator.wait_for_consensus(
    command=f"DEPLOY_PATCH_{incident_id}",
    payload={"patch_description": desc, "incident_id": incident_id},
    timeout_seconds=3.0,
)
```
**What happens:** The patch deployment command is submitted to the Raft leader's log. The leader broadcasts it to all followers via AppendEntries RPCs. Once 3/5 nodes acknowledge (`acks >= quorum`), the entry is committed. This commit is persisted to SQLite.

**Output:** `ClusterStateLog` with `consensus_reached=True`, `committed_index`

---

### Stage 7: DEPLOYMENT 🚀
```python
# Apply fix, run tests with retry, commit to git
result = await _retry_subprocess(
    [sys.executable, "-m", "pytest", ...],
    max_attempts=3, backoff_base_ms=500
)
if result.returncode == 0:
    git_commit(branch_name=f"aegis-v3/fix-{incident_id}")
```
**What happens:**
1. Parses the AI patch to extract only the function (strips test blocks)
2. Applies the fix to the target file using string replacement
3. Runs an inline functional verification test
4. Retries up to 3 times with exponential backoff if tests flake
5. Creates a Git branch `aegis-v3/fix-<incident_id>` and commits

**Output:** `StageResult` with `tests_passed`, `branch_name`, `commit_hash`

---

### Stage 8: TELEMETRY_FLUSH 📡
```python
# Persist run to audit log
await audit_backend.save_pipeline_run(result)

# Compute and store MTTR
mttr.compute_mttr()
await audit_backend.save_mttr(mttr)

# Emit MTTR as live telemetry alert
await emit_raw(TelemetryEvent(event_type=ALERT, ...))
```
**What happens:** The complete `PipelineResult` is serialized and written to SQLite. If a chaos event was injected, the MTTR is computed as `resolved_at - injected_at` and stored. A live alert is emitted to all connected WebSocket clients showing the MTTR.

**Output:** Audit log entry + MTTR record + WebSocket broadcast

---

## 🔩 Core Modules

### `aegis_v3/orchestrator.py` — Pipeline Coordinator
The central runtime. Manages an 8-stage experimental pipeline with `perf_counter_ns()` timing. Coordinates Python patch-execution checks and heuristic verification (Stages 4+5 run concurrently), plus audit logging and MTTR tracking.

**Key classes:** `AegisV3Orchestrator`, `_StageTimer`
**Key functions:** `run_full_pipeline()`, `_retry_subprocess()`, `_emit_raw()`

---

### `aegis_v3/wasm_sandbox.py` — Python Patch-Execution Checks (legacy module name)
Simulates a WebAssembly runtime's security model in Python. Uses `ProcessPoolExecutor` for true OS-level process isolation. Two-phase security check: AST static analysis then bytecode opcode audit.

**Key classes:** `WasmIsolationEngine`, `SandboxConfig`, `_DenylistVisitor`
**Key functions:** `compile_patch()`, `execute_isolated()`, `run_full_isolation_pipeline()`, `_audit_bytecode()`

---

### `aegis_v3/formal_verification.py` — Heuristic Verification Engine
Implements an interval arithmetic solver that mimics SMT (Satisfiability Modulo Theories) verification. Walks the patch AST to extract safety constraints, then evaluates each over declared variable domains.

**Key classes:** `FormalVerificationEngine`, `_IntervalSolver`, `_ConstraintExtractor`
**Key functions:** `verify_patch()`, `declare_variable()`, `_solve_constraint()`

---

### `aegis_v3/distributed_consensus.py` — Raft Cluster
Experimental consensus coordinator using asyncio tasks as simulated nodes. It models leader election, log replication, quorum decisions, heartbeats, and SQLite persistence for learning/testing; it is not a production-validated Raft implementation.

**Key classes:** `AgentClusterCoordinator`, `_NodeState`
**Key functions:** `start_cluster()`, `wait_for_consensus()`, `_follower_loop()`, `_candidate_loop()`, `_leader_loop()`

---

### `aegis_v3/chaos_monkey.py` — Fault Injector
AST-based mutation engine. Parses target Python files into syntax trees, applies targeted mutations, writes the mutated code back, and preserves the original for rollback.

**Key classes:** `ChaosMonkey`, `ChaosEvent`, `BugType`
**Key functions:** `inject_bug()`, `restore()`, `_mutate_arithmetic()`, `_inject_zero_division()`

---

### `aegis_v3/realtime_telemetry.py` — FastAPI Dashboard
Full-featured async web server. Serves the monitoring UI, streams live events via WebSocket, exposes REST endpoints for control and history, runs background CPU/memory metrics collection.

**Key endpoints:** `/health`, `/ready`, `/api/status`, `/api/history`, `/api/mttr/stats`, `/api/pipeline/run`, `/ws/telemetry`, `/ws/metrics`

---

### `aegis_v3/persistence.py` — Audit Storage
Async SQLite backend using `aiosqlite`. Two independent storage backends: `AuditLogBackend` for pipeline runs and MTTR records, `RaftPersistenceBackend` for Raft node durable state.

**Key classes:** `AuditLogBackend`, `RaftPersistenceBackend`
**Key functions:** `save_pipeline_run()`, `get_recent_runs()`, `save_mttr()`, `get_mttr_stats()`, `persist_state()`, `load_state()`

---

### `aegis_v3/schema_v3.py` — Type Contracts
All Pydantic v2 models for the entire system. Strict typing with validators, field aliases, and computed properties.

**Key models:** `PipelineResult`, `StageResult`, `ChaosEvent`, `ClusterStateLog`, `VerificationReport`, `TelemetryEvent`, `MttrRecord`, `PipelineRunRecord`, `SandboxConfig`, `NodeConfig`, `RaftLogEntry`

---

### `aegis_v3/logging_config.py` — Structured Logging
JSON-format logging module compatible with all major log aggregation platforms. Rotating file handler, noise suppression for third-party loggers.

**Key functions:** `configure_logging(level, json_format, log_file)`

---

## 🚀 Quick Start

### Prerequisites
- Python 3.10 or higher
- Git
- (Optional) OpenAI API key for real AI patches

### Installation

```bash
# 1. Clone the repository
git clone https://github.com/maheshraja12/aegis-push.git
cd aegis-push

# 2. Create virtual environment (recommended)
python -m venv .venv

# Windows
.venv\Scripts\activate

# Linux / Mac
source .venv/bin/activate

# 3. Install all dependencies
pip install -r requirements_v3.txt

# 4. Verify installation
python -c "import aegis_v3; print('Aegis V3 ready!')"
```

---

## 🎮 Running Modes

### Mode 1: Pipeline (Console — Simulation)
Run the experimental simulation workflow in your terminal with rich colored output. **No API key is needed for simulation mode.**

```bash
python run_aegis_v3.py --mode pipeline
```

**Expected output:**
```
  PASS  FAULT_INJECTION               18ms   Fault confirmed in payment.py
  PASS  CLUSTER_COORDINATION         155ms   Leader: node-02 | Term: 2 | Quorum: 3
  PASS  PATCH_GENERATION              56ms   Simulation patch loaded
  PASS  WASM_SANDBOX                 513ms   compile=3ms | exec=509ms | mem=54.7KB
  PASS  FORMAL_VERIFICATION            1ms   verdict=PROVED | div=PROVED

> Legacy sample output: `WASM_SANDBOX` is a module/stage label, not WebAssembly isolation; `PROVED` is a heuristic verdict label, not a formal proof. The timings above are illustrative, not benchmark evidence.
  PASS  CONSENSUS_COMMIT              11ms   Committed at index=2 | leader=node-02
  PASS  DEPLOYMENT                   778ms   tests=PASS | branch=aegis-v3/fix-xxx
  PASS  TELEMETRY_FLUSH               24ms   Flushed 7 stages | saved to audit log

  ┌──────────────────── Aegis V3 Pipeline SUCCESS ─────────────────────┐
  │  ✅ Total time: 1,581ms  |  Incident resolved autonomously          │
  └─────────────────────────────────────────────────────────────────────┘
```

---

### Mode 2: Dashboard (Live Web UI)
Start the real-time monitoring dashboard. Watch pipeline runs, live metrics, and event streams in your browser.

```bash
python run_aegis_v3.py --mode dashboard
# Open: http://localhost:8001
```

---

### Mode 3: Chaos Mode (Full Autonomous Demo)
Inject a real bug into production code, watch Aegis detect it and fix it autonomously. **Requires OpenAI API key.**

```bash
# Windows PowerShell
$env:OPENAI_API_KEY="sk-your-key-here"
python run_aegis_v3.py --mode chaos

# Linux / Mac
export OPENAI_API_KEY="sk-your-key-here"
python run_aegis_v3.py --mode chaos
```

---

### Mode 4: Consensus (Raft Cluster Test)
Test the Raft distributed consensus cluster in isolation. Watch leader election, quorum voting, and log replication.

```bash
python run_aegis_v3.py --mode consensus
```

---

### Mode 5: Verify (Patch Verification Only)
Run the heuristic verification checks on a specific patch file without executing the full pipeline. Results are not formal proofs.

```bash
python run_aegis_v3.py --mode verify
```

---

## 🖥️ Local Dashboard

The dashboard gives you a real-time window into every Aegis operation.

### Accessing the Dashboard

```bash
python run_aegis_v3.py --mode dashboard
```

Then open your browser:

| Page | URL | Description |
|------|-----|-------------|
| 🏠 Main Dashboard | `http://localhost:8001` | Full monitoring UI |
| 💚 Health Check | `http://localhost:8001/health` | Liveness probe |
| ✅ Readiness | `http://localhost:8001/ready` | Subsystem check |
| 📊 System Status | `http://localhost:8001/api/status` | Live snapshot |
| 📋 Run History | `http://localhost:8001/api/history` | All past runs |
| ⏱️ MTTR Stats | `http://localhost:8001/api/mttr/stats` | Recovery time metrics |
| 📖 API Docs | `http://localhost:8001/docs` | Interactive Swagger UI |
| 🔌 WS Events | `ws://localhost:8001/ws/telemetry` | Live event stream |
| 🔌 WS Metrics | `ws://localhost:8001/ws/metrics` | Resource metrics |

### Dashboard Features
- **Live Event Feed** — every pipeline stage appears in real time
- **Resource Monitor** — CPU %, memory MB, active tasks update every second
- **Pipeline Trigger** — click "Run Pipeline" to start a new run from the UI
- **Chaos Injection** — click "Simulate Chaos" to inject a bug and watch auto-fix
- **Cluster Status** — see which Raft node is the current leader
- **Run Counter** — total runs, success rate, average duration

---

## 📡 API Reference

### Health & Status

#### `GET /health` — Liveness Probe
Returns 200 if the server is running. Used by load balancers and Kubernetes.

```bash
curl http://localhost:8001/health
```
```json
{
  "status": "ok",
  "service": "aegis-v3",
  "uptime_seconds": 142.3,
  "pipeline_running": false,
  "cpu_percent": 4.2,
  "memory_mb": 55.9,
  "timestamp": "2026-06-12T06:00:00.000000Z"
}
```

---

#### `GET /ready` — Readiness Probe
Returns 200 if all 5 subsystems are importable. Returns 503 if any subsystem fails.

```bash
curl http://localhost:8001/ready
```
```json
{
  "status": "ready",
  "checks": {
    "wasm_sandbox": "ok",
    "raft_consensus": "ok",
    "formal_verification": "ok",
    "audit_persistence": "ok",
    "chaos_engine": "ok"
  },
  "timestamp": "2026-06-12T06:00:00.000000Z"
}
```

---

#### `GET /api/status` — Live System Snapshot
```bash
curl http://localhost:8001/api/status
```
```json
{
  "system": "Aegis V3 — Prometheus",
  "status": "IDLE",
  "uptime_seconds": 142.3,
  "cpu_percent": 4.2,
  "memory_mb": 55.9,
  "active_ws_telemetry": 2,
  "active_ws_metrics": 1,
  "queue_depth": 0,
  "last_pipeline": { ... },
  "timestamp": "2026-06-12T06:00:00.000000Z"
}
```

---

### Pipeline Control

#### `POST /api/pipeline/run` — Trigger Pipeline
```bash
curl -X POST "http://localhost:8001/api/pipeline/run?simulation=true&severity=P1"
```
```json
{
  "status": "triggered",
  "run_id": "A1B2C3D4",
  "severity": "P1"
}
```

**Query Parameters:**
| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `target_file` | string | `dummy_app/payment.py` | File to heal |
| `severity` | string | `P1` | P0/P1/P2/P3 |
| `simulation` | boolean | `true` | Use simulation patch |
| `chaos` | boolean | `false` | Inject chaos bug first |

---

#### `POST /api/chaos/inject` — Chaos + Auto-Fix
Injects a random bug in the demo target and triggers the simulation workflow. Use only in a disposable local checkout.
```bash
curl -X POST "http://localhost:8001/api/chaos/inject"
```

---

### History & Analytics

#### `GET /api/history` — Pipeline Run History
Returns the last N pipeline runs from SQLite. **Persists across restarts.**

```bash
curl "http://localhost:8001/api/history?limit=10"
```
```json
{
  "count": 3,
  "limit": 10,
  "runs": [
    {
      "run_id": "259aee67-b56",
      "incident_id": "2E23D5E9",
      "severity": "P1",
      "success": 1,
      "total_duration_us": 1581512.0,
      "stage_count": 8,
      "deployed_branch": "aegis-v3/fix-2e23d5e9",
      "started_at": "2026-06-12T06:00:00Z",
      "completed_at": "2026-06-12T06:00:01Z",
      "mttr_seconds": 12.4
    }
  ],
  "timestamp": "2026-06-12T06:01:00Z"
}
```

---

#### `GET /api/mttr/stats` — MTTR Aggregate Statistics
```bash
curl "http://localhost:8001/api/mttr/stats"
```
```json
{
  "total_chaos_incidents": 5,
  "resolved_incidents": 5,
  "min_mttr_seconds": 1.2,
  "max_mttr_seconds": 18.7,
  "avg_mttr_seconds": 8.4,
  "timestamp": "2026-06-12T06:01:00Z"
}
```

---

## 🔌 WebSocket Streams

### Telemetry Events — `ws://localhost:8001/ws/telemetry`

Connect to receive real-time pipeline events as JSON:

```javascript
const ws = new WebSocket('ws://localhost:8001/ws/telemetry');
ws.onmessage = (event) => {
    const data = JSON.parse(event.data);
    console.log(data.event_type, data.title, data.detail);
};
```

**Event structure:**
```json
{
  "event_id": "uuid",
  "event_type": "STAGE_COMPLETE",
  "source": "orchestrator/abc123",
  "title": "WASM_SANDBOX",
  "detail": "compile=3ms | exec=509ms | mem=54.7KB",
  "severity": "SUCCESS",
  "duration_us": 513127.9,
  "data": { "bytecode_size": 34, "ast_node_count": 54 },
  "timestamp": "2026-06-12T06:00:00Z"
}
```

**Event Types:**
| Type | When |
|------|------|
| `PIPELINE_START` | Pipeline begins |
| `STAGE_COMPLETE` | Each stage finishes |
| `PIPELINE_END` | Pipeline completes |
| `ALERT` | MTTR computed, critical error |
| `CHAOS_INJECT` | Bug injected |
| `CLUSTER_STATE` | Raft leader change |

---

### Resource Metrics — `ws://localhost:8001/ws/metrics`

Receives system resource snapshots every 1 second:

```json
{
  "cpu_percent": 12.4,
  "memory_mb": 58.2,
  "memory_percent": 42.1,
  "active_tasks": 8,
  "event_queue_depth": 0,
  "uptime_seconds": 142.3,
  "timestamp": "2026-06-12T06:00:00Z"
}
```

---

## 🧪 Testing

### Run All Tests

```bash
pytest tests/test_v3_pipeline.py -v --asyncio-mode=auto
```

### Run Only Unit Tests (Fast — No Pipeline Startup)
```bash
pytest tests/test_v3_pipeline.py -v --asyncio-mode=auto -k "not Integration"
```

### Run a Specific Test Class
```bash
# Test formal verification only
pytest tests/test_v3_pipeline.py::TestFormalVerification -v --asyncio-mode=auto

# Test Python patch checks (legacy class/module name)
pytest tests/test_v3_pipeline.py::TestWasmSandbox -v --asyncio-mode=auto

# Test persistence only
pytest tests/test_v3_pipeline.py::TestAuditLogBackend -v --asyncio-mode=auto
```

### Full Test Coverage

| Test Class | Tests | What It Covers |
|---|---|---|
| `TestFormalVerification` | 7 | Safe patch PROVED, zero-division REFUTED, syntax error UNKNOWN, trivial patch, overflow detection, timing, domain-bounded safe division |
| `TestWasmSandbox` | 6 | Clean patch compile+execute, import denial, eval() denial, syntax error FAULT, compilation timing, LOAD_GLOBAL regression (GAP 5 fix) |
| `TestChaosMonkey` | 4 | Inject+restore round-trip, arithmetic mutation, MTTR timestamp presence, no-eligible-files returns None |
| `TestAuditLogBackend` | 4 | Save+retrieve pipeline run, get_run includes stages, nonexistent run → None, MTTR persist+query |
| `TestRaftPersistenceBackend` | 2 | Persist+load state round-trip, fresh node → term=0 |
| `TestSchemaModels` | 4 | MttrRecord.compute_mttr positive, unresolved → None, PipelineRunRecord defaults, TelemetryEventType enum |
| `TestFullPipelineIntegration` | 1 | Complete end-to-end simulation pipeline, 8 stages, success=True |
| `TestDeploymentGuard` | 3 | Deployment is blocked by default and demo opt-in remains guarded |
| `TestSandboxTimeout` | 1 | Infinite-loop worker is terminated at the configured timeout |
| **TOTAL** | **32** | **32 tests passed in the latest CI run** |

---

## ⚙️ Configuration

### Environment Variables

| Variable | Default | Description |
|---|---|---|
| `OPENAI_API_KEY` | `""` | OpenAI API key. Without this, runs in simulation mode |
| `LOG_FORMAT` | `plain` | Set to `json` for structured JSON logging (Datadog/Grafana compatible) |
| `LOG_LEVEL` | `INFO` | Logging level: `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL` |
| `LOG_FILE` | `""` | Path to write rotating log file (10MB max, 5 backups) |

### Example `.env` file
```bash
OPENAI_API_KEY=sk-your-openai-key-here
LOG_FORMAT=json
LOG_LEVEL=INFO
LOG_FILE=logs/aegis_v3.log
```

### SandboxConfig (in code)
```python
from aegis_v3.wasm_sandbox import WasmIsolationEngine
from aegis_v3.schema_v3 import SandboxConfig

engine = WasmIsolationEngine(SandboxConfig(
    max_execution_us=3_000_000,   # 3 second execution timeout
    max_memory_kb=4_096,          # observed-allocation threshold; not OS-enforced
    max_ast_nodes=800,            # max complexity
    allow_imports=["math", "re"], # whitelisted imports
    deny_builtins=["open", "eval", "exec", "__import__"],
))
```

### NodeConfig (Raft cluster)
```python
from aegis_v3.schema_v3 import NodeConfig

configs = [
    NodeConfig(
        node_id="node-00",
        election_timeout_ms=200.0,
        heartbeat_interval_ms=50.0,
        simulate_failure=False,
        failure_probability=0.0,
    ),
    # ... 4 more nodes
]
```

---

## 🔒 Security Design

## Guarded dashboard deployment

The repository includes a Docker image and Render Blueprint for a **read-only production dashboard**. This is not an approval to run autonomous repair against production systems.

### Local container smoke test

```bash
docker build -t aegis-v3 .
docker run --rm -p 8001:8001 \
  -e AEGIS_ENV=production \
  -e AEGIS_DASHBOARD_USERNAME=operator \
  -e AEGIS_DASHBOARD_PASSWORD='replace-with-a-long-random-password' \
  -e AEGIS_DB_PATH=/var/data/aegis_v3_audit.db \
  -e AEGIS_ENABLE_PIPELINE_API=false \
  -v aegis-data:/var/data \
  aegis-v3
```

Open `http://127.0.0.1:8001`; the browser should prompt for Basic authentication. Verify `/health` returns 200 and unauthenticated dashboard/API/WebSocket requests are rejected.

### Render deployment

1. Connect the repository to Render and review `render.yaml`.
2. Set unique `AEGIS_DASHBOARD_USERNAME` and `AEGIS_DASHBOARD_PASSWORD` secret values.
3. Keep `AEGIS_ENABLE_PIPELINE_API=false`; the hosted dashboard must not execute generated patches.
4. Deploy, verify health/readiness, authentication, WebSocket connectivity, and persistent audit storage.

The Blueprint requests a paid Starter service and a 1 GB persistent disk. Review Render pricing before creating the service. Adding these files does **not** mean the app has already been deployed.

## Current Security Boundaries

AEGIS is not production-safe. The following limitations are known and should be treated as release blockers:

1. **Python is not WebAssembly.** The patch runner does not implement a WASM runtime or a complete sandbox.
2. **Process separation is not sufficient isolation.** OS-level policy, container isolation, restricted filesystem mounts, network denial, CPU quotas, and hard memory enforcement are not comprehensively provided.
3. **Static deny-lists are bypassable in principle.** AST and bytecode checks are defense-in-depth, not a security boundary.
4. **Verification is heuristic.** It does not prove arbitrary patches correct.
5. **Deployment must remain opt-in and disposable.** The guard requires `AEGIS_ENABLE_DEMO_DEPLOYMENT=1`, `AEGIS_DEMO_CHECKOUT_ROOT` set to the exact absolute path of a disposable Git checkout, the `dummy_app/payment.py` target, and a clean worktree. Never point it at production repositories or credentials.
6. **Recovery claims need evidence.** Rollback, secret handling, consensus correctness, and end-to-end failure recovery require independent testing.

The current tests cover selected regressions, including the deployment guard and timeout termination. They do not certify the limitations above as solved.

### Best Practices
```bash
# Never commit API keys
echo ".env" >> .gitignore

# Use environment variables only
export OPENAI_API_KEY="sk-..."

# Rotate tokens after use
# github.com/settings/tokens → Revoke old tokens
```

---

## 📈 Performance Measurements

No reproducible end-to-end benchmark is published at this time. Earlier timing figures in this README were illustrative estimates and must not be interpreted as measured performance or guarantees. To publish credible numbers, record the machine/OS/Python version, run the same workload repeatedly, report sample size and percentiles, and include failures and cold-start overhead.

## 📁 Project Structure

```
MY VISION/
│
├── 📄 README.md                    ← This file
├── 🔒 .gitignore                   ← Excludes secrets, cache, DB files
├── 📋 requirements_v3.txt          ← All Python dependencies
├── 🚀 run_aegis_v3.py              ← Master entry point (all modes)
│
├── 🐍 aegis_v3/                    ← Core engine package
│   ├── __init__.py
│   ├── orchestrator.py             ← 8-stage pipeline coordinator (1,200+ lines)
│   ├── wasm_sandbox.py             ← Process-isolated patch execution (590+ lines)
│   ├── formal_verification.py      ← Interval/rule-based heuristic verifier (not an SMT solver)
│   ├── distributed_consensus.py    ← experimental simulated consensus coordinator
│   ├── chaos_monkey.py             ← AST-based fault injection (500+ lines)
│   ├── realtime_telemetry.py       ← FastAPI dashboard + WebSocket (1,200+ lines)
│   ├── persistence.py              ← Async SQLite audit log + Raft storage (300+ lines)
│   ├── logging_config.py           ← Structured JSON logging (160+ lines)
│   └── schema_v3.py                ← Pydantic v2 type contracts (370+ lines)
│
├── 🧪 tests/
│   ├── __init__.py
│   └── test_v3_pipeline.py         ← 28 async unit + integration tests (500+ lines)
│
├── 🐛 dummy_app/                   ← Target application (the "buggy" service)
│   └── payment.py                  ← Payment processing with calculate_tax()
│
└── 💾 aegis_v3_audit.db            ← SQLite audit log (auto-created, gitignored)
```

---

## 📦 Dependencies

```
# Core
openai>=1.35.0          # AI patch generation (GPT-4o)
pydantic>=2.7.0         # Type contracts (v2)
fastapi>=0.111.0        # REST API + WebSocket server
uvicorn[standard]>=0.30.0  # ASGI server
python-dotenv>=1.0.0    # .env file loading

# Async & Performance
asyncio (stdlib)        # Async runtime
aiofiles>=23.0.0        # Async file I/O
aiosqlite>=0.20.0       # Async SQLite (audit log + Raft persistence)
anyio>=4.0.0            # Async primitives

# Observability
psutil>=5.9.0           # CPU/memory metrics
rich>=13.0.0            # Beautiful terminal output
python-json-logger>=2.0.7  # Structured JSON logging

# Vector/AI (optional, for RAG features)
chromadb>=0.5.0         # Vector database for codebase embedding
tiktoken>=0.7.0         # Token counting

# Testing
pytest>=8.2.0           # Test runner
pytest-asyncio>=0.23.0  # Async test support
pytest-timeout          # Test timeout enforcement

# HTTP
httpx>=0.27.0           # Async HTTP client
websockets>=12.0        # WebSocket client/server
```

---

## 🗺️ Roadmap

### Version 3.1 — Intelligence Upgrade
- [ ] **Real Z3 SMT solver** integration — replace interval arithmetic with production-grade formal verification
- [ ] **ChromaDB RAG** — embed entire codebase into vector DB for context-aware multi-file patch generation
- [ ] **Multi-file patch support** — fix bugs that span across multiple Python files

### Version 3.2 — Platform Integration
- [ ] **GitHub Actions workflow** — automatic CI/CD with Aegis self-healing on test failures
- [ ] **Kubernetes CRD operator** — native k8s integration with custom resource definitions
- [ ] **Prometheus + Grafana** — export metrics in Prometheus format, pre-built Grafana dashboards
- [ ] **Webhook integration** — trigger pipelines from PagerDuty, OpsGenie, Datadog alerts

### Version 3.3 — Multi-Language Support
- [ ] **JavaScript/Node.js patches** — fix JS/TS code with GPT-4o
- [ ] **Go patch generation** — support Go service self-healing
- [ ] **Java support** — enterprise Java application healing
- [ ] **Language-agnostic sandbox** — Docker-based isolation for any language

### Version 4.0 — Full Autonomous Operations
- [ ] **Predictive healing** — detect bugs before they occur using anomaly detection
- [ ] **Multi-cluster federation** — coordinate healing across geographically distributed clusters
- [ ] **Self-improving prompts** — Aegis improves its own patch generation prompts based on past success rates
- [ ] **Security patch generation** — automatically apply CVE patches from vulnerability databases

---

## 🤝 Contributing

Contributions are welcome! Here's how to get started:

```bash
# 1. Fork the repo on GitHub
# 2. Clone your fork
git clone https://github.com/YOUR_USERNAME/aegis-push.git
cd aegis-push

# 3. Create a feature branch
git checkout -b feature/your-feature-name

# 4. Make your changes
# ...

# 5. Run the test suite
pytest tests/test_v3_pipeline.py -v --asyncio-mode=auto

# 6. Commit with a descriptive message
git commit -m "feat: add your feature description"

# 7. Push and create a Pull Request
git push origin feature/your-feature-name
```

### Contribution Guidelines
- All new features must include async pytest tests
- Follow Pydantic v2 type contract patterns in `schema_v3.py`
- Use `logging.getLogger("aegis.<module>")` for all logging
- Run the full test suite before submitting (28/28 must pass)
- Format with `black` and type-check with `mypy`

---

## 📜 License

```
MIT License

Copyright (c) 2026 maheshraja12

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT.
```

---

## 🙏 Acknowledgements

Built with these amazing open-source technologies:
- [FastAPI](https://fastapi.tiangolo.com/) — Modern async web framework
- [OpenAI](https://openai.com/) — GPT-4o for intelligent patch generation
- [Pydantic](https://docs.pydantic.dev/) — Data validation and type safety
- [aiosqlite](https://aiosqlite.omnilib.dev/) — Async SQLite for Python
- [Rich](https://rich.readthedocs.io/) — Beautiful terminal output
- [pytest-asyncio](https://pytest-asyncio.readthedocs.io/) — Async testing support

---

<div align="center">

**⭐ Star this repo if Aegis V3 impressed you!**

Built with ❤️ using Python, FastAPI, OpenAI GPT-4o, Pydantic v2, and asyncio

`github.com/maheshraja12/aegis-push`

</div>
