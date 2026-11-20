"""G2-0083 native predicted G1 training, provenance, acceptance, and sabotage tests."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import torch
from safetensors.torch import save_file

from tools import nanojev_gamer_v1_g2_native_training_0083 as m
from tools import nanojev_gamer_v1_graph_curriculum as cur
from tools import nanojev_gamer_v1_graph_head as gh


def test_predeclared_plan_and_model_id():
    assert m.STEPS==400 and m.ACCUM==4 and m.LR>0
    assert m.SEED != 790079
    assert m.DEV==96 and m.CHALLENGE==64
    assert cur.GATE_REQUIREMENTS['G2']=={'metric':'binding_exact_rate','dev':.8,'challenge':.7,'neural_gain':.08}


def test_prospective_rows_are_new_and_match_corpus(monkeypatch,tmp_path):
    seen=set()
    monkeypatch.setattr(m,'previously_seen',lambda paths: seen)
    rows=m.prospective_rows({},seed=m.SEED)
    assert len(rows)==160 and len({r['prompt_sha256'] for r in rows})==160
    assert len(seen)==160  # construction registers every new prompt as consumed
    assert {r['family'] for r in rows}=={'encounter','allocation'}
    assert {r['split'] for r in rows}=={'dev','challenge'}
    assert all(r['graph']['edges'] and r['gold_objects'] for r in rows)
    with pytest.raises(ValueError,match='DESIGN_LOCKED'):
        m.prospective_rows({},seed=m.SEED+1)


def test_gold_binding_edge_roundtrip():
    graph={'nodes':[{'id':'a','kind':'actor','span':[0,3]},
                    {'id':'b','kind':'action','span':[5,10]}],
           'edges':[{'src':'a','dst':'b','type':'owns'}]}
    gold=m._gold_edges(graph)
    assert gold=={('actor',(0,3),'action',(5,10),'owns')}
    coded=m._safe_edges({(('actor',(0,3)),('action',(5,10)),'owns')})
    assert m._keyed_edges(coded)==gold
    with pytest.raises(ValueError,match='DUPLICATE'):
        m._keyed_edges(coded+coded)
    with pytest.raises(ValueError,match='EDGE_INVALID'):
        m._keyed_edges([[['actor',[0,3]],['action',[5,10]],'secret_answer']])


def test_scorer_rejects_binding_on_unpredicted_gold_node(monkeypatch,tmp_path):
    root=tmp_path/'study';(root/'blind').mkdir(parents=True)
    (root/'seal').mkdir()
    def fake_paths(root): return {'dataset':tmp_path/'corpus','certificate':tmp_path/'cert','composite':tmp_path/'composite'}
    monkeypatch.setattr(m,'defaults',fake_paths)
    (tmp_path/'corpus').write_text('a');(tmp_path/'cert').write_text('b')
    (tmp_path/'composite').mkdir();(tmp_path/'composite'/'composite-interface.json').write_text('c')
    m.write_json(root/'seal'/'commit.json',dict(schema=m.SCHEMA,status='COMMITTED_BEFORE_G2_TRAINING',
       seed=m.SEED,dev=m.DEV,challenge=m.CHALLENGE,prompts_sha256='x',answers_sha256='y',
       dataset_sha256=m.sha(tmp_path/'corpus'),native_certificate_sha256=m.sha(tmp_path/'cert'),
       composite_interface_sha256=m.sha(tmp_path/'composite'/'composite-interface.json'),
       parent_head_sha256='parent'))
    (root/'seal'/'answers.SECRET.jsonl').write_text('a')
    data=m.js(root/'seal'/'commit.json')
    data.update(answers_sha256=m.sha(root/'seal'/'answers.SECRET.jsonl'))
    m.write_json(root/'seal'/'commit.json',data)
    (root/'training').mkdir();(root/'training'/cur.HEAD_FILE).write_text('trained')
    edges=[[['actor',[0,3]],['action',[5,10]],'owns']]
    modes={name:{'binding_edges':edges,'g1_objects':[[0,3,'actor']]}
           for name in ('trained','untrained','no_binding','zero_hidden')}
    rows=[];answers=[];prompts=[]
    for split,n in (('dev',m.DEV),('challenge',m.CHALLENGE)):
        for j in range(n):
            ident=split+str(j)
            import hashlib
            text='wrong binding case ' + ident
            digest=hashlib.sha256(text.encode()).hexdigest()
            prompts.append({'case_id':ident,'split':split,
                'family':'encounter' if j%2==0 else 'allocation',
                'prompt_sha256':digest,'prompt':text})
            rows.append({'case_id':ident,'split':split,'family':'encounter' if j%2==0 else 'allocation',
                         'prompt_sha256':digest,'modes':modes,'surface_count':1})
            answers.append({'case_id':ident,'split':split,'family':rows[-1]['family'],
                            'prompt_sha256':digest,'graph':{'nodes':[],'edges':[]},'gold_objects':[]})
    m.write_jsonl(root/'blind'/'predictions.jsonl',rows)
    m.write_jsonl(root/'seal'/'prompts.jsonl',prompts)
    (root/'seal'/'answers.SECRET.jsonl').unlink()
    m.write_jsonl(root/'seal'/'answers.SECRET.jsonl',answers)
    data=m.js(root/'seal'/'commit.json')
    data['prompts_sha256']=m.sha(root/'seal'/'prompts.jsonl')
    data['answers_sha256']=m.sha(root/'seal'/'answers.SECRET.jsonl')
    m.write_json(root/'seal'/'commit.json',data)
    m.write_json(root/'blind'/'lock.json',{'schema':m.SCHEMA,
       'status':'BLIND_G2_NATIVE_PREDICTIONS_LOCKED','cases':160,'prompts_sha256':data['prompts_sha256'],
       'gold_read_during_inference':False,'teacher_forced_g1_spans':False,
       'g2_head_sha256':m.sha(root/'training'/cur.HEAD_FILE),
       'predictions_sha256':m.sha(root/'blind'/'predictions.jsonl')})
    monkeypatch.setattr(cur,'_load_checkpoint',lambda *a,**k:(None,{'stage':'G2',
        'native_composite_g1':{'mode':'predicted_G1_spans_native_G2_binding'},
        'head_sha256':m.sha(root/'training'/cur.HEAD_FILE),
        'dataset_sha256':data['dataset_sha256']}))
    with pytest.raises(ValueError,match='BINDING_ANCHOR_NOT_G1_PREDICTION'):
        m.score(root=root)


def test_scorer_rejects_prediction_tampering(monkeypatch,tmp_path):
    root=tmp_path/'out';(root/'seal').mkdir(parents=True);(root/'blind').mkdir()
    paths={'dataset':tmp_path/'dataset','certificate':tmp_path/'cert',
           'composite':tmp_path/'interface'}
    monkeypatch.setattr(m,'defaults',lambda root:paths)
    paths['dataset'].write_text('dataset');paths['certificate'].write_text('cert')
    paths['composite'].mkdir();(paths['composite']/'composite-interface.json').write_text('i')
    (root/'seal'/'prompts.jsonl').write_text('prompt')
    (root/'seal'/'answers.SECRET.jsonl').write_text('answer')
    m.write_json(root/'seal'/'commit.json',dict(schema=m.SCHEMA,status='COMMITTED_BEFORE_G2_TRAINING',
        seed=m.SEED,dev=m.DEV,challenge=m.CHALLENGE,
        prompts_sha256=m.sha(root/'seal'/'prompts.jsonl'),
        answers_sha256=m.sha(root/'seal'/'answers.SECRET.jsonl'),
        dataset_sha256=m.sha(paths['dataset']),native_certificate_sha256=m.sha(paths['certificate']),
        composite_interface_sha256=m.sha(paths['composite']/'composite-interface.json')))
    (root/'blind'/'predictions.jsonl').write_text('tamper')
    (root/'training').mkdir();(root/'training'/cur.HEAD_FILE).write_text('train')
    m.write_json(root/'blind'/'lock.json',dict(schema=m.SCHEMA,status='BLIND_G2_NATIVE_PREDICTIONS_LOCKED',
        cases=160,prompts_sha256=m.sha(root/'seal'/'prompts.jsonl'),
        predictions_sha256='0'*64,gold_read_during_inference=False,teacher_forced_g1_spans=False,
        g2_head_sha256=m.sha(root/'training'/cur.HEAD_FILE)))
    with pytest.raises(ValueError,match='PREDICTION_COMMITMENT_DRIFT'):
        m.score(root=root)


def test_only_g2_parameters_change(tmp_path):
    p=tmp_path/'parent';q=tmp_path/'trained';p.mkdir();q.mkdir()
    state={'span_classifier.weight':torch.tensor([[1.,2.]]),
           'binding.weight':torch.tensor([[0.,0.]]),
           'pair_projection.weight':torch.tensor([[0.,1.]]),
           'interface_couplers.G1_G2.delta.weight':torch.tensor([[0.,0.]])}
    save_file(state,str(p/cur.HEAD_FILE))
    after={k:v.clone() for k,v in state.items()}
    after['binding.weight'][0,0]=1
    save_file(after,str(q/cur.HEAD_FILE))
    result=m.compare_frozen_stage(p,q)
    assert result['changed_g2_parameters']==['binding.weight']
    after['span_classifier.weight'][0,0]=4
    save_file(after,str(q/cur.HEAD_FILE))
    with pytest.raises(ValueError,match='FROZEN_G1_OR_FUTURE_STAGE_MUTATED'):
        m.compare_frozen_stage(p,q)


def test_composite_supervision_never_teacher_forces_g1():
    # Existing curriculum is certified and cannot be edited without invalidating
    # the G1-0082 code commitment. Test actual node routing, not just flag names.
    head=gh.GamerGraphHead(hidden_size=16,width=24,layers=1,latent_slots=2,
                           memory_slots=2,max_operands=2,max_span_tokens=5)
    head.eval()
    hidden=torch.randn(8,16)
    predicted=[{'kind':'actor','token_first':1,'token_last':2},
               {'kind':'action','token_first':4,'token_last':4}]
    output=head(hidden,eligible=torch.ones(8,dtype=torch.bool),active_stage='G2',
                inference_spans=predicted)
    assert output['spans']==predicted and output['surface_count']==2
    assert output['binding_logits'].shape[:2]==(4,4)
    with pytest.raises(ValueError,match='INFERENCE_SPANS_CONFLICT'):
        head(hidden,eligible=torch.ones(8,dtype=torch.bool),active_stage='G2',
             inference_spans=predicted,training_spans=predicted)
    with pytest.raises(ValueError,match='INFERENCE_SPANS_CONFLICT'):
        head(hidden,eligible=torch.ones(8,dtype=torch.bool),active_stage='G1',
             inference_spans=predicted)


def test_run_calls_certified_predicted_span_training(monkeypatch,tmp_path):
    root=tmp_path/'root';root.mkdir()
    paths={k:root/k for k in ('dataset','adapter','g1','composite','certificate')}
    monkeypatch.setattr(m,'defaults',lambda r:paths)
    parent={'deterministic_fabric':{'cutover_id':'oracle-fabric-0001'},'head_sha256':'frozen'}
    monkeypatch.setattr(m,'preflight',lambda p:(parent,{}))
    monkeypatch.setattr(m,'commit_seal',lambda *args:None)
    observations={}
    def train(**kwargs):
        observations.update(kwargs)
        return {'stage':'G2','train_limit':0,'native_composite_g1':
                {'mode':'predicted_G1_spans_native_G2_binding'},'head_sha256':'new'}
    monkeypatch.setattr(cur,'train',train)
    monkeypatch.setattr(m,'compare_frozen_stage',lambda *args:{'non_g2_weights_mutated':False})
    def evaluate(**kwargs):
        return {'metrics':{'binding_exact_rate':.9,'object_exact_rate':.9}}
    monkeypatch.setattr(cur,'evaluate',evaluate)
    monkeypatch.setattr(cur,'certify',lambda **kwargs:{'status':'DEVELOPMENT_GATE_PASSED','failures':[]})
    monkeypatch.setattr(m.subprocess,'run',lambda args,check:None)
    monkeypatch.setattr(m,'score',lambda root:{'status':'PROSPECTIVE_G2_BINDING_GATE_PASSED','failures':[],
        'splits':{'dev':{'metrics':{'trained':{'exact':83,'edge_f1':.97}},
                       'gold_edges_unreachable_from_predicted_g1':0},
                  'challenge':{'metrics':{'trained':{'exact':49,'edge_f1':.96}},
                       'gold_edges_unreachable_from_predicted_g1':0}}})
    out=m.run(root=root,device='cpu',steps=3,accum=1)
    assert observations['stage']=='G2'
    assert observations['native_composite_g1']==paths['composite']
    assert observations['native_composite_cert']==observations['prerequisite_cert']==paths['certificate']
    assert observations['cutover_id']=='oracle-fabric-0001'
    assert observations.get('train_limit',0)==0
    assert out['status']=='G2_PREDICTED_SPAN_TRAINING_AND_GATES_PASSED'
    assert out['g3_training_unlocked'] is False


def test_sealed_scoring_full_success_and_pairwise_learning(monkeypatch,tmp_path):
    """Entire scorer pass with 160 author-graph examples, no model inference mock."""
    root=tmp_path/'gamer-v1'/'g2';root.mkdir(parents=True)
    (root/'seal').mkdir();(root/'blind').mkdir();(root/'training').mkdir()
    adapter=root.parent/'adapter';adapter.mkdir()
    (adapter/'composite-interface.json').write_text('frozen-interface')
    dataset=root.parent/'dataset';dataset.write_text('original-corpus')
    cert=root.parent/'cert';cert.write_text('native-certificate')
    paths={'dataset':dataset,'certificate':cert,'composite':adapter}
    monkeypatch.setattr(m,'defaults',lambda _:paths)
    (root/'training'/cur.HEAD_FILE).write_text('trained-G2')
    graph={'nodes':[{'id':'a','kind':'actor','span':[0,3]},
                    {'id':'b','kind':'action','span':[4,9]}],
           'edges':[{'src':'a','dst':'b','type':'owns'}]}
    edge=[[['actor',[0,3]],['action',[4,9]],'owns']]
    objects=[[0,3,'actor'],[4,9,'action']]
    prompts=[];answers=[];predictions=[]
    for split,n in (('dev',m.DEV),('challenge',m.CHALLENGE)):
        for i in range(n):
            ident=f'{split}-{i}'
            text='One action a' + str(i)
            # This is a structural metric test, so gold char positions are
            # synthetic; no generator text is parsed by the evaluator.
            import hashlib
            digest=hashlib.sha256(text.encode()).hexdigest()
            common={'case_id':ident,'split':split,
                    'family':'encounter' if i%2==0 else 'allocation',
                    'prompt_sha256':digest}
            prompts.append(dict(common,prompt=text))
            answers.append(dict(common,graph=graph,gold_objects=[
                {'start':0,'end':3,'kind':'actor'},
                {'start':4,'end':9,'kind':'action'}]))
            predictions.append(dict(common,modes={
                'trained':{'binding_edges':edge,'g1_objects':objects},
                'untrained':{'binding_edges':[],'g1_objects':objects},
                'zero_hidden':{'binding_edges':[],'g1_objects':[]},
                'no_binding':{'binding_edges':[],'g1_objects':objects}},surface_count=2))
    m.write_jsonl(root/'seal'/'prompts.jsonl',prompts)
    m.write_jsonl(root/'seal'/'answers.SECRET.jsonl',answers)
    m.write_jsonl(root/'blind'/'predictions.jsonl',predictions)
    m.write_json(root/'seal'/'commit.json',dict(schema=m.SCHEMA,status='COMMITTED_BEFORE_G2_TRAINING',
        seed=m.SEED,dev=m.DEV,challenge=m.CHALLENGE,
        prompts_sha256=m.sha(root/'seal'/'prompts.jsonl'),
        answers_sha256=m.sha(root/'seal'/'answers.SECRET.jsonl'),
        dataset_sha256=m.sha(dataset),parent_head_sha256='parent-sha',
        native_certificate_sha256=m.sha(cert),
        composite_interface_sha256=m.sha(adapter/'composite-interface.json')))
    m.write_json(root/'blind'/'lock.json',dict(schema=m.SCHEMA,
        status='BLIND_G2_NATIVE_PREDICTIONS_LOCKED',cases=160,
        prompts_sha256=m.sha(root/'seal'/'prompts.jsonl'),
        predictions_sha256=m.sha(root/'blind'/'predictions.jsonl'),
        g2_head_sha256=m.sha(root/'training'/cur.HEAD_FILE),
        gold_read_during_inference=False,teacher_forced_g1_spans=False))
    monkeypatch.setattr(cur,'_load_checkpoint',lambda *a,**k:(None,{
        'stage':'G2','head_sha256':m.sha(root/'training'/cur.HEAD_FILE),
        'dataset_sha256':m.sha(dataset),
        'native_composite_g1':{'mode':'predicted_G1_spans_native_G2_binding'}}))
    scored=m.score(root=root)
    assert scored['g2_prospective_pass'] is True
    assert scored['splits']['dev']['metrics']['trained']['exact']==96
    assert scored['splits']['challenge']['metrics']['trained']['exact']==64
    assert scored['splits']['dev']['metrics']['untrained']['exact']==0
    assert scored['splits']['dev']['paired_gain']==96
    assert scored['splits']['challenge']['gold_edges_unreachable_from_predicted_g1']==0
    assert scored['g3_training_unlocked'] is False
    with pytest.raises(FileExistsError):
        m.score(root=root)


def test_resume_reuses_checkpoint_and_never_retrains(monkeypatch,tmp_path):
    root=tmp_path/'gamer-v1';root.mkdir()
    dest=root/'oracle-fabric-g2-native-training-0083';dest.mkdir()
    parent={'head_sha256':'parent-verified'}
    trained={'head_sha256':'g2-verified'}
    monkeypatch.setattr(m,'preflight',lambda paths:(parent,{'g2_training_unlocked':True}))
    calls=[]
    monkeypatch.setattr(m,'_verify_saved_training',lambda paths,dest,par:(
        calls.append(('verify',par['head_sha256'])) or trained))
    monkeypatch.setattr(m,'compare_frozen_stage',lambda *args:{'non_g2_weights_mutated':False})
    monkeypatch.setattr(m,'_finish_trained_run',lambda paths,dest,trained,unchanged,**kwargs:(
        calls.append(('finish',kwargs['device'])) or {'status':'BLOCKED'}))
    monkeypatch.setattr(cur,'train',lambda **kwargs:pytest.fail('resume attempted to retrain G2'))
    monkeypatch.setattr(m,'commit_seal',lambda *args:pytest.fail('resume attempted to reseal'))
    assert m.resume(root=root,device='cpu')=={'status':'BLOCKED'}
    assert calls==[('verify','parent-verified'),('finish','cpu')]


def test_existing_G2_evaluation_recomputes_case_accuracy(tmp_path):
    path=tmp_path/'eval';path.mkdir()
    manifest={'head_sha256':'g2-hash','dataset_sha256':'data-hash',
              'source_adapter_sha256':'adapter-hash'}
    payload={'schema':cur.SCHEMA,'stage':'G2','checkpoint_stage':'G2',
             'split':'challenge','cases':32,'limited':False,'test_split_used':False,
             'generated_tokens':0,'teacher_graph_at_inference':False,
             'checkpoint_head_sha256':'g2-hash','dataset_sha256':'data-hash',
             'source_adapter_sha256':'adapter-hash',
             'metrics':{'binding_exact_rate':0.0},
             'cases_detail':[{'index':i,'passed':False} for i in range(1,33)]}
    m.write_json(path/'report.json',payload)
    assert m._verified_existing_evaluation(path,manifest,stage='G2',split='challenge')==payload
    payload['metrics']['binding_exact_rate']=1.0
    m.write_json(path/'report.json',payload)
    with pytest.raises(ValueError,match='EXISTING_EVALUATION_METRICS'):
        m._verified_existing_evaluation(path,manifest,stage='G2',split='challenge')
    payload['metrics']['binding_exact_rate']=0.0
    payload['checkpoint_head_sha256']='tampered'
    m.write_json(path/'report.json',payload)
    with pytest.raises(ValueError,match='EXISTING_EVALUATION_LINEAGE'):
        m._verified_existing_evaluation(path,manifest,stage='G2',split='challenge')


def test_completion_respects_G1_retention_only_and_G2_ablations(monkeypatch,tmp_path):
    dest=tmp_path/'campaign';dest.mkdir()
    (dest/'training').mkdir()
    paths={'dataset':tmp_path/'dataset'}
    manifest={'head_sha256':'g2-head'}
    calls=[]
    def fake_eval(**kw):
        calls.append(('eval',kw['target_stage'],kw['split'],kw['ablation_limit']))
        output=kw['output'];output.mkdir(parents=True)
        stage=kw['target_stage']
        report={'stage':stage,'metrics':{'binding_exact_rate':0.0,'object_exact_rate':.85}}
        m.write_json(output/'report.json',report)
        return report
    monkeypatch.setattr(cur,'evaluate',fake_eval)
    def fake_certify(**kw):
        assert kw['retention_dev']['G1'].is_file()
        assert kw['retention_challenge']['G1'].is_file()
        calls.append(('certify',))
        return {'status':'DEVELOPMENT_GATE_BLOCKED','failures':['dev:binding_exact_rate_below_threshold']}
    monkeypatch.setattr(cur,'certify',fake_certify)
    monkeypatch.setattr(m.subprocess,'run',lambda args,check:calls.append(('blind',)))
    monkeypatch.setattr(m,'score',lambda root:{'status':'PROSPECTIVE_G2_BINDING_GATE_BLOCKED',
        'failures':['dev:binding_exact_rate'], 'splits':{
            'dev':{'metrics':{'trained':{'exact':0,'edge_f1':.1}},
                   'gold_edges_unreachable_from_predicted_g1':4},
            'challenge':{'metrics':{'trained':{'exact':0,'edge_f1':.2}},
                   'gold_edges_unreachable_from_predicted_g1':5}}})
    summary=m._finish_trained_run(paths,dest,manifest,{'non_g2_weights_mutated':False},
                                   device='cpu',max_length=100)
    assert ('eval','G1','dev',0) in calls
    assert ('eval','G1','challenge',0) in calls
    assert ('eval','G2','dev',32) in calls
    assert ('eval','G2','challenge',32) in calls
    assert summary['g2_certified'] is False
    assert summary['g3_training_unlocked'] is False
    assert summary['g1_retention']['G1_dev']==.85
    assert summary['status']=='G2_PREDICTED_SPAN_TRAINED_BUT_GATE_BLOCKED'
    assert (dest/'report.json').is_file()
    with pytest.raises(ValueError,match='COMPLETION_ALREADY_RECORDED'):
        m._finish_trained_run(paths,dest,manifest,{},device='cpu',max_length=100)


def test_run_and_resume_both_use_shared_finish(monkeypatch,tmp_path):
    root=tmp_path/'gamer-v1';root.mkdir()
    paths={k:root/k for k in ('dataset','adapter','g1','composite','certificate')}
    monkeypatch.setattr(m,'defaults',lambda _:paths)
    parent={'deterministic_fabric':{'cutover_id':'oracle-fabric-0001'},'head_sha256':'orig'}
    monkeypatch.setattr(m,'preflight',lambda paths:(parent,{}))
    monkeypatch.setattr(m,'commit_seal',lambda *args:None)
    trained={'stage':'G2','train_limit':0,'native_composite_g1':{
             'mode':'predicted_G1_spans_native_G2_binding'},'head_sha256':'g2'}
    monkeypatch.setattr(cur,'train',lambda **kwargs:trained)
    monkeypatch.setattr(m,'compare_frozen_stage',lambda *args:{'non_g2_weights_mutated':False})
    seen=[]
    monkeypatch.setattr(m,'_finish_trained_run',lambda *args,**kwargs:(
        seen.append((args[1],args[2]['head_sha256'])) or {'status':'BLOCKED'}))
    assert m.run(root=root,device='cpu',steps=1,accum=1)=={'status':'BLOCKED'}
    assert seen==[(root/'oracle-fabric-g2-native-training-0083','g2')]
