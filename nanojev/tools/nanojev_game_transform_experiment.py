#!/usr/bin/env python3
"""Source-generated game-transformation curriculum and real NanoJev checkpoint experiment.

The SQLite teacher is never exposed to model inference. The existing frozen-Qwen
attention decoder is loaded from a CURRENT v4 checkpoint; an isolated copy is
optimized and evaluated on genuinely withheld instances and whole game IDs.
No active NanoJev run/checkpoint is written by this script.
"""
from __future__ import annotations
import argparse
from collections import defaultdict
from contextlib import closing
import hashlib
import json
import math
from pathlib import Path
import random
import sqlite3
import sys
from types import SimpleNamespace
from typing import Any

from tools import nanojev_game_transformation_library as mathlib

SCHEMA = 'nanojev.game-transform-experiment.v1'
RESERVED_GAMES = frozenset(('chicken','captain_ship_defense','bayes_posterior',
                           'routing_delay','pursuit_gap'))
# Exact full-minibatch quotas; no time/cycle-based transitions. One legacy example
# is retained per optimizer update. Multi-orientation supervision uses the same case.
DEFAULT_MIX = {'positive_affine':2,'permute_row':1,'permute_column':1,
               'compose_transformations':1,'scalar_parameter_shift':2,'retention':1}
# All teachers are source-defined in the MC3 catalog and source game models.
ALL_OPERATORS = tuple(sorted({x[0] for x in mathlib.TRANSFORMS if x[5]=='executable'
                              and x[0] in ('swap_players','permute_row','permute_column',
                                 'positive_affine','shared_payoff_shift','change_one_payoff',
                                 'reverse_payoff_sign','compose_transformations',
                                 'scalar_parameter_shift')}))
OBJECTIVE = {
 'schema':SCHEMA,'teacher':'source-generated verified MC3 mathematical oracles',
 'executable_now':{'XY':'existing sign/exponent/mantissa objective on source and transformed values',
     'operator':'cross entropy of independent game-operator classifier from prompt features',
     'orientation':'forward and reordered share identical gold; independently scored',
     'retention':'legacy numerical tasks sampled from preexisting v4 source distribution'},
 'proposed_not_yet_executable':{'payoff_tensor':'structured tensor prediction',
      'equilibrium_set':'set-equivalent equilibrium prediction',
      'game_family_inference':'unseen game-family structural generalization'},
 'inference_information':'transformation name, source game parameters/payoffs, operator definition, selected payoff coordinate; never computed target',
 'thresholds':[0.05,0.10],
 'evaluation':['unseen parameter instances','unseen named games','paired presentation accuracy',
               'untrained operators separately reported','legacy retention'],
 'mix_policy':{'type':'fixed exact quotas','counts':DEFAULT_MIX,'advance_by_step':False},
 'claims':'Training a scalar projection is NOT proof of learning the complete game or equilibrium structure.'}

def canon(value:Any)->str:
 return json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False,ensure_ascii=False)

def digest(v:Any)->str:
 return hashlib.sha256(canon(v).encode('utf8')).hexdigest()

def filehash(path:Path)->str:
 h=hashlib.sha256()
 with Path(path).open('rb') as f:
  for part in iter(lambda:f.read(1<<20),b''): h.update(part)
 return h.hexdigest()

def parse_mix(spec:str|None)->dict[str,int]:
 if spec is None: return dict(DEFAULT_MIX)
 counts={}
 for entry in spec.split(','):
  try: name,count=entry.rsplit('=',1);name=name.strip();count=int(count)
  except (TypeError,ValueError) as exc:raise ValueError('MIX_EXPECTS_op=count,...') from exc
  if name not in ALL_OPERATORS+('retention',) or name in counts or count<0:
   raise ValueError('MIX_UNKNOWN_DUPLICATE_OR_NEGATIVE:'+name)
  if count: counts[name]=count
 if not counts or 'retention' not in counts:raise ValueError('MIX_NEEDS_RETENTION')
 return dict(sorted(counts.items()))

def game_split(raw_split:str, game_id:str)->str:
 if game_id in RESERVED_GAMES:return 'game_transfer'
 return raw_split

