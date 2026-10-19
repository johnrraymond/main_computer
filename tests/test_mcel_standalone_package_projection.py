"""Patch 17: real independent presentation artifacts, and deliberate behavior refusal."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from main_computer.mcel_standalone_package import (
    SCHEMA,
    StandaloneProjectionError,
    _check_node,
    build_standalone_package,
)
from tools.mcel_standalone_package_probe import _check_assets, build_report

REPO = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('app,expected_intents', [('calculator',11),('code-editor',8)])
def test_compiled_standalone_assets_have_no_host_document_or_css(tmp_path, app, expected_intents):
    manifest = build_standalone_package(REPO, app, tmp_path)
    directory = tmp_path / app
    assert manifest['schema'] == SCHEMA
    assert manifest['sourceHostAssetsIncluded'] is False
    assert manifest['fullSemanticRuntimeProven'] is False
    assert manifest['behavioralParityProven'] is False
    assert manifest['portableImplementations'] == {}
    assert len(manifest['declaredIntents']) == expected_intents
    assert manifest['missingPortableIntents'] == manifest['declaredIntents']
    assert _check_assets(directory, manifest) == []
    assert {p.relative_to(directory).as_posix() for p in directory.rglob('*') if p.is_file()} == {
        'index.html','presentation.json','standalone.js','standalone.css',
        'contracts/layout.js','contracts/surface.js','contracts/intents.js'
    }
    html = (directory/'index.html').read_text(encoding='utf-8')
    js = (directory/'standalone.js').read_text(encoding='utf-8')
    assert '/applications/apps/' not in html + js
    assert '/applications/styles/' not in html + js
    assert 'MainComputerCalculatorRuntime' not in html + js
    assert 'MainComputerCodeEditorRuntime' not in html + js
    assert 'mcel-host-bound-application-runtime.js' not in html + js
    assert 'mcel-application-package-host.js' not in html + js
    assert 'disabled = true' in js


def test_compiler_source_and_package_fingerprints_are_bound_to_generated_assets(tmp_path):
    manifest=build_standalone_package(REPO,'calculator',tmp_path)
    digest='sha256:'+hashlib.sha256((REPO/'mcel_apps/calculator/application.js').read_bytes()).hexdigest()
    assert manifest['source']['sourceSha256']==digest
    assert manifest['source']['packageFingerprint'].startswith('sha256:')
    assert manifest['source']['hostBoundProjectionFingerprint'].startswith('sha256:')
    assert manifest['source']['semanticFingerprint'].startswith('sha256:')
    # Tampering with a generated contract is not accepted as the certified output.
    contract=tmp_path/'calculator/contracts/layout.js'
    contract.write_bytes(contract.read_bytes()+b'\n// corruption\n')
    assert _check_assets(tmp_path/'calculator',manifest)==['generated-asset-integrity-failed:contracts/layout.js']


def test_unsupported_constructive_selectors_are_rejected():
    bad={'id':'root','selector':'.a > .b','role':'group','children':[]}
    with pytest.raises(StandaloneProjectionError,match='cannot construct selector'):
        _check_node(bad,set())
    duplicate={'id':'root','selector':'.root','children':[{'id':'root','selector':'.other','children':[]}]}
    with pytest.raises(StandaloneProjectionError,match='Duplicate layout node'):
        _check_node(duplicate,set())


def test_undeclared_grid_regions_are_rejected():
    bad={'id':'root','selector':'.root','children':[{'id':'one','selector':'.one','children':[]}],
         'variants':[{'tracks':['1fr'],'areas':['fictional']}]}
    with pytest.raises(StandaloneProjectionError,match='undeclared child'):
        _check_node(bad,set())


def test_generator_evidence_refuses_full_semantic_runtime_even_with_assets_present(tmp_path):
    report=build_report(REPO,['calculator','code-editor'],browser=False)
    assert report['schema']=='mcel.standalone-package-execution-report.v1'
    assert report['passed'] is False
    assert report['fullSemanticRuntimeProven'] is False
    assert report['structuralPassed'] is False  # no browser was run
    assert report['status']=='refused'
    assert len(report['applications'])==2
    assert sum(len(app['missingPortableIntents']) for app in report['applications'])==19
    assert report['browser']==[]

@pytest.mark.parametrize('app,width',[
    ('calculator',1140),('calculator',750),('code-editor',1220),('code-editor',1050)
])
def test_standalone_browser_module_wiring_without_network(tmp_path,app,width):
    """Node DOM smoke checks JS imports and refusal; not browser certification."""
    import shutil
    import subprocess
    if not shutil.which('node'):
        pytest.skip('Node is needed for this ECMAScript smoke')
    build_standalone_package(REPO,app,tmp_path)
    proc=subprocess.run(['node',str(REPO/'tests/mcel_standalone_node_smoke.cjs'),
                         str(tmp_path),app,str(width)],capture_output=True,text=True,timeout=20)
    assert proc.returncode==0,proc.stderr
    result=json.loads(proc.stdout)
    assert result['appId']==app
    assert result['ready'] is True
    assert result['fullSemanticRuntimeProven'] is False
    assert result['nodeCount']==(14 if app=='calculator' else 9)


def test_browser_module_refuses_forged_semantic_success(tmp_path):
    import shutil
    import subprocess
    if not shutil.which('node'):
        pytest.skip('Node is needed for this ECMAScript smoke')
    build_standalone_package(REPO,'code-editor',tmp_path)
    path=tmp_path/'code-editor/presentation.json'
    manifest=json.loads(path.read_text(encoding='utf-8'))
    manifest['fullSemanticRuntimeProven']=True
    path.write_text(json.dumps(manifest),encoding='utf-8')
    proc=subprocess.run(['node',str(REPO/'tests/mcel_standalone_node_smoke.cjs'),
                         str(tmp_path),'code-editor','1050'],capture_output=True,text=True,timeout=20)
    assert proc.returncode != 0
    assert 'invalid or overclaimed presentation manifest' in proc.stderr
