"""G1-0073: competing LEARNED admission/selection interfaces, frozen G1 heads.

Three distinct supervised objectives train *interface-only* deterministic CPU
coefficients: pointwise class admission, pairwise local preference, and structured
interval max-margin. Frozen G1-0072 predictions are a mandatory control. Each
head has separate learned interface weights. Training is dev-train only; dev-
selection picks a research arm; dev-audit and previously inspected challenge are
read ONLY afterward. No training/checkpoint/gate mutations or certification.
"""
from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path

import torch
import torch.nn.functional as F

from tools.nanojev_gamer_v1_graph_contract import SPAN_KINDS
from tools.nanojev_gamer_v1_graph_head import token_span_char_bounds
from tools.nanojev_gamer_v1_g1_decoder_frob import _char_set
from tools.nanojev_gamer_v1_g1_oracle_decoder_cutover_frob import _checked_preflight, _summarize, sha256
from tools import nanojev_gamer_v1_g1_admission_bakeoff_0070 as admission
from tools import nanojev_gamer_v1_g1_complete_object_bakeoff_0072 as complete

SCHEMA='nanojev.gamer.graph.g1.learned-interface-bakeoff-0073.v1'
CONTROL='g1_0072_frozen_control'
ARMS=(CONTROL,'pointwise_admission','pairwise_competition','structured_interval','pointwise_pairwise_ensemble')
ACTOR=SPAN_KINDS.index('actor')+1
ACTION=SPAN_KINDS.index('action')+1
REQUIRED_KINDS=('actor','action','quantity','outcome')
DIM=11
MAX_OPTIONS=4096


@dataclass(frozen=True)
class Option:
    start:int
    end:int  # exclusive
    kind:int
    base:float  # 0072-score per token (already positive for original candidates)
    features:tuple[float,...]
    char_key:tuple|None
    anchored:bool


@dataclass
class Case:
    model:str
    split:str
    family:str
    prompt_hash:str
    n_tokens:int
    options:list[Option]
    control:set
    gold:set
    control_prediction:list


def _feature(*,margin,p_cond,width,n,first,kind,original,base,top):
    """Bounded deterministic features; no label-dependent statistics."""
    return (1.,max(-2.,min(3.,margin))/3., 2.*p_cond-1.,
            min(width,32)/16., min(width,32)**.5/6., first/max(1,n),
            float(kind==ACTOR),float(kind==ACTION),float(original),
            min(base,8.)/8.,float(top))


