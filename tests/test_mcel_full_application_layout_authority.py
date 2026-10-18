"""Patch 14: canonical application-wide structural authority, visibility and rollback.

Uses actual generated DSL/IR projections and real application host HTML/CSS.
No application CSS is edited and no synthetic layout contract replaces the DSL.
"""
from __future__ import annotations

from test_mcel_package_canonical_layout_mount import _browser, _page, async_mount, payload


def test_application_package_trees_declare_structural_workspaces():
    for app, expected in (("calculator", ".calculator-shell"),
                          ("code-editor", ".code-studio-shell")):
        result = payload(app)
        assert expected in result["sources"]["layout"]
        assert '"variants"' in result["sources"]["layout"]


def test_calculator_full_workspace_visible_graph_and_basic_mode_do_not_unhide_panels():
    for browser in _browser():
        page = _page(browser, "calculator")
        page.evaluate('''() => {
          const root=document.querySelector('#calculator-app');
          root.style.setProperty('display','grid','important');root.style.width='1140px';root.style.height='890px';
          root.querySelector('#calculator-graphing-panel').hidden=false;
          root.querySelector('#calculator-mathics-panel').hidden=false;
          root.querySelector('#calculator-chat-panel').hidden=false;
        }''')
        result=page.evaluate(async_mount,payload("calculator"))
        assert result["status"] == "pass",result
        graph=page.evaluate('''() => {
          const root=document.querySelector('#calculator-app');
          const tree=root.querySelector('.calculator-shell');
          const workspace=root.querySelector('.calculator-workspace');
          const grid=root.querySelector('.calculator-scientific-functions');
          const mount=McelHostBoundApplicationRuntime.getMount('calculator');
          return {authority:tree.dataset.mcelCanonicalLayoutAuthority,
            structure:workspace.style.getPropertyValue('grid-template-areas'),
            columns:getComputedStyle(grid).gridTemplateColumns.split(' ').length,
            broken:McelSelfDiagnosis._private.detectAtomicControlLabelSplits(tree,{appId:'calculator'}).length,
            independent:mount.layoutReport.initial.independent,
            recordIds:mount.layoutReport.initial.observed.records.map(item=>item.id)};
        }''')
        assert graph["authority"] == "mcel-layout-tree.v1",graph
        assert "graphing" in graph["structure"] and "workspace" in graph["recordIds"],graph
        assert graph["columns"] == 3 and graph["broken"] == 0,graph
        basic=page.evaluate('''() => {
          const root=document.querySelector('#calculator-app');
          root.querySelector('#calculator-graphing-panel').hidden=true;
          root.querySelector('#calculator-mathics-panel').hidden=true;
          const mount=McelHostBoundApplicationRuntime.getMount('calculator');
          const result=mount.refreshLayout();
          const workspace=root.querySelector('.calculator-workspace');
          return {status:mount.layoutStatus, value:result?.status,
            areas:workspace.style.getPropertyValue('grid-template-areas'),
            graphCss:getComputedStyle(root.querySelector('#calculator-graphing-panel')).display,
            graphHidden:root.querySelector('#calculator-graphing-panel').hidden,
            mathCss:getComputedStyle(root.querySelector('#calculator-mathics-panel')).display};
        }''')
        assert basic["status"] == "pass" and basic["value"] == "pass",basic
        assert "basic" in basic["areas"] and "graphing" not in basic["areas"],basic
        assert basic["graphHidden"] and basic["graphCss"] == "none" and basic["mathCss"] == "none",basic
        narrow=page.evaluate('''() => {
          const root=document.querySelector('#calculator-app');
          root.style.width='750px';
          root.querySelector('#calculator-graphing-panel').hidden=false;
          root.querySelector('#calculator-mathics-panel').hidden=false;
          const mount=McelHostBoundApplicationRuntime.getMount('calculator');
          const result=mount.refreshLayout();
          const grid=root.querySelector('.calculator-scientific-functions');
          const evaluation=result?.initial?.evaluation;
          const selected=evaluation?.selected;
          const gridIndex=selected?.evidence?.records?.findIndex(record => record.id==='function-buttons');
          const viable=(evaluation?.candidates || []).filter(candidate => candidate.accepted);
          // Independently re-rank the search results using the canonical
          // quality contract. Font metrics differ across Windows/Chromium
          // installations; three columns at 750px is not a universal invariant.
          const compare=(a,b)=>a.evidence.quality.height-b.evidence.quality.height ||
            a.evidence.quality.emptyCells-b.evidence.quality.emptyCells ||
            a.evidence.quality.gridRows-b.evidence.quality.gridRows ||
            JSON.stringify(a.choices).localeCompare(JSON.stringify(b.choices));
          viable.sort(compare);
          return {status:mount.layoutStatus,result:result?.status,
            columns:getComputedStyle(grid).gridTemplateColumns.split(' ').length,
            selectedColumns:gridIndex>=0 ? selected.choices[gridIndex].columns : null,
            selectedQuality:selected?.evidence?.quality,
            bestQuality:viable[0]?.evidence?.quality,
            candidateCount:viable.length,
            selectedFailures:selected?.evidence?.failures?.length,
            liveFailures:result?.initial?.observed?.failures?.length,
            independent:result?.initial?.independent,
            controls:grid.querySelectorAll('button').length,
            broken:McelSelfDiagnosis._private.detectAtomicControlLabelSplits(root.querySelector('.calculator-shell'),{appId:'calculator'}).length,
            workspace:root.querySelector('.calculator-workspace').style.getPropertyValue('grid-template-areas')};
        }''')
        assert narrow["status"] == "pass" and narrow["result"] == "pass",narrow
        assert "graphing" in narrow["workspace"],narrow
        assert 1 <= narrow["columns"] <= 3 and narrow["columns"] == narrow["selectedColumns"],narrow
        assert narrow["controls"] == 21 and narrow["broken"] == 0,narrow
        assert narrow["candidateCount"] > 0 and narrow["selectedFailures"] == 0,narrow
        assert narrow["liveFailures"] == 0 and narrow["independent"] == "pass",narrow
        assert narrow["selectedQuality"] == narrow["bestQuality"],narrow
        state=page.evaluate('''() => {
          const mount=McelHostBoundApplicationRuntime.getMount('calculator');
          const shell=mount.root.querySelector('.calculator-shell');
          mount.unmount();
          return {authority:shell.dataset.mcelCanonicalLayoutAuthority||null,
            areas:mount.root.querySelector('.calculator-workspace').style.getPropertyValue('grid-template-areas')};
        }''')
        assert state["authority"] is None and state["areas"] == "",state


