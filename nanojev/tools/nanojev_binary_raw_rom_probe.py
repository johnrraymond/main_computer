#!/usr/bin/env python3
"""Independent accuracy probe for original structured ROM SAME/DIFFERENT.

Read-only DEV/PREDEV/TEST evaluation. No training, optimizer updates, or
question generation. Only this script computes correctness or accuracy.
"""
from __future__ import annotations
import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path
import sys
import traceback

from nanojev_binary_consensus_runtime import deps, load_model, frozen_evidence, evidence_on_cuda
from nanojev_binary_raw_rom_bank import load_raw_bank as load_bank

DEFAULT_BANK=Path(r'C:\Users\subsi\NanoJev\runs\binary_consensus_raw_rom_bank_v1')
DEFAULT_TRAINING=Path(r'C:\Users\subsi\NanoJev\runs\three_backbone_clef_binary_raw_rom_residual_v1')


class Logger:
    def emit(self,event,**fields):
        if event in ('clef_sized_backbone_load_start','clef_sized_backbone_load_complete','binary_model_ready','rom333_token_visibility_verified'):
            print(json.dumps({'event':event,**fields},sort_keys=True),flush=True)


def run(args):
    if args.split=='train':
        raise RuntimeError('BINARY_PROBE_FORBIDS_TRAIN_SPLIT')
    if args.rom333:
        from nanojev_binary_rom333_bank import load_rom333_bank
        bank=load_rom333_bank(args.bank_dir,split=args.split,max_groups=args.limit_groups)
        forward='rom333'
    else:
        bank=load_bank(args.bank_dir,split=args.split,max_groups=args.limit_groups)
        forward='raw_rom'
    if args.champion_only:
        checkpoint=None
    else:
        state=json.loads((args.training_dir/'state.json').read_text(encoding='utf-8'))
        if state['bankManifestSha256']!=bank['manifest_sha256']:
            raise RuntimeError('BINARY_PROBE_BANK_IDENTITY_MISMATCH')
        trained_prompt_budget=state.get('config',{}).get('evidence',{}).get('max_prompt_tokens')
        if trained_prompt_budget!=args.max_prompt_tokens:
            raise RuntimeError('BINARY_PROBE_PROMPT_BUDGET_MISMATCH')
        if state.get('config',{}).get('variant')!=forward+'-both-orientations':
            raise RuntimeError('BINARY_PROBE_REPRESENTATION_MISMATCH')
        checkpoint=Path(state['checkpoint']).resolve(strict=True)
    if args.dry_run:
        print(json.dumps({'ok':True,'event':'binary_probe_plan','groups':bank['count'],
              'split':args.split,'presentations':bank['count']*2,
              'checkpoint':str(checkpoint) if checkpoint else 'champion-cutover',
              'no_training':True},sort_keys=True))
        return
    import torch
    champion,_,objective=deps()
    head,bundles,optimizer,_=load_model(torch=torch,args=args,source=bank['source'],
             logger=Logger(),checkpoint=checkpoint,load_optimizer=False)
    del optimizer
    if args.rom333:
        from nanojev_binary_rom333_bank import audit_token_visibility
        audit_token_visibility(bank=bank,bundles=bundles,champion=champion,
                               objective=objective,args=args,logger=Logger())
    head.eval()
    counts=Counter()
    pair_by_parent=defaultdict(dict)
    gold_by_parent={}
    with torch.inference_mode():
        for i,(group_id,g) in enumerate(bank['groups'],1):
            for variant,serialized in sorted(g['variants'].items()):
                question=champion.base.deserialize_question(serialized,objective)
                rows=frozen_evidence(torch=torch,champion=champion,bundles=bundles,question=question,args=args)
                logits=head(evidence_on_cuda(champion,rows))
                cross_entropy=torch.nn.functional.cross_entropy(logits.float().unsqueeze(0),
                    torch.tensor([question.gold_index],dtype=torch.long,device=logits.device))
                pred=int(logits.float().argmax().item())
                correct=(pred==question.gold_index)
                counts['examples']+=1
                counts['correct']+=int(correct)
                counts['loss_sum']+=float(cross_entropy.item())
                counts['forward_correct' if variant==forward else 'reverse_correct']+=int(correct)
                counts['forward_total' if variant==forward else 'reverse_total']+=1
                if variant==forward:
                    pair_by_parent[g['parent']][g['pair']]=str(question.candidates[pred].candidate_id).lower()
                    gold_by_parent[g['parent']]=g['topology']
            if args.progress_groups and i%args.progress_groups==0:
                print(json.dumps({'event':'binary_probe_progress','groups_done':i,'groups_total':bank['count']}),flush=True)
    complete=0
    topology_correct=0
    ambiguous=0
    for parent,pairs in pair_by_parent.items():
        if set(pairs)=={'ab','ac','bc'}:
            complete+=1
            result=champion.compose_consensus_relations(pairs['ab'],pairs['ac'],pairs['bc'])
            topology_correct+=int(result==gold_by_parent[parent])
            ambiguous+=int(result=='ambiguous')
    out={'event':'binary_probe_result','split':args.split,'checkpoint':str(checkpoint) if checkpoint else 'champion-cutover',
         'binary_questions':int(counts['examples']),
         'binary_accuracy':counts['correct']/counts['examples'],
         'binary_cross_entropy':counts['loss_sum']/counts['examples'],
         'forward_accuracy':counts['forward_correct']/counts['forward_total'],
         'reverse_accuracy':counts['reverse_correct']/counts['reverse_total'],
         'complete_consensus_triples':complete,
         'topology_accuracy':topology_correct/complete if complete else None,
         'ambiguous_rate':ambiguous/complete if complete else None,
         'weights_modified':False,'representation':forward}
    print(json.dumps(out,sort_keys=True),flush=True)


