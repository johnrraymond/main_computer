"""Focused anti-shortcut tests for the unified G1..G7 architecture."""
from __future__ import annotations
import json
from pathlib import Path
from types import SimpleNamespace
import pytest
import torch
from tools import nanojev_gamer_v1_graph_contract as c
from tools import nanojev_gamer_v1_graph_head as h
from tools import nanojev_gamer_v1_graph_corpus as corpus
from tools import nanojev_gamer_v1_graph_curriculum as cur
from tools import nanojev_gamer_v1_graph_system as system
from tools import nanojev_gamer_v1_backbone_first as backbone


@pytest.fixture(autouse=True)
def single_thread():
    old=torch.get_num_threads();torch.set_num_threads(1)
    yield
    torch.set_num_threads(old)


class CharTokenizer:
    is_fast=True
    def apply_chat_template(self,messages,tokenize=False,add_generation_prompt=False,enable_thinking=False):
        assert messages[0]['role']=='user'
        assert not add_generation_prompt
        return '<user>'+messages[0]['content']+'</user>'
    def __call__(self,text,**kwargs):
        n=len(text)
        return {'input_ids':torch.tensor([[ord(ch)%100 for ch in text]]),
                'attention_mask':torch.ones((1,n),dtype=torch.long),
                'offset_mapping':torch.tensor([[[i,i+1] for i in range(n)]])}


class FrozenQwen(torch.nn.Module):
    def __init__(self):
        super().__init__()
        self.config=SimpleNamespace(hidden_size=16)
        self.embedding=torch.nn.Embedding(100,16)
        self.calls=0
        for p in self.parameters():p.requires_grad_(False)
    def forward(self,*,input_ids,attention_mask,output_hidden_states,use_cache):
        assert output_hidden_states is True and use_cache is False
        self.calls+=1
        return SimpleNamespace(hidden_states=(self.embedding(input_ids),))
    def generate(self,*args,**kwargs):
        raise AssertionError('GAMER_TOKEN_GENERATION_IS_PROHIBITED')


def fake_source(tmp_path):
    source=tmp_path/'adapter';source.mkdir()
    (source/'adapter_config.json').write_text('{}')
    (source/'adapter_model.safetensors').write_bytes(b'protected-source')
    return source


def fake_model(monkeypatch):
    model=FrozenQwen();tok=CharTokenizer()
    monkeypatch.setattr(backbone,'model_components',lambda *args:(model,tok))
    return model,tok


def dataset(tmp_path,train=6,dev=4,challenge=4,test=4):
    path=tmp_path/'dataset.jsonl'
    corpus.generate(path,train=train,dev=dev,challenge=challenge,test=test,seed=17)
    return path


def test_no_game_specific_contract_and_exact_dag():
    graph=c.empty_graph()
    graph['nodes']=[{'id':'u','kind':'action','span':[0,4]},
                    {'id':'v','kind':'action','span':[5,9]},
                    {'id':'x','kind':'quantity','value':'9/2'},
                    {'id':'y','kind':'quantity','value':'-3'},
                    {'id':'z','kind':'operation','operator':'add'},
                    {'id':'w','kind':'operation','operator':'mul'}]
    graph['edges']=[{'src':'z','dst':'x','type':'operand','position':0},
                    {'src':'z','dst':'y','type':'operand','position':1},
                    {'src':'w','dst':'x','type':'operand','position':0},
                    {'src':'w','dst':'x','type':'operand','position':1},
                    {'src':'u','dst':'z','type':'score'},
                    {'src':'v','dst':'w','type':'score'}]
    graph['objective']={'kind':'maximize','candidates':['u','v']}
    assert c.execute_graph(graph)['decisions']==['v']
    graph['objective']['kind']='minimize'
    assert c.execute_graph(graph)['decisions']==['u']
    assert 'Hawk' not in repr(c.OPERATORS)


def test_missing_rules_are_not_guessed():
    graph=c.empty_graph()
    graph['nodes']=[{'id':'a','kind':'action'}]
    graph['objective']={'kind':'maximize','candidates':['a']}
    with pytest.raises(ValueError,match='ONE_SCORE'):
        c.execute_graph(graph)


def test_graph_cycles_and_unsupported_objectives_fail_closed():
    g=c.empty_graph()
    g['nodes']=[{'id':'a','kind':'action'},{'id':'z','kind':'operation','operator':'add'}]
    g['edges']=[{'src':'a','dst':'z','type':'score'},
                {'src':'z','dst':'z','type':'operand','position':0},
                {'src':'z','dst':'z','type':'operand','position':1}]
    g['objective']={'kind':'maximize','candidates':['a']}
    with pytest.raises(ValueError,match='CYCLE'):c.execute_graph(g)
    g['objective']['kind']='equilibrium'
    with pytest.raises(ValueError,match='OBJECTIVE_UNEXECUTABLE'):c.execute_graph(g)


def test_corpus_annotated_from_author_not_runtime_parser(tmp_path):
    path=dataset(tmp_path)
    rows=cur.load_rows(path)
    assert len(rows)==18
    assert set(r['family'] for r in rows)=={'encounter','allocation'}
    for row in rows:
        assert c.execute_graph(row['graph'])['decisions']==row['expected_decisions']
        assert {n['kind'] for n in row['graph']['nodes']} >= {'actor','action','quantity','operation'}
        assert '[[' not in row['prompt']
        assert 'train' not in row['prompt']
    assert set(corpus.TRAIN_WORDS).isdisjoint(corpus.TEST_WORDS)
    assert set(corpus.CHALLENGE_WORDS).isdisjoint(corpus.TRAIN_WORDS)


def test_full_architecture_allocated_before_stage_one():
    head=h.GamerGraphHead(hidden_size=16,width=32,latent_slots=16,memory_slots=8)
    params=set(name.split('.')[0] for name in head.state_dict())
    assert {'span_start','span_end','span_pool','span_width_embed','span_classifier',
            'binding','relationship','operator','operand_query',
            'objective','memory_query','memory_write','latent_query'} <= params
    assert len(cur.plan()['stages'])==7
    assert cur.plan()['initially_unlocked']=='G1'
    for step in c.STAGES:
        assert cur.trainable_stage(head,step)>0
        if step=='G1':
            assert not head.binding.weight.requires_grad
            assert not head.operator.weight.requires_grad


def test_forward_non_autoregressive_and_gradient_only_to_g1():
    model=FrozenQwen();tok=CharTokenizer()
    prompt='The planner compares Jade and Quartz given 7 and 11.'
    prepared=backbone.prepare_prompt(tok,prompt)
    hidden=backbone.frozen_context(model,prepared,device='cpu')
    head=h.GamerGraphHead(hidden_size=16,width=32,latent_slots=8,memory_slots=8)
    cur.trainable_stage(head,'G1')
    out=head(hidden,eligible=prepared['eligible'],active_stage='G1')
    assert set(out)=={'span_logits','span_valid','global_state'}
    loss=out['span_logits'][out['span_valid']].square().mean()
    loss.backward()
    assert head.span_classifier.weight.grad is not None
    assert head.binding.weight.grad is None
    assert all(p.grad is None for p in model.parameters())
    assert model.calls==1