def _lattice(logits,valid,prepared,prior_selected):
    """Construct candidates from logits only. Gold/row never enters this call."""
    size,pool=admission._pool(logits,valid)
    prior=complete._prior_scored_candidates(size,pool,prior_selected)
    arm=next(a for a in complete.ARMS if a.name=='action_rescue_2p00_cond0p50')
    enriched=complete._add_action_options(prior,pool,arm)
    control=complete._decode_typed(enriched,arm)
    raw={}
    for row in enriched:
        for start,offset,k,score in row:
            raw[(start,offset,k)]=float(score)
    originals=frozenset(raw)
    # Do not drop any old eligible candidate or selected anchor.
    for start,offset,k,margin,_,cond,alternative,alt_margin in pool:
        if k in (ACTOR,ACTION) and margin>=-3. and cond>=.25:
            raw.setdefault((start,offset,k),-1.0+.25*(margin+3.))
        if alternative in (ACTOR,ACTION) and margin-alt_margin<=.75 and alt_margin>=-3.:
            alt_cond=cond*math.exp(max(-60.,min(0.,alt_margin-margin)))
            if alt_cond>=.20:
                raw.setdefault((start,offset,alternative),-1.0+.25*(alt_margin+3.))
    pool_by={(start,offset):(k,margin,cond,alternate,alt_margin)
             for start,offset,k,margin,_,cond,alternate,alt_margin in pool}
    options=[]
    for (start,offset,k),base in raw.items():
        if k not in (ACTOR,ACTION) and (start,offset,k) not in originals:
            continue
        if not 0<=start<size or not 0<=offset or start+offset>=size:
            raise ValueError('G1_0073_INVALID_OPTION')
        best,margin,cond,alt,alt_margin=pool_by[(start,offset)]
        is_top=(k==best)
        use_margin=margin if is_top else alt_margin
        use_cond=cond if is_top else cond*math.exp(max(-60.,min(0.,alt_margin-margin)))
        baseline=(start,offset,k) in originals
        span={'token_first':start,'token_last':start+offset,'kind':SPAN_KINDS[k-1]}
        chars=token_span_char_bounds(span,prepared)
        char_key=(*chars,SPAN_KINDS[k-1]) if chars is not None else None
        options.append(Option(start,start+offset+1,k,float(base),
            _feature(margin=use_margin,p_cond=use_cond,width=offset+1,n=size,
                     first=start,kind=k,original=baseline,base=base,top=is_top),
            char_key,baseline))
    options.sort(key=lambda x:(x.end,x.start,x.kind))
    if len(options)>MAX_OPTIONS:
        # Deterministic truncation: preserve every original candidate; choose
        # highest margin extended candidates without consulting labels.
        originals=[o for o in options if o.anchored]
        if len(originals)>MAX_OPTIONS:raise ValueError('G1_0073_BASELINE_CANDIDATE_OVERFLOW')
        new=sorted((o for o in options if not o.anchored),
                   key=lambda o:(-o.base,o.start,o.end,o.kind))
        options=sorted(originals+new[:MAX_OPTIONS-len(originals)],
                       key=lambda x:(x.end,x.start,x.kind))
    if len(options)>MAX_OPTIONS:raise ValueError('G1_0073_OPTION_OVERFLOW')
    return options,control,size


def _decode_options(options,n_tokens,weights=None,*,loss_aug_gold=None,loss_margin=0.):
    """Global nonoverlapping interval DP. Only score/coordinates at inference."""
    if weights is not None and (len(weights)!=DIM or any(not math.isfinite(float(x)) for x in weights)):
        raise ValueError('G1_0073_INVALID_INTERFACE_WEIGHTS')
    by_end=[[] for _ in range(n_tokens+1)]
    for index,o in enumerate(options):
        if o.start<0 or o.end>n_tokens or o.start>=o.end or not math.isfinite(o.base):
            raise ValueError('G1_0073_INVALID_LATTICE')
        learned=0.0 if weights is None or o.kind not in (ACTOR,ACTION) else sum(
            float(a)*float(b) for a,b in zip(weights,o.features))
        score=o.base+learned
        # Type-agnostic 0072 whole-span objective with the action-specific
        # objective of its fixed selected rule, including unchanged positive scores.
        gain=score*(o.end-o.start)
        if loss_aug_gold is not None:
            gain+=(-loss_margin if o.char_key in loss_aug_gold else loss_margin)
        if not math.isfinite(gain):raise ValueError('G1_0073_NONFINITE_GAIN')
        by_end[o.end].append((o.start,gain,index))
    dp=[0.0]*(n_tokens+1)
    back=[(0,None)]*(n_tokens+1)
    for end in range(1,n_tokens+1):
        value=dp[end-1]; choice=(end-1,None)
        for start,gain,index in by_end[end]:
            candidate=dp[start]+gain
            if gain>0 and candidate>value+1.e-8:
                value=candidate;choice=(start,index)
        dp[end]=value;back[end]=choice
    chosen=[];at=n_tokens
    while at:
        first,ix=back[at]
        if ix is not None:chosen.append(ix)
        at=first
    if len(chosen)>128:raise ValueError('G1_0073_PREDICTION_OVERFLOW')
    return list(reversed(chosen))


def _prediction(case,arm,weights):
    if arm==CONTROL:return set(case.control)
    ix=_decode_options(case.options,case.n_tokens,weights)
    return {case.options[j].char_key for j in ix if case.options[j].char_key is not None}


