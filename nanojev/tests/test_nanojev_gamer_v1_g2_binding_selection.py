"""G2-0002 frozen-score decoding, calibration isolation, and replay contracts."""
import hashlib
import json
import random
import subprocess
import sys
from pathlib import Path

import pytest
import torch

from tools import nanojev_gamer_v1_g2_binding_selection as s
from tools import nanojev_gamer_v1_g2_binding_bootstrap as parent
from tools.nanojev_gamer_v1_graph_corpus import encounter, allocation
from tools.nanojev_gamer_v1_graph_head import BIND_CLASSES, GamerGraphHead


def rows():
    return [encounter(random.Random(18),'train',1), allocation(random.Random(40),'train',2)]


def logits(n,entries):
    out=torch.full((n,n,len(BIND_CLASSES)),-8.0)
    out[:,:,0]=8.0
    for a,b,typ,strength in entries:
        out[a,b,0]=0.0
        out[a,b,BIND_CLASSES.index(typ)]=strength
    return out


def test_compatibility_from_train_is_bound_to_train_graphs():
    inventory=s.compatibility_from_train(rows())
    assert ('owns','actor','action') in inventory
    assert ('measures','quantity','outcome') in inventory
    assert ('owns','action','actor') not in inventory
    tampered=rows()
    tampered[0]['split']='dev'
    with pytest.raises(ValueError,match='REQUIRES_TRAIN'):
        s.compatibility_from_train(tampered)


def test_decoder_filters_wrong_direction_and_preserves_scores_order():
    anchors=[(0,2,'actor'),(3,6,'action'),(7,10,'action'),(11,13,'quantity')]
    matrix=logits(4,[(0,1,'owns',8.0),(1,0,'owns',9.0),
                     (0,2,'owns',8.7),(3,1,'applies_to',8.0)])
    allowed=frozenset({('owns','actor','action'),('applies_to','quantity','action')})
    raw=s.choose_edges(anchors,matrix,{'threshold':0.5,'compatible':False,'per_source_cap':None},allowed)
    assert (anchors[1],anchors[0],'owns') in raw
    gated=s.choose_edges(anchors,matrix,{'threshold':0.5,'compatible':True,'per_source_cap':None},allowed)
    assert len(gated)==3 and (anchors[1],anchors[0],'owns') not in gated
    capped=s.choose_edges(anchors,matrix,{'threshold':0.5,'compatible':True,'per_source_cap':1},allowed)
    assert (anchors[0],anchors[2],'owns') in capped
    assert (anchors[0],anchors[1],'owns') not in capped
    assert (anchors[3],anchors[1],'applies_to') in capped


def test_threshold_rejection_and_invalid_inputs():
    a=[(0,1,'actor'),(2,3,'action')]
    m=logits(2,[(0,1,'owns',0.5)])
    assert len(s.choose_edges(a,m,{'threshold':0.90,'compatible':False,'per_source_cap':None},set()))==0
    with pytest.raises(ValueError,match='PAIR_SHAPE'):
        s.choose_edges(a,m[:1],{'threshold':0.5,'compatible':False,'per_source_cap':1},set())
    m[0,1,0]=float('nan')
    with pytest.raises(ValueError,match='NONFINITE'):
        s.choose_edges(a,m,{'threshold':0.5,'compatible':False,'per_source_cap':1},set())


def test_no_oracle_input_to_decoder_and_no_implicit_edge_creation():
    a=[(0,1,'actor'),(2,3,'action')]
    m=logits(2,[(0,1,'owns',9.0)])
    allowed={('owns','actor','action')}
    rule={'threshold':0.5,'compatible':True,'per_source_cap':1}
    edges=s.choose_edges(a,m,rule,allowed)
    assert edges=={(a[0],a[1],'owns')}
    no_edge=s.choose_edges(a,logits(2,[]),rule,allowed)
    assert not no_edge
    # Gold sets appear only in the scoring layer, never in selection arguments.
    assert s.score_set(edges,edges)['exact']
    assert s.score_set(edges,set())['false_positive']==1


