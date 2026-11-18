"""G2-0050: author-verified counterfactual learning using G2-0048 contextual evidence.

Every arm begins with the exact G2-0048 weights.  Freshly authored TRAIN
counterfactual *pairs*, never DEV or sealed TEST examples, go through frozen
Qwen/G1/G2 candidate construction.  We compare an equal-step original-only
control, added counterfactual supervision, and paired transformation consistency.
The held-out counterfactual probe is not used for updates or model selection.
Inference remains the unmodified 0048 scorer and 0033 decoder.
"""
from __future__ import annotations

from collections import Counter
import copy
import gzip
import hashlib
import json
import math
import random
import shutil
import tempfile
from pathlib import Path

import numpy as np
import torch
from torch.nn import functional as F
from safetensors.torch import load_file, save_file

from tools import nanojev_gamer_v1_g2_integrated_curriculum as d49
from tools import nanojev_gamer_v1_graph_corpus as corpus
from tools.nanojev_gamer_v1_graph_contract import validate_graph, execute_graph


d48=d49.d48
d47=d49.d47
d46=d49.d46
d44=d49.d44
g40=d49.g40
backbone=d49.backbone

SCHEMA='nanojev.gamer.graph.g2.counterfactual-learning.v1'
REPORT='g2_counterfactual_learning_report.json'
LEDGER='g2_counterfactual_learning_per_case.jsonl.gz'
WEIGHTS='g2_counterfactual_learning_development.safetensors'
POLICIES='g2_counterfactual_learning_frozen_policies.json'
STATUS='TRAIN_COUNTERFACTUAL_LEARNING_COMPLETE_NOT_CERTIFIED'
SEED=460550
STEPS=360
BATCH_PROMPTS=8
EVAL_EVERY=120
LR=5e-5
PAIR_WEIGHT=.25
TRAIN_PAIRS=48
PROBE_PAIRS=16
MAX_STEPS=1200
ARMS=('replay_bce','counterfactual_bce','counterfactual_consistency')
SECTIONS=('learn','transfer','train_fit','natural_train_audit','counterfactual_probe')
POLICY={'enabled':False,'config':None,'disabled_reason':'diagnostic_only_no_selection'}


def refuse(code):
    raise ValueError('GAMER_G2_0050_'+code)


def verify_parent49(directory,expected_hashes,p48):
    folder=Path(directory)
    report=json.loads((folder/d49.REPORT).read_text(encoding='utf-8'))
    for key,value in {
        'schema':d49.SCHEMA,'status':'TRAIN_INTEGRATED_CURRICULUM_DIAGNOSTIC_COMPLETE',
        'source_parent_schema':d48.SCHEMA,'parent_hashes':expected_hashes,
        'cohorts':p48['cohorts'],'train_policy':POLICY,
        'development_evaluation_skipped':True,'dev_labels_used_for_training':False,
        'dev_labels_used_for_calibration':False,'sealed_test_used':False,
        'production_promotion':False,'g2_certified':False,
    }.items():
        if report.get(key)!=value:refuse('PARENT49_CONTRACT:'+key)
    if report.get('decision',{}).get('decision')!='NO_DECISIVE_CURRICULUM_TRANSFER_SIGNAL' or \
       report.get('decision',{}).get('promotion_authorized') is not False or \
       report.get('reference_0048')!=p48['results']:
        refuse('PARENT49_NEGATIVE_RESULT_DRIFT')
    if json.loads((folder/d49.POLICIES).read_text(encoding='utf-8'))!=POLICY:
        refuse('PARENT49_POLICY_DRIFT')
    hashes={}
    for filename,label in ((d49.REPORT,'report'),(d49.LEDGER,'ledger'),
                           (d49.WEIGHTS,'weights'),(d49.POLICIES,'policies')):
        digest=backbone.sha256(folder/filename)
        hashes[f'g2_0049_{label}_sha256']=digest
        if label!='report' and report.get(label+'_sha256')!=digest:
            refuse('PARENT49_HASH:'+label)
    observed=set();ledgers={(a,s):[] for a in d49.ARMS for s in d49.SECTIONS}
    with gzip.open(folder/d49.LEDGER,'rt',encoding='utf-8') as stream:
        for raw in stream:
            item=json.loads(raw)
            sec=item.get('section');arm=item.get('arm');sha=item.get('prompt_sha256')
            if sec not in d49.SECTIONS or arm not in d49.ARMS or \
               sha not in p48['cohorts'][d49._cohort_name(sec)] or \
               (arm,sec,sha) in observed:
                refuse('PARENT49_LEDGER_DRIFT')
            if (item.get('exact') is not (item.get('missing')==item.get('extra')==0) or
                item.get('selected_count',-1)-item.get('gold_count',-1)!=
                    item.get('extra',-1)-item.get('missing',-1) or
                item.get('candidate_reachable') is not
                    (item.get('missing_candidate_gold_edges')==0)):
                refuse('PARENT49_LEDGER_ARITHMETIC')
            observed.add((arm,sec,sha))
            ledgers[arm,sec].append(item)
    if len(observed)!=len(d49.ARMS)*sum(len(p48['cohorts'][d49._cohort_name(s)])
                                          for s in d49.SECTIONS):
        refuse('PARENT49_LEDGER_MISSING')
    for (arm,sec),rows in ledgers.items():
        observed_metrics=d46.summarize(rows)
        expected=report.get('results',{}).get(arm,{}).get(sec,{})
        for group in ('all','candidate_reachable','candidate_unreachable'):
            for field in ('cases','exact','missing','extra','count_error_l1','rank_inversions'):
                if observed_metrics[group][field]!=expected.get(group,{}).get(field):
                    refuse('PARENT49_METRIC_DRIFT:'+arm+':'+sec+':'+group+':'+field)
    return report,hashes


