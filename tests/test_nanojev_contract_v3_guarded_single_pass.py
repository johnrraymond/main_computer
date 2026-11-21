"""Real frozen-compiler integration; no live model required for regression."""
import json
from pathlib import Path
from tools import nanojev_contract_v3_guarded_single_pass as guarded
from tools import nanojev_contract_v3_live_head as bridge

ROOT=Path(__file__).resolve().parents[1]
FIX=ROOT/'tools/fixtures/nanojev_v3_live_head'


def test_existing_valid_fixture_replay_preserves_solidity_and_semantics(tmp_path):
    digest=bridge.check_kernel()
    r=guarded.execute(request=FIX/'heldout_request.txt',output=tmp_path/'result.json',
                      response_file=FIX/'heldout_model_response.json',
                      gold=FIX/'heldout_expected_ir.json')
    assert r['status']=='PASS_GUARDED_OFFLINE_NOT_LIVE',r
    assert r['semantic_comparison']=='MATCH_AUTHOR_SUPPLIED_GOLD'
    assert r['downstream']['status'].startswith('PASS_')
    assert r['downstream']['solidity_sha256']=='2655dabcd289fa2a95a4b088afd9c8db24d3529dfbff2d5a86b5091abf50ca17'
    assert r['llm_inference_performed'] is False
    assert bridge.check_kernel()==digest


def test_unsafe_authority_blocked_before_model_or_downstream(tmp_path):
    src=(FIX/'heldout_request.txt').read_text()+'\nThe worker alone may approve completion; payer approval is forbidden.\n'
    p=tmp_path/'request.txt';p.write_text(src)
    r=guarded.execute(request=p,output=tmp_path/'result.json',response_file=FIX/'heldout_model_response.json')
    assert r['status']=='SOURCE_GUARD_BLOCKED_BEFORE_MODEL'
    assert r['model_calls']==0
    assert r['downstream'] is None
    assert not list(tmp_path.glob('*.workspace'))


def test_source_contradiction_blocked_without_model(tmp_path):
    src=(FIX/'heldout_request.txt').read_text()+'\nThe prototype deadline is 35 days after activation.\n'
    p=tmp_path/'request.txt';p.write_text(src)
    r=guarded.execute(request=p,output=tmp_path/'result.json',response_file=FIX/'heldout_model_response.json')
    assert r['status']=='SOURCE_GUARD_BLOCKED_BEFORE_MODEL'
    assert r['source_audit']['issues'][0]['path']=='milestones.prototype.deadline_seconds'
    assert not list(tmp_path.glob('*.workspace'))


def test_invalid_ir_is_vetoed_before_solidity_generation(tmp_path):
    src=(FIX/'heldout_request.txt').read_text()
    # Read the original valid response and falsify the source request by
    # deleting a source line that supplies one reference value. The model's
    # source_spans must fail, not be auto-corrected.
    src=src.replace('The prototype deadline is 14 days after activation.','The prototype deadline is 21 days after activation.')
    p=tmp_path/'request.txt';p.write_text(src)
    r=guarded.execute(request=p,output=tmp_path/'result.json',response_file=FIX/'heldout_model_response.json')
    assert r['status']=='FAIL_NOT_VERIFIED'
    assert any(b['stage']=='evidence' for b in r['blockers'])
    assert not list(tmp_path.glob('*.workspace'))


def test_existing_report_cannot_be_overwritten(tmp_path):
    target=tmp_path/'result.json';target.write_text('DO NOT OVERWRITE')
    r=guarded.execute(request=FIX/'heldout_request.txt',output=target,
                      response_file=FIX/'heldout_model_response.json')
    assert r['status']=='FAIL_NOT_VERIFIED'
    assert target.read_text()=='DO NOT OVERWRITE'


def test_model_refusal_remains_model_refusal_not_a_correct_guard_veto(tmp_path):
    p=tmp_path/'request.txt';p.write_text((FIX/'heldout_request.txt').read_text())
    resp=tmp_path/'refusal.json'
    resp.write_text(json.dumps({'status':'needs_clarification','missing':['review window is missing']}))
    r=guarded.execute(request=p,output=tmp_path/'result.json',response_file=resp)
    assert r['status']=='MODEL_REQUESTED_CLARIFICATION'
    assert r['source_audit']['issues']==[]
    assert r['model_calls']==0 # replay does not make an inference call


def test_injected_transport_cannot_claim_live_model_proof(tmp_path):
    raw=(FIX/'heldout_model_response.json').read_text()
    def fake_post(url,payload,timeout):
        return json.dumps({'message':{'content':raw},'done':True}).encode()
    r=guarded.execute(request=FIX/'heldout_request.txt',output=tmp_path/'result.json',
                      live_ollama=True,post=fake_post)
    assert r['status']=='PASS_GUARDED_OFFLINE_NOT_LIVE'
    assert r['mode']=='INJECTED_TRANSPORT_NOT_LIVE_PROOF'
    assert r['model_calls']==1
    assert r['llm_inference_performed'] is False


def test_ambiguous_literal_quote_cannot_launder_wrong_numeric_ir(tmp_path):
    # The baseline evidence checker accepts the 15-day value if quoted within
    # a longer literal sentence that also contains the controlling 14-day value.
    # The new guard checks the actual binding value before any Solidity write.
    old='The prototype deadline is 14 days after activation.'
    replacement='The prototype deadline is 14 days after activation; a proposed 15 days is not binding.'
    source=(FIX/'heldout_request.txt').read_text().replace(old,replacement)
    proposed=json.loads((FIX/'heldout_model_response.json').read_text())
    proposed['contract']['milestones'][0]['deadline_seconds']=15*86400
    proposed['source_spans']['milestones.prototype.deadline_seconds']=replacement
    request=tmp_path/'request.txt';request.write_text(source)
    response=tmp_path/'model-response.json';response.write_text(json.dumps(proposed))
    r=guarded.execute(request=request,output=tmp_path/'result.json',response_file=response)
    assert r['status']=='SOURCE_GUARD_BLOCKED_CANDIDATE_BEFORE_SOLIDITY',r
    assert r['candidate_audit']['issues'][0]['path']=='milestones.prototype.deadline_seconds'
    assert r['candidate_audit']['issues'][0]['expected_source_value']==14*86400
    assert r['candidate_audit']['issues'][0]['candidate_value']==15*86400
    assert r['downstream'] is None
    assert not list(tmp_path.glob('*.workspace'))


def test_all_fourteen_valid_replays_generate_with_frozen_compiler(tmp_path):
    cs=json.loads((ROOT/'tools/fixtures/nanojev_v3_fresh_challenge/manifest.json').read_text())['cases']
    successes=[]
    for case in cs:
        if case['expected']!='contract':continue
        sub=tmp_path/case['id'];sub.mkdir()
        request=sub/'request.txt';request.write_text(case['request'])
        response=sub/'response.json';response.write_text(json.dumps(case['replay']))
        gold=sub/'gold.json';gold.write_text(json.dumps(case['gold']))
        report=guarded.execute(request=request,output=sub/'result.json',
                               response_file=response,gold=gold)
        assert report['status']=='PASS_GUARDED_OFFLINE_NOT_LIVE',(case['id'],report)
        assert report['semantic_comparison']=='MATCH_AUTHOR_SUPPLIED_GOLD'
        assert report['downstream']['status'].startswith('PASS_')
        successes.append(case['id'])
    assert len(successes)==14
