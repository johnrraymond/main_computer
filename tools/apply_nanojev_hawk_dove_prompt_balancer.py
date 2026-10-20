"""Guarded, source-preserving upgrade of an existing Hawk-Dove trainer.

The export archive predates observed cache-progress instrumentation. Do NOT
replace the live trainer with the archived copy; apply only these selected
changes, preserving all unrelated optimizations and local modifications.

Usage from main_computer repo root:
  python tools/apply_nanojev_hawk_dove_prompt_balancer.py --check
  python tools/apply_nanojev_hawk_dove_prompt_balancer.py --apply
"""
from __future__ import annotations

import argparse
import ast
import hashlib
from pathlib import Path


def replace_once(source: str, old: str, new: str, name: str) -> str:
    occurrences = source.count(old)
    if occurrences != 1:
        raise ValueError(f'INCOMPATIBLE_TRAINER_{name}: matched {occurrences} times; unchanged')
    return source.replace(old, new, 1)


def transformed(source: str, source_sha: str) -> str:
    if '--adaptive-prompts' in source or "'prompt_balance'" in source:
        raise ValueError('ADAPTIVE_PROMPTS_ALREADY_INSTALLED: unchanged')
    s = source
    s = replace_once(s, "MASK_POLICY_SCHEMA='nanojev.hawk-dove.million-counts.mask-weights.v1'",
        "MASK_POLICY_SCHEMA='nanojev.hawk-dove.million-counts.mask-weights.v1'\n"
        "# Pre-upgrade trainer bytes, permitted as optimizer lineage ancestor.\n"
        f"ADAPTIVE_BASE_TRAINER_SHA256='{source_sha}'", 'SCHEMA')
    s = replace_once(s,"MASK_DIAGNOSTICS_TRAINER_SHA256,expected['trainer_sha256']",
        "MASK_DIAGNOSTICS_TRAINER_SHA256,ADAPTIVE_BASE_TRAINER_SHA256,expected['trainer_sha256']", 'LINEAGE')
    s = replace_once(s,
        'def choose_rows(db:Path,focus:str,step:int,seed:int,views:int,mask_weights_by_tier=None):',
        'def choose_rows(db:Path,focus:str,step:int,seed:int,views:int,mask_weights_by_tier=None,adaptive_sampler=None):', 'SAMPLER_SIGNATURE')
    s = replace_once(s,"        if not weights or all(float(v)==1 for v in weights.values()):",
        "        if tier==focus and adaptive_sampler is not None:\n"
        "            chosen.append(adaptive_sampler.choose(tier,rng,candidates))\n"
        "        elif not weights or all(float(v)==1 for v in weights.values()):",'SAMPLING')
    s = replace_once(s,
        '    mask_file,mask_policy=resolve_mask_policy(destination,ident,args.mask_weight,args.reset_mask_weights)\n',
        '    mask_file,mask_policy=resolve_mask_policy(destination,ident,args.mask_weight,args.reset_mask_weights)\n'
        "    from tools.nanojev_hawk_dove_prompt_balance import PromptBalance\n"
        "    has_adaptive_checkpoint = ck is not None and 'prompt_balance' in ck\n"
        "    adaptive = (PromptBalance(info['sha256'], ck['prompt_balance'] if has_adaptive_checkpoint else None)\n"
        "                if args.mode=='train' and (args.adaptive_prompts or has_adaptive_checkpoint) else None)\n",'RESTORE')
    s = replace_once(s,
        "        examples=choose_rows(args.teacher,focus,target,args.seed,args.views,mask_policy['weights_by_tier'])",
        "        examples=choose_rows(args.teacher,focus,target,args.seed,args.views,\n"
        "                             mask_policy['weights_by_tier'],adaptive)", 'CHOOSE_CALL')
    s = replace_once(s,
        "            output,_=ex.forward(model,[row['prompt']])\n            truth=",
        "            output,_=ex.forward(model,[row['prompt']])\n"
        "            if adaptive is not None and row['tier']==focus:\n"
        "                # Train-split numerical error from the ALREADY computed forward.\n"
        "                with torch.no_grad():\n"
        "                    point=model['v3'].compose_logits(output['sign'].detach(),\n"
        "                        output['exponent'].detach(),output['fraction'].detach(),10_000_000)\n"
        "                    px,py=point.detach().float().cpu().tolist()[0]\n"
        "                adaptive.observe(row,px,py)\n"
        "            truth=",'OBSERVE')
    s = replace_once(s,
        "        if target%args.chunk_steps==0 or target==args.steps:\n            report=probe",
        "        if target%args.chunk_steps==0 or target==args.steps:\n"
        "            trained_tier=focus\n"
        "            balance_summary=adaptive.commit(trained_tier,target) if adaptive is not None else None\n"
        "            report=probe",'COMMIT')
    s = replace_once(s,
        "            if args.device=='cuda':state['rng_cuda']=torch.cuda.get_rng_state_all()",
        "            if args.device=='cuda':state['rng_cuda']=torch.cuda.get_rng_state_all()\n"
        "            if adaptive is not None:state['prompt_balance']=adaptive.snapshot()",'CHECKPOINT')
    s = replace_once(s,
        "            report.update({'event':'hawk_dove_million_xy_probe','step':target,'focus':focus,",
        "            report.update({'event':'hawk_dove_million_xy_probe','step':target,'focus':focus,\n"
        "                           'prompt_balance':balance_summary,",'REPORT')
    s = replace_once(s,
        "                              'loss':loss_total,'checkpoint':str(checkfile)}),flush=True)",
        "                              'loss':loss_total,'checkpoint':str(checkfile),\n"
        "                              'prompt_balance':balance_summary}),flush=True)", 'LOG')
    s = replace_once(s,
        "    p.add_argument('--reset-mask-weights',action='store_true',",
        "    p.add_argument('--adaptive-prompts',action='store_true',\n"
        "                   help='Enable train-split, per-prompt hard-example sampling; persists in latest.pt')\n"
        "    p.add_argument('--reset-mask-weights',action='store_true',", 'CLI')
    ast.parse(s)
    return s


def main(argv=None):
    ap=argparse.ArgumentParser(description=__doc__)
    which=ap.add_mutually_exclusive_group(required=True)
    which.add_argument('--check',action='store_true')
    which.add_argument('--apply',action='store_true')
    ap.add_argument('--trainer',type=Path,default=Path('tools/nanojev_hawk_dove_million_train.py'))
    args=ap.parse_args(argv)
    path=args.trainer.resolve()
    original=path.read_bytes()
    try: source=original.decode('utf-8')
    except UnicodeDecodeError as exc: raise ValueError('TRAINER_NOT_UTF8: unchanged') from exc
    sha=hashlib.sha256(original).hexdigest()
    altered=transformed(source,sha)
    print('trainer:',path)
    print('source_sha256:',sha)
    print('compatible: yes')
    print('new_code_length:',len(altered))
    if args.apply:
        backup=path.with_name(path.name+'.pre-adaptive-'+sha[:12]+'.bak')
        if backup.exists() and backup.read_bytes()!=original:
            raise ValueError('CONFLICTING_BACKUP: unchanged')
        if not backup.exists():backup.write_bytes(original)
        staging=path.with_name(path.name+'.adaptive.tmp')
        staging.write_bytes(altered.encode('utf-8'))
        staging.replace(path)
        print('status: APPLIED; backup:',backup)
    else:
        print('status: CHECK_ONLY; file not modified')

if __name__=='__main__':main()
