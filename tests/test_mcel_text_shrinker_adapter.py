"""Small deterministic contract tests for the experimental shrinker adapter."""
import copy
import json
import tempfile
import unittest
from pathlib import Path

from tools.mcel_text_shrinker_adapter import adapt_widget, load_flat_file, render_html

FIXTURE = Path(__file__).resolve().parents[1] / 'tools' / 'fixtures' / 'mcel_text_shrinker_widgets.json'


class TestTextShrinkerAdapter(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.doc = load_flat_file(FIXTURE)

    def adapt(self, widget='downloads', viewport=1100, *, doc=None):
        doc = doc or self.doc
        return adapt_widget(doc['widgets'][widget], doc['value_sets'], viewport_px=viewport)

    def test_01_human_lookup_context_discards_obvious_affixes(self):
        result = self.adapt()
        cell = result['rows'][0]['cells']['name']
        self.assertEqual(cell['display'], 'nanojev_zero_line_distribution_convergence')
        self.assertEqual(cell['full'], 'main_computer_test_nanojev_zero_line_distribution_convergence_patch')
        self.assertEqual(result['inference']['name']['shared_prefix'], 'main_computer_test_')
        self.assertEqual(result['inference']['name']['shared_suffix'], '_patch')

    def test_02_required_size_remains_present(self):
        for px in (320, 390, 768, 1100):
            out = self.adapt(viewport=px)
            for row in out['rows']:
                self.assertTrue(row['cells']['size']['display'])
                self.assertEqual(row['cells']['size']['raw'], self.doc['widgets']['downloads']['rows'][int(row['id'][1:]) - 1]['size'])

    def test_03_size_precision_uses_reference_values(self):
        result = self.adapt()
        self.assertNotEqual(result['rows'][0]['cells']['size']['display'], result['rows'][1]['cells']['size']['display'])

    def test_04_disabled_field_is_exact(self):
        result = self.adapt(viewport=320)
        self.assertEqual(result['rows'][0]['cells']['modified']['display'], '2026-10-25')
        self.assertFalse(result['rows'][0]['cells']['modified']['enabled'])

    def test_05_no_shrinker_optin_means_no_compression(self):
        doc = copy.deepcopy(self.doc)
        doc['widgets']['downloads']['columns'][0]['presentation']['text_shrinker']['enabled'] = False
        out = self.adapt(doc=doc)
        self.assertEqual(out['rows'][0]['cells']['name']['display'], doc['widgets']['downloads']['rows'][0]['name'])

    def test_06_inventory_unrelated_holdout(self):
        out = self.adapt(widget='inventory')
        self.assertEqual([r['cells']['sku']['display'] for r in out['rows']], ['blue', 'red', 'green'])
        self.assertEqual([r['cells']['quantity']['display'] for r in out['rows']], ['110','3','85'])

    def test_07_reference_edit_changes_result(self):
        doc = copy.deepcopy(self.doc)
        doc['value_sets']['main_computer_patch_names'][0] = 'unrelated_test_value_patch'
        out = self.adapt(doc=doc)
        self.assertNotEqual(out['rows'][0]['cells']['name']['display'], self.adapt()['rows'][0]['cells']['name']['display'])

    def test_08_repeat_is_bitwise_deterministic(self):
        a = self.adapt()
        b = self.adapt()
        self.assertEqual(json.dumps(a, sort_keys=True), json.dumps(b, sort_keys=True))

    def test_09_invalid_reference_refused(self):
        doc = copy.deepcopy(self.doc)
        doc['widgets']['downloads']['columns'][0]['presentation']['text_shrinker']['values_ref'] = 'not-in-fixture'
        with self.assertRaises(ValueError): self.adapt(doc=doc)

    def test_10_immutable_input(self):
        doc = copy.deepcopy(self.doc)
        before = json.dumps(doc, sort_keys=True)
        self.adapt(doc=doc)
        self.assertEqual(json.dumps(doc, sort_keys=True), before)

    def test_11_html_escaping(self):
        doc = copy.deepcopy(self.doc)
        doc['widgets']['downloads']['rows'][0]['modified'] = '<script>bad()</script>'
        html_text = render_html(self.adapt(doc=doc), 'downloads')
        self.assertNotIn('<script>', html_text)
        self.assertIn('&lt;script&gt;', html_text)

    def test_12_mobile_preview_stacked_and_exact_names(self):
        out = self.adapt(viewport=320)
        self.assertEqual(out['layout'], 'stacked')
        page = render_html(out, 'downloads')
        self.assertIn('class=\'record\'', page)
        self.assertIn('main_computer_test_nanojev_zero_line_distribution_convergence_patch', page)
        self.assertIn('Size', page)

    def test_13_flat_json_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'fixture.json'
            path.write_text(json.dumps(self.doc), encoding='utf-8')
            self.assertEqual(load_flat_file(path), self.doc)

    def test_14_potential_duplicate_id_refused(self):
        doc = copy.deepcopy(self.doc)
        doc['widgets']['downloads']['rows'][1]['id'] = doc['widgets']['downloads']['rows'][0]['id']
        with self.assertRaises(ValueError): self.adapt(doc=doc)


if __name__ == '__main__':
    unittest.main()
