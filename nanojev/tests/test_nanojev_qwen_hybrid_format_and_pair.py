"""CPU contract tests: precise masks, gradients, resume, checkpoint integrity, pairs."""
from types import SimpleNamespace
import json
from pathlib import Path
import pytest
import torch
from torch import nn

from tools import nanojev_qwen_hybridagent as hybrid
from tools import nanojev_qwen_hybridagent_activation as activation
from tools import nanojev_qwen_zero_line_backbone as zero
from tools import nanojev_qwen_hybridagent_self_conditioned as joint
from tools import nanojev_qwen_hybridagent_format_activation as fmt
from tools import nanojev_qwen_hybridagent_paired_diagnostic as paired

class Tokenizer:
    eos_token_id=0
    def __call__(self,text,**kwargs):
        if text==hybrid.START:return {'input_ids':[250]}
        if text==hybrid.END:return {'input_ids':[251]}
        return {'input_ids':[ord(x) if ord(x)<240 else 240 for x in text]}
    def decode(self,ids,**kwargs):return ''.join(chr(i) for i in ids if 0<i<240)

class Layer(nn.Module):
    def __init__(self,d):
        super().__init__()
        self.q_proj=nn.Linear(d,d)
        self.v_proj=nn.Linear(d,d)
    def forward(self,x):return torch.tanh(self.q_proj(x)+self.v_proj(x))

class Backbone(nn.Module):
    def __init__(self):
        super().__init__()
        self.config=SimpleNamespace(num_hidden_layers=1)
        self.emb=nn.Embedding(256,16)
        self.layer=Layer(16)
        self.lm_head=nn.Linear(16,256)
    def get_decoder(self):return self
    def get_input_embeddings(self):return self.emb
    def get_output_embeddings(self):return self.lm_head
    def forward(self,*,input_ids=None,inputs_embeds=None,attention_mask=None,past_key_values=None,
                output_hidden_states=False,use_cache=False,return_dict=True):
        x=self.emb(input_ids) if input_ids is not None else inputs_embeds
        y=self.layer(x)
        if output_hidden_states:
            length=0 if past_key_values is None else past_key_values[0]
            return SimpleNamespace(hidden_states=(y,),logits=self.lm_head(y),past_key_values=(length+x.size(1),))
        return SimpleNamespace(last_hidden_state=y)


def fake_loader(**kwargs):
    torch.manual_seed(444)
    b=Backbone(); b.requires_grad_(False)
    return hybrid.HybridQwen(b,hybrid.HybridLink(16),250,251),Tokenizer(),{
      'base_model':hybrid.MODEL_ID,'revision':hybrid.MODEL_REVISION,'schema':hybrid.SCHEMA}


def source_tree(tmp_path,monkeypatch):
    base=tmp_path/'base'; backbone=tmp_path/'backbone'; head=tmp_path/'head'
    for p in (base,backbone,head):p.mkdir()
    (base/'adapter.pt').write_bytes(b'base')
    (base/'adapter_config.json').write_bytes(b'base-cfg')
    monkeypatch.setattr(fmt.hybrid,'load_adapter',lambda *a,**k: None)
    hc={x:activation.file_hash(base/x) for x in ('adapter.pt','adapter_config.json')}
    m,_,_=fake_loader()
    zero.atomic_json(backbone/'run_config.json',{'schema':zero.SCHEMA,
       'baseline':str(base.resolve()),'base_model':hybrid.MODEL_ID,
       'revision':hybrid.MODEL_REVISION,'base_hashes':hc,'rank':2,'alpha':4.})
    zero.atomic_json(backbone/'result.json',{'status':'TRAINING_SMOKE_COMPLETED_UNCERTIFIED'})
    zero.install_lora(m.backbone,2,4.,required_layers=1)
    zero.atomic_torch(backbone/'best_lora.pt',{'schema':zero.SCHEMA,'step':1,'lora':zero.lora_state(m.backbone)})
    zero.atomic_json(head/'run_config.json',{'schema':activation.SCHEMA,'baseline_dir':str(base.resolve()),
       'base_model':hybrid.MODEL_ID,'revision':hybrid.MODEL_REVISION})
    zero.atomic_json(head/'result.json',{'status':'PASS_MECHANICS_ONLY'})
    zero.atomic_torch(head/'best_head.pt',{'schema':activation.SCHEMA,'step':1,
      'training_stage':'activation_only','head':m.head.state_dict()})
    return base,backbone,head


def test_text_only_loss_masks_both_mode_markers_and_prompt():
    model,tok,_=fake_loader()
    row=zero.make_cases(train_count=6,dev_count=6,test_count=6)['train'][0]
    d=fmt.encode_format(tok,model,row,512)
    assert all(x==-100 for x in d['labels'][:d['entry']])
    assert d['labels'][d['entry']]==model.start_id
    assert d['labels'][d['exit']]==model.end_id
    assert all(x!=-100 for x in d['labels'][d['entry']:])
    terms=fmt.format_loss(model,d,'cpu')
    assert torch.isfinite(terms['loss'])
    assert terms['marker_ce'].item()>0
    assert terms['token_count']==len(d['ids'])-d['entry']
    with pytest.raises(AssertionError,match='FORMAT_OVER_MAX_SEQ'):
        fmt.encode_format(tok,model,row,64)


