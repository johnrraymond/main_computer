"""G2-0050 counterfactual authoring, actual training, holdout, and lineage gates."""
from __future__ import annotations

import copy
import gzip
import json
import subprocess
import sys

import pytest
import torch

from tools import nanojev_gamer_v1_g2_counterfactual_learning as d
from tests.test_nanojev_gamer_v1_g2_integrated_curriculum import setup


def test_author_balanced_executable_counterfactuals_and_heldout_name_pool():
    groups=d.author_pairs(8,4)
    train=groups['counterfactual_train']
    probe=groups['counterfactual_probe']
    assert len(train)==8 and len(probe)==4
    assert set((p['family'],p['mode']) for p in train)=={
        ('encounter','role_swap'),('allocation','role_swap'),
        ('encounter','renaming'),('allocation','renaming')}
    assert all(p['template'] in (0,1,2) for p in train)
    assert all(p['template']==3 for p in probe)
    assert all(p['proof']['role_crossed']==(p['mode']=='role_swap')
               for group in groups.values() for p in group)
    assert all(p['rows'][0]['graph']['edges']==p['rows'][1]['graph']['edges']
               for group in groups.values() for p in group)
    assert len({sha for group in groups.values() for p in group for sha in p['sha256']})==24
    assert [p['sha256'] for p in d.author_pairs(8,4)['counterfactual_train']]==[
        p['sha256'] for p in train]
    # Newly manipulated names are disjoint; fixed actor vocabulary is shared.
    train_pool=set(d.corpus.NAME_POOLS['train'][:len(d.corpus.NAME_POOLS['train'])*3//4])
    probe_pool=set(d.corpus.NAME_POOLS['train'][len(d.corpus.NAME_POOLS['train'])*3//4:])
    assert not train_pool & probe_pool


def test_pair_rejects_wrong_role_change_and_corrupted_semantics():
    p=d.author_pairs(4,4)['counterfactual_train'][0]
    a,b=p['rows']
    with pytest.raises(ValueError,match='RENAMING_CROSSES_ROLES'):
        d.verify_pair(a,b,'renaming','encounter')
    with pytest.raises(ValueError,match='PAIR_ORIGIN'):
        d.verify_pair(a,a,'role_swap','encounter')
    broken=copy.deepcopy(b)
    broken['expected_decisions']={'wrong':'outcome'}
    with pytest.raises(ValueError,match='PAIR_EXECUTION'):
        d.verify_pair(a,broken,'role_swap','encounter')
    with pytest.raises(ValueError,match='PAIR_BUDGET'):
        d.author_pairs(6,4)


def _candidate_record(row):
    from tools.nanojev_gamer_v1_g2_binding_bootstrap import gold_binding_edges
    gold=sorted(gold_binding_edges(row))
    nodes=[(n['span'][0],n['span'][1],n['kind'])
           for n in row['graph']['nodes'] if 'span' in n]
    wrong=(nodes[0],nodes[2],'refers_to')
    assert wrong not in gold
    return {'row':copy.deepcopy(row),'shortlisted':{'edges':[wrong,*gold]}}


def test_canonical_alignment_tracks_node_ids_across_different_text_spans():
    p=d.author_pairs(4,4)['counterfactual_train'][0]
    left,right=(_candidate_record(x) for x in p['rows'])
    idx_l,idx_r=d.aligned_edges(left,right)
    assert len(idx_l)>10
    assert len(idx_l)==len(idx_r)
    assert all((left['shortlisted']['edges'][i] in d.d46._gold(left))==
               (right['shortlisted']['edges'][j] in d.d46._gold(right))
               for i,j in zip(idx_l.tolist(),idx_r.tolist()))
    broken=copy.deepcopy(right)
    broken['shortlisted']['edges'][1]=(broken['shortlisted']['edges'][1][0],
        broken['shortlisted']['edges'][1][1],'owns' if
        broken['shortlisted']['edges'][1][2]!='owns' else 'refers_to')
    # A novel edge is simply absent from the intersection: it cannot create a
    # fabricated paired target or be used to supervise the other side.
    a,b=d.aligned_edges(left,broken)
    assert len(a)<=len(idx_l)


def test_captured_probe_targets_are_never_returned_as_training_labels(monkeypatch):
    groups=d.author_pairs(4,4)
    def capture(rows,tag):
        assert tag.startswith('g2_0050_')
        return [_candidate_record(row) for row in rows]
    monkeypatch.setattr(d.d48,'prepare_evidence',lambda rec,parent:
                        {'mock':torch.ones(1)})
    sets,accepted,evidence,labels,alignment,audit=d.prepare_paired_records(
                groups,capture,object(),set())
    assert len(sets['counterfactual_train'])==8
    assert len(sets['counterfactual_probe'])==8
    assert len(accepted['counterfactual_train'])==4
    assert len(alignment)==4
    assert all(p['candidate_reachable_both'] for p in audit['counterfactual_train'])
    probe={s for pair in groups['counterfactual_probe'] for s in pair['sha256']}
    assert not (probe&set(labels))
    assert {s for p in groups['counterfactual_train'] for s in p['sha256']}==set(labels)


def test_capture_refuses_existing_prompt_or_changed_graph(monkeypatch):
    groups=d.author_pairs(4,4)
    def capture(rows,tag):return [_candidate_record(row) for row in rows]
    monkeypatch.setattr(d.d48,'prepare_evidence',lambda rec,parent:
                        {'mock':torch.ones(1)})
    with pytest.raises(ValueError,match='CAPTURE_IDENTITY_OR_COHORT_LEAK'):
        d.prepare_paired_records(groups,capture,object(),
                                 {groups['counterfactual_train'][0]['sha256'][0]})
    def malicious(rows,tag):
        result=capture(rows,tag)
        result[0]['row']['prompt']='changed after capture'
        return result
    with pytest.raises(ValueError,match='CAPTURE_IDENTITY_OR_COHORT_LEAK'):
        d.prepare_paired_records(groups,malicious,object(),set())


def test_training_arms_have_matched_steps_and_do_not_edit_0048(monkeypatch):
    (*_,policy,cohort,evidence,contexts,rows,supervision,baseline)=setup(monkeypatch)
    learn=cohort['learn']
    original_ids={d.d46._sha(r) for r in learn}
    small={k:evidence[k] for k in original_ids}
    targets={k:(supervision[k][2],supervision[k][4]) for k in original_ids}
    exemplar=learn[0];anchor=d.d46._sha(exemplar)
    pair_rows=[];alignment={}
    for i in range(4):
        fake_ids=[]
        for side in ('left','right'):
            sha=d._sha('synthetic-pair-'+str(i)+'-'+side)
            fake_ids.append(sha)
            ex={key:value.clone() for key,value in small[anchor].items()}
            ex['windows']=ex['windows'].clone()
            ex['windows']+=i*.01+(0.001 if side=='right' else 0.)
            small[sha]=ex
            targets[sha]=targets[anchor]
        p={'id':str(i),'sha256':tuple(fake_ids),'mode':'role_swap',
           'family':'encounter'}
        pair_rows.append(p)
        alignment[p['id']]=(torch.tensor([0,2]),torch.tensor([0,2]))
    snapshot={k:v.clone() for k,v in baseline.items()}
    models,histories=d.train_models(learn,pair_rows,baseline,small,targets,alignment,
                        policy,steps=3,batch_size=4,eval_every=3,device='cpu')
    assert set(models)==set(d.ARMS)
    assert all(h[-1]['step']==3 for h in histories.values())
    assert all(torch.equal(baseline[k],snapshot[k]) for k in baseline)
    bad=dict(targets)
    bad.pop(next(iter(original_ids)))
    with pytest.raises(ValueError,match='TRAIN_LABEL_SCOPE'):
        d.train_models(learn,pair_rows,baseline,small,bad,alignment,policy,
                       steps=3,batch_size=4,eval_every=3)


def test_summary_counts_paired_exact_and_decision_no_promotion():
    p=d.author_pairs(4,4)['counterfactual_probe']
    rows=[{'prompt_sha256':sha,'exact':True} for x in p for sha in x['sha256']]
    counts=d.analyze_pairs(p,rows)
    assert counts['both_exact']==4 and counts['pairs']==4
    rows[0]['exact']=False
    counts=d.analyze_pairs(p,rows)
    assert counts['both_exact']==3 and counts['right_only']==1
    sample={'candidate_reachable':{'exact':9,'missing':20,'extra':22},
            'all':{'exact':29}}
    results={arm:{'transfer':copy.deepcopy(sample),
                  'natural_train_audit':copy.deepcopy(sample)} for arm in d.ARMS}
    p48={'results':{'natural_train_audit':{'all':{'exact':29}}}}
    paired={arm:{'both_exact':1} for arm in d.ARMS}
    decision=d.decision(results,paired,p48)
    assert decision['promotion_authorized'] is False
    assert decision['status']=='COUNTERFACTUAL_LEARNING_NOT_YET_TRANSFER_CONFIRMED'
    results['counterfactual_consistency']['transfer']['candidate_reachable']['exact']=14
    paired['counterfactual_consistency']['both_exact']=4
    assert d.decision(results,paired,p48)['status']=='STRUCTURAL_TRANSFER_SIGNAL'


def test_g2_cli_registered_and_frozen_callback_has_train_scope():
    proc=subprocess.run([sys.executable,'-m','tools.nanojev_gamer_v1_graph_curriculum',
         'frob-g2-counterfactual-learning','--help'],capture_output=True,text=True,check=True)
    assert '--integrated49-run' in proc.stdout
    assert '--train-pairs' in proc.stdout
    assert '--probe-pairs' in proc.stdout
    import inspect
    src=inspect.getsource(d.g40.run)
    assert 'capture_counterfactual_train_rows' in src
    assert "row.get('split')!='train'" in src
    assert 'assert_all_frozen()' in src


def test_0049_parent_accepts_authentic_ledger_shape_and_refuses_metric_drift(tmp_path):
    cohorts={d.d49._cohort_name(sec):['sha-'+sec] for sec in d.d49.SECTIONS}
    reference={'learn':{'all':{'exact':1}},'transfer':{'all':{'exact':1}},
               'train_fit':{'all':{'exact':1}},'natural_train_audit':{'all':{'exact':1}}}
    rows=[];results={}
    for arm in d.d49.ARMS:
        results[arm]={}
        for section in d.d49.SECTIONS:
            row={'arm':arm,'section':section,'prompt_sha256':'sha-'+section,
                 'family':'allocation','gold_count':2,'selected_count':2,
                 'exact':True,'missing':0,'extra':0,'inversions':0,
                 'missing_candidate_gold_edges':0,'candidate_reachable':True}
            rows.append(row)
            results[arm][section]=d.d46.summarize([row])
    with gzip.open(tmp_path/d.d49.LEDGER,'wt',encoding='utf-8') as stream:
        for row in rows:stream.write(json.dumps(row)+'\n')
    (tmp_path/d.d49.WEIGHTS).write_bytes(b'test-only weights')
    (tmp_path/d.d49.POLICIES).write_text(json.dumps(d.POLICY))
    hashes={label+'_sha256':d.backbone.sha256(tmp_path/filename)
            for label,filename in (('ledger',d.d49.LEDGER),('weights',d.d49.WEIGHTS),
                                   ('policies',d.d49.POLICIES))}
    report={'schema':d.d49.SCHEMA,
        'status':'TRAIN_INTEGRATED_CURRICULUM_DIAGNOSTIC_COMPLETE',
        'source_parent_schema':d.d48.SCHEMA,
        'parent_hashes':{'verified':'ancestor'},'cohorts':cohorts,
        'train_policy':d.POLICY,'development_evaluation_skipped':True,
        'dev_labels_used_for_training':False,'dev_labels_used_for_calibration':False,
        'sealed_test_used':False,'production_promotion':False,'g2_certified':False,
        'decision':{'decision':'NO_DECISIVE_CURRICULUM_TRANSFER_SIGNAL',
                    'promotion_authorized':False},
        'reference_0048':reference,'results':results,**hashes}
    (tmp_path/d.d49.REPORT).write_text(json.dumps(report))
    parent={'cohorts':cohorts,'results':reference}
    observed,receipts=d.verify_parent49(tmp_path,{'verified':'ancestor'},parent)
    assert observed['results']==results
    assert 'g2_0049_ledger_sha256' in receipts
    report['results']['replay_bce']['learn']['all']['exact']=0
    (tmp_path/d.d49.REPORT).write_text(json.dumps(report))
    with pytest.raises(ValueError,match='PARENT49_METRIC_DRIFT'):
        d.verify_parent49(tmp_path,{'verified':'ancestor'},parent)
