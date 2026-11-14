#!/usr/bin/env python3
"""Independent accuracy authority for the loss-only binary consensus trainer.

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

from nanojev_binary_consensus_runtime import deps, load_bank, load_model, frozen_evidence, evidence_on_cuda

DEFAULT_BANK=Path(r'C:\Users\subsi\NanoJev\runs\binary_consensus_bank_v1')
DEFAULT_TRAINING=Path(r'C:\Users\subsi\NanoJev\runs\three_backbone_clef_binary_nlrom_continuous_v1')


class Logger:
    def emit(self,event,**fields):
        if event in ('clef_sized_backbone_load_start','clef_sized_backbone_load_complete','binary_model_ready'):
            print(json.dumps({'event':event,**fields},sort_keys=True),flush=True)


def run(args):
    if args.split=='train':
        raise RuntimeError('BINARY_PROBE_FORBIDS_TRAIN_SPLIT')
    bank=load_bank(args.bank_dir,split=args.split,max_groups=args.limit_groups)
    if args.champion_only:
        checkpoint=None
    else:
        state=json.loads((args.training_dir/'state.json').read_text(encoding='utf-8'))
        if state['bankManifestSha256']!=bank['manifest_sha256']:
            raise RuntimeError('BINARY_PROBE_BANK_IDENTITY_MISMATCH')
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
                counts['forward_correct' if variant=='natural_rom' else 'reverse_correct']+=int(correct)
                counts['forward_total' if variant=='natural_rom' else 'reverse_total']+=1
                if variant=='natural_rom':
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
         'weights_modified':False}
    print(json.dumps(out,sort_keys=True),flush=True)


def parse(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--bank-dir',type=Path,default=DEFAULT_BANK)
    p.add_argument('--training-dir',type=Path,default=DEFAULT_TRAINING)
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
    p.add_argument('--max-prompt-tokens',type=int,default=512)
    p.add_argument('--max-answer-tokens',type=int,default=64)
    p.add_argument('--prompt-evidence-tokens',type=int,default=6)
    p.add_argument('--answer-evidence-tokens',type=int,default=4)
    p.add_argument('--path-batch',type=int,default=4)
    p.add_argument('--tinystories-path-batch',type=int,default=4)
    return p.parse_args(argv)

if __name__=='__main__':
    try:
        run(parse())
    except Exception as e:
        print(json.dumps({'event':'binary_probe_error','error':str(e),'traceback':traceback.format_exc()}),file=sys.stderr)
        raise SystemExit(1)
