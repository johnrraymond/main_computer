"""G1-0020 frozen selective-conflict replay: CPU-only and no promotion."""
import hashlib
import json

import pytest
import torch

from tools import nanojev_gamer_v1_g1_selective_conflict as exp
from tools import nanojev_gamer_v1_graph_curriculum as cur
from tools import nanojev_gamer_v1_backbone_first as backbone
from tools.nanojev_gamer_v1_g1_action_ranking import decode_action_ranked
from tools.nanojev_gamer_v1_g1_action_width_validation import CONTROL, CHALLENGER
from tools.nanojev_gamer_v1_g1_calibration_validation import _summarize, _paired
from tools.nanojev_gamer_v1_graph_head import SPAN_CLASS_COUNT

ACTION = exp.SPAN_KINDS.index('action')+1
ACTOR = exp.SPAN_KINDS.index('actor')+1


def _simple(margin=3.5, kind=ACTION):
    by_end=[[],[(0,0,ACTION,4.)],[(1,0,ACTION,4.),(0,1,kind,margin)]]
    a=decode_action_ranked(by_end,action_power=1.0,action_penalty=0.)
    b=decode_action_ranked(by_end,action_power=1.25,action_penalty=0.)
    return by_end,a,b


def _select(candidates,a,b,rule):
    return exp.selective_decode(a,b,candidates,min_score_retention=rule,
                                boundary_guard=False)


def test_fixed_rules_and_safe_cli(capsys):
    assert exp.ARMS==(CONTROL,CHALLENGER)+tuple(x[0] for x in exp.RULES)
    assert all(x[1] in (.85,.90,.95) for x in exp.RULES)
    with pytest.raises(SystemExit) as exc:
        cur.main(['frob-g1-selective-conflicts','--help'])
    assert exc.value.code==0
    helptext=capsys.readouterr().out
    for flag in ('--conflict-run','--width-run','--ranking-run','--output','--device'):
        assert flag in helptext
    for flag in ('--train','--promote','--g2','--margin','--power','--seed'):
        assert flag not in helptext


def test_margin_retention_selects_without_gold():
    candidates,a,b=_simple()
    assert len(a)==2 and len(b)==1
    chosen_strict,d0=_select(candidates,a,b,.95)
    chosen_mid,_=_select(candidates,a,b,.90)
    chosen_lenient,d1=_select(candidates,a,b,.85)
    assert chosen_strict==chosen_mid==a
    assert chosen_lenient==b
    assert d0[0]['score_retention']==.875
    assert d1[0]['reason']=='accepted'
    assert 'gold' not in str(exp.selective_decode.__code__.co_varnames)


def test_boundary_guard_declines_fragment_merges():
    by_end,a,b=_simple()
    selected,events=exp.selective_decode(a,b,by_end,min_score_retention=.85,
                                         boundary_guard=True)
    assert selected==a
    assert events[0]['reason']=='multiple_shorter'


def test_mixed_object_kind_cannot_be_promoted():
    by_end,a,b=_simple(margin=3.5,kind=ACTOR)
    # A hand-constructed hypothetical challenger can replace action fragments
    # with an actor; the selective action rule must reject this.
    alt=[{'kind':'actor','token_first':0,'token_last':1}]
    chosen,events=exp.selective_decode(a,alt,by_end,min_score_retention=.85)
    assert chosen==a
    assert events[0]['reason']=='mixed_kind'


def test_hybrid_two_nonoverlapping_components():
    by_end=[[],[(0,0,ACTION,4.)],[(1,0,ACTION,4.),(0,1,ACTION,3.5)],
            [(2,0,ACTION,10.)],[(3,0,ACTION,4.)],
            [(4,0,ACTION,4.),(3,1,ACTION,3.2)]]
    a=[{'kind':'action','token_first':i,'token_last':i} for i in range(5)]
    b=[{'kind':'action','token_first':0,'token_last':1},
       {'kind':'action','token_first':2,'token_last':2},
       {'kind':'action','token_first':3,'token_last':4}]
    selected,events=exp.selective_decode(a,b,by_end,min_score_retention=.85)
    # First long has .875 score retention; second has .8 and loses.
    assert [(s['token_first'],s['token_last']) for s in selected] == [
        (0,1),(2,2),(3,3),(4,4)]
    assert [e['take_challenger'] for e in events]==[True,False]
    assert all(selected[i]['token_last'] < selected[i+1]['token_first']
               for i in range(len(selected)-1))


