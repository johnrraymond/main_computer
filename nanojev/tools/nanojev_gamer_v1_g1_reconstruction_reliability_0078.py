"""G1-0078: diagnose G1-0077 sealed failures, then fit competitors on NEW data.

Diagnose reads the already-unsealed 0077 cohort only after validating the original
SHA-256 commitments, and never modifies the seal, prediction or score folders.
Bakeoff uses the already-scored 0077 answers and predictions only to diagnose
frozen candidate availability; it never passes those cases into training or
model selection. It generates new TEST-vocabulary examples, disjoint from both
the original corpus and 0077, trains each interface on NEW train, chooses on
NEW selection and reports NEW audit.

No G1 head/Qwen/LoRA changes, G2 unlock, production promotion or certification.
"""
from __future__ import annotations

import argparse
import copy
from collections import Counter
import hashlib
import json
from pathlib import Path
import random

import torch

from tools import nanojev_gamer_v1_g1_sealed_generalization_0077 as sealed
from tools import nanojev_gamer_v1_g1_discrimination_trainability_bakeoff_0076 as previous
from tools import nanojev_gamer_v1_g1_candidate_coverage_bakeoff_0075 as coverage
from tools import nanojev_gamer_v1_g1_learned_interface_bakeoff_0073 as learned
from tools import nanojev_gamer_v1_g1_structural_learning_bakeoff_0074 as structural
from tools.nanojev_gamer_v1_g1_oracle_decoder_cutover_frob import sha256, _summarize
from tools.nanojev_gamer_v1_graph_contract import SPAN_KINDS

SCHEMA='nanojev.gamer.graph.g1.reconstruction-reliability-0078.v1'
CONTROL='frozen_staged_joint_64'
ARMS=(CONTROL,'continued_structured_64','continued_structured_256',
      'fresh_staged_64','mined_staged_64')
PARTITIONS=('train','selection','audit')
DEFAULT_SEED=780078


def _read(path):
    return json.loads(Path(path).read_text(encoding='utf8'))


def _rows(path):
    return [json.loads(s) for s in Path(path).read_text(encoding='utf8').splitlines() if s.strip()]


def _write_json(path, data):
    Path(path).write_text(json.dumps(data,sort_keys=True,indent=2,ensure_ascii=False,
                               allow_nan=False)+'\n',encoding='utf8')


def _write_rows(path, records):
    with Path(path).open('x',encoding='utf8') as f:
        for r in records:f.write(json.dumps(r,sort_keys=True,ensure_ascii=False,
                                           allow_nan=False)+'\n')


def _set(items):
    return {tuple(x) for x in items}


