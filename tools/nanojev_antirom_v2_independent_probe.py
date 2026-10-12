#!/usr/bin/env python3
"""Independent, read-only AntiROM v2 DEV/OOD accuracy and CE probe.

Never modifies model weights, writes training state, or reads training examples
as evaluation data. Validates checkpoint, sealed stream and renderer provenance.
"""
from __future__ import annotations
import argparse
from collections import Counter,defaultdict
import json
from pathlib import Path
import sys
import traceback

from nanojev_antirom_compression import LEVELS, representation, renderer_fingerprint
from nanojev_antirom_v2_stream_bank import DEFAULT_OUTPUT as BANK_OUTPUT, build_sample,load
from nanojev_antirom_v2_fresh_train import SCHEMA, DEFAULT_OUTPUT as TRAIN_OUTPUT
from nanojev_arithmetic_runtime import DEFAULT_BANK, deps,load_bank,load_model,frozen_evidence,evidence_on_cuda
from nanojev_arithmetic_task_bank import LABELS
from nanojev_arithmetic_continuous_train import atomic_json
from nanojev_rom_arithmetic_language import verify_prompt_visibility


def parse(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--bank-dir',type=Path,default=BANK_OUTPUT)
    p.add_argument('--legacy-bank-dir',type=Path,default=DEFAULT_BANK)
    p.add_argument('--output-root',type=Path,default=TRAIN_OUTPUT)
    p.add_argument('--level',choices=LEVELS,required=True)
    p.add_argument('--split',choices=('dev','ood'),default='dev')
    p.add_argument('--step',type=int,default=3000,help='0 = original pinned champion')
    p.add_argument('--results-file',type=Path,required=True)
    p.add_argument('--seed',type=int,default=20261012)
    p.add_argument('--head-lr',type=float,default=1e-4)
    p.add_argument('--weight-decay',type=float,default=.01)
    p.add_argument('--max-prompt-tokens',type=int,default=512)
    p.add_argument('--max-answer-tokens',type=int,default=64)
    p.add_argument('--prompt-evidence-tokens',type=int,default=6)
    p.add_argument('--answer-evidence-tokens',type=int,default=4)
    p.add_argument('--path-batch',type=int,default=4)
    p.add_argument('--tinystories-path-batch',type=int,default=4)
    p.add_argument('--local-files-only',action='store_true',default=True)
    p.add_argument('--allow-model-download',action='store_false',dest='local_files_only')
    p.add_argument('--dry-run',action='store_true')
    a=p.parse_args(argv)
    if a.step<0:p.error('ANTIROM_V2_NEGATIVE_EVAL_STEP')
    return a


def metrics(records):
    if not records:raise RuntimeError('ANTIROM_V2_EMPTY_PROBE_RESULTS')
    return {'examples':len(records),
            'accuracy':sum(int(r['correct']) for r in records)/len(records),
            'mean_cross_entropy':sum(r['cross_entropy'] for r in records)/len(records)}


def checkpoint_identity(args,bank):
    if args.step==0:return None
    root=(Path(args.output_root).expanduser().resolve()/args.level)
    experiment=json.loads((root/'experiment.json').read_text(encoding='utf-8'))
    config=experiment['config']
    if (experiment.get('schema')!=SCHEMA or
        experiment.get('mode')!='fresh-unique-training-no-reexposure' or
        experiment.get('bankManifestSha256')!=bank['bank_sha256'] or
        config.get('bank_sha256')!=bank['bank_sha256'] or
        config.get('legacy_bank_sha256')!=bank['legacy_bank_sha256'] or
        config.get('level')!=args.level or config.get('representation')!=representation(args.level) or
        config.get('renderer_sha256')!=renderer_fingerprint(args.level) or
        config.get('seed')!=args.seed or config.get('head_lr')!=args.head_lr or
        config.get('weight_decay')!=args.weight_decay or
        config.get('evidence')!={k:int(getattr(args,k)) for k in (
            'max_prompt_tokens','max_answer_tokens','prompt_evidence_tokens',
            'answer_evidence_tokens','path_batch','tinystories_path_batch')}):
        raise RuntimeError('ANTIROM_V2_PROBE_TRAINING_IDENTITY_MISMATCH')
    if (args.step>config['max_steps'] or args.step%config['checkpoint_steps']!=0
        and args.step!=config['max_steps']):
        raise RuntimeError('ANTIROM_V2_UNSUPPORTED_CHECKPOINT_STEP')
    path=root/'checkpoints'/f'step-{args.step:09d}'
    meta=json.loads((path/'meta.json').read_text(encoding='utf-8'))
    if (meta.get('config')!=config or meta.get('bankManifestSha256')!=bank['bank_sha256']
        or meta.get('cursor',{}).get('global_step')!=args.step
        or meta.get('cursor',{}).get('examples_seen')!=args.step*config['grad_accumulation']):
        raise RuntimeError('ANTIROM_V2_PROBE_CHECKPOINT_DRIFT')
    if not (path/'head.safetensors').is_file():raise RuntimeError('ANTIROM_V2_PROBE_HEAD_MISSING')
    return path.resolve(strict=True)


class Logger:
    def emit(self,event,**fields):
        if event in ('clef_sized_backbone_load_start','clef_sized_backbone_load_complete','binary_model_ready'):
            print(json.dumps({'event':event,**fields},sort_keys=True),flush=True)


def evaluate(args):
    bank=load(bank_dir=args.bank_dir,legacy_bank_dir=args.legacy_bank_dir,split='ood')
    if args.split=='dev':
        old=load_bank(args.legacy_bank_dir,split='dev')
        source=old['source']
        from nanojev_antirom_compression import convert_presentation
        cases=[(f'{gid}:{variant}',convert_presentation(sample,g['target'],variant,args.level),
                g['label']) for gid,g in old['groups']
                for variant,sample in sorted(g['variants'].items())]
        eval_source_sha=old['manifest_sha256']
    else:
        cases=[(r['id'],build_sample(r,args.level),r['label']) for r in bank['rows']]
        eval_source_sha=bank['bank_sha256']
        source=bank['source']
    if source!=bank['source']:
        raise RuntimeError('ANTIROM_V2_DEV_SOURCE_MODEL_DRIFT')
    cp=checkpoint_identity(args,bank)
    if args.dry_run:
        print(json.dumps({'event':'antirom_v2_probe_plan','split':args.split,'level':args.level,
              'checkpoint':str(cp) if cp else 'pinned-cycle-136-champion',
              'examples':len(cases),'training_bank_sha256':bank['bank_sha256'],
              'eval_sha256':eval_source_sha,'no_training':True},sort_keys=True))
        return
    if args.results_file.exists():
        raise RuntimeError('ANTIROM_V2_PROBE_RESULT_EXISTS: '+str(args.results_file))
    import torch
    torch.manual_seed(args.seed)
    torch.cuda.manual_seed_all(args.seed)
    champion,_,objective=deps()
    head,bundles,optimizer,_=load_model(torch=torch,args=args,source=source,
                                       logger=Logger(),checkpoint=cp,load_optimizer=False)
    del optimizer
    head.eval()
    visible=verify_prompt_visibility(samples=[x[1] for x in cases],bundles=bundles,args=args)
    print(json.dumps({'event':'antirom_v2_probe_visibility','level':args.level,
       'split':args.split,'backbones':visible,'presentations':len(cases)}),flush=True)
    all_records=[];by_class=defaultdict(list);by_prediction=Counter();predictions={}
    with torch.inference_mode():
        for i,(case_id,sample,gold) in enumerate(cases,1):
            question=champion.base.deserialize_question(sample,objective)
            evidence=frozen_evidence(torch=torch,champion=champion,bundles=bundles,
                                     question=question,args=args)
            logits=head(evidence_on_cuda(champion,evidence))
            if logits.numel()!=3:raise RuntimeError('ANTIROM_V2_EXPECTED_THREE_LOGITS')
            label=LABELS[question.gold_index]
            if label!=gold:raise RuntimeError('ANTIROM_V2_PROBE_GOLD_MISMATCH')
            loss=torch.nn.functional.cross_entropy(logits.float().unsqueeze(0),
                    torch.tensor([question.gold_index],dtype=torch.long,device=logits.device))
            predicted=LABELS[int(logits.float().argmax().item())]
            record={'gold':label,'predicted':predicted,'correct':predicted==label,
                    'cross_entropy':float(loss.item())}
            all_records.append(record);by_class[label].append(record)
            by_prediction[predicted]+=1
            predictions[case_id]=record
            if i%100==0:print(json.dumps({'event':'antirom_v2_probe_progress','level':args.level,
                              'split':args.split,'done':i,'total':len(cases)}),flush=True)
    result={'schema':'nanojev.antirom-fresh-independent-probe.v2','level':args.level,
            'representation':representation(args.level),'split':args.split,
            'checkpoint':str(cp) if cp else 'pinned-cycle-136-champion','step':args.step,
            'training_bank_sha256':bank['bank_sha256'],'eval_sha256':eval_source_sha,
            'overall':metrics(all_records),
            'by_class':{k:metrics(v) for k,v in sorted(by_class.items())},
            'predicted_class_counts':dict(by_prediction),'predictions':predictions,
            'weights_modified':False}
    args.results_file.parent.mkdir(parents=True,exist_ok=True)
    atomic_json(args.results_file,result)
    print(json.dumps({'event':'antirom_v2_probe_complete','level':args.level,'split':args.split,
                      'step':args.step,'overall':result['overall'],
                      'predicted_class_counts':dict(by_prediction)}),flush=True)


if __name__=='__main__':
    try:evaluate(parse())
    except Exception as e:
        print(json.dumps({'event':'antirom_v2_probe_error','error':str(e),
                          'traceback':traceback.format_exc()},sort_keys=True),file=sys.stderr)
        raise SystemExit(1)
