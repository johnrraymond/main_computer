"""Head-only anonymous latent training: CPU contracts and held-out guards."""
from __future__ import annotations

from pathlib import Path
import json
from types import SimpleNamespace

import pytest

from tools import nanojev_hybridlink_micro_latent_transfer as link
from tools import nanojev_hybridlink_micro_reduction as micro
from tools import nanojev_hybridlink_micro_ood_probe as ood
from tools import nanojev_hybridlink_rung_reduction as base


def config():
    cfg={'band':[0,4],'scales':[8,16,32,64,128,256],
         'seed':71,'train_count':36,'dev_count':12,'test_count':12}
    cfg['deck_hash']=micro.deck_hash(micro.deck_for(
        band=(0,4),scales=tuple(cfg['scales']),seed=71,
        train_count=36,dev_count=12,test_count=12))
    return cfg


def test_new_training_deck_diverse_deterministic_and_nonleaky():
    cfg=config()
    d=link.make_decks(cfg)
    assert len(d['new_train'])==192
    assert len(d['new_dev'])==48
    assert {c['depth'] for c in d['new_train']}=={2,4,6,10}
    assert {c['shape'] for c in d['new_train']}==set(link.SHAPES)
    assert {c['scale'] for c in d['new_train']}==set(link.SCALES)
    assert {-4,-3,-2,-1,0,1,2,3,4} <= {c['answer'] for c in d['new_train']}
    assert {c['reverse'] for c in d['new_train']}=={True,False}
    assert any(ood._negative_intermediate(c['state']) for c in d['new_train'])
    assert link._fingerprint(d)==link._fingerprint(link.make_decks(cfg))
    a=link._transition_states(d['new_train'])
    b=link._transition_states(d['new_dev'])
    assert not a & b
    assert not a & link._transition_states(d['retention_dev'])
    assert any(' - -' in state for state in a)
    assert all('Hawk' not in state and 'Dove' not in state and 'payoff' not in state for state in a)


def test_all_expanded_teacher_steps_are_exactly_one_and_value_preserving():
    d=link.make_decks(config(),train_count=24,dev_count=12)
    for deck in d.values():
        for case in deck:
            state=case['state']
            while micro.steps(state):
                completed=micro.target(state)
                score=micro.evaluate_transition(state,completed)
                assert score['valid'],completed
                assert micro.steps(score['state'])==micro.steps(state)-1
                assert micro.result_value(score['state'])==case['answer']
                state=score['state']


def test_preview_loads_no_models_or_checkpoints(monkeypatch,capsys):
    args=SimpleNamespace(text_checkpoint='text-best.pt',seed=90271,train_count=24,dev_count=12)
    result=link.preview(args,config())
    assert result['model_loaded'] is False
    assert result['counts']['new_train']['cases']==24
    assert result['heldout_ood_used_for_training'] is False
    assert 'MICRO_LINK_PREVIEW=' in capsys.readouterr().out


def test_parent_rejects_wrong_stage_and_missing_parent(tmp_path):
    with pytest.raises(ValueError,match='MISSING'):
        link._parent_config(tmp_path/'no.pt')
    p=tmp_path/'best.pt';p.write_bytes(b'checkpoint')
    (tmp_path/'run_config.json').write_text(json.dumps({'schema':micro.SCHEMA,'stage':'train-link'}))
    with pytest.raises(ValueError,match='WRONG_FAMILY'):
        link._parent_config(p)


def test_selection_covers_signs_and_positions():
    rows=micro.training_rows(link._new_cases(50,90271,'dev'))
    picked=link._sample_dev(rows,24)
    assert len(picked)==24
    assert len({p['position']%4 for p in picked})==4
    assert len({(p['expected']>0)-(p['expected']<0) for p in picked})==3


def test_directed_one_step_rejects_double_edit_and_sign_corruption():
    s=micro.parse('<REDUCE>\nA = 166 + 13\nB = 135 - -44\nR = A - B\n</REDUCE>')
    assert micro.evaluate_transition(s,micro.target(s))['valid']
    assert not micro.evaluate_transition(s,'<REDUCE>\nA = 179\nB = 179\nR = A - B\n</REDUCE>\nEND_RUNG')['valid']
    assert not micro.evaluate_transition(s,'<REDUCE>\nA = 179\nB = 135 - -44\nR = B - A\n</REDUCE>\nEND_RUNG')['valid']


