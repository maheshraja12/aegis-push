"""Regression tests for AEGIS's fail-closed deployment guard."""
import pytest

from aegis_v3.orchestrator import AegisV3Orchestrator
from aegis_v3.schema_v3 import PipelineStage


@pytest.mark.asyncio
async def test_simulation_skips_deployment_without_explicit_opt_in(monkeypatch):
    monkeypatch.delenv("AEGIS_ENABLE_DEMO_DEPLOYMENT", raising=False)
    orchestrator = object.__new__(AegisV3Orchestrator)
    orchestrator._simulation = True

    result = await orchestrator._stage_deployment("print(1)", "dummy_app/payment.py", "test-incident")

    assert result.stage == PipelineStage.DEPLOYMENT
    assert result.success is True
    assert result.data["deployment_skipped"] is True
    assert result.data["branch_name"] is None
    assert "skipped" in result.detail.lower()


@pytest.mark.asyncio
async def test_non_simulation_deployment_fails_closed_without_opt_in(monkeypatch):
    monkeypatch.delenv("AEGIS_ENABLE_DEMO_DEPLOYMENT", raising=False)
    orchestrator = object.__new__(AegisV3Orchestrator)
    orchestrator._simulation = False

    result = await orchestrator._stage_deployment("print(1)", "dummy_app/payment.py", "test-incident")

    assert result.stage == PipelineStage.DEPLOYMENT
    assert result.success is False
    assert result.data["deployment_skipped"] is True
    assert "blocked" in result.error.lower()


@pytest.mark.asyncio
async def test_explicit_demo_opt_in_reaches_demo_deployment_logic(monkeypatch):
    # Opt-in is only for a disposable test checkout; do not run a real deployment here.
    monkeypatch.setenv("AEGIS_ENABLE_DEMO_DEPLOYMENT", "1")
    orchestrator = object.__new__(AegisV3Orchestrator)
    orchestrator._simulation = True
    orchestrator._repo_root = "/path/that/does/not/exist"

    result = await orchestrator._stage_deployment("print(1)", "dummy_app/payment.py", "test-incident")

    assert result.stage == PipelineStage.DEPLOYMENT
    assert result.success is False
    assert result.error
