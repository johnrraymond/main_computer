#!/usr/bin/env python3
"""Read-only NanoJev Gamer 100-vector zero-line/sign-reversal localization frob.

An oracle-only run verifies controlled mathematical pairs without model weights.
An expanded run probes a *single fingerprint-matched frozen LoRA + best_model.pt*.
It includes certified source-development controls, source-native arithmetic versus
2x2 payoff-surface pairs with identical answers, and Hawk-Dove indifference
threshold pairs. Reports describe observed representation/head behavior, NOT
unearned causal attribution or a new training result.

  python -m tools.nanojev_gamer_v1_zero_sign_frob
  python -m tools.nanojev_gamer_v1_zero_sign_frob --mode expanded \\
    --run runtime/reports/gamer-v1/transition-005 \\
    --expanded-run runtime/reports/gamer-v1/transition-005-expanded-head-100-0001 \\
    --device cuda --capture-batch 2 --head-batch 4

No source weights, cache, source reports, training state, or datasets are changed.
A new sibling output directory contains the model probe results. The frob
never resumes training or automatically diagnoses a LoRA fault.
"""
from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass
from fractions import Fraction
import hashlib
import json
import math
from pathlib import Path
import re

from tools import nanojev_two_game_spine_smoke as spine

SCHEMA = 'nanojev.gamer.v1.zero-sign-frob.v1'
OUTLIER_SHA256 = '300705ca08d018cf54989dba83ee9dbcaea1810704d27217254b8648f57d08ef'
NUM = r'(-?(?:\d+/\d+|\d+(?:\.\d+)?))'
SOURCE_PATTERN = re.compile(
    r'A spacecraft is at signed coordinate ' + NUM +
    r', while a navigation beacon is at signed coordinate ' + NUM)
MATRIX_PATTERN = re.compile(r'CC=' + NUM + r', CD=' + NUM +
                            r', DC=' + NUM + r', DD=' + NUM)
PROSE_PATTERN = re.compile(r'cooperation R=' + NUM + r', cooperation against defection S=' + NUM +
                           r', defection against cooperation T=' + NUM + r', mutual defection P=' + NUM)
BELIEF_PATTERN = re.compile(r'cooperates with probability ' + NUM)


@dataclass(frozen=True)
class Probe:
    id: str
    group: str
    kind: str
    surface: str
    prompt: str
    target: Fraction
    source_index: int | None = None
    game: str = ''
    series: str = ''
    offset: Fraction | None = None

    def public(self):
        return {'id':self.id,'group':self.group,'kind':self.kind,
                'surface':self.surface,'game':self.game,'series':self.series,
                'source_index':self.source_index,
                'target':spine.fstr(self.target),'prompt_sha256':_sha_text(self.prompt)}


def _sha_text(text: str) -> str:
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def _fraction(value) -> Fraction:
    if isinstance(value, bool):
        raise ValueError('BOOLEAN_NOT_A_NUMERICAL_TARGET')
    return Fraction(str(value))


def independent_oracle(probe: Probe) -> Fraction:
    """Recompute numeric labels solely from the *visible* controlled prompt."""
    if probe.kind == 'certified_source':
        raise ValueError('SOURCE_CERTIFICATION_REQUIRES_ORIGINAL_DATASET')
    if probe.surface == 'source_native':
        m = SOURCE_PATTERN.search(probe.prompt)
        if not m:
            raise ValueError('INVALID_SOURCE_NATIVE_PROMPT')
        return _fraction(m[1]) - _fraction(m[2])
    if probe.surface == 'spine_difference':
        m = MATRIX_PATTERN.search(probe.prompt)
        if not m or 'Compute T-R' not in probe.prompt:
            raise ValueError('INVALID_SPINE_DIFFERENCE_PROMPT')
        return _fraction(m[3]) - _fraction(m[1])
    if probe.surface == 'spine_belief':
        m = MATRIX_PATTERN.search(probe.prompt)
        q = BELIEF_PATTERN.search(probe.prompt)
        if not m or not q:
            raise ValueError('INVALID_SPINE_BELIEF_PROMPT')
        R,S,T,P = map(_fraction,m.groups())
        belief = _fraction(q[1])
    elif probe.surface == 'game_prose_belief':
        m = PROSE_PATTERN.search(probe.prompt)
        q = BELIEF_PATTERN.search(probe.prompt)
        if not m or not q:
            raise ValueError('INVALID_GAME_PROSE_PROMPT')
        R,S,T,P = map(_fraction,m.groups())
        belief = _fraction(q[1])
    else:
        raise ValueError('UNKNOWN_PROBE_SURFACE')
    if not 0 <= belief <= 1:
        raise ValueError('INVALID_PROBABILITY')
    return belief * (T - R) + (1 - belief) * (P - S)


