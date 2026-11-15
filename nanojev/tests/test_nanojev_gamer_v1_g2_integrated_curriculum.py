"""G2-0049: matched learning methods, source-level causal safeguards and artifacts."""
from __future__ import annotations

import copy
import gzip
import json
import subprocess
import sys

import pytest
import torch

from tools import nanojev_gamer_v1_g2_integrated_curriculum as d
from tests.test_nanojev_gamer_v1_g2_occurrence_evidence_repair import fixture


def setup(monkeypatch,count=28):
    torch.manual_seed(24049)
    records,fitting,auditing,parent,p45,p46,p47,policy,cohort=fixture(monkeypatch,count=count)
    evidence={};contexts={}
    for sec in d.SECTIONS:
        for rec in cohort[sec]:
            sha=d.d46._sha(rec)
            ex=d.d48.prepare_evidence(rec,parent)
            evidence[sha]=ex
            contexts[sha]=d.prepare_count_context(rec,ex)
    learn={d.d46._sha(rec) for rec in cohort['learn']}
    model=d.d48.OccurrenceEvidenceScorer(12)
    scores={}
    for sec in d.SECTIONS:
        rows=d.d48.score_records(cohort[sec],model,policy,sec,0,evidence,'cpu')
        scores[sec]={r['prompt_sha256']:r for r in rows}
    p48={'cohorts':p46['cohorts'],'results':{
        sec:d.d46.summarize(list(scores[sec].values())) for sec in d.SECTIONS}}
    p46['train_dataset_sha256']='synthetic-g2-0040'
    p46['training_sha256']={'train_0040':'synthetic','train_fit_0040':'synthetic',
                             'train_audit_0040':'synthetic'}
    supervision=d.d46.prepare_supervision(cohort['learn'],parent)
    baseline={key:v.detach().clone() for key,v in model.state_dict().items()}
    return (records,fitting,auditing,parent,p45,p46,p47,p48,policy,cohort,
            evidence,contexts,scores,supervision,baseline)


def test_prior_count_is_label_blind_and_aligned(monkeypatch):
    records,_,_,parent,*_=setup(monkeypatch)
    ex=d.d48.prepare_evidence(records[0],parent)
    base=d.prepare_count_context(records[0],ex)
    poisoned=copy.deepcopy(records[0])
    poisoned['row']['gold']={'a':'deliberately invalid gold'}
    second=d.prepare_count_context(poisoned,ex)
    assert all(torch.equal(base[k],second[k]) for k in base)
    assert set(base)=={'group_index','frozen_logcounts','frozen_total'}
    assert len(base['group_index'])==len(records[0]['shortlisted']['edges'])
    poisoned=copy.deepcopy(records[0])
    poisoned['structured_pred']=(poisoned['structured_pred'][0],
            poisoned['structured_pred'][1][:-1],poisoned['structured_pred'][2])
    with pytest.raises(ValueError,match='COUNT_CONTEXT_ALIGNMENT'):
        d.prepare_count_context(poisoned,ex)


def test_unmodified_0048_logits_before_training_and_count_head_is_live(monkeypatch):
    *_, evidence,contexts,scores,supervision,baseline=setup(monkeypatch)
    sha=next(iter(evidence))
    ex=evidence[sha];ctx=contexts[sha]
    model=d.CurriculumScorer(12)
    model.load_baseline(baseline)
    raw=d.d48.OccurrenceEvidenceScorer(12)
    raw.load_state_dict(baseline)
    with torch.no_grad():
        s,c,t=model(ex,ctx)
        assert torch.allclose(s,raw(ex))
        assert torch.equal(c,ctx['frozen_logcounts'])
        assert torch.equal(t,ctx['frozen_total'])
        s2,c2,t2=model(ex,ctx,train_counts=True)
        assert torch.allclose(s2,s)
        assert torch.allclose(c2,c,atol=1e-6)
        assert torch.allclose(t2,t,atol=1e-5)
    with torch.no_grad():
        model.group_count[-1].bias[2]=2.0
        model.total_count[-1].bias[0]=.2
        _,c3,t3=model(ex,ctx,train_counts=True)
        assert not torch.allclose(c3,c)
        assert not torch.allclose(t3,t)
        _,c4,t4=model(ex,ctx,train_counts=False)
        assert torch.equal(c4,c)
        assert torch.equal(t4,t)


