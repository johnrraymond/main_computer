"""SCM route and in-memory draft lifecycle, not an authorization to write source."""
import json
from playwright.sync_api import sync_playwright
from tools.mcel_phase2b_browser import launch_semantic_browser
from main_computer.mcel_semantic_other_apps_host import compile_semantic_app
from tests.test_mcel_semantic_editor_scm_runtime_lifecycle import _port_page


def test_paired_semantic_app_declares_governed_in_memory_studio_lifecycle():
    ir = compile_semantic_app('code-editor')
    intents = {i['id']:i for i in ir['intents']}
    for op in ['route-enter','stage','commit','route-leave']:
        assert intents['intent:studio-runtime-'+op]['risk']=='external-read'
    assert not ir['layouts']


def test_real_scm_route_dirty_block_commit_and_replay_without_host_mutation():
    with sync_playwright() as p:
        browser,_=launch_semantic_browser(p)
        try:
            page=_port_page(browser)
            result=page.evaluate('''async()=>{
              const port=window.__port;
              const methods={mount:'mountStudioRuntime','route-enter':'enterStudioRoute',
                stage:'stageStudioDraft',commit:'commitStudioDraftInMemory',
                'route-leave':'leaveStudioRoute',evidence:'readStudioRuntimeEvidence',
                replay:'replayStudioRuntime'};
              const args={path:'example.txt',text:'hello world'};
              const req=name=>({appId:'semantic-code-editor',intentId:'intent:studio-runtime-'+name,
                effectId:'effect:studio-runtime-'+name+'.request',capabilityId:'capability:scm-runtime',
                operation:name,runtimeMethod:methods[name],risk:'external-read',arguments:args});
              const run=async name=>JSON.parse(await port.invoke(req(name)));
              await port.invoke(window.__req('list-files',{}));
              await port.invoke(window.__req('open-source',{path:'example.txt'}));
              const before=await port.authorize(req('commit'));
              const mount=await run('mount');
              const initial=await port.authorize(req('commit'));
              const route=await run('route-enter');
              const stage=await run('stage');
              const blocked=await run('route-leave');
              const commit=await run('commit');
              const leave=await run('route-leave');
              const replay=await run('replay');
              return {before,initial,mount,route,stage,blocked,commit,leave,replay,
                calls:window.__requestLog};
            }''')
            assert result['before'] is False
            assert result['initial'] is True  # authorization may pass; execution must refuse if not staged
            assert result['route']['routeStatus']=='entered'
            assert result['stage']['inMemoryDraftCommitted'] is False
            assert result['blocked']['routeLeaveBlocked'] is True
            assert 'component.state.dirty' in result['blocked']['routeBlockers']
            assert result['commit']['inMemoryDraftCommitted'] is True
            assert result['commit']['sourceMutation']=='none'
            assert result['leave']['routeStatus']=='left'
            assert result['replay']['deterministicStateMatch'] is True
            assert result['calls']==['/api/applications/editor/files','/api/applications/editor/read']
        finally:
            browser.close()