def _native_prompt(ship: Fraction, beacon: Fraction) -> str:
    # Identical prose template to the certified captain_signed_offset source.
    from tools.nanojev_gamer_v1_pairs import SCENARIOS
    return SCENARIOS['captain_signed_offset'][0].format(
        ship_offset=spine.fstr(ship), beacon_offset=spine.fstr(beacon))


def _spine_difference_prompt(ship: Fraction, beacon: Fraction) -> str:
    # The controls CD and DD do not influence this T-R problem. No extra oracle input.
    return (f'Two actions: C (cooperate) or D (defect). Your payoffs: '
            f'CC={spine.fstr(beacon)}, CD=-3, DC={spine.fstr(ship)}, DD=-5. '
            'The first letter is your action. Compute T-R (defection gain when the other cooperates). '
            'Answer with ONE number only.')


def _spine_belief_prompt(stage: spine.Stage, q: Fraction) -> str:
    return (spine.payoff_text(stage) +
            f' The other cooperates with probability {spine.fstr(q)}. '
            'Calculate q*(T-R)+(1-q)*(P-S). Answer with ONE number only.')


def _game_prose_belief_prompt(stage: spine.Stage, q: Fraction) -> str:
    return (f'A game of cooperation and confrontation has these payoffs for you: '
            f'mutual cooperation R={spine.fstr(stage.R)}, '
            f'cooperation against defection S={spine.fstr(stage.S)}, '
            f'defection against cooperation T={spine.fstr(stage.T)}, '
            f'mutual defection P={spine.fstr(stage.P)}. '
            f'The other cooperates with probability {spine.fstr(q)}. '
            'What is expected payoff from defecting minus expected payoff from cooperating? '
            'Answer with ONE number only.')


def build_controlled() -> list[Probe]:
    """Two independently parsed prompts for every identical exact-rational problem.

    Four kinds of comparisons are present: zero, ±0.01/0.05/0.25/1,
    numerical-magnitude shift (10 and 100), and a Hawk-Dove threshold sweep.
    Synthetic cases are explicitly NOT certified original dataset cases.
    """
    probes=[]
    offsets = (Fraction(-1),Fraction(-1,4),Fraction(-1,20),Fraction(-1,100),
               Fraction(0),Fraction(1,100),Fraction(1,20),Fraction(1,4),Fraction(1))
    for center in (Fraction(10),Fraction(100)):
        for offset in offsets:
            ship,beacon=center+offset,center
            group=f'signed_offset.center_{int(center)}.delta_{spine.fstr(offset)}'
            for surface,render in [('source_native',_native_prompt),
                                   ('spine_difference',_spine_difference_prompt)]:
                probes.append(Probe(group+'.'+surface,group,'controlled',surface,
                            render(ship,beacon),offset,game='captain_signed_offset_vs_hawk_dove',
                            series=f'signed_offset.center_{int(center)}',offset=offset))
    stage=spine.ordinal_stage(spine.PAYOFF_ORDERS['chicken_hawk_dove'])
    threshold=spine.critical_q(stage)
    assert threshold is not None and stage.margin(threshold)==0
    for offset in (Fraction(-1,100),Fraction(-1,1000),Fraction(0),
                   Fraction(1,1000),Fraction(1,100)):
        q=threshold+offset
        target=stage.margin(q)
        group=f'hawk_indifference.delta_{spine.fstr(offset)}'
        for surface,render in [('spine_belief',_spine_belief_prompt),
                               ('game_prose_belief',_game_prose_belief_prompt)]:
            probes.append(Probe(group+'.'+surface,group,'controlled',surface,
                            render(stage,q),target,game='chicken_hawk_dove',
                            series='hawk_indifference',offset=offset))
    validate_controlled(probes)
    return probes


