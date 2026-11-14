#!/usr/bin/env python3
"""Compile sealed preROM (JSON) Python-AST comparison pairs into English-like ROM3.3.3.

An offline, read-only transformation: no source regeneration and no label changes.
Three distinct natural-language views describe one relation, without leaking the answer.
"""
from __future__ import annotations
import argparse
import ast
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import tempfile

from nanojev_binary_consensus_runtime import deps, hash_file, load_bank
from nanojev_binary_raw_rom_bank import load_raw_bank, RAW_VARIANTS, equivalent
from nanojev_training_task_store import TrainingTaskStore

SCHEMA = 'nanojev.binary-consensus-rom333-bank.v1'
VARIANTS = ('rom333', 'rom333_reverse')
DEFAULT_SOURCE = Path(r'C:\Users\subsi\NanoJev\runs\binary_consensus_raw_rom_bank_v1')
DEFAULT_OUTPUT = Path(r'C:\Users\subsi\NanoJev\runs\binary_consensus_rom333_bank_v1')

# Give the models the question in three familiar phrasings. Unlike preROM,
# this is a small controlled English-like language, not a JSON instruction program.
# Keep the operands at the end so any unexpected suffix truncation is detectable.
PREFIX = ("ROM3.3.3\n"
          "TASK: Compare the Python syntax of two programs.\n"
          "VIEW 1 - SOURCE: Ignore layout and formatting. Do the programs represent the same Python syntax?\n"
          "VIEW 2 - PARSER: Would Python build the same abstract syntax tree for both, ignoring source positions?\n"
          "VIEW 3 - STRUCTURE: Are the statements, names, constants, and operators identical in the same tree positions?\n"
          "Use all three views of the same two programs. Ignore whitespace and comments, but not names or values.\n"
          "\nPROGRAM A:\n```python\n")
MIDDLE = "\n```\n\nPROGRAM B:\n```python\n"
SUFFIX = ("\n```\n\n"
          "VERDICT: Choose one consistent answer for all three views.\n"
          "SAME / SAME / SAME = the normalized Python syntax trees match.\n"
          "DIFFERENT / DIFFERENT / DIFFERENT = the normalized Python syntax trees do not match.\n"
          "ANSWER:")
ANSWERS = {'SAME':' SAME / SAME / SAME', 'DIFFERENT':' DIFFERENT / DIFFERENT / DIFFERENT'}


def render(a:str,b:str) -> str:
    if not a.strip() or not b.strip() or '```' in a or '```' in b:
        raise RuntimeError('ROM333_PROGRAM_CANNOT_BE_RENDERED')
    # Requiring valid Python ensures that the three views agree on AST semantics.
    ast.parse(a)
    ast.parse(b)
    return PREFIX + a + MIDDLE + b + SUFFIX


def unpack(prompt:str):
    if not (prompt.startswith(PREFIX) and prompt.endswith(SUFFIX)):
        raise RuntimeError('ROM333_PROMPT_GRAMMAR_DRIFT')
    body=prompt[len(PREFIX):-len(SUFFIX)]
    if body.count(MIDDLE)!=1:
        raise RuntimeError('ROM333_OPERAND_DELIMITER_INVALID')
    a,b=body.split(MIDDLE)
    if render(a,b)!=prompt:
        raise RuntimeError('ROM333_PROMPT_NOT_CANONICAL')
    return a,b


def transform(sample:dict, *, expected_label:str, rom) -> dict:
    if (sample.get('task')!='consensus_pairwise' or len(sample.get('candidates',[]))!=2
        or [c['candidate_id'].upper() for c in sample['candidates']]!=['SAME','DIFFERENT']
        or any(len(c.get('paths',[]))!=1 for c in sample['candidates'])):
        raise RuntimeError('ROM333_SOURCE_QUESTION_INVALID')
    p=sample['candidates'][0]['paths'][0]['prompt']
    if p!=sample['candidates'][1]['paths'][0]['prompt'] or not p.endswith('\nROM_OUTPUT:'):
        raise RuntimeError('ROM333_PREROM_PROMPT_MISMATCH')
    program=json.loads(p[:-len('\nROM_OUTPUT:')])
    if (program.get('schema')!=rom.ROM_SCHEMA or program.get('template_id')!=rom.rom_template_id('triad')
        or program.get('decision') is not None or program.get('task')!='triad'):
        raise RuntimeError('ROM333_PREROM_TEMPLATE_MISMATCH')
    inputs=program['inputs']
    a,b=inputs['left_program'],inputs['right_program']
    label='SAME' if equivalent(a,b) else 'DIFFERENT'
    if label!=expected_label or sample['gold_index']!=(0 if label=='SAME' else 1):
        raise RuntimeError('ROM333_AST_ORACLE_MISMATCH')
    new=json.loads(json.dumps(sample))
    new['question_id']=str(sample['question_id'])+'::rom333-v1'
    prompt=render(a,b)
    for candidate in new['candidates']:
        candidate['paths'][0]={'prompt':prompt,'answer':ANSWERS[candidate['candidate_id'].upper()]}
    return new


