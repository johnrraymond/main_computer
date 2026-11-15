"""G2-0046 acceptance: true prompt-disjoint training and immutable diagnostic."""
from __future__ import annotations

import copy
import json
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest
import torch
from torch.nn import functional as F

from tools import nanojev_gamer_v1_g2_multiprompt_discrimination as d


def scenario(monkeypatch, count=28):
    parent=d.d45.x33.GraphSetEnergy().eval().requires_grad_(False)
    edges=[('a','b','rel'),('a','c','rel'),('d','e','rel'),('d','f','rel')]
    features={'edges':np.zeros((4,d.d45.x33.EDGE_DIM),dtype=np.float32),
              'groups':np.zeros((2,d.d45.x33.GROUP_DIM),dtype=np.float32),
              'index':np.array([0,0,1,1],dtype=np.int64)}
    features['edges'][:,0]=[.8,-.7,.6,-.5]
    features['edges'][:,1]=[-.3,-.5,-.2,-.4]
    features['groups'][:,2]=[.3,-.3]
    with torch.inference_mode():
        x,g,i=d.d45.x33._tensor_features(features,'cpu')
        logits,cnt,total=parent(x,g,i)
    prediction=(logits.detach().double().numpy(),
                F.log_softmax(cnt,-1).detach().double().numpy(),
                float(torch.expm1(total).item()))
    records=[]
    for idx in range(count):
        record={'row':{'split':'train','prompt':f'g2-0046-prompt-{idx:03d}',
                       'family':('synthetic-A' if idx%2 else 'synthetic-B'),
                       'gold':{edges[0],edges[2]}},
                'shortlisted':{'edges':edges},
                'features':copy.deepcopy(features),
                'structured_pred':copy.deepcopy(prediction),
                'opportunity':{'repairable':True,'shortlist_representable':True},
                'admitted':True}
        records.append(record)
    monkeypatch.setattr(d.d45.base,'gold_binding_edges',lambda row:row['gold'])
    policy={'enabled':True,'source':d.g40.SOURCE,'group_weight':.7,
            'global_weight':0.,'fallback':None}
    parent45={'case_selection':{'simple':{'prompt_sha256':d._sha(records[0])},
                                'hard':{'prompt_sha256':d._sha(records[1])}},
              'unseen_train_prompts':[d._sha(r) for r in records[2:8]],
              'training_sha256':{},'train_dataset_sha256':'synthetic-sha'}
    return records,parent,parent45,policy


def test_cohort_is_prompt_disjoint_and_predeclared(monkeypatch):
    records,parent,report,policy=scenario(monkeypatch)
    fit=copy.deepcopy(records[:3]);audit=copy.deepcopy(records[:3])
    for i,rec in enumerate(fit):rec['row']['prompt']=f'fit-{i}'
    for i,rec in enumerate(audit):rec['row']['prompt']=f'audit-{i}'
    result=d.select_cohorts(records,fit,audit,report,policy)
    training={d._sha(r) for r in result['learn']}
    transfer={d._sha(r) for r in result['transfer']}
    assert len(training)>=8
    assert not training&transfer
    assert set(result['fixed_transfer_prompts'])<=transfer
    assert not training&{d._sha(r) for r in fit+audit}
    assert result['all_source_train_count']==len(records)
    assert sum(result['oracle_by_sha'].values())==len(records)
    # Same inputs must produce exactly the same split, never inspect model scores.
    again=d.select_cohorts(records,fit,audit,report,policy)
    assert [d._sha(r) for r in again['learn']]==[d._sha(r) for r in result['learn']]
    fit[0]['row']['prompt']=records[10]['row']['prompt']
    with pytest.raises(ValueError,match='COHORT_OVERLAP'):
        d.select_cohorts(records,fit,audit,report,policy)


def test_nontrain_and_missing_candidate_refused(monkeypatch):
    records,parent,report,policy=scenario(monkeypatch)
    changed=copy.deepcopy(records[0]);changed['row']['split']='dev'
    with pytest.raises(ValueError,match='DEV_LABEL_SUPERVISION_FORBIDDEN'):
        d.prepare_supervision([changed],parent)
    with pytest.raises(ValueError,match='NONTRAIN_RECORD'):
        d.select_cohorts([changed,*records[1:]],[],[],report,policy)
    bad=copy.deepcopy(records[0]);bad['shortlisted']['edges']=bad['shortlisted']['edges'][:1]
    with pytest.raises(ValueError,match='UNREACHABLE_FITTING_PROMPT'):
        d.prepare_supervision([bad],parent)