def validate_controlled(probes: list[Probe]):
    if len({p.id for p in probes}) != len(probes):
        raise AssertionError('DUPLICATE_CONTROLLED_ID')
    groups={}
    for p in probes:
        if independent_oracle(p) != p.target:
            raise AssertionError('VISIBLE_PROMPT_TARGET_MISMATCH: '+p.id)
        groups.setdefault(p.group,[]).append(p)
    if not groups or any(len(v)!=2 or v[0].target != v[1].target for v in groups.values()):
        raise AssertionError('MISSING_MATHEMATICALLY_MATCHED_SURFACES')
    by_series={}
    for p in probes:
        if p.surface not in ('source_native','spine_belief'):continue
        by_series.setdefault(p.series,{})[p.offset]=p.target
    if not by_series or any(Fraction(0) not in x for x in by_series.values()):
        raise AssertionError('MISSING_EXACT_ZERO_LINE')
    for series,values in by_series.items():
        for offset,target in values.items():
            if values.get(-offset) != -target:
                raise AssertionError('SIGN_FLIP_ORACLE_MISMATCH: '+series)
    return {'controlled_cases':len(probes),'matched_surface_pairs':len(groups),
            'series':sorted(by_series),'source_native_template':'SCENARIOS[captain_signed_offset]',
            'zero_targets':sum(p.target==0 for p in probes),
            'positive_targets':sum(p.target>0 for p in probes),
            'negative_targets':sum(p.target<0 for p in probes),
            'independently_recomputed':True}


def _manifest_verified_dev(run:Path, identity:dict) -> list[dict]:
    from tools import nanojev_gamer_v1_handoff as gamer
    path=run/'certified_pairs.jsonl'
    manifest_path=run/'handoff_manifest.json'
    if not path.is_file() or not manifest_path.is_file():
        raise FileNotFoundError('SOURCE_CERTIFIED_PAIRS_OR_MANIFEST_MISSING')
    manifest=json.loads(manifest_path.read_text(encoding='utf-8'))
    all_rows,verified=gamer.load_cases(path,
        allow_partial=bool(manifest.get('allow_partial_smoke')),
        include_catalog_extensions=bool(manifest.get('bounded_catalog_extensions')),
        include_transition_games=bool(manifest.get('bounded_transition_games')))
    if (verified['dataset_sha256']!=identity.get('dataset_sha256') or
        verified['original_prompt_corpus_sha256']!=identity.get('original_prompt_corpus_sha256') or
        manifest.get('dataset_sha256')!=identity.get('dataset_sha256') or
        manifest.get('original_prompt_corpus_sha256')!=identity.get('original_prompt_corpus_sha256')):
        raise ValueError('SOURCE_DATASET_PROVENANCE_MISMATCH')
    return gamer.by_split(all_rows,'dev')


