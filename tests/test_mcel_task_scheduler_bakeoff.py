"""Debug-only scheduler bake-off wiring and behavior (no service requests)."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "main_computer/web/applications/scripts"
HOST = ROOT / "main_computer/web/applications.html"
LAB = ROOT / "main_computer/web/applications/apps/mcel-lab.html"


def node_run(tmp_path: Path, script: str) -> dict:
    node = shutil.which("node")
    if not node:
        pytest.skip("Node unavailable")
    path = tmp_path / "mcel-task-scheduler-smoke.cjs"
    path.write_text(script, encoding="utf-8")
    proc = subprocess.run([node, str(path)], cwd=ROOT, capture_output=True,
                          text=True, timeout=15, check=True)
    return json.loads(proc.stdout)


def host_source() -> str:
    return (SCRIPTS / "mcel-diagnostic-plugins.js").read_text(encoding="utf-8")


def plugin_source() -> str:
    return (SCRIPTS / "mcel-task-scheduler-bakeoff-plugin.js").read_text(encoding="utf-8")


def bootstrap() -> str:
    # Real registry and plugin source; browser-like VM with no network or services.
    return """
const vm=require('node:vm');
const registry=JSON.parse(process.env.MCEL_TEST_REGISTRY);
const plugin=JSON.parse(process.env.MCEL_TEST_PLUGIN);
const output={textContent:'',dataset:{}};
const button={dataset:{},disabled:false,addEventListener(type,handler){this.click=handler}};
const controls={'mcel-task-bakeoff-report':output,'mcel-task-bakeoff-run':button};
const window={
  setTimeout,clearTimeout,setInterval,clearInterval,AbortController,performance,
  addEventListener(){},dispatchEvent(){},
  document:{readyState:'complete',getElementById:id=>controls[id]||null}
};
const globals={window,document:window.document,setTimeout,clearTimeout,setInterval,clearInterval,
  AbortController,performance,CustomEvent:class CustomEvent{constructor(type,data){this.type=type;this.detail=data.detail}}};
vm.runInNewContext(registry,globals,{filename:'mcel-diagnostic-plugins.js'});
vm.runInNewContext(plugin,globals,{filename:'mcel-task-scheduler-bakeoff-plugin.js'});
"""


def test_lab_wires_opt_in_debug_plugin_only():
    assert '<!-- @include applications/scripts/mcel-task-scheduler-bakeoff-plugin.js -->' in HOST.read_text(encoding="utf-8")
    html = LAB.read_text(encoding="utf-8")
    assert 'id="mcel-task-bakeoff-run"' in html
    assert 'id="mcel-task-bakeoff-report"' in html
    source = plugin_source()
    assert 'mcel-task-scheduler-bakeoff' in source
    assert "plugins.register" in source
    assert "plugins.run(ID)" in source
    assert "serviceRequests:0" in source
    assert "allowCdn:true" in source  # explicit click only
    assert "startPlugin(" not in source


def test_native_runs_offline_and_unavailable_libraries_cannot_win(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("MCEL_TEST_REGISTRY", json.dumps(host_source()))
    monkeypatch.setenv("MCEL_TEST_PLUGIN", json.dumps(plugin_source()))
    script = bootstrap() + """
(async()=>{
  const plugins=window.MCELDiagnosticPlugins;
  plugins.configure('mcel-task-scheduler-bakeoff',{
    allowCdn:false,intervalMs:35,workMs:75,durationMs:850});
  const response=await plugins.run('mcel-task-scheduler-bakeoff');
  const result=response.result;
  console.log(JSON.stringify({
    status:response.status,bakeoffStatus:result.status,winner:result.winner,
    actual:result.candidates.map(c=>({id:c.candidate,status:c.status,
      safe:c.safetyPassed,recovered:c.recoveredAfterFailure,maxConcurrent:c.maxConcurrent,
      lateWrites:c.lateWrites,failures:c.failures,successes:c.successes})),
    requests:result.serviceRequests,taskChanges:result.productionTasksChanged
  }));
})().catch(e=>{console.error(e);process.exitCode=1});
"""
    result = node_run(tmp_path, script)
    assert result['status'] == 'complete'
    assert result['bakeoffStatus'] == 'comparison-incomplete'
    assert result['winner'] is None
    assert result['requests'] == 0 and result['taskChanges'] is False
    native, rx, query = result['actual']
    assert native['id'] == 'native-timeout' and native['status'] == 'measured'
    assert native['safe'] is True and native['recovered'] is True
    assert native['failures'] == 1 and native['successes'] >= 3
    assert native['maxConcurrent'] == 1 and native['lateWrites'] == 0
    assert rx['status'] == 'unavailable' and query['status'] == 'unavailable'


def test_click_runs_one_debug_audit_not_a_service_action(tmp_path: Path, monkeypatch):
    monkeypatch.setenv('MCEL_TEST_REGISTRY', json.dumps(host_source()))
    monkeypatch.setenv('MCEL_TEST_PLUGIN', json.dumps(plugin_source()))
    # Configure availability through non-network globals, retaining the real
    # plugin binding and opt-in user gesture. Test does not claim library parity.
    script = bootstrap() + """