def test_checkpoint_link_requires_source_and_parent_hash(tmp_path):
    torch=pytest.importorskip('torch')
    ck_path=tmp_path/'head'/'best.pt';ck_path.parent.mkdir()
    cfg=config();cfg['sources']={'source':'sha'};cfg['frozen_backbone']='hash'
    saved={'schema':link.SCHEMA,'text_sha256':'text-hash'}
    (ck_path.parent/'run_config.json').write_text(json.dumps(saved))
    torch.save({'schema':link.SCHEMA,'status':'LINK_TRAINED','config_hash':base.digest(saved),
                'text_sha256':'different','source_sha256':cfg['sources'],
                'frozen_backbone_sha256':'hash','head':{}},ck_path)
    with pytest.raises(RuntimeError,match='PARENT_HASH'):
        link._load_link_into(SimpleNamespace(head=None),ck_path,cfg,'text-hash')


def test_latent_loss_target_alignment_after_end_marker():
    """The loss must supervise decoded tokens AFTER </latent>, never latent itself."""
    torch=pytest.importorskip('torch')
    from torch import nn
    class Tok:
        def __call__(self,text,add_special_tokens=False):
            return {'input_ids':[1,2] if text=='prompt' else [4,5,6]}
    class Decoder(nn.Module):
        def forward(self,*,inputs_embeds,attention_mask=None,return_dict=True,use_cache=False):
            n=inputs_embeds.shape[1]
            # Strongly predict token ID equal to current position at that position.
            positions=torch.arange(n,device=inputs_embeds.device)
            logits=15*torch.nn.functional.one_hot(positions,num_classes=16).float()
            return SimpleNamespace(last_hidden_state=logits[None,:,:])
    class Backbone(nn.Module):
        def __init__(self):
            super().__init__();self.embedding=nn.Embedding(16,16);self.decoder=Decoder()
            self.out=nn.Linear(16,16,bias=False)
            with torch.no_grad():self.out.weight.copy_(torch.eye(16))
        def get_input_embeddings(self):return self.embedding
        def get_decoder(self):return self.decoder
        def get_output_embeddings(self):return self.out
    class Model(nn.Module):
        def __init__(self):
            super().__init__();self.backbone=Backbone();self.head=nn.Linear(16,16)
            self.start_id=3;self.end_id=7
    loss=base.latent_loss(Model(),Tok(),{'prompt':'prompt','target':'target'},'cpu',64)
    assert float(loss)<.001, f'Likely shifted before the decoded boundary: loss={float(loss)}'


def test_repaired_early_stopping_does_not_truncate_long_markup():
    torch=pytest.importorskip('torch')
    from torch import nn
    # 400+ chars and a complete marker. The prior [:300] truncation would fail.
    answer='<REDUCE>\nA = '+' + '.join(['1']*110)+'\nB = 1\nR = A - B\n</REDUCE>\nEND_RUNG'
    assert len(answer)>300
    class Tok:
        eos_token_id=1000
        def __call__(self,text,add_special_tokens=False):return {'input_ids':[500]}
        def decode(self,ids,skip_special_tokens=True):return ''.join(chr(x) for x in ids)
    class Model(nn.Module):
        def __init__(self):
            super().__init__();self.head=nn.Linear(3,3);self.backbone=nn.Linear(3,3)
            self.generated=0
        def logits(self):
            l=torch.zeros((1,1001));l[0,ord(answer[self.generated])]=1
            return l
        def rollout(self,t,depth,return_trace=False):
            return None,self.logits(),None,torch.ones((1,1)),[]
        def _step(self,*,input_ids=None,cache=None,mask=None):
            self.generated+=1
            return None,self.logits(),None
    result=base._decode(Model(),Tok(),'prompt','text_only',len(answer)+10,stop_at_end_rung=True)
    assert result==answer


