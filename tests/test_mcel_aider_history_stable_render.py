"""Browser regression for the stable Aider history renderer.

Run from repository root: python -m unittest discover -s tests -p test_mcel_aider_history_stable_render.py
Requires Playwright and Chromium; skips if unavailable.
"""
from __future__ import annotations

import copy
import os
import unittest
from pathlib import Path

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None

SCRIPTS = Path(__file__).resolve().parents[1] / 'main_computer/web/applications/scripts'
SAMPLE = {
    'active': {'id': 'thread-1', 'archive_id': 'arch-1', 'entries': [{
        'id': 'entry-1', 'kind': 'run', 'timestamp': '2026-10-20T20:00:00Z',
        'instruction': 'find <a>', 'result_excerpt': 'done', 'file_count': 2,
        'repo_dir': '/repo', 'dry_run': False, 'returncode': 0
    }]},
    'archives': [{'id': 'arch-1', 'label': 'Archive 1', 'entry_count': 1}],
    'activities': [],
}
STUBS = r'''
let aiderContextState = {active: {entries: []}, archives: [], activities: []};
const aiderSessionMeta = document.querySelector('#aider-session-meta');
const aiderHistoryList = document.querySelector('#aider-history-list');
const aiderArchiveList = document.querySelector('#aider-archive-list');
const aiderArchiveMeta = document.querySelector('#aider-archive-meta');
window.aiderUpdates = {activity: 0, polling: 0, route: 0};
function escapeHtml(value) {
  return String(value == null ? '' : value)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}
function latestAiderActivityForArchive() { return null; }
function syncAiderThreadRoute() { window.aiderUpdates.route++; }
function restoreAiderEditorState() {}
function renderAttachedAiderActivity() { window.aiderUpdates.activity++; }
function syncAiderActivityPolling() { window.aiderUpdates.polling++; }
function updateAiderRunButtonState() {}
'''

@unittest.skipIf(sync_playwright is None, 'Playwright unavailable')
class StableAiderHistoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.playwright = sync_playwright().start()
        options = {'headless': True}
        if os.environ.get('CHROMIUM_EXECUTABLE'):
            options['executable_path'] = os.environ['CHROMIUM_EXECUTABLE']
        if os.name != 'nt':
            options['args'] = ['--no-sandbox']
        try:
            cls.browser = cls.playwright.chromium.launch(**options)
        except Exception as exc:
            cls.playwright.stop()
            raise unittest.SkipTest(f'Chromium unavailable: {exc}') from exc

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, 'browser'):
            cls.browser.close()
        if hasattr(cls, 'playwright'):
            cls.playwright.stop()

    def setUp(self):
        self.page = self.browser.new_page()
        self.page.set_content('''
          <div id="aider-session-meta"></div>
          <section id="aider-history-list"></section>
          <select id="aider-archive-list"></select>
          <div id="aider-archive-meta"></div>
        ''')
        self.page.add_script_tag(content=STUBS)
        helpers = (SCRIPTS / 'code-editor-aider-output.js').read_text(encoding='utf-8')
        start = helpers.index('    function formatAiderContextTimestamp(value)')
        end = helpers.index('    function aiderHistoryDetail(entry)', start)
        end = helpers.index('\n    }', end) + len('\n    }')
        self.page.add_script_tag(content=helpers[start:end])
        self.page.add_script_tag(content=(SCRIPTS / 'code-editor-aider-context.js').read_text(encoding='utf-8'))

    def tearDown(self):
        self.page.close()

    def render(self, value):
        self.page.evaluate('(data) => renderAiderContext(data)', value)

    def test_unchanged_history_keeps_card_and_disclosure_while_activity_updates(self):
        result = self.page.evaluate('''(data) => {
          renderAiderContext(data);
          const list = document.querySelector('#aider-history-list');
          const card = list.firstElementChild;
          const disclosure = card.querySelector('details');
          disclosure.open = false;
          const observer = new MutationObserver(() => {});
          observer.observe(list, {childList: true, subtree: true, attributes: true, characterData: true});
          const changed = JSON.parse(JSON.stringify(data));
          changed.activities = [{id: 'active', status: 'running'}];
          changed.active.updated_at = '2026-10-20T20:05:00Z';
          changed.archives[0].preview = 'new archive status';
          renderAiderContext(changed);
          const mutations = observer.takeRecords().length;
          observer.disconnect();
          return {
            sameCard: list.firstElementChild === card,
            detailsClosed: !disclosure.open,
            mutations,
            polling: window.aiderUpdates.polling,
            activity: window.aiderUpdates.activity,
            archiveUpdated: document.querySelector('#aider-archive-meta').textContent.includes('new archive status'),
            sessionUpdated: document.querySelector('#aider-session-meta').textContent.includes('updated')
          };
        }''', SAMPLE)
        self.assertEqual(result, {'sameCard': True, 'detailsClosed': True, 'mutations': 0,
                                  'polling': 2, 'activity': 2,
                                  'archiveUpdated': True, 'sessionUpdated': True})

    def test_changed_result_updates_card(self):
        value = copy.deepcopy(SAMPLE)
        self.render(value)
        value['active']['entries'][0]['result_excerpt'] = 'revised result'
        result = self.page.evaluate('''(data) => {
          const before = document.querySelector('#aider-history-list article');
          renderAiderContext(data);
          const after = document.querySelector('#aider-history-list article');
          return {newNode: before !== after, changedText: after.textContent.includes('revised result')};
        }''', value)
        self.assertEqual(result, {'newNode': True, 'changedText': True})

    def test_thread_switch_rebuilds_even_with_same_entries(self):
        self.render(SAMPLE)
        value = copy.deepcopy(SAMPLE)
        value['active']['id'] = 'thread-2'
        value['active']['archive_id'] = 'arch-2'
        self.assertTrue(self.page.evaluate('''(data) => {
          const before = document.querySelector('#aider-history-list article');
          renderAiderContext(data);
          return document.querySelector('#aider-history-list article') !== before;
        }''', value))

    def test_empty_and_restored_history(self):
        self.render(SAMPLE)
        value = copy.deepcopy(SAMPLE)
        value['active']['entries'] = []
        self.render(value)
        self.assertEqual(self.page.locator('article.aider-history-entry').count(), 0)
        self.assertIn('No Aider web-context history', self.page.locator('#aider-history-list').inner_text())
        self.render(SAMPLE)
        self.assertEqual(self.page.locator('article.aider-history-entry').count(), 1)

    def test_new_entry_preserves_reverse_order(self):
        value = copy.deepcopy(SAMPLE)
        value['active']['entries'].append({
            'id': 'entry-2', 'kind': 'run', 'timestamp': '2026-10-20T20:10:00Z',
            'instruction': 'next step', 'result_excerpt': 'second result'
        })
        self.render(value)
        keys = self.page.locator('article.aider-history-entry').evaluate_all(
            'nodes => nodes.map(node => node.dataset.mcItemKey)'
        )
        self.assertEqual(keys, ['entry-2', 'entry-1'])

if __name__ == '__main__':
    unittest.main()
