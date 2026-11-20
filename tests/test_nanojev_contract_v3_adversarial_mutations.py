"""Offline controls for EVM mutation harness (these are NOT EVM runs)."""
from __future__ import annotations

import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from tools import nanojev_contract_v3_independent_behavior as gate
from tools import nanojev_contract_v3_independent_forge_gold as forge_gold
from tools import nanojev_contract_v3_adversarial_mutations as campaign
from tools import nanojev_contract_v3_semantic_adjudication as review
from tools import nanojev_contract_v3_evidence_freeze as freeze

ROOT=Path(__file__).resolve().parents[1]
REPORT=ROOT/'runtime/reports/nanojev-contract-v3-governance-heldout-forge-0001.json'
GOLD=ROOT/'runtime/reports/nanojev-contract-v3-independent-gold.v1.json'

@pytest.fixture
def artifacts():
    if not REPORT.exists() or not GOLD.exists():pytest.skip('V3 source report/gold not installed')
    return gate.read_json(REPORT),gate.read_json(GOLD)


def baseline_for_tests(report,gold):
    # Test-only fixture. It does not serve as a real certificate and is never
    # placed into the production repo's runtime/reports path.
    report_cases={c['id']:c for c in report['cases'] if c['expect']=='accept'}
    res=[]
    for c in gold['cases']:
        f=next(s for seq in c['sequences'] for s in gate._flatten_steps(seq['steps'])
               if s.get('type')=='send' and s.get('signature')=='fund()' and s.get('expect')=='success')
        sol,n=forge_gold.generated_suite(c,gate.parse_wei(f['value_wei'],'amount'))
        res.append({'id':c['id'],'source_sha256':report_cases[c['id']]['solidity_sha256'],
                    'independent_gold_solidity_sha256':gate.digest(sol.encode()),
                    'generated_gold_tests':n,'passed_gold_tests':n,'docker_exit_code':0})
    return {'schema':gate.CERT_SCHEMA,'status':campaign.BASELINE_STATUS,
            'preflight':'PASS_GOLD_AND_PINNED_ARTIFACT_PREFLIGHT_NOT_EVM_CERTIFIED',
            'source_report_sha256':gate.digest_json(report),'independent_gold_sha256':gate.digest_json(gold),
            'cases_passed':6,'forge_gold_tests_passed':52,'no_remote_rpc_no_keys_no_live_deployment':True,
            'expert_gold_independence_verified':False,'foundry_image_id':'sha256:'+'a'*64,
            'foundry_image':'ghcr.io/foundry-rs/foundry:latest','cases':res}


def locked_inputs(tmp_path,report,gold,baseline):
    baseline_file=tmp_path/'fixture-baseline.json'
    baseline_file.write_text(json.dumps(baseline,indent=2),encoding='utf8')
    lock=freeze.build_lock(report,gold,baseline,
        report_path=REPORT,gold_path=GOLD,baseline_path=baseline_file,
        workspaces_root=ROOT/'runtime/reports',repo_root=ROOT)
    args={'evidence_lock':lock,'report_path':REPORT,'gold_path':GOLD,'baseline_path':baseline_file}
    return lock,args


def test_mutation_catalog_has_unique_anchors_and_gold_witnesses(artifacts):
    report,gold=artifacts
    by_id={c['id']:c for c in gold['cases']}
    assert len(campaign.MUTANTS)==20
    assert len({m.id for m in campaign.MUTANTS})==20
    assert set(m.case_id for m in campaign.MUTANTS)==set(campaign.CASES)
    assert len({m.domain for m in campaign.MUTANTS})>=8
    for m in campaign.MUTANTS:
        origin=next((ROOT/'runtime/reports').glob('*-'+m.case_id+'.workspace/src/NanoJevGoverned01.sol'))
        src=origin.read_text(encoding='utf8')
        result=campaign.apply_mutation(src,m)
        assert result!=src and m.after in result
        assert campaign.witness_count(by_id[m.case_id],m)>0


