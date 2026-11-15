"""G2-0048: acceptance of corrected G1->G2 evidence path and non-promotion."""
from __future__ import annotations

import copy
import json
import subprocess
import sys

import pytest
import torch

from tools import nanojev_gamer_v1_g2_occurrence_evidence_repair as d
from tests.test_nanojev_gamer_v1_g2_architectural_crossover import fixture as old_fixture


def fixture(monkeypatch,count=28,width=12):
    records,fitting,auditing,parent,p45,p46,policy,cohort=old_fixture(monkeypatch,count=count)
    anchors=[(5+i*11,12+i*11,'actor') for i in range(6)]
    edges=[(anchors[0],anchors[1],'owns'),(anchors[0],anchors[2],'owns'),
           (anchors[3],anchors[4],'owns'),(anchors[3],anchors[5],'owns')]
    for rec in (*records,*fitting,*auditing):
        rec['shortlisted']['edges']=list(edges)
        rec['row']['gold']={edges[0],edges[2]}
        gen=torch.Generator().manual_seed(int(sum(map(ord,rec['row']['prompt']))))
        windows=torch.randn((6,5,width),generator=gen)
        rec['recovered']={
            'anchors':list(anchors),'windows':windows.to(torch.float16),
            'window_mask':torch.ones(6,5,dtype=torch.bool),
            'gold':set(),
        }
    cohort=d.d46.select_cohorts(records,fitting,auditing,p45,policy)
    p46['cohorts'].update({
        'train_learn_prompts':[d.d46._sha(r) for r in cohort['learn']],
        'transfer_prompts':[d.d46._sha(r) for r in cohort['transfer']],
        'train_fit_prompts':[d.d46._sha(r) for r in cohort['train_fit']],
        'natural_train_audit_prompts':[d.d46._sha(r) for r in cohort['natural_train_audit']],
        'excluded_train_prompts':cohort['excluded_train_prompts'],
        'fixed_g2_0045_transfer_prompts':cohort['fixed_transfer_prompts'],
    })
    p46['results']['arms']['nonlinear:bce']={section:{} for section in
              ('learn','transfer','train_fit','natural_train_audit')}
    p47={'cohorts':p46['cohorts'],'results':{'arms':{'frozen:bce':{
         section:{'candidate_reachable':{'exact':1,'missing':7,'extra':9},'all':{}}
         for section in ('train_learn','transfer','train_fit','natural_train_audit')}}}}
    return records,fitting,auditing,parent,p45,p46,p47,policy,cohort


def test_source_audit_confirms_fifteen_score_only_features():
    import inspect
    src=inspect.getsource(d.x33.set_features)
    assert 'refs[i]' in src and 'scores[i]' in src
    assert "item['edges'][i] in chosen" in src
    assert 'node_vectors' not in src
    assert 'windows' not in src
    assert d.x33.EDGE_DIM==15


def test_restored_context_matches_predicted_occurrences_and_is_gold_blind(monkeypatch):
    records,_,_,parent,*_=fixture(monkeypatch)
    rec=records[0]
    x=d.prepare_evidence(rec,parent)
    assert x['windows'].shape==(6,5,12)
    assert x['source'].tolist()==[0,0,3,3]
    assert x['destination'].tolist()==[1,2,4,5]
    assert x['relation'].tolist()==[1,1,1,1]
    assert x['source_kind'].tolist()==[1]*4
    assert x['geometry'].shape==(4,4)
    assert 'gold' not in x and 'prompt' not in x
    poisoned=copy.deepcopy(rec)
    poisoned['row']['gold']={'unrelated':True}
    poisoned['recovered']['gold']={'fake'}
    y=d.prepare_evidence(poisoned,parent)
    assert all(torch.equal(x[k],y[k]) for k in x)
    assert all(not value.requires_grad for value in x.values())


def test_corruption_refusal_and_no_silent_context_drop(monkeypatch):
    records,_,_,parent,*_=fixture(monkeypatch)
    rec=records[0]
    for mutation,pattern in (
        (lambda r:r.pop('recovered'),'MISSING_UPSTREAM_OCCURRENCES'),
        (lambda r:r['recovered'].update(window_mask=torch.zeros(6,5,dtype=torch.bool)),
         'UPSTREAM_WINDOW_ALIGNMENT'),
        (lambda r:r['shortlisted'].update(edges=[((900,901,'actor'),
                  r['shortlisted']['edges'][0][1],'owns'),*r['shortlisted']['edges'][1:]]),
         'CANDIDATE_ENDPOINT_UNREACHABLE'),
    ):
        broken=copy.deepcopy(rec);mutation(broken)
        with pytest.raises(ValueError,match=pattern):
            d.prepare_evidence(broken,parent)
    dev=copy.deepcopy(rec);dev['row']['split']='dev'
    with pytest.raises(ValueError,match='NONTRAIN_EVIDENCE'):
        d.prepare_evidence(dev,parent)


