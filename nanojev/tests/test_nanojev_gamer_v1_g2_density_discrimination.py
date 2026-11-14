"""G2-0005: strict parent replay, frozen density-aware decoder, independent holdout."""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
from pathlib import Path
import hashlib
import json
import random
import subprocess
import sys

import pytest
import torch
from safetensors.torch import save_file

from tools import nanojev_gamer_v1_g2_density_discrimination as m
from tools import nanojev_gamer_v1_g2_binding_bootstrap as base
from tools import nanojev_gamer_v1_g2_binding_selection as sel
from tools import nanojev_gamer_v1_g2_binding_discrimination as disc
from tools import nanojev_gamer_v1_g2_relational_training as prior
from tools.nanojev_gamer_v1_graph_corpus import allocation, encounter
from tools.nanojev_gamer_v1_graph_head import GamerGraphHead, BIND_CLASSES


def _head():
    return GamerGraphHead(hidden_size=16,width=16,layers=1,latent_slots=2,
                          memory_slots=4,max_span_tokens=4)


def _train():
    return [encounter(random.Random(18),'train',1), allocation(random.Random(40),'train',2)]


def _case(row,index,head):
    gold=base.gold_binding_edges(row)
    anchors=sorted({a for a,b,k in gold}|{b for a,b,k in gold})
    assert 2<=len(anchors)<=128
    vectors=torch.randn(len(anchors),16,generator=torch.Generator().manual_seed(
        int(base._sha(row['prompt'])[:8],16)))
    with torch.inference_mode():logits=prior.full_logits(head,vectors).detach()
    return {'index':index,'family':row['family'],'prompt_sha256':base._sha(row['prompt']),
            'anchors':anchors,'gold':gold,'logits':logits,'vectors':vectors}


def test_cli():
    result=subprocess.run([sys.executable,'-m','tools.nanojev_gamer_v1_graph_curriculum',
                           'frob-g2-density-discrimination','--help'],
                          cwd=Path(__file__).resolve().parents[1],
                          capture_output=True,text=True,timeout=30)
    assert result.returncode==0,result.stderr
    assert '--relational-run' in result.stdout and '--validation-limit' in result.stdout


def test_new_holdout_prompt_disjoint_and_balanced():
    train=_train()
    first=base.novel_dev_rows(train,limit=4,seed=base.SEED)
    second=sel.new_validation_rows(train,first,limit=4)
    third=disc.new_validation_rows(train,first,second,limit=4)
    fourth=prior.new_validation_rows(train,first,second,third,limit=4)
    fresh=m.new_validation_rows(train,first,second,third,fourth,limit=8)
    assert Counter(x['family'] for x in fresh)=={'encounter':4,'allocation':4}
    assert set(base._sha(x['prompt']) for x in fresh).isdisjoint(
        set(base._sha(x['prompt']) for x in train+first+second+third+fourth))
    with pytest.raises(ValueError,match='BAD_COHORT'):
        m.new_validation_rows([],first,second,third,fourth,limit=4)
    with pytest.raises(ValueError,match='BAD_COHORT'):
        m.new_validation_rows(train,first,second,third,fourth,limit=3)


def test_density_penalty_and_source_competition_no_cap():
    anchors=[(0,1,'actor'),(2,3,'action'),(4,5,'action'),(6,7,'action')]
    scores=torch.full((4,4,len(BIND_CLASSES)),-2.)
    scores[:,:,0]=1.
    scores[0,1,1]=5.;scores[0,2,1]=3.2;scores[0,3,1]=2.8
    allowed={('owns','actor','action')}
    features=m.candidates(anchors,scores,allowed)
    assert len(features)==3
    assert {r[5] for r in features}=={3}
    assert max(r[6] for r in features)>0
    permissive={'minimum_confidence':.45,'density_penalty':0.,'competition_penalty':0.}
    strict={'minimum_confidence':.45,'density_penalty':.75,'competition_penalty':.50}
    all_edges=m.select_edges(features,permissive)
    smaller=m.select_edges(features,strict)
    assert len(all_edges)==3
    assert 0<len(smaller)<len(all_edges)
    assert smaller.issubset(all_edges)
    assert m.select_edges(features,strict)==m.select_edges(features,strict)
    with pytest.raises(ValueError,match='BAD_RULE_VALUE'):
        m.select_edges(features,{**strict,'density_penalty':-0.1})
    with pytest.raises(ValueError,match='BAD_PAIR_SHAPE'):
        m.candidates(anchors,scores[:3],allowed)
    invalid=scores.clone();invalid[0,1,1]=float('nan')
    with pytest.raises(ValueError,match='NONFINITE_LOGITS'):
        m.candidates(anchors,invalid,allowed)


