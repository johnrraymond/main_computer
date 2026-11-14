"""Production Wide inference is unchanged; exact candidate is shadow-only."""
from __future__ import annotations

from dataclasses import replace
from fractions import Fraction
from types import SimpleNamespace
import json

import numpy as np
import pytest

from tools import nanojev_gamer_v1_to_numerical_shadow as sh
from tools import nanojev_gamer_v1_s_payoff_operational_acceptance as s
from tools import nanojev_gamer_v1_exact_belief_lab as exact
from tools import nanojev_gamer_v1_handoff as gamer


def _deck_with_protected():
    fresh,tags=s._operational_deck()
    zero=[i for i,t in enumerate(tags) if t['panel']=='zero_anchor']
    assert len(zero)==48
    old=[];old_tags=[]
    for j,i in enumerate(zero):
        source=fresh[i]
        panel='heldout_zero_anchor' if j<24 else 'challenge_zero_anchor'
        old.append(replace(source,id=f'protected.{j:03d}'))
        if panel == 'challenge_zero_anchor':
            # The real exact-belief challenge uses 'group', not 'profile'.
            # Do not add an artificial profile as the old shadow tests did.
            old_tags.append({'panel':'zero_anchor','origin_panel':panel,
                             'group':panel+'.'+tags[i]['profile'],
                             'surface':tags[i]['surface'],'edit':tags[i]['edit'],
                             'id':old[-1].id})
        else:
            old_tags.append({**tags[i], 'profile':panel+'.'+tags[i]['profile'],
                             'panel':'zero_anchor','origin_panel':panel})
    return fresh+old,tags+old_tags


def _oracle_inputs(deck):
    qs=exact._exact_q(deck)
    s_values=[int(exact._parse_payoffs(case.prompt)[1]) for case in deck]
    pay={k:np.array([int(dict(zip(s.payoff.PAYOFFS,exact._parse_payoffs(case.prompt)))[k])
         for case in deck],dtype=np.int64) for k in ('R','T','P')}
    wide=np.array([float(c.target) for c in deck],dtype=np.float64)
    return qs,s_values,pay,wide


def test_end_to_end_shadow_scores_all_32_strict_zero_triples():
    deck,tags=_deck_with_protected()
    q,sv,pay,wide=_oracle_inputs(deck)
    result=sh._score(deck,tags,q,sv,pay,wide)
    assert result['candidate_passed'] is True
    assert result['zero_triples']=={
        'fresh':16,'heldout':8,'challenge':8,'strict_full_triples':32,'accepted':True}
    assert result['fresh']['s_only_counterfactuals']=={'cases':64,'exact':64}
    assert result['shadow']['wide_exact_1e6']==160
    assert result['shadow']['disagreements']==0
    assert all(r['returned_authority']=='existing_to_numerical_wide' for r in result['case_records'])



def test_shadow_protected_challenge_uses_group_only_tags_without_fresh_s_scorer(monkeypatch):
    deck,tags=_deck_with_protected()
    assert 'profile' in tags[sh.FRESH_COUNT]
    assert 'group' in tags[sh.FRESH_COUNT+24]
    assert 'profile' not in tags[sh.FRESH_COUNT+24]
    calls=[]
    original=s._score_live
    def fresh_only(deck,tags,qhat,shat,pay):
        calls.append(len(deck))
        assert len(deck)==sh.FRESH_COUNT
        assert all('profile' in tag for tag in tags)
        return original(deck,tags,qhat,shat,pay)
    monkeypatch.setattr(s,'_score_live',fresh_only)
    q,sv,pay,wide=_oracle_inputs(deck)
    result=sh._score(deck,tags,q,sv,pay,wide)
    assert calls==[112]
    assert result['protected']['cases']==48
    assert result['protected']['s_only_counterfactuals']=={'cases':0,'exact':0}
    assert result['protected']['strict_zero_triples']['strict_exact_full_triples']==16
    assert result['protected_zero_triples']['heldout']['accepted']
    assert result['protected_zero_triples']['challenge']['accepted']
    assert result['candidate_passed']


