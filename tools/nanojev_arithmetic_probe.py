#!/usr/bin/env python3
"""Independent LESS/EQUAL/MORE accuracy authority for arithmetic CLEF control.

Never evaluates training data; never updates any weights. Can compare a saved
champion baseline directly against later checkpoints on the exact same cases.
"""
from __future__ import annotations
import argparse
from collections import Counter,defaultdict
import json
from pathlib import Path
import sys
import traceback

from nanojev_arithmetic_runtime import (DEFAULT_BANK, DEFAULT_OUTPUT, SCHEMA, deps, load_bank,
                                         load_model, frozen_evidence, evidence_on_cuda)
from nanojev_arithmetic_task_bank import LABELS
from nanojev_antirom_compression import (
    LEVELS as ANTIROM_LEVELS, representation as antirom_representation,
    output_for as antirom_output_for, convert_presentation as antirom_convert_presentation,
    renderer_fingerprint as antirom_renderer_fingerprint,
)
from nanojev_rom_arithmetic_language import (
    REPRESENTATION as ROM_REPRESENTATION, ROM_OUTPUT,
    convert_presentation, representation_fingerprint, verify_prompt_visibility,
)

class Logger:
    def emit(self,event,**fields):
        if event in ('clef_sized_backbone_load_start','clef_sized_backbone_load_complete','binary_model_ready'):
            print(json.dumps({'event':event,**fields},sort_keys=True),flush=True)


def summarize(counter):
    return {'examples':int(counter['n']),
            'accuracy':counter['correct']/counter['n'] if counter['n'] else None,
            'mean_cross_entropy':counter['loss_sum']/counter['n'] if counter['n'] else None}


def compare_predictions(current,baseline):
    if current.keys()!=baseline.keys():
        raise RuntimeError('ARITH_PROBE_COMPARE_CASES_DIFFER')
    improved=regressed=unchanged=0
    for k,cur in current.items():
        old=baseline[k]
        if cur['gold']!=old['gold']:
            raise RuntimeError('ARITH_PROBE_GOLD_DRIFT')
        if cur['correct'] and not old['correct']:
            improved+=1
        elif not cur['correct'] and old['correct']:
            regressed+=1
        else:
            unchanged+=1
    return {'improved_cases':improved,'regressed_cases':regressed,
            'unchanged_cases':unchanged,'net_gain_cases':improved-regressed,
            'net_gain_accuracy_points':100*(improved-regressed)/len(current)}


