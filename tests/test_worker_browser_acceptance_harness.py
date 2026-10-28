"""Offline harness checks; actual MetaMask acceptance runs on Windows."""
from __future__ import annotations

import importlib.util
import io
import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from contextlib import redirect_stdout

import pytest

ROOT = Path(__file__).resolve().parents[1]
ENTRY = ROOT / 'tools' / 'worker_browser_acceptance.py'
SPEC = importlib.util.spec_from_file_location('worker_browser_acceptance_tested', ENTRY)
assert SPEC and SPEC.loader
app = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(app)
PROJECT = ROOT / 'tools' / 'worker_browser_acceptance'


def test_plan_has_no_mutations_or_network_calls():
    output = io.StringIO()
    with redirect_stdout(output):
        assert app.run(['plan']) == 0
    plan = json.loads(output.getvalue())
    assert plan['mode'] == 'wallet'
    assert plan['production_code_changed'] is False
    assert plan['purchases_credits'] is False
    assert 'generate disposable wallet' in plan['wallet_policy']
    assert plan['commands']['run'][0] == ['node', 'native_acceptance.mjs']
    assert plan['commands']['cache-wallet'][0] == ['node', 'native_acceptance.mjs']


@pytest.mark.parametrize('bad', [
    'https://127.0.0.1:8765/applications/worker',
    'http://remote.example/applications/worker',
    'http://127.0.0.1:8765/applications/wallet',
    'http://127.0.0.1:8765/applications/worker?x=1',
    'http://127.0.0.1/applications/worker',
])
def test_refuses_nonlocal_or_wrong_application(bad):
    with pytest.raises(app.AcceptanceError):
        app.valid_worker_url(bad)


def test_valid_loopback_urls():
    assert app.valid_worker_url(app.DEFAULT_URL) == app.DEFAULT_URL
    assert app.valid_worker_url('http://localhost:8765/applications/worker')


def test_wallet_cache_does_not_require_user_secrets(monkeypatch, tmp_path):
    monkeypatch.setattr(app, 'PROJECT', tmp_path)
    (tmp_path / 'node_modules').mkdir()
    app.checked_env('cache-wallet', {})
    app.checked_env('run', {})
    with pytest.raises(app.AcceptanceError, match='Remove legacy'):
        app.checked_env('cache-wallet', {'MC_WORKER_TEST_MNEMONIC': 'test seed'})
    with pytest.raises(app.AcceptanceError, match='Remove legacy'):
        app.checked_env('run', {'MC_WORKER_TEST_PASSWORD': 'old'})


def test_worker_offer_requires_explicit_authorization():
    with pytest.raises(app.AcceptanceError, match='authorize-worker-offer'):
        app.run(['run', '--mode', 'worker'])


def test_real_metamask_native_windows_not_a_mock():
    spec = (PROJECT / 'native_acceptance.mjs').read_text(encoding='utf8')
    assert "chromium.launchPersistentContext(profileDir" in spec
    assert "channel: 'chromium', headless: false" in spec
    assert "--load-extension=${extensionDir}" in spec
    assert "from '@synthetixio/synpress/playwright'" in spec
    assert 'completeWorkerWalletConnection({' in spec
    assert 'metamask.addNetwork(' not in spec
    assert 'metamask.switchNetwork(' not in spec
    assert 'startWorkerWalletConnection(page, {' in spec
    assert 'approveWorkerMskSignature(context, page, extensionId, walletSecret.address' in spec
    assert "method: 'eth_blockNumber'" in spec
    assert 'waitForWorkerMsk({' in spec
    assert "toHaveText('Accepted'" in spec
    assert "await importDisposableWallet(activePage, walletSecret.mnemonic.trim(), walletSecret.password, {context, extensionId})" in spec
    assert "safeExtensionTabsDiagnostics(context, extensionId, walletPage)" in spec
    assert 'metamask.importWallet(' not in spec
    assert 'selectMetaMaskPage(context, extensionId, {fresh: freshProfile})' in spec
    assert "expect(wallet.chainId).toBe('0x28757b1')" in spec
    assert 'expect(wallet.blockNumber).toMatch' in spec
    assert 'page.route(' not in spec and 'addInitScript(' not in spec
    assert 'Synpress MetaMask cache' not in spec
    assert "'0x28757b1'" in spec
    assert 'wallet.account.toLowerCase()).toBe(walletSecret.address.toLowerCase())' in spec