def test_group_only_challenge_wrong_neighbor_still_blocks_all_32_triples():
    deck,tags=_deck_with_protected()
    q,sv,pay,wide=_oracle_inputs(deck)
    q[sh.FRESH_COUNT+24]+=Fraction(1,5700)
    result=sh._score(deck,tags,q,sv,pay,wide)
    assert result['fresh']['passed']
    assert result['protected']['strict_zero_triples']['strict_exact_full_triples']==15
    assert result['zero_triples']['strict_full_triples']==31
    assert result['protected_zero_triples']['heldout']['accepted']
    assert not result['protected_zero_triples']['challenge']['accepted']
    assert not result['candidate_passed']


def test_protected_mixed_tag_panel_missing_or_wrong_origin_rejected():
    deck,tags=_deck_with_protected()
    q,sv,pay,wide=_oracle_inputs(deck)
    tags[sh.FRESH_COUNT+24] = {**tags[sh.FRESH_COUNT+24],
                                'origin_panel':'wrong_origin'}
    with pytest.raises(ValueError,match='PROTECTED_ZERO_PANEL_INCOMPLETE'):
        sh._score(deck,tags,q,sv,pay,wide)


def test_protected_deck_preserves_original_profile_and_group_tag_schemas(monkeypatch):
    deck,tags=_deck_with_protected()
    source={'source':{'underlying':{'composition':object()}}}
    monkeypatch.setattr(sh.positional,'_deck',lambda _src:(
        deck[sh.FRESH_COUNT:],tags[sh.FRESH_COUNT:],
        {'heldout_zero_anchor':list(range(24)),
         'challenge_zero_anchor':list(range(24,48))},None))
    selected,selected_tags=sh._protected_deck(source)
    assert len(selected)==len(selected_tags)==48
    assert all('profile' in t for t in selected_tags[:24])
    assert all('group' in t and 'profile' not in t for t in selected_tags[24:])
    assert {t['origin_panel'] for t in selected_tags}=={
        'heldout_zero_anchor','challenge_zero_anchor'}



