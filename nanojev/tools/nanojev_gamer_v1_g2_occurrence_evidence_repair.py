"""G2-0048: restore original, label-blind occurrence evidence to graph selection.

The G2-0033/0047 scorer sees only 15 scalar score/rank features per edge.
This private correction feeds actual Qwen token windows, frozen predicted node
occurrences, ordered endpoints and relation IDs alongside the same frozen
parent graph score, with the SAME candidate inventory/count heads/decoder.
TRAIN-only learning; no DEV/TEST, production route, or policy selection.
"""
from __future__ import annotations

import gzip
import json
import math
import shutil
import tempfile
from pathlib import Path

import numpy as np
import torch
from torch import nn
from torch.nn import functional as F
from safetensors.torch import save_file

from tools import nanojev_gamer_v1_g2_architectural_crossover as d47


d46=d47.d46
d45=d47.d45
d44=d47.d44
x33=d47.x33
g40=d47.g40
g41=d47.g41
g42=d47.g42
b43=d47.b43
backbone=d47.backbone
x31=g40.x31
source13=x31.source13
from tools.nanojev_gamer_v1_graph_head import BIND_CLASSES
from tools.nanojev_gamer_v1_graph_contract import SPAN_KINDS

SCHEMA='nanojev.gamer.graph.g2.occurrence-evidence-repair.v1'
REPORT='g2_occurrence_evidence_repair_report.json'
LEDGER='g2_occurrence_evidence_repair_per_case.jsonl.gz'
WEIGHTS='g2_occurrence_evidence_repair_development.safetensors'
POLICIES='g2_occurrence_evidence_repair_frozen_policies.json'
STEPS=d46.STEPS
BATCH_PROMPTS=d46.BATCH_PROMPTS
EVAL_EVERY=d46.EVAL_EVERY
MAX_STEPS=d46.MAX_STEPS
SEED=460548
LR=3e-4
ENCODER_WIDTH=96


