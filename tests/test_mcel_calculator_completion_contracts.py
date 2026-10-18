"""Executable acceptance of Calculator's DSL-governed runtime and layout contracts.

The browser, generic projection, and AI-loop acceptance selectors are bound
independently in mcel_acceptance_bindings.json. These checks cover semantic
completeness and MCEL Lab's executable classification rules.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from main_computer.mcel_dsl_compiler import compile_dsl_application

ROOT = Path(__file__).resolve().parents[1]


def _ir():
    compiled = compile_dsl_application(ROOT / 'mcel_apps/calculator/application.js', write_candidate=False)
    assert compiled.valid and compiled.normalized_ir, compiled.diagnostics
    return compiled.normalized_ir


def test_full_semantic_runtime_declares_all_governed_lanes_and_observable_state():
    ir = _ir()
    intents = {i['runtimeMethod']: i for i in ir['intents']}
    assert len(intents) == len(ir['intents']) == 11
    assert set(intents) == {
        'switchMode', 'enterToken', 'clearExpression', 'evaluateExpression',
        'askModelForExpression', 'askModelForGraphExpression', 'drawGraph',
        'resetGraph', 'askModelForMathicsExpression', 'evaluateMathics',
        'askResultQuestion',
    }
    for name in ('switchMode', 'enterToken', 'clearExpression', 'evaluateExpression', 'drawGraph', 'resetGraph'):
        assert not intents[name].get('effectRefs') and not intents[name].get('writes'), name
    for name in ('askModelForExpression', 'askModelForGraphExpression',
                 'askModelForMathicsExpression', 'evaluateMathics', 'askResultQuestion'):
        assert intents[name].get('effectRefs') and intents[name]['risk'] == 'external-read' and not intents[name].get('writes'), name
    states = {item['id'] for item in ir['states']}
    for suffix in ('mode', 'arithmetic-expression', 'arithmetic-result', 'graph-expression',
                   'graph-range', 'mathics-result', 'result-context'):
        assert any(str(state).endswith(suffix) for state in states), (suffix, states)
    assert len(ir['surfaces']) == len(ir['layouts']) == 1


def test_layout_semantics_cover_lab_surface_and_explicit_controls():
    ir = _ir()
    surface = ir['surfaces'][0]['semanticSurface']
    grammar = ir['layouts'][0]['layoutGrammar']
    regions = {region['id']: region for region in surface['regions']}
    assert {'root', 'shell', 'mode-switch', 'workspace', 'arithmetic', 'graphing',
            'mathics', 'result-qa', 'chat'} <= set(regions)
    assert regions['workspace']['primary'] is True
    assert regions['arithmetic']['focusModes'] == ['basic']
    assert regions['graphing']['focusModes'] == ['graphing']
    assert {item['id'] for item in grammar['regions']} >= set(regions) - {'root'}
    controls = {control['intent'] for control in surface['controls']}
    assert controls == {intent['runtimeMethod'] for intent in ir['intents']}


@pytest.mark.skipif(shutil.which('node') is None, reason='Node required for MCEL Lab classification')
def test_mcel_lab_calculator_classifies_keypad_canvas_and_helper_boundaries(tmp_path):
    """Execute actual MCEL Lab rules; do not use text-presence as classification proof."""
    probe = tmp_path / 'lab-calculator.cjs'
    probe.write_text(r'''
const vm=require('node:vm'), fs=require('node:fs'), assert=require('node:assert/strict');
const context={window:{}};
vm.runInNewContext(fs.readFileSync(process.argv[2], 'utf8'), context);
const pack=context.window.McelSupercutPacksCalculator.calculatorDomainPack;
const records=[
  {tag:'div', classes:['calculator-mode-switch'], controlCount:2, expected:'calculator.mode-toolbar'},
  {tag:'div', classes:['calculator-keypad'], controlCount:14, expected:'calculator.keypad'},
  {tag:'div', domId:'calculator-display', expected:'calculator.display'},
  {tag:'canvas', domId:'calculator-graph-canvas', expected:'calculator.graph-surface'},
  {tag:'div', domId:'calculator-mathics-panel', expected:'calculator.mathics-panel'},
  {tag:'div', domId:'calculator-qa-panel', expected:'calculator.result-qa-panel'},
  {tag:'div', domId:'calculator-chat-panel', expected:'calculator.chat-panel'},
  {tag:'button', domId:'calculator-ask-model', expected:'calculator.analysis.ask-model'},
  {tag:'button', domId:'calculator-mathics-evaluate', expected:'calculator.mathics.evaluate'},
  {tag:'button', classes:['calculator-key'], directText:'7', expected:'calculator.keypad.digit-entry', attrs:{'data-calc-key':'7'}},
];
const observed=[];
for (let i=0;i<records.length;i++) {
 const r=records[i];r.id=`r${i}`;r.index=i+1;
 r.element={getAttribute: x=>r.attrs?.[x] || '', closest:()=>({id:'calculator-app'})};
 const board={specimenId:'calculator', rootNode:{id:'calculator-app'}, components:[], actions:[], risks:[],
  addEvidence:()=>({id:'evidence'}), ensureComponent:(_, item)=>board.components.push(item),
  explain:()=>{}, addAction:(_,item)=>{board.actions.push(item);return item}};
 for(const rule of pack.rules) {
   if(rule.phase==='purpose-inference' || rule.id==='calculator.detect-local-actions') {
     if(rule.when(r,board)) rule.apply(r,board);
   }
 }
 assert.ok(board.components.some(c=>c.purpose===r.expected), JSON.stringify({id:r.id,expected:r.expected,components:board.components}));
 if(r.tag==='button') {
   const action=board.actions.find(a=>a.purpose===r.expected);
   assert.ok(action);
   assert.equal(action.proofPolicy,'inspect-only');
   assert.equal(action.risk,(r.domId==='calculator-ask-model'||r.domId==='calculator-mathics-evaluate')?'analysis':'safe');
 }
 observed.push(r.expected);
}
console.log(JSON.stringify({classified:observed}));
''', encoding='utf-8', newline='\n')
    js = ROOT / 'main_computer/web/applications/scripts/mcel-supercut-packs-calculator.js'
    result = subprocess.run(['node', str(probe), str(js)], cwd=ROOT, text=True,
                            capture_output=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert len(json.loads(result.stdout)['classified']) == 10
