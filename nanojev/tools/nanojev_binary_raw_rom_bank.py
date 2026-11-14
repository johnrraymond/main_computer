#!/usr/bin/env python3
"""Offline, fail-closed cutover of an existing binary consensus bank to *structured* ROM.

Never regenerate source questions or modify the input SQLite file.  Render each
verified AB/AC/BC program pair with the original ROM triad template.  Preserve
all group, split, source-fingerprint, and answer contracts.
"""
from __future__ import annotations

import argparse
import ast
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import tempfile

from nanojev_binary_consensus_runtime import deps, load_bank, hash_file
from nanojev_training_task_store import TrainingTaskStore

DEFAULT_SOURCE = Path(r'C:\Users\subsi\NanoJev\runs\binary_consensus_bank_v1')
DEFAULT_OUTPUT = Path(r'C:\Users\subsi\NanoJev\runs\binary_consensus_raw_rom_bank_v1')
RAW_VARIANTS = ('raw_rom', 'raw_rom_reverse')
RAW_SCHEMA = 'nanojev.binary-consensus-raw-rom-bank.v1'


def equivalent(a: str, b: str) -> bool:
    return ast.dump(ast.parse(a), include_attributes=False) == ast.dump(ast.parse(b), include_attributes=False)


def raw_question(sample: dict, *, label: str, rom, orientation: str) -> dict:
    if sample['task'] != 'consensus_pairwise' or len(sample['candidates']) != 2:
        raise RuntimeError('RAW_ROM_SOURCE_QUESTION_INVALID')
    if [c['candidate_id'].lower() for c in sample['candidates']] != ['same', 'different']:
        raise RuntimeError('RAW_ROM_SOURCE_CANDIDATES_INVALID')
    if any(len(c['paths']) != 1 for c in sample['candidates']):
        raise RuntimeError('RAW_ROM_SOURCE_PATH_COUNT_INVALID')
    source = sample['candidates'][0]['paths'][0]['prompt']
    if source != sample['candidates'][1]['paths'][0]['prompt']:
        raise RuntimeError('RAW_ROM_SOURCE_PROMPT_MISMATCH')
    a, b = rom._program_pair(source)
    oracle = 'SAME' if equivalent(a, b) else 'DIFFERENT'
    if oracle != label or sample['gold_index'] != (0 if oracle == 'SAME' else 1):
        raise RuntimeError('RAW_ROM_AST_ORACLE_MISMATCH')
    prompt = rom.rom_prompt('triad', {'left_program':a, 'right_program':b})
    payload = json.loads(prompt.removesuffix('\nROM_OUTPUT:'))
    if (not prompt.endswith('\nROM_OUTPUT:') or payload['schema'] != rom.ROM_SCHEMA
        or payload['template_id'] != rom.rom_template_id('triad')
        or payload['decision'] is not None):
        raise RuntimeError('RAW_ROM_TEMPLATE_DRIFT')
    new = json.loads(json.dumps(sample))
    new['question_id'] = str(sample['question_id']) + '::structured-rom-v1'
    for c in new['candidates']:
        c['paths'][0] = {'prompt':prompt, 'answer':rom.rom_candidate_answer('triad',c['candidate_id'].upper())}
    if new['candidates'][0]['paths'][0]['prompt'] != new['candidates'][1]['paths'][0]['prompt']:
        raise RuntimeError('RAW_ROM_PROMPT_DRIFT')
    return new