def test_stage_losses_accept_variable_node_and_operator_graphs(tmp_path):
    path=dataset(tmp_path)
    row=next(r for r in cur.load_rows(path) if r['family']=='encounter')
    prep=backbone.prepare_prompt(CharTokenizer(),row['prompt'],max_input_tokens=2048)
    labels=cur.aligned_labels(prep,row)
    head=h.GamerGraphHead(hidden_size=16,width=32,latent_slots=16,memory_slots=8)
    hidden=torch.randn(len(prep['offsets']),16)
    for stage in c.STAGES[:5]:
        head.zero_grad()
        cur.trainable_stage(head,stage)
        out=head(hidden,eligible=prep['eligible'],
                 training_spans=labels['spans'] if stage!='G1' else None,
                 active_stage=stage)
        loss=cur.stage_loss(head,out,labels,row,stage)
        assert bool(torch.isfinite(loss))
        loss.backward()
        assert any(p.grad is not None for p in head.parameters() if p.requires_grad)
        assert all(p.grad is None for p in head.parameters() if not p.requires_grad)


def test_memory_cannot_write_before_gate():
    head=h.GamerGraphHead(hidden_size=16,width=32,latent_slots=4,memory_slots=8)
    memory=torch.zeros(8,32)
    with pytest.raises(PermissionError,match='G6_LOCKED'):
        head.updated_memory(memory,{'global_state':torch.randn(32)},slot=0)
    new=head.updated_memory(memory,{'global_state':torch.randn(32)},slot=0,authorized=True)
    assert not torch.equal(new[0],memory[0])
    assert torch.equal(new[1:],memory[1:])


def test_future_train_rejected_before_backbone_load(tmp_path):
    data=dataset(tmp_path);src=fake_source(tmp_path)
    with pytest.raises(PermissionError,match='STAGE_LOCKED'):
        cur.train(dataset=data,output=tmp_path/'nope',source_adapter=src,
                  stage='G2',device='cpu',steps=1)
    assert not (tmp_path/'nope').exists()


def test_g1_real_training_and_4_case_evaluation_with_fake_frozen_qwen(tmp_path,monkeypatch):
    model,tok=fake_model(monkeypatch)
    source=fake_source(tmp_path);data=dataset(tmp_path)
    before={k:v.clone() for k,v in model.state_dict().items()}
    manifest=cur.train(dataset=data,output=tmp_path/'head',source_adapter=source,
                       device='cpu',steps=2,accum=1,train_limit=4,width=32,
                       latent_slots=16,memory_slots=8,max_length=2048)
    assert manifest['stage']=='G1' and manifest['all_stages_instantiated']==list(c.STAGES)
    assert manifest['generated_tokens']==0
    assert all(torch.equal(before[k],v) for k,v in model.state_dict().items())
    assert backbone.adapter_fingerprint(source)==manifest['source_adapter_sha256']
    report=cur.evaluate(dataset=data,checkpoint=tmp_path/'head',
                        output=tmp_path/'dev',device='cpu',split='dev',limit=0,
                        ablation_limit=2,max_length=2048)
    assert report['cases']==4 and report['stage']=='G1'
    assert report['generated_tokens']==0 and not report['teacher_graph_at_inference']
    with pytest.raises(PermissionError,match='BLIND_TEST_SEALED'):
        cur.evaluate(dataset=data,checkpoint=tmp_path/'head',output=tmp_path/'blind',
                     split='test',device='cpu')
    result=system.query(checkpoint=tmp_path/'head',prompt='A new policy: Jade or Quartz?',device='cpu')
    assert result['generated_tokens']==0
    assert result['graph']['objective'] is None
    assert result['status']=='NEURAL_GRAPH_INCOMPLETE_UNCERTIFIED'
    with pytest.raises(PermissionError,match='G6'):
        system.acquire(checkpoint=tmp_path/'head',certificate='unavailable',
                       teaching_prompt='rules',output=tmp_path/'memory.safetensors',device='cpu')
    return_result=cur.certify(checkpoint=tmp_path/'head',
        dev_report=tmp_path/'dev'/'report.json',
        challenge_report=_challenge_report(tmp_path,cur,data,device='cpu'),
        output=tmp_path/'gate')
    assert return_result['status']=='DEVELOPMENT_GATE_BLOCKED'
    assert return_result['production_promotion'] is False


def _challenge_report(tmp_path,cur,data,device='cpu'):
    cur.evaluate(dataset=data,checkpoint=tmp_path/'head',output=tmp_path/'challenge',
                 device=device,split='challenge',limit=0,ablation_limit=2)
    return tmp_path/'challenge'/'report.json'


def test_inference_has_no_training_corpus_or_game_identity_import():
    text=Path(system.__file__).read_text(encoding='utf-8')
    for forbidden in ('TRAIN_NAMES','T08_TRAIN','_word_vector','spans_from_prompt',
                      '.generate(', 'model.generate(', 'hawk_count', 'dove_count'):
        assert forbidden not in text


def test_checkpoint_hash_tampering_rejected(tmp_path,monkeypatch):
    fake_model(monkeypatch);src=fake_source(tmp_path);data=dataset(tmp_path)
    cur.train(dataset=data,output=tmp_path/'head',source_adapter=src,
              device='cpu',steps=1,accum=1,train_limit=2,width=32)
    file=tmp_path/'head'/cur.HEAD_FILE
    with file.open('ab') as f:f.write(b'tampered')
    with pytest.raises(ValueError,match='HEAD_HASH_MISMATCH'):
        cur._load_checkpoint(tmp_path/'head')


def test_relationships_have_real_positive_supervision_not_all_none(tmp_path):
    rows=cur.load_rows(dataset(tmp_path))
    for row in rows:
        nodes={n['id']:n for n in row['graph']['nodes']}
        positive=[e for e in row['graph']['edges'] if e['type'] in c.RELATIONS
                  and 'span' in nodes[e['src']] and 'span' in nodes[e['dst']]]
        assert positive, 'G3 must not pass by predicting no relationships'
        assert any(e['type'] in ('causes','depends_on') for e in positive)


def test_stage_four_scores_supervised_as_score_not_cause(tmp_path):
    row=cur.load_rows(dataset(tmp_path))[0]
    prep=backbone.prepare_prompt(CharTokenizer(),row['prompt'])
    labels=cur.aligned_labels(prep,row)
    head=h.GamerGraphHead(hidden_size=16,width=32,latent_slots=16,memory_slots=8)
    out=head(torch.randn(len(prep['offsets']),16),eligible=prep['eligible'],
             training_spans=labels['spans'],active_stage='G4')
    assert h.REL_CLASSES.index('score') != h.REL_CLASSES.index('causes')
    assert out['relationship_logits'].shape[-1]==len(h.REL_CLASSES)
    assert cur._align_graph_nodes(labels,row,out)[labels['latent_nodes'][0]['id']]>=out['surface_count']
    loss=cur.stage_loss(head,out,labels,row,'G4')
    assert torch.isfinite(loss)