def test_context_changes_edge_scores_after_learning_and_source_target_are_ordered(monkeypatch):
    records,_,_,parent,*_=fixture(monkeypatch)
    x=d.prepare_evidence(records[0],parent)
    net=d.OccurrenceEvidenceScorer(12)
    with torch.inference_mode():
        assert torch.allclose(net(x),x['parent_score'],atol=1e-6)
    # Give the residual layer a nonzero known weight; the contextual evidence
    # must now affect predictions, while the frozen parent stays identical.
    with torch.no_grad():
        net.scorer[-1].weight.fill_(0.02)
    with torch.inference_mode():
        baseline=net(x)
        changed={**x,'windows':x['windows'].clone()}
        changed['windows'][1]*=-2
        perturbed=net(changed)
        assert torch.max(torch.abs(baseline-perturbed))>1e-6
        swapped={**x,'source':x['destination'],'destination':x['source']}
        reversed_scores=net(swapped)
        assert torch.max(torch.abs(baseline-reversed_scores))>1e-6


def test_gradient_reaches_context_encoder_and_frozen_parent_unmodified(monkeypatch):
    records,_,_,parent,*_=fixture(monkeypatch)
    x=d.prepare_evidence(records[0],parent)
    parent_state={k:v.clone() for k,v in parent.state_dict().items()}
    net=d.OccurrenceEvidenceScorer(12)
    scores=net(x)
    first=(scores.sum())
    first.backward()
    assert net.scorer[-1].weight.grad is not None
    optimizer=torch.optim.AdamW(net.parameters(),lr=.002)
    optimizer.step();optimizer.zero_grad(set_to_none=True)
    net(x).sum().backward()
    assert any(p.grad is not None and torch.isfinite(p.grad).all() and float(p.grad.abs().sum())>0
               for p in net.encoder.parameters())
    assert all(torch.equal(value,parent.state_dict()[k]) for k,value in parent_state.items())


def test_small_training_and_immutable_diagnostic(monkeypatch,tmp_path):
    records,fit,audit,parent,p45,p46,p47,policy,cohort=fixture(monkeypatch)
    directory=tmp_path/'evidence-repair'
    result=d.stage2(records,fit,audit,policy,parent,lambda:None,directory,
         p45,p46,p47,{},steps=2,batch_size=3,eval_every=1,device='cpu')
    assert result['status']=='TRAIN_OCCURRENCE_EVIDENCE_REPAIR_COMPLETE'
    assert result['results']['transfer']['all']['cases']==len(cohort['transfer'])
    assert result['fit']['steps_completed']==2
    assert result['production_promotion'] is False
    assert result['train_policy']['enabled'] is False
    assert result['development_evaluation_skipped'] is True
    assert set(result['input_audit']['restored_evidence'])>={'source_destination_order',
                                                              'relation_id','frozen_qwen_token_windows'}
    for filename in (d.REPORT,d.LEDGER,d.WEIGHTS,d.POLICIES):
        assert (directory/filename).is_file()
    assert json.loads((directory/d.POLICIES).read_text())==result['train_policy']
    with pytest.raises(FileExistsError,match='IMMUTABLE_OUTPUT'):
        d.stage2(records,fit,audit,policy,parent,lambda:None,directory,
                 p45,p46,p47,{},steps=2,eval_every=1)


def test_non_train_cannot_be_scored(monkeypatch):
    records,_,_,parent,*rest=fixture(monkeypatch)
    policy=rest[-2]
    x=d.prepare_evidence(records[0],parent)
    broken=copy.deepcopy(records[0]);broken['row']['split']='dev'
    with pytest.raises(ValueError,match='DEV_EVALUATION_REFUSED'):
        d.score_records([broken],d.OccurrenceEvidenceScorer(12),policy,'transfer',0,
                        {d.d46._sha(broken):x},'cpu')


