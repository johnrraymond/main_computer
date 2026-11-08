#!/usr/bin/env python3
"""V20: read-only, exact-replay audit of V19's rejected two-margin trials.

Reproduces V19 from the verified V14 anchor with its historical configuration.
The added V19 callback is observational only. Every replay trial is checked
against the saved V19 CSV before the result can be certified. A second model
recomputes the adapter and downstream logits in float64 (the captured upstream
features and stored checkpoint weights remain their original precision).
Neither optimizer admission criteria nor checkpoints are modified.
"""
from __future__ import annotations

import argparse
import copy
import csv
import json
import math
from pathlib import Path

from tools import nanojev_gamer_v1_precision_continuation_v19 as v19
from tools import nanojev_gamer_v1_nonlinear_feasibility_v16 as v16
from tools import nanojev_gamer_v1_margin_consolidation_v13 as v13
from tools import nanojev_gamer_v1_margin_feasibility_v15 as v15
from tools import nanojev_gamer_v1_gradient_sign_audit_v17 as v17
from tools import nanojev_gamer_v1_joint_constraint_v18 as v18
from tools import nanojev_gamer_v1_boundary_isolation_v12 as v12
from tools import nanojev_gamer_v1_math_retention_v9 as v9
from tools import nanojev_gamer_v1_protected_math_repair_v7 as v7
from tools import nanojev_gamer_v1_protected_alignment_lab as v6
from tools import nanojev_gamer_v1_joint_head_repair as joint
from tools import nanojev_gamer_v1_head_only_lab as lab
from tools import nanojev_gamer_v1_sign_information_probe as probe
from tools import nanojev_gamer_v1_conflict_projection_v10 as v10

SCHEMA='nanojev.gamer.v1.precision-resolution-replay.v20'
CONFIG_KEYS=('reserve','max_iterations','max_stalls','target_step',
             'min_target_step','trust_radius','max_halvings','fd_epsilon',
             'min_joint_gain','min_merit_gain','max_gradient_cases')


def _validate_saved_report(report, expected, capture, data, proof, verified14,
                           sha15,sha16,sha17,evidence,prior_sha,focus):
    if (report.get('schema')!=v19.SCHEMA or report.get('status')!='EXPERIMENT_COMPLETE_NO_PROMOTION'
        or report.get('mode')!='solve' or report.get('source_anchor')!=
                   'VERIFIED_V14_POST_CONSOLIDATION_NOT_V18'
        or report.get('measurement_authority')!='V13_FULL_TRAIN_128_CASE_CENSUS'
        or report.get('hard_train_retention')!='UNCHANGED_V14_FULL_TRAIN_EXACT_INFERENCE'
        or report.get('general_acquisition_training') is not False
        or report.get('no_new_trainable_capacity') is not True
        or report.get('frozen_v11_specialist') is not True
        or report.get('production_weights_modified') is not False
        or report.get('promotion_authorized') is not False
        or report.get('synthetic_dev_used_for_training_or_selection') is not False
        or report.get('previously_seen_test_used_for_training_or_selection') is not False
        or report.get('training_capture_sha256')!=capture
        or report.get('frozen_lineage')!=data['probe_info']['lineage']
        or report.get('v6_prior_sha256')!=prior_sha
        or report.get('v11_verified')!=proof or report.get('v14_verified')!=verified14
        or report.get('v15_report_sha256')!=sha15
        or report.get('v16_report_sha256')!=sha16
        or report.get('v17_report_sha256')!=sha17
        or report.get('reference_v18')!=evidence
        or report.get('focus_group')!=focus
        or report.get('checkpoint_saved') is not True
        or report.get('summary',{}).get('hard_full_train_retention') is not True):
        raise ValueError('V20_V19_REPORT_PROVENANCE_MISMATCH')
    if set(report.get('config',{}))!=set(CONFIG_KEYS):
        raise ValueError('V20_V19_CONFIG_INCOMPLETE')
    for key in CONFIG_KEYS:
        x=report['config'][key]
        y=expected[key]
        if isinstance(y,float):
            if not math.isclose(float(x),y,rel_tol=1e-11,abs_tol=1e-12):
                raise ValueError('V20_V19_CONFIG_MISMATCH_'+key)
        elif x!=y:
            raise ValueError('V20_V19_CONFIG_MISMATCH_'+key)
    return True


