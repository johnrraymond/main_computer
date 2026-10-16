#!/usr/bin/env python3
"""Read-only T00--T08 format and transformation probe for NanoJev V4/MC3.

Uses the actual Qwen capture and V4 decoder pathways from the existing integrated
experiment. This file never changes checkpoints, optimizers, or SQLite data.
A synthetic/mock smoke is limited to verifying the instrument, not model ability.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from contextlib import closing
import hashlib
import json
import math
from pathlib import Path
import sqlite3
from typing import Any

from tools import nanojev_game_transform_experiment as ex

VERSION = 'nanojev.game-transform-level-probe.v1'
LEVELS = {
 'T00': 'Native XXXXXX/YYYYYY registered_logbar; both values explicit',
 'T01': 'Same native registers; append game-transformation operator label',
 'T02': 'Same native registers; add original game/transformation context',
 'T03': 'Only XXXXXX known; calculate transformed YYYYYY',
 'T04': 'Only YYYYYY known; reconstruct original XXXXXX',
 'T05': 'Calculate original and transformed operands from formal game definition',
 'T06': 'Calculate operands from narrative instructions (JSON mathematics scaffold retained)',
 'T07': 'Calculate a real two-operator composition, distinct case subset',
 'T08': 'Narrative T06 on games entirely withheld from training',
}
MAIN_LEVELS = tuple(f'T{i:02d}' for i in range(7))

class UnsupportedNumericNative(ValueError):
 pass


def available_integer(row:dict,max_value:int=2_000_000)->bool:
 """Avoid silently int-casting scalar targets in the native V4 formatter."""
 return all(math.isfinite(float(row[k])) and abs(float(row[k]))<=max_value
            and float(row[k]).is_integer() for k in ('x','y'))


def select_balanced(rows:list[dict],limit:int,*,changed_only:bool=False)->list[dict]:
 """Greedy diversity in both *game* and *operator*, seeded only by canonical IDs."""
 if limit<1:raise ValueError('LIMIT_MUST_BE_POSITIVE')
 pool=[r for r in rows if not changed_only or r['x']!=r['y']]
 pool=sorted(pool,key=lambda r:r['id'])
 games=Counter();ops=Counter();pairs=Counter();chosen=[]
 while pool and len(chosen)<limit:
  ix=min(range(len(pool)),key=lambda i:(games[pool[i]['game']]+ops[pool[i]['operator']],
            games[pool[i]['game']],ops[pool[i]['operator']],
            pairs[(pool[i]['game'],pool[i]['operator'])],pool[i]['id']))
  item=pool.pop(ix);chosen.append(item)
  games[item['game']]+=1;ops[item['operator']]+=1
  pairs[(item['game'],item['operator'])]+=1
 return chosen


def selection(db:Path,limit:int,include_unchanged:bool=False)->dict[str,list[dict]]:
 with closing(sqlite3.connect(f'file:{Path(db).resolve().as_posix()}?mode=ro',uri=True)) as cn:
  subsets={s:ex.rows_for(cn,s) for s in ('dev','test','game_transfer')}
 # All levels retain the SAME canonical samples. Float-valued teacher targets
 # remain in T05--T08; native *integer-only* formats are explicitly inapplicable.
 return {s:select_balanced(items,limit,
                           changed_only=not include_unchanged)
         for s,items in subsets.items()}


def native_prompt(mix,base,item:dict,reverse:bool)->str:
 return mix.curriculum_prompt(int(item['x']),int(item['y']),
                              'written_sub','registered_logbar',reverse)


def insert_before_answer(prompt:str,text:str)->str:
 if not prompt.endswith('Answer:'):raise AssertionError('MISSING_NATIVE_ANSWER_SUFFIX')
 return prompt[:-len('Answer:')]+text+'\nAnswer:'


def render(level:str,item:dict,orientation:str,mix,base)->str:
 if level not in LEVELS:raise ValueError('UNKNOWN_PROBE_LEVEL:'+str(level))
 if level in ('T00','T01','T02','T03','T04') and not available_integer(item):
  raise UnsupportedNumericNative('NATIVE_V4_IS_INTEGER_ONLY')
 reverse=(orientation=='reordered')
 formal=item['forward']
 narrative=item['reordered']
 if level in ('T00','T01','T02'):
  native=native_prompt(mix,base,item,reverse)
  if level=='T00':return native
  if level=='T01':return insert_before_answer(native,'Transformation label: '+item['operator']+'.')
  # Keep every native register and operation line byte-identical to T00.
  # Prefix game information, then leave the native prompt as the final block.
  return formal.rsplit('\nAnswer:',1)[0]+'\n'+native
 if level in ('T03','T04'):
  # No call to a formatter on a hidden register. Do not fabricate a hidden value
  # just to include it in a LogBar. Known side retains the *exact* legacy syntax.
  known='XXXXXX' if level=='T03' else 'YYYYYY'
  hidden='YYYYYY' if level=='T03' else 'XXXXXX'
  value=int(item['x'] if level=='T03' else item['y'])
  known_line=f'{known} = {base.registers.number_repr(value)}'
  hidden_line=f'{hidden} = [compute from transformation]'
  lines=[known_line,hidden_line]
  if reverse:lines.reverse()
  return (formal.rsplit('\nAnswer:',1)[0]+'\n'+'\n'.join(lines)+
          '\noperation: Subtract YYYYYY from XXXXXX.\nAnswer:')
 if level=='T05':return narrative if reverse else formal
 if level in ('T06','T07','T08'):
  # T06/T08: narrative is the default, but test orientation consistency by
  # moving the identical sentence blocks rather than leaking the gold target.
  if level=='T07' and item['operator']!='compose_transformations':
   raise ValueError('T07_REQUIRES_COMPOSITION_CASE')
  text=narrative
  if not reverse:return text
  segments=text.rsplit('\n',2)
  if len(segments)>=3:
   return segments[-2]+'\n'+segments[0]+'\n'+segments[-1]
  return formal  # equivalent target, distinct symbolic presentation
 raise AssertionError(level)


def token_check(tokenizer,prompt:str,max_tokens:int)->dict:
 ids=tokenizer(prompt,add_special_tokens=False)['input_ids']
 n=len(ids)
 return {'tokens':n,'status':('ok' if 4<=n<=max_tokens else 'token_limit')}


def error_components(pred:list[float],item:dict,threshold:float)->dict:
 truth=(float(item['x']),float(item['y']))
 residual=[abs(float(p)-t)/max(1.,abs(t)) for p,t in zip(pred,truth)]
 return {'x':residual[0]<=threshold,'y':residual[1]<=threshold,
         'joint':max(residual)<=threshold,'x_relative_error':residual[0],
         'y_relative_error':residual[1]}


def score(rows:list[dict],level:str,threshold:float=.05)->dict:
 """Strict paired scores; overlength and inference errors never become zeros."""
 bycase=defaultdict(dict)
 for row in rows:bycase[row['case_id']][row['orientation']]=row
 n=0;count=Counter();one=Counter();changed=Counter();pergame=defaultdict(list);perop=defaultdict(list)
 invalid=Counter();relative=[];predictions=[]
 for cid,orients in bycase.items():
  if not all(q in orients for q in ('forward','reordered')):
   invalid['missing_orientation']+=1;continue
  a,b=orients['forward'],orients['reordered']
  if a['status']!='ok' or b['status']!='ok':
   invalid[a['status'] if a['status']!='ok' else b['status']]+=1;continue
  item={'x':a['truth']['x'],'y':a['truth']['y']}
  hits=[]
  for r in (a,b):
   hit=error_components(r['prediction'],item,threshold);hits.append(hit)
   for side in ('x','y','joint'):one[f"{r['orientation']}_{side}"]+=int(hit[side])
   relative.extend((hit['x_relative_error'],hit['y_relative_error']))
  opboth=(a['predicted_operator']==a['operator']==b['predicted_operator'])
  both={key:all(q[key] for q in hits) for key in ('x','y','joint')}
  for key,val in both.items():count['paired_'+key]+=int(val)
  count['operator_paired']+=int(opboth)
  # Joint accuracy must not be confused with independent operator recognition.
  count['joint_and_operator']+=int(both['joint'] and opboth)
  ischanged=(a['truth']['x']!=a['truth']['y'])
  changed['changed' if ischanged else 'unchanged']+=1
  n+=1
  pergame[a['game']].append(cid);perop[a['operator']].append(cid)
  predictions.append((a['game'],a['operator'],both['joint'],opboth,ischanged))
 def part(predicate):
  selected=[p for p in predictions if predicate(p)]
  return {'n':len(selected),'paired_joint':round(sum(p[2] for p in selected)/len(selected),6) if selected else None,
          'operator_paired':round(sum(p[3] for p in selected)/len(selected),6) if selected else None}
 output={'level':level,'threshold':threshold,'n':n,'cases_attempted':len(bycase),
    'unscorable':dict(sorted(invalid.items())),
    'paired':{key:round(count['paired_'+key]/n,6) if n else None for key in ('x','y','joint')},
    'operator_paired':round(count['operator_paired']/n,6) if n else None,
    'joint_and_operator':round(count['joint_and_operator']/n,6) if n else None,
    'forward_joint':round(one['forward_joint']/n,6) if n else None,
    'reordered_joint':round(one['reordered_joint']/n,6) if n else None,
    'mean_relative_error':round(sum(relative)/len(relative),6) if relative else None,
    'target_change':{'changed':part(lambda p:p[4]),'unchanged':part(lambda p:not p[4])},
    'per_game':{g:part(lambda p,g=g:p[0]==g) for g in sorted(pergame)},
    'per_operator':{op:part(lambda p,op=op:p[1]==op) for op in sorted(perop)}}
 return output


def identity(source:Path,experimental:Path,database:Path,config:Path|None)->dict:
 # READ, never create or alter identities. If experiment config is provided,
 # verify that its sealed source/DB hashes match the supplied artifacts.
 source=source.resolve();experimental=experimental.resolve();database=database.resolve()
 if not all(p.is_file() for p in (source,experimental,database)):
  raise FileNotFoundError('SOURCE_EXPERIMENT_CHECKPOINT_AND_DATABASE_REQUIRED')
 if source==experimental:raise ValueError('CHECKPOINTS_MUST_BE_DISTINCT')
 sha={'source_checkpoint_sha256':ex.filehash(source),
      'experimental_checkpoint_sha256':ex.filehash(experimental),
      'database_sha256':ex.filehash(database)}
 if config:
  c=json.loads(config.read_text(encoding='utf8'))
  if c.get('source_sha256')!=sha['source_checkpoint_sha256'] or c.get('database_sha256')!=sha['database_sha256']:
   raise RuntimeError('EXPERIMENT_SOURCE_DATABASE_DRIFT')
  sha['experiment_identity']=ex.digest(c)
 return sha


def compare(model,subset:dict[str,list[dict]],compositions:dict[str,list[dict]],*,emit_rows:bool=True)->dict:
 tokenizer=model['tokenizer'];max_tokens=int(model['config']['max_prompt_tokens'])
 rows=[];summary={}
 # Explicit T07 dedicated to composition; T08 dedicated to held-out games.
 work=[]
 for split,items in subset.items():
  for level in MAIN_LEVELS:
   work.append((split,level,items))
  work.append((split,'T07',compositions[split]))
 work.append(('game_transfer','T08',subset['game_transfer']))
 for split,level,items in work:
  r=[]
  for item in items:
   for orientation in ('forward','reordered'):
    try:
     prompt=render(level,item,orientation,model['mix'],model['base'])
    except UnsupportedNumericNative:
     prompt=None
    validity=(token_check(tokenizer,prompt,max_tokens) if prompt is not None
              else {'tokens':None,'status':'unsupported_noninteger_native'})
    record={'level':level,'split':split,'case_id':item['id'],'game':item['game'],
            'operator':item['operator'],'orientation':orientation,
            'truth':{'x':item['x'],'y':item['y']},'scale':1.,
            'prompt_sha256':(hashlib.sha256(prompt.encode('utf8')).hexdigest() if prompt is not None else None),
            'tokens':validity['tokens'],'status':validity['status']}
    if validity['status']=='ok':
     try:
      xy,op=ex.predict_one(model,prompt)
      record.update(prediction=xy,predicted_operator=op)
     except Exception as exc:
      # Missing/wrong token setup or CUDA runtime failures must NOT be masked
      # as learning errors or zero-accuracy predictions.
      raise RuntimeError(f'PROBE_INFERENCE_FAILED {level} {split} {item["id"]} {orientation}') from exc
    r.append(record)
  five=score(r,level,.05);ten=score(r,level,.10)
  summary[f'{split}/{level}']={'n_selected':len(items),'5pct':five,'10pct':ten}
  rows.extend(r)
 return {'summary':summary,'rows':rows if emit_rows else None,
         'max_prompt_tokens':max_tokens}


def run(args):
 # No DB writes, no optimizer, no checkpoint state writes. Reports only --report.
 db=args.database.expanduser().resolve();src=args.source_checkpoint.expanduser().resolve()
 exp=args.experimental_checkpoint.expanduser().resolve()
 cfg=args.experiment_config.expanduser().resolve() if args.experiment_config else None
 ids=identity(src,exp,db,cfg)
 with closing(sqlite3.connect(f'file:{db.as_posix()}?mode=ro',uri=True)) as cn:
  ex.inspect_database(cn)
 subset=selection(db,args.limit_per_split,args.include_unchanged)
 with closing(sqlite3.connect(f'file:{db.as_posix()}?mode=ro',uri=True)) as cn:
  compositions={split:select_balanced([r for r in ex.rows_for(cn,split)
      if r['operator']=='compose_transformations'],args.limit_per_split,
      changed_only=not args.include_unchanged) for split in subset}
 if not all(subset.values()) or not all(compositions.values()):raise ValueError('EMPTY_EVALUATION_SPLIT')
 # Explicit case manifest permits comparing the exact same prompts across models.
 manifest={s:[{'case_id':x['id'],'game':x['game'],'operator':x['operator'],
               'x':x['x'],'y':x['y']} for x in items] for s,items in subset.items()}
 result={'schema':VERSION,'oracle_database':str(db),'identity':ids,
         'levels':LEVELS,'samples':manifest,'sample_count':{k:len(v) for k,v in subset.items()},
         'qualification_min_cases':args.qualification_min_cases,
         'qualification_warning':'Under-minimum or overlength rungs are diagnostic, never qualified.',
         'composition_manifest':{s:[x['id'] for x in rows] for s,rows in compositions.items()},
         'checkpoints':{}}
 for name,path in (('source',src),('experimental',exp)):
  model=ex.load_current(src,args.device,args.dtype,args.allow_model_download,args.seed)
  if name=='experimental':
   cp=model['torch'].load(exp,map_location='cpu',weights_only=True)
   if cp.get('schema')!=ex.SCHEMA or 'decoder' not in cp or 'game_operator_head' not in cp:
    raise RuntimeError('INVALID_EXPERIMENT_CHECKPOINT')
   if cfg and cp.get('identity')!=ids['experiment_identity']:
    raise RuntimeError('EXPERIMENT_IDENTITY_MISMATCH')
   model['decoder'].load_state_dict(cp['decoder'],strict=True)
   model['gamehead'].load_state_dict(cp['game_operator_head'],strict=True)
   step=cp.get('step')
  else:step=model['source'].get('step')
  model['decoder'].eval();model['gamehead'].eval()
  with model['torch'].inference_mode():
   metrics=compare(model,subset,compositions,emit_rows=True)
  for level_key,entry in metrics['summary'].items():
   for tolerance in ('5pct','10pct'):
    v=entry[tolerance];n=v['n'];whole=(n==entry['n_selected'])
    v['numerical_qualified']=(n>=args.qualification_min_cases and whole and
           v['paired']['joint'] is not None and v['paired']['joint']>=.8)
    v['operator_qualified']=(n>=args.qualification_min_cases and whole and
           v['operator_paired'] is not None and v['operator_paired']>=.8)
    v['combined_qualified']=v['numerical_qualified'] and v['operator_qualified']
  result['checkpoints'][name]={'checkpoint_path':str(path),'step':step,**metrics}
  # Free GPU allocations between independently loaded lineages.
  framework=model['torch'];del model
  if args.device=='cuda':framework.cuda.empty_cache()
 result['delta_experimental_minus_source']={}
 for key in result['checkpoints']['source']['summary']:
  left=result['checkpoints']['source']['summary'][key];right=result['checkpoints']['experimental']['summary'][key]
  result['delta_experimental_minus_source'][key]={}
  for tolerance in ('5pct','10pct'):
   a=left[tolerance];b=right[tolerance]
   result['delta_experimental_minus_source'][key][tolerance]={name:(round(b[name]-a[name],6) if a[name] is not None and b[name] is not None else None)
      for name in ('operator_paired',)}
   for key2 in ('x','y','joint'):
    av=a['paired'][key2];bv=b['paired'][key2]
    result['delta_experimental_minus_source'][key][tolerance]['paired_'+key2]=(round(bv-av,6) if av is not None and bv is not None else None)
 if any(ex.filehash(path)!=expected for path,expected in ((src,ids['source_checkpoint_sha256']),
         (exp,ids['experimental_checkpoint_sha256']),(db,ids['database_sha256']))):
  raise RuntimeError('READ_ONLY_INPUT_HASH_CHANGED')
 if args.report:
  dest=args.report.expanduser().resolve()
  if dest in (src,exp,db) or dest==cfg:raise ValueError('REPORT_WOULD_OVERWRITE_INPUT')
  ex.write_json(dest,result)
 # Console summary always compact; full per-case detail optional report.
 short={name:{key:{'n':v['5pct']['n'],'paired5':v['5pct']['paired']['joint'],
                   'paired10':v['10pct']['paired']['joint'],
                   'operator_paired':v['5pct']['operator_paired'],
                   'unscorable':v['5pct']['unscorable']}
               for key,v in payload['summary'].items()}
        for name,payload in result['checkpoints'].items()}
 print(ex.canon({'event':'game_transform_level_probe_complete','source_step':result['checkpoints']['source']['step'],
                 'experimental_step':result['checkpoints']['experimental']['step'],
                 'identity':ids,'scores':short,'report':str(args.report) if args.report else None}),flush=True)
 return result


def parser():
 p=argparse.ArgumentParser(description=__doc__)
 p.add_argument('--database',type=Path,default=Path('runtime/game-transformation-experiment/game_transformations.sqlite3'))
 p.add_argument('--source-checkpoint',type=Path,default=Path('runtime/game-transform-experiment/source-v4.pt'))
 p.add_argument('--experimental-checkpoint',type=Path,default=Path.home()/'NanoJev'/'runs'/'game_transform_experiment_v1'/'latest.pt')
 p.add_argument('--experiment-config',type=Path,default=Path.home()/'NanoJev'/'runs'/'game_transform_experiment_v1'/'experiment-config.json')
 p.add_argument('--limit-per-split',type=int,default=12)
 p.add_argument('--qualification-min-cases',type=int,default=30)
 p.add_argument('--include-unchanged',action='store_true',help='Include cases whose observed projection is an identity at selected coordinate')
 p.add_argument('--report',type=Path,help='Optional JSON report; no other output files written')
 p.add_argument('--seed',type=int,default=20261015)
 p.add_argument('--device',choices=('cpu','cuda'),default='cuda')
 p.add_argument('--dtype',choices=('float16','bfloat16','float32'),default='float16')
 p.add_argument('--allow-model-download',action='store_true')
 return p

if __name__=='__main__':raise SystemExit(0 if run(parser().parse_args()) else 1)
