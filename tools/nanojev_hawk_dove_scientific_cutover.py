"""Fork an existing Hawk–Dove v1 checkpoint into the scientific v2 lineage.

Strictly read-only for the donor. Validates the original / replacement SQLite
teachers, immutable fixture, source checkpoint, policies and copied optimizer.
Does not load GPU, perform training or replace any existing destination.
"""
from __future__ import annotations

import argparse
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile

from tools import nanojev_hawk_dove_million as hd
from tools import nanojev_hawk_dove_million_test as audit
from tools import nanojev_hawk_dove_million_train as legacy
from tools import nanojev_hawk_dove_scientific_train as science


def check_teachers(old_db:Path,new_db:Path,fixture:Path)->dict:
    old=hd.db_manifest(old_db);new=hd.db_manifest(new_db)
    oc=old['config'];nc=new['config']
    if oc.get('schema')!=hd.SCHEMA or nc.get('schema')!=hd.SCHEMA_SCIENCE:
        raise ValueError('OLD_V1_AND_NEW_SCIENTIFIC_V2_TEACHERS_REQUIRED')
    if nc.get('representation')!=hd.REP_SCIENCE or oc.get('mutation_mode')!='lattice' or nc.get('mutation_mode')!='lattice':
        raise ValueError('SCIENTIFIC_LATTICE_REQUIRED')
    for name in ('fixture','instances','seed','encounter_total','tier_order','rows','z_loss_weight','goal','mutation_mode'):
        if oc[name]!=nc[name]:raise ValueError('TEACHER_SEMANTIC_OR_SPLIT_DRIFT:'+name)
    _,meta=hd.read_fixture(fixture)
    if meta['sha256']!=oc['fixture']['sha256']:
        raise ValueError('EXISTING_FIXTURE_DOES_NOT_MATCH_ORIGINAL_TEACHER')
    with closing(sqlite3.connect(f'file:{old_db.resolve().as_posix()}?mode=ro',uri=True)) as db:
        db.execute('ATTACH DATABASE ? AS science',(f'file:{new_db.resolve().as_posix()}?mode=ro',))
        # Each old and new presentation must have the same id/instance/tier,
        # mask, target, split and full_count. Only the annotation changes.
        joins='a.presentation_id=b.presentation_id'
        missing=db.execute(f'''SELECT count(*) FROM presentations a LEFT JOIN science.presentations b ON {joins}
            WHERE b.presentation_id IS NULL''').fetchone()[0]
        extra=db.execute(f'''SELECT count(*) FROM science.presentations b LEFT JOIN presentations a ON {joins}
            WHERE a.presentation_id IS NULL''').fetchone()[0]
        if missing or extra:raise ValueError('PRESENTATION_POPULATION_DRIFT')
        drift=db.execute(f'''SELECT count(*) FROM presentations a JOIN science.presentations b ON {joins}
            WHERE a.instance_id!=b.instance_id OR a.tier!=b.tier OR a.mask!=b.mask
              OR a.full_count!=b.full_count OR a.x!=b.x OR a.y!=b.y OR a.z!=b.z
              OR a.split!=b.split''').fetchone()[0]
        if drift:raise ValueError('NUMERIC_TARGET_OR_MASK_DRIFT')
        maskzero=db.execute(f'''SELECT count(*) FROM presentations a JOIN science.presentations b ON {joins}
            WHERE a.mask=0 AND a.prompt!=b.prompt''').fetchone()[0]
        if maskzero:raise ValueError('RAW_MASK_PROMPTS_CHANGED')
        changed=db.execute(f'''SELECT count(*) FROM presentations a JOIN science.presentations b ON {joins}
            WHERE a.mask!=0 AND a.prompt!=b.prompt''').fetchone()[0]
        annotated=db.execute('SELECT count(*) FROM presentations WHERE mask!=0').fetchone()[0]
        if changed!=annotated:raise ValueError('SCIENTIFIC_ANNOTATIONS_NOT_APPLIED_TO_ALL_FULL_MASKS')
        edge_drift=db.execute('''SELECT count(*) FROM mutation_edges a LEFT JOIN science.mutation_edges b
            ON a.source_id=b.source_id AND a.target_id=b.target_id AND a.slot=b.slot
            WHERE b.source_id IS NULL''').fetchone()[0]
        if edge_drift:raise ValueError('MUTATION_LATTICE_DRIFT')
    return {'old_teacher_sha256':old['sha256'],'new_teacher_sha256':new['sha256'],
            'fixture_sha256':meta['sha256'],'presentations':nc['rows']['presentations'],
            'mutation_edges':nc['rows']['edges'],'scientific_presentations_changed':changed,
            'raw_only_prompts_identical':True,'split_and_targets_identical':True}