def _train_pointwise(cases,steps=130):
    feats=[];labels=[]
    for case in cases:
        for o in case.options:
            if o.kind in (ACTOR,ACTION) and o.char_key is not None:
                feats.append(o.features);labels.append(float(o.char_key in case.gold))
    if not feats or not 0<sum(labels)<len(labels):raise ValueError('G1_0073_POINTWISE_CLASS_COLLAPSE')
    X=torch.tensor(feats,dtype=torch.float64);Y=torch.tensor(labels,dtype=torch.float64)
    w=torch.zeros(DIM,dtype=torch.float64,requires_grad=True)
    opt=torch.optim.Adam([w],lr=.035)
    pos=Y>0
    for _ in range(steps):
        opt.zero_grad()
        logits=X@w
        loss=.5*F.softplus(-logits[pos]).mean()+.5*F.softplus(logits[~pos]).mean()+.04*w.square().sum()
        loss.backward();opt.step()
    return tuple(float(x) for x in w.detach())


def _train_pairwise(cases,steps=130):
    differences=[]
    for case in cases:
        positive=[o for o in case.options if o.kind in (ACTOR,ACTION) and o.char_key in case.gold]
        wrong=[o for o in case.options if o.kind in (ACTOR,ACTION) and o.char_key not in case.gold]
        for p in positive:
            # Fixed hardest local competitors under original neural scores;
            # gold only determines supervision, never inference eligibility.
            rivals=sorted((n for n in wrong if n.start<p.end and p.start<n.end),
                          key=lambda n:(-n.base,n.start,n.end,n.kind))[:3]
            for r in rivals:
                differences.append((tuple(a-b for a,b in zip(p.features,r.features)),
                                   (p.base*(p.end-p.start)-r.base*(r.end-r.start))/4.))
    if not differences:raise ValueError('G1_0073_PAIRWISE_EMPTY')
    X=torch.tensor([x[0] for x in differences],dtype=torch.float64)
    B=torch.tensor([x[1] for x in differences],dtype=torch.float64)
    w=torch.zeros(DIM,dtype=torch.float64,requires_grad=True)
    opt=torch.optim.Adam([w],lr=.025)
    for _ in range(steps):
        opt.zero_grad()
        loss=F.softplus(1.-B-X@w).mean()+.06*w.square().sum()
        loss.backward();opt.step()
    return tuple(float(x) for x in w.detach())


def _train_structured(cases,epochs=24):
    """Deterministic structured perceptron, loss-augmented interval competition."""
    w=[0.]*DIM
    for epoch in range(epochs):
        rate=.025/(1+epoch/12.)
        for case in cases:
            selected=_decode_options(case.options,case.n_tokens,w,
                                     loss_aug_gold=case.gold,loss_margin=.40)
            selected_set=set(selected)
            # Only reachable gold candidates participate in the oracle reference.
            available={}
            for j,o in enumerate(case.options):
                if o.kind in (ACTOR,ACTION) and o.char_key in case.gold:
                    # Only one representation of each gold object; choose
                    # a stable highest-base option if tokenization aliases occur.
                    prior=available.get(o.char_key)
                    if prior is None or o.base>case.options[prior].base:
                        available[o.char_key]=j
            gold_index=set(available.values())
            for j in gold_index-selected_set:
                o=case.options[j]
                for d in range(DIM):w[d]+=rate*o.features[d]
            for j in selected_set-gold_index:
                o=case.options[j]
                if o.kind in (ACTOR,ACTION):
                    for d in range(DIM):w[d]-=rate*o.features[d]
            w=[max(-4.,min(4.,x*(1.-.0005))) for x in w]
    return tuple(w)


