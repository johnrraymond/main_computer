"""Boundary contract for read-only fresh-profile RPC origin diagnostics."""
import importlib.util
from pathlib import Path
import shutil
import subprocess
import pytest

ROOT = Path(__file__).resolve().parents[1]
ENTRY = ROOT / 'tools' / 'worker_browser_acceptance.py'
SPEC = importlib.util.spec_from_file_location('worker_rpc_probe_tested', ENTRY)
app = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(app)


def test_probe_command_and_cached_profile_enforced(monkeypatch, tmp_path):
    assert app.commands('rpc-probe') == [['node', 'native_acceptance.mjs']]
    monkeypatch.setattr(app, 'WALLET_FILE', tmp_path / 'wallet.json')
    monkeypatch.setattr(app, 'PROFILE_DIR', tmp_path / 'profile')
    monkeypatch.setattr(app, 'PROFILE_MARKER', tmp_path / 'marker')
    monkeypatch.setattr(app, 'checked_env', lambda action, env: None)
    with pytest.raises(app.AcceptanceError, match='existing disposable wallet profile'):
        app.run(['rpc-probe'])


def test_probe_implementation_never_mutates_wallet_or_network():
    source = (ROOT/'tools'/'worker_browser_acceptance'/'native_acceptance.mjs').read_text()
    # Branch must precede all onboarding/Connect code paths.
    a = source.index("if (action === 'rpc-probe')")
    b = source.index('const freshProfile', a)
    probe_branch = source[a:b]
    assert 'probeRpcClients(' in probe_branch
    assert 'importDisposableWallet(' not in probe_branch
    assert 'completeWorkerWalletConnection(' not in probe_branch
    assert 'wallet_addEthereumChain' not in probe_branch
    assert "rpc-origin-probe.json" in probe_branch


def test_actual_rpc_probe_regressions():
    if not (node := shutil.which('node')):
        pytest.skip('Node.js required')
    result = subprocess.run([node, '--test', str(ROOT/'tools'/'worker_browser_acceptance'/'rpc_client_probe.test.mjs')],
                            capture_output=True, text=True, timeout=20, check=False)
    assert result.returncode == 0, result.stdout + result.stderr
    assert '# pass 16' in result.stdout