def _validation(seal_dir,pred_dir,score_dir):
    """Require genuine already-consumed G1-0077 score; never re-score or mutate."""
    seal_dir=Path(seal_dir).resolve();pred_dir=Path(pred_dir).resolve();score_dir=Path(score_dir).resolve()
    seal_root,commit,prompts=sealed._load_seal(seal_dir)
    if seal_root!=seal_dir:raise ValueError('G1_0078_BAD_SEAL_PATH')
    unsealed=_read(seal_dir/'UNSEALED.json')
    report=_read(score_dir/'report.json')
    manifest=_read(pred_dir/'manifest.json')
    if (unsealed.get('phase')!='SCORED_ONCE' or
            unsealed.get('report_sha256')!=sha256(score_dir/'report.json') or
            unsealed.get('prediction_sha256')!=manifest.get('prediction_sha256') or
            unsealed.get('answer_sha256')!=commit['committed_answer_sha256'] or
            manifest.get('prediction_sha256')!=sha256(pred_dir/'predictions.jsonl') or
            manifest.get('prompts_sha256')!=commit['prompts_sha256'] or
            manifest.get('frozen_report_sha256')!=commit['frozen_report_sha256'] or
            manifest.get('checkpoints_sha256')!=commit['checkpoint_sha256'] or
            manifest.get('phase')!='BLIND_PREDICTIONS_LOCKED' or
            manifest.get('answers_opened_during_prediction') is not False or
            manifest.get('gold_used_for_candidate_selection_or_inference') is not False or
            manifest.get('training_performed') is not False or
            manifest.get('selected_by_model')!={n:sealed.SELECTED for n in sealed.REQUIRED_MODEL_NAMES} or
            report.get('schema')!=sealed.SCHEMA or
            report.get('status')!='SEALED_COHORT_SCORED_NO_PROMOTION' or
            report.get('precommitted_answer_sha256')!=commit['committed_answer_sha256'] or
            report.get('blind_prediction_sha256')!=manifest['prediction_sha256'] or
            report.get('frozen_report_sha256')!=commit['frozen_report_sha256'] or
            report.get('trained_or_tuned_on_sealed_cohort') is not False or
            report.get('stage_g1_certified') is not False or
            report.get('g2_unlocked') is not False or
            sha256(seal_dir/'answers.SECRET.jsonl')!=commit['committed_answer_sha256']):
        raise ValueError('G1_0078_SEALED_LINEAGE_DRIFT')
    answers=sealed._validate_gold(_rows(seal_dir/'answers.SECRET.jsonl'),prompts)
    predicted=_rows(pred_dir/'predictions.jsonl')
    if len(predicted)!=2*len(prompts):raise ValueError('G1_0078_PREDICTION_COUNT')
    expected={(p['case_id'],n) for p in prompts for n in sealed.REQUIRED_MODEL_NAMES}
    by_case={p['case_id']:p for p in prompts}
    found=set()
    for r in predicted:
        key=(r.get('case_id'),r.get('model'))
        if key not in expected or key in found:raise ValueError('G1_0078_PREDICTION_KEY')
        found.add(key)
        p=by_case[key[0]]
        if r.get('prompt_sha256')!=p['prompt_sha256'] or r.get('family')!=p['family']:
            raise ValueError('G1_0078_PREDICTION_BINDING')
        for fld in ('control','selected'):
            values=r.get(fld)
            if not isinstance(values,list) or len(values)!=len(_set(values)):
                raise ValueError('G1_0078_PREDICTION_DUPLICATE')
            for v in values:
                if (not isinstance(v,list) or len(v)!=3 or
                        type(v[0]) is not int or type(v[1]) is not int or
                        not 0<=v[0]<v[1]<=len(p['prompt']) or v[2] not in SPAN_KINDS):
                    raise ValueError('G1_0078_INVALID_PREDICTION')
    if found!=expected:raise ValueError('G1_0078_PREDICTION_INCOMPLETE')
    for name in sealed.REQUIRED_MODEL_NAMES:
        for fld,report_col in (('control','control'),('selected','selected')):
            metrics,_=sealed._summarize_predictions(predicted,answers,name,fld)
            original=report['metrics'][name][report_col]
            for key in ('cases','correct','predicted','expected','exact_prompts'):
                if metrics[key]!=original[key]:
                    raise ValueError('G1_0078_SCORED_PARITY:'+name+':'+key)
    return commit,report,prompts,answers,predicted


def _classify_missing(obj,predictions):
    start,end,kind=obj
    if any(a==start and b==end and k!=kind for a,b,k in predictions):
        return 'wrong_type_same_bounds'
    if any(k==kind and max(a,start)<min(b,end) for a,b,k in predictions):
        return 'overlapping_same_type_boundary'
    if any(max(a,start)<min(b,end) for a,b,_ in predictions):
        return 'overlap_other_type_competition'
    return 'no_overlapping_selected_candidate'


