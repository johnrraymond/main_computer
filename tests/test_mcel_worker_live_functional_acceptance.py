"""Live acceptance must not weaken any of the 29 isolated Worker cases."""
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
import pytest

from tools import mcel_worker_full_functional_acceptance as runner
from tools import mcel_worker_live_functional_acceptance as live


def test_live_is_exact_local_native_candidate_only():
    assert live.valid_native_url(live.DEFAULT_URL) == live.DEFAULT_URL
    for url in ('http://localhost:8765/applications/mcel-semantic-worker',
                'http://127.0.0.1:8765/applications/worker',
                'http://127.0.0.1:8766/applications/mcel-semantic-worker',
                'http://127.0.0.1:8765/applications/mcel-semantic-worker?x=1',
                'https://127.0.0.1:8765/applications/mcel-semantic-worker',
                'http://testnet-hub.greatlibrary.io/applications/mcel-semantic-worker'):
        with pytest.raises(ValueError):
            live.valid_native_url(url)


def test_plan_writes_noncertifying_report_without_live_preflights(tmp_path):
    output = tmp_path / 'plan.json'
    assert runner.main(['--mode','live','--action','plan','--report',str(output)]) == 0
    report = json.loads(output.read_text(encoding='utf-8'))
    assert report['surface'] == 'native-mcel'
    assert report['status'] == 'NOT_CERTIFIED'
    assert report['cutover']['status'] == 'BLOCKED'
    assert all(gate['status'] == 'NOT_TESTED' for gate in report['gates'].values())


def test_live_mutations_require_explicit_authorization():
    for argv in (['--mode','live','--action','run'],
                 ['--mode','live','--action','run','--gate','wallet-msk'],
                 ['--mode','live','--action','run','--gate','offer-registration','--authorize-wallet-signing'],
                 ['--mode','live','--action','run','--gate','work-cycle','--authorize-wallet-signing','--authorize-worker-offer'],
                 ['--mode','live','--action','run','--gate','requester-enforcement']):
        with pytest.raises(SystemExit) as exc:
            runner.main(argv)
        assert exc.value.code != 0


def test_evidence_requires_actual_gate_postconditions_and_cleanup():
    for gate in live.GATES:
        evidence = {'schema':'mcel.worker.native-live-evidence.v1',
                    'gate':gate,'surface':'native-mcel','network':'testnet',
                    'ok':True,'cleanupOk':True,'proofs':{},
                    'verifiedAt':datetime.now(timezone.utc).isoformat()}
        assert not live.verify_evidence(evidence,gate)[0]
        evidence['proofs'] = {key:True for key in (
            ('realExtension','testnetChain','walletConnected','hubMskVerified','hubOfferAccepted',
             'hubSessionLive','captainCompleted','workerDelivered','hubMskRevoked','requesterPolicyRead',
             'backendDeniedUnauthorizedSpend','noPaidSubmission','budgetLimitDenied',
             'localBusyPolicyDenied','restartRetainedReservation','duplicateDenied',
             'consentReplayDenied','concurrentBudgetDenied'))}
        assert live.verify_evidence(evidence,gate)[0]
        evidence['cleanupOk'] = False
        assert not live.verify_evidence(evidence,gate)[0]
        evidence['cleanupOk'] = True
        evidence['surface'] = 'legacy'
        assert not live.verify_evidence(evidence,gate)[0]


def test_old_evidence_is_not_reused_after_source_fingerprint_change(tmp_path):
    target=tmp_path/'report.json'
    current=live.new_report(live.DEFAULT_URL,'run','wallet-msk')
    obsolete={**current,'source_fingerprint':'stale',
              'gates':{'wallet-msk':{'status':'PASS'}}}
    target.write_text(json.dumps(obsolete),encoding='utf-8')
    assert live.prior_verified_gates(target,current)=={}


def test_requester_policy_cannot_substitute_for_budget_postcondition():
    row={'schema':'mcel.worker.native-live-evidence.v1','gate':'requester-enforcement',
         'surface':'native-mcel','network':'testnet','ok':True,'cleanupOk':True,
         'verifiedAt':datetime.now(timezone.utc).isoformat(),
         'proofs':{'requesterPolicyRead':True,'backendDeniedUnauthorizedSpend':True,
                   'noPaidSubmission':True}}
    accepted,reason=live.verify_evidence(row,'requester-enforcement')
    assert not accepted and reason=='requester-budget-not-proven'


def test_stale_live_receipts_must_not_certify_cutover():
    row = {'schema':'mcel.worker.native-live-evidence.v1',
           'gate':'wallet-msk','surface':'native-mcel','network':'testnet',
           'ok':True,'cleanupOk':True,
           'verifiedAt':(datetime.now(timezone.utc)-timedelta(hours=3)).isoformat(),
           'proofs':{'realExtension':True,'testnetChain':True,'walletConnected':True,
                     'hubMskVerified':True,'hubMskRevoked':True}}
    ok,why=live.verify_evidence(row,'wallet-msk')
    assert not ok and why=='stale-or-future-live-evidence'


def test_live_failed_stage_does_not_claim_cleanup_failure():
    row = {'schema': 'mcel.worker.native-live-evidence.v1', 'gate': 'wallet-msk',
           'surface': 'native-mcel', 'network': 'testnet',
           'ok': False, 'cleanupOk': True, 'stage': 'METAMASK_PRECONNECT_UNLOCK',
           'proofs': {}}
    assert live.verify_evidence(row, 'wallet-msk') == (False, 'live-operation-failed')
    assert live.safe_stage(row) == 'METAMASK_PRECONNECT_UNLOCK'
    row['ok'] = True
    row['cleanupOk'] = False
    assert live.verify_evidence(row, 'wallet-msk') == (False, 'browser-cleanup-unverified')
    assert live.safe_stage({'stage': 'secret:some sensitive contents'}) == 'NOT_AVAILABLE'


def test_cached_metamask_unlocked_before_live_worker_network_or_wallet_actions():
    script = (live.ROOT / 'tools/worker_browser_acceptance/mcel_live_acceptance.mjs').read_text(encoding='utf-8')
    begin = script.index("proof.stage='METAMASK_PRECONNECT_UNLOCK'")
    end = script.index('await testMarketplace(page,context,extensionId)')
    assert begin < end
    assert 'ensureDisposableMetaMaskUnlocked' in script[begin:end]
    assert 'chrome-extension://${extensionId}/home.html' in script[begin:end]


@pytest.mark.parametrize('explicit_flag', [False, True])
def test_wallet_msk_defaults_to_fresh_disposable_wallet_without_an_extra_flag(tmp_path, monkeypatch, explicit_flag):
    output = tmp_path / 'live.json'
    observed = {}

    class FakeResponse:
        status = 200
        def __enter__(self):
            return self
        def __exit__(self, *_):
            return False
        def read(self, *_):
            return b'<html data-mcel-worker-phase="candidate"></html>'

    def fake_invoke(gate, report_path, timeout, url, **kwargs):
        observed.update(gate=gate, **kwargs)
        # Do not synthesize a successful Hub operation or a PASS receipt.
        return 1, 'test-controlled-stop'

    monkeypatch.setattr(live, 'urlopen', lambda *a, **k: FakeResponse())
    monkeypatch.setattr(live, 'invoke_node', fake_invoke)
    argv = ['--action', 'run', '--gate', 'wallet-msk',
            '--authorize-wallet-signing', '--report', str(output)]
    if explicit_flag:
        argv.append('--fresh-disposable-wallet')
    result = live.main(argv)
    assert result == 1
    assert observed['gate'] == 'wallet-msk'
    assert observed['fresh_disposable_wallet'] is True
    assert observed['authorize_signing'] is True
    assert json.loads(output.read_text(encoding='utf-8'))['gates']['wallet-msk']['status'] == 'UNVERIFIED'


