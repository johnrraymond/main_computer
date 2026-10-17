"""Game-native math targets and action-label semantic invariants."""
import json
import sqlite3
import unittest
from fractions import Fraction
from pathlib import Path
from tempfile import TemporaryDirectory

from tools import nanojev_game_math_smoke as smoke
from tools import nanojev_game_native_objectives_v6 as native
from tools import nanojev_game_math_rebuild_audit as rebuild
from tools import nanojev_game_transformation_library as legacy

class NativeMathTests(unittest.TestCase):
 def test_captain_hull_mixed_equilibrium(self):
  m=[[[4,-13],[-16,-7]],[[-24,2],[0,-1]]]
  self.assertEqual(native._mixed(m),(Fraction(-3),Fraction(-9),Fraction(16),Fraction(44)))
  f=native._facts('matrix','captain_ship_defense','Captain Hull Preservation',
      {'U':m,'row_actions':['Intercept','Evade'],'column_actions':['Raid','Withdraw']},'mixed_row')
  p=native.walk(native.render08(f))
  self.assertEqual(p['answer']['OP'],'DIVIDE')
  self.assertEqual(p['answer']['Z'],{'numerator':1,'denominator':3})
  f['mode']='mixed_col';p=native.walk(native.render08(f))
  self.assertEqual(p['answer']['Z'],{'numerator':4,'denominator':11})

 def test_best_response_differs_by_opponent(self):
  m=[[[4,-13],[-16,-7]],[[-24,2],[0,-1]]]
  f=native._facts('matrix','captain_ship_defense','Captain Hull Preservation',
      {'U':m,'row_actions':['Intercept','Evade'],'column_actions':['Raid','Withdraw']},'best_response',0)
  a=native.walk(native.render08(f))['answer']
  f['selector']=1
  b=native.walk(native.render08(f))['answer']
  self.assertEqual((a['Z'],a['Z_semantics']),(0,'Intercept'))
  self.assertEqual((b['Z'],b['Z_semantics']),(1,'Evade'))

 def test_original_action_labels_survive_permutations(self):
  f={'U':[[[4,-13],[-16,-7]],[[-24,2],[0,-1]]],
     'row_actions':['Intercept','Evade'],'column_actions':['Raid','Withdraw']}
  for op in ('permute_row','permute_column','compose_transformations'):
   after=native.audit_permutation(f,op)
   self.assertEqual(native._semantic_payoffs(f),native._semantic_payoffs(after))
   self.assertTrue(after['representation_only'])
  self.assertEqual(native.audit_permutation(f,'permute_row')['row_actions'],['Evade','Intercept'])
  self.assertEqual(native.audit_permutation(f,'permute_column')['column_actions'],['Withdraw','Raid'])
  self.assertFalse(native.audit_permutation(f,'positive_affine')['representation_only'])
  self.assertTrue(native.audit_permutation(f,'positive_affine')['strategically_equivalent'])

 def test_scalar_math_is_native_not_delta(self):
  for gid,(_,_,_,parameters,_,_) in legacy.SCALAR_MODELS.items():
   f=native._facts('scalar',gid,legacy.SCALAR_MODELS[gid][0],{'parameters':parameters},'scalar_value')
   result=native.walk(native.render08(f))
   z=result['answer']['Z'];z=Fraction(z['numerator'],z['denominator'])
   self.assertAlmostEqual(float(z),legacy.scalar_oracle(gid,parameters),places=8)
   self.assertNotEqual(result['answer']['Z_semantics'],'legacy_original_minus_transformed_not_action')

 def test_early_stages_do_not_calculate(self):
  f=native._facts('matrix','captain_ship_defense','Captain Hull Preservation',
      {'U':[[[4,-13],[-16,-7]],[[-24,2],[0,-1]]],
       'row_actions':['Intercept','Evade'],'column_actions':['Raid','Withdraw']},'best_response',0)
  p=native.walk(native.render08(f))
  for e in p['edges'][:4]:
   self.assertTrue(all(step['operator'] not in ('LOOKUP_ACTION_PAYOFF','DIVIDE','SUBTRACT','ARGMAX') for step in e['steps']))

 def test_public_story_parsing_rejects_corruption(self):
  f=native._facts('matrix','captain_ship_defense','Captain Hull Preservation',
      {'U':[[[4,-13],[-16,-7]],[[-24,2],[0,-1]]],
       'row_actions':['Intercept','Evade'],'column_actions':['Raid','Withdraw']},'best_response',0)
  good=native.render08(f)
  with self.assertRaises(ValueError):native.parse08(good.replace('(4, -13)','(4, bogus)'))
  with self.assertRaises(ValueError):native.parse08(good.replace('Ties favor the first listed action','Ties favor either action'))

class GeneratedNativeDatabaseTests(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  cls.tmp=TemporaryDirectory()
  cls.path=Path(cls.tmp.name)
  smoke.generate(cls.path,20261016,4)
 @classmethod
 def tearDownClass(cls):cls.tmp.cleanup()

 def test_preserves_existing_verified_cases(self):
  with sqlite3.connect(self.path/'game_transformations.sqlite3') as db:
   self.assertEqual(db.execute('SELECT COUNT(*) FROM transformation_cases').fetchone()[0],524)
   self.assertEqual(native.verify(db)['games_with_native_objectives'],33)
   self.assertEqual(db.execute('SELECT COUNT(*) FROM native_v6_cases').fetchone()[0],284)

 def test_no_catalog_only_gold(self):
  with sqlite3.connect(self.path/'game_transformations.sqlite3') as db:
   self.assertEqual(db.execute("SELECT COUNT(*) FROM native_v6_game_coverage WHERE status='catalog_only_no_oracle'").fetchone()[0],196)
   self.assertEqual(db.execute("SELECT COUNT(*) FROM native_v6_cases n JOIN games g ON n.game_id=g.game_id WHERE g.status='catalog_only'").fetchone()[0],0)

 def test_tampered_database_fails(self):
  with sqlite3.connect(self.path/'game_transformations.sqlite3') as db:
   cid=db.execute('SELECT case_id FROM native_v6_cases ORDER BY case_id LIMIT 1').fetchone()[0]
   original=db.execute('SELECT prompt FROM native_v6_tiers WHERE case_id=? AND tier=0',(cid,)).fetchone()[0]
   db.execute('UPDATE native_v6_tiers SET prompt=? WHERE case_id=? AND tier=0',(original+' TAMPER',cid))
   with self.assertRaises(AssertionError):native.verify(db)
   db.rollback()

 def test_manifest_and_proof_only(self):
  manifest=json.loads((self.path/'manifest.json').read_text())
  self.assertIn('nanojev_game_native_objectives_v6.py',manifest['source_sha256'])
  with sqlite3.connect(self.path/'native_game_task_bank.sqlite3') as db:
   self.assertEqual(db.execute("SELECT COUNT(*) FROM task_cases WHERE usage<>'proof_only'").fetchone()[0],0)

if __name__=='__main__':unittest.main()
