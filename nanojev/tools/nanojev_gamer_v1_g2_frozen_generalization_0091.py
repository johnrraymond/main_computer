"""G2-0091: frozen G2-0090 eight-graph checkpoint vs G2-0087/0088.

Run from nanojev:
    python -m tools.nanojev_gamer_v1_g2_frozen_generalization_0091 run --device cuda

Read-only with respect to parent checkpoints. Fresh same-generator gold is sealed
before a separate gold-blind inference process; results are diagnostic, NOT G2
certification. No decoder refitting, optimization, promotion, or G3 unlock.
"""
from __future__ import annotations

import argparse
from collections import Counter,defaultdict
import hashlib
import json
from pathlib import Path
import random
import subprocess
import sys

import torch

from tools import nanojev_gamer_v1_g2_trainability_diagnostic_0090 as trainability
from tools import nanojev_gamer_v1_g2_structural_learning_0089 as structural
from tools import nanojev_gamer_v1_g2_posttrain_graph_bakeoff_0088 as bakeoff
from tools import nanojev_gamer_v1_g2_hard_negative_learning_0087 as hard
from tools import nanojev_gamer_v1_g2_causal_activation_fuzz_0085 as fuzz
from tools import nanojev_gamer_v1_graph_curriculum as cur
from tools import nanojev_gamer_v1_graph_corpus as corpus
from tools import nanojev_gamer_v1_g1_native_composite_0082 as native_g1
from tools import nanojev_gamer_v1_backbone_first as backbone

SCHEMA='nanojev.gamer.graph.g2.frozen-generalization-0091.v1'
MODULE='tools.nanojev_gamer_v1_g2_frozen_generalization_0091'
SEED=910091
DEV=96
CHALLENGE=64
FUZZ_PAIRS=8
HISTORICAL_DEV=80
HISTORICAL_CHALLENGE=32
HISTORICAL=HISTORICAL_DEV+HISTORICAL_CHALLENGE
ARMS=('g2_0087_native','g2_0088_joint','g2_0090_native','g2_0090_joint')
SPLITS=('dev','challenge','historical_dev','historical_challenge')
ROOT_DEFAULT='../runtime/reports/gamer-v1'


def require(condition,reason):
    if not condition:raise ValueError('G2_0091_'+reason)


def sha(path):return hard.sha(path)
def js(path):return hard.js(path)
def write_json(path,obj):return hard.write_json(path,obj)
def read_jsonl(path):return hard.read_jsonl(path)
def write_jsonl(path,rows):return hard.write_jsonl(path,rows)


def _roots(root):
    paths=structural.paths_for(root)
    paths['parent_0090']=Path(root)/'oracle-fabric-g2-progressive-trainability-0090'
    return paths