@pytest.mark.parametrize('gate,extra', [
    ('offer-registration', ['--authorize-worker-offer']),
    ('work-cycle', ['--authorize-worker-offer', '--authorize-captain-spend', '--authorize-settings-change']),
])
@pytest.mark.parametrize('explicit_flag', [False, True])
def test_offer_gates_provision_fresh_isolated_wallet_instead_of_expired_cached_key(
        tmp_path, monkeypatch, gate, extra, explicit_flag):
    # The live request must remain uncertified if its real Hub proof is absent.
    output = tmp_path / 'live.json'
    observed = {}

    class FakeResponse:
        status = 200
        def __enter__(self):
            return self
        def __exit__(self, *_):
            return False
        def read(self, *_):
            return b'<html data-mcel-worker-phase="candidate"></html>'

    def fake_invoke(selected, report_path, timeout, url, **kwargs):
        observed.update(gate=selected, **kwargs)
        return 1, 'test-controlled-stop'

    monkeypatch.setattr(live, 'urlopen', lambda *a, **k: FakeResponse())
    monkeypatch.setattr(live, 'invoke_node', fake_invoke)
    argv = ['--action', 'run', '--gate', gate, '--authorize-wallet-signing',
            *extra, '--report', str(output)]
    if explicit_flag:
        argv.append('--fresh-disposable-wallet')
    assert live.main(argv) == 1
    assert observed['gate'] == gate
    assert observed['fresh_disposable_wallet'] is True
    assert observed['authorize_signing'] is True
    assert json.loads(output.read_text(encoding='utf-8'))['gates'][gate]['status'] == 'UNVERIFIED'


def test_compat_fresh_disposable_flag_cannot_mutate_other_gates():
    for arguments in (
        ['--action', 'plan'],
        ['--action', 'run', '--gate', 'requester-enforcement', '--authorize-settings-change'],
    ):
        with pytest.raises(SystemExit) as error:
            live.main([*arguments, '--fresh-disposable-wallet'])
        assert error.value.code != 0


def test_fresh_disposable_wallet_preparation_never_rotates_existing_test_account(tmp_path, monkeypatch):
    import subprocess
    from tools import worker_browser_acceptance as wallet
    monkeypatch.setattr(wallet, 'WALLET_STATE', tmp_path / 'owned-state')
    node_dir = tmp_path / 'node-tools'
    node_dir.mkdir()
    calls = []
    address = '0x' + '42' * 20
    def pretend_run(command, *, cwd, env, stdout, stderr, check, timeout):
        assert env['MC_WORKER_TEST_ACTION'] == 'cache-wallet'
        assert env['MC_WORKER_TEST_WALLET_FILE'].startswith(str(wallet.WALLET_STATE))
        assert command[1].startswith(str(node_dir))
        assert '--rotate' not in command
        assert stdout == subprocess.DEVNULL and stderr == subprocess.DEVNULL
        calls.append(Path(command[1]).name)
        if len(calls) == 1:
            Path(env['MC_WORKER_TEST_WALLET_FILE']).write_text(
                json.dumps({'address': address}), encoding='utf-8')
        else:
            Path(env['MC_WORKER_TEST_PROFILE_DIR']).mkdir(parents=True)
            Path(env['MC_WORKER_TEST_PROFILE_MARKER']).write_text(address, encoding='utf-8')
        return subprocess.CompletedProcess(command, 0)
    monkeypatch.setattr(live.subprocess, 'run', pretend_run)
    wallet_file, profile_dir, marker = live.provision_fresh_live_wallet(
        node='node', node_dir=node_dir, extension_dir=tmp_path / 'pinned-extension')
    assert calls == ['create_wallet.mjs','native_acceptance.mjs']
    assert wallet_file.is_file() and profile_dir.is_dir()
    assert marker.read_text() == address
    assert wallet_file.parent == profile_dir.parent
    assert wallet_file.parent != wallet.WALLET_STATE


def test_live_wallet_refreshes_existing_legacy_session_before_connect():
    script = (live.ROOT / 'tools/worker_browser_acceptance/mcel_live_acceptance.mjs').read_text(encoding='utf-8')
    refresh = script.index("proof.stage='WALLET_CONTEXT_REFRESH'")
    bridge = script.index('window.McelWorkerLegacyMarketplace.refresh()', refresh)
    panel = script.index("proof.stage='WALLET_PANEL_READY'", bridge)
    connect = script.index("proof.stage='WALLET_CONNECT'", panel)
    assert refresh < bridge < panel < connect
    assert "waitFor({state:'visible',timeout:15000})" in script[panel:connect]


def test_live_diagnostics_are_bounded_and_do_not_print_raw_errors():
    script = (live.ROOT / 'tools/mcel_worker_live_functional_acceptance.py').read_text(encoding='utf-8')
    diagnosis = script[script.index('WORKER_LIVE_WALLET_CONNECT_DIAGNOSTIC'):]
    assert 'evidence.get(\'walletConnectFailure\')' in script
    assert "re.fullmatch(r'[A-Z_]{3,72}', failure)" in script
    assert 'error.message' not in diagnosis
    assert 'print(\'WORKER_LIVE_CUTOVER\'' in diagnosis


