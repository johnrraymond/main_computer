"""G1-0080: independent, read-only audit of G1-0079 prospective evidence.

Recompute predictions vs committed gold without importing the experiment scorer.
Verify SHA-256 lineage, one-time seal, fitted parameter bundles, quantity
retention and original G1 stage gates. Never produce a certifying stage
certificate, mutate frozen artifacts, train, promote, or authorize G2.
"""
from __future__ import annotations

import argparse
import ast
from collections import Counter, defaultdict
import hashlib
import json
import math
from pathlib import Path

SCHEMA = 'nanojev.gamer.graph.g1.read-only-certification-audit-0080.v1'
SOURCE_SCHEMA = 'nanojev.gamer.graph.g1.quantity-preserving-bakeoff-0079.v1'
EXPECTED_MODELS = ('cutover', 'legacy')
REQUIRED_KINDS = ('actor', 'action', 'quantity')
ALL_KINDS = ('actor', 'action', 'quantity', 'condition', 'outcome', 'state', 'other')
QUANTITY = 'quantity'
SELECTION_KINDS = ('actor', 'action', 'quantity', 'outcome')
CONTROL_ARM = 'frozen_0078_selected'
ARMS = (CONTROL_ARM,'historical_continued_64_unprotected',
        'pinned_historical_continued_64','pinned_structured_1x',
        'pinned_quantity_margin_2x','pinned_quantity_margin_4x')
SELECTABLE_ARMS=(CONTROL_ARM,)+ARMS[2:]


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024*1024), b''):
            h.update(chunk)
    return h.hexdigest()


def hash_text(value):
    return hashlib.sha256(value.encode('utf8')).hexdigest()


def object_hash(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,allow_nan=False,
             separators=(',',':')).encode('utf8')).hexdigest()


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf8'))


def read_jsonl(path):
    return [json.loads(s) for s in Path(path).read_text(encoding='utf8').splitlines() if s.strip()]


def require(check, code):
    if not check:
        raise ValueError('G1_0080_'+code)


def match_hash(path, expected, code):
    require(isinstance(expected,str) and len(expected)==64, code+'_NO_COMMITMENT')
    actual = sha(path)
    require(actual==expected,code+'_DRIFT')
    return actual


def frozen_gate_requirements(curriculum_path):
    """Read the actual declared stage gate from source, without importing torch."""
    parsed=ast.parse(Path(curriculum_path).read_text(encoding='utf8'))
    for node in parsed.body:
        if isinstance(node,ast.Assign) and any(isinstance(t,ast.Name) and
                     t.id=='GATE_REQUIREMENTS' for t in node.targets):
            gates=ast.literal_eval(node.value)
            result=gates['G1']
            require(set(result)=={'metric','dev','challenge','macro_f1','kind_recall',
                    'neural_gain'} and result['metric']=='object_exact_rate',
                    'ORIGINAL_GATE_CHANGED')
            return result
    raise ValueError('G1_0080_ORIGINAL_GATE_NOT_FOUND')


def frozen_span_kinds(contract_path):
    parsed=ast.parse(Path(contract_path).read_text(encoding='utf8'))
    for node in parsed.body:
        if isinstance(node,ast.Assign) and any(isinstance(t,ast.Name) and
                t.id=='SPAN_KINDS' for t in node.targets):
            return tuple(ast.literal_eval(node.value))
    raise ValueError('G1_0080_CANONICAL_SPAN_KINDS_NOT_FOUND')


def checked_objects(raw, length, tag):
    require(isinstance(raw,list),tag+'_NOT_LIST')
    output=set()
    for x in raw:
        require(isinstance(x,list) and len(x)==3,tag+'_SHAPE')
        a,b,k=x
        require(type(a) is int and type(b) is int and 0<=a<b<=length and
                isinstance(k,str) and k in ALL_KINDS,tag+'_BOUND_OR_KIND')
        require((a,b,k) not in output,tag+'_DUPLICATE')
        output.add((a,b,k))
    return output