def test_calibration_selection_ignores_fresh_results():
    rules=m.decoder_rules();chosen=next(iter(rules))
    scores={name:{'recall':.65 if name==chosen else .49,
                  'precision':.31,'f1':.40,'false_positive':400} for name in rules}
    result,info=m.choose_rule(scores,{'recall':.77})
    assert result==chosen
    assert info['recall_floor']==pytest.approx(.62)
    scores[chosen]['recall']=.30
    result,info=m.choose_rule(scores,{'recall':.77})
    assert result is None and info['eligible_count']==0
    assert len(rules)==len(m.THRESHOLDS)*len(m.DENSITY_PENALTIES)*len(m.COMPETITION_PENALTIES)
    assert not any('cap' in key for key in rules)


def _fixture(monkeypatch,tmp_path):
    train=_train()
    first=base.novel_dev_rows(train,limit=4,seed=base.SEED)
    second=sel.new_validation_rows(train,first,limit=4)
    third=disc.new_validation_rows(train,first,second,limit=4)
    fourth=prior.new_validation_rows(train,first,second,third,limit=4)
    fresh=m.new_validation_rows(train,first,second,third,fourth,limit=4)
    root=tmp_path/'root';root.mkdir()
    selection_dir=tmp_path/'selection';selection_dir.mkdir()
    discrim_dir=tmp_path/'discrimination';discrim_dir.mkdir()
    relational_dir=tmp_path/'relational';relational_dir.mkdir()
    g2_dir=tmp_path/'bootstrap';g2_dir.mkdir()
    g1_report=root/'g1.json';g1_report.write_text('{}')
    dataset=root/'dataset';dataset.write_text('train')
    head=_head()
    ranking_head=deepcopy(head)
    with torch.no_grad():ranking_head.binding.bias[1].add_(.025)
    weights={k:v.detach().cpu().contiguous() for k,v in base._binding_snapshot(ranking_head).items()}
    save_file(weights,str(relational_dir/prior.WEIGHTS))
    manifest={'model_id':'dummy','source_adapter':'dummy',
              'head_sha256':base.G1_HEAD_SHA}
    bootstrap={'train_prompt_hashes_sha256':base._sha('\n'.join(base._sha(row['prompt']) for row in train)),
               'dev_dataset_sha256':m.sha_rows(first)}
    hashes={'bootstrap':'hash'}
    allowed=sel.compatibility_from_train(train)
    (selection_dir/sel.REPORT).write_text(json.dumps({
        'schema':sel.SCHEMA,'g2_parent_hashes':hashes,
        'validation_dataset_sha256':m.sha_rows(second),
        'compatibility_sha256':sel._hash_json([list(x) for x in sorted(allowed)]),
        'production_promotion':False}))
    (selection_dir/sel.CASES).write_text('old cases')
    third_hashes={'g2_0003_report_sha256':'third-report','g2_0003_cases_sha256':'third-cases'}
    cached=[_case(row,i,head) for i,row in enumerate(fourth,1)]
    old_metrics,old_cases=prior.compute_predictions(cached,ranking_head,allowed,device='cpu')
    for item,saved in zip(cached,old_cases):
        for name,rule in {'g2_0001_argmax_050':sel.configurations()[0]['argmax_050'],
                          'g2_0001_compatible_050':sel.configurations()[0]['compatible_050']}.items():
            saved['configurations'][name]=sel.score_set(
                sel.choose_edges(item['anchors'],item['logits'],rule,allowed),item['gold'])
    metrics={name:base.metrics([{**saved['configurations'][name],'family':saved['family']}
                               for saved in old_cases]) for name in old_cases[0]['configurations']}
    (relational_dir/prior.CASES).write_bytes(base._jsonl([
        {'group':'new_independent_validation',**row} for row in old_cases]))
    report={'schema':prior.SCHEMA,'g1_development_candidate':base.G1_ARM,
            'g1_frozen':True,'g2_bootstrap_weights_frozen':True,
            'g2_0003_fitted_rules_frozen':True,'g2_certified':False,
            'g2_production_unlocked':False,'production_promotion':False,
            'certification_authorized':False,'graph_execution_authorized':False,
            'memory_writes':False,'sealed_test_used':False,'challenge_or_test_split_used':False,
            'gold_used_for_training_and_offline_scoring_only':True,
            'inference_uses_predicted_g1_only':True,'selected_independent_screen_pass':False,
            'rank_selected_rule':'trained_compatible_050',
            'independent_validation_cases':len(fourth),'independent_seed':prior.SEED,
            'independent_dataset_sha256':m.sha_rows(fourth),
            'g1_head_sha256':base.G1_HEAD_SHA,
            'upstream_hashes':{**hashes,**third_hashes},
            'development_weights_sha256':m.backbone.sha256(relational_dir/prior.WEIGHTS),
            'rank_calibration_metrics':{'trained_compatible_050':{'f1':.3}},
            'independent_validation_metrics':metrics}
    (relational_dir/prior.REPORT).write_text(json.dumps(report))
    monkeypatch.setattr(m.sel,'load_parents',lambda *a:(deepcopy(head),manifest,dataset,
                                                       bootstrap,[],{},hashes))
    monkeypatch.setattr(m.base,'original_training_rows',lambda *a:train)
    monkeypatch.setattr(m.prior,'TRAIN_LIMIT',len(train))
    monkeypatch.setattr(m.prior,'CALIBRATION_LIMIT',len(first))
    monkeypatch.setattr(m.sel,'VALIDATION_LIMIT',len(second))
    monkeypatch.setattr(m.disc,'VALIDATION_LIMIT',len(third))
    monkeypatch.setattr(m.prior,'VALIDATION_LIMIT',len(fourth))
    monkeypatch.setattr(m.prior,'verify_discrimination_parent',lambda *a:(None,third_hashes))
    class Model:
        def parameters(self):return iter(())
    monkeypatch.setattr(m.backbone,'model_components',lambda *a:(Model(),None))
    seen=[]
    def capture(capture_head,_model,_tok,rows,**kw):
        seen.append(kw['group'])
        return [_case(row,index,capture_head) for index,row in enumerate(rows,1)]
    monkeypatch.setattr(m.prior,'capture',capture)
    return root,selection_dir,discrim_dir,relational_dir,g2_dir,g1_report,seen,report


