"""MCEL read-only semantic-presentation bridge oracle (no app-specific color policy)."""
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "main_computer/web/applications/scripts"


NODE_ORACLE = r"""
'use strict';
const assert = require('node:assert/strict');
const bridge = require(process.argv[1]);
global.window = global;
require(process.argv[2]);
const ir = global.McelTaskManagerNotebookCandidate.ir;
const node = ir.surfaces[0].nodes.find(n => n.nodeKind === 'control');
const intent = ir.intents.find(i => i.id === node.intent?.ref);
assert(node && intent);
let mutations = 0;
function fakeElement(attrs, disabled=false) {
  return {
    tagName:'BUTTON', disabled, attrs,
    getAttribute(k) {return Object.hasOwn(this.attrs,k) ? this.attrs[k] : null;},
    hasAttribute(k) {return Object.hasOwn(this.attrs,k);},
    setAttribute() {mutations++;throw Error('DOM mutation');},
    classList: {add(){mutations++;throw Error('class mutation');}},
    style: {setProperty(){mutations++;throw Error('style mutation');}}
  };
}
function fakeRoot(items) {
  return {matches:()=>false,querySelectorAll:()=>items};
}
const root = fakeRoot([fakeElement({'data-mcel-node':node.id,'data-intent':intent.id},true)]);
const styleLaw={themes:['theme-machine','theme-civic'],normalizeTheme:x=>x};
const chromeLaw={chromes:['chrome-strict-hierarchy','chrome-journey'],normalizeChrome:x=>x};
const before=JSON.stringify(ir);
const report=bridge.observeCompatibility({ir,root,themeLaw:styleLaw,chromeLaw,theme:'theme-machine',chrome:'chrome-strict-hierarchy'});
assert.equal(report.schema,'mcel.semantic-presentation-observation.v1');
assert.equal(report.source.status,'canonical-ir');
assert.equal(report.coverage.observedElements,1);
assert.equal(report.coverage.exactlyBound,1);
assert.equal(report.coverage.ambiguous,0);
assert.equal(report.elements[0].runtimeDisabled,true);
assert(report.elements[0].bindings.some(x=>x.id===node.id));
assert(report.elements[0].bindings.some(x=>x.id===intent.id && x.risk===intent.risk && x.operationKind===intent.operationKind));
assert(report.elements[0].bindings.every(x=>x.actionMeaning===null));
assert.equal(report.presentation.theme.semanticMapping,'unresolved');
assert.equal(report.presentation.chrome.semanticMapping,'unresolved');
assert.equal(mutations,0);
assert.equal(JSON.stringify(ir),before);

// Theme/chrome selection may change only catalog reporting, never source observations.
const changed=bridge.observeCompatibility({ir,root,themeLaw:styleLaw,chromeLaw,theme:'theme-civic',chrome:'chrome-journey'});
assert.deepEqual(changed.elements,report.elements);
assert.deepEqual(changed.coverage,report.coverage);
assert.equal(mutations,0);

// A displayed label has no authority: neither 'Start' nor 'Delete' creates an action class.
const changedLabel=structuredClone(ir);
changedLabel.surfaces[0].nodes[0].label='Delete everything';
const labelReport=bridge.observeCompatibility({ir:changedLabel,root});
assert(labelReport.elements[0].bindings.every(x=>x.actionMeaning===null));

// DOM-only provenance cannot be upgraded to a semantic-IR mapping.
const withoutIR=bridge.observeCompatibility({root});
assert.equal(withoutIR.source.status,'missing-ir');
assert.equal(withoutIR.coverage.exactlyBound,0);
assert.equal(withoutIR.coverage.unbound,1);
assert(withoutIR.diagnostics.some(x=>x.includes('cannot establish operation meaning')));

// An unmounted IR reports an absence of runtime evidence, not a matched DOM.
const withoutDom=bridge.observeCompatibility({ir});
assert.equal(withoutDom.coverage.exactlyBound,0);
assert.equal(withoutDom.source.declaredRecords>0,true);
assert(withoutDom.diagnostics.some(x=>x.includes('No mounted DOM')));

// Duplicate source identities must not be counted as unique matches.
const dup=structuredClone(ir);
dup.surfaces[0].nodes.push({...dup.surfaces[0].nodes[0]});
const dupReport=bridge.observeCompatibility({ir:dup,root});
assert.equal(dupReport.coverage.ambiguous,1);
assert.equal(dupReport.coverage.exactlyBound,0);

// Two valid identifiers on the same element must refer to a coherent node->intent relationship.
const wrongIntent=ir.intents.find(item=>item.id!==intent.id);
assert(wrongIntent);
const crossed=bridge.observeCompatibility({ir,root:fakeRoot([
  fakeElement({'data-mcel-node':node.id,'data-intent':wrongIntent.id})
])});
assert.equal(crossed.coverage.ambiguous,1);
assert.equal(crossed.coverage.exactlyBound,0);

// Legacy data-mc attributes are observations, not modern semantic bindings.
const legacy=bridge.observeCompatibility({ir,root:fakeRoot([fakeElement({'data-mc-kind':'action'})])});
assert.equal(legacy.coverage.legacyStyleElements,1);
assert.equal(legacy.coverage.exactlyBound,0);

// Generality: the same adapter accepts the independent canonical surface-IR schema.
const surface={contractVersion:'mcel.semantic-surface-ir.v1',surface:{id:'surface:test'},graph:{nodes:[{id:'element:a',type:'control',source:'author',provenance:'test'}],regions:[],controls:[]}};
const surfaceReport=bridge.observeCompatibility({ir:surface,root:fakeRoot([fakeElement({'data-mcel-node-id':'element:a'})])});
assert.equal(surfaceReport.source.status,'canonical-surface-ir');
assert.equal(surfaceReport.coverage.exactlyBound,1);
assert.equal(mutations,0);
console.log('MCEL semantic presentation bridge oracle PASS');
"""


def test_semantic_presentation_bridge_oracle():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js not available")
    result = subprocess.run(
        [node, "-e", NODE_ORACLE,
         str(SCRIPTS / "mcel-semantic-presentation-bridge.js"),
         str(SCRIPTS / "mcel-task-manager-notebook-candidate-ir.js")],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout + "\n" + result.stderr
    assert "oracle PASS" in result.stdout


def test_lab_inspector_is_read_only_and_integrated_in_existing_workspace():
    html = (ROOT / "main_computer/web/applications/apps/mcel-lab.html").read_text(encoding="utf-8")
    app = (SCRIPTS / "mcel-lab.js").read_text(encoding="utf-8")
    host = (ROOT / "main_computer/web/applications.html").read_text(encoding="utf-8")
    assert html.count('id="mcel-semantic-compatibility"') == 1
    assert html.count('id="mcel-semantic-compatibility-source"') == 1
    assert html.index('id="mcel-semantic-compatibility"') < html.index('class="mcel-site-skeleton"')
    assert '@include applications/scripts/mcel-semantic-presentation-bridge.js' in host
    assert 'renderMcelSemanticCompatibility();' in app
    assert 'window.McelTaskManagerNotebookCandidate?.ir || null' in app
    # Compatibility observation does not run the mutating legacy laws.
    observer = (SCRIPTS / "mcel-semantic-presentation-bridge.js").read_text(encoding="utf-8")
    assert "applyRuntimeLaw(" not in observer
    assert "applyChromeHtml(" not in observer
    assert "setAttribute(" not in observer
    assert "style.setProperty(" not in observer
