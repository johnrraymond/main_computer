"""Isolated NanoJev X/Y learning experiment on pure-Lisp Hawk–Dove prompts.

Loads frozen Qwen + pre-existing decoder through the installed V10 runtime. Only
isolated decoder weights train; original checkpoints, sources and V10 DB remain
untouched. ARGMAX/Z is a read-only diagnostic: no Z gradient is applied.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from contextlib import closing
import random

from tools import nanojev_hawk_dove_lisp as hd

CHECKPOINT_SCHEMA='nanojev.hawk-dove.pure-lisp.training.v1'
DEFAULT_PARENT=Path.home()/'NanoJev/runs/game_math_xy_v10_operand_audited/latest.pt'
DEFAULT_SOURCE=Path('runtime/game-transform-experiment/source-v4.pt')
DEFAULT_TEACHER=Path('runtime/hawk-dove-pure-lisp/teacher.sqlite3')
DEFAULT_OUT=Path.home()/'NanoJev/runs/hawk_dove_pure_lisp_v1'


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
    import sqlite3
    with closing(sqlite3.connect(f'file:{db.resolve().as_posix()}?mode=ro',uri=True)) as cn:
        row=cn.execute("SELECT value FROM manifest WHERE key='config'").fetchone()
        if not row:raise ValueError('HAWK_DOVE_TEACHER_MANIFEST_MISSING')
        config=json.loads(row[0]);
        if config['schema']!=hd.SCHEMA:raise ValueError('HAWK_DOVE_TEACHER_SCHEMA_MISMATCH')
        groups=dict(cn.execute('SELECT split,COUNT(*) FROM cases GROUP BY split'))
        tiers={t:n for t,n in cn.execute('SELECT tier,COUNT(*) FROM cases GROUP BY tier')}
        if set(tiers)!=set(hd.TIERS) or not all(k in groups for k in ('train','dev','test')):
            raise ValueError('HAWK_DOVE_INCOMPLETE_TRAINING_SPLITS')
        # Minimal content regression, not just a row count. No plaintext payoffs/registrations.
        for (prompt,) in cn.execute('SELECT prompt FROM cases'):
            if 'INPUT_A' in prompt or 'INPUT_B' in prompt or 'Add(' in prompt or 'With(' in prompt:
                raise ValueError('LEGACY_PROMPT_FORMAT_IN_TEACHER')
    return {'config':config,'groups':groups,'tiers':tiers,'sha256':sha(db)}


def config(args,info):
    return {'schema':CHECKPOINT_SCHEMA,'source_sha256':sha(args.source),
            'parent_sha256':sha(args.parent),'teacher_sha256':info['sha256'],
            'trainer_sha256':sha(Path(__file__)),
            'gate_fraction':args.gate_percent/100.,'probe_per_tier':args.probe_per_tier,
            'lr':args.lr,'seed':args.seed,'views':args.views,
            'device':args.device,'dtype':args.dtype,'Z_loss_weight':0}


def predict(model,prompts:list[str]):
    from tools import nanojev_game_math_curriculum_v10_runtime as rt
    return rt.batch_predict(model,prompts)


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
            except Exception as exc:raise RuntimeError('HAWK_DOVE_PREDICTION_ERROR') from exc
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
    return {'n':n,'eligible':eligible,'x_5pct':xok/n,'y_5pct':yok/n,
            'joint_5pct':ok/n,'z_diag_accuracy':(zok/zn if zn else None), 'failures':worst,'objective':rows[0]['objective'] if rows else ''}


def rows_for_probe(db:Path,split:str,tier:str,n:int,seed:int):
    pool=hd.read_rows(db,split,tier)
    if not pool:raise ValueError('EMPTY_PROBE_TIER:'+tier)
    # Select across presentation variants AND distinct probability instances.
    # In particular T00/T01 must include BOTH payoff columns, and T03 must
    # exercise all 16 equivalent arithmetic/display rearrangements.
    rng=random.Random(f'{seed}/{split}/{tier}')
    by_view={}
    for row in pool:
        key=tuple(map(int,row['case_id'].split('/')[-2:]))
        by_view.setdefault(key,[]).append(row)
    for group in by_view.values():rng.shuffle(group)
    views=list(by_view);views.sort();rng.shuffle(views)
    chosen=[];at=0
    while len(chosen)<min(n,len(pool)):
        added=False
        for key in views:
            items=by_view[key]
            if at<len(items):chosen.append(items[at]);added=True
            if len(chosen)>=min(n,len(pool)):break
        if not added:break
        at+=1
    return chosen


def probe(model,db,limit:int,seed:int,gate:float,split:str='dev'):
    model['decoder'].eval();model['gamehead'].eval()
    scores={}
    for tier in hd.TIERS:
        rs=rows_for_probe(db,split,tier,limit,seed)
        scores[tier]=eval_tier(model,rs)
    first=next((t for t in hd.TIERS if scores[t]['joint_5pct']<gate or
                scores[t]['eligible']!=scores[t]['n']),None)
    return {'scores':scores,'first_failing':first,'gate':gate,'split':split,
            'z_loss_weight':0,'no_logits_labels':True}


def choose_rows(db:Path,focus:str,step:int,seed:int,views:int):
    pool=hd.read_rows(db,'train',focus)
    previous=hd.TIERS[:hd.TIERS.index(focus)]
    rng=random.Random(f'hd-train/{seed}/{step}')
    chosen=[]
    for i in range(views):
        tier=focus if i<max(1,views-1) or not previous else rng.choice(previous)
        candidates=pool if tier==focus else hd.read_rows(db,'train',tier)
        chosen.append(candidates[rng.randrange(len(candidates))])
    return chosen


def run(args):
    info=teacher_info(args.teacher)
    if args.mode=='preflight':return {'status':'PASS','teacher':info,'model_loaded':False}
    import torch
    from tools import nanojev_game_math_curriculum_v10_runtime as rt
    from tools import nanojev_game_transform_experiment as ex
    destination=args.output.resolve();parent=args.parent.resolve();source=args.source.resolve()
    if not parent.is_file() or not source.is_file():raise FileNotFoundError('SOURCE_AND_PARENT_CHECKPOINT_REQUIRED')
    if destination in (args.teacher.resolve().parent,parent.parent,source.parent):
        raise ValueError('ISOLATED_OUTPUT_REQUIRED')
    ident=config(args,info)
    if args.mode=='train':
        destination.mkdir(parents=True,exist_ok=True)
        cfile=destination/'config.json'
        if cfile.exists() and json.loads(cfile.read_text())!=ident:
            raise ValueError('HAWK_DOVE_RESUME_CONFIG_DRIFT')
        if not cfile.exists():jwrite(cfile,ident)
    model,_,_=rt.load_model(source,parent,device=args.device,dtype=args.dtype,
                             seed=args.seed,allow_model_download=args.allow_model_download)
    decoder=model['decoder'];gamehead=model['gamehead']
    for p in gamehead.parameters():p.requires_grad_(False)
    optimizer=torch.optim.AdamW(decoder.parameters(),lr=args.lr)
    checkfile=destination/'latest.pt';step=0;focus=hd.TIERS[0];streak=0
    if checkfile.exists():
        ck=torch.load(checkfile,map_location='cpu',weights_only=True)
        if ck['schema']!=CHECKPOINT_SCHEMA or ck['config']!=ident:
            raise ValueError('HAWK_DOVE_LINEAGE_DRIFT')
        decoder.load_state_dict(ck['decoder'],strict=True)
        gamehead.load_state_dict(ck['game_operator_head'],strict=True)
        if args.mode=='train':optimizer.load_state_dict(ck['optimizer'])
        step=int(ck['step']);focus=ck['focus'];streak=int(ck['streak'])
        if args.mode=='train':
            torch.set_rng_state(ck['rng_cpu'])
            if args.device=='cuda' and ck.get('rng_cuda') is not None:
                torch.cuda.set_rng_state_all(ck['rng_cuda'])
    elif args.mode in ('probe','test'):
        raise FileNotFoundError('NO_LOCAL_HAWK_DOVE_CHECKPOINT_TO_PROBE')
    report=probe(model,args.teacher,args.probe_per_tier,args.seed,ident['gate_fraction'],
                 split='test' if args.mode=='test' else 'dev')
    report.update({'event':'hawk_dove_baseline' if step==0 else 'hawk_dove_probe',
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
        examples=choose_rows(args.teacher,focus,target,args.seed,args.views)
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
            report=probe(model,args.teacher,args.probe_per_tier,args.seed,ident['gate_fraction'])
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
            report.update({'event':'hawk_dove_xy_probe','step':target,'focus':focus,
                           'last_loss':loss_total,'streak':streak})
            jwrite(destination/f'probe-step-{target:06d}.json',report)
            print(json.dumps({'event':'hawk_dove_xy_probe','step':target,
                              'first_failing':failing,'focus':focus,
                              'joint_5pct':report['scores'][focus]['joint_5pct'],
                              'loss':loss_total,'checkpoint':str(checkfile)}),flush=True)
            if failing is None and streak>=2:break
    return {'status':'CHECKPOINT_SAVED','step':target,'checkpoint':str(checkfile),
            'first_failing':report['first_failing'],'focus':focus,'Z_loss_weight':0,
            'original_checkpoint_untouched':True}


def make_parser():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('mode',choices=('preflight','baseline','train','probe','test'))
    p.add_argument('--teacher',type=Path,default=DEFAULT_TEACHER)
    p.add_argument('--source',type=Path,default=DEFAULT_SOURCE)
    p.add_argument('--parent',type=Path,default=DEFAULT_PARENT)
    p.add_argument('--output',type=Path,default=DEFAULT_OUT)
    p.add_argument('--steps',type=int,default=50,help='Total local optimizer steps')
    p.add_argument('--chunk-steps',type=int,default=25)
    p.add_argument('--views',type=int,default=4)
    p.add_argument('--probe-per-tier',type=int,default=24)
    p.add_argument('--gate-percent',type=float,default=50.)
    p.add_argument('--lr',type=float,default=1e-5)
    p.add_argument('--seed',type=int,default=20261018)
    p.add_argument('--device',choices=('cuda','cpu'),default='cuda')
    p.add_argument('--dtype',choices=('float16','bfloat16','float32'),default='float16')
    p.add_argument('--allow-model-download',action='store_true')
    return p


def main(argv=None):
    args=make_parser().parse_args(argv)
    if args.views<1 or args.chunk_steps<1 or args.probe_per_tier<8 or not (0<args.gate_percent<=100):
        raise ValueError('INVALID_TRAINING_CONFIGURATION')
    print(json.dumps(run(args),sort_keys=True,default=str),flush=True)

if __name__=='__main__':main()