def test_mocked_complete_cpu_runner_replay_and_fail_closed(monkeypatch,tmp_path):
    root,selection_dir,discrim_dir,relational_dir,g2_dir,g1_report,seen,parent=_fixture(monkeypatch,tmp_path)
    out=tmp_path/'g2-0005'
    args={'root':root,'g1_validation_report':g1_report,'g2_run':g2_dir,
          'selection_run':selection_dir,'discrimination_run':discrim_dir,
          'relational_run':relational_dir,'output':out,'device':'cpu',
          'validation_limit':4}
    result=m.run(**args)
    assert seen==['calibration_replay','new_independent']
    assert result['calibration_cases']==4 and result['independent_cases']==4
    assert result['g1_frozen'] and result['g2_0004_rank_weights_frozen']
    assert result['trainable_parameters']==0 and not result['production_promotion']
    assert result['parent_g2_0004_replay_pass']
    assert result['parent_hashes']['g2_0004_weights_sha256']==m.backbone.sha256(relational_dir/prior.WEIGHTS)
    assert len((out/m.CASES).read_text().splitlines())==8
    assert (out/m.AUDIT).is_file()
    assert result['independent_metrics']['trained_compatible_050']['cases']==4
    assert result['no_fixed_edge_cap']
    with pytest.raises(FileExistsError,match='IMMUTABLE_OUTPUT'):
        m.run(**args)
    parent['independent_validation_metrics']['trained_compatible_050']['correct']+=1
    (relational_dir/prior.REPORT).write_text(json.dumps(parent))
    with pytest.raises(ValueError,match='PARENT_METRICS'):
        m.run(**{**args,'output':tmp_path/'next'})


def test_parent_checkpoint_tamper_rejected(monkeypatch,tmp_path):
    root,selection_dir,discrim_dir,relational_dir,g2_dir,g1_report,seen,parent=_fixture(monkeypatch,tmp_path)
    parent['development_weights_sha256']='tampered'
    (relational_dir/prior.REPORT).write_text(json.dumps(parent))
    with pytest.raises(ValueError,match='PARENT_WEIGHTS_HASH'):
        m.run(root=root,g1_validation_report=g1_report,g2_run=g2_dir,
              selection_run=selection_dir,discrimination_run=discrim_dir,
              relational_run=relational_dir,output=tmp_path/'reject',device='cpu',
              validation_limit=4)
    assert not (tmp_path/'reject').exists()