def _read_trials(path):
    if not Path(path).is_file():
        raise ValueError('V20_V19_TRIAL_LEDGER_MISSING')
    with Path(path).open(newline='',encoding='utf-8') as fh:
        rows=list(csv.DictReader(fh))
    if not rows or any('decision' not in row for row in rows):
        raise ValueError('V20_V19_TRIAL_LEDGER_INVALID')
    return rows


def _verify_replay_rows(saved,reproduced,*,tolerance=3e-6):
    """No certification if decisions, candidate ordering, or outcomes differ."""
    if len(saved)!=len(reproduced):
        raise ValueError('V20_REPLAY_TRIAL_COUNT_MISMATCH')
    for n,(actual,rerun) in enumerate(zip(saved,reproduced)):
        for key in ('iteration','direction','decision'):
            if str(actual.get(key,''))!=str(rerun.get(key,'')):
                raise ValueError(f'V20_REPLAY_DECISION_MISMATCH_{n}_{key}')
        if actual.get('halvings','')!=('' if 'halvings' not in rerun else str(rerun['halvings'])):
            raise ValueError(f'V20_REPLAY_HALVING_MISMATCH_{n}')
        for key in ('after_a','after_b','merit_after','merit_gain','correct_train'):
            if key not in rerun:
                continue
            try:
                x=float(actual[key]);y=float(rerun[key])
            except (ValueError,TypeError,KeyError):
                raise ValueError(f'V20_REPLAY_VALUE_MISSING_{n}_{key}') from None
            if not math.isfinite(x) or abs(x-y)>tolerance:
                raise ValueError(f'V20_REPLAY_NUMERIC_MISMATCH_{n}_{key}')
    return True


def _numerical_census(torch,model,geom,indices,*,capture_logits=False):
    """Evaluate 128-aligned batches in the model's actual parameter dtype."""
    import numpy as np
    dtype=next(model.parameters()).dtype
    x=geom['encoded'].to(dtype=dtype)
    old=geom['old'].to(dtype=dtype)
    values=[];logits=[]
    model.eval()
    with torch.no_grad():
        for start in range(0,len(geom['truth']),128):
            segment=x[start:start+128]
            base=old[start:start+128]
            residual=model(segment)
            observed=v13._class_margins(torch,model,segment,base,
                                          geom['truth'][start:start+128])
            values.append(observed.detach().cpu().numpy())
            if capture_logits:
                raw=joint._head_logits(torch,base,residual).detach().cpu().numpy()
                logits.append(raw)
    safety=np.concatenate(values).astype(np.float64)
    required=np.asarray(geom['required'],dtype=np.float64)
    protected=np.asarray(geom['mask'],dtype=bool)
    merit=float(np.maximum(required[protected]-safety[protected],0.).sum())
    raw=np.concatenate(logits) if capture_logits else None
    return {'pair':[float(safety[i]) for i in indices],
            'merit':merit,'raw_logits':[raw[i].tolist() for i in indices] if raw is not None else None}


def _ulp_scale(logits):
    import numpy as np
    return [float(max(abs(np.spacing(np.float32(v))) for v in item)) for item in logits]


def _observed_gain(before,after):
    return [float(a-b) for a,b in zip(after,before)]


def _classify_trial(row,*,fp32,fp64,repeat_jitter,ulp):
    """Disjoint evidence categories; not a speculative cause assignment."""
    a,b=fp32; da,db=fp64
    resolution=max(4.*max(ulp),3.*repeat_jitter,1e-9)
    if repeat_jitter>max(1e-6,4.*max(ulp)):
        return 'EVALUATION_NOT_REPEATABLE'
    if a>resolution and b < -resolution or b>resolution and a < -resolution:
        return 'RESOLVED_OPPOSING_MARGIN_TRADEOFF'
    if a<=resolution and b<=resolution and da<=resolution and db<=resolution:
        return 'NO_RESOLVED_JOINT_ASCENT'
    if min(da,db)>resolution and min(a,b)<=resolution:
        return 'FP32_VS_FP64_EVALUATION_DISAGREEMENT'
    if min(a,b)>0 and (min(a,b)<row['min_joint_gain'] or
                       row['merit_gain']<row['min_merit_gain']):
        return 'POSITIVE_BUT_BELOW_ACCEPTANCE_THRESHOLDS'
    if min(a,b)>0:
        return 'BOTH_MARGIN_GAINS_RESOLVED'
    return 'MIXED_OR_SUB_RESOLUTION_CHANGES'