@pytest.mark.parametrize('invalid', [float('nan'),.6,1,True,-.5])
def test_rule_parameters_frozen(invalid):
    candidates,a,b=_simple()
    with pytest.raises(ValueError,match='INVALID_FIXED_RULE'):
        exp.selective_decode(a,b,candidates,min_score_retention=invalid)


def test_conflict_reference_rejects_tampered_counts(tmp_path):
    run=tmp_path/'conflicts';run.mkdir()
    report={'schema':exp.CONFLICT_SCHEMA,'status':'FROZEN_DEV_DIAGNOSIS_ONLY_NOT_CERTIFICATION',
            'cases':320,'families':{'encounter':160,'allocation':160},
            'frozen_reference':CONTROL,'experimental_challenger':CHALLENGER,
            'source_width_report_sha256':'width-sha',
            'source_width_dataset_sha256':'dataset-sha',
            'metrics_replayed':{'x':1},'paired_replayed':{'x':2},
            'changed_object_rows':0,'changed_case_count':26}
    (run/'action_conflict_report.json').write_text(json.dumps(report))
    (run/'action_conflict_objects.jsonl').write_text('')
    with pytest.raises(ValueError,match='changed_object_rows'):
        exp._read_conflict_reference(run,{
            'metrics':{'x':1},'paired_comparison':{'x':2},
            'validation_dataset_sha256':'dataset-sha'},
            {exp.WIDTH_FILES[0]:'width-sha'},
            [{'prompt':'a','family':'encounter'}]*320)


def test_gold_tagged_signal_analysis_is_only_post_inference():
    def item(direction):
        long={'margin':3.5,'control_score':7.,'token_first':0,'token_last':1,'token_width':2}
        short={'margin':4.,'control_score':4.,'token_first':0,'token_last':0,'token_width':1}
        # This fixture is not an observed 0019 record; it verifies only the
        # post-inference statistic computation and class labels.
        own,other=(long,short) if direction=='recovered_gold' else (short,long)
        return {'index':1,'direction':direction,'candidate':own,
                'overlapping_other_selected':[{'kind':'action',**other}]}
    report,signal=exp._signal_diagnosis([item('recovered_gold'),item('lost_gold')])
    assert report['recovered_gold']['median_raw_margin_ratio']==.875
    assert report['lost_gold']['score_retention_ge_095']==1
    assert report['gold_tagged_separability_after_inference_only']['selective_score_retention_095']['beneficial_long_actions_accepted']==1
    assert all('score_retention' in x for rows in signal.values() for x in rows)