def test_mutation_denies_missing_or_ambiguous_anchor(artifacts):
    source=next((ROOT/'runtime/reports').glob('*-single_odd_wei_silence.workspace/src/NanoJevGoverned01.sol')).read_text()
    wrong=campaign.Mutation('X','single_odd_wei_silence','wrong','approve','not-a-real-solidity-pattern','replaced',
                            'approve()','outsider','revert','test')
    with pytest.raises(gate.CertificationFailure,match='anchor not unique'):
        campaign.apply_mutation(source,wrong)


@pytest.mark.parametrize('code,stdout,expected,status',[
    (1,'ParserError: unexpected token',7,'INVALID_RUN'),
    (1,'',7,'INVALID_RUN'),
    (1,'Ran 7 test suites in 5ms: 5 tests passed, 2 failed, 0 skipped (7 total tests)',7,'KILLED_BY_GOLD_ASSERTION'),
    (0,'Ran 7 test suites in 5ms: 7 tests passed, 0 failed, 0 skipped (7 total tests)',7,'SURVIVED_GOLD'),
    (0,'Ran 7 test suites in 5ms: 5 tests passed, 2 failed, 0 skipped (7 total tests)',7,'INVALID_RUN'),
    (1,'Ran 6 test suites in 5ms: 5 tests passed, 1 failed, 0 skipped (6 total tests)',7,'INVALID_RUN'),
    (1,'Ran 7 test suites in 5ms: 5 tests passed, 1 failed, 1 skipped (7 total tests)',7,'INVALID_RUN'),
    (1,'Ran 7 test suites in 5ms: 7 tests passed, 0 failed, 0 skipped (7 total tests)',7,'INVALID_RUN'),
])
def test_forge_mutant_kill_requires_real_failing_assertions(code,stdout,expected,status):
    assert campaign.classify_execution(code,stdout,expected)['outcome']==status


def test_baseline_pins_forge_test_source_and_report(artifacts):
    r,g=artifacts
    good=baseline_for_tests(r,g)
    assert len(campaign.validate_baseline(r,g,good))==6
    bad=copy.deepcopy(good)
    bad['cases'][0]['independent_gold_solidity_sha256']='0'*64
    with pytest.raises(gate.CertificationFailure,match='gold test Solidity changed'):
        campaign.validate_baseline(r,g,bad)
    bad=copy.deepcopy(good)
    bad['foundry_image_id']='missing'
    with pytest.raises(gate.CertificationFailure,match='image digest'):
        campaign.validate_baseline(r,g,bad)


def test_campaign_offline_fake_docker_only_confirms_classification(monkeypatch,tmp_path,artifacts):
    r,g=artifacts
    fake=baseline_for_tests(r,g)
    lock,locked=locked_inputs(tmp_path,r,g,fake)
    monkeypatch.setattr(campaign.shutil,'which',lambda name:'/mock/docker')
    def fake_run(cmd,**kwargs):
        if cmd[:3]==['docker','image','inspect']:
            return SimpleNamespace(returncode=0,stdout=fake['foundry_image_id']+'\n',stderr='')
        assert cmd[0]=='docker'
        if '--match-path' in cmd:
            assert cmd[-3:]==['--match-path','test/NanoJevAdversarialProbes.t.sol','-vv']
            return SimpleNamespace(returncode=0,stdout='Ran 1 test suite in 1ms: 1 test passed, 0 failed, 0 skipped (1 total test)',stderr='')
        assert cmd[-2:]==['test','-vv']
        return SimpleNamespace(returncode=1,stdout='Ran 8 test suites in 5ms: 7 tests passed, 1 failed, 0 skipped (8 total tests)',stderr='')
    monkeypatch.setattr(campaign.subprocess,'run',fake_run)
    out=tmp_path/'campaign.json'
    result=campaign.run_campaign(r,g,fake,report_root=ROOT/'runtime/reports',repo_root=ROOT,output=out,ids=['M01'],**locked)
    assert result['status']==campaign.FAIL # partial runs never certify full catalog
    assert result['killed_by_Forge_assertion']==1
    assert result['mutations'][0]['execution']['outcome']=='KILLED_BY_GOLD_ASSERTION'
    # No file in original frozen workspace is changed.
    source=next((ROOT/'runtime/reports').glob('*-single_odd_wei_silence.workspace/src/NanoJevGoverned01.sol'))
    expected=next(c for c in r['cases'] if c['id']=='single_odd_wei_silence')['solidity_sha256']
    gate.canonical_solidity_sha256(source,expected)


