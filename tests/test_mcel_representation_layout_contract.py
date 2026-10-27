"""Patch 2 semantic-to-layout invariants: browser Frob is separately executable."""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from tools.mcel_text_shrinker_adapter import load_flat_file
from tools.mcel_representation_layout_contract import project_layout, page_for_layout, SCHEMA
from tools.mcel_representation_layout_frob import main as frob_main

FIXTURE = Path(__file__).resolve().parents[1]/'tools/fixtures/mcel_text_shrinker_widgets.json'


class RepresentationLayoutContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = load_flat_file(FIXTURE)

    def test_contract_and_identity_stable_at_multiple_viewports(self):
        source = deepcopy(self.fixture)
        for widget in ('downloads','inventory'):
            for width in (320,390,560,640,1100):
                off = project_layout(source['widgets'][widget],source['value_sets'],viewport_px=width,enabled=False)
                on = project_layout(source['widgets'][widget],source['value_sets'],viewport_px=width,enabled=True)
                self.assertEqual(off['layout_link']['schema'], SCHEMA)
                self.assertEqual(off['source_rows'], on['source_rows'])
                self.assertEqual(off['tree'],on['tree'])
                self.assertTrue(off['layout_link']['required_keys'])
                for left,right in zip(off['projection']['rows'],on['projection']['rows']):
                    self.assertEqual(left['id'],right['id'])
                    for key,old in left['cells'].items():
                        new = right['cells'][key]
                        self.assertEqual(old['raw'],new['raw'])
                        self.assertEqual(str(old['raw']),old['display'])
                        self.assertEqual(str(new['raw']),new['full'])
                self.assertTrue(on['layout_link']['identifier_keys'])
                self.assertFalse(on['layout_link']['production_integrated'])
        self.assertEqual(source,self.fixture)

    def test_only_semantically_opted_in_fields_change(self):
        off=project_layout(self.fixture['widgets']['downloads'],self.fixture['value_sets'],viewport_px=560,enabled=False)
        on=project_layout(self.fixture['widgets']['downloads'],self.fixture['value_sets'],viewport_px=560,enabled=True)
        for a,b in zip(off['projection']['rows'],on['projection']['rows']):
            self.assertNotEqual(a['cells']['name']['display'], b['cells']['name']['display'])
            self.assertEqual(a['cells']['modified']['display'],b['cells']['modified']['display'])
            self.assertTrue(b['cells']['size']['enabled'])
            self.assertEqual(b['cells']['name']['raw'],a['cells']['name']['raw'])

    def test_disable_all_optins_preserves_strings(self):
        widget=deepcopy(self.fixture['widgets']['downloads'])
        for column in widget['columns']:
            column['presentation']['text_shrinker']['enabled']=False
        result=project_layout(widget,self.fixture['value_sets'],viewport_px=560,enabled=True)
        self.assertTrue(all(cell['display']==str(cell['raw']) for row in result['projection']['rows']
                            for cell in row['cells'].values()))

    def test_browser_layout_script_provided_on_actual_elements(self):
        on=project_layout(self.fixture['widgets']['downloads'],self.fixture['value_sets'],viewport_px=560,enabled=True)
        html=page_for_layout(on,'downloads','after')
        self.assertIn('window.__MCEL_REPRESENTATION_LAYOUT',html)
        self.assertIn('element.core.collection',html)
        self.assertIn('McelElementRegistry',html)
        self.assertIn('measured field text',html)
        self.assertNotIn('document.write(',html)

    def test_no_browser_cannot_report_pass(self):
        with TemporaryDirectory() as tmp:
            rc=frob_main(['--no-browser','--output',tmp])
            summary=json.loads((Path(tmp)/'report.json').read_text(encoding='utf-8'))
            self.assertEqual(rc,0)
            self.assertEqual(summary['status'],'UNVERIFIED')
            self.assertIsNone(summary['evidence'])
            self.assertTrue((Path(tmp)/'representation-layout-review.zip').is_file())


if __name__ == '__main__': unittest.main()