def run(args):
    if args.split=='train':
        raise RuntimeError('ARITH_PROBE_FORBIDS_TRAIN_SPLIT')
    bank=load_bank(args.bank_dir,split=args.split,max_groups=args.limit_groups)
    representation = (antirom_representation(args.antirom_level) if args.antirom_level
                      else ROM_REPRESENTATION if args.rom_language else 'english-arithmetic-original-v1')
    if args.champion_only:
        checkpoint=None
    else:
        state=json.loads((args.training_dir/'state.json').read_text(encoding='utf-8'))
        if (state['schema']!=SCHEMA or state['bankManifestSha256']!=bank['manifest_sha256']):
            raise RuntimeError('ARITH_PROBE_TRAINING_STATE_MISMATCH')
        checkpoint=Path(state['checkpoint']).resolve(strict=True)
        if args.antirom_level:
            config=state.get('config', {})
            if (config.get('representation')!=representation
                or config.get('antirom_level')!=args.antirom_level
                or config.get('renderer_sha256')!=antirom_renderer_fingerprint(args.antirom_level)
                or config.get('evidence')!={k:int(getattr(args,k)) for k in (
                    'max_prompt_tokens','max_answer_tokens','prompt_evidence_tokens',
                    'answer_evidence_tokens','path_batch','tinystories_path_batch')}):
                raise RuntimeError('ANTIROM_PROBE_REPRESENTATION_OR_EVIDENCE_MISMATCH')
            experiment=json.loads((args.training_dir/'experiment.json').read_text(encoding='utf-8'))
            if (experiment.get('mode')!='fixed32-antirom-compression-sweep-v1'
                or experiment.get('config')!=config):
                raise RuntimeError('ANTIROM_PROBE_TRAINING_MODE_MISMATCH')
        elif args.rom_language:
            config=state.get('config', {})
            if (config.get('representation') != representation
                or config.get('renderer_sha256') != representation_fingerprint()
                or config.get('evidence') != {k:int(getattr(args,k)) for k in (
                    'max_prompt_tokens','max_answer_tokens','prompt_evidence_tokens',
                    'answer_evidence_tokens','path_batch','tinystories_path_batch')}):
                raise RuntimeError('ROM_ARITH_PROBE_REPRESENTATION_OR_EVIDENCE_MISMATCH')
            experiment=json.loads((args.training_dir/'experiment.json').read_text(encoding='utf-8'))
            if experiment.get('mode') != 'fixed32-rom-arithmetic-language-control':
                raise RuntimeError('ROM_ARITH_PROBE_TRAINING_MODE_MISMATCH')
        elif state.get('config', {}).get('representation') == ROM_REPRESENTATION:
            raise RuntimeError('ROM_ARITH_PROBE_REQUIRES_ROM_LANGUAGE_FLAG')
        elif str(state.get('config', {}).get('representation','')).startswith('antirom-arithmetic-compression-v1:'):
            raise RuntimeError('ANTIROM_PROBE_REQUIRES_CORRECT_LEVEL')
    if args.dry_run:
        print(json.dumps({'ok':True,'event':'arithmetic_probe_plan','split':args.split,
              'groups':bank['count'],'presentations':bank['count']*2,'accuracy_computed':False,
              'checkpoint':str(checkpoint) if checkpoint else 'pinned-cycle-136-champion',
              'no_training':True,'representation':representation},sort_keys=True))
        return
    import torch
    champion,_,objective=deps()
    head,bundles,optimizer,_=load_model(torch=torch,args=args,source=bank['source'],
            logger=Logger(),checkpoint=checkpoint,load_optimizer=False)
    del optimizer
    head.eval()
    if args.rom_language or args.antirom_level:
        transformed=[(antirom_convert_presentation(sample,g['target'],variant,args.antirom_level)
                      if args.antirom_level else convert_presentation(sample,g['target'],variant))
                     for _,g in bank['groups'] for variant,sample in sorted(g['variants'].items())]
        visibility=verify_prompt_visibility(samples=transformed,bundles=bundles,args=args)
        print(json.dumps({'event':'rom_arithmetic_visibility_verified',
                          'backbones':visibility,'presentations':len(transformed)},sort_keys=True),flush=True)
    total=Counter()
    by_label=defaultdict(Counter)
    by_distance=defaultdict(Counter)
    by_target=defaultdict(Counter)
    by_orientation=defaultdict(Counter)
    by_carry=defaultdict(Counter)
    predictions={}
    forward={};reverse={}
    with torch.inference_mode():
        for i,(group_id,g) in enumerate(bank['groups'],1):
            t=g['target']
            a,b=map(int,(t['a'],t['b']))
            carry=(a%10)+(b%10)>=10
            for variant,sample in sorted(g['variants'].items()):
                if args.antirom_level:
                    sample=antirom_convert_presentation(sample,t,variant,args.antirom_level)
                elif args.rom_language:
                    sample=convert_presentation(sample,t,variant)
                question=champion.base.deserialize_question(sample,objective)
                rows=frozen_evidence(torch=torch,champion=champion,bundles=bundles,question=question,args=args)
                logits=head(evidence_on_cuda(champion,rows))
                if logits.numel()!=3:
                    raise RuntimeError('ARITH_PROBE_EXPECTED_THREE_LOGITS')
                cross_entropy=torch.nn.functional.cross_entropy(logits.float().unsqueeze(0),
                       torch.tensor([question.gold_index],dtype=torch.long,device=logits.device))
                predicted=LABELS[int(logits.float().argmax().item())]
                label=LABELS[question.gold_index]
                correct=(predicted==label)
                case_key=f'{group_id}:{variant}'
                predictions[case_key]={'gold':label,'predicted':predicted,'correct':correct}
                (forward if variant=='forward' else reverse)[group_id]=predicted
                for c in (total,by_label[label],by_distance[t['distanceBand'] if label!='EQUAL' else 'exact'],
                          by_target[t['targetBand']],by_orientation[variant],
                          by_carry['carry' if carry else 'no_carry']):
                    c['n']+=1
                    c['correct']+=int(correct)
                    c['loss_sum']+=float(cross_entropy.item())
            if args.progress_groups and i%args.progress_groups==0:
                print(json.dumps({'event':'arithmetic_probe_progress','groups_done':i,
                                  'groups_total':bank['count']}),flush=True)
    compared=sum(forward[g]==reverse[g] for g in forward)
    result={'event':'arithmetic_probe_result','split':args.split,
            'representation':representation,
            'checkpoint':str(checkpoint) if checkpoint else 'pinned-cycle-136-champion',
            'overall':summarize(total),
            'by_class':{k:summarize(v) for k,v in sorted(by_label.items())},
            'by_distance':{k:summarize(v) for k,v in sorted(by_distance.items())},
            'by_target_scale':{k:summarize(v) for k,v in sorted(by_target.items())},
            'by_orientation':{k:summarize(v) for k,v in sorted(by_orientation.items())},
            'by_carry':{k:summarize(v) for k,v in sorted(by_carry.items())},
            'operand_swap_consistency':compared/len(forward),
            'chance_accuracy':1/3,'weights_modified':False}
    if args.compare_to:
        baseline=json.loads(args.compare_to.read_text(encoding='utf-8'))
        if (baseline['bankManifestSha256']!=bank['manifest_sha256'] or baseline['split']!=args.split
                or baseline.get('representation','english-arithmetic-original-v1')!=representation):
            raise RuntimeError('ARITH_PROBE_BASELINE_IDENTITY_MISMATCH')
        result['vs_baseline']=compare_predictions(predictions,baseline['predictions'])
    if args.results_file:
        args.results_file.parent.mkdir(parents=True,exist_ok=True)
        if args.results_file.exists():
            raise RuntimeError('ARITH_PROBE_RESULTS_FILE_EXISTS')
        args.results_file.write_text(json.dumps({'schema':'nanojev.arithmetic-probe-results.v1',
                    'split':args.split,'bankManifestSha256':bank['manifest_sha256'],
                    'representation':representation,
                    'checkpoint':result['checkpoint'],'result':result,'predictions':predictions},
                    sort_keys=True,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(result,sort_keys=True),flush=True)


def parse(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--bank-dir',type=Path,default=DEFAULT_BANK)
    p.add_argument('--training-dir',type=Path,default=None)
    representation_flags=p.add_mutually_exclusive_group()
    representation_flags.add_argument('--rom-language',action='store_true',
                   help='evaluate the same sealed arithmetic bank translated into ROM')
    representation_flags.add_argument('--antirom-level',choices=ANTIROM_LEVELS,
                   help='evaluate matching AntiROM compression presentation')
    p.add_argument('--split',choices=('dev','predev'),default='predev')
    p.add_argument('--champion-only',action='store_true')
    p.add_argument('--limit-groups',type=int,default=0)
    p.add_argument('--progress-groups',type=int,default=50)
    p.add_argument('--dry-run',action='store_true')
    p.add_argument('--results-file',type=Path,default=None)
    p.add_argument('--compare-to',type=Path,default=None)
    p.add_argument('--local-files-only',action='store_true',default=True)
    p.add_argument('--allow-model-download',action='store_false',dest='local_files_only')
    p.add_argument('--seed',type=int,default=20261010)
    p.add_argument('--head-lr',type=float,default=1e-4)
    p.add_argument('--clef-head-lr',type=float,default=1e-6)
    p.add_argument('--weight-decay',type=float,default=.01)
    p.add_argument('--max-prompt-tokens',type=int,default=512)
    p.add_argument('--max-answer-tokens',type=int,default=64)
    p.add_argument('--prompt-evidence-tokens',type=int,default=6)
    p.add_argument('--answer-evidence-tokens',type=int,default=4)
    p.add_argument('--path-batch',type=int,default=4)
    p.add_argument('--tinystories-path-batch',type=int,default=4)
    args=p.parse_args(argv)
    if args.training_dir is None:
        args.training_dir=(antirom_output_for(args.antirom_level) if args.antirom_level
                           else ROM_OUTPUT if args.rom_language else DEFAULT_OUTPUT)
    return args

if __name__=='__main__':
    try:
        run(parse())
    except Exception as exc:
        print(json.dumps({'event':'arithmetic_probe_error','error':str(exc),'traceback':traceback.format_exc()},sort_keys=True),file=sys.stderr)
        raise SystemExit(1)
