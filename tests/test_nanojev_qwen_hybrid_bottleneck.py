"""Regression tests for latent gradient provenance, question-hidden channel and gates."""
from types import MethodType,SimpleNamespace
from pathlib import Path

import pytest
import torch

from tools import nanojev_qwen_hybrid_bottleneck as b
from tools import nanojev_qwen_hybrid_causal_joint as joint
from tools import nanojev_qwen_zero_line_backbone as zero
from test_nanojev_qwen_hybrid_causal_joint import configured


def causal_fixture():
    model,tok=configured()
    with torch.no_grad():model.backbone.get_input_embeddings().weight.mul_(0.03)
    def contextual(self,*,input_ids=None,inputs_embeds=None,attention_mask=None,
                 past_key_values=None,output_hidden_states=False,use_cache=False,return_dict=True):
        x=self.emb(input_ids) if input_ids is not None else inputs_embeds
        previous=torch.zeros_like(x[:,:1]) if past_key_values is None else past_key_values[0]
        acc=torch.cumsum(x,dim=1)+previous
        hidden=self.layer(x+0.2*acc)
        if output_hidden_states:
            return SimpleNamespace(hidden_states=(hidden,),logits=self.out(hidden),
                past_key_values=(acc[:,-1:,:].detach(),))
        return SimpleNamespace(last_hidden_state=hidden)
    model.backbone.forward=MethodType(contextual,model.backbone)
    return model,tok


def test_disjoint_balanced_channel_qualification_and_final_holdout():
    data=b.data_splits(2041,12,6,6,6)
    all_rows=[r for k in data for r in data[k]]
    assert len(all_rows)==30
    assert len({r['expression'] for r in all_rows})==30
    for key in ('qualify','test'):
        assert set(r['sign'] for r in data[key])=={'negative','zero','positive'}
        assert set(r['family'] for r in data[key])=={'direct','composed'}
    assert all(r['split']=='test' for r in data['qualify']+data['test'])
    with pytest.raises(AssertionError,match='BOT_BAD_COUNTS'):
        b.data_splits(2041,6,6,5,6)


def test_consumer_fixed_text_contains_no_operands_or_gold():
    m,t=causal_fixture()
    rows=b.data_splits(2041,6,6,6,6)['train']
    p=b.fixed_consumer_ids(m,t,'cpu')
    original=p.clone()
    for row in rows:
        changed=dict(row);changed['expression']='never-shared-secret-expression';changed['answer']=99999
        assert torch.equal(b.fixed_consumer_ids(m,t,'cpu'),original)
        assert not b.CONSUMER_TEXT.count(row['expression'])
        a=b.hidden_from_question(m,t,row,'cpu')
        z1,_=b.consumer_ce(m,t,row,a,max_seq=512)
        z2,_=b.consumer_ce(m,t,changed,a,max_seq=512)
        assert torch.isfinite(z1) and torch.isfinite(z2)
        # Different labels alter teacher-forced loss, but not consumer prefix.
    assert original.shape[1]>1


def test_answer_gradient_enters_latent_head_and_lora():
    m,t=causal_fixture()
    rows=b.data_splits(2041,6,6,6,6)['train']
    probe=b.gradient_probe(m,t,rows,'cpu',512)
    assert probe['pass'],probe
    assert probe['latent_grad_norm']>0
    assert probe['head']['norm']>0
    assert probe['lora']['norm']>0
    assert probe['producer_source_detached']
    assert probe['base_parameters_frozen']
    # A real optimizer objective also includes mismatched and zero controls.
    loss,parts=b.train_one(m,t,rows[0],rows[1],'cpu',512,0.7)
    assert float(loss.detach())>0
    assert parts['ranking']>0 and parts['zero_ce']>=0
    loss.backward()
    assert b.group_norm(joint.param_scope(m)[1])['norm']>0


