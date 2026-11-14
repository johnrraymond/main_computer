"""Direct GAMER architecture: no token decoder, no game-specific inference labels."""
from __future__ import annotations
import json
from pathlib import Path
from types import SimpleNamespace
import pytest
import torch
from tools import nanojev_gamer_v1_backbone_first as core
g = core  # generic executor compatibility tests
from tools import nanojev_gamer_v1_backbone_first_train as trainer
from tools import nanojev_gamer_v1_backbone_first_corpus as corpus
from tools.nanojev_gamer_v1_backbone_first_head import (GamerDirectHead,direct_loss,
                                                    decode_action_spans, coupled_start_logits,
                                                    PLAYER_BEGIN, PLAYER_INSIDE, OPP_BEGIN, OPP_INSIDE)

class CharTokenizer:
    is_fast=True
    def apply_chat_template(self,messages,tokenize=False,add_generation_prompt=False,enable_thinking=False):
        assert not add_generation_prompt
        return '<user>'+messages[0]['content']+'</user>'
    def __call__(self,text,return_offsets_mapping=True,add_special_tokens=False,
                 return_tensors='pt',truncation=False):
        n=len(text)
        return {'input_ids':torch.tensor([[ord(c)%100 for c in text]]),
                'attention_mask':torch.ones((1,n),dtype=torch.long),
                'offset_mapping':torch.tensor([[[i,i+1] for i in range(n)]])}

class FrozenFakeQwen(torch.nn.Module):
    def __init__(self):
        super().__init__()
        self.embed=torch.nn.Embedding(100,16)
        self.calls=0
        for p in self.parameters():p.requires_grad_(False)
    def forward(self,*,input_ids,attention_mask,output_hidden_states,use_cache):
        assert output_hidden_states and not use_cache
        self.calls+=1
        hidden=self.embed(input_ids)
        return SimpleNamespace(hidden_states=(hidden,))
    def generate(self,*args,**kwargs):
        raise AssertionError('AUTOREGRESSIVE_GENERATION_FORBIDDEN')


def state(names=('Quartz','Jade'),scores=(3,7)):
    return {'status':'ok','objective':'maximize',
       'facts':{'a':str(scores[0]),'b':str(scores[1])},
       'actions':[{'name':names[0],'score':{'ref':'a'}},
                  {'name':names[1],'score':{'ref':'b'}}]}


def fake_checkpoint(tmp_path,kind='contextual-player-opponent-role-and-coupled-preference'):
    output=tmp_path/'head';output.mkdir()
    source=tmp_path/'source';source.mkdir()
    (source/'adapter_config.json').write_text('{}')
    (source/'adapter_model.safetensors').write_bytes(b'fake')
    (output/'gamer_head.safetensors').write_bytes(b'fake')
    manifest={'schema':core.SCHEMA,'status':'HEAD_TRAINED_NOT_CERTIFIED',
       'head_sha256':core.sha256(output/'gamer_head.safetensors'),
       'source_adapter':str(source),'source_adapter_sha256':core.adapter_fingerprint(source),
       'head':{'type':kind,'hidden_size':16,'width':32,'layers':1},
       'backbone_frozen':True,'lora_frozen':True,'model_id':'fake'}
    (output/'manifest.json').write_text(json.dumps(manifest))
    return output


def annotate(prompt,roles):
    """Test helper only, not an inference parser."""
    import re
    annotations=[]
    for role,names in roles.items():
        for name in names:
            for m in re.finditer(re.escape(name),prompt):
                annotations.append({'role':role,'name':name,'start':m.start(),'end':m.end()})
    return sorted(annotations,key=lambda a:a['start'])