def verify_parent(paths):
    """Validate model lineage and every saved stage-8 payload, fail-closed."""
    parent_manifest,spec=structural.preflight(paths)
    dest=paths['parent_0090']
    report=js(dest/'report.json')
    prov=js(dest/'provenance.json')
    lock=js(dest/'stage-8'/'lock.json')
    summary=js(dest/'stage-8'/'summary.json')
    sel=js(paths['parent_0088']/'selection.json')
    require(sel.get('status')=='DECODER_FROZEN_BEFORE_NEW_INFERENCE' and
            sel.get('winner_method')=='joint' and spec==sel['winner'] and
            prov.get('decoder')==spec,'FROZEN_DECODER_CHANGED')
    require(report.get('schema')==trainability.SCHEMA and
            report.get('status') in ('EIGHT_GRAPH_MEMORIZATION_WITH_INTERMEDIATE_GAPS',
                 'PROGRESSIVE_ALL_STAGES_PASSED_NOT_CERTIFIED') and
            report.get('eight_exact')==8 and report.get('eight_graph_errors')==0 and
            report.get('progressive_eight_pass') is True and
            report.get('g3_unlocked') is False and
            report.get('provenance_sha256')==sha(dest/'provenance.json'),
            'UNVERIFIED_EIGHT_GRAPH_RESULT')
    require(lock.get('schema')==trainability.SCHEMA and lock.get('stage')==8 and
            lock.get('g3_unlocked') is False and
            lock.get('provenance_sha256')==sha(dest/'provenance.json') and
            summary.get('best_exact')==8 and summary.get('best_graph_errors')==0 and
            summary.get('g3_unlocked') is False,'EIGHT_GRAPH_LOCK_INVALID')
    for rel,key in [('checkpoint/'+cur.HEAD_FILE,'head_sha256'),
                    ('checkpoint/manifest.json','checkpoint_manifest_sha256'),
                    ('optimizer.pt','optimizer_sha256'),('history.json','history_sha256'),
                    ('gradients.json','gradients_sha256'),('summary.json','summary_sha256')]:
        require(sha(dest/'stage-8'/rel)==lock[key],'EIGHT_GRAPH_FILE_DRIFT_'+key)
    require(any(x.get('stage')==8 and
                x.get('checkpoint_sha256')==lock['head_sha256'] and
                x.get('lock_sha256')==sha(dest/'stage-8'/'lock.json')
                for x in report['stages']),'EIGHT_GRAPH_REPORT_DRIFT')
    require(prov.get('head_sha256')==parent_manifest['head_sha256'] and
            prov.get('g1_certificate_sha256')==sha(paths['certificate']) and
            prov.get('g1_composite_sha256')==sha(paths['composite']/'composite-interface.json') and
            prov.get('dataset_sha256')==sha(paths['dataset']) and
            prov.get('parent_0089_selection_sha256')==sha(
                Path(paths['parent_0088']).parent/'oracle-fabric-g2-structural-learning-0089'/'trainability-selection.json') and
            prov.get('selected_cases_sha256')==prov.get('parent_0089_selection_sha256') and
            all(r.get('family')=='allocation' for r in prov['selected_cases']) and
            prov.get('g3_unlocked') is False,'PROGRESSION_PROVENANCE_DRIFT')
    stage=dest/'stage-8'/'checkpoint'
    record=js(stage/'manifest.json')
    require(record.get('head_sha256')==lock['head_sha256'] and
            record.get('g3_training_unlocked') is False and
            record.get('g2_0089',{}).get('arm')=='PROGRESSIVE_MEMORIZATION_NOT_DEPLOYABLE' and
            record.get('g2_0089',{}).get('parent_head_sha256')==parent_manifest['head_sha256'] and
            record.get('g2_0089',{}).get('frozen_g1') is True,
            'MEMORIZATION_HEAD_MANIFEST')
    changed=structural.native.compare_frozen_stage(paths['parent_0087']/'training',stage)
    require(changed['non_g2_weights_mutated'] is False,'G1_OR_FUTURE_STAGE_MUTATION')
    return {'parent_sha256':parent_manifest['head_sha256'],
            'memorized_sha256':lock['head_sha256'],
            'selection_sha256':sha(paths['parent_0088']/'selection.json'),
            'provenance_sha256':sha(dest/'provenance.json'),
            'stage_lock_sha256':sha(dest/'stage-8'/'lock.json'),
            'g1_certificate_sha256':sha(paths['certificate']),
            'g1_composite_sha256':sha(paths['composite']/'composite-interface.json'),
            'dataset_sha256':sha(paths['dataset']),
            'decoder':spec}


def _excluded(paths,source):
    seen=hard.excluded_prompts(paths)
    for sub in ('oracle-fabric-g2-hard-negative-learning-0087',
                'oracle-fabric-g2-posttrain-graph-bakeoff-0088',
                'oracle-fabric-g2-structural-learning-0089'):
        file=source/sub/'seal'/'prompts.jsonl'
        if file.exists():
            seen.update(r['prompt_sha256'] for r in read_jsonl(file))
    # All 0090 memorization prompts are already in the training corpus, but
    # verify and exclude them explicitly as an independent defense.
    prov=js(paths['parent_0090']/'provenance.json')
    seen.update(r['prompt_sha256'] for r in prov['selected_cases'])
    return seen