def test_format_update_and_resume_and_hash_integrity(tmp_path,monkeypatch):
    base,bb,head=source_tree(tmp_path,monkeypatch)
    kwargs={'device':'cpu','baseline':base,'backbone':bb,'loader':fake_loader,
       'steps':2,'eval_every':1,'seed':71,'max_seq':512,'train_count':6,
       'dev_count':6,'test_count':6}
    fmt.train(output=tmp_path/'straight',**kwargs)
    one=fmt.train(output=tmp_path/'paused',stop_after=1,**kwargs)
    assert one['status']=='PAUSED_RESUMABLE'
    two=fmt.train(output=tmp_path/'paused',resume=True,**kwargs)
    assert two['status']=='FORMAT_ACTIVATION_SMOKE_COMPLETED_UNCERTIFIED'
    a=torch.load(tmp_path/'straight'/'last.pt',weights_only=True)
    b=torch.load(tmp_path/'paused'/'last.pt',weights_only=True)
    assert a['step']==b['step']==2
    for k,v in a['lora'].items():assert torch.equal(v,b['lora'][k])
    with pytest.raises(AssertionError,match='FORMAT_REFUSES_EXISTING_OUTPUT'):
        fmt.train(output=tmp_path/'straight',**kwargs)
    with pytest.raises(AssertionError,match='FORMAT_RESUME_CONFIG_DRIFT'):
        fmt.train(output=tmp_path/'paused',resume=True,lr=1e-5,**{k:v for k,v in kwargs.items() if k!='lr'})


def test_pair_decoding_and_scores_are_oracle_blind():
    model,tok,_=fake_loader()
    ids=torch.tensor([[ord(c) for c in 'Question: 2 + 2. Reasoning: ']])
    a=paired.decode_once(model,tok,ids,0,4)
    b=paired.decode_once(model,tok,ids,2,4)
    c=paired.decode_once(model,tok,ids,2,4,corrupt=True)
    assert a['latents']==0 and b['latents']==2 and c['latents']==2
    assert model.head.__class__ is hybrid.HybridLink
    assert paired.parse_answer('Answer: -7\n')['answer_label']==-7
    assert paired.parse_answer('7\nNext')['leading_integer']==7
    assert paired.parse_answer('The result might be 7')['answer_label'] is None
    rec=[{'configuration':'trained','depth':4,'control':'intact','expected':2,'answer_label':2,
          'leading_integer':None,'tokens':7,'latents':4,'seconds':0.2,'sign':'positive','family':'direct'},
         {'configuration':'trained','depth':4,'control':'intact','expected':-3,'answer_label':2,
          'leading_integer':None,'tokens':7,'latents':4,'seconds':0.2,'sign':'negative','family':'composed'}]
    scores=paired.paired_scores(rec)
    assert scores[0]['answer_label_correct']==1 and scores[0]['negative_correct']==0
    assert scores[0]['composed_correct']==0


def test_case_groups_have_balanced_signs_and_families():
    r=paired.selected_cases(n=6)
    assert len(r)==6
    assert {x['sign'] for x in r}=={'negative','positive','zero'}
    assert {x['family'] for x in r}=={'direct','composed'}
    with pytest.raises(AssertionError,match='PAIR_NEEDS_BALANCED_CASE_COUNT'):
        paired.selected_cases(n=5)


def test_teacher_forced_marker_probability_is_structured_and_finite():
    model,tok,_=fake_loader()
    rows=zero.make_cases(train_count=6,dev_count=6,test_count=6)['dev']
    probabilities=fmt.marker_probabilities(model,tok,rows,'cpu',512)
    assert 0<probabilities['entry_probability']<1
    assert 0<probabilities['exit_probability']<1