def test_raw_prompt_enters_qwen_intact_and_there_is_one_forward_no_generation():
    prompt='Pick Quartz or Jade with arbitrary rules: Ω.\nNo parser allowed.'
    tokenizer=CharTokenizer();backbone=FrozenFakeQwen()
    p=core.prepare_prompt(tokenizer,prompt)
    assert p['formatted']=='<user>'+prompt+'</user>'
    assert p['prompt']==prompt
    hidden=core.frozen_context(backbone,p,device='cpu')
    assert backbone.calls==1 and hidden.shape==(len(p['formatted']),16)
    assert not any(x.requires_grad for x in backbone.parameters())
    assert core.SCHEMA.endswith('.v4')


def test_training_supervision_is_not_used_by_inference():
    prompt='Choose Quartz or Jade. Opponent chooses Onyx. Quartz yields 3 and Jade yields 7.'
    p=core.prepare_prompt(CharTokenizer(),prompt)
    row={'prompt':prompt,'state':state(),'split':'train',
         'role_spans':annotate(prompt,{'player':['Quartz','Jade'],'opponent':['Onyx']})}
    sup=trainer.supervision(row,p)
    assert sup['winners']=={'Jade'}
    assert set(sup['action_starts'])=={'Quartz','Jade'}
    assert sum(sup['tags']==1)>=2
    assert 'supervision' not in core.__dict__
    assert not hasattr(core,'spans_from_prompt')
    assert not hasattr(core,'_word_vector')
    assert not hasattr(core,'generate_state')


def test_direct_head_is_not_a_vocabulary_head_and_gradients_only_update_head():
    backbone=FrozenFakeQwen()
    head=GamerDirectHead(16,width=32)
    item={'prompt':'Quartz or Jade. Rival uses Onyx. Quartz earns 3. Jade earns 7.',
          'state':state(),'split':'train'}
    item['role_spans']=annotate(item['prompt'],{'player':['Quartz','Jade'],
                                                  'opponent':['Onyx']})
    tok=CharTokenizer()
    p=core.prepare_prompt(tok,item['prompt'])
    h=core.frozen_context(backbone,p,device='cpu')
    target=trainer.supervision(item,p)
    tags,pref=head(h)
    loss,parts=direct_loss(tags,pref,target['tags'],p['eligible'],
                            target['action_starts'],target['winners'])
    assert torch.isfinite(loss)
    old_backbone={k:v.detach().clone() for k,v in backbone.state_dict().items()}
    old_head={k:v.detach().clone() for k,v in head.state_dict().items()}
    optim=torch.optim.Adam(head.parameters(),lr=1e-2)
    loss.backward();optim.step()
    assert any(not torch.equal(old_head[k],v) for k,v in head.state_dict().items())
    assert all(torch.equal(old_backbone[k],v) for k,v in backbone.state_dict().items())
    assert all(p.grad is None for p in backbone.parameters())
    assert head.span_tags.out_features==5
    assert not hasattr(head,'vocab_size')


def test_neural_pointer_extracts_new_action_names_no_lexicon():
    prompt='Try Quartz or Jade. Arbitrary payoff prose; select.'
    p=core.prepare_prompt(CharTokenizer(),prompt)
    n=len(p['offsets'])
    tags=[0]*n;preferences=[0.0]*n
    for name,value in [('Quartz',1.0),('Jade',5.0)]:
        a=p['prompt_start']+prompt.index(name)
        b=a+len(name)
        tags[a]=1
        for i in range(a+1,b):tags[i]=2
        preferences[a]=value
    choices=decode_action_spans(tags,preferences,p['eligible'].tolist(),
                p['offsets'],p['prompt_start'],prompt)
    assert [c['name'] for c in choices]==['Jade','Quartz']
    assert choices[0]['mentions']==[[prompt.index('Jade'),prompt.index('Jade')+4]]