def select_source_controls(dev:list[dict], limit:int) -> tuple[list[Probe],dict]:
    """Deterministic sign-balanced controls, including the known zero outlier if present."""
    if limit<0:
        raise ValueError('INVALID_SOURCE_CONTROL_LIMIT')
    if limit==0:
        return [],{'source_count':0,'outlier':'NOT_CHECKED_SOURCE_DISABLED'}
    from tools import nanojev_gamer_v1_handoff as gamer
    outlier=[i for i,r in enumerate(dev) if
             gamer.original_prompt_fingerprint(r)==OUTLIER_SHA256 and
             str(r.get('game_id'))=='discounted_repeat' and
             float(r['answer'])==0]
    pool={ 'zero':[], 'negative':[], 'positive':[]}
    for i,row in enumerate(dev):
        value=float(row['answer'])
        if not math.isfinite(value): raise ValueError('NONFINITE_CERTIFIED_SOURCE_TARGET')
        cls='zero' if value==0 else ('negative' if value<0 else 'positive')
        pool[cls].append(i)
    for kind in pool:
        pool[kind].sort(key=lambda i:(abs(float(dev[i]['answer'])),
                                      gamer.original_prompt_fingerprint(dev[i])))
    selected=outlier[:1]
    # Near-boundary *and* high-magnitude source controls, not just the first
    # source rows or the latest development table's highest-loss examples.
    pools={
       'zero_near':pool['zero'],
       'zero_far':list(reversed(pool['zero'])),
       'negative_near':pool['negative'],
       'negative_far':list(reversed(pool['negative'])),
       'positive_near':pool['positive'],
       'positive_far':list(reversed(pool['positive'])),
    }
    for t in range(max(limit,1)):
        progressed=False
        for candidates in pools.values():
            if t<len(candidates):
                i=candidates[t]
                if i not in selected:
                    selected.append(i);progressed=True
                if len(selected)>=limit:break
        if len(selected)>=limit:break
        if not progressed and t>=max(len(x) for x in pools.values()):break
    if len(selected)<limit:
        for i in sorted(range(len(dev)),key=lambda i:gamer.original_prompt_fingerprint(dev[i])):
            if i not in selected:selected.append(i)
            if len(selected)>=limit:break
    results=[]
    for i in selected:
        r=dev[i];prompt=gamer.original_prompt(r)
        results.append(Probe(f'certified.dev.{i:05d}','source_control',
                             'certified_source','source_exact',prompt,_fraction(r['answer']),
                             source_index=i,game=str(r['game_id']),series='certified_dev'))
    return results,{'source_count':len(results),'source_available':len(dev),
            'outlier':('INCLUDED' if outlier and outlier[0] in selected else
                       'FOUND_BUT_EXCLUDED' if outlier else 'NOT_FOUND_IN_CERTIFIED_DEV'),
            'outlier_dev_index':outlier[0] if outlier else None,
            'source_zero':sum(p.target==0 for p in results),
            'source_negative':sum(p.target<0 for p in results),
            'source_positive':sum(p.target>0 for p in results)}


def _check_original_evaluation(run:Path,expanded:Path,checkpoint_step:int,
                               controls:list[Probe],reference_csv:Path|None) -> dict:
    """Reference CSV may be compared only if its step equals checkpoint step."""
    import csv
    path=reference_csv or expanded/'dev_evaluations'/f'step_{checkpoint_step:07d}.csv'
    if not path.is_file():
        return {'status':'NO_CHECKPOINT_MATCHED_REFERENCE','expected_file':str(path)}
    rows=list(csv.DictReader(path.open(encoding='utf-8',newline='')))
    if not rows:return {'status':'EMPTY_REFERENCE','file':str(path)}
    step_values={r.get('step') for r in rows}
    if step_values!={str(checkpoint_step)}:
        return {'status':'REFERENCE_STEP_DIFFERS_FROM_BEST_HEAD',
                'reference_steps':sorted(str(x) for x in step_values),
                'best_head_step':checkpoint_step,'file':str(path)}
    indexed={int(r['case_index']):r for r in rows}
    validated={}
    for probe in controls:
        row=indexed.get(probe.source_index)
        if row is None:continue
        if row.get('prompt_sha256') != _sha_text(probe.prompt) or \
           not math.isclose(float(row['target']),float(probe.target),rel_tol=0,abs_tol=1e-10):
            raise ValueError('SOURCE_REFERENCE_PROMPT_OR_TARGET_MISMATCH: '+probe.id)
        validated[probe.id]=float(row['prediction'])
    return {'status':'CHECKPOINT_MATCHED','file':str(path),
            'validated_reference_predictions':validated,'rows':len(rows)}


def _positive_target_sign(target:float, prediction:float) -> bool:
    if target==0:return prediction==0
    return (target>0)==(prediction>0) and prediction!=0


