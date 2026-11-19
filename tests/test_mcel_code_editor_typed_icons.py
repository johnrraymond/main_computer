"""Code Editor typed semantics and independent rendered-control oracle.

This tests the source used by the promoted Code Editor HTTP route, not the
separate host-bound authoring DSL. The oracle does not feed the resolver.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

from main_computer.mcel_semantic_other_apps_host import compile_semantic_app, render_semantic_page

ROOT = Path(__file__).resolve().parents[1]
SPEC = json.loads((ROOT / 'tests/fixtures/mcel-semantic-icon-oracle.v1.json').read_text())['applications']['code-editor']

# Original capability executable identity and risk are independent of icon
# semantics. These high-impact operations must never be rebound by migration.
EXISTING_BINDINGS = {
    'list-files': ('list', 'listSourceFiles', 'external-read'),
    'open-source': ('read', 'readSource', 'external-read'),
    'browse-directory': ('directory', 'listDirectory', 'external-read'),
    'save-source': ('save', 'saveSource', 'source-write'),
    'run-aider': ('run', 'runAider', 'consequential'),
    'reset-aider-context': ('reset', 'resetAiderContext', 'consequential'),
    'load-aider-archive': ('load', 'loadAiderArchive', 'consequential'),
    'apply-reviewed-change': ('apply', 'applyReviewedChange', 'source-write'),
    'studio-runtime-commit': ('commit', 'commitStudioDraftInMemory', 'external-read'),
    'studio-runtime-route-enter': ('route-enter', 'enterStudioRoute', 'external-read'),
    'studio-runtime-route-leave': ('route-leave', 'leaveStudioRoute', 'external-read'),
    'studio-runtime-serialize': ('serialize', 'serializeStudioRuntime', 'external-read'),
    'studio-proof-export': ('packet', 'exportStudioProofPacket', 'external-read'),
}

SEMANTIC_EXPECTATIONS = {
    'list-files': ('open', 'folder'),
    'open-source': ('open', 'file'),
    'browse-directory': ('open', 'folder'),
    'save-source': ('save', 'file'),
    'run-aider': ('run', 'task'),
    'reset-aider-context': ('reset', 'session'),
    'load-aider-archive': ('open', 'session'),
    'apply-reviewed-change': ('apply', 'document'),
    'studio-runtime-commit': ('apply', 'document'),
    'studio-proof-export': ('download', 'document'),
}


def test_promoted_code_editor_compiles_typed_operations_without_rebinding_capabilities():
    ir = compile_semantic_app('code-editor', ROOT)
    assert ir['application']['source']['file'] == SPEC['source']
    assert ir['application']['appId'] == SPEC['appId']
    intents = {x['id'].removeprefix('intent:'): x for x in ir['intents']}
    assert len(intents) == 39
    assert sum('typedOperation' in i for i in intents.values()) == 33
    for name, (operation, method, risk) in EXISTING_BINDINGS.items():
        i = intents[name]
        assert (i['operation'], i['runtimeMethod'], i['risk']) == (operation, method, risk), name
        assert len(i['effectRefs']) == 1
    for name, (kind, target) in SEMANTIC_EXPECTATIONS.items():
        assert intents[name]['typedOperation'] == {
            'schema': 'mcel.typed-operation.v1', 'kind': kind, 'target': target
        }, name
    # These remain legacy: the current type vocabulary cannot express
    # internal route navigation / serialization without misclassification.
    for name in ('studio-runtime-route-enter', 'studio-runtime-route-leave', 'studio-runtime-serialize'):
        assert 'typedOperation' not in intents[name]
    source = (ROOT / SPEC['source']).read_text()
    assert 'action.' not in source and 'navigation.' not in source and 'resource.' not in source


def test_rendered_code_editor_and_document_save_match_oracle_without_icon_duplicates():
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, executable_path=shutil.which('chromium') or None)
        page = browser.new_page(viewport={'width': 1280, 'height': 900})
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        requests = []
        try:
            page.route('**/api/**', lambda route: (
                requests.append({'url': route.request.url, 'method': route.request.method}),
                route.fulfill(status=503, content_type='application/json', body='{"ok":false}')
            ))
            page.set_content(render_semantic_page('code-editor', ROOT, promoted=True),
                             wait_until='domcontentloaded', timeout=30000)
            page.wait_for_function('''() => document.querySelector('[data-mcel-document-save]')
              ?.querySelector('[data-mcel-icon-identity="action.save"]')!==null''')
            actual = page.evaluate('''() => {
              const root=document.querySelector('#mcel-workbench');
              const nodes={};
              for(const holder of root.querySelectorAll('[data-mcel-node][data-node-kind="control"], [data-mcel-node][data-node-kind="form"]')) {
                const id=holder.getAttribute('data-mcel-node').split('.').pop();
                const b=holder.getAttribute('data-node-kind')==='form'
                  ?holder.querySelector('form button[type="submit"]')
                  :holder.querySelector('button[data-intent]');
                const slots=b?.querySelectorAll(':scope > [data-mcel-icon-core="slot"]')||[];
                nodes[id]={icon:slots[0]?.getAttribute('data-mcel-icon-identity')||null,
                  slots:slots.length};
              }
              const generated={};
              for(const b of root.querySelectorAll('[data-mcel-generated-action]')) {
                const type=b.getAttribute('data-mcel-generated-action');
                (generated[type]??=[]).push({id:b.querySelector(':scope > [data-mcel-icon-core="slot"]')?.getAttribute('data-mcel-icon-identity')||null,
                  count:b.querySelectorAll(':scope > [data-mcel-icon-core="slot"]').length});
              }
              return {nodes,generated,appId:JSON.parse(document.querySelector('#mcel-workbench-ir').textContent).application.appId,
                report:McelSemanticIconCore.inspect(root)};
            }''')
            assert actual['appId'] == SPEC['appId']
            assert set(actual['nodes']) == set(SPEC['nodes'])
            for name, icon in SPEC['nodes'].items():
                assert actual['nodes'][name] == {'icon': icon, 'slots': int(icon is not None)}, (name, actual)
            for type_, icon in SPEC['generatedControls'].items():
                assert type_ in actual['generated']
                assert all(x == {'id': icon, 'count': 1} for x in actual['generated'][type_]), (type_, actual)
            assert actual['report']['resolved'] == 32, actual['report']
            assert actual['report']['unresolved'] == 3, actual['report']
            assert actual['report']['ambiguous'] == 0, actual['report']
            assert not errors, errors
            # The icon renderer must not invoke capabilities or mutate source.
            assert requests == [], requests
        finally:
            browser.close()


def test_semantic_icon_vocabulary_has_no_app_specific_resolution():
    core = (ROOT / 'main_computer/web/applications/scripts/mcel-semantic-icon-core.js').read_text()
    for forbidden in ('semantic-code-editor', 'studio-proof-', 'aider-', 'open-source'):
        assert forbidden not in core