def test_configurations_and_calibrated_selection_are_predeclared():
    controls,candidates=s.configurations()
    assert set(controls)=={'argmax_050','argmax_080','compatible_050','top2_050'}
    assert len(candidates)==len(s.THRESHOLDS)*len(s.CAPS)
    fake={'argmax_050':{'recall':.78,'f1':.22,'precision':.13,'false_positive':100}}
    for name in candidates:fake[name]={'recall':.6,'f1':.31,'precision':.45,'false_positive':30}
    best=next(iter(candidates))
    fake[best]={'recall':.70,'f1':.52,'precision':.60,'false_positive':20}
    got,selection=s.select_calibrated_rule(fake,controls,candidates)
    assert got==best and selection['calibration_recall_floor']==pytest.approx(.63)
    fake[best]={'recall':.62,'f1':.99,'precision':.99,'false_positive':0}
    chosen,_=s.select_calibrated_rule(fake,controls,candidates)
    assert chosen!=best
    for name in candidates:fake[name]['recall']=.1
    assert s.select_calibrated_rule(fake,controls,candidates)[0] is None


def test_validation_is_balanced_and_disjoint_from_train_and_calibration():
    train=rows()
    calibration=parent.novel_dev_rows(train,limit=10,seed=parent.SEED)
    independent=s.new_validation_rows(train,calibration,limit=20)
    a={parent._sha(x['prompt']) for x in train+calibration}
    b={parent._sha(x['prompt']) for x in independent}
    assert not a&b and len(b)==20
    assert {r['split'] for r in independent}=={'dev'}
    assert [x['family'] for x in independent].count('encounter')==10


def test_exact_saved_case_replay_and_invalid_metrics():
    anchors=[(0,1,'actor'),(2,3,'action')]
    m=logits(2,[(0,1,'owns',9.0)])
    gold={(anchors[0],anchors[1],'owns')}
    item={'index':1,'family':'encounter','prompt_sha256':'p',
          'anchors':anchors,'logits':m,'gold':gold}
    item['legacy']=s.score_set(s.choose_edges(anchors,m,s.configurations()[0]['argmax_050'],set()),gold)
    saved=[{'index':1,'family':'encounter','prompt_sha256':'p',
            'configurations':{'trained':item['legacy']}}]
    report={'metrics':{'trained':s._metric([{**item['legacy'],'family':'encounter'}])}}
    s.verify_bootstrap_replay([item],saved,report,set())
    saved[0]['configurations']['trained']['correct']=0
    with pytest.raises(ValueError,match='REPLAY_PREDICTIONS'):
        s.verify_bootstrap_replay([item],saved,report,set())


def test_gate_requires_precision_recall_and_false_positive_progress():
    baseline={'precision':.15,'recall':.79,'f1':.25,'false_positive':2000}
    positive={'precision':.45,'recall':.65,'f1':.53,'false_positive':400}
    assert all(s.validation_gate(positive,baseline).values())
    assert not s.validation_gate({**positive,'recall':.50},baseline)['recall_at_least_060']


def test_cli_direct_module():
    cwd=Path(__file__).resolve().parents[1]
    proc=subprocess.run([sys.executable,'-m','tools.nanojev_gamer_v1_graph_curriculum',
                         'frob-g2-binding-selection','--help'],cwd=cwd,text=True,
                        capture_output=True,timeout=30)
    assert proc.returncode==0,proc.stderr
    assert '--device' in proc.stdout and '--g2-run' in proc.stdout


