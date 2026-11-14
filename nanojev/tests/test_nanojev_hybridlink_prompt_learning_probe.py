"""CPU-only regression of HybridLink prompt-vs-learning question geometry."""
import json
import subprocess
import sys

import pytest
from tools import nanojev_hybridlink_prompt_learning_probe as probe


def test_rung_names():
    assert len(probe.RUNGS)==16
    assert probe.RUNGS[0]=='Z0' and probe.RUNGS[-1]=='T08'


@pytest.mark.parametrize('rung',probe.RUNGS)
@pytest.mark.parametrize('delta',(-4,0,4))
def test_expected_signed_difference(rung,delta):
    for style in ('direct','scaffold'):
        p,a=probe.question(rung,8,delta,style)
        assert a==delta
        assert p.endswith('\nReasoning: ')
        assert '\nResponse:' not in p and '\nMethod:' not in p
        assert ('\nInstruction:' in p) == (style == 'scaffold')
        assert 'Answer: '+str(a) not in p



@pytest.mark.parametrize('rung',probe.RUNGS)
def test_prompt_variants_change_only_instruction_and_keep_trained_suffix(rung):
    direct,target=probe.question(rung,16,-4,'direct')
    scaffold,target2=probe.question(rung,16,-4,'scaffold')
    assert target==target2==-4
    prefix_direct,marker=direct.rsplit('\nReasoning: ',1)
    assert marker==''
    prefix_scaffold,marker2=scaffold.rsplit('\nReasoning: ',1)
    assert marker2==''
    assert scaffold.startswith(prefix_direct+'\nInstruction: ')
    assert scaffold.endswith('\nReasoning: ')
    assert 'Answer: -4' not in scaffold


@pytest.mark.parametrize('completion,expected,kind,clean,extra_question',[
    ('8 + (-9) = -1.\nAnswer: -1\n',-1,'answer_label',True,False),
    ('8 + (-9) = -1.\nAnswer: -1\nQuestion: Invent another question.',-1,'answer_label',False,True),
    ('Question: 2+2?\nAnswer: 4',None,'none',False,True),
    ('Answer: -1\nAnswer: 88',-1,'answer_label',False,False),
    ('-1\n',-1,'leading_integer',True,False),
    ('8 + (-9) = -1.',None,'none',False,False),
    ('Answer: -1 with further content',-1,'answer_label',False,False),
])
def test_parse_first_completed_answer_independent_of_format(completion,expected,kind,clean,extra_question):
    r=probe.parse_completion(completion)
    assert r['first_completed_answer']==expected
    assert r['first_answer_kind']==kind
    assert r['single_answer_terminated'] is clean
    assert r['generated_new_question'] is extra_question
    assert r['multiple_answer_labels'] == ('Answer: 88' in completion)


def test_generated_next_question_answer_cannot_get_credit():
    r=probe.parse_completion('The model gave no answer.\nQuestion: next\nAnswer: 4')
    assert r['first_completed_answer'] is None
    assert r['strict_answer'] is None
    assert 'generated_new_question' in r['format_violations']
    assert 'missing_completed_answer' in r['format_violations']


def test_truncated_completion_does_not_get_clean_termination_credit():
    r=probe.parse_completion('Answer: 4'+(' '*291))
    assert r['first_completed_answer']==4
    assert not r['single_answer_terminated']
    assert r['completion_window_limit_reached']


def test_exhausted_generation_budget_is_not_clean_termination():
    limited=probe.parse_completion('Answer: 4',budget_exhausted=True)
    assert limited['first_completed_answer']==4
    assert limited['generation_token_budget_reached']
    assert 'generation_token_budget_reached' in limited['format_violations']
    assert not limited['single_answer_terminated']
    assert probe.parse_completion('Answer: 4',budget_exhausted=False)['single_answer_terminated']


def test_first_answer_and_termination_score_separately():
    rows=[]
    for response in ('Answer: -1\nQuestion: continued','Answer: -1','Not an answer'):
        rows.append({'configuration':'format','id':response,'rung':'Z0','style':'direct',
                     'depth':0,'control':'intact','sign':'negative','expected':-1,
                     **probe.parse_completion(response)})
    score=probe.summarize(rows)[0]
    assert score['n']==3
    assert score['first_completed_correct']==2
    assert score['terminated_correct']==1
    assert score['generated_new_question_count']==1