def test_failed_connect_reports_actual_control_transitions_and_extension_targets(tmp_path, monkeypatch, capsys):
    """Do not collapse a missing MetaMask prompt into one ambiguous failure line."""
    output = tmp_path / 'live.json'

    class FakeResponse:
        status = 200
        def __enter__(self):
            return self
        def __exit__(self, *_):
            return False
        def read(self, *_):
            return b'<html data-mcel-worker-phase="candidate"></html>'

    def fake_invoke(gate, report_path, timeout, url, **kwargs):
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps({
            'schema': 'mcel.worker.native-live-evidence.v1',
            'gate': 'wallet-msk', 'network': 'testnet', 'surface': 'native-mcel',
            'ok': False, 'cleanupOk': True, 'stage': 'WALLET_CONNECT',
            'diagnosticVersion':'wallet-connect-v3',
            'errorClass': 'Error', 'proofs': {},
            'walletConnectFailure': 'METAMASK_PROMPT_MISSING',
            'walletBrowserFaults': ['TypeError'],
            'walletBrowserNetworkFailures': ['worker-api:5xx'],
            'walletConnectState': {'panelVisible':True,'buttonPresent':True,
                                   'handlerBound':True,'disabled':True,'hubChoice':'testnet'},
            'walletConnectProgress': ['worker.connect.control-ready',
                                      'worker.connect.click-dispatched',
                                      'worker.status.account-requested'],
            'walletEarlyEvents': ['provider.hydrate.start', 'connect.ethers.requestAccounts.start'],
            'walletConnectTimeline': [
                {'kind':'start','elapsedMs':0,'state':{'button':'idle','disabled':False,'status':'none'}},
                {'kind':'mutation','elapsedMs':23,'state':{'button':'requesting','disabled':True,
                                                          'contextVerified':True,'busy':True,'status':'account-requested'}},
            ],
            'walletBackendSession': {'httpOk':True,'selectedNetwork':'testnet',
                                     'status':'connected','ring':'3'},
            'walletExtension': {'pages':1,'pageStates':[{'kind':'home','networkAdd':False}],
                                'targets':[{'kind':'sidepanel','type':'page'}]},
        }),encoding='utf-8')
        return 1, 'exited-1'

    monkeypatch.setattr(live, 'urlopen', lambda *a, **k: FakeResponse())
    monkeypatch.setattr(live, 'invoke_node', fake_invoke)
    assert live.main(['--mode','live','--action','run','--gate','wallet-msk',
                      '--authorize-wallet-signing','--report',str(output)]) == 1
    printed = capsys.readouterr().out
    assert 'WORKER_LIVE_DIAGNOSTIC_VERSION version=wallet-connect-v3 evidence=complete missing-fields=none' in printed
    assert 'WORKER_LIVE_WALLET_PROGRESS phase=worker.connect.click-dispatched' in printed
    assert 'WORKER_LIVE_WALLET_STATE t-ms=0 when=start button=idle disabled=no' in printed
    assert 'WORKER_LIVE_WALLET_STATE t-ms=23 when=mutation button=requesting disabled=yes' in printed
    assert 'WORKER_LIVE_WALLET_BACKEND http-ok=yes selected=testnet status=connected ring=3' in printed
    assert 'WORKER_LIVE_METAMASK_TARGET index=0 kind=sidepanel type=page' in printed
    assert 'WORKER_LIVE_BROWSER_FAULT class=TypeError' in printed
    assert 'WORKER_LIVE_BROWSER_REQUEST_FAILURE kind=worker-api:5xx' in printed
    assert 'WORKER_LIVE_CUTOVER BLOCKED canonical-route-unchanged' in printed


def test_lifecycle_event_capture_starts_before_worker_page_load_and_watches_changes():
    script = (live.ROOT / 'tools/worker_browser_acceptance/mcel_live_acceptance.mjs').read_text(encoding='utf-8')
    early = script.index('await page.addInitScript(')
    navigation = script.index("await page.goto(url,{waitUntil:'domcontentloaded'})")
    tracker = script.index('new MutationObserver(')
    connect = script.index("proof.stage='WALLET_CONNECT'")
    assert early < navigation
    assert tracker < connect
    assert 'safeWalletApprovalDiagnostics(context,extensionIdForDiagnostics,workerPage)' in script
    assert 'captureBackendSession(workerPage)' in script
    assert 'safeWalletDiagnosticTimeline' in script


def test_diagnostic_build_marker_refuses_silent_old_javascript_harness(tmp_path,monkeypatch,capsys):
    """An old JS evidence writer must not masquerade as the installed v3 diagnostics."""
    output=tmp_path/'live.json'

    class FakeResponse:
        status=200
        def __enter__(self):return self
        def __exit__(self,*_):return False
        def read(self,*_):return b'<html data-mcel-worker-phase="candidate"></html>'

    def fake_invoke(gate,report_path,timeout,url,**kwargs):
        report_path.parent.mkdir(parents=True,exist_ok=True)
        report_path.write_text(json.dumps({
            'schema':'mcel.worker.native-live-evidence.v1','gate':'wallet-msk',
            'surface':'native-mcel','network':'testnet','ok':False,'cleanupOk':True,
            'stage':'WALLET_CONNECT','errorClass':'Error',
            'walletConnectFailure':'METAMASK_PROMPT_MISSING',
            'walletConnectState':{'buttonPresent':True,'disabled':True}
        }),encoding='utf-8')
        return 1,'exited-1'

    monkeypatch.setattr(live,'urlopen',lambda *a,**k:FakeResponse())
    monkeypatch.setattr(live,'invoke_node',fake_invoke)
    assert live.main(['--mode','live','--action','run','--gate','wallet-msk',
                      '--authorize-wallet-signing','--report',str(output)])==1
    printed=capsys.readouterr().out
    assert 'WORKER_LIVE_DIAGNOSTIC_VERSION_MISMATCH expected=wallet-connect-v3 actual=absent-or-outdated' in printed
    assert 'WORKER_LIVE_GATE wallet-msk UNVERIFIED' in printed


def test_metamask_unlock_url_is_accepted_before_wallet_identity_check():
    script = (live.ROOT / 'tools/worker_browser_acceptance/mcel_live_acceptance.mjs').read_text(encoding='utf-8')
    helper = (live.ROOT / 'tools/worker_browser_acceptance/metamask_active_url.mjs').read_text(encoding='utf-8')
    preconnect=script[script.index("proof.stage='EXTENSION_DISCOVERY'"):]
    assert preconnect.index('requireVerifiedMetaMaskUrl(before.evidence)') < preconnect.index('await ensureDisposableMetaMaskUnlocked(')
    assert preconnect.index('await ensureDisposableMetaMaskUnlocked(') < preconnect.index('requireVerifiedMetaMaskUrl(after.evidence,{requireHome:true})')
    assert "['home-root','unlock']" in helper
    assert 'requireForeground=false' in helper


def test_locked_metamask_preconnect_reports_actual_url_and_unlock_progress(tmp_path,monkeypatch,capsys):
    output=tmp_path/'live.json'
    class FakeResponse:
        status=200
        def __enter__(self):return self
        def __exit__(self,*_):return False
        def read(self,*_):return b'<html data-mcel-worker-phase="candidate"></html>'
    def fake_invoke(gate,report_path,timeout,url,**kwargs):
        report_path.parent.mkdir(parents=True,exist_ok=True)
        extension='dnamoknnfmliijgdpmkcjmgihhhidcgj'
        locked=f'chrome-extension://{extension}/home.html#/onboarding/unlock'
        home=f'chrome-extension://{extension}/home.html#/'
        report_path.write_text(json.dumps({
            'schema':'mcel.worker.native-live-evidence.v1','gate':'wallet-msk',
            'surface':'native-mcel','network':'testnet','ok':False,'cleanupOk':True,
            'stage':'METAMASK_PRECONNECT_UNLOCK','errorClass':'Error',
            'metaMaskPreconnectUrl':{'expectedUrl':home,'actualUrl':locked,
                 'selectedRoute':'unlock','visibility':'hidden','focused':False,'tabCount':1},
            'metaMaskPreconnectFailureReason':'METAMASK_UNLOCK_INCOMPLETE',
            'metaMaskUnlockProgress':['unlock-submitted'],
            'metaMaskUnlockLastState':'unlock-submitted-awaiting-verified-home'
        }),encoding='utf-8')
        return 1,'exited-1'
    monkeypatch.setattr(live,'urlopen',lambda *a,**k:FakeResponse())
    monkeypatch.setattr(live,'invoke_node',fake_invoke)
    assert live.main(['--mode','live','--action','run','--gate','wallet-msk',
                      '--authorize-wallet-signing','--report',str(output)])==1
    printed=capsys.readouterr().out
    assert 'checkpoint=before-unlock expected=home-or-unlock' in printed
    assert 'route=unlock visible=no focused=no' in printed
    assert 'reason=METAMASK_UNLOCK_INCOMPLETE' in printed
    assert 'WORKER_LIVE_METAMASK_UNLOCK_PROGRESS phase=unlock-submitted' in printed
    assert 'WORKER_LIVE_METAMASK_UNLOCK_STATE state=unlock-submitted-awaiting-verified-home' in printed
    assert 'WORKER_LIVE_CUTOVER BLOCKED' in printed