def build_rows(paths,root):
    seen=_excluded(paths,Path(root));rng=random.Random(SEED);serial=91_000_000
    rows=[]
    for split,total in (('dev',DEV),('challenge',CHALLENGE)):
        for idx in range(total):
            factory=(corpus.encounter if idx%2==0 else corpus.allocation)
            for attempt in range(4096):
                item=factory(rng,'test',serial);serial+=1
                digest=hashlib.sha256(item['prompt'].encode()).hexdigest()
                if digest not in seen:break
            else:raise ValueError('G2_0091_FRESH_COHORT_EXHAUSTED')
            seen.add(digest)
            rows.append({'case_id':f'g2-0091-{split}-{idx+1:04}',
                'split':split,'family':item['family'],'prompt':item['prompt'],
                'prompt_sha256':digest,'graph':item['graph'],
                'gold_objects':item['object_spans']})
    historical=[r for r in cur.load_rows(paths['dataset']) if r['split'] in ('dev','challenge')]
    require(sum(r['split']=='dev' for r in historical)==HISTORICAL_DEV and
            sum(r['split']=='challenge' for r in historical)==HISTORICAL_CHALLENGE,'HISTORICAL_CASES_CHANGED')
    for idx,item in enumerate(historical):
        rows.append({'case_id':f'g2-0091-historical-{idx:04}',
            'split':'historical_'+item['split'],'family':item['family'],
            'prompt':item['prompt'],
            'prompt_sha256':hashlib.sha256(item['prompt'].encode()).hexdigest(),
            'graph':item['graph'],'gold_objects':item['object_spans']})
    for pair in fuzz.make_pairs(seed=SEED,pairs=FUZZ_PAIRS):
        for side in ('base','fuzz'):
            item=pair[side]
            digest=hashlib.sha256(item['prompt'].encode()).hexdigest()
            require(digest not in seen,'COUNTERFACTUAL_OVERLAPS_TRAIN_OR_TEST')
            seen.add(digest)
            rows.append({'case_id':f'g2-0091-fuzz-{pair["id"]}-{side}',
                'split':'fuzz','family':item['family'],'prompt':item['prompt'],
                'prompt_sha256':digest,'graph':item['graph'],
                'gold_objects':item['object_spans'],'fuzz_pair':pair['id'],
                'fuzz_side':side,'fuzz_mode':pair['mode']})
    require(len(rows)==DEV+CHALLENGE+HISTORICAL+2*FUZZ_PAIRS,'TOTAL_CASE_COUNT')
    return rows


