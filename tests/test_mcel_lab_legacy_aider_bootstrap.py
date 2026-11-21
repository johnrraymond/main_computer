"""Native Code Editor must not crash the older shared shell or MCEL Lab."""
from __future__ import annotations

from pathlib import Path
import shutil
import subprocess
import textwrap

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "main_computer/web/applications/scripts"


def test_legacy_code_editor_absent_does_not_abort_lab_application_boot():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node required for browser JavaScript bootstrap check")
    # The viewport intentionally substitutes an inert stub for the old Code
    # Editor markup. All incumbent Aider elements are therefore null.
    projection = (ROOT / "main_computer/viewport_pages.py").read_text(encoding="utf-8")
    assert 'if include_name == "applications/apps/code-editor.html":' in projection
    assert 'id="code-editor-app" style="display:none"' in projection
    scripts = [
        (SCRIPTS / "code-editor-aider-output.js").read_text(encoding="utf-8"),
        (SCRIPTS / "code-editor-aider-actions.js").read_text(encoding="utf-8"),
    ]
    harness = textwrap.dedent(r"""
        const assert = require('node:assert/strict');
        const vm = require('node:vm');
        const source = process.env.MCEL_LEGACY_AIDER_OUTPUT + '\n' + process.env.MCEL_LEGACY_AIDER_ACTIONS;
        const fields = ['aiderInstruction','aiderTimeoutSeconds','aiderPreview','aiderRun',
          'aiderArchiveList','aiderArchiveCurrent','aiderResetContext','aiderLoadArchive',
          'fileMapRefresh','fileMapApply','fileMapSearch'];
        const absent = {aiderContextState:{active:{},activities:[]},aiderActionInFlight:false,
          aiderActivityIsRunning:()=>false,loadFileMap:()=>{},syncAiderFilesFromMarked:()=>{}};
        for (const field of fields) absent[field]=null;
        vm.runInNewContext(source,absent,{timeout:1500});
        const bindings=[];
        const present={aiderContextState:{active:{},activities:[]},aiderActionInFlight:false,
          aiderActivityIsRunning:()=>false,loadFileMap:()=>{},syncAiderFilesFromMarked:()=>{}};
        for (const field of fields) present[field]={value:'',
          addEventListener:(name)=>bindings.push([field,name]),setAttribute:()=>{}};
        present.aiderTimeoutSeconds.value='600';
        vm.runInNewContext(source,present,{timeout:1500});
        assert.equal(present.aiderTimeoutSeconds.value,'600');
        assert(bindings.some(([id,name])=>id==='aiderInstruction'&&name==='input'));
        assert(bindings.some(([id,name])=>id==='aiderRun'&&name==='click'));
        assert(bindings.some(([id,name])=>id==='fileMapRefresh'&&name==='click'));
        console.log('MCEL_LAB_LEGACY_AIDER_BOOTSTRAP_PASS');
    """)
    import os
    env = {**os.environ, 'MCEL_LEGACY_AIDER_OUTPUT':scripts[0], 'MCEL_LEGACY_AIDER_ACTIONS':scripts[1]}
    # The runtime checks both states without a browser/network dependency.
    result = subprocess.run([node, '-e', harness], cwd=ROOT, env=env,
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert 'MCEL_LAB_LEGACY_AIDER_BOOTSTRAP_PASS' in result.stdout
