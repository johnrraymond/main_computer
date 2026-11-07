#!/usr/bin/env python3
"""Read-only causal sign-contrast audit across frozen C0 and trained LoRA snapshots.

This experiment uses the EXACT pre-existing GAMER sign oracle and the snapshots
from nanojev_gamer_v1_lora_sign_stress. It never trains, promotes, or edits a
model. It asks whether natural signed oracle contrasts retain geometry through
C0, whether its two heads distinguish those contrasts, and whether synthetic
interventions along *real same-context sign vectors* move its final output more
than matched-norm shuffled-coordinate null directions.

Example:
  python -m tools.nanojev_gamer_v1_c0_sign_causality \
    --audit runtime/reports/gamer-v1/lora-sign-audit-0001 \
    --output runtime/reports/gamer-v1/c0-sign-causality-0001 \
    --steps 0,500,1500,3000 --device cuda

Statistical readouts and activations are observational; signed contrasts
may reflect lexical numeral changes as well as arithmetic. Counterfactual
stage interventions are synthetic and do not constitute correct answers.
"""
from __future__ import annotations

import argparse
import gc
import hashlib
import json
import os
from fractions import Fraction
from pathlib import Path

import numpy as np

SCHEMA='nanojev.gamer.v1.c0-sign-contrast-causal-response.v1'
STAGES=('c0_raw_flat','c0_norm','enc_linear_1','enc_gelu_1',
        'enc_linear_2','enc_gelu_2','enc_linear_3','enc_gelu_3',
        'mid_linear','mid_gelu')
INTERVENTIONS=('c0_norm','enc_linear_1','enc_gelu_1','enc_gelu_3','mid_gelu')


def _groups(rows, split='test'):
    """True minus/zero/plus triples from a *single* group and surface."""
    sets={}
    for i,r in enumerate(rows):
        if r['split']!=split:continue
        key=(r['group'],r['surface'])
        sign=int(np.sign(float(Fraction(r['target']))))
        if sign in sets.setdefault(key,{}):
            raise ValueError('GAMER_CAUSAL_DUPLICATE_SIGN_IN_GROUP')
        sets[key][sign]=i
    result=[]
    for (group,surface),s in sorted(sets.items()):
        if set(s)!=set((-1,0,1)):
            raise ValueError('GAMER_CAUSAL_INCOMPLETE_ORACLE_TRIPLE')
        families={rows[j]['family'] for j in s.values()}
        if len(families)!=1:raise ValueError('GAMER_CAUSAL_MIXED_FAMILY_GROUP')
        result.append({'group':group,'surface':surface,'family':next(iter(families)),
                       'negative':s[-1],'zero':s[0],'positive':s[1]})
    if not result:raise ValueError('GAMER_CAUSAL_EMPTY_TRIPLE_SPLIT')
    return result


def _stats(values):
    v=np.asarray(values,dtype=np.float64)
    if not len(v) or not np.all(np.isfinite(v)):
        raise ValueError('GAMER_CAUSAL_EMPTY_OR_NONFINITE_STATISTICS')
    return {'count':len(v),'min':float(v.min()),'p10':float(np.percentile(v,10)),
            'median':float(np.median(v)),'p90':float(np.percentile(v,90)),
            'max':float(v.max()),'mean':float(v.mean())}