def _validated_old_policy(directory:Path,old_identity:dict)->tuple[dict,dict]:
    old_gate=directory/'tier-gates.json'
    if old_gate.exists():
        g=json.loads(old_gate.read_text(encoding='utf8'))
        if g.get('schema')!=legacy.GATE_POLICY_SCHEMA or g.get('lineage_config_sha256')!=legacy.gate_policy_config_digest(old_identity):
            raise ValueError('DONOR_GATE_POLICY_IDENTITY_DRIFT')
        gates=legacy.validate_gate_map(g['gate_percent_by_tier'])
    else:
        gates={tier:old_identity['gate_fraction']*100 for tier in hd.TIERS}
    old_mask=directory/'mask-weights.json'
    if old_mask.exists():
        m=json.loads(old_mask.read_text(encoding='utf8'))
        if m.get('schema')!=legacy.MASK_POLICY_SCHEMA or m.get('lineage_config_sha256')!=legacy.gate_policy_config_digest(old_identity):
            raise ValueError('DONOR_MASK_POLICY_IDENTITY_DRIFT')
        weights=m['weights_by_tier']
    else:
        weights={t:{str(m):1.0 for m,_ in hd.masks(t)} for t in hd.TIERS}
    # Apply the donor's existing strict policy validators independently.
    if set(weights)!=set(hd.TIERS):raise ValueError('INVALID_DONOR_MASK_POLICY_TIERS')
    for tier in hd.TIERS:
        if set(weights[tier])!={str(m) for m,_ in hd.masks(tier)}:
            raise ValueError('INVALID_DONOR_MASK_POLICY_MASKS')
        for w in weights[tier].values():
            if type(w) not in (float,int) or not 0<float(w)<=100:
                raise ValueError('INVALID_DONOR_MASK_POLICY_WEIGHT')
    return gates,weights


