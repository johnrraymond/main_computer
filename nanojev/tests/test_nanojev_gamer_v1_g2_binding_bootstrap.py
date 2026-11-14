"""G2-0001 development isolation and actual predicted-node binding contracts."""
import json
import random
import subprocess
import sys
from pathlib import Path

import pytest
import torch

from tools import nanojev_gamer_v1_g2_binding_bootstrap as g2
from tools.nanojev_gamer_v1_graph_corpus import encounter, allocation
from tools.nanojev_gamer_v1_graph_head import GamerGraphHead, BIND_CLASSES


def source(family='encounter',seed=37):
    return (encounter if family=='encounter' else allocation)(random.Random(seed),'train',seed)


def test_load_only_train_and_never_parse_sealed_records(tmp_path):
    rows=[source('encounter',1),source('allocation',2),source('encounter',3),source('allocation',4)]
    path=tmp_path/'corpus.jsonl'
    raw=''.join(json.dumps(r,sort_keys=True)+'\n' for r in rows)
    # These are deliberately NOT valid JSON; they must never be JSON-decoded.
    raw+='{"split":"challenge","private": NOT_JSON }\n'
    raw+='{"split":"test","private": NOT_JSON }\n'
    path.write_text(raw,encoding='utf-8')
    selected=g2.original_training_rows(path,4)
    assert [r['family'] for r in selected]==['encounter','allocation','encounter','allocation']
    assert all(r['split']=='train' for r in selected)


def test_dev_is_prompt_disjoint_balanced_and_not_sealed():
    train=[source('encounter',1),source('allocation',2)]
    dev=g2.novel_dev_rows(train,20)
    assert len({r['prompt'] for r in dev})==20
    assert all(r['split']=='dev' for r in dev)
    assert sum(r['family']=='encounter' for r in dev)==10
    assert not {r['prompt'] for r in dev}&{r['prompt'] for r in train}


def test_binding_supervision_never_teacher_forces_missing_predicted_gold():
    row=source()
    all_anchors=[(s['start'],s['end'],s['kind']) for s in row['object_spans']]
    samples,diag=g2.supervision(all_anchors,row)
    assert diag['matched_nodes']==len(row['object_spans'])
    assert diag['reachable_gold_edges']==diag['gold_edges']==len(g2.gold_binding_edges(row))
    assert all(0<=i<len(all_anchors) and 0<=j<len(all_anchors) and i!=j for i,j,_ in samples)
    missing=all_anchors.pop(0)
    samples,diag=g2.supervision(all_anchors,row)
    assert diag['matched_nodes']==len(all_anchors)
    assert diag['reachable_gold_edges']<=diag['gold_edges']
    assert all(i<len(all_anchors) and j<len(all_anchors) for i,j,_ in samples)
    assert missing not in all_anchors


def test_false_predictions_cannot_acquire_positive_labels():
    row=source('allocation')
    anchors=[(s['start'],s['end'],s['kind']) for s in row['object_spans']]
    anchors.append((9999,10000,'action'))
    pairs,diag=g2.supervision(anchors,row)
    assert diag['matched_nodes']==len(anchors)-1
    assert all(i!=len(anchors)-1 and j!=len(anchors)-1 for i,j,_ in pairs)


def test_pair_logits_uses_learned_projection_on_predicted_vectors():
    head=GamerGraphHead(hidden_size=16,width=16,layers=1,latent_slots=2,memory_slots=4,max_span_tokens=4)
    vectors=torch.randn(4,16)
    pairs=[(0,1,1),(2,1,0)]
    logits=g2.pair_logits(head,vectors,pairs)
    assert logits.shape==(2,len(BIND_CLASSES))
    assert torch.isfinite(logits).all()


def test_g2_train_updates_only_binding_parameters():
    head=GamerGraphHead(hidden_size=16,width=16,layers=1,latent_slots=2,memory_slots=4,max_span_tokens=4)
    frozen={k:v.clone() for k,v in head.state_dict().items()
            if not k.startswith(('binding.','pair_projection.'))}
    initial=head.binding.weight.detach().clone()
    train=[{'family':f,'pairs':[(0,1,1),(1,0,0)],'vectors':torch.randn(2,16)}
           for f in ('encounter','allocation')]
    count,loss=g2.train_g2(head,train,steps=2,batch=2,device='cpu')
    assert count>0 and len(loss)==2
    g2._assert_g1_frozen(head,frozen)
    assert not torch.equal(initial,head.binding.weight)


def test_score_output_real_partial_g2_graph_and_metrics():
    row=source()
    # Explicitly synthetic neural outputs with no oracle data supplied to decode_graph.
    prompt='Alice Bob';prepared={'prompt':prompt,'prompt_start':0,'offsets':[(0,5),(6,9)],'eligible':torch.ones(2,dtype=torch.bool)}
    g2_output={'spans':[{'token_first':0,'token_last':0,'kind':'actor'},
                        {'token_first':1,'token_last':1,'kind':'action'}],
               'binding_logits':torch.zeros(2,2,len(BIND_CLASSES))}
    result=g2.score_output(g2_output,prepared,row)
    assert result['correct']==0 and result['missing']==len(g2.gold_binding_edges(row))
    assert result['exact'] is False
    m=g2.metrics([{**result,'family':'encounter'}])
    assert m['cases']==1 and 0<=m['f1']<=1