def test_training_only_annotations_not_used_by_query(tmp_path,monkeypatch):
    fake_model(monkeypatch);data=dataset(tmp_path);source=fake_source(tmp_path)
    cur.train(dataset=data,output=tmp_path/'checkpoint',source_adapter=source,
              device='cpu',steps=1,accum=1,train_limit=2,width=32)
    result=system.query(checkpoint=tmp_path/'checkpoint',
        prompt='Arbitrary unknown game: Sigma or Aurora?',device='cpu')
    assert 'expected_decisions' not in result
    assert 'object_spans' not in result
    assert result['labels_used_at_inference'] is False
    assert result['game_selected_externally'] is False


def test_stage_specific_ablation_disables_the_learned_head():
    head=h.GamerGraphHead(hidden_size=16,width=32,latent_slots=4,memory_slots=8)
    result=head(torch.randn(30,16),eligible=torch.ones(30,dtype=torch.bool),
                training_spans=[{'kind':'action','token_first':5,'token_last':8}],
                active_stage='G2')
    ablated=cur._ablate_stage_head(result,'G2')
    assert torch.equal(ablated['binding_logits'],torch.zeros_like(ablated['binding_logits']))
    assert not torch.equal(result['binding_logits'],ablated['binding_logits'])


def test_g6_requires_episode_supervision_not_retrieval_inference_oracle(tmp_path):
    path=dataset(tmp_path)
    row=cur.load_rows(path)[0]
    prep=backbone.prepare_prompt(CharTokenizer(),row['prompt'])
    labels=cur.aligned_labels(prep,row)
    head=h.GamerGraphHead(hidden_size=16,width=32,latent_slots=16,memory_slots=8)
    bank=torch.randn(8,32)
    out=head(torch.randn(len(prep['offsets']),16),eligible=prep['eligible'],
             training_spans=labels['spans'],memory=bank,active_stage='G6')
    with pytest.raises(ValueError,match='REQUIRES_ACQUISITION_EPISODES'):
        cur.stage_loss(head,out,labels,row,'G6',memory=bank)
    valid={**row,'memory_target':3}
    loss=cur.stage_loss(head,out,labels,valid,'G6',memory=bank)
    assert torch.isfinite(loss)


def test_g7_refuses_right_winner_from_wrong_arithmetic(tmp_path):
    row=cur.load_rows(dataset(tmp_path))[1]  # two possible project alternatives
    gold=row['graph']
    from copy import deepcopy
    wrong=deepcopy(gold)
    # Pick a quantity and change it to a nearby value. Winner may remain same,
    # but the integrated graph certificate must still reject fabricated math.
    for node in wrong['nodes']:
        if node['kind']=='quantity' and 'value' in node:
            node['value']=str(int(node['value'])+1)
            break
    # Graph execution itself still works, but the G7 comparison must reject it.
    assert c.execute_graph(wrong)['status']=='evaluated'
    assert not cur._compare(wrong,gold,'G7')[0]


def test_only_g1_approved_without_certificate_and_parent_hash_enforced(tmp_path,monkeypatch):
    fake_model(monkeypatch);data=dataset(tmp_path);source=fake_source(tmp_path)
    parent=tmp_path/'G1'
    m=cur.train(dataset=data,output=parent,source_adapter=source,
                device='cpu',steps=1,accum=1,train_limit=2,width=32)
    with pytest.raises(PermissionError,match='CERT_REQUIRED'):
        cur.train(dataset=data,output=tmp_path/'G2',source_adapter=source,
                  parent_checkpoint=parent,stage='G2',device='cpu',steps=1)
    cert=tmp_path/'cert.json'
    cert.write_text(json.dumps({'schema':cur.SCHEMA,'status':'DEVELOPMENT_GATE_PASSED',
        'certified_stage':'G1','head_sha256':'incorrect-hash',
        'dataset_sha256':m['dataset_sha256'],
        'checkpoint_path':str(parent.resolve())}))
    with pytest.raises(PermissionError,match='CERTIFICATE_INVALID'):
        cur.train(dataset=data,output=tmp_path/'G2',source_adapter=source,
                  parent_checkpoint=parent,prerequisite_cert=cert,stage='G2',
                  device='cpu',steps=1)


def test_new_stage_does_not_mutate_previous_learned_parameters(tmp_path,monkeypatch):
    fake_model(monkeypatch);data=dataset(tmp_path);source=fake_source(tmp_path)
    parent=tmp_path/'G1'
    m=cur.train(dataset=data,output=parent,source_adapter=source,
                device='cpu',steps=1,accum=1,train_limit=2,width=32)
    cert=tmp_path/'cert.json'
    # Simulated valid certificate for testing parameter-stage isolation.
    # No user-facing score or certification is fabricated by this test.
    cert.write_text(json.dumps({'schema':cur.SCHEMA,'status':'DEVELOPMENT_GATE_PASSED',
        'certified_stage':'G1','head_sha256':m['head_sha256'],
        'dataset_sha256':m['dataset_sha256'],
        'checkpoint_path':str(parent.resolve())}))
    child=tmp_path/'G2'
    # Only the certificate verifier is mocked to isolate downstream training.
    # Production verification requires untampered passing reports, not this stub.
    monkeypatch.setattr(cur,'_verify_certification',lambda *args:{'unit_test_only':True})
    nxt=cur.train(dataset=data,output=child,source_adapter=source,
                  parent_checkpoint=parent,prerequisite_cert=cert,
                  stage='G2',device='cpu',steps=1,accum=1,train_limit=2)
    from safetensors.torch import load_file
    old=load_file(str(parent/cur.HEAD_FILE))
    new=load_file(str(child/cur.HEAD_FILE))
    for key in old:
        if not any(key.startswith(pre) for pre in cur.STAGE_MODULES['G2']):
            assert torch.equal(old[key],new[key]),key
    assert nxt['stage']=='G2'
    assert nxt['parent_checkpoint']==str(parent.resolve())


def _score_spans(eligible, positives, width=24):
    """Unit-only fixture: force neural span scores, never used by GAMER inference."""
    size=len(eligible)
    logits=torch.full((size,width,h.SPAN_CLASS_COUNT),-10.0)
    logits[...,0]=10.0
    valid=torch.zeros((size,width),dtype=torch.bool)
    for i in range(size):
        for length in range(1,width+1):
            if i+length<=size and all(eligible[i:i+length]):
                valid[i,length-1]=True
    for start,end,kind in positives:
        index=h.SPAN_KINDS.index(kind)+1
        logits[start,end-start,index]=12.0
        logits[start,end-start,0]=-12.0
    return {'span_logits':logits,'span_valid':valid}


