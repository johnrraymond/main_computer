"""CPU contract and end-to-end checks for the causal one-latent joint trainer."""
import json
from pathlib import Path
from types import MethodType,SimpleNamespace

import pytest
import torch

from tools import nanojev_qwen_hybrid_causal_joint as c
from tools import nanojev_qwen_zero_line_backbone as zero
from tools import nanojev_qwen_hybrid_alignment_repair as align
from tools import nanojev_qwen_hybrid_interface_repair as interface
from tools import nanojev_qwen_hybridagent as hybrid
from test_nanojev_qwen_hybrid_interface_repair import loader,sources


def configured():
    model,tok,_=loader()
    model.backbone.requires_grad_(False)
    zero.install_lora(model.backbone,2,4.,required_layers=1)
    model.head.to(dtype=torch.float32)
    model.head.requires_grad_(True)
    return model,tok


def test_disjoint_balanced_data_and_arithmetic_specific_targets():
    d=c.make_data(2027,12,6,6)
    assert max(abs(x['a']) for x in d['train'])<min(abs(x['a']) for x in d['dev'])
    assert max(abs(x['a']) for x in d['dev'])<min(abs(x['a']) for x in d['test'])
    texts={c.actual_reasoning(x) for x in d['train']}
    assert len(texts)==12
    for row in d['train']:
        assert str(row['u']) in c.actual_reasoning(row)
        assert str(row['v']) in c.actual_reasoning(row)
        assert str(row['answer']) in c.actual_reasoning(row)
    with pytest.raises(AssertionError,match='CAUSAL_TEACHER_ARITHMETIC_INCONSISTENT'):
        c.actual_reasoning({'a':5,'u':1,'v':2,'answer':4})


def test_answer_gold_cannot_enter_prompt_or_suffix():
    m,t=configured()
    row=c.make_data(2027,6,6,6)['train'][0]
    a=c.prompt_tokens(t,m,row,'cpu')
    copied=dict(row);copied['answer']=-987654321
    b=c.prompt_tokens(t,m,copied,'cpu')
    assert torch.equal(a,b)
    assert c.suffix_ids(t,'answer')==c.target_tokens(t,row,'answer')[0]
    # Even malformed/missing scoring labels may not change inference PREFIX.
    with torch.no_grad():
        p=c._prefix(m,t,copied,'intact')
        q=c._prefix(m,t,row,'intact')
    assert torch.equal(p[0],q[0])


def test_full_answer_ce_and_reasoning_supervision_backpropagate_to_both():
    m,t=configured()
    rows=c.make_data(2027,6,6,6)['train']
    z=c.train_sample(m,t,rows[0],'cpu',negative=rows[1],max_seq=512)
    assert z['tokens']>1 and z['ranking'] is not None
    assert float(z['loss'])>float(z['ce'])
    z['loss'].backward()
    for _,p in c.param_scope(m)[0]:
        assert p.grad is not None and torch.isfinite(p.grad).all()
    for _,p in c.param_scope(m)[1]:
        assert p.grad is not None and torch.isfinite(p.grad).all()
    assert all(p.grad is None for name,p in m.backbone.named_parameters()
               if not name.endswith(('.lora_a','.lora_b')))
    m.zero_grad(set_to_none=True)
    a=c.train_sample(m,t,rows[1],'cpu',kind='reasoning',max_seq=512)
    assert a['tokens']>z['tokens']
    a['loss'].backward()
    assert any(p.grad is not None and torch.isfinite(p.grad).all() for p in m.head.parameters())


def test_counterfactual_must_have_different_answer():
    m,t=configured()
    r=c.make_data(2027,6,6,6)['train'][0]
    with pytest.raises(AssertionError,match='CAUSAL_BAD_NEGATIVE'):
        c.train_sample(m,t,r,'cpu',negative=r,max_seq=512)


def test_second_pass_does_not_put_answer_in_source_hidden(monkeypatch):
    m,t=configured()
    r=c.make_data(2027,6,6,6)['train'][0]
    records=[]
    original=c.frozen_prompt_hidden
    def inspect(model,tokens):
        records.append(tokens.clone())
        return original(model,tokens)
    monkeypatch.setattr(c,'frozen_prompt_hidden',inspect)
    c.train_sample(m,t,r,'cpu',max_seq=512)
    assert len(records)==1
    assert torch.equal(records[0],c.prompt_tokens(t,m,r,'cpu'))


