"""Canonical Task Manager source, lifecycle isolation and parity refusal tests."""
from pathlib import Path
import pytest
from main_computer.task_manager import TaskManagerService
from main_computer.mcel_semantic_generated_host import P3_SOURCE,P3_APP_ID,compile_p3_generated_candidate
from tools.mcel_task_manager_acceptance.lab import isolated_server_lifecycle
from tools.mcel_task_manager_acceptance.evidence import ROWS,source_ledger
ROOT=Path(__file__).resolve().parents[2]

def test_canonical_source_and_generated_identity_preserved():
    assert P3_SOURCE.as_posix()=='mcel_apps/task-manager/application.js'
    ir,_=compile_p3_generated_candidate()
    assert ir['application']['appId']==P3_APP_ID=='semantic-task-manager-generated-p3'
    assert not (ROOT/'tools/fixtures/mcel_p3_task_manager/application.js').exists()

def test_parity_requires_ten_real_workflows():
    assert len(ROWS)==10
    ledger=source_ledger(ROOT)
    assert len(ledger['rows'])==10
    assert all(x['liveParity']=='UNVERIFIED' for x in ledger['rows'])

def test_isolated_lifecycle_requires_ack():
    result=isolated_server_lifecycle()
    assert result['status']=='BLOCKED' and result['controllerFidelity']=='UNVERIFIED'

def test_injected_lifecycle_refuses_production_port_and_shared_control_root(tmp_path):
    with pytest.raises(ValueError):
        TaskManagerService(tmp_path,default_port=8765,control_root=tmp_path/'private',isolated_server_executor=lambda x:{'ok':True})
    with pytest.raises(ValueError):
        TaskManagerService(tmp_path,default_port=18651,control_root=tmp_path,isolated_server_executor=lambda x:{'ok':True})

def test_lab_does_not_certify_unverified_production_controller():
    pytest.importorskip('psutil')
    result=isolated_server_lifecycle(ack=True)
    assert result['isolatedLifecycle']=='PASS',result
    assert result['status']=='BLOCKED' and result['controllerFidelity']=='UNVERIFIED'
    assert result['productionPortTouched'] is False
    assert [v['operation'] for v in result['transitions']]==['start','stop','start','restart','stop']

def test_isolated_executor_ignores_production_environment(monkeypatch,tmp_path):
    monkeypatch.setenv('MAIN_COMPUTER_CONTROL_ROOT',str(tmp_path/'production'))
    monkeypatch.setenv('MAIN_COMPUTER_CONTROL_PORT','8765')
    monkeypatch.setenv('MAIN_COMPUTER_HEARTBEAT_PORT','8766')
    root=tmp_path/'lab'
    svc=TaskManagerService(tmp_path,default_port=23457,control_root=root,
                           isolated_server_executor=lambda verb:{'ok':True})
    assert svc.control_root==root.resolve()
    assert svc.port==23457 and svc.heartbeat_port==23458


def test_parity_mode_state_uses_each_ui_aria_contract():
    # The generated mode switcher is a button group, not an ARIA tablist.
    from tools.mcel_task_manager_acceptance.hosted import _mode_selection_evidence
    class Locator:
        def __init__(self, attrs):self.attrs=attrs
        def count(self):return 1
        def get_attribute(self,key):return self.attrs.get(key)
    class Page:
        def __init__(self,attrs):self.attrs=attrs
        def locator(self,selector):return Locator(self.attrs)
    matched=_mode_selection_evidence(Page({'aria-selected':'true'}),
        Page({'aria-pressed':'true','aria-selected':None}), 'all-processes','all-processes')
    assert matched['legacy'] and matched['generated']
    assert matched['generatedAttribute']=='aria-pressed'
    failed=_mode_selection_evidence(Page({'aria-selected':'true'}),
        Page({'aria-pressed':'false'}), 'all-processes','all-processes')
    assert not failed['generated']


def test_generated_mode_switcher_runtime_contract():
    script=(ROOT/'main_computer/web/applications/scripts/mcel-semantic-operational-runtime.js').read_text(encoding='utf-8')
    assert 'button.setAttribute("aria-pressed",String(active))' in script
    assert '"data-mcel-mode-tabs"' in script