def test_paired_readonly_end_to_end_and_refuses_altered_format(tmp_path,monkeypatch):
    base,bb,head=source_tree(tmp_path,monkeypatch)
    fdir=tmp_path/'format'
    fmt.train(device='cpu',baseline=base,backbone=bb,output=fdir,loader=fake_loader,
      steps=1,eval_every=1,seed=71,max_seq=512,train_count=6,dev_count=6,test_count=6)
    model,_,_=fake_loader()
    model.backbone.requires_grad_(False)
    zero.install_lora(model.backbone,2,4.,required_layers=1)
    jointdir=tmp_path/'joint';jointdir.mkdir()
    jconfig={'schema':joint.SCHEMA,'sources':joint.source_fingerprints(base,head,bb)}
    zero.atomic_json(jointdir/'run_config.json',jconfig)
    zero.atomic_json(jointdir/'result.json',{'status':'SELF_CONDITIONED_SMOKE_COMPLETED_UNCERTIFIED'})
    zero.atomic_torch(jointdir/'best.pt',{'schema':joint.SCHEMA,
        'config_sha256':joint.sha_config(jconfig),'lora':zero.lora_state(model.backbone),
        'head':{k:v.cpu() for k,v in model.head.state_dict().items()}})
    report=paired.run(device='cpu',baseline=base,backbone=bb,activation_dir=head,
         format_dir=fdir,joint_dir=jointdir,output=tmp_path/'pair',cases=6,
         max_new_tokens=2,depths=(0,4),loader=fake_loader)
    assert report['status']=='DIAGNOSTIC_COMPLETE_UNCERTIFIED'
    assert report['configurations']==3
    assert len(report['scores'])==9  # per checkpoint: text, four latent, zero intervention
    assert report['optimizer_steps']==0 and report['source_mutations']==0
    assert (tmp_path/'pair'/'result.json').is_file()
    corrupt=json.loads((fdir/'run_config.json').read_text())
    corrupt['seed']=999
    zero.atomic_json(fdir/'run_config.json',corrupt)
    with pytest.raises(AssertionError,match='PAIR_FORMAT_CHECKPOINT_DRIFT'):
        paired.run(device='cpu',baseline=base,backbone=bb,activation_dir=head,
            format_dir=fdir,joint_dir=jointdir,output=tmp_path/'pair_2',cases=6,
            max_new_tokens=2,depths=(0,4),loader=fake_loader)


def test_single_command_gate_reuses_completed_format_but_never_overwrites_pair(tmp_path,monkeypatch):
    from tools import nanojev_qwen_hybridagent_format_gate as gate
    format_path=tmp_path/'format';format_path.mkdir()
    zero.atomic_json(format_path/'run_config.json',{'steps':24,'eval_every':6,'seed':71})
    phase={'status':'FORMAT_ACTIVATION_SMOKE_COMPLETED_UNCERTIFIED',
       'initial_dev':{'loss':2.,'transitions':{'entry_probability':.02,'exit_probability':.03}},
       'current_dev':{'loss':1.,'transitions':{'entry_probability':.04,'exit_probability':.06}}}
    zero.atomic_json(format_path/'result.json',phase)
    def no_training(**kwargs):raise AssertionError('GATE_SHOULD_NOT_RETRAIN_COMPLETED_FORMAT')
    monkeypatch.setattr(gate.fmt,'train',no_training)
    rows=[]
    for depth in (0,1,2,4):
        for c in ('numerical_pre_format','format_activated'):
            rows.append({'configuration':c,'depth':depth,'control':'intact',
              'answer_label_correct':1 if c=='numerical_pre_format' else 2,
              'parseable_answer_label':2 if c=='numerical_pre_format' else 4})
    monkeypatch.setattr(gate.paired,'run',lambda **kwargs:{'configurations':2,'depths':[0,1,2,4], 'scores':rows})
    result=gate.run(device='cpu',format_output=format_path,pair_output=tmp_path/'pair',skip_joint=True)
    assert result['status']=='FORMAT_AND_PAIRED_COMPLETED_UNCERTIFIED'
    assert all(d['net_correct_gain']==1 for d in result['paired_comparison'])
    assert result['format']['format_dev_loss_decreased']
    with pytest.raises(AssertionError,match='FORMAT_GATE_PRIOR_CONFIG_DRIFT'):
        gate.run(device='cpu',format_output=format_path,pair_output=tmp_path/'other',steps=25)


def test_format_prestep_metadata_recovery_and_drift_refusal(tmp_path,monkeypatch):
    base,bb,_=source_tree(tmp_path,monkeypatch)
    out=tmp_path/'prestep'
    kwargs=dict(device='cpu',baseline=base,backbone=bb,output=out,loader=fake_loader,
      steps=1,eval_every=1,max_seq=512,train_count=6,dev_count=6,test_count=6)
    first=fmt.train(**kwargs)
    assert first['status']=='FORMAT_ACTIVATION_SMOKE_COMPLETED_UNCERTIFIED'
    # Simulate previous crash before first optimizer step: retain only config.
    for artifact in out.iterdir():
        if artifact.name!='run_config.json':artifact.unlink()
    recovered=fmt.train(**kwargs)
    assert recovered['status']=='FORMAT_ACTIVATION_SMOKE_COMPLETED_UNCERTIFIED'
    for artifact in out.iterdir():
        if artifact.name!='run_config.json':artifact.unlink()
    config=json.loads((out/'run_config.json').read_text(encoding='utf8'))
    config['seed']=12345
    zero.atomic_json(out/'run_config.json',config)
    with pytest.raises(AssertionError,match='FORMAT_PRESTEP_CONFIG_DRIFT'):
        fmt.train(**kwargs)