def test_mocked_320_case_replay_is_frozen_and_immutable(tmp_path,monkeypatch):
    rows=[{'prompt':'abc','family':'encounter' if idx%2==0 else 'allocation',
           'split':'dev','object_spans':[
               {'start':i,'end':i+1,'kind':'action'} for i in range(3)]} for idx in range(320)]
    short={(i,i+1,'action') for i in range(3)}
    long={(0,3,'action')}
    summaries={CONTROL:_summarize([(short,short)]*320,rows),
               CHALLENGER:_summarize([(long,short)]*320,rows)}
    reference={'metrics':summaries,
               'paired_comparison':_paired([(short,short)]*320,[(long,short)]*320),
               'validation_dataset_sha256':'dataset-sha'}
    cases=[{'configurations':{
        CONTROL:{'correct':3,'missing':0,'false_positives':0,'exact':True},
        CHALLENGER:{'correct':0,'missing':3,'false_positives':1,'exact':False}}}
        for _ in range(320)]
    cp=tmp_path/'cp';cp.mkdir()
    (cp/'manifest.json').write_bytes(b'manifest')
    (cp/cur.HEAD_FILE).write_bytes(b'head')
    digest=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
    adapter=tmp_path/'adapter';adapter.write_text('adapter')
    info={'entries':{'null_focused':{'checkpoint':cp,'manifest':{
        'source_adapter':str(adapter),'model_id':'mock','head_sha256':'sha'}}},
        'adapter_sha':'adapter-sha',
        'checkpoint_files':{'null_focused':{
            'manifest_sha256':digest(cp/'manifest.json'),
            'head_file_sha256':digest(cp/cur.HEAD_FILE)}},
        'fresh_rows':[],'validation_rows':[],'dataset':tmp_path/'dataset'}
    monkeypatch.setattr(exp,'_load_prior_inputs',lambda *args:info)
    monkeypatch.setattr(exp,'_load_action_reference',lambda *args:({}, {}, {}))
    monkeypatch.setattr(exp,'_load_ranking_reference',lambda *args:({}, {}, {}))
    monkeypatch.setattr(exp,'fresh_rows',lambda *args:rows)
    monkeypatch.setattr(exp,'_load_width_reference',lambda *args:(reference,cases,{},
        {exp.WIDTH_FILES[0]:'width-sha'}))
    monkeypatch.setattr(exp,'_read_conflict_reference',lambda *args:({},[],{},
        {exp.CONFLICT_FILES[0]:'conflict-sha'}))
    monkeypatch.setattr(exp,'_changed_objects',lambda *args:[])
    monkeypatch.setattr(exp,'_signal_diagnosis',lambda *args:({},{}))
    monkeypatch.setattr(exp,'_verify_no_drift',lambda *args:None)
    monkeypatch.setattr(backbone,'adapter_fingerprint',lambda *args:'adapter-sha')
    monkeypatch.setattr(backbone,'model_components',lambda *args:(object(),object()))
    class Head:
        def eval(self): return self
        def __call__(self,hidden,**kwargs):
            logits=torch.full((3,3,SPAN_CLASS_COUNT),-40.,dtype=torch.float32)
            logits[:,:,0]=4.
            for i in range(3):logits[i,0,ACTION]=5.
            logits[0,2,ACTION]=4.5
            valid=torch.zeros(3,3,dtype=torch.bool)
            for i in range(3):valid[i,:3-i]=True
            return {'span_logits':logits,'span_valid':valid}
    monkeypatch.setattr(cur,'_load_checkpoint',lambda *args,**kwargs:(Head(),{
        'stage':'G1','head_sha256':'sha'}))
    monkeypatch.setattr(cur,'capture',lambda *args:{
        'prepared':{'eligible':torch.ones(3),'offsets':[(0,1),(1,2),(2,3)],
                    'prompt_start':0,'prompt':'abc'},
        'hidden':torch.zeros(3,4)})
    output=tmp_path/'output'
    report=exp.run(root='root',transfer_report='transfer',fresh_run='fresh',null_run='null',
        validation_run='validation',action_run='action',ranking_run='ranking',
        width_run='width',conflict_run='conflict',output=output,device='cpu')
    assert report['metrics'][CONTROL]==summaries[CONTROL]
    assert report['metrics'][CHALLENGER]==summaries[CHALLENGER]
    assert report['metrics']['selective_score_retention_095']==summaries[CONTROL]
    assert report['metrics']['selective_score_retention_085']==summaries[CHALLENGER]
    assert report['g2_unlocked'] is False and report['production_promotion'] is False
    assert report['candidate_for_new_independent_validation'] is None
    assert len(exp._read_jsonl(output/'selective_conflict_cases.jsonl'))==320
    assert len(exp._read_jsonl(output/'selective_conflict_decisions.jsonl'))==320*len(exp.RULES)
    with pytest.raises(FileExistsError,match='IMMUTABLE_OUTPUT'):
        exp.run(root='root',transfer_report='transfer',fresh_run='fresh',null_run='null',
            validation_run='validation',action_run='action',ranking_run='ranking',
            width_run='width',conflict_run='conflict',output=output,device='cpu')
