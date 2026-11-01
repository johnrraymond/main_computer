"""MCEL V1 Phase 1: paired HTML+DSL semantic compilation (not UI synthesis)."""
from __future__ import annotations

import copy
from pathlib import Path

from main_computer.mcel_application_ir import validate_application_ir
from main_computer.mcel_dsl_compiler import compile_dsl_application

ROOT = Path(__file__).resolve().parents[1]
PAIR = ROOT / 'tools' / 'fixtures' / 'mcel_html_pair'


def _compile(html: str, js: str, directory: Path):
    directory.mkdir(parents=True, exist_ok=True)
    (directory/'application.html').write_text(html, encoding='utf-8')
    (directory/'application.js').write_text(js, encoding='utf-8')
    return compile_dsl_application(directory/'application.js')


def _base():
    return (PAIR/'application.html').read_text(), (PAIR/'application.js').read_text()


def _code(report):
    return {str(item.get('code')) for item in report.diagnostics}


def test_paired_sources_round_trip_hierarchy_and_bindings():
    report=compile_dsl_application(PAIR/'application.js')
    assert report.valid,report.diagnostics
    ir=report.normalized_ir
    assert not ir['layouts']
    intent=ir['surfaces'][0]['interfaceIntent']
    assert intent['hierarchy']['rootId']=='interface-region:semantic-file-probe.application'
    regions={r['id'].split('.')[-1]:r for r in intent['regions']}
    assert [regions[n]['ordinal'] for n in ('locations','workspace','details','operation-status')]==[0,1,2,3]
    assert all(regions[n]['parentId']==intent['hierarchy']['rootId'] for n in ('locations','workspace','details','operation-status'))
    assert regions['workspace']['purpose']=='workspace'
    assert regions['workspace']['tasks']==['browse','inspect']
    assert regions['workspace']['content']=={'kind':'collection','ref':'state:entries'}
    assert regions['locations']['preferences']=={'side':'start'}
    assert regions['workspace']['members']==[{'ref':'surface-node:semantic-file-probe.entries'}]
    assert {e['kind'] for e in intent['relations']}=={'supports','contextual-to'}
    sources=ir['provenance']['frontend']['sourceFiles']
    assert {Path(s['path']).name for s in sources}=={'application.js','application.html'}
    assert len({s['sha256'] for s in sources})==2
    assert validate_application_ir(ir).valid


def test_pair_deterministic_and_html_semantic_changes_detected(tmp_path):
    html,js=_base()
    a=_compile(html,js,tmp_path/'a')
    b=_compile(html,js,tmp_path/'b')
    assert a.valid and b.valid
    assert a.semantic_fingerprint==b.semantic_fingerprint
    c=_compile(html.replace('data-mcel-prefer-side="start"','data-mcel-prefer-side="end"'),js,tmp_path/'c')
    assert c.valid and a.semantic_fingerprint!=c.semantic_fingerprint
    assert a.normalized_ir['intents']==c.normalized_ir['intents']
    assert a.normalized_ir['effects']==c.normalized_ir['effects']
    assert a.normalized_ir['states']==c.normalized_ir['states'] or [x['schema'] for x in a.normalized_ir['states']]==[x['schema'] for x in c.normalized_ir['states']]


def test_html_source_order_preserved_not_sorted(tmp_path):
    html,js=_base()
    first=html.index('  <nav ')
    middle=html.index('  <section ',first)
    end=html.index('  <aside ',middle)
    nav,section=html[first:middle],html[middle:end]
    modified=html[:first]+section+nav+html[end:]
    r=_compile(modified,js,tmp_path)
    assert r.valid,r.diagnostics
    intent=r.normalized_ir['surfaces'][0]['interfaceIntent']
    regions={x['id'].split('.')[-1]:x for x in intent['regions']}
    assert regions['workspace']['ordinal']==0 and regions['locations']['ordinal']==1


def test_authored_heading_content_preserved(tmp_path):
    html,js=_base()
    html=html.replace('  <nav ', '  <h1>Files and locations</h1>\n  <nav ',1)
    r=_compile(html,js,tmp_path)
    assert r.valid,r.diagnostics
    root=next(x for x in r.normalized_ir['surfaces'][0]['interfaceIntent']['regions'] if x['parentId'] is None)
    assert root['staticContent'][0]['tag']=='h1'
    assert root['staticContent'][0]['text']=='Files and locations'
    nav=next(x for x in r.normalized_ir['surfaces'][0]['interfaceIntent']['regions'] if x['nativeTag']=='nav')
    assert nav['ordinal']==1