def _stats(records:list[dict]) -> dict:
    if not records:return {'count':0}
    import numpy as np
    target=np.asarray([r['target'] for r in records],dtype=np.float64)
    prediction=np.asarray([r['prediction'] for r in records],dtype=np.float64)
    error=np.abs(target-prediction)
    zero=target==0
    negative=target<0
    positive=target>0
    def part(mask):
        idx=np.flatnonzero(mask)
        if not len(idx):return {'count':0}
        return {'count':int(len(idx)), 'mae':float(error[idx].mean()),
                'within_0_01':int(np.sum(error[idx]<=0.01)),
                'within_0_05':int(np.sum(error[idx]<=0.05)),
                'within_0_1':int(np.sum(error[idx]<=0.1)),
                'within_0_25':int(np.sum(error[idx]<=0.25)),
                'correct_strict_sign':int(sum(_positive_target_sign(target[i],prediction[i]) for i in idx)),
                'prediction_min':float(prediction[idx].min()),
                'prediction_max':float(prediction[idx].max())}
    return {'count':len(records),'mae':float(error.mean()),
            'zero':part(zero),'negative':part(negative),'positive':part(positive)}


def _geometry(a,b):
    import numpy as np
    aa=np.asarray(a,dtype=np.float64);bb=np.asarray(b,dtype=np.float64)
    if aa.shape!=bb.shape or aa.ndim!=1 or not np.isfinite(aa).all() or not np.isfinite(bb).all():
        raise ValueError('INVALID_FEATURE_PAIR')
    delta=aa-bb
    na=float(np.linalg.norm(aa));nb=float(np.linalg.norm(bb));nd=float(np.linalg.norm(delta))
    return {'l2':nd, 'relative_l2':nd/max(na,nb,1e-12),
            'cosine':float(np.dot(aa,bb)/(na*nb)) if na*nb>0 else None,
            'max_abs_difference':float(np.max(np.abs(delta)))}


def _pair_analysis(probes:list[Probe],features,predictions:list[float]) -> dict:
    import numpy as np
    if len(probes)!=len(features) or len(probes)!=len(predictions):
        raise ValueError('PAIR_PROBE_FEATURE_MISMATCH')
    maps={p.id:i for i,p in enumerate(probes)}
    by_group={};by_flips={}
    for p in probes:
        if p.kind!='controlled':continue
        by_group.setdefault(p.group,[]).append(p)
        by_flips.setdefault((p.series,p.surface),{})[p.offset]=p
    surface_pairs=[]
    for group,members in sorted(by_group.items()):
        if len(members)!=2 or members[0].target!=members[1].target:
            raise AssertionError('SURFACE_PAIR_MISMATCH')
        i,j=(maps[p.id] for p in members)
        surface_pairs.append({'group':group,'target':float(members[0].target),
                              'surfaces':[p.surface for p in members],
                              'predictions':[float(predictions[i]),float(predictions[j])],
                              'prediction_difference_abs':float(abs(predictions[i]-predictions[j])),
                              'feature_geometry':_geometry(features[i],features[j])})
    reversals=[]
    for (series,surface),by_offset in sorted(by_flips.items()):
        for offset in sorted(k for k in by_offset if k>0):
            if -offset not in by_offset:continue
            minus=by_offset[-offset];plus=by_offset[offset]
            if plus.target<=0 or minus.target>=0:
                raise AssertionError('INVALID_ORACLE_SIGN_REVERSAL')
            i,j=maps[minus.id],maps[plus.id]
            change=float(predictions[j]-predictions[i]); expected=float(plus.target-minus.target)
            geo=_geometry(features[i],features[j])
            reversals.append({'series':series,'surface':surface,'offset':spine.fstr(offset),
                     'negative':{'id':minus.id,'target':float(minus.target),
                                 'prediction':float(predictions[i])},
                     'positive':{'id':plus.id,'target':float(plus.target),
                                 'prediction':float(predictions[j])},
                     'target_difference':expected, 'prediction_difference':change,
                     'predicted_correct_order':change>0,
                     'sign_pair_correct':predictions[i]<0 and predictions[j]>0,
                     'near_identical_features':geo['relative_l2']<=1e-3,
                     'head_flat_on_separated_features':(geo['relative_l2']>1e-3 and
                                                        abs(change)<0.05 and expected>=0.1),
                     'feature_geometry':geo})
    return {'surface_pairs':surface_pairs,'sign_reversal_pairs':reversals,
            'summary':{'surface_pairs':len(surface_pairs),'sign_reversal_pairs':len(reversals),
                       'both_signs_correct':sum(p['sign_pair_correct'] for p in reversals),
                       'proper_order':sum(p['predicted_correct_order'] for p in reversals),
                       'near_identical_feature_pairs':sum(p['near_identical_features'] for p in reversals),
                       'head_flat_separated_feature_pairs':sum(p['head_flat_on_separated_features'] for p in reversals),
                       'surface_disagreement_gt_0_1':sum(p['prediction_difference_abs']>0.1 for p in surface_pairs)},
            'interpretation':('Feature geometry cannot by itself prove that the LoRA did or did not encode '
                              'the required semantics. Output insensitivity to different features is a '
                              'measured property of the combined frozen pipeline; attribution requires '
                              'further controlled interventions.')}