def _assigned_int(path, name):
    parsed=ast.parse(Path(path).read_text(encoding='utf8'))
    for node in parsed.body:
        if isinstance(node,ast.Assign) and any(isinstance(t,ast.Name) and t.id==name
                for t in node.targets):
            value=ast.literal_eval(node.value)
            require(type(value) is int and value>0,'CANONICAL_DIM_'+name)
            return value
    raise ValueError('G1_0080_MISSING_DIMENSION_'+name)


def _canonical_shape():
    base=Path(__file__).parent
    return (_assigned_int(base/'nanojev_gamer_v1_g1_candidate_coverage_bakeoff_0075.py','PROP_DIM') +
            _assigned_int(base/'nanojev_gamer_v1_g1_structural_learning_bakeoff_0074.py','DIM') +
            1+len(ALL_KINDS),
            _assigned_int(base/'nanojev_gamer_v1_g1_discrimination_trainability_bakeoff_0076.py','HIDDEN'))


def selection_replay(metrics_by_arm):
    """Reapply the G1-0079 predeclared model selector to recorded selection metrics.

    These per-arm summaries have no independent case predictions, therefore
    this is a consistency check on the claim, not a new evaluation.
    """
    require(set(metrics_by_arm)==set(ARMS),'SELECTION_ARM_CATALOG')
    baseline=metrics_by_arm[CONTROL_ARM]
    winners=[CONTROL_ARM]
    for arm in SELECTABLE_ARMS[1:]:
        cand=metrics_by_arm[arm]
        def usable(t,key):
            x=t.get(key)
            require(type(x) in (float,int) and math.isfinite(x),
                    'SELECTION_METRIC_'+key)
            return x
        if (usable(cand,'exact_prompts') >= usable(baseline,'exact_prompts') and
            usable(cand,'f1')>=usable(baseline,'f1')-1e-12 and
            usable(cand,'precision')>=usable(baseline,'precision')-.005 and
            all(cand.get('by_kind',{}).get(k,{}).get('recall',0)>=
                baseline.get('by_kind',{}).get(k,{}).get('recall',0)-1e-12
                for k in SELECTION_KINDS) and
            (cand['exact_prompts']>baseline['exact_prompts'] or
             cand['f1']>baseline['f1']+1e-10)):
            winners.append(arm)
    return max(winners,key=lambda arm:(metrics_by_arm[arm]['exact_prompts'],
        metrics_by_arm[arm]['f1'],metrics_by_arm[arm]['precision'],-ARMS.index(arm)))


def bundle_integrity(bundle,tag):
    """Validate exact exported coefficients without requiring GPU/PyTorch."""
    require(isinstance(bundle,dict) and isinstance(bundle.get('parameters'),dict),tag+'_BUNDLE')
    p=bundle['parameters']
    require(object_hash(p)==bundle.get('sha256'),tag+'_HASH')
    require(set(p)=={'hidden','proj','type_offset','new_offset'},tag+'_KEYS')
    dim,hidden=_canonical_shape()
    require(len(p['type_offset'])==len(ALL_KINDS) and len(p['new_offset'])==1 and
            len(p['proj'])==hidden and len(p['hidden'])==dim,tag+'_SHAPE')
    require(all(isinstance(row,list) and len(row)==hidden for row in p['hidden']),tag+'_HIDDEN_SHAPE')
    for seq in [*p['hidden'],p['proj'],p['type_offset'],p['new_offset']]:
        require(all(type(x) in (float,int) and math.isfinite(x) and abs(x)<=8.000001
                    for x in seq),tag+'_VALUE')