def _competitors(train):
    point=_train_pointwise(train)
    pair=_train_pairwise(train)
    structured=_train_structured(train)
    ensemble=tuple((a+b)*.5 for a,b in zip(point,pair))
    return {CONTROL:None,'pointwise_admission':point,'pairwise_competition':pair,
            'structured_interval':structured,'pointwise_pairwise_ensemble':ensemble}


def _partition(rows):
    """Stratified 60% train / 20% selection / 20% audit, fixed hash order."""
    families={}
    for row in rows:
        families.setdefault(row['family'],[]).append(row)
    result={'train':[],'selection':[],'audit':[]}
    for name,subset in sorted(families.items()):
        ordered=sorted(subset,key=lambda r:hashlib.sha256(
            ('g1-0073-partition-v1:'+r['prompt']).encode('utf8')).hexdigest())
        n=len(ordered)
        a=int(.6*n);b=int(.8*n)
        if a<2 or b-a<2 or n-b<2:
            raise ValueError('G1_0073_COHORT_TOO_SMALL:'+name)
        for key,chunk in zip(('train','selection','audit'),
                             (ordered[:a],ordered[a:b],ordered[b:])):
            result[key].extend(chunk)
    hashes=[hashlib.sha256(r['prompt'].encode('utf8')).hexdigest()
            for rows in result.values() for r in rows]
    if len(hashes)!=len(set(hashes)):raise ValueError('G1_0073_PARTITION_LEAK')
    return result


def _require_complete_reference(ref,entries,ds_sha,cohorts):
    doc=json.loads((ref/'report.json').read_text(encoding='utf8'))
    if (doc.get('schema')!=complete.SCHEMA or
        doc.get('status')!='FROZEN_COMPLETE_OBJECT_BAKEOFF_NOT_CERTIFIED' or
        doc.get('lineage',{}).get('dataset_sha256')!=ds_sha or
        doc.get('lineage',{}).get('checkpoint_sha256')!={
            model:e['manifest']['head_sha256'] for model,e in entries.items()} or
        doc.get('design',{}).get('dev_cases')!=len(cohorts['dev']) or
        doc.get('design',{}).get('historical_challenge_cases')!=len(cohorts['challenge']) or
        any(doc.get(key) is not False for key in ('weights_modified','training_performed',
            'production_promotion','g2_unlocked','sealed_test_used','certification_authorized'))):
        raise ValueError('G1_0073_REFERENCE_LINEAGE')
    for model in entries:
        if doc['models'][model]['dev']['selected']!='action_rescue_2p00_cond0p50' or \
           doc['models'][model]['historical_challenge']['selected']!='action_rescue_2p00_cond0p50':
            raise ValueError('G1_0073_UNEXPECTED_PREVIOUS_CONTROL')
    return doc,sha256(ref/'report.json')


def _capture(rows,heads,model,tok,*,device,max_length,prior,split):
    from tools import nanojev_gamer_v1_backbone_first as backbone
    result={name:[] for name in heads}
    for i,row in enumerate(rows,1):
        prepared=backbone.prepare_prompt(tok,row['prompt'],max_input_tokens=max_length)
        with torch.inference_mode():
            hidden=backbone.frozen_context(model,prepared,device=device)
            outputs={name:head(hidden.to(device=device,dtype=torch.float32),
                        eligible=prepared['eligible'].to(device),active_stage='G1')
                     for name,head in heads.items()}
        frozen_options={}
        for name,output in outputs.items():
            options,ctl,n=_lattice(output['span_logits'],output['span_valid'],
                                    prepared,prior[name])
            # Mandatory parity with exact 0072 selected result (not with 0071).
            actual, _=complete._decode(output['span_logits'],output['span_valid'],prior[name])
            if ctl!=actual['action_rescue_2p00_cond0p50']:
                raise ValueError('G1_0073_0072_CONTROL_DRIFT')
            frozen_options[name]=(options,ctl,n)
        # Supervision is materialized only AFTER all inference candidates and
        # exact frozen-control predictions have been generated.
        gold={(s['start'],s['end'],s['kind']) for s in row['object_spans']}
        for name,(options,ctl,n) in frozen_options.items():
            result[name].append(Case(name,split,row['family'],hashlib.sha256(
                row['prompt'].encode('utf8')).hexdigest(),n,options,
                _char_set(ctl,prepared),gold,ctl))
        if i==1 or i%10==0 or i==len(rows):
            print(f'G1_LEARNED_{split.upper()} {i}/{len(rows)} legacy+cutover frozen',flush=True)
    return result