def test_separate_adapter_shadow_run_writes_report_with_real_protected_tag_shapes(tmp_path,monkeypatch):
    """Exercise the previously failing post-CUDA scoring/report path end to end."""
    import tools.nanojev_two_game_spine_smoke as adapter_tools
    deck,tags=_deck_with_protected()
    qs,ss,pay,wide_outputs=_oracle_inputs(deck)
    prod=tmp_path/'production';prod.mkdir()
    adapter=prod/'staircase'/'head_cycle_0002'/'lora'/'best_adapter'
    adapter.mkdir(parents=True)
    wide_path=adapter.parent.parent/'to_numerical'/'wide.pt'
    wide_path.parent.mkdir()
    wide_path.write_bytes(b'untouched frozen Wide')
    frozen=tmp_path/'candidate';frozen.mkdir()
    closure=tmp_path/'closure';closure.mkdir()
    shared=tmp_path/'shared';shared.mkdir()
    closure_report=closure/'final_report.json';closure_report.write_text('{}')
    s_file=closure/'s-diagnostic.npz';s_file.write_bytes(b'immutable S')
    payload='a'*64;wide_sha='b'*64
    src={'root':closure,'report':{'frozen_lora_sha256':payload},
         'report_path':closure_report,'report_sha256':sh.token._sha(closure_report),
         's_path':s_file,'s_sha256':sh.token._sha(s_file),
         'source':{'root':shared,'digit_model':object()}}
    monkeypatch.setattr(sh,'_source',lambda *_:src)
    monkeypatch.setattr(sh.s_lab,'_model_inputs',lambda *_:('model',frozen,{'max_length':1024}))
    monkeypatch.setattr(sh.stress,'_verify_snapshot',lambda *_:payload)
    monkeypatch.setattr(sh,'_resolve_adapter',lambda *args:(adapter,wide_sha,
        'SEPARATE_FROZEN_ADAPTER_FORWARD_PASSES'))
    monkeypatch.setattr(sh,'_wide',lambda *args:{'path':wide_path,
        'sha256':sh.gamer.sha_bytes(wide_path.read_bytes()),
        'state':{'dataset_sha256':'d'*64}})
    monkeypatch.setattr(sh,'_verify_unchanged',lambda *args:None)
    monkeypatch.setattr(sh,'_deck',lambda *_:(deck,tags))
    monkeypatch.setattr(sh.gamer,'model_components',lambda *args,**kwargs:(None,object(),object()))
    monkeypatch.setattr(sh.s_lab,'_live_capture',lambda *args,**kwargs:(object(),object()))
    monkeypatch.setattr(sh.s_lab,'_predict_live_groups',lambda *args:(qs,ss))
    monkeypatch.setattr(sh.s_lab,'_new_checkpoint_predictions',lambda *args:pay)
    monkeypatch.setattr(sh,'_wide_original_features',lambda *args:
                         np.zeros((len(deck),1024),dtype=np.float32))
    monkeypatch.setattr(sh,'_wide_predict',lambda *args:wide_outputs)
    monkeypatch.setattr(adapter_tools,'adapter_identity',lambda *_:wide_sha)
    result_dir=tmp_path/'shadow-output'
    args=SimpleNamespace(shadow_closure=closure,shadow_wide=None,
        output=prod,shadow_report=result_dir,shadow_plan_only=False,
        shadow_separate_adapters=True,capture_batch=2,eval_batch=64,device='cpu')
    report=sh.run(args)
    written=json.loads((result_dir/'final_report.json').read_text())
    assert report==written
    assert report['status']=='SHADOW_CANDIDATE_PASS'
    assert report['candidate']['passed'] is True
    assert report['candidate']['protected']['strict_zero_triples']['strict_exact_full_triples']==16
    assert report['candidate']['zero_triples']['strict_full_triples']==32
    assert report['forward_pass_strategy']=='SEPARATE_FROZEN_ADAPTER_FORWARD_PASSES'
    assert report['wide_shadow']['returned_authority']=='existing_to_numerical_wide'
    assert report['gate']['promotion']==sh.PROMOTION
    assert len((result_dir/'shadow_cases.jsonl').read_text().splitlines())==160
    assert wide_path.read_bytes()==b'untouched frozen Wide'


def test_disagreements_do_not_redirect_wide_authority():
    deck,tags=_deck_with_protected()
    q,sv,pay,wide=_oracle_inputs(deck)
    wide[:]=12345.
    result=sh._score(deck,tags,q,sv,pay,wide)
    assert result['candidate_passed'] is True
    assert result['shadow']['disagreements']==160
    assert result['shadow']['wide_exact_1e6']==0
    assert result['shadow']['candidate_was_never_returned']
    assert {x['returned_authority'] for x in result['case_records']}=={'existing_to_numerical_wide'}


def test_one_wrong_neighbor_blocks_entire_shadow_candidate_gate():
    deck,tags=_deck_with_protected()
    q,sv,pay,wide=_oracle_inputs(deck)
    q[112]=q[112]+Fraction(1,5000)
    result=sh._score(deck,tags,q,sv,pay,wide)
    assert not result['candidate_passed']
    assert result['zero_triples']['strict_full_triples']==31
    assert result['shadow']['returned_authority']=='existing_to_numerical_wide'


def test_wrong_s_only_counterfactual_blocks_fresh_shadow():
    deck,tags=_deck_with_protected()
    q,sv,pay,wide=_oracle_inputs(deck)
    i=next(i for i,t in enumerate(tags) if t['panel']=='s_only')
    sv[i]+=1
    result=sh._score(deck,tags,q,sv,pay,wide)
    assert result['fresh']['s_only_counterfactuals']['exact']==63
    assert not result['candidate_passed']


def test_missing_q_never_oracle_substituted():
    deck,tags=_deck_with_protected()
    q,sv,pay,wide=_oracle_inputs(deck)
    q[0]=None
    result=sh._score(deck,tags,q,sv,pay,wide)
    assert result['fresh']['q_exact']<112
    assert not result['candidate_passed']
    assert result['case_records'][0]['candidate_result'] is None