def metrics(pairs):
    """Independent counting, rather than calling _summarize from G1-0079."""
    total=len(pairs); exact=correct=pred_count=gold_count=0
    kinds=defaultdict(lambda:Counter())
    families=defaultdict(lambda:Counter())
    for pred,gold,family in pairs:
        common=pred&gold
        exact+=int(pred==gold);correct+=len(common);pred_count+=len(pred);gold_count+=len(gold)
        families[family]['total']+=1;families[family]['correct']+=int(pred==gold)
        for _,_,kind in pred:kinds[kind]['predicted']+=1
        for _,_,kind in gold:kinds[kind]['total']+=1
        for _,_,kind in common:kinds[kind]['correct']+=1
    precision=correct/pred_count if pred_count else 0.
    recall=correct/gold_count if gold_count else 0.
    f1=2*correct/(pred_count+gold_count) if pred_count+gold_count else 0.
    details={}
    weighted=[]
    for kind in ALL_KINDS:
        k=kinds[kind]
        if not k['total']:continue
        kprecision=k['correct']/k['predicted'] if k['predicted'] else 0.
        krecall=k['correct']/k['total']
        kf1=2*k['correct']/(k['predicted']+k['total']) if k['predicted']+k['total'] else 0.
        weighted.append(kf1)
        details[kind]={'correct':k['correct'],'total':k['total'],
                      'predicted':k['predicted'],'recall':krecall,
                      'precision':kprecision,'f1':kf1}
    return {'cases':total,'exact_prompts':exact,'object_exact_rate':exact/total if total else 0.,
            'precision':precision,'recall':recall,'f1':f1,
            'object_macro_f1':sum(weighted)/len(weighted) if weighted else 0.,
            'correct':correct,'predicted':pred_count,'expected':gold_count,
            'incorrect_objects':gold_count-correct,
            'false_positive_objects':pred_count-correct,'missing_objects':gold_count-correct,
            'by_kind':details,'by_family':{n:{'correct':v['correct'],'total':v['total'],
                         'rate':v['correct']/v['total']} for n,v in sorted(families.items())}}


def compare_score(recomputed, supplied,tag):
    """Assert every field in the original scorer's summary, including by_kind."""
    for field in ('cases','exact_prompts','object_exact_rate','precision','recall','f1',
                  'correct','predicted','expected','incorrect_objects','false_positive_objects'):
        a,b=recomputed[field],supplied.get(field)
        require(type(b) in (float,int) and (abs(a-b)<1e-10 if isinstance(a,float) else a==b),
                tag+'_METRIC_'+field.upper())
    for kind in ALL_KINDS:
        ours=recomputed['by_kind'].get(kind)
        theirs=supplied.get('by_kind',{}).get(kind)
        require((ours is None)==(theirs is None),tag+'_KIND_'+kind)
        if ours:
            for field in ('correct','total','recall'):
                a,b=ours[field],theirs.get(field)
                require(type(b) in (float,int) and (abs(a-b)<1e-10 if field=='recall' else a==b),
                        tag+'_KIND_'+kind+'_'+field)


def _case_ledger(cases):
    gains=Counter(); losses=Counter();false_new=Counter();false_removed=Counter()
    won=lost=0;flips=[]
    for item in cases:
        ctl,chosen,gold=item['control'],item['selected'],item['gold']
        a=(chosen&gold)-(ctl&gold)
        b=(ctl&gold)-(chosen&gold)
        c=(chosen-gold)-(ctl-gold)
        d=(ctl-gold)-(chosen-gold)
        for x in a:gains[x[2]]+=1
        for x in b:losses[x[2]]+=1
        for x in c:false_new[x[2]]+=1
        for x in d:false_removed[x[2]]+=1
        control_exact=ctl==gold;selected_exact=chosen==gold
        won+=selected_exact and not control_exact
        lost+=control_exact and not selected_exact
        if control_exact!=selected_exact or a or b or c or d:
            flips.append({'case_id':item['case_id'],'family':item['family'],
                'control_exact':control_exact,'selected_exact':selected_exact,
                'gained_correct':len(a),'lost_correct':len(b),
                'new_false':len(c),'removed_false':len(d)})
    # Exact binomial two-sided sign test on discordant examples, descriptive only.
    n=won+lost
    p=min(1.,2*sum(math.comb(n,k) for k in range(min(won,lost)+1))/2**n) if n else 1.
    return {'exact_cases_gained':int(won),'exact_cases_lost':int(lost),
            'paired_exact_two_sided_p_exploratory':p,'newly_correct_by_kind':dict(gains),
            'lost_correct_by_kind':dict(losses),'new_false_by_kind':dict(false_new),
            'removed_false_by_kind':dict(false_removed),'changed_cases':flips}