def make_projections(cn:sqlite3.Connection)->dict:
 """Seal exact source/transformed scalar targets and TWO target-free presentations.

 Projection coordinates are selected by case ID, not the target; action permutations
 genuinely change the payoff at a fixed coordinate. For scalar oracles X is the
 original function value, Y is f(modified_parameters).
 """
 cn.executescript('''
 CREATE TABLE experiment_objectives(objective_id TEXT PRIMARY KEY, full_json TEXT NOT NULL);
 CREATE TABLE experiment_cases(case_id TEXT PRIMARY KEY,source_case_id TEXT NOT NULL REFERENCES transformation_cases(case_id),
  game_id TEXT NOT NULL REFERENCES games(game_id), family_id TEXT NOT NULL REFERENCES families(family_id),
  operator TEXT NOT NULL, split TEXT NOT NULL CHECK(split IN ('train','dev','test','game_transfer')),
  x REAL NOT NULL,y REAL NOT NULL,scale REAL NOT NULL,oracle_status TEXT NOT NULL CHECK(oracle_status='verified'));
 CREATE TABLE experiment_prompts(case_id TEXT NOT NULL REFERENCES experiment_cases(case_id),
  orientation TEXT NOT NULL CHECK(orientation IN ('forward','reordered')),
  prompt TEXT NOT NULL, PRIMARY KEY(case_id,orientation));
 CREATE TABLE experiment_mix(operator TEXT PRIMARY KEY,count INTEGER NOT NULL CHECK(count>0));
 CREATE INDEX experiment_cases_by_split ON experiment_cases(split,operator,game_id);
 ''')
 cn.execute('INSERT INTO experiment_objectives VALUES (?,?)',('game_transform_experiment_v1',canon(OBJECTIVE)))
 # Preserve the source-generated patch's distinct game and presentation catalog
 # WITHOUT importing missing captain modules or treating private coefficients
 # as valid mathematical labels.
 extra_path=Path(__file__).with_name('nanojev_source_game_catalog_v1.json')
 extra=json.loads(extra_path.read_text(encoding='utf8'))
 cn.executescript('''
 CREATE TABLE integrated_source_game_catalog(game_id TEXT PRIMARY KEY, source_record_json TEXT NOT NULL,
     mc3_game_id TEXT REFERENCES games(game_id), training_status TEXT NOT NULL);
 CREATE TABLE integrated_source_transform_catalog(source_transform_id TEXT PRIMARY KEY,source_record_json TEXT NOT NULL);
 CREATE TABLE integrated_source_dictionary(term TEXT PRIMARY KEY,source_record_json TEXT NOT NULL);
 CREATE TABLE integrated_source_objective(key TEXT PRIMARY KEY,objective_json TEXT NOT NULL);
 ''')
 seen_games={r[0] for r in cn.execute('SELECT game_id FROM games')}
 for row in extra['games']:
  gid=row[0]
  cn.execute('INSERT INTO integrated_source_game_catalog VALUES (?,?,?,?)',
             (gid,canon(row),gid if gid in seen_games else None,'catalog_only_unverified_teacher'))
 for row in extra['transforms']:
  cn.execute('INSERT INTO integrated_source_transform_catalog VALUES (?,?)',(row[0],canon(row)))
 for row in extra['dictionary']:
  cn.execute('INSERT OR REPLACE INTO integrated_source_dictionary VALUES (?,?)',(row[0],canon(row)))
 cn.execute('INSERT INTO integrated_source_objective VALUES (?,?)',('source_generated_game_v1',canon(extra['objective'])))
 cn.executemany('INSERT INTO experiment_mix VALUES (?,?)',sorted(DEFAULT_MIX.items()))
 games={row[0]:row[1] for row in cn.execute('SELECT game_id,family_id FROM games')}
 count=0;maxabs=0.0
 sql=('SELECT c.case_id,c.op_id,c.input_json,c.target_json,i.game_id,c.split '
      'FROM transformation_cases c JOIN instances i ON c.instance_id=i.instance_id ORDER BY c.case_id')
 for case_id,op,src_text,gold_text,gid,split in cn.execute(sql).fetchall():
  source=json.loads(src_text);gold=json.loads(gold_text)
  if gold['kind']=='matrix':
   before=source['U'];after=gold['U_prime']
   # Fixed-coordinate comparisons test actual transformed tensor effects.
   # Project to a legal coordinate in both tensors, including rectangular swaps.
   seed=int(digest([case_id,'coordinate'])[:10],16)
   r=seed%min(len(before),len(after)); c=(seed//16)%min(len(before[r]),len(after[r]));player=(seed//256)%2
   x=float(before[r][c][player]);y=float(after[r][c][player])
   formal=(f'Finite game {gid}. U[row][column]=[row-player payoff,column-player payoff]: '
           f'{canon(before)}. Actions: row={canon(source["row_actions"])}; '
           f'column={canon(source["column_actions"])}. '
           f'Apply {op}: {mathlib.TRANSFORM_LOOKUP[op]}. '
           f'Compute original and transformed payoff for player={player}, row={r}, column={c}.')
   narrative=(f'A two-player strategic encounter ({gid}) has this payoff table: {canon(before)}. '
              f'The transformation is {op}, meaning {mathlib.TRANSFORM_LOOKUP[op]}. '
              f'At the entry row {r}, column {c}, player {player}, determine the payoff before '
              'and after that change.')
  else:
   params=source['parameters'];changed=source['changed'];increment=source['increment']
   x=float(gold['value_before']);y=float(gold['value_after'])
   formal=(f'Game {gid}. Function: {source["formula"]}. Parameters: {canon(params)}. '
           f'Transformation scalar_parameter_shift: increase parameter {changed} by {increment:.9g}. '
           'Evaluate the function before and after the transformation.')
   narrative=(f'For the strategic model {gid}, calculate the value using {source["formula"]}. '
              f'Its starting parameters are {canon(params)}. Now increase {changed} by {increment:.9g}. '
              'What are the original and updated values?')
  # The existing head has log/sign magnitude output. Multiply both gold operands
  # by the same known scale if needed to avoid losing non-integer subunit values.
  scale=1000. if gold['kind']=='scalar' else 1.
  x*=scale;y*=scale
  # Capped only as an explicit curriculum constraint, never silently clipped.
  if not math.isfinite(x) or not math.isfinite(y) or max(abs(x),abs(y))>2_000_000:
   raise ValueError('NUMERIC_BRIDGE_UNSUPPORTED_RANGE:'+case_id)
  maxabs=max(maxabs,abs(x),abs(y))
  instruction=(f'Units: multiply both computed values by {int(scale)} before reporting. '
               'Return XXXXXX = original scaled value and YYYYYY = transformed scaled value.')
  prompt1=formal+'\n'+instruction+'\nAnswer:'
  prompt2=instruction+'\n'+narrative+'\nAnswer:'
  # Guard against accidental leak of golden values into the prompt serialization.
  if '"value_after"' in prompt1 or '"U_prime"' in prompt1:
   raise AssertionError('TARGET_LEAK')
  local_split=game_split(split,gid)
  cn.execute('INSERT INTO experiment_cases VALUES (?,?,?,?,?,?,?,?,?,?)',
             (case_id,case_id,gid,games[gid],op,local_split,x,y,scale,'verified'))
  cn.executemany('INSERT INTO experiment_prompts VALUES (?,?,?)',
       ((case_id,'forward',prompt1),(case_id,'reordered',prompt2)))
  count+=1
 return {'projections':count,'prompts':count*2,'max_abs_scaled_target':maxabs,
         'reserved_games':sorted(RESERVED_GAMES)}

def generate(path:Path,seed:int=20261015,instances_per_game:int=16,replace:bool=False)->dict:
 path=Path(path)
 if path.exists() and not replace:raise FileExistsError(path)
 # Preserve MC3 catalog, dictionary and objective verbatim, then append
 # experiment-specific targets and its actually executable objective.
 info=mathlib.build(path,seed,cases_per_game=instances_per_game,replace=replace)
 with closing(sqlite3.connect(path)) as cn:
  cn.execute('PRAGMA foreign_keys=ON')
  stats=make_projections(cn);cn.commit()
  inspect_database(cn)
 return {'source_library':info,'experiment':stats,'database_sha256':filehash(path)}

def inspect_database(cn:sqlite3.Connection)->dict:
 if cn.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise AssertionError('SQL_INTEGRITY')
 if cn.execute('PRAGMA foreign_key_check').fetchall():raise AssertionError('SQL_FOREIGN_KEYS')
 rows=cn.execute('SELECT case_id,split,game_id,operator,x,y,scale FROM experiment_cases').fetchall()
 if not rows:raise AssertionError('NO_EXPERIMENT_CASES')
 for case_id,split,gid,op,x,y,scale in rows:
  if (gid in RESERVED_GAMES)!=(split=='game_transfer'):
   raise AssertionError('GAME_SPLIT_LEAK:'+case_id)
  count=cn.execute('SELECT count(*) FROM experiment_prompts WHERE case_id=?',(case_id,)).fetchone()[0]
  if count!=2:raise AssertionError('PAIRED_PROJECTION_MISSING')
  if not all(math.isfinite(v) for v in (x,y,scale)):raise AssertionError('NONFINITE_GOLD')
 for op,qty in cn.execute('SELECT operator,count FROM experiment_mix'):
  if op!='retention' and not cn.execute('SELECT 1 FROM experiment_cases WHERE operator=? AND split="train" LIMIT 1',(op,)).fetchone():
   raise AssertionError('UNTRAINABLE_MIX:'+op)
 checked=mathlib.verify(cn)
 # Independent positive and negative oracle controls.
 row=cn.execute('SELECT c.input_json,c.target_json,c.op_id FROM transformation_cases c JOIN experiment_cases e ON e.case_id=c.case_id WHERE e.operator="positive_affine" LIMIT 1').fetchone()
 src,gold,op=map(lambda z:z,row)
 truth=json.loads(gold)['U_prime'];computed=mathlib.matrix_transform(json.loads(src)['U'],op)
 assert computed==truth
 bad=json.loads(canon(truth));bad[0][0][0]+=1
 assert bad!=computed
 return {'cases':len(rows),'splits':dict(cn.execute('SELECT split,count(*) FROM experiment_cases GROUP BY split')),
         'operator_counts':dict(cn.execute('SELECT operator,count(*) FROM experiment_cases GROUP BY operator')),
         'positive_oracle_pass':True,'negative_oracle_rejected':True,'mathlib':checked}

def inspect(path:Path)->dict:
 with closing(sqlite3.connect(path)) as cn:return inspect_database(cn)

def rows_for(cn,split:str,ops:set[str]|None=None):
 data=cn.execute('''SELECT c.case_id,c.game_id,c.operator,c.x,c.y,p.orientation,p.prompt
 FROM experiment_cases c JOIN experiment_prompts p USING(case_id)
 WHERE c.split=? ORDER BY c.case_id,p.orientation''',(split,)).fetchall()
 samples=defaultdict(dict)
 for cid,gid,op,x,y,orientation,prompt in data:
  if ops is not None and op not in ops:continue
  entry=samples[cid]
  entry.update(id=cid,game=gid,operator=op,x=x,y=y)
  entry[orientation]=prompt
 return list(samples.values())

def select_case(pool:list[dict],step:int,slot:int,seed:int)->dict:
 if not pool:raise ValueError('EMPTY_TRAINING_OPERATOR_POOL')
 # Coprime walk over the entire eligible corpus. Step stride is deterministic,
 # no random replacement of one small periodic subset.
 stride=17
 while math.gcd(stride,len(pool))!=1:stride+=1
 index=(seed+step*stride+slot*37)%len(pool)
 return pool[index]

def metric(predictions:dict[str,dict[str,list[float]]],cases:list[dict])->dict:
 counters={key:0 for key in ('original5','reversed5','paired5','original10','reversed10','paired10')}
 errors=[];acc=0
 for item in cases:
  a=predictions[item['id']]; truth=[item['x'],item['y']]
  if len(a['forward'])!=2 or len(a['reordered'])!=2:raise AssertionError('INFERENCE_SHAPE')
  check={}
  for label,actual in (('original',a['forward']),('reversed',a['reordered'])):
   errs=[abs(float(z)-y)/max(1.,abs(y)) for z,y in zip(actual,truth)]
   errors.extend(errs)
   check[label]=(max(errs)<=.05,max(errs)<=.10)
   counters[label+'5']+=check[label][0]
   counters[label+'10']+=check[label][1]
  counters['paired5']+=check['original'][0] and check['reversed'][0]
  counters['paired10']+=check['original'][1] and check['reversed'][1]
  acc+=int(a['op_forward']==item['operator'] and a['op_reordered']==item['operator'])
 n=len(cases)
 return {'n':n,'accuracy':{key:round(float(val)/n,6) for key,val in counters.items()},
         'operator_paired_accuracy':round(acc/n,6),
         'mean_relative_error':round(sum(errors)/len(errors),6)}

def torch_runtime():
 """Lazy imports allow generating/testing SQL without CUDA, transformers or a model."""
 import torch
 from torch import nn
 from torch.nn import functional as F
 tools_root=str(Path(__file__).resolve().parent)
 if tools_root not in sys.path:sys.path.insert(0,tools_root)
 from tools import nanojev_altreg_attention_curriculum_mix_v4 as mix
 from tools import nanojev_altreg_math_v3_decoder_experiment as v3
 from tools import nanojev_altreg_independent_math_qwen_precision_v2 as base
 return torch,nn,F,mix,v3,base

def load_current(checkpoint:Path,device:str,dtype:str,allow_download:bool,seed:int):
 torch,nn,F,mix,v3,base=torch_runtime()
 cp=torch.load(checkpoint,map_location='cpu',weights_only=True)
 if cp.get('schema')!=mix.SCHEMA or cp.get('config',{}).get('backbone')!='qwen':
  raise RuntimeError('SOURCE_NOT_NANOJEV_V4_CHECKPOINT')
 cfg=cp['config']
 parent_args=SimpleNamespace(parent_root=Path(cfg['parent_root']),
                             parent_checkpoint=cfg['parent_checkpoint'],backbone='qwen',device=device)
 parent_cp,cache,parent,parent_hash=v3.get_parent(base,parent_args)
 if parent_hash!=cfg['parent_sha256']:raise RuntimeError('SOURCE_PARENT_SHA_MISMATCH')
 tdim=(parent_cp['config']['input_dim']//8)*2
 dec=v3.V3Decoder(parent_cp['config']['width'],len(base.OP_IDS),'attention',
                  math.floor(math.log10(base.registers.MAX_VALUE)),cfg['hidden'],tdim).to(device)
 dec.load_state_dict(cp['head'],strict=True)
 ophead=mix.OperationHead(tdim,cfg['hidden']).to(device)
 ophead.load_state_dict(cp['operation_head'],strict=True)
 ophead.eval()
 for p in ophead.parameters():p.requires_grad_(False)
 torch.manual_seed(seed)
 if device=='cuda':torch.cuda.manual_seed_all(seed)
 # New classifier consumes frozen Qwen representations. It is initialized once,
 # then kept constant for baseline and subsequent checkpoint comparisons.
 class GameOperatorHead(nn.Module):
  def __init__(self):
   super().__init__();self.net=nn.Sequential(nn.LayerNorm(tdim),nn.Linear(tdim,cfg['hidden']),nn.GELU(),nn.Linear(cfg['hidden'],len(ALL_OPERATORS)))
  def forward(self,tokens,mask):
   w=mask.to(dtype=tokens.dtype).unsqueeze(-1)
   pool=(tokens*w).sum(1)/w.sum(1).clamp(min=1)
   return self.net(pool)
 gamehead=GameOperatorHead().to(device)
 params=SimpleNamespace(device=device,dtype=dtype,allow_model_download=allow_download)
 _,backbone,tokenizer,actual_device,_=base.cassette.load_source('qwen',params)
 if str(actual_device)!=device or any(p.requires_grad for p in backbone.parameters()):
  raise RuntimeError('BACKBONE_NOT_FROZEN')
 dec.eval();gamehead.eval()
 return dict(torch=torch,F=F,mix=mix,v3=v3,base=base,parent=parent,
             decoder=dec,gamehead=gamehead,ophead=ophead,backbone=backbone,
             tokenizer=tokenizer,config=cfg,device=device,source=cp,
             source_sha=filehash(checkpoint))

def forward(model,prompts:list[str]):
 torch=model['torch'];v3=model['v3'];base=model['base'];device=model['device']
 flat,seq,mask=v3.full_capture(model['backbone'],model['tokenizer'],device,prompts,
                              model['config']['max_prompt_tokens'])
 # The existing XY attention decoder retains its original operation embedding;
 # the game-operator ID is predicted independently from tokens, NOT supplied as
 # an out-of-band gold operator ID to the decoder.
 with torch.no_grad():
  h=model['parent'].extract(flat.to(device))
  pxy=model['parent'].xy_coord(h)
 neutral=torch.full((len(prompts),),base.OP_IDS['written_sub'],device=device,dtype=torch.long)
 out=model['decoder'](h,pxy,neutral,seq.to(device),mask.to(device))
 logits=model['gamehead'](seq.to(device),mask.to(device))
 return out,logits

def predict_one(model,prompt:str):
 torch=model['torch'];v3=model['v3'];base=model['base']
 with torch.no_grad():
  out,logits=forward(model,[prompt]);xy=v3.predict(out,base,'attention')[0].cpu().tolist()
 return [float(z) for z in xy], ALL_OPERATORS[int(logits.argmax(-1).item())]

def infer_cases(model,cases:list[dict]):
 pred={}
 model['decoder'].eval();model['gamehead'].eval()
 for j,ex in enumerate(cases):
  a,op1=predict_one(model,ex['forward']);b,op2=predict_one(model,ex['reordered'])
  pred[ex['id']]={'forward':a,'reordered':b,'op_forward':op1,'op_reordered':op2}
 return pred

def balanced_limit(items:list[dict],limit:int)->list[dict]:
 """Round robin over operators, then diverse games: no hidden easy-only slice."""
 groups=defaultdict(list)
 for row in items:groups[row['operator']].append(row)
 for op in groups:groups[op].sort(key=lambda row:(row['game'],row['id']))
 selected=[];index=defaultdict(int)
 while len(selected)<limit:
  grew=False
  for op in sorted(groups):
   k=index[op]
   if k<len(groups[op]):
    selected.append(groups[op][k]);index[op]+=1;grew=True
    if len(selected)>=limit:break
  if not grew:break
 return selected

def evaluate(model,database:Path,limit:int=32)->dict:
 with closing(sqlite3.connect(database)) as cn:
  groups={split:balanced_limit(rows_for(cn,split),limit) for split in ('dev','test','game_transfer')}
 result={}
 for split,items in groups.items():
  if not items:continue
  preds=infer_cases(model,items)
  per_op={op:metric(preds,[x for x in items if x['operator']==op])
          for op in sorted({x['operator'] for x in items})}
  per_game={gid:metric(preds,[x for x in items if x['game']==gid])
            for gid in sorted({x['game'] for x in items})}
  result[split]={'overall':metric(preds,items),'per_operator':per_op,'per_game':per_game}
 return result

def write_json(path:Path,data:Any):
 path.parent.mkdir(parents=True,exist_ok=True)
 tmp=path.with_name(path.name+'.tmp');tmp.write_text(json.dumps(data,sort_keys=True,indent=2,allow_nan=False)+'\n');tmp.replace(path)

def experiment(args):
 """Inference and optionally train from current saved v4 decoder, without mutations to it."""
 torch,nn,F,mix,v3,base=torch_runtime()
 if args.device=='auto':args.device='cuda' if torch.cuda.is_available() else 'cpu'
 if args.device=='cuda' and not torch.cuda.is_available():raise RuntimeError('CUDA_UNAVAILABLE')
 path=args.database.expanduser().resolve();source=args.checkpoint.expanduser().resolve()
 if not path.is_file() or not source.is_file():raise FileNotFoundError('DATABASE_AND_CHECKPOINT_REQUIRED')
 out=args.output.expanduser().resolve();out.mkdir(parents=True,exist_ok=True)
 if source==out/'latest.pt' or out==source.parent or out==path.parent:raise ValueError('EXPERIMENT_MUST_NOT_OVERWRITE_SOURCE')
 mixcounts=parse_mix(args.training_mix)
 with closing(sqlite3.connect(path)) as cn:
  inspect_database(cn)
  pools={op:rows_for(cn,'train',{op}) for op in mixcounts if op!='retention'}
  retention_source=base.registers.build_plan(args.seed)[1]
  reserved={(min(int(e['a']),int(e['b'])),max(int(e['a']),int(e['b']))) for e in retention_source if e['split']!='train'}
  stream=base.Stream(args.seed+303,reserved)
  retention_holdout=[e for e in retention_source if e['split']!='train'][:args.eval_limit]
  if not retention_holdout:raise RuntimeError('NO_LEGACY_RETENTION_HOLDOUT')
 ident={'schema':SCHEMA,'source_checkpoint':str(source),'source_sha256':filehash(source),
        'database':str(path),'database_sha256':filehash(path),'seed':args.seed,
        'training_mix':mixcounts,'lr':args.lr,'op_weight':args.op_weight,'paired':True,
        'new_optimizer':'AdamW; current decoder weights loaded; original optimizer not mutated'}
 config_path=out/'experiment-config.json'
 if config_path.exists():
  previous=json.loads(config_path.read_text())
  if previous!=ident:raise RuntimeError('EXPERIMENT_IDENTITY_DRIFT')
 else:write_json(config_path,ident)
 model=load_current(source,args.device,args.dtype,args.allow_model_download,args.seed)
 def retention_score():
  model['decoder'].eval()
  yes=0
  for e in retention_holdout:
   prompt=mix.curriculum_prompt(e['a'],e['b'],e['operation'],'registered_logbar',False)
   with torch.no_grad():
    flat,seq,mask=v3.full_capture(model['backbone'],model['tokenizer'],args.device,[prompt],model['config']['max_prompt_tokens'])
    h=model['parent'].extract(flat.to(args.device));parentxy=model['parent'].xy_coord(h)
    op=torch.tensor([base.OP_IDS[e['operation']]],device=args.device,dtype=torch.long)
    decoded=v3.predict(model['decoder'](h,parentxy,op,seq.to(args.device),mask.to(args.device)),base,'attention')[0].cpu().tolist()
   yes+=all(abs(p-t)/max(1.,abs(t))<=.05 for p,t in zip(decoded,(e['a'],e['b'])))
  return {'n':len(retention_holdout),'joint_5pct':round(yes/len(retention_holdout),6)}
 base_file=out/'baseline.json'
 if not base_file.exists():
  write_json(base_file,{'schema':SCHEMA,'source_step':model['source']['step'],
                        'evaluation':evaluate(model,path,args.eval_limit),'retention':retention_score(),
                        'source_sha256':ident['source_sha256'],'database_sha256':ident['database_sha256']})
 if args.action=='baseline':return {'baseline':json.loads(base_file.read_text()),'output':str(out)}
 opt=torch.optim.AdamW(list(model['decoder'].parameters())+list(model['gamehead'].parameters()),lr=args.lr)
 checkpoint=out/'latest.pt'
 current=0
 if checkpoint.exists():
  cp=torch.load(checkpoint,map_location='cpu',weights_only=True)
  if cp.get('identity')!=digest(ident):raise RuntimeError('EXPERIMENT_RESUME_IDENTITY_MISMATCH')
  model['decoder'].load_state_dict(cp['decoder'],strict=True)
  model['gamehead'].load_state_dict(cp['game_operator_head'],strict=True)
  opt.load_state_dict(cp['optimizer']);current=int(cp['step'])
  if 'retention_stream' not in cp or 'torch_rng_cpu' not in cp:
   raise RuntimeError('EXPERIMENT_CHECKPOINT_MISSING_RNG_OR_STREAM')
  stream.load_state_dict(cp['retention_stream'])
  torch.set_rng_state(cp['torch_rng_cpu'])
  if args.device=='cuda' and cp.get('torch_rng_cuda') is not None:
   torch.cuda.set_rng_state_all(cp['torch_rng_cuda'])
 if args.action=='evaluate':
  if not checkpoint.exists():raise FileNotFoundError('NO_EXPERIMENT_CHECKPOINT')
  metrics={'schema':SCHEMA,'step':current,'evaluation':evaluate(model,path,args.eval_limit),
           'retention':retention_score(),'baseline':json.loads(base_file.read_text())}
  write_json(out/'comparison.json',metrics)
  return metrics
 if args.action not in ('train','all'):raise ValueError('BAD_ACTION')
 if current>=args.steps:raise ValueError('TARGET_STEPS_NOT_AHEAD_OF_EXPERIMENT')
 for step in range(current+1,args.steps+1):
  model['decoder'].train();model['gamehead'].train();opt.zero_grad(set_to_none=True)
  loss_sum=0.;nview=0
  # An entire logical batch is accumulated with microbatch size 1. On small
  # VRAM (e.g. 6GB), the frozen backbone never retains grad activations.
  items=[op for op,qty in mixcounts.items() for _ in range(qty)]
  random.Random(args.seed+step).shuffle(items)
  for slot,op in enumerate(items):
   if op=='retention':
    e=stream.get();prompt=mix.curriculum_prompt(e['a'],e['b'],e['operation'],'registered_logbar',False)
    flat,seq,mask=v3.full_capture(model['backbone'],model['tokenizer'],args.device,[prompt],model['config']['max_prompt_tokens'])
    with torch.no_grad():
     h=model['parent'].extract(flat.to(args.device));pxy=model['parent'].xy_coord(h)
    ops=torch.tensor([base.OP_IDS[e['operation']]],device=args.device,dtype=torch.long)
    pred=model['decoder'](h,pxy,ops,seq.to(args.device),mask.to(args.device))
    gold=torch.tensor([[e['a'],e['b']]],device=args.device,dtype=torch.float32)
    error,_=v3.objective(pred,gold,base,'attention')
    (error/len(items)).backward();loss_sum+=float(error.detach())/len(items)
   else:
    ex=select_case(pools[op],step,slot,args.seed)
    for ori in ('forward','reordered'):
     pred,logits=forward(model,[ex[ori]])
     gold=torch.tensor([[ex['x'],ex['y']]],device=args.device,dtype=torch.float32)
     xy_loss,_=v3.objective(pred,gold,base,'attention')
     label=torch.tensor([ALL_OPERATORS.index(op)],device=args.device,dtype=torch.long)
     error=xy_loss+args.op_weight*F.cross_entropy(logits,label)
     (error/(len(items)*2)).backward();loss_sum+=float(error.detach())/(len(items)*2)
     nview+=1
  torch.nn.utils.clip_grad_norm_(list(model['decoder'].parameters())+list(model['gamehead'].parameters()),1.)
  opt.step()
  if step%args.save_every==0 or step==args.steps:
   write_state={'schema':SCHEMA,'identity':digest(ident),'step':step,
     'decoder':model['decoder'].state_dict(),'game_operator_head':model['gamehead'].state_dict(),
     'optimizer':opt.state_dict(),'last_loss':loss_sum,
     'retention_stream':stream.state_dict(),'torch_rng_cpu':torch.get_rng_state()}
   if args.device=='cuda':write_state['torch_rng_cuda']=torch.cuda.get_rng_state_all()
   temp=out/'latest-tmp.pt';torch.save(write_state,temp);temp.replace(checkpoint)
   print(canon({'event':'game_transform_checkpoint','step':step,'loss':loss_sum,'paired_views':nview,
                'path':str(checkpoint)}),flush=True)
 metrics={'schema':SCHEMA,'step':args.steps,'evaluation':evaluate(model,path,args.eval_limit),
          'retention':retention_score(),'baseline':json.loads(base_file.read_text())}
 write_json(out/'comparison.json',metrics)
 return metrics

def parser():
 p=argparse.ArgumentParser(description=__doc__)
 p.add_argument('action',choices=('build','smoke','baseline','train','evaluate','all'))
 p.add_argument('--database',type=Path,default=Path('runtime/game-transformation-experiment/game_transformations.sqlite3'))
 p.add_argument('--output',type=Path,default=Path.home()/'NanoJev'/'runs'/'game_transform_experiment_v1')
 p.add_argument('--checkpoint',type=Path,default=Path.home()/'NanoJev'/'runs'/'independent_altreg_math_qwen_curriculum_v4_exact_mix'/'latest.pt')
 p.add_argument('--seed',type=int,default=20261015)
 p.add_argument('--instances-per-game',type=int,default=16)
 p.add_argument('--replace',action='store_true')
 p.add_argument('--steps',type=int,default=20,help='Total experiment optimizer steps (not original v4 steps)')
 p.add_argument('--training-mix',default=None,help='Exact fixed quotas operator=count,...; require retention')
 p.add_argument('--eval-limit',type=int,default=16,help='Cases per split; two presentations for each')
 p.add_argument('--save-every',type=int,default=5)
 p.add_argument('--lr',type=float,default=1e-5)
 p.add_argument('--op-weight',type=float,default=.1)
 p.add_argument('--device',choices=('auto','cpu','cuda'),default='auto')
 p.add_argument('--dtype',choices=('float16','bfloat16','float32'),default='float16')
 p.add_argument('--allow-model-download',action='store_true')
 return p

def main(argv=None):
 args=parser().parse_args(argv)
 if args.action in ('build','all'):
  report=generate(args.database,args.seed,args.instances_per_game,replace=args.replace)
  print(canon({'event':'game_transform_database_built',**report}),flush=True)
 if args.action in ('smoke','all'):
  result=inspect(args.database)
  print(canon({'event':'game_transform_oracle_smoke_pass',**result}),flush=True)
 if args.action in ('baseline','train','evaluate','all'):
  out=experiment(args)
  print(canon({'event':'game_transform_'+args.action+'_complete','result':out}),flush=True)
 return 0

if __name__=='__main__':raise SystemExit(main())
