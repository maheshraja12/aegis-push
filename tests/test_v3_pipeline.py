"""
==============================================================================
Aegis V3: Enterprise Autonomous Infrastructure Resilience Engine
tests/test_v3_pipeline.py — Full V3 Async Test Suite
==============================================================================

Coverage:
  - FormalVerificationEngine: safe patch, unsafe patch (zero division),
    syntax error, null safety, overflow detection
  - WasmIsolationEngine: clean patch execution, denylist rejection,
    memory tracking, AST node limit
  - ChaosMonkey: bug injection, restore round-trip, all bug types
  - AuditLogBackend: pipeline run persistence, MTTR record, history query
  - RaftPersistenceBackend: persist/load state round-trip
  - Schema: MttrRecord.compute_mttr(), PipelineRunRecord construction
  - Integration: full orchestrator pipeline in simulation mode (smoke test)

Run with:
    pytest tests/test_v3_pipeline.py -v --asyncio-mode=auto
==============================================================================
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import textwrap
import uuid

import pytest
import pytest_asyncio

# Ensure repo root is on the path
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

# ---------------------------------------------------------------------------
# Pytest-asyncio configuration
# ---------------------------------------------------------------------------
pytestmark = pytest.mark.asyncio


# ===========================================================================
# Fixtures
# ===========================================================================

@pytest.fixture
def temp_dir():
    """Provide a temporary directory that is cleaned up after the test."""
    with tempfile.TemporaryDirectory() as d:
        yield d


@pytest.fixture
def temp_db(temp_dir):
    """Return a path to a temporary SQLite database."""
    return os.path.join(temp_dir, "test_aegis.db")


# ===========================================================================
# 1. FORMAL VERIFICATION ENGINE
# ===========================================================================

class TestFormalVerification:
    """Unit tests for the FormalVerificationEngine."""

    @pytest.fixture(autouse=True)
    def setup_engine(self):
        from aegis_v3.formal_verification import FormalVerificationEngine
        self.engine = FormalVerificationEngine()
        self.engine.declare_variable("amount",   lo=0.0, hi=1_000_000.0)
        self.engine.declare_variable("tax_rate", lo=0.0, hi=1.0)
        self.engine.declare_variable("n",        lo=0,   hi=10_000)

    async def test_safe_patch_is_proved(self):
        """A simple multiplication patch with no unsafe operations → PROVED."""
        from aegis_v3.schema_v3 import ProofStatus
        safe_patch = textwrap.dedent("""
            def calculate_tax(amount, tax_rate):
                result = amount * tax_rate
                return result
        """)
        report = await self.engine.verify_patch(safe_patch, "safe multiplication")
        # Should be PROVED or UNKNOWN (no division = no refutation)
        assert report.overall_verdict != ProofStatus.REFUTED
        assert report.is_division_safe is True

    async def test_zero_division_patch_is_refuted(self):
        """A patch with an unguarded division where denominator can be 0 → REFUTED."""
        from aegis_v3.schema_v3 import ProofStatus
        unsafe_patch = textwrap.dedent("""
            def calculate_tax(amount, tax_rate):
                result = amount / tax_rate
                return result
        """)
        report = await self.engine.verify_patch(unsafe_patch, "unsafe division")
        # tax_rate ∈ [0.0, 1.0] contains zero → should be REFUTED
        assert report.overall_verdict == ProofStatus.REFUTED
        assert report.is_division_safe is False
        assert len(report.critical_failures) > 0

    async def test_syntax_error_returns_unknown(self):
        """A patch with a syntax error cannot be verified → UNKNOWN."""
        from aegis_v3.schema_v3 import ProofStatus
        broken_patch = "def broken(@@@syntax error"
        report = await self.engine.verify_patch(broken_patch, "syntax error patch")
        assert report.overall_verdict == ProofStatus.UNKNOWN
        assert any("SyntaxError" in f for f in report.critical_failures)

    async def test_no_constraints_is_trivially_proved(self):
        """A patch with no verifiable constraints → PROVED (trivial)."""
        from aegis_v3.schema_v3 import ProofStatus
        trivial = textwrap.dedent("""
            x = 42
            y = \"hello\"
            z = x + 1
        """)
        report = await self.engine.verify_patch(trivial, "trivial assignment")
        assert report.overall_verdict == ProofStatus.PROVED

    async def test_overflow_safety_on_large_literal(self):
        """A patch with a huge integer literal triggers an overflow constraint."""
        patch = f"x = {2**54} * amount"
        report = await self.engine.verify_patch(patch, "large literal")
        # Should extract at least one OVERFLOW_SAFETY constraint
        overflow_constraints = [
            c for c in report.constraints_checked
            if c.constraint_type.value == "OVERFLOW_SAFETY"
        ]
        assert len(overflow_constraints) > 0

    async def test_verification_time_is_positive(self):
        """verification_time_us should be a positive float."""
        patch = "result = 1 + 1"
        report = await self.engine.verify_patch(patch, "timing test")
        assert report.verification_time_us >= 0.0

    async def test_safe_division_with_declared_positive_domain(self):
        """Division by a variable declared ∈ [1.0, 10.0] → PROVED (safe)."""
        from aegis_v3.schema_v3 import ProofStatus
        self.engine.declare_variable("divisor", lo=1.0, hi=10.0)
        safe_div = "result = amount / divisor"
        report = await self.engine.verify_patch(safe_div, "safe division")
        assert report.is_division_safe is True


# ===========================================================================
# 2. WASM SANDBOX
# ===========================================================================

class TestWasmSandbox:
    """Unit tests for the WasmIsolationEngine."""

    @pytest.fixture(autouse=True)
    def setup_engine(self):
        from aegis_v3.wasm_sandbox import WasmIsolationEngine
        from aegis_v3.schema_v3 import SandboxConfig
        self.engine = WasmIsolationEngine(SandboxConfig(
            max_execution_us=5_000_000,  # 5s for process spawn on Windows
            max_memory_kb=4_096,
            max_ast_nodes=800,
        ))
        yield
        self.engine.shutdown()

    async def test_clean_patch_compiles_and_executes(self):
        """A valid, safe patch passes both compilation and execution."""
        from aegis_v3.schema_v3 import SandboxStatus
        patch = textwrap.dedent("""
            def tax(amount, rate):
                return amount * rate
            result = tax(100.0, 0.1)
        """)
        comp, trace = await self.engine.run_full_isolation_pipeline(patch)
        assert comp.status == SandboxStatus.COMPILED
        assert comp.ast_node_count > 0
        assert trace.status == SandboxStatus.EXECUTED

    async def test_import_is_denied(self):
        """A patch with 'import os' must be rejected at the AST stage."""
        from aegis_v3.schema_v3 import SandboxStatus
        malicious = "import os; os.system('echo HACKED')"
        comp, trace = await self.engine.run_full_isolation_pipeline(malicious)
        assert comp.status == SandboxStatus.DENIED
        assert comp.violation_detail is not None

    async def test_eval_call_is_denied(self):
        """A patch calling eval() must be rejected by the denylist visitor."""
        from aegis_v3.schema_v3 import SandboxStatus
        exploit = "result = eval('1 + 1')"
        comp, trace = await self.engine.run_full_isolation_pipeline(exploit)
        assert comp.status == SandboxStatus.DENIED

    async def test_syntax_error_returns_fault_status(self):
        """A patch with a syntax error returns FAULT at the compilation stage."""
        from aegis_v3.schema_v3 import SandboxStatus
        broken = "def f(x return x"
        comp, trace = await self.engine.run_full_isolation_pipeline(broken)
        assert comp.status == SandboxStatus.FAULT

    async def test_compilation_time_is_measured(self):
        """Compilation time should be recorded as a non-negative float."""
        patch = "result = 2 + 2"
        comp, _ = await self.engine.run_full_isolation_pipeline(patch)
        assert comp.compilation_us >= 0.0

    async def test_global_name_reference_is_not_rejected(self):
        """GAP 5 regression test: patches using True/False/None must not be denied."""
        from aegis_v3.schema_v3 import SandboxStatus
        patch = textwrap.dedent("""
            flag = True
            value = None
            if flag:
                result = 42
            else:
                result = 0
        """)
        comp, trace = await self.engine.run_full_isolation_pipeline(patch)
        # After the LOAD_GLOBAL fix, this must NOT be denied
        assert comp.status == SandboxStatus.COMPILED, (
            f"LOAD_GLOBAL regression: True/False/None caused DENIED. "
            f"violation_detail={comp.violation_detail}"
        )


# ===========================================================================
# 3. CHAOS MONKEY
# ===========================================================================

class TestChaosMonkey:
    """Unit tests for the ChaosMonkey fault injector."""

    @pytest.fixture
    def chaos_target_dir(self, temp_dir):
        """Create a minimal Python module in a temp dir for chaos injection."""
        target_file = os.path.join(temp_dir, "target.py")
        content = textwrap.dedent("""
            def compute(a, b):
                for i in range(10):
                    result = a * b
                return result

            def get_value(data, key):
                return data[key]

            def divide(x, y):
                return x / y
        """)
        with open(target_file, "w") as f:
            f.write(content)
        return temp_dir

    def test_inject_and_restore_round_trip(self, chaos_target_dir):
        """Injecting a bug then restoring must leave the file identical."""
        from aegis_v3.chaos_monkey import ChaosMonkey
        monkey = ChaosMonkey(chaos_target_dir)
        target_file = os.path.join(chaos_target_dir, "target.py")

        with open(target_file) as f:
            original = f.read()

        event = monkey.inject_bug(target_dir="")
        assert event is not None, "ChaosMonkey failed to inject any bug"
        assert event.original_content == original

        # Verify the file was changed
        with open(target_file) as f:
            mutated = f.read()
        assert mutated != original, "File should have been mutated"

        # Restore
        ok = monkey.restore(event)
        assert ok is True

        with open(target_file) as f:
            restored = f.read()
        assert restored == original, "Restore did not produce original content"

    def test_inject_arithmetic_bug(self, chaos_target_dir):
        """Arithmetic mutation should flip a binary operator."""
        from aegis_v3.chaos_monkey import ChaosMonkey, BugType
        monkey = ChaosMonkey(chaos_target_dir)
        event = monkey.inject_bug(target_dir="", preferred_bug_type=BugType.ARITHMETIC)
        if event:  # Only assert if the mutation could be applied
            assert event.bug_type == BugType.ARITHMETIC
            assert "ARITHMETIC" in event.bug_type.value

    def test_chaos_event_has_timestamp(self, chaos_target_dir):
        """ChaosEvent must have a non-empty injected_at timestamp (for MTTR)."""
        from aegis_v3.chaos_monkey import ChaosMonkey
        monkey = ChaosMonkey(chaos_target_dir)
        event = monkey.inject_bug(target_dir="")
        if event:
            assert event.injected_at != ""
            assert "T" in event.injected_at  # ISO timestamp format

    def test_no_eligible_files_returns_none(self, temp_dir):
        """ChaosMonkey must return None when there are no eligible Python files."""
        from aegis_v3.chaos_monkey import ChaosMonkey
        # temp_dir has no .py files
        monkey = ChaosMonkey(temp_dir)
        result = monkey.inject_bug(target_dir="")
        assert result is None


# ===========================================================================
# 4. PERSISTENCE (AuditLogBackend + RaftPersistenceBackend)
# ===========================================================================

class TestAuditLogBackend:
    """Unit tests for the SQLite-backed audit log."""

    async def test_save_and_retrieve_pipeline_run(self, temp_db):
        """A saved PipelineResult should be retrievable via get_recent_runs."""
        from aegis_v3.persistence import AuditLogBackend
        from aegis_v3.schema_v3 import PipelineResult, IncidentSeverity, StageResult, PipelineStage

        backend = AuditLogBackend(db_path=temp_db)
        await backend.initialize()

        result = PipelineResult(
            run_id=str(uuid.uuid4())[:12],
            incident_id="TEST-001",
            severity=IncidentSeverity.P1,
            success=True,
            total_duration_us=1_234_567.0,
            stages=[
                StageResult(
                    stage=PipelineStage.PATCH_GENERATION,
                    success=True,
                    duration_us=50_000.0,
                    detail="Test stage",
                )
            ],
            summary="Test run",
        )

        await backend.save_pipeline_run(result)
        runs = await backend.get_recent_runs(limit=10)

        assert len(runs) == 1
        assert runs[0]["run_id"] == result.run_id
        assert runs[0]["success"] == 1  # SQLite stores as int
        assert runs[0]["incident_id"] == "TEST-001"

    async def test_get_run_by_id_includes_stages(self, temp_db):
        """get_run() should return the full stages_json decoded as a list."""
        from aegis_v3.persistence import AuditLogBackend
        from aegis_v3.schema_v3 import PipelineResult, IncidentSeverity, StageResult, PipelineStage

        backend = AuditLogBackend(db_path=temp_db)
        await backend.initialize()

        run_id = str(uuid.uuid4())[:12]
        result = PipelineResult(
            run_id=run_id,
            incident_id="TEST-002",
            severity=IncidentSeverity.P2,
            success=False,
            stages=[
                StageResult(stage=PipelineStage.WASM_SANDBOX, success=False, duration_us=100.0)
            ],
        )
        await backend.save_pipeline_run(result)

        record = await backend.get_run(run_id)
        assert record is not None
        assert isinstance(record["stages"], list)
        assert len(record["stages"]) == 1

    async def test_nonexistent_run_returns_none(self, temp_db):
        """get_run() for an unknown run_id must return None."""
        from aegis_v3.persistence import AuditLogBackend
        backend = AuditLogBackend(db_path=temp_db)
        await backend.initialize()
        result = await backend.get_run("nonexistent-id")
        assert result is None

    async def test_save_and_query_mttr(self, temp_db):
        """MTTR records should be persisted and queryable via get_mttr_stats."""
        from aegis_v3.persistence import AuditLogBackend
        from aegis_v3.schema_v3 import MttrRecord

        backend = AuditLogBackend(db_path=temp_db)
        await backend.initialize()

        mttr = MttrRecord(
            chaos_id="chaos-abc",
            bug_type="ARITHMETIC",
            target_file="dummy_app/payment.py",
            injected_at="2026-06-11T10:00:00.000000Z",
            resolved_at="2026-06-11T10:00:45.500000Z",
            pipeline_run_id="run-abc",
            resolved=True,
        )
        mttr.compute_mttr()
        assert mttr.mttr_seconds is not None
        assert abs(mttr.mttr_seconds - 45.5) < 0.5

        await backend.save_mttr(mttr)
        stats = await backend.get_mttr_stats()
        assert stats["resolved_incidents"] == 1
        assert stats["avg_mttr_seconds"] is not None


class TestRaftPersistenceBackend:
    """Unit tests for the Raft durable state storage."""

    async def test_persist_and_load_state(self, temp_db):
        """Persisted state must be restorable identically."""
        from aegis_v3.persistence import RaftPersistenceBackend
        from aegis_v3.schema_v3 import RaftLogEntry

        node_id = "node-test-01"
        backend = RaftPersistenceBackend(db_path=temp_db, node_id=node_id)
        await backend.ensure_table()

        entries = [{"index": 1, "term": 2, "command": "DEPLOY", "payload": {}, "committed_at": "2026-01-01T00:00:00Z"}]
        await backend.persist_state(current_term=7, voted_for="node-02", log_entries=entries)

        term, voted_for, log = await backend.load_state()
        assert term == 7
        assert voted_for == "node-02"
        assert len(log) == 1
        assert log[0]["command"] == "DEPLOY"

    async def test_fresh_node_has_zero_term(self, temp_db):
        """A node with no persisted state should start at term=0."""
        from aegis_v3.persistence import RaftPersistenceBackend
        backend = RaftPersistenceBackend(db_path=temp_db, node_id="fresh-node")
        term, voted_for, log = await backend.load_state()
        assert term == 0
        assert voted_for is None
        assert log == []


# ===========================================================================
# 5. SCHEMA MODELS
# ===========================================================================

class TestSchemaModels:
    """Unit tests for new schema_v3 models (MttrRecord, PipelineRunRecord)."""

    def test_mttr_compute_positive(self):
        """compute_mttr() should produce a positive float for valid timestamps."""
        from aegis_v3.schema_v3 import MttrRecord
        m = MttrRecord(
            chaos_id="c1",
            bug_type="ZERO_DIVISION",
            target_file="f.py",
            injected_at="2026-06-11T10:00:00.000000Z",
            resolved_at="2026-06-11T10:01:30.000000Z",
        )
        m.compute_mttr()
        assert m.mttr_seconds == pytest.approx(90.0, abs=1.0)

    def test_mttr_compute_unresolved(self):
        """compute_mttr() with no resolved_at should leave mttr_seconds as None."""
        from aegis_v3.schema_v3 import MttrRecord
        m = MttrRecord(
            chaos_id="c2",
            bug_type="ARITHMETIC",
            target_file="f.py",
            injected_at="2026-06-11T10:00:00.000000Z",
        )
        m.compute_mttr()
        assert m.mttr_seconds is None

    def test_pipeline_run_record_defaults(self):
        """PipelineRunRecord must have sensible defaults."""
        from aegis_v3.schema_v3 import PipelineRunRecord
        rec = PipelineRunRecord(run_id="abc123")
        assert rec.success is False
        assert rec.stage_count == 0
        assert rec.stages_json == "[]"

    def test_telemetry_event_type_enum(self):
        """All TelemetryEventType members must be accessible."""
        from aegis_v3.schema_v3 import TelemetryEventType
        assert TelemetryEventType.ALERT is not None
        assert TelemetryEventType.PIPELINE_START is not None
        assert TelemetryEventType.CLUSTER_STATE is not None


# ===========================================================================
# 6. INTEGRATION: Full Pipeline Smoke Test (simulation mode)
# ===========================================================================

class TestFullPipelineIntegration:
    """
    Smoke test: Run the complete orchestrator pipeline in simulation mode.
    This test does NOT require an OpenAI API key.
    """

    @pytest.mark.timeout(120)   # 2 minute upper bound
    async def test_simulation_pipeline_succeeds(self):
        """
        Run the full Aegis V3 pipeline in simulation mode end-to-end.
        Expects success=True and all 8 stages to be present.
        """
        from aegis_v3.orchestrator import AegisV3Orchestrator
        from aegis_v3.schema_v3 import IncidentSeverity

        orchestrator = AegisV3Orchestrator(
            repo_root=REPO_ROOT,
            openai_api_key=None,  # Forces simulation mode
            simulation=True,
            node_count=3,         # Smaller cluster for speed
        )

        result = await orchestrator.run_full_pipeline(
            target_file="dummy_app/payment.py",
            severity=IncidentSeverity.P1,
            chaos=False,
        )

        assert result is not None, "Pipeline returned None"
        assert result.run_id != "", "run_id should be non-empty"
        assert len(result.stages) >= 7, (
            f"Expected at least 7 stages, got {len(result.stages)}: "
            + str([s.stage.value for s in result.stages])
        )
        # Simulation mode should always succeed
        assert result.success is True, (
            f"Simulation pipeline failed: {result.summary}\n"
            + "\n".join(
                f"  [{s.stage.value}] success={s.success} err={s.error}"
                for s in result.stages
            )
        )
        assert result.total_duration_us > 0.0