def test_complete_runner_mocked_cpu_replays_parent_and_writes_immutable_artifacts(monkeypatch,tmp_path):
    # In-memory end-to-end runner; model capture is replaced but all lineage,
    # calibration selection, scoring, result writing and immutability execute.
    train=rows()
    calibration=parent.novel_dev_rows(train,limit=4,seed=parent.SEED)
    independent=parent.novel_dev_rows(train+calibration,limit=4,seed=s.SEED)
    head=GamerGraphHead(hidden_size=16,width=16,layers=1,latent_slots=2,memory_slots=4,max_span_tokens=4)
    old={k:v.detach().clone() for k,v in head.state_dict().items()}
    dataset=tmp_path/'train.jsonl';dataset.write_text('train')
    report={'train_prompt_hashes_sha256':parent._sha('\n'.join(parent._sha(r['prompt']) for r in train)),
            'dev_dataset_sha256':hashlib.sha256(parent._jsonl(calibration)).hexdigest()}
    def make_case(row,index):
        nodes=[(0,1,'actor'),(2,3,'action'),(4,5,'quantity')]
        out=logits(3,[(0,1,'owns',9.0),(1,0,'owns',8.0),(2,1,'applies_to',8.0)])
        gold={(nodes[0],nodes[1],'owns')}
        legacy=s.score_set(s.choose_edges(nodes,out,s.configurations()[0]['argmax_050'],set()),gold)
        return {'index':index,'family':row['family'],'prompt_sha256':parent._sha(row['prompt']),
                'anchors':nodes,'logits':out,'gold':gold,'legacy':legacy}
    prior=[make_case(r,i) for i,r in enumerate(calibration,1)]
    saved=[{'index':r['index'],'family':r['family'],'prompt_sha256':r['prompt_sha256'],
            'configurations':{'trained':r['legacy']}}
           for r in prior]
    report['metrics']={'trained':s._metric([{**r['legacy'],'family':r['family']} for r in prior])}
    monkeypatch.setattr(s,'TRAIN_LIMIT',len(train))
    monkeypatch.setattr(s,'CALIBRATION_LIMIT',len(calibration))
    manifest={'head_sha256':'G1','model_id':'dummy','source_adapter':'dummy'}
    monkeypatch.setattr(s,'load_parents',lambda *args:(head,manifest,dataset,report,saved,old,{'evidence':'hash'}))
    monkeypatch.setattr(s.base,'original_training_rows',lambda dataset,limit:train)
    original_dev=s.base.novel_dev_rows
    monkeypatch.setattr(s.base,'novel_dev_rows',lambda selected,limit,seed:
        calibration if seed==parent.SEED else independent)
    monkeypatch.setattr(s,'new_validation_rows',lambda t,c,limit:independent)
    class NoParam:
        def parameters(self):return iter([])
    monkeypatch.setattr(s.backbone,'model_components',lambda *args:(NoParam(),None))
    monkeypatch.setattr(s,'capture',lambda _head,_manifest,_model,_tok,selected,**kw:
        [make_case(r,i) for i,r in enumerate(selected,1)])
    out=tmp_path/'output'
    result=s.run(root=tmp_path,output=out,g1_validation_report=tmp_path/'g1',
                 g2_run=tmp_path/'g2',device='cpu',validation_limit=4)
    assert result['trainable_parameters']==0
    assert result['parent_bootstrap_replay_pass'] is True
    assert result['g2_certified'] is False
    assert result['calibration_only_rule_selection'] is True
    assert (out/s.REPORT).exists() and (out/s.CASES).exists()
    assert len((out/s.CASES).read_text().splitlines())==8
    with pytest.raises(FileExistsError,match='IMMUTABLE'):
        s.run(root=tmp_path,output=out,g1_validation_report=tmp_path/'g1',
              g2_run=tmp_path/'g2',device='cpu',validation_limit=4)


def test_capture_real_graph_decode_with_cpu_mock_backbone(monkeypatch):
    data=rows()
    head=GamerGraphHead(hidden_size=16,width=16,layers=1,latent_slots=2,memory_slots=4,max_span_tokens=4).eval()
    mapping={r['prompt']:r for r in data}
    def prep(_tok,prompt,max_input_tokens):
        marks=sorted(mapping[prompt]['object_spans'],key=lambda x:x['start'])[:4]
        return {'prompt':prompt,'prompt_start':0,'offsets':[(x['start'],x['end']) for x in marks],
                'mock_kinds':[x['kind'] for x in marks],
                'eligible':torch.ones(len(marks),dtype=torch.bool)}
    monkeypatch.setattr(s.backbone,'prepare_prompt',prep)
    monkeypatch.setattr(s.backbone,'frozen_context',lambda model,prepared,device:
        torch.randn(len(prepared['offsets']),16))
    monkeypatch.setattr(s.base,'predicted_spans',lambda scores,prepared:[
        {'kind':kind,'token_first':i,'token_last':i}
        for i,kind in enumerate(prepared['mock_kinds'])])
    captured=s.capture(head,{},object(),None,data,device='cpu',max_length=256,group='test')
    assert len(captured)==2
    for case in captured:
        assert case['logits'].shape==(4,4,len(BIND_CLASSES))
        new=s.score_set(s.choose_edges(case['anchors'],case['logits'],
                    s.configurations()[0]['argmax_050'],set()),case['gold'])
        assert new==case['legacy']