def _geometry(rows,features):
    """Readout-independent within-context sign differences, anchored on train.

    Unit train sign axes are learned ONLY from train triples; their directional
    transfer to TEST is descriptive (no test-derived axis selection). Normed
    pair spans/midpoint residuals are reported separately by family.
    """
    train=_groups(rows,'train');test=_groups(rows,'test')
    x=np.asarray(features,dtype=np.float32)
    if x.ndim!=2 or len(x)!=len(rows) or not np.isfinite(x).all():
        raise ValueError('GAMER_CAUSAL_BAD_GEOMETRY_FEATURES')
    def span(t):
        p=x[t['positive']].astype(np.float64)
        n=x[t['negative']].astype(np.float64)
        z=x[t['zero']].astype(np.float64)
        d=p-n;norm=np.linalg.norm(d)
        return d,norm,float(np.linalg.norm(z-.5*(p+n))/max(norm,1e-12)),float(norm/max(np.linalg.norm(z),1e-12))
    train_directions=[]
    for t in train:
        direction,magnitude,*_=span(t)
        if magnitude>1e-12:train_directions.append(direction/magnitude)
    if not train_directions:
        raise ValueError('GAMER_CAUSAL_DEGENERATE_TRAIN_CONTRAST')
    axis=np.mean(train_directions,axis=0)
    if not np.linalg.norm(axis)>1e-12:
        raise ValueError('GAMER_CAUSAL_AMBIGUOUS_TRAIN_AXIS')
    axis=axis/np.linalg.norm(axis)
    by_family={}
    for family in sorted({t['family'] for t in test}):
        pairs=[t for t in test if t['family']==family]
        cos=[];spans=[];offsets=[];midpoints=[]
        for t in pairs:
            d,magnitude,midoffset,span_relative=span(t)
            cos.append(float(d@axis/max(magnitude,1e-12)))
            spans.append(magnitude)
            offsets.append(midoffset)
            midpoints.append(span_relative)
        by_family[family]={
            'triples':len(pairs),'train_axis_signed_cosine':_stats(cos),
            'sign_ordering_along_frozen_train_axis':float(np.mean(np.asarray(cos)>0)),
            'positive_negative_l2_span':_stats(spans),
            'span_over_zero_activation_l2':_stats(midpoints),
            'zero_distance_from_signed_midpoint_over_span':_stats(offsets)}
    return {'train_axis_fit':'TRAIN_TRIPLES_ONLY','test_by_family':by_family,
            'interpretation':'Geometry measures real paired oracle contrasts, which may reflect lexical differences; it is not proof of arithmetic competence or information-theoretic sufficiency.'}


def _read_stages(net,raw,*,device='cuda',batch=8):
    """Exactly replay the production C0 network and both frozen scalar heads."""
    import torch
    from tools.nanojev_qwen_c0_compressive_train import inverse_signed_log
    x=np.asarray(raw,dtype=np.float32)
    if x.ndim!=2 or x.shape[1]!=net.norm.normalized_shape[0] or not np.isfinite(x).all():
        raise ValueError('GAMER_CAUSAL_BAD_C0_INPUT')
    if len(net.enc)!=6 or len(net.mid)!=2:
        raise ValueError('GAMER_CAUSAL_UNEXPECTED_C0_GRAPH')
    dtype=next(net.parameters()).dtype
    outputs={s:[] for s in STAGES};raw_corrected=[];raw_aux=[]
    with torch.inference_mode():
        for start in range(0,len(x),batch):
            h=torch.as_tensor(x[start:start+batch],device=device,dtype=dtype)
            outputs['c0_raw_flat'].append(h.float().cpu().numpy())
            h=net.norm(h)
            outputs['c0_norm'].append(h.float().cpu().numpy())
            for i,layer in enumerate(net.enc):
                h=layer(h)
                key=f'enc_{"linear" if i%2==0 else "gelu"}_{i//2+1}'
                outputs[key].append(h.float().cpu().numpy())
            raw_aux.append(net.uncorrected(h).flatten().float().cpu().numpy())
            for i,layer in enumerate(net.mid):
                h=layer(h)
                outputs['mid_linear' if i==0 else 'mid_gelu'].append(h.float().cpu().numpy())
            raw_corrected.append(net.corrected(h).flatten().float().cpu().numpy())
    stages={k:np.concatenate(v,axis=0) for k,v in outputs.items()}
    c=np.concatenate(raw_corrected).astype(np.float64)
    u=np.concatenate(raw_aux).astype(np.float64)
    if any(not np.isfinite(v).all() for v in (*stages.values(),c,u)):
        raise ValueError('GAMER_CAUSAL_NONFINITE_C0_STAGE')
    return stages,c,u,np.asarray(inverse_signed_log(c),dtype=np.float64)