def _read_frozen_artifacts(*,seal,predictions,scored,seal_0077,reliability_reference,
                            discrimination_reference,dataset,legacy,cutover,cutover_certificate):
    """Require all inputs committed, mutually consistent, unchanged; no writes."""
    seal=Path(seal);predictions=Path(predictions);scored=Path(scored)
    oldseal=Path(seal_0077);rel=Path(reliability_reference);disc=Path(discrimination_reference)
    source=Path(__file__).with_name('nanojev_gamer_v1_g1_quantity_preserving_bakeoff_0079.py')
    generator=Path(__file__).with_name('nanojev_gamer_v1_graph_corpus.py')
    curriculum=Path(__file__).with_name('nanojev_gamer_v1_graph_curriculum.py')
    gate=frozen_gate_requirements(curriculum)
    require(frozen_span_kinds(Path(__file__).with_name('nanojev_gamer_v1_graph_contract.py'))==
            ALL_KINDS,'CANONICAL_SPAN_KINDS_DRIFT')
    manifest=read_json(seal/'manifest.json')
    blind=read_json(predictions/'report.json')
    results=read_json(scored/'report.json')
    unsealed=read_json(seal/'UNSEALED.json')
    older=read_json(oldseal/'manifest.json')
    reliability=read_json(rel/'report.json')
    discr=read_json(disc/'report.json')
    prior_cert=read_json(cutover_certificate)
    require(manifest.get('schema')==SOURCE_SCHEMA and
            blind.get('schema')==SOURCE_SCHEMA and
            results.get('schema')==SOURCE_SCHEMA,'SCHEMA')
    require(manifest.get('status')=='AUDIT_COMMITTED_NOT_PREDICTED' and
            blind.get('status')=='AUDIT_BLIND_PREDICTIONS_LOCKED_NOT_SCORED' and
            results.get('status')=='PROSPECTIVE_AUDIT_SCORED_ONCE_NO_PROMOTION' and
            unsealed.get('status')=='SCORED_ONCE','PROTOCOL_STATE')
    for name,flag,value in [('manifest','audit_answers_used_in_fit_or_selection',False),
         ('manifest','evaluation_is_certification',False),('blind','audit_answers_opened_during_fit',False),
         ('blind','audit_gold_used_for_selection_or_inference',False),
         ('blind','head_weights_modified',False),('blind','g1_certified',False),
         ('blind','g2_unlocked',False),('blind','production_promotion',False),
         ('result','audit_used_for_fit_or_model_selection',False),
         ('result','certification_authorized',False),('result','g1_certified',False),
         ('result','g2_unlocked',False),('result','production_promotion',False)]:
        where={'manifest':manifest,'blind':blind,'result':results}[name]
        require(where.get(flag) is value,'FLAG_'+name+'_'+flag)
    require(results.get('audit_same_generator_distribution') is True and
            blind.get('new_audit_is_not_certification') is True,'SCOPE')
    for p,expect,tag in [
       (source,manifest['runner_sha256'],'RUNNER'),
       (generator,manifest['generator_sha256'],'GENERATOR'),
       (dataset,manifest['original_dataset_sha256'],'DATASET'),
       (oldseal/'manifest.json',manifest['seal_0077_manifest_sha256'],'PREVIOUS_SEAL'),
       (rel/'report.json',manifest['reliability_report_sha256'],'RELIABILITY_REPORT'),
       (rel/'cases.jsonl',manifest['reliability_cases_sha256'],'RELIABILITY_CASES'),
       (disc/'report.json',manifest['discrimination_report_sha256'],'DISCRIMINATION'),
       (seal/'train.jsonl',manifest['train_sha256'],'TRAIN'),
       (seal/'selection.jsonl',manifest['selection_sha256'],'SELECTION'),
       (seal/'audit.prompts.jsonl',manifest['audit_prompts_sha256'],'PROMPTS'),
       (seal/'audit.answers.SECRET.jsonl',manifest['committed_audit_answers_sha256'],'ANSWERS'),
       (predictions/'audit.predictions.jsonl',blind['prediction_sha256'],'PREDICTIONS'),
       (seal/'manifest.json',blind['seal_manifest_sha256'],'MANIFEST_FROM_BLIND'),
       (predictions/'report.json',results['selection_report_sha256'],'BLIND_FROM_SCORE')]:
        match_hash(p,expect,tag)
    require(blind.get('committed_audit_answers_sha256')==manifest['committed_audit_answers_sha256'] and
            results.get('precommitted_answers_sha256')==manifest['committed_audit_answers_sha256'] and
            blind.get('audit_prompts_sha256')==manifest['audit_prompts_sha256'] and
            results.get('prediction_sha256')==blind['prediction_sha256'] and
            unsealed.get('prediction_sha256')==blind['prediction_sha256'],
            'CHAIN_OF_CUSTODY')
    require(unsealed.get('scored_report_sha256')==sha(scored/'report.json'),
            'SCORING_MARKER')
    require(manifest.get('discrimination_report_sha256')==older.get('frozen_report_sha256') and
            older.get('checkpoint_sha256')==discr.get('lineage',{}).get('checkpoint_sha256') and
            older.get('original_dataset_sha256')==manifest.get('original_dataset_sha256') and
            older.get('generator_sha256')==manifest.get('generator_sha256'),
            'UPSTREAM_LINEAGE')
    match_hash(oldseal/'prompts.jsonl',older.get('prompts_sha256'),'EARLIER_PROSPECTIVE_PROMPTS')
    match_hash(Path(__file__).with_name('nanojev_gamer_v1_g1_sealed_generalization_0077.py'),
               older.get('sealed_runner_sha256'),'EARLIER_SEALED_RUNNER')
    require(reliability.get('status')=='RESEARCH_COMPARISON_NOT_CERTIFIED' and
            discr.get('status')=='EXPLORATORY_TRAINABLE_NOT_CERTIFIED',
            'UPSTREAM_STATUS')
    require(manifest.get('old_0077_0078_original_prompt_overlap')==0 and
            manifest.get('prompt_hashes_sha256') is not None,'PROMPT_PROVENANCE')
    require(prior_cert.get('status')=='DEVELOPMENT_GATE_BLOCKED' and
            prior_cert.get('certified_stage')=='G1' and
            prior_cert.get('next_stage') is None and
            prior_cert.get('head_sha256')==older['checkpoint_sha256']['cutover'],
            'EARLIER_GATE_CERTIFICATE')
    for name,path in (('cutover',cutover),('legacy',legacy)):
        record=read_json(Path(path)/'manifest.json')
        expected=older['checkpoint_sha256'][name]
        require(record.get('stage')=='G1' and record.get('status')=='TRAINED_NOT_CERTIFIED' and
                record.get('head_sha256')==expected and
                record.get('dataset_sha256')==sha(dataset) and
                record.get('source_adapter_sha256')==older.get('source_adapter_sha256'),
                'HEAD_MANIFEST_'+name)
        match_hash(Path(path)/'gamer_graph_head.safetensors',expected,'HEAD_'+name)
    require(set(blind.get('selected',{}))==set(EXPECTED_MODELS) and
            blind['selected']==results.get('selected'),'SELECTED_LINEAGE')
    require(blind['selected'].get('cutover')=='pinned_historical_continued_64' and
            blind['selected'].get('legacy')=='pinned_quantity_margin_4x',
            'EXPECTED_SELECTED_ARMS')
    for name in EXPECTED_MODELS:
        s_metrics=blind.get('selection_metrics',{}).get(name)
        require(isinstance(s_metrics,dict) and
                selection_replay(s_metrics)==blind['selected'][name],
                'SELECTION_REPLAY_'+name)
    require(results.get('audit_cases')==manifest.get('cohorts',{}).get('audit')==128 and
            manifest['cohorts'].get('train')==96 and
            manifest['cohorts'].get('selection')==32,'COHORT_COUNTS')
    # Independently validate every saved fitted coefficient bundle, including unused competitors.
    for name,bundles in blind.get('trained_coefficients',{}).items():
        require(name in EXPECTED_MODELS and set(bundles)=={
                'pinned_structured_1x','pinned_quantity_margin_2x','pinned_quantity_margin_4x'},
                'TRAINED_BUNDLE_KEYS')
        for label,bundle in bundles.items():bundle_integrity(bundle,name+'_'+label)
    require(set(blind.get('trained_coefficients',{}))==set(EXPECTED_MODELS),
            'MISSING_TRAINED_BUNDLES')
    require(blind.get('quantity_guard_guarantee') and
            blind.get('historical_unprotected_continued_is_diagnostic_only') is True,
            'QUANTITY_GUARD_DECLARATION')
    # Snapshot input fingerprints so code can prove it did not modify any evidence.
    files=[seal/'manifest.json',seal/'UNSEALED.json',seal/'audit.prompts.jsonl',
           seal/'audit.answers.SECRET.jsonl',seal/'train.jsonl',seal/'selection.jsonl',
           predictions/'audit.predictions.jsonl',predictions/'report.json',
           scored/'report.json',scored/'cases.jsonl',rel/'report.json',disc/'report.json',
           oldseal/'manifest.json',oldseal/'prompts.jsonl',
           Path(cutover_certificate),source,generator,curriculum,
           Path(dataset),Path(cutover)/'gamer_graph_head.safetensors',
           Path(legacy)/'gamer_graph_head.safetensors']
    before={str(p.resolve()):sha(p) for p in files}
    return manifest,blind,results,gate,before