def test_frozen_g1_predicted_spans_never_receive_gold(monkeypatch):
    pred={'span_logits':torch.zeros(1,1,8),'span_valid':torch.ones(1,1,dtype=torch.bool)}
    prepared={'prompt':'Alice','prompt_start':0,'offsets':[(0,5)]}
    seen=[]
    selection={'guard':[],'filtered_base':[],'filtered_wide':[],'filtered':[]}
    monkeypatch.setattr(g2.diag,'frozen_stages',lambda p,q:{'selected':selection,'filtered_catalog':{}})
    def fake(*args,**kwargs):
        seen.append((args,kwargs))
        return ([{'token_first':0,'token_last':0,'kind':'actor'}],{})
    monkeypatch.setattr(g2.recon,'reconstruct_components',fake)
    spans=g2.predicted_spans(pred,prepared)
    assert spans[0]['kind']=='actor'
    assert seen[0][1]=={'guard_bonus':0.25}
    assert all('gold' not in str(item) for item in seen)


def test_parent_validation_fails_closed_on_fake_pass(tmp_path):
    validation=tmp_path/'report.json'
    validation.write_text(json.dumps({'frozen_125_independent_validation_pass':True}),encoding='utf-8')
    with pytest.raises(ValueError,match='PARENT_NOT_VALIDATED'):
        g2.validate_parent(tmp_path,validation)


def test_cli_direct_command():
    cwd=Path(__file__).resolve().parents[1]
    process=subprocess.run([sys.executable,'-m','tools.nanojev_gamer_v1_graph_curriculum',
         'frob-g2-binding-bootstrap','--help'],cwd=cwd,capture_output=True,text=True,timeout=25)
    assert process.returncode==0,process.stderr
    assert '--device' in process.stdout


def test_full_bootstrap_runner_cpu_synthetic_with_immutable_outputs(monkeypatch,tmp_path):
    import re
    from types import SimpleNamespace
    train=[source('encounter',11),source('allocation',12)]
    dev=[source('encounter',15),source('allocation',16)]
    for row in dev:row['split']='dev'
    tiny=GamerGraphHead(hidden_size=16,width=16,layers=1,latent_slots=2,memory_slots=4,max_span_tokens=4)
    dataset=tmp_path/'dataset.jsonl';dataset.write_text('synthetic-corpus')
    report=tmp_path/'frozen-g1-validation.json';report.write_text('{}')
    manifest={'stage':'G1','head_sha256':g2.G1_HEAD_SHA,
              'dataset_sha256':'source_hash','source_adapter_sha256':'adapter_hash',
              'model_id':'dummy','source_adapter':'dummy'}
    monkeypatch.setattr(g2,'validate_parent',lambda root,report_path:(tiny,manifest,dataset,'report_hash'))
    monkeypatch.setattr(g2,'original_training_rows',lambda path,limit:train[:limit])
    monkeypatch.setattr(g2,'novel_dev_rows',lambda rows,limit:dev[:limit])
    class Model:
        def parameters(self):return iter([])
    monkeypatch.setattr(g2.backbone,'model_components',lambda *args:(Model(),object()))
    by_prompt={r['prompt']:r for r in train+dev}
    def prep(_tok,prompt,max_input_tokens):
        # A toy tokenizer whose tokens deliberately align with author-marked
        # source spans; the predicted model is still given NO gold graph.
        row=by_prompt[prompt]
        marks=sorted(row['object_spans'],key=lambda s:s['start'])[:4]
        return {'prompt':prompt,'prompt_start':0,
                'offsets':[(s['start'],s['end']) for s in marks],
                'mock_kinds':[s['kind'] for s in marks],
                'eligible':torch.ones(len(marks),dtype=torch.bool)}
    monkeypatch.setattr(g2.backbone,'prepare_prompt',prep)
    monkeypatch.setattr(g2.backbone,'frozen_context',lambda model,prepared,device:
        torch.randn(len(prepared['offsets']),16))
    monkeypatch.setattr(g2.backbone,'adapter_fingerprint',lambda *args:'adapter_hash')
    monkeypatch.setattr(g2,'predicted_spans',lambda score,prepared:[
        {'kind':kind,'token_first':i,'token_last':i}
        for i,kind in enumerate(prepared['mock_kinds'])])
    output=tmp_path/'g2-out'
    result=g2.run(root=tmp_path,output=output,validation_report=report,
                  device='cpu',train_limit=2,dev_limit=2,steps=2)
    assert result['g2_development_started'] is True
    assert result['g2_certified'] is False
    assert result['g1_frozen'] is True
    assert result['train_cases']==2 and result['dev_cases']==2
    assert set(result['metrics'])=={'untrained','trained','zero_node_features'}
    assert all((output/name).exists() for name in (g2.REPORT,g2.CASES,g2.HEAD))
    with pytest.raises(FileExistsError,match='IMMUTABLE'):
        g2.run(root=tmp_path,output=output,validation_report=report,
               device='cpu',train_limit=2,dev_limit=2,steps=2)
