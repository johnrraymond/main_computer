"""Chat Console oracle is independent of the legacy compositor and catches bad embeds."""
from __future__ import annotations

import unittest
from unittest.mock import patch

from tools import mcel_chat_console_layout_oracle as oracle

# This is only a reference *test fixture* for a correctly composed responsive
# notebook, not a repair shipped to production. It proves the oracle can pass.
REFERENCE_COMPOSITION = '''
.chat-console-shell,.chat-console-thread-layout,.chat-thread-workspace,.chat-console-notebook,
.chat-cell {min-width:0;max-width:100%;}
.chat-cell-tabs {position:static;top:auto;left:auto;display:flex;flex-wrap:wrap;
  overflow:visible;max-width:100%;gap:3px;}
.chat-cell-tab {min-width:0;max-width:100%;}
.chat-cell textarea {min-width:0;max-width:100%;}
.chat-console-cell-output.chat-console-output-indent {width:100%;margin-left:0;}
'''


def rect(x=0,y=0,w=100,h=40):
    return {'x':x,'y':y,'w':w,'h':h,'right':x+w,'bottom':y+h}


def clean_observation():
    return {
        'root':rect(0,0,500,950),'shell':rect(0,0,500,900),
        'notebook':rect(10,80,480,780),'nav':rect(10,10,200,60),
        'workspace':rect(0,70,500,800),
        'cells':[rect(20,110,440,200),rect(20,330,440,140)],
        'tabs':[rect(25,118,350,30)],
        'tabButtons':[[rect(30,120,80,25),rect(120,120,80,25)]],
        'editorInputs':[rect(30,165,400,90)],'controlRows':[rect(20,265,400,35)],
        'controlButtons':[rect(25,269,80,29),rect(110,269,80,29)],
        'thinking':[rect(30,200,400,80)],
        'thinkingCards':[rect(30,210,190,50),rect(230,210,190,50)],
        'outputs':[rect(20,330,440,140)],'ragControls':[rect(20,180,420,35)],
        'scrolledX':False,'bodyScrolledX':False,'semanticMarker':True
    }


class PureChatLayoutOracleTests(unittest.TestCase):
    def test_reference_standalone_and_embedded_pass(self):
        for context in oracle.ORACLE['contexts']:
            with self.subTest(context=context):
                self.assertEqual(oracle.evaluate_layout(context,clean_observation())['status'],'PASS')

    def test_missing_notebook_content_does_not_pass(self):
        candidate=clean_observation()
        candidate['cells']=[];candidate['outputs']=[]
        result=oracle.evaluate_layout('embedded',candidate)
        self.assertEqual(result['status'],'FAIL')
        self.assertIn('missing-populated-cells',result['failures'])
        self.assertIn('missing-output-cell',result['failures'])

    def test_tab_clip_is_detected_even_with_zero_browser_overflow(self):
        candidate=clean_observation()
        candidate['tabButtons']=[[rect(30,120,80,25),rect(470,120,75,25)]]
        result=oracle.evaluate_layout('embedded',candidate)
        self.assertEqual(result['status'],'FAIL')
        self.assertIn('cell-type-choices-overflow-notebook',result['failures'])
        self.assertIn('tab-button-escapes-host',result['failures'])

    def test_thinking_card_width_refused(self):
        candidate=clean_observation()
        candidate['thinkingCards']=[rect(30,210,60,50),rect(100,210,60,50)]
        self.assertIn('thinking-card-unreadably-narrow',oracle.evaluate_layout('embedded',candidate)['failures'])

    def test_controls_cannot_overlap_or_lose_click_target(self):
        candidate=clean_observation()
        candidate['controlButtons']=[rect(25,269,80,29),rect(75,269,80,29)]
        self.assertIn('action-buttons-overlap',oracle.evaluate_layout('embedded',candidate)['failures'])
        candidate=clean_observation()
        candidate['controlButtons'][0]=rect(25,269,80,12)
        self.assertIn('control-too-small',oracle.evaluate_layout('embedded',candidate)['failures'])

    def test_out_of_host_rejected_not_just_document_scroll(self):
        candidate=clean_observation()
        candidate['outputs']=[rect(455,330,90,140)]
        self.assertIn('output-escapes-host',oracle.evaluate_layout('embedded',candidate)['failures'])

    def test_standalone_thread_navigation_required(self):
        candidate=clean_observation()
        candidate['nav']=None
        self.assertIn('missing-thread-navigation',oracle.evaluate_layout('standalone',candidate)['failures'])
        self.assertNotIn('missing-thread-navigation',oracle.evaluate_layout('embedded',candidate)['failures'])

    def test_semantic_migration_gate_is_independent_of_visual_fit(self):
        candidate=clean_observation()
        candidate['semanticMarker']=False
        self.assertEqual(oracle.evaluate_layout('embedded',candidate)['status'],'PASS')
        self.assertIn('raw-chat-renderer-not-migrated',
                      oracle.evaluate_layout('embedded',candidate,require_semantic=True)['failures'])

    def test_contexts_are_explicit(self):
        with self.assertRaisesRegex(ValueError,'Unknown Chat Console context'):
            oracle.evaluate_layout('text-console',clean_observation())

    def test_only_loopback_http_accepted_for_hosted_mode(self):
        with self.assertRaises(SystemExit):oracle.main(['--mode','hosted','--host-url','https://example.com'])
        with self.assertRaises(SystemExit):oracle.main(['--mode','hosted','--host-url','http://example.com'])


class BrowserChatLayoutOracleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:from playwright.sync_api import sync_playwright
        except ImportError as exc:raise unittest.SkipTest('Playwright not installed') from exc
        cls.pw=sync_playwright().start()
        try:cls.browser=oracle._launch(cls.pw)
        except Exception as exc:
            cls.pw.stop();raise unittest.SkipTest('Chromium unavailable: '+str(exc)[:160]) from exc

    @classmethod
    def tearDownClass(cls):
        cls.browser.close();cls.pw.stop()

    def test_legacy_embedded_fixture_fails_at_narrow_host_width_inside_wide_viewport(self):
        for width in (320,390,480,500):
            with self.subTest(width=width):
                result=oracle.fixture_sample(self.browser,'embedded',width)
                self.assertEqual(result['status'],'FAIL')
                self.assertIn('cell-type-choices-overflow-notebook',result['failures'])

    def test_legacy_standalone_narrow_layout_fails(self):
        for width in (320,390):
            with self.subTest(width=width):
                result=oracle.fixture_sample(self.browser,'standalone',width)
                self.assertIn('cell-type-choices-overflow-notebook',result['failures'])

    def test_well_composed_reference_passes_every_width_without_changing_app(self):
        for context,widths in [('standalone',oracle.VIEWPORTS),('embedded',oracle.EMBED_WIDTHS)]:
            for width in widths:
                with self.subTest(context=context,width=width):
                    result=oracle.fixture_sample(self.browser,context,width,repair_css=REFERENCE_COMPOSITION)
                    self.assertEqual(result['status'],'PASS',result['failures'])

    def test_mounted_chat_reflows_when_calculator_dock_resizes(self):
        widths=(680,500,390,680)
        before=oracle.fixture_resize_sequence(self.browser,'embedded',widths)
        self.assertEqual(before['status'],'FAIL')
        self.assertEqual(before['steps'][0]['status'],'PASS')
        self.assertEqual(before['steps'][1]['status'],'FAIL')
        after=oracle.fixture_resize_sequence(self.browser,'embedded',widths,repair_css=REFERENCE_COMPOSITION)
        self.assertEqual(after['status'],'PASS',after['steps'])
        self.assertTrue(all(step['status']=='PASS' for step in after['steps']))

    def test_missing_cell_input_not_silently_accepted(self):
        with patch.object(oracle,'_sample_cell',return_value='<article class="chat-cell"></article>'):
            result=oracle.fixture_sample(self.browser,'embedded',390,repair_css=REFERENCE_COMPOSITION)
        self.assertEqual(result['status'],'FAIL')
        self.assertIn('missing-editable-input',result['failures'])


if __name__=='__main__':unittest.main()
