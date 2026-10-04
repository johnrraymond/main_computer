from __future__ import annotations

import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "tools" / "space_captain_clef_backend.py"
LIVE_SMOKE = ROOT / "tools" / "space_captain_live_clef_smoke.py"
CONTRACT_SMOKE = ROOT / "tools" / "space_captain_differentiable_smoke.py"


def test_live_clef_tools_have_help_without_loading_cuda() -> None:
    for script in (BACKEND, LIVE_SMOKE):
        proc = subprocess.run(
            [sys.executable, str(script), "--help"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        assert proc.returncode == 0, proc.stderr or proc.stdout


def test_live_backend_loads_evolving_checkpoint_and_scores_one_ensemble_call() -> None:
    source = BACKEND.read_text(encoding="utf-8")
    assert "nanojev_three_backbone_clef_tinystories_consensus_pairwise_train.py" in source
    assert '"head.safetensors"' in source
    assert '"tinystories.safetensors"' in source
    assert 'task="captain_ensemble"' in source
    assert '"provider": "live-tinystories-clef"' in source
    assert "smoke.load_backbones" in source
    assert "self.head(evidence)" in source
    assert '"captainModelCallCount": 1' in source
    assert '"clefHeadForwardCount": 1' in source
    assert '"schema": "game.captainDecisionResponse.v2"' in source


def test_live_wrapper_runs_call_time_characterization_contract() -> None:
    source = LIVE_SMOKE.read_text(encoding="utf-8")
    assert "space_captain_clef_backend.py" in source
    assert "space_captain_differentiable_smoke.py" in source
    assert '"--steps", str(int(args.steps))' in source
    assert '"--decision-interval-seconds", str(float(args.decision_interval_seconds))' in source
    assert '"--questions-per-call", str(int(args.questions_per_call))' in source

    contract = CONTRACT_SMOKE.read_text(encoding="utf-8")
    assert "captainTemporalUnitIsOneModelCall" in contract
    assert "steadyCallLatencyP95Ms" in contract
    assert "jacketExpectedPolicySurvivesInjectedIncoherence" in contract
    assert "backendProviderUsedAsRequested" in contract