def _output_directory(run:Path, requested:Path|None) -> Path:
    if requested is not None:
        out=requested.resolve()
    else:
        root=run.parent
        out=next((root/f'{run.name}-zero-sign-frob-{i:04d}' for i in range(1,10000)
                  if not (root/f'{run.name}-zero-sign-frob-{i:04d}').exists()),None)
        if out is None:raise RuntimeError('FROB_OUTPUT_NAMES_EXHAUSTED')
    if out==run or run in out.parents or out in run.parents:
        raise ValueError('FROB_OUTPUT_OVERLAPS_SOURCE_RUN')
    return out


def _write_report(out:Path,summary:dict,records:list[dict],analysis:dict):
    import csv
    out.mkdir(parents=True,exist_ok=False)
    (out/'summary.json').write_text(json.dumps(summary,indent=2,sort_keys=True,allow_nan=False)+'\n',encoding='utf-8')
    (out/'paired_features.json').write_text(json.dumps(analysis,indent=2,sort_keys=True,allow_nan=False)+'\n',encoding='utf-8')
    names=('id','kind','game','series','group','surface','source_index','prompt_sha256',
           'target','prediction','absolute_error','strict_sign_correct','zero_within_0_05')
    with (out/'predictions.csv').open('w',encoding='utf-8',newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=names,extrasaction='ignore')
        writer.writeheader();writer.writerows(records)