def test_html_validation_errors_fail_closed(tmp_path):
    html,js=_base()
    cases=[
        ('duplicate id',html.replace('data-mcel-id="details"','data-mcel-id="locations"'), 'MCEL_HTML_REGION_ID_INVALID'),
        ('unsupported attribute',html.replace('<main ', '<main class="grid" '), 'MCEL_HTML_ATTRIBUTE_UNSUPPORTED'),
        ('unknown mcel hint',html.replace('<main ','<main data-mcel-blindspot="x" '), 'MCEL_HTML_ATTRIBUTE_UNSUPPORTED'),
        ('inline visual CSS',html.replace('<main ','<main style="display:grid" '), 'MCEL_HTML_ATTRIBUTE_UNSUPPORTED'),
        ('script',html.replace('</main>','<script>alert(1)</script></main>'), 'MCEL_HTML_TAG_UNSUPPORTED'),
        ('native conflict',html.replace('data-mcel-purpose="navigation"','data-mcel-purpose="results"',1), 'MCEL_HTML_NATIVE_PURPOSE_CONFLICT'),
        ('unknown task',html.replace('browse inspect','browse levitate'), 'MCEL_HTML_TASK_INVALID'),
        ('unknown content ref',html.replace('state:entries','state:imaginary'), 'MCEL_HTML_BINDING_INVALID'),
        ('kind mismatch',html.replace('data-mcel-content-kind="collection" data-mcel-content-ref="state:entries"','data-mcel-content-kind="scalar" data-mcel-content-ref="state:entries"'), 'MCEL_HTML_BINDING_INVALID'),
        ('bad nesting',html.replace('</nav>','</section>',1), 'MCEL_HTML_NESTING_INVALID'),
        ('unbound raw content',html.replace('  <nav ','  Surprising raw text\n  <nav ',1), 'MCEL_HTML_UNBOUND_TEXT'),
    ]
    for i,(name,changed,code) in enumerate(cases):
        report=_compile(changed,js,tmp_path/str(i))
        assert not report.valid,name
        assert code in _code(report),(name,report.diagnostics)


def test_js_html_cross_source_failures(tmp_path):
    html,js=_base()
    cases=[
        (js.replace('locations:[rootList]','wrong:[rootList]'),'MCEL_HTML_BINDING_INVALID'),
        (js.replace('workspace:[entryList]','workspace:[]'),'MCEL_DSL_UNBOUND_SURFACE_NODE'),
        (js.replace('details:[previewText]','details:[entryList,previewText]'),'MCEL_DSL_SURFACE_NODE_DUPLICATE'),
        (js.replace('"contextual-to","workspace"','"contextual-to","undeclared"'),'MCEL_HTML_BINDING_INVALID'),
    ]
    for i,(edited,code) in enumerate(cases):
        r=_compile(html,edited,tmp_path/str(i))
        assert not r.valid,(i,r.diagnostics)
        assert code in _code(r),(i,r.diagnostics)


def test_html_requires_explicit_bindings(tmp_path):
    html,js=_base()
    edited=js.replace('dsl.surface.bindHtml(', 'dsl.surface.define(')
    r=_compile(html,edited,tmp_path/'notbound')
    assert not r.valid
    # The JS DSL rejects its inappropriate surface shape before any HTML join.
    assert r.diagnostics
    # Conversely, an HTML binding cannot be compiled without its companion.
    (tmp_path/'orphan').mkdir()
    (tmp_path/'orphan'/'application.js').write_text(js)
    r=compile_dsl_application(tmp_path/'orphan'/'application.js')
    assert not r.valid and 'MCEL_HTML_COMPANION_REQUIRED' in _code(r)


def test_canonical_ir_revalidates_hierarchy_and_cross_references():
    compiled=compile_dsl_application(PAIR/'application.js')
    assert compiled.valid
    ir=compiled.normalized_ir
    changes=[
        lambda i: i['surfaces'][0]['interfaceIntent']['regions'][1].__setitem__('parentId', 'interface-region:missing'),
        lambda i: i['surfaces'][0]['interfaceIntent']['regions'][1].__setitem__('ordinal', 3),
        lambda i: i['surfaces'][0]['interfaceIntent']['regions'][1]['content'].__setitem__('ref', 'state:fake'),
        lambda i: i['surfaces'][0]['interfaceIntent']['regions'][1].__setitem__('purpose','flying'),
        lambda i: i['surfaces'][0]['interfaceIntent']['regions'][1].__setitem__('members', []),
    ]
    for change in changes:
        tampered=copy.deepcopy(ir)
        change(tampered)
        result=validate_application_ir(tampered)
        assert not result.valid
        assert any('MCEL_IR_INTERFACE_INTENT_INVALID'==d.code for d in result.diagnostics),result.diagnostics


def test_existing_js_only_and_hostbound_keep_compiling():
    for path in (
        ROOT/'mcel_apps/calculator/application.js',
        ROOT/'mcel_apps/code-editor/application.js',
    ):
        result=compile_dsl_application(path)
        assert result.valid,(path,result.diagnostics)
    # 07B.1c reference probes may not be installed on earlier repositories.
    optional = ROOT/'tools/fixtures/mcel_reference_apps/file-manager/application.js'
    if optional.exists():
        result=compile_dsl_application(optional)
        assert result.valid,(optional,result.diagnostics)
