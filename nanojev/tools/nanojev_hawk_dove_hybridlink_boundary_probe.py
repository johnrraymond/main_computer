#!/usr/bin/env python3
"""Read-only HybridLink boundary probe: teacher format, alignment, recurrence.

Reads saved HybridLink v1 states, never updates parameters and never uses TEST.
The v1 trainer did not save stage-boundary weights. This probe does NOT claim
that final joint-training states represent historical format/alignment stages.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import torch
from tools import nanojev_hawk_dove_hybridlink_sequence as hy
from tools import nanojev_hawk_dove_gaussian_hybrid_agent_v4 as v4
from tools import nanojev_hawk_dove_hybrid_representation_lab as lab

SCHEMA = 'nanojev.hawk-dove.hybridlink-boundary-probe.v1'
CHECKPOINTS = ('best', 'latest')
PATHS = ('teacher_tokens', 'teacher_conditioned_generated', 'autonomous_generated',
         'zero_thoughts', 'repeat_first')


def _identity_and_checkpoints(reference_checkpoint,run_dir,arms,checkpoints):
    root=Path(run_dir).expanduser().resolve()
    config_path=root/'config.json'
    if not config_path.is_file():raise RuntimeError('BOUNDARY_CONFIG_NOT_FOUND')
    ident=json.loads(config_path.read_text(encoding='utf-8'))
    source=Path(reference_checkpoint).expanduser().resolve()
    if ident.get('schema')!=hy.SCHEMA or ident.get('source_sha256')!=hy.file_sha256(source):
        raise RuntimeError('BOUNDARY_SOURCE_IDENTITY_DRIFT')
    paths={'config':config_path,'reference':source}
    parsed={}
    for arm in arms:
        parsed[arm]={}
        for kind in checkpoints:
            path=root/arm/(kind+'.pt')
            if not path.is_file():raise RuntimeError(f'BOUNDARY_CHECKPOINT_MISSING:{arm}/{kind}')
            saved=torch.load(path,map_location='cpu',weights_only=True)
            if (saved.get('schema')!=hy.SCHEMA+'.'+('best' if kind=='best' else 'progress')
                    or saved.get('identity')!=ident or saved.get('arm')!=arm
                    or not isinstance(saved.get('model'),dict)):
                raise RuntimeError(f'BOUNDARY_CHECKPOINT_IDENTITY_DRIFT:{arm}/{kind}')
            paths[f'{arm}/{kind}']=path
            parsed[arm][kind]=saved
    return ident,parsed,paths


def _full_truth_metrics(predictions,split,tolerance):
    return {name:lab.strict_metrics(values,split,tolerance) for name,values in predictions.items()}


def _paired_control(correct,control,split,tolerance):
    """All-mask per-instance paired rescue/regression without metric shortcuts."""
    truth=split['numeric'].detach().cpu()
    good=((correct-truth).abs() <= tolerance*truth.abs()).all(dim=1).tolist()
    alt=((control-truth).abs() <= tolerance*truth.abs()).all(dim=1).tolist()
    grouped={}
    for row,a,b in zip(split['rows'],good,alt):
        k=(row['tier'],row['instance'])
        grouped.setdefault(k,[[],[]])[0].append(bool(a))
        grouped[k][1].append(bool(b))
    result={}
    for tier in hy.blind.TIERS:
        pairs=[(all(vals[0]),all(vals[1])) for k,vals in grouped.items() if k[0]==tier]
        resc=sum(a and not b for a,b in pairs)
        regress=sum(b and not a for a,b in pairs)
        result[tier]={'rescued':resc,'regressed':regress,'net':resc-regress,
                      'accuracy_gain':(resc-regress)/len(pairs),'instances':len(pairs)}
    return result


def _safe_cosine(a,b):
    return torch.nn.functional.cosine_similarity(a.float(),b.float(),dim=-1)


def _vector_alignment(source,teacher):
    err=(source.float()-teacher.float())
    cos=_safe_cosine(source,teacher)
    return {'mean_squared_error':float(err.square().mean()),
            'mean_absolute_difference':float(err.abs().mean()),
            'mean_cosine':float(cos.mean()),
            'mean_prediction_norm':float(source.float().norm(dim=-1).mean()),
            'mean_target_norm':float(teacher.float().norm(dim=-1).mean())}


def _product_metrics(pred,actual):
    err=pred.float()-actual.float()
    return {'normalized_product_mae':float(err.abs().mean()),
            'normalized_product_mse':float(err.square().mean()),
            'per_position_normalized_mae':[float(err[:,j].abs().mean()) for j in range(hy.DEPTH)],
            'normalized_product_R2':float(1.-err.square().sum()/((actual.float()-actual.float().mean(dim=0))**2).sum().clamp_min(1e-12))}


@torch.no_grad()
def diagnose_state(model,split,scale,mode,batch_size,tolerance):
    """Evaluate all paths on precisely the same DEV rows; teacher is diagnostic only."""
    model.eval()
    outputs={key:[] for key in PATHS}
    teacher_next=[];free_next=[];products=[];consumed=[];anticipated=[]
    generated_teacher=[];generated_free=[];teacher_vectors=[]
    for features in split['features'].split(batch_size):
        teacher=model.teacher_tokens(features)
        seed=model.seed(features)
        pre_hidden=model.backbone(torch.cat((seed,teacher),dim=1))
        teacher_answer=model.answer(pre_hidden[:,-1,:])
        consumed.append(model.value_probe(pre_hidden[:,2:]).squeeze(-1).cpu())
        anticipated.append(model.value_probe(pre_hidden[:,1:5]).squeeze(-1).cpu())
        expected=v4.stage_values(features)
        products.append(expected.cpu())
        generated=model.teacher_first_pass(features,mode)
        conditioned,_=model.generated_second_pass(features,generated)
        autonomous,thoughts=model.rollout(features,hy.DEPTH,mode,trace=True)
        normal_thoughts=torch.stack(thoughts,dim=1)
        preds={'teacher_tokens':teacher_answer,'teacher_conditioned_generated':conditioned,
               'autonomous_generated':autonomous,
               'zero_thoughts':model.rollout(features,hy.DEPTH,mode,'zero'),
               'repeat_first':model.rollout(features,hy.DEPTH,mode,'repeat_first')}
        for key,pred in preds.items():outputs[key].append((pred*scale).detach().cpu())
        teacher_vectors.append(teacher.cpu())
        generated_teacher.append(generated.cpu())
        generated_free.append(normal_thoughts.cpu())
    predictions={k:torch.cat(v,dim=0) for k,v in outputs.items()}
    target=torch.cat(teacher_vectors)
    forced=torch.cat(generated_teacher)
    free=torch.cat(generated_free)
    observed=torch.cat(products)
    scores=_full_truth_metrics(predictions,split,tolerance)
    pairs={name:_paired_control(predictions['teacher_tokens'],predictions[name],split,tolerance)
           for name in PATHS if name!='teacher_tokens'}
    token_alignment={'teacher_conditioned':{},'autonomous':{},'generated_vs_generated':{}}
    for j in range(hy.DEPTH):
        k=str(j+1)
        token_alignment['teacher_conditioned'][k]=_vector_alignment(forced[:,j],target[:,j])
        token_alignment['autonomous'][k]=_vector_alignment(free[:,j],target[:,j])
        token_alignment['generated_vs_generated'][k]=_vector_alignment(free[:,j],forced[:,j])
    return {'DEV_paths':scores,
            'format_probe':{'product_after_consuming_teacher':_product_metrics(torch.cat(consumed),observed),
                            'next_product_from_preceding_state':_product_metrics(torch.cat(anticipated),observed),
                            'warning':'The value_probe is trained on product-consumed positions, NOT on preceding positions; its next-product result is diagnostic, not a trained next-token classifier'},
            'alignment_probe':{'per_step':token_alignment,
                               'note':'Teacher-conditioned link inputs come from teacher context. Autonomous inputs are generated recursively; neither path receives oracle X/Y.'},
            'paired_teacher_vs_other_paths':pairs,
            'DEV_trained_depth':hy.DEPTH}


def _stage_availability(saved,kind):
    if kind=='best':
        return {'saved_stage':'joint','saved_step':saved['step'],
                'historical_format_boundary_weights':False,'historical_alignment_boundary_weights':False}
    stage=saved.get('phase')
    return {'saved_stage':stage,'saved_step':saved['index'],
            'historical_format_boundary_weights':stage=='format' and saved['index']>0,
            'historical_alignment_boundary_weights':stage=='alignment' and saved['index']>0,
            'history_contains_metrics_not_stage_weights':True}


def run(args):
    if args.device=='cuda' and not torch.cuda.is_available():raise RuntimeError('BOUNDARY_CUDA_UNAVAILABLE')
    if args.batch_size<=0 or args.cpu_threads<=0 or not 0<args.tolerance<1:
        raise ValueError('BOUNDARY_INVALID_OPTIONS')
    arms=[a.strip() for a in args.arms.split(',')]
    kinds=[k.strip() for k in args.checkpoints.split(',')]
    if (len(set(arms))!=len(arms) or not arms or any(a not in hy.ARMS for a in arms)
        or len(set(kinds))!=len(kinds) or not kinds or any(k not in CHECKPOINTS for k in kinds)):
        raise ValueError('BOUNDARY_INVALID_SELECTION')
    torch.set_num_threads(args.cpu_threads)
    ident,saved,paths=_identity_and_checkpoints(args.reference_checkpoint,args.run_dir,arms,kinds)
    hashes={name:hy.file_sha256(path) for name,path in paths.items()}
    reference,splits,domain,source_cfg=hy.load_source(args.reference_checkpoint,args.device)
    if (source_cfg['source_identity']!=ident.get('source_identity')
            or source_cfg['matrix_hashes']!=ident.get('matrix_hashes')):
        raise RuntimeError('BOUNDARY_DATASET_IDENTITY_DRIFT')
    scale=hy.xy_scale(ident['source_identity']['payoff_bound'])
    dev=splits['dev']
    result={'schema':SCHEMA,'provenance':{'split':'DEV','readonly_models':True,
            'TEST_evaluated':False,'source_sha256':hashes['reference'],
            'input_policy':'Six permitted numeric features only; teacher products injected only for diagnostic teacher-conditioned comparisons',
            'limitation':'End-of-format/alignment weights are not saved separately by the v1 run; do not interpret current-state probes as historical stage measurements',
            'checkpoint_sha256':hashes,'source_schema':hy.SCHEMA},
            'states':{}}
    for arm in arms:
        result['states'][arm]={}
        for kind in kinds:
            ck=saved[arm][kind]
            model=hy.HybridSequence(reference,ident['options']['heads'],ident['options']['layers']).to(args.device)
            model.load_state_dict(ck['model'],strict=True)
            diag=diagnose_state(model,dev,scale,arm,args.batch_size,args.tolerance)
            diag['checkpoint']=_stage_availability(ck,kind)
            result['states'][arm][kind]=diag
            a=diag['DEV_paths']
            aligned=diag['alignment_probe']['per_step']
            teacher_cos=[round(aligned['teacher_conditioned'][str(i)]['mean_cosine'],4) for i in range(1,hy.DEPTH+1)]
            autonomous_cos=[round(aligned['autonomous'][str(i)]['mean_cosine'],4) for i in range(1,hy.DEPTH+1)]
            print(f'BOUNDARY_STATE arm={arm} checkpoint={kind} saved_stage={diag["checkpoint"]["saved_stage"]} '
                  f'teacher={a["teacher_tokens"]["min_tier_joint5"]:.6f} '
                  f'teacher_generated={a["teacher_conditioned_generated"]["min_tier_joint5"]:.6f} '
                  f'autonomous={a["autonomous_generated"]["min_tier_joint5"]:.6f} '
                  f'consumed_product_MAE={diag["format_probe"]["product_after_consuming_teacher"]["normalized_product_mae"]:.6g} '
                  f'next_product_MAE={diag["format_probe"]["next_product_from_preceding_state"]["normalized_product_mae"]:.6g}',flush=True)
            print(f'BOUNDARY_TOKENS arm={arm} checkpoint={kind} teacher_cos={teacher_cos} autonomous_cos={autonomous_cos}',flush=True)
    for name,path in paths.items():
        if hy.file_sha256(path)!=hashes[name]:raise RuntimeError('BOUNDARY_INPUT_MUTATED:'+name)
    out=Path(args.output).expanduser().resolve() if args.output else Path(args.run_dir).expanduser().resolve()/'boundary_probe_v1'
    if out.exists() and any(out.iterdir()) and not args.overwrite_report:
        raise FileExistsError('BOUNDARY_OUTPUT_EXISTS')
    out.mkdir(parents=True,exist_ok=True)
    hy.gauss._atomic_json(out/'report.json',result)
    print('BOUNDARY_PROBE_READ_ONLY=TRUE SOURCE_IMMUTABLE=TRUE TEST_UNTOUCHED=TRUE',flush=True)
    print('BOUNDARY_PROBE_REPORT='+str(out/'report.json'),flush=True)
    return result


def parser():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--reference-checkpoint',type=Path,required=True)
    p.add_argument('--run-dir',type=Path,required=True)
    p.add_argument('--output',type=Path)
    p.add_argument('--device',choices=('cpu','cuda'),default='cuda')
    p.add_argument('--arms',default='recurrent,static')
    p.add_argument('--checkpoints',default='best,latest')
    p.add_argument('--batch-size',type=int,default=256)
    p.add_argument('--cpu-threads',type=int,default=4)
    p.add_argument('--tolerance',type=float,default=.05)
    p.add_argument('--overwrite-report',action='store_true')
    return p


def main(argv=None):return run(parser().parse_args(argv))

if __name__=='__main__':main()