def test_fail_closed_when_neural_head_identifies_no_actions():
    prompt='Which move is best?'
    p=core.prepare_prompt(CharTokenizer(),prompt)
    class NoActionHead(torch.nn.Module):
        def __init__(self):super().__init__();self.p=torch.nn.Parameter(torch.zeros(1))
        def forward(self,h):
            return torch.stack((torch.ones(len(h))*100,
                                torch.zeros(len(h)),torch.zeros(len(h)),
                                torch.zeros(len(h)),torch.zeros(len(h))),dim=1),torch.zeros(len(h))
    result=core.direct_decide(NoActionHead(),torch.randn(len(p['offsets']),16),p)
    assert result['status']=='NO_NEURAL_ACTION_SPANS'
    assert result['decisions']==[]


def test_query_rejects_old_language_generator_checkpoint(tmp_path):
    cp=fake_checkpoint(tmp_path)
    manifest=json.loads((cp/'manifest.json').read_text())
    manifest['schema']='nanojev.gamer.v1.frozen-backbone-head.v2'
    (cp/'manifest.json').write_text(json.dumps(manifest))
    with pytest.raises(ValueError,match='LEGACY_TOKEN_GENERATOR'):
        core.query(checkpoint=cp,prompt='Unknown game',device='cpu')


def test_query_uses_frozen_model_then_direct_decision_without_lexical_cheat(tmp_path,monkeypatch):
    cp=fake_checkpoint(tmp_path)
    model=FrozenFakeQwen();tok=CharTokenizer()
    monkeypatch.setattr(core,'model_components',lambda *args:(model,tok))
    class FakeHead(torch.nn.Module):
        def __init__(self):super().__init__();self.w=torch.nn.Parameter(torch.zeros(1))
        def forward(self,h):
            tags=torch.zeros((len(h),5));tags[:,0]=10
            p=core.prepare_prompt(tok,'Jade or Quartz')
            start=p['prompt_start']
            # The output is chosen by this fake neural head, not by the core.
            tags[start:start+4,0]=0;tags[start,1]=20;tags[start+1:start+4,2]=20
            pref=torch.zeros(len(h));pref[start]=5
            return tags,pref
    monkeypatch.setattr(core,'load_head_from_checkpoint',lambda *a,**k:FakeHead())
    out=core.query(checkpoint=cp,prompt='Jade or Quartz',device='cpu')
    assert out['decisions']==['Jade']
    assert out['generated_tokens']==0
    assert out['semantic_parser_used'] is False
    assert model.calls==1


def test_training_corpus_not_in_inference_and_heldout_names_disjoint(tmp_path):
    path=tmp_path/'corpus.jsonl'
    info=corpus.generate(path,train=8,dev=3,test=3,
                        t08_train=4,t08_dev=2,t08_test=2)
    assert info['test_names_disjoint_from_train']
    rows=trainer.load_dataset(path)
    assert len(rows)==22
    assert len(set(x['prompt'] for x in rows))==22
    for row in rows:
        if row['split']=='train':
            trainer.supervision(row,core.prepare_prompt(CharTokenizer(),row['prompt'],max_input_tokens=3000))
    assert not hasattr(core,'T08_TRAIN')


def test_training_data_duplicate_prompt_rejected(tmp_path):
    p=tmp_path/'data.jsonl'
    rows=[{'prompt':'same','state':state(),'split':s,
           'role_spans':[{'role':'player','name':'same','start':0,'end':4}]}
          for s in ('train','dev','test')]
    p.write_text('\n'.join(json.dumps(r) for r in rows))
    with pytest.raises(ValueError,match='DUPLICATE_PROMPT'):
        trainer.load_dataset(p)


