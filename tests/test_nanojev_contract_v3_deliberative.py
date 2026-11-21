"""Full three-pass integration tests through the frozen V3 verifier and generator."""
import json
from pathlib import Path
from tools import nanojev_contract_v3_deliberative as d
from tools import nanojev_contract_v3_live_head as bridge
from tools import nanojev_contract_v3_semantic_guard as g
from test_nanojev_contract_v3_semantic_guard import fixture,ledger_from_answer,review_for

FIX=Path(__file__).resolve().parents[1]/'tools'/'fixtures'/'nanojev_v3_live_head'


def setup(tmp_path,request_text=None,answer=None, ledger=None, review=None, independent=None, final=None):
    text, original=fixture()
    if request_text is not None:text=request_text
    if answer is None:answer=original
    if ledger is None:ledger=ledger_from_answer(text,answer)
    if review is None:review=review_for(ledger)
    raw={'ledger':ledger,'review':review,'final':answer}
    if independent is not None:raw['independent_review']=independent
    paths={}
    for key,value in raw.items():
        paths[key]=tmp_path/(key+'.json')
        paths[key].write_text(json.dumps(value))
    request=tmp_path/'request.txt';request.write_text(text)
    return request,paths


def test_full_replay_uses_real_v3_verifier_and_generator(tmp_path):
    request,paths=setup(tmp_path)
    report=d.execute(request=request,output=tmp_path/'result.json',mode='replay',
                     replay_files=paths,gold=FIX/'heldout_expected_ir.json')
    assert report['status']=='PASS_DELIBERATIVE_V3_IR_FORGE_NOT_RUN',report
    assert report['semantic_comparison']=='MATCH_AUTHOR_SUPPLIED_GOLD'
    assert report['model_calls']==3
    assert report['llm_inference_performed'] is False
    assert report['downstream']['status'].startswith('PASS_')
    assert report['graph_status']=='PASS_CONDITIONAL'
    assert report['assist_type']=='MULTIPASS_INFERENCE_NOT_FIRST_PASS'


def test_ledger_refusal_blocks_before_final(tmp_path):
    text,answer=fixture();text+='\nThe worker alone approves completion; the payer may not approve work.\n'
    ledger=ledger_from_answer(text,answer)
    ledger['lines'][-1].update(kind='unsupported',issue='worker approval authority not expressible')
    review=review_for(ledger,issues=[{'line':len(text.splitlines()),
                     'category':'unsupported','reason':'worker approval authority not expressible'}])
    request,paths=setup(tmp_path,text,answer,ledger,review)
    report=d.execute(request=request,output=tmp_path/'result.json',mode='replay',replay_files=paths)
    assert report['status']=='REFUSED_BY_SEMANTIC_REVIEW_NOT_CERTIFIED'
    assert report['model_calls']==2
    assert 'worker approval' in str(report['clarification_needed'])
    assert not list(tmp_path.glob('*.semantic.workspace'))


def test_silent_ledger_omission_rejected_even_if_review_proceeds(tmp_path):
    text,answer=fixture();text+='\nThe worker alone approves completion; the payer may not approve work.\n'
    ledger=ledger_from_answer(text,answer)
    request,paths=setup(tmp_path,text,answer,ledger)
    report=d.execute(request=request,output=tmp_path/'result.json',mode='replay',replay_files=paths)
    assert report['status']=='FAIL_NOT_VERIFIED'
    assert report['model_calls']==1
    assert report['blockers'][0]['stage']=='UNMAPPED_MATERIAL'


def test_ledger_maps_unsupported_to_fake_covered_field_rejected(tmp_path):
    text,answer=fixture();text+='\nThe worker alone approves completion; the payer may not approve work.\n'
    ledger=ledger_from_answer(text,answer)
    ledger['lines'][-1].update(kind='term',paths=['policy.on_review_silence'])
    request,paths=setup(tmp_path,text,answer,ledger)
    report=d.execute(request=request,output=tmp_path/'result.json',mode='replay',replay_files=paths)
    assert report['status']=='FAIL_NOT_VERIFIED'
    assert report['model_calls']==3
    assert report['blockers'][0]['stage']=='UNCITED_MATERIAL'


def test_independent_reviewer_veto(tmp_path):
    text,answer=fixture()
    ledger=ledger_from_answer(text,answer)
    issue=[{'line':2,'category':'ambiguous','reason':'reviewer sees unaddressed semantic ambiguity'}]
    independent=review_for(ledger,issues=issue)
    request,paths=setup(tmp_path,text,answer,ledger,review_for(ledger),independent=independent)
    report=d.execute(request=request,output=tmp_path/'result.json',mode='replay',
                     replay_files=paths,independent_review_model='qwen3.8:27b')
    assert report['status']=='REFUSED_BY_SEMANTIC_REVIEW_NOT_CERTIFIED'
    assert report['model_calls']==3
    assert report['independent_review_model']=='qwen3.8:27b'


