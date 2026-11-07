"""The actual shared semantic Chat renderer; no Calculator-specific workaround."""
from __future__ import annotations

import unittest
from pathlib import Path

from tools import mcel_chat_console_layout_oracle as oracle

ROOT=Path(__file__).resolve().parents[1]
APPLICATIONS=(ROOT/'main_computer/web/applications.html').read_text(encoding='utf-8')
CHAT_RUNTIME=(ROOT/'main_computer/web/applications/scripts/chat-console.js').read_text(encoding='utf-8')
CALCULATOR=(ROOT/'mcel_apps/calculator/application.js').read_text(encoding='utf-8')


class IntegrationContractTests(unittest.TestCase):
    def test_semantic_layout_is_loaded_after_the_existing_app_styles(self):
        self.assertIn('applications/styles/mcel-semantic-chat-console.css', APPLICATIONS)
        self.assertGreater(APPLICATIONS.index('applications/styles/mcel-semantic-chat-console.css'),
                           APPLICATIONS.index('applications/styles/calculator.css'))
        self.assertLess(APPLICATIONS.index('applications/scripts/mcel-semantic-chat-console.js'),
                        APPLICATIONS.index('applications/scripts/chat-console.js'))

    def test_existing_embedding_and_executor_survive(self):
        self.assertIn('function chatConsoleMountEmbedded(root, options = {})', CHAT_RUNTIME)
        self.assertIn('mountEmbedded: chatConsoleMountEmbedded', CHAT_RUNTIME)
        self.assertIn('renderTabs: () => renderChatConsoleTabs(cell)', CHAT_RUNTIME)
        self.assertIn('renderBody: () => cell.type === "output"', CHAT_RUNTIME)
        self.assertIn('onFiles: (files) => addChatConsoleAttachments(cell.id, files)', CHAT_RUNTIME)
        # The Calculator semantic app remains completely independent.
        self.assertIn('embedded', CALCULATOR)

    def test_oracle_rejects_fake_marker_without_semantic_roles(self):
        from tests.test_mcel_chat_console_layout_oracle import clean_observation
        sample=clean_observation()
        sample['semanticContract']=oracle.SEMANTIC_CONTRACT
        sample['semanticRegions']=[]
        result=oracle.evaluate_layout('embedded',sample,require_semantic=True)
        self.assertIn('semantic-region-coverage-missing',result['failures'])
        sample['semanticRegions']=list({'shell','thread-layout','workspace','notebook','cell',
            'cell-choices','cell-editor','cell-actions','thinking','thinking-collection','thinking-card','output'})
        self.assertEqual(oracle.evaluate_layout('embedded',sample,require_semantic=True)['status'],'PASS')


class BrowserSemanticChatTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as e:
            raise unittest.SkipTest('Playwright not installed') from e
        cls.playwright=sync_playwright().start()
        try:cls.browser=oracle._launch(cls.playwright)
        except Exception as e:
            cls.playwright.stop()
            raise unittest.SkipTest('Chromium not available: '+str(e)[:120]) from e

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()

    def test_fixture_repaired_at_every_standalone_and_embedded_width(self):
        for context,widths in (('standalone',oracle.VIEWPORTS),('embedded',oracle.EMBED_WIDTHS)):
            for width in widths:
                with self.subTest(context=context,width=width):
                    result=oracle.fixture_sample(self.browser,context,width,semantic=True)
                    self.assertEqual(result['status'],'PASS',result['failures'])
                    self.assertEqual(result['measured']['semantic_contract'],oracle.SEMANTIC_CONTRACT)

    def test_existing_legacy_fixture_is_still_red(self):
        for context,width in (('standalone',320),('embedded',500)):
            with self.subTest(context=context):
                result=oracle.fixture_sample(self.browser,context,width)
                self.assertEqual(result['status'],'FAIL')
                self.assertIn('cell-type-choices-overflow-notebook',result['failures'])

    def test_resize_reflows_without_remount(self):
        result=oracle.fixture_resize_sequence(self.browser,'embedded',(680,500,390,680),semantic=True)
        self.assertEqual(result['status'],'PASS',result['steps'])

    def test_semantic_renderer_preserves_dom_node_handlers(self):
        page=self.browser.new_page()
        try:
            page.set_content('<!doctype html><div id="chat-console-app"></div>')
            page.add_script_tag(content=oracle.SEMANTIC_RUNTIME.read_text(encoding='utf-8'))
            outcome=page.evaluate('''() => {
                const composer=window.MainComputerMCELChatConsole;
                let selected=0,attached=0,runs=0;
                const tabs=document.createElement('nav');tabs.className='chat-cell-tabs';
                const button=document.createElement('button');button.textContent='AI';
                button.addEventListener('click',()=>runs++);tabs.append(button);
                const body=document.createElement('div');
                const input=document.createElement('textarea');input.value='unsaved draft';body.append(input);
                const element=composer.renderCell({id:'cell-1',type:'ai',renderTabs:()=>tabs,
                    renderBody:()=>body,onSelect:()=>selected++,onFiles:files=>attached+=files.length});
                document.querySelector('#chat-console-app').append(element);
                button.click();input.focus();input.value='still unsaved';
                const drag=new Event('dragover',{bubbles:true,cancelable:true});element.dispatchEvent(drag);
                const drop=new Event('drop',{bubbles:true,cancelable:true});
                Object.defineProperty(drop,'dataTransfer',{value:{files:[new File(['x'],'x.txt')]}});
                element.dispatchEvent(drop);
                return {runs,selected,attached,dragPrevented:drag.defaultPrevented,
                    dirtyDraft:input.value,focus:document.activeElement===input,
                    role:element.getAttribute('data-mcel-chat-role'),
                    tabRole:tabs.getAttribute('data-mcel-chat-role'),
                    bodyRole:body.getAttribute('data-mcel-chat-role')};
            }''')
            self.assertEqual(outcome['runs'],1)
            self.assertGreaterEqual(outcome['selected'],1)
            self.assertEqual(outcome['attached'],1)
            self.assertTrue(outcome['dragPrevented'])
            self.assertEqual(outcome['dirtyDraft'],'still unsaved')
            self.assertTrue(outcome['focus'])
            self.assertEqual(outcome['role'],'cell')
            self.assertEqual(outcome['tabRole'],'cell-choices')
            self.assertEqual(outcome['bodyRole'],'cell-body')
        finally:page.close()

    def test_output_cells_cannot_be_drag_drop_editors(self):
        page=self.browser.new_page()
        try:
            page.set_content('<!doctype html><div id="chat-console-app"></div>')
            page.add_script_tag(content=oracle.SEMANTIC_RUNTIME.read_text(encoding='utf-8'))
            outcome=page.evaluate('''() => {
                let attempted=0;
                const make=()=>document.createElement('div');
                const el=window.MainComputerMCELChatConsole.renderCell({id:'out',type:'output',
                    renderTabs:make,renderBody:make,onFiles:()=>attempted++});
                el.dispatchEvent(new Event('drop',{bubbles:true,cancelable:true}));
                return {attempted,role:el.dataset.mcelChatRole};
            }''')
            self.assertEqual(outcome,{'attempted':0,'role':'output'})
        finally:page.close()

    def test_incomplete_shell_is_not_misrepresented_as_semantically_composed(self):
        page=self.browser.new_page()
        try:
            page.set_content('<!doctype html><div id="chat-console-app"><div class="chat-console-shell"></div></div>')
            page.add_script_tag(content=oracle.SEMANTIC_RUNTIME.read_text(encoding='utf-8'))
            result=page.evaluate('''() => {
                const root=document.querySelector('#chat-console-app');
                return {result:window.MainComputerMCELChatConsole.compose(root),marker:root.hasAttribute('data-mcel-semantic-chat')};
            }''')
            self.assertEqual(result,{'result':None,'marker':False})
        finally:page.close()


if __name__=='__main__':unittest.main()