def test_msk_signing_failure_reports_click_approval_and_api_separately(tmp_path,monkeypatch,capsys):
    output=tmp_path/'report.json'
    class FakeResponse:
        status=200
        def __enter__(self):return self
        def __exit__(self,*_):return False
        def read(self,*_):return b'<html data-mcel-worker-phase="candidate"></html>'
    def fake_invoke(gate,report_path,timeout,url,**kwargs):
        report_path.parent.mkdir(parents=True,exist_ok=True)
        report_path.write_text(json.dumps({
            'schema':'mcel.worker.native-live-evidence.v1','gate':'wallet-msk',
            'surface':'native-mcel','network':'testnet','ok':False,'cleanupOk':True,
            'stage':'MSK_SIGNING_APPROVAL','errorClass':'Error','proofs':{},
            'mskSigningDiagnosticVersion':'msk-signing-v1',
            'mskFailureCode':'signature-not-signature-request',
            'mskSigning':{'clicked':True,'approved':False,'hubVerified':False,
                'apiStatus':None,'apiRequestFailed':False,
                'buttonBefore':{'count':1,'enabled':True,'visible':True},
                'events':['request.start'],
                'approvalProgress':['msk.target.home','msk.inspect.not-signature-request']},
            'mskUiState':{'status':'awaiting-signature','buttonPresent':True,'buttonDisabled':True}
        }),encoding='utf-8')
        return 1,'exited-1'
    monkeypatch.setattr(live,'urlopen',lambda *a,**k:FakeResponse())
    monkeypatch.setattr(live,'invoke_node',fake_invoke)
    assert live.main(['--mode','live','--action','run','--gate','wallet-msk',
                      '--authorize-wallet-signing','--report',str(output)])==1
    printed=capsys.readouterr().out
    assert 'WORKER_LIVE_MSK_DIAGNOSTIC_VERSION version=msk-signing-v1' in printed
    assert 'WORKER_LIVE_MSK_SIGNING stage=MSK_SIGNING_APPROVAL cause=signature-not-signature-request click=yes approved=no hub-verified=no ui=awaiting-signature api-http=none' in printed
    assert 'WORKER_LIVE_MSK_BUTTON count=1 enabled=yes visible=yes' in printed
    assert 'WORKER_LIVE_MSK_EVENT type=request.start' in printed
    assert 'WORKER_LIVE_MSK_APPROVAL phase=msk.target.home' in printed
    assert 'WORKER_LIVE_MSK_APPROVAL phase=msk.inspect.not-signature-request' in printed
    assert 'WORKER_LIVE_CUTOVER BLOCKED canonical-route-unchanged' in printed


def test_live_failure_prints_verified_screenshot_paths_and_rejects_path_injection(tmp_path, monkeypatch, capsys):
    """Failure output identifies real local PNGs; cannot print untrusted paths."""
    output = tmp_path / 'report.json'

    class FakeResponse:
        status = 200
        def __enter__(self): return self
        def __exit__(self, *_): return False
        def read(self, *_): return b'<html data-mcel-worker-phase="candidate"></html>'

    def fake_invoke(gate, report_path, timeout, url, **kwargs):
        folder = report_path.parent / 'live-wallet-msk-failure-ABCDEF'
        folder.mkdir()
        (folder / 'desktop.png').write_bytes(b'PNG-TEST')
        (folder / 'worker.png').write_bytes(b'PNG-TEST')
        report_path.write_text(json.dumps({
            'schema':'mcel.worker.native-live-evidence.v1', 'gate':'wallet-msk',
            'surface':'native-mcel','network':'testnet','ok':False,'cleanupOk':True,
            'proofs':{},'stage':'MSK_SIGNING_APPROVAL','errorClass':'Error',
            'failureScreenshots':{'version':'failure-screens-v1','captures':[
                {'surface':'desktop','status':'saved',
                 'file':'live-wallet-msk-failure-ABCDEF/desktop.png'},
                {'surface':'worker','status':'saved',
                 'file':'live-wallet-msk-failure-ABCDEF/worker.png'},
                {'surface':'metamask-home','status':'saved',
                 'file':'../untrusted/secret.png'},
                {'surface':'metamask-notification','status':'unavailable'},
            ]},
        }), encoding='utf-8')
        return 1, 'exited-1'

    monkeypatch.setattr(live, 'urlopen', lambda *args, **kwargs: FakeResponse())
    monkeypatch.setattr(live, 'invoke_node', fake_invoke)
    assert live.main(['--action','run','--gate','wallet-msk',
                      '--authorize-wallet-signing','--report',str(output)]) == 1
    printed = capsys.readouterr().out
    assert 'WORKER_LIVE_FAILURE_SCREENSHOT surface=desktop status=saved path=' in printed
    assert 'WORKER_LIVE_FAILURE_SCREENSHOT surface=worker status=saved path=' in printed
    assert 'WORKER_LIVE_FAILURE_SCREENSHOT surface=metamask-home status=unavailable' in printed
    assert '../untrusted' not in printed
    assert 'WORKER_LIVE_GATE wallet-msk UNVERIFIED' in printed
    assert 'WORKER_LIVE_CUTOVER BLOCKED' in printed


def test_msk_live_moment_paths_are_printed_and_reject_traversal(tmp_path, monkeypatch, capsys):
    output=tmp_path / 'report.json'
    class FakeResponse:
        status=200
        def __enter__(self):return self
        def __exit__(self,*_):return False
        def read(self,*_):return b'<html data-mcel-worker-phase="candidate"></html>'
    def fake_invoke(gate, report_path, timeout, url, **kwargs):
        folder=report_path.parent / 'live-wallet-msk-moment-request-start-ABCDEF'
        folder.mkdir()
        (folder/'desktop.png').write_bytes(b'PNG-TEST')
        (folder/'metamask-cdp-notification-1.png').write_bytes(b'PNG-TEST')
        report_path.write_text(json.dumps({
            'schema':'mcel.worker.native-live-evidence.v1','gate':'wallet-msk',
            'surface':'native-mcel','network':'testnet','ok':False,'cleanupOk':True,
            'proofs':{},'stage':'MSK_SIGNING_APPROVAL','errorClass':'Error',
            'mskMoments':[{'version':'msk-moment-screens-v1','phase':'request-start','captures':[
                {'surface':'desktop','status':'saved',
                 'file':'live-wallet-msk-moment-request-start-ABCDEF/desktop.png'},
                {'surface':'metamask-cdp-notification','status':'saved',
                 'file':'live-wallet-msk-moment-request-start-ABCDEF/metamask-cdp-notification-1.png'},
                {'surface':'worker','status':'saved','file':'../outside/secret.png'},
            ]}],
        }),encoding='utf8')
        return 1,'exited-1'
    monkeypatch.setattr(live,'urlopen',lambda *args,**kwargs:FakeResponse())
    monkeypatch.setattr(live,'invoke_node',fake_invoke)
    assert live.main(['--action','run','--gate','wallet-msk',
                      '--authorize-wallet-signing','--report',str(output)])==1
    printed=capsys.readouterr().out
    assert 'WORKER_LIVE_MSK_MOMENT_SCREENSHOT phase=request-start surface=desktop status=saved path=' in printed
    assert 'WORKER_LIVE_MSK_MOMENT_SCREENSHOT phase=request-start surface=metamask-cdp-notification status=saved path=' in printed
    assert 'WORKER_LIVE_MSK_MOMENT_SCREENSHOT phase=request-start surface=worker status=unavailable' in printed
    assert '../outside' not in printed