def test_load_parent_real_report_contract_and_corrupt_artifact_rejection(monkeypatch,tmp_path):
    from safetensors.torch import save_file
    head=GamerGraphHead(hidden_size=16,width=16,layers=1,latent_slots=2,memory_slots=4,max_span_tokens=4)
    root=tmp_path/'root';root.mkdir()
    parent_run=tmp_path/'g2-parent';parent_run.mkdir()
    g1=tmp_path/'g1.json';g1.write_text('{}')
    dataset=root/'dataset.jsonl';dataset.write_text('dummy')
    manifest={'head_sha256':s.base.G1_HEAD_SHA,'dataset_sha256':'dataset-hash',
              'source_adapter_sha256':'adapter-hash','source_adapter':'adapter'}
    monkeypatch.setattr(s.base,'validate_parent',lambda root,validation:(head,manifest,dataset,'g1-validation-hash'))
    monkeypatch.setattr(s.backbone,'adapter_fingerprint',lambda _: 'adapter-hash')
    weights=s.base._binding_snapshot(head)
    save_file({k:v.contiguous() for k,v in weights.items()},str(parent_run/s.base.HEAD))
    metadata={'schema':s.base.SCHEMA,'g2_development_started':True,'g2_certified':False,
              'g2_production_unlocked':False,'g1_frozen':True,
              'g1_development_candidate':s.base.G1_ARM,'g1_known_residual_losses':1,
              'g2_inference_uses_predicted_g1_only':True,'g2_training_only_author_gold':True,
              'certification_authorized':False,'challenge_or_test_split_used':False,
              'sealed_test_used':False,'production_promotion':False,
              'graph_execution_authorized':False,'memory_writes':False,
              'train_cases':s.TRAIN_LIMIT,'dev_cases':s.CALIBRATION_LIMIT,
              'train_steps':s.base.STEPS,
              'g1_independent_validation_report_sha256':'g1-validation-hash',
              'g1_head_sha256':s.base.G1_HEAD_SHA,'g1_dataset_sha256':'dataset-hash',
              'g1_source_adapter_sha256':'adapter-hash','source_adapter_sha256':'adapter-hash',
              'g2_development_weights_sha256':s.backbone.sha256(parent_run/s.base.HEAD)}
    path=parent_run/s.base.REPORT
    path.write_text(json.dumps(metadata))
    rows=[{'index':i,'family':'encounter','prompt_sha256':str(i),
           'configurations':{'untrained':{},'trained':{},'zero_node_features':{}}}
          for i in range(1,s.CALIBRATION_LIMIT+1)]
    (parent_run/s.base.CASES).write_text(''.join(json.dumps(x)+'\n' for x in rows))
    result=s.load_parents(root,g1,parent_run)
    assert result[0] is head and result[3]['g2_certified'] is False
    metadata['production_promotion']=True
    path.write_text(json.dumps(metadata))
    with pytest.raises(ValueError,match='PARENT_CONTRACT:production_promotion'):
        s.load_parents(root,g1,parent_run)
    metadata['production_promotion']=False
    path.write_text(json.dumps(metadata))
    (parent_run/s.base.HEAD).write_bytes(b'corrupt')
    with pytest.raises(ValueError,match='WEIGHTS_HASH_MISMATCH'):
        s.load_parents(root,g1,parent_run)
