"""Failure ZIPs must be useful, isolated to the invocation, and local-only."""
from __future__ import annotations

import json
from pathlib import Path
from zipfile import ZipFile

from tools.worker_live_failure_bundle import build_failure_bundle


PNG = b'\x89PNG\r\n\x1a\n' + b'P' * 180


def _image(root: Path, relative: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(PNG)


def _example(tmp_path):
    work = tmp_path / 'reports'
    work.mkdir()
    report = work / 'report.json'
    report.write_text(json.dumps({'schema': 'mcel.worker.live-functional.v1',
                                  'status':'INCOMPLETE'}), encoding='utf-8')
    evidence_path = work / 'live-work-cycle-evidence.json'
    before = 'live-work-cycle-moment-policy-before-aBc123/worker.png'
    failed = 'live-work-cycle-moment-policy-failed-Def789/desktop.png'
    final = 'live-work-cycle-failure-xyz789/worker.png'
    previous = 'live-work-cycle-failure-OLD999/worker.png'
    for file in (before,failed,final,previous):
        _image(work,file)
    evidence = {'schema':'mcel.worker.native-live-evidence.v1',
        'gate':'work-cycle','stage':'OFFER_TEMPORARY_AVAILABILITY_POLICY',
        'offerRegistration':{'availabilityModeBaseline':'totally_idle','availabilityRestored':True,
            'policyTransition':{'version':'offer-policy-transition-v1',
                                'lastAction':'apply-policy', 'errorKind':'save-postcondition-timeout'}},
        'offerMoments':[
            {'phase':'policy-before','captures':[{'file':before,'status':'saved'}]},
            {'phase':'policy-failed','captures':[{'file':failed,'status':'saved'}]},
            {'phase':'policy-failed','captures':[{'file':'../sneaky.png','status':'saved'}]}],
        'failureScreenshots':{'captures':[{'file':final,'status':'saved'}]}}
    evidence_path.write_text(json.dumps(evidence),encoding='utf-8')
    return evidence_path,report,evidence,previous


def test_bundle_contains_only_current_run_evidence_and_moment_pngs(tmp_path):
    path,report,evidence,previous=_example(tmp_path)
    output=build_failure_bundle(gate='work-cycle', evidence_path=path,
        report_path=report,evidence=evidence,reason='live-operation-failed',process='exited-1')
    assert output['screenshots']==3
    assert output['missing']==0
    with ZipFile(output['path']) as z:
        names=set(z.namelist())
        assert 'manifest.json' in names
        assert 'evidence/live-gate-evidence.json' in names
        assert 'evidence/live-run-report.json' in names
        assert 'evidence/failure-timeline.json' in names
        assert 'README.txt' in names
        assert len([name for name in names if name.startswith('screenshots/')])==3
        assert not any(previous in name for name in names)
        assert not any('sneaky' in name for name in names)
        manifest=json.loads(z.read('manifest.json'))
        assert manifest['status']=='UNVERIFIED'
        assert manifest['stage']=='OFFER_TEMPORARY_AVAILABILITY_POLICY'
        assert manifest['policy']['restorationVerified']=='yes'
        assert manifest['policy']['expectedBaseline']=='totally_idle'
        assert all(item['sha256'] for item in manifest['files'])
        timeline=json.loads(z.read('evidence/failure-timeline.json'))
        assert timeline['policyTransition']['lastAction']=='apply-policy'
        assert timeline['policyTransition']['errorKind']=='save-postcondition-timeout'


def test_distinct_failed_runs_do_not_overwrite_archives_or_reuse_old_evidence(tmp_path):
    path,report,evidence,_=_example(tmp_path)
    first=build_failure_bundle(gate='work-cycle', evidence_path=path,
        report_path=report,evidence=evidence,reason='live-operation-failed',process='exited-1')
    second=build_failure_bundle(gate='work-cycle', evidence_path=path,
        report_path=report,evidence=evidence,reason='live-operation-failed',process='exited-1')
    assert first['path'] != second['path']
    assert Path(first['path']).is_file() and Path(second['path']).is_file()


def test_node_early_failure_still_creates_useful_bundle(tmp_path):
    work=tmp_path / 'reports'
    work.mkdir()
    missing=work / 'live-offer-registration-evidence.json'
    output=build_failure_bundle(gate='offer-registration',evidence_path=missing,
        report_path=work/'report.json',evidence=None,
        reason='node-local-module-missing',process='node-local-module-missing')
    assert output['screenshots']==0
    assert output['missing']>=2
    with ZipFile(output['path']) as z:
        manifest=json.loads(z.read('manifest.json'))
        assert manifest['status']=='UNVERIFIED'
        assert manifest['reason']=='node-local-module-missing'
        assert 'evidence/failure-timeline.json' in z.namelist()


def test_never_include_private_wallet_files_or_other_unreferenced_assets(tmp_path):
    path,report,evidence,_=_example(tmp_path)
    (path.parent/'wallet.json').write_text('SEED_PHRASE_DO_NOT_INCLUDE',encoding='utf-8')
    (path.parent/'stderr.log').write_text('PRIVATE_ERROR',encoding='utf-8')
    output=build_failure_bundle(gate='work-cycle',evidence_path=path,
        report_path=report,evidence=evidence,reason='secret text',process='exited-1')
    with ZipFile(output['path']) as z:
        content=b''.join(z.read(name) for name in z.namelist())
        assert b'SEED_PHRASE_DO_NOT_INCLUDE' not in content
        assert b'PRIVATE_ERROR' not in content
        assert b'secret text' not in content


def test_cli_packages_existing_evidence_without_running_worker(tmp_path,capsys):
    from tools.worker_live_failure_bundle import main
    path,report,evidence,_=_example(tmp_path)
    assert main(['--gate','work-cycle','--evidence',str(path),'--report',str(report)])==0
    printed=capsys.readouterr().out
    assert 'WORKER_LIVE_FAILURE_BUNDLE status=saved' in printed
    assert 'screenshots=3' in printed


def test_runner_automatically_packages_failed_attempt(tmp_path,monkeypatch,capsys):
    from tools import mcel_worker_live_functional_acceptance as live
    class Response:
        status=200
        def __enter__(self): return self
        def __exit__(self,*args): return False
        def read(self,*args): return b'<html data-mcel-worker-phase="candidate"></html>'
    def fail_gate(gate, evidence_path, timeout, url, **kwargs):
        evidence_path.write_text(json.dumps({
            'schema':'mcel.worker.native-live-evidence.v1','gate':gate,
            'surface':'native-mcel','network':'testnet',
            'stage':'OFFER_TEMPORARY_AVAILABILITY_POLICY',
            'ok':False,'cleanupOk':True,'proofs':{},
            'offerRegistration':{'version':'offer-registration-v3','failure':'offer-operation-unclassified',
              'policyTransition':{'version':'offer-policy-transition-v1',
                'lastAction':'apply-policy','errorKind':'save-postcondition-timeout'}}
        }),encoding='utf-8')
        return 1,'exited-1'
    monkeypatch.setattr(live,'urlopen',lambda *a,**k:Response())
    monkeypatch.setattr(live,'invoke_node',fail_gate)
    target=tmp_path/'report.json'
    rc=live.main(['--action','run','--gate','work-cycle','--authorize-wallet-signing',
                  '--authorize-worker-offer','--authorize-captain-spend',
                  '--authorize-settings-change','--report',str(target)])
    assert rc!=0
    printed=capsys.readouterr().out
    assert 'WORKER_LIVE_GATE work-cycle UNVERIFIED' in printed
    assert 'WORKER_LIVE_FAILURE_BUNDLE status=saved' in printed
    assert 'WORKER_LIVE_OFFER_POLICY_TRANSITION action=apply-policy' in printed
    assert list((tmp_path/'failure-bundles').glob('*.zip'))
    assert json.loads(target.read_text())['cutover']['status']=='BLOCKED'


def test_bundle_redacts_accidental_secret_fields_from_evidence_json(tmp_path):
    path,report,evidence,_=_example(tmp_path)
    evidence['walletPassword']='DO_NOT_LEAK_PASSWORD'
    evidence['walletMnemonic']='DO_NOT_LEAK_MNEMONIC'
    evidence['address']='0x'+'ab'*20
    output=build_failure_bundle(gate='work-cycle', evidence_path=path,
        report_path=report,evidence=evidence,reason='live-operation-failed',process='exited-1')
    with ZipFile(output['path']) as z:
        receipt=z.read('evidence/live-gate-evidence.json')
        assert b'DO_NOT_LEAK' not in receipt
        assert b'0xabababab' not in receipt
        assert b'[redacted]' in receipt