class _AuditObserver:
    """Read-only observer invoked only after V19 restores the proposal source."""
    def __init__(self,torch,model,geom,*,min_joint_gain,min_merit_gain,repeats,from_iteration):
        self.torch=torch;self.model=model;self.geom=geom
        self.min_joint_gain=min_joint_gain;self.min_merit_gain=min_merit_gain
        self.repeats=repeats;self.from_iteration=from_iteration
        self.double_model=copy.deepcopy(model).double().eval()
        self.rows=[];self.memo={}
        self.initial=v9._copy_state(model)

    def _measure(self,net,state,indices,*,repeats,capture):
        net.load_state_dict(state,strict=True);net.eval()
        return [_numerical_census(self.torch,net,self.geom,indices,capture_logits=capture)
                 for _ in range(repeats)]

    def __call__(self,*,iteration,direction_name,halvings,alpha,model,geometry,
                 state,candidate,direction,indices,before_pair,after_pair,
                 current_merit,evaluation,decision,admitted,correct_trial):
        if iteration<self.from_iteration:
            return
        if model is not self.model or geometry is not self.geom:
            raise ValueError('V20_OBSERVER_INCORRECT_MODEL')
        key=int(iteration)
        if key not in self.memo:
            baseline_double=self._measure(self.double_model,state,indices,
                                          repeats=self.repeats,capture=True)
            baseline_32=self._measure(model,state,indices,repeats=self.repeats,capture=True)
            self.memo[key]={'double':baseline_double,'float':baseline_32}
        baseline=self.memo[key]
        try:
            repeated32=self._measure(model,candidate,indices,repeats=self.repeats,capture=True)
            repeated64=self._measure(self.double_model,candidate,indices,repeats=self.repeats,capture=True)
            d32=_observed_gain(baseline['float'][0]['pair'],repeated32[0]['pair'])
            d64=_observed_gain(baseline['double'][0]['pair'],repeated64[0]['pair'])
            jitters=[]
            for repeated in (baseline['float'],baseline['double'],repeated32,repeated64):
                for j in (0,1):
                    jitters.append(max(v['pair'][j] for v in repeated)-min(v['pair'][j] for v in repeated))
            jitter=max(jitters)
            ulp=_ulp_scale(repeated32[0]['raw_logits']+baseline['float'][0]['raw_logits'])
            gradient_cache=self.memo[key].get('gradients')
            if gradient_cache is None:
                model.load_state_dict(state,strict=True);model.eval()
                gradient_cache=v19._census_gradients(self.torch,model,geometry,indices)
                self.memo[key]['gradients']=gradient_cache
            actual_delta=[]
            for name,parameter in model.named_parameters():
                actual_delta.append((candidate[name].detach().cpu().float()-
                                     state[name].detach().cpu().float()).reshape(-1))
            actual_delta=self.torch.cat(actual_delta)
            prediction=[float(self.torch.dot(grad,actual_delta)) for grad in gradient_cache]
            effective_norm=float(self.torch.linalg.vector_norm(actual_delta))
            measured_merit_gain=float(baseline['float'][0]['merit']-repeated32[0]['merit'])
            measured_double_gain=float(baseline['double'][0]['merit']-repeated64[0]['merit'])
            classification=_classify_trial({'min_joint_gain':self.min_joint_gain,
                'min_merit_gain':self.min_merit_gain,'merit_gain':measured_merit_gain},
                fp32=d32,fp64=d64,repeat_jitter=jitter,ulp=ulp)
            self.rows.append({'iteration':iteration,'direction':direction_name,
                'halvings':halvings,'alpha':float(alpha),'decision':decision,
                'originally_admitted':bool(admitted),'protected_retention_pass':bool(evaluation['retention_pass']),
                'protected_lost':int(evaluation['protected_lost']),
                'effective_parameter_displacement_norm':effective_norm,
                'predicted_delta_a':prediction[0],'predicted_delta_b':prediction[1],
                'float32_delta_a':d32[0],'float32_delta_b':d32[1],
                'float64_delta_a':d64[0],'float64_delta_b':d64[1],
                'float32_merit_gain':measured_merit_gain,
                'float64_merit_gain':measured_double_gain,
                'float32_merit_after':repeated32[0]['merit'],
                'float64_merit_after':repeated64[0]['merit'],
                'float32_raw_before_a':probe.canonical(baseline['float'][0]['raw_logits'][0]),
                'float32_raw_before_b':probe.canonical(baseline['float'][0]['raw_logits'][1]),
                'float32_raw_after_a':probe.canonical(repeated32[0]['raw_logits'][0]),
                'float32_raw_after_b':probe.canonical(repeated32[0]['raw_logits'][1]),
                'float64_raw_before_a':probe.canonical(baseline['double'][0]['raw_logits'][0]),
                'float64_raw_before_b':probe.canonical(baseline['double'][0]['raw_logits'][1]),
                'float64_raw_after_a':probe.canonical(repeated64[0]['raw_logits'][0]),
                'float64_raw_after_b':probe.canonical(repeated64[0]['raw_logits'][1]),
                'float32_vs_original_delta_a':d32[0]-(after_pair[0]-before_pair[0]),
                'float32_vs_original_delta_b':d32[1]-(after_pair[1]-before_pair[1]),
                'repeat_max_safety_spread':jitter,
                'max_raw_logit_float32_ulp':max(ulp),
                'joint_margin_gain_threshold':self.min_joint_gain,
                'aggregate_gain_threshold':self.min_merit_gain,
                'joint_margin_threshold_shortfall':max(0.,self.min_joint_gain-min(d32)),
                'aggregate_gain_threshold_shortfall':max(0.,self.min_merit_gain-measured_merit_gain),
                'diagnostic_class':classification})
            if len(self.rows)%11==0:
                print('V20_REPLAY_PROGRESS '+probe.canonical({
                    'audited':len(self.rows),'iteration':iteration,
                    'last_diagnostic_class':classification}),flush=True)
        finally:
            model.load_state_dict(state,strict=True);model.eval()
            self.double_model.load_state_dict(self.initial,strict=True);self.double_model.eval()