def verify_parent47(directory, expected_hashes, parent46):
    """Verify the completed private crossover and its immutable outputs."""
    folder=Path(directory)
    report=json.loads((folder/d47.REPORT).read_text(encoding='utf-8'))
    expected={
        'schema':d47.SCHEMA,
        'status':'TRAIN_ARCHITECTURAL_CROSSOVER_COMPLETE',
        'source_parent_schema':d46.SCHEMA,
        'parent_hashes':expected_hashes,
        'cohorts':parent46['cohorts'],
        'development_evaluation_skipped':True,
        'dev_labels_used_for_training':False,
        'dev_labels_used_for_calibration':False,
        'sealed_test_used':False,
        'production_promotion':False,
        'g2_certified':False,
        'frozen_g1':True,
        'frozen_g2_0033_parent':True,
        'train_policy':{'enabled':False,'config':None,
                        'disabled_reason':'diagnostic_only_no_selection'},
    }
    for name,value in expected.items():
        if report.get(name)!=value:
            raise ValueError('GAMER_G2_0048_PARENT_0047_CONTRACT:'+name)
    protocol=report.get('protocol',{})
    if not protocol.get('reference_budget_reproduced') or \
       protocol.get('steps')!=STEPS or protocol.get('batch_prompts')!=BATCH_PROMPTS or \
       protocol.get('representations')!=list(d47.REPRESENTATIONS) or \
       protocol.get('objectives')!=list(d47.OBJECTIVES):
        raise ValueError('GAMER_G2_0048_PARENT_0047_BUDGET')
    if set(report.get('results',{}).get('arms',{}))!={
        f'{representation}:{objective}' for representation in d47.REPRESENTATIONS
        for objective in d47.OBJECTIVES}:
        raise ValueError('GAMER_G2_0048_PARENT_0047_ARMS')
    frozen=report['results']['arms']['frozen:bce']
    previous=parent46['results']['arms']['nonlinear:bce']
    for section in ('train_learn','transfer','train_fit','natural_train_audit'):
        first=frozen.get(section,{})
        second=previous.get('learn' if section=='train_learn' else section,{})
        for part in ('all','candidate_reachable','candidate_unreachable'):
            for field in ('cases','exact','missing','extra','rank_inversions'):
                if first.get(part,{}).get(field)!=second.get(part,{}).get(field):
                    raise ValueError('GAMER_G2_0048_PARENT_0047_REPRODUCTION:'+section+':'+part+':'+field)
    if json.loads((folder/d47.POLICIES).read_text(encoding='utf-8'))!=report['train_policy']:
        raise ValueError('GAMER_G2_0048_PARENT_0047_POLICY')
    hashes={}
    for file,name in ((d47.REPORT,'report'),(d47.LEDGER,'ledger'),
                      (d47.WEIGHTS,'weights'),(d47.POLICIES,'policies')):
        digest=backbone.sha256(folder/file)
        hashes[f'g2_0047_{name}_sha256']=digest
        if name!='report' and digest!=report.get(name+'_sha256'):
            raise ValueError('GAMER_G2_0048_PARENT_0047_HASH:'+file)
    if report.get('decision',{}).get('decision') != (
       'NO_DECISIVE_TRANSFER_GAIN_REEXAMINE_INPUT_EVIDENCE_AND_GRAPH_OBJECTIVE'):
        raise ValueError('GAMER_G2_0048_PARENT_0047_DECISION')
    expected_sections={'train_learn','transfer','train_fit','natural_train_audit'}
    expected_arms=set(report['results']['arms'])
    observed=set()
    with gzip.open(folder/d47.LEDGER,'rt',encoding='utf-8') as stream:
        for raw in stream:
            item=json.loads(raw)
            section=item.get('section');arm=item.get('arm')
            if section not in expected_sections or arm not in expected_arms:
                raise ValueError('GAMER_G2_0048_PARENT_0047_LEDGER')
            sh=item.get('prompt_sha256')
            if sh not in report['cohorts'][section+'_prompts']:
                raise ValueError('GAMER_G2_0048_PARENT_0047_LEDGER_PROMPT')
            identity=(section,arm,sh)
            if identity in observed:
                raise ValueError('GAMER_G2_0048_PARENT_0047_LEDGER_DUPLICATE')
            observed.add(identity)
    for section in expected_sections:
        for arm in expected_arms:
            if any((section,arm,sh) not in observed
                   for sh in report['cohorts'][section+'_prompts']):
                raise ValueError('GAMER_G2_0048_PARENT_0047_LEDGER_COUNT')
    return report, hashes


