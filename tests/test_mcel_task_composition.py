"""Task-aware compositions must preserve MCEL identities, semantics, and safety."""
from __future__ import annotations
import copy
import json
from pathlib import Path
import tempfile
import unittest

from tools import mcel_semantic_generate as gen
from tools.mcel_presentation_planner import plan_presentation
from tools.mcel_task_composition import SCHEMA, COMPOSITES, natural_label, plan_task_composition


class TaskCompositionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.design=gen.yaml.safe_load(gen.DEFAULT_DESIGN.read_text(encoding='utf-8'))
        cls.plan=plan_task_composition(cls.design,plan_presentation(cls.design))

    def test_generic_composite_coverage_and_provenance(self):
        self.assertEqual(self.plan['schema'],SCHEMA)
        self.assertEqual(set(self.plan['role_ids']),{r['role'] for r in self.design['hierarchy']['children']})
        self.assertEqual(len(set(self.plan['role_ids'].values())),6)
        self.assertTrue(self.plan['preservation']['all_fields_inspectable'])
        self.assertFalse(self.plan['preservation']['actions_authorized_by_composition'])
        self.assertEqual(self.plan['authoring']['app_specific_css_rules'],0)
        self.assertEqual(self.plan['authoring']['added_author_hints'],0)

    def test_labels_are_generic_and_never_change_raw_identifiers(self):
        self.assertEqual(natural_label('process_row'),'Process')
        self.assertEqual(natural_label('memory_rss'),'Memory RSS')
        self.assertEqual(natural_label('cpu_percent'),'CPU Percent')
        self.assertEqual(natural_label('unused','A custom label'),'A custom label')
        self.assertEqual(self.plan['labels']['rows']['process-row'],'Process')
        self.assertIn('cpu_percent', self.plan['labels']['fields'])
        self.assertIn('process-row',self.design['rows']['types'])

    def test_requires_real_semantics_and_complete_roles(self):
        bad=copy.deepcopy(plan_presentation(self.design));bad['semantic_preservation']=False
        with self.assertRaisesRegex(ValueError,'lost semantics'):
            plan_task_composition(self.design,bad)
        bad=copy.deepcopy(plan_presentation(self.design));del bad['regions']['primary-content']
        with self.assertRaisesRegex(ValueError,'role coverage'):
            plan_task_composition(self.design,bad)

    def test_no_application_name_heuristics(self):
        other=copy.deepcopy(self.design)
        other['application']['name']='Unrelated Application'
        other['application']['id']='unrelated'
        plan=plan_task_composition(other,plan_presentation(other))
        self.assertEqual(plan,self.plan)

    def test_independent_inventory_schema_without_new_patterns(self):
        other=copy.deepcopy(self.design)
        other['application']['name']='Warehouse Inventory'
        other['application']['id']='warehouse-inventory'
        other['semantics']['entities']['inventory_item']={'identity':['sku'],'status':'proposed'}
        other['rows']['types']={'inventory-row':{
            'entity':'inventory_item','primary':['sku','name'],
            'summary':['quantity','location','restock_date','supplier','notes'],
            'actions':['inspect_inventory_item'],'status':'proposed'}}
        primary=next(r for r in other['hierarchy']['children'] if r['role']=='primary-content')
        primary['views']=[{'id':'inventory','title':'Inventory','sections':[{'id':'stock','row':'inventory-row'}]}]
        plan=plan_task_composition(other,plan_presentation(other))
        self.assertEqual(plan['labels']['rows']['inventory-row'],'Inventory')
        self.assertEqual(plan['views'],['inventory'])
        self.assertEqual(set(plan['composites']),set(COMPOSITES))
        with tempfile.TemporaryDirectory() as tmp:
            design=Path(tmp)/'design.yaml'
            design.write_text(gen.yaml.safe_dump(other,sort_keys=False),encoding='utf-8')
            self.assertEqual(gen.run(design,Path(tmp)/'result',verify=False,presentation_mode='planned'),0)
            manifest=json.loads((Path(tmp)/'result'/'manifest.json').read_text())
            self.assertEqual(manifest['compositionPlan']['labels']['rows']['inventory-row'],'Inventory')

    def test_fixture_browser_composite_geometry_and_interaction(self):
        import os,shutil
        if not (os.environ.get('MCEL_CHROMIUM_EXECUTABLE') or shutil.which('chromium') or shutil.which('chromium-browser')):
            self.skipTest('Chromium unavailable')
        with tempfile.TemporaryDirectory() as folder:
            result=Path(folder)
            self.assertEqual(gen.run(gen.DEFAULT_DESIGN,result,verify=True,presentation_mode='planned'),0)
            report=json.loads((result/'report.json').read_text())
            for width,data in report['browser']['viewports'].items():
                self.assertEqual(data['status'],'PASS')
                self.assertTrue(data['checks']['composite_structure'])
                self.assertTrue(data['checks']['task_modes'])
                self.assertTrue(data['checks']['natural_labels'])
                self.assertTrue(data['checks']['task_region_integrity'])
                self.assertEqual(data['evidence']['composition']['mounted'],153)
                self.assertEqual(data['evidence']['inspect_task'],'inspect')
                self.assertEqual(data['evidence']['search_task'],'search')
                self.assertFalse(data['evidence']['overflow'])

if __name__=='__main__':unittest.main()