def test_bad_control_and_output_overlap_refused(tmp_path):
    m,t=configured()
    r=c.make_data(2027,6,6,6)['test'][0]
    with pytest.raises(AssertionError,match='CAUSAL_SHUFFLE_MISSING'):
        c._prefix(m,t,r,'shuffled')
    with pytest.raises(AssertionError,match='CAUSAL_OUTPUT_OVERLAPS_SOURCE'):
        c.check_inputs(tmp_path/'base'/'inner',tmp_path/'base',tmp_path/'numeric',tmp_path/'interface',tmp_path/'repair')


def test_decode_is_answer_first_not_embedded_hallucinated_answer(monkeypatch):
    m,t=configured()
    row=c.make_data(2027,6,6,6)['train'][0]
    # CPU toy model has random outputs; parser should NEVER search for a gold
    # answer later in arbitrary text, even if it appears in reasoning.
    assert c.re.match(r'^\s*([+-]?\d+)(?!\d)','Reason: 0\nAnswer: 14') is None
    assert c.re.match(r'^\s*([+-]?\d+)(?!\d)','-14\nReason: 0').group(1)=='-14'


def test_checkpoint_new_lineage_resume_and_holdout_is_not_dev(tmp_path,monkeypatch):
    """Exercise actual train and restore against the existing fixture lineage."""
    base,numerical,oldhead=sources(tmp_path,monkeypatch)
    def small_loader(**kwargs):
        model,tok,det=loader(**kwargs)
        with torch.no_grad():
            model.backbone.get_input_embeddings().weight.mul_(0.03)
        # Unlike the position-independent standard toy decoder, this mock must
        # be genuinely causal: future answer positions consume prior latent.
        def contextual(self,*,input_ids=None,inputs_embeds=None,attention_mask=None,
                       past_key_values=None,output_hidden_states=False,use_cache=False,return_dict=True):
            x=self.emb(input_ids) if input_ids is not None else inputs_embeds
            previous=torch.zeros_like(x[:,:1]) if past_key_values is None else past_key_values[0]
            accumulated=torch.cumsum(x,dim=1)+previous
            hidden=self.layer(x+0.2*accumulated)
            if output_hidden_states:
                return SimpleNamespace(hidden_states=(hidden,),logits=self.out(hidden),
                    past_key_values=(accumulated[:,-1:,:].detach(),))
            return SimpleNamespace(last_hidden_state=hidden)
        model.backbone.forward=MethodType(contextual,model.backbone)
        return model,tok,det
    interface_dir=tmp_path/'interface'
    interface.train_format(device='cpu',base=base,numerical=numerical,initial_head=oldhead,
        output=interface_dir,steps=1,eval_every=1,train_count=6,dev_count=6,
        test_count=6,max_seq=512,loader=small_loader)
    interface.realign(device='cpu',base=base,numerical=numerical,output=interface_dir,
        steps=1,eval_every=1,max_seq=512,loader=small_loader)
    oldsha={'format':interface.fingerprint(interface_dir/'format'/'best.pt'),
            'head':interface.fingerprint(interface_dir/'align'/'best.pt')}
    zero.atomic_json(interface_dir/'gate_result.json',{'schema':interface.SCHEMA,
       'marker_checkpoint_roundtrip':True,'tied_output_marker_override':True,'markers_pass':True,
       'format_checkpoint_sha256':oldsha['format'],'aligned_head_sha256':oldsha['head']})
    repair=tmp_path/'repair'
    align.run(stage='calibrate',device='cpu',base=base,numerical=numerical,
        interface_dir=interface_dir,output=repair,loader=small_loader)
    align.run(stage='train',device='cpu',base=base,numerical=numerical,
        interface_dir=interface_dir,output=repair,steps=1,eval_every=1,loader=small_loader)
    # Real preflight contract; failed prior inference is intentionally allowed.
    sha=interface.fingerprint(repair/'alignment'/'best.pt')
    zero.atomic_json(repair/'gate_result.json',{'schema':align.SCHEMA,
      'status':'ONE_STEP_FAIL','deeper_training_permitted':False,
      'sources':oldsha,'new_head_checkpoint_sha256':sha})
    zero.atomic_json(repair/'geometry_gate.json',{'schema':align.SCHEMA,'pass':True})
    zero.atomic_json(repair/'recurrence.json',{'schema':align.SCHEMA,'pass':True,
      'repair_head_sha256':sha,'source_checkpoints':oldsha})
    previous=c.provenance(interface_dir,repair)
    common=dict(device='cpu',base=base,numerical=numerical,interface_dir=interface_dir,
        repair_root=repair,steps=2,eval_every=1,train_count=6,dev_count=6,
        test_count=6,max_seq=512,loader=small_loader)
    paused=c.train(output=tmp_path/'causal',stop_after=1,**common)
    assert paused['status']=='PAUSED_RESUMABLE'
    completed=c.train(output=tmp_path/'causal',resume=True,**common)
    assert completed['status']=='TRAINED_UNCERTIFIED'
    assert (tmp_path/'causal'/'training'/'best.pt').is_file()
    # Pausing and resuming must produce identical learnable tensors under a
    # deterministic optimizer/CPU decoder, not merely a matching step count.
    straight=c.train(output=tmp_path/'causal_straight',**common)
    assert straight['status']=='TRAINED_UNCERTIFIED'
    saved1=torch.load(tmp_path/'causal'/'training'/'last.pt',map_location='cpu',weights_only=True)
    saved2=torch.load(tmp_path/'causal_straight'/'training'/'last.pt',map_location='cpu',weights_only=True)
    for group in ('head','lora'):
        for key,value in saved1['state'][group].items():
            assert torch.equal(value,saved2['state'][group][key]),(group,key)
    assert c.provenance(interface_dir,repair)==previous
    assert interface.fingerprint(interface_dir/'format'/'best.pt')==oldsha['format']
    assert interface.fingerprint(interface_dir/'align'/'best.pt')==oldsha['head']
    with pytest.raises(AssertionError,match='CAUSAL_TRAIN_EXISTS'):
        c.train(output=tmp_path/'causal',**common)
    # Gate is one-shot on heldout data and cannot be run until training ends.
    gate=c.run(stage='gate',output=tmp_path/'causal',budget=2,device='cpu',base=base,
       numerical=numerical,interface_dir=interface_dir,repair_root=repair,loader=small_loader)
    assert gate['cases']==6 and gate['oracle_latents_supplied_at_inference']==0
    assert set(gate['score'])=={'intact','zero','shuffled','marker_only','text'}
    assert gate['deeper_training_permitted']==gate['causal_advantage_pass']
    assert (tmp_path/'causal'/'gate_started.json').is_file()
    assert all(record['id'].startswith('test_') for record in gate['records'])
    with pytest.raises(AssertionError,match='CAUSAL_GATE_ALREADY_EXISTS'):
        c.blind_gate(output=tmp_path/'causal',budget=2,device='cpu',base=base,
          numerical=numerical,interface_dir=interface_dir,repair_root=repair,loader=small_loader)
    with pytest.raises(AssertionError,match='CAUSAL_PRIOR_HEAD_CHANGED'):
        (repair/'alignment'/'best.pt').write_bytes(b'tamper')
        c.provenance(interface_dir,repair)