def test_canonical_all_process_and_schedule_selection_are_operational_semantics():
    ir, _ = compile_p3_generated_candidate()
    by_intent = {i['id']: i for i in ir['intents']}
    by_node = {n['id'].rsplit('.', 1)[-1]: n for n in ir['surfaces'][0]['nodes']}
    assert len(ir['intents']) == 19
    for node, intent, source, target in (
        ('all-process-list','choose-all-process','state:all-processes','state:selected'),
        ('schedule-list','choose-schedule','state:schedules','state:selected-schedule'),
    ):
        assert by_node[node]['selectionIntent'] == {'ref':'intent:'+intent}
        selection = by_intent['intent:'+intent]['collectionSelection']
        assert selection['source'] == {'ref':source}
        assert selection['target'] == {'ref':target}
        assert by_intent['intent:'+intent]['risk'] == 'local-state'
    for operation in ('run-schedule','delete-schedule'):
        assert by_intent['intent:'+operation]['requestArguments'] == [
            {'name':'scheduleId','state':{'ref':'state:selected-schedule'}}]
        assert by_node[operation]['nodeKind']=='control'
    assert not ir.get('layouts')


def test_generic_inspector_resolves_multiple_semantic_collection_sources():
    code = (ROOT/'main_computer/web/applications/scripts/mcel-semantic-operational-runtime.js').read_text()
    assert 'const selections=[...c.intents.values()].filter' in code
    assert 'const values=read(ref(source.source))' in code
    assert 'if(record)break' in code


def test_refresh_request_match_requires_exact_submission():
    from tools.mcel_task_manager_acceptance.hosted import _overview_request
    from types import SimpleNamespace
    host='http://127.0.0.1:8765'
    req=SimpleNamespace(url=host+'/api/applications/task/overview',method='POST',
                        post_data_json={'query':'python','limit':24,'include_all':False})
    assert _overview_request(req,host,query='python',limit=24,include_all=False)
    assert not _overview_request(req,host,query='python',limit=50,include_all=False)
    assert not _overview_request(req,host,query='other',limit=24,include_all=False)
    assert not _overview_request(req,host,query='python',limit=24,include_all=True)
    req.method='GET'
    assert not _overview_request(req,host,query='python',limit=24)


class _RefreshTestPage:
    """Virtual Playwright event clock: distinguish old responses from new requests."""
    def __init__(self, tag, host, clock):
        self.tag=tag
        self.host=host
        self.clock=clock
        self.watchers={'request':[],'response':[]}
        self.paused=False
    def locator(self,selector):
        return _RefreshTestControl(self, selector)
    def bring_to_front(self):
        self.clock['foreground']=self.tag
    def evaluate(self,expression):
        return {'visibility':'visible','activeApp':'task-manager'}
    def on(self,event,callback):
        self.watchers[event].append(callback)
    def remove_listener(self,event,callback):
        self.watchers[event].remove(callback)
    def emit(self,event,request=None):
        from types import SimpleNamespace
        if request is None:
            request=SimpleNamespace(url=self.host+'/api/applications/task/overview',
                                    method='POST',post_data_json={'query':'python','limit':24,'include_all':False})
        value=request if event=='request' else SimpleNamespace(request=request,url=request.url,status=200)
        for callback in list(self.watchers[event]):
            callback(value)
    def expect_response(self, predicate, timeout):
        from types import SimpleNamespace
        page=self
        class Pending:
            def __enter__(self):return self
            def __exit__(self,*args):
                req=SimpleNamespace(url=page.host+'/api/applications/task/overview',
                                    method='POST',post_data_json={'query':'python','limit':24,'include_all':False})
                self.value=SimpleNamespace(request=req,url=req.url,status=200)
                assert predicate(self.value)
        return Pending()
    def wait_for_timeout(self,ms):
        if ms==500:return
        if ms==5600:
            # Simulates an overview request started *before* pause, whose HTTP
            # response completes after pause. This must not become a failure.
            self.emit('response')
            if self.clock.get('new_request_while_paused'):
                self.emit('request')
                self.emit('response')
        if ms==6500 and self.clock.get('foreground')==self.tag:
            if self.tag not in self.clock.get('no_resume_for',[]):
                self.emit('request')
                self.emit('response')


class _RefreshTestControl:
    def __init__(self,page,selector):self.page=page;self.selector=selector
    def locator(self,selector):return _RefreshTestControl(self.page,selector)
    def fill(self,value):pass
    def click(self):
        if self.selector=='[data-mcel-refresh-toggle]':
            self.page.paused=not self.page.paused
    def check(self):self.page.paused=False
    def uncheck(self):self.page.paused=True
    def is_checked(self):return not self.page.paused
    def get_attribute(self,name):
        if name=='aria-pressed':return 'false' if self.page.paused else 'true'
        return None