def validate_sample(sample:dict, *, expected_label:str):
    if (sample.get('task')!='consensus_pairwise' or len(sample.get('candidates',[]))!=2
        or [c['candidate_id'].upper() for c in sample['candidates']]!=['SAME','DIFFERENT']
        or any(len(c.get('paths',[]))!=1 for c in sample['candidates'])
        or sample.get('gold_index')!=(0 if expected_label=='SAME' else 1)):
        raise RuntimeError('ROM333_SAMPLE_CONTRACT_INVALID')
    p=sample['candidates'][0]['paths'][0]['prompt']
    if p!=sample['candidates'][1]['paths'][0]['prompt']:
        raise RuntimeError('ROM333_CANDIDATE_PROMPT_MISMATCH')
    a,b=unpack(p)
    if ('SAME' if equivalent(a,b) else 'DIFFERENT')!=expected_label:
        raise RuntimeError('ROM333_SAMPLE_ORACLE_MISMATCH')
    for c in sample['candidates']:
        if c['paths'][0]['answer']!=ANSWERS[c['candidate_id'].upper()]:
            raise RuntimeError('ROM333_TRIPLE_ANSWER_INVALID')
    return (a,b)


def create(*,source_bank:Path,output_dir:Path) -> dict:
    source_bank=Path(source_bank).expanduser().resolve(strict=True)
    output_dir=Path(output_dir).expanduser().resolve()
    if output_dir.exists():
        raise FileExistsError('ROM333_OUTPUT_ALREADY_EXISTS: '+str(output_dir))
    source_manifest=json.loads((source_bank/'manifest.json').read_text(encoding='utf-8'))
    if (source_manifest.get('rawRomOnly') is not True
        or source_manifest.get('rawRomSchema')!='nanojev.binary-consensus-raw-rom-bank.v1'):
        raise RuntimeError('ROM333_SOURCE_MUST_BE_SEALED_PREROM')
    _,rom,_=deps()
    rom.validate_rom_templates()
    conn=sqlite3.connect((source_bank/'training-tasks.sqlite').as_uri()+'?mode=ro',uri=True)
    conn.row_factory=sqlite3.Row
    try:
        cohorts=conn.execute('SELECT * FROM cohorts ORDER BY cohort_id').fetchall()
        groups=conn.execute('SELECT * FROM task_groups ORDER BY group_id').fetchall()
        splits=sorted({g['split'] for g in groups})
        if not cohorts or not groups or not splits:
            raise RuntimeError('ROM333_EMPTY_SOURCE')
        original={s:load_raw_bank(source_bank,split=s) for s in splits}
        raw=conn.execute("SELECT group_id,variant,sample_json,sample_sha256 FROM presentations "
                         "WHERE variant IN ('raw_rom','raw_rom_reverse') ORDER BY group_id,variant").fetchall()
        keyed={}
        for r in raw:
            if hashlib.sha256(r['sample_json'].encode('utf-8')).hexdigest()!=r['sample_sha256']:
                raise RuntimeError('ROM333_SOURCE_PRESENTATION_HASH_MISMATCH')
            keyed.setdefault(r['group_id'],{})[r['variant']]=json.loads(r['sample_json'])
        if set(keyed)!={g['group_id'] for g in groups}:
            raise RuntimeError('ROM333_SOURCE_VARIANTS_MISSING')
        output_dir.parent.mkdir(parents=True,exist_ok=True)
        temp=Path(tempfile.mkdtemp(prefix='.'+output_dir.name+'-tmp-',dir=output_dir.parent))
        try:
            counts=Counter()
            with TrainingTaskStore(temp/'training-tasks.sqlite') as store:
                with store.conn:
                    for c in cohorts:
                        store.add_cohort(c['cohort_id'],family=c['family'],split=c['split'],
                                         cycle=c['cycle'],source=json.loads(c['source_json']))
                    for g in groups:
                        if (g['family']!='clef_binary_consensus' or g['task']!='consensus_pairwise'
                            or g['oracle']!='normalized-python-ast-equivalence-v1' or g['label_status']!='verified'
                            or g['train_count'] or g['eval_count']):
                            raise RuntimeError('ROM333_SOURCE_GROUP_CONTRACT_INVALID')
                        target=json.loads(g['target_json'])
                        label=target['label']
                        if label not in ANSWERS or target['goldIndex']!=(0 if label=='SAME' else 1):
                            raise RuntimeError('ROM333_SOURCE_GOLD_INVALID')
                        src=keyed[g['group_id']]
                        if set(src)!=set(RAW_VARIANTS):
                            raise RuntimeError('ROM333_SOURCE_ORIENTATION_MISSING')
                        f=transform(src['raw_rom'],expected_label=label,rom=rom)
                        r=transform(src['raw_rom_reverse'],expected_label=label,rom=rom)
                        fa,fb=unpack(f['candidates'][0]['paths'][0]['prompt'])
                        ra,rb=unpack(r['candidates'][0]['paths'][0]['prompt'])
                        if not (fa==rb and fb==ra):
                            raise RuntimeError('ROM333_REVERSE_PAIR_DRIFT')
                        gid=store.put(cohort_id=g['cohort_id'],family=g['family'],task=g['task'],
                                      split=g['split'],fingerprint=g['source_fingerprint'],oracle=g['oracle'],
                                      label_status=g['label_status'],target=target,
                                      provenance=json.loads(g['provenance_json']),
                                      variants={'rom333':f,'rom333_reverse':r})
                        if gid!=g['group_id']:
                            raise RuntimeError('ROM333_GROUP_ID_CHANGED')
                        counts[(g['split'],label)]+=1
                checks=store.verify()
                stats=store.stats()
            if counts[('train','SAME')]!=counts[('train','DIFFERENT')]:
                raise RuntimeError('ROM333_LABEL_IMBALANCE')
            manifest={'schema':'nanojev.training-task-generator.v1','ok':True,'binaryConsensusOnly':True,
                      'rom333Only':True,'rom333Schema':SCHEMA,'noSourceGeneration':True,
                      'sourceBankManifestSha256':hash_file(source_bank/'manifest.json'),
                      'sourceBankDbSha256':hash_file(source_bank/'training-tasks.sqlite'),
                      'counts':stats,'checks':checks,
                      'transformation':'sealed-preROM-to-three-English-views-triple-verdict-v1'}
            (temp/'manifest.json').write_text(json.dumps(manifest,indent=2,sort_keys=True)+'\n',encoding='utf-8')
            for split in splits:
                compiled=load_rom333_bank(temp,split=split)
                if compiled['count']!=original[split]['count']:
                    raise RuntimeError('ROM333_SPLIT_CARDINALITY_DRIFT')
            os.replace(temp,output_dir)
            return {'event':'rom333_bank_created','ok':True,'output':str(output_dir),
                    'groups':stats['groups'],'presentations':stats['presentations'],
                    'by_split':stats['by_family_split'],'no_generation':True}
        finally:
            if temp.exists():
                shutil.rmtree(temp)
    finally:
        conn.close()