def test_rank_and_cardinality_terms_have_gradients(monkeypatch):
    *_,evidence,contexts,scores,supervision,baseline=setup(monkeypatch)
    key=next(k for k in supervision if supervision[k][3])
    model=d.CurriculumScorer(12)
    model.load_baseline(baseline)
    scores,counts,total=model(evidence[key],contexts[key],train_counts=True)
    loss,terms=d.loss_terms(scores,counts,total,supervision[key],
            contexts[key]['group_index'],use_rank=True,use_count=True)
    assert terms['ranking']>0
    assert terms['group_count']>0
    loss.backward()
    assert model.group_count[-1].weight.grad is not None
    assert model.total_count[-1].weight.grad is not None
    assert model.binding.scorer[-1].weight.grad is not None
    assert torch.isfinite(model.group_count[-1].weight.grad).all()
    assert torch.isfinite(model.total_count[-1].weight.grad).all()
    assert model.group_count[-1].weight.grad.abs().sum()>0
    assert model.total_count[-1].weight.grad.abs().sum()>0
    # Inverse their label preference: the contrastive ranking term increases.
    truth=supervision[key][2]
    y_idx=(truth==1).nonzero().flatten()
    n_idx=(truth==0).nonzero().flatten()
    scores_bad=torch.where(truth.bool(),torch.full_like(scores,-5),torch.full_like(scores,5))
    loss_bad,parts_bad=d.loss_terms(scores_bad,counts.detach(),total.detach(),supervision[key],
                contexts[key]['group_index'],use_rank=True)
    assert parts_bad['ranking']>terms['ranking']



def test_learned_count_logits_change_actual_frozen_decoder_decisions(monkeypatch):
    *_,policy,cohort,evidence,contexts,rows,supervision,baseline=setup(monkeypatch)
    rec=cohort['learn'][0]
    sha=d.d46._sha(rec)
    net=d.CurriculumScorer(12)
    net.load_baseline(baseline)
    with torch.no_grad():
        s,c,t=net(evidence[sha],contexts[sha])
        original=d.d44._decode(rec,(s.double().numpy(),c.double().numpy(),float(t)),policy)
        # Deliberately favor one edge per source-relation group; this only
        # changes the learned count head and does not touch the fixed decoder.
        net.group_count[-1].bias[1]=100.
        s,c,t=net(evidence[sha],contexts[sha],train_counts=True)
        corrected=d.d44._decode(rec,(s.double().numpy(),c.double().numpy(),float(t)),policy)
    assert len(original)==0
    assert len(corrected)==len(rec['features']['groups'])
    assert corrected!=original


def test_negative_frozen_total_is_valid_and_decoder_compatible(monkeypatch):
    records,_,_,parent,*_=setup(monkeypatch)
    rec=copy.deepcopy(records[0]);rec['structured_pred']=(
        rec['structured_pred'][0],rec['structured_pred'][1],-.5)
    ex=d.d48.prepare_evidence(rec,parent)
    ctx=d.prepare_count_context(rec,ex)
    net=d.CurriculumScorer(12)
    with torch.no_grad():
        s,c,t=net(ex,ctx,train_counts=True)
    assert float(t)==pytest.approx(-.5,abs=1e-5)
    assert float(t)<0

