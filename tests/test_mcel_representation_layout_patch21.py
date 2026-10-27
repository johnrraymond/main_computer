"""Patch 2.1 static safeguards. The full test is the browser-gated Frob."""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from tools.mcel_text_shrinker_adapter import load_flat_file
from tools.mcel_representation_layout_contract import project_layout, page_for_layout
from tools.mcel_representation_layout_frob import (
    ADVERSARIAL_WIDGETS, WIDTHS, main as frob_main,
)

ROOT=Path(__file__).resolve().parents[1]
ADVERSARIAL=ROOT/'tools/fixtures/mcel_representation_layout_adversarial.json'


class RepresentationLayoutPatch21Test(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture=load_flat_file(ADVERSARIAL)

    def test_four_independent_datasets_have_source_preservation(self):
        before=deepcopy(self.fixture)
        self.assertEqual(set(self.fixture['widgets']),set(ADVERSARIAL_WIDGETS))
        for name in ADVERSARIAL_WIDGETS:
            widget=self.fixture['widgets'][name]
            for width in WIDTHS:
                off=project_layout(widget,self.fixture['value_sets'],viewport_px=width,enabled=False)
                on=project_layout(widget,self.fixture['value_sets'],viewport_px=width,enabled=True)
                self.assertEqual(off['source_rows'],on['source_rows'])
                self.assertEqual(off['tree'],on['tree'])
                self.assertEqual(on['source_rows'],widget['rows'])
                self.assertFalse(on['projection']['collisions'])
                self.assertTrue(any(
                    a['cells'][k]['display']!=b['cells'][k]['display']
                    for a,b in zip(off['projection']['rows'],on['projection']['rows'])
                    for k in a['cells']))
        self.assertEqual(before,self.fixture)

    def test_missing_and_explicitly_empty_are_different_in_authoritative_rows(self):
        rows=self.fixture['widgets']['missing_fields']['rows']
        self.assertNotIn('owner',rows[0])
        self.assertIn('owner',rows[1])
        self.assertEqual(rows[1]['owner'],'')
        page=page_for_layout(project_layout(self.fixture['widgets']['missing_fields'],
            self.fixture['value_sets'],viewport_px=320,enabled=True),'missing_fields','after')
        self.assertIn('sourceMissing',page)
        self.assertIn('Not provided',page)
        self.assertIn('[empty]',page)
        self.assertIn('overflow-wrap:anywhere',page)

    def test_pairwise_near_collisions_not_hidden_by_unrelated_fields(self):
        widget=self.fixture['widgets']['near_collisions']
        on=project_layout(widget,self.fixture['value_sets'],viewport_px=320,enabled=True)
        shown=[r['cells']['name']['display'] for r in on['projection']['rows']]
        self.assertEqual(len(shown),len(set(shown)))
        self.assertTrue(all(len(x)>0 for x in shown))

    def test_identifier_collision_rejected_by_layout_contract(self):
        widget=self.fixture['widgets']['near_collisions']
        real=project_layout(widget,self.fixture['value_sets'],viewport_px=320,enabled=True)
        broken=deepcopy(real)
        broken['projection']['rows'][1]['cells']['name']['display']=\
            broken['projection']['rows'][0]['cells']['name']['display']
        with patch('tools.mcel_representation_layout_contract.project_collection', return_value=broken):
            with self.assertRaisesRegex(ValueError,'Identity collision'):
                project_layout(widget,self.fixture['value_sets'],viewport_px=320,enabled=True)

    def test_no_browser_cannot_promote_independent_datasets(self):
        with TemporaryDirectory() as tmp:
            self.assertEqual(frob_main(['--no-browser','--output',tmp]),0)
            report=json.loads((Path(tmp)/'report.json').read_text(encoding='utf-8'))
            self.assertEqual(report['status'],'UNVERIFIED')
            self.assertEqual(set(report['independent_datasets']),set(ADVERSARIAL_WIDGETS))
            self.assertIsNone(report['evidence'])


if __name__=='__main__':unittest.main()