def test_live_revocation_failure_reports_distinct_http_and_ui_evidence(tmp_path, monkeypatch, capsys):
    output = tmp_path / 'report.json'

    class FakeResponse:
        status = 200
        def __enter__(self): return self
        def __exit__(self, *_): return False
        def read(self, *_): return b'<html data-mcel-worker-phase="candidate"></html>'

    def fake_invoke(gate, report_path, timeout, url, **kwargs):
        report_path.write_text(json.dumps({
            'schema':'mcel.worker.native-live-evidence.v1','gate':'wallet-msk',
            'surface':'native-mcel','network':'testnet','ok':False,'cleanupOk':True,
            'proofs':{},'stage':'MSK_REVOCATION_HTTP_VERIFY','errorClass':'Error',
            'mskSigningDiagnosticVersion':'msk-signing-v1',
            'mskSigning':{'clicked':True,'approved':True,'hubVerified':True,
                          'apiStatus':200,'events':['request.start','signature.created',
                                    'hub.response','revoke.start','revoke.signature.created','revoke.failed']},
            'mskRevocation':{'version':'msk-revocation-v1','click':True,'approval':True,
                 'requestSeen':True,'requestTransportFailed':False,'httpStatus':400,
                 'failure':'http-rejected','rejectionCategory':'hub-cluster-mismatch',
                 'uiState':{'keyActive':True,'keyInactive':False,'workerOutcome':'worker-revoke-failed'}}
        }),encoding='utf-8')
        return 1,'exited-1'

    monkeypatch.setattr(live,'urlopen',lambda *a,**k:FakeResponse())
    monkeypatch.setattr(live,'invoke_node',fake_invoke)
    assert live.main(['--action','run','--gate','wallet-msk','--authorize-wallet-signing',
                      '--report',str(output)])==1
    printed=capsys.readouterr().out
    assert 'WORKER_LIVE_MSK_REVOCATION stage=MSK_REVOCATION_HTTP_VERIFY cause=http-rejected click=yes approved=yes request-seen=yes http=400' in printed
    assert 'api-rejection=hub-cluster-mismatch' in printed
    assert 'WORKER_LIVE_MSK_REVOCATION_UI key-active=yes key-inactive=no worker-outcome=worker-revoke-failed' in printed
    assert 'WORKER_LIVE_MSK_REVOCATION_EVENT type=revoke.signature.created' in printed
    assert 'WORKER_LIVE_CUTOVER BLOCKED canonical-route-unchanged' in printed


def test_live_revocation_output_does_not_echo_untrusted_error_text(tmp_path,monkeypatch,capsys):
    output=tmp_path/'report.json'
    class FakeResponse:
        status=200
        def __enter__(self):return self
        def __exit__(self,*_):return False
        def read(self,*_):return b'<html data-mcel-worker-phase="candidate"></html>'
    def fake_invoke(gate,report_path,timeout,url,**kwargs):
        report_path.write_text(json.dumps({
            'schema':'mcel.worker.native-live-evidence.v1','gate':'wallet-msk',
            'surface':'native-mcel','network':'testnet','ok':False,'cleanupOk':True,
            'proofs':{},'stage':'MSK_REVOCATION_RESPONSE_WAIT','errorClass':'Error',
            'mskRevocation':{'version':'msk-revocation-v1','click':True,'approval':True,
                 'requestSeen':False,'httpStatus':None,'failure':'response-missing',
                 'rejectionCategory':'private 0xdeadbeef','uiState':{'workerOutcome':'password=SECRET'}}
        }),encoding='utf-8')
        return 1,'exited-1'
    monkeypatch.setattr(live,'urlopen',lambda *a,**k:FakeResponse())
    monkeypatch.setattr(live,'invoke_node',fake_invoke)
    assert live.main(['--action','run','--gate','wallet-msk',
                      '--authorize-wallet-signing','--report',str(output)])==1
    printed=capsys.readouterr().out
    assert 'cause=response-missing' in printed
    assert 'api-rejection=none' in printed
    assert 'password=SECRET' not in printed
    assert '0xdeadbeef' not in printed


def test_offer_gate_records_explicit_seller_policy_and_live_http_diagnostics():
    script = (live.ROOT / 'tools/worker_browser_acceptance/mcel_live_acceptance.mjs').read_text(encoding='utf-8')
    assert "proof.stage='OFFER_POLICY_PREFLIGHT'" in script
    # Early refusal must precede MSK signing so a disabled seller does not
    # accumulate another active Hub key just from an impossible test.
    assert script.index("if(gate==='offer-registration' && !auth('SETTINGS'))") < script.index("proof.stage='MSK_SIGNING_CLICK'")
    assert "OFFER_SELLER_DISABLED_SETTINGS_AUTH_REQUIRED" in script
    assert "if(!auth('SETTINGS'))throw" in script
    assert "offer.requestSeen=true" in script
    assert "proof.stage='OFFER_HUB_ACCEPTANCE_VERIFY'" in script
    assert "if(result.hubStatus==='failed')" in script
    assert "assessOfferHubAcceptance(s,sync)" in script
    assert "if(offer.syncStatus==='deferred')" in script
    assert "page.off('response',onOfferResponse)" in script
    reporter = (live.ROOT / 'tools/mcel_worker_live_functional_acceptance.py').read_text(encoding='utf-8')
    assert 'WORKER_LIVE_OFFER_REGISTRATION' in reporter
    assert 'WORKER_LIVE_OFFER_DIAGNOSTIC_VERSION_MISMATCH' in reporter
    assert 'seller-enabled-before=' in reporter


def test_authorized_offer_acceptance_temporarily_uses_real_ai_idle_policy_and_restores():
    source = (live.ROOT / 'tools/worker_browser_acceptance/mcel_live_acceptance.mjs').read_text(encoding='utf-8')
    helper = (live.ROOT / 'tools/worker_browser_acceptance/worker_offer_live_policy.mjs').read_text(encoding='utf-8')
    assert "planOfferAcceptancePolicy(sellerBefore,auth('SETTINGS'))" in source
    assert "proof.stage='OFFER_TEMPORARY_AVAILABILITY_POLICY'" in source
    assert 'selectOption(availability.modeForTest)' in source
    assert 'actual.sellerAvailabilityMode===availability.modeForTest' in source
    assert 'restoreSellerPolicy(page,baseline)' in source
    assert 'sellerPolicyReceipt((changedOfferTokens||changedOfferPrice) ? sellerRestoreBaseline' in source
    assert 'offer.restoration.policyAttempted=true' in source
    assert 'offer.restoration.policyVerified=policyRestored' in source
    assert 'offer.restoration.offerAttempted=true' in source
    assert "modeForTest=settingsAuthorized && originalMode==='totally_idle'" in helper
    assert 'work-now' not in helper.lower()
    reporter = (live.ROOT / 'tools/mcel_worker_live_functional_acceptance.py').read_text(encoding='utf-8')
    for field in ['availability-before=','availability-during=','availability-restored=']:
        assert field in reporter