def test_g1_qwen_space_prefixed_token_offsets_are_not_semantic_errors():
    """Tokenizer-space includes blanks; author-space labels deliberately do not."""
    pieces = ['<u>', 'A', ' contender', ' chooses', ' Move', ' B', ',',
              ' and', ' Yield', '.', '</u>']
    formatted = ''.join(pieces)
    prompt = 'A contender chooses Move B, and Yield.'
    assert formatted == '<u>' + prompt + '</u>'
    offsets=[];pos=0
    for piece in pieces:
        offsets.append((pos,pos+len(piece)));pos+=len(piece)
    p={'offsets':offsets,'prompt_start':3,'prompt':prompt,
       'eligible':torch.tensor([False]+[True]*9+[False])}
    output=_score_spans(p['eligible'].tolist(),[(2,2,'actor'),(4,5,'action'),(8,8,'action')])
    # Override with the correct unambiguous neural span predictions.
    spans=h.decode_neural_spans(output['span_logits'],output['span_valid'])
    expected={(prompt.index(name),prompt.index(name)+len(name),kind)
              for kind,name in [('actor','contender'),('action','Move B'),
                                ('action','Yield')]}
    assert cur._char_objects(output,p,normalize_whitespace=False)!=expected
    assert cur._char_objects(output,p)==expected
    graph,surfaces=h.decode_graph({'spans':spans},p,unlocked='G1')
    assert {(n['span'][0],n['span'][1],n['kind']) for n in graph['nodes']}==expected
    assert {x['surface'] for x in surfaces}=={'contender','Move B','Yield'}


def test_g1_boundary_normalization_is_not_a_word_or_punctuation_parser():
    text = 'The agent chooses R&D, but may Yield.'
    # Use a synthetic tokenizer offset spanning the space and the symbol name.
    p = {'prompt': text, 'prompt_start': 0,
         'offsets': [(0, 3), (3, 9), (9, 18), (18, 23), (23, 27), (27, len(text))]}
    a = text.index(' R&D')
    p['offsets'][3] = (a, a + len(' R&D'))
    assert h.token_span_char_bounds({'token_first':3,'token_last':3},p) == (a+1,a+4)
    # Whitespace is the only thing normalized; punctuation remains untouched.
    assert h.token_span_char_bounds({'token_first':3,'token_last':3},p,
                                    normalize_whitespace=False) == (a,a+4)
    p['offsets'][0] = (0, 1)
    p['prompt'] = ' ' + text[1:]
    assert h.token_span_char_bounds({'token_first':0,'token_last':0},p) is None


def test_public_g1_query_uses_same_normalized_spans_as_evaluation(monkeypatch):
    prompt='A trader chooses Stone.'
    pieces=['<u>','A',' trader',' chooses',' Stone','.','</u>']
    assert ''.join(pieces)=='<u>'+prompt+'</u>'
    offsets=[];pos=0
    for piece in pieces:
        offsets.append((pos,pos+len(piece)));pos+=len(piece)
    prepared={'prompt':prompt,'prompt_start':3,'offsets':offsets,
              'eligible':torch.tensor([False,True,True,True,True,True,False])}
    output=_score_spans(prepared['eligible'].tolist(),[(2,2,'actor'),(4,4,'action')])
    class StubHead:
        def __call__(self,hidden,**kwargs):return output
    monkeypatch.setattr(system,'_verified_head',lambda checkpoint,device:(
        StubHead(),{'stage':'G1','model_id':'mock','source_adapter':'mock',
                    'head_sha256':'fake-head'}))
    monkeypatch.setattr(backbone,'model_components',lambda *args:(None,None))
    monkeypatch.setattr(backbone,'prepare_prompt',lambda *args,**kwargs:prepared)
    monkeypatch.setattr(backbone,'frozen_context',lambda *args,**kwargs:
                        torch.zeros(len(pieces),16))
    result=system.query(checkpoint='fake',prompt=prompt,device='cpu')
    assert {item['surface'] for item in result['objects']}=={'trader','Stone'}
    assert {tuple(item['span']) for item in result['objects']}=={
        (prompt.index('trader'),prompt.index('trader')+len('trader')),
        (prompt.index('Stone'),prompt.index('Stone')+len('Stone'))}
    assert result['generated_tokens']==0 and not result['semantic_parser_used']


def test_g1_joint_span_rejects_fragmented_entity_candidates():
    """A multi-subword unknown name is one proposed object, not BIO fragments."""
    eligible=[True]*9
    full=_score_spans(eligible,[(1,3,'action'),(6,7,'action')],width=9)
    logits=full['span_logits']
    # Competing high-scoring partials must lose to the correct complete name.
    for a,b in [(1,1),(2,3),(6,6),(7,7)]:
        idx=h.SPAN_KINDS.index('action')+1
        logits[a,b-a,idx]=5.0
        logits[a,b-a,0]=0.0
    result=h.decode_neural_spans(logits,full['span_valid'])
    assert [(s['token_first'],s['token_last'],s['kind']) for s in result]==[
        (1,3,'action'),(6,7,'action')]


def test_g1_span_loss_teaches_complete_boundaries_and_mines_fragments():
    head=h.GamerGraphHead(hidden_size=16,width=32,latent_slots=4,memory_slots=8,
                          max_span_tokens=8)
    hidden=torch.randn(12,16)
    out=head(hidden,eligible=torch.ones(12,dtype=torch.bool),active_stage='G1')
    labels={'spans':[{'kind':'action','token_first':2,'token_last':4},
                     {'kind':'actor','token_first':7,'token_last':8}]}
    value=cur.stage_loss(head,out,labels,{},'G1')
    assert torch.isfinite(value)
    value.backward()
    assert head.span_start.weight.grad is not None
    assert head.span_end.weight.grad is not None
    assert head.span_pool.weight.grad is not None
    assert head.span_width_embed.weight.grad is not None
    assert head.span_classifier.weight.grad is not None


def test_g1_span_capacity_violation_fails_closed():
    head=h.GamerGraphHead(hidden_size=16,width=32,latent_slots=4,memory_slots=8,
                          max_span_tokens=3)
    out=head(torch.randn(10,16),eligible=torch.ones(10,dtype=torch.bool),active_stage='G1')
    label={'spans':[{'kind':'action','token_first':2,'token_last':7}]}
    with pytest.raises(ValueError,match='EXCEEDS_MAX_SPAN_TOKENS'):
        cur.stage_loss(head,out,label,{},'G1')


def test_g1_split_name_inventories_disjoint_and_diverse():
    pools=corpus.NAME_POOLS
    assert all(not set(pools[a]) & set(pools[b])
               for a in pools for b in pools if a!=b)
    assert any(' ' in name for name in pools['train'])
    assert any('-' in name for name in pools['train'])
    assert len(pools['train'])>100