def test_replay_cohort_and_stage_are_precommitted(monkeypatch):
    *_,cohort,evidence,contexts,scores,supervision,baseline=setup(monkeypatch)
    learn=cohort['learn']
    batches,anchors=d.plan_batches(learn,12,8)
    batches2,anchors2=d.plan_batches(learn,12,8)
    assert batches==batches2 and anchors==anchors2
    assert all(len(batch)==8 for batch in batches)
    assert all(set(batch)<=set(range(len(learn))) for batch in batches)
    assert d.curriculum_stage(1,12)=='foundation'
    assert d.curriculum_stage(4,12)=='ranking'
    assert d.curriculum_stage(8,12)=='integration'
    with pytest.raises(ValueError,match='TRAIN_PROMPT_INTEGRITY'):
        broken=copy.deepcopy(learn)
        broken[0]['row']['split']='dev'
        d.plan_batches(broken,12,8)


def test_identical_foundation_arm_updates_and_no_parent_mutation(monkeypatch):
    *_,parent,p45,p46,p47,p48,policy,cohort,evidence,contexts,scores,supervision,baseline=setup(monkeypatch)
    subset={d.d46._sha(r) for r in cohort['learn']}
    learn={s:evidence[s] for s in subset}
    counts={s:contexts[s] for s in subset}
    before={k:v.detach().clone() for k,v in parent.state_dict().items()}
    schedule,_=d.plan_batches(cohort['learn'],3,2)
    models=[]
    for arm in d.ARMS:
        net,info=d.train_arm(cohort['learn'],baseline,learn,counts,supervision,policy,
            arm=arm,steps=3,batch_size=2,eval_every=3,device='cpu',schedule=schedule)
        assert info['steps_completed']==3
        models.append(net)
    assert all(torch.equal(value,parent.state_dict()[k]) for k,value in before.items())
    assert all(model.binding.state_dict() for model in models)
    # Arms use same pretrained weights and the same foundation minibatch.
    # The learning methods produce distinct outcomes only after their phase begins.
    assert any(not torch.equal(models[0].binding.state_dict()[k],models[1].binding.state_dict()[k])
               for k in baseline)


def test_diagnostic_stage_writes_all_arms_without_mutation(monkeypatch,tmp_path):
    (records,fitting,auditing,parent,p45,p46,p47,p48,policy,cohort,
             evidence,contexts,rows,supervision,weights)=setup(monkeypatch)
    dest=tmp_path/'curriculum-0049'
    result=d.stage2(records,fitting,auditing,policy,parent,lambda:None,dest,
        p45,p46,p47,p48,rows,weights,{},steps=3,batch_size=2,eval_every=3,device='cpu')
    assert result['status']=='TRAIN_INTEGRATED_CURRICULUM_DIAGNOSTIC_COMPLETE'
    assert result['production_promotion'] is False
    assert result['g2_certified'] is False
    assert result['protocol']['no_training_on_transfer_or_audits'] is True
    assert set(result['results'])==set(d.ARMS)
    assert result['decision']['promotion_authorized'] is False
    assert result['train_policy']==d.EXPECTED_POLICY
    for arm in d.ARMS:
        for sec in d.SECTIONS:
            assert result['results'][arm][sec]['all']['cases']==len(cohort[sec])
            assert result['paired_vs_0048'][arm][sec]['fixed']>=0
    for name in (d.REPORT,d.LEDGER,d.WEIGHTS,d.POLICIES):
        assert (dest/name).is_file()
    with pytest.raises(FileExistsError,match='IMMUTABLE_OUTPUT'):
        d.stage2(records,fitting,auditing,policy,parent,lambda:None,dest,
            p45,p46,p47,p48,rows,weights,{},steps=3,batch_size=2,eval_every=3)


