"""G2-0047: decisive TRAIN-only relational representation x objective crossover.

Compare frozen-feature and jointly trainable G2-0033 edge representations with
binary and exact-decoder structured supervision on IDENTICAL G2-0046 prompts.
No DEV/TEST, no policy selection, no certification, no production promotion.
"""
from __future__ import annotations

import copy
from collections import Counter
import gzip
import json
import math
import shutil
import tempfile
from pathlib import Path

import torch
from torch import nn
from torch.nn import functional as F
from safetensors.torch import load_file, save_file

from tools import nanojev_gamer_v1_g2_multiprompt_discrimination as d46

d45=d46.d45
d44=d46.d44
b43=d46.b43
g40=d46.g40
g41=d46.g41
g42=d46.g42
x33=d45.x33
backbone=d46.backbone

SCHEMA='nanojev.gamer.graph.g2.architectural-crossover-diagnosis.v1'
REPORT='g2_architectural_crossover_report.json'
LEDGER='g2_architectural_crossover_per_case.jsonl.gz'
WEIGHTS='g2_architectural_crossover_development.safetensors'
POLICIES='g2_architectural_crossover_frozen_policies.json'
SEED=d46.SEED
STEPS=d46.STEPS
BATCH_PROMPTS=d46.BATCH_PROMPTS
EVAL_EVERY=d46.EVAL_EVERY
MAX_STEPS=d46.MAX_STEPS
REPRESENTATIONS=('frozen','joint')
OBJECTIVES=('bce','structured')
STRUCTURED_PER_BATCH=2
REPR_LR=.0008
READOUT_LR=.004
STRUCTURED_WEIGHT=.7
GOLD_MARGIN=.75