def test_incorrect_s_nonzero_protected_blocks_gate():
    deck,tags=_deck_with_protected()
    q,sv,pay,wide=_oracle_inputs(deck)
    sv[112]+=1
    result=sh._score(deck,tags,q,sv,pay,wide)
    assert result['zero_triples']['strict_full_triples']==31
    assert not result['candidate_passed']


def test_incorrect_R_payoff_blocks_gate():
    deck,tags=_deck_with_protected()
    q,sv,pay,wide=_oracle_inputs(deck)
    pay['R'][112]+=1
    result=sh._score(deck,tags,q,sv,pay,wide)
    assert not result['candidate_passed']


def test_incomplete_wide_checkpoint_requires_correct_adapter(tmp_path,monkeypatch):
    import torch
    filename=tmp_path/'wide.pt';filename.write_bytes(b'test')
    params={'variant':'wide','hidden':[1024,512,128,32],
        'input_width':1024,'backbone_model':'Qwen/Qwen3-0.6B',
        'frozen_adapter_sha256':'A'*64,'dataset_sha256':'B'*64,
        'model':{'weight':torch.zeros(1)}}
    monkeypatch.setattr(torch,'load',lambda *args,**kwargs:params)
    checkpoint=sh._wide(filename,'Qwen/Qwen3-0.6B','A'*64)
    assert checkpoint['state'] is params
    assert checkpoint['path']==filename
    params['frozen_adapter_sha256']='C'*64
    with pytest.raises(ValueError,match='FROZEN_ADAPTER'):
        sh._wide(filename,'Qwen/Qwen3-0.6B','A'*64)
    params['frozen_adapter_sha256']='A'*64
    params['variant']='narrow'
    with pytest.raises(ValueError,match='WRONG_VARIANT'):
        sh._wide(filename,'Qwen/Qwen3-0.6B','A'*64)


def test_missing_selected_wide_refuses_no_checkpoint_fallback(tmp_path):
    with pytest.raises(FileNotFoundError,match='WIDE_PT_MISSING'):
        sh._wide(tmp_path/'latest.pt','Qwen/Qwen3-0.6B','A'*64)


def test_wide_predict_real_legacy_network_cpu():
    import torch
    from tools.nanojev_qwen_c0_compressive_train import make_network,VARIANTS
    net=make_network(torch,8,VARIANTS['wide'])
    state={'model':net.state_dict(),'input_width':8}
    out=sh._wide_predict({'state':state},np.zeros((4,8),dtype=np.float32),'cpu',2)
    assert out.shape==(4,) and np.isfinite(out).all()
    with pytest.raises(ValueError,match='INVALID_PRODUCTION_WIDE_FEATURES'):
        sh._wide_predict({'state':state},np.zeros((4,9),dtype=np.float32),'cpu',2)


def test_handoff_shadow_wires_without_manifest_or_mutation(monkeypatch):
    from tools import nanojev_gamer_v1_to_numerical_shadow as target
    seen=[]
    monkeypatch.setattr(target,'run',lambda args:seen.append((args.output,args.shadow_closure,args.shadow_report)) or {'promotion':sh.PROMOTION})
    result=gamer.main(['shadow','--output','RUN','--shadow-closure','CLOSURE',
                       '--shadow-report','REPORT','--device','cpu'])
    assert result['promotion']==sh.PROMOTION
    assert seen==[('RUN','CLOSURE','REPORT')]


def test_shadow_flags_invalid_for_training_or_probe():
    args=gamer.parser().parse_args(['probe','--output','RUN','--shadow-closure','CLOSURE'])
    with pytest.raises(ValueError,match='SHADOW_FLAGS_ONLY'):
        gamer.validate_flags(args)
    args=gamer.parser().parse_args(['shadow','--output','RUN'])
    with pytest.raises(ValueError,match='REQUIRES_CLOSURE'):
        gamer.validate_flags(args)