def test_metamask_current_onboarding_offline_state_machine():
    """Run real JS navigation code against deterministic Playwright-page fixtures."""
    node = shutil.which('node')
    if not node:
        pytest.skip('Node.js is not installed')
    fixture = PROJECT / 'metamask_onboarding.test.mjs'
    module = PROJECT / 'metamask_onboarding.mjs'
    source = module.read_text(encoding='utf-8')
    assert "locator('#metametrics-opt-in')" not in source
    assert 'chrome-extension://' in source
    assert 'page.screenshot' not in source
    result = subprocess.run([node, '--test', str(fixture)], cwd=PROJECT,
                            capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stdout + result.stderr
    passed = re.search(r'^# pass (\d+)$', result.stdout, re.MULTILINE)
    assert passed and int(passed.group(1)) >= 26, result.stdout
    assert 'pressSequentially' in source and "page.keyboard.press('Tab')" in source
    assert "page.keyboard.press('Space')" not in source
    assert 'v13 SRP editor may detach after typing' in result.stdout
    assert 'selects rendered existing-wallet welcome over earlier stale MetaMask onboarding tab' in result.stdout


def test_worker_connection_approval_offline_regressions():
    """Worker owns network negotiation; only known MetaMask dialogs are approved."""
    node = shutil.which('node')
    if not node:
        pytest.skip('Node.js is not installed')
    adapter = PROJECT / 'worker_wallet_approvals.mjs'
    testfile = PROJECT / 'worker_wallet_approvals.test.mjs'
    source = adapter.read_text(encoding='utf-8')
    native = (PROJECT / 'native_acceptance.mjs').read_text(encoding='utf-8')
    assert 'startWorkerWalletConnection(page, {' in native
    assert 'completeWorkerWalletConnection({' in native
    assert 'metamask.addNetwork(' not in native
    assert 'metamask.switchNetwork(' not in native
    assert 'wallet_addEthereumChain' in source
    assert 'addInitScript(' not in source and 'page.route(' not in source
    assert 'walletSecret.mnemonic' not in source and 'walletSecret.password' not in source
    result = subprocess.run([node, '--test', str(testfile)], cwd=PROJECT,
                            capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stdout + result.stderr
    passed = re.search(r'^# pass (\d+)$', result.stdout, re.MULTILINE)
    assert passed and int(passed.group(1)) >= 8, result.stdout


def test_wallet_generation_uses_secure_entropy_not_a_shared_seed():
    source = (PROJECT / 'create_wallet.mjs').read_text(encoding='utf8')
    assert 'Wallet.createRandom()' in source
    assert 'randomBytes(24)' in source
    assert 'writeFileSync(temporary' in source
    assert "mode: 0o600" in source
    assert 'MC_WORKER_TEST_MNEMONIC' not in source
    assert 'console.log(\'Generated disposable test wallet:\', wallet.address)' in source
    assert 'console.log(wallet.mnemonic' not in source
    package = json.loads((PROJECT / 'package.json').read_text(encoding='utf-8'))
    lock = json.loads((PROJECT / 'package-lock.json').read_text(encoding='utf-8'))
    assert package['devDependencies']['ethers'] == '^5.8.0'
    assert lock['packages']['']['devDependencies']['ethers'] == '^5.8.0'
    assert lock['packages']['node_modules/ethers']['version'].startswith('5.')
    assert 'Wallet.fromMnemonic' in source and 'Wallet.fromPhrase' in source



@pytest.mark.parametrize('recovery_method', ('fromMnemonic', 'fromPhrase'))
def test_wallet_creation_reuse_and_validation_with_both_ethers_apis(tmp_path, recovery_method):
    """Exercise the actual Node script with minimal v5/v6 module-boundary fixtures.

    No external network, browser, wallet secrets or npm installation required.
    The module fixture only tests dispatch/validation, not cryptography.
    """
    if not (node := shutil.which('node')):
        pytest.skip('Node.js is not installed')
    harness = tmp_path / 'harness'
    harness.mkdir()
    (harness / 'create_wallet.mjs').write_bytes((PROJECT / 'create_wallet.mjs').read_bytes())
    ethers_dir = harness / 'node_modules' / 'ethers'
    ethers_dir.mkdir(parents=True)
    (ethers_dir / 'package.json').write_text(json.dumps({
        'name': 'ethers', 'type': 'module', 'exports': './index.mjs',
    }), encoding='utf-8')
    (ethers_dir / 'index.mjs').write_text(f"""
const address = '0x' + 'a'.repeat(40)
export const Wallet = {{
  createRandom() {{ return {{address, mnemonic: {{phrase: 'disposable fixture mnemonic'}}}} }},
  {recovery_method}(phrase) {{
    if (phrase !== 'disposable fixture mnemonic') throw new Error('invalid mnemonic')
    return {{address}}
  }},
}}
""", encoding='utf-8')
    wallet_file = tmp_path / 'private' / 'wallet.json'
    env = {**os.environ, 'MC_WORKER_TEST_WALLET_FILE': str(wallet_file)}

    def invoke(*args):
        return subprocess.run([node, str(harness / 'create_wallet.mjs'), *args],
                              env=env, capture_output=True, text=True, check=False)

    generated = invoke()
    assert generated.returncode == 0, generated.stderr
    assert 'Generated disposable test wallet:' in generated.stdout
    stored = json.loads(wallet_file.read_text(encoding='utf-8'))
    assert stored['address'] == '0x' + 'a' * 40
    assert stored['password'].startswith('Test@')
    assert len(stored['password']) > 20
    assert stored['mnemonic'] not in generated.stdout
    assert stored['password'] not in generated.stdout

    reused = invoke()
    assert reused.returncode == 0, reused.stderr
    assert 'Reusing disposable test wallet:' in reused.stdout
    assert json.loads(wallet_file.read_text(encoding='utf-8')) == stored

    wallet_file.write_text(json.dumps({**stored, 'address': '0x' + 'b' * 40}))
    rejected = invoke()
    assert rejected.returncode != 0
    assert 'Existing test wallet is invalid' in rejected.stderr
    assert json.loads(wallet_file.read_text(encoding='utf-8'))['address'] == '0x' + 'b' * 40

    rotated = invoke('--rotate')
    assert rotated.returncode == 0, rotated.stderr
    assert json.loads(wallet_file.read_text(encoding='utf-8'))['address'] == stored['address']


def test_wallet_script_with_installed_ethers_when_available(tmp_path):
    """Catch actual ethers API mismatches on the operator's installed toolchain."""
    if not (node := shutil.which('node')):
        pytest.skip('Node.js is not installed')
    if not (PROJECT / 'node_modules' / 'ethers').exists():
        pytest.skip('Project ethers dependency is not installed')
    wallet_file = tmp_path / 'state' / 'wallet.json'
    env = {**os.environ, 'MC_WORKER_TEST_WALLET_FILE': str(wallet_file)}
    for expected in ('Generated disposable test wallet:', 'Reusing disposable test wallet:'):
        result = subprocess.run([node, str(PROJECT / 'create_wallet.mjs')],
                                env=env, cwd=PROJECT, capture_output=True, text=True, check=False)
        assert result.returncode == 0, result.stderr
        assert expected in result.stdout
    assert json.loads(wallet_file.read_text(encoding='utf-8'))['version'] == 1

def test_profile_reuses_matching_wallet_and_clears_mismatch(monkeypatch, tmp_path):
    profile = tmp_path / 'profile'
    marker = tmp_path / 'marker.txt'
    monkeypatch.setattr(app, 'PROFILE_DIR', profile)
    monkeypatch.setattr(app, 'PROFILE_MARKER', marker)
    profile.mkdir()
    (profile / 'some-state').write_text('cached')
    marker.write_text('0x' + 'a' * 40 + '\n')
    app.prepare_profile('0x' + 'a' * 40)
    assert (profile / 'some-state').exists()
    app.prepare_profile('0x' + 'b' * 40)
    assert not profile.exists()
    assert not marker.exists()


def test_symlinked_profile_refused(monkeypatch, tmp_path):
    dest = tmp_path / 'target'
    dest.mkdir()
    link = tmp_path / 'linked'
    try:
        link.symlink_to(dest, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip('Host does not permit symlinks')
    monkeypatch.setattr(app, 'PROFILE_DIR', link)
    monkeypatch.setattr(app, 'PROFILE_MARKER', tmp_path / 'marker')
    with pytest.raises(app.AcceptanceError, match='symlinked'):
        app.prepare_profile('0x' + 'a' * 40)
    assert dest.exists()


def test_previous_run_rotates_only_same_wallet(monkeypatch, tmp_path):
    marker = tmp_path / 'used.json'
    monkeypatch.setattr(app, 'WALLET_LAST_RUN', marker)
    assert not app.previous_wallet_run('0xaaa')
    marker.write_text('{"address":"0xaaa"}')
    assert app.previous_wallet_run('0xaaa')
    assert not app.previous_wallet_run('0xbbb')
    marker.write_text('corrupt')
    assert app.previous_wallet_run('0xbbb')  # Fail closed for uncertain prior state.


def test_no_paid_cli_and_production_files_untouched():
    spec = (PROJECT / 'native_acceptance.mjs').read_text(encoding='utf8')
    assert 'bridge-credits' not in spec
    assert 'submitPaid' not in spec
    assert "action: 'finish'" not in spec
    assert 'work-session-finish-requested' in spec
    assert 'MC_WORKER_TEST_WALLET_FILE' in spec


def test_run_auto_provisions_wallet_and_rotates_on_next_signing_run(monkeypatch, tmp_path):
    from types import SimpleNamespace

    reports = tmp_path / 'reports'
    state = tmp_path / 'state'
    state.mkdir()
    monkeypatch.setattr(app, 'REPORTS', reports)
    monkeypatch.setattr(app, 'WALLET_STATE', state)
    monkeypatch.setattr(app, 'WALLET_FILE', state / 'wallet.json')
    monkeypatch.setattr(app, 'WALLET_LAST_RUN', state / 'wallet-last-run.json')
    monkeypatch.setattr(app, 'checked_env', lambda action, env: None)
    monkeypatch.setattr(app.shutil, 'which', lambda executable: executable)
    monkeypatch.setattr(app.subprocess, 'run', lambda *a, **kw: SimpleNamespace(stdout='v22.16.0'))

    class AvailableWorker:
        status = 200
        def __enter__(self): return self
        def __exit__(self, *args): return False

    monkeypatch.setattr(app, 'urlopen', lambda *a, **kw: AvailableWorker())
    fresh_flags = []
    extension_paths = []

    def fake_provision(env, *, fresh=False):
        fresh_flags.append(fresh)
        address = '0x' + ('b' if fresh else 'a') * 40
        app.WALLET_FILE.write_text(json.dumps({'address': address}))
        return address

    monkeypatch.setattr(app, 'provision_wallet', fake_provision)
    monkeypatch.setattr(app, 'ensure_metamask_extension', lambda: tmp_path / 'extension')

    def fake_execute(cmd, env):
        assert cmd == ['node', 'native_acceptance.mjs']
        assert env['MC_WORKER_TEST_EXTENSION_DIR'] == str(tmp_path / 'extension')
        reports.mkdir(parents=True, exist_ok=True)
        (reports / 'acceptance.json').write_text(json.dumps({'ok': True}))
        return 0

    monkeypatch.setattr(app, 'execute', fake_execute)
    assert app.run(['run']) == 0
    assert app.run(['run']) == 0
    assert fresh_flags == [False, True]
    assert app.WALLET_LAST_RUN.exists()
    # Existing wallet can still be reused for the separately authorized worker test.
    assert app.run(['run', '--mode', 'worker', '--authorize-worker-offer']) == 0
    assert fresh_flags == [False, True, False]


def _fake_official_release(monkeypatch, zip_bytes, *, published_sha=None, tag=None, asset_digest=None,
                           asset_url=None, checksums_override=None):
    """Exercise the official GitHub ZIP and SHA256SUMS contract, without network access."""
    import hashlib

    name = f'metamask-chrome-{app.METAMASK_VERSION}.zip'
    base = f'https://github.com/MetaMask/metamask-extension/releases/download/v{app.METAMASK_VERSION}/'
    digest = hashlib.sha256(zip_bytes).hexdigest()
    reported = digest if published_sha is None else published_sha
    sums = (f'{reported}  {name}\n').encode() if checksums_override is None else checksums_override
    release = {'tag_name': tag or f'v{app.METAMASK_VERSION}',
               'assets': [{'name': name, 'browser_download_url': asset_url or base + name,
                           'digest': asset_digest if asset_digest is not None else f'sha256:{reported}'},
                          {'name': 'SHA256SUMS', 'browser_download_url': base + 'SHA256SUMS'}]}
    calls = []

    def fetch(request, **kw):
        calls.append(request.full_url)
        if request.full_url == app.METAMASK_RELEASE_API:
            return io.BytesIO(json.dumps(release).encode())
        if request.full_url == base + 'SHA256SUMS':
            return io.BytesIO(sums)
        return io.BytesIO(zip_bytes)

    monkeypatch.setattr(app, 'urlopen', fetch)
    return calls


def _fake_metamask_zip(*, version=None, manifest_version=3, traversal=False):
    import zipfile

    archive_bytes = io.BytesIO()
    with zipfile.ZipFile(archive_bytes, 'w') as archive:
        archive.writestr('manifest.json', json.dumps({
            'version': version if version is not None else app.METAMASK_VERSION,
            'manifest_version': manifest_version, 'name': 'MetaMask',
        }))
        archive.writestr('background.js', '/* authentic archive shape fixture */')
        if traversal:
            archive.writestr('../outside', 'do not write')
    return archive_bytes.getvalue()


def _fake_extension_state(monkeypatch, tmp_path):
    state = tmp_path / 'state'
    monkeypatch.setattr(app, 'WALLET_STATE', state)
    monkeypatch.setattr(app, 'METAMASK_DIR', state / 'metamask-extension')
    return state


def test_pinned_official_extension_download_and_cache(monkeypatch, tmp_path):
    _fake_extension_state(monkeypatch, tmp_path)
    calls = _fake_official_release(monkeypatch, _fake_metamask_zip())
    assert app.ensure_metamask_extension() == app.METAMASK_DIR
    assert (app.METAMASK_DIR / 'background.js').is_file()
    proof = json.loads((app.METAMASK_DIR / app.METAMASK_RELEASE_PROOF_NAME).read_text())
    assert proof['releaseVersion'] == app.METAMASK_VERSION
    assert proof['manifestVersion'] == app.METAMASK_VERSION
    assert len(proof['archiveSha256']) == 64
    assert len(calls) == 3
    assert app.ensure_metamask_extension() == app.METAMASK_DIR
    assert len(calls) == 3  # Does not re-download the verified extension.


def test_official_checksum_allows_distinct_chrome_manifest_version(monkeypatch, tmp_path, capsys):
    """Reproduce the actual refusal path while retaining release integrity."""
    _fake_extension_state(monkeypatch, tmp_path)
    _fake_official_release(monkeypatch, _fake_metamask_zip(version=app.METAMASK_VERSION + '.0'))
    assert app.ensure_metamask_extension() == app.METAMASK_DIR
    assert f"manifest version '{app.METAMASK_VERSION}.0' differs" in capsys.readouterr().out
    assert app.ensure_metamask_extension() == app.METAMASK_DIR


def test_official_checksum_mismatch_refused(monkeypatch, tmp_path):
    _fake_extension_state(monkeypatch, tmp_path)
    _fake_official_release(monkeypatch, _fake_metamask_zip(), published_sha='0' * 64)
    with pytest.raises(app.AcceptanceError, match='archive SHA256 differs'):
        app.ensure_metamask_extension()
    assert not app.METAMASK_DIR.exists()


def test_metadata_digest_mismatch_refused(monkeypatch, tmp_path):
    _fake_extension_state(monkeypatch, tmp_path)
    _fake_official_release(monkeypatch, _fake_metamask_zip(), asset_digest='sha256:' + '0' * 64)
    with pytest.raises(app.AcceptanceError, match='digest disagrees'):
        app.ensure_metamask_extension()
    assert not app.METAMASK_DIR.exists()


def test_missing_official_checksum_refused(monkeypatch, tmp_path):
    _fake_extension_state(monkeypatch, tmp_path)
    _fake_official_release(monkeypatch, _fake_metamask_zip(), checksums_override=b'')
    with pytest.raises(app.AcceptanceError, match='lacks unique checksum'):
        app.ensure_metamask_extension()
    assert not app.METAMASK_DIR.exists()


def test_official_release_tag_mismatch_refused(monkeypatch, tmp_path):
    _fake_extension_state(monkeypatch, tmp_path)
    _fake_official_release(monkeypatch, _fake_metamask_zip(), tag='v1.2.3')
    with pytest.raises(app.AcceptanceError, match='metadata tag differs'):
        app.ensure_metamask_extension()
    assert not app.METAMASK_DIR.exists()


def test_manifest_version_incompatible_refused_despite_valid_sha(monkeypatch, tmp_path):
    _fake_extension_state(monkeypatch, tmp_path)
    _fake_official_release(monkeypatch, _fake_metamask_zip(manifest_version=4))
    with pytest.raises(app.AcceptanceError, match='Unsupported MetaMask Chrome manifest_version: 4'):
        app.ensure_metamask_extension()
    assert not app.METAMASK_DIR.exists()


def test_cached_extension_tampering_refused(monkeypatch, tmp_path):
    _fake_extension_state(monkeypatch, tmp_path)
    _fake_official_release(monkeypatch, _fake_metamask_zip())
    assert app.ensure_metamask_extension() == app.METAMASK_DIR
    manifest = app.METAMASK_DIR / 'manifest.json'
    manifest.write_text(manifest.read_text() + '  ', encoding='utf-8')
    with pytest.raises(app.AcceptanceError, match='release proof or its manifest changed'):
        app.ensure_metamask_extension()


def test_extension_install_rejects_nonofficial_url(monkeypatch, tmp_path):
    _fake_extension_state(monkeypatch, tmp_path)
    _fake_official_release(monkeypatch, _fake_metamask_zip(),
                           asset_url='https://evil.invalid/extension.zip')
    with pytest.raises(app.AcceptanceError, match='pinned official repository'):
        app.ensure_metamask_extension()
    assert not app.METAMASK_DIR.exists()


def test_extension_install_refuses_zip_traversal(monkeypatch, tmp_path):
    state = _fake_extension_state(monkeypatch, tmp_path)
    _fake_official_release(monkeypatch, _fake_metamask_zip(traversal=True))
    with pytest.raises(app.AcceptanceError, match='Unsafe MetaMask archive path'):
        app.ensure_metamask_extension()
    assert not app.METAMASK_DIR.exists()
    assert not (state / 'outside').exists()


def test_reserve_siphon_requires_separate_explicit_work_cycle_authorization():
    # No autonomous reserve payout from ordinary Worker registration or plan.
    with pytest.raises(app.AcceptanceError, match='reserve-siphon'):
        app.run(['plan', '--authorize-reserve-siphon'])
    with pytest.raises(app.AcceptanceError, match='reserve-siphon'):
        app.run(['run', '--mode', 'work-cycle', '--authorize-reserve-siphon'])
    script = (PROJECT / 'native_acceptance.mjs').read_text(encoding='utf8')
    assert "MC_WORKER_TEST_AUTHORIZE_RESERVE_SIPHON !== 'explicit'" in script
    assert "evidence?.reason === 'insufficient_credit_under_cumulative_cap'" in script
    assert "'--required-credit-wei', requiredCreditWei, '--execute'" in script
    assert 'captainFundingProbe({python, cwd, requiredCreditWei, priceCeilingCredits})' in script
