"""G1-0074: frozen-head structural-learning competition, no promotion.

Competing interface-only objectives:
  * nonlinear whole-action-boundary logistic + fragment contrast;
  * span-grouped three-class null/actor/action cross-entropy;
  * globally loss-augmented structured-set max-margin updates.

Every candidate and inference feature is computed solely from frozen G1 logits.
Training labels are used only by fitting and post-prediction scoring.
A G1-0073 report pins the exact G1-0072 control in every split. Historical
challenge and within-campaign audit are *not* independent certification.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path

import torch
import torch.nn.functional as F

from tools.nanojev_gamer_v1_g1_oracle_decoder_cutover_frob import _summarize, sha256, _checked_preflight
from tools import nanojev_gamer_v1_g1_learned_interface_bakeoff_0073 as previous
from tools import nanojev_gamer_v1_g1_admission_bakeoff_0070 as admission
from tools import nanojev_gamer_v1_g1_complete_object_bakeoff_0072 as complete

SCHEMA = 'nanojev.gamer.graph.g1.structural-learning-bakeoff-0074.v1'
CONTROL = previous.CONTROL
ARMS = (CONTROL, 'whole_span_boundary', 'joint_type_admission', 'structured_set_maxmargin')
REQUIRED_KINDS = previous.REQUIRED_KINDS
ACTOR, ACTION = previous.ACTOR, previous.ACTION
MAX_OPTIONS = previous.MAX_OPTIONS
# Deterministically engineered features. No random weight initialization.
DIM = 32
HEAD_ADJUSTMENT = 0.40
MAX_WEIGHT = 4.0


@dataclass
class Encoded:
    case: previous.Case
    features: list[tuple[float, ...]]


def _phi_features(case):
    """Static nonlinear boundary/fragment/competition coordinates from logits.

    No row, label, target, corpus-frequency, or oracle value is consulted.
    Gold is deliberately not accepted in this function's signature.
    """
    options = case.options
    gains = {(o.start,o.end,o.kind):o.base*(o.end-o.start) for o in options}
    starts = defaultdict(list)
    ends = defaultdict(list)
    same_span = defaultdict(list)
    for o in options:
        score = o.base*(o.end-o.start)
        starts[(o.kind,o.start)].append(score)
        ends[(o.kind,o.end)].append(score)
        same_span[(o.start,o.end)].append((o.kind,score))
    n = max(1,case.n_tokens)
    rows=[]
    for o in options:
        width=o.end-o.start
        gain=o.base*width
        other=max((v for kind,v in same_span[(o.start,o.end)] if kind!=o.kind),default=0.0)
        left=max(ends.get((o.kind,o.start),[0.0]))
        right=max(starts.get((o.kind,o.end),[0.0]))
        pairs=[gains[(o.start,k,o.kind)]+gains[(k,o.end,o.kind)]
               for k in range(o.start+1,o.end)
               if (o.start,k,o.kind) in gains and (k,o.end,o.kind) in gains]
        fragment=max(pairs,default=0.0)
        margin=float(o.features[1])
        cond=float(o.features[2])
        pos=o.start/n
        raw=list(o.features)+[
            math.tanh(gain/8.),
            math.tanh(other/8.),
            math.tanh((gain-other)/8.),
            math.tanh(left/8.),
            math.tanh(right/8.),
            math.tanh(fragment/8.),
            math.tanh((gain-fragment)/8.),
            math.tanh((gain-left)/8.),
            math.tanh((gain-right)/8.),
            math.log1p(width)/4.,
            float(width==1),
            float(width>=4),
            float(width>=8),
            math.sin(2*math.pi*pos),
            math.cos(2*math.pi*pos),
            margin*float(o.kind==ACTOR),
            margin*float(o.kind==ACTION),
            cond*math.log1p(width)/4.,
            float(o.anchored)*margin,
            math.tanh((gain-fragment)/8.)*float(o.kind==ACTION),
            math.tanh((gain-other)/8.)*float(o.kind==ACTOR),
        ]
        if len(raw)!=DIM or not all(math.isfinite(x) for x in raw):
            raise ValueError('G1_0074_FEATURE_CONTRACT')
        rows.append(tuple(float(x) for x in raw))
    return rows


def _encode(case):
    if len(case.options)>MAX_OPTIONS:
        raise ValueError('G1_0074_TOO_MANY_CANDIDATES')
    return Encoded(case,_phi_features(case))


def _safe_weights(arm,weights):
    if arm==CONTROL:
        return
    count=2 if arm=='joint_type_admission' else 1
    if (not isinstance(weights,(list,tuple)) or len(weights)!=count
        or any(len(row)!=DIM or any(not math.isfinite(float(v)) or abs(float(v))>MAX_WEIGHT+1e-8
              for v in row) for row in weights)):
        raise ValueError('G1_0074_INVALID_WEIGHTS')


def _scores(data,arm,weights):
    _safe_weights(arm,weights)
    scored=[]
    for o,features in zip(data.case.options,data.features):
        gain=float(o.base)*(o.end-o.start)
        if o.kind in (ACTOR,ACTION) and arm!=CONTROL:
            w=weights[0 if arm!='joint_type_admission' or o.kind==ACTOR else 1]
            if arm=='whole_span_boundary' and o.kind!=ACTION:
                bonus=0.0
            else:
                bonus=HEAD_ADJUSTMENT*sum(float(a)*b for a,b in zip(w,features))
            gain+=bonus*(o.end-o.start)
        if not math.isfinite(gain):
            raise ValueError('G1_0074_NONFINITE_SCORE')
        scored.append(gain)
    return scored


def _viterbi(data,scores,*,loss_gold=None,oracle_gold=None,delta=0.8):
    """Global interval optimum. Oracle labels allowed ONLY in training calls."""
    n=data.case.n_tokens
    if len(scores)!=len(data.case.options) or n<1 or len(scores)>MAX_OPTIONS:
        raise ValueError('G1_0074_BAD_LATTICE')
    if loss_gold is not None and oracle_gold is not None:
        raise ValueError('G1_0074_ORACLE_CONFLICT')
    by_end=[[] for _ in range(n+1)]
    for index,(opt,score) in enumerate(zip(data.case.options,scores)):
        if not 0<=opt.start<opt.end<=n or not math.isfinite(score):
            raise ValueError('G1_0074_BAD_INTERVAL')
        if oracle_gold is not None:
            if opt.char_key not in oracle_gold:
                continue
            score=100000.0+score*.000001
        elif loss_gold is not None:
            score+=(-delta if opt.char_key in loss_gold else delta)
        by_end[opt.end].append((opt.start,float(score),index))
    dp=[0.0]*(n+1)
    back=[(0,None)]*(n+1)
    for end in range(1,n+1):
        best=dp[end-1]
        picked=(end-1,None)
        for start,gain,index in by_end[end]:
            value=dp[start]+gain
            if gain>0 and value>best+1.e-8:
                best=value
                picked=(start,index)
        dp[end]=best
        back[end]=picked
    indices=[]
    at=n
    while at:
        start,index=back[at]
        if index is not None:
            indices.append(index)
        at=start
    if len(indices)>128:
        raise ValueError('G1_0074_PREDICTION_OVERFLOW')
    return tuple(reversed(indices))


def _predict(data,arm,weights):
    if arm==CONTROL:
        return set(data.case.control)
    selected=_viterbi(data,_scores(data,arm,weights))
    return {data.case.options[j].char_key for j in selected
            if data.case.options[j].char_key is not None}


def _hard_action_rows(train):
    pos=[];neg=[];diff=[]
    for data in train:
        positives=[j for j,o in enumerate(data.case.options)
                   if o.kind==ACTION and o.char_key in data.case.gold]
        negatives=[j for j,o in enumerate(data.case.options)
                   if o.kind==ACTION and o.char_key not in data.case.gold]
        pos.extend(data.features[j] for j in positives)
        # Hard, local negatives: preferentially fragmented spans or overlaps.
        subset=set()
        for j in positives:
            true=data.case.options[j]
            adversaries=sorted((k for k in negatives if
                data.case.options[k].start<true.end and true.start<data.case.options[k].end),
                key=lambda k:(-data.case.options[k].base,k))[:5]
            for k in adversaries:
                subset.add(k)
                diff.append(tuple(a-b for a,b in zip(data.features[j],data.features[k])))
        for k in sorted(negatives,key=lambda k:(-data.case.options[k].base,k))[:max(3,2*len(positives))]:
            subset.add(k)
        neg.extend(data.features[k] for k in sorted(subset))
    if not pos or not neg or not diff:
        raise ValueError('G1_0074_INSUFFICIENT_BOUNDARY_SUPERVISION')
    return pos,neg,diff


def _train_boundary(train,steps=180):
    pos,neg,diff=_hard_action_rows(train)
    Xp=torch.tensor(pos,dtype=torch.float64)
    Xn=torch.tensor(neg,dtype=torch.float64)
    Xd=torch.tensor(diff,dtype=torch.float64)
    w=torch.zeros(DIM,dtype=torch.float64,requires_grad=True)
    opt=torch.optim.Adam([w],lr=.028)
    for _ in range(steps):
        opt.zero_grad()
        # Whole-span versus fragment learning, not generic pointwise candidate BCE.
        loss=(.30*F.softplus(-Xp@w).mean()+
              .30*F.softplus(Xn@w).mean()+
              .40*F.softplus(1.-Xd@w).mean()+.025*w.square().sum())
        loss.backward()
        opt.step()
        with torch.no_grad():w.clamp_(-MAX_WEIGHT,MAX_WEIGHT)
    return (tuple(float(x) for x in w.detach()),),{'positive':len(pos),'negative':len(neg),'whole_vs_fragment_pairs':len(diff)}


def _grouped_type_rows(train):
    matrices=[];fixed=[];targets=[]
    counts={'null':0,'actor':0,'action':0}
    for data in train:
        groups=defaultdict(dict)
        for j,o in enumerate(data.case.options):
            if o.kind in (ACTOR,ACTION):
                old=groups[(o.start,o.end)].get(o.kind)
                if old is None or o.base>data.case.options[old].base:
                    groups[(o.start,o.end)][o.kind]=j
        # Every gold type survives. Null spans are sampled by FROZEN neural
        # competition only, to focus on hard negatives without fitting on
        # tens of thousands of irrelevant low-score spans per prompt.
        positives=[];negatives=[]
        for coords,ids in groups.items():
            gold=[k for k,j in ids.items() if data.case.options[j].char_key in data.case.gold]
            if len(gold)>1:raise ValueError('G1_0074_MULTITYPE_GOLD')
            record=(coords,ids,gold[0] if gold else 0)
            if gold:positives.append(record)
            else:negatives.append(record)
        negatives.sort(key=lambda record:(
            -max(data.case.options[j].base*(data.case.options[j].end-data.case.options[j].start)
                 for j in record[1].values()),record[0]))
        selected=positives+negatives[:max(20,6*len(positives))]
        for _,ids,target in selected:
            types=[];base=[]
            for k in (ACTOR,ACTION):
                j=ids.get(k)
                types.append(data.features[j] if j is not None else (0.,)*DIM)
                base.append(max(-3.,min(3.,data.case.options[j].base*min(5,data.case.options[j].end-data.case.options[j].start)/3.))
                            if j is not None else -100.)
            classes={0:'null',ACTOR:'actor',ACTION:'action'}
            counts[classes[target]]+=1
            matrices.append(types);fixed.append(base);targets.append(target)
    if min(counts.values())==0:
        raise ValueError('G1_0074_JOINT_TYPE_COLLAPSE')
    return matrices,fixed,targets,counts


def _train_joint(train,steps=200):
    rows,fixed,targets,counts=_grouped_type_rows(train)
    X=torch.tensor(rows,dtype=torch.float64)
    base=torch.tensor(fixed,dtype=torch.float64)
    target=torch.tensor(targets,dtype=torch.long)
    weights=torch.zeros((2,DIM),dtype=torch.float64,requires_grad=True)
    opt=torch.optim.Adam([weights],lr=.025)
    # Fixed class-balancing, no data-derived inference statistics.
    sample=torch.where(target==0,torch.tensor(.50),torch.tensor(2.0)).to(torch.float64)
    for _ in range(steps):
        opt.zero_grad()
        logits=base+torch.einsum('nkd,kd->nk',X,weights)
        logits=torch.cat([torch.zeros((X.shape[0],1),dtype=torch.float64),logits],dim=1)
        losses=F.cross_entropy(logits,target,reduction='none')
        loss=(sample*losses).mean()+.035*weights.square().sum()
        loss.backward()
        opt.step()
        with torch.no_grad():weights.clamp_(-MAX_WEIGHT,MAX_WEIGHT)
    return (tuple(float(x) for x in weights.detach()[0]),
            tuple(float(x) for x in weights.detach()[1])),counts


def _sum_features(data,selected):
    result=[0.0]*DIM
    for j in selected:
        o=data.case.options[j]
        if o.kind in (ACTOR,ACTION):
            width=o.end-o.start
            for k,v in enumerate(data.features[j]):
                result[k]+=width*v
    return result


def _train_structured(train,epochs=24):
    """Global structured-hinge updates against reachable full gold object sets.

    The loss-augmented Viterbi answer is the hardest predicted set. The oracle
    path is the maximum reachable nonoverlapping set of gold objects. Each
    update minimizes full-set violation, not local candidate classification.
    """
    w=[0.0]*DIM
    violations=0; unrecoverable=0
    targets=[]
    for data in train:
        oracle=_viterbi(data,[0.0]*len(data.case.options),oracle_gold=data.case.gold)
        recovered={data.case.options[j].char_key for j in oracle}
        unrecoverable+=len(data.case.gold-recovered)
        targets.append(oracle)
    for epoch in range(epochs):
        rate=.060/(1.+epoch/10.)
        for data,oracle in zip(train,targets):
            current=(tuple(w),)
            base=_scores(data,'structured_set_maxmargin',current)
            hard=_viterbi(data,base,loss_gold=data.case.gold,delta=.85)
            if set(hard)==set(oracle):
                w=[v*(1.-.0005) for v in w]
                continue
            # Subgradient of structured hinge: hard competitor minus oracle set.
            bad=_sum_features(data,hard)
            good=_sum_features(data,oracle)
            denom=max(1.0,math.sqrt(len(hard)+len(oracle)))
            for k in range(DIM):
                w[k]=max(-MAX_WEIGHT,min(MAX_WEIGHT,
                       w[k]*(1.-.0005)-rate*(bad[k]-good[k])/denom))
            violations+=1
    return (tuple(w),),{'epochs':epochs,'structured_violations':violations,
                        'unreachable_gold_objects_in_train':unrecoverable}


def _fit(train):
    b,bd=_train_boundary(train)
    j,jd=_train_joint(train)
    s,sd=_train_structured(train)
    weights={CONTROL:None,'whole_span_boundary':b,
             'joint_type_admission':j,'structured_set_maxmargin':s}
    return weights,{'whole_span_boundary':bd,'joint_type_admission':jd,'structured_set_maxmargin':sd}


def _metrics(cases,weights):
    record={arm:[(_predict(c,arm,weights[arm]),c.case.gold) for c in cases] for arm in ARMS}
    return {arm:_summarize(rows) for arm,rows in record.items()},record


def _choose(metrics):
    control=metrics[CONTROL]
    viable=[CONTROL]
    for arm in ARMS[1:]:
        candidate=metrics[arm]
        if (candidate['exact_prompts']>=control['exact_prompts'] and
            candidate['f1']>=control['f1']-1.e-12 and
            candidate['precision']>=control['precision']-.005 and
            all(candidate['by_kind'][kind]['recall']+1.e-12>=control['by_kind'][kind]['recall']
                for kind in REQUIRED_KINDS) and
            (candidate['exact_prompts']>control['exact_prompts'] or
             candidate['f1']>control['f1']+1.e-9)):
            viable.append(arm)
    return max(viable,key=lambda arm:(metrics[arm]['exact_prompts'],
            metrics[arm]['f1'],metrics[arm]['precision'],-ARMS.index(arm)))



def _oracle_candidate_ceiling(cases):
    """POST-HOC only: best simultaneously realizable gold candidates in lattice.

    This is a label-assisted upper bound on candidate *coverage*, not an
    inference decoder and not a generalization or independent-test score.
    """
    pairs=[]
    for data in cases:
        choices=_viterbi(data,[0.0]*len(data.case.options),
                         oracle_gold=data.case.gold)
        supported={data.case.options[j].char_key for j in choices}
        pairs.append((supported,data.case.gold))
    return _summarize(pairs)

def _changes(records):
    return {arm:admission._changes(records[CONTROL],records[arm]) for arm in ARMS[1:]}


def _details(cases,weights,selected):
    result=[]
    for c in cases:
        before=c.case.control
        gold=c.case.gold
        row={'split':c.case.split,'model':c.case.model,'family':c.case.family,
             'prompt_sha256':c.case.prompt_hash,'gold_count':len(gold),
             'control_exact':before==gold,'selected':selected,
             'selected_exact':_predict(c,selected,weights[selected])==gold,
             'arm_changes':{}}
        for arm in ARMS[1:]:
            after=_predict(c,arm,weights[arm])
            row['arm_changes'][arm]={
                'recovered_correct':[list(v) for v in sorted((after&gold)-(before&gold))],
                'lost_correct':[list(v) for v in sorted((before&gold)-(after&gold))],
                'introduced_false':[list(v) for v in sorted((after-gold)-(before-gold))],
                'removed_false':[list(v) for v in sorted((before-gold)-(after-gold))],
                'exact':after==gold}
        result.append(row)
    return result


def _require_previous(path,entries,dataset_hash,partitions,challenge_length):
    file=path/'report.json'
    report=json.loads(file.read_text(encoding='utf8'))
    lineage=report.get('lineage',{})
    expected={k:e['manifest']['head_sha256'] for k,e in entries.items()}
    if (report.get('schema')!=previous.SCHEMA or
        report.get('status')!='EXPLORATORY_LEARNED_INTERFACE_NOT_CERTIFIED' or
        lineage.get('dataset_sha256')!=dataset_hash or
        lineage.get('checkpoint_sha256')!=expected or
        report.get('selected',{}).get('cutover')!=CONTROL or
        report.get('selected',{}).get('legacy')!='pairwise_competition' or
        report.get('design',{}).get('cohorts')!={k:len(v) for k,v in partitions.items()} or
        report.get('design',{}).get('historical_challenge_cases')!=challenge_length or
        any(report.get(k) is not False for k in
            ('g1_head_weights_modified','production_promotion','g2_unlocked',
             'sealed_test_used','certification_authorized'))):
        raise ValueError('G1_0074_PREVIOUS_LINEAGE_OR_GATE_DRIFT')
    return report,sha256(file)


def _check_phase_parity(reference,phase,model,metrics):
    expected=reference['phases'][phase][model]['arms'][CONTROL]
    for key in ('cases','correct','expected','predicted','exact_prompts','false_positive_objects'):
        if metrics[CONTROL][key]!=expected[key]:
            raise ValueError(f'G1_0074_CONTROL_DRIFT:{phase}:{model}:{key}')
    for kind in REQUIRED_KINDS:
        if metrics[CONTROL]['by_kind'][kind]!=expected['by_kind'][kind]:
            raise ValueError(f'G1_0074_CONTROL_KIND_DRIFT:{phase}:{model}:{kind}')



def _capture(rows,heads,model,tok,*,device,max_length,prior,split):
    """Same frozen capture and exact G1-0072 parity as 0073, own progress tag."""
    from tools import nanojev_gamer_v1_backbone_first as backbone
    from tools.nanojev_gamer_v1_g1_decoder_frob import _char_set
    result={name:[] for name in heads}
    for i,row in enumerate(rows,1):
        prepared=backbone.prepare_prompt(tok,row['prompt'],max_input_tokens=max_length)
        with torch.inference_mode():
            hidden=backbone.frozen_context(model,prepared,device=device)
            outputs={name:head(hidden.to(device=device,dtype=torch.float32),
                eligible=prepared['eligible'].to(device),active_stage='G1')
                for name,head in heads.items()}
        frozen={}
        for name,output in outputs.items():
            options,control,n=previous._lattice(output['span_logits'],
                output['span_valid'],prepared,prior[name])
            original,_=complete._decode(output['span_logits'],
                output['span_valid'],prior[name])
            if control!=original['action_rescue_2p00_cond0p50']:
                raise ValueError('G1_0074_0072_CONTROL_DRIFT')
            frozen[name]=(options,control,n)
        # Labels materialized only after all label-blind candidate structures.
        gold={(s['start'],s['end'],s['kind']) for s in row['object_spans']}
        for name,(options,control,n) in frozen.items():
            case=previous.Case(name,split,row['family'],hashlib.sha256(
                row['prompt'].encode('utf8')).hexdigest(),n,options,
                _char_set(control,prepared),gold,control)
            result[name].append(_encode(case))
        if i==1 or i%10==0 or i==len(rows):
            print(f'G1_STRUCTURAL_{split.upper()} {i}/{len(rows)} legacy+cutover frozen',flush=True)
    return result

def _load_external(independent_dataset,dataset,cohorts):
    if independent_dataset is None:return None,None
    from tools import nanojev_gamer_v1_graph_curriculum as cur
    if Path(independent_dataset).resolve()==Path(dataset).resolve():
        raise ValueError('G1_0074_EXTERNAL_IDENTICAL_SOURCE')
    rows=cur.load_rows(independent_dataset)
    extra=[r for r in rows if r['split'] in ('dev','challenge')]
    current={hashlib.sha256(r['prompt'].encode('utf8')).hexdigest()
             for group in cohorts.values() for r in group}
    hashes=[hashlib.sha256(r['prompt'].encode('utf8')).hexdigest() for r in extra]
    if not extra or len(hashes)!=len(set(hashes)) or set(hashes)&current:
        raise ValueError('G1_0074_EXTERNAL_PROMPT_OVERLAP')
    return extra,sha256(independent_dataset)


def run(*,dataset,legacy,cutover,learned_reference,output,device='cuda',max_length=2048,
        independent_dataset=None):
    from tools import nanojev_gamer_v1_graph_curriculum as cur
    from tools import nanojev_gamer_v1_backbone_first as backbone
    dest=Path(output).resolve()
    ref=Path(learned_reference).resolve()
    if dest.exists():raise FileExistsError('G1_0074_IMMUTABLE_OUTPUT')
    if dest==ref or ref in dest.parents:raise ValueError('G1_0074_OUTPUT_IN_REFERENCE')
    if max_length<1:raise ValueError('G1_0074_BAD_LENGTH')
    entries,ds_hash,cohorts=_checked_preflight(dataset,legacy,cutover,output)
    partitions=previous._partition(cohorts['dev'])
    earlier,ref_hash=_require_previous(ref,entries,ds_hash,partitions,len(cohorts['challenge']))
    extra,external_hash=_load_external(independent_dataset,dataset,cohorts)
    heads={name:cur._load_checkpoint(e['path'],device=device)[0].eval() for name,e in entries.items()}
    manifest=entries['legacy']['manifest']
    model,tok=backbone.model_components(manifest['model_id'],manifest['source_adapter'],device)
    prior=earlier['design']['per_model_0072_control']
    captured={}
    phase_rows={**partitions,'challenge':cohorts['challenge']}
    if extra is not None:phase_rows['external']=extra
    for phase,rows in phase_rows.items():
        captured[phase]=_capture(rows,heads,model,tok,device=device,max_length=max_length,
                                 prior=prior,split=phase)
        for name,batch in captured[phase].items():
            baseline={CONTROL:_summarize([(c.case.control,c.case.gold) for c in batch])}
            if phase in earlier['phases']:
                _check_phase_parity(earlier,phase,name,baseline)
    trained={};training_ledger={}
    for name in entries:
        trained[name],training_ledger[name]=_fit(captured['train'][name])
    selection={name:_metrics(captured['selection'][name],trained[name])[0] for name in entries}
    selected={name:_choose(selection[name]) for name in entries}
    print('G1_STRUCTURAL_SELECTION '+json.dumps(selected,sort_keys=True),flush=True)
    phases={};case_rows=[]
    for phase in phase_rows:
        phases[phase]={}
        for name in entries:
            batch=captured[phase][name]
            results,records=_metrics(batch,trained[name])
            phases[phase][name]={'arms':results,'selected':selected[name],
                'selected_metrics':results[selected[name]],'control_metrics':results[CONTROL],
                'changes_vs_control':_changes(records),
                'previous_0073_selected_metrics':(earlier['phases'][phase][name]['selected_metrics']
                    if phase in earlier['phases'] else None),
                'oracle_candidate_ceiling_diagnostic_not_inference':_oracle_candidate_ceiling(batch)}
            case_rows.extend(_details(batch,trained[name],selected[name]))
    retrospective={}
    for name in entries:
        merged=sum((captured[phase][name] for phase in ('train','selection','audit')),[])
        metrics,records=_metrics(merged,trained[name])
        retrospective[name]={'arms':metrics,'selected':selected[name],
            'selected_metrics':metrics[selected[name]],
            'control_metrics':metrics[CONTROL],
            'changes_vs_control':_changes(records),
            'previous_0073_selected_metrics':earlier['full_dev_retrospective_not_holdout'][name]['selected_metrics'],
            'oracle_candidate_ceiling_diagnostic_not_inference':_oracle_candidate_ceiling(merged),
            'in_sample_includes_training_and_selection':True}
        earlier_full=earlier['full_dev_retrospective_not_holdout'][name]['arms'][CONTROL]
        for key in ('cases','correct','expected','predicted','exact_prompts','false_positive_objects'):
            if metrics[CONTROL][key]!=earlier_full[key]:
                raise ValueError('G1_0074_FULL_DEV_DRIFT:'+name+':'+key)
    # Recheck every mutable input before writing. No experiment can overwrite
    # predecessor reports, head checkpoints, or source-adapter state.
    if sha256(dataset)!=ds_hash or sha256(ref/'report.json')!=ref_hash:
        raise ValueError('G1_0074_INPUT_DRIFT')
    if extra is not None and sha256(independent_dataset)!=external_hash:
        raise ValueError('G1_0074_EXTERNAL_DRIFT')
    if backbone.adapter_fingerprint(manifest['source_adapter'])!=manifest['source_adapter_sha256']:
        raise ValueError('G1_0074_ADAPTER_DRIFT')
    for name,e in entries.items():
        if sha256(Path(e['path'])/cur.HEAD_FILE)!=e['manifest']['head_sha256']:
            raise ValueError('G1_0074_CHECKPOINT_DRIFT')
    report={'schema':SCHEMA,'status':'EXPLORATORY_STRUCTURAL_LEARNING_NOT_CERTIFIED',
        'design':{'arms':ARMS,'cohorts':{p:len(rows) for p,rows in partitions.items()},
            'historical_challenge_cases':len(cohorts['challenge']),
            'external_cases':len(extra or []),'selection':'DEV_SELECTION_ONLY',
            'competitors':{
                'whole_span_boundary':'whole-action logistic plus exact-span versus fragmented-overlap contrast',
                'joint_type_admission':'per-span null/actor/action cross-entropy with separate class corrections',
                'structured_set_maxmargin':'loss-augmented global interval selection against reachable gold object sets'},
            'control':'exact G1-0072 action_rescue_2p00_cond0p50 for both heads',
            'legacy_previous_0073_selected':'pairwise_competition (context only, not control)',
            'audit_not_used_for_training_or_selection':True,
            'challenge_previously_inspected':True,
            'full_dev_not_holdout':True,
            'external_provenance_independence_not_verified':True,
            'oracle_excluded_from_inference':True},
        'lineage':{'dataset_sha256':ds_hash,'learned_reference_sha256':ref_hash,
            'source_adapter_sha256':manifest['source_adapter_sha256'],
            'checkpoint_sha256':{name:e['manifest']['head_sha256'] for name,e in entries.items()},
            'external_dataset_sha256':external_hash},
        'training_ledger':training_ledger,
        'interface_weights':{name:{arm:([list(w) for w in value] if value is not None else None)
             for arm,value in weights.items()} for name,weights in trained.items()},
        'selected':selected,'phases':phases,
        'full_dev_retrospective_not_holdout':retrospective,
        'g1_head_weights_modified':False,'interface_training_performed':True,
        'production_promotion':False,'g2_unlocked':False,'sealed_test_used':False,
        'certification_authorized':False}
    dest.mkdir(parents=True,exist_ok=False)
    (dest/'report.json').write_text(json.dumps(report,sort_keys=True,indent=2,allow_nan=False)+'\n',encoding='utf8')
    with (dest/'cases.jsonl').open('x',encoding='utf8') as f:
        for row in case_rows:f.write(json.dumps(row,sort_keys=True,ensure_ascii=False)+'\n')
    print('G1_STRUCTURAL_RESULT '+json.dumps({name:{
        'selected':selected[name],
        'selection_exact':phases['selection'][name]['selected_metrics']['exact_prompts'],
        'audit_exact':phases['audit'][name]['selected_metrics']['exact_prompts'],
        'audit_control_exact':phases['audit'][name]['control_metrics']['exact_prompts'],
        'challenge_exact':phases['challenge'][name]['selected_metrics']['exact_prompts'],
        'full_dev_exact_retrospective':retrospective[name]['selected_metrics']['exact_prompts'],
        'full_dev_control_exact':retrospective[name]['control_metrics']['exact_prompts']}
        for name in entries},sort_keys=True),flush=True)
    return report


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('dataset','legacy','cutover','learned-reference','output'):
        parser.add_argument('--'+name,required=True)
    parser.add_argument('--device',default='cuda')
    parser.add_argument('--max-length',type=int,default=2048)
    parser.add_argument('--independent-dataset')
    run(**vars(parser.parse_args(argv)))


if __name__=='__main__':main()