def _metrics(cases,parameters):
    records={}
    predicted={}
    for name,weights in parameters.items():
        predictions=[_prediction(c,name,weights) for c in cases]
        records[name]=[(p,c.gold) for p,c in zip(predictions,cases)]
        predicted[name]=predictions
    return {name:_summarize(recs) for name,recs in records.items()},records,predicted


def _parity(reference,split,metrics):
    key='dev' if split=='dev' else 'historical_challenge'
    for name,arms in metrics.items():
        expected=reference['models'][name][key]['selected_metrics']
        observed=arms[CONTROL]
        for metric in ('correct','expected','predicted','exact_prompts','false_positive_objects'):
            if expected[metric]!=observed[metric]:
                raise ValueError(f'G1_0073_CONTROL_METRIC_DRIFT:{name}:{metric}')
        for kind in REQUIRED_KINDS:
            if observed['by_kind'][kind]!=expected['by_kind'][kind]:
                raise ValueError(f'G1_0073_CONTROL_KIND_DRIFT:{name}:{kind}')


def _select(val):
    base=val[CONTROL]
    options=[CONTROL]
    for name in ARMS[1:]:
        m=val[name]
        if (m['exact_prompts']>=base['exact_prompts'] and
            m['f1']>=base['f1']-1e-12 and
            m['precision']>=base['precision']-.005 and
            all(m['by_kind'].get(kind,{}).get('recall',0.)+1e-12>=
                base['by_kind'].get(kind,{}).get('recall',1.) for kind in REQUIRED_KINDS) and
            (m['exact_prompts']>base['exact_prompts'] or m['f1']>base['f1']+1e-9)):
            options.append(name)
    return max(options,key=lambda n:(val[n]['exact_prompts'],val[n]['f1'],
                      val[n]['precision'],-ARMS.index(n)))


def _detail(cases,parameters):
    details=[]
    for case in cases:
        before=case.control;gold=case.gold
        row={'split':case.split,'model':case.model,'family':case.family,
             'prompt_sha256':case.prompt_hash,'gold_count':len(gold),
             'control_exact':before==gold,'arm_changes':{}}
        for name in ARMS[1:]:
            after=_prediction(case,name,parameters[name])
            row['arm_changes'][name]={
                'recovered_correct':[list(s) for s in sorted((after&gold)-(before&gold))],
                'lost_correct':[list(s) for s in sorted((before&gold)-(after&gold))],
                'introduced_false':[list(s) for s in sorted((after-gold)-(before-gold))],
                'removed_false':[list(s) for s in sorted((before-gold)-(after-gold))],
                'exact':after==gold}
        details.append(row)
    return details


