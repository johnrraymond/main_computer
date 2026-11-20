"""G1-0081: fail-closed composite gate, sealed lineage, and causal ablation tests."""
from __future__ import annotations
import copy
import json
from pathlib import Path
from types import SimpleNamespace
import pytest
import torch

from tools import nanojev_gamer_v1_g1_certification_completion_0081 as sut
from tools import nanojev_gamer_v1_g1_certification_audit_0080 as evidence
from tools import nanojev_gamer_v1_g1_quantity_preserving_bakeoff_0079 as quantity
from tools import nanojev_gamer_v1_g1_discrimination_trainability_bakeoff_0076 as discr
from tools import nanojev_gamer_v1_g1_candidate_coverage_bakeoff_0075 as coverage


@pytest.fixture
def gate():
    return evidence.frozen_gate_requirements(Path(evidence.__file__).with_name(
        'nanojev_gamer_v1_graph_curriculum.py'))


def report(n, exact, kind=1., zero=0., component=0.):
    kinds={k:{'recall':kind} for k in sut.KINDS}
    return {'cases':n,'metrics':{'object_exact_rate':exact/n,
       'object_macro_f1':.99,'by_kind':kinds,
       'by_family':{'encounter':{'total':n//2},'allocation':{'total':n//2}},
       'zero_hidden_ablation':{'cases':n,'net_gain':zero},
       'stage_component_ablation':{'cases':n,'net_gain':component}}}


def test_source_gate_thresholds_are_not_weakened(gate):
    assert gate=={'metric':'object_exact_rate','dev':.8,'challenge':.7,
                  'macro_f1':.9,'kind_recall':.82,'neural_gain':.08}
    assert sut.gate_reasons({'dev':report(80,64,zero=.2,component=.2),
                             'challenge':report(32,23,zero=.2,component=.2)},gate)==[]


def test_original_gate_rejects_any_failed_requirement(gate):
    cases={'dev':report(80,63,zero=.2,component=.2),
           'challenge':report(32,23,zero=.2,component=.2)}
    assert 'dev:object_exact_rate_below_threshold' in sut.gate_reasons(cases,gate)
    cases['dev']=report(80,64,kind=.81,zero=.2,component=.2)
    assert 'dev:missing_or_inaccurate_quantity' in sut.gate_reasons(cases,gate)
    cases['dev']=report(80,64,zero=.05,component=.1)
    assert 'dev:neural_causality_not_demonstrated' in sut.gate_reasons(cases,gate)
    cases['dev']=report(80,64,zero=.1,component=.05)
    assert 'dev:stage_component_causality_not_demonstrated' in sut.gate_reasons(cases,gate)
    cases['dev']=report(80,64,zero=.1,component=.1)
    cases['dev']['metrics']['zero_hidden_ablation']['cases']=7
    assert any('missing_causal_ablation' in x for x in sut.gate_reasons(cases,gate))


def test_verified_0080_is_required_and_not_a_native_certificate(gate):
    s={'schema':evidence.SCHEMA,'status':'READ_ONLY_EVIDENCE_VERIFIED_NOT_CERTIFIED',
       'integrity':'PASS','quantity_invariance':'PASS','source_gate_requirements':gate,
       'g1_certified':False,'g2_unlocked':False,'audit_used_to_train_or_select':False,
       'models':{n:{'selected':{'exact_prompts':107,'cases':128,'f1':.993}}
                 for n in sut.MODELS}}
    r={'metrics':{n:{'selected':{'exact_prompts':107,'f1':.993}} for n in sut.MODELS}}
    sut.validate_audit(s,r,gate)
    for edit in ('g1_certified','g2_unlocked','audit_used_to_train_or_select'):
        c=copy.deepcopy(s);c[edit]=True
        with pytest.raises(ValueError,match='UPSTREAM_AUDIT'):
            sut.validate_audit(c,r,gate)
    c=copy.deepcopy(s);c['models']['cutover']['selected']['exact_prompts']=105
    with pytest.raises(ValueError,match='AUDIT'):
        sut.validate_audit(c,r,gate)


def test_real_zero_hidden_and_stage_component_are_distinct_and_frozen():
    class Head(torch.nn.Module):
        def __init__(self):
            super().__init__();self.parameter=torch.nn.Parameter(torch.tensor(2.))
        def forward(self,h,**kwargs):
            return {'span_logits':h*self.parameter,'span_valid':torch.ones(1,2,dtype=torch.bool),
                    'spans':[1]}
    head=Head().eval().requires_grad_(False)
    h=torch.tensor([[[2.,3.]]])
    original=head(h)['span_logits'].detach().clone()
    zero=sut._head_variant(head,'zero_hidden')(h)
    component=sut._head_variant(head,'stage_component')(h)
    assert torch.equal(zero['span_logits'],torch.zeros_like(original))
    assert torch.equal(component['span_logits'],torch.zeros_like(original))
    assert component['spans']==[]
    assert torch.equal(head(h)['span_logits'],original)
    assert not any(x.requires_grad for x in head.parameters())
    with pytest.raises(ValueError,match='UNKNOWN_ABLATION'):
        sut._head_variant(head,'invalid')


def test_evaluation_keeps_gold_out_of_every_inference_path(monkeypatch):
    class TinyHead(torch.nn.Module):
        def forward(self,h,**kwargs):return {'span_logits':h,'span_valid':torch.ones(1,2,dtype=torch.bool)}
    gold={(0,1,'actor'),(1,2,'action'),(2,3,'quantity')}
    observed=[]
    def capture(rows,heads,model,tok,**kwargs):
        assert all(r['object_spans']==[] and 'graph' not in r for r in rows)
        outputs=[]
        for row in rows:
            state=heads['cutover'](torch.ones(1,2),eligible=None,active_stage='G1')
            p=gold if state['span_logits'].abs().sum().item()>0 else set()
            c=SimpleNamespace(gold=set())
            outputs.append(SimpleNamespace(added=SimpleNamespace(case=c),
                    base=SimpleNamespace(case=SimpleNamespace(gold=set())),prediction=p))
        observed.append(len(rows))
        return {'cutover':outputs}
    monkeypatch.setattr(coverage,'_capture',capture)
    monkeypatch.setattr(quantity,'_predict',lambda data,*args,**kwargs:data.prediction)
    rows=[{'prompt':'abc','family':'encounter' if i%2 else 'allocation',
           'object_spans':[{'start':a,'end':b,'kind':k} for a,b,k in sorted(gold)]}
          for i in range(20)]
    result=sut.evaluate_current_interface(rows_by_split={'dev':rows},head=TinyHead(),
        model=None,tok=None,device='cpu',weights={'cutover':()},priors={},
        initial=None,continued=None,trained={},arm=sut.SELECTED_CUTOVER)
    m=result['dev'][0]['metrics']
    assert m['exact_prompts']==20
    assert m['zero_hidden_ablation']['cases']==20
    assert m['zero_hidden_ablation']['net_gain']==1.
    assert m['stage_component_ablation']['net_gain']==1.
    assert observed==[20,20,20]


def test_bundle_reexports_exact_frozen_coefficients(tmp_path,monkeypatch):
    net=discr.Discriminator().eval().requires_grad_(False)
    monkeypatch.setattr(quantity,'_models',lambda a,b,c:(net,net))
    src=tmp_path/'source';src.mkdir()
    (src/'gamer_graph_head.safetensors').write_bytes(b'original immutable model')
    head_sha=evidence.sha(src/'gamer_graph_head.safetensors')
    (src/'manifest.json').write_text(json.dumps({'head_sha256':head_sha}))
    checked=tmp_path/'audit.json';checked.write_text('{}')
    dest=tmp_path/'new-checkpoint'
    record=sut.checkpoint_record({'trained_coefficients':{'cutover':{}}},{},{},
         sut.SELECTED_CUTOVER,src,checked,head_sha,'a'*64,dest,
         candidate_prior='runnerup_joint_gap0p50_bonus0p50',
         structured_reference_weights=((0.,)*coverage.SET_DIM,),
         upstream_hashes={'G1_0076':'a'*64,'G1_0078':'b'*64,'G1_0079':'c'*64})
    assert (record['coefficient_bundles']['quantity_control_interface']['sha256']==
            record['coefficient_bundles']['historical_continued_interface']['sha256'])
    assert evidence.sha(dest/'gamer_graph_head.safetensors')==head_sha
    assert record['extra_candidate_budget']==64
    assert record['candidate_prior']=='runnerup_joint_gap0p50_bonus0p50'
    assert record['g2_unlocked'] is False
    assert record['production_promotion'] is False


def test_output_layout_never_overwrites_evidence(tmp_path):
    paths=sut.relative_defaults(tmp_path)
    assert paths['output'] not in [p for k,p in paths.items() if k!='output']
    assert paths['audit_0080'].name=='oracle-fabric-g1-certification-audit-0080'
    assert paths['output'].name=='oracle-fabric-g1-certification-completion-0081'


def test_real_candidate_builder_decodes_all_three_causal_modes(monkeypatch):
    """Integration smoke exercises 0075 candidate building and 0079 selection."""
    from tools import nanojev_gamer_v1_backbone_first as backbone
    from tools.nanojev_gamer_v1_graph_contract import SPAN_KINDS
    valid=torch.tensor([[i+j<6 for j in range(5)] for i in range(6)],dtype=torch.bool)
    class FakeHead(torch.nn.Module):
        def forward(self,hidden,**kwargs):
            logits=torch.full((6,5,len(SPAN_KINDS)+1),-5.0)
            logits[:,:,0]=0.
            logits[0,0,1]=4.;logits[1,1,2]=4.;logits[3,0,3]=4.
            return {'span_logits':logits*int(hidden.sum()>0),'span_valid':valid}
    monkeypatch.setattr(backbone,'prepare_prompt',lambda tok,prompt,**kwargs:
        {'offsets':[(i,i+1) for i in range(len(prompt))],'prompt_start':0,
         'prompt':prompt,'eligible':torch.tensor([1])})
    monkeypatch.setattr(backbone,'frozen_context',lambda model,prepared,**kwargs:
        torch.ones(6,1))
    net=discr.Discriminator().eval().requires_grad_(False)
    no_change=((0.,)*coverage.SET_DIM,)
    src=[{'prompt':'abcdef','family':'encounter' if i%2 else 'allocation',
          'object_spans':[{'start':0,'end':1,'kind':'actor'},
                          {'start':1,'end':3,'kind':'action'}]}
         for i in range(8)]
    result=sut.evaluate_current_interface(rows_by_split={'dev':src},
         head=FakeHead().eval(),model=None,tok=None,device='cpu',
         weights={'cutover':no_change},
         priors={'cutover':'runnerup_joint_gap0p50_bonus0p50'},
         initial=net,continued=net,trained={},arm=sut.SELECTED_CUTOVER)
    a=result['dev'][0]['metrics']
    assert a['zero_hidden_ablation']['cases']==8
    assert a['stage_component_ablation']['cases']==8
    assert len(result['dev'][1])==8
    assert a['exact_prompts']>=0
    assert isinstance(a['zero_hidden_ablation']['net_gain'],float)


def test_0081_real_quantity_decoder_handles_zero_candidate_causal_replay(monkeypatch):
    """Regression at the 0081 entrypoint: zero candidates must mean zero objects.

    Calls the actual G1-0079 quantity guard and G1-0076 learned scorer, not a
    mock prediction. This is the zero-lattice boundary seen in CUDA ablation.
    """
    from tools import nanojev_gamer_v1_g1_learned_interface_bakeoff_0073 as learned
    from tools import nanojev_gamer_v1_g1_structural_learning_bakeoff_0074 as structural

    def capture(rows, heads, model, tok, **kwargs):
        output=[]
        for row in rows:
            empty=learned.Case('cutover',kwargs['split'],row['family'],
                               'no-candidates',6,[],set(),set(),[])
            encoded=structural.Encoded(empty,[])
            output.append(coverage.Expanded(encoded,encoded,(),(),(),0,0,0,
                                            frozenset()))
        return {'cutover':output}

    monkeypatch.setattr(coverage,'_capture',capture)
    net=discr.Discriminator().eval().requires_grad_(False)
    rows=[{'prompt':'abcdef','family':'allocation' if i%2 else 'encounter',
           'object_spans':[]} for i in range(8)]
    result=sut.evaluate_current_interface(rows_by_split={'dev':rows},head=None,
          model=None,tok=None,device='cpu',
          weights={'cutover':((0.,)*coverage.SET_DIM,)},priors={},
          initial=net,continued=net,trained={},arm=sut.SELECTED_CUTOVER)
    m=result['dev'][0]['metrics']
    assert m['exact_prompts']==8
    assert m['zero_hidden_ablation']['cases']==8
    assert m['stage_component_ablation']['cases']==8
    assert all(not row['selected'] for row in result['dev'][1])