def test_direct_checkpoint_metadata_no_completion_language(tmp_path,monkeypatch):
    from safetensors.torch import load_file
    # Integration with lightweight fake frozen Qwen, no transformers/CUDA needed.
    path=tmp_path/'corpus.jsonl'
    corpus.generate(path,train=6,dev=2,test=2,t08_train=2,t08_dev=1,t08_test=1)
    adapter=tmp_path/'adapter';adapter.mkdir()
    (adapter/'adapter_config.json').write_text('{}')
    (adapter/'adapter_model.safetensors').write_bytes(b'fake')
    backbone=FrozenFakeQwen();tok=CharTokenizer()
    monkeypatch.setattr(trainer,'_frozen',lambda *a:(backbone,tok))
    out=tmp_path/'direct'
    report=trainer.train(input=path,output=out,source_adapter=adapter,
          device='cpu',steps=2,accum=2,width=32,lr=0.001,max_length=4000)
    assert report['generated_tokens']==0
    assert report['training_target']!='GAME_STATE_IR_TOKEN_COMPLETIONS'
    assert report['backbone_frozen'] is True
    assert report['head']['type']=='contextual-player-opponent-role-and-coupled-preference'
    assert load_file(str(out/'gamer_head.safetensors'))
    with pytest.raises(FileExistsError,match='OUTPUT_EXISTS'):
        trainer.train(input=path,output=out,source_adapter=adapter,device='cpu',steps=1)


def test_evaluator_one_pass_zero_generated_tokens_and_immutable_results(tmp_path,monkeypatch):
    data=tmp_path/'data.jsonl'
    corpus.generate(data,train=5,dev=2,test=2,t08_train=2,t08_dev=1,t08_test=1)
    adapter=tmp_path/'source';adapter.mkdir()
    (adapter/'adapter_config.json').write_text('{}')
    (adapter/'adapter_model.safetensors').write_bytes(b'fake')
    model=FrozenFakeQwen();tok=CharTokenizer()
    monkeypatch.setattr(trainer,'_frozen',lambda *a:(model,tok))
    cp=tmp_path/'checkpoint'
    trainer.train(input=data,output=cp,source_adapter=adapter,device='cpu',
                  steps=1,accum=1,width=32,max_length=4000,train_limit=4)
    before=model.calls
    report=trainer.evaluate(input=data,checkpoint=cp,output=tmp_path/'report',
                split='dev',device='cpu',limit=2,ablation_limit=1,max_input_tokens=4000)
    assert model.calls==before+2  # precisely one frozen Qwen pass per eval prompt
    assert report['cases']==2
    assert report['no_generated_tokens'] is True
    assert report['status']=='MEASURED_NOT_CERTIFIED'
    assert all(c['generated_tokens']==0 for c in report['cases_detail'])
    with pytest.raises(FileExistsError,match='IMMUTABLE_EVALUATION_EXISTS'):
        trainer.evaluate(input=data,checkpoint=cp,output=tmp_path/'report',
                split='dev',device='cpu',limit=1)


def test_legacy_v2_default_query_routes_to_direct_head_only(monkeypatch,tmp_path):
    from tools import nanojev_gamer_v1_hawk_dove_t08_role_v2 as legacy
    calls=[]
    def fake(*,checkpoint,prompt,device):
        calls.append((checkpoint,prompt,device))
        return {'status':'NEURAL_DECISION_UNCERTIFIED','generated_tokens':0}
    monkeypatch.setattr(core,'query',fake)
    cp=tmp_path/'direct'
    result=legacy.main(['query','--output',str(cp),
                        '--prompt','The unfamiliar game prompt','--device','cpu'])
    assert result['generated_tokens']==0
    assert calls==[(cp,'The unfamiliar game prompt','cpu')]
    with pytest.raises(ValueError,match='EXTERNAL_GAME_HEAD_SELECTION_FORBIDDEN'):
        legacy.main(['query','--output',str(cp), '--game-head','old-game',
                     '--prompt','The unfamiliar game prompt','--device','cpu'])


def test_source_adapter_lineage_tamper_rejected_before_model_load(tmp_path):
    cp=fake_checkpoint(tmp_path)
    manifest=json.loads((cp/'manifest.json').read_text())
    Path(manifest['source_adapter'],'adapter_config.json').write_text('tampered')
    with pytest.raises(ValueError,match='SOURCE_ADAPTER_LINEAGE_MISMATCH'):
        core.query(checkpoint=cp,prompt='Some unknown game',device='cpu')