def prepare_evidence(rec,parent):
    """Restore information *already computed* by frozen Qwen/G1/G2.

    Only predicted anchors, frozen contextual windows and label-blind candidate
    edges are used. The row's prompt and gold graph are NEVER read or returned.
    The numerical G2-0033 summary is retained as a frozen scoring prior.
    """
    if rec['row'].get('split')!='train':
        raise ValueError('GAMER_G2_0048_NONTRAIN_EVIDENCE')
    occurrence=rec.get('recovered')
    if not isinstance(occurrence,dict):
        raise ValueError('GAMER_G2_0048_MISSING_UPSTREAM_OCCURRENCES')
    anchors=occurrence.get('anchors')
    if not isinstance(anchors,list) or not 1<=len(anchors)<=128 or len(set(anchors))!=len(anchors):
        raise ValueError('GAMER_G2_0048_ANCHOR_IDENTITY')
    windows=torch.as_tensor(occurrence['windows']).detach().cpu()
    mask=torch.as_tensor(occurrence['window_mask']).detach().cpu().bool()
    if windows.ndim!=3 or windows.shape[0]!=len(anchors) or windows.shape[1]!=5 or \
       mask.shape!=(len(anchors),5) or not bool(mask[:,2].all()) or \
       not torch.isfinite(windows.float()).all():
        raise ValueError('GAMER_G2_0048_UPSTREAM_WINDOW_ALIGNMENT')
    index={anchor:i for i,anchor in enumerate(anchors)}
    rel_lookup={name:i for i,name in enumerate(BIND_CLASSES)}
    kind_lookup={name:i+1 for i,name in enumerate(SPAN_KINDS)}
    edges=rec['shortlisted']['edges']
    src=[];dst=[];relations=[];src_kinds=[];dst_kinds=[];geometry=[]
    if not edges or not all(isinstance(edge,tuple) and len(edge)==3 for edge in edges):
        raise ValueError('GAMER_G2_0048_INVALID_CANDIDATE_EDGES')
    for source,target,relation in edges:
        if source not in index or target not in index or relation not in rel_lookup or \
           rel_lookup[relation]==0 or source[2] not in kind_lookup or target[2] not in kind_lookup:
            raise ValueError('GAMER_G2_0048_CANDIDATE_ENDPOINT_UNREACHABLE')
        src.append(index[source]);dst.append(index[target]);relations.append(rel_lookup[relation])
        src_kinds.append(kind_lookup[source[2]]);dst_kinds.append(kind_lookup[target[2]])
        source_start,source_end=source[:2];target_start,target_end=target[:2]
        geometry.append([math.tanh((target_start-source_start)/128.),
                         math.tanh((target_end-source_end)/128.),
                         math.tanh((source_end-source_start)/32.),
                         math.tanh((target_end-target_start)/32.)])
    group_ids=np.asarray(rec['features']['index'],dtype=np.int64)
    if len(group_ids)!=len(edges):
        raise ValueError('GAMER_G2_0048_GROUP_ALIGNMENT')
    group_sources={}
    for i,g in enumerate(group_ids):
        identity=(src[i],relations[i])
        if int(g) in group_sources and group_sources[int(g)]!=identity:
            raise ValueError('GAMER_G2_0048_GROUP_ENDPOINT_CONFLICT')
        group_sources[int(g)]=identity
    h,prior,base_score=d45.extract_frozen(rec,parent)
    if len(h)!=len(edges) or len(base_score)!=len(edges):
        raise ValueError('GAMER_G2_0048_SUMMARY_ALIGNMENT')
    return {
       'windows':windows.contiguous(), 'mask':mask.contiguous(),
       'source':torch.tensor(src,dtype=torch.long),
       'destination':torch.tensor(dst,dtype=torch.long),
       'relation':torch.tensor(relations,dtype=torch.long),
       'source_kind':torch.tensor(src_kinds,dtype=torch.long),
       'destination_kind':torch.tensor(dst_kinds,dtype=torch.long),
       'geometry':torch.tensor(geometry,dtype=torch.float32),
       'summary':h.detach().clone(),
       'parent_score':base_score.detach().clone(),
    }


class OccurrenceEvidenceScorer(nn.Module):
    """Trainable relation and occurrence-conditioned correction to frozen scores.

    Shared token-window encoding is reused for every edge; explicit source,
    destination and relation types survive to the score. No gold at inference.
    """
    def __init__(self,input_width,hidden=ENCODER_WIDTH):
        super().__init__()
        self.input_width=input_width
        self.encoder=x31.TokenOccurrenceEncoder(input_width,hidden=hidden)
        self.relations=nn.Embedding(len(BIND_CLASSES),16)
        self.kinds=nn.Embedding(len(SPAN_KINDS)+1,8)
        size=x33.HIDDEN+hidden*4+16+16+4
        self.scorer=nn.Sequential(nn.LayerNorm(size),nn.Linear(size,192),nn.GELU(),
                                  nn.Linear(192,1))
        # Exactly the frozen parent at initialization. Learning begins only in
        # the private head, while its encoder receives gradients thereafter.
        nn.init.zeros_(self.scorer[-1].weight)
        nn.init.zeros_(self.scorer[-1].bias)

    def forward(self,example):
        if example['windows'].shape[-1]!=self.input_width:
            raise ValueError('GAMER_G2_0048_CONTEXT_WIDTH_CHANGED')
        n=example['windows'].shape[0]
        idx_a=example['source'];idx_b=example['destination']
        if bool((idx_a>=n).any()) or bool((idx_b>=n).any()):
            raise ValueError('GAMER_G2_0048_CONTEXT_INDEX')
        node,_=self.encoder(example['windows'],example['mask'])
        a=node[idx_a];b=node[idx_b]
        parts=torch.cat((example['summary'],a,b,a*b,a-b,
                 self.relations(example['relation']),
                 self.kinds(example['source_kind']),
                 self.kinds(example['destination_kind']),
                 example['geometry']),dim=-1)
        return example['parent_score']+self.scorer(parts).flatten()