def test_parent_0048_integrity_and_metric_refusal(monkeypatch,tmp_path):
    (*_,p46,p47,p48,policy,cohort,evidence,contexts,rows,supervision,baseline)=setup(monkeypatch)
    folder=tmp_path/'parent'
    folder.mkdir()
    save_path=folder/d.d48.WEIGHTS
    from safetensors.torch import save_file
    save_file(baseline,str(save_path))
    (folder/d.d48.POLICIES).write_text(json.dumps(d.EXPECTED_POLICY))
    with gzip.open(folder/d.d48.LEDGER,'wt',encoding='utf-8') as out:
        for section in d.SECTIONS:
            for item in rows[section].values():
                out.write(json.dumps(item)+'\n')
    p48.update({'schema':d.d48.SCHEMA,'status':'TRAIN_OCCURRENCE_EVIDENCE_REPAIR_COMPLETE',
            'source_parent_schema':d.d47.SCHEMA,
            'parent_hashes':{'parent':'sha'},
            'train_dataset_sha256':p46['train_dataset_sha256'],
            'training_sha256':p46['training_sha256'],
            'protocol':{'steps':d.d48.STEPS,'batch_prompts':d.d48.BATCH_PROMPTS,
              'shared_trainable_qwen_window_encoder':True,
              'candidate_inventory_from_g2_0040_unchanged':True},
            'reference_frozen_bce':{
                 sec:p47['results']['arms']['frozen:bce'][
                       'train_learn' if sec=='learn' else sec] for sec in d.SECTIONS},
            'development_evaluation_skipped':True,'dev_labels_used_for_training':False,
            'dev_labels_used_for_calibration':False,'sealed_test_used':False,
            'production_promotion':False,'g2_certified':False,'frozen_g1':True,
            'frozen_g2_0033_parent':True,'train_policy':d.EXPECTED_POLICY})
    for field,file in (('ledger_sha256',d.d48.LEDGER),
                       ('weights_sha256',d.d48.WEIGHTS),
                       ('policies_sha256',d.d48.POLICIES)):
        p48[field]=d.backbone.sha256(folder/file)
    (folder/d.d48.REPORT).write_text(json.dumps(p48))
    verified,hashes,ledger=d.verify_parent48(folder,{'parent':'sha'},p46,p47)
    assert verified==p48
    assert sum(map(len,ledger.values()))==sum(len(cohort[s]) for s in d.SECTIONS)
    altered=copy.deepcopy(p48)
    altered['results']['transfer']['all']['exact']+=1
    (folder/d.d48.REPORT).write_text(json.dumps(altered))
    with pytest.raises(ValueError,match='PARENT48_METRIC_DRIFT'):
        d.verify_parent48(folder,{'parent':'sha'},p46,p47)
    (folder/d.d48.REPORT).write_text(json.dumps(p48))
    with gzip.open(folder/d.d48.LEDGER,'at',encoding='utf-8') as out:
        out.write(json.dumps(next(iter(rows['transfer'].values())))+'\n')
    with pytest.raises(ValueError,match='PARENT48_HASH'):
        d.verify_parent48(folder,{'parent':'sha'},p46,p47)
    (folder/d.d48.LEDGER).write_bytes(b'corrupt')
    with pytest.raises(ValueError,match='PARENT48_HASH'):
        d.verify_parent48(folder,{'parent':'sha'},p46,p47)


def test_refuses_held_out_supervision(monkeypatch):
    *_,p48,policy,cohort,evidence,contexts,rows,supervision,baseline=setup(monkeypatch)
    training=cohort['learn']
    train_keys={d.d46._sha(r) for r in training}
    assert set(supervision)==train_keys
    other=cohort['transfer'][0]
    with pytest.raises(ValueError,match='TRAIN_COHORT_LEAK'):
        d.train_arm(training,baseline,evidence,contexts,supervision,policy,
            arm='integrated',steps=3,batch_size=2,eval_every=3,device='cpu')
    fake=copy.deepcopy(training)
    fake[0]['row']['split']='dev'
    with pytest.raises(ValueError,match='TRAIN_COHORT_LEAK'):
        d.train_arm(fake,baseline,{k:evidence[k] for k in train_keys},
            {k:contexts[k] for k in train_keys},supervision,policy,
            arm='integrated',steps=3,batch_size=2,eval_every=3,device='cpu')


def test_cli_registration():
    proc=subprocess.run([sys.executable,'-m','tools.nanojev_gamer_v1_graph_curriculum',
        'frob-g2-integrated-curriculum','--help'],check=True,capture_output=True,text=True)
    assert '--occurrence48-run' in proc.stdout
    assert '--batch-prompts' in proc.stdout
    assert '--device' in proc.stdout
