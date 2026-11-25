from pathlib import Path
from main_computer.mcel_dsl_compiler import compile_dsl_application

def test_document_consumes_native_surface():
    report=compile_dsl_application(Path(__file__).resolve().parents[1]/'application.js')
    assert report.valid, report.diagnostics
    nodes=[n for s in report.normalized_ir['surfaces'] for n in s['nodes'] if n['nodeKind']=='document-surface']
    assert len(nodes)==1 and nodes[0]['elementId']=='element.authoring.document-surface'
    assert nodes[0]['toolbar'] is True