def _suffix_corrected(net,stage,values,*,device='cuda',batch=8):
    """Frozen suffix of C0 starting immediately AFTER named stage.

    No parameter mutation or hooks. Its output is pre-inverse-signed-log,
    preserving the actual sign decision boundary at zero.
    """
    import torch
    if stage not in STAGES:raise ValueError('GAMER_CAUSAL_UNKNOWN_STAGE')
    x=np.asarray(values,dtype=np.float32)
    result=[];dtype=next(net.parameters()).dtype
    with torch.inference_mode():
        for start in range(0,len(x),batch):
            h=torch.as_tensor(x[start:start+batch],dtype=dtype,device=device)
            if stage=='c0_raw_flat':h=net.norm(h)
            enc_start=0
            if stage.startswith('enc_'):
                part=stage.split('_')
                enc_start=(int(part[2])-1)*2+(1 if part[1]=='linear' else 2)
            elif stage in ('mid_linear','mid_gelu'):
                enc_start=len(net.enc)
            if stage not in ('mid_linear','mid_gelu'):
                for layer in list(net.enc)[enc_start:]:h=layer(h)
            if stage=='mid_linear':h=net.mid[1](h)
            elif stage!='mid_gelu':
                for layer in net.mid:h=layer(h)
            result.append(net.corrected(h).flatten().float().cpu().numpy())
    return np.concatenate(result).astype(np.float64)


def _counterfactual(net,rows,stages,corrected,stage,*,device,batch=8,seed=47):
    """Intervene on held-out ZERO examples using their natural +/- contrast.

    This does not create valid mathematical prompts: it measures downstream
    sensitivity to a real same-context change in hidden activation. Permuted
    contrasts provide matched L2 nulls with identical intervention size.
    """
    if stage not in stages:raise ValueError('GAMER_CAUSAL_STAGE_UNAVAILABLE')
    x=np.asarray(stages[stage],dtype=np.float32)
    triples=_groups(rows,'test')
    rng=np.random.default_rng(seed+STAGES.index(stage)*7001)
    records=[]
    for t in triples:
        n,z,p=(x[t[k]].astype(np.float64) for k in ('negative','zero','positive'))
        diff=(p-n)*.5
        # Keep same absolute intervention norm for real and null axes.
        null=diff[rng.permutation(len(diff))]
        records.append((t,z,diff,null))
    result={}
    for arm in ('real_sign_contrast','coordinate_permuted_equal_l2_null'):
        samples=[]
        for t,z,d,null in records:
            axis=d if arm=='real_sign_contrast' else null
            samples.extend((z-axis,z+axis))
        probe=_suffix_corrected(net,stage,np.asarray(samples,dtype=np.float32),
                                device=device,batch=batch)
        by_family={}
        for family in sorted({t['family'] for t in triples}):
            ixs=[i for i,(t,_,_,_) in enumerate(records) if t['family']==family]
            minus=probe[2*np.asarray(ixs)];plus=probe[2*np.asarray(ixs)+1]
            centers=np.asarray([corrected[records[i][0]['zero']] for i in ixs])
            negative=np.asarray([corrected[records[i][0]['negative']] for i in ixs])
            positive=np.asarray([corrected[records[i][0]['positive']] for i in ixs])
            by_family[family]={
                'triples':len(ixs),
                'intervention_plus_minus_signed_log_span':_stats(plus-minus),
                'fraction_plus_increases_output':float(np.mean(plus>centers)),
                'fraction_minus_decreases_output':float(np.mean(minus<centers)),
                'fraction_both_directions_expected':float(np.mean((plus>centers)&(minus<centers))),
                'fraction_plus_crosses_zero':float(np.mean(plus>0)),
                'fraction_minus_crosses_zero':float(np.mean(minus<0)),
                'actual_oracle_positive_minus_negative_signed_log_span':_stats(positive-negative),
                'mean_abs_intervention_span':float(np.mean(np.abs(plus-minus)))}
        result[arm]=by_family
    comparisons={}
    for family in result['real_sign_contrast']:
        real=result['real_sign_contrast'][family]
        null=result['coordinate_permuted_equal_l2_null'][family]
        comparisons[family]={
            'real_over_null_abs_output_response':real['mean_abs_intervention_span']/max(null['mean_abs_intervention_span'],1e-12),
            'real_fraction_both_expected_minus_null':real['fraction_both_directions_expected']-null['fraction_both_directions_expected'],
            'real_mean_signed_response':real['intervention_plus_minus_signed_log_span']['mean'],
            'null_mean_signed_response':null['intervention_plus_minus_signed_log_span']['mean']}
    return {'stage':stage,'arms':result,'real_versus_equal_l2_null':comparisons,
            'intervention_is_synthetic_not_mathematical_counterfactual':True}