def create(*, source_bank:Path, output_dir:Path) -> dict:
    source_bank=Path(source_bank).expanduser().resolve(strict=True)
    output_dir=Path(output_dir).expanduser().resolve()
    if output_dir.exists():
        raise FileExistsError('RAW_ROM_OUTPUT_ALREADY_EXISTS: '+str(output_dir))
    source_manifest=json.loads((source_bank/'manifest.json').read_text(encoding='utf-8'))
    if source_manifest.get('binaryConsensusOnly') is not True or source_manifest.get('ok') is not True:
        raise RuntimeError('RAW_ROM_SOURCE_NOT_SEALED_BINARY_BANK')
    champion,rom,_=deps()
    rom.validate_rom_templates()
    # Validate every populated split using the existing read-only fingerprint,
    # label, and split checker before considering a new bank authoritative.
    src_db=sqlite3.connect((source_bank/'training-tasks.sqlite').as_uri()+'?mode=ro',uri=True)
    src_db.row_factory=sqlite3.Row
    try:
        cohorts=src_db.execute('SELECT * FROM cohorts ORDER BY cohort_id').fetchall()
        groups=src_db.execute('SELECT * FROM task_groups ORDER BY group_id').fetchall()
        if not groups or not cohorts:
            raise RuntimeError('RAW_ROM_SOURCE_EMPTY')
        splits={r['split'] for r in groups}
        loaded={s:load_bank(source_bank,split=s) for s in sorted(splits)}
        # Loader validation checks the natural-ROM pair; this converter additionally
        # reads the source-language pair, whose bindings are the oracle authority.
        presentations=src_db.execute('''SELECT group_id,variant,sample_json,sample_sha256 FROM presentations
              WHERE variant IN ('natural','natural_reverse') ORDER BY group_id,variant''').fetchall()
        keyed={}
        for r in presentations:
            if hashlib.sha256(r['sample_json'].encode('utf-8')).hexdigest()!=r['sample_sha256']:
                raise RuntimeError('RAW_ROM_SOURCE_PRESENTATION_HASH_MISMATCH')
            keyed.setdefault(r['group_id'],{})[r['variant']]=json.loads(r['sample_json'])
        if set(keyed)!={r['group_id'] for r in groups}:
            raise RuntimeError('RAW_ROM_SOURCE_VARIANTS_MISSING')
        output_dir.parent.mkdir(parents=True,exist_ok=True)
        temporary=Path(tempfile.mkdtemp(prefix='.'+output_dir.name+'-tmp-',dir=output_dir.parent))
        try:
            count=Counter()
            with TrainingTaskStore(temporary/'training-tasks.sqlite') as store:
                with store.conn:
                    for r in cohorts:
                        store.add_cohort(r['cohort_id'],family=r['family'],split=r['split'],cycle=r['cycle'],source=json.loads(r['source_json']))
                    parent_seen={}
                    for r in groups:
                        if (r['family'] != 'clef_binary_consensus' or r['task']!='consensus_pairwise'
                            or r['label_status']!='verified' or r['oracle']!='normalized-python-ast-equivalence-v1'
                            or r['train_count'] or r['eval_count']):
                            raise RuntimeError('RAW_ROM_SOURCE_GROUP_CONTRACT_INVALID')
                        target=json.loads(r['target_json'])
                        provenance=json.loads(r['provenance_json'])
                        label=target['label']
                        if label not in ('SAME','DIFFERENT'):
                            raise RuntimeError('RAW_ROM_INVALID_LABEL')
                        originals=keyed[r['group_id']]
                        if set(originals)!= {'natural','natural_reverse'}:
                            raise RuntimeError('RAW_ROM_SOURCE_ORIENTATION_MISSING')
                        forward=raw_question(originals['natural'],label=label,rom=rom,orientation='forward')
                        reverse=raw_question(originals['natural_reverse'],label=label,rom=rom,orientation='reverse')
                        fa,fb=rom._program_pair(originals['natural']['candidates'][0]['paths'][0]['prompt'])
                        ra,rb=rom._program_pair(originals['natural_reverse']['candidates'][0]['paths'][0]['prompt'])
                        if not (fa == rb and fb == ra):
                            raise RuntimeError('RAW_ROM_SOURCE_REVERSE_BINDING_DRIFT')
                        parent=provenance['parentFingerprint']
                        if parent in parent_seen and parent_seen[parent]!=r['split']:
                            raise RuntimeError('RAW_ROM_SOURCE_PARENT_SPLIT_LEAK')
                        parent_seen[parent]=r['split']
                        gid=store.put(cohort_id=r['cohort_id'],family=r['family'],task=r['task'],split=r['split'],
                             fingerprint=r['source_fingerprint'],oracle=r['oracle'],label_status=r['label_status'],
                             target=target,provenance=provenance,variants={'raw_rom':forward,'raw_rom_reverse':reverse})
                        if gid!=r['group_id']:
                            raise RuntimeError('RAW_ROM_GROUP_ID_CHANGED')
                        count[(r['split'],label)]+=1
                checks=store.verify()
                stats=store.stats()
            if ('train','SAME') not in count or count[('train','SAME')]!=count[('train','DIFFERENT')]:
                raise RuntimeError('RAW_ROM_TRAIN_LABEL_IMBALANCE')
            info={'schema':'nanojev.training-task-generator.v1','ok':True,'binaryConsensusOnly':True,
                  'rawRomOnly':True,'rawRomSchema':RAW_SCHEMA,
                  'romSchema':rom.ROM_SCHEMA,'romTemplateVersion':rom.ROM_TEMPLATE_VERSION,
                  'romTemplateId':rom.rom_template_id('triad'),
                  'sourceBankManifestSha256':hash_file(source_bank/'manifest.json'),
                  'sourceBankDbSha256':hash_file(source_bank/'training-tasks.sqlite'),
                  'counts':stats,'checks':checks,
                  'transformation':'existing-natural-pair-bindings-to-original-triad-structured-ROM',
                  'noSourceGeneration':True,'noTrainingPerformed':True}
            (temporary/'manifest.json').write_text(json.dumps(info,indent=2,sort_keys=True)+'\n',encoding='utf-8')
            # Strong end-to-end validation *before* atomically publishing output.
            for split in splits:
                bank=load_raw_bank(temporary,split=split)
                if bank['count']!=loaded[split]['count']:
                    raise RuntimeError('RAW_ROM_SPLIT_CARDINALITY_DRIFT')
            os.replace(temporary,output_dir)
            return {'event':'binary_raw_rom_bank_created','output':str(output_dir),'groups':stats['groups'],
                    'presentations':stats['presentations'],'by_split':stats['by_family_split'],
                    'romTemplateId':rom.rom_template_id('triad'),'no_generation':True,'ok':True}
        finally:
            if temporary.exists():
                shutil.rmtree(temporary)
    finally:
        src_db.close()