def verify_parent46(directory, expected_hashes):
    """Treat G2-0046 as an immutable predeclared cohort and control."""
    folder=Path(directory)
    report=json.loads((folder/d46.REPORT).read_text(encoding='utf-8'))
    required={
        'schema':d46.SCHEMA,
        'status':'TRAIN_MULTIPROMPT_DIAGNOSIS_COMPLETE',
        'source_parent_schema':d45.SCHEMA,
        'parent_hashes':expected_hashes,
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
    for key,value in required.items():
        if report.get(key)!=value:
            raise ValueError('GAMER_G2_0047_PARENT_0046_CONTRACT:'+key)
    cohorts=report.get('cohorts',{})
    required_cohorts=('train_learn_prompts','transfer_prompts','train_fit_prompts',
                      'natural_train_audit_prompts','fixed_g2_0045_transfer_prompts')
    for name in required_cohorts:
        if not isinstance(cohorts.get(name),list) or len(cohorts[name])!=len(set(cohorts[name])):
            raise ValueError('GAMER_G2_0047_PARENT_COHORT:'+name)
    actual={name:set(cohorts[name]) for name in required_cohorts[:4]}
    if any(actual[a]&actual[b] for i,a in enumerate(actual) for b in list(actual)[i+1:]):
        raise ValueError('GAMER_G2_0047_PARENT_COHORT_OVERLAP')
    if not set(cohorts['fixed_g2_0045_transfer_prompts'])<=actual['transfer_prompts']:
        raise ValueError('GAMER_G2_0047_PARENT_0045_TRANSFER_LOST')
    protocol=report.get('protocol',{})
    if protocol.get('architecture')!=list(d46.KINDS) or protocol.get('objectives')!=list(d46.LOSSES) or \
       protocol.get('steps')!=STEPS or protocol.get('batch_prompts')!=BATCH_PROMPTS or \
       protocol.get('seed')!=SEED or not protocol.get('identical_training_batches'):
        raise ValueError('GAMER_G2_0047_PARENT_BUDGET_DRIFT')
    if set(report.get('results',{}).get('arms',{}))!={f'{k}:{v}' for k in d46.KINDS for v in d46.LOSSES}:
        raise ValueError('GAMER_G2_0047_PARENT_ARMS')
    if report['results']['arms']['nonlinear:bce'].get('transfer',{}).get('all',{}).get('cases')!=len(cohorts['transfer_prompts']):
        raise ValueError('GAMER_G2_0047_PARENT_TRANSFER_STATS')
    if json.loads((folder/d46.POLICIES).read_text(encoding='utf-8'))!=report['train_policy']:
        raise ValueError('GAMER_G2_0047_PARENT_POLICY_DRIFT')
    hashes={}
    for filename,name in ((d46.REPORT,'report'),(d46.LEDGER,'ledger'),
                          (d46.WEIGHTS,'weights'),(d46.POLICIES,'policies')):
        digest=backbone.sha256(folder/filename)
        hashes[f'g2_0046_{name}_sha256']=digest
        if name!='report' and digest!=report.get(name+'_sha256'):
            raise ValueError('GAMER_G2_0047_PARENT_HASH:'+filename)
    weights=load_file(str(folder/d46.WEIGHTS),device='cpu')
    p=x33.GraphSetEnergy().eval().requires_grad_(False)
    keys={f'{kind}_{obj}.{name}':value.shape
          for kind in d46.KINDS for obj in d46.LOSSES
          for name,value in d45.Readout(kind,p).state_dict().items()}
    if set(weights)!=set(keys) or any(weights[name].shape!=shape or not torch.isfinite(weights[name]).all()
                                     for name,shape in keys.items()):
        raise ValueError('GAMER_G2_0047_PARENT_WEIGHT_SHAPES')
    # G2-0046 writes the raw cohort key ``learn`` to its ledger. Only G2-0047
    # uses ``train_learn`` as a display/report key; they are not interchangeable
    # in an immutable parent artifact. Validate the real writer contract, and
    # require one entry for every (cohort, arm, prompt), not merely a row total.
    ledger_cohorts={
        'learn':'train_learn_prompts',
        'transfer':'transfer_prompts',
        'train_fit':'train_fit_prompts',
        'natural_train_audit':'natural_train_audit_prompts',
    }
    expected_prompts={name:set(cohorts[field])
                      for name,field in ledger_cohorts.items()}
    expected_arms={'frozen_parent',
                   *(f'{kind}:{loss}' for kind in d46.KINDS for loss in d46.LOSSES)}
    counts=Counter()
    seen=set()
    with gzip.open(folder/d46.LEDGER,'rt',encoding='utf-8') as f:
        for raw in f:
            row=json.loads(raw)
            section=row.get('section')
            if section not in ledger_cohorts:
                raise ValueError('GAMER_G2_0047_PARENT_LEDGER_SECTION')
            arm=row.get('arm')
            if arm not in expected_arms:
                raise ValueError('GAMER_G2_0047_PARENT_LEDGER_ARM')
            prompt=row.get('prompt_sha256')
            if prompt not in expected_prompts[section]:
                raise ValueError('GAMER_G2_0047_PARENT_LEDGER_PROMPT')
            identity=(section,arm,prompt)
            if identity in seen:
                raise ValueError('GAMER_G2_0047_PARENT_LEDGER_DUPLICATE')
            seen.add(identity)
            counts[section,arm]+=1
    if any(counts[section,arm]!=len(expected_prompts[section])
           for section in ledger_cohorts for arm in expected_arms):
        raise ValueError('GAMER_G2_0047_PARENT_LEDGER_COUNT')
    return report,hashes


def verify_cohorts(cohort,parent46):
    observed={
       'learn':'train_learn_prompts',
       'transfer':'transfer_prompts',
       'train_fit':'train_fit_prompts',
       'natural_train_audit':'natural_train_audit_prompts',
    }
    for section,field in observed.items():
        have=[d46._sha(rec) for rec in cohort[section]]
        expected=parent46['cohorts'][field]
        if have!=expected:
            raise ValueError('GAMER_G2_0047_PARENT_COHORT_DRIFT:'+field)
    if cohort['fixed_transfer_prompts']!=parent46['cohorts']['fixed_g2_0045_transfer_prompts']:
        raise ValueError('GAMER_G2_0047_FIXED_TRANSFER_DRIFT')
    if cohort['excluded_train_prompts']!=parent46['cohorts']['excluded_train_prompts']:
        raise ValueError('GAMER_G2_0047_EXCLUSIONS_DRIFT')


def prepare_features(records,parent):
    """Identical label-free inputs for every architecture; no gold in encoding."""
    cache={}
    for rec in records:
        if rec['row'].get('split')!='train':
            raise ValueError('GAMER_G2_0047_NONTRAIN_FEATURES')
        key=d46._sha(rec)
        if key in cache:raise ValueError('GAMER_G2_0047_DUPLICATE_PROMPT')
        h,prior,original=d45.extract_frozen(rec,parent)
        edge,groups,index=x33._tensor_features(rec['features'],'cpu')
        cache[key]={'edges':edge,'groups':groups,'index':index,'h':h,
                    'prior':prior,'parent_score':original}
    return cache


class CrossoverScorer(nn.Module):
    """Same parent initialization; optionally learn edge/group interactions.

    Qwen, G1, G2-0033 parent, candidate generator, count heads, and exact
    decoder remain frozen. Only a private diagnostic copy is trainable.
    """
    def __init__(self,parent,representation):
        super().__init__()
        if representation not in REPRESENTATIONS:
            raise ValueError('GAMER_G2_0047_UNKNOWN_REPRESENTATION')
        self.representation=representation
        self.readout=d45.Readout('nonlinear',parent)
        self.encoder=copy.deepcopy(parent).eval().requires_grad_(False) if representation=='joint' else None
        if self.encoder is not None:
            for name in ('edge_embed','group_embed','group_norm','edge_update'):
                getattr(self.encoder,name).requires_grad_(True)

    def forward(self,item):
        h=item['h']
        if self.encoder is not None:
            e=self.encoder.edge_embed(item['edges'])
            idx=item['index']; n=len(item['groups'])
            pool=torch.zeros((n,x33.HIDDEN),dtype=e.dtype,device=e.device).index_add(0,idx,e)
            count=torch.bincount(idx,minlength=n).clamp(min=1)
            pool=pool/count[:,None]
            g=self.encoder.group_norm(self.encoder.group_embed(item['groups'])+pool)
            global_g=g.mean(dim=0)
            h=self.encoder.edge_update(torch.cat((e,g[idx],global_g.expand_as(e)),-1))
        return self.readout(h,item['prior'])

    def learned_params(self):
        return [param for param in self.readout.parameters() if param.requires_grad]

    def representation_params(self):
        return [param for param in self.encoder.parameters() if param.requires_grad] if self.encoder else []


def structured_margin(logits,rec,truth,policy):
    """Exact-decoder TRAIN-only loss-augmented comparison, no gold in inference."""
    if rec['row'].get('split')!='train':
        raise ValueError('GAMER_G2_0047_STRUCTURED_DEV_REFUSED')
    with torch.no_grad():
        selected=d44._decode(rec,(logits.detach().double().cpu().numpy(),
                 rec['structured_pred'][1],rec['structured_pred'][2]),policy)
    gold=d46._gold(rec)
    if selected==gold:
        return logits.sum()*0.
    candidate=rec['shortlisted']['edges']
    choice=torch.as_tensor([edge in selected for edge in candidate],dtype=truth.dtype)
    distance=(choice-truth).abs().sum().detach()
    if not (float(distance)>0):return logits.sum()*0.
    group_logits=torch.as_tensor(rec['structured_pred'][1],dtype=logits.dtype)
    total_log=torch.log1p(torch.tensor(float(rec['structured_pred'][2]),dtype=logits.dtype).clamp(min=0.))
    gold_energy=b43._selection_energy(logits,group_logits,total_log,truth,rec['features'],policy)
    wrong_energy=b43._selection_energy(logits,group_logits,total_log,choice,rec['features'],policy)
    return F.relu(GOLD_MARGIN*distance+wrong_energy-gold_energy)/distance


def score_records(records,model,policy,cohort,arm,step,cache):
    rows=[]
    model.eval()
    for rec in records:
        if rec['row'].get('split')!='train':
            raise ValueError('GAMER_G2_0047_NONTRAIN_SCORE')
        key=d46._sha(rec)
        with torch.inference_mode():
            logits=model(cache[key]).detach().double().cpu().numpy()
        selected=d44._decode(rec,(logits,rec['structured_pred'][1],
                                  rec['structured_pred'][2]),policy)
        row=d44._quality(rec,selected,logits,section=cohort,kind=arm,
                         phase='architecture-crossover',step=step)
        row['missing_candidate_gold_edges']=d46._candidate_gap(rec)
        row['candidate_reachable']=row['missing_candidate_gold_edges']==0
        rows.append(row)
    return rows


def train_arm(records,parent,policy,representation,objective,cache,supervision,*,
              steps=STEPS,batch_size=BATCH_PROMPTS,eval_every=EVAL_EVERY,
              structured_per_batch=STRUCTURED_PER_BATCH,seed=SEED):
    if representation not in REPRESENTATIONS or objective not in OBJECTIVES or \
       not 1<=steps<=MAX_STEPS or not 1<=batch_size<=32 or \
       not 1<=structured_per_batch<=batch_size or not 1<=eval_every<=steps:
        raise ValueError('GAMER_G2_0047_BAD_ARM_OR_BUDGET')
    if set(supervision)!={d46._sha(rec) for rec in records}:
        raise ValueError('GAMER_G2_0047_SUPERVISION_COHORT')
    torch.manual_seed(seed+100)  # exactly G2-0046 nonlinear initialization
    model=CrossoverScorer(parent,representation).train()
    groups=[{'params':model.learned_params(),'lr':READOUT_LR}]
    if model.representation_params():
        groups.append({'params':model.representation_params(),'lr':REPR_LR})
    optimizer=torch.optim.Adam(groups)
    trainable=[p for group in groups for p in group['params']]
    schedule=d46.fixed_prompt_batches(len(records),steps,batch_size,seed)
    history=[]
    for step,batch in enumerate(schedule,1):
        optimizer.zero_grad(set_to_none=True)
        binary_losses=[]
        graph_losses=[]
        for rank,i in enumerate(batch):
            rec=records[i];key=d46._sha(rec)
            scores=model(cache[key])
            binary_losses.append(d46.prompt_loss(scores,supervision[key],'bce'))
            if objective=='structured' and rank<structured_per_batch:
                graph_losses.append(structured_margin(scores,rec,supervision[key][2],policy))
        loss=torch.stack(binary_losses).mean()
        if graph_losses:
            # Normalize independently: subsampling graph decodes cannot dilute
            # the predeclared structured weight by batch size.
            loss=loss+STRUCTURED_WEIGHT*torch.stack(graph_losses).mean()
        loss.backward()
        grad=torch.nn.utils.clip_grad_norm_(trainable,5.)
        if not torch.isfinite(grad) or any(
            p.grad is None or not torch.isfinite(p.grad).all() for p in trainable):
            raise ValueError('GAMER_G2_0047_NONFINITE_GRADIENT')
        optimizer.step()
        if step==1 or step%eval_every==0 or step==steps:
            scored=score_records(records,model,policy,'train_learn',
                   f'{representation}:{objective}',step,cache)
            summary=d46.summarize(scored)
            history.append({'step':step,'loss':float(loss.detach()),
                            'grad_norm':float(grad.detach()),'training':summary})
            if step==1 or step%max(eval_every,240)==0 or step==steps:
                print(f'GAMER_G2_0047_FIT {representation}:{objective} '
                      f'{step}/{steps} exact={summary["all"]["exact"]}/{len(records)} '
                      f'loss={float(loss.detach()):.6f}',flush=True)
            model.train()
    model.eval().requires_grad_(False)
    return model,{'steps_completed':steps,'train_final':history[-1]['training'],
                  'history':history,'prompts_trained':len(records)}


def determine_action(results):
    """Predeclared descriptive decision matrix; never claims proof of absence."""
    arms=results['arms']
    def exact(key):return arms[key]['transfer']['candidate_reachable']['exact']
    f=exact('frozen:bce')
    j=exact('joint:bce')
    fs=exact('frozen:structured')
    js=exact('joint:structured')
    # At 32 holdout prompts, single-graph improvements are inconclusive.
    threshold=4
    return {
        'frozen_bce_transfer_exact':f,'joint_bce_transfer_exact':j,
        'frozen_structured_transfer_exact':fs,'joint_structured_transfer_exact':js,
        'minimum_practical_transfer_gain_graphs':threshold,
        'representation_learning_signal':j-f>=threshold,
        'structured_supervision_signal':fs-f>=threshold or js-j>=threshold,
        'joint_interaction_signal':js-max(f,j,fs)>=threshold,
        'causal_proof_of_representation_sufficiency':False,
        'causal_proof_of_representation_insufficiency':False,
        'decision':('CONSIDER_JOINT_RELATIONAL_ARCHITECTURE' if js-max(f,j,fs)>=threshold else
                    'CONSIDER_TRAINABLE_REPRESENTATIONS' if j-f>=threshold else
                    'CONSIDER_GRAPH_STRUCTURED_SUPERVISION' if fs-f>=threshold else
                    'NO_DECISIVE_TRANSFER_GAIN_REEXAMINE_INPUT_EVIDENCE_AND_GRAPH_OBJECTIVE'),
    }


def stage2(training,fitting,auditing,policy,parent,assert_frozen,output,parent45,parent46,hashes,
           *,steps=STEPS,batch_size=BATCH_PROMPTS,eval_every=EVAL_EVERY,
           structured_per_batch=STRUCTURED_PER_BATCH):
    folder=Path(output)
    if folder.exists():raise FileExistsError('GAMER_G2_0047_IMMUTABLE_OUTPUT')
    if not 1<=steps<=MAX_STEPS or not 1<=batch_size<=32 or \
       not 1<=eval_every<=steps or not 1<=structured_per_batch<=batch_size:
        raise ValueError('GAMER_G2_0047_BUDGET')
    cohort=d46.select_cohorts(training,fitting,auditing,parent45,policy)
    verify_cohorts(cohort,parent46)
    sections={key:cohort[key] for key in ('learn','transfer','train_fit','natural_train_audit')}
    parent_snapshot={k:v.detach().clone() for k,v in parent.state_dict().items()}
    combined=[*sections['learn'],*sections['transfer'],*sections['train_fit'],
              *sections['natural_train_audit']]
    cache=prepare_features(combined,parent)
    supervision=d46.prepare_supervision(sections['learn'],parent)
    ledger=[];arms={};weights={}
    for representation in REPRESENTATIONS:
        for objective in OBJECTIVES:
            arm=f'{representation}:{objective}'
            model,metrics=train_arm(sections['learn'],parent,policy,representation,objective,
                cache,supervision,steps=steps,batch_size=batch_size,eval_every=eval_every,
                structured_per_batch=structured_per_batch)
            for key,rows in sections.items():
                section='train_learn' if key=='learn' else key
                new=score_records(rows,model,policy,section,arm,steps,cache)
                ledger.extend(new)
                metrics[section]=d46.summarize(new)
            arms[arm]=metrics
            weights.update({f'{representation}_{objective}.{name}':value.detach().cpu().contiguous().clone()
                            for name,value in model.state_dict().items()})
            assert_frozen()
    if any(not torch.equal(value,parent.state_dict()[name])
           for name,value in parent_snapshot.items()):
        raise ValueError('GAMER_G2_0047_PARENT_MUTATED')
    # At the reference budget reproduce the prior frozen:BCE arm, or refuse a
    # scientifically invalid comparison against G2-0046.
    comparable=steps==STEPS and batch_size==BATCH_PROMPTS and eval_every==EVAL_EVERY
    if comparable:
        reference=parent46['results']['arms']['nonlinear:bce']
        for section in ('train_learn','transfer','train_fit','natural_train_audit'):
            actual=arms['frozen:bce'][section]
            previous=reference['learn' if section=='train_learn' else section]
            for part in ('all','candidate_reachable','candidate_unreachable'):
                for measurement in ('cases','exact','missing','extra','rank_inversions'):
                    if actual[part][measurement]!=previous[part][measurement]:
                        raise ValueError('GAMER_G2_0047_BASELINE_REPRODUCTION_FAILED:'+
                                         section+':'+part+':'+measurement)
    reference=parent46['results']['arms']['nonlinear:bce']
    report={
      'schema':SCHEMA,'status':'TRAIN_ARCHITECTURAL_CROSSOVER_COMPLETE',
      'source_parent_schema':d46.SCHEMA,'parent_hashes':hashes,
      'train_dataset_sha256':parent46['train_dataset_sha256'],
      'training_sha256':parent46['training_sha256'],
      'cohorts':parent46['cohorts'],
      'protocol':{
        'representations':list(REPRESENTATIONS),'objectives':list(OBJECTIVES),
        'steps':steps,'batch_prompts':batch_size,'eval_every':eval_every,
        'seed':SEED,'identical_training_batches':True,
        'structured_prompts_per_batch':structured_per_batch,
        'structured_weight':STRUCTURED_WEIGHT,'structured_margin':GOLD_MARGIN,
        'readout_learning_rate':READOUT_LR,'representation_learning_rate':REPR_LR,
        'original_prompt_g1_and_qwen_frozen':True,
        'graph_representation_trainable_copy_only':True,
        'fixed_parent_prior_count_heads_decoder':True,
        'same_g2_0046_prompts_and_oracle_reconstructibility':True,
        'no_dev_or_test_selection':True,'immutable_results':True,
        'reference_budget_reproduced':comparable,
      },
      'results':{'arms':arms,'g2_0046_reference_nonlinear_bce':{
          section:reference['learn' if section=='train_learn' else section]
          for section in ('train_learn','transfer','train_fit','natural_train_audit')}},
      'development_evaluation_skipped':True,
      'dev_labels_used_for_training':False,
      'dev_labels_used_for_calibration':False,
      'sealed_test_used':False,'production_promotion':False,
      'g2_certified':False,'frozen_g1':True,'frozen_g2_0033_parent':True,
      'train_policy':{'enabled':False,'config':None,
                      'disabled_reason':'diagnostic_only_no_selection'},
    }
    report['decision']=determine_action(report['results'])
    assert_frozen()
    folder.parent.mkdir(parents=True,exist_ok=True)
    temp=Path(tempfile.mkdtemp(prefix=folder.name+'.staging-',dir=folder.parent))
    try:
        with gzip.open(temp/LEDGER,'wt',encoding='utf-8',compresslevel=6) as f:
            for row in ledger:
                f.write(json.dumps(row,sort_keys=True,allow_nan=False)+'\n')
        save_file(weights,str(temp/WEIGHTS))
        (temp/POLICIES).write_text(json.dumps(report['train_policy'],sort_keys=True,indent=2)+'\n',
                                   encoding='utf-8')
        report.update({'ledger_sha256':backbone.sha256(temp/LEDGER),
                       'weights_sha256':backbone.sha256(temp/WEIGHTS),
                       'policies_sha256':backbone.sha256(temp/POLICIES)})
        (temp/REPORT).write_text(json.dumps(report,sort_keys=True,indent=2,allow_nan=False)+'\n',
                                 encoding='utf-8')
        if folder.exists():raise FileExistsError('GAMER_G2_0047_IMMUTABLE_OUTPUT_RACE')
        temp.rename(folder)
    finally:
        if temp.exists():shutil.rmtree(temp)
    return report


def run(*,bakeoff40_run,oracle41_run,rank42_run,absolute43_run,
        memorization44_run,separability45_run,multiprompt46_run,output,
        steps=STEPS,batch_size=BATCH_PROMPTS,eval_every=EVAL_EVERY,
        structured_per_batch=STRUCTURED_PER_BATCH,**kwargs):
    p40,h40=g41.verify_parent40(bakeoff40_run)
    p41,_,h41=g42.verify_parent41(oracle41_run,h40)
    p42,h42=b43.verify_parent42(rank42_run,h40,h41)
    p43,h43=d44.verify_parent43(absolute43_run,h40,h41,h42)
    p44,h44=d45.verify_parent44(memorization44_run,h40,h41,h42,h43)
    p45,h45=d46.verify_parent45(separability45_run,{**h40,**h41,**h42,**h43,**h44})
    p46,h46=verify_parent46(multiprompt46_run,{**h40,**h41,**h42,**h43,**h44,**h45})
    for key in ('train_dataset_sha256','training_sha256'):
        if p46[key]!=p45[key]:
            raise ValueError('GAMER_G2_0047_PARENT_TRAIN_DRIFT:'+key)
    if Path(output).exists():raise FileExistsError('GAMER_G2_0047_IMMUTABLE_OUTPUT')
    def callback(*,training,fitting,auditing,train_rows,fit_rows,audit_rows,
                 pol33,parent33_models,assert_all_frozen,**ignored):
        for name,rows in (('train_0040',train_rows),('train_fit_0040',fit_rows),
                          ('train_audit_0040',audit_rows)):
            if p46['training_sha256'][name]!=g40.sha_rows(rows):
                raise ValueError('GAMER_G2_0047_TRAIN_COHORT_DRIFT:'+name)
        policy=pol33[f'structured_set_s{g40.SOURCE_SEED}']
        if policy!=p41['protocol']['config']:
            raise ValueError('GAMER_G2_0047_DECODER_POLICY_DRIFT')
        return stage2(training,fitting,auditing,policy,parent33_models[g40.SOURCE_SEED],
             assert_all_frozen,output,p45,p46,{**h40,**h41,**h42,**h43,**h44,**h45,**h46},
             steps=steps,batch_size=batch_size,eval_every=eval_every,
             structured_per_batch=structured_per_batch)
    return g40.run(output=output,diagnostic_callback=callback,**kwargs)