def prepare(*,root=ROOT_DEFAULT,output=None):
    paths=_roots(root);dest=(Path(output) if output else
        Path(root)/'oracle-fabric-g2-frozen-generalization-0091').resolve()
    require(not dest.exists() and dest.parent.is_dir(),'OUTPUT_ALREADY_EXISTS')
    sources=verify_parent(paths)
    rows=build_rows(paths,root)
    dest.mkdir(exist_ok=False)
    # Commit both the parent selection and the checkpoint before any inference.
    write_json(dest/'comparison.json',{'schema':SCHEMA,'arms':list(ARMS),
        'winner_decoder':sources['decoder'],'weights_fixed':True,
        'decoder_parameters_fixed':True,'checkpoint_training':False,
        'g3_unlocked':False,'independent_distribution':False})
    seal=dest/'seal';seal.mkdir(exist_ok=False)
    public=('case_id','split','family','prompt','prompt_sha256','fuzz_pair','fuzz_side','fuzz_mode')
    secret=('case_id','split','family','prompt_sha256','graph','gold_objects',
            'fuzz_pair','fuzz_side','fuzz_mode')
    write_jsonl(seal/'prompts.jsonl',
                [{k:r[k] for k in public if k in r} for r in rows])
    write_jsonl(seal/'answers.SECRET.jsonl',
                [{k:r[k] for k in secret if k in r} for r in rows])
    lock={'schema':SCHEMA,'state':'GOLD_COMMITTED_NO_PREDICTIONS',
        'seed':SEED,'dev':DEV,'challenge':CHALLENGE,'historical':HISTORICAL,
        'fuzz':2*FUZZ_PAIRS,'comparison_sha256':sha(dest/'comparison.json'),
        'prompts_sha256':sha(seal/'prompts.jsonl'),
        'answers_sha256':sha(seal/'answers.SECRET.jsonl'),
        'runner_sha256':sha(__file__),'source':sources,
        'gold_used_for_model_selection':False,'g3_unlocked':False,
        'independent_certification':False,'no_training':True}
    write_json(seal/'commit.json',lock)
    print('G2_0091_PREPARED '+json.dumps({'fresh':DEV+CHALLENGE,
          'historical':HISTORICAL,'fuzz':2*FUZZ_PAIRS,
          'checkpoint':sources['memorized_sha256'],
          'gold_commit':lock['answers_sha256']},sort_keys=True),flush=True)
    return lock


def check_seal(paths,dest,*,gold):
    record=js(dest/'seal'/'commit.json')
    require(record['schema']==SCHEMA and record['state']=='GOLD_COMMITTED_NO_PREDICTIONS' and
            record['g3_unlocked'] is False and record['no_training'] is True and
            record['runner_sha256']==sha(__file__) and
            record['comparison_sha256']==sha(dest/'comparison.json') and
            record['prompts_sha256']==sha(dest/'seal'/'prompts.jsonl') and
            record['source']==verify_parent(paths),'SEAL_OR_PARENT_CHANGED')
    require((record['dev'],record['challenge'],record['historical'],record['fuzz'])==
            (DEV,CHALLENGE,HISTORICAL,FUZZ_PAIRS*2),'CASE_COUNTS_CHANGED')
    if gold:
        require(record['answers_sha256']==sha(dest/'seal'/'answers.SECRET.jsonl'),
                'ANSWERS_CHANGED')
    return record


def _infer(head,h,eligible,spans,prepared):
    return bakeoff._infer(head,h,eligible,spans,prepared,{},with_margins=True)


def _blind_row(row,net,tok,composite,heads,spec,device):
    # Deliberately no gold graph in this function. All arms use identical G1.
    prepared,h=bakeoff.previous._capture_inference(net,tok,row['prompt'],device,2048)
    elig=prepared['eligible'].to(device)
    g1=composite.predict(h,prepared,family=row['family'],native_stage='G1')
    spans=g1['token_spans']
    parent=_infer(heads['parent'],h,elig,spans,prepared)
    mem=_infer(heads['memorized'],h,elig,spans,prepared)
    require(parent['g1_objects']==mem['g1_objects'],'PREDICTED_G1_NOT_SHARED')
    zero_g1=composite.predict(h,prepared,family=row['family'],
                              mode='zero_hidden',native_stage='G1')
    zero=_infer(heads['memorized'],torch.zeros_like(h),elig,
                zero_g1['token_spans'],prepared)
    return {k:v for k,v in row.items() if k!='prompt'} | {
         'g1_objects':parent['g1_objects'],
         'raw_heads':{'parent':parent,'memorized':mem,'zero_hidden_memorized':zero}}


