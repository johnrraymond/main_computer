"""Captain smoke cannot depend on the viewport/MCEL application catalog."""
from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / 'tools' / 'worker_browser_acceptance.py'


def _harness():
    spec = importlib.util.spec_from_file_location('captain_isolation_harness', MODULE)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_general_cli_captain_smoke_help_does_not_initialize_viewport():
    process = subprocess.run(
        [sys.executable, '-m', 'main_computer.cli', 'captain', 'smoke', '--help'],
        cwd=ROOT, capture_output=True, text=True, timeout=30,
    )
    assert process.returncode == 0, process.stderr[-500:]
    assert '--prepare-only' in process.stdout
    assert 'VirtualMcelAssetError' not in process.stderr


def test_direct_captain_entrypoint_help_uses_same_smoke_options():
    process = subprocess.run(
        [sys.executable, '-m', 'main_computer.captain_entrypoint', 'smoke', '--help'],
        cwd=ROOT, capture_output=True, text=True, timeout=30,
    )
    assert process.returncode == 0, process.stderr[-500:]
    assert '--network' in process.stdout
    assert '--prepare-only' in process.stdout


def test_captain_entrypoint_import_does_not_pull_viewport_dependencies():
    code = (
        'import sys; import main_computer.captain_entrypoint; '
        'assert not any(n.startswith("main_computer.viewport") for n in sys.modules); '
        'assert "main_computer.mcel_application_virtual_assets" not in sys.modules; '
        'print("CAPTAIN_IMPORT_ISOLATED")'
    )
    result = subprocess.run([sys.executable, '-c', code], cwd=ROOT,
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr[-500:]
    assert 'CAPTAIN_IMPORT_ISOLATED' in result.stdout


def test_pending_bridge_guard_does_not_modify_recovery_record(tmp_path, monkeypatch):
    harness = _harness()
    monkeypatch.setattr(harness, 'ROOT', tmp_path)
    folder = tmp_path / 'runtime/state/captain/pending-bridge-deposits'
    folder.mkdir(parents=True)
    record = folder / ('a' * 64 + '.json')
    record.write_text('{"transaction_hash":"public-but-preserve-this"}', encoding='utf-8')
    before = record.read_bytes()
    with pytest.raises(harness.AcceptanceError, match='1 unresolved Captain bridge deposit record'):
        harness.refuse_unreconciled_captain_bridge_records()
    assert record.read_bytes() == before


def test_pending_bridge_guard_allows_clean_work_cycle(tmp_path, monkeypatch):
    harness = _harness()
    monkeypatch.setattr(harness, 'ROOT', tmp_path)
    harness.refuse_unreconciled_captain_bridge_records()
    folder = tmp_path / 'runtime/state/captain/pending-bridge-deposits'
    folder.mkdir(parents=True)
    (folder / 'accounting-drift.jsonl').write_text('diagnostic only', encoding='utf-8')
    harness.refuse_unreconciled_captain_bridge_records()


def test_pending_bridge_guard_is_before_wallet_and_captain_spend():
    source = MODULE.read_text(encoding='utf-8')
    assert source.index('        refuse_unreconciled_captain_bridge_records()') < source.index("    if args.action == 'plan':")
    assert "if args.action == 'run' and args.mode == 'work-cycle':" in source


def test_smoke_entrypoint_calls_existing_captain_implementation(monkeypatch):
    import main_computer.captain_entrypoint as entry

    captured = {}
    def fake(argv, *, cwd):
        captured['argv'] = argv
        captured['cwd'] = cwd
        return 7
    monkeypatch.setattr(entry, 'run_captain', fake)
    assert entry.main(['smoke', 'probe', '--network', 'testnet', '--prepare-only']) == 7
    assert captured['argv'] == ['smoke', 'probe', '--network', 'testnet', '--prepare-only']
    assert captured['cwd'] == Path.cwd()


def test_pending_inspection_reports_only_public_record_fields_without_mutation(tmp_path, monkeypatch):
    import json
    harness = _harness()
    monkeypatch.setattr(harness, 'ROOT', tmp_path)
    folder = tmp_path / 'runtime/state/captain/pending-bridge-deposits'
    folder.mkdir(parents=True)
    record = folder / ('f' * 64 + '.json')
    data = {
        'transaction_hash': '0x' + '1' * 64,
        'deposit_id': '0x' + '2' * 64,
        'wallet_address': '0x' + '3' * 40,
        'amount_credit_wei': '1000',
        'receipt': {'status': '0x1', 'blockNumber': '0x42'},
        'private_key': 'MUST_NOT_BE_EMITTED',
    }
    record.write_text(json.dumps(data), encoding='utf-8')
    before = record.read_bytes()
    info = harness.captain_pending_bridge_evidence()
    assert info['readOnly'] is True
    assert info['count'] == 1
    assert info['records'][0]['receiptStatus'] == '0x1'
    assert 'MUST_NOT_BE_EMITTED' not in json.dumps(info)
    assert record.read_bytes() == before


def test_pending_inspection_cli_action_is_read_only(tmp_path, monkeypatch, capsys):
    import json
    harness = _harness()
    monkeypatch.setattr(harness, 'ROOT', tmp_path)
    assert harness.run(['captain-pending']) == 0
    output = json.loads(capsys.readouterr().out)
    assert output['count'] == 0
    assert output['reconciliation'] == 'not-verified-against-Hub-or-chain'