window.rxjs={};window.TanStackQueryCore={};
(async()=>{
  const before=output.textContent;
  await button.click();
  const report=JSON.parse(output.textContent.split('Full evidence JSON:\\n')[1]);
  console.log(JSON.stringify({before,enabled:button.disabled,operation:report.status,
    network:report.serviceRequests,
    candidates:report.candidates.map(c=>c.status)}));
})().catch(e=>{console.error(e);process.exitCode=1});
"""
    result = node_run(tmp_path, script)
    assert result['before'] == ''
    assert result['enabled'] is False
    assert result['operation'] == 'comparison-incomplete'
    assert result['network'] == 0
    assert result['candidates'] == ['measured','unavailable','unavailable']


def test_three_adapters_with_isolated_scheduler_contract_stubs(tmp_path: Path, monkeypatch):
    """Tests adapter dispatch/cleanup, not third-party implementation internals."""
    monkeypatch.setenv('MCEL_TEST_REGISTRY', json.dumps(host_source()))
    monkeypatch.setenv('MCEL_TEST_PLUGIN', json.dumps(plugin_source()))
    script = bootstrap() + r"""
class Observable {
  constructor(fn) {this.fn=fn}
  pipe(...ops) {return ops.reduce((o,op)=>op(o),this)}
  subscribe(obs={}) {return this.fn(obs)}
}
const rx = {};
rx.EMPTY=new Observable(obs=>{obs.complete?.();return {unsubscribe(){}}});
rx.timer=(begin,interval)=>new Observable(obs=>{
  let intervalId=null;
  const timeoutId=setTimeout(()=>{
    obs.next?.(0);intervalId=setInterval(()=>obs.next?.(0),interval);
  },begin);
  return {unsubscribe(){clearTimeout(timeoutId);clearInterval(intervalId)}};
});
rx.from=p=>new Observable(obs=>{
  let subscribed=true;
  Promise.resolve(p).then(value=>{if(!subscribed)return;
    obs.next?.(value);obs.complete?.();},error=>{if(subscribed)obs.error?.(error)});
  return {unsubscribe(){subscribed=false}};
});
rx.catchError=selector=>source=>new Observable(obs=>source.subscribe({
  next:value=>obs.next?.(value),
  complete:()=>obs.complete?.(),
  error:error=>selector(error).subscribe({next:value=>obs.next?.(value),
    complete:()=>obs.complete?.(),error:e=>obs.error?.(e)})
}));
rx.exhaustMap=project=>source=>new Observable(obs=>{
  let busy=false;
  const upstream=source.subscribe({next(value){
    if(busy)return;
    busy=true;
    project(value).subscribe({next:val=>obs.next?.(val),complete:()=>{busy=false},
      error:()=>{busy=false}});
  }});
  return {unsubscribe(){upstream.unsubscribe()}};
});
class QueryClient {
  cancelQueries(){return Promise.resolve()}
  clear(){}
}
class QueryObserver {
  constructor(client,options){this.options=options;this.alive=false;this.timer=null;this.abort=null;}
  subscribe(){
    this.alive=true;
    const step=async()=>{
      if(!this.alive)return;
      this.abort=new AbortController();
      try {await this.options.queryFn({signal:this.abort.signal})}catch(_){}
      if(this.alive)this.timer=setTimeout(step,this.options.refetchInterval);
    };
    this.timer=setTimeout(step,0);
    return ()=>{this.alive=false;clearTimeout(this.timer);this.abort?.abort()};
  }
  destroy(){this.alive=false;clearTimeout(this.timer);this.abort?.abort()}
}
window.rxjs=rx;
window.TanStackQueryCore={QueryClient,QueryObserver};
(async()=>{
  window.MCELDiagnosticPlugins.configure('mcel-task-scheduler-bakeoff',{
    allowCdn:false,intervalMs:35,workMs:75,durationMs:850});
  const result=(await window.MCELDiagnosticPlugins.run('mcel-task-scheduler-bakeoff')).result;
  console.log(JSON.stringify({status:result.status,
    requests:result.serviceRequests,changed:result.productionTasksChanged,
    candidates:result.candidates.map(x=>({id:x.candidate,status:x.status,
      safe:x.safetyPassed,recovered:x.recoveredAfterFailure,
      failures:x.failures,successes:x.successes,lateWrites:x.lateWrites}))}));
})().catch(e=>{console.error(e);process.exitCode=1});
"""
    result = node_run(tmp_path, script)
    assert result['status'] == 'comparison-complete'
    assert result['requests'] == 0 and result['changed'] is False
    assert len(result['candidates']) == 3
    for row in result['candidates']:
        assert row['status'] == 'measured', row
        assert row['safe'] is True, row
        assert row['recovered'] is True, row
        assert row['failures'] == 1, row
        assert row['successes'] >= 3, row
        assert row['lateWrites'] == 0, row