def predict(*,root=ROOT_DEFAULT,output=None,device='cuda'):
    paths=_roots(root);dest=(Path(output) if output else
        Path(root)/'oracle-fabric-g2-frozen-generalization-0091').resolve()
    commit=check_seal(paths,dest,gold=False)
    require(not (dest/'blind').exists(),'BLIND_ALREADY_COMMITTED')
    # No access to current answers.SECRET.jsonl or any scoring code below.
    record,_=hard._verify_trained(paths,paths['parent_0087'])
    network,tok=backbone.model_components(record['model_id'],paths['adapter'],device)
    require(not any(p.requires_grad for p in network.parameters()),'BACKBONE_NOT_FROZEN')
    composite=native_g1.load_composite(paths['composite'],device=device)
    parent,_=cur._load_checkpoint(paths['parent_0087']/'training',device=device)
    memorized,_=cur._load_checkpoint(paths['parent_0090']/'stage-8'/'checkpoint',device=device)
    for model in (parent,memorized):model.eval().requires_grad_(False)
    prompts=read_jsonl(dest/'seal'/'prompts.jsonl')
    require(len(prompts)==DEV+CHALLENGE+HISTORICAL+FUZZ_PAIRS*2 and
            len({r['case_id'] for r in prompts})==len(prompts),'PROMPT_COUNT')
    predictions=[]
    for idx,row in enumerate(prompts,1):
        require(hashlib.sha256(row['prompt'].encode()).hexdigest()==row['prompt_sha256'],
                'PROMPT_HASH_CHANGED')
        predictions.append(_blind_row(row,network,tok,composite,
                           {'parent':parent,'memorized':memorized},
                           commit['source']['decoder'],device))
        if idx==1 or idx%20==0 or idx==len(prompts):
            print(f'G2_0091_BLIND {idx}/{len(prompts)} frozen_G2_0087_and_0090',flush=True)
    blind=dest/'blind';blind.mkdir(exist_ok=False)
    write_jsonl(blind/'predictions.jsonl',predictions)
    lock={'schema':SCHEMA,'state':'BLIND_PREDICTIONS_LOCKED',
        'cases':len(predictions),'prompts_sha256':commit['prompts_sha256'],
        'comparison_sha256':commit['comparison_sha256'],
        'head_0087_sha256':commit['source']['parent_sha256'],
        'head_0090_sha256':commit['source']['memorized_sha256'],
        'predictions_sha256':sha(blind/'predictions.jsonl'),
        'gold_opened_during_predict':False,'g3_unlocked':False}
    write_json(blind/'lock.json',lock)
    print('G2_0091_BLIND_LOCKED '+json.dumps({'cases':lock['cases'],
        'prediction_sha256':lock['predictions_sha256']},sort_keys=True),flush=True)
    return lock


def _derived_arms(row,spec):
    raw=row['raw_heads']
    for k in ('parent','memorized','zero_hidden_memorized'):
        bakeoff.validate_surface(raw[k])
    require(raw['parent']['g1_objects']==raw['memorized']['g1_objects']==row['g1_objects'],
            'G1_DIFFERENT_BETWEEN_ARMS')
    result={
        'g2_0087_native':bakeoff.previous._keyed_edges(raw['parent']['binding_edges']),
        'g2_0088_joint':bakeoff.decode(raw['parent'],spec),
        'g2_0090_native':bakeoff.previous._keyed_edges(raw['memorized']['binding_edges']),
        'g2_0090_joint':bakeoff.decode(raw['memorized'],spec),
        'g2_0090_zero_hidden_joint':bakeoff.decode(raw['zero_hidden_memorized'],spec),
        'g2_0090_no_binding':set()}
    for name in ARMS:
        anchored={(r[2],(r[0],r[1])) for r in row['g1_objects']}
        require(all((s,si) in anchored and (d,di) in anchored
                    for s,si,d,di,typ in result[name]),'UNANCHORED_EDGE_'+name)
    return result