def load_raw_bank(bank_dir: Path, *, split:str, max_groups:int=0):
    """Require exact structured ROM format; delegate label checks to shared loader."""
    from nanojev_binary_consensus_runtime import load_bank
    bank_dir=Path(bank_dir).expanduser().resolve(strict=True)
    m=json.loads((bank_dir/'manifest.json').read_text(encoding='utf-8'))
    _,rom,_=deps()
    if (m.get('rawRomOnly') is not True or m.get('rawRomSchema')!=RAW_SCHEMA
        or m.get('romTemplateId')!=rom.rom_template_id('triad')):
        raise RuntimeError('RAW_ROM_BANK_TEMPLATE_CONTRACT_MISMATCH')
    result=load_bank(bank_dir,split=split,variants=RAW_VARIANTS,max_groups=max_groups)
    for gid,g in result['groups']:
        for variant,row in g['variants'].items():
            c=row['candidates']
            if any(len(x['paths'])!=1 for x in c):
                raise RuntimeError('RAW_ROM_PATH_CONTRACT_INVALID')
            prompt=c[0]['paths'][0]['prompt']
            if not prompt.endswith('\nROM_OUTPUT:'):
                raise RuntimeError('RAW_ROM_PROMPT_FORMAT_INVALID')
            payload=json.loads(prompt[:-len('\nROM_OUTPUT:')])
            if payload.get('schema')!=rom.ROM_SCHEMA or payload.get('template_id')!=m['romTemplateId'] or payload.get('decision') is not None:
                raise RuntimeError('RAW_ROM_PROMPT_TEMPLATE_INVALID')
            for x in c:
                expected=rom.rom_candidate_answer('triad',x['candidate_id'].upper())
                if x['paths'][0]['answer']!=expected:
                    raise RuntimeError('RAW_ROM_ANSWER_FORMAT_INVALID')
    return result


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source-bank',type=Path,default=DEFAULT_SOURCE)
    p.add_argument('--output-dir',type=Path,default=DEFAULT_OUTPUT)
    a=p.parse_args(argv)
    print(json.dumps(create(source_bank=a.source_bank,output_dir=a.output_dir),sort_keys=True),flush=True)


if __name__=='__main__':
    main()
