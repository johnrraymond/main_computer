"""Independent compression/pattern and emitted real-browser proof regressions."""
from __future__ import annotations
import copy
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest

from tools import mcel_semantic_generate as gen
from tools import mcel_presentation_planner as planner


class PresentationPlannerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.design=gen.yaml.safe_load(gen.DEFAULT_DESIGN.read_text(encoding='utf-8'))

    def test_all_fields_preserved_with_disclosure(self):
        plan=planner.plan_presentation(self.design)
        self.assertTrue(plan['semantic_preservation'])
        self.assertEqual(plan['stats']['row_types'],len(self.design['rows']['types']))
        for key,row in self.design['rows']['types'].items():
            chosen=plan['rows'][key]
            self.assertEqual(set(chosen['all_fields']),set(row['primary']+row.get('summary',[])))
            self.assertEqual(set(chosen['identity']+chosen['summary']+chosen['deferred']),set(chosen['all_fields']))
            self.assertEqual(chosen['actions'],row.get('actions',[]))
        self.assertGreater(plan['stats']['deferred_row_fields'],0)
        self.assertLess(plan['stats']['visible_row_fields'],plan['stats']['declared_row_fields'])

    def test_recipe_is_role_driven_not_app_name(self):
        altered=copy.deepcopy(self.design)
        altered['application']['id']='other-application'
        altered['application']['name']='Other Application'
        old=planner.plan_presentation(self.design)
        new=planner.plan_presentation(altered)
        self.assertEqual(old,new)
        self.assertEqual(len(new['regions']),6)
        self.assertEqual(len({r['recipe'] for r in new['regions'].values()}),6)

    def test_unfamiliar_domain_collection_supported(self):
        holdout=copy.deepcopy(self.design)
        holdout['application']['id']='holdout-inventory'
        holdout['application']['name']='Inventory Review'
        holdout['semantics']['entities']['inventory_item']={'status':'proposed','identity':['sku']}
        holdout['rows']['types']={'inventory-row':{'entity':'inventory_item','primary':['sku','name'],
            'summary':['status','location','restock_date','supplier','notes'],
            'actions':['inspect_inventory_item'],'status':'proposed'}}
        primary=next(r for r in holdout['hierarchy']['children'] if r['role']=='primary-content')
        primary['views']=[{'id':'inventory','title':'Inventory','sections':[{'id':'stock','row':'inventory-row'}]}]
        plan=planner.plan_presentation(holdout)
        self.assertEqual(plan['stats']['row_types'],1)
        self.assertEqual(plan['rows']['inventory-row']['entity'],'inventory_item')
        self.assertEqual(len(plan['rows']['inventory-row']['all_fields']),7)
        self.assertTrue(plan['semantic_preservation'])
        with tempfile.TemporaryDirectory() as folder:
            d=Path(folder)/'design.yaml'
            d.write_text(gen.yaml.safe_dump(holdout,sort_keys=False),encoding='utf-8')
            self.assertEqual(gen.run(d,Path(folder)/'output',verify=False,presentation_mode='planned'),0)
            manifest=json.loads((Path(folder)/'output'/'manifest.json').read_text(encoding='utf-8'))
            self.assertIn('inventory-row',manifest['presentationPlan']['rows'])

    def test_pattern_registry_missing_is_refusal(self):
        plan=planner.plan_presentation(self.design)
        with self.assertRaisesRegex(ValueError,'not registered'):
            planner.validate_pattern_registry(plan,{'element.core.field'})

    def test_recipe_has_provenance_and_no_effect_override(self):
        plan=planner.plan_presentation(self.design)
        self.assertIn('no presentation pattern enables',plan['effect_contract'])
        for row in plan['rows'].values():self.assertTrue(row['reason'])
        for section in plan['regions'].values():self.assertTrue(section['reason'])

    def test_browser_all_deferred_fields_reachable(self):
        executable=os.environ.get('MCEL_CHROMIUM_EXECUTABLE') or shutil.which('chromium') or shutil.which('chromium-browser')
        if not executable:self.skipTest('Chromium unavailable')
        with tempfile.TemporaryDirectory() as folder:
            self.assertEqual(gen.run(gen.DEFAULT_DESIGN,Path(folder),verify=True,presentation_mode='planned'),0)
            report=json.loads((Path(folder)/'report.json').read_text(encoding='utf-8'))
            self.assertEqual(report['status'],'PASS')
            self.assertEqual(report['presentation']['mode'],'planned')
            for width,record in report['browser']['viewports'].items():
                self.assertEqual(record['status'],'PASS',record)
                self.assertTrue(record['checks']['deferred_fields_inspectable'])
                self.assertTrue(record['checks']['presentation_semantics_preserved'])
                self.assertTrue(record['checks']['presentation_rows_accounted_for'])
                self.assertTrue(record['checks']['presentation_pattern_rendered'])
                self.assertEqual(record['evidence']['presentation_disclosure']['checked'],11)
                self.assertEqual(record['evidence']['componentCount'],153)
                self.assertTrue(record['checks']['destructive_disabled'])

if __name__=='__main__':unittest.main()