def _on_device(ex,device):
    return {name:value.to(device) for name,value in ex.items()}


def score_records(records,model,policy,section,step,cache,device):
    rows=[];model.eval()
    for rec in records:
        if rec['row'].get('split')!='train':
            raise ValueError('GAMER_G2_0048_DEV_EVALUATION_REFUSED')
        key=d46._sha(rec)
        with torch.inference_mode():
            logits=model(_on_device(cache[key],device)).detach().double().cpu().numpy()
        prediction=(logits,rec['structured_pred'][1],rec['structured_pred'][2])
        selected=d44._decode(rec,prediction,policy)
        row=d44._quality(rec,selected,logits,section=section,kind='occurrence_evidence',
                         phase='corrected-data-path',step=step)
        row['missing_candidate_gold_edges']=d46._candidate_gap(rec)
        row['candidate_reachable']=row['missing_candidate_gold_edges']==0
        rows.append(row)
    return rows


def train(records,parent,policy,cache,supervision,*,device='cpu',steps=STEPS,
          batch_size=BATCH_PROMPTS,eval_every=EVAL_EVERY,seed=SEED):
    if not records or not 1<=steps<=MAX_STEPS or not 1<=batch_size<=32 or \
       not 1<=eval_every<=steps or set(cache)!={d46._sha(r) for r in records} or \
       set(supervision)!=set(cache) or device not in ('cpu','cuda'):
        raise ValueError('GAMER_G2_0048_TRAIN_CONTRACT')
    torch.manual_seed(seed)
    width=next(iter(cache.values()))['windows'].shape[-1]
    if any(ex['windows'].shape[-1]!=width for ex in cache.values()):
        raise ValueError('GAMER_G2_0048_INCONSISTENT_WINDOW_WIDTH')
    net=OccurrenceEvidenceScorer(width).to(device).train()
    parameters=list(net.parameters())
    optim=torch.optim.AdamW(parameters,lr=LR,weight_decay=1e-4)
    schedule=d46.fixed_prompt_batches(len(records),steps,batch_size,d46.SEED)
    history=[]
    parent_state={name:value.detach().clone() for name,value in parent.state_dict().items()}
    for step,indices in enumerate(schedule,1):
        optim.zero_grad(set_to_none=True)
        losses=[]
        for i in indices:
            key=d46._sha(records[i]);example=_on_device(cache[key],device)
            scores=net(example)
            _,_,truth,contrasts,pos_weight=supervision[key]
            truth=truth.to(device);pos_weight=pos_weight.to(device)
            losses.append(F.binary_cross_entropy_with_logits(scores,truth,pos_weight=pos_weight))
        loss=torch.stack(losses).mean()
        if not torch.isfinite(loss):
            raise ValueError('GAMER_G2_0048_NONFINITE_LOSS')
        loss.backward()
        gradient=torch.nn.utils.clip_grad_norm_(parameters,5.)
        if not torch.isfinite(gradient) or any(p.grad is not None and
             not torch.isfinite(p.grad).all() for p in parameters):
            raise ValueError('GAMER_G2_0048_NONFINITE_GRADIENT')
        optim.step()
        if step==1 or step%eval_every==0 or step==steps:
            metrics=d46.summarize(score_records(records,net,policy,'train_learn',step,cache,device))
            history.append({'step':step,'loss':float(loss.detach().cpu()),
                            'gradient_norm':float(gradient.detach().cpu()),
                            'training':metrics})
            if step==1 or step%max(240,eval_every)==0 or step==steps:
                print('GAMER_G2_0048_FIT context '+str(step)+'/'+str(steps)+
                      ' exact='+str(metrics['all']['exact'])+'/'+str(len(records))+
                      ' loss='+f'{float(loss.detach().cpu()):.6f}',flush=True)
            net.train()
    if any(not torch.equal(value,parent.state_dict()[name])
           for name,value in parent_state.items()):
        raise ValueError('GAMER_G2_0048_PARENT_MUTATED')
    net.eval().requires_grad_(False)
    return net,{'steps_completed':steps,'prompts_trained':len(records),
                'history':history,'train_final':history[-1]['training']}