def run(*,dataset,legacy,cutover,complete_reference,output,device='cuda',max_length=2048,
        independent_dataset=None):
    from tools import nanojev_gamer_v1_graph_curriculum as cur
    from tools import nanojev_gamer_v1_backbone_first as backbone
    dest=Path(output).resolve();ref=Path(complete_reference).resolve()
    if Path(output).exists():raise FileExistsError('G1_0073_IMMUTABLE_OUTPUT')
    if dest==ref or ref in dest.parents:raise PermissionError('G1_0073_OUTPUT_IN_REFERENCE')
    if max_length<1:raise ValueError('G1_0073_BAD_LENGTH')
    entries,ds_hash,cohorts=_checked_preflight(dataset,legacy,cutover,output)
    previous,ref_hash=_require_complete_reference(ref,entries,ds_hash,cohorts)
    prior=previous['design']['per_model_control']
    partitions=_partition(cohorts['dev'])
    # Independent data is optional, must have DISJOINT exact prompts. Its
    # provenance remains user supplied; this tool cannot certify independence.
    extra=None; extra_hash=None
    if independent_dataset:
        if Path(independent_dataset).resolve()==Path(dataset).resolve():
            raise ValueError('G1_0073_INDEPENDENT_DATASET_EQUALS_DEVELOPMENT')
        extra_hash=sha256(independent_dataset)
        alternate=cur.load_rows(independent_dataset)
        existing={hashlib.sha256(r['prompt'].encode('utf8')).hexdigest() for rs in cohorts.values() for r in rs}
        extra=[r for r in alternate if r['split'] in ('dev','challenge')]
        h=[hashlib.sha256(r['prompt'].encode('utf8')).hexdigest() for r in extra]
        if not extra or len(h)!=len(set(h)) or existing.intersection(h):
            raise ValueError('G1_0073_INDEPENDENT_DATASET_OVERLAP')
    heads={name:cur._load_checkpoint(data['path'],device=device)[0].eval()
           for name,data in entries.items()}
    manifest=entries['legacy']['manifest']
    model,tok=backbone.model_components(manifest['model_id'],manifest['source_adapter'],device)
    caches={}
    for segment,rows in [('train',partitions['train']),('selection',partitions['selection']),
                         ('audit',partitions['audit']),('challenge',cohorts['challenge'])]:
        caches[segment]=_capture(rows,heads,model,tok,device=device,max_length=max_length,
                                 prior=prior,split=segment)
    if extra:
        caches['external']=_capture(extra,heads,model,tok,device=device,max_length=max_length,
                                    prior=prior,split='external')
    # Full DEV control parity is checked before any result can be trusted.
    full_dev={name:{} for name in entries}
    for name in entries:
        all_dev=sum((caches[split][name] for split in ('train','selection','audit')),[])
        full_dev[name]={CONTROL:_summarize([(c.control,c.gold) for c in all_dev])}
    _parity(previous,'dev',full_dev)
    historic={name:{CONTROL:_summarize([(c.control,c.gold) for c in caches['challenge'][name]])}
              for name in entries}
    _parity(previous,'challenge',historic)
    training={name:_competitors(caches['train'][name]) for name in entries}
    selection={name:_metrics(caches['selection'][name],training[name])[0] for name in entries}
    chosen={name:_select(selection[name]) for name in entries}
    print('G1_LEARNED_SELECTION '+json.dumps(chosen,sort_keys=True),flush=True)
    phases={}
    cases=[]
    for phase in ('train','selection','audit','challenge')+tuple(['external'] if extra else []):
        phases[phase]={}
        for name in entries:
            metrics,rec,_=_metrics(caches[phase][name],training[name])
            phases[phase][name]={'arms':metrics,'selected':chosen[name],
                'selected_metrics':metrics[chosen[name]],'control_metrics':metrics[CONTROL],
                'changes_vs_control':{a:admission._changes(rec[CONTROL],rec[a]) for a in ARMS[1:]}}
            cases.extend(_detail(caches[phase][name],training[name]))
    # Full development is a retrospective diagnostic (includes training and
    # selection examples), never a held-out score or a certification gate.
    full_dev_after_fit={}
    for name in entries:
        combined=sum((caches[phase][name] for phase in ('train','selection','audit')),[])
        scores,records,_=_metrics(combined,training[name])
        full_dev_after_fit[name]={'arms':scores,'selected':chosen[name],
            'selected_metrics':scores[chosen[name]],
            'control_metrics':scores[CONTROL],
            'changes_vs_control':{a:admission._changes(records[CONTROL],records[a])
                                  for a in ARMS[1:]},
            'in_sample_includes_train_and_selection':True}
    # Integrity checks before writing anything, including source-adapter and reference.
    if sha256(dataset)!=ds_hash or sha256(ref/'report.json')!=ref_hash:
        raise ValueError('G1_0073_INPUT_DRIFT')
    if extra and sha256(independent_dataset)!=extra_hash:
        raise ValueError('G1_0073_INDEPENDENT_SOURCE_DRIFT')
    if backbone.adapter_fingerprint(manifest['source_adapter'])!=manifest['source_adapter_sha256']:
        raise ValueError('G1_0073_ADAPTER_DRIFT')
    for name,e in entries.items():
        if sha256(Path(e['path'])/cur.HEAD_FILE)!=e['manifest']['head_sha256']:
            raise ValueError('G1_0073_HEAD_DRIFT')
    result={'schema':SCHEMA,'status':'EXPLORATORY_LEARNED_INTERFACE_NOT_CERTIFIED',
        'design':{'arms':ARMS,'competitors':{
             'pointwise_admission':'balanced logistic BCE on typed candidates',
             'pairwise_competition':'local overlapping positive-negative soft ranking',
             'structured_interval':'loss-augmented interval structured perceptron',
             'pointwise_pairwise_ensemble':'equal-weight unfitted combination'},
            'cohorts':{name:len(rows) for name,rows in partitions.items()},
            'historical_challenge_cases':len(cohorts['challenge']),
            'external_cases':len(extra or []),'per_model_0072_control':prior,
            'select_on':'DEV_SELECTION_ONLY','audit_excluded_from_fit_and_selection':True,
            'challenge_previously_inspected':True,
            'external_provenance_independence_not_verified':True,
            'training_oracle_excluded_from_inference':True},
        'lineage':{'dataset_sha256':ds_hash,'complete_reference_sha256':ref_hash,
            'source_adapter_sha256':manifest['source_adapter_sha256'],
            'checkpoint_sha256':{name:data['manifest']['head_sha256'] for name,data in entries.items()},
            'external_dataset_sha256':extra_hash},
        'interface_weights':{name:{arm:list(weights) if weights is not None else None
              for arm,weights in arms.items()} for name,arms in training.items()},
        'selected':chosen,'phases':phases,
        'full_dev_retrospective_not_holdout':full_dev_after_fit,
        'g1_head_weights_modified':False,'interface_training_performed':True,
        'production_promotion':False,'g2_unlocked':False,
        'sealed_test_used':False,'certification_authorized':False}
    dest.mkdir(parents=True,exist_ok=False)
    (dest/'report.json').write_text(json.dumps(result,sort_keys=True,indent=2,allow_nan=False)+'\n',encoding='utf8')
    with (dest/'cases.jsonl').open('x',encoding='utf8') as f:
        for row in cases:f.write(json.dumps(row,sort_keys=True,ensure_ascii=False)+'\n')
    print('G1_LEARNED_RESULT '+json.dumps({name:{'selected':chosen[name],
          'full_dev_exact_retrospective':full_dev_after_fit[name]['selected_metrics']['exact_prompts'],
          'full_dev_control_exact':full_dev_after_fit[name]['control_metrics']['exact_prompts'],
          'selection_exact':phases['selection'][name]['selected_metrics']['exact_prompts'],
          'audit_exact':phases['audit'][name]['selected_metrics']['exact_prompts'],
          'audit_control_exact':phases['audit'][name]['control_metrics']['exact_prompts'],
          'challenge_exact':phases['challenge'][name]['selected_metrics']['exact_prompts']}
          for name in entries},sort_keys=True),flush=True)
    return result


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    for key in ('dataset','legacy','cutover','complete-reference','output'):
        p.add_argument('--'+key,required=True)
    p.add_argument('--device',default='cuda')
    p.add_argument('--max-length',type=int,default=2048)
    p.add_argument('--independent-dataset',default=None)
    run(**vars(p.parse_args(argv)))


if __name__=='__main__':main()