def test_code_editor_complete_shell_and_dock_workspace_owned_and_restorable():
    for browser in _browser():
        page=_page(browser,"code-editor")
        page.evaluate('''() => {
          const root=document.querySelector('#code-editor-app');
          root.style.setProperty('display','block','important');
          root.style.width='1220px';root.style.height='800px';
          root.dataset.mcelLayoutLive='true';
          root.querySelector('.code-studio-activitybar').style.setProperty('display','grid','important');
        }''')
        result=page.evaluate(async_mount,payload("code-editor"))
        assert result["status"] == "pass",result
        state=page.evaluate('''() => {
          const root=document.querySelector('#code-editor-app');
          const shell=root.querySelector('.code-studio-shell');
          const body=root.querySelector('.code-studio-body');
          const mount=McelHostBoundApplicationRuntime.getMount('code-editor');
          const areas=body.style.getPropertyValue('grid-template-areas');
          const columns=getComputedStyle(body).gridTemplateColumns.split(' ').length;
          const layout=mount.layoutReport.initial;
          const visible=layout.observed.records.filter(r=>r.visible!==false).map(r=>r.id);
          return {status:mount.layoutStatus,authority:shell.dataset.mcelCanonicalLayoutAuthority,
            areas, columns,visible, independent:layout.independent,
            nodes:layout.observed.records.length, bodyTracks:body.style.getPropertyValue('grid-template-columns')};
        }''')
        assert state['status']=='pass' and state['authority']=='mcel-layout-tree.v1',state
        assert state['columns']==4 and 'rail sidebar editor inspector' in state['areas'],state
        assert 'editor' in state['visible'] and 'inspector' in state['visible'],state
        assert state['nodes'] >= 9,state
        restored=page.evaluate('''() => {
          const root=document.querySelector('#code-editor-app');
          const mount=McelHostBoundApplicationRuntime.getMount('code-editor');
          mount.unmount();
          return {bodyTracks:root.querySelector('.code-studio-body').style.getPropertyValue('grid-template-columns'),
            shellAuthority:root.querySelector('.code-studio-shell').dataset.mcelCanonicalLayoutAuthority||null};
        }''')
        assert restored=={'bodyTracks':'','shellAuthority':None},restored