def stage2(training,fitting,auditing,policy,parent,assert_frozen,output,
           parent45,parent46,parent47,hashes,*,steps=STEPS,
           batch_size=BATCH_PROMPTS,eval_every=EVAL_EVERY,device='cpu'):
    folder=Path(output)
    if folder.exists():raise FileExistsError('GAMER_G2_0048_IMMUTABLE_OUTPUT')
    if not 1<=steps<=MAX_STEPS or not 1<=batch_size<=32 or not 1<=eval_every<=steps:
        raise ValueError('GAMER_G2_0048_BUDGET')
    cohort=d46.select_cohorts(training,fitting,auditing,parent45,policy)
    d47.verify_cohorts(cohort,parent46)
    if parent47.get('cohorts')!=parent46['cohorts']:
        raise ValueError('GAMER_G2_0048_COHORT_DRIFT')
    sets={key:cohort[key] for key in ('learn','transfer','train_fit','natural_train_audit')}
    # All context is retrieved from the *same* G2-0040 capture. No Qwen
    # re-inference, no replacement spans and no new candidate selection.
    cache={}
    for records in sets.values():
        for rec in records:
            sha=d46._sha(rec)
            if sha in cache:raise ValueError('GAMER_G2_0048_DUPLICATE_EVIDENCE')
            cache[sha]=prepare_evidence(rec,parent)
    supervision=d46.prepare_supervision(sets['learn'],parent)
    net,fit=train(sets['learn'],parent,policy,{d46._sha(r):cache[d46._sha(r)] for r in sets['learn']},
                  supervision,device=device,steps=steps,batch_size=batch_size,eval_every=eval_every)
    results={};ledger=[]
    for section,records in sets.items():
        rows=score_records(records,net,policy,section,steps,cache,device)
        ledger.extend(rows);results[section]=d46.summarize(rows)
    baseline=parent47['results']['arms']['frozen:bce']
    transfer=results['transfer']['candidate_reachable']
    old=baseline['transfer']['candidate_reachable']
    improvement=transfer['exact']-old['exact']
    # Predeclared: at least four additional fully correct reachable graphs,
    # without increasing total missing+extra reachable edges.
    improved=(improvement>=4 and transfer['missing']+transfer['extra']<=old['missing']+old['extra'])
    report={
        'schema':SCHEMA,'status':'TRAIN_OCCURRENCE_EVIDENCE_REPAIR_COMPLETE',
        'source_parent_schema':d47.SCHEMA,'parent_hashes':hashes,
        'train_dataset_sha256':parent46['train_dataset_sha256'],
        'training_sha256':parent46['training_sha256'],
        'cohorts':parent46['cohorts'],
        'protocol':{'steps':steps,'batch_prompts':batch_size,'eval_every':eval_every,
                    'seed':SEED,'optimizer':'AdamW','learning_rate':LR,
                    'shared_trainable_qwen_window_encoder':True,
                    'ordered_source_destination_and_relation_id':True,
                    'candidate_inventory_from_g2_0040_unchanged':True,
                    'g2_0033_parent_and_decoder_frozen':True,
                    'learn_on_train_only':True,'no_dev_or_test_selection':True,
                    'immutable_output':True,'context_window':5,
                    'reference_0047_transfer_exact':old['exact'],
                    'practical_transfer_gain_required':4},
        'input_audit':{
            'old_g2_0033_edge_features':x33.EDGE_DIM,
            'old_features_contain_actual_endpoint_vectors':False,
            'restored_evidence':['frozen_qwen_token_windows','predicted_g1_occurrence_identity',
                                 'source_destination_order','relation_id','node_kinds',
                                 'relative_span_geometry','unchanged_parent_graph_features'],
            'new_candidate_edges':0,
        },
        'fit':fit,'results':results,
        'reference_frozen_bce':{section:baseline['train_learn' if section=='learn' else section]
                                for section in sets},
        'decision':{'exact_reachable_transfer_gain':improvement,
                    'passes_predeclared_practical_signal':bool(improved),
                    'decision':('CONTEXT_EVIDENCE_REPAIR_TRANSFER_SIGNAL' if improved else
                                'CONTEXT_EVIDENCE_REPAIR_NOT_YET_VALIDATED')},
        'development_evaluation_skipped':True,
        'dev_labels_used_for_training':False,
        'dev_labels_used_for_calibration':False,
        'sealed_test_used':False,
        'production_promotion':False,
        'g2_certified':False,'frozen_g1':True,'frozen_g2_0033_parent':True,
        'train_policy':{'enabled':False,'config':None,
                        'disabled_reason':'diagnostic_only_no_selection'},
    }
    assert_frozen()
    folder.parent.mkdir(parents=True,exist_ok=True)
    temp=Path(tempfile.mkdtemp(prefix=folder.name+'.staging-',dir=folder.parent))
    try:
        with gzip.open(temp/LEDGER,'wt',encoding='utf-8',compresslevel=6) as f:
            for row in ledger:f.write(json.dumps(row,sort_keys=True,allow_nan=False)+'\n')
        save_file({name:value.detach().cpu().contiguous().clone()
                   for name,value in net.state_dict().items()},str(temp/WEIGHTS))
        (temp/POLICIES).write_text(json.dumps(report['train_policy'],sort_keys=True,indent=2)+'\n',encoding='utf-8')
        report.update({'ledger_sha256':backbone.sha256(temp/LEDGER),
                       'weights_sha256':backbone.sha256(temp/WEIGHTS),
                       'policies_sha256':backbone.sha256(temp/POLICIES)})
        (temp/REPORT).write_text(json.dumps(report,sort_keys=True,indent=2,allow_nan=False)+'\n',encoding='utf-8')
        if folder.exists():raise FileExistsError('GAMER_G2_0048_OUTPUT_RACE')
        temp.rename(folder)
    finally:
        if temp.exists():shutil.rmtree(temp)
    return report


