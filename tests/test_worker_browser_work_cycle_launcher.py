"""Work-cycle CLI may create a real Testnet Captain credit-bearing request only by opt in."""
import importlib.util
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("browser_acceptance", ROOT / "tools" / "worker_browser_acceptance.py")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

@pytest.mark.parametrize('flags', [
    [], ['--authorize-worker-offer'], ['--authorize-captain-spend'],
])
def test_spending_requires_both_authorizations(flags):
    with pytest.raises(mod.AcceptanceError):
        mod.run(['run','--mode','work-cycle', *flags])


def test_explicit_mode_does_not_change_wallet_mode(monkeypatch, capsys):
    assert mod.run(['plan']) == 0
    assert 'wallet' in capsys.readouterr().out


def test_work_cycle_is_listed_as_plan_only_without_mutation(capsys):
    assert mod.run(['plan','--mode','work-cycle']) == 0
    printed = capsys.readouterr().out
    assert 'work-cycle' in printed
    assert '"purchases_credits": true' in printed