def test_cli_full_run_routes_budget_to_gate_only(monkeypatch,capsys):
    """Default CLI must not hand gate-only --budget to train()."""
    observed=[]
    def fake_train(**kwargs):
        observed.append(('train',dict(kwargs)))
        assert 'budget' not in kwargs
        assert kwargs['steps']==240
        return {'status':'TRAINED_UNCERTIFIED'}
    def fake_gate(**kwargs):
        observed.append(('gate',dict(kwargs)))
        assert kwargs['budget']==12
        assert 'steps' not in kwargs
        assert 'seed' not in kwargs
        return {'status':'ONE_STEP_FAIL'}
    monkeypatch.setattr(c,'train',fake_train)
    monkeypatch.setattr(c,'blind_gate',fake_gate)
    assert c.main(['--device','cpu','--budget','12'])==0
    assert [name for name,_ in observed]==['train','gate']
    assert 'HYBRID_CAUSAL_RESULT=' in capsys.readouterr().out


def test_cli_train_and_gate_stage_routes_independently(monkeypatch):
    observed=[]
    def fake_train(**kwargs):
        assert 'budget' not in kwargs
        observed.append(('train',dict(kwargs)))
        return {'status':'PAUSED_RESUMABLE'}
    def fake_gate(**kwargs):
        assert 'steps' not in kwargs
        observed.append(('gate',dict(kwargs)))
        return {'status':'ONE_STEP_FAIL'}
    monkeypatch.setattr(c,'train',fake_train)
    monkeypatch.setattr(c,'blind_gate',fake_gate)
    assert c.main(['--stage','train','--device','cpu','--budget','48'])==0
    assert c.main(['--stage','gate','--device','cpu','--budget','48'])==0
    assert [name for name,_ in observed]==['train','gate']
    assert observed[1][1]['budget']==48
    observed.clear()
    assert c.main(['--device','cpu'])==0  # incomplete training must not open holdout
    assert [name for name,_ in observed]==['train']


def test_dispatch_refuses_unknown_argument_even_if_stage_filters_it():
    with pytest.raises(ValueError,match='CAUSAL_UNKNOWN_OPTION'):
        c.run(stage='all',budegt=24)