def test_campaign_missing_docker_fails_closed(monkeypatch,tmp_path,artifacts):
    r,g=artifacts
    base=baseline_for_tests(r,g)
    lock,locked=locked_inputs(tmp_path,r,g,base)
    monkeypatch.setattr(campaign.shutil,'which',lambda name:None)
    with pytest.raises(gate.CertificationFailure,match='Docker executable required'):
        campaign.run_campaign(r,g,base,report_root=ROOT/'runtime/reports',
                              repo_root=ROOT,output=tmp_path/'x.json',**locked)


def test_semantic_packet_requires_real_mutation_success(artifacts):
    r,g=artifacts
    base=baseline_for_tests(r,g)
    fake={'schema':campaign.SCHEMA,'status':campaign.FAIL}
    with pytest.raises(gate.CertificationFailure,match='not wholly killed'):
        review.prepare(r,g,fake,base)
    fake={'schema':campaign.SCHEMA,'status':campaign.PASS,'campaign_complete':True,
          'killed_by_Forge_assertion':20,'source_report_sha256':gate.digest_json(r),
          'independent_gold_sha256':gate.digest_json(g),'baseline_certificate_sha256':gate.digest_json(base),
          'foundry_image_id':base['foundry_image_id'],'pristine_probe_tests_passed':6,'frozen_inputs_reverified':True}
    packet=review.prepare(r,g,fake,base)
    assert packet['status']=='REVIEW_REQUIRED_NO_EXPERT_CERTIFICATION'
    assert len(packet['claims'])==14
    assert all(x['gold_coverage'] for x in packet['claims'] if x['id'] not in ('R10',))
    form=review.empty_review(packet)
    assert form['packet_sha256']==gate.digest_json(packet)
    with pytest.raises(gate.CertificationFailure,match='reviewer metadata'):
        review.adjudicate(packet,form,repo_root=ROOT)


def test_semantic_review_rejects_false_external_independence(artifacts,tmp_path):
    r,g=artifacts
    base=baseline_for_tests(r,g)
    fake={'schema':campaign.SCHEMA,'status':campaign.PASS,'campaign_complete':True,'killed_by_Forge_assertion':20,
          'source_report_sha256':gate.digest_json(r),'independent_gold_sha256':gate.digest_json(g),
          'baseline_certificate_sha256':gate.digest_json(base),'foundry_image_id':base['foundry_image_id'],'pristine_probe_tests_passed':6,'frozen_inputs_reverified':True}
    packet=review.prepare(r,g,fake,base)
    form=review.empty_review(packet)
    form['reviewer'].update({'full_name':'ChatGPT helper','affiliation':'OpenAI assistant',
                'qualification':'Automated reviewer','independence_declaration':'Not truly independent',
                'review_date':'2026-11-19','not_author_of_V3_code_or_gold':True,
                'outside_contract_specification_provided':True})
    with pytest.raises(gate.CertificationFailure,match='AI-authored'):
        review.adjudicate(packet,form,repo_root=tmp_path)