def run(*,bakeoff40_run,oracle41_run,rank42_run,absolute43_run,
        memorization44_run,separability45_run,multiprompt46_run,crossover47_run,
        output,steps=STEPS,batch_size=BATCH_PROMPTS,eval_every=EVAL_EVERY,
        device='cuda',**kwargs):
    p40,h40=g41.verify_parent40(bakeoff40_run)
    p41,_,h41=g42.verify_parent41(oracle41_run,h40)
    p42,h42=b43.verify_parent42(rank42_run,h40,h41)
    p43,h43=d45.d44.verify_parent43(absolute43_run,h40,h41,h42)
    p44,h44=d45.verify_parent44(memorization44_run,h40,h41,h42,h43)
    p45,h45=d46.verify_parent45(separability45_run,{**h40,**h41,**h42,**h43,**h44})
    p46,h46=d47.verify_parent46(multiprompt46_run,{**h40,**h41,**h42,**h43,**h44,**h45})
    p47,h47=verify_parent47(crossover47_run,{**h40,**h41,**h42,**h43,**h44,**h45,**h46},p46)
    for key in ('train_dataset_sha256','training_sha256'):
        if p47[key]!=p46[key] or p46[key]!=p45[key]:
            raise ValueError('GAMER_G2_0048_PARENT_TRAIN_DRIFT:'+key)
    if Path(output).exists():raise FileExistsError('GAMER_G2_0048_IMMUTABLE_OUTPUT')
    def callback(*,training,fitting,auditing,train_rows,fit_rows,audit_rows,
                 pol33,parent33_models,assert_all_frozen,**ignored):
        for name,rows in (('train_0040',train_rows),('train_fit_0040',fit_rows),
                          ('train_audit_0040',audit_rows)):
            if p46['training_sha256'][name]!=g40.sha_rows(rows):
                raise ValueError('GAMER_G2_0048_TRAIN_COHORT_DRIFT:'+name)
        policy=pol33[f'structured_set_s{g40.SOURCE_SEED}']
        if policy!=p41['protocol']['config']:
            raise ValueError('GAMER_G2_0048_DECODER_POLICY_DRIFT')
        return stage2(training,fitting,auditing,policy,parent33_models[g40.SOURCE_SEED],
            assert_all_frozen,output,p45,p46,p47,{**h40,**h41,**h42,**h43,**h44,**h45,**h46,**h47},
            steps=steps,batch_size=batch_size,eval_every=eval_every,device=device)
    return g40.run(output=output,diagnostic_callback=callback,device=device,**kwargs)
