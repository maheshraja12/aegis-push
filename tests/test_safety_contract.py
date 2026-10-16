"""Safety contract tests for AEGIS's non-production prototype."""
import pytest

from aegis_v3.orchestrator import AegisV3Orchestrator
from aegis_v3.schema_v3 import PipelineStage


@pytest.mark.asyncio
async def test_deployment_stage_never_writes_without_explicit_opt_in(monkeypatch, tmp_path):
    monkeypatch.delenv("AEGIS_ENABLE_DEMO_DEPLOYMENT", raising=False)
    orchestrator = object.__new__(AegisV3Orchestrator)
    orchestrator._simulation = False
    orchestrator._repo_root = str(tmp_path)

    result = await orchestrator._stage_deployment(
        "raise RuntimeError('must not execute')",
        "dummy_app/payment.py",
        "safety-contract",
    )

    assert result.stage == PipelineStage.DEPLOYMENT
    assert result.success is False
    assert result.data["deployment_skipped"] is True
    assert not (tmp_path / "dummy_app" / "payment.py").exists()


@pytest.mark.asyncio
async def test_demo_opt_in_still_requires_a_real_disposable_checkout(monkeypatch, tmp_path):
    """An invalid checkout must fail safely even when the opt-in is set."""
    monkeypatch.setenv("AEGIS_ENABLE_DEMO_DEPLOYMENT", "1")
    orchestrator = object.__new__(AegisV3Orchestrator)
    orchestrator._simulation = True
    orchestrator._repo_root = str(tmp_path / "missing-checkout")

    result = await orchestrator._stage_deployment(
        "print('candidate')",
        "dummy_app/payment.py",
        "safety-contract",
    )

    assert result.stage == PipelineStage.DEPLOYMENT
    assert result.success is False
    assert result.error