def test_same_forward_pass_produces_wide_and_candidate_features(monkeypatch):
    import torch
    from torch import nn
    from tools import nanojev_gamer_v1_sign_information_probe as oracle
    from tools import nanojev_gamer_v1_shared_digit_repair_lab as shared

    class DigitTokenizer:
        padding_side='right'
        def _encode(self,t):
            ids=[50];offsets=[(0,0)]
            for i,c in enumerate(t):
                ids.append(1+int(c) if c.isdigit() else 30)
                offsets.append((i,i+1))
            return ids,offsets
        def encode(self,text,add_special_tokens=True):return self._encode(text)[0]
        def __call__(self,text,**kwargs):
            if isinstance(text,list):
                encoded=[self._encode(x)[0] for x in text]; n=max(map(len,encoded))
                return {'input_ids':torch.tensor([v+[0]*(n-len(v)) for v in encoded]),
                        'attention_mask':torch.tensor([[1]*len(v)+[0]*(n-len(v)) for v in encoded])}
            ids,offsets=self._encode(text)
            return {'input_ids':ids,'offset_mapping':offsets}
    class Core(nn.Module):
        def __init__(self):
            super().__init__()
            self.embed_tokens=nn.Embedding(64,1024)
            self.layers=nn.ModuleList(nn.Identity() for _ in range(10))
            self.norm=nn.Identity()
    class Model(nn.Module):
        def __init__(self):super().__init__();self.model=Core();self.count=0
        def forward(self,input_ids,attention_mask=None,output_hidden_states=False,**kwargs):
            self.count+=1
            h=self.model.embed_tokens(input_ids)
            for layer in self.model.layers:h=layer(h)
            return SimpleNamespace(hidden_states=(h,h) if output_hidden_states else None)
    model=Model();tok=DigitTokenizer()
    deck,_=s._operational_deck()
    groups,views,wide=s._live_capture(model,tok,deck[:3],
                {'curvature':4.,'max_length':1024},'cpu',2,capture_wide=True)
    assert model.count==2
    assert wide.shape==(3,1024) and wide.dtype==np.float32
    assert all(len(groups[k])==3 for k in ('numerator','denominator','S'))
    assert all(v.shape[0]==3 for v in views.values())
    assert np.allclose(wide[0],model.model.embed_tokens(
        torch.tensor([tok.encode(deck[0].prompt)[-1]])).detach().numpy()[0])


def test_original_s_live_capture_signature_still_returns_two_items():
    import inspect
    assert inspect.signature(s._live_capture).parameters['capture_wide'].default is False


def test_source_report_is_guarded_against_non_passed_live_result(tmp_path):
    report={'schema':s.SCHEMA,'status':'COMPLETE_BOUNDED_S_REPAIR_AND_LIVE_ACCEPTANCE',
        'promotion':'BLOCKED_DIAGNOSTIC_ONLY','gate':{'old_s_errors_closed':True,
            'fresh_live_acceptance_passed':False,'bounded_candidate_passed':True}}
    (tmp_path/'final_report.json').write_text(json.dumps(report))
    with pytest.raises(ValueError,match='REQUIRES_COMPLETE'):
        sh._source(tmp_path)


def test_plan_only_performs_preflight_without_model_forward(tmp_path,monkeypatch):
    prod=tmp_path/'production';prod.mkdir()
    closure=tmp_path/'closure';closure.mkdir()
    snapshot=tmp_path/'frozen';snapshot.mkdir()
    p=prod/'to_numerical';p.mkdir()
    wide=p/'wide.pt';wide.write_bytes(b'wide')
    digest='a'*64
    src={'root':closure,'report':{'frozen_lora_sha256':digest},
         'source':{'root':closure/'shared'},'report_path':closure/'final_report.json'}
    monkeypatch.setattr(sh,'_source',lambda *args:src)
    monkeypatch.setattr(sh.s_lab,'_model_inputs',lambda *args:('Qwen/Qwen3-0.6B',snapshot,{}))
    monkeypatch.setattr(sh.stress,'_verify_snapshot',lambda *args:digest)
    monkeypatch.setattr(sh,'_wide',lambda *args:{'path':wide.resolve(),'sha256':'w'*64,'state':{}})
    monkeypatch.setattr(sh,'_verify_unchanged',lambda *args:None)
    monkeypatch.setattr(sh.gamer,'model_components',lambda *args,**kwargs:pytest.fail('should not load model in plan mode'))
    args=SimpleNamespace(shadow_closure=closure,shadow_wide=None,output=prod,
        shadow_report=tmp_path/'shadow-result',shadow_plan_only=True,
        capture_batch=2,eval_batch=64,device='cpu')
    status=sh.run(args)
    assert status['status']=='READY_FOR_IMMUTABLE_PRODUCTION_WIDE_SHADOW'
    assert status['promotion']==sh.PROMOTION
    assert not (tmp_path/'shadow-result').exists()


