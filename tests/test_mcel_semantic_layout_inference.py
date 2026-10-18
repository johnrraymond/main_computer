"""Patch 10C: inferred workspace layouts are semantic, generic and observable."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

from main_computer.mcel_dsl_compiler import compile_dsl_application
from main_computer.mcel_application_package_browser_catalog import build_repository_browser_catalog_payload
from main_computer.mcel_calculator_host_bound_profile import build_calculator_browser_observation_profile
from main_computer.mcel_host_bound_browser_observation import (
    _browser_injected_projection, _host_bound_observation_html,
)

ROOT = Path(__file__).resolve().parents[1]
PLANNER = ROOT / "main_computer/web/applications/scripts/mcel-semantic-layout-inference.js"


def _contracts():
    compiled = compile_dsl_application(ROOT / "mcel_apps/calculator/application.js", write_candidate=False)
    assert compiled.valid and compiled.normalized_ir
    return (compiled.normalized_ir["surfaces"][0]["semanticSurface"],
            compiled.normalized_ir["layouts"][0]["layoutGrammar"])


def test_reusable_layout_derives_placement_from_semantics_not_app_id(tmp_path):
    semantic, grammar = _contracts()
    script = tmp_path / "proof.cjs"
    script.write_text("""
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const source = fs.readFileSync(process.argv[2], 'utf8');
const scope = {};
vm.runInNewContext(source, scope);
const api = scope.McelSemanticLayoutInference;
const input = JSON.parse(fs.readFileSync(process.argv[3], 'utf8'));
const visible = ['arithmetic', 'graphing', 'mathics', 'chat'];
function infer(mode, width, s=input.semantic) {
  return api.inferWorkspaceLayout({semanticSurface:s, layoutGrammar:input.grammar,
    mode, width, visibleRegions:visible});
}
const wide = infer('graphing', 1500);
const narrow = infer('graphing', 480);
assert.equal(wide.schema, 'mcel.inferred-workspace-layout.v1');
assert.ok(wide.rows[0].length > narrow.rows[0].length);
assert.equal(wide.rows[0][0], 'graphing');
assert.deepEqual(Array.from(narrow.order).sort(), Array.from(wide.order).sort());
assert.equal(narrow.rows[0][0], 'graphing');
assert.deepEqual(Array.from(infer('graphing', 1500).rows).map(r=>Array.from(r)),
  Array.from(wide.rows).map(r=>Array.from(r)));
const modified = JSON.parse(JSON.stringify(input.semantic));
modified.regions.find(r=>r.id==='graphing').focusModes = [];
modified.regions.find(r=>r.id==='arithmetic').focusModes = ['graphing'];
const rearranged = infer('graphing', 1500, modified);
assert.equal(rearranged.rows[0][0], 'arithmetic');
assert.notEqual(rearranged.gridTemplateAreas, wide.gridTemplateAreas);
// Reuse without Calculator identifiers: exactly the same algorithm.
const generic = JSON.parse(JSON.stringify(input.semantic));
const genericGrammar = JSON.parse(JSON.stringify(input.grammar));
for(const r of generic.regions) {r.id = 'x_' + r.id;}
for(const r of genericGrammar.regions) {
  r.id = 'x_' + r.id;
  if(Array.isArray(r.children)) r.children = r.children.map(id=>'x_'+id);
}
const other = api.inferWorkspaceLayout({semanticSurface:generic, layoutGrammar:genericGrammar,
  mode:'graphing', width:480, visibleRegions:visible.map(x=>'x_'+x)});
assert.equal(other.rows[0][0], 'x_graphing');
assert.equal(other.rows.length, narrow.rows.length);
assert.throws(()=> api.inferWorkspaceLayout({semanticSurface:generic,
  layoutGrammar:genericGrammar, mode:'graphing', width:0}), /width/);
console.log(JSON.stringify({wideRows:wide.rows.length,narrowRows:narrow.rows.length,
  semanticChange:rearranged.rows[0][0], genericRows:other.rows.length}));
""", encoding="utf-8", newline="\n")
    args = tmp_path / "contracts.json"
    args.write_text(json.dumps({"semantic": semantic, "grammar": grammar}), encoding="utf-8", newline="\n")
    result = subprocess.run(["node", str(script), str(PLANNER), str(args)], cwd=ROOT,
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert data["semanticChange"] == "arithmetic"
    assert data["narrowRows"] > data["wideRows"]


def test_live_calculator_mount_uses_canonical_workspace_authority():
    # The standalone semantic inference API remains available, but a host-bound
    # application with a canonical shell tree must not run two CSS authorities.
    from test_mcel_package_canonical_layout_mount import _browser, _page, payload, async_mount
    for browser in _browser():
        page = _page(browser, "calculator")
        page.evaluate("""() => {
          const root=document.querySelector('#calculator-app');
          root.style.display='grid';root.style.width='1100px';
          root.querySelector('#calculator-graphing-panel').hidden=false;
          root.querySelector('#calculator-mathics-panel').hidden=false;
          root.querySelector('#calculator-chat-panel').hidden=false;
        }""")
        result=page.evaluate(async_mount,payload("calculator"))
        assert result["status"] == "pass",result
        record=page.evaluate("""() => {
          const root=document.querySelector('#calculator-app');
          return {canonical:root.querySelector('.calculator-shell').dataset.mcelCanonicalLayoutAuthority,
            structure:root.querySelector('.calculator-workspace').style.getPropertyValue('grid-template-areas'),
            inferred:root.querySelector('.calculator-workspace').dataset.mcelLayoutAuthority || null};
        }""")
        assert record["canonical"] == "mcel-layout-tree.v1",record
        assert "graphing" in record["structure"],record
        assert record["inferred"] is None,record


def test_same_planner_infers_code_editor_workbench_without_application_rules(tmp_path):
    compiled = compile_dsl_application(ROOT / "mcel_apps/code-editor/application.js", write_candidate=False)
    assert compiled.valid and compiled.normalized_ir
    payload = {"surface": compiled.normalized_ir["surfaces"][0]["semanticSurface"],
               "grammar": compiled.normalized_ir["layouts"][0]["layoutGrammar"]}
    data = tmp_path / "code-editor-contract.json"
    data.write_text(json.dumps(payload), encoding="utf-8", newline="\n")
    script = tmp_path / "code-editor-proof.cjs"
    script.write_text("""
const fs=require('node:fs'); const vm=require('node:vm'); const assert=require('node:assert/strict');
const context={}; vm.runInNewContext(fs.readFileSync(process.argv[2], 'utf8'), context);
const {surface,grammar}=JSON.parse(fs.readFileSync(process.argv[3], 'utf8'));
const plan=context.McelSemanticLayoutInference.inferWorkspaceLayout({
  semanticSurface:surface, layoutGrammar:grammar, mode:'editing', width:1050,
  visibleRegions:['activitybar','explorer','editor-group','assistant']
});
assert.equal(plan.workspaceSelector,'.code-studio-body');
assert.equal(plan.order[0],'editor-group');
assert.equal(plan.order.length,4);
console.log(JSON.stringify({workspace:plan.workspaceSelector, order:plan.order}));
""", encoding="utf-8", newline="\n")
    result = subprocess.run(["node", str(script), str(PLANNER), str(data)],
                            cwd=ROOT, capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    proof = json.loads(result.stdout)
    assert proof["order"][0] == "editor-group"