def test_positive_model_wrong_ir_is_not_repaired(tmp_path):
    text,answer=fixture()
    answer=json.loads(json.dumps(answer))
    answer['contract']['milestones'][0]['deadline_seconds']=15*86400
    # Keep original source; changed value cannot be evidenced. No rewriting.
    request,paths=setup(tmp_path,text,answer,
                         ledger=ledger_from_answer(text,json.loads((FIX/'heldout_model_response.json').read_text())))
    report=d.execute(request=request,output=tmp_path/'result.json',mode='replay',replay_files=paths)
    assert report['status']=='FAIL_NOT_VERIFIED'
    assert report['blockers'][0]['stage']=='evidence'


def test_source_value_conflict_refused_even_if_models_overlook_it(tmp_path):
    text,answer=fixture();text+='\nThe prototype deadline is 35 days after activation.\n'
    ledger=ledger_from_answer(text,answer)
    ledger['lines'][-1].update(kind='term',paths=['milestones.prototype.deadline_seconds'])
    request,paths=setup(tmp_path,text,answer,ledger)
    report=d.execute(request=request,output=tmp_path/'result.json',mode='replay',replay_files=paths)
    assert report['status']=='REFUSED_BY_SEMANTIC_REVIEW_NOT_CERTIFIED'
    assert report['literal_collisions'][0]['path']=='milestones.prototype.deadline_seconds'


def test_ir_candidate_can_refuse_and_not_produce_solidity(tmp_path):
    text,answer=fixture()
    ledger=ledger_from_answer(text,answer)
    refusal={'status':'needs_clarification','missing':['uncertain interpretation of prototype deadline']}
    request,paths=setup(tmp_path,text,answer,ledger,final=refusal)
    paths['final'].write_text(json.dumps(refusal))
    report=d.execute(request=request,output=tmp_path/'result.json',mode='replay',replay_files=paths)
    assert report['status']=='REFUSED_BY_IR_GENERATOR_NOT_CERTIFIED'
    assert report['downstream'] is None


def test_no_overwrite_existing_report(tmp_path):
    request,paths=setup(tmp_path)
    (tmp_path/'result.json').write_text('original')
    result=d.execute(request=request,output=tmp_path/'result.json',mode='replay',replay_files=paths)
    assert result['status']=='FAIL_NOT_VERIFIED'
    assert (tmp_path/'result.json').read_text()=='original'


def test_no_kernel_mutation_or_model_training_from_replay(tmp_path):
    before=bridge.check_kernel()
    request,paths=setup(tmp_path)
    report=d.execute(request=request,output=tmp_path/'result.json',mode='replay',replay_files=paths)
    assert before==bridge.check_kernel()
    assert report['native_nanojev_neural_model_used'] is False
    assert report['trained_model'] is False


def test_postflight_reviewer_can_veto_even_valid_ir(tmp_path):
    text,answer=fixture();ledger=ledger_from_answer(text,answer)
    postflight=review_for(ledger,issues=[{'line':3,'category':'ambiguous',
                                         'reason':'model reviewer requests deadline clarification'}])
    request,paths=setup(tmp_path,text,answer,ledger)
    post=tmp_path/'postflight.json';post.write_text(json.dumps(postflight))
    paths['postflight']=post
    report=d.execute(request=request,output=tmp_path/'result.json',mode='replay',
                     replay_files=paths,postflight_review=True)
    assert report['status']=='REFUSED_BY_POSTFLIGHT_REVIEW_NOT_CERTIFIED'
    assert report['model_calls']==4
    assert report['downstream'] is None


def test_postflight_approval_allows_real_v3_generation(tmp_path):
    text,answer=fixture();ledger=ledger_from_answer(text,answer)
    request,paths=setup(tmp_path,text,answer,ledger)
    post=tmp_path/'postflight.json';post.write_text(json.dumps(review_for(ledger)))
    paths['postflight']=post
    report=d.execute(request=request,output=tmp_path/'result.json',mode='replay',
                     replay_files=paths,postflight_review=True)
    assert report['status']=='PASS_DELIBERATIVE_V3_IR_FORGE_NOT_RUN'
    assert report['model_calls']==4
    assert report['postflight_review_enabled'] is True


def test_delta_reports_changed_path_not_just_ir_hash():
    _,answer=fixture()
    original=answer['contract']
    changed=json.loads(json.dumps(original))
    changed['milestones'][0]['deadline_seconds']+=86400
    delta=d.semantic_delta(changed,original)
    assert delta==[{'path':'milestones.prototype.deadline_seconds',
                    'model':original['milestones'][0]['deadline_seconds']+86400,
                    'reference':original['milestones'][0]['deadline_seconds']}]
