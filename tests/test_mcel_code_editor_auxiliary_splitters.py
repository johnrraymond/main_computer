"""Canonical Code Editor auxiliary resize controls must stay out of grid authority."""
from __future__ import annotations

from pathlib import Path

from main_computer.mcel_dsl_compiler import compile_dsl_application
from main_computer.mcel_application_ir import _executable_layout_tree_errors
from test_mcel_package_canonical_layout_mount import _browser, _page, payload, async_mount

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / 'main_computer/web/applications/scripts/code-editor-layout-contract.js'


def test_splitter_selectors_are_authored_in_normalized_layout_tree():
    report = compile_dsl_application(ROOT / 'mcel_apps/code-editor/application.js', write_candidate=False)
    assert report.valid, report.diagnostics
    tree = report.normalized_ir['layouts'][0]['layoutTree']
    shell = tree['root']
    workbench = next(child for child in shell['children'] if child['id'] == 'workbench')
    assert shell['auxiliarySelectors'] == ['[data-code-editor-layout-splitter="proof"]']
    assert workbench['auxiliarySelectors'] == [
        '[data-code-editor-layout-splitter="explorer"]',
        '[data-code-editor-layout-splitter="inspector"]',
    ]
    assert not _executable_layout_tree_errors(tree)
    # The strict compiler must reject catch-all and malformed declarations.
    shell['auxiliarySelectors'] = []
    assert _executable_layout_tree_errors(tree)
    shell['auxiliarySelectors'] = ['.code-editor-layout-splitter'] * 2
    assert _executable_layout_tree_errors(tree)