def test_cli_registered():
    proc=subprocess.run([sys.executable,'-m','tools.nanojev_gamer_v1_graph_curriculum',
       'frob-g2-occurrence-evidence-repair','--help'],check=True,capture_output=True,text=True)
    assert '--crossover47-run' in proc.stdout
    assert '--steps' in proc.stdout
    assert '--device' in proc.stdout


def test_0047_parent_lineage_and_ledger_refuses_drift(monkeypatch,tmp_path):
    import gzip
    records,fit,audit,parent,p45,p46,p47,policy,cohort=fixture(monkeypatch)
    metric_fields=('cases','exact','missing','extra','rank_inversions')
    sections=('train_learn','transfer','train_fit','natural_train_audit')
    def metrics(count):
        return {name:{k:(count if k=='cases' else 0) for k in metric_fields}
                for name in ('all','candidate_reachable','candidate_unreachable')}
    for section in sections:
        p46['results']['arms']['nonlinear:bce']['learn' if section=='train_learn' else section]=metrics(
            len(p46['cohorts'][section+'_prompts']))
    frozen={section:metrics(len(p46['cohorts'][section+'_prompts'])) for section in sections}
    arms={f'{a}:{b}':{'transfer':metrics(len(p46['cohorts']['transfer_prompts']))}
          for a in d.d47.REPRESENTATIONS for b in d.d47.OBJECTIVES}
    arms['frozen:bce']=frozen
    policies={'enabled':False,'config':None,'disabled_reason':'diagnostic_only_no_selection'}
    report={'schema':d.d47.SCHEMA,'status':'TRAIN_ARCHITECTURAL_CROSSOVER_COMPLETE',
        'source_parent_schema':d.d46.SCHEMA,'parent_hashes':{'mock':'lineage'},
        'cohorts':p46['cohorts'],'results':{'arms':arms},
        'protocol':{'reference_budget_reproduced':True,'steps':d.STEPS,
                    'batch_prompts':d.BATCH_PROMPTS,
                    'representations':list(d.d47.REPRESENTATIONS),
                    'objectives':list(d.d47.OBJECTIVES)},
        'decision':{'decision':'NO_DECISIVE_TRANSFER_GAIN_REEXAMINE_INPUT_EVIDENCE_AND_GRAPH_OBJECTIVE'},
        'development_evaluation_skipped':True,'dev_labels_used_for_training':False,
        'dev_labels_used_for_calibration':False,'sealed_test_used':False,
        'production_promotion':False,'g2_certified':False,'frozen_g1':True,
        'frozen_g2_0033_parent':True,'train_policy':policies}
    (tmp_path/d.d47.POLICIES).write_text(json.dumps(policies))
    (tmp_path/d.d47.WEIGHTS).write_bytes(b'mock frozen parent weights')
    with gzip.open(tmp_path/d.d47.LEDGER,'wt',encoding='utf-8') as stream:
        for section in sections:
            for arm in arms:
                for sha in p46['cohorts'][section+'_prompts']:
                    stream.write(json.dumps({'section':section,'arm':arm,'prompt_sha256':sha})+'\n')
    for filename,field in ((d.d47.POLICIES,'policies_sha256'),
                           (d.d47.WEIGHTS,'weights_sha256'),
                           (d.d47.LEDGER,'ledger_sha256')):
        report[field]=d.backbone.sha256(tmp_path/filename)
    (tmp_path/d.d47.REPORT).write_text(json.dumps(report))
    valid,h=d.verify_parent47(tmp_path,{'mock':'lineage'},p46)
    assert valid['status']=='TRAIN_ARCHITECTURAL_CROSSOVER_COMPLETE'
    assert h['g2_0047_report_sha256']==d.backbone.sha256(tmp_path/d.d47.REPORT)
    broken=copy.deepcopy(report);broken['results']['arms']['frozen:bce']['transfer']['all']['exact']=1
    (tmp_path/d.d47.REPORT).write_text(json.dumps(broken))
    with pytest.raises(ValueError,match='REPRODUCTION'):
        d.verify_parent47(tmp_path,{'mock':'lineage'},p46)
    (tmp_path/d.d47.REPORT).write_text(json.dumps(report))
    with gzip.open(tmp_path/d.d47.LEDGER,'at',encoding='utf-8') as stream:
        stream.write(json.dumps({'section':'transfer','arm':'frozen:bce',
                                 'prompt_sha256':p46['cohorts']['transfer_prompts'][0]})+'\n')
    with pytest.raises(ValueError,match='HASH'):
        d.verify_parent47(tmp_path,{'mock':'lineage'},p46)