# Preserve verified generic exact-executor regression coverage.
def make_state(hawk=940000,dove=60000,names=('Hawk','Dove'),payoffs=(-1,3,0,'3/2')):
    return {'status':'ok','objective':'maximize',
      'facts':{'n0':str(hawk),'n1':str(dove),
         'p00':str(payoffs[0]),'p01':str(payoffs[1]),
         'p10':str(payoffs[2]),'p11':str(payoffs[3])},
      'actions':[
        {'name':names[0],'score':{'op':'add','args':[
            {'op':'mul','args':[{'ref':'n0'},{'ref':'p00'}]},
            {'op':'mul','args':[{'ref':'n1'},{'ref':'p01'}]}]}},
        {'name':names[1],'score':{'op':'add','args':[
            {'op':'mul','args':[{'ref':'n0'},{'ref':'p10'}]},
            {'op':'mul','args':[{'ref':'n1'},{'ref':'p11'}]}]}}]}



def test_hawk_dove_exact_and_renamed_strategies():
    result=g.execute_state(make_state())
    assert result['scores']=={'Hawk':'-760000','Dove':'90000'}
    assert result['decisions']==['Dove']
    changed=g.execute_state(make_state(names=('Quartz','Jade')))
    assert changed['decisions']==['Jade']
    assert changed['scores']=={'Quartz':'-760000','Jade':'90000'}


def test_changed_game_payoffs_change_decision_without_changes_to_executor():
    before=g.execute_state(make_state())
    after=g.execute_state(make_state(payoffs=(4,3,0,'3/2')))
    assert before['decisions']==['Dove']
    assert after['decisions']==['Hawk']


def test_not_two_actions_not_a_million_not_hawk_dove():
    state={'status':'ok','objective':'minimize',
         'facts':{'x':'1/3','y':'-8','z':'0.125'},
         'actions':[{'name':'A','score':{'ref':'x'}},
                    {'name':'B','score':{'ref':'y'}},
                    {'name':'C','score':{'ref':'z'}}]}
    assert g.execute_state(state)['decisions']==['B']


def test_reference_unknown_or_illegal_operator_rejected():
    state=make_state()
    state['actions'][0]['score']['args'][0]['args'][0]={'ref':'wrong'}
    with pytest.raises(ValueError,match='UNKNOWN_REFERENCE'):
        g.execute_state(state)
    state=make_state();state['actions'][0]['score']={'op':'python','args':[]}
    with pytest.raises(ValueError,match='UNSUPPORTED_OPERATOR'):
        g.execute_state(state)


def test_reject_numeric_literals_inside_expression():
    state=make_state()
    state['actions'][0]['score']={'op':'mul','args':[{'const':'9'},{'ref':'n0'}]}
    with pytest.raises(ValueError,match='INVALID_EXPRESSION'):
        g.execute_state(state)


def test_abstention_and_division_by_zero():
    assert g.execute_state({'status':'insufficient_information',
                            'missing':['payoff matrix']})['status']=='insufficient_information'
    state={'status':'ok','objective':'maximize','facts':{'x':'1','zero':'0'},
           'actions':[{'name':'only','score':{'op':'div','args':[{'ref':'x'},{'ref':'zero'}]}}]}
    with pytest.raises(ValueError,match='DIVISION_BY_ZERO'):
        g.execute_state(state)




def test_roles_separate_player_and_opponent_even_when_names_match():
    import random
    row=corpus.t08_example(random.Random(47),'train',0)
    prompt=row['prompt']
    roles=row['role_spans']
    assert {r['name'] for r in roles if r['role']=='player'} == {'Hawk','Dove'}
    assert any(r['role']=='opponent' for r in roles)
    assert all(prompt[r['start']:r['end']]==r['name'] for r in roles)
    assert '\ue000' not in prompt and '\ue001' not in prompt
    p=core.prepare_prompt(CharTokenizer(),prompt)
    labels=trainer.supervision(row,p)
    assert int((labels['tags']==PLAYER_BEGIN).sum())>=2
    assert int((labels['tags']==OPP_BEGIN).sum())>=1
    assert labels['opponent_mentions']>=1


