"""Scientific-notation fork of NanoJev X/Y Hawk–Dove training.

Loads frozen Qwen, then restores complete decoder/gamehead state from a
verified v2 cutover checkpoint. Original v1 run and parent remain untouched.
ARGMAX/Z is read-only: no Z gradient is applied.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from contextlib import closing
import random

from tools import nanojev_hawk_dove_million as hd

CHECKPOINT_SCHEMA='nanojev.hawk-dove.million-counts.scientific.training.v2'
DEFAULT_PARENT=Path.home()/'NanoJev/runs/game_math_xy_v10_operand_audited/latest.pt'
DEFAULT_SOURCE=Path('runtime/game-transform-experiment/source-v4.pt')
DEFAULT_TEACHER=Path('runtime/hawk-dove-million/teacher-scientific-v2.sqlite3')
DEFAULT_OUT=Path.home()/'NanoJev/runs/hawk_dove_million_scientific_v2'
# Exact SHA-256 of the shipped v1 trainer. Accept only this known ancestor
# when resuming a v1 checkpoint with the new policy-capable trainer.
LEGACY_TRAINER_SHA256='de900d9d63170fd1466807d0581792d47f8b3ce330029a081d387d96cc7c2080'
# Prior independent-tier-gates patch; preserve already-running optimizer ancestry.
TIER_GATE_TRAINER_SHA256='20312401b2120af35bacc5577a5bdb121089da157d4e43b9ed2bb82811b41e18'
# Prior T00 mask-diagnostics trainer; accepts its checkpoints for strict training resume.
MASK_DIAGNOSTICS_TRAINER_SHA256='38ec9659730ad25a7ad5a3d9da55311bbbecd32965d9a929da41ef7231d06294'
GATE_POLICY_SCHEMA='nanojev.hawk-dove.million-counts.tier-gates.v1'
MASK_POLICY_SCHEMA='nanojev.hawk-dove.million-counts.mask-weights.v1'


def sha(path:Path)->str:
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda:f.read(1<<20),b''):h.update(block)
    return h.hexdigest()


def jwrite(path:Path,value:dict):
    path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_name(path.name+'.tmp')
    temp.write_text(json.dumps(value,sort_keys=True,indent=2,allow_nan=False)+'\n',encoding='utf8')
    temp.replace(path)


def teacher_info(db:Path):
    return hd.db_manifest(db)


def config(args,info):
    """Scientific v2 only starts from an explicitly verified cutover checkpoint.

    No historical parent is reloaded: the donor supplies the complete trained
    decoder and frozen game operator head. Old parent SHA stays in provenance.
    """
    if info['config'].get('schema')!=hd.SCHEMA_SCIENCE or info['config'].get('representation')!=hd.REP_SCIENCE:
        raise ValueError('SCIENTIFIC_TEACHER_REQUIRED')
    path=args.output/'config.json'
    if not path.is_file():raise FileNotFoundError('RUN_SCIENTIFIC_CUTOVER_FIRST:'+str(path))
    saved=json.loads(path.read_text(encoding='utf8'))
    expected=dict(saved)
    expected.update({'source_sha256':sha(args.source),'teacher_sha256':info['sha256'],
       'trainer_sha256':sha(Path(__file__)),'device':args.device,'dtype':args.dtype,
       'seed':args.seed,'lr':args.lr,'views':args.views,'probe_per_tier':args.probe_per_tier,
       'decoder_head_decode_cap':10_000_000,'Z_loss_weight':0,
       'representation':hd.REP_SCIENCE})
    if args.gate_percent is not None:
        # Gate policies live in their own independently persisted sidecar.
        # The original checkpoint's gate_fraction is not rewritten.
        pass
    return expected


def lineage_compatible(saved:dict,expected:dict)->bool:
    """Permit only the verified v1 trainer -> tier-gate-control upgrade.

    The original serialized config remains the checkpoint identity. Changing
    evaluation thresholds cannot rewrite model/optimizer ancestry.
    """
    if not isinstance(saved,dict) or set(saved)!=set(expected):return False
    if saved.get('trainer_sha256') != expected['trainer_sha256']:
        return False
    for name,value in expected.items():
        if name not in ('trainer_sha256','gate_fraction') and saved[name]!=value:
            return False
    return isinstance(saved.get('gate_fraction'),(int,float)) and 0<saved['gate_fraction']<=1


# A diagnosis reads an existing checkpoint and does not create optimizer updates.
# It must verify the model/data lineage, but it must not reject a checkpoint
# merely because the invocation omitted an old training-only CLI option or
# because the trainer source was updated to add diagnostic functionality.
READ_ONLY_PROVENANCE_FIELDS=(
    'schema','source_sha256','teacher_sha256',
    'representation','cutover_checkpoint_sha256','cutover_from_teacher_sha256','cutover_checkpoint_step',
    'decoder_head_decode_cap','global_decoder_untouched','Z_loss_weight',
)


def resolve_run_identity(saved:dict,expected:dict,mode:str):
    """Return the verbatim saved checkpoint identity and its harmless drift fields.

    Training keeps its original strict resume rule. Diagnose permits differences
    only in options irrelevant to loading/evaluating the saved weights. Source,
    parent, database, head decoder contract and the Z-loss contract remain
    pinned to their original identities. The checkpoint's own config is checked
    for exact equality with this saved identity after loading.
    """
    differences=sorted(k for k in set(saved)|set(expected)
                       if saved.get(k)!=expected.get(k))
    if lineage_compatible(saved,expected):
        return saved,[]
    if mode!='diagnose':
        raise ValueError('HAWK_DOVE_MILLION_RESUME_CONFIG_DRIFT: '+
                         ','.join(differences))
    required_drift=[k for k in READ_ONLY_PROVENANCE_FIELDS
                    if k not in saved or saved[k]!=expected.get(k)]
    if required_drift:
        raise ValueError('HAWK_DOVE_MILLION_DIAGNOSE_PROVENANCE_DRIFT: '+
                         ','.join(required_drift))
    if saved.get('schema')!=CHECKPOINT_SCHEMA or not isinstance(saved.get('seed'),int):
        raise ValueError('HAWK_DOVE_MILLION_DIAGNOSE_INVALID_SAVED_CONFIG')
    # Do not mutate or normalize the saved identity: policy sidecar hashes and
    # the actual .pt checkpoint refer to these exact serialized config values.
    return saved,differences


def parse_tier_overrides(values:list[str])->dict[str,float]:
    """Repeated --tier-gate T03=85, with strict names and percentages."""
    overrides={}
    for item in values:
        tier,sep,number=item.partition('=')
        if not sep or tier not in hd.TIERS or not number:
            raise ValueError('INVALID_TIER_GATE: expected T00=90 ... T08=90')
        if tier in overrides:raise ValueError('DUPLICATE_TIER_GATE:'+tier)
        try:percent=float(number)
        except ValueError as exc:raise ValueError('INVALID_TIER_GATE_PERCENT:'+item) from exc
        if not (0<percent<=100):raise ValueError('INVALID_TIER_GATE_PERCENT:'+item)
        overrides[tier]=percent
    return overrides


def validate_gate_map(raw:dict)->dict[str,float]:
    if not isinstance(raw,dict) or set(raw)!=set(hd.TIERS):
        raise ValueError('INVALID_PERSISTED_TIER_GATES')
    result={}
    for tier in hd.TIERS:
        value=raw[tier]
        if type(value) not in (int,float) or not 0<float(value)<=100:
            raise ValueError('INVALID_PERSISTED_TIER_GATE:'+tier)
        result[tier]=float(value)
    return result


def gate_policy_config_digest(ident:dict)->str:
    # Checkpoint identity is kept intact, including its legacy trainer SHA.
    return hashlib.sha256(json.dumps(ident,sort_keys=True,allow_nan=False).encode()).hexdigest()


def resolve_gate_policy(destination:Path,ident:dict,args):
    """Saved per-tier policy persists across runs; CLI modifies only named tiers.

    --gate-percent explicitly resets all tiers before tier overrides.
    No --gate-percent and no --tier-gate => keep previous policy on resume.
    """
    file=destination/'tier-gates.json'
    if file.exists():
        policy=json.loads(file.read_text(encoding='utf8'))
        if (policy.get('schema')!=GATE_POLICY_SCHEMA or
            policy.get('lineage_config_sha256')!=gate_policy_config_digest(ident)):
            raise ValueError('TIER_GATE_POLICY_LINEAGE_DRIFT')
        gate_percent=validate_gate_map(policy.get('gate_percent_by_tier'))
    else:
        gate_percent={tier:ident['gate_fraction']*100 for tier in hd.TIERS}
    if args.gate_percent is not None:
        gate_percent={tier:float(args.gate_percent) for tier in hd.TIERS}
    gate_percent.update(parse_tier_overrides(args.tier_gate))
    gate_percent=validate_gate_map(gate_percent)
    return file,{'schema':GATE_POLICY_SCHEMA,
                 'lineage_config_sha256':gate_policy_config_digest(ident),
                 'gate_percent_by_tier':gate_percent}


def parse_mask_overrides(values:list[str])->dict[str,dict[int,float]]:
    """Optional per-tier per-mask sampling weights. Defaults are all 1.

    --mask-weight T00:3=4 makes mask 3 four times as likely as each
    other T00 mask for T00-focused rows (not for development evaluation).
    """
    import math
    result={}
    for item in values:
        slot,sep,number=item.partition('=')
        tier,colon,mask_string=slot.partition(':')
        if not sep or not colon or tier not in hd.TIERS or not mask_string.isdigit():
            raise ValueError('INVALID_MASK_WEIGHT: expected T00:3=4')
        mask=int(mask_string)
        if mask>=2**len(hd.SLOTS[tier]):
            raise ValueError('INVALID_MASK_FOR_TIER:'+item)
        try:value=float(number)
        except ValueError as exc:raise ValueError('INVALID_MASK_WEIGHT_VALUE:'+item) from exc
        if not (math.isfinite(value) and 0<value<=100):
            raise ValueError('INVALID_MASK_WEIGHT_VALUE:'+item)
        if mask in result.setdefault(tier,{}):
            raise ValueError('DUPLICATE_MASK_WEIGHT:'+item)
        result[tier][mask]=value
    return result


def resolve_mask_policy(destination:Path,ident:dict,overrides:list[str],reset:bool=False):
    """Sampling policy is independent of the checkpoint and tier gates."""
    path=destination/'mask-weights.json'
    weights={tier:{str(mask):1.0 for mask,_ in hd.masks(tier)} for tier in hd.TIERS}
    if path.exists():
        saved=json.loads(path.read_text(encoding='utf8'))
        if (saved.get('schema')!=MASK_POLICY_SCHEMA or
            saved.get('lineage_config_sha256')!=gate_policy_config_digest(ident) or
            not isinstance(saved.get('weights_by_tier'),dict) or
            set(saved['weights_by_tier'])!=set(hd.TIERS)):
            raise ValueError('MASK_WEIGHT_POLICY_LINEAGE_DRIFT')
        for tier in hd.TIERS:
            entry=saved['weights_by_tier'][tier]
            if not isinstance(entry,dict) or set(entry)!=set(weights[tier]):
                raise ValueError('INVALID_PERSISTED_MASK_WEIGHTS:'+tier)
            for mask,value in entry.items():
                if type(value) not in (int,float) or not 0<float(value)<=100:
                    raise ValueError('INVALID_PERSISTED_MASK_WEIGHT:'+tier+':'+mask)
                weights[tier][mask]=float(value)
    if reset:
        weights={tier:{str(mask):1.0 for mask,_ in hd.masks(tier)} for tier in hd.TIERS}
    for tier,masks in parse_mask_overrides(overrides).items():
        for mask,value in masks.items():weights[tier][str(mask)]=value
    return path,{'schema':MASK_POLICY_SCHEMA,
                 'lineage_config_sha256':gate_policy_config_digest(ident),
                 'weights_by_tier':weights}


def diagnose_tier(model,db:Path,tier:str='T00'):
    """Every dev instance under every mask. No training, no test split."""
    from tools import nanojev_game_math_curriculum_v10_runtime as rt
    import statistics
    model['decoder'].eval();model['gamehead'].eval()
    rows=hd.read_rows(db,'dev',tier)
    if not rows:raise ValueError('EMPTY_DIAGNOSIS_TIER:'+tier)
    buckets={str(m):[] for m,_ in hd.masks(tier)}
    cases=[]
    for offset in range(0,len(rows),4):
        batch=rows[offset:offset+4]
        viable=[r for r in batch if rt.token_eligible(model,r['prompt'])]
        result=iter(predict(model,[r['prompt'] for r in viable]) if viable else [])
        for row in batch:
            if rt.token_eligible(model,row['prompt']):
                px,py=next(result)
                x,y=float(row['x']),float(row['y'])
                ex=abs(px-x)/max(1.,abs(x));ey=abs(py-y)/max(1.,abs(y))
                case={'id':row['case_id'],'instance':row['instance'],'mask':row['mask'],
                      'full_count':row['full_count'],'x':x,'y':y,'pred_x':px,'pred_y':py,
                      'relative_error_x':ex,'relative_error_y':ey,
                      'x_pass':ex<=.05,'y_pass':ey<=.05,
                      'joint_pass':ex<=.05 and ey<=.05,'prompt':row['prompt']}
            else:
                case={'id':row['case_id'],'instance':row['instance'],'mask':row['mask'],
                      'full_count':row['full_count'],'eligible':False,'prompt':row['prompt']}
            cases.append(case);buckets[str(row['mask'])].append(case)
    summary={}
    for mask,group in buckets.items():
        good=[c for c in group if c.get('eligible') is not False]
        n=len(group)
        failures=[c for c in good if not c['joint_pass']]
        summary[mask]={'mask':int(mask),'n':n,'eligible':len(good),
             'full_count':group[0]['full_count'] if group else None,
             'x_5pct':sum(c['x_pass'] for c in good)/n if n else 0,
             'y_5pct':sum(c['y_pass'] for c in good)/n if n else 0,
             'joint_5pct':sum(c['joint_pass'] for c in good)/n if n else 0,
             'x_failures':sum(not c['x_pass'] for c in good),
             'y_failures':sum(not c['y_pass'] for c in good),
             'joint_failures':len(failures),
             'mean_relative_error_x':statistics.mean(c['relative_error_x'] for c in good) if good else None,
             'mean_relative_error_y':statistics.mean(c['relative_error_y'] for c in good) if good else None,
             'median_relative_error_x':statistics.median(c['relative_error_x'] for c in good) if good else None,
             'median_relative_error_y':statistics.median(c['relative_error_y'] for c in good) if good else None,
             'examples':sorted(failures,key=lambda c:max(c['relative_error_x'],c['relative_error_y']),reverse=True)[:3]}
    order=sorted(summary,key=lambda m:(summary[m]['joint_5pct'],
                           -max(summary[m]['mean_relative_error_x'] or 0,
                                summary[m]['mean_relative_error_y'] or 0),int(m)))
    return {'event':'hawk_dove_mask_diagnosis','tier':tier,'split':'dev',
            'instances':len(set(r['instance'] for r in rows)),'presentations':len(rows),
            'masks':summary,'weakest_masks':order,
            'cases':cases,'no_training_performed':True,
            'note':'Full dev population, not the 48-row advancement subset. Does not evaluate the test split.'}

def predict(model,prompts:list[str]):
    """Decode without the legacy 2,000,000 cap. X=2,200,000 requires this.

    Original V3 attention head has exponent class 6 and can represent 2.2M;
    rt.batch_predict clamps to 2M. This override changes ONLY evaluation
    of this isolated experiment, not global registers or source checkpoint.
    """
    import torch
    from tools import nanojev_game_transform_experiment as ex
    with torch.inference_mode():
        out,_=ex.forward(model,prompts)
        xy=model['v3'].compose_logits(out['sign'],out['exponent'],
                                    out['fraction'],10_000_000)
    return [tuple(map(float,p)) for p in xy.detach().cpu().tolist()]


def passes(x:float,y:float,px:float,py:float):
    return (abs(px-x)/max(1.,abs(x))<=.05 and
            abs(py-y)/max(1.,abs(y))<=.05)


def eval_tier(model,rows:list[dict],batch_size:int=4)->dict:
    truths=[(float(r['x']),float(r['y'])) for r in rows]
    predictions=[]
    eligible=0
    from tools import nanojev_game_math_curriculum_v10_runtime as rt
    for i in range(0,len(rows),batch_size):
        batch=rows[i:i+batch_size]
        to_run=[r['prompt'] for r in batch if rt.token_eligible(model,r['prompt'])]
        if to_run:
            try: result=predict(model,to_run)
            except Exception as exc:raise RuntimeError('HAWK_DOVE_MILLION_PREDICTION_ERROR') from exc
        else:result=[]
        it=iter(result)
        for r in batch:
            if rt.token_eligible(model,r['prompt']):
                eligible+=1
                p=next(it);predictions.append(p)
            else:predictions.append(None)
    ok=0;xok=0;yok=0;zok=0;zn=0;worst=[]
    for r,(x,y),prediction in zip(rows,truths,predictions):
        if prediction is None:continue
        px,py=prediction
        a=abs(px-x)/max(1.,abs(x))<=.05
        b=abs(py-y)/max(1.,abs(y))<=.05
        xok+=a;yok+=b;ok+=a and b
        if r['z'] is not None:
            zn+=1
            zpred=('TIE' if abs(px-py)<1e-5 else 'HAWK' if px>py else 'DOVE')
            zok+=zpred==r['z']
        if not (a and b) and len(worst)<3:
            worst.append({'id':r['case_id'],'prompt':r['prompt'],
                          'true':[x,y],'pred':[px,py]})
    n=len(rows)
    by_depth={}
    for d in sorted({r['full_count'] for r in rows}):
        by_depth[str(d)]={'n':sum(r['full_count']==d for r in rows)}
    return {'logbar_depths':by_depth,'n':n,'eligible':eligible,'x_5pct':xok/n,'y_5pct':yok/n,
            'joint_5pct':ok/n,'z_diag_accuracy':(zok/zn if zn else None), 'failures':worst,'objective':'full_game_cumulative_xy'}


def rows_for_probe(db:Path,split:str,tier:str,n:int,seed:int):
    """Balance held-out numerical instances and LogBar mutation depths."""
    pool=hd.read_rows(db,split,tier)
    if not pool:raise ValueError('EMPTY_PROBE_TIER:'+tier)
    rng=random.Random(f'{seed}/{split}/{tier}')
    buckets={}
    for row in pool:
        buckets.setdefault(row['full_count'],{}).setdefault(row['instance'],[]).append(row)
    for groups in buckets.values():
        for examples in groups.values():rng.shuffle(examples)
    counts=sorted(buckets)
    rng.shuffle(counts)
    chosen=[];remaining=min(n,len(pool));used=set()
    # Stage A: at least one presentation at each LogBar depth.
    for depth in counts:
        groups=buckets[depth];keys=list(groups);rng.shuffle(keys)
        for k in keys:
            entry=groups[k][0]
            if entry['case_id'] not in used:
                chosen.append(entry);used.add(entry['case_id']);break
        if len(chosen)>=remaining:return chosen
    # Stage B: diversify instances within each depth before repeating one.
    candidate=[]
    for depth in counts:
        groups=buckets[depth];instances=list(groups);rng.shuffle(instances)
        for k in instances:
            candidate.extend(groups[k])
    rng.shuffle(candidate)
    for entry in candidate:
        if entry['case_id'] not in used:
            chosen.append(entry);used.add(entry['case_id'])
            if len(chosen)>=remaining:break
    return chosen


def probe(model,db,limit:int,seed:int,gates:dict[str,float],split:str='dev'):
    model['decoder'].eval();model['gamehead'].eval()
    scores={}
    for tier in hd.TIERS:
        rs=rows_for_probe(db,split,tier,limit,seed)
        scores[tier]=eval_tier(model,rs)
    first=next((t for t in hd.TIERS if scores[t]['joint_5pct']<gates[t] or
                scores[t]['eligible']!=scores[t]['n']),None)
    unique=set(gates.values())
    return {'scores':scores,'first_failing':first,
            'gate':next(iter(unique)) if len(unique)==1 else None,
            'gate_fraction_by_tier':dict(gates),
            'gate_percent_by_tier':{t:100*gates[t] for t in hd.TIERS},
            'split':split,'z_loss_weight':0,'no_logits_labels':True}


def choose_rows(db:Path,focus:str,step:int,seed:int,views:int,mask_weights_by_tier=None):
    pool=hd.read_rows(db,'train',focus)
    previous=hd.TIERS[:hd.TIERS.index(focus)]
    rng=random.Random(f'hd-train/{seed}/{step}')
    chosen=[]
    for i in range(views):
        tier=focus if i<max(1,views-1) or not previous else rng.choice(previous)
        candidates=pool if tier==focus else hd.read_rows(db,'train',tier)
        weights=(mask_weights_by_tier or {}).get(tier,{}) if tier==focus else {}
        if not weights or all(float(v)==1 for v in weights.values()):
            # Identical to the original sampler when weights are unset/all 1.
            chosen.append(candidates[rng.randrange(len(candidates))])
        else:
            by_mask={}
            for row in candidates:by_mask.setdefault(str(row['mask']),[]).append(row)
            masks=sorted(by_mask,key=int)
            selected=rng.choices(masks,weights=[weights.get(m,1) for m in masks],k=1)[0]
            members=by_mask[selected]
            chosen.append(members[rng.randrange(len(members))])
    return chosen


def run(args):
    info=teacher_info(args.teacher)
    if args.mode=='preflight':return {'status':'PASS','teacher':info,'model_loaded':False}
    import torch
    from tools import nanojev_game_math_curriculum_v10_runtime as rt
    from tools import nanojev_game_transform_experiment as ex
    destination=args.output.resolve();parent=args.parent.resolve();source=args.source.resolve()
    if not source.is_file():raise FileNotFoundError('SOURCE_CHECKPOINT_REQUIRED')
    if info['config'].get('schema')!=hd.SCHEMA_SCIENCE:
        raise ValueError('SCIENTIFIC_TEACHER_REQUIRED')
    if destination in (args.teacher.resolve().parent,source.parent):
        raise ValueError('ISOLATED_OUTPUT_REQUIRED')
    cfile=destination/'config.json'
    checkfile=destination/'latest.pt'
    expected=config(args,info)
    saved=json.loads(cfile.read_text(encoding='utf8'))
    # No live historical parent is consulted. The donor's parent digest is
    # retained in the new immutable lineage, but its file need not exist now.
    ident,differences=resolve_run_identity(saved,expected,args.mode)
    if differences and args.mode!='diagnose':
        raise ValueError('SCIENTIFIC_CONFIG_DRIFT:'+','.join(differences))
    diagnostic_config_differences=differences
    diagnostic_parent_status={'parent_used_for_diagnosis':False,
       'parent_sha256_recorded':ident.get('parent_sha256'),
       'parent_file_required':False}
    # Verify the config embedded within the checkpoint before initializing
    # expensive backbone weights. Diagnose does not restore optimizer/RNG.
    ck=None
    if checkfile.exists():
        ck=torch.load(checkfile,map_location='cpu',weights_only=True)
        if ck['schema']!=CHECKPOINT_SCHEMA or ck['config']!=ident:
            raise ValueError('HAWK_DOVE_MILLION_LINEAGE_DRIFT')
    else:
        raise FileNotFoundError('NO_SCIENTIFIC_CUTOVER_CHECKPOINT')
    policy_file,policy=resolve_gate_policy(destination,ident,args)
    mask_file,mask_policy=resolve_mask_policy(destination,ident,args.mask_weight,args.reset_mask_weights)
    gates={tier:policy['gate_percent_by_tier'][tier]/100. for tier in hd.TIERS}
    # The cutover checkpoint contains the entire trained decoder and game
    # operator head. The original historical parent is not loaded/re-hashed.
    model=ex.load_current(source,args.device,args.dtype,
                          args.allow_model_download,ident['seed'])
    decoder=model['decoder'];gamehead=model['gamehead']
    for p in gamehead.parameters():p.requires_grad_(False)
    optimizer=(torch.optim.AdamW(decoder.parameters(),lr=args.lr)
               if args.mode!='diagnose' else None)
    step=0;focus=hd.TIERS[0];streak=0
    if ck is not None:
        decoder.load_state_dict(ck['decoder'],strict=True)
        gamehead.load_state_dict(ck['game_operator_head'],strict=True)
        if args.mode=='train':optimizer.load_state_dict(ck['optimizer'])
        step=int(ck['step']);focus=ck['focus'];streak=int(ck['streak'])
        if args.mode=='train':
            torch.set_rng_state(ck['rng_cpu'])
            if args.device=='cuda' and ck.get('rng_cuda') is not None:
                torch.cuda.set_rng_state_all(ck['rng_cuda'])
    if args.mode=='train':
        if not cfile.exists():jwrite(cfile,ident)
        # Write after checkpoint verification, and before scheduling a new step.
        if not policy_file.exists() or json.loads(policy_file.read_text())!=policy:
            jwrite(policy_file,policy)
        if args.mask_weight or args.reset_mask_weights or mask_file.exists():
            if not mask_file.exists() or json.loads(mask_file.read_text())!=mask_policy:
                jwrite(mask_file,mask_policy)
    if args.mode=='diagnose':
        diagnosis=diagnose_tier(model,args.teacher,args.diagnose_tier)
        diagnosis.update({'step':step,'checkpoint':str(checkfile),
                          'diagnostic_config_differences':diagnostic_config_differences,
                          'parent_status':diagnostic_parent_status,
                          'mask_weights':mask_policy['weights_by_tier'][args.diagnose_tier],
                          'gate_percent':policy['gate_percent_by_tier'][args.diagnose_tier]})
        target=destination/f'diagnose-{args.diagnose_tier}-step-{step:06d}.json'
        jwrite(target,diagnosis)
        # Console: compact summary; full per-case predictions in saved JSON.
        return {k:v for k,v in diagnosis.items() if k!='cases'} | {'report_path':str(target)}
    report=probe(model,args.teacher,args.probe_per_tier,args.seed,gates,
                 split='test' if args.mode=='test' else 'dev')
    report.update({'event':'hawk_dove_million_baseline' if step==0 else 'hawk_dove_probe',
                   'step':step,'lineage':ident})
    if args.mode=='baseline':return report
    if args.mode in ('probe','test'):
        name='test' if args.mode=='test' else 'probe'
        jwrite(destination/f'{name}-step-{step:06d}.json',report)
        return report
    jwrite(destination/'baseline.json' if step==0 else destination/f'probe-step-{step:06d}.json',report)
    if report['first_failing'] is None:
        return {'status':'ALL_STAGES_PASS','step':step,'report':report}
    focus=report['first_failing'];streak=0
    if step>=args.steps:raise ValueError('TRAIN_STEPS_MUST_EXCEED_CURRENT_LOCAL_STEP')
    for target in range(step+1,args.steps+1):
        decoder.train();optimizer.zero_grad(set_to_none=True)
        examples=choose_rows(args.teacher,focus,target,args.seed,args.views,mask_policy['weights_by_tier'])
        loss_total=0.0
        for row in examples:
            if not rt.token_eligible(model,row['prompt']):
                raise ValueError('TRAIN_PROMPT_EXCEEDS_PARENT_TOKEN_WINDOW:'+row['tier'])
            output,_=ex.forward(model,[row['prompt']])
            truth=torch.tensor([[float(row['x']),float(row['y'])]],device=args.device,dtype=torch.float32)
            loss,_=model['v3'].objective(output,truth,model['base'],'attention')
            if not torch.isfinite(loss):raise FloatingPointError('NONFINITE_XY_LOSS')
            (loss/len(examples)).backward()
            loss_total+=float(loss.detach())/len(examples)
        torch.nn.utils.clip_grad_norm_(decoder.parameters(),1.)
        optimizer.step()
        if target%args.chunk_steps==0 or target==args.steps:
            report=probe(model,args.teacher,args.probe_per_tier,args.seed,gates)
            failing=report['first_failing']
            if failing==focus:streak=0
            elif failing is None:streak+=1
            elif hd.TIERS.index(failing)<hd.TIERS.index(focus):
                focus=failing;streak=0
            else:
                streak+=1
                if streak>=2:focus=failing;streak=0
            state={'schema':CHECKPOINT_SCHEMA,'config':ident,'step':target,
                   'focus':focus,'streak':streak,'decoder':decoder.state_dict(),
                   'game_operator_head':gamehead.state_dict(),
                   'optimizer':optimizer.state_dict(),'rng_cpu':torch.get_rng_state()}
            if args.device=='cuda':state['rng_cuda']=torch.cuda.get_rng_state_all()
            temp=destination/'latest.tmp.pt';torch.save(state,temp);temp.replace(checkfile)
            report.update({'event':'hawk_dove_million_xy_probe','step':target,'focus':focus,
                           'last_loss':loss_total,'streak':streak})
            jwrite(destination/f'probe-step-{target:06d}.json',report)
            print(json.dumps({'event':'hawk_dove_million_xy_probe','step':target,
                              'first_failing':failing,'focus':focus,
                              'joint_5pct':report['scores'][focus]['joint_5pct'],
                              'gate_percent':policy['gate_percent_by_tier'][focus],
                              'loss':loss_total,'checkpoint':str(checkfile)}),flush=True)
            if failing is None and streak>=2:break
    return {'status':'CHECKPOINT_SAVED','step':target,'checkpoint':str(checkfile),
            'first_failing':report['first_failing'],'focus':focus,'Z_loss_weight':0,
            'original_checkpoint_untouched':True}


def make_parser():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('mode',choices=('preflight','baseline','train','probe','test','diagnose'))
    p.add_argument('--teacher',type=Path,default=DEFAULT_TEACHER)
    p.add_argument('--source',type=Path,default=DEFAULT_SOURCE)
    p.add_argument('--parent',type=Path,default=DEFAULT_PARENT,
                   help='Legacy compatibility only; scientific v2 does not load the historical parent')
    p.add_argument('--output',type=Path,default=DEFAULT_OUT)
    p.add_argument('--steps',type=int,default=50,help='Total local optimizer steps')
    p.add_argument('--chunk-steps',type=int,default=25)
    p.add_argument('--views',type=int,default=4)
    p.add_argument('--probe-per-tier',type=int,default=48)
    p.add_argument('--gate-percent',type=float,default=None,
                   help='Explicitly reset ALL tier gates to this percent (default for new lineage: 50)')
    p.add_argument('--tier-gate',action='append',default=[],metavar='TIER=PERCENT',
                   help='Override one tier independently; repeat, e.g. --tier-gate T00=90 --tier-gate T01=75')
    p.add_argument('--mask-weight',action='append',default=[],metavar='TIER:MASK=WEIGHT',
                   help='Optional focused-tier train sampler weighting, e.g. T00:3=4; persisted in a sidecar')
    p.add_argument('--reset-mask-weights',action='store_true',
                   help='Reset all persisted mask sampling weights to 1 before applying overrides')
    p.add_argument('--diagnose-tier',choices=hd.TIERS,default='T00',
                   help='Read-only exhaustive dev mask breakdown for diagnose mode')
    p.add_argument('--lr',type=float,default=1e-5)
    p.add_argument('--seed',type=int,default=20261018)
    p.add_argument('--device',choices=('cuda','cpu'),default='cuda')
    p.add_argument('--dtype',choices=('float16','bfloat16','float32'),default='float16')
    p.add_argument('--allow-model-download',action='store_true')
    return p


def main(argv=None):
    args=make_parser().parse_args(argv)
    if (args.views<1 or args.chunk_steps<1 or args.probe_per_tier<8 or
        (args.gate_percent is not None and not 0<args.gate_percent<=100)):
        raise ValueError('INVALID_TRAINING_CONFIGURATION')
    parse_tier_overrides(args.tier_gate)
    parse_mask_overrides(args.mask_weight)
    print(json.dumps(run(args),sort_keys=True,default=str),flush=True)

if __name__=='__main__':main()
