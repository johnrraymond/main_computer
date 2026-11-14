"""G2-0004 lineage, train-only labels, hard-negative ranking, and complete runner."""
from __future__ import annotations

from collections import Counter
from pathlib import Path
import hashlib
import json
import random
import subprocess
import sys

import pytest
import torch

from tools import nanojev_gamer_v1_g2_relational_training as m
from tools import nanojev_gamer_v1_g2_binding_bootstrap as base
from tools import nanojev_gamer_v1_g2_binding_selection as sel
from tools import nanojev_gamer_v1_g2_binding_discrimination as disc
from tools.nanojev_gamer_v1_graph_corpus import allocation, encounter
from tools.nanojev_gamer_v1_graph_head import BIND_CLASSES, GamerGraphHead


def _case(prompt='example',family='encounter'):
    anchors=[(0,2,'actor'),(3,5,'action'),(6,8,'action'),(9,10,'quantity')]
    gold={(anchors[0],anchors[1],'owns'),(anchors[3],anchors[2],'applies_to')}
    g=torch.Generator().manual_seed(int(base._sha(prompt)[:8],16))
    vectors=torch.randn(4,16,generator=g)
    return {'index':1,'family':family,'prompt_sha256':base._sha(prompt),
            'anchors':anchors,'gold':gold,'vectors':vectors,
            'logits':torch.zeros((4,4,len(BIND_CLASSES)))}


def _head():
    return GamerGraphHead(hidden_size=16,width=16,layers=1,latent_slots=2,
                          memory_slots=4,max_span_tokens=4)


def _rows():
    return [encounter(random.Random(18),'train',1), allocation(random.Random(40),'train',2)]


def test_targets_include_unmatched_predicted_nodes_as_negative():
    c=_case()
    pos,neg=m.target_pairs(c)
    assert len(pos)==2 and len(neg)==10
    assert {(i,j) for i,j,k in pos}=={(0,1),(3,2)}
    c['anchors'].append((14,15,'action'))
    c['vectors']=torch.randn(5,16)
    pos,neg=m.target_pairs(c)
    assert len(pos)==2 and len(neg)==18
    c['anchors'][4]=c['anchors'][0]
    with pytest.raises(ValueError,match='DUPLICATE'):
        m.target_pairs(c)


def test_hard_mining_and_source_relation_rank_training_only():
    c=_case()
    head=_head()
    pos,neg=m.target_pairs(c)
    first=m.mine_hard_negatives(head,c['vectors'],pos,neg,seed=17)
    assert first==m.mine_hard_negatives(head,c['vectors'],pos,neg,seed=17)
    assert first and all(k==0 for i,j,k in first)
    ranks=m.ranking_pairs(pos,first,head,c['vectors'])
    assert all(pos[p][0]==first[n][0] and label==pos[p][2] for p,n,label in ranks)
    assert ranks
    ce,ce_counts=m.training_loss(head,c,mode='hardneg_ce',seed=21,device='cpu')
    ranked,rank_counts=m.training_loss(head,c,mode='relation_rank',seed=21,device='cpu')
    assert ce.isfinite() and ranked.isfinite()
    assert ce_counts['rank_pairs']==0 and rank_counts['rank_pairs']>0
    assert ranked.item()>=ce.item()
    with pytest.raises(ValueError,match='BAD_TRAIN_MODE'):
        m.training_loss(head,c,mode='wrong',seed=1,device='cpu')


def test_gradient_updates_only_g2_params_and_parent_stays_immutable():
    c=_case()
    h=_head()
    original={k:v.detach().clone() for k,v in h.state_dict().items()}
    count=m.cur.trainable_stage(h,'G2')
    assert count>0
    loss,_=m.training_loss(h,c,mode='relation_rank',seed=1,device='cpu')
    loss.backward()
    for name,p in h.named_parameters():
        if name.startswith(('binding.','pair_projection.')):
            assert p.grad is not None
        else:
            assert p.grad is None
    m.validate_original_state(h,{k:v for k,v in original.items()
                                if not k.startswith(('binding.','pair_projection.'))})
    with torch.no_grad():h.project[1].weight.add_(1)
    with pytest.raises(ValueError,match='FROZEN_PARAMETER_DRIFT'):
        m.validate_original_state(h,{k:v for k,v in original.items()
                                    if not k.startswith(('binding.','pair_projection.'))})


def test_new_split_excludes_prior_four_cohorts():
    train=_rows()
    first=base.novel_dev_rows(train,limit=4,seed=base.SEED)
    second=sel.new_validation_rows(train,first,limit=4)
    third=disc.new_validation_rows(train,first,second,limit=4)
    final=m.new_validation_rows(train,first,second,third,limit=4)
    assert Counter(row['family'] for row in final)=={'encounter':2,'allocation':2}
    hashes=[base._sha(row['prompt']) for row in train+first+second+third+final]
    assert len(set(hashes))==len(hashes)


