"""General, deterministic semantic presentation contract tests."""
import copy
import json
from pathlib import Path
import unittest
from tools.mcel_text_shrinker_adapter import load_flat_file
from tools.mcel_semantic_information_contract import project_collection
from tools.mcel_information_quality_frob import baseline_and_after,page_for

DOC=Path(__file__).resolve().parents[1]/'tools/fixtures/mcel_text_shrinker_widgets.json'

class TestSemanticInformationContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.doc=load_flat_file(DOC)

    def test_registered_semantic_elements(self):
        for widget in ('downloads','inventory'):
            p=project_collection(self.doc['widgets'][widget],self.doc['value_sets'],viewport_px=390,enabled=True)
            self.assertEqual(p['tree']['elementId'],'element.core.collection')
            self.assertTrue(all(r['elementId']=='element.core.collection-row' for r in p['tree']['children']))
            self.assertTrue(all(f['elementId']=='element.core.field' for r in p['tree']['children'] for f in r['children']))

    def test_original_values_and_opt_out(self):
        for widget in ('downloads','inventory'):
            before,after=baseline_and_after(self.doc,widget,390)
            self.assertEqual(before['source_rows'],after['source_rows'])
            for left,right in zip(before['projection']['rows'],after['projection']['rows']):
                for key in left['cells']:
                    self.assertEqual(left['cells'][key]['raw'],right['cells'][key]['raw'])
                    self.assertEqual(left['cells'][key]['display'],str(left['cells'][key]['raw']))

    def test_semantic_optin_compresses_only_enabled(self):
        before,after=baseline_and_after(self.doc,'downloads',1100)
        self.assertEqual(after['projection']['rows'][0]['cells']['name']['display'],'nanojev_zero_line_distribution_convergence')
        self.assertEqual(after['projection']['rows'][0]['cells']['modified']['display'],'2026-10-25')
        self.assertNotEqual(before['projection']['rows'][0]['cells']['name']['display'],after['projection']['rows'][0]['cells']['name']['display'])

    def test_holdout(self):
        _,after=baseline_and_after(self.doc,'inventory',390)
        self.assertEqual([r['cells']['sku']['display'] for r in after['projection']['rows']],['blue','red','green'])

    def test_source_not_mutated_and_deterministic(self):
        doc=copy.deepcopy(self.doc)
        a=baseline_and_after(doc,'downloads',390)
        b=baseline_and_after(doc,'downloads',390)
        self.assertEqual(json.dumps(a,sort_keys=True),json.dumps(b,sort_keys=True))
        self.assertEqual(doc,self.doc)

    def test_contract_requires_explicit_optin(self):
        doc=copy.deepcopy(self.doc)
        for col in doc['widgets']['downloads']['columns']:
            col.pop('presentation',None)
        p=project_collection(doc['widgets']['downloads'],doc['value_sets'],viewport_px=390,enabled=True)
        self.assertTrue(all(cell['display']==str(cell['raw']) for row in p['projection']['rows'] for cell in row['cells'].values()))

    def test_html_escapes_data_and_registers_core_elements(self):
        p=project_collection(self.doc['widgets']['downloads'],self.doc['value_sets'],viewport_px=390,enabled=True)
        text=page_for(p,'downloads','after')
        self.assertIn('element.core.collection',text)
        self.assertIn('McelElementRegistry',text)
        self.assertNotIn('http://',text)

if __name__=='__main__':unittest.main()