def test_g1_pre_span_checkpoint_is_incompatible(tmp_path,monkeypatch):
    fake_model(monkeypatch);data=dataset(tmp_path);source=fake_source(tmp_path)
    path=tmp_path/'G1'
    cur.train(dataset=data,output=path,source_adapter=source,
              device='cpu',steps=1,accum=1,train_limit=2,width=32)
    doc=json.loads((path/cur.MANIFEST).read_text())
    doc['head']['object_extractor']='independent-BIO'
    (path/cur.MANIFEST).write_text(json.dumps(doc))
    with pytest.raises(ValueError,match='BAD_CHECKPOINT_SCHEMA'):
        cur._load_checkpoint(path)


def test_g1_span_training_penalizes_fragment_even_with_correct_full_span():
    """The hard-negative objective must disfavor a falsely split subword."""
    head=h.GamerGraphHead(hidden_size=16,width=32,latent_slots=4,memory_slots=8,
                          max_span_tokens=8)
    gold={'spans':[{'kind':'action','token_first':2,'token_last':5}]}
    base=torch.full((10,8,h.SPAN_CLASS_COUNT),-4.0)
    base[...,0]=4.0
    action=h.SPAN_KINDS.index('action')+1
    base[2,3,action]=8.0
    base[2,3,0]=-8.0
    good={'span_logits':base.clone(),'span_valid':torch.ones(10,8,dtype=torch.bool)}
    damaged=base.clone()
    damaged[2,1,action]=8.0
    damaged[2,1,0]=-8.0
    bad={'span_logits':damaged,'span_valid':good['span_valid']}
    loss_good=cur.stage_loss(head,good,gold,{},'G1')
    loss_bad=cur.stage_loss(head,bad,gold,{},'G1')
    assert loss_bad>loss_good+0.5

# G1 score attribution intentionally does not affect training, live decoding,
# evaluation, G1 gate thresholds, or saved checkpoint weights.
from tools import nanojev_gamer_v1_graph_diagnostic as diagnostic


def _diagnostic_case(predictions, gold_spans, size=14):
    """Synthetically scored candidate spans; no preselected words or decoder rules."""
    logits=torch.full((size,8,h.SPAN_CLASS_COUNT),-10.0)
    logits[...,0]=10.0
    valid=torch.zeros((size,8),dtype=torch.bool)
    for a in range(size):
        valid[a,:min(size-a,8)]=True
    for a,b,logit_values in predictions:
        logits[a,b-a]=torch.tensor(logit_values,dtype=torch.float32)
    prompt=''.join(chr(97+(i%26)) for i in range(size))
    p={'prompt':prompt,'offsets':[(i,i+1) for i in range(size)],'prompt_start':0}
    row={'object_spans':[{'start':a,'end':b+1,'kind':k} for a,b,k in gold_spans]}
    labels={'spans':[{'token_first':a,'token_last':b,'kind':k} for a,b,k in gold_spans]}
    return {'span_logits':logits,'span_valid':valid},p,row,labels


def test_g1_diagnostic_assigns_below_threshold_and_threshold_sweep_without_oracle():
    # The correct type wins, but below the 0.50 cutoff.
    labels=[0.0]+[-3.0]*(h.SPAN_CLASS_COUNT-1)
    action_id=h.SPAN_KINDS.index('action')+1
    labels[action_id]=0.05
    out,p,row,gold=_diagnostic_case([(2,3,labels)],[(2,3,'action')])
    result=diagnostic.attribute_case(out,p,row,gold)
    assert result['miss_reasons']=={'below_threshold':1}
    assert result['candidate_correct_type_count']==1
    assert result['misses'][0]['gold_probability']<0.50
    assert diagnostic._char_set(h.decode_neural_spans(out['span_logits'],
                out['span_valid'],confidence=.20),p)=={(2,4,'action')}
    assert diagnostic._char_set(h.decode_neural_spans(out['span_logits'],
                out['span_valid'],confidence=.50),p)==set()


def test_g1_diagnostic_identifies_wrong_type_and_null_wins():
    actor=h.SPAN_KINDS.index('actor')+1
    action=h.SPAN_KINDS.index('action')+1
    wrong=[-8.0]*h.SPAN_CLASS_COUNT;wrong[0]=0;wrong[action]=5
    absent=[-8.0]*h.SPAN_CLASS_COUNT;absent[0]=6;absent[actor]=0
    out,p,row,gold=_diagnostic_case([(2,3,wrong),(6,6,absent)],
                                   [(2,3,'actor'),(6,6,'actor')])
    report=diagnostic.attribute_case(out,p,row,gold)
    assert report['miss_reasons']=={'wrong_type':1,'null_wins':1}
    assert report['candidate_correct_type_count']==0


def test_g1_diagnostic_identifies_greedy_overlap_blocker():
    action=h.SPAN_KINDS.index('action')+1
    correct=[-10.0]*h.SPAN_CLASS_COUNT;correct[0]=0;correct[action]=4.0
    wrong=[-10.0]*h.SPAN_CLASS_COUNT;wrong[0]=0;wrong[action]=7.0
    out,p,row,gold=_diagnostic_case([(2,5,correct),(4,5,wrong)],[(2,5,'action')])
    report=diagnostic.attribute_case(out,p,row,gold)
    assert report['miss_reasons']=={'overlap_blocked':1}
    assert report['misses'][0]['blocking_proposals']==[
        {'kind':'action','token_span':[4,5],'char_span':[4,6]}]
    assert report['extra_objects'][0]['span']==[4,6]


def test_g1_diagnostic_only_dev_and_frozen_checkpoint(tmp_path,monkeypatch):
    fake_model(monkeypatch)
    data=dataset(tmp_path);source=fake_source(tmp_path)
    checkpoint=tmp_path/'G1'
    manifest=cur.train(dataset=data,output=checkpoint,source_adapter=source,
                       device='cpu',steps=1,accum=1,train_limit=2,width=32)
    saved=(checkpoint/cur.HEAD_FILE).read_bytes()
    summary=diagnostic.diagnose(dataset=data,checkpoint=checkpoint,
                                output=tmp_path/'diagnose',device='cpu',limit=2)
    assert summary['status']=='DIAGNOSTIC_ONLY_NOT_A_GATE'
    assert summary['cases']==2
    assert summary['checkpoint_head_sha256']==manifest['head_sha256']
    assert summary['checkpoint_mutated'] is False
    assert summary['certification_authorized'] is False
    assert set(summary['threshold_sweep']) >= {'0.05','0.50','0.95'}
    assert (tmp_path/'diagnose'/'summary.json').exists()
    # Attribution must reproduce the production decoder at the fixed .50 cutoff.
    original=cur.evaluate(dataset=data,checkpoint=checkpoint,
                          output=tmp_path/'original',device='cpu',split='dev',
                          limit=2,ablation_limit=0)
    baseline=summary['threshold_sweep']['0.50']
    assert baseline['exact_rate']==original['metrics']['object_exact_rate']
    assert baseline['precision']==original['metrics']['object_micro_precision']
    assert baseline['recall']==original['metrics']['object_micro_recall']
    assert (checkpoint/cur.HEAD_FILE).read_bytes()==saved
    assert not (checkpoint/'certification.json').exists()
    with pytest.raises(FileExistsError,match='DIAGNOSTIC_IMMUTABLE'):
        diagnostic.diagnose(dataset=data,checkpoint=checkpoint,
                            output=tmp_path/'diagnose',device='cpu',limit=1)
    # The diagnostic cannot unlock G2.
    with pytest.raises(PermissionError,match='CERT_REQUIRED'):
        cur.train(dataset=data,output=tmp_path/'G2',source_adapter=source,
                  parent_checkpoint=checkpoint,stage='G2',device='cpu',steps=1)