def _run_expanded(args,controlled:list[Probe]):
    """Production-compatible two-phase inference: Qwen capture then CPU release then head."""
    import gc
    import numpy as np
    import torch
    from tools import nanojev_gamer_v1_handoff as gamer
    from tools.nanojev_gamer_v1_expanded_head import _capture,make_expanded_head,SCHEMA as HEAD_SCHEMA
    from tools.nanojev_qwen_c0_compressive_train import inverse_signed_log

    if not args.run or not args.expanded_run:
        raise ValueError('EXPANDED_MODE_REQUIRES_RUN_AND_EXPANDED_RUN')
    args.run=args.run.resolve();args.expanded_run=args.expanded_run.resolve()
    out=_output_directory(args.run,args.output)
    if out.exists():
        raise FileExistsError('FROB_OUTPUT_ALREADY_EXISTS: '+str(out))
    # Reuse the currently deployed read-only path/lineage checks.
    other=argparse.Namespace(run=args.run,expanded_run=args.expanded_run,
                             adapter_dir=None,mode='expanded')
    spine.resolve_model_paths(other)
    adapter=other.adapter_dir
    expected_sha=spine.adapter_identity(adapter)
    metadata=spine.expanded_metadata(args.expanded_run,expected_sha,args.model_id)
    if metadata.get('schema')!=HEAD_SCHEMA:
        raise ValueError('EXPANDED_WRONG_HEAD_SCHEMA')
    hp=metadata['configuration']
    checkpoint=args.expanded_run/'best_model.pt'
    checkpoint_sha=spine.file_sha(checkpoint)
    state=torch.load(checkpoint,map_location='cpu',weights_only=True)
    if (state.get('schema')!=HEAD_SCHEMA or state.get('identity')!=metadata or
        not isinstance(state.get('model'),dict) or not isinstance(state.get('step'),int)):
        raise ValueError('EXPANDED_CHECKPOINT_LINEAGE_MISMATCH')
    checkpoint_step=int(state['step'])
    if args.max_input_tokens>hp['max_length']:
        raise ValueError('MAX_INPUT_TOKENS_EXCEEDS_FROZEN_TRAINING_LIMIT')
    dev=_manifest_verified_dev(args.run,metadata)
    source,source_report=select_source_controls(dev,args.source_cases)
    probes=source+controlled
    if not probes:raise ValueError('NO_PROBES_SELECTED')
    print(f'FROB capture: best_step={checkpoint_step}; source={len(source)}; '
          f'controlled={len(controlled)}; adapter={expected_sha[:16]}',flush=True)
    spine.cache_policy(args.allow_model_download)
    _,tok,model=gamer.model_components(args.model_id,args.device,adapter_dir=adapter)
    model.eval()
    features,capture=_capture(torch,model,tok,[{'prompt_08':p.prompt} for p in probes],
        device=args.device,max_length=args.max_input_tokens,
        batch_size=args.capture_batch,samples=100,curvature=hp['curvature'])
    if features.shape != (len(probes),hp['input_width']):
        raise ValueError('UNEXPECTED_100_VECTOR_FEATURE_SHAPE')
    del model,tok
    gc.collect()
    if torch.cuda.is_available():torch.cuda.empty_cache()
    net=make_expanded_head(torch,hp['input_width'],tuple(hp['hidden']))
    net.load_state_dict(state['model'],strict=True)
    del state
    gc.collect()
    dtype=torch.float16 if args.device.startswith('cuda') else torch.float32
    net=net.to(device=args.device,dtype=dtype)
    net.eval()
    results=[]
    with torch.inference_mode():
        for begin in range(0,len(probes),args.head_batch):
            x=torch.as_tensor(features[begin:begin+args.head_batch],device=args.device,dtype=dtype)
            raw,_=net(x)
            results.extend(float(v) for v in inverse_signed_log(raw.float().cpu().numpy()))
    del net
    if len(results)!=len(probes) or not np.isfinite(results).all():
        raise ValueError('FROB_NONFINITE_OR_MISSING_MODEL_OUTPUT')
    if spine.file_sha(checkpoint)!=checkpoint_sha or spine.adapter_identity(adapter)!=expected_sha:
        raise ValueError('FROZEN_CHECKPOINT_OR_ADAPTER_CHANGED_DURING_FROB')
    comparison=_check_original_evaluation(args.run,args.expanded_run,checkpoint_step,
                                          source,args.reference_csv)
    baseline_predictions=comparison.pop('validated_reference_predictions',{})
    records=[]
    for probe,pred in zip(probes,results):
        target=float(probe.target)
        records.append({**probe.public(),'target':target,'prediction':pred,
                   'absolute_error':abs(pred-target),
                   'strict_sign_correct':int(_positive_target_sign(target,pred)),
                   'zero_within_0_05':int(target==0 and abs(pred)<=0.05)})
    if baseline_predictions:
        by_id={p.id:r for p,r in zip(probes,results)}
        diff={k:abs(by_id[k]-v) for k,v in baseline_predictions.items()}
        comparison.update({'count':len(diff),'max_absolute_delta':max(diff.values()),
                           'within_0_01':sum(v<=0.01 for v in diff.values()),
                           'parity_0_01':('PASS' if all(v<=0.01 for v in diff.values())
                                          else 'FAIL'), 'values':diff})
    analysis=_pair_analysis(probes,features,results)
    source_records=[r for r in records if r['kind']=='certified_source']
    controlled_records=[r for r in records if r['kind']=='controlled']
    by_surface={s:_stats([r for r in controlled_records if r['surface']==s])
                for s in sorted({r['surface'] for r in controlled_records})}
    source_zeros=[r for r in source_records if r['target']==0]
    controlled_zeros=[r for r in controlled_records if r['target']==0]
    control_zero_ok=all(abs(r['prediction'])<=0.05 for r in controlled_zeros)
    source_zero_ok=all(abs(r['prediction'])<=0.05 for r in source_zeros)
    zero_gate=('UNVERIFIED_MISSING_ZERO_CLASS' if not controlled_zeros or not source_zeros else
               'PASS' if control_zero_ok and source_zero_ok else 'FAIL')
    near_neighbors=[r for r in controlled_records
                    if r['target']!=0 and abs(r['target'])<=0.25]
    near_gate=('PASS' if near_neighbors and all(
                _positive_target_sign(r['target'],r['prediction'])
                for r in near_neighbors) else 'FAIL')
    conclusion={'status':'DIAGNOSTIC_ONLY_NO_CAUSAL_CERTIFICATION',
                'zero_line_gate':zero_gate,
                'zero_line_tolerance':0.05,
                'controlled_zero_count':len(controlled_zeros),
                'source_zero_count':len(source_zeros),
                'near_neighbor_strict_sign_gate':near_gate,
                'near_neighbor_count':len(near_neighbors),
                'original_source_vs_transfer':{'source':_stats(source_records),
                                               'synthetic':_stats(controlled_records)},
                'causal_note':'The two necessary interfaces are probed, but correlated feature geometry and head output alone cannot uniquely separate missing LoRA semantics from an undertrained head.'}
    summary={'schema':SCHEMA,'mode':'expanded','source_run':str(args.run),
             'expanded_run':str(args.expanded_run),'model_id':args.model_id,
             'checkpoint_step':checkpoint_step,'checkpoint_sha256':checkpoint_sha,
             'adapter_sha256':expected_sha,'adapter_dir':str(adapter.resolve()),
             'certified_dev_count':len(dev),'source_selection':source_report,
             'capture':capture,'original_eval_comparison':comparison,
             'oracle':validate_controlled(controlled),
             'source_controls':_stats(source_records),
             'controlled':_stats(controlled_records),
             'controlled_by_surface':by_surface,'pairs':analysis['summary'],
             'assessment':conclusion,'no_training':True,
             'source_weights_modified':False,'source_dataset_modified':False}
    _write_report(out,summary,records,analysis)
    if args.export_features:
        # Optional output-only artifact. Source model / run and head are untouched.
        meta={'schema':SCHEMA,'checkpoint_step':checkpoint_step,
              'checkpoint_sha256':checkpoint_sha,'adapter_sha256':expected_sha,
              'input_width':int(features.shape[1]),
              'case_order':[p.public() for p in probes],
              'feature_policy':hp.get('feature_policy'),
              'values_are_frozen':True}
        with (out/'frob_features.npz').open('wb') as stream:
            np.savez_compressed(stream,features=features.astype(np.float16),
                                predictions=np.asarray(results,dtype=np.float32),
                                metadata=json.dumps(meta,sort_keys=True,allow_nan=False))
    summary['report_dir']=str(out)
    return summary