def _check_checkpoint(torch,path,report,verified14,capture,evidence,model,expected_state,geom):
    import numpy as np
    cp=torch.load(path,map_location='cpu',weights_only=True)
    if (cp.get('schema')!=v19.SCHEMA or cp.get('status')!='DISPOSABLE_NON_PRODUCTION'
        or cp.get('promotion_authorized') is not False
        or cp.get('protected_original_passthrough') is not True
        or cp.get('hard_full_train_retention') is not True
        or cp.get('training_capture_sha256')!=capture
        or cp.get('v14_post_sha256')!=verified14['v14_post_consolidation_sha256']
        or cp.get('v18_checkpoint_sha256')!=evidence['v18_checkpoint_sha256']
        or not np.array_equal(cp['train_ledger_mask'].numpy(),geom['mask'])):
        # V19 ledger may have expanded only when new answers are acquired;
        # no such expansion happened in the recorded v19 run.
        raise ValueError('V20_V19_CHECKPOINT_PROVENANCE_MISMATCH')
    if cp.get('frozen_lineage')!=report['frozen_lineage']:
        raise ValueError('V20_V19_CHECKPOINT_LINEAGE_MISMATCH')
    max_diff=0.
    for k,v in expected_state.items():
        if k not in cp['weights'] or cp['weights'][k].shape!=v.shape:
            raise ValueError('V20_REPLAY_CHECKPOINT_SHAPE_MISMATCH')
        max_diff=max(max_diff,float((cp['weights'][k].float()-v.cpu().float()).abs().max()))
    if max_diff>1e-6:
        raise ValueError('V20_REPLAY_CHECKPOINT_WEIGHT_MISMATCH')
    return {'checkpoint_sha256':v19._sha(path),
            'max_float32_parameter_difference_from_replay':max_diff,
            'verified':True}


def _aggregate(rows):
    from collections import Counter
    counts=Counter(r['diagnostic_class'] for r in rows)
    return {'classification_counts':dict(sorted(counts.items())),
            'retention_safe':sum(bool(r['protected_retention_pass']) for r in rows),
            'fp32_positive_joint':sum(min(r['float32_delta_a'],r['float32_delta_b'])>0 for r in rows),
            'fp64_positive_joint':sum(min(r['float64_delta_a'],r['float64_delta_b'])>0 for r in rows),
            'float32_vs_float64_sign_changes':sum(any((r[f'float32_delta_{c}']*r[f'float64_delta_{c}']<0)
                                    for c in ('a','b')) for r in rows),
            'max_repeat_spread':max((r['repeat_max_safety_spread'] for r in rows),default=0.)}