@pytest.mark.parametrize('new_request_while_paused,expected_status',[(False,'PASS'),(True,'FAIL')])
def test_refresh_parity_distinguishes_delayed_response_from_new_paused_request(
        new_request_while_paused,expected_status):
    from tools.mcel_task_manager_acceptance.hosted import _observe_refresh
    host='http://127.0.0.1:8765'
    clock={'new_request_while_paused':new_request_while_paused}
    legacy=_RefreshTestPage('legacy',host,clock)
    generated=_RefreshTestPage('generated',host,clock)
    clock['pages']=[legacy,generated]
    rows={'refresh':{'status':'UNVERIFIED'}}
    _observe_refresh(legacy,generated,host,rows)
    entry=rows['refresh']
    assert entry['status']==expected_status,entry
    assert entry['observations']['legacyManualRequests']==2
    assert entry['observations']['generatedManualRequests']==2
    assert entry['observations']['pausedCompletedResponses']['legacy']>=1
    assert entry['observations']['pausedRequests']['legacy']==int(new_request_while_paused)
    assert entry['observations']['pausedRequests']['generated']==0
    assert entry['observations']['resumedRequests']=={'legacy':1,'generated':1}
    assert entry['observations']['counting']=='correlated-http-request-initiation'


def test_parity_console_summary_accounts_for_blocked_workflows():
    from tools.mcel_task_manager_acceptance.__main__ import _parity_console_summary
    from pathlib import Path
    all_names=('overview-parameters','four-views','refresh','inspection','responsive',
               'scheduling','ai-brief','process-effects','failure-lifecycle','server-lifecycle')
    passed=['overview-parameters','four-views','inspection']
    blocked=[name for name in all_names if name not in passed]
    result={'status':'BLOCKED','passed':passed,'failed':[],
            'blocked':blocked,'unverified':[],
            'rows':{name:{'reason':name+' needs evidence'} for name in all_names},
            'reason':'Not yet certified'}
    summary=_parity_console_summary(result,Path('report.json'))
    assert summary['blocked']==blocked
    assert summary['blockedReasons']['refresh']=='refresh needs evidence'
    assert summary['blockedReasons']['responsive']=='responsive needs evidence'
    assert summary['unverified']==[]


def test_parity_console_refuses_workflow_suppression():
    from tools.mcel_task_manager_acceptance.__main__ import _parity_console_summary
    from pathlib import Path
    result={'status':'BLOCKED','passed':['inspection'],'failed':[],
            'blocked':[],'unverified':[],
            'rows':{'inspection':{'reason':''},'refresh':{'reason':'not tested'}}}
    with pytest.raises(ValueError, match='missing-workflow-accounting'):
        _parity_console_summary(result,Path('report.json'))


def test_refresh_missing_foreground_timer_is_blocked_not_passed():
    from tools.mcel_task_manager_acceptance.hosted import _observe_refresh
    host='http://127.0.0.1:8765'
    clock={'no_resume_for':['legacy']}
    legacy=_RefreshTestPage('legacy',host,clock)
    generated=_RefreshTestPage('generated',host,clock)
    clock['pages']=[legacy,generated]
    rows={'refresh':{'status':'UNVERIFIED'}}
    _observe_refresh(legacy,generated,host,rows)
    assert rows['refresh']['status']=='BLOCKED'
    assert rows['refresh']['observations']['resumedRequests']=={'legacy':0,'generated':1}
    assert rows['refresh']['observations']['foreground']['legacy']['visibility']=='visible'


def test_responsive_does_not_force_click_or_programmatically_activate_tabs():
    from tools.mcel_task_manager_acceptance import hosted
    import inspect
    source=inspect.getsource(hosted._observe_responsive)
    assert 'bring_to_front()' in source
    assert 'scroll_into_view_if_needed' in source
    assert '.click(timeout=6000)' in source
    assert 'force=True' not in source
    assert 'n.click()' not in source
    assert "'hitTarget'" in source


def test_inspection_pointer_click_diagnostic_does_not_hide_failure(tmp_path):
    from tools.mcel_task_manager_acceptance.hosted import _pointer_click_with_evidence
    class Page:
        def __init__(self): self.front=False
        def bring_to_front(self):self.front=True
        def evaluate(self,script): return 'visible'
        def screenshot(self,**kwargs): Path(kwargs['path']).write_bytes(b'png')
    class Locator:
        def __init__(self):self.calls=[]
        def scroll_into_view_if_needed(self,**kw):self.calls.append(('scroll',kw))
        def click(self,**kw):
            self.calls.append(('click',kw))
            raise TimeoutError('Element receives pointer-events intercepted by overlay')
        def evaluate(self,*args,**kwargs):
            return {'targetContainsHit':False,'hit':{'tag':'DIV','id':'overlay'}}
    page=Page();locator=Locator()
    evidence=_pointer_click_with_evidence(page,locator,label='select',output=tmp_path)
    assert page.front and evidence['target']['hit']['id']=='overlay'
    assert 'intercepted' in evidence['callLog']
    assert evidence['screenshot']=='pointer-select.png'
    assert not any('force' in kwargs for _,kwargs in locator.calls)
    assert [name for name,_ in locator.calls]==['scroll','click']


