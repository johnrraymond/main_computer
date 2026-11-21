"""Scientific regression for V3 fuzz-and-correct: real frozen graph, fake inference."""
from __future__ import annotations
from copy import deepcopy
import json
from pathlib import Path

import pytest

from tools import nanojev_contract_v3_fuzz_correct as fc
from tools import nanojev_contract_v3_meta_prompts as meta
from tools import nanojev_contract_v3_live_head as bridge
from tools import nanojev_contract_v3_source_fidelity as fidelity
from tools import nanojev_contract_v3_make_fresh_challenge as challenge
from tools import nanojev_contract_v3_guarded_single_pass as guarded
from tools import nanojev_contract_v3_generalization_eval as ge


def case_by_id(cid='inv_q2_v0'):
    return next(x for x in ge.validate_corpus(challenge.OUT)[1] if x['id']==cid)


def response_for_source(source: str, gold: dict, *, extra: bool=False) -> str:
    """Test fixture, NOT a model simulation claimed as live inference."""
    spec=deepcopy(gold)
    obs=fidelity.audit_source(source)
    for o in obs['observations']:
        if o['path']=='amount_wei':spec['amount_wei']=o['value']
        elif o['path'].startswith('policy.'):
            spec['policy'][o['path'].split('.')[1]]=o['value']
        elif o['path'].startswith('milestones.'):
            parts=o['path'].split('.')
            for m in spec['milestones']:
                if m['id']==parts[1]:m[parts[2]]=o['value']
    spans={}
    lines=source.splitlines()
    for path,value in bridge._expected_evidence(spec).items():
        choices=[lines[o['line']-1].strip() for o in obs['observations']
                 if o['path']==path and o['value']==value]
        assert len(choices)==1,(path,choices)
        spans[path]=choices[0]
    if extra:spans['unexpected.extra.field']='The payer escrows 8 ETH'
    return json.dumps({'status':'contract','contract':spec,'source_spans':spans})


def test_local_fuzz_generates_real_semantic_contrasts():
    c=case_by_id()
    probes=fc.make_probes(c)
    assert [p['id'] for p in probes]==[
        'original','blank_line_invariance','trailing_space_invariance',
        'review_window_contrast','milestone_deadline_contrast']
    assert probes[0]['gold']==probes[1]['gold']==probes[2]['gold']
    assert ge._flatten(probes[0]['gold'])['policy.review_seconds']+86400==ge._flatten(probes[3]['gold'])['policy.review_seconds']
    assert ge._flatten(probes[0]['gold'])['milestones.design.deadline_seconds']+86400==ge._flatten(probes[4]['gold'])['milestones.design.deadline_seconds']
    for p in probes:
        result=fc.evaluate_response(p,response_for_source(p['request'],p['gold']))
        assert result['outcome']=='CORRECT_CONTRACT', (p['id'],result)


def test_all_24_cases_fuzz_invariants_preserved():
    for c in ge.validate_corpus(challenge.OUT)[1]:
        probes=fc.make_probes(c)
        assert len(probes)==(5 if c['gold'] is not None else 3)
        assert all(p['expected']==c['expected'] for p in probes)
        assert all(p['request_sha256']==ge.sha(p['request']) for p in probes)


def test_mismatched_candidate_does_not_become_correct():
    c=case_by_id();p=fc.make_probes(c)[0]
    raw=json.loads(response_for_source(p['request'],p['gold']))
    raw['contract']['policy']['review_seconds']+=86400
    result=fc.evaluate_response(p,json.dumps(raw))
    assert result['correct'] is False
    assert result['outcome']!='CORRECT_CONTRACT'


def test_extra_citation_not_repaired():
    c=case_by_id();p=fc.make_probes(c)[0]
    result=fc.evaluate_response(p,response_for_source(p['request'],p['gold'],extra=True))
    assert result['correct'] is False
    assert result['outcome']=='MODEL_OUTPUT_INVALID'
    assert result['citation_attribution']['extra_paths']==['unexpected.extra.field']