def test_g1_diagnostic_cli_available_and_sealed_split_not_exposed(capsys):
    with pytest.raises(SystemExit) as err:
        cur.main(['diagnose-g1','--help'])
    assert err.value.code==0
    content=capsys.readouterr().out
    assert '--dataset' in content and '--checkpoint' in content and '--output' in content
    assert '--split' not in content and '--final-test' not in content

# G1 loss and refinement based on the read-only attribution that identified
# null-vs-object and decoder-overlap failures. They are supervised in train only.
def test_g1_positive_null_margin_directly_penalizes_gold_suppression():
    head=h.GamerGraphHead(hidden_size=16,width=32,latent_slots=4,memory_slots=8,max_span_tokens=6)
    labels={'spans':[{'kind':'actor','token_first':2,'token_last':3}]}
    log=torch.full((9,6,h.SPAN_CLASS_COUNT),-4.0)
    log[...,0]=4.0
    idx=h.SPAN_KINDS.index('actor')+1
    log[2,1,idx]=4.5
    log[2,1,0]=4.0
    valid=torch.ones((9,6),dtype=torch.bool)
    weak=cur.stage_loss(head,{'span_logits':log,'span_valid':valid},labels,{},'G1')
    better=log.clone();better[2,1,idx]=7.0
    strong=cur.stage_loss(head,{'span_logits':better,'span_valid':valid},labels,{},'G1')
    assert strong < weak


def test_g1_overlap_rank_loss_penalizes_stronger_competing_span():
    head=h.GamerGraphHead(hidden_size=16,width=32,latent_slots=4,memory_slots=8,max_span_tokens=6)
    labels={'spans':[{'kind':'action','token_first':2,'token_last':4}]}
    base=torch.full((9,6,h.SPAN_CLASS_COUNT),-4.0)
    base[...,0]=4.0
    idx=h.SPAN_KINDS.index('action')+1
    base[2,2,0]=-4; base[2,2,idx]=6
    valid=torch.ones((9,6),dtype=torch.bool)
    good=cur.stage_loss(head,{'span_logits':base,'span_valid':valid},labels,{},'G1')
    blocker=base.clone()
    blocker[3,1,0]=-4;blocker[3,1,idx]=9
    bad=cur.stage_loss(head,{'span_logits':blocker,'span_valid':valid},labels,{},'G1')
    assert bad > good


def test_g1_refinement_keeps_parent_and_locked_weights(tmp_path,monkeypatch):
    fake_model(monkeypatch)
    data=dataset(tmp_path);source=fake_source(tmp_path)
    original=tmp_path/'original'
    parent=cur.train(dataset=data,output=original,source_adapter=source,
                     device='cpu',steps=2,accum=1,width=32)
    original_bytes=(original/cur.HEAD_FILE).read_bytes()
    from safetensors.torch import load_file
    old=load_file(str(original/cur.HEAD_FILE))
    refined=cur.train(dataset=data,output=tmp_path/'refined',source_adapter=source,
                      device='cpu',steps=2,accum=1,refine_checkpoint=original)
    assert refined['g1_refined_head_sha256']==parent['head_sha256']
    assert refined['g1_training_objective']=='positive-null-overlap-structured-set-counterfactual-v1'
    assert refined['next_training_gate']=='LOCKED_PENDING_CERTIFICATION'
    assert (original/cur.HEAD_FILE).read_bytes()==original_bytes
    changed=load_file(str(tmp_path/'refined'/cur.HEAD_FILE))
    for k in old:
        if not any(k.startswith(p) for p in cur.STAGE_MODULES['G1']):
            assert torch.equal(old[k],changed[k]), k
    assert any(not torch.equal(old[k],changed[k]) for k in old
               if any(k.startswith(p) for p in cur.STAGE_MODULES['G1']))
    with pytest.raises(FileExistsError,match='OUTPUT_IMMUTABLE'):
        cur.train(dataset=data,output=tmp_path/'refined',source_adapter=source,
                  device='cpu',steps=1,accum=1,refine_checkpoint=original)


def test_g1_refinement_rejects_wrong_dataset_and_other_stages(tmp_path,monkeypatch):
    fake_model(monkeypatch)
    data=dataset(tmp_path);source=fake_source(tmp_path)
    parent=tmp_path/'g1'
    cur.train(dataset=data,output=parent,source_adapter=source,
              device='cpu',steps=1,accum=1,width=32)
    different=tmp_path/'different.jsonl'
    corpus.generate(different,train=6,dev=4,challenge=4,test=4,seed=18)
    with pytest.raises(ValueError,match='REFINE_DATASET_LINEAGE'):
        cur.train(dataset=different,output=tmp_path/'nope',source_adapter=source,
                  device='cpu',steps=1,accum=1,refine_checkpoint=parent)
    with pytest.raises(ValueError,match='REFINE_G1_ONLY'):
        cur.train(dataset=data,output=tmp_path/'nope',source_adapter=source,
                  device='cpu',steps=1,accum=1,stage='G2',refine_checkpoint=parent)
    assert not (tmp_path/'nope').exists()


def test_global_span_decoder_maximizes_whole_set_instead_of_greedy():
    from tools.nanojev_gamer_v1_graph_head import decode_greedy_spans, decode_neural_spans
    action=c.SPAN_KINDS.index('action')+1
    logits=torch.full((4,4,1+len(c.SPAN_KINDS)),-20.0)
    logits[...,0]=0.0
    valid=torch.zeros((4,4),dtype=torch.bool)
    valid[0,3]=True;valid[0,1]=True;valid[2,1]=True
    logits[0,3,action]=5.0
    logits[0,1,action]=4.0
    logits[2,1,action]=3.0
    greedy=decode_greedy_spans(logits,valid)
    global_choice=decode_neural_spans(logits,valid)
    assert [(s['token_first'],s['token_last']) for s in greedy]==[(0,3)]
    assert [(s['token_first'],s['token_last']) for s in global_choice]==[(0,1),(2,3)]
    # Global choice selects one complete-object span once its learned score
    # exceeds the competing sum, without reading any surface name.
    logits[0,3,action]=8.0
    assert [(s['token_first'],s['token_last']) for s in decode_neural_spans(logits,valid)]==[(0,3)]