def test_candidate_coupled_loss_penalizes_predicted_opponent_as_winner():
    # Player action A starts at position 1; opponent mention at position 3.
    # Both are in the denominator. A teacher-forced gold-action-only softmax
    # would be blind to the opponent's high score here.
    tag_targets=torch.tensor([0,PLAYER_BEGIN,0,OPP_BEGIN,0])
    eligible=torch.ones(5,dtype=torch.bool)
    logits=torch.zeros((5,5),requires_grad=True)
    preferences=torch.tensor([0.,0.,0.,5.,0.],requires_grad=True)
    args=({'A':torch.tensor([1])},{'A'})
    loss_high,parts=direct_loss(logits,preferences,tag_targets,eligible,*args)
    low=preferences.detach().clone();low[3]=-5
    loss_low,_=direct_loss(logits,low,tag_targets,eligible,*args)
    assert loss_high.item()>loss_low.item()+1.0
    loss_high.backward()
    assert preferences.grad is not None and preferences.grad[3]>0
    assert logits.grad is not None
    assert torch.isfinite(logits.grad).all()
    assert parts['choice_loss']>0


def test_decoding_opponent_is_not_player_and_multiword_is_complete():
    prompt='You can play Move C or Retreat. Opponent can play Red or Blue.'
    p=core.prepare_prompt(CharTokenizer(),prompt)
    n=len(p['offsets']); tags=[0]*n; scores=[0.0]*n
    for role,name,score in [('player','Move C',7),('player','Retreat',1),
                             ('opponent','Red',999),('opponent','Blue',999)]:
        a=p['prompt_start']+prompt.index(name)
        b=a+len(name)
        tags[a]=PLAYER_BEGIN if role=='player' else OPP_BEGIN
        for i in range(a+1,b):
            tags[i]=PLAYER_INSIDE if role=='player' else OPP_INSIDE
        scores[a]=score
    decoded=decode_action_spans(tags,scores,p['eligible'].tolist(),
                    p['offsets'],p['prompt_start'],prompt)
    assert [x['name'] for x in decoded]==['Move C','Retreat']
    assert all(x['name'] not in ('Move','Red','Blue') for x in decoded)


def test_training_rejects_old_unannotated_corpus(tmp_path):
    dataset=tmp_path/'old.jsonl'
    dataset.write_text('\n'.join(json.dumps({'prompt':f'prompt-{split}',
         'state':state(), 'split':split}) for split in ('train','dev','test')))
    with pytest.raises(ValueError,match='ROLE_ANNOTATIONS_REQUIRED'):
        trainer.load_dataset(dataset)


def test_direct_v3_checkpoint_rejected_before_model_load(tmp_path):
    cp=fake_checkpoint(tmp_path)
    manifest=json.loads((cp/'manifest.json').read_text())
    manifest['schema']='nanojev.gamer.v1.direct-neural-decision.v3'
    (cp/'manifest.json').write_text(json.dumps(manifest))
    with pytest.raises(ValueError,match='INVALID_OR_LEGACY'):
        core.query(checkpoint=cp,prompt='Jade or Quartz',device='cpu')


def test_training_role_offsets_fail_closed_on_wrong_label():
    prompt='Player action Quartz. Opponent Red.'
    row={'prompt':prompt,'split':'train','state':state(),
         'role_spans':[{'role':'player','name':'Quartz','start':0,'end':6}]}
    with pytest.raises(ValueError,match='ROLE_OFFSET_MISMATCH'):
        trainer.supervision(row,core.prepare_prompt(CharTokenizer(),prompt))