def test_mismatched_and_zero_controls_are_not_oracle_inputs():
    m,t=causal_fixture()
    rows=b.data_splits(2041,6,6,6,6)['train']
    h=b.hidden_from_question(m,t,rows[0],'cpu')
    wrong=b.hidden_from_question(m,t,rows[1],'cpu')
    for mode in ('intact','zero','shuffled'):
        loss,_=b.consumer_ce(m,t,rows[0],h,condition=mode,wrong_hidden=wrong,max_seq=512)
        assert torch.isfinite(loss)
    with pytest.raises(AssertionError,match='BOT_BAD_SOURCE'):
        b.consumer_ce(m,t,rows[0],h,condition='shuffled',max_seq=512)
    assert b.pick_other(rows,0)['id']==rows[2]['id']
    assert b.pick_other(rows,len(rows)-1)['id']==rows[1]['id']
    assert not b.channel_advantage({'intact':8,'zero':8,'shuffled':7})
    assert b.channel_advantage({'intact':12,'zero':9,'shuffled':10})


def test_channel_generation_does_not_reuse_producer_cache(monkeypatch):
    m,t=causal_fixture()
    row=b.data_splits(2041,6,6,6,6)['train'][0]
    # Producer sends its final hidden tensor but no KV cache or prompt tokens.
    records=[]
    orig=m._step
    def observed(*args,**kwargs):
        records.append({'input_ids':kwargs.get('input_ids'),
                         'cache':kwargs.get('cache'), 'embeds':kwargs.get('inputs_embeds')})
        return orig(*args,**kwargs)
    monkeypatch.setattr(m,'_step',observed)
    with torch.no_grad():
        b.channel_prefix(m,t,row,'intact')
    assert records[0]['cache'] is None
    assert records[1]['embeds'] is not None and records[1]['cache'] is not None
    assert torch.equal(records[0]['input_ids'],b.fixed_consumer_ids(m,t,'cpu'))
    assert len(records)==3


def test_cli_argument_routes(monkeypatch,capsys):
    seen=[]
    def fake(**kwargs):
        seen.append(kwargs)
        return {'status':'CHANNEL_FAIL','scores':{'intact':0}}
    monkeypatch.setattr(b,'run',fake)
    assert b.main(['--stage','channel','--device','cpu','--channel-steps','2','--budget','3'])==0
    assert seen[0]['stage']=='channel' and seen[0]['channel_steps']==2
    assert seen[0]['budget']==3
    assert 'HYBRID_BOT_RESULT=' in capsys.readouterr().out


def test_new_lineage_resume_and_fail_closed(tmp_path,monkeypatch):
    source=tmp_path/'joint';source.mkdir()
    (source/'training').mkdir()
    (source/'training'/'best.pt').write_bytes(b'source checkpoint unchanged')
    def fake_source(*args):
        return {'joint_best':b.interface.fingerprint(source/'training'/'best.pt'),'joint_gate':'fixed','joint_config':'fixed','previous':{}}
    def fake_load(*args,**kwargs):return causal_fixture()
    monkeypatch.setattr(b,'source_provenance',fake_source)
    monkeypatch.setattr(b,'load_trainable',fake_load)
    common=dict(device='cpu',base=tmp_path/'base',numerical=tmp_path/'numerical',
        interface_dir=tmp_path/'interface',repair_root=tmp_path/'repair',joint_root=source,
        output=tmp_path/'bot',channel_steps=2,restore_steps=1,eval_every=1,
        train_count=6,dev_count=6,qualify_count=6,test_count=6,max_seq=512,budget=2)
    cp=b.interface.fingerprint(source/'training'/'best.pt')
    # Probe and train one step, demonstrating checkpoint preservation and
    # reproducible resume from saved torch RNG / optimizer state.
    res=b.run(stage='channel',stop_after=1,**common)
    assert res['status']=='PAUSED_RESUMABLE'
    with pytest.raises(AssertionError,match='BOT_RESUME_REQUIRED'):
        b.run(stage='channel',**common)
    res=b.run(stage='channel',resume=True,**common)
    assert res['status']=='TRAINED_UNCERTIFIED'
    assert b.interface.fingerprint(source/'training'/'best.pt')==cp
    with pytest.raises(AssertionError,match='BOT_CONFIG_DRIFT'):
        b.run(stage='channel',resume=True,seed=999,**{k:v for k,v in common.items() if k!='seed'})
    # Test gate scoring failure must prevent restoration.
    monkeypatch.setattr(b,'evaluate',lambda *a,**k:{'scores':{'intact':1,'zero':4,'shuffled':4,'marker_only':4},
        'cases':6,'records':[]})
    q=b.run(stage='qualify',**common)
    assert q['status']=='CHANNEL_FAIL' and not q['restore_permitted']
    with pytest.raises(AssertionError,match='BOT_CHANNEL_NOT_QUALIFIED'):
        b.run(stage='restore',**common)
    with pytest.raises(AssertionError,match='BOT_QUALIFICATION_ALREADY_USED'):
        b.run(stage='qualify',**common)