def test_scaled_hawk_dove_exact_invariant():
    for anchor in (8,32,2048,65536,4_000_000):
        for delta in (-4,-1,0,1,4):
            a,b,hh,hd,dh,dd,x,y=probe._payoffs(anchor,delta,1)
            assert x==a*hh+b*hd and y==a*dh+b*dd and x-y==delta


def test_r2_counterfactual_same_operand_numbers_opposite_answers():
    p1,a1=probe.question('R2',16,4,'direct',reverse=False)
    p2,a2=probe.question('R2',16,4,'direct',reverse=True)
    assert a1==4 and a2==-4
    for s in ('16','20'):assert s in p1 and s in p2


def test_split_style_geometry():
    rows=probe.make_rows(('Z0','R2','T08'),0,4,(8,32),('direct','scaffold'),71)
    assert len(rows)==76
    assert all(r['anchor']>=2*abs(r['delta']) for r in rows)
    assert {'positive','negative','zero'}==set(r['sign'] for r in rows)
    assert all(r['style'] in ('direct','scaffold') for r in rows)
    assert len(set((r['id'],r['style']) for r in rows))==len(rows)
    assert rows==probe.make_rows(('Z0','R2','T08'),0,4,(8,32),('direct','scaffold'),71)


def test_oversized_or_malformed_radius_fails():
    with pytest.raises(AssertionError):probe.make_rows(('Z0',),5,16,(16,),('direct',),71)
    with pytest.raises(AssertionError):probe.make_rows(('Z0',),0,4,(4_000_001,),('direct',),71)
    with pytest.raises(AssertionError):probe.make_rows(('BAD',),0,4,(8,),('direct',),71)


def test_paired_comparison_rescues_regressions():
    records=[]
    for style,depth,control,answer in (
        ('direct',0,'text_only',None),('direct',0,'marker_only',4),
        ('scaffold',0,'text_only',4),('scaffold',0,'marker_only',4),
        ('direct',1,'intact',4),('direct',1,'zero_latent',None),
        ('direct',2,'intact',None),('direct',2,'zero_latent',4),
        ('direct',4,'intact',4),('direct',4,'zero_latent',None),
        ('scaffold',1,'intact',4),('scaffold',1,'zero_latent',None),
        ('scaffold',2,'intact',None),('scaffold',2,'zero_latent',4),
        ('scaffold',4,'intact',4),('scaffold',4,'zero_latent',None)):
        records.append({'configuration':'format','id':'a','rung':'Z0','style':style,
                        'depth':depth,'control':control,'expected':4,
                        'sign':'positive',**probe.parse_completion('Answer: '+str(answer) if answer is not None else '???')})
    result=probe.paired_comparisons(records)
    assert len(result)>15
    def get(name,style='direct'):
        return next(r for r in result if r['comparison']==name and r['style']==style)
    assert get('marker_only_vs_text_only')['a_only']==1
    assert get('intact_1_vs_zero_1')['a_only']==1
    assert get('intact_2_vs_zero_2')['b_only']==1
    assert get('intact_4_vs_zero_4')['a_only']==1
    assert get('scaffold_vs_direct_text_only','paired_styles')['a_only']==1
    assert probe.summarize(records)


@pytest.mark.parametrize('depth,control',[(0,'text_only'),(0,'marker_only'),
    (1,'zero_latent'),(1,'intact'),(2,'zero_latent'),(2,'intact'),
    (4,'zero_latent'),(4,'intact')])
def test_all_expected_inference_conditions(depth,control):
    assert (depth,control) in probe.inference_conditions((0,1,2,4))


def test_bad_inference_depths_rejected():
    with pytest.raises(AssertionError):probe.inference_conditions((1,2,4))
    with pytest.raises(AssertionError):probe.inference_conditions((0,1,1,4))
    with pytest.raises(AssertionError):probe.inference_conditions((0,3))


