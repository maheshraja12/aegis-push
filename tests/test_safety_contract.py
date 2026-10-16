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

@pytest.mark.asyncio
async def test_demo_target_path_traversal_is_rejected_before_writes(monkeypatch, tmp_path):
    """Even an opt-in cannot redirect the demo patch outside its one allowed file."""
    import subprocess

    checkout = tmp_path / "disposable-checkout"
    target = checkout / "dummy_app" / "payment.py"
    target.parent.mkdir(parents=True)
    target.write_text("def calculate_tax(amount, tax_rate):\n    return amount / tax_rate\n", encoding="utf-8")
    subprocess.run(["git", "init"], cwd=checkout, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "AEGIS Test"], cwd=checkout, check=True)
    subprocess.run(["git", "config", "user.email", "aegis-test@example.invalid"], cwd=checkout, check=True)
    subprocess.run(["git", "add", "dummy_app/payment.py"], cwd=checkout, check=True)
    subprocess.run(["git", "commit", "-m", "test fixture"], cwd=checkout, check=True, capture_output=True)

    monkeypatch.setenv("AEGIS_ENABLE_DEMO_DEPLOYMENT", "1")
    monkeypatch.setenv("AEGIS_DEMO_CHECKOUT_ROOT", str(checkout))
    orchestrator = object.__new__(AegisV3Orchestrator)
    orchestrator._simulation = True
    orchestrator._repo_root = str(checkout)
    before = target.read_text(encoding="utf-8")

    result = await orchestrator._stage_deployment("print('candidate')", "../dummy_app/payment.py", "path-traversal")

    assert result.stage == PipelineStage.DEPLOYMENT
    assert result.success is False
    assert "only dummy_app/payment.py" in result.error
    assert target.read_text(encoding="utf-8") == before