def _head_analysis(rows,c_log,u_log,stage_mid,net):
    from tools.nanojev_qwen_c0_compressive_train import inverse_signed_log
    t=_groups(rows,'test')
    test=sorted({j for triple in t for j in (triple['negative'],triple['zero'],triple['positive'])})
    bias=float(net.corrected.bias.detach().float().cpu().numpy().reshape(-1)[0])
    contribution=c_log-bias
    by_family={}
    for family in sorted({rows[i]['family'] for i in test}):
        d={}
        for s in (-1,0,1):
            ids=[i for i in test if rows[i]['family']==family and int(np.sign(float(Fraction(rows[i]['target']))))==s]
            label=str(s)
            d[label]={
                'corrected_signed_log':_stats(c_log[ids]),
                'uncorrected_signed_log':_stats(u_log[ids]),
                'corrected_value':_stats(inverse_signed_log(c_log[ids])),
                'corrected_weight_dot_mid':_stats(contribution[ids]),
                'raw_fraction_negative':float(np.mean(c_log[ids]<0)),
                'raw_fraction_positive':float(np.mean(c_log[ids]>0)),
                'corrected_threshold_zero_fraction':float(np.mean(np.abs(inverse_signed_log(c_log[ids]))<=.05)),
            }
        by_family[family]=d
    mat=np.asarray(stage_mid,dtype=np.float32)
    return {'corrected_final_linear_bias_signed_log':bias,
            'corrected_final_linear_weight_l2':float(np.linalg.norm(net.corrected.weight.detach().float().cpu().numpy())),
            'mid_activation_rms':float(np.sqrt(np.mean(np.asarray(mat,dtype=np.float64)**2))),
            'test_by_family_and_true_sign':by_family,
            'auxiliary_uncorrected_head_is_frozen_diagnostic_only':True}