def test_complete_campaign_simulation_is_not_real_EVM_evidence(monkeypatch,tmp_path,artifacts):
    """Mock results check orchestration; this DOES NOT establish actual mutation kills."""
    r,g=artifacts
    baseline=baseline_for_tests(r,g)
    lock,locked=locked_inputs(tmp_path,r,g,baseline)
    monkeypatch.setattr(campaign.shutil,'which',lambda x:'/mock/docker')
    def fake_docker(cmd,**kwargs):
        if cmd[:3]==['docker','image','inspect']:
            return SimpleNamespace(returncode=0,stdout=baseline['foundry_image_id']+'\n',stderr='')
        mount=cmd[cmd.index('--mount')+1]
        path=Path(next(x for x in mount.split(',') if x.startswith('source=')).split('=',1)[1])
        probe=(path/'test/NanoJevAdversarialProbes.t.sol')
        gold_file=(path/'test/NanoJevIndependentGold.t.sol')
        if '--match-path' in cmd:
            assert probe.is_file() and not gold_file.exists()
            count=probe.read_text().count('function test')
            return SimpleNamespace(returncode=0,stdout=f'Ran {count} test suites in 1ms: {count} tests passed, 0 failed, 0 skipped ({count} total tests)',stderr='')
        count=gold_file.read_text().count('function test') + (probe.read_text().count('function test') if probe.exists() else 0)
        return SimpleNamespace(returncode=1,stdout=f'[FAIL: behavioral mismatch] testIndependentGoldTrace1()\nRan {count} test suites in 1ms: {count-1} tests passed, 1 failed, 0 skipped ({count} total tests)',stderr='')
    monkeypatch.setattr(campaign.subprocess,'run',fake_docker)
    result=campaign.run_campaign(r,g,baseline,report_root=ROOT/'runtime/reports',repo_root=ROOT,
                                 output=tmp_path/'full-simulation.json',**locked)
    assert result['status']==campaign.PASS
    assert result['killed_by_Forge_assertion']==20
    assert result['pristine_probe_tests_passed']==6
    assert result['frozen_inputs_reverified'] is True
    assert len(result['pristine_probe_baselines'])==5
    assert all(e['execution']['outcome']=='KILLED_BY_GOLD_ASSERTION' for e in result['mutations'])


def test_review_metadata_crosswalk_cannot_claim_authenticated_expert(artifacts,tmp_path):
    r,g=artifacts
    baseline=baseline_for_tests(r,g)
    mutation={'schema':campaign.SCHEMA,'status':campaign.PASS,'campaign_complete':True,
              'killed_by_Forge_assertion':20,'source_report_sha256':gate.digest_json(r),
              'independent_gold_sha256':gate.digest_json(g),'baseline_certificate_sha256':gate.digest_json(baseline),
              'foundry_image_id':baseline['foundry_image_id'],
              'pristine_probe_tests_passed':6,'frozen_inputs_reverified':True}
    packet=review.prepare(r,g,mutation,baseline)
    terms=tmp_path/'outside-contractual-terms.txt'
    terms.write_text('Fixture terms for checking validator wiring, not real contractual evidence.',encoding='utf8')
    form=review.empty_review(packet)
    form['reviewer']={'full_name':'Independent Example Reviewer','affiliation':'Example Review Organization',
                    'qualification':'Contract law technical analyst','independence_declaration':'No prior participation in implementation or gold authoring',
                    'review_date':'2026-11-19','not_author_of_V3_code_or_gold':True,
                    'outside_contract_specification_provided':True}
    form['primary_sources']=[{'id':'terms','type':'authorized_requirements','path':terms.name,
                              'sha256':gate.digest(terms.read_bytes())}]
    for item in form['findings']:
        item.update(finding='AGREE',primary_source_ids=['terms'],
                    normative_requirement='This is a synthetic example contractual requirement for validator unit tests.',
                    reasoning='Synthetic validation of the reviewer-attestation schema only; no actual legal or independent external determination is being made here.',
                    reviewer_reference_location='section example')
    validated=review.adjudicate(packet,form,repo_root=tmp_path)
    assert validated['status']=='PASS_DOCUMENTED_EXTERNAL_REVIEW_ATTESTATIONS_NOT_IDENTITY_VERIFIED'
    assert validated['expert_independence_verified'] is False
    assert validated['reviewer_identity_authenticated'] is False
    terms.write_text('changed',encoding='utf8')
    with pytest.raises(gate.CertificationFailure,match='external terms changed'):
        review.adjudicate(packet,form,repo_root=tmp_path)
    terms.write_text('Fixture terms for checking validator wiring, not real contractual evidence.',encoding='utf8')
    form['findings'][0]['finding']='CONFLICT'
    assert review.adjudicate(packet,form,repo_root=tmp_path)['status']=='FAIL_SEMANTIC_REVIEW_CONFLICT_OR_UNRESOLVED'
