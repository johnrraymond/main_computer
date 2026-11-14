"""Diagnostic, level-map, and checkpoint-isolation contracts."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import sys
TOOLS=Path(__file__).resolve().parents[1]/'tools'
if str(TOOLS) not in sys.path:sys.path.insert(0,str(TOOLS))

from tools import nanojev_game_transform_experiment as ex
from tools import nanojev_game_transform_ladder_map as ladder
from tools import nanojev_game_transform_level_probe as probe

class FakeBase:
 class registers:
  @staticmethod
  def number_repr(value):return f'{value}'
class FakeMix:
 @staticmethod
 def curriculum_prompt(a,b,op,stage,reverse=False):
  assert op=='written_sub'
  lines=[f'XXXXXX = {a}',f'YYYYYY = {b}']
  if reverse:lines.reverse()
  return '\n'.join(lines)+'\noperation: Subtract YYYYYY from XXXXXX.\nAnswer:'
class Tokenizer:
 def __call__(self,prompt,add_special_tokens=False):
  return {'input_ids':list(range(max(6,len(prompt)//15)))}

class LadderContracts(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  cls.tmp=TemporaryDirectory();cls.db=Path(cls.tmp.name)/'db.sqlite3'
  ex.generate(cls.db,instances_per_game=3)
  with __import__('sqlite3').connect(cls.db) as cn:
   pool=ladder.materialize_source(cn,ex.rows_for(cn,'train'))
  cls.matrix=next(x for x in pool if probe.available_integer(x) and 'U' in x['source_structure'])
 @classmethod
 def tearDownClass(cls):cls.tmp.cleanup()

 def test_t02_variants_preserve_native_targets_and_labels(self):
  row=self.matrix
  native=ladder.contextual_prompt('T00',row,'forward',FakeMix,FakeBase)
  for lvl in ladder.CONTROL_LEVELS[2:]:
   text=ladder.contextual_prompt(lvl,row,'forward',FakeMix,FakeBase)
   self.assertEqual(text.count('XXXXXX = '+str(int(row['x']))),1,lvl)
   self.assertEqual(text.count('YYYYYY = '+str(int(row['y']))),1,lvl)
   self.assertEqual(text.count('Answer:'),1,lvl)
   self.assertEqual(text.count('operation: Subtract'),1,lvl)
   self.assertNotIn('U_prime',text)
   self.assertIn('Answer:',text)
  c=ladder.contextual_prompt('T02c',row,'forward',FakeMix,FakeBase)
  self.assertRegex(c, r'^Auxiliary observation: [0-9]+\.\nXXXXXX')
  self.assertEqual(c.count('Auxiliary observation:'),1)
  self.assertEqual(ladder.contextual_prompt('T02j',row,'forward',FakeMix,FakeBase).count('Auxiliary observation:'),1)
  self.assertTrue(ladder.contextual_prompt('T02k',row,'forward',FakeMix,FakeBase).startswith('XXXXXX'))
  self.assertEqual(ladder.contextual_prompt('T02f',row,'forward',FakeMix,FakeBase),
                    probe.render('T02',row,'forward',FakeMix,FakeBase))
  self.assertTrue(ladder.contextual_prompt('T02a',row,'forward',FakeMix,FakeBase).endswith(native))
  self.assertNotEqual(ladder.contextual_prompt('T02f',row,'forward',FakeMix,FakeBase),
                      ladder.contextual_prompt('T02g',row,'forward',FakeMix,FakeBase))

 def test_reordered_control_preserves_native_orientation(self):
  row=self.matrix
  forward=ladder.contextual_prompt('T02a',row,'forward',FakeMix,FakeBase)
  reverse=ladder.contextual_prompt('T02a',row,'reordered',FakeMix,FakeBase)
  self.assertNotEqual(forward,reverse)
  self.assertEqual(forward.splitlines()[0],reverse.splitlines()[0])

 def test_float_only_sublevels_refuse_native_cast(self):
  row={'x':1.25,'y':3.75,'forward':'math\nAnswer:', 'reordered':'story\nAnswer:',
       'operator':'scalar_parameter_shift','game':'g','source_structure':{'parameters':{'x':1.25}}}
  for level in ladder.CONTROL_LEVELS+('T03','T04'):
   with self.assertRaises(probe.UnsupportedNumericNative):
    ladder.contextual_prompt(level,row,'forward',FakeMix,FakeBase)
  self.assertEqual(ladder.contextual_prompt('T05',row,'forward',FakeMix,FakeBase),'math\nAnswer:')

 def test_excludes_reserved_transfer_games_and_retains_all(self):
  splits=ladder.contexts(self.db,25)
  for split,rows in splits.items():
   self.assertTrue(all((r['game'] in ex.RESERVED_GAMES)==(split=='game_transfer') for r in rows))
  self.assertEqual(set(r['game'] for r in splits['game_transfer']),ex.RESERVED_GAMES)

 def test_invalid_map_and_no_auto_phase_promotion(self):
  file=Path(__file__).resolve().parents[1]/'tools'/'nanojev_game_transform_ladder_map.json'
  spec,q=ladder.read_map(file,'t02_focus')
  self.assertGreater(sum(v for k,v in q.items() if k.startswith('T02')),
                     sum(v for k,v in q.items() if k in ('T00','T01','T03','T04')))
  self.assertEqual(q['retention'],2)
  with self.assertRaisesRegex(ValueError,'UNKNOWN_MAP_PHASE'):
   ladder.read_map(file,'missing')
  for stage,quotas in spec['phases'].items():
   self.assertNotIn('T08',quotas)

 def test_diag_identity_guard_blocks_wrong_source(self):
  with TemporaryDirectory() as d:
   d=Path(d);src=d/'a.pt';bad=d/'b.pt';diag=d/'diag.json'
   src.write_bytes(b'good');bad.write_bytes(b'bad')
   diag.write_text(json.dumps({'schema':ladder.SCHEMA,
       'database_sha256':ex.filehash(self.db),
       'generator_sha256':ex.filehash(Path(ladder.__file__)),
       'source_checkpoint_sha256':ex.filehash(src),
       'levels':list(ladder.DIAG_LEVELS),'models':{'source':{'step':1}}}))
   self.assertEqual(ladder.check_diagnostic(diag,self.db,src)['models']['source']['step'],1)
   with self.assertRaisesRegex(RuntimeError,'DIAGNOSTIC_IDENTITY_MISMATCH'):
    ladder.check_diagnostic(diag,self.db,bad)

 def test_readonly_probe_scores_and_carries_case_provenance(self):
  with patch.object(ex,'predict_one',return_value=([self.matrix['x'],self.matrix['y']],self.matrix['operator'])):
   records,summary=ladder.infer_level({'tokenizer':Tokenizer(),'mix':FakeMix,'base':FakeBase,
       'config':{'max_prompt_tokens':256}},[self.matrix],'T02a','dev')
  self.assertEqual(summary['5pct']['n'],1)
  self.assertEqual(summary['10pct']['paired']['joint'],1.)
  self.assertEqual(summary['5pct']['operator_paired'],1.)
  self.assertEqual({r['orientation'] for r in records},{'forward','reordered'})

class LadderCpuSmoke(unittest.TestCase):
 def test_diagnostic_then_training_and_manual_phase_transition(self):
  try:
   import torch
   from tools import nanojev_altreg_attention_curriculum_mix_v4 as mix
   from tools import nanojev_altreg_math_v3_decoder_experiment as v3
   from tools import nanojev_altreg_independent_math_qwen_precision_v2 as base
  except ImportError as exc:self.skipTest('V4 dependencies missing: '+str(exc))
  class Parent(torch.nn.Module):
   def extract(self,flat):return torch.zeros(len(flat),16)
   def xy_coord(self,h):return torch.zeros(len(h),2)
  class Frozen(torch.nn.Module):pass
  def get_parent(*a):return ({'config':{'input_dim':96,'width':16}},None,Parent(),'parent_sha')
  def full_capture(_backbone,_tok,_dev,prompts,_max):
   flat=[];seq=[]
   for p in prompts:
    digest=hashlib.sha256(p.encode()).digest()
    flat.append(torch.tensor([(digest[i%32]-128)/128 for i in range(96)],dtype=torch.float32))
    seq.append(torch.tensor([[(digest[(i*7+j)%32]-128)/128 for j in range(24)] for i in range(8)],dtype=torch.float32))
   return torch.stack(flat),torch.stack(seq),torch.ones(len(flat),8,dtype=torch.bool)
  with TemporaryDirectory() as temp:
   path=Path(temp);db=path/'db.sqlite3';ex.generate(db,instances_per_game=3)
   torch.manual_seed(7)
   dec=v3.V3Decoder(16,len(base.OP_IDS),'attention',6,32,24)
   op=mix.OperationHead(24,32)
   source=path/'source.pt'
   torch.save({'schema':mix.SCHEMA,'step':104500,
       'config':{'backbone':'qwen','parent_root':str(path),'parent_checkpoint':'best',
                'parent_sha256':'parent_sha','hidden':32,'max_prompt_tokens':512},
       'head':dec.state_dict(),'operation_head':op.state_dict()},source)
   sha=(ex.filehash(source),ex.filehash(db))
   diag=path/'diagnostic.json'
   schedule=path/'map.json'
   schedule.write_text(json.dumps({'schema':ladder.SCHEMA,'phases':{
       't02_focus':{'T02a':1,'retention':1},
       't03_bridge':{'T03':1,'retention':1}}}))
   args=SimpleNamespace(database=db,source_checkpoint=source,experimental_checkpoint=None,
       levels=','.join(ladder.DIAG_LEVELS),limit_per_split=1,include_unchanged=False,
       report=diag,diagnostic=diag,level_map=schedule,phase='t02_focus',output=path/'output',
       steps=1,save_every=1,lr=1e-4,op_weight=.1,seed=20261015,
       cache_per_level=2,retention_cache=2,report_every=1,refresh_every=2,max_wall_hours=0.,
       device='cpu',dtype='float32',allow_model_download=False)
   from contextlib import redirect_stdout
   from io import StringIO
   with patch.object(v3,'get_parent',get_parent),patch.object(v3,'full_capture',full_capture),\
        patch.object(base.cassette,'load_source',lambda *_:(None,Frozen(),Tokenizer(),'cpu',None)):
    with redirect_stdout(StringIO()):
     report=ladder.diagnose(args)
     self.assertEqual(report['models']['source']['step'],104500)
     result=ladder.train(args)
     self.assertEqual(result['step'],1)
     cp=torch.load(args.output/'latest.pt',map_location='cpu',weights_only=True)
     self.assertEqual(cp['phase_history'][0]['phase'],'t02_focus')
     first=ex.filehash(args.output/'latest.pt')
     args.phase='t03_bridge';args.steps=2
     result=ladder.train(args)
     self.assertEqual(result['step'],2)
     cp=torch.load(args.output/'latest.pt',map_location='cpu',weights_only=True)
     self.assertEqual([x['phase'] for x in cp['phase_history']],['t02_focus','t03_bridge'])
     self.assertNotEqual(first,ex.filehash(args.output/'latest.pt'))
     self.assertTrue((args.output/'frozen-capture-cache-t02_focus.pt').is_file())
     self.assertTrue((args.output/'frozen-capture-cache-t03_bridge.pt').is_file())
     args.steps=3
     result=ladder.train(args)
     self.assertEqual(result['step'],3)
     cache=torch.load(args.output/'frozen-capture-cache-t03_bridge.pt',map_location='cpu',weights_only=True)
     self.assertEqual(cache['identity']['cache_window'],1)
     current_hash=ex.filehash(args.output/'latest.pt')
     args.experimental_checkpoint=args.output/'latest.pt'
     args.report=path/'post-training.json'
     after=ladder.diagnose(args)
     self.assertIn('experimental',after['models'])
     self.assertEqual(after['models']['experimental']['step'],3)
     self.assertEqual(current_hash,ex.filehash(args.output/'latest.pt'))
   self.assertEqual(sha,(ex.filehash(source),ex.filehash(db)))

if __name__=='__main__':unittest.main()