def _sha(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def verify_pair(left,right,mode,family):
    """Verify both author-provided graphs and the *textual* role intervention."""
    if family not in ('encounter','allocation') or mode not in ('role_swap','renaming'):
        refuse('PAIR_CATEGORY')
    if any(row.get('split')!='train' or row.get('family')!=family or
           row.get('annotation_provenance')!='AUTHOR_MARKED_IN_SOURCE_GENERATOR'
           for row in (left,right)) or left['prompt']==right['prompt']:
        refuse('PAIR_ORIGIN')
    for row in (left,right):
        validate_graph(row['graph'],complete=True)
        if execute_graph(row['graph'])['decisions']!=row['expected_decisions']:
            refuse('PAIR_EXECUTION')
        for node in row['graph']['nodes']:
            if 'span' in node:
                start,end=node['span']
                if not (0<=start<end<=len(row['prompt'])):refuse('PAIR_NODE_SPAN')
    ln,rn=left['graph']['nodes'],right['graph']['nodes']
    if len(ln)!=len(rn) or any(
        (a['id'],a['kind'],a.get('operator'),a.get('value'))!=
        (b['id'],b['kind'],b.get('operator'),b.get('value')) for a,b in zip(ln,rn)) or \
        left['graph']['edges']!=right['graph']['edges'] or \
        left['graph']['objective']!=right['graph']['objective'] or \
        left['expected_decisions']!=right['expected_decisions']:
        refuse('PAIR_SEMANTIC_DRIFT')
    def node_texts(row,kind):
        return [row['prompt'][n['span'][0]:n['span'][1]]
                for n in row['graph']['nodes'] if n['kind']==kind and 'span' in n]
    la,ra=node_texts(left,'actor'),node_texts(right,'actor')
    lx,rx=node_texts(left,'action'),node_texts(right,'action')
    if not la or not ra or not lx or not rx or la==ra or lx==rx:
        refuse('PAIR_TEXT_DID_NOT_CHANGE')
    switched=bool(set(la)&set(rx) and set(lx)&set(ra))
    if mode=='role_swap' and not switched:refuse('ROLE_SWAP_NOT_OBSERVED')
    if mode=='renaming' and switched:refuse('RENAMING_CROSSES_ROLES')
    return {'family':family,'mode':mode,'role_crossed':switched,
            'graph_edge_count':len(left['graph']['edges']),
            'graph_node_count':len(ln)}


def author_pairs(train_pairs=TRAIN_PAIRS,probe_pairs=PROBE_PAIRS,*,forbidden=(),seed=SEED):
    """Precommit balanced author pairs; probe has disjoint new lexical inventory."""
    if (train_pairs<4 or probe_pairs<4 or train_pairs%4 or probe_pairs%4 or
        train_pairs>256 or probe_pairs>128):refuse('PAIR_BUDGET')
    vocab=tuple(corpus.NAME_POOLS['train'])
    train_vocab=vocab[:len(vocab)*3//4]
    probe_vocab=vocab[len(vocab)*3//4:]
    if len(train_vocab)<9 or len(probe_vocab)<9 or set(train_vocab)&set(probe_vocab):
        refuse('NAME_POOL_PARTITION')
    seen=set(forbidden)
    all_groups={}
    for section,count,pool,offset in (('counterfactual_train',train_pairs,train_vocab,0),
                                      ('counterfactual_probe',probe_pairs,probe_vocab,100000)):
        rng=random.Random(seed+offset)
        samples=[]
        for i in range(count):
            family=('encounter','allocation')[i%2]
            mode=('role_swap','renaming')[(i//2)%2]
            # Reserve a new *augmentation* template for probe; this is not a
            # claim that the template is absent from the older 0048 corpus.
            template_index=(i//4)%3 if section=='counterfactual_train' else 3
            for _ in range(150):
                left,right=corpus._counterfactual_pair(
                    rng,family,mode,template_index,name_pool=pool)
                hashes=(_sha(left['prompt']),_sha(right['prompt']))
                if hashes[0]!=hashes[1] and not (set(hashes)&seen):break
            else:refuse('AUTHOR_COLLISION')
            proof=verify_pair(left,right,mode,family)
            seen.update(hashes)
            pair_id=_sha(section+':'+str(i)+':'+':'.join(hashes))
            samples.append({'id':pair_id,'section':section,'family':family,'mode':mode,
                            'proof':proof,'rows':(left,right),'sha256':hashes,
                            'template':template_index})
        all_groups[section]=samples
    return all_groups


def evidence_gold(rec,parent):
    ex=d48.prepare_evidence(rec,parent)
    candidates=rec['shortlisted']['edges']
    gold=d46._gold(rec)
    labels=torch.tensor([float(e in gold) for e in candidates],dtype=torch.float32)
    if labels.sum().item()==0 or labels.sum().item()>=len(labels):
        return ex,None
    wt=min(4.,max(1.,math.sqrt((len(labels)-labels.sum().item())/
                               max(1.,labels.sum().item()))))
    return ex,(labels,torch.tensor(wt,dtype=torch.float32))


def _canonical_index(rec):
    """Map predicted candidate endpoints to mechanically authored node IDs."""
    nodes={((n['span'][0],n['span'][1],n['kind'])):n['id']
           for n in rec['row']['graph']['nodes'] if 'span' in n}
    index={}
    for i,(source,dest,relation) in enumerate(rec['shortlisted']['edges']):
        if source in nodes and dest in nodes:
            key=(nodes[source],nodes[dest],relation)
            if key in index:refuse('DUPLICATE_CANONICAL_EDGE')
            index[key]=i
    return index


def aligned_edges(left,right):
    la=_canonical_index(left);ra=_canonical_index(right)
    shared=sorted(set(la)&set(ra))
    if not shared:return torch.empty(0,dtype=torch.long),torch.empty(0,dtype=torch.long)
    labels_l=d46._gold(left);labels_r=d46._gold(right)
    for key in shared:
        l_edge=left['shortlisted']['edges'][la[key]]
        r_edge=right['shortlisted']['edges'][ra[key]]
        if (l_edge in labels_l)!=(r_edge in labels_r):
            refuse('PAIRED_CANDIDATE_LABEL_MISMATCH')
    return (torch.tensor([la[k] for k in shared],dtype=torch.long),
            torch.tensor([ra[k] for k in shared],dtype=torch.long))


def prepare_paired_records(groups,capture,parent,existing_hashes):
    sets={};records={};evidence={};labels={};alignment={};audit={}
    for section,pairs in groups.items():
        rows=[row for pair in pairs for row in pair['rows']]
        captured=capture(rows,'g2_0050_'+section)
        if len(captured)!=len(rows):refuse('COUNTERFACTUAL_CAPTURE_COUNT')
        by_sha={}
        for expected,rec in zip(rows,captured):
            sha=_sha(expected['prompt'])
            if rec['row']['prompt']!=expected['prompt'] or rec['row']['graph']!=expected['graph'] or \
               rec['row']['split']!='train' or sha in by_sha or sha in existing_hashes:
                refuse('CAPTURE_IDENTITY_OR_COHORT_LEAK')
            if section=='counterfactual_train':
                ex,target=evidence_gold(rec,parent)
            else:
                # Probe labels are not decoded into a training target until
                # the frozen model has made its predictions after training.
                ex,target=d48.prepare_evidence(rec,parent),None
            by_sha[sha]=rec
            evidence[sha]=ex
            if target is not None:labels[sha]=target
        accepted=[];details=[]
        for pair in pairs:
            a,b=pair['sha256']
            l,r=by_sha[a],by_sha[b]
            if section=='counterfactual_train':
                ia,ib=aligned_edges(l,r)
                positive=int(sum(l['shortlisted']['edges'][j] in d46._gold(l)
                                 for j in ia.tolist()))
                valid=(a in labels and b in labels and len(ia)>=2 and positive>=1)
                left_missing=d46._candidate_gap(l)
                right_missing=d46._candidate_gap(r)
                reachable=left_missing==0 and right_missing==0
                shared_count=len(ia)
            else:
                # Do not inspect the probe's target edges, not even for
                # candidate-coverage reporting, until training has finished.
                ia=ib=torch.empty(0,dtype=torch.long)
                positive=shared_count=left_missing=right_missing=reachable=None
                valid=False
            details.append({'pair_sha256':pair['id'],'mode':pair['mode'],
                            'family':pair['family'],'accepted_for_learning':bool(valid),
                            'shared_candidates':shared_count,'shared_positive':positive,
                            'candidate_reachable_both':reachable,
                            'left_missing_candidates':left_missing,
                            'right_missing_candidates':right_missing})
            if section=='counterfactual_train' and valid:
                accepted.append(pair)
                alignment[pair['id']]=(ia,ib)
        sets[section]=[by_sha[_sha(row['prompt'])] for row in rows]
        records[section]=accepted
        audit[section]=details
    by_cell=Counter((p['family'],p['mode']) for p in records['counterfactual_train'])
    if any(by_cell[(f,m)]==0 for f in ('encounter','allocation')
           for m in ('role_swap','renaming')):
        refuse('NO_VIABLE_PAIRED_TRAINING_FOR_EACH_CELL')
    return sets,records,evidence,labels,alignment,audit


def fixed_schedules(n_original,n_pairs,steps,batch_size):
    if not n_original or not n_pairs or not 2<=batch_size<=32 or batch_size%2 or \
       not 1<=steps<=MAX_STEPS:refuse('BATCH_CONTRACT')
    original=d46.fixed_prompt_batches(n_original,steps,batch_size,SEED)
    replay=d46.fixed_prompt_batches(n_original,steps,batch_size//2,SEED+1)
    pairs=d46.fixed_prompt_batches(n_pairs,steps,batch_size//4,SEED+2)
    return original,replay,pairs


def train_models(learn,pairs,parent_state,evidence,supervision,alignment,policy,*,
                 steps=STEPS,batch_size=BATCH_PROMPTS,eval_every=EVAL_EVERY,device='cpu'):
    if batch_size%4 or not 4<=batch_size<=32 or not 1<=steps<=MAX_STEPS or \
       not 1<=eval_every<=steps or not learn or not pairs:refuse('TRAIN_BUDGET')
    if any(r['row'].get('split')!='train' for r in learn):refuse('NONTRAIN_SUPERVISION')
    learn_keys={d46._sha(rec) for rec in learn}
    paired_keys={k for pair in pairs for k in pair['sha256']}
    if learn_keys & paired_keys or not paired_keys.issubset(supervision) or \
       not learn_keys.issubset(supervision) or \
       not (paired_keys|learn_keys).issubset(evidence):refuse('TRAIN_LABEL_SCOPE')
    original,replay,pair_schedule=fixed_schedules(len(learn),len(pairs),steps,batch_size)
    width=next(iter(evidence.values()))['windows'].shape[-1]
    models={};histories={}
    for arm in ARMS:
        torch.manual_seed(SEED)
        model=d48.OccurrenceEvidenceScorer(width).to(device)
        model.load_state_dict(parent_state,strict=True)
        optim=torch.optim.AdamW(model.parameters(),lr=LR,weight_decay=1e-4)
        history=[]
        for step in range(1,steps+1):
            model.train();optim.zero_grad(set_to_none=True)
            orig=original[step-1][:batch_size//2]
            if arm=='replay_bce':
                training=[d46._sha(learn[i]) for i in orig+replay[step-1]]
                paired_batch=[]
            else:
                paired_batch=[pairs[i] for i in pair_schedule[step-1]]
                training=[d46._sha(learn[i]) for i in orig]+[
                    sha for pair in paired_batch for sha in pair['sha256']]
            if len(training)!=batch_size:refuse('BATCH_SIZE_DRIFT')
            cache_logits={};losses=[]
            for sha in training:
                scores=model(d48._on_device(evidence[sha],device))
                cache_logits[sha]=scores
                target,weight=supervision[sha]
                losses.append(F.binary_cross_entropy_with_logits(
                    scores,target.to(device),pos_weight=weight.to(device)))
            loss=torch.stack(losses).mean()
            if arm=='counterfactual_consistency':
                pair_losses=[]
                for pair in paired_batch:
                    a,b=pair['sha256'];ia,ib=alignment[pair['id']]
                    pair_losses.append(F.smooth_l1_loss(
                        cache_logits[a][ia.to(device)],cache_logits[b][ib.to(device)]))
                loss=loss+PAIR_WEIGHT*torch.stack(pair_losses).mean()
            if not torch.isfinite(loss):refuse('LOSS_NONFINITE')
            loss.backward()
            grad=torch.nn.utils.clip_grad_norm_(model.parameters(),5.)
            if not torch.isfinite(grad) or any(p.grad is not None and not torch.isfinite(p.grad).all()
                                                    for p in model.parameters()):
                refuse('GRAD_NONFINITE')
            optim.step()
            if step==1 or step%eval_every==0 or step==steps:
                train_metrics=d46.summarize(d48.score_records(learn,model,policy,'learn',
                                         step,evidence,device))
                history.append({'step':step,'loss':float(loss.detach()),
                                'learn_exact':train_metrics['all']['exact']})
                print(f'GAMER_G2_0050_FIT {arm} {step}/{steps} '
                      f'exact={train_metrics["all"]["exact"]}/{len(learn)} '
                      f'loss={float(loss.detach()):.6f}',flush=True)
        model.eval().requires_grad_(False)
        models[arm]=model
        histories[arm]=history
    return models,histories


def analyze_pairs(pairs,rows):
    index={r['prompt_sha256']:r for r in rows}
    both=left_only=right_only=none=0
    by_cell={}
    for pair in pairs:
        a,b=pair['sha256'];l=index[a]['exact'];r=index[b]['exact']
        both+=bool(l and r);left_only+=bool(l and not r)
        right_only+=bool(r and not l);none+=bool(not l and not r)
        key=pair['family']+':'+pair['mode']
        cell=by_cell.setdefault(key,{'pairs':0,'both_exact':0})
        cell['pairs']+=1;cell['both_exact']+=bool(l and r)
    return {'pairs':len(pairs),'both_exact':both,'left_only':left_only,
            'right_only':right_only,'neither_exact':none,'by_cell':by_cell}


def decision(results,paired,parent48):
    control=results['replay_bce']['transfer']['candidate_reachable']
    candidate=results['counterfactual_consistency']['transfer']['candidate_reachable']
    bce=results['counterfactual_bce']['transfer']['candidate_reachable']
    audit_ref=parent48['results']['natural_train_audit']['all']['exact']
    cf_gain=paired['counterfactual_consistency']['both_exact']-paired['replay_bce']['both_exact']
    gain=candidate['exact']-control['exact']
    edge_no_worse=(candidate['missing']+candidate['extra']<=
                   control['missing']+control['extra'])
    audit_retention=(results['counterfactual_consistency']['natural_train_audit']['all']['exact']
                     >=audit_ref-2)
    signal=(gain>=4 and cf_gain>=2 and edge_no_worse and audit_retention)
    return {'status':('STRUCTURAL_TRANSFER_SIGNAL' if signal else
                       'COUNTERFACTUAL_LEARNING_NOT_YET_TRANSFER_CONFIRMED'),
            'original_transfer_control_exact':control['exact'],
            'counterfactual_bce_transfer_exact':bce['exact'],
            'paired_training_transfer_exact':candidate['exact'],
            'paired_gain_over_matched_control':gain,
            'heldout_pair_both_exact_gain':cf_gain,
            'no_additional_transfer_edge_errors':edge_no_worse,
            'natural_audit_retention':audit_retention,
            'minimum_graph_gain':4,'minimum_heldout_pair_gain':2,
            'promotion_authorized':False}


def stage2(training,fitting,auditing,policy,parent,assert_frozen,output,
           parent45,p46,p47,p48,p49,rows48,baseline_state,parent_hashes,capture,
           *,steps=STEPS,batch_size=BATCH_PROMPTS,eval_every=EVAL_EVERY,
           train_pairs=TRAIN_PAIRS,probe_pairs=PROBE_PAIRS,device='cpu'):
    folder=Path(output)
    if folder.exists():raise FileExistsError('GAMER_G2_0050_IMMUTABLE_OUTPUT')
    cohort=d46.select_cohorts(training,fitting,auditing,parent45,policy)
    d47.verify_cohorts(cohort,p46)
    if p48['cohorts']!=p46['cohorts'] or p49['cohorts']!=p46['cohorts']:
        refuse('COHORT_DRIFT')
    sets={s:cohort[s] for s in d49.SECTIONS}
    original={};supervision={}
    orig_input=d46.prepare_supervision(sets['learn'],parent)
    for section,records in sets.items():
        for rec in records:
            sha=d46._sha(rec)
            if sha in original or sha not in rows48[section]:refuse('ORIGINAL_COHORT_DRIFT')
            original[sha]=d48.prepare_evidence(rec,parent)
            if sha in orig_input:
                supervision[sha]=(orig_input[sha][2],orig_input[sha][4])
    authored=author_pairs(train_pairs,probe_pairs,forbidden=set(original))
    cf_sets,accepted,cf_evidence,cf_labels,alignments,cf_audit=prepare_paired_records(
                    authored,capture,parent,set(original))
    evidence={**original,**cf_evidence}
    supervision.update(cf_labels)
    train_models_result,histories=train_models(sets['learn'],accepted['counterfactual_train'],
                   baseline_state,evidence,supervision,alignments,policy,
                   steps=steps,batch_size=batch_size,eval_every=eval_every,device=device)
    # Now that every optimizer has finished, reveal probe gold strictly for
    # reporting; no code path returns to fitting or adjusts the arm choice.
    probe_by_sha={d46._sha(rec):rec for rec in cf_sets['counterfactual_probe']}
    for pair,entry in zip(authored['counterfactual_probe'],
                          cf_audit['counterfactual_probe']):
        a,b=pair['sha256']
        left,right=probe_by_sha[a],probe_by_sha[b]
        ia,ib=aligned_edges(left,right)
        left_missing=d46._candidate_gap(left)
        right_missing=d46._candidate_gap(right)
        entry.update({'shared_candidates':len(ia),
            'shared_positive':sum(left['shortlisted']['edges'][j] in d46._gold(left)
                                    for j in ia.tolist()),
            'candidate_reachable_both':left_missing==0 and right_missing==0,
            'left_missing_candidates':left_missing,
            'right_missing_candidates':right_missing})
    # Exactly evaluate the original 0048 checkpoint on the same fixed cohorts.
    reference=d48.OccurrenceEvidenceScorer(
                next(iter(original.values()))['windows'].shape[-1]).to(device)
    reference.load_state_dict(baseline_state,strict=True)
    reference.eval().requires_grad_(False)
    for section,records in sets.items():
        actual=d46.summarize(d48.score_records(records,reference,policy,section,0,
                                                       original,device))
        for group in ('all','candidate_reachable'):
            for field in ('cases','exact','missing','extra'):
                if actual[group][field]!=p48['results'][section][group][field]:
                    refuse('BASELINE_REPRODUCTION:'+section+':'+group+':'+field)
    results={};ledgers=[];paired={}
    for arm,model in train_models_result.items():
        results[arm]={}
        for section,records in {**sets,'counterfactual_probe':cf_sets['counterfactual_probe']}.items():
            rows=d48.score_records(records,model,policy,section,steps,evidence,device)
            for row in rows:row['arm']=arm
            results[arm][section]=d46.summarize(rows)
            ledgers.extend(rows)
            if section=='counterfactual_probe':
                paired[arm]=analyze_pairs(authored['counterfactual_probe'],rows)
    assert_frozen()
    final_decision=decision(results,paired,p48)
    report={'schema':SCHEMA,'status':STATUS,'source_parent_schema':d48.SCHEMA,
        'parent_hashes':parent_hashes,'cohorts':p46['cohorts'],
        'train_dataset_sha256':p46['train_dataset_sha256'],
        'training_sha256':p46['training_sha256'],
        'training':{
            'author_pairs_requested':train_pairs,'probe_pairs_requested':probe_pairs,
            'author_train_pair_count':len(authored['counterfactual_train']),
            'train_pairs_eligible':len(accepted['counterfactual_train']),
            'train_pairs_by_cell':{f+':'+m:sum(p['family']==f and p['mode']==m
                for p in accepted['counterfactual_train']) for f in ('encounter','allocation')
                for m in ('role_swap','renaming')},
            'counterfactual_probe_all_pairs_evaluated':len(authored['counterfactual_probe']),
            'cf_capture_rows':sum(len(v) for v in cf_sets.values()),
            'candidate_reachability':{sec:{'both':sum(x['candidate_reachable_both']
                for x in rows),'pairs':len(rows)} for sec,rows in cf_audit.items()},
            'augmentation_template_held_out':3,
            'manipulated_name_pools_disjoint':True,
            'fixed_actor_vocabulary_may_overlap':True,
        },
        'protocol':{'steps_per_arm':steps,'batch_prompts':batch_size,'eval_every':eval_every,
                    'seed':SEED,'optimizer':'AdamW','learning_rate':LR,
                    'arms':list(ARMS),'consistency_weight':PAIR_WEIGHT,
                    'initial_weights':'immutable_g2_0048',
                    'frozen_qwen_g1_g2_0033_parent_and_decoder':True,
                    'author_generated_executable_graphs_verified':True,
                    'source_destination_labels_not_derived_from_model_predictions':True,
                    'paired_loss_uses_author_node_id_correspondence':True,
                    'heldout_counterfactual_probe_never_trained_or_used_for_selection':True,
                    'all_existing_transfer_and_audit_labels_evaluation_only':True,
                    'train_only_ground_truth':True,'immutable_output':True,
                    'counterfactual_evaluation_is_train_domain_not_sealed_test':True},
        'fit':histories,'results':results,'heldout_pair_results':paired,
        'counterfactual_capture_audit':cf_audit,'decision':final_decision,
        'development_evaluation_skipped':True,'dev_labels_used_for_training':False,
        'dev_labels_used_for_calibration':False,'sealed_test_used':False,
        'production_promotion':False,'g2_certified':False,'frozen_g1':True,
        'frozen_g2_0033_parent':True,'train_policy':POLICY}
    folder.parent.mkdir(parents=True,exist_ok=True)
    temp=Path(tempfile.mkdtemp(prefix=folder.name+'.staging-',dir=folder.parent))
    try:
        with gzip.open(temp/LEDGER,'wt',encoding='utf-8',compresslevel=6) as stream:
            for item in ledgers:stream.write(json.dumps(item,sort_keys=True,allow_nan=False)+'\n')
        save_file({arm+'.'+k:v.detach().cpu().contiguous().clone()
                   for arm,model in train_models_result.items()
                   for k,v in model.state_dict().items()},str(temp/WEIGHTS))
        (temp/POLICIES).write_text(json.dumps(POLICY,indent=2,sort_keys=True)+'\n',encoding='utf-8')
        report.update({'ledger_sha256':backbone.sha256(temp/LEDGER),
                       'weights_sha256':backbone.sha256(temp/WEIGHTS),
                       'policies_sha256':backbone.sha256(temp/POLICIES)})
        (temp/REPORT).write_text(json.dumps(report,indent=2,sort_keys=True,
                                      allow_nan=False)+'\n',encoding='utf-8')
        if folder.exists():raise FileExistsError('GAMER_G2_0050_OUTPUT_RACE')
        temp.rename(folder)
    finally:
        if temp.exists():shutil.rmtree(temp)
    return report


def run(*,bakeoff40_run,oracle41_run,rank42_run,absolute43_run,
        memorization44_run,separability45_run,multiprompt46_run,crossover47_run,
        occurrence48_run,integrated49_run,output,steps=STEPS,batch_size=BATCH_PROMPTS,
        eval_every=EVAL_EVERY,train_pairs=TRAIN_PAIRS,probe_pairs=PROBE_PAIRS,
        device='cuda',**kwargs):
    p40,h40=d49.g41.verify_parent40(bakeoff40_run)
    p41,_,h41=d49.g42.verify_parent41(oracle41_run,h40)
    p42,h42=d49.b43.verify_parent42(rank42_run,h40,h41)
    p43,h43=d48.d45.d44.verify_parent43(absolute43_run,h40,h41,h42)
    p44,h44=d48.d45.verify_parent44(memorization44_run,h40,h41,h42,h43)
    p45,h45=d46.verify_parent45(separability45_run,{**h40,**h41,**h42,**h43,**h44})
    p46,h46=d47.verify_parent46(multiprompt46_run,{**h40,**h41,**h42,**h43,**h44,**h45})
    p47,h47=d48.verify_parent47(crossover47_run,
             {**h40,**h41,**h42,**h43,**h44,**h45,**h46},p46)
    p48,h48,rows48=d49.verify_parent48(occurrence48_run,
             {**h40,**h41,**h42,**h43,**h44,**h45,**h46,**h47},p46,p47)
    p49,h49=verify_parent49(integrated49_run,
             {**h40,**h41,**h42,**h43,**h44,**h45,**h46,**h47,**h48},p48)
    if Path(output).exists():raise FileExistsError('GAMER_G2_0050_IMMUTABLE_OUTPUT')
    weights=load_file(str(Path(occurrence48_run)/d48.WEIGHTS),device='cpu')
    def callback(*,training,fitting,auditing,train_rows,fit_rows,audit_rows,
                 pol33,parent33_models,assert_all_frozen,
                 capture_counterfactual_train_rows=None,**ignored):
        if capture_counterfactual_train_rows is None:refuse('CAPTURE_HOOK_UNAVAILABLE')
        for name,rows in (('train_0040',train_rows),('train_fit_0040',fit_rows),
                          ('train_audit_0040',audit_rows)):
            if p46['training_sha256'][name]!=g40.sha_rows(rows):
                refuse('TRAIN_COHORT_DRIFT:'+name)
        policy=pol33[f'structured_set_s{g40.SOURCE_SEED}']
        if policy!=p41['protocol']['config']:refuse('DECODER_POLICY_DRIFT')
        return stage2(training,fitting,auditing,policy,
             parent33_models[g40.SOURCE_SEED],assert_all_frozen,output,
             p45,p46,p47,p48,p49,rows48,weights,
             {**h40,**h41,**h42,**h43,**h44,**h45,**h46,**h47,**h48,**h49},
             capture_counterfactual_train_rows,steps=steps,batch_size=batch_size,
             eval_every=eval_every,train_pairs=train_pairs,probe_pairs=probe_pairs,
             device=device)
    return g40.run(output=output,diagnostic_callback=callback,device=device,**kwargs)