def _metrics(rows,answers,arm):
    c=Counter();by_family=defaultdict(Counter);per_case={};reachable_exact=0
    for row,decisions in rows:
        expected=bakeoff.truth(answers[row['case_id']]['graph'])
        got=decisions[arm]
        tp=len(got&expected); gold=len(expected);p=len(got)
        exact=int(got==expected)
        anchors={(x[2],(x[0],x[1])) for x in row['g1_objects']}
        reachable=all((s,sp) in anchors and (d,dp) in anchors
                      for s,sp,d,dp,t in expected)
        g1_expected={(n['kind'],tuple(n['span'])) for n in
                     answers[row['case_id']]['graph']['nodes'] if 'span' in n}
        g1_exact=anchors==g1_expected
        c.update({'cases':1,'exact':exact,'tp':tp,'pred':p,'gold':gold,
                  'reachable_cases':int(reachable),'reachable_exact':int(reachable and exact),
                  'g1_exact':int(g1_exact)})
        by_family[row['family']].update({'cases':1,'exact':exact,
                  'tp':tp,'pred':p,'gold':gold,'reachable':int(reachable),
                  'reachable_exact':int(reachable and exact),
                  'g1_exact':int(g1_exact)})
        per_case[row['case_id']]={'exact':exact,'fp':p-tp,'fn':gold-tp,
                  'g1_gold_reachable':reachable,'g1_exact':g1_exact,
                  'pred_edges':p,'gold_edges':gold}
    def out(d):
        return {'cases':d['cases'],'exact':d['exact'],
            'exact_rate':d['exact']/d['cases'] if d['cases'] else 0,
            'tp':d['tp'],'fp':d['pred']-d['tp'],'fn':d['gold']-d['tp'],
            'edge_f1':2*d['tp']/max(d['pred']+d['gold'],1),
            'reachable_cases':d['reachable_cases'] if 'reachable_cases' in d else d['reachable'],
            'reachable_exact':d['reachable_exact'],'g1_exact':d['g1_exact']}
    return out(c),{k:out(v) for k,v in sorted(by_family.items())},per_case


def paired_delta(primary,baseline):
    require(primary.keys()==baseline.keys(),'PAIRED_CASE_MISMATCH')
    result=Counter()
    for k in primary:
        a=primary[k];b=baseline[k]
        result['cases']+=1
        result['exact_gain']+=int(a['exact'] and not b['exact'])
        result['exact_loss']+=int(b['exact'] and not a['exact'])
        result['exact_both']+=int(a['exact'] and b['exact'])
        result['edge_errors_decreased']+=int(a['fp']+a['fn']<b['fp']+b['fn'])
        result['edge_errors_increased']+=int(a['fp']+a['fn']>b['fp']+b['fn'])
    return dict(result)


def _normalized_edges(edges,gold_graph):
    inverse={(x['kind'],tuple(x['span'])):x['id']
             for x in gold_graph['nodes'] if 'span' in x}
    result=set()
    for s,si,d,di,typ in edges:
        a=inverse.get((s,tuple(si)))
        b=inverse.get((d,tuple(di)))
        result.add((a if a is not None else 'UNKNOWN_SRC:'+s+str(si),
                    b if b is not None else 'UNKNOWN_DST:'+d+str(di),typ))
    return result