def test_structured_object_set_loss_backpropagates_to_complete_span():
    action=c.SPAN_KINDS.index('action')+1
    logits=torch.full((4,4,1+len(c.SPAN_KINDS)),-20.0)
    logits[...,0]=0.0
    valid=torch.zeros((4,4),dtype=torch.bool)
    valid[0,3]=True;valid[0,1]=True;valid[2,1]=True
    logits[0,3,action]=3.0
    logits[0,1,action]=4.0
    logits[2,1,action]=4.0
    logits.requires_grad_(True)
    spans=[{'token_first':0,'token_last':3,'kind':'action'}]
    bad=cur.g1_structured_object_set_loss({'span_logits':logits,'span_valid':valid},spans)
    bad.backward()
    assert bad.item()>0
    assert logits.grad[0,3,action]<0    # gradient descent raises gold score
    assert logits.grad[0,1,action]>0    # pushes false partial down
    good=logits.detach().clone()
    good[0,3,action]=25.0
    assert cur.g1_structured_object_set_loss(
        {'span_logits':good,'span_valid':valid},spans).item()==0


def test_counterfactual_extension_preserves_blind_and_swaps_semantic_roles(tmp_path):
    base=tmp_path/'base.jsonl';extended=tmp_path/'new.jsonl'
    corpus.generate(base,train=8,dev=4,challenge=4,test=4)
    meta=corpus.extend_with_counterfactuals(base,extended,pairs=4)
    old=cur.load_rows(base);new=cur.load_rows(extended)
    assert meta['training_only_counterfactual_pairs']==4
    assert new[:len(old)]==old
    pairs={}
    for row in new[len(old):]:
        assert row['split']=='train'
        pairs.setdefault(row['counterfactual_pair'],[]).append(row)
    assert len(pairs)==4
    for pair in pairs.values():
        left,right=pair
        assert left['expected_decisions']==right['expected_decisions']
        assert [(n['kind'],n.get('value')) for n in left['graph']['nodes']] == \
               [(n['kind'],n.get('value')) for n in right['graph']['nodes']]
        assert left['prompt']!=right['prompt']
        if left['counterfactual_mode']=='role_swap':
            aa=[n for n in left['graph']['nodes'] if n['kind']=='actor'][0]
            ab=[n for n in right['graph']['nodes'] if n['kind']=='actor'][0]
            la=left['prompt'][aa['span'][0]:aa['span'][1]]
            ra=right['prompt'][ab['span'][0]:ab['span'][1]]
            assert la!=ra
            assert any(right['prompt'][n['span'][0]:n['span'][1]]==la
                       for n in right['graph']['nodes'] if n['kind']=='action')
            assert any(left['prompt'][n['span'][0]:n['span'][1]]==ra
                       for n in left['graph']['nodes'] if n['kind']=='action')
    with pytest.raises(FileExistsError,match='IMMUTABLE'):
        corpus.extend_with_counterfactuals(base,extended,pairs=4)


def test_counterfactual_distribution_consistency_neural_objective():
    actor=c.SPAN_KINDS.index('actor')+1
    a=torch.zeros(1,1,1+len(c.SPAN_KINDS),requires_grad=True)
    b=torch.zeros(1,1,1+len(c.SPAN_KINDS),requires_grad=True)
    a.data[0,0,actor]=5
    b.data[0,0,actor]=5
    span=[{'kind':'actor','token_first':0,'token_last':0}]
    assert cur.g1_counterfactual_consistency({'span_logits':a},{'spans':span},
        {'span_logits':b},{'spans':span}).item()<1e-5
    b.data[0,0,actor]=-5
    loss=cur.g1_counterfactual_consistency({'span_logits':a},{'spans':span},
        {'span_logits':b},{'spans':span})
    assert loss.item()>1
    loss.backward()
    assert a.grad is not None and b.grad is not None


def test_g1_extension_refines_once_without_unlocking_g2(tmp_path,monkeypatch):
    fake_model(monkeypatch)
    source=fake_source(tmp_path);base=dataset(tmp_path)
    parent=tmp_path/'parent'
    old=cur.train(dataset=base,output=parent,source_adapter=source,
                  device='cpu',steps=1,accum=1,width=32)
    extended=tmp_path/'extended.jsonl'
    corpus.extend_with_counterfactuals(base,extended,pairs=4)
    with pytest.raises(ValueError,match='REFINE_DATASET_LINEAGE'):
        cur.train(dataset=extended,output=tmp_path/'invalid',source_adapter=source,
                  device='cpu',steps=1,accum=1,refine_checkpoint=parent)
    from safetensors.torch import load_file
    original=load_file(str(parent/cur.HEAD_FILE))
    manifest=cur.train(dataset=extended,output=tmp_path/'child',source_adapter=source,
                       device='cpu',steps=1,accum=2,refine_checkpoint=parent,
                       refine_parent_dataset=base,counterfactual_fraction=1.0)
    assert manifest['counterfactual_pair_count']==4
    assert manifest['g1_parent_dataset_sha256']==old['dataset_sha256']
    assert manifest['next_training_gate']=='LOCKED_PENDING_CERTIFICATION'
    new=load_file(str(tmp_path/'child'/cur.HEAD_FILE))
    assert all(torch.equal(original[k],new[k]) for k in original
               if not any(k.startswith(p) for p in cur.STAGE_MODULES['G1']))
    mutated=tmp_path/'mutated.jsonl'
    rows=cur.load_rows(extended)
    rows[0]['prompt']+=' tampering'
    # A legitimate graph is required; alter an added train example instead
    # while keeping the old rows untouched, to verify incomplete pairs.
    rows=cur.load_rows(extended)
    rows.pop()
    mutated.write_text(''.join(json.dumps(r)+'\n' for r in rows),encoding='utf-8')
    with pytest.raises(ValueError,match='PAIR_INCOMPLETE'):
        cur.train(dataset=mutated,output=tmp_path/'invalid2',source_adapter=source,
                  device='cpu',steps=1,accum=1,refine_checkpoint=parent,
                  refine_parent_dataset=base)


def test_controlled_g1_comparison_same_prompts_and_sealed_test(tmp_path,monkeypatch):
    from tools.nanojev_gamer_v1_graph_comparison import compare
    fake_model(monkeypatch)
    source=fake_source(tmp_path);base=dataset(tmp_path)
    parent=tmp_path/'parent'
    cur.train(dataset=base,output=parent,source_adapter=source,
              device='cpu',steps=1,accum=1,width=32)
    extended=tmp_path/'extended.jsonl'
    corpus.extend_with_counterfactuals(base,extended,pairs=4)
    cur.train(dataset=extended,output=tmp_path/'child',source_adapter=source,
              device='cpu',steps=1,accum=2,refine_checkpoint=parent,
              refine_parent_dataset=base,counterfactual_fraction=1.0)
    report=compare(baseline_dataset=base,candidate_dataset=extended,
                   baseline_checkpoint=parent,candidate_checkpoint=tmp_path/'child',
                   output=tmp_path/'comparison',device='cpu')
    assert report['cases']==4 and not report['blind_test_used']
    assert report['status']=='DEVELOPMENT_COMPARISON_ONLY_NOT_CERTIFICATION'
    assert set(report['results'])=={'greedy_baseline','global_decoder_only','global_plus_refinement'}
    with pytest.raises(FileExistsError,match='IMMUTABLE'):
        compare(baseline_dataset=base,candidate_dataset=extended,
                baseline_checkpoint=parent,candidate_checkpoint=tmp_path/'child',
                output=tmp_path/'comparison',device='cpu')