def parser():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--mode',choices=('oracle','expanded'),default='oracle')
    ap.add_argument('--run',type=Path)
    ap.add_argument('--expanded-run',type=Path)
    ap.add_argument('--model-id',default='Qwen/Qwen3-0.6B')
    ap.add_argument('--device',default='cuda')
    ap.add_argument('--capture-batch',type=int,default=2)
    ap.add_argument('--head-batch',type=int,default=4)
    ap.add_argument('--source-cases',type=int,default=18)
    ap.add_argument('--max-input-tokens',type=int,default=1024)
    ap.add_argument('--reference-csv',type=Path,help='Only compare if its step matches best_model.pt')
    ap.add_argument('--output',type=Path,help='New output directory; existing directories refused')
    ap.add_argument('--allow-model-download',action='store_true')
    ap.add_argument('--export-features',action='store_true',help='Write fingerprint-bound 100-vector features to new frob report directory')
    return ap


def main(argv=None):
    args=parser().parse_args(argv)
    if args.capture_batch<1 or args.head_batch<1 or args.source_cases<0 or args.max_input_tokens<1:
        raise ValueError('INVALID_FROB_LIMITS')
    controlled=build_controlled()
    if args.mode=='oracle':
        result={'schema':SCHEMA,'mode':'oracle',
                'oracle':validate_controlled(controlled),'model_status':'NOT_EVALUATED'}
    else:
        result=_run_expanded(args,controlled)
    print(json.dumps(result,sort_keys=True,ensure_ascii=False,allow_nan=False),flush=True)
    return 0


if __name__=='__main__':
    raise SystemExit(main())
