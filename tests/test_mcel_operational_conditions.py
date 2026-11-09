"""Additional generic compiler guards for backend-derived applicability.

This suite supplements, but does not modify, Operational Workflow Oracle V1.
"""
from pathlib import Path

import pytest

from main_computer.mcel_dsl_compiler import compile_dsl_application
from main_computer.mcel_knowledge.semantic_composition import synthesize_composition


FIXTURE = Path(__file__).resolve().parents[1] / 'mcel_oracles/operational-workflows/generic_application.js'


def test_generic_derived_condition_is_accepted_without_authored_layout():
    compiled = compile_dsl_application(FIXTURE)
    assert compiled.valid, compiled.diagnostics
    composition = synthesize_composition(compiled.normalized_ir, policy='mcel.semantic-structure.p3')
    assert composition['sourceIrHash'] == compiled.normalized_ir['fingerprints']['semantic']
    nodes = compiled.normalized_ir['surfaces'][0]['nodes']
    assert any(node.get('visibleWhen', {}).get('state', {}).get('ref') == 'state:phase'
               for node in nodes)


@pytest.mark.parametrize('invalid', [
    '{state:phase,path:"notAField",equals:"inactive"}',
    '{state:phase,equals:"invented"}',
    '{state:phase,equals:false}',
    '{state:phase,equals:"inactive",maxAgeSeconds:60}',
    '{state:phase,equals:"inactive",unknown:true}',
    '{any:[]}',
    '{all:[{state:phase,equals:"inactive"},{state:phase,path:"bad",equals:"active"}]}',
])
def test_malformed_derived_visibility_is_refused_at_compile_time(tmp_path, invalid):
    original = FIXTURE.read_text(encoding='utf-8')
    assert 'const inactiveWhen = {state:phase,equals:"inactive"};' in original
    source = tmp_path / 'invalid-visibility.js'
    source.write_text(original.replace(
        'const inactiveWhen = {state:phase,equals:"inactive"};',
        f'const inactiveWhen = {invalid};'), encoding='utf-8')
    compiled = compile_dsl_application(source)
    assert not compiled.valid, invalid
    assert any(str(d.get('code', '')).startswith('MCEL_DSL_') for d in compiled.diagnostics), compiled.diagnostics