def test_production_wide_must_be_inside_selected_run(tmp_path,monkeypatch):
    prod=tmp_path/'production';prod.mkdir()
    snapshot=tmp_path/'frozen';snapshot.mkdir()
    wrong=tmp_path/'other';wrong.mkdir();other=wrong/'wide.pt';other.write_bytes(b'x')
    digest='a'*64
    monkeypatch.setattr(sh,'_source',lambda *args:{'root':tmp_path/'closure',
           'report':{'frozen_lora_sha256':digest},'source':{'root':tmp_path/'shared'}})
    monkeypatch.setattr(sh.s_lab,'_model_inputs',lambda *args:('model',snapshot,{}))
    monkeypatch.setattr(sh.stress,'_verify_snapshot',lambda *args:digest)
    monkeypatch.setattr(sh,'_wide',lambda *args:{'path':other.resolve(),'sha256':'w'*64,'state':{}})
    args=SimpleNamespace(shadow_closure=tmp_path/'closure',shadow_wide=other,
          output=prod,shadow_report=tmp_path/'result',shadow_plan_only=True,
          capture_batch=2,eval_batch=64,device='cpu')
    with pytest.raises(ValueError,match='WIDE_NOT_INSIDE'):
        sh.run(args)


def test_strict_integrity_check_rejects_mutated_wide_file(tmp_path,monkeypatch):
    f=tmp_path/'wide.pt';f.write_bytes(b'original')
    monkeypatch.setattr(sh.s_lab,'_verify_immutable',lambda source:None)
    r=tmp_path/'report.json';r.write_text('report')
    sfile=tmp_path/'s.npz';sfile.write_bytes(b'checkpoint')
    src={'source':{},'report_path':r,'report_sha256':sh.token._sha(r),
         's_path':sfile,'s_sha256':sh.token._sha(sfile)}
    wide={'path':f,'sha256':sh.gamer.sha_bytes(f.read_bytes())}
    sh._verify_unchanged(src,wide)
    f.write_bytes(b'changed')
    with pytest.raises(ValueError,match='PROTECTED_CHECKPOINT_MUTATED'):
        sh._verify_unchanged(src,wide)


def test_head_cycle_selection_uses_latest_paired_completed_not_first_recursive(tmp_path):
    root=tmp_path/'run'; st=root/'staircase'
    for name in ('head_cycle_0001','head_cycle_0002'):
        p=st/name/'to_numerical';p.mkdir(parents=True)
        (p/'wide.pt').write_bytes(name.encode())
        (st/name/'lora'/'best_adapter').mkdir(parents=True)
    (root/'noise').mkdir()
    (root/'noise'/'wide.pt').write_bytes(b'do not use')
    assert sh._select_wide(root)==(st/'head_cycle_0002'/'to_numerical'/'wide.pt').resolve()
    with pytest.raises(ValueError,match='NOT_INSIDE_SELECTED'):
        sh._select_wide(root, tmp_path/'elsewhere.pt')
    assert sh._select_wide(root,st/'head_cycle_0001'/'to_numerical'/'wide.pt') == (
        st/'head_cycle_0001'/'to_numerical'/'wide.pt').resolve()


def test_no_completed_cycle_fails_with_exact_required_location(tmp_path):
    (tmp_path/'staircase'/'head_cycle_0002').mkdir(parents=True)
    with pytest.raises(FileNotFoundError,match='NO_COMPLETED_WIDE'):
        sh._select_wide(tmp_path)


