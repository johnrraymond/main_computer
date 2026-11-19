"""G1-0079: quantity-preserving structured learning, prospective sealed audit.

Prepare new disjoint TRAIN/SELECTION + sealed AUDIT.  Fit and select competing
interfaces WITHOUT reading audit answers; predict every audit prompt and lock
predictions.  Finally unseal and score exactly once.  G1 heads, Qwen, LoRA,
0077 evidence and 0078 evidence remain frozen.  Never promote or unlock G2.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path
import random

import torch

from tools import nanojev_gamer_v1_g1_reconstruction_reliability_0078 as reliability
from tools import nanojev_gamer_v1_g1_discrimination_trainability_bakeoff_0076 as previous
from tools import nanojev_gamer_v1_g1_sealed_generalization_0077 as sealed
from tools import nanojev_gamer_v1_g1_candidate_coverage_bakeoff_0075 as coverage
from tools import nanojev_gamer_v1_g1_structural_learning_bakeoff_0074 as structural
from tools.nanojev_gamer_v1_g1_oracle_decoder_cutover_frob import sha256, _summarize
from tools.nanojev_gamer_v1_graph_contract import SPAN_KINDS

SCHEMA='nanojev.gamer.graph.g1.quantity-preserving-bakeoff-0079.v1'
SEED=790079
CONTROL='frozen_0078_selected'
RAW_CONTINUED='historical_continued_64_unprotected'
PINNED_CONTINUED='pinned_historical_continued_64'
PINNED_ONEX='pinned_structured_1x'
PINNED_TWOX='pinned_quantity_margin_2x'
PINNED_FOURX='pinned_quantity_margin_4x'
ARMS=(CONTROL,RAW_CONTINUED,PINNED_CONTINUED,PINNED_ONEX,PINNED_TWOX,PINNED_FOURX)
TRAINABLE=(PINNED_ONEX,PINNED_TWOX,PINNED_FOURX)
SELECTABLE=(CONTROL,PINNED_CONTINUED,PINNED_ONEX,PINNED_TWOX,PINNED_FOURX)
Q='quantity'
PHASES=('train','selection','audit')
MODELS=sealed.REQUIRED_MODEL_NAMES


def _json(path):
    return json.loads(Path(path).read_text(encoding='utf8'))


def _rows(path):
    return [json.loads(s) for s in Path(path).read_text(encoding='utf8').splitlines() if s.strip()]


def _write_json(path, obj):
    Path(path).write_text(json.dumps(obj,sort_keys=True,ensure_ascii=False,
        indent=2,allow_nan=False)+'\n',encoding='utf8')


def _write_rows(path, rows):
    with Path(path).open('x',encoding='utf8') as handle:
        for obj in rows:
            handle.write(json.dumps(obj,sort_keys=True,ensure_ascii=False,
                                   allow_nan=False)+'\n')


def _hash(s):
    return hashlib.sha256(s.encode('utf8')).hexdigest()


def _require_prior(reliability_reference,seal_0077,dataset):
    rdir=Path(reliability_reference).resolve()
    old=_json(rdir/'report.json')
    if (old.get('schema')!=reliability.SCHEMA or
        old.get('status')!='RESEARCH_COMPARISON_NOT_CERTIFIED' or
        old.get('selected')!={'cutover':'fresh_staged_64','legacy':reliability.CONTROL} or
        old.get('new_cohorts')!={'train':96,'selection':32,'audit':64} or
        old.get('new_prompt_overlap')!=0 or
        old.get('g1_certified') is not False or
        old.get('g2_unlocked') is not False or
        old.get('head_weights_modified') is not False or
        old.get('production_promotion') is not False or
        old.get('original_0077_test_reused_for_selection') is not False or
        old.get('sealed_0077_cases_used_in_fitting_or_selection') is not False):
        raise ValueError('G1_0079_PREVIOUS_BAKEOFF_NOT_ELIGIBLE')
    sealed_dir,commit,old_sealed=sealed._load_seal(seal_0077)
    if (old['original_dataset_sha256']!=sha256(dataset) or
        old['generator_sha256']!=commit['generator_sha256'] or
        old['frozen_discrimination_report_sha256']!=commit['frozen_report_sha256']):
        raise ValueError('G1_0079_PREVIOUS_LINEAGE_DRIFT')
    casefile=rdir/'cases.jsonl'
    prior_cases=_rows(casefile)
    old_hashes=[]
    for phase,amount in [('train',96),('selection',32),('audit',64)]:
        for model in MODELS:
            subset=[x['prompt_sha256'] for x in prior_cases
                    if x.get('phase')==phase and x.get('model')==model]
            if len(subset)!=amount or len(set(subset))!=amount:
                raise ValueError('G1_0079_0078_CASE_COUNT_DRIFT')
            if model==MODELS[0]:old_hashes.extend(subset)
            elif subset!=old_hashes[-amount:]:
                raise ValueError('G1_0079_0078_MODEL_PROMPT_DRIFT')
    if _hash(''.join(old_hashes))!=old['new_prompt_digest']:
        raise ValueError('G1_0079_0078_CASE_DIGEST_DRIFT')
    from tools import nanojev_gamer_v1_graph_corpus as corpus
    if sha256(Path(corpus.__file__))!=commit['generator_sha256']:
        raise ValueError('G1_0079_GENERATOR_DRIFT')
    return old,commit,old_sealed,set(old_hashes),sha256(rdir/'report.json'),sha256(casefile)


def prepare(*,dataset,seal_0077,reliability_reference,output,seed=SEED,
            train=96,selection=32,audit=128):
    """Commit fresh audit answers BEFORE any 0079 fitting or model selection."""
    from tools import nanojev_gamer_v1_graph_corpus as corpus
    from tools import nanojev_gamer_v1_graph_curriculum as curriculum
    target=Path(output).resolve()
    if target.exists():raise FileExistsError('G1_0079_PREPARE_OUTPUT_EXISTS')
    if (seed in (sealed.DEFAULT_SEED,reliability.DEFAULT_SEED) or seed<0 or
        any(x<16 or x%2 for x in (train,selection,audit)) or
        audit<64):
        raise ValueError('G1_0079_INVALID_COHORT_CONFIG')
    old,commit,old_sealed,prior_hashes,old_digest,case_digest=_require_prior(
        reliability_reference,seal_0077,dataset)
    excluded={_hash(x['prompt']) for x in curriculum.load_rows(dataset)}
    excluded.update(p['prompt_sha256'] for p in old_sealed)
    excluded.update(prior_hashes)
    rng=random.Random(seed); partitions={}; seen=[]; counter=79_000_000
    for phase,count in [('train',train),('selection',selection),('audit',audit)]:
        made=[]
        for i in range(count):
            factory=corpus.encounter if i%2==0 else corpus.allocation
            for attempt in range(300):
                row=factory(rng,'test',counter);counter+=1
                digest=_hash(row['prompt'])
                if digest not in excluded:break
            else:raise ValueError('G1_0079_FRESH_GENERATOR_EXHAUSTED')
            excluded.add(digest);seen.append(digest)
            made.append({'prompt':row['prompt'],'family':row['family'],
                'prompt_sha256':digest,'object_spans':[
                    {'start':s['start'],'end':s['end'],'kind':s['kind']}
                    for s in row['object_spans']]})
        partitions[phase]=made
    audit_prompts=[{'case_id':f'prospective-{i:05d}','prompt':x['prompt'],
                    'family':x['family'],'prompt_sha256':x['prompt_sha256']}
                    for i,x in enumerate(partitions['audit'])]
    audit_gold=[{'case_id':p['case_id'],'family':p['family'],
                'prompt_sha256':p['prompt_sha256'],
                'gold':sorted([[s['start'],s['end'],s['kind']]
                               for s in x['object_spans']])}
                for p,x in zip(audit_prompts,partitions['audit'])]
    target.mkdir(parents=True,exist_ok=False)
    _write_rows(target/'train.jsonl',partitions['train'])
    _write_rows(target/'selection.jsonl',partitions['selection'])
    _write_rows(target/'audit.prompts.jsonl',audit_prompts)
    _write_rows(target/'audit.answers.SECRET.jsonl',audit_gold)
    record={'schema':SCHEMA,'status':'AUDIT_COMMITTED_NOT_PREDICTED',
        'protocol':'PRECOMMIT_FIT_SELECT_BLIND_PREDICT_SCORE_ONCE',
        'seed':seed,'cohorts':{x:len(partitions[x]) for x in PHASES},
        'original_dataset_sha256':sha256(dataset),
        'seal_0077_manifest_sha256':sha256(Path(seal_0077)/'manifest.json'),
        'reliability_report_sha256':old_digest,
        'reliability_cases_sha256':case_digest,
        'discrimination_report_sha256':commit['frozen_report_sha256'],
        'generator_sha256':commit['generator_sha256'],
        'runner_sha256':sha256(Path(__file__)),
        'train_sha256':sha256(target/'train.jsonl'),
        'selection_sha256':sha256(target/'selection.jsonl'),
        'audit_prompts_sha256':sha256(target/'audit.prompts.jsonl'),
        'committed_audit_answers_sha256':sha256(target/'audit.answers.SECRET.jsonl'),
        'prompt_hashes_sha256':_hash(''.join(seen)),
        'old_0077_0078_original_prompt_overlap':0,
        'audit_answers_used_in_fit_or_selection':False,
        'evaluation_is_certification':False,
        'distribution':'same author-generated TEST-vocabulary distribution',
        'g1_certified':False,'g2_unlocked':False}
    _write_json(target/'manifest.json',record)
    print('G1_0079_PREPARED '+json.dumps({'cohorts':record['cohorts'],
        'committed_audit_answers_sha256':record['committed_audit_answers_sha256'],
        'status':record['status']},sort_keys=True),flush=True)
    return record


def _load_prepared(seal_dir,dataset,seal_0077,reliability_reference):
    seal_dir=Path(seal_dir).resolve()
    record=_json(seal_dir/'manifest.json')
    old,commit,old_sealed,prior_hashes,old_digest,case_digest=_require_prior(
        reliability_reference,seal_0077,dataset)
    if (record.get('schema')!=SCHEMA or record.get('status')!='AUDIT_COMMITTED_NOT_PREDICTED' or
        record.get('runner_sha256')!=sha256(Path(__file__)) or
        record.get('original_dataset_sha256')!=sha256(dataset) or
        record.get('seal_0077_manifest_sha256')!=sha256(Path(seal_0077)/'manifest.json') or
        record.get('reliability_report_sha256')!=old_digest or
        record.get('reliability_cases_sha256')!=case_digest or
        record.get('discrimination_report_sha256')!=commit['frozen_report_sha256'] or
        record.get('generator_sha256')!=commit['generator_sha256'] or
        record.get('audit_answers_used_in_fit_or_selection') is not False or
        record.get('evaluation_is_certification') is not False):
        raise ValueError('G1_0079_PREPARED_LINEAGE_DRIFT')
    for name,field in [('train.jsonl','train_sha256'),('selection.jsonl','selection_sha256'),
                       ('audit.prompts.jsonl','audit_prompts_sha256')]:
        if sha256(seal_dir/name)!=record[field]:
            raise ValueError('G1_0079_PREPARED_FILE_CHANGED:'+name)
    train=_rows(seal_dir/'train.jsonl');select=_rows(seal_dir/'selection.jsonl')
    prompts=_rows(seal_dir/'audit.prompts.jsonl')
    if (len(train)!=record['cohorts']['train'] or
        len(select)!=record['cohorts']['selection'] or
        len(prompts)!=record['cohorts']['audit']):
        raise ValueError('G1_0079_PREPARED_CASE_COUNT')
    candidates=train+select+prompts
    hashes=[r['prompt_sha256'] for r in candidates]
    if (len(set(hashes))!=len(hashes) or
        record['prompt_hashes_sha256']!=_hash(''.join(hashes)) or
        set(hashes)&prior_hashes or
        set(hashes)&{p['prompt_sha256'] for p in old_sealed}):
        raise ValueError('G1_0079_PROSPECTIVE_PROMPT_DRIFT')
    for r in candidates:
        if _hash(r['prompt'])!=r['prompt_sha256'] or r['family'] not in ('encounter','allocation'):
            raise ValueError('G1_0079_BAD_PROSPECTIVE_PROMPT')
    # No reads, stat, or hashing of audit.answers.SECRET.jsonl in fitting.
    return record,train,select,prompts


def _load_model(bundle):
    model=previous.Discriminator()
    expected=model.state_dict()
    if not isinstance(bundle,dict) or sealed._dict_hash(bundle.get('parameters'))!=bundle.get('sha256'):
        raise ValueError('G1_0079_COEFFICIENT_HASH_DRIFT')
    p=bundle['parameters']
    if set(p)!=set(expected):raise ValueError('G1_0079_COEFFICIENT_KEYS')
    state={}
    for key,ref in expected.items():
        value=torch.tensor(p[key],dtype=ref.dtype)
        if (value.shape!=ref.shape or not bool(torch.isfinite(value).all()) or
            bool(torch.any(value.abs()>8.000001))):
            raise ValueError('G1_0079_COEFFICIENT_SHAPE_OR_RANGE')
        state[key]=value
    model.load_state_dict(state)
    return model.eval().requires_grad_(False)


def _models(report0078,report0076,name):
    initial=(sealed._load_coefficients(report0076,name) if
             report0078['selected'][name]==reliability.CONTROL else
             _load_model(report0078['learned_interface_parameters'][name]
                         [report0078['selected'][name]]))
    contender=_load_model(report0078['learned_interface_parameters'][name]
                          ['continued_structured_64'])
    return initial,contender


def _quantity(prediction):
    return {s for s in prediction if s[2]==Q}


def _baseline(data,weights,initial):
    return reliability._baseline(data,weights,initial)


def _guarded_scores(data,model,pinned):
    """Pin the control's quantity coordinates without oracle labels.

    The pin is imposed on token intervals, which are mapped to public
    character-span keys by the candidate lattice.  Other candidates remain
    scored by the learned interface and retain their gradients.
    """
    raw=previous._model_scores(data,model)
    options=data.added.case.options
    intervals_by_key={o.char_key:(o.start,o.end) for o in options if o.char_key in pinned}
    intervals=list(intervals_by_key.values())
    if set(intervals_by_key)!=pinned:
        raise ValueError('G1_0079_PINNED_QUANTITY_MISSING_FROM_LATTICE')
    replacement=[]
    for o in options:
        if o.char_key in pinned:
            replacement.append(1.e8)
        elif (o.kind==SPAN_KINDS.index(Q)+1 or
              any(o.start<b and a<o.end for a,b in intervals)):
            replacement.append(-1.e8)
        else:
            replacement.append(None)
    mask=torch.tensor([v is not None for v in replacement],dtype=torch.bool)
    values=torch.tensor([0.0 if v is None else v for v in replacement],dtype=raw.dtype)
    return torch.where(mask,values,raw)


def _predict(data,arm,weights,initial,continued,trained=None):
    reduced=reliability._budget(data,64)
    base=_baseline(reduced,weights,initial)
    if arm==CONTROL:return base
    model=continued if arm in (RAW_CONTINUED,PINNED_CONTINUED) else trained[arm]
    if arm==RAW_CONTINUED:
        return previous._predict(reduced,'staged_joint_64',weights,model)
    pinned=_quantity(base)
    with torch.no_grad():
        scores=_guarded_scores(reduced,model,pinned).tolist()
    positions=structural._viterbi(reduced.added,scores)
    selected={reduced.added.case.options[i].char_key for i in positions}
    if _quantity(selected)!=pinned:
        raise ValueError('G1_0079_QUANTITY_GUARD_FAILED')
    return selected


def _quality(train,weights,baseline,model):
    exact=correct=false=missing=0
    for d in train:
        gold=d.added.case.gold
        pred=_predict(d,PINNED_CONTINUED,weights,baseline,model)
        exact+=pred==gold
        correct+=len(pred&gold);false+=len(pred-gold);missing+=len(gold-pred)
    return exact,correct,-false,-missing


def _fit_weighted(train,weights,initial,*,quantity_margin,epochs=12,lr=.012):
    """Differentiable structured loss; quantity spans are pinned during training AND inference."""
    if quantity_margin not in (1.,2.,4.) or epochs<1 or not train:
        raise ValueError('G1_0079_BAD_TRAIN_CONFIG')
    model=reliability._trainable_copy(initial)
    reduced=[reliability._budget(d,64) for d in train]
    oracle_goals=[];pinned=[];unreachable=0
    frozen_quantity_scores=[];quantity_masks=[]
    for d in reduced:
        base=_baseline(d,weights,initial)
        q=_quantity(base)
        # Quantity control is a source prediction, never the gold label.
        # A control quantity may itself be wrong. A quantity-preserving
        # decoder cannot recover gold objects overlapping that fixed interval;
        # remove those incompatible gold targets rather than treating them as
        # an optimizer failure or displacing the quantity pin.
        compatible={v for v in d.added.case.gold if v[2]!=Q and not any(
            v[0]<item[1] and item[0]<v[1] for item in q)}
        target=compatible | q
        goal=structural._viterbi(d.added,[0.0]*len(d.added.case.options),
                                 oracle_gold=target)
        candidate={d.added.case.options[i].char_key for i in goal}
        if not q<=candidate:raise ValueError('G1_0079_ORACLE_Q_TARGET_UNREACHABLE')
        unreachable+=len(target-candidate)+len(d.added.case.gold-compatible-{
            v for v in d.added.case.gold if v[2]==Q and v in q})
        oracle_goals.append(goal);pinned.append(q)
        frozen_quantity_scores.append(previous._model_scores(d,initial).detach())
        quantity_masks.append(torch.tensor([o.kind==SPAN_KINDS.index(Q)+1
                            for o in d.added.case.options],dtype=torch.bool))
    optim=torch.optim.Adam(model.parameters(),lr=lr)
    best=copy.deepcopy(model);best_quality=_quality(reduced,weights,initial,model)
    best_epoch=0;history=[];updates=0;anchor_samples=[]
    for epoch in range(epochs):
        for d,q,goal,reference_q,qmask in zip(reduced,pinned,oracle_goals,
                                             frozen_quantity_scores,quantity_masks):
            optim.zero_grad()
            scores=_guarded_scores(d,model,q)
            target={d.added.case.options[i].char_key for i in goal}
            augmented=scores.detach().tolist()
            for i,opt in enumerate(d.added.case.options):
                if opt.char_key in q:continue
                if augmented[i]<-1.e7:continue
                weight=quantity_margin if SPAN_KINDS[opt.kind-1]==Q else 1.
                augmented[i]+=0.35*weight * (-1 if opt.char_key in target else 1)
            pred=structural._viterbi(d.added,augmented)
            if set(pred)==set(goal):continue
            mismatch=set(pred).symmetric_difference(goal)
            penalty=sum(0.35*(quantity_margin if
                SPAN_KINDS[d.added.case.options[i].kind-1]==Q else 1.) for i in mismatch)
            loss=scores[list(pred)].sum()-scores[list(goal)].sum()+penalty
            # Distinct quantity-preservation objectives: 1x is the hard
            # constraint alone; 2x/4x additionally distil the frozen control's
            # quantity scores so updates elsewhere cannot erode that channel.
            # This anchor uses only frozen predictions, never gold/audit labels.
            if quantity_margin>1. and bool(qmask.any()):
                raw=previous._model_scores(d,model)
                widths=torch.tensor([max(1,o.end-o.start) for o in d.added.case.options],
                                    dtype=raw.dtype)
                drift=((raw-reference_q)/widths)[qmask]
                anchor_term=(.02*(quantity_margin-1.))*torch.mean(drift.square())
                anchor_samples.append(float(anchor_term.detach()))
                loss=loss+anchor_term
            if float(loss.detach())<=0:continue
            if not loss.requires_grad:
                raise ValueError('G1_0079_NO_STRUCTURED_GRADIENT')
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(),max_norm=5.)
            optim.step()
            with torch.no_grad():
                for value in model.parameters():value.clamp_(-8.,8.)
            updates+=1
        quality=_quality(reduced,weights,initial,model)
        history.append(list(quality))
        if quality>best_quality:
            best=copy.deepcopy(model);best_quality=quality;best_epoch=epoch+1
        if epoch==0 or epoch+1==epochs or (epoch+1)%6==0:
            print('G1_0079_STRUCTURED '+json.dumps({'examples':len(reduced),
                'quantity_margin':quantity_margin,'epoch':epoch+1,
                'train_exact':quality[0],'unreachable_under_quantity_pin':unreachable}),flush=True)
    return best.eval().requires_grad_(False),{'epochs':epochs,'updates':updates,
        'quantity_margin':quantity_margin,'train_quality_by_epoch':history,
        'best_train_quality':list(best_quality),'best_epoch':best_epoch,
        'quantity_anchor_coefficient':.02*(quantity_margin-1.),
        'quantity_anchor_samples':anchor_samples[:20],
        'unreachable_under_quantity_pin':unreachable}


def _metrics(cases,weights,initial,continued,trained):
    result={}
    for arm in ARMS:
        pairs=[(_predict(d,arm,weights,initial,continued,trained),d.added.case.gold)
               for d in cases]
        result[arm]=_summarize(pairs)
    return result


def _choose(metrics):
    """Prior strict gate remains intact, including every semantic-kind recall."""
    baseline=metrics[CONTROL]
    viable=[CONTROL]
    for arm in SELECTABLE[1:]:
        cand=metrics[arm]
        if (cand['exact_prompts']>=baseline['exact_prompts'] and
            cand['f1']>=baseline['f1']-1e-12 and
            cand['precision']>=baseline['precision']-.005 and
            all(cand['by_kind'].get(k,{}).get('recall',0)>=
                baseline['by_kind'].get(k,{}).get('recall',0)-1.e-12
                for k in coverage.REQUIRED_KINDS) and
            (cand['exact_prompts']>baseline['exact_prompts'] or
             cand['f1']>baseline['f1']+1.e-10)):
            viable.append(arm)
    return max(viable,key=lambda a:(metrics[a]['exact_prompts'],metrics[a]['f1'],
        metrics[a]['precision'],-ARMS.index(a)))


def fit_predict(*,seal,seal_0077,reliability_reference,dataset,legacy,cutover,
                structural_reference,learned_reference,coverage_reference,
                discrimination_reference,output,device='cuda',max_length=2048,
                epochs=12):
    """Never open the audit answer file; only prompts are supplied to `_capture`."""
    from tools import nanojev_gamer_v1_graph_curriculum as cur
    from tools import nanojev_gamer_v1_backbone_first as backbone
    output=Path(output).resolve()
    if output.exists():raise FileExistsError('G1_0079_FIT_OUTPUT_EXISTS')
    manifest,train,select,prompts=_load_prepared(
        seal,dataset,seal_0077,reliability_reference)
    oldreport=_json(Path(reliability_reference)/'report.json')
    d_report=_json(Path(discrimination_reference)/'report.json')
    base_seal,basecommit,_=sealed._load_seal(seal_0077)
    entries,weights,priors=sealed._verify_frozen_lineage(
        record=d_report,seal=basecommit,dataset=dataset,legacy=legacy,cutover=cutover,
        structural_reference=structural_reference,learned_reference=learned_reference,
        coverage_reference=coverage_reference,discrimination_reference=discrimination_reference,
        output=output)
    if sha256(Path(discrimination_reference)/'report.json')!=manifest['discrimination_report_sha256']:
        raise ValueError('G1_0079_DISCRIMINATION_REFERENCE_DRIFT')
    base={};continued={}
    for name in MODELS:
        base[name],continued[name]=_models(oldreport,d_report,name)
    heads={name:cur._load_checkpoint(entries[name]['path'],device=device)[0].eval()
           for name in MODELS}
    b=entries['legacy']['manifest']
    qwen,tok=backbone.model_components(b['model_id'],b['source_adapter'],device)
    captured={}
    for phase,rows in [('train',train),('selection',select),('audit',[
        {'prompt':p['prompt'],'family':p['family'],'object_spans':[]} for p in prompts])]:
        captured[phase]=coverage._capture(rows,heads,qwen,tok,device=device,
            max_length=max_length,prior_selected=priors,reference_weights=weights,
            split='prospective_'+phase,budget=coverage.MAX_OPTIONS)
        print('G1_0079_CAPTURE '+json.dumps({'phase':phase,'cases':len(rows)},sort_keys=True),flush=True)
    trained={};fitlog={};chosen={};sel_metrics={}
    for name in MODELS:
        trained[name]={};fitlog[name]={}
        for arm,weight in [(PINNED_ONEX,1.),(PINNED_TWOX,2.),(PINNED_FOURX,4.)]:
            trained[name][arm],fitlog[name][arm]=_fit_weighted(
                captured['train'][name],weights[name],base[name],
                quantity_margin=weight,epochs=epochs)
        sel_metrics[name]=_metrics(captured['selection'][name],weights[name],
                                   base[name],continued[name],trained[name])
        chosen[name]=_choose(sel_metrics[name])
        print('G1_0079_SELECTION '+json.dumps({'model':name,'selected':chosen[name],
             'selection_exact':sel_metrics[name][chosen[name]]['exact_prompts'],
             'control_exact':sel_metrics[name][CONTROL]['exact_prompts']},sort_keys=True),flush=True)
    audit_predictions=[]
    for name in MODELS:
        for p,d in zip(prompts,captured['audit'][name]):
            if d.added.case.gold:raise ValueError('G1_0079_AUDIT_ORACLE_LEAK')
            ctl=_predict(d,CONTROL,weights[name],base[name],continued[name],trained[name])
            selected=_predict(d,chosen[name],weights[name],base[name],continued[name],trained[name])
            audit_predictions.append({'case_id':p['case_id'],'model':name,
                'prompt_sha256':p['prompt_sha256'],'family':p['family'],
                'control':[list(v) for v in sorted(ctl)],
                'selected':[list(v) for v in sorted(selected)]})
    if (sha256(Path(reliability_reference)/'report.json')!=manifest['reliability_report_sha256'] or
        sha256(Path(reliability_reference)/'cases.jsonl')!=manifest['reliability_cases_sha256'] or
        sha256(Path(seal)/'audit.prompts.jsonl')!=manifest['audit_prompts_sha256'] or
        sha256(Path(discrimination_reference)/'report.json')!=manifest['discrimination_report_sha256']):
        raise ValueError('G1_0079_LINEAGE_CHANGED_DURING_TRAIN')
    for name in MODELS:
        if sha256(Path(entries[name]['path'])/cur.HEAD_FILE)!=basecommit['checkpoint_sha256'][name]:
            raise ValueError('G1_0079_HEAD_CHANGED:'+name)
    output.mkdir(parents=True,exist_ok=False)
    _write_rows(output/'audit.predictions.jsonl',audit_predictions)
    trained_params={n:{a:previous._export_model(model) for a,model in fitted.items()}
                    for n,fitted in trained.items()}
    report={'schema':SCHEMA,'status':'AUDIT_BLIND_PREDICTIONS_LOCKED_NOT_SCORED',
        'seal_manifest_sha256':sha256(Path(seal)/'manifest.json'),
        'reliability_report_sha256':manifest['reliability_report_sha256'],
        'discrimination_report_sha256':manifest['discrimination_report_sha256'],
        'audit_prompts_sha256':manifest['audit_prompts_sha256'],
        'committed_audit_answers_sha256':manifest['committed_audit_answers_sha256'],
        'prediction_sha256':sha256(output/'audit.predictions.jsonl'),
        'selected':chosen,'selection_metrics':sel_metrics,'fit_logs':fitlog,
        'trained_coefficients':trained_params,'arms':ARMS,
        'quantity_guard_guarantee':'selected quantity spans exactly equal frozen control for every pinned competitor',
        'historical_unprotected_continued_is_diagnostic_only':True,
        'audit_answers_opened_during_fit':False,'audit_gold_used_for_selection_or_inference':False,
        'head_weights_modified':False,'g1_certified':False,'g2_unlocked':False,
        'production_promotion':False,'new_audit_is_not_certification':True}
    _write_json(output/'report.json',report)
    print('G1_0079_PREDICTED '+json.dumps({'selected':chosen,'cases':len(prompts),
        'predictions_locked':len(audit_predictions),'status':report['status']},sort_keys=True),flush=True)
    return report


def score(*,seal,predictions,output):
    """Verify precommitted answers and frozen predictions; score at most once."""
    seal=Path(seal).resolve();predictions=Path(predictions).resolve();output=Path(output).resolve()
    if output.exists() or (seal/'UNSEALED.json').exists():
        raise FileExistsError('G1_0079_ALREADY_SCORED')
    manifest=_json(seal/'manifest.json');report=_json(predictions/'report.json')
    if (manifest.get('schema')!=SCHEMA or
        report.get('schema')!=SCHEMA or
        report.get('status')!='AUDIT_BLIND_PREDICTIONS_LOCKED_NOT_SCORED' or
        report.get('seal_manifest_sha256')!=sha256(seal/'manifest.json') or
        report.get('audit_answers_opened_during_fit') is not False or
        report.get('audit_gold_used_for_selection_or_inference') is not False or
        report.get('committed_audit_answers_sha256')!=manifest['committed_audit_answers_sha256'] or
        report.get('audit_prompts_sha256')!=manifest['audit_prompts_sha256'] or
        sha256(predictions/'audit.predictions.jsonl')!=report['prediction_sha256'] or
        sha256(seal/'audit.prompts.jsonl')!=manifest['audit_prompts_sha256'] or
        sha256(seal/'audit.answers.SECRET.jsonl')!=manifest['committed_audit_answers_sha256']):
        raise ValueError('G1_0079_SEAL_OR_PREDICTION_DRIFT')
    prompts=_rows(seal/'audit.prompts.jsonl')
    gold=_rows(seal/'audit.answers.SECRET.jsonl')
    actual=_rows(predictions/'audit.predictions.jsonl')
    if len(gold)!=len(prompts) or len(actual)!=len(prompts)*len(MODELS):
        raise ValueError('G1_0079_SCORED_CASE_COUNT')
    golden={};promptmap={}
    for p,g in zip(prompts,gold):
        if (g['case_id']!=p['case_id'] or g['prompt_sha256']!=p['prompt_sha256'] or
            g['family']!=p['family'] or _hash(p['prompt'])!=p['prompt_sha256']):
            raise ValueError('G1_0079_AUDIT_ANSWER_BINDING')
        keys={tuple(x) for x in g['gold']}
        if len(keys)!=len(g['gold']) or any(len(x)!=3 or x[2] not in SPAN_KINDS or
            type(x[0]) is not int or type(x[1]) is not int or not 0<=x[0]<x[1]<=len(p['prompt'])
            for x in keys):raise ValueError('G1_0079_BAD_GOLD')
        if p['case_id'] in golden:raise ValueError('G1_0079_DUPLICATE_CASE')
        golden[p['case_id']]=keys;promptmap[p['case_id']]=p
    bymodel={n:[] for n in MODELS};seen=set();case_rows=[]
    for r in actual:
        ident=(r.get('model'),r.get('case_id'))
        if ident in seen or ident[0] not in MODELS or ident[1] not in golden:
            raise ValueError('G1_0079_BAD_PREDICTION_KEY')
        seen.add(ident);p=promptmap[ident[1]]
        if r.get('prompt_sha256')!=p['prompt_sha256'] or r.get('family')!=p['family']:
            raise ValueError('G1_0079_BAD_PREDICTION_BINDING')
        vals=[]
        for key in ('control','selected'):
            raw=r.get(key)
            if not isinstance(raw,list):raise ValueError('G1_0079_BAD_PREDICTION_FORMAT')
            choices={tuple(v) for v in raw}
            if len(choices)!=len(raw) or any(len(x)!=3 or x[2] not in SPAN_KINDS or
                type(x[0]) is not int or type(x[1]) is not int or not 0<=x[0]<x[1]<=len(p['prompt'])
                for x in choices):raise ValueError('G1_0079_BAD_PREDICTION_SPAN')
            vals.append(choices)
        ctrl,pred=vals;true=golden[ident[1]]
        if _quantity(ctrl)!=_quantity(pred):
            raise ValueError('G1_0079_AUDIT_QUANTITY_PRESERVATION_FAILED')
        bymodel[ident[0]].append((ctrl,pred,true))
        case_rows.append({'model':ident[0],'case_id':ident[1],
            'control_exact':ctrl==true,'selected_exact':pred==true,
            'recovered_correct':[list(x) for x in sorted((pred&true)-(ctrl&true))],
            'lost_correct':[list(x) for x in sorted((ctrl&true)-(pred&true))],
            'new_false':[list(x) for x in sorted((pred-true)-(ctrl-true))],
            'removed_false':[list(x) for x in sorted((ctrl-true)-(pred-true))]})
    metrics={}
    for name,pairs in bymodel.items():
        metrics[name]={'control':_summarize([(c,g) for c,p,g in pairs]),
                       'selected':_summarize([(p,g) for c,p,g in pairs])}
        if metrics[name]['control']['by_kind'][Q]!=metrics[name]['selected']['by_kind'][Q]:
            raise ValueError('G1_0079_QUANTITY_KIND_REGRESSION')
    output.mkdir(parents=True,exist_ok=False)
    result={'schema':SCHEMA,'status':'PROSPECTIVE_AUDIT_SCORED_ONCE_NO_PROMOTION',
        'audit_cases':len(prompts),'selected':report['selected'],'metrics':metrics,
        'precommitted_answers_sha256':manifest['committed_audit_answers_sha256'],
        'prediction_sha256':report['prediction_sha256'],
        'selection_report_sha256':sha256(predictions/'report.json'),
        'audit_used_for_fit_or_model_selection':False,
        'audit_same_generator_distribution':True,'certification_authorized':False,
        'g1_certified':False,'g2_unlocked':False,'production_promotion':False}
    _write_json(output/'report.json',result)
    _write_rows(output/'cases.jsonl',case_rows)
    _write_json(seal/'UNSEALED.json',{'status':'SCORED_ONCE',
        'scored_report_sha256':sha256(output/'report.json'),
        'prediction_sha256':report['prediction_sha256']})
    print('G1_0079_RESULT '+json.dumps({'status':result['status'],
        'selected':result['selected'],'audit':{n:{
            'control_exact':v['control']['exact_prompts'],
            'selected_exact':v['selected']['exact_prompts'],
            'selected_f1':round(v['selected']['f1'],5)}
            for n,v in metrics.items()}},sort_keys=True),flush=True)
    return result


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    sub=p.add_subparsers(dest='command',required=True)
    a=sub.add_parser('prepare',help='Generate and commit new sealed audit, before fitting')
    for field in ('dataset','seal-0077','reliability-reference','output'):
        a.add_argument('--'+field,required=True)
    a.add_argument('--seed',type=int,default=SEED)
    for field,default in [('train',96),('selection',32),('audit',128)]:
        a.add_argument('--'+field,type=int,default=default)
    b=sub.add_parser('fit-predict',help='Fit on new train, select, predict blind audit')
    for field in ('seal','seal-0077','reliability-reference','dataset','legacy','cutover',
                  'structural-reference','learned-reference','coverage-reference',
                  'discrimination-reference','output'):
        b.add_argument('--'+field,required=True)
    b.add_argument('--device',default='cuda');b.add_argument('--max-length',type=int,default=2048)
    b.add_argument('--epochs',type=int,default=12)
    c=sub.add_parser('score',help='One-time unseal and score locked predictions')
    for field in ('seal','predictions','output'):
        c.add_argument('--'+field,required=True)
    args=vars(p.parse_args(argv));command=args.pop('command')
    return {'prepare':prepare,'fit-predict':fit_predict,'score':score}[command](**args)


if __name__=='__main__':main()