def load_rom333_bank(bank_dir:Path, *, split:str,max_groups:int=0):
    bank_dir=Path(bank_dir).expanduser().resolve(strict=True)
    manifest=json.loads((bank_dir/'manifest.json').read_text(encoding='utf-8'))
    if not (manifest.get('rom333Only') is True and manifest.get('rom333Schema')==SCHEMA):
        raise RuntimeError('ROM333_BANK_SCHEMA_MISMATCH')
    data=load_bank(bank_dir,split=split,variants=VARIANTS,max_groups=max_groups)
    for _, group in data['groups']:
        for sample in group['variants'].values():
            validate_sample(sample,expected_label=group['label'])
    return data


def audit_token_visibility(*,bank,bundles,champion,objective,args,logger):
    """Fail before training if any view/operand would be suffix-truncated by a backbone."""
    longest={k:{'prompt_tokens':0,'answer_tokens':0} for k in bundles}
    checked=0
    for gid,group in bank['groups']:
        for variant,sample in group['variants'].items():
            question=champion.base.deserialize_question(sample,objective)
            for label,bundle in bundles.items():
                for candidate in question.candidates:
                    for path in candidate.paths:
                        prompt_tokens=len(bundle.tokenizer.encode(path.prompt,add_special_tokens=False))
                        answer_tokens=len(bundle.tokenizer.encode(path.answer,add_special_tokens=False))
                        longest[label]['prompt_tokens']=max(longest[label]['prompt_tokens'],prompt_tokens)
                        longest[label]['answer_tokens']=max(longest[label]['answer_tokens'],answer_tokens)
                        budget=min(int(args.max_prompt_tokens),int(bundle.max_positions)-answer_tokens)
                        if prompt_tokens > budget:
                            raise RuntimeError(f'ROM333_WOULD_TRUNCATE: model={label} group={gid} '
                                               f'variant={variant} prompt_tokens={prompt_tokens} budget={budget}')
            checked+=1
    logger.emit('rom333_token_visibility_verified',presentations=checked,
                max_prompt_tokens=args.max_prompt_tokens,backbones=longest,
                both_programs_and_all_views_visible=True)
    return longest


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source-bank',type=Path,default=DEFAULT_SOURCE)
    p.add_argument('--output-dir',type=Path,default=DEFAULT_OUTPUT)
    args=p.parse_args(argv)
    print(json.dumps(create(source_bank=args.source_bank,output_dir=args.output_dir),sort_keys=True),flush=True)

if __name__=='__main__':
    main()