def test_restoration_replays_channel_and_opens_final_holdout_once(tmp_path,monkeypatch):
    source=tmp_path/'joint';(source/'training').mkdir(parents=True)
    (source/'training'/'best.pt').write_bytes(b'source')
    monkeypatch.setattr(b,'source_provenance',lambda *args:{'joint_best':'stub','joint_gate':'stub','joint_config':'stub','previous':{}})
    monkeypatch.setattr(b,'load_trainable',lambda *a,**k:causal_fixture())
    common=dict(device='cpu',base=tmp_path/'base',numerical=tmp_path/'numerical',
        interface_dir=tmp_path/'interface',repair_root=tmp_path/'repair',joint_root=source,
        output=tmp_path/'bot',channel_steps=1,restore_steps=1,eval_every=1,
        train_count=6,dev_count=6,qualify_count=6,test_count=6,max_seq=512,budget=2)
    assert b.run(stage='channel',**common)['status']=='TRAINED_UNCERTIFIED'
    # Qualification is a separate partition. No test examples have been seen.
    def positive(*args,**kwargs):
        return {'scores':{'intact':6,'zero':0,'shuffled':0,'marker_only':1,'text':5},
          'cases':6,'records':[]}
    monkeypatch.setattr(b,'evaluate',positive)
    assert b.run(stage='qualify',**common)['status']=='CHANNEL_PASS'
    r=b.run(stage='restore',**common)
    assert r['status']=='TRAINED_UNCERTIFIED'
    final=b.run(stage='gate',**common)
    assert final['causal_advantage_pass'] and final['status']=='ONE_STEP_PASS'
    assert final['gold_answer_used_as_input'] is False
    assert (tmp_path/'bot'/'gate_started.json').is_file()
    with pytest.raises(AssertionError,match='BOT_FINAL_GATE_ALREADY_USED'):
        b.run(stage='gate',**common)


def test_group_norm_aggregates_without_losing_semantics():
    a=torch.nn.Parameter(torch.tensor([3.,4.]))
    b0=torch.nn.Parameter(torch.tensor([12.,0.]))
    c=torch.nn.Parameter(torch.tensor([0.,0.]))
    a.grad=torch.tensor([3.,4.]);b0.grad=torch.tensor([12.,0.]);c.grad=torch.zeros(2)
    result=b.group_norm([('a',a),('b',b0),('c',c)])
    assert result['tensors_with_grad']==3
    assert result['nonzero_tensors']==2
    assert result['norm']==pytest.approx(13.0)
    b0.grad=torch.tensor([float('inf'),0.])
    with pytest.raises(AssertionError,match='BOT_NONFINITE_GRADIENT'):
        b.group_norm([('a',a),('b',b0)])
    assert b.group_norm([])=={'norm':0.,'tensors_with_grad':0,'nonzero_tensors':0}


def test_cpu_device_preflight_and_memory_report():
    model,_=causal_fixture()
    assert b.cuda_preflight(model,'cpu')=={'status':'CPU','device':'cpu'}
    assert b.cuda_memory('cpu') is None


@pytest.mark.skipif(not torch.cuda.is_available(),reason='CUDA hardware unavailable')
def test_real_cuda_bottleneck_mixed_precision_and_backward():
    model,tok=causal_fixture()
    # Production uses BF16 Qwen + FP32 head/LoRA. Recreate these contracts.
    model.to('cuda')
    model.backbone.get_input_embeddings().weight.data=model.backbone.get_input_embeddings().weight.data.to(torch.bfloat16)
    report=b.cuda_preflight(model,'cuda')
    assert report['status']=='CUDA_READY' and report['device'].startswith('cuda')
    assert b.cuda_memory('cuda')['allocated_mib']>=0