def test_real_generated_splitters_mount_with_canonical_authority_and_fail_closed():
    for browser in _browser():
        page = _page(browser, 'code-editor')
        page.add_script_tag(path=str(CONTRACT))
        before = page.evaluate('''() => {
          const root = document.querySelector('#code-editor-app');
          root.style.setProperty('display','block','important');
          root.style.width='1220px'; root.style.height='800px';
          root.dataset.codeEditorMode='legacy-fidelity';
          const rail=root.querySelector('.code-studio-activitybar');
          rail.style.setProperty('display','grid','important');
          rail.style.width='52px'; rail.style.height='370px';rail.style.padding='2px';
          const ctrl=MainComputerCodeEditorLayout.mount(root);
          const host=root.querySelector('.code-studio-shell');
          const body=root.querySelector('.code-studio-body');
          const handles=[...root.querySelectorAll('[data-code-editor-layout-splitter]')];
          return {live:root.dataset.mcelLayoutLive, count:handles.length,
            names:handles.map(h=>h.dataset.codeEditorLayoutSplitter).sort(),
            positions:handles.map(h=>getComputedStyle(h).position),
            owners:handles.map(h=>h.parentElement===body?'workbench':h.parentElement===host?'editor-shell':'other'),
            roles:handles.map(h=>h.getAttribute('role')),
            tabbable:handles.every(h=>h.tabIndex===0),
            controller:!!ctrl};
        }''')
        assert before['live'] == 'true', before
        assert before['count'] == 3, before
        assert before['names'] == ['explorer','inspector','proof'], before
        assert before['positions'] == ['absolute'] * 3, before
        assert before['owners'].count('workbench') == 2 and before['owners'].count('editor-shell') == 1, before
        assert before['roles'] == ['separator'] * 3 and before['tabbable'], before
        mounted = page.evaluate(async_mount, payload('code-editor'))
        assert mounted['status'] == 'pass', mounted
        positive = page.evaluate('''() => {
          const root=document.querySelector('#code-editor-app'),
                host=root.querySelector('.code-studio-shell'),
                mount=McelHostBoundApplicationRuntime.getMount('code-editor'),
                tree=mount.modules.layout.layoutTree,
                evidence=McelSemanticLayoutInference.evaluateCanonicalLayoutTree(host,tree);
          const originalTree=structuredClone(tree);
          delete originalTree.root.auxiliarySelectors;
          delete originalTree.root.children.find(child=>child.id==='workbench').auxiliarySelectors;
          const originalResult=McelSemanticLayoutInference.evaluateCanonicalLayoutTree(host,originalTree);
          const explorer=root.querySelector('[data-code-editor-layout-splitter="explorer"]');
          const controller=root.__mcelCodeEditorLayoutController;
          const shareBefore=controller.preferences.units['code-editor.explorer'].preferredShare;
          explorer.dispatchEvent(new KeyboardEvent('keydown',{key:'ArrowRight',bubbles:true}));
          const shareAfter=controller.preferences.units['code-editor.explorer'].preferredShare;
          return {mount:!!mount, status:mount.layoutStatus, candidates:evidence.candidates.length,
                  accepted:evidence.candidates.filter(c=>c.accepted).length,
                  layoutAuthority:host.dataset.mcelCanonicalLayoutAuthority,
                  originalAccepted:originalResult.candidates.filter(c=>c.accepted).length,
                  originalUndeclared:originalResult.candidates.filter(c=>c.evidence.failures.some(f=>f.code==='undeclared-visible-layout-region')).length,
                  shareBefore,shareAfter,handleStillPresent:explorer.isConnected};
        }''')
        assert positive['mount'] and positive['status'] == 'pass', positive
        assert positive['candidates'] == 6 and positive['accepted'] >= 1, positive
        assert positive['layoutAuthority'] == 'mcel-layout-tree.v1', positive
        assert positive['originalAccepted'] == 0 and positive['originalUndeclared'] == 6, positive
        assert positive['shareAfter'] > positive['shareBefore'] and positive['handleStillPresent'], positive
        # A new unrelated visible child is still rejected by ALL candidates.
        unexpected = page.evaluate('''() => {
          const root=document.querySelector('#code-editor-app'), host=root.querySelector('.code-studio-shell');
          const el=document.createElement('div'); el.textContent='unexpected layout child';
          el.style.cssText='display:block;width:150px;height:20px';host.append(el);
          const tree=McelHostBoundApplicationRuntime.getMount('code-editor').modules.layout.layoutTree;
          const ev=McelSemanticLayoutInference.evaluateCanonicalLayoutTree(host,tree);
          el.remove();
          return {status:ev.status, accepted:ev.candidates.filter(c=>c.accepted).length,
            count:ev.candidates.filter(c=>c.evidence.failures.some(f=>f.code==='undeclared-visible-layout-region')).length};
        }''')
        assert unexpected['accepted'] == 0 and unexpected['count'] == 6, unexpected
        # A declared separator put back in ordinary flow must fail its explicit out-of-flow check.
        in_flow = page.evaluate('''() => {
          const root=document.querySelector('#code-editor-app'), host=root.querySelector('.code-studio-shell');
          const splitter=root.querySelector('[data-code-editor-layout-splitter="explorer"]');
          splitter.style.setProperty('position','static','important');
          const tree=McelHostBoundApplicationRuntime.getMount('code-editor').modules.layout.layoutTree;
          const ev=McelSemanticLayoutInference.evaluateCanonicalLayoutTree(host,tree);
          splitter.style.removeProperty('position');
          return {accepted:ev.candidates.filter(c=>c.accepted).length,
            count:ev.candidates.filter(c=>c.evidence.failures.some(f=>f.code==='auxiliary-control-in-layout-flow')).length};
        }''')
        assert in_flow['accepted'] == 0 and in_flow['count'] == 6, in_flow
        duplicate = page.evaluate('''() => {
          const root=document.querySelector('#code-editor-app'), host=root.querySelector('.code-studio-shell');
          const splitter=root.querySelector('[data-code-editor-layout-splitter="explorer"]');
          const clone=splitter.cloneNode(false);splitter.parentElement.append(clone);
          try {
            const tree=McelHostBoundApplicationRuntime.getMount('code-editor').modules.layout.layoutTree;
            McelSemanticLayoutInference.evaluateCanonicalLayoutTree(host,tree);
            return {rejected:false};
          } catch (e) {return {rejected:String(e).includes('auxiliary structure')}}
          finally {clone.remove();}
        }''')
        assert duplicate['rejected'], duplicate
        page.evaluate("McelHostBoundApplicationRuntime.getMount('code-editor').unmount()")
        page.close()
