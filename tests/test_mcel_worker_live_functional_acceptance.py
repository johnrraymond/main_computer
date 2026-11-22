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
             'backendDeniedUnauthorizedSpend','noPaidSubmission','budgetLimitDenied'))}
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