def test_offer_failure_prioritizes_original_policy_restoration_and_diagnoses_hub_auth():
    source = (live.ROOT / 'tools/worker_browser_acceptance/mcel_live_acceptance.mjs').read_text(encoding='utf-8')
    reporter = (live.ROOT / 'tools/mcel_worker_live_functional_acceptance.py').read_text(encoding='utf-8')
    restore_start = source.index('if(changedSeller && sellerBefore){')
    restore_code = source[restore_start:source.index('if(cleanupFailure)throw Error(cleanupFailure)',restore_start)]
    assert 'await restoreSellerPolicy(page,baseline)' in restore_code
    assert 'if(changedOfferTokens||changedOfferPrice)' in restore_code
    assert "cleanupFailure='seller-policy-restore-unverified'" in restore_code
    assert 'hub_registration_error' in source
    for code in ('hub-key-not-active','hub-key-expired','hub-key-wallet-mismatch','hub-auth-rejected-unclassified'):
        assert code in reporter


def test_previous_failure_receipt_auto_restores_before_new_wallet_signing(tmp_path):
    source = (live.ROOT / 'tools/worker_browser_acceptance/mcel_live_acceptance.mjs').read_text(encoding='utf-8')
    prior = tmp_path / 'live-offer-registration-evidence.json'
    prior.write_text(json.dumps({
        'schema':'mcel.worker.native-live-evidence.v1','gate':'offer-registration',
        'surface':'native-mcel','network':'testnet',
        'offerRegistration':{'settingsChanged':True,'availabilityRestored':False,
            'sellerEnabledBefore':True,'availabilityModeBefore':'totally_idle'}
    }),encoding='utf-8')
    assert live.seed_interrupted_seller_policy_recovery(prior) is True
    receipt=tmp_path/'live-worker-offer-policy-recovery.json'
    saved=json.loads(receipt.read_text(encoding='utf-8'))
    assert saved['sellerAvailabilityMode']=='totally_idle'
    assert saved['sellerEnabled'] is True
    assert 'wallet' not in json.dumps(saved).lower()
    assert live.seed_interrupted_seller_policy_recovery(prior) is False
    assert source.index('await recoverInterruptedSellerPolicy(page)') < source.index("proof.stage='EXTENSION_DISCOVERY'")
    assert source.index('writeSellerPolicyReceipt(policyRecoveryPath,gate=') < source.index("proof.stage='OFFER_TEMPORARY_AVAILABILITY_POLICY'")


def test_no_unverified_prior_policy_can_trigger_auto_mutation(tmp_path):
    prior=tmp_path/'live-offer-registration-evidence.json'
    for payload in (
        {'schema':'unrelated'},
        {'schema':'mcel.worker.native-live-evidence.v1','gate':'offer-registration',
         'surface':'native-mcel','network':'testnet','offerRegistration':{
             'settingsChanged':True,'availabilityRestored':True,
             'sellerEnabledBefore':True,'availabilityModeBefore':'totally_idle'}},
        {'schema':'mcel.worker.native-live-evidence.v1','gate':'offer-registration',
         'surface':'native-mcel','network':'testnet','offerRegistration':{
             'settingsChanged':True,'availabilityRestored':False,
             'sellerEnabledBefore':True,'availabilityModeBefore':'unknown'}}
    ):
        prior.write_text(json.dumps(payload),encoding='utf-8')
        assert live.seed_interrupted_seller_policy_recovery(prior) is False
    assert not (tmp_path/'live-worker-offer-policy-recovery.json').exists()


def test_offer_registration_requires_fresh_http_request_before_examining_prior_hub_failure():
    script=(live.ROOT / 'tools/worker_browser_acceptance/mcel_live_acceptance.mjs').read_text(encoding='utf-8')
    pos_confirm=script.index("await clickMcelConfirmed(page,page.locator('button[data-intent=\"intent:register-offer\"]'),'register-offer')")
    pos_request=script.index('await waitForFreshOfferRequest(',pos_confirm)
    pos_hub=script.index("proof.stage='OFFER_HUB_ACCEPTANCE_VERIFY'",pos_request)
    assert pos_confirm < pos_request < pos_hub
    assert "offer.priorHubStatus" in script
    assert "offer.uiOutcome" in script
    reporter=(live.ROOT / 'tools/mcel_worker_live_functional_acceptance.py').read_text(encoding='utf-8')
    assert 'prior-hub-status=' in reporter
    assert 'ui-outcome=' in reporter


def test_offer_policy_reconciles_leaked_ai_idle_to_original_totally_idle():
    harness=(live.ROOT / 'tools/worker_browser_acceptance/mcel_live_acceptance.mjs').read_text(encoding='utf-8')
    reporter=(live.ROOT / 'tools/mcel_worker_live_functional_acceptance.py').read_text(encoding='utf-8')
    assert "process.env.MC_WORKER_TEST_SELLER_ORIGINAL_MODE" in harness
    assert 'baselineCorrection' in harness
    assert 'sellerRestoreBaseline.sellerAvailabilityMode' in harness
    assert 'writeSellerPolicyReceipt(policyRecoveryPath,gate=' in harness
    assert "'MC_WORKER_TEST_SELLER_ORIGINAL_MODE': 'totally_idle'" in reporter
    assert 'availability-baseline=' in reporter
    assert harness.index('writeSellerPolicyReceipt(policyRecoveryPath,gate=') < harness.index("proof.stage='OFFER_CONFIRMATION'")


def test_offer_diagnostic_schema_matches_node_on_normal_and_early_preflight_paths():
    node=(live.ROOT/'tools/worker_browser_acceptance/mcel_live_acceptance.mjs').read_text(encoding='utf-8')
    python=(live.ROOT/'tools/mcel_worker_live_functional_acceptance.py').read_text(encoding='utf-8')
    assert "proof.offerRegistration={version:'offer-registration-v3'" in node
    assert "const offer={version:'offer-registration-v3'" in node
    assert "proof.offerRegistration?.version==='offer-registration-v3'" in node
    assert "offer.get('version') != 'offer-registration-v3'" in python
    assert 'expected=offer-registration-v3' in python


