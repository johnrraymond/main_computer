"""Patch 17B: executable package contracts and isolated real-file effects.

Chromium is a separate, mandatory gate; these tests never assert browser truth.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from main_computer.mcel_portable_workspace import PortableWorkspaceBroker
from tools.mcel_portable_intent_probe import seed_reference_file, finalize_evidence
from main_computer.mcel_standalone_package import build_standalone_package
from tools.mcel_standalone_package_probe import _check_assets

ROOT=Path(__file__).resolve().parents[1]


def test_registry_has_all_nineteen_compiled_intents_and_package_owned_pure_cores(tmp_path):
    total=0
    for app in ('calculator','code-editor'):
        manifest=build_standalone_package(ROOT,app,tmp_path,portable=True)
        assert manifest['portableMode'] is True
        assert manifest['behavioralParityProven'] is False
        assert manifest['fullSemanticRuntimeProven'] is False
        assert manifest['missingPortableIntents']==[]
        assert set(manifest['portableImplementations'])==set(manifest['declaredIntents'])
        assert set(manifest['portableImplementations'].values())=={'local-pure-core','authorized-capability'}
        assert manifest['sourceHostAssetsIncluded'] is False
        assert _check_assets(tmp_path/app,manifest)==[]
        assert all('/applications/' not in str(x) for x in manifest['assetDigests'])
        files={p.relative_to(tmp_path/app).as_posix() for p in (tmp_path/app).rglob('*') if p.is_file()}
        assert files=={'index.html','presentation.json','standalone.css','standalone.js',
                       'contracts/layout.js','contracts/surface.js','contracts/intents.js','portable.js',
                       'calculator-core.js' if app=='calculator' else 'code-editor-core.js'}
        total+=len(manifest['declaredIntents'])
    assert total==19


@pytest.mark.parametrize('app',('calculator','code-editor'))
def test_pure_core_dispatch_and_capability_wiring_in_node(tmp_path,app):
    if not shutil.which('node'):pytest.skip('Node required')
    build_standalone_package(ROOT,app,tmp_path,portable=True)
    cmd=['node',str(ROOT/'tests/mcel_portable_domain_node_smoke.cjs'),str(tmp_path),app]
    result=subprocess.run(cmd,capture_output=True,text=True,timeout=30)
    assert result.returncode==0,result.stderr
    parsed=json.loads(result.stdout)
    assert parsed['declaredIntents']==(11 if app=='calculator' else 8)
    assert parsed['fullSemanticRuntimeProven'] is False


def test_real_write_with_fresh_hash_and_stale_source_refusal(tmp_path):
    path=tmp_path/'example.txt';path.write_bytes(b'before\n')
    broker=PortableWorkspaceBroker(tmp_path)
    opened=broker.request('openFile',{'path':'example.txt'})
    assert opened['ok'] and opened['content']=='before\n'
    base={'path':'example.txt','replacement_text':'after\n',
          'expected_before_sha256':opened['sourceHash'],'explicit_save':True,'stale_source_checked':True}
    assert not broker.request('saveFile',{**base,'explicit_save':False})['ok']
    assert path.read_bytes()==b'before\n'
    saved=broker.request('saveFile',base)
    assert saved['ok'] and saved['changed'] is True
    assert path.read_bytes()==b'after\n'
    assert saved['sourceHash']==hashlib.sha256(b'after\n').hexdigest()
    assert not broker.request('saveFile',base)['ok']
    assert len(broker.effects)==1


def test_reviewed_preview_does_not_write_and_approval_is_consumable(tmp_path):
    path=tmp_path/'example.txt';path.write_bytes(b'before\n')
    broker=PortableWorkspaceBroker(tmp_path)
    base=hashlib.sha256(path.read_bytes()).hexdigest()
    preview=broker.request('previewAiderPlan',{'preview_only':True,'mutation_allowed':False,
                  'changes':[{'operation':'modify','path':'example.txt','expected_before_sha256':base,
                              'replacement_text':'approved\n'}]})
    assert preview['ok'] and not broker.effects and path.read_bytes()==b'before\n'
    denied=broker.request('applyReviewedPatch',{'handle':preview['handle'],'reviewed':True,'approved':False})
    assert not denied['ok'] and path.read_bytes()==b'before\n'
    applied=broker.request('applyReviewedPatch',{'handle':preview['handle'],'reviewed':True,'approved':True})
    assert applied['ok'] and applied['state']=='applied'
    assert path.read_bytes()==b'approved\n'
    assert len(broker.effects)==1
    assert not broker.request('applyReviewedPatch',{'handle':preview['handle'],'reviewed':True,'approved':True})['ok']


def test_preview_stale_after_external_edit_does_not_write(tmp_path):
    path=tmp_path/'example.txt';path.write_bytes(b'before\n')
    broker=PortableWorkspaceBroker(tmp_path)
    expected=hashlib.sha256(path.read_bytes()).hexdigest()
    preview=broker.request('previewAiderPlan',{'preview_only':True,'mutation_allowed':False,
        'changes':[{'operation':'modify','path':'example.txt','expected_before_sha256':expected,
                    'replacement_text':'approved\n'}]})
    path.write_bytes(b'external edit\n')
    failed=broker.request('applyReviewedPatch',{'handle':preview['handle'],'reviewed':True,'approved':True})
    assert not failed['ok'] and path.read_bytes()==b'external edit\n' and not broker.effects
    assert not broker.request('applyReviewedPatch',{'handle':preview['handle'],'reviewed':True,'approved':True})['ok']


@pytest.mark.parametrize('path',['../secret.txt','/etc/passwd','C:/sensitive.txt','a/../../outside.txt','a\\b.txt'])
def test_workspace_rejects_path_escape(tmp_path,path):
    broker=PortableWorkspaceBroker(tmp_path)
    assert not broker.request('openFile',{'path':path})['ok']


def test_workspace_rejects_symlink_escape(tmp_path):
    outer=tmp_path.parent/'real-outside.txt';outer.write_text('secret')
    try:(tmp_path/'escape.txt').symlink_to(outer)
    except (OSError,NotImplementedError):pytest.skip('symlinks unavailable')
    broker=PortableWorkspaceBroker(tmp_path)
    assert not broker.request('openFile',{'path':'escape.txt'})['ok']
    outer.unlink(missing_ok=True)


def test_external_domain_capabilities_refuse_without_provider_and_verify_provider_claim(tmp_path):
    broker=PortableWorkspaceBroker(tmp_path)
    for n in ['askModelForExpression','askModelForGraphExpression','askModelForMathicsExpression',
              'evaluateMathics','askResultQuestion']:
        assert not broker.request(n,{})['ok']
    invalid=PortableWorkspaceBroker(tmp_path,external={'evaluateMathics':lambda _: {'text':'2'}})
    assert not invalid.request('evaluateMathics',{})['ok']
    sample=PortableWorkspaceBroker(tmp_path,external={'evaluateMathics':lambda _:
        {'text':'2','provider':'explicit-test-provider','evidence':{'test':True}}})
    assert sample.request('evaluateMathics',{})['ok']

@pytest.mark.parametrize('app',('calculator','code-editor'))
def test_generated_portable_host_wires_domain_dispatch_in_node(tmp_path,app):
    if not shutil.which('node'):pytest.skip('Node required')
    build_standalone_package(ROOT,app,tmp_path,portable=True)
    result=subprocess.run(['node',str(ROOT/'tests/mcel_standalone_portable_host_node_smoke.cjs'),
                           str(tmp_path),app,'1140' if app=='calculator' else '1220'],
                          capture_output=True,text=True,timeout=30)
    assert result.returncode==0,result.stderr
    proof=json.loads(result.stdout)
    assert proof['portableMode'] is True
    assert proof['ready'] is True
    assert proof['fullSemanticRuntimeProven'] is False
    assert proof['disabledIntents']==5  # only externally authorized capabilities are disabled


def test_browser_reference_fixture_has_exact_lf_bytes(tmp_path):
    # Windows text-mode write_text() translates LF to CRLF. The portable read
    # assertion is byte-sensitive, so this must not depend on the host OS.
    original=seed_reference_file(tmp_path)
    assert original.read_bytes()==b'original\n'
    assert PortableWorkspaceBroker(tmp_path).request('openFile',{'path':'example.txt'})['content']=='original\n'


def test_open_file_preserves_crlf_bytes_and_matching_hash(tmp_path):
    # The broker must not normalize CRLF; sourceHash covers exactly these bytes.
    original=b'before\r\n'
    (tmp_path/'example.txt').write_bytes(original)
    opened=PortableWorkspaceBroker(tmp_path).request('openFile',{'path':'example.txt'})
    assert opened['ok'] and opened['content']=='before\r\n'
    assert opened['sourceHash']==hashlib.sha256(original).hexdigest()
    assert (tmp_path/'example.txt').read_bytes()==original


def _successful_probe_evidence():
    """Synthetic verdict inputs, NOT a substitute for the Chromium proof."""
    return {
        'schema':'mcel.portable-intent-execution.v1',
        'passed':False,'fullSemanticRuntimeProven':False,
        'applications':[
            {'appId':'calculator','status':'pass','ready':True,'portableMode':True,
             'executed':['clearExpression','enterToken','evaluateExpression',
                         'switchMode','drawGraph','resetGraph'],
             'refusals':['askModelForExpression','askModelForGraphExpression',
                         'askModelForMathicsExpression','evaluateMathics','askResultQuestion'],
             'safetyRefusals':[]},
            {'appId':'code-editor','status':'pass','ready':True,'portableMode':True,
             'executed':['inspectWorkspace','openFile','editDraft','saveFile',
                         'editDraft','discardDraft','editDraft','previewAiderPlan',
                         'applyReviewedPatch','closeFile'],
             'refusals':[], 'safetyRefusals':['applyReviewedPatch'],
             'sourceTextAfterProof':'reviewed\n',
             'filesystemEffects':[{'intent':'saveFile'}, {'intent':'applyReviewedPatch'}]}],
        'missingExternalProviderProof':['askModelForExpression','askModelForGraphExpression',
                                        'askModelForMathicsExpression','evaluateMathics','askResultQuestion']
    }


def test_verdict_counts_unique_intents_not_repeated_editor_calls():
    evidence=_successful_probe_evidence()
    finalize_evidence(evidence)
    assert evidence['status']=='partial-pass'
    assert evidence['portableLocalAndWorkspacePassed'] is True
    assert evidence['passed'] is False and evidence['fullSemanticRuntimeProven'] is False
    editor=evidence['applications'][1]
    assert editor['successfulCallCount']==10
    assert editor['distinctExecutedIntents']==8
    assert editor['safetyRefusals']==['applyReviewedPatch']
    assert editor['refusals']==[]
    assert len(editor['filesystemEffects'])==2


@pytest.mark.parametrize('tamper', ('missing-local-intent','unexpected-external-refusal',
                                    'missing-safety-refusal','missing-file-effect',
                                    'missing-external-capability-refusal','browser-failed'))
def test_portable_verdict_refuses_incomplete_or_misclassified_proof(tamper):
    evidence=_successful_probe_evidence()
    calc,editor=evidence['applications']
    if tamper=='missing-local-intent':
        editor['executed'].remove('saveFile')
    elif tamper=='unexpected-external-refusal':
        editor['refusals'].append('inspectWorkspace')
    elif tamper=='missing-safety-refusal':
        editor['safetyRefusals'].clear()
    elif tamper=='missing-file-effect':
        editor['filesystemEffects'].pop()
    elif tamper=='missing-external-capability-refusal':
        calc['refusals'].remove('askModelForExpression')
    elif tamper=='browser-failed':
        editor['status']='refused'
    finalize_evidence(evidence)
    assert evidence['status']=='refused'
    assert evidence['portableLocalAndWorkspacePassed'] is False
    assert evidence['passed'] is False and evidence['fullSemanticRuntimeProven'] is False


def test_source_drift_diagnostics_are_bounded_and_never_hide_changes(tmp_path):
    from tools.mcel_portable_intent_probe import source_drift_diagnostics
    from main_computer.mcel_evidence_provenance import build_repository_provenance
    first = tmp_path / 'main_computer' / 'old.py'
    first.parent.mkdir(parents=True)
    first.write_text('original\n', encoding='utf-8')
    removed = tmp_path / 'tests' / 'test_mcel_removed.py'
    removed.parent.mkdir()
    removed.write_text('pass\n', encoding='utf-8')
    before = build_repository_provenance(tmp_path, include_file_digests=True)
    first.write_text('edited\n', encoding='utf-8')
    removed.unlink()
    (first.parent / 'new.py').write_text('new\n', encoding='utf-8')
    after = build_repository_provenance(tmp_path, include_file_digests=True)
    drift = source_drift_diagnostics(before, after)
    assert drift['status'] == 'source-modified-during-proof'
    assert drift['previousFingerprint'] != drift['currentFingerprint']
    assert drift['fileChanges']['modified']['paths'] == ['main_computer/old.py']
    assert drift['fileChanges']['removed']['paths'] == ['tests/test_mcel_removed.py']
    assert drift['fileChanges']['added']['paths'] == ['main_computer/new.py']
    assert drift['provenanceMetadataChanged'] is False
    shortened = source_drift_diagnostics(before, after, max_paths=0)
    assert shortened['fileChanges']['modified']['count'] == 1
    assert shortened['fileChanges']['modified']['paths'] == []
    assert shortened['fileChanges']['modified']['additionalPathsOmitted'] == 1