def cutover(*,old_run:Path,new_run:Path,old_db:Path,new_db:Path,
            fixture:Path,source:Path)->dict:
    import torch
    old_run=old_run.resolve();new_run=new_run.resolve()
    old_db=old_db.resolve();new_db=new_db.resolve()
    source=source.resolve();fixture=fixture.resolve()
    if old_run==new_run or new_run.exists():raise FileExistsError('DESTINATION_MUST_NOT_EXIST:'+str(new_run))
    if not source.is_file():raise FileNotFoundError('SOURCE_CHECKPOINT_REQUIRED')
    if not (old_run/'config.json').is_file() or not (old_run/'latest.pt').is_file():
        raise FileNotFoundError('DONOR_CONFIG_AND_LATEST_CHECKPOINT_REQUIRED')
    evidence=check_teachers(old_db,new_db,fixture)
    # Independent end-to-end audit of the complete new database and all edges.
    independent=audit.audit(new_db,fixture)
    if independent['status']!='PASS':raise ValueError('SCIENTIFIC_TEACHER_AUDIT_FAILED')
    old_ident=json.loads((old_run/'config.json').read_text(encoding='utf8'))
    if old_ident.get('schema')!=legacy.CHECKPOINT_SCHEMA or old_ident.get('teacher_sha256')!=evidence['old_teacher_sha256']:
        raise ValueError('DONOR_CONFIG_OR_TEACHER_DRIFT')
    if old_ident.get('source_sha256')!=hd.sha(source):raise ValueError('DONOR_SOURCE_DRIFT')
    before=hd.sha(old_run/'latest.pt')
    old_ck=torch.load(old_run/'latest.pt',map_location='cpu',weights_only=True)
    if old_ck['schema']!=legacy.CHECKPOINT_SCHEMA or old_ck['config']!=old_ident:
        raise ValueError('DONOR_EMBEDDED_CONFIG_DRIFT')
    for k in ('decoder','game_operator_head','optimizer','rng_cpu','step','focus','streak'):
        if k not in old_ck:raise ValueError('DONOR_INCOMPLETE_CHECKPOINT:'+k)
    if old_ck['focus'] not in hd.TIERS or int(old_ck['step'])<0:
        raise ValueError('DONOR_CHECKPOINT_STATE_INVALID')
    gates,weights=_validated_old_policy(old_run,old_ident)
    new_ident=dict(old_ident)
    new_ident.update({'schema':science.CHECKPOINT_SCHEMA,
        'teacher_sha256':evidence['new_teacher_sha256'],
        'trainer_sha256':science.sha(Path(science.__file__)),
        'representation':hd.REP_SCIENCE,
        'cutover_checkpoint_sha256':before,
        'cutover_checkpoint_step':int(old_ck['step']),
        'cutover_from_teacher_sha256':evidence['old_teacher_sha256']})
    new_ck=dict(old_ck,config=new_ident,schema=science.CHECKPOINT_SCHEMA)
    provenance={'schema':'nanojev.hawk-dove.scientific.cutover.v2',
        'donor_run':str(old_run),'donor_checkpoint_sha256':before,
        'donor_step':old_ck['step'],'donor_focus':old_ck['focus'],
        'optimizer_and_rng_preserved':True,'parent_loaded':False,
        'data':evidence,'source_checkpoint_sha256':hd.sha(source),
        'new_trainer_sha256':new_ident['trainer_sha256'],
        'gate_percent_by_tier':gates,'mask_weights_by_tier':weights}
    new_run.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=new_run.name+'-staging-',dir=new_run.parent) as td:
        target=Path(td)
        science.jwrite(target/'config.json',new_ident)
        torch.save(new_ck,target/'latest.pt')
        lineage=science.gate_policy_config_digest(new_ident)
        science.jwrite(target/'tier-gates.json',{'schema':science.GATE_POLICY_SCHEMA,
            'lineage_config_sha256':lineage,'gate_percent_by_tier':gates})
        science.jwrite(target/'mask-weights.json',{'schema':science.MASK_POLICY_SCHEMA,
            'lineage_config_sha256':lineage,'weights_by_tier':weights})
        science.jwrite(target/'cutover.json',provenance)
        # Validate serialized new file as it will later be loaded by the trainer.
        restored=torch.load(target/'latest.pt',map_location='cpu',weights_only=True)
        if restored['schema']!=science.CHECKPOINT_SCHEMA or restored['config']!=new_ident:
            raise ValueError('SCIENTIFIC_CHECKPOINT_ROUNDTRIP_FAILED')
        if hd.sha(old_run/'latest.pt')!=before or hd.sha(old_db)!=evidence['old_teacher_sha256'] or hd.sha(new_db)!=evidence['new_teacher_sha256']:
            raise ValueError('CUTOVER_INPUT_CHANGED_DURING_FORK')
        if new_run.exists():raise FileExistsError('DESTINATION_RACED_DURING_CUTOVER')
        target.rename(new_run)
    return {'status':'PASS','new_run':str(new_run),'step':old_ck['step'],
        'focus_at_cutover':old_ck['focus'],'new_checkpoint':str(new_run/'latest.pt'),
        'donor_checkpoint_untouched':True,**evidence}


def make_parser():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--old-run',type=Path,default=legacy.DEFAULT_OUT)
    p.add_argument('--new-run',type=Path,default=science.DEFAULT_OUT)
    p.add_argument('--old-db',type=Path,default=legacy.DEFAULT_TEACHER)
    p.add_argument('--new-db',type=Path,default=science.DEFAULT_TEACHER)
    p.add_argument('--fixture',type=Path,default=hd.DEFAULT_FIXTURE)
    p.add_argument('--source',type=Path,default=legacy.DEFAULT_SOURCE)
    return p


def main(argv=None):
    a=make_parser().parse_args(argv)
    print(json.dumps(cutover(old_run=a.old_run,new_run=a.new_run,old_db=a.old_db,
        new_db=a.new_db,fixture=a.fixture,source=a.source),sort_keys=True,indent=2))

if __name__=='__main__':main()