def _head_analytical_ablation(rows,corrected_log,aux_log,net):
    """No-training head-offset test and frozen uncorrected head comparison.

    These alternatives are diagnostic transforms, NOT legitimate deployed C0
    predictions. The zero and full-triple gates are scored unconditionally.
    """
    from tools import nanojev_gamer_v1_sign_information_probe as oracle
    from tools.nanojev_qwen_c0_compressive_train import inverse_signed_log
    corrected_bias=float(net.corrected.bias.detach().float().cpu().reshape(-1)[0])
    aux_bias=float(net.uncorrected.bias.detach().float().cpu().reshape(-1)[0])
    indices=oracle._test_partitions(rows)['test']
    targets=np.asarray([float(Fraction(r['target'])) for r in rows],dtype=np.float64)
    out={}
    for name,logit in {
        'deployed_corrected_head':np.asarray(corrected_log,dtype=np.float64),
        'corrected_minus_frozen_bias':np.asarray(corrected_log,dtype=np.float64)-corrected_bias,
        'frozen_uncorrected_auxiliary_head':np.asarray(aux_log,dtype=np.float64),
        'uncorrected_minus_frozen_bias':np.asarray(aux_log,dtype=np.float64)-aux_bias,
    }.items():
        vals=np.asarray(inverse_signed_log(logit),dtype=np.float64)
        classes=np.where(np.abs(vals)<=.05,0,np.sign(vals)).astype(np.int8)
        total=oracle._metrics(targets[indices],vals[indices],classes[indices],rows,indices)
        families={}
        for family in sorted({rows[int(i)]['family'] for i in indices}):
            ids=np.asarray([i for i in indices if rows[int(i)]['family']==family],dtype=int)
            families[family]=oracle._metrics(targets[ids],vals[ids],classes[ids],rows,ids)
        out[name]={'test':total,'test_by_family':families}
    return {'corrected_bias_signed_log':corrected_bias,
            'uncorrected_bias_signed_log':aux_bias,
            'alternatives':out,
            'deployed_head_unchanged':True,
            'bias_ablation_is_diagnostic_not_a_numerical_repair':True}



def _trajectory_summary(result):
    """Descriptive, predeclared comparisons, never promotes a causal verdict."""
    steps=sorted(result['steps'],key=int)
    baseline=result['steps'][steps[0]]
    final=result['steps'][steps[-1]]
    report={'baseline_step':int(steps[0]),'last_step':int(steps[-1]),
            'fixed_c0':True,'families':{},
            'interpretation':'Changes are measured against frozen C0. Intervention effects are synthetic; neither neural information destruction nor arithmetic transfer is certified.'}
    for family in ('signed_offset','hawk_indifference'):
        def evidence(r):
            prod=r['production_c0']['test_by_family'][family]
            pair={}
            for stage in ('c0_norm','enc_linear_1','enc_gelu_3','mid_gelu'):
                metrics=r['real_oracle_pair_geometry'][stage]['test_by_family'][family]
                pair[stage]={
                    'frozen_train_axis_sign_ordering':metrics['sign_ordering_along_frozen_train_axis'],
                    'median_sign_span_over_zero_activation':metrics['span_over_zero_activation_l2']['median'],
                    'median_zero_midpoint_residual_over_span':metrics['zero_distance_from_signed_midpoint_over_span']['median']}
            head=r['corrected_vs_aux_and_bias_ablations']['alternatives']
            return {'production_sign_accuracy':prod['balanced_sign_accuracy'],
                    'production_negative_recall':prod['recall']['-1'],
                    'production_zero_recall':prod['recall']['0'],
                    'production_complete_triples':prod['triple_all_correct'],
                    'bias_subtraction_sign_accuracy':head['corrected_minus_frozen_bias']['test_by_family'][family]['balanced_sign_accuracy'],
                    'bias_subtraction_complete_triples':head['corrected_minus_frozen_bias']['test_by_family'][family]['triple_all_correct'],
                    'auxiliary_head_sign_accuracy':head['frozen_uncorrected_auxiliary_head']['test_by_family'][family]['balanced_sign_accuracy'],
                    'stages':pair}
        report['families'][family]={'before':evidence(baseline),'after':evidence(final)}
    return report


def _select_steps(report,requested):
    available={int(k) for k in report['steps']}
    if requested=='all':selected=sorted(available)
    else:
        try:selected=sorted({int(x.strip()) for x in requested.split(',') if x.strip()})
        except ValueError:raise ValueError('GAMER_CAUSAL_BAD_STEP_LIST') from None
    if not selected or 0 not in selected or not set(selected)<=available:
        raise ValueError('GAMER_CAUSAL_REQUIRE_ZERO_AND_AUDITED_SNAPSHOTS')
    return selected


