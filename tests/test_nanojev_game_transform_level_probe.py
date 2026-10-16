"""Pure and SQLite-backed contracts for the read-only T00--T08 checkpoint probe."""
from __future__ import annotations
from collections import Counter
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import sys
TOOLS = Path(__file__).resolve().parents[1] / 'tools'
if str(TOOLS) not in sys.path:sys.path.insert(0,str(TOOLS))

from tools import nanojev_game_transform_experiment as ex
from tools import nanojev_game_transform_level_probe as probe


class FakeBase:
 class registers:
  @staticmethod
  def number_repr(value):return f'{int(value)}, [LogBar {value}]'

class FakeMix:
 @staticmethod
 def curriculum_prompt(a,b,op,stage,reverse=False):
  assert op=='written_sub' and stage=='registered_logbar'
  lines=[f'XXXXXX = {a}, [LogBar {a}]',f'YYYYYY = {b}, [LogBar {b}]']
  if reverse:lines.reverse()
  return '\n'.join(lines)+'\noperation: Subtract YYYYYY from XXXXXX.\nAnswer:'

class FakeTokenizer:
 def __call__(self,prompt,add_special_tokens=False):
  # Length controlled independently in dedicated token-limit test.
  return {'input_ids':list(range(8))}


class TransformationLevelProbeTest(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  cls.tmp=TemporaryDirectory()
  cls.db=Path(cls.tmp.name)/'games.sqlite3'
  ex.generate(cls.db,instances_per_game=4)
 @classmethod
 def tearDownClass(cls):cls.tmp.cleanup()

 def test_same_case_held_out_and_game_diversity(self):
  original=ex.filehash(self.db)
  selected=probe.selection(self.db,20)
  self.assertEqual(original,ex.filehash(self.db))
  self.assertEqual(set(selected),{'dev','test','game_transfer'})
  self.assertEqual(selected,probe.selection(self.db,20))
  expected_games={'bayes_posterior','captain_ship_defense','chicken','pursuit_gap','routing_delay'}
  self.assertEqual({x['game'] for x in selected['game_transfer']},expected_games)
  for split,items in selected.items():
   self.assertLessEqual(len(items),20)
   self.assertTrue(all(x['x']!=x['y'] for x in items))
   self.assertEqual(len({x['id'] for x in items}),len(items))

 def test_t00_is_byte_identical_to_v4_formatter_and_t01_label_only(self):
  with __import__('sqlite3').connect(self.db) as cn:
   item=next(x for x in probe.selection(self.db,30)['dev'] if probe.available_integer(x))
  n=FakeMix.curriculum_prompt(int(item['x']),int(item['y']),'written_sub','registered_logbar',False)
  self.assertEqual(probe.render('T00',item,'forward',FakeMix,FakeBase),n)
  t01=probe.render('T01',item,'forward',FakeMix,FakeBase)
  self.assertEqual(t01.replace('Transformation label: '+item['operator']+'.\n',''),n)
  t02=probe.render('T02',item,'forward',FakeMix,FakeBase)
  self.assertTrue(t02.endswith(n))
  self.assertNotIn('U_prime',t02)

 def test_hidden_operand_is_not_leaked_as_registered_number(self):
  item=next(x for x in probe.selection(self.db,30)['test'] if probe.available_integer(x))
  known_x=probe.render('T03',item,'forward',FakeMix,FakeBase)
  known_y=probe.render('T04',item,'forward',FakeMix,FakeBase)
  self.assertIn(f'XXXXXX = {int(item["x"])}',known_x)
  self.assertIn('YYYYYY = [compute from transformation]',known_x)
  self.assertNotIn(f'YYYYYY = {int(item["y"])}',known_x)
  self.assertIn(f'YYYYYY = {int(item["y"])}',known_y)
  self.assertIn('XXXXXX = [compute from transformation]',known_y)
  self.assertNotIn(f'XXXXXX = {int(item["x"])}',known_y)

 def test_float_values_remain_exact_and_do_not_enter_native_integer_controls(self):
  row={'id':'example','x':1.25,'y':2.75,'operator':'scalar_parameter_shift',
       'forward':'formal\nAnswer:','reordered':'story\nAnswer:'}
  for lvl in ('T00','T01','T02','T03','T04'):
   with self.assertRaises(probe.UnsupportedNumericNative):
    probe.render(lvl,row,'forward',FakeMix,FakeBase)
  self.assertEqual(probe.render('T05',row,'forward',FakeMix,FakeBase),row['forward'])
  self.assertEqual(probe.render('T06',row,'forward',FakeMix,FakeBase),row['reordered'])

 def test_t07_only_accepts_real_composition(self):
  row={'operator':'compose_transformations','x':4.,'y':6.,
       'forward':'first, then second\nAnswer:',
       'reordered':'first, then second\nAnswer:'}
  self.assertIn('first, then second',probe.render('T07',row,'forward',FakeMix,FakeBase))
  row['operator']='positive_affine'
  with self.assertRaises(ValueError):probe.render('T07',row,'forward',FakeMix,FakeBase)

 def test_strict_paired_and_threshold_and_operator_metric(self):
  example={'case_id':'c','operator':'positive_affine','game':'g','truth':{'x':10.,'y':20.},
           'status':'ok'}
  a={**example,'orientation':'forward','prediction':[10.,20.],
     'predicted_operator':'positive_affine'}
  b={**a,'orientation':'reordered','prediction':[10.,21.5]}
  five=probe.score([a,b],'T03',.05)
  ten=probe.score([a,b],'T03',.10)
  self.assertEqual(five['paired']['joint'],0.)
  self.assertEqual(ten['paired']['joint'],1.)
  self.assertEqual(five['paired']['x'],1.)
  self.assertEqual(five['operator_paired'],1.)
  self.assertEqual(five['target_change']['changed']['n'],1)
  b['predicted_operator']='swap_players'
  self.assertEqual(probe.score([a,b],'T03',.10)['joint_and_operator'],0.)

 def test_token_limit_excluded_not_failed(self):
  case={'case_id':'c','operator':'compose_transformations','game':'g',
        'truth':{'x':2.,'y':5.},'status':'token_limit','orientation':'forward'}
  case2={**case,'orientation':'reordered','status':'ok','prediction':[2.,5.],
         'predicted_operator':'compose_transformations'}
  result=probe.score([case,case2],'T07')
  self.assertEqual(result['n'],0)
  self.assertEqual(result['unscorable'],{'token_limit':1})
  self.assertIsNone(result['paired']['joint'])

 def test_compare_is_read_only_inference_with_actual_subsets(self):
  subset=probe.selection(self.db,3)
  with __import__('sqlite3').connect(self.db) as cn:
   comp={s:probe.select_balanced([r for r in ex.rows_for(cn,s)
            if r['operator']=='compose_transformations'],3,changed_only=True) for s in subset}
  model={'tokenizer':FakeTokenizer(),'config':{'max_prompt_tokens':256},
         'mix':FakeMix,'base':FakeBase}
  # Simulate deterministic independent model predictions; this checks
  # instrument dispatch, not real learned checkpoint ability.
  with patch.object(ex,'predict_one',lambda *_:([2.,3.],'positive_affine')) as predict:
   run=probe.compare(model,subset,comp)
  self.assertIn('dev/T00',run['summary'])
  self.assertIn('game_transfer/T08',run['summary'])
  self.assertGreater(run['summary']['dev/T00']['n_selected'],0)
  self.assertEqual(len(run['rows']),sum(v['n_selected']*2 for v in run['summary'].values()))
  self.assertEqual(run['summary']['test/T07']['n_selected'],len(comp['test']))

 def test_identity_guard_rejects_database_source_drift(self):
  with TemporaryDirectory() as d:
   d=Path(d);src=d/'source.pt';exp=d/'latest.pt';cfg=d/'config.json'
   src.write_bytes(b'source');exp.write_bytes(b'experimental')
   cfg.write_text(json.dumps({'source_sha256':'bad','database_sha256':ex.filehash(self.db)}))
   with self.assertRaisesRegex(RuntimeError,'EXPERIMENT_SOURCE_DATABASE_DRIFT'):
    probe.identity(src,exp,self.db,cfg)

if __name__=='__main__':unittest.main()

class ReadOnlyCheckpointComparisonSmoke(unittest.TestCase):
 def test_saved_experiment_checkpoint_is_compared_without_weight_changes(self):
  try:
   import hashlib
   import torch
   from tools import nanojev_altreg_attention_curriculum_mix_v4 as mix
   from tools import nanojev_altreg_math_v3_decoder_experiment as v3
   from tools import nanojev_altreg_independent_math_qwen_precision_v2 as base
  except ImportError as err:
   self.skipTest('PyTorch/V3/V4 unavailable: '+str(err))

  class Parent(torch.nn.Module):
   def extract(self,flat):return torch.zeros(len(flat),16)
   def xy_coord(self,hidden):return torch.zeros(len(hidden),2)
  class Frozen(torch.nn.Module):pass
  def get_parent(*unused):return ({'config':{'input_dim':96,'width':16}},None,Parent(),'test_parent_hash')
  def full_capture(_b,_t,_device,prompts,_max):
   flat=[];seq=[]
   for p in prompts:
    data=hashlib.sha256(p.encode('utf8')).digest()
    flat.append(torch.tensor([(data[i%32]-128)/128 for i in range(96)],dtype=torch.float32))
    seq.append(torch.tensor([[(data[(i*11+j)%32]-128)/128 for j in range(24)] for i in range(8)],dtype=torch.float32))
   return torch.stack(flat),torch.stack(seq),torch.ones(len(flat),8,dtype=torch.bool)

  with TemporaryDirectory() as tmp:
   d=Path(tmp);db=d/'games.sqlite3';ex.generate(db,instances_per_game=3)
   torch.manual_seed(7)
   dec=v3.V3Decoder(16,len(base.OP_IDS),'attention',6,32,24)
   op=mix.OperationHead(24,32)
   src=d/'source-v4.pt'
   torch.save({'schema':mix.SCHEMA,'step':104500,
        'config':{'backbone':'qwen','parent_root':str(d),'parent_checkpoint':'best',
                  'parent_sha256':'test_parent_hash','hidden':32,'max_prompt_tokens':512},
        'head':dec.state_dict(),'operation_head':op.state_dict()},src)
   train=SimpleNamespace(database=db,output=d/'isolated',checkpoint=src,seed=20261015,
        device='cpu',dtype='float32',allow_model_download=False,steps=1,
        training_mix='positive_affine=1,retention=1',eval_limit=1,
        lr=1e-4,op_weight=.1,save_every=1,action='train')
   from contextlib import redirect_stdout
   from io import StringIO
   with patch.object(v3,'get_parent',get_parent),patch.object(v3,'full_capture',full_capture),\
        patch.object(base.cassette,'load_source',lambda *_:(None,Frozen(),FakeTokenizer(),'cpu',None)):
    with redirect_stdout(StringIO()):
     ex.experiment(train)
     before=(ex.filehash(db),ex.filehash(src),ex.filehash(train.output/'latest.pt'))
     args=SimpleNamespace(database=db,source_checkpoint=src,
         experimental_checkpoint=train.output/'latest.pt',
         experiment_config=train.output/'experiment-config.json',
         limit_per_split=1,qualification_min_cases=30,include_unchanged=False,
         report=d/'probe_report.json',seed=20261015,device='cpu',dtype='float32',
         allow_model_download=False)
     outcome=probe.run(args)
   after=(ex.filehash(db),ex.filehash(src),ex.filehash(train.output/'latest.pt'))
   self.assertEqual(before,after)
   self.assertEqual(outcome['checkpoints']['source']['step'],104500)
   self.assertEqual(outcome['checkpoints']['experimental']['step'],1)
   self.assertTrue(args.report.exists())
   self.assertEqual(set(outcome['delta_experimental_minus_source']),
       set(outcome['checkpoints']['source']['summary']))
