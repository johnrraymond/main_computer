"""Independent MCEL public contract regressions for the two v5 defects.

Uses the actual shipped JavaScript in Node, without mutating apps or providers.
These tests insist on the promised behavior; they do not normalize bad inputs
or substitute a test-only validator for the public registry and resolver.
"""
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'main_computer/web/applications/scripts'


def run_real_apis(body: str):
    script = f'''const fs = require("fs"), vm = require("vm");
const sandbox = {{ console }};
sandbox.window = sandbox;
for (const name of ["mcel-element-registry.js", "mcel-elements-core.js", "mcel-toolkit-core.js"]) {{
  vm.runInNewContext(fs.readFileSync({json.dumps(str(SOURCE))} + "/" + name, "utf8"), sandbox, {{filename:name}});
}}
const registry = sandbox.McelElementRegistry, toolkit = sandbox.McelToolkitCore;
{body}
'''
    proc = subprocess.run(['node', '-e', script], capture_output=True, text=True, timeout=15)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def test_every_explicit_child_constraint_refuses_forbidden_and_keeps_valid():
    out = run_real_apis('''
const constrained = registry.all().filter(d => d.allowedChildren.length);
const results = constrained.map(def => {
  const legal = {id:"legal", elementId:def.allowedChildren[0]};
  const forbidden = registry.all().find(x => x.id !== def.id && !def.allowedChildren.includes(x.id));
  const before = registry.all().length;
  const valid = registry.serializeElement(def.id, {id:"parent", children:[legal]});
  let message = null;
  try { registry.serializeElement(def.id, {id:"bad", children:[legal, {id:"bad", elementId:forbidden.id}]}); }
  catch (error) { message = String(error.message); }
  const validAgain = registry.serializeElement(def.id, {id:"parent2", children:[legal]});
  return {id:def.id, legal:valid.children[0]?.elementId, retained:valid.children.length,
    error:message, forbidden:forbidden.id, unchanged:registry.all().length===before,
    validAgain:validAgain.children.length};
});
console.log(JSON.stringify(results));
''')
    assert len(out) >= 6
    assert all(row['legal'] and row['retained'] == 1 and row['validAgain'] == 1
               and row['unchanged'] for row in out)
    assert all(row['error'] and row['forbidden'] in row['error']
               and row['id'] in row['error'] for row in out)


def test_unknown_child_identity_is_refused_and_emitted_children_are_detached():
    out = run_real_apis('''
const child = {id:"safe", elementId:"element.core.action", metadata:{owned:"yes"}};
const parent = registry.serializeElement("element.core.toolbar", {id:"toolbar-1", children:[child]});
child.metadata.owned = "outside mutation";
parent.children[0].metadata.owned = "output mutation";
const missing = [{}, {id:"unnamed"}, "element.core.action"];
const errors = missing.map(c => {
  try { registry.serializeElement("element.core.toolbar", {id:"bad", children:[c]}); return false; }
  catch (error) { return /invalid child/i.test(String(error)); }
});
const free = registry.serializeElement("element.core.field", {id:"field-1", children:[{id:"legacy-child"}]});
const unknown = registry.serializeElement("element.not.registered", {id:"unknown"});
console.log(JSON.stringify({detached:parent.props.children[0].metadata.owned==="yes",
  inputUntouched:child.metadata.owned==="outside mutation",
  errors, legacyUnconstrained:free.children.length===1,
  unknownElement:unknown.elementId, unknownChildren:unknown.children.length}));
''')
    assert out['detached'] and out['inputUntouched']
    assert all(out['errors'])
    assert out['legacyUnconstrained']
    assert out['unknownElement'] == 'element.unknown'
    assert out['unknownChildren'] == 0


def test_resolver_declines_missing_requirements_and_explains_each_one():
    out = run_real_apis('''
const contract = {id:"acid", label:"Acid", requires:["c1","c2"],
  requiredPrimitives:["p1","p2"], mustReject:["forbidden"]};
const full = {id:"full", label:"Full", capabilities:["c1","c2"], primitiveIds:["p1","p2"]};
const rows = {
  full:toolkit.evaluateView(contract,full),
  missingCapability:toolkit.evaluateView(contract,{...full, capabilities:["c1"]}),
  missingPrimitive:toolkit.evaluateView(contract,{...full, primitiveIds:["p1"]}),
  missingBoth:toolkit.evaluateView(contract,{...full, capabilities:[], primitiveIds:[]}),
  forbidden:toolkit.evaluateView(contract,{...full,id:"forbidden"}),
  noneRequired:toolkit.evaluateView({id:"optional"}, {id:"empty",label:"Empty"})
};
console.log(JSON.stringify(rows));
''')
    assert out['full']['eligible'] is True
    assert out['noneRequired']['eligible'] is True
    for key in ('missingCapability', 'missingPrimitive', 'missingBoth', 'forbidden'):
        assert out[key]['eligible'] is False, (key, out[key])
        assert 'Rejected' in out[key]['reason']
    assert out['missingCapability']['missingCapabilities'] == ['c2']
    assert out['missingPrimitive']['missingPrimitives'] == ['p2']
    assert 'c2' in out['missingCapability']['reason']
    assert 'p2' in out['missingPrimitive']['reason']
    assert 'missing capabilities' in out['missingBoth']['reason']
    assert 'missing primitives' in out['missingBoth']['reason']
    assert 'explicitly rejected' in out['forbidden']['reason']


def test_existing_reconciled_recipes_remain_eligible_without_laundering_missing_primitives():
    out = run_real_apis('''
const supported = [
  ["fileBasket", "contract-treegrid"],
  ["workbenchSpecification", "workbench-composition-shell"],
  ["gitBackedDocumentHistory", "workbench-composition-shell"],
  ["documentEditorWorkbench", "document-authoring-workbench"]
];
const okay = supported.map(([key, expected]) => {
  const view=toolkit.resolveViews(toolkit.CONTRACT_PATTERNS[key])[0];
  const recipe=toolkit.getViewRecipe(view.id);
  return {contract:key,id:view.id,expected,eligible:view.eligible,
    missingPrimitives:view.missingPrimitives,
    realIds:recipe.primitiveIds.every(id => !!toolkit.getPrimitive(id))};
});
const incomplete=toolkit.resolveViews(toolkit.CONTRACT_PATTERNS.appAspectInspector)[0];
console.log(JSON.stringify({okay, incomplete}));
''')
    assert len(out['okay']) == 4
    for row in out['okay']:
        assert row['id'] == row['expected']
        assert row['eligible'] is True, row
        assert row['missingPrimitives'] == []
        assert row['realIds'] is True, row
    assert out['incomplete']['eligible'] is False
    assert out['incomplete']['missingPrimitives']
    assert 'missing primitives' in out['incomplete']['reason']
