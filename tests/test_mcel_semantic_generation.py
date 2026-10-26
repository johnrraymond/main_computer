"""Read-only MCEL semantic generator/hint and actual-browser acceptance."""
from __future__ import annotations
import copy
from pathlib import Path
import os
import shutil
import tempfile
import unittest
from tools import mcel_semantic_generate as gen


class SemanticGenerationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.design=gen.yaml.safe_load(gen.DEFAULT_DESIGN.read_text(encoding='utf-8'))

    def test_design_hints_have_source_authority(self):
        self.assertTrue(gen.validate_design(self.design))
        self.assertGreaterEqual(len(gen.checked_hints(self.design)),3)
        self.assertEqual(gen.plan(self.design,390)['selected']['decisions'][0]['strength'],'prefer')

    def test_hint_scope_is_explicit(self):
        desktop=gen.plan(self.design,1440)
        mobile=gen.plan(self.design,390)
        self.assertIn('attention-desktop-upper-left',desktop['applicable_hints'])
        self.assertNotIn('attention-desktop-upper-left',mobile['applicable_hints'])
        self.assertIn('attention-mobile-first',mobile['applicable_hints'])

    def test_progressive_strength_changes_selection(self):
        priority=next(x for x in self.design['hierarchy']['children'] if x['role']=='status-and-attention')['id']
        hint={'id':'priority-hint','target':priority,'property':'position','value':'upper-left','when':'desktop'}
        weak=gen.plan(self.design,1440,override=[{**hint,'strength':'suggest'}])
        strong=gen.plan(self.design,1440,override=[{**hint,'strength':'require'}])
        self.assertNotEqual(weak['selected']['fingerprint'],strong['selected']['fingerprint'])
        self.assertFalse(weak['selected']['decisions'][0]['satisfied'])
        self.assertTrue(strong['selected']['decisions'][0]['satisfied'])

    def test_conflicting_mandatory_hints_refuse(self):
        a=gen.frob.regions(self.design)['attention']
        b=gen.frob.regions(self.design)['work']
        hints=[{'id':'attention-must-first','target':a,'property':'position','value':'upper-left','strength':'require'},
               {'id':'work-must-first','target':b,'property':'position','value':'upper-left','strength':'require'}]
        with self.assertRaisesRegex(ValueError,'MCEL_LAYOUT_HINT_CONFLICT'):
            gen.plan(self.design,1440,override=hints)

    def test_unrecognized_hint_never_silently_passes(self):
        modified=copy.deepcopy(self.design)
        modified['layout_hints'].append({'id':'bad-hint','target':'priority-area','property':'gravity','value':'up','strength':'prefer'})
        with self.assertRaisesRegex(ValueError,'unsupported hint property'):
            gen.validate_design(modified)

    def test_row_relationships_and_identity_are_preserved(self):
        tree=gen.build_component_tree(self.design)
        self.assertEqual(tree['elementId'],'element.core.app')
        self.assertEqual(len(tree['children']),6)
        rows=[node for region in tree['children'] for node in region['children'] if node['props'].get('rowType')]
        self.assertEqual({row['props']['rowType'] for row in rows},set(self.design['rows']['types']))
        for node in rows:
            self.assertEqual(node['props']['semanticEntity'],self.design['rows']['types'][node['props']['rowType']]['entity'])
            self.assertTrue(all(child['elementId'] in ('element.core.field','element.core.action') for child in node['children']))

    def test_real_registry_serialization_rejects_illegal_children(self):
        with tempfile.TemporaryDirectory() as tmp:
            good=gen.build_component_tree(self.design)
            result=gen.serialize_components(good,Path(tmp))
            self.assertEqual(result['counts']['element.core.region'],6)
            self.assertEqual(sum(result['counts'].values()),153)
            invalid=copy.deepcopy(good)
            invalid['children'][0]['children']=[gen._component('forbidden','element.core.app','Invalid')]
            with self.assertRaisesRegex(ValueError,'MCEL_REGISTRY_REFUSED'):
                gen.serialize_components(invalid,Path(tmp))

    def test_no_task_manager_specific_role_names_required_for_planning(self):
        cloned=copy.deepcopy(self.design)
        cloned['application']['id']='other-application'
        cloned['application']['name']='Other Application'
        renames={r['id']:'other-'+r['id'] for r in cloned['hierarchy']['children']}
        for r in cloned['hierarchy']['children']:r['id']=renames[r['id']]
        for hint in cloned['layout_hints']:
            hint['target']=renames[hint['target']]
            if isinstance(hint['value'],dict):hint['value']={k:renames[v] for k,v in hint['value'].items()}
        gen.validate_design(cloned)
        self.assertEqual(len(gen.plan(cloned,1440)['candidates']),len(gen.plan(self.design,1440)['candidates']))

    def test_no_non_fixture_production_effects_are_generated(self):
        with tempfile.TemporaryDirectory() as tmp:
            result=gen.run(gen.DEFAULT_DESIGN,Path(tmp),verify=False)
            self.assertEqual(result,0)
            preview=(Path(tmp)/'preview.html').read_text(encoding='utf-8')
            script=gen.RUNTIME_JS.read_text(encoding='utf-8')
            self.assertNotIn('fetch(',script)
            self.assertNotIn('XMLHttpRequest',script)
            self.assertIn('SYNTHETIC / NOT LIVE',preview)
            self.assertNotIn('main_computer/task_manager.py',script)

    def test_responsive_resize_reapplies_canonical_layout(self):
        executable=os.environ.get('MCEL_CHROMIUM_EXECUTABLE') or shutil.which('chromium') or shutil.which('chromium-browser')
        if not executable:self.skipTest('Chromium unavailable')
        try:from playwright.sync_api import sync_playwright
        except ImportError:self.skipTest('Playwright unavailable')
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(gen.run(gen.DEFAULT_DESIGN,Path(tmp),verify=False),0)
            source=(Path(tmp)/'preview.html').read_text(encoding='utf-8')
            with sync_playwright() as pw:
                browser=pw.chromium.launch(executable_path=executable,headless=True,args=['--no-sandbox'])
                try:
                    page=browser.new_page(viewport={'width':390,'height':850})
                    errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
                    page.set_content(source,wait_until='load')
                    page.wait_for_function('window.__MCEL_GENERATED_READY === true')
                    for width,want in ((390,1),(1440,2),(390,1),(768,2),(320,1),(1920,2)):
                        page.set_viewport_size({'width':width,'height':850})
                        page.wait_for_function("count => getComputedStyle(document.getElementById('generated-root')).gridTemplateColumns.split(' ').filter(Boolean).length===count",arg=want)
                        self.assertEqual(page.evaluate('window.__MCEL_LAYOUT_PROOF.status'),'pass')
                    self.assertEqual(errors,[])
                finally:browser.close()

    def test_visual_evidence_validation_rejects_tampered_or_missing_files(self):
        from tools.mcel_frob_visual_evidence import png_size,verify_capture,write_gallery
        with tempfile.TemporaryDirectory() as tmp:
            out=Path(tmp)
            captured={'id':'sample','kind':'generated-app','label':'sample','viewport':320,
                      'view':'overview','state':'default','screenshot':'missing.png',
                      'screenshot_sha256':'incorrect','pixel_width':320,'pixel_height':800,'full_page':True}
            with self.assertRaisesRegex(ValueError,'Missing screenshot'):
                verify_capture(out,captured)
            with self.assertRaisesRegex(ValueError,'Missing screenshot'):
                write_gallery(out,[captured])

    def test_genuine_generated_browser_interactions(self):
        executable=os.environ.get('MCEL_CHROMIUM_EXECUTABLE') or shutil.which('chromium') or shutil.which('chromium-browser')
        if not executable:self.skipTest('Chromium unavailable')
        try:import playwright
        except ImportError:self.skipTest('Playwright unavailable')
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(gen.run(gen.DEFAULT_DESIGN,Path(tmp),verify=True),0)
            report=gen.json.loads((Path(tmp)/'report.json').read_text(encoding='utf-8'))
            self.assertEqual(report['status'],'PASS')
            for width,entry in report['browser']['viewports'].items():
                self.assertEqual(entry['status'],'PASS',width)
                self.assertTrue(all(entry['checks'].values()),width)
                self.assertEqual(entry['evidence']['componentCount'],153)
            self.assertEqual(report['dsl_compiler']['valid'],True)
            self.assertEqual(report['dsl_compiler']['tree_preserved'],True)
            visual=report['browser']['visual_evidence']
            self.assertEqual(visual['status'],'CAPTURED_NOT_REVIEWED')
            self.assertTrue(visual['full_page'])
            self.assertEqual(visual['count'],39)
            self.assertEqual(len(visual['views']),5)
            for target in ('gallery.html','visual-manifest.json','visual-review-template.csv'):
                self.assertTrue((Path(tmp)/target).is_file(),target)
            from tools.mcel_frob_visual_evidence import png_size,verify_capture
            manifest=gen.json.loads((Path(tmp)/'visual-manifest.json').read_text(encoding='utf-8'))
            self.assertEqual(manifest['review_status'],'UNREVIEWED')
            self.assertEqual(len(manifest['captures']),39)
            captures=manifest['captures']
            self.assertEqual(len({item['id'] for item in captures}),39)
            for item in captures:
                verify_capture(Path(tmp),item)
                self.assertTrue(item['full_page'])
                self.assertFalse(item['observed']['horizontalOverflow'], item['id'])
                self.assertEqual(item['observed']['layoutStatus'],'pass',item['id'])
                if item['state'].startswith('operation_'):
                    self.assertEqual(item['observed']['visualFixture'],item['state'])
            self.assertIn('long_text',{item['state'] for item in captures})
            self.assertIn('search_empty',{item['state'] for item in captures})
            self.assertGreaterEqual(png_size(Path(tmp)/'preview-1440.png')[1],900) # compact full-page can equal viewport
            gallery=(Path(tmp)/'gallery.html').read_text(encoding='utf-8')
            self.assertEqual(gallery.count('<article '),39)
            self.assertIn('not a visual-quality PASS',gallery)


if __name__=='__main__':unittest.main()