def test_identical_batches_finite_gradients_parent_unchanged(monkeypatch):
    records,parent,report,policy=scenario(monkeypatch)
    samples=records[8:18]
    prior={name:t.clone() for name,t in parent.state_dict().items()}
    schedule=d.fixed_prompt_batches(len(samples),3,4,d.SEED)
    assert schedule==d.fixed_prompt_batches(len(samples),3,4,d.SEED)
    assert len(schedule)==3 and len(schedule[0])==4
    cached={d._sha(r):d.d45.extract_frozen(r,parent) for r in samples}
    truth=d.prepare_supervision(samples,parent)
    assert set(cached)==set(truth)
    for kind in d.KINDS:
        for loss in d.LOSSES:
            learner,stats=d.fit_shared(samples,parent,kind,loss,policy,steps=2,
                batch_size=4,eval_every=1,supervision=truth,caches=cached)
            assert stats['steps_completed']==2
            assert stats['train_final']['all']['cases']==len(samples)
            assert len(stats['history'])==2
            assert all(torch.equal(parent.state_dict()[key],value)
                           for key,value in prior.items())
            assert all(not p.requires_grad for p in learner.parameters())
    with pytest.raises(ValueError,match='TRAIN_BUDGET_OR_KIND'):
        d.fit_shared(samples,parent,'linear','bce',policy,steps=0)


def test_full_diagnostic_is_atomic_immutable_and_nonpromoting(monkeypatch,tmp_path):
    records,parent,report,policy=scenario(monkeypatch)
    fit=copy.deepcopy(records[:2]);audit=copy.deepcopy(records[:2])
    for i,rec in enumerate(fit):rec['row']['prompt']=f'fit_{i}'
    for i,rec in enumerate(audit):rec['row']['prompt']=f'audit_{i}'
    output=tmp_path/'diagnosis'
    result=d.stage2(records,fit,audit,policy,parent,lambda:None,output,report,{},
                    steps=2,batch_size=4,eval_every=1)
    assert result['status']=='TRAIN_MULTIPROMPT_DIAGNOSIS_COMPLETE'
    assert result['development_evaluation_skipped']
    assert result['g2_certified'] is False
    assert result['production_promotion'] is False
    assert result['train_policy']['enabled'] is False
    assert len(result['results']['arms'])==4
    assert set(result['cohorts']['fixed_g2_0045_transfer_prompts'])<=set(result['cohorts']['transfer_prompts'])
    assert not set(result['cohorts']['train_learn_prompts'])&set(result['cohorts']['transfer_prompts'])
    assert not list(tmp_path.glob('*.staging-*'))
    for file in (d.REPORT,d.LEDGER,d.WEIGHTS,d.POLICIES):
        assert (output/file).is_file()
    assert json.loads((output/d.POLICIES).read_text())==result['train_policy']
    with pytest.raises(FileExistsError,match='IMMUTABLE_OUTPUT'):
        d.stage2(records,fit,audit,policy,parent,lambda:None,output,report,{},steps=2)


def test_output_failure_leaves_no_partial_final_dir(monkeypatch,tmp_path):
    records,parent,report,policy=scenario(monkeypatch)
    # Deliberate refusal after selecting cohorts, before output publication.
    changed=copy.deepcopy(records)
    changed[9]['row']['split']='dev'
    output=tmp_path/'should-not-exist'
    with pytest.raises(ValueError,match='NONTRAIN_RECORD'):
        d.stage2(changed,[],[],policy,parent,lambda:None,output,report,{},steps=2,eval_every=1)
    assert not output.exists()


def test_real_uploaded_0045_parent_verifier_and_tampering(tmp_path):
    originals=Path('/mnt/data')
    source={d.d45.REPORT:'g2_separability_diagnosis_report(1).json',
            d.d45.LEDGER:'g2_separability_diagnosis_per_case.jsonl(1).gz',
            d.d45.WEIGHTS:'g2_separability_diagnosis_development(1).safetensors',
            d.d45.POLICIES:'g2_separability_diagnosis_frozen_policies(1).json'}
    if not all((originals/name).exists() for name in source.values()):
        pytest.skip('uploaded G2-0045 artifacts are not mounted')
    for name,origin in source.items():
        shutil.copy2(originals/origin,tmp_path/name)
    report=json.loads((tmp_path/d.d45.REPORT).read_text())
    verified,hashes=d.verify_parent45(tmp_path,report['parent_hashes'])
    assert verified['status']=='TRAIN_SEPARABILITY_DIAGNOSIS_COMPLETE'
    assert hashes['g2_0045_report_sha256']==d.backbone.sha256(tmp_path/d.d45.REPORT)
    (tmp_path/d.d45.POLICIES).write_text('{"enabled":true}')
    with pytest.raises(ValueError,match='PARENT_POLICY_DRIFT'):
        d.verify_parent45(tmp_path,report['parent_hashes'])


def test_cli_command_registered():
    proc=subprocess.run([sys.executable,'-m','tools.nanojev_gamer_v1_graph_curriculum',
        'frob-g2-multiprompt-discrimination','--help'],capture_output=True,check=True,text=True)
    assert '--separability45-run' in proc.stdout
    assert '--steps' in proc.stdout
    assert '--batch-prompts' in proc.stdout
    assert '--eval-every' in proc.stdout