def counterfactual_metrics(rows,answers):
    groups=defaultdict(dict)
    for row,edges in rows:
        groups[row['fuzz_pair']][row['fuzz_side']]=(row,edges)
    require(len(groups)==FUZZ_PAIRS and
            all(set(v)=={'base','fuzz'} for v in groups.values()),'FUZZ_PAIR_MISSING')
    results={}
    for arm in ARMS:
        totals=Counter();detail=[]
        for pair,sides in sorted(groups.items()):
            base,altered=sides['base'],sides['fuzz']
            before,after=base[0],altered[0]
            require(before['fuzz_mode']==after['fuzz_mode'],'FUZZ_MODE_CHANGED')
            gb=answers[before['case_id']]['graph'];ga=answers[after['case_id']]['graph']
            gold_base={(x['src'],x['dst'],x['type']) for x in gb['edges']}
            gold_fuzz={(x['src'],x['dst'],x['type']) for x in ga['edges']}
            pb=_normalized_edges(base[1][arm],gb)
            pa=_normalized_edges(altered[1][arm],ga)
            exact_g1=all({(x[2],(x[0],x[1])) for x in r['g1_objects']}==
                         {(n['kind'],tuple(n['span'])) for n in g['nodes'] if 'span' in n}
                         for r,g in ((before,gb),(after,ga)))
            gold_invariant=gold_base==gold_fuzz
            if before['fuzz_mode']=='renaming':
                require(gold_invariant,'RENAMING_GOLD_NOT_INVARIANT')
            totals['pairs']+=1
            totals['g1_exact_both']+=int(exact_g1)
            totals['gold_invariant_pairs']+=int(gold_invariant)
            totals['predicted_invariant']+=int(pb==pa)
            totals['correct_both']+=int(pb==gold_base and pa==gold_fuzz)
            totals['delta_exact']+=int((pb^pa)==(gold_base^gold_fuzz))
            totals['delta_exact_g1_exact']+=int(exact_g1 and
                                                 (pb^pa)==(gold_base^gold_fuzz))
            detail.append({'pair_id':pair,'mode':before['fuzz_mode'],
                 'g1_exact_both':exact_g1,'gold_invariant':gold_invariant,
                 'predicted_invariant':pb==pa,
                 'correct_both':pb==gold_base and pa==gold_fuzz,
                 'gold_delta':len(gold_base^gold_fuzz),
                 'predicted_delta':len(pb^pa),
                 'delta_exact':(pb^pa)==(gold_base^gold_fuzz)})
        results[arm]={'summary':dict(totals),'pairs':detail}
    return results