def test_missing_node_evidence_reports_safe_startup_cause(tmp_path,monkeypatch,capsys):
    output=tmp_path/'report.json'
    class Response:
        status=200
        def __enter__(self):return self
        def __exit__(self,*args):return False
        def read(self,*args):return b'<html data-mcel-worker-phase="candidate"></html>'
    def fake_invoke(gate,report_path,timeout,url,**kwargs):
        assert not report_path.exists()
        return 1,'node-module-not-found'
    monkeypatch.setattr(live,'urlopen',lambda *args,**kwargs:Response())
    monkeypatch.setattr(live,'invoke_node',fake_invoke)
    assert live.main(['--action','run','--gate','offer-registration',
      '--authorize-wallet-signing','--authorize-worker-offer','--report',str(output)])==1
    printed=capsys.readouterr().out
    assert 'WORKER_LIVE_GATE offer-registration UNVERIFIED reason=node-module-not-found stage=NOT_AVAILABLE' in printed
    assert 'WORKER_LIVE_NODE_EVIDENCE status=not-written cause=node-module-not-found' in printed
    assert 'WORKER_LIVE_OFFER_DIAGNOSTIC_VERSION_MISMATCH expected=offer-registration-v3' in printed
    assert 'WORKER_LIVE_CUTOVER BLOCKED' in printed


def test_offer_v3_failure_reports_real_offer_state_instead_of_diagnostic_mismatch(tmp_path,monkeypatch,capsys):
    output=tmp_path/'report.json'
    class Response:
        status=200
        def __enter__(self):return self
        def __exit__(self,*args):return False
        def read(self,*args):return b'<html data-mcel-worker-phase="candidate"></html>'
    def fake_invoke(gate,report_path,timeout,url,**kwargs):
        report_path.write_text(json.dumps({'schema':'mcel.worker.native-live-evidence.v1',
          'gate':'offer-registration','surface':'native-mcel','network':'testnet',
          'ok':False,'cleanupOk':True,'stage':'OFFER_SYNC_REQUEST_VERIFY','errorClass':'Error',
          'offerRegistration':{'version':'offer-registration-v3','failure':'offer-sync-request-not-observed',
            'settingsAuthorized':True,'priorHubStatus':'failed','uiOutcome':'not-observed',
            'availabilityModeBaseline':'totally_idle','restoration':{'policyAttempted':True,
              'policyVerified':True,'failedAt':'none'}}}),encoding='utf-8')
        return 1,'exited-1'
    monkeypatch.setattr(live,'urlopen',lambda *args,**kwargs:Response())
    monkeypatch.setattr(live,'invoke_node',fake_invoke)
    assert live.main(['--action','run','--gate','offer-registration',
       '--authorize-wallet-signing','--authorize-worker-offer','--report',str(output)])==1
    printed=capsys.readouterr().out
    assert 'WORKER_LIVE_OFFER_REGISTRATION stage=OFFER_SYNC_REQUEST_VERIFY cause=offer-sync-request-not-observed' in printed
    assert 'prior-hub-status=failed' in printed
    assert 'availability-baseline=totally_idle' in printed
    assert 'WORKER_LIVE_OFFER_RESTORATION policy-attempted=yes policy-verified=yes' in printed
    assert 'WORKER_LIVE_OFFER_DIAGNOSTIC_VERSION_MISMATCH' not in printed


def test_node_startup_diagnostic_classification_never_echoes_untrusted_output():
    assert live.classify_node_startup_failure('Error [ERR_MODULE_NOT_FOUND]: wallet secret abc')=='node-module-not-found'
    assert live.classify_node_startup_failure('SyntaxError: unexpected token')=='node-syntax-error'
    assert live.classify_node_startup_failure("requested module does not provide an export named 'x'")=='node-module-export-mismatch'
    assert live.classify_node_startup_failure('private account mnemonic word123')=='node-exited-without-evidence'


def test_local_import_preflight_detects_missing_direct_helper_without_wallet(tmp_path):
    node=tmp_path/'worker_browser_acceptance';node.mkdir()
    (node/'mcel_live_acceptance.mjs').write_text(
        "import {x} from './worker_offer_request_wait.mjs'\n",encoding='utf-8')
    assert live.missing_node_local_import(node)==(
        'worker_offer_request_wait.mjs','mcel_live_acceptance.mjs')


def test_local_import_preflight_detects_transitive_missing_helper(tmp_path):
    node=tmp_path/'worker_browser_acceptance';node.mkdir()
    (node/'mcel_live_acceptance.mjs').write_text(
        "import {x} from './worker_offer_policy_recovery.mjs'\n",encoding='utf-8')
    (node/'worker_offer_policy_recovery.mjs').write_text(
        "import {y} from './missing_nested.mjs'\n",encoding='utf-8')
    assert live.missing_node_local_import(node)==(
        'missing_nested.mjs','worker_offer_policy_recovery.mjs')


def test_local_import_preflight_passes_real_harness():
    node=live.ROOT/'tools'/'worker_browser_acceptance'
    assert live.missing_node_local_import(node) is None


def test_safe_node_missing_module_prints_only_basename():
    good = "Error [ERR_MODULE_NOT_FOUND]: Cannot find module 'C:/repo/tools/worker_browser_acceptance/worker_offer_request_wait.mjs' imported from C:/private"
    assert live.safe_node_missing_module(good)=='worker_offer_request_wait.mjs'
    assert live.safe_node_missing_module("Error: Cannot find package '@playwright/test' imported from C:/repo")=='@playwright/test'
    assert live.safe_node_missing_module("Cannot find module 'C:/Users/private-wallet/seed-phrase.txt'")=='unidentified'
    assert live.safe_node_missing_module('ERR_MODULE_NOT_FOUND')=='unidentified'


def test_invoke_node_refuses_missing_helper_before_wallet_provision(tmp_path,monkeypatch,capsys):
    node=tmp_path/'tools'/'worker_browser_acceptance';node.mkdir(parents=True)
    (node/'node_modules').mkdir()
    (node/'mcel_live_acceptance.mjs').write_text(
        "import './worker_offer_request_wait.mjs'\n", encoding='utf-8')
    monkeypatch.setattr(live,'ROOT',tmp_path)
    monkeypatch.setattr(live.legacy.shutil,'which',lambda name:'/usr/bin/node')
    # If this preflight works the test must never reach extension discovery or
    # wallet provision, regardless of whether either is installed.
    code,reason=live.invoke_node('offer-registration',tmp_path/'evidence.json',10,
        live.DEFAULT_URL,authorize_signing=True,authorize_offer=True,
        authorize_spend=False,authorize_settings=True,fresh_disposable_wallet=True)
    assert code is None and reason=='node-local-module-missing'
    text=capsys.readouterr().out
    assert 'WORKER_LIVE_NODE_IMPORT_PREFLIGHT status=missing' in text
    assert 'module=worker_offer_request_wait.mjs' in text


def test_offer_registration_waits_for_matching_response_not_previous_hub_failure():
    node=(live.ROOT/'tools/worker_browser_acceptance/mcel_live_acceptance.mjs').read_text(encoding='utf-8')
    reporter=(live.ROOT/'tools/mcel_worker_live_functional_acceptance.py').read_text(encoding='utf-8')
    pos_arm=node.index('offerRequests.arm()')
    pos_click=node.index("await clickMcelConfirmed(page,page.locator('button[data-intent=\"intent:register-offer\"]'),'register-offer')", pos_arm)
    pos_request=node.index('await waitForFreshOfferRequest(', pos_click)
    pos_response=node.index('await waitForFreshOfferResponse(', pos_request)
    pos_hub=node.index("proof.stage='OFFER_HUB_ACCEPTANCE_VERIFY'",pos_response)
    assert pos_arm < pos_click < pos_request < pos_response < pos_hub
    assert 'if(offerRequests.observeResponse(response))' in node
    assert 'if(offerRequests.observeFailure(request))' in node
    assert 'response-seen=' in reporter
    assert "'offer-sync-response-not-observed'" in reporter