def test_calibration_only_selection_enforces_recall_floor():
    metrics={k:{'recall':.5,'f1':.75,'precision':.2,'false_positive':300}
             for k in m.candidate_rules()}
    assert m.choose_rule(metrics,{'recall':.79})[0] is None
    metrics['trained_compatible_060'].update(recall=.65,f1=.3)
    assert m.choose_rule(metrics,{'recall':.79})[0]=='trained_compatible_060'
    metrics['trained_argmax_050'].update(recall=.70,f1=.35)
    assert m.choose_rule(metrics,{'recall':.79})[0]=='trained_argmax_050'


def test_cli_direct_command():
    p=subprocess.run([sys.executable,'-m','tools.nanojev_gamer_v1_graph_curriculum',
                      'frob-g2-relational-training','--help'],cwd=Path(__file__).resolve().parents[1],
                     text=True,capture_output=True,timeout=30)
    assert p.returncode==0,p.stderr
    assert '--device' in p.stdout and '--discrimination-run' in p.stdout


def _fixture(monkeypatch,tmp_path):
    train=_rows()
    first=base.novel_dev_rows(train,limit=4,seed=base.SEED)
    second=sel.new_validation_rows(train,first,limit=4,seed=sel.SEED)
    third=disc.new_validation_rows(train,first,second,limit=4,seed=disc.SEED)
    final=m.new_validation_rows(train,first,second,third,limit=4,seed=m.SEED)
    root=tmp_path/'root';root.mkdir()
    bootstrap_run=tmp_path/'bootstrap';bootstrap_run.mkdir()
    selection_run=tmp_path/'selection';selection_run.mkdir()
    discrimination_run=tmp_path/'discrimination';discrimination_run.mkdir()
    g1_report=root/'g1.json';g1_report.write_text('{}')
    dataset=root/'dataset.jsonl';dataset.write_text('train')
    head=_head()
    manifest={'model_id':'dummy','source_adapter':'dummy','head_sha256':'mock-g1'}
    bootstrap={'train_prompt_hashes_sha256':base._sha('\n'.join(base._sha(r['prompt']) for r in train)),
               'dev_dataset_sha256':m._sha_rows(first)}
    hashes={'parent':'checksum'}
    allowed=sel.compatibility_from_train(train)
    select_report={'schema':sel.SCHEMA,'g2_parent_hashes':hashes,
                   'validation_dataset_sha256':m._sha_rows(second),
                   'compatibility_sha256':sel._hash_json([list(x) for x in sorted(allowed)]),
                   'production_promotion':False}
    (selection_run/sel.REPORT).write_text(json.dumps(select_report))
    (selection_run/sel.CASES).write_text('prior evidence')
    def stats(row):
        gold=base.gold_binding_edges(row)
        return {'correct':0,'missing':len(gold),'false_positive':0,'exact':not gold}
    cases=[]
    config_names=('argmax_050','relation_confidence_f2','source_competition_f1')
    for index,row in enumerate(third,1):
        cases.append({'group':'new_independent_validation','index':index,'family':row['family'],
                      'prompt_sha256':base._sha(row['prompt']),
                      'g1_predicted_nodes':5,'gold_binding_edges':len(base.gold_binding_edges(row)),
                      'configurations':{name:stats(row) for name in config_names}})
    (discrimination_run/disc.CASES).write_bytes(base._jsonl(cases))
    fitted={name:{'mode':('confidence' if 'confidence' in name else 'source_share'),
                  'thresholds':dict.fromkeys(disc.RELATIONS,.5),'compatible':True}
            for name in ('relation_confidence_f1','relation_confidence_f2','relation_confidence_f3',
                         'source_competition_f1','source_competition_f2','source_competition_f3')}
    fitted['dual_relation_source_f2']={'mode':'dual','compatible':True,
                                      'confidence':dict.fromkeys(disc.RELATIONS,.5),
                                      'source_share':dict.fromkeys(disc.RELATIONS,.5)}
    compare_hashes={**hashes,
        'g2_0002_report_sha256':m.backbone.sha256(selection_run/sel.REPORT),
        'g2_0002_cases_sha256':m.backbone.sha256(selection_run/sel.CASES)}
    previous={'schema':disc.SCHEMA,'g1_frozen':True,'g2_development_weights_frozen':True,
              'trainable_parameters':0,'g2_certified':False,'g2_production_unlocked':False,
              'production_promotion':False,'certification_authorized':False,
              'graph_execution_authorized':False,'memory_writes':False,
              'sealed_test_used':False,'challenge_or_test_split_used':False,
              'oracle_input_to_decoder':False,'train_only_compatibility':True,
              'g2_0002_replay_pass':True,
              'selection_split':'g2_0002_previously_inspected_dev_independent',
              'validation_split':'new_prompt_disjoint_dev',
              'g1_development_candidate':base.G1_ARM,'independent_seed':disc.SEED,
              'independent_validation_cases':len(third),
              'independent_validation_dataset_sha256':m._sha_rows(third),
              'parent_hashes':compare_hashes,'decoder_rules_calibration_fitted':fitted,
              'selected_rule':'relation_confidence_f2',
              'candidate_discrimination_screen_pass':False,
              'independent_validation_metrics':{
                  name:base.metrics([{**stats(row),'family':row['family']} for row in third])
                  for name in config_names}}
    (discrimination_run/disc.REPORT).write_text(json.dumps(previous))
    monkeypatch.setattr(m.select,'load_parents',lambda *a:(head,manifest,dataset,bootstrap,[],{},hashes))
    monkeypatch.setattr(m,'TRAIN_LIMIT',len(train))
    monkeypatch.setattr(m,'CALIBRATION_LIMIT',len(first))
    monkeypatch.setattr(m.select,'VALIDATION_LIMIT',len(second))
    monkeypatch.setattr(m.discrim,'VALIDATION_LIMIT',len(third))
    monkeypatch.setattr(m.base,'original_training_rows',lambda *_:train)
    monkeypatch.setattr(m.base,'novel_dev_rows',lambda rows,limit,seed:
        {base.SEED:first,sel.SEED:second,disc.SEED:third,m.SEED:final}[seed])
    monkeypatch.setattr(m.select,'new_validation_rows',lambda *a,**kw:second)
    monkeypatch.setattr(m.discrim,'new_validation_rows',lambda *a,**kw:third)
    monkeypatch.setattr(m,'new_validation_rows',lambda *a,**kw:final)
    class NoGradModel:
        def parameters(self):return iter(())
    monkeypatch.setattr(m.backbone,'model_components',lambda *a:(NoGradModel(),None))
    groups=[]
    def fake_capture(_head,_model,_tok,rows,**kw):
        groups.append(kw['group'])
        out=[]
        for index,row in enumerate(rows,1):
            c=_case(row['prompt'],row['family'])
            c['index']=index
            with torch.inference_mode():c['logits']=m.full_logits(_head,c['vectors'])
            out.append(c)
        return out
    monkeypatch.setattr(m,'capture',fake_capture)
    return root,bootstrap_run,selection_run,discrimination_run,g1_report,groups,previous