def score(*,root=ROOT_DEFAULT,output=None):
    paths=_roots(root);dest=(Path(output) if output else
        Path(root)/'oracle-fabric-g2-frozen-generalization-0091').resolve()
    commit=check_seal(paths,dest,gold=True)
    require(not (dest/'scored').exists(),'ALREADY_SCORED')
    blind=js(dest/'blind'/'lock.json')
    require(blind['schema']==SCHEMA and blind['state']=='BLIND_PREDICTIONS_LOCKED' and
            blind['g3_unlocked'] is False and
            blind['gold_opened_during_predict'] is False and
            blind['prompts_sha256']==commit['prompts_sha256'] and
            blind['comparison_sha256']==commit['comparison_sha256'] and
            blind['head_0087_sha256']==commit['source']['parent_sha256'] and
            blind['head_0090_sha256']==commit['source']['memorized_sha256'] and
            blind['predictions_sha256']==sha(dest/'blind'/'predictions.jsonl'),
            'BLIND_LOCK_CHANGED')
    prompts=read_jsonl(dest/'seal'/'prompts.jsonl')
    answers_rows=read_jsonl(dest/'seal'/'answers.SECRET.jsonl')
    preds=read_jsonl(dest/'blind'/'predictions.jsonl')
    expected=DEV+CHALLENGE+HISTORICAL+FUZZ_PAIRS*2
    require(len(prompts)==len(answers_rows)==len(preds)==expected and
            len({r['case_id'] for r in preds})==expected,'SCORED_COUNTS_CHANGED')
    answers={r['case_id']:r for r in answers_rows}
    qindex={r['case_id']:r for r in prompts}
    require(len(answers)==len(qindex)==expected,'CASE_ID_DUPLICATE')
    by_split=defaultdict(list)
    for row in preds:
        cid=row['case_id']
        require(cid in answers and cid in qindex and
                all(row[k]==answers[cid][k]==qindex[cid][k]
                    for k in ('split','family','prompt_sha256')) and
                all(row.get(k)==answers[cid].get(k)==qindex[cid].get(k)
                    for k in ('fuzz_pair','fuzz_side','fuzz_mode')) and
                hashlib.sha256(qindex[cid]['prompt'].encode()).hexdigest()==row['prompt_sha256']
                and set(row['raw_heads'])=={'parent','memorized','zero_hidden_memorized'},
                'BLIND_GOLD_PROMPT_ALIGNMENT')
        by_split[row['split']].append((row,_derived_arms(row,commit['source']['decoder'])))
    require(all(len(by_split[k])==n for k,n in
            [('dev',DEV),('challenge',CHALLENGE),('historical_dev',HISTORICAL_DEV),
             ('historical_challenge',HISTORICAL_CHALLENGE),('fuzz',FUZZ_PAIRS*2)]),'SPLIT_COUNT_MISMATCH')
    summary={};detail=[]
    for split in SPLITS:
        metrics={};families={};records={}
        for arm in (*ARMS,'g2_0090_zero_hidden_joint','g2_0090_no_binding'):
            m,f,r=_metrics(by_split[split],answers,arm)
            metrics[arm]=m;families[arm]=f;records[arm]=r
        summary[split]={'arms':metrics,'families':families,
            'paired_vs_0088':paired_delta(records['g2_0090_joint'],records['g2_0088_joint']),
            'paired_native':paired_delta(records['g2_0090_native'],records['g2_0087_native'])}
        for row,edges in by_split[split]:
            detail.append({'case_id':row['case_id'],'split':split,'family':row['family'],
                'arms':{arm:records[arm][row['case_id']] for arm in ARMS}})
    fuzz_result=counterfactual_metrics(by_split['fuzz'],answers)
    # G1 retention is a source-weight and observed-object identity invariant,
    # not an inference that G2 has passed any historical or prospective gate.
    result={'schema':SCHEMA,'status':'FROZEN_G2_0090_GENERALIZATION_DIAGNOSIS_ONLY',
        'splits':summary,'counterfactual':fuzz_result,
        'source':commit['source'],'source_0090_eight_graph_exact':8,
        'comparison_claim':'same G1 objects, same prompts, fixed G2 weights and decoder',
        'fresh_generator_independent':False,'g2_certified':False,'g3_unlocked':False,
        'promotion':False,'new_training':False,
        'integrity':{'sealed_gold_sha256':commit['answers_sha256'],
          'prompts_sha256':commit['prompts_sha256'],
          'blind_predictions_sha256':blind['predictions_sha256'],
          'parent_0090_lock_sha256':commit['source']['stage_lock_sha256'],
          'gold_accessed_after_blind_lock':True}}
    scored=dest/'scored';scored.mkdir(exist_ok=False)
    write_json(scored/'report.json',result)
    write_jsonl(scored/'cases.jsonl',detail)
    print('G2_0091_RESULT '+json.dumps({'status':result['status'],
         'fresh':{s:{a:summary[s]['arms'][a]['exact'] for a in ARMS}
                  for s in ('dev','challenge')},
         'edge_f1':{s:{a:round(summary[s]['arms'][a]['edge_f1'],5) for a in ARMS}
                    for s in ('dev','challenge')},
         'g3_unlocked':False},sort_keys=True),flush=True)
    return result


def run(*,root=ROOT_DEFAULT,output=None,device='cuda'):
    dest=(Path(output) if output else
        Path(root)/'oracle-fabric-g2-frozen-generalization-0091').resolve()
    prepare(root=root,output=dest)
    for command in ('predict','score'):
        args=[sys.executable,'-u','-m',MODULE,command,'--root',str(Path(root).resolve()),
              '--output',str(dest)]
        if command=='predict':args+=['--device',device]
        subprocess.run(args,check=True)
    return js(dest/'scored'/'report.json')


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    sub=p.add_subparsers(dest='command',required=True)
    for cmd in ('run','prepare','predict','score'):
        x=sub.add_parser(cmd)
        x.add_argument('--root',default=ROOT_DEFAULT)
        x.add_argument('--output',default=None)
        if cmd in ('run','predict'):x.add_argument('--device',default='cuda')
    args=vars(p.parse_args(argv));cmd=args.pop('command')
    return {'run':run,'prepare':prepare,'predict':predict,'score':score}[cmd](**args)


if __name__=='__main__':main()