def test_decoder_only_comparison_without_new_checkpoint(tmp_path,monkeypatch):
    from tools.nanojev_gamer_v1_graph_comparison import compare
    fake_model(monkeypatch)
    source=fake_source(tmp_path);base=dataset(tmp_path)
    checkpoint=tmp_path/'old'
    cur.train(dataset=base,output=checkpoint,source_adapter=source,
              device='cpu',steps=1,accum=1,width=32)
    report=compare(baseline_dataset=base,baseline_checkpoint=checkpoint,
                   output=tmp_path/'decoder_only',device='cpu')
    assert report['development_improvement_status']=='DECODER_ONLY'
    assert set(report['results'])=={'greedy_baseline','global_decoder_only'}
    assert report['development_requirements'] is None
    assert not report['certification_authorized'] and not report['blind_test_used']
    with pytest.raises(ValueError,match='REQUIRES_BOTH_CANDIDATE'):
        compare(baseline_dataset=base,baseline_checkpoint=checkpoint,
                candidate_dataset=base,output=tmp_path/'bad',device='cpu')


def test_counterfactual_v2_is_balanced_and_roles_move_in_both_families(tmp_path):
    from collections import Counter
    base=dataset(tmp_path)
    extended=tmp_path/'balanced.jsonl'
    metadata=corpus.extend_with_counterfactuals(base,extended,pairs=12,seed=111)
    assert metadata['family_mode_balanced'] is True
    assert metadata['counterfactual_pairs_by_family_mode']=={
        'encounter':{'role_swap':3,'renaming':3},
        'allocation':{'role_swap':3,'renaming':3}}
    original=cur.load_rows(base);new=cur.load_rows(extended)
    assert new[:len(original)]==original
    groups={}
    for row in new[len(original):]:
        assert row['split']=='train'
        groups.setdefault(row['counterfactual_pair'],[]).append(row)
    assert len(groups)==12
    for pair in groups.values():
        left,right=sorted(pair,key=lambda r:r['counterfactual_side'])
        assert left['family']==right['family']==left['counterfactual_family']
        assert left['counterfactual_mode']==right['counterfactual_mode']
        assert left['graph']['edges']==right['graph']['edges']
        assert left['graph']['objective']==right['graph']['objective']
        assert left['expected_decisions']==right['expected_decisions']
        for row in pair:
            assert c.execute_graph(row['graph'])['decisions']==row['expected_decisions']
        if left['counterfactual_mode']=='role_swap':
            def surface(row,kind):
                n=next(n for n in row['graph']['nodes'] if n['kind']==kind)
                return row['prompt'][n['span'][0]:n['span'][1]]
            assert surface(left,'actor')==surface(right,'action')
            assert surface(left,'action')==surface(right,'actor')
    with pytest.raises(ValueError,match='MULTIPLE_OF_FOUR'):
        corpus.extend_with_counterfactuals(base,tmp_path/'odd.jsonl',pairs=6)


def test_balanced_g1_training_and_loss_ablation_flags_are_independent(tmp_path,monkeypatch):
    fake_model(monkeypatch)
    source=fake_source(tmp_path);base=dataset(tmp_path)
    parent=tmp_path/'parent'
    cur.train(dataset=base,output=parent,source_adapter=source,
              device='cpu',steps=1,accum=1,width=32)
    extension=tmp_path/'balanced.jsonl'
    corpus.extend_with_counterfactuals(base,extension,pairs=4)
    manifests={}
    for name,sw,cw in [('none',0.0,0.0),('combined',.4,.15)]:
        m=cur.train(dataset=extension,output=tmp_path/name,
              source_adapter=source,device='cpu',steps=2,accum=4,
              refine_checkpoint=parent,refine_parent_dataset=base,
              family_balanced=True,structured_weight=sw,consistency_weight=cw,
              counterfactual_fraction=.5)
        assert m['g1_family_balanced'] is True
        assert m['structured_weight']==sw and m['consistency_weight']==cw
        counts=m['g1_microbatch_family_counts']
        assert sum(counts['encounter'].values())==sum(counts['allocation'].values())==4
        assert m['next_training_gate']=='LOCKED_PENDING_CERTIFICATION'
        manifests[name]=m
    assert manifests['none']['head_sha256']!=manifests['combined']['head_sha256']
    with pytest.raises(ValueError,match='INVALID_COUNTERFACTUAL_TRAINING_SETTINGS'):
        cur.train(dataset=extension,output=tmp_path/'bad',source_adapter=source,
                  device='cpu',steps=1,accum=1,structured_weight=-1)


def test_ablations_audit_requires_matched_controls_and_never_promotes(tmp_path,monkeypatch):
    from tools.nanojev_gamer_v1_graph_comparison import audit_ablations,compare
    fake_model(monkeypatch)
    source=fake_source(tmp_path);base=dataset(tmp_path)
    parent=tmp_path/'parent'
    cur.train(dataset=base,output=parent,source_adapter=source,
              device='cpu',steps=1,accum=1,width=32)
    extension=tmp_path/'balanced.jsonl'
    corpus.extend_with_counterfactuals(base,extension,pairs=4)
    arms={'none':(0,0),'structured':(.4,0),'consistency':(0,.15),'combined':(.4,.15)}
    reports={}
    for name,(sw,cw) in arms.items():
        checkpoint=tmp_path/('ck_'+name)
        cur.train(dataset=extension,output=checkpoint,source_adapter=source,
                  device='cpu',steps=2,accum=4,refine_checkpoint=parent,
                  refine_parent_dataset=base,family_balanced=True,
                  structured_weight=sw,consistency_weight=cw)
        report=tmp_path/('cmp_'+name)
        compare(baseline_dataset=base,baseline_checkpoint=parent,
                candidate_dataset=extension,candidate_checkpoint=checkpoint,
                output=report,device='cpu')
        reports[name]=report
    result=audit_ablations(**reports,output=tmp_path/'audit')
    assert result['status']=='DEV_ABLATION_AUDIT_ONLY_NOT_CERTIFICATION'
    assert result['blind_test_used'] is False
    assert result['production_promotion'] is False
    assert result['g2_unlocked'] is False
    assert set(result['arms'])==set(arms)
    assert set(result['effect_structured_without_consistency']) >= {'f1','actor_recall'}
    with pytest.raises(ValueError,match='ARM_CONFIGURATION_MISMATCH'):
        audit_ablations(**{**reports,'combined':reports['none']},
                        output=tmp_path/'audit_invalid')