def test_mocked_complete_runner_and_lineage_failure(monkeypatch,tmp_path):
    root,bootstrap_run,selection_run,discrimination_run,g1_report,groups,previous=_fixture(monkeypatch,tmp_path)
    output=tmp_path/'g2-0004'
    args={'root':root,'output':output,'g1_validation_report':g1_report,
          'g2_run':bootstrap_run,'selection_run':selection_run,
          'discrimination_run':discrimination_run,'device':'cpu',
          'validation_limit':4,'steps':2}
    result=m.run(**args)
    assert groups==['train','calibration','new_independent']
    assert result['train_cases']==2 and result['independent_validation_cases']==4
    assert result['g1_frozen'] and not result['production_promotion']
    assert result['ce_only_mining']['rank_pairs']==0
    assert result['relation_rank_mining']['rank_pairs']>0
    assert result['g2_bootstrap_weights_frozen']
    assert 'g2_0001_argmax_050' in result['independent_validation_metrics']
    assert 'g2_0003_source_competition_f1' in result['independent_validation_metrics']
    assert 'ce_only_trained_argmax_050' in result['independent_validation_metrics']
    assert 'trained_argmax_050' in result['independent_validation_metrics']
    assert len((output/m.CASES).read_text().splitlines())==4
    assert (output/m.WEIGHTS).is_file()
    assert result['development_weights_sha256']==m.backbone.sha256(output/m.WEIGHTS)
    with pytest.raises(FileExistsError,match='IMMUTABLE_OUTPUT'):
        m.run(**args)
    previous['decoder_rules_calibration_fitted']['relation_confidence_f2']['thresholds']['owns']=float('nan')
    (discrimination_run/disc.REPORT).write_text(json.dumps(previous))
    with pytest.raises(ValueError,match='PARENT_RULE_VALUE'):
        m.run(**{**args,'output':tmp_path/'g2-next'})