def test_inspection_pointer_click_success_requires_actual_click(tmp_path):
    from tools.mcel_task_manager_acceptance.hosted import _pointer_click_with_evidence
    class Page:
        def __init__(self):self.front=False
        def bring_to_front(self):self.front=True
    class Locator:
        def __init__(self): self.clicked=False
        def scroll_into_view_if_needed(self,**kwargs):pass
        def click(self,**kwargs):
            assert kwargs.get('force') is not True
            self.clicked=True
    page=Page();locator=Locator()
    assert _pointer_click_with_evidence(page,locator,label='select',output=tmp_path) is None
    assert page.front and locator.clicked


class _ResponsiveTestLocator:
    def __init__(self, page, selector):
        self.page, self.selector = page, selector
    def scroll_into_view_if_needed(self, **kwargs):
        pass
    def click(self, **kwargs):
        if (self.page.tag, self.page.width) in self.page.obstructed and 'all-processes' in self.selector:
            raise TimeoutError('pointer was intercepted by another element')
    def focus(self):
        pass
    def evaluate(self, expression):
        if 'getBoundingClientRect' in expression:
            return {'point':[100,70], 'element':'DIV', 'id':'overlay',
                    'targetContainsHit':False}
        return True
    def get_attribute(self, key, **kwargs):
        return 'true'
    def is_visible(self, **kwargs):
        return True
    def bounding_box(self, **kwargs):
        return {'x':20,'y':20,'width':120,'height':32}

class _ResponsiveTestPage:
    def __init__(self, tag, obstructed):
        self.tag, self.obstructed, self.width = tag, obstructed, 1100
        self.visited = []
    def bring_to_front(self):
        pass
    def set_viewport_size(self, viewport):
        self.width = viewport['width']
        self.visited.append(self.width)
    def locator(self, selector):
        return _ResponsiveTestLocator(self, selector)
    def evaluate(self, expression):
        return 'visible' if 'visibilityState' in expression else 0
    def screenshot(self, *, path, **kwargs):
        Path(path).write_bytes(b'fixture screenshot')


def test_responsive_legacy_obstruction_does_not_skip_generated_or_later_widths(tmp_path,monkeypatch):
    from tools.mcel_task_manager_acceptance import hosted
    monkeypatch.setattr(hosted,'WIDTHS',(320,390,560,1100,1272))
    obstructed={('legacy',320)}
    legacy=_ResponsiveTestPage('legacy',obstructed)
    generated=_ResponsiveTestPage('generated',obstructed)
    rows={'responsive':{'status':'UNVERIFIED'}}
    hosted._observe_responsive(legacy,generated,rows,tmp_path)
    entry=rows['responsive']
    assert entry['status']=='BLOCKED' and entry['reason'].startswith('Pointer navigation obstructed (legacy:320px)')
    obs=entry['observations']
    assert obs['obstructedWidths']=={'legacy':[320],'generated':[]}
    assert obs['completedWidths']=={'legacy':[390,560,1100,1272],
                                    'generated':[320,390,560,1100,1272]}
    assert legacy.visited==generated.visited==[320,390,560,1100,1272]
    assert obs['viewports'][0]['legacy']['hitTarget']['id']=='overlay'
    assert obs['viewports'][0]['generated']['selected'] is True
    assert (tmp_path/'legacy-320-blocked.png').exists()
    assert (tmp_path/'generated-320.png').exists()


def test_responsive_generated_obstruction_is_never_accepted_as_pass(tmp_path,monkeypatch):
    from tools.mcel_task_manager_acceptance import hosted
    monkeypatch.setattr(hosted,'WIDTHS',(320,390))
    obstructed={('generated',320)}
    legacy=_ResponsiveTestPage('legacy',obstructed)
    generated=_ResponsiveTestPage('generated',obstructed)
    rows={'responsive':{'status':'UNVERIFIED'}}
    hosted._observe_responsive(legacy,generated,rows,tmp_path)
    assert rows['responsive']['status']=='BLOCKED'
    assert rows['responsive']['observations']['obstructedWidths']=={'legacy':[], 'generated':[320]}
    assert legacy.visited==generated.visited==[320,390]


def test_responsive_both_interfaces_are_fully_measured_before_pass(tmp_path,monkeypatch):
    from tools.mcel_task_manager_acceptance import hosted
    monkeypatch.setattr(hosted,'WIDTHS',(320,390,560,1100,1272))
    legacy=_ResponsiveTestPage('legacy',set())
    generated=_ResponsiveTestPage('generated',set())
    rows={'responsive':{'status':'UNVERIFIED'}}
    hosted._observe_responsive(legacy,generated,rows,tmp_path)
    assert rows['responsive']['status']=='PASS'
    assert len(rows['responsive']['observations']['viewports'])==5
    assert len(rows['responsive']['evidence'])==10