def test_captain_preflight_is_reported_without_subprocess_secrets():
    script=(live.ROOT/'tools/worker_browser_acceptance/mcel_live_acceptance.mjs').read_text(encoding='utf-8')
    # Funding check must run before the temporary Work Now override.
    assert script.index("proof.stage='CAPTAIN_FUNDING_PREFLIGHT'") < script.index("proof.stage='WORK_NOW_START'")
    assert 'proof.proofs.hubOfferAccepted===true' in script
    assert 'captainFundingPreflightEvidence' in script
    assert 'proof.captainFundingPreflight=funding' in script
    assert 'if(!funding.verified)' in script
    report=(live.ROOT/'tools/mcel_worker_live_functional_acceptance.py').read_text(encoding='utf-8')
    assert 'WORKER_LIVE_CAPTAIN_FUNDING' in report
    assert "funding.get('reason') in allowed_reasons" in report
    assert 'stderr-shape=' in report


def test_work_cycle_applies_existing_idle_policy_before_low_credit_offer_price():
    source = (live.ROOT / 'tools/worker_browser_acceptance/mcel_live_acceptance.mjs').read_text(encoding='utf-8')
    start = source.index("proof.stage='OFFER_POLICY_PREFLIGHT'")
    policy = source.index("proof.stage='OFFER_TEMPORARY_AVAILABILITY_POLICY'", start)
    price = source.index("proof.stage='OFFER_TEMPORARY_OFFER_PRICE'", start)
    registration = source.index("proof.stage='OFFER_CONFIRMATION'", start)
    assert policy < price < registration
    assert "snapshotOfferMoment('offer-price-failed')" in source
    assert "actual.sellerAvailabilityMode===availability.modeForTest" in source


def test_policy_transition_report_preserves_safe_action_and_error_categories():
    reporter=(live.ROOT / 'tools/mcel_worker_live_functional_acceptance.py').read_text(encoding='utf-8')
    assert 'WORKER_LIVE_OFFER_POLICY_TRANSITION' in reporter
    assert "'last-action=' + action" in reporter
    assert "'error=' + failure" in reporter
    assert "'policy-before','policy-failed','offer-price-failed'" in reporter


def test_successful_work_cycle_reports_funding_and_returns_zero(tmp_path, monkeypatch, capsys):
    """A real PASS skips failed-offer diagnostics but must print funding safely."""
    output = tmp_path / 'work-cycle-report.json'

    class FakeResponse:
        status = 200
        def __enter__(self):
            return self
        def __exit__(self, *_):
            return False
        def read(self, *_):
            return b'<html data-mcel-worker-phase="candidate"></html>'

    def fake_invoke(gate, path, timeout, url, **kwargs):
        assert gate == 'work-cycle'
        path.write_text(json.dumps({
            'schema': 'mcel.worker.native-live-evidence.v1',
            'surface': 'native-mcel', 'network': 'testnet', 'gate': gate,
            'stage': 'workerDelivered', 'ok': True, 'cleanupOk': True,
            'verifiedAt': datetime.now(timezone.utc).isoformat(),
            'proofs': {name: True for name in (
                'realExtension', 'testnetChain', 'hubMskVerified',
                'hubOfferAccepted', 'hubSessionLive', 'captainCompleted',
                'workerDelivered')},
            'captainFundingPreflight': {
                'version': 'captain-funding-preflight-v1',
                'verified': True, 'reason': 'none',
                'verdict': 'existing_credits_sufficient', 'processExit': 0,
                'requiredCreditWei': '5120000000000000',
                'availableCreditWei': '25000000000000000',
                'bridgeCompletedCreditWei': '3073000000000000000',
                'possibleTopUpCreditWei': '0',
                'bridgeFundingRequired': False, 'stderrKind': 'empty'
            }
        }), encoding='utf-8')
        return 0, 'exited-0'

    monkeypatch.setattr(live, 'urlopen', lambda *a, **k: FakeResponse())
    monkeypatch.setattr(live, 'invoke_node', fake_invoke)
    assert live.main(['--action', 'run', '--gate', 'work-cycle',
                      '--authorize-wallet-signing', '--authorize-worker-offer',
                      '--authorize-captain-spend', '--authorize-settings-change',
                      '--report', str(output)]) == 0
    printed = capsys.readouterr().out
    assert 'WORKER_LIVE_GATE work-cycle PASS' in printed
    assert 'WORKER_LIVE_CAPTAIN_FUNDING verified=yes' in printed
    assert 'bridge-needed=no' in printed
    assert 'WORKER_LIVE_CUTOVER BLOCKED canonical-route-unchanged' in printed
    assert json.loads(output.read_text(encoding='utf-8'))['gates']['work-cycle']['status'] == 'PASS'


def test_work_cycle_failed_funding_without_offer_diagnostic_is_reported(tmp_path, monkeypatch, capsys):
    output = tmp_path / 'work-cycle-failed-report.json'

    class FakeResponse:
        status = 200
        def __enter__(self):
            return self
        def __exit__(self, *_):
            return False
        def read(self, *_):
            return b'<html data-mcel-worker-phase="candidate"></html>'

    def fake_invoke(gate, path, timeout, url, **kwargs):
        path.write_text(json.dumps({
            'schema': 'mcel.worker.native-live-evidence.v1',
            'surface': 'native-mcel', 'network': 'testnet', 'gate': gate,
            'stage': 'CAPTAIN_FUNDING_PREFLIGHT', 'ok': False, 'cleanupOk': True,
            'proofs': {},
            'captainFundingPreflight': {
                'version': 'captain-funding-preflight-v1',
                'verified': False, 'reason': 'testnet_ledger_unavailable',
                'verdict': 'unknown', 'processExit': 1,
                'requiredCreditWei': '5120000000000000',
                'availableCreditWei': '0',
                'bridgeCompletedCreditWei': '0',
                'possibleTopUpCreditWei': '0',
                'bridgeFundingRequired': False, 'stderrKind': 'empty'
            }
        }), encoding='utf-8')
        return 1, 'exited-1'

    monkeypatch.setattr(live, 'urlopen', lambda *a, **k: FakeResponse())
    monkeypatch.setattr(live, 'invoke_node', fake_invoke)
    monkeypatch.setattr(live, 'build_failure_bundle', lambda **kwargs: None)
    assert live.main(['--action', 'run', '--gate', 'work-cycle',
                      '--authorize-wallet-signing', '--authorize-worker-offer',
                      '--authorize-captain-spend', '--authorize-settings-change',
                      '--report', str(output)]) == 1
    printed = capsys.readouterr().out
    assert 'WORKER_LIVE_GATE work-cycle UNVERIFIED' in printed
    assert 'WORKER_LIVE_CAPTAIN_FUNDING verified=no' in printed
    assert 'bridge-needed=no' in printed
    assert 'WORKER_LIVE_CUTOVER BLOCKED canonical-route-unchanged' in printed