def parse(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--bank-dir',type=Path,default=None)
    p.add_argument('--rom333',action='store_true',
                   help='probe the English-like ROM3.3.3 held-out bank')
    p.add_argument('--arithmetic-control',action='store_true',
                   help='use the interleaved arithmetic-control training run (probe remains ROM only)')
    p.add_argument('--training-dir',type=Path,default=None)
    p.add_argument('--split',choices=('dev','predev','test'),default='predev')
    p.add_argument('--champion-only',action='store_true',help='probe original cycle 136 before training')
    p.add_argument('--limit-groups',type=int,default=0)
    p.add_argument('--progress-groups',type=int,default=50)
    p.add_argument('--dry-run',action='store_true')
    p.add_argument('--local-files-only',action='store_true',default=True)
    p.add_argument('--allow-model-download',action='store_false',dest='local_files_only')
    p.add_argument('--seed',type=int,default=20261010)
    p.add_argument('--head-lr',type=float,default=1e-4)
    p.add_argument('--clef-head-lr',type=float,default=1e-6)
    p.add_argument('--weight-decay',type=float,default=.01)
    p.add_argument('--max-prompt-tokens',type=int,default=None)
    p.add_argument('--max-answer-tokens',type=int,default=64)
    p.add_argument('--prompt-evidence-tokens',type=int,default=6)
    p.add_argument('--answer-evidence-tokens',type=int,default=4)
    p.add_argument('--path-batch',type=int,default=4)
    p.add_argument('--tinystories-path-batch',type=int,default=4)
    args=p.parse_args(argv)
    if args.max_prompt_tokens is None:
        args.max_prompt_tokens=768 if args.rom333 else 512
    if args.bank_dir is None:
        if args.rom333:
            from nanojev_binary_rom333_bank import DEFAULT_OUTPUT as ROM333_BANK
            args.bank_dir=ROM333_BANK
        else:
            args.bank_dir=DEFAULT_BANK
    if args.training_dir is None:
        if args.rom333:
            from nanojev_binary_raw_rom_continuous_train import DEFAULT_ROM333_OUTPUT,DEFAULT_ROM333_CONTROL_OUTPUT
            args.training_dir=DEFAULT_ROM333_CONTROL_OUTPUT if args.arithmetic_control else DEFAULT_ROM333_OUTPUT
        else:
            if args.arithmetic_control:
                from nanojev_binary_raw_rom_continuous_train import DEFAULT_CONTROL_OUTPUT
                args.training_dir=DEFAULT_CONTROL_OUTPUT
            else:
                args.training_dir=DEFAULT_TRAINING
    return args

if __name__=='__main__':
    try:
        run(parse())
    except Exception as e:
        print(json.dumps({'event':'binary_probe_error','error':str(e),'traceback':traceback.format_exc()}),file=sys.stderr)
        raise SystemExit(1)