def test_marker_only_uses_actual_tokens_and_zero_latent_preserves_depth(monkeypatch):
    import torch
    from tools import nanojev_qwen_hybridagent_paired_diagnostic as paired
    calls=[]
    def fake_decode(model,tokenizer,ids,depth,budget,*,corrupt=False):
        calls.append((ids.tolist(),depth,budget,corrupt))
        return {'completion':'Answer: 0','tokens':3}
    monkeypatch.setattr(paired,'decode_once',fake_decode)
    class Markers:
        start_id=40001
        end_id=40002
    ids=torch.tensor([[10,20,30]],dtype=torch.long)
    for depth,control in probe.inference_conditions((0,1,2,4)):
        probe.decode_condition(Markers(),None,ids,depth,control,48)
    assert len(calls)==8
    assert calls[0]==([[10,20,30]],0,48,False)  # No marker, no added positions.
    assert calls[1]==([[10,20,30,40001,40002]],0,48,False)  # Literal only.
    assert calls[2]==([[10,20,30]],1,48,True)   # Same markers/step as learned.
    assert calls[3]==([[10,20,30]],1,48,False)
    assert calls[4]==([[10,20,30]],2,48,True)
    assert calls[5]==([[10,20,30]],2,48,False)
    assert calls[6]==([[10,20,30]],4,48,True)
    assert calls[7]==([[10,20,30]],4,48,False)
    assert ids.tolist()==[[10,20,30]]  # Input tensor is immutable.


def test_marker_only_invalid_collision_refused():
    import torch
    class Bad:
        start_id=13
        end_id=13
    with pytest.raises(AssertionError,match='BAD_MARKERS'):
        probe.decode_condition(Bad(),None,torch.tensor([[3]]),0,'marker_only',48)


def test_preview_does_not_load_qwen(capsys):
    assert probe.main(['--stage','preview','--rungs','Z0,T08','--band','0-4','--scales','8'])==0
    output=capsys.readouterr().out
    preview=json.loads(output.split('=',1)[1]);assert preview['model_loaded'] is False
    assert preview['rows']==20


def test_read_only_preflight_refuses_output_overlap(tmp_path):
    directory=tmp_path/'source'
    directory.mkdir()
    with pytest.raises(AssertionError,match='OUTPUT_OVERLAPS_SOURCE'):
        probe.run(device='cpu',baseline=directory,output=directory)


def test_fake_end_to_end_read_only_matched_arms(tmp_path,monkeypatch):
    from tools import nanojev_qwen_hybridagent_paired_diagnostic as paired
    from tools import nanojev_qwen_zero_line_backbone as zero
    from tools import nanojev_qwen_hybridagent_activation as activation

    class FakeBackbone:
        pass
    class FakeHead:
        def load_state_dict(self,*args,**kwargs):return None
    class FakeModel:
        backbone=FakeBackbone()
        head=FakeHead()
        start_id=10101
        end_id=10102
    class FakeTokenizer:
        def __call__(self,*a,**kw):return {'input_ids':[11,12]}
    monkeypatch.setattr(probe,'_source_setup',lambda *a,**kw: (
        FakeModel(),FakeTokenizer(),[('old',{},{}),('format',{}, {})],[],{},'stable'))
    monkeypatch.setattr(zero,'restore_lora',lambda *a,**kw:None)
    monkeypatch.setattr(zero,'frozen_backbone_sha256',lambda *a,**kw:'stable')
    monkeypatch.setattr(paired,'decode_once',lambda *a,**kw:{
        'answer_label':0,'leading_integer':0,'completion':'Answer: 0','tokens':3})
    out=tmp_path/'new_report'
    result=probe.run(device='cpu',rungs=('Z0',),scales=(8,),
        baseline=tmp_path/'base',backbone=tmp_path/'backbone',
        activation_dir=tmp_path/'activation',format_dir=tmp_path/'format',
        joint_dir=tmp_path/'joint',output=out)
    assert result['status']=='READ_ONLY_DIAGNOSTIC_NOT_CERTIFIED'
    assert result['optimizer_steps']==0 and result['test_evaluated'] is False
    assert result['questions']==10
    assert result['inference_records']==160  # 10 prompts * 2 configs * 8 conditions
    stored=json.loads((out/'result.json').read_text())
    assert len(stored['records'])==160
    assert any(x['comparison']=='intact_4_vs_zero_4' for x in stored['summary']['paired_comparisons'])
    assert any(x['comparison']=='marker_only_vs_text_only' for x in stored['summary']['paired_comparisons'])
    assert result['conditions'][0]=={'depth':0,'control':'text_only'}
    assert result['conditions'][1]=={'depth':0,'control':'marker_only'}
    assert len(result['conditions'])==8
    assert all(r['latent_positions']==r['depth'] for r in stored['records'])
    assert stored['summary']['prompt_suffix']=='Reasoning: '
    assert stored['summary']['format_contract'].startswith('trained_prefix_')
    assert all(v['prompt'].endswith('\nReasoning: ') for v in stored['prompt_deck'])
    assert all('first_completed_answer' in v and 'single_answer_terminated' in v for v in stored['records'])