def _validate(args,torch):
    if (args.mode!='audit' or not 2<=args.repeats<=8
        or not 3<=args.from_iteration<=100 or args.device.startswith('cuda') and
        not torch.cuda.is_available()):
        raise ValueError('V20_INVALID_CONFIGURATION')


def run(args):
    import numpy as np
    import torch
    _validate(args,torch)
    input_files=[args.run,args.expanded_run,args.input,args.source_features,args.prior,
           args.v11_run,args.v14_run,args.v15_run,args.v16_run,args.v17_run,args.v18_run,args.v19_run]
    out=lab._output(args.output,[Path(x) for x in input_files])
    if out.exists():raise FileExistsError(out)
    file19=Path(args.v19_run)/'report.json'
    cp19=Path(args.v19_run)/'precision_joint_disposable_candidate.pt'
    if not (file19.is_file() and cp19.is_file()):
        raise ValueError('V20_V19_REPORT_OR_CHECKPOINT_MISSING')
    saved=json.loads(file19.read_text(encoding='utf-8'))
    config=saved.get('config',{})
    if set(config)!=set(CONFIG_KEYS):
        raise ValueError('V20_V19_CONFIG_INCOMPLETE')
    data,prepared=joint._prepare(args)
    spec,_=v6._routed_profiles(data,prepared)
    _,capture=v9._groups_and_fingerprints(spec)
    _,mu,sd,_=v7._read_v6_checkpoint(torch,args.prior,data,spec)
    _,cp11,_,proof=v12._verify_v11(torch,args.v11_run,data,capture,
                                    v19._sha(args.prior),spec,args.device)
    if not (np.array_equal(cp11['selected']['mean'].numpy(),mu)
            and np.array_equal(cp11['selected']['std'].numpy(),sd)):
        raise ValueError('V20_NORMALIZER_MISMATCH')
    report14,post,model,verified14=v15._check_prior14(args.v14_run,proof,capture,
          v19._sha(args.prior),data,args.focus_group,torch,spec,mu,sd,args.device)
    _,sha15=v16._checked_prior15(args.v15_run,verified14,capture,
                                v19._sha(args.prior),args.focus_group,data,config['reserve'])
    _,sha16=v17._checked_v16(args.v16_run,sha15,capture,verified14,data,
                             v19._sha(args.prior),args.focus_group,config['reserve'])
    _,sha17=v18._checked_v17(args.v17_run,sha16,capture,verified14,data,
                             v19._sha(args.prior),args.focus_group,config['reserve'])
    geom=v16._geometry(torch,model,post,spec,mu,sd,args.device,args.focus_group,
                       config['reserve'],config['max_gradient_cases'],v12._deck_prompts(data))
    evidence=v19._verify_v18(torch,args.v18_run,sha17,verified14,capture,data,
            v19._sha(args.prior),args.focus_group,config['reserve'],model,
            geom,spec,mu,sd,args.device)
    _validate_saved_report(saved,config,capture,data,proof,verified14,
                            sha15,sha16,sha17,evidence,v19._sha(args.prior),args.focus_group)
    expected_saved=_read_trials(Path(args.v19_run)/'precision_joint_trials.csv')
    model.eval();anchor=v9._copy_state(model)
    observer=_AuditObserver(torch,model,geom,min_joint_gain=config['min_joint_gain'],
                      min_merit_gain=config['min_merit_gain'],repeats=args.repeats,
                      from_iteration=args.from_iteration)
    replay_rows=[]
    summary,replayed_checkpoint=v19._search(torch,model,geom,spec,mu,sd,args.device,
        max_iterations=config['max_iterations'],max_stalls=config['max_stalls'],
        target_step=config['target_step'],min_target_step=config['min_target_step'],
        trust_radius=config['trust_radius'],max_halvings=config['max_halvings'],
        fd_epsilon=config['fd_epsilon'],min_joint_gain=config['min_joint_gain'],
        min_merit_gain=config['min_merit_gain'],trial_rows=replay_rows,
        probe_observer=observer)
    _verify_replay_rows(expected_saved,replay_rows)
    saved_summary=saved['summary']
    if (summary['accepted_iterations']!=saved_summary['accepted_iterations']
        or summary['trial_count']!=saved_summary['trial_count']
        or abs(summary['final_deficit']-saved_summary['final_deficit'])>3e-6
        or any(abs(a-b)>3e-6 for a,b in zip(summary['final_safety'],saved_summary['final_safety']))):
        raise ValueError('V20_REPLAY_SUMMARY_MISMATCH')
    checkpoint=_check_checkpoint(torch,cp19,saved,verified14,capture,evidence,
                                  model,replayed_checkpoint,geom)
    if any(not torch.equal(model.state_dict()[k].cpu(),v.cpu()) for k,v in anchor.items()):
        raise ValueError('V20_V14_ANCHOR_MUTATED')
    if not np.array_equal(v9._correct_train(torch,model,spec,mu,sd,args.device),geom['mask']):
        raise ValueError('V20_V14_TRAIN_MASK_MUTATED')
    report_hash=v19._sha(file19);ledger_hash=v19._sha(Path(args.v19_run)/'precision_joint_trials.csv')
    count_expected=sum(int(x['iteration'])>=args.from_iteration and x.get('halvings','')!=''
                      for x in expected_saved)
    if len(observer.rows)!=count_expected:
        raise ValueError('V20_REPLAY_OBSERVER_COVERAGE_MISMATCH')
    statistics=_aggregate(observer.rows)
    report={'schema':SCHEMA,'status':'READ_ONLY_VERIFIED_DIAGNOSTIC_NO_PROMOTION',
        'algorithm':'REPLAY_REJECTED_V19_CANDIDATES_FP32_AND_TRUE_ADAPTER_FP64',
        'v19_report_sha256':report_hash,'v19_trials_sha256':ledger_hash,
        'v19_checkpoint_verified':checkpoint,'source_anchor':'VERIFIED_V14_POST_CONSOLIDATION',
        'v19_trial_count_verified':len(replay_rows),
        'candidate_probes_verified':len(observer.rows),
        'replay_iteration_floor':args.from_iteration,'repeat_count':args.repeats,
        'classification':statistics,'v19_final_deficit':summary['final_deficit'],
        'v19_accepted_updates':summary['accepted_iterations'],
        'interpretation_limitations':[
          'Float64 recomputes the existing adapter and downstream logits only',
          'Frozen upstream features and saved weights were originally float32',
          'Replay certification requires saved decisions and checkpoint agreement',
          'No new parameter updates are accepted or stored',
          'No general claim of global optimizer feasibility'],
        'TRAIN_only_gradients':True,'hard_train_retention_unchanged':True,
        'frozen_lineage':data['probe_info']['lineage'],
        'production_weights_modified':False,'promotion_authorized':False,
        'original_task_bypass':'UNCHANGED_V6',
        'synthetic_dev_used_for_training_or_selection':False,
        'previously_seen_test_used_for_training_or_selection':False,
        'checkpoint_saved':False}
    if (v19._sha(file19)!=report_hash or
        v19._sha(Path(args.v19_run)/'precision_joint_trials.csv')!=ledger_hash or
        v19._sha(cp19)!=checkpoint['checkpoint_sha256']):
        raise ValueError('V20_V19_EVIDENCE_CHANGED_DURING_AUDIT')
    joint._verify_after(data)
    out.mkdir(parents=True,exist_ok=False)
    v10._csv(out/'precision_replay_trials.csv',observer.rows or [{'diagnostic_class':'NO_REJECTED_TRIALS'}])
    v10._csv(out/'precision_classification_summary.csv',[
        {'category':key,'count':value} for key,value in statistics['classification_counts'].items()
        ] or [{'category':'NO_REJECTED_TRIALS','count':0}])
    (out/'report.json').write_text(json.dumps(report,indent=2,sort_keys=True,allow_nan=False)+'\n',encoding='utf-8')
    print('V20_COMPLETE '+probe.canonical({'status':report['status'],
        'probes':len(observer.rows),'classifications':statistics['classification_counts'],
        'checkpoint_saved':False,'promotion':False,'report':str(out/'report.json')}),flush=True)
    return report


def parser():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--mode',choices=('audit',),default='audit')
    for name in ('run','expanded-run','input','source-features','prior','v11-run',
            'v14-run','v15-run','v16-run','v17-run','v18-run','v19-run','output'):
        p.add_argument('--'+name,required=True,type=Path)
    p.add_argument('--device',default='cuda')
    p.add_argument('--focus-group',default=v12.FOCUS)
    p.add_argument('--from-iteration',type=int,default=3)
    p.add_argument('--repeats',type=int,default=3)
    return p


def main(argv=None):
    run(parser().parse_args(argv));return 0


if __name__=='__main__':
    raise SystemExit(main())