def _atlas(prompts,answers,predicted):
    by_id={p['case_id']:p for p in prompts};failures=[];summary={}
    for model in sealed.REQUIRED_MODEL_NAMES:
        counts=Counter();missing_kinds=Counter();false_kinds=Counter();case_count=Counter()
        for r in predicted:
            if r['model']!=model:continue
            gold=answers[r['case_id']];selected=_set(r['selected']);control=_set(r['control'])
            if selected==gold:continue
            miss=sorted(gold-selected);bad=sorted(selected-gold)
            case_count[by_id[r['case_id']]['family']]+=1
            mis=[];fp=[]
            for a in miss:
                reason=_classify_missing(a,selected)
                counts[reason]+=1;missing_kinds[a[-1]]+=1
                mis.append({'span':list(a),'text':by_id[r['case_id']]['prompt'][a[0]:a[1]],
                            'observed_prediction_relation':reason})
            for a in bad:
                false_kinds[a[-1]]+=1
                fp.append({'span':list(a),'text':by_id[r['case_id']]['prompt'][a[0]:a[1]]})
            failures.append({'model':model,'case_id':r['case_id'],
                'prompt_sha256':r['prompt_sha256'],'family':r['family'],
                'gold_count':len(gold),'selected_count':len(selected),
                'control_exact':control==gold,'missing':mis,'false':fp,
                'correct_objects':len(gold&selected),'selected_exact':False})
        summary[model]={'inexact_cases':sum(case_count.values()),
            'inexact_by_family':dict(case_count),
            'missing_object_observed_categories':dict(counts),
            'missing_by_kind':dict(missing_kinds),'false_by_kind':dict(false_kinds),
            'diagnostic_scope':'selected output comparison; not neural candidate reachability'}
    return summary,failures


def diagnose(*,seal,predictions,scored,output):
    target=Path(output).resolve()
    if target.exists():raise FileExistsError('G1_0078_DIAGNOSIS_IMMUTABLE')
    sources=[Path(x).resolve() for x in (seal,predictions,scored)]
    if any(target==p or p in target.parents for p in sources):
        raise ValueError('G1_0078_OUTPUT_OVER_SOURCE')
    commitment,original,prompts,answers,rows=_validation(*sources)
    counts,failures=_atlas(prompts,answers,rows)
    # Strict fail closed against the actual prospective published result.
    for model in sealed.REQUIRED_MODEL_NAMES:
        if len([r for r in failures if r['model']==model])!=(
            original['metrics'][model]['selected']['cases']-
            original['metrics'][model]['selected']['exact_prompts']):
            raise ValueError('G1_0078_INEXACT_COUNT_DRIFT')
    report={'schema':SCHEMA,'status':'READ_ONLY_SEALED_FAILURE_ATLAS',
        'cases':len(prompts),'sealed_report_sha256':sha256(sources[2]/'report.json'),
        'sealed_prediction_sha256':sha256(sources[1]/'predictions.jsonl'),
        'sealed_prompts_sha256':commitment['prompts_sha256'],
        'precommitted_answers_sha256':commitment['committed_answer_sha256'],
        'sealed_metrics':original['metrics'],'observed_failure_atlas':counts,
        'warning':'This prospective cohort is now opened for diagnostic analysis; never train or select on it.',
        'sealed_cohort_used_for_training_or_selection':False,
        'neural_candidate_coverage_inferred_from_predictions':False,
        'g1_certified':False,'g2_unlocked':False,'production_promotion':False}
    target.mkdir(parents=True,exist_ok=False)
    _write_json(target/'report.json',report)
    _write_rows(target/'failures.jsonl',failures)
    print('G1_0078_DIAGNOSIS '+json.dumps({'models':{m:v['inexact_cases'] for m,v in counts.items()},
        'status':report['status'],'report':str(target/'report.json')},sort_keys=True),flush=True)
    return report


def _generate_new(*,original_dataset,old_seal,seed,train,selection,audit):
    """Generate after 0077; these are never the 0077 cases themselves."""
    from tools import nanojev_gamer_v1_graph_corpus as corpus
    from tools import nanojev_gamer_v1_graph_curriculum as curriculum
    if (min(train,selection,audit)<2 or any(n%2 for n in (train,selection,audit)) or
            seed<0 or seed==sealed.DEFAULT_SEED):
        raise ValueError('G1_0078_INVALID_NEW_COHORT')
    exclusions={hashlib.sha256(r['prompt'].encode('utf8')).hexdigest()
                for r in curriculum.load_rows(original_dataset)}
    prior=sealed._rows(Path(old_seal)/'prompts.jsonl')
    exclusions.update(p['prompt_sha256'] for p in prior)
    rng=random.Random(seed);new={};all_hashes=[];counter=10_000_000
    for phase,n in (('train',train),('selection',selection),('audit',audit)):
        batch=[]
        for i in range(n):
            factory=corpus.encounter if i%2==0 else corpus.allocation
            for attempt in range(200):
                candidate=factory(rng,'test',counter)
                counter+=1
                fingerprint=hashlib.sha256(candidate['prompt'].encode('utf8')).hexdigest()
                if fingerprint not in exclusions:break
            else:raise ValueError('G1_0078_EXHAUSTED_DISJOINT_PROMPTS')
            exclusions.add(fingerprint);all_hashes.append(fingerprint)
            batch.append(candidate)
        new[phase]=batch
    return new,all_hashes