def _safe_output(path,protected):
    out=Path(path).resolve()
    if len(out.name)>110 or (os.name=='nt' and len(str(out/'final_report.json'))>=240):
        raise ValueError('GAMER_CAUSAL_OUTPUT_PATH_TOO_LONG')
    for p in protected:
        p=Path(p).resolve()
        if out==p or out in p.parents or p in out.parents:
            raise ValueError('GAMER_CAUSAL_OUTPUT_OVERLAPS_SOURCE')
    if out.exists():raise FileExistsError('GAMER_CAUSAL_OUTPUT_EXISTS')
    return out


def _digest(path):
    from tools import nanojev_gamer_v1_sufficiency as suff
    return suff._digest(Path(path))


def run(args):
    import torch
    from tools import nanojev_gamer_v1_lora_sign_stress as stress
    from tools import nanojev_gamer_v1_sign_information_probe as oracle
    from tools import nanojev_gamer_v1_c0_boundary as c0
    from tools import nanojev_gamer_v1_handoff as gamer
    from tools.nanojev_qwen_c0_compressive_train import inverse_signed_log

    audit_root=Path(args.audit).resolve()
    audit_report_path=audit_root/'final_report.json'
    report=json.loads(audit_report_path.read_text(encoding='utf-8'))
    if (report.get('schema')!=stress.AUDIT_SCHEMA or
        report.get('status')!='AUDIT_COMPLETE_NO_PRODUCTION_UPDATES' or
        report.get('promotion')!='BLOCKED_DIAGNOSTIC_ONLY' or
        report.get('no_c0_weight_update') is not True):
        raise ValueError('GAMER_CAUSAL_REQUIRES_COMPLETED_READ_ONLY_LORA_AUDIT')
    steps=_select_steps(report,args.steps)
    source,source_report,meta,archive,capture=stress._source(report['source'])
    training=Path(report['training_run']).resolve()
    exp=json.loads((training/'experiment.json').read_text(encoding='utf-8'))
    lineage=meta['lineage']
    if (report['frozen_c0_sha256']!=lineage['checkpoint_sha256'] or
        report['original_adapter_sha256']!=lineage['adapter_sha256'] or
        report['oracle_dataset_sha256']!=meta['dataset_sha256'] or
        exp['source_preservation']!=str(source) or
        exp['source_preservation_sha256']!=_digest(source/'final_report.json') or
        exp['training_deck_sha256']!=report['training_deck_sha256'] or
        report['source']!=str(source) or
        Path(report['training_run']).resolve()!=training):
        raise ValueError('GAMER_CAUSAL_SOURCE_LINEAGE_MISMATCH')
    ckpt=Path(lineage['checkpoint_path'])
    original_adapter=Path(lineage['adapter_dir'])
    out=_safe_output(args.output,(audit_root,training,source,capture,ckpt.parent,original_adapter))
    original_fingerprints={
        'audit_report':_digest(audit_report_path),
        'preservation_report':_digest(source/'final_report.json'),
        'c0_checkpoint':_digest(ckpt),
        'original_adapter':stress._weights_hash(original_adapter),
        'training_plan':_digest(training/'experiment.json')}
    if (original_fingerprints['c0_checkpoint']!=lineage['checkpoint_sha256'] or
        original_fingerprints['original_adapter']!=lineage['adapter_sha256']):
        raise ValueError('GAMER_CAUSAL_SOURCE_WEIGHTS_CHANGED')
    rows=meta['dataset']
    baseline_deck=oracle.make_deck(
        sum(r['split']=='train' for r in rows)//6,
        sum(r['split']=='dev' for r in rows)//6,
        sum(r['split']=='test' and r['family']=='signed_offset' for r in rows)//6,
        include_ood=True)
    if [x.public() for x in baseline_deck]!=rows:
        raise ValueError('GAMER_CAUSAL_ORACLE_DECK_CHANGED')
    net=c0._load_head(meta,args.device)
    net.eval()
    cached=np.asarray(archive['lora__final_flat'],dtype=np.float32)
    _,base_logits,_,base_values=_read_stages(net,cached,device=args.device,batch=args.head_batch)
    c0._verify_baseline(base_values,archive['production_head_predictions'])
    del cached,base_logits,base_values,net
    gc.collect()
    if torch.cuda.is_available():torch.cuda.empty_cache()
    result={'schema':SCHEMA,'status':'INCOMPLETE','audit_source':str(audit_report_path),
            'frozen_fingerprints':original_fingerprints,'oracle_dataset_sha256':meta['dataset_sha256'],
            'steps_requested':steps,'steps':{},'no_training_or_model_mutation':True,
            'promotion':'BLOCKED_DIAGNOSTIC_ONLY',
            'interpretation_contract':{
                'native_heldout_pairs':'Same mathematical group and prompt surface, negative/zero/positive',
                'train_axis':'Learned only on frozen oracle TRAIN triples, evaluated on TEST',
                'counterfactual':'Synthetic zero-activation plus/minus half real within-triple contrast; matched-coordinate-permutation control',
                'absence_cannot_be_proven':'Information loss is not certified by a failed readout or geometrical axis',
                'real_task_gate':'Negative recall, zero recall and complete heldout triples at production head'}}
    out.mkdir(parents=True,exist_ok=False)
    for step in steps:
        snap=training/'snapshots'/f'step-{step:07d}'
        actual_sha=stress._verify_snapshot(snap,step)
        if actual_sha!=report['steps'][str(step)]['snapshot_sha256']:
            raise ValueError('GAMER_CAUSAL_SNAPSHOT_FINGERPRINT_MISMATCH')
        print(f'GAMER C0 CAUSAL CONTRAST: step={step}, LoRA={actual_sha[:12]} frozen C0',flush=True)
        _,tok,model=gamer.model_components(lineage['model_id'],args.device,adapter_dir=snap)
        features,_=oracle._capture_profiles(torch,model,tok,baseline_deck,
            device=args.device,max_length=lineage['configuration']['max_length'],
            batch_size=args.capture_batch,curvature=lineage['configuration']['curvature'],
            verify_exact_final=True)
        del model,tok
        gc.collect()
        if torch.cuda.is_available():torch.cuda.empty_cache()
        x=np.asarray(features['final_flat'],dtype=np.float16).astype(np.float32)
        if step==0:
            original=np.asarray(archive['lora__final_flat'],dtype=np.float16).astype(np.float32)
            if x.shape!=original.shape or np.max(np.abs(x-original))>.01:
                raise ValueError('GAMER_CAUSAL_ZERO_STEP_ACTIVATION_REPLAY_FAILED')
        del features
        # Load the 400M+ parameter C0 encoder only AFTER unloading Qwen.
        # Both resident on a laptop GPU can fail despite each working alone.
        net=c0._load_head(meta,args.device)
        net.eval()
        stages,c_log,u_log,output=_read_stages(net,x,device=args.device,batch=args.head_batch)
        del x
        production=stress._head_metrics(rows,output)
        prior=report['steps'][str(step)]['metrics']['production_c0']['test']
        if (abs(prior['balanced_sign_accuracy']-production['test']['balanced_sign_accuracy'])>1e-10 or
            prior['triple_all_correct']!=production['test']['triple_all_correct'] or
            abs(prior['signed_mae']-production['test']['signed_mae'])>0.001):
            raise ValueError('GAMER_CAUSAL_PREVIOUS_PRODUCTION_OUTPUT_PARITY_FAILED')
        # A measured counterfactual is meaningless if its C0 suffix does not
        # replay the exact same frozen output at the original activation.
        # Verify EVERY insertion boundary before intervening at any of them.
        suffix_parity={}
        for stage,acts in stages.items():
            reproduced=_suffix_corrected(net,stage,acts[:4],device=args.device,batch=4)
            error=float(np.max(np.abs(reproduced-c_log[:4])))
            suffix_parity[stage]=error
            if error>0.005:
                raise ValueError('GAMER_CAUSAL_FROZEN_SUFFIX_REPLAY_FAILED:'+stage)
        geometry={}
        for stage,acts in stages.items():
            geometry[stage]=_geometry(rows,acts)
        head=_head_analysis(rows,c_log,u_log,stages['mid_gelu'],net)
        offset_ablation=_head_analytical_ablation(rows,c_log,u_log,net)
        interventions={}
        for stage in INTERVENTIONS:
            print('  synthetic matched-null response: '+stage,flush=True)
            interventions[stage]=_counterfactual(net,rows,stages,c_log,stage,
                device=args.device,batch=args.head_batch,seed=args.seed)
        snapshot_report={'snapshot_adapter_sha256':actual_sha,
                         'production_c0':production,
                         'frozen_suffix_replay_max_abs_signed_log_delta':suffix_parity,
                         'frozen_head_log_geometry':head,
                         'corrected_vs_aux_and_bias_ablations':offset_ablation,
                         'real_oracle_pair_geometry':geometry,
                         'signed_axis_interventions':interventions}
        result['steps'][str(step)]=snapshot_report
        gamer.atomic_json(out/f'step-{step:07d}.json',snapshot_report)
        gamer.atomic_json(out/'progress.json',{'status':'IN_PROGRESS','complete_steps':list(result['steps'])})
        print(f'  production sign={production["test"]["balanced_sign_accuracy"]:.4f}, '
              f'neg={production["test"]["recall"]["-1"]:.4f}, '
              f'zero={production["test"]["recall"]["0"]:.4f}, '
              f'triples={production["test"]["triple_all_correct"]}',flush=True)
        del stages,c_log,u_log,output,net
        gc.collect()
        if torch.cuda.is_available():torch.cuda.empty_cache()
    after={'audit_report':_digest(audit_report_path),
           'preservation_report':_digest(source/'final_report.json'),
           'c0_checkpoint':_digest(ckpt),'original_adapter':stress._weights_hash(original_adapter),
           'training_plan':_digest(training/'experiment.json')}
    if after!=original_fingerprints:
        raise ValueError('GAMER_CAUSAL_PROTECTED_SOURCE_MUTATED')
    result['trajectory_summary']=_trajectory_summary(result)
    result['status']='COMPLETE_READ_ONLY_NO_CAUSAL_DESTRUCTION_CERTIFICATION'
    gamer.atomic_json(out/'final_report.json',result)
    for family,comparison in result['trajectory_summary']['families'].items():
        a=comparison['before'];b=comparison['after']
        print(f'  {family}: C0 -recall {a["production_negative_recall"]:.3f}->{b["production_negative_recall"]:.3f}; '
              f'complete-triples {a["production_complete_triples"]}->{b["production_complete_triples"]}; '
              f'encoder-axis ordering {a["stages"]["enc_gelu_3"]["frozen_train_axis_sign_ordering"]:.3f}'
              f'->{b["stages"]["enc_gelu_3"]["frozen_train_axis_sign_ordering"]:.3f}',flush=True)
    print('GAMER C0 SIGN CONTRAST AUDIT COMPLETE: '+str(out/'final_report.json'),flush=True)
    return result


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--audit',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--steps',default='0,500,1500,3000')
    p.add_argument('--device',default='cuda')
    p.add_argument('--capture-batch',type=int,default=2)
    p.add_argument('--head-batch',type=int,default=8)
    p.add_argument('--seed',type=int,default=47)
    args=p.parse_args(argv)
    if args.capture_batch<1 or args.head_batch<1:
        raise ValueError('GAMER_CAUSAL_INVALID_BATCH')
    return run(args)


if __name__=='__main__':main()