def test_canonical_mode_reflow_is_automatic_on_hidden_panels():
    for browser in _browser():
        page=_page(browser,"calculator")
        page.evaluate('''() => {const root=document.querySelector('#calculator-app');
          root.style.display='grid';root.style.width='1160px';
          root.querySelector('#calculator-graphing-panel').hidden=false;
          root.querySelector('#calculator-mathics-panel').hidden=false;
          root.querySelector('#calculator-chat-panel').hidden=false;}''')
        assert page.evaluate(async_mount,payload("calculator"))["status"]=="pass"
        page.evaluate('''() => {
          const root=document.querySelector('#calculator-app');
          root.querySelector('#calculator-graphing-panel').hidden=true;
          root.querySelector('#calculator-mathics-panel').hidden=true;
        }''')
        page.wait_for_function('''() => {
          const root=document.querySelector('#calculator-app');
          return root.dataset.mcelHostBoundLayoutStatus==='pass' &&
            !root.querySelector('.calculator-workspace').style.getPropertyValue('grid-template-areas').includes('graphing');
        }''',timeout=20000)
        evidence=page.evaluate('''() => {
          const root=document.querySelector('#calculator-app');
          const graph=root.querySelector('#calculator-graphing-panel');
          return {display:getComputedStyle(graph).display, hidden:graph.hidden,
            authority:root.querySelector('.calculator-shell').dataset.mcelCanonicalLayoutAuthority};
        }''')
        assert evidence=={'display':'none','hidden':True,'authority':'mcel-layout-tree.v1'},evidence


def test_required_hidden_code_editor_region_defers_until_host_initializes():
    for browser in _browser():
        page=_page(browser,"code-editor")
        page.evaluate('''() => {
          const root=document.querySelector('#code-editor-app');
          root.style.setProperty('display','block','important');root.style.width='1200px';
          root.style.height='800px';root.dataset.mcelLayoutLive='true';
        }''')
        first=page.evaluate(async_mount,payload("code-editor"))
        assert first['status']=='deferred',first
        claim=page.evaluate('''() => {
          const r=document.querySelector('#code-editor-app');
          return {status:r.dataset.mcelHostBoundLayoutStatus,
            detail:r.dataset.mcelHostBoundLayoutDetail,
            rail:getComputedStyle(r.querySelector('.code-studio-activitybar')).display};
        }''')
        assert claim['status']=='deferred' and 'rail' in claim['detail'] and claim['rail']=='none',claim
        page.evaluate('''() => {
          const rail=document.querySelector('.code-studio-activitybar');
          rail.style.setProperty('display','grid','important');
        }''')
        page.wait_for_function("document.querySelector('#code-editor-app').dataset.mcelHostBoundLayoutStatus === 'pass'",timeout=20000)
        assert page.evaluate("document.querySelector('.code-studio-shell').dataset.mcelCanonicalLayoutAuthority")=='mcel-layout-tree.v1'


def test_malformed_structural_variants_rejected_by_compiler():
    from main_computer.mcel_application_ir import _executable_layout_tree_errors
    import copy
    tree={"schema":"mcel.layout-tree.v1","root":{"id":"workspace","selector":"#workspace",
      "role":"group","variants":[{"tracks":["minmax(0, 1fr)"],"areas":["pane"]}],
      "children":[{"id":"pane","selector":".pane","role":"region"}]}}
    assert not _executable_layout_tree_errors(tree)
    for field, bad in (("tracks",["url(javascript:evil)"]),
                       ("areas",["undeclared"]),
                       ("areas",["pane pane", "pane ."])):
        variant=copy.deepcopy(tree)
        variant['root']['variants'][0][field]=bad
        assert _executable_layout_tree_errors(variant),variant