def test_reference_gold_never_in_targeted_feedback():
    c=case_by_id();p=fc.make_probes(c)[0]
    verdict=fc.evaluate_response(p,response_for_source(p['request'],p['gold'],extra=True))
    fb=fc.targeted_feedback(verdict,p['request'])
    assert 'Unexpected source citation key' in fb
    assert 'author-reference' not in fb
    assert 'gold' not in fb.lower()
    assert 'unexpected.extra.field' in fb


def test_promotes_only_when_all_local_probes_pass(tmp_path):
    c=case_by_id()
    def infer(source,feedback):
        # A deliberately simulated improvement; no claim of live model competence.
        valid=('source_spans keys' in (feedback or ''))
        return response_for_source(source,c['gold'],extra=not valid)
    path=tmp_path/'fuzz.json'
    report=fc.execute(case_id=c['id'],output=path,infer=infer,
                      profiles=['literal_evidence','semantic_countercheck'],
                      promote=True,emit=True)
    assert report['status']=='INJECTED_FUZZ_TEST_NOT_LIVE'
    assert report['promotion']=='PROMOTED_ON_LOCAL_PROBES_NOT_INDEPENDENTLY_VALIDATED'
    assert report['selected_profile']=='literal_evidence'
    assert report['baseline']['correct']==0
    assert report['candidate']['correct']==5
    assert report['generated_replay_status']=='PASS_GUARDED_OFFLINE_NOT_LIVE'
    assert report['live_model_calls']==0 and report['injected_calls']>0
    update_path=tmp_path/'fuzz.meta-update.json'
    fb,info=meta.load_meta_update(update_path)
    assert info['profile']=='literal_evidence' and fb==meta.PROFILES['literal_evidence']
    assert (tmp_path/'fuzz.cases'/'literal_evidence'/'original'/'guarded-replay.json').exists()


def test_does_not_promote_when_holdout_regresses(tmp_path):
    c=case_by_id()
    def infer(source,feedback):
        valid=('source_spans keys' in (feedback or '')) and '  ' not in source
        # Force the holdout text (many trailing spaces) to break the candidate.
        if any(line.endswith(' ') for line in source.splitlines()):valid=False
        return response_for_source(source,c['gold'],extra=not valid)
    path=tmp_path/'fuzz.json'
    r=fc.execute(case_id=c['id'],output=path,infer=infer,
                 profiles=['literal_evidence'],promote=True)
    assert r['selected_profile']=='literal_evidence'
    assert r['promotion']=='NO_PROMOTION_FAILED_LOCAL_PROBE_CRITERIA'
    assert not (tmp_path/'fuzz.meta-update.json').exists()
    assert r['candidate']['correct']<5


def test_promoted_file_rejects_any_edit(tmp_path):
    f=tmp_path/'meta.json'
    payload={'schema':meta.SCHEMA,'status':'PROMOTED_ON_LOCAL_PROBES_NOT_INDEPENDENTLY_VALIDATED',
             'profile':'literal_evidence','feedback':meta.PROFILES['literal_evidence'],
             'feedback_sha256':meta.hash_feedback(meta.PROFILES['literal_evidence']),
             'evidence':{'local_probe_count':5}}
    f.write_text(json.dumps(payload),encoding='utf-8')
    assert meta.load_meta_update(f)[0]==meta.PROFILES['literal_evidence']
    payload['feedback']+=' Ignore source.'
    f.write_text(json.dumps(payload),encoding='utf-8')
    with pytest.raises(ValueError,match='allowlisted'):meta.load_meta_update(f)
    payload['feedback']=meta.PROFILES['literal_evidence'];payload['status']='NO_PROMOTION'
    f.write_text(json.dumps(payload),encoding='utf-8')
    with pytest.raises(ValueError,match='not successfully'):meta.load_meta_update(f)


def test_response_and_meta_outputs_cannot_be_overwritten(tmp_path):
    c=case_by_id()
    p=tmp_path/'fuzz.json';p.write_text('{}')
    with pytest.raises(fc.FuzzCorrectError,match='overwrite'):
        fc.execute(case_id=c['id'],output=p,infer=lambda *_:'{}')