def test_different_original_adapter_requires_explicit_dual_mode(tmp_path, monkeypatch):
    from tools.nanojev_two_game_spine_smoke import adapter_identity
    orig=tmp_path/'staircase'/'head_cycle_0002'/'lora'/'best_adapter'
    orig.mkdir(parents=True)
    (orig/'adapter_config.json').write_text('{}')
    (orig/'adapter_model.safetensors').write_bytes(b'unchanged')
    import tools.nanojev_two_game_spine_smoke as wideprobe
    sha=wideprobe.adapter_identity(orig)
    cp=orig.parent.parent/'to_numerical'/'wide.pt';cp.parent.mkdir()
    cp.write_bytes(b'original wide')
    assert sha != 'a'*64
    with pytest.raises(ValueError,match='DIFFERENT_FROZEN_ADAPTERS'):
        sh._resolve_adapter(cp,'a'*64,False)
    p,d,strategy=sh._resolve_adapter(cp,'a'*64,True)
    assert p==orig and d==sha and strategy=='SEPARATE_FROZEN_ADAPTER_FORWARD_PASSES'
    p,d,strategy=sh._resolve_adapter(cp,sha,True)
    assert strategy=='SAME_FROZEN_FORWARD_PASS'


def test_dual_wide_original_capture_uses_original_frozen_adapter_and_original_feature_function(monkeypatch,tmp_path):
    import torch
    adapter=tmp_path/'original';adapter.mkdir()
    deck,_=_deck_with_protected()
    class FakeTok:
        def encode(self,text,add_special_tokens=True):return [1,2,3]
    class FakeModel: pass
    seen=[]
    def get_model(model_id,device,adapter_dir=None):
        seen.append(('model',str(adapter_dir)))
        return torch,FakeTok(),FakeModel()
    def fake_capture(t,model,tok,rows,args):
        seen.append(('capture',len(rows),args.max_length,args.capture_batch))
        return np.zeros((len(rows),1024),dtype=np.float32)
    import tools.nanojev_qwen_c0_compressive_train as c0
    monkeypatch.setattr(sh.gamer,'model_components',get_model)
    monkeypatch.setattr(c0,'capture_features',fake_capture)
    out=sh._wide_original_features('Qwen/Qwen3-0.6B',adapter,deck,'cpu',2,1024)
    assert out.shape==(160,1024)
    assert seen==[('model',str(adapter)),('capture',160,1024,2)]


def test_original_wide_capture_rejects_prompt_truncation(monkeypatch,tmp_path):
    import torch
    folder=tmp_path/'adapter';folder.mkdir()
    class FakeTok:
        def encode(self,text,add_special_tokens=True):return [1]*999
    monkeypatch.setattr(sh.gamer,'model_components',lambda *args,**kwargs:(torch,FakeTok(),object()))
    deck,_=_deck_with_protected()
    with pytest.raises(ValueError,match='TRUNCATION_FORBIDDEN'):
        sh._wide_original_features('model',folder,deck,'cpu',2,100)


def test_cli_rejects_cross_adapter_flag_for_non_shadow_stages():
    args=gamer.parser().parse_args(['probe','--output','RUN','--shadow-separate-adapters'])
    with pytest.raises(ValueError,match='SHADOW_FLAGS_ONLY'):
        gamer.validate_flags(args)