def _budget(data,b):
    return previous._restrict(data,b,'frozen')


def _baseline(data,weights,model):
    return previous._predict(_budget(data,64),'staged_joint_64',weights,model)


def _trainable_copy(frozen):
    """Copy a verified inference-only G1-0076 interface for fitting.

    G1-0077's loader intentionally returns ``requires_grad_(False)``. The
    original model must remain frozen for control scoring and provenance;
    every competing learner instead receives its own trainable parameter copy.
    """
    model = copy.deepcopy(frozen).train()
    model.requires_grad_(True)
    if not all(p.requires_grad and p.is_leaf for p in model.parameters()):
        raise ValueError('G1_0078_TRAINABLE_PARAMETER_COPY_FAILED')
    if any(a.data_ptr() == b.data_ptr() for a, b in zip(model.parameters(), frozen.parameters())):
        raise ValueError('G1_0078_PARAMETER_ALIASING')
    return model


def _fit_variants(train,initial,*,hard_steps=120,structured_epochs=14):
    """Only new TRAIN data is permitted to enter optimizer and oracle losses."""
    train64=[_budget(x,64) for x in train]
    train256=[_budget(x,256) for x in train]
    cont64,log64=previous._fit_structured(train64,_trainable_copy(initial),epochs=structured_epochs,
                                         strict_targets=False)
    cont256,log256=previous._fit_structured(train256,_trainable_copy(initial),epochs=structured_epochs,
                                           strict_targets=False)
    fresh,hardlog=previous._fit_hard(train64,steps=hard_steps)
    fresh,frlog=previous._fit_structured(train64,fresh,epochs=structured_epochs,
                                        strict_targets=False)
    mined,minelog=previous._mine_counterexamples(train64,_trainable_copy(initial),(),rounds=2,
                                                   steps=max(30,hard_steps//2))
    mined,stagedlog=previous._fit_structured(train64,mined,epochs=structured_epochs,
                                            strict_targets=False)
    return {'continued_structured_64':cont64,'continued_structured_256':cont256,
            'fresh_staged_64':fresh,'mined_staged_64':mined}, {
        'continued_structured_64':log64,'continued_structured_256':log256,
        'fresh_staged_64':{'hard':hardlog,'structured':frlog},
        'mined_staged_64':{'mined':minelog,'structured':stagedlog}}


def _predict_arm(data,arm,weights,initial,fitted):
    if arm not in ARMS:raise ValueError('G1_0078_UNKNOWN_ARM')
    if arm==CONTROL:return _baseline(data,weights,initial)
    budget=256 if arm.endswith('_256') else 64
    return previous._predict(_budget(data,budget),'staged_joint_'+str(budget),
                             weights,fitted[arm])


def _metrics(cases,weights,initial,fitted):
    outputs={}
    for arm in ARMS:
        predictions=[(_predict_arm(d,arm,weights,initial,fitted),d.added.case.gold) for d in cases]
        outputs[arm]={'metrics':_summarize(predictions),'pairs':predictions}
    return outputs


def _choose(results):
    """Selection never touches test/audit/0077 labels."""
    control=results[CONTROL]['metrics'];viable=[CONTROL]
    for arm in ARMS[1:]:
        candidate=results[arm]['metrics']
        if (candidate['exact_prompts']>=control['exact_prompts'] and
            candidate['f1']>=control['f1']-1e-12 and
            candidate['precision']>=control['precision']-.005 and
            all(candidate['by_kind'].get(k,{}).get('recall',0)>=
                control['by_kind'].get(k,{}).get('recall',0)-1e-12
                for k in coverage.REQUIRED_KINDS) and
            (candidate['exact_prompts']>control['exact_prompts'] or
             candidate['f1']>control['f1']+1e-10)):
            viable.append(arm)
    return max(viable,key=lambda arm:(results[arm]['metrics']['exact_prompts'],
        results[arm]['metrics']['f1'],results[arm]['metrics']['precision'],
        -ARMS.index(arm)))


def _cohort_summary(phase,cases,weights,initial,fitted,selected):
    metrics=_metrics(cases,weights,initial,fitted)
    case_rows=[]
    for d in cases:
        gold=d.added.case.gold
        control=_predict_arm(d,CONTROL,weights,initial,fitted)
        chosen=_predict_arm(d,selected,weights,initial,fitted)
        case_rows.append({'phase':phase,'model':d.added.case.model,'family':d.added.case.family,
            'prompt_sha256':d.added.case.prompt_hash,
            'control_exact':control==gold,'selected_exact':chosen==gold,
            'gained_correct':[list(v) for v in sorted((chosen&gold)-(control&gold))],
            'lost_correct':[list(v) for v in sorted((control&gold)-(chosen&gold))],
            'introduced_false':[list(v) for v in sorted((chosen-gold)-(control-gold))],
            'removed_false':[list(v) for v in sorted((control-gold)-(chosen-gold))]})
    return {'arms':{a:v['metrics'] for a,v in metrics.items()},
            'selected':selected,'selected_metrics':metrics[selected]['metrics'],
            'control_metrics':metrics[CONTROL]['metrics']},case_rows


def _oracle_ceilings(cases):
    result={}
    for budget in (64,256):
        found=0;exact=0
        for d in cases:
            reduced=_budget(d,budget)
            keys={x.char_key for x in reduced.added.case.options}
            gold=d.added.case.gold
            found+=len(keys&gold)
            # This is just coordinate coverage; not necessarily a nonoverlap assignment.
            exact+=int(gold<=keys)
        result[str(budget)]={'all_gold_coordinates_present':exact,
            'gold_candidate_coordinates_present':found,
            'cases':len(cases),'not_a_decoder_accuracy_claim':True}
    return result



def _candidate_failure_atlas(rows,preexisting,prepared):
    """Oracle-only attribution performed AFTER frozen inference; no fitting path.

    `prepared` is a list of EXPANDED candidate latices for already-incorrect
    sealed cases. All labels are overlaid only after label-blind candidate
    generation. This function never returns models or optimizer inputs.
    """
    stats=Counter();records=[]
    for r,data in zip(rows,prepared):
        expected=preexisting[r['case_id']]
        gold=set(expected['gold'])
        selected=_set(r['selected'])
        if gold==selected:raise ValueError('G1_0078_EXPECTED_FAILED_CASE')
        frozen64=_budget(data,64);frozen256=_budget(data,256)
        sets={'64':{o.char_key for o in frozen64.added.case.options},
              '256':{o.char_key for o in frozen256.added.case.options},
              'all':{o.char_key for o in data.added.case.options},
              'raw':set(data.raw_span_gold_types)}
        missing=[]
        for obj in sorted(gold-selected):
            if obj not in sets['raw']:
                reason='no_valid_raw_neural_span_coordinate'
            elif obj not in sets['all']:
                reason='filtered_from_unrestricted_proposals'
            elif obj not in sets['256']:
                reason='excluded_from_256_budget'
            elif obj not in sets['64']:
                reason='excluded_from_64_budget'
            else:
                reason='available_in_64_but_not_selected'
            stats[reason]+=1
            stats['missing_'+obj[-1]]+=1
            missing.append({'span':list(obj),'reason':reason})
        rec={'model':'cutover','case_id':r['case_id'],
             'gold_count':len(gold),'missing':missing,
             'all_gold_in_64':gold<=sets['64'],
             'all_gold_in_256':gold<=sets['256'],
             'all_gold_in_unrestricted':gold<=sets['all'],
             'unrestricted_candidate_count':len(sets['all']),
             'correct_selected':len(gold&selected),'false_selected':len(selected-gold)}
        records.append(rec)
    return dict(stats),records


def _capture_sealed_failures(*,prompts,answers,predicted,heads,qwen,tok,
                             priors,weights,device,max_length):
    """Re-capture ONLY the 0077 cutover mistakes as offline diagnostics.

    This is not an evaluation or fitting cohort; the returned values must never
    be passed to _fit_variants(), _choose(), or _cohort_summary().
    """
    prompt_map={p['case_id']:p for p in prompts}
    failed=[r for r in predicted if r['model']=='cutover' and
            _set(r['selected']) != answers[r['case_id']]]
    blind=[{'prompt':prompt_map[r['case_id']]['prompt'],
            'family':r['family'],'object_spans':[]} for r in failed]
    if len(failed)!=len(blind):raise ValueError('G1_0078_BAD_SEALED_DIAGNOSIS')
    if not failed:return {},[]
    # _capture constructs options and fixed neural scores before it overlays
    # the label set (empty here). The oracle is applied later, only for attribution.
    captured=coverage._capture(blind,heads,qwen,tok,device=device,
        max_length=max_length,prior_selected=priors,reference_weights=weights,
        split='sealed_diagnosis_read_only',budget=coverage.MAX_OPTIONS)
    for r,data in zip(failed,captured['cutover']):
        if data.added.case.gold:
            raise ValueError('G1_0078_ORACLE_ENTERED_NEURAL_CAPTURE')
        # Compare neural candidate universe, not fitted predictions: source
        # parameters are validated separately before any capture.
        if r['prompt_sha256']!=data.added.case.prompt_hash:
            raise ValueError('G1_0078_DIAGNOSTIC_PROMPT_DRIFT')
    raw_answers={k:{'gold':list(v)} for k,v in answers.items()}
    return _candidate_failure_atlas(failed,raw_answers,captured['cutover'])


def bakeoff(*,diagnostic_reference,seal,predictions,scored,dataset,legacy,cutover,structural_reference,
            learned_reference,coverage_reference,discrimination_reference,output,
            device='cuda',seed=DEFAULT_SEED,train=96,selection=32,audit=64,
            hard_steps=120,structured_epochs=14,max_length=2048):
    from tools import nanojev_gamer_v1_graph_curriculum as cur
    from tools import nanojev_gamer_v1_backbone_first as backbone
    out=Path(output).resolve();diagnostic=Path(diagnostic_reference).resolve()
    if out.exists():raise FileExistsError('G1_0078_IMMUTABLE_BAKEOFF_OUTPUT')
    if min(hard_steps,structured_epochs,max_length)<=0:
        raise ValueError('G1_0078_INVALID_TRAINING_CONFIG')
    previous_atlas=_read(diagnostic/'report.json')
    if previous_atlas.get('schema')!=SCHEMA or previous_atlas.get('status')!='READ_ONLY_SEALED_FAILURE_ATLAS':
        raise ValueError('G1_0078_MISSING_SEALED_DIAGNOSIS')
    sealed_dir,manifest,_=sealed._load_seal(seal)
    if previous_atlas.get('sealed_prompts_sha256')!=manifest['prompts_sha256'] or \
       previous_atlas.get('precommitted_answers_sha256')!=manifest['committed_answer_sha256']:
        raise ValueError('G1_0078_DIAGNOSIS_LINEAGE_DRIFT')
    report=_read(Path(discrimination_reference)/'report.json')
    entries,weights,priors=sealed._verify_frozen_lineage(
        record=report,seal=manifest,dataset=dataset,legacy=legacy,cutover=cutover,
        structural_reference=structural_reference,learned_reference=learned_reference,
        coverage_reference=coverage_reference,discrimination_reference=discrimination_reference,
        output=out)
    initial={name:sealed._load_coefficients(report,name) for name in sealed.REQUIRED_MODEL_NAMES}
    verified_commit,verified_score,old_prompts,old_answers,old_predictions=_validation(
        seal,predictions,scored)
    if sha256(Path(scored)/'report.json')!=previous_atlas['sealed_report_sha256']:
        raise ValueError('G1_0078_DIAGNOSTIC_REPORT_CHANGED')
    cohorts,hashes=_generate_new(original_dataset=dataset,old_seal=sealed_dir,seed=seed,
                                 train=train,selection=selection,audit=audit)
    # No paths to the G1-0077 answers, predictions or scores enter the fit path.
    heads={name:cur._load_checkpoint(entries[name]['path'],device=device)[0].eval()
           for name in sealed.REQUIRED_MODEL_NAMES}
    qwen,tok=backbone.model_components(entries['legacy']['manifest']['model_id'],
                entries['legacy']['manifest']['source_adapter'],device)
    diagnostic_stats,diagnostic_records=_capture_sealed_failures(
        prompts=old_prompts,answers=old_answers,
        predicted=old_predictions,heads=heads,qwen=qwen,tok=tok,
        priors=priors,weights=weights,device=device,max_length=max_length)
    if len(diagnostic_records)!=previous_atlas['observed_failure_atlas']['cutover']['inexact_cases']:
        raise ValueError('G1_0078_CANDIDATE_DIAGNOSIS_COUNT_DRIFT')
    print('G1_0078_CANDIDATE_DIAGNOSIS '+json.dumps({
        'cases':len(diagnostic_records),'missing_object_reasons':diagnostic_stats},
        sort_keys=True),flush=True)
    encoded={}
    for phase in PARTITIONS:
        encoded[phase]=coverage._capture(cohorts[phase],heads,qwen,tok,
            device=device,max_length=max_length,prior_selected=priors,
            reference_weights=weights,split='research_'+phase,budget=coverage.MAX_OPTIONS)
        print('G1_0078_CAPTURE '+json.dumps({'phase':phase,'cases':len(cohorts[phase])},
                                             sort_keys=True),flush=True)
    fitted={};fitlogs={};chosen={};phases={};case_records=[]
    for name in sealed.REQUIRED_MODEL_NAMES:
        fitted[name],fitlogs[name]=_fit_variants(encoded['train'][name],initial[name],
             hard_steps=hard_steps,structured_epochs=structured_epochs)
        selected_metrics=_metrics(encoded['selection'][name],weights[name],
                                  initial[name],fitted[name])
        chosen[name]=_choose(selected_metrics)
        print('G1_0078_SELECTION '+json.dumps({'model':name,'selected':chosen[name],
           'selection_exact':selected_metrics[chosen[name]]['metrics']['exact_prompts']},
           sort_keys=True),flush=True)
    for phase in PARTITIONS:
        phases[phase]={}
        for name in sealed.REQUIRED_MODEL_NAMES:
            section,rows=_cohort_summary(phase,encoded[phase][name],weights[name],
                                        initial[name],fitted[name],chosen[name])
            section['coordinate_oracle_ceilings']=_oracle_ceilings(encoded[phase][name])
            phases[phase][name]=section
            case_records.extend(rows)
    if sha256(Path(discrimination_reference)/'report.json')!=manifest['frozen_report_sha256']:
        raise ValueError('G1_0078_FROZEN_INTERFACE_CHANGED')
    if sha256(Path(sealed_dir)/'prompts.jsonl')!=manifest['prompts_sha256']:
        raise ValueError('G1_0078_SEALED_PROMPTS_CHANGED')
    from tools import nanojev_gamer_v1_graph_corpus as corpus
    if sha256(Path(corpus.__file__))!=manifest['generator_sha256']:
        raise ValueError('G1_0078_GENERATOR_CHANGED')
    for name in sealed.REQUIRED_MODEL_NAMES:
        if sha256(Path(entries[name]['path'])/cur.HEAD_FILE)!=manifest['checkpoint_sha256'][name]:
            raise ValueError('G1_0078_CHECKPOINT_CHANGED:'+name)
    if backbone.adapter_fingerprint(entries['legacy']['manifest']['source_adapter']) != manifest['source_adapter_sha256']:
        raise ValueError('G1_0078_ADAPTER_CHANGED')
    export={name:{arm:previous._export_model(model) for arm,model in arms.items()}
            for name,arms in fitted.items()}
    result={'schema':SCHEMA,'status':'RESEARCH_COMPARISON_NOT_CERTIFIED',
        'sealed_0077_original_report_sha256':previous_atlas['sealed_report_sha256'],
        'sealed_0077_outcome_is_frozen_historical_evidence':True,
        'sealed_0077_cases_used_in_fitting_or_selection':False,
        'original_dataset_sha256':manifest['original_dataset_sha256'],
        'frozen_discrimination_report_sha256':manifest['frozen_report_sha256'],
        'frozen_initial_interface_parameters_sha256':{
            name:report['learned_interface_parameters'][name]['staged_joint']['sha256']
            for name in sealed.REQUIRED_MODEL_NAMES},
        'new_generator_seed':seed,'generator_sha256':manifest['generator_sha256'],
        'new_prompt_digest':hashlib.sha256(''.join(hashes).encode('ascii')).hexdigest(),
        'new_cohorts':{k:len(v) for k,v in cohorts.items()},'new_prompt_overlap':0,
        'sealed_0077_neural_candidate_error_attribution':diagnostic_stats,
        'sealed_0077_failure_cases_inspected_only_after_scoring':len(diagnostic_records),
        'arms':ARMS,'selected':chosen,'training':fitlogs,'metrics':phases,
        'learned_interface_parameters':export,
        'selected_audit_results_are_not_sealed_certification':True,
        'audit_exact_at_least_80pct_diagnostic_only':{
            name:phases['audit'][name]['selected_metrics']['exact_prompts']/
                 phases['audit'][name]['selected_metrics']['cases']>=0.8
            for name in sealed.REQUIRED_MODEL_NAMES},
        'original_0077_test_reused_for_selection':False,
        'head_weights_modified':False,'qwen_adapter_modified':False,
        'production_promotion':False,'g1_certified':False,'g2_unlocked':False}
    out.mkdir(parents=True,exist_ok=False)
    _write_json(out/'report.json',result)
    _write_rows(out/'cases.jsonl',case_records)
    _write_rows(out/'sealed_failure_candidates.jsonl',diagnostic_records)
    print('G1_0078_RESULT '+json.dumps({'status':result['status'],
        'selected':chosen,'audit':{n:{'control_exact':phases['audit'][n]['control_metrics']['exact_prompts'],
        'selected_exact':phases['audit'][n]['selected_metrics']['exact_prompts'],
        'f1':round(phases['audit'][n]['selected_metrics']['f1'],5)}
        for n in sealed.REQUIRED_MODEL_NAMES}},sort_keys=True),flush=True)
    return result


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    sub=p.add_subparsers(dest='command',required=True)
    d=sub.add_parser('diagnose',help='Read-only atlas of already-unsealed G1-0077 failures')
    for key in ('seal','predictions','scored','output'):
        d.add_argument('--'+key,required=True)
    b=sub.add_parser('bakeoff',help='Train competing interfaces on NEW disjoint examples')
    for key in ('diagnostic-reference','seal','predictions','scored','dataset','legacy','cutover',
                'structural-reference','learned-reference','coverage-reference',
                'discrimination-reference','output'):
        b.add_argument('--'+key,required=True)
    b.add_argument('--device',default='cuda')
    b.add_argument('--seed',type=int,default=DEFAULT_SEED)
    b.add_argument('--train',type=int,default=96)
    b.add_argument('--selection',type=int,default=32)
    b.add_argument('--audit',type=int,default=64)
    b.add_argument('--hard-steps',type=int,default=120)
    b.add_argument('--structured-epochs',type=int,default=14)
    b.add_argument('--max-length',type=int,default=2048)
    values=vars(p.parse_args(argv));command=values.pop('command')
    return {'diagnose':diagnose,'bakeoff':bakeoff}[command](**values)

if __name__=='__main__':main()