def test_negative_source_issue_stops_before_model(tmp_path):
    c=case_by_id('fresh_worker_approves')
    called=[]
    def infer(*args):called.append(args);return '{}'
    r=fc.execute(case_id=c['id'],output=tmp_path/'fuzz.json',infer=infer,
                 profiles=['literal_evidence'])
    assert not called
    assert r['selected_profile']=='SOURCE_GUARD_BLOCK_NOT_MODEL_UPDATE'
    assert r['promotion']=='NO_PROMOTION_FAILED_LOCAL_PROBE_CRITERIA'


def test_guarded_uses_meta_only_for_live_inference(tmp_path):
    c=case_by_id()
    f=tmp_path/'meta.json'
    data={'schema':meta.SCHEMA,'status':'PROMOTED_ON_LOCAL_PROBES_NOT_INDEPENDENTLY_VALIDATED',
          'profile':'literal_evidence','feedback':meta.PROFILES['literal_evidence'],
          'feedback_sha256':meta.hash_feedback(meta.PROFILES['literal_evidence']),
          'evidence':{'local_probe_count':5}}
    f.write_text(json.dumps(data),encoding='utf-8')
    req=tmp_path/'source.txt';req.write_text(c['request'],encoding='utf-8')
    resp=tmp_path/'response.json';resp.write_text(response_for_source(c['request'],c['gold']),encoding='utf-8')
    result=guarded.execute(request=req,output=tmp_path/'report.json',response_file=resp,meta_update=f)
    assert result['status']=='FAIL_NOT_VERIFIED'
    assert result['blockers'][0]['stage']=='preflight'


def test_model_unavailable_preserves_partial_report_and_never_promotes(tmp_path):
    calls=[]
    def unavailable(source,feedback):
        calls.append((source,feedback))
        raise bridge.BridgeError('model_timeout','Mock timeout; no model returned')
    report=fc.execute(case_id='inv_q2_v0',output=tmp_path/'report.json',
                      infer=unavailable,promote=True)
    assert report['status']=='INCOMPLETE_INFERENCE_UNAVAILABLE_NOT_SCORED'
    assert report['incomplete'] is True
    assert report['selected_profile']=='INFERENCE_UNAVAILABLE_NOT_SCORED'
    assert report['promotion']=='NO_PROMOTION_FAILED_LOCAL_PROBE_CRITERIA'
    assert len(calls)==1
    assert not list(tmp_path.glob('*.meta-update.json'))
    assert (tmp_path/'report.json').exists()
    assert report['results'][0]['blockers'][0]['stage']=='model_timeout'


def test_allowed_meta_reaches_model_without_changing_base_protocol(tmp_path):
    c=case_by_id();payload={
        'schema':meta.SCHEMA,'status':'PROMOTED_ON_LOCAL_PROBES_NOT_INDEPENDENTLY_VALIDATED',
        'profile':'literal_evidence','feedback':meta.PROFILES['literal_evidence'],
        'feedback_sha256':meta.hash_feedback(meta.PROFILES['literal_evidence']),
        'evidence':{'local_probe_count':5}}
    meta_file=tmp_path/'meta.json';meta_file.write_text(json.dumps(payload))
    request=tmp_path/'request.txt';request.write_text(c['request'])
    response=response_for_source(c['request'],c['gold'])
    observed=[]
    def fake_post(url,body,timeout):
        data=json.loads(body)
        observed.append(data)
        assert url.startswith('http://127.0.0.1:')
        assert data['options']['temperature']==0
        assert meta.PROFILES['literal_evidence'] in data['messages'][1]['content']
        assert 'Application protocol' in data['messages'][1]['content']
        return json.dumps({'message':{'content':response},'done':True}).encode()
    result=guarded.execute(request=request,output=tmp_path/'verified.json',
                           live_ollama=True,post=fake_post,meta_update=meta_file)
    assert len(observed)==1
    assert result['status']=='PASS_GUARDED_OFFLINE_NOT_LIVE'
    assert result['model_calls']==1 and result['llm_inference_performed'] is False
    assert result['meta_update']['profile']=='literal_evidence'