def test_mock_head_training_freezes_backbone_and_writes_separate_checkpoint(tmp_path,monkeypatch):
    torch=pytest.importorskip('torch')
    from torch import nn
    from tools import nanojev_qwen_zero_line_backbone as zero
    from tools import nanojev_qwen_hybridagent_activation as activation
    cfg=config();cfg.update(sources={'source':'sha'},frozen_backbone='frozen')
    text=tmp_path/'text'/'best.pt';text.parent.mkdir();text.write_bytes(b'text parent')
    out=tmp_path/'head'
    class Fake(nn.Module):
        def __init__(self):
            super().__init__();self.backbone=nn.Linear(3,3);self.head=nn.Linear(3,3)
    model=Fake()
    backbone_snapshot={k:v.detach().clone() for k,v in model.backbone.state_dict().items()}
    head_snapshot={k:v.detach().clone() for k,v in model.head.state_dict().items()}
    monkeypatch.setattr(link,'_load_text_model',lambda args,cfg:(model,None,cfg['sources'],'frozen',()))
    monkeypatch.setattr(zero,'lora_named_parameters',lambda backbone: [])
    monkeypatch.setattr(zero,'lora_state',lambda backbone:{})
    monkeypatch.setattr(zero,'frozen_backbone_sha256',lambda backbone:'frozen')
    monkeypatch.setattr(activation,'file_hash',lambda p:'text-hash')
    monkeypatch.setattr(base,'_encode',lambda tok,row,max_seq:([1,2],[3,4]))
    def fake_loss(model,tok,row,device,max_seq):
        return (model.head.weight**2).sum()+(.1*model.head.bias**2).sum()
    monkeypatch.setattr(base,'latent_loss',fake_loss)
    args=SimpleNamespace(output=str(out),text_checkpoint=str(text),device='cpu',
        seed=90271,train_count=24,dev_count=12,steps=2,eval_every=1,
        dev_samples=6,retention_frequency=2,lr=.1,max_seq=1024)
    result=link.train(args,cfg)
    assert result['status']=='TRAINED_UNCERTIFIED'
    assert (out/'best.pt').exists()
    assert result['head_only']
    assert all(torch.equal(v,model.backbone.state_dict()[k]) for k,v in backbone_snapshot.items())
    assert any(not torch.equal(v,model.head.state_dict()[k]) for k,v in head_snapshot.items())
    saved=torch.load(out/'best.pt',weights_only=True)
    assert saved['status']=='LINK_TRAINED'
    assert saved['text_sha256']=='text-hash'
    assert 'lora' not in saved  # never silently claim the LoRA was trained
    with pytest.raises(RuntimeError,match='OUTPUT_NOT_EMPTY'):
        link.train(args,cfg)


def test_mock_ood_probe_comparison_has_paired_records(tmp_path,monkeypatch):
    torch=pytest.importorskip('torch')
    from torch import nn
    from tools import nanojev_qwen_hybridagent_activation as activation
    cfg=config();cfg.update(sources={'source':'sha'},frozen_backbone='frozen')
    parent=tmp_path/'text'/'best.pt';parent.parent.mkdir();parent.write_bytes(b'text')
    head=tmp_path/'head'/'best.pt';head.parent.mkdir();head.write_bytes(b'head')
    class Model:
        def __init__(self):
            self.backbone=nn.Linear(2,2);self.head=nn.Linear(2,2)
    model=Model()
    monkeypatch.setattr(link,'_load_text_model',lambda args,cfg:(model,None,cfg['sources'],'frozen',()))
    monkeypatch.setattr(link,'_load_link_into',lambda *a,**k: {})
    monkeypatch.setattr(activation,'file_hash',lambda p:'hash')
    # Fake greedy inference returns exactly the permitted teacher's first step.
    def fake_decode(model,tok,text,control,budget,*,stop_at_end_rung=False):
        payload=text.split('Current:\n',1)[1].split('\nNext:\n',1)[0]
        return micro.target(micro.parse(payload))
    monkeypatch.setattr(base,'_decode',fake_decode)
    args=SimpleNamespace(text_checkpoint=str(parent),link_checkpoint=str(head),
        output=str(tmp_path/'probe'),controls=['text_only','zero_latent','intact'],
        suites=['control','negative'],limit_per_suite=1,ood_seed=20261027,
        progress_every=40,max_new_tokens=512)
    result=link._probe(args,cfg,'ood')
    assert result['no_training']
    assert result['cases']==2
    assert result['scores']['intact']['negative']['one_step']['accuracy']==1
    key='negative:intact_vs_zero_latent'
    assert result['paired'][key]['both_correct']>0
    assert result['paired'][key]['intact_only']==0
    assert result['test_evaluated'] is False
    assert (Path(args.output)/'one_steps.json').exists()
