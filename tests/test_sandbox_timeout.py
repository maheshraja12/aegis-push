"""Regression tests for sandbox worker timeout termination."""
import pytest

from aegis_v3.schema_v3 import SandboxConfig, SandboxStatus
from aegis_v3.wasm_sandbox import WasmIsolationEngine


@pytest.mark.asyncio
async def test_infinite_loop_worker_is_terminated_on_timeout():
    engine = WasmIsolationEngine(
        SandboxConfig(
            max_execution_us=2_000_000,
            max_memory_kb=4_096,
            max_ast_nodes=800,
        )
    )
    try:
        compilation, trace = await engine.run_full_isolation_pipeline(
            "while True:\n    pass",
            patch_id="timeout-regression",
        )
        assert compilation.status == SandboxStatus.COMPILED
        assert trace.status == SandboxStatus.TIMED_OUT
        assert "terminated" in (trace.fault_traceback or "").lower()
    finally:
        engine.shutdown()