def test_full_shadow_run_two_frozen_adapters_captures_separately_and_reports_truth(tmp_path,monkeypatch):
    from tools import nanojev_gamer_v1_sign_information_probe as oracle
    run=tmp_path/'production'; head_cycle=run/'staircase'/'head_cycle_0002'
    adapter=head_cycle/'lora'/'best_adapter';adapter.mkdir(parents=True)
    (adapter/'adapter_config.json').write_text('{}')
    (adapter/'adapter_model.safetensors').write_bytes(b'orig adapter')
    wide_path=head_cycle/'to_numerical'/'wide.pt';wide_path.parent.mkdir()
    wide_path.write_bytes(b'wide state')
    from tools.nanojev_two_game_spine_smoke import adapter_identity
    wide_sha=adapter_identity(adapter)
    candidate_sha='9'*64
    src={'root':tmp_path/'closure','report':{'frozen_lora_sha256':candidate_sha},
         'source':{'root':tmp_path/'shared','digit_model':'DIGITS'},
         'report_path':tmp_path/'closure'/'final_report.json',
         'report_sha256':'a'*64,'s_path':tmp_path/'closure'/'s.npz','s_sha256':'b'*64}
    src['root'].mkdir();src['report_path'].write_text('{}')
    snapshot=tmp_path/'candidate';snapshot.mkdir()
    monkeypatch.setattr(sh,'_source',lambda *args:src)
    monkeypatch.setattr(sh.s_lab,'_model_inputs',lambda *args:('Qwen/Qwen3-0.6B',snapshot,{'max_length':1024}))
    monkeypatch.setattr(sh.stress,'_verify_snapshot',lambda *args:candidate_sha)
    monkeypatch.setattr(sh,'_wide',lambda path,model,digest:{
        'path':path.resolve(),'state':{'dataset_sha256':'3'*64},'sha256':'4'*64,
        'adapter_sha256':digest})
    monkeypatch.setattr(sh,'_verify_unchanged',lambda *args:None)
    monkeypatch.setattr(sh.gamer,'model_components',lambda *args,**kwargs:(None,'tok','candidate_model'))
    fresh,tags=_deck_with_protected()
    monkeypatch.setattr(sh,'_deck',lambda src:(fresh,tags))
    calls=[]
    def capture(model,tok,deck,cfg,device,batch,*,capture_wide):
        calls.append(('candidate_capture',capture_wide,len(deck)))
        assert capture_wide is False
        return {'numerator':[],'denominator':[],'S':[]}, {'P':np.zeros(160)}
    monkeypatch.setattr(sh.s_lab,'_live_capture',capture)
    monkeypatch.setattr(sh.s_lab,'_predict_live_groups',lambda groups,model:(['q']*160,['s']*160))
    monkeypatch.setattr(sh.s_lab,'_new_checkpoint_predictions',lambda *args:{'R':np.zeros(160),'T':np.zeros(160),'P':np.zeros(160)})
    def orig(model,adapter_dir,deck,device,batch,maxlen):
        calls.append(('wide_original_capture',str(adapter_dir),len(deck)))
        return np.zeros((len(deck),1024),dtype=np.float32)
    monkeypatch.setattr(sh,'_wide_original_features',orig)
    monkeypatch.setattr(sh,'_wide_predict',lambda ck,features,device,batch:np.zeros(len(features)))
    def score(deck,tags,qs,ss,pay,wide):
        calls.append(('score',len(deck),len(wide)))
        return {'candidate_passed':True,'fresh':{'passed':True},'protected':{'passed':True},
             'protected_zero_triples':{},'zero_triples':{'accepted':True,'strict_full_triples':32},
             'shadow':{'disagreements':0},'case_records':[{'case_id':c.id} for c in deck]}
    monkeypatch.setattr(sh,'_score',score)
    args=SimpleNamespace(shadow_closure=src['root'],shadow_wide=None,output=run,
        shadow_separate_adapters=True,shadow_report=tmp_path/'shadow_result',
        shadow_plan_only=False,capture_batch=2,eval_batch=64,device='cpu')
    report=sh.run(args)
    assert calls==[('candidate_capture',False,160),
                   ('wide_original_capture',str(adapter),160),('score',160,160)]
    assert report['forward_pass_strategy']=='SEPARATE_FROZEN_ADAPTER_FORWARD_PASSES'
    assert report['gate']['same_forward_pass_captured'] is False
    assert report['production_output_authority']=='existing_to_numerical_wide'
    assert report['candidate_output_authority'] is False
    assert report['promotion']==sh.PROMOTION
    assert report['wide_adapter_sha256']==wide_sha
    assert report['frozen_lora_sha256']==candidate_sha
    assert (tmp_path/'shadow_result'/'final_report.json').is_file()