def audit(*,seal,predictions,scored,seal_0077,reliability_reference,
          discrimination_reference,dataset,legacy,cutover,cutover_certificate,output):
    target=Path(output).resolve()
    require(not target.exists(),'IMMUTABLE_OUTPUT_EXISTS')
    protected=[Path(p).resolve() for p in (seal,predictions,scored,seal_0077,
              reliability_reference,discrimination_reference,legacy,cutover)]
    require(all(target!=p and p not in target.parents for p in protected),
            'OUTPUT_INSIDE_PROTECTED_EVIDENCE')
    manifest,blind,scored_report,gate,before=_read_frozen_artifacts(
        seal=seal,predictions=predictions,scored=scored,seal_0077=seal_0077,
        reliability_reference=reliability_reference,
        discrimination_reference=discrimination_reference,dataset=dataset,
        legacy=legacy,cutover=cutover,cutover_certificate=cutover_certificate)
    seal_dir=Path(seal);blind_dir=Path(predictions)
    prompts=read_jsonl(seal_dir/'audit.prompts.jsonl')
    gold_rows=read_jsonl(seal_dir/'audit.answers.SECRET.jsonl')
    pred_rows=read_jsonl(blind_dir/'audit.predictions.jsonl')
    training=read_jsonl(seal_dir/'train.jsonl')
    selection=read_jsonl(seal_dir/'selection.jsonl')
    require(len(prompts)==len(gold_rows)==128 and len(pred_rows)==256 and
            len(training)==96 and len(selection)==32,'EXACT_ROW_COUNTS')
    prompt_ids={};gold_by_id={};prompt_digests=set();all_digests=[]
    for phase,rows in [('train',training),('selection',selection),('audit',prompts)]:
        for index,row in enumerate(rows):
            require(isinstance(row.get('prompt'),str) and row['prompt'] and
                    row.get('family') in ('encounter','allocation') and
                    row.get('prompt_sha256')==hash_text(row['prompt']) and
                    row['prompt_sha256'] not in prompt_digests,
                    'DISJOINT_PROMPT_'+phase)
            prompt_digests.add(row['prompt_sha256']);all_digests.append(row['prompt_sha256'])
            if phase=='audit':
                ident=f'prospective-{index:05d}'
                require(row.get('case_id')==ident and
                        set(row)=={'case_id','prompt','prompt_sha256','family'},
                        'PROMPTS_ONLY_AND_ORDER')
                prompt_ids[ident]=row
    require(hash_text(''.join(all_digests))==manifest['prompt_hashes_sha256'],
            'COHORT_DIGEST')
    original=read_jsonl(dataset)
    original_hashes={hash_text(r['prompt']) for r in original}
    older_prompts=read_jsonl(Path(seal_0077)/'prompts.jsonl')
    old_hashes={r['prompt_sha256'] for r in older_prompts}
    reliability_cases=read_jsonl(Path(reliability_reference)/'cases.jsonl')
    previous_hashes={r['prompt_sha256'] for r in reliability_cases}
    require(len(old_hashes)==len(older_prompts) and len(previous_hashes)>0 and
            len(original_hashes)>0 and
            not prompt_digests.intersection(original_hashes|old_hashes|previous_hashes),
            'ORIGINAL_OR_PRIOR_COHORT_OVERLAP')
    for prompt,gold in zip(prompts,gold_rows):
        require(gold.get('case_id')==prompt['case_id'] and
                gold.get('prompt_sha256')==prompt['prompt_sha256'] and
                gold.get('family')==prompt['family'] and
                set(gold)=={'case_id','family','prompt_sha256','gold'},
                'ANSWER_TO_PROMPT_BINDING')
        gold_by_id[prompt['case_id']]=checked_objects(gold['gold'],len(prompt['prompt']),
                                                      'GOLD_'+prompt['case_id'])
    groups={n:[] for n in EXPECTED_MODELS}
    comparisons={n:[] for n in EXPECTED_MODELS};seen=set();paired_cases=[]
    for r in pred_rows:
        model=r.get('model');ident=r.get('case_id')
        require(model in EXPECTED_MODELS and ident in prompt_ids and
                (model,ident) not in seen,'PREDICTION_IDENTITY')
        seen.add((model,ident));p=prompt_ids[ident]
        require(set(r)=={'case_id','model','prompt_sha256','family','control','selected'}
                and r['prompt_sha256']==p['prompt_sha256'] and r['family']==p['family'],
                'PREDICTION_BINDING')
        ctl=checked_objects(r['control'],len(p['prompt']),'CONTROL')
        sel=checked_objects(r['selected'],len(p['prompt']),'SELECTED')
        gold=gold_by_id[ident]
        require({x for x in ctl if x[2]==QUANTITY}=={x for x in sel if x[2]==QUANTITY},
                'QUANTITY_NOT_PRESERVED')
        groups[model].append((ctl,sel,gold,p['family']))
        comparisons[model].append({'case_id':ident,'family':p['family'],
                                   'control':ctl,'selected':sel,'gold':gold})
        paired_cases.append({'model':model,'case_id':ident,
            'control_exact':ctl==gold,'selected_exact':sel==gold,
            'recovered_correct':sorted([list(x) for x in (sel&gold)-(ctl&gold)]),
            'lost_correct':sorted([list(x) for x in (ctl&gold)-(sel&gold)]),
            'new_false':sorted([list(x) for x in (sel-gold)-(ctl-gold)]),
            'removed_false':sorted([list(x) for x in (ctl-gold)-(sel-gold)])})
    require(len(seen)==2*len(prompts),'PREDICTION_COMPLETENESS')
    recomputed={};paired={};observations={}
    for model in EXPECTED_MODELS:
        data=groups[model]
        ctrl=metrics([(a,c,f) for a,b,c,f in data]);sel=metrics([(b,c,f) for a,b,c,f in data])
        recomputed[model]={'control':ctrl,'selected':sel}
        for kind in ALL_KINDS:
            if kind in ctrl['by_kind'] or kind in sel['by_kind']:
                require(ctrl['by_kind'].get(kind,{}).get('total')==
                        sel['by_kind'].get(kind,{}).get('total'),
                        'GOLD_DENOMINATOR_DRIFT_'+kind)
        compare_score(ctrl,scored_report['metrics'][model]['control'],model+'_CONTROL')
        compare_score(sel,scored_report['metrics'][model]['selected'],model+'_SELECTED')
        paired[model]=_case_ledger(comparisons[model])
        prospective={'exact_rate_at_least_dev_threshold':sel['object_exact_rate']>=gate['dev'],
            'macro_f1_at_least_stage_threshold':sel['object_macro_f1']>=gate['macro_f1'],
            'all_mandatory_kind_recall_at_least_threshold':all(
                sel['by_kind'].get(k,{}).get('recall',0)>=gate['kind_recall']
                for k in REQUIRED_KINDS),
            'family_diversity':len(sel['by_family'])>=2,
            'selected_quantity_exactly_preserves_control':True,
            'selection_required_kind_recall_nonregression':all(
                sel['by_kind'].get(k,{}).get('recall',0)>=
                ctrl['by_kind'].get(k,{}).get('recall',0)-1e-12 for k in SELECTION_KINDS),
            'object_f1_nonregression':sel['f1']>=ctrl['f1']-1e-12,
            'exact_set_nonregression':sel['exact_prompts']>=ctrl['exact_prompts']}
        observations[model]=prospective
    record_rows=read_jsonl(Path(scored)/'cases.jsonl')
    require(len(record_rows)==256,'SCORE_CASE_ROWS_COUNT')
    mapped={(r['model'],r['case_id']):r for r in record_rows}
    require(len(mapped)==len(record_rows) and set(mapped)==seen,'SCORE_CASE_KEYS')
    for item in paired_cases:
        original=mapped[(item['model'],item['case_id'])]
        for field in ('control_exact','selected_exact','recovered_correct','lost_correct',
                      'new_false','removed_false'):
            require(original[field]==item[field],
                    'SCORE_CASE_RECOMPUTE_'+field)
    # Nothing has been mutated by the read-only auditor before emitting output.
    for path,digest in before.items():require(sha(path)==digest,'SOURCE_MUTATION_DETECTED')
    blockers=[
       'G1-0079 prospective audit is not the declared dev split under the current interface',
       'G1-0079 prospective audit is not the declared challenge split under the current interface',
       'no current-interface zero-hidden ablation with >=8 cases and >= neural_gain',
       'no current-interface stage-component ablation with >= neural_gain',
       'original G1 developmental gate certificate remains DEVELOPMENT_GATE_BLOCKED',
       'G1-0079 training and decoder interface are not contained in a new certified G1 head checkpoint',
       'same known generator distribution; independent cross-distribution capability is not established']
    overall={'schema':SCHEMA,'status':'READ_ONLY_EVIDENCE_VERIFIED_NOT_CERTIFIED',
       'scope':'PROSPECTIVE_0079_RECOMPUTATION_NOT_G1_ADVANCEMENT_CERTIFICATE',
       'integrity':'PASS','quantity_invariance':'PASS',
       'source_gate_requirements':gate,
       'verified_commitments':before,
       'models':recomputed,'paired_nonregression':paired,
       'prospective_gate_observations':observations,
       'formal_g1_advancement_gate':'NOT_SATISFIED_BY_THIS_EVIDENCE',
       'formal_gate_blockers':blockers,
       'dev_split_evaluated_for_current_interface':False,
       'challenge_split_evaluated_for_current_interface':False,
       'required_causal_ablations_evaluated_for_current_interface':False,
       'quantity_pinning_may_preserve_original_quantity_errors':True,
       'prospective_audit_already_unsealed':True,
       'audit_used_to_train_or_select':False,
       'g1_certified':False,'g2_unlocked':False,'production_promotion':False,
       'artifact_created_by':'G1-0080 read-only standalone stdlib evaluator',
       'output_is_not_a_developmental_certificate':True}
    target.mkdir(parents=True,exist_ok=False)
    (target/'report.json').write_text(json.dumps(overall,sort_keys=True,indent=2,
                                                allow_nan=False)+'\n',encoding='utf8')
    # Only case counts and deltas in report; per-case atlas here for inspection.
    (target/'integrity.json').write_text(json.dumps({'status':'PASS','source_sha256':before},
                 sort_keys=True,indent=2)+'\n',encoding='utf8')
    print('G1_0080_AUDIT '+json.dumps({'status':overall['status'],
        'cutover':{'exact':recomputed['cutover']['selected']['exact_prompts'],
                   'of':len(prompts),'f1':round(recomputed['cutover']['selected']['f1'],6),
                   'macro_f1':round(recomputed['cutover']['selected']['object_macro_f1'],6),
                   'actor_recall':round(recomputed['cutover']['selected']['by_kind'].get('actor',{}).get('recall',0),6),
                   'action_recall':round(recomputed['cutover']['selected']['by_kind'].get('action',{}).get('recall',0),6),
                   'quantity_recall':round(recomputed['cutover']['selected']['by_kind'].get('quantity',{}).get('recall',0),6)},
        'g1_certified':False,'g2_unlocked':False},sort_keys=True),flush=True)
    return overall


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    for field in ('seal','predictions','scored','seal-0077','reliability-reference',
                  'discrimination-reference','dataset','legacy','cutover',
                  'cutover-certificate','output'):
        parser.add_argument('--'+field,required=True)
    return audit(**vars(parser.parse_args(argv)))


if __name__=='__main__':
    main()
