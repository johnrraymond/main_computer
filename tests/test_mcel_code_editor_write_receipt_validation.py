"""Reject transport-level success unless the effect receipt matches the write."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "main_computer/web/applications/scripts"


@pytest.mark.skipif(shutil.which("node") is None, reason="Node unavailable")
@pytest.mark.parametrize("mode,expected", [
    ("valid", True),
    ("missing-receipt", False),
    ("wrong-path", False),
    ("wrong-written-hash", False),
    ("wrong-before-hash", False),
])
def test_http_200_must_carry_authoritative_hash_matched_receipt(mode: str, expected: bool):
    script = '''
const crypto=require('node:crypto');
const caps=require(process.argv[1]);
const mode=process.argv[2];
const sha=t=>crypto.createHash('sha256').update(t).digest('hex');
const req={repo_dir:'.',project_root:'.',path:'src/proof.js',
  replacement_text:'after();\\n',expected_before_sha256:sha('before();\\n')};
const record={operation:'modify',path:'src/proof.js',
  before_sha256:req.expected_before_sha256,after_sha256:sha(req.replacement_text),
  written_sha256:sha(req.replacement_text)};
const response={ok:true,schema:'mcel-code-editor-file-save-v1',state:'applied',
  status:'pass',savedPath:'src/proof.js',handle:'b'.repeat(32),
  changedFiles:['src/proof.js'],receipt:{ok:true,rollback_required:false,
    transaction_id:'a'.repeat(24),files:[record]}};
if(mode==='missing-receipt')delete response.receipt;
if(mode==='wrong-path')record.path='different.js';
if(mode==='wrong-written-hash')record.written_sha256='0'.repeat(64);
if(mode==='wrong-before-hash')record.before_sha256='0'.repeat(64);
const fetcher=async()=>({ok:true,status:200,json:async()=>response});
caps.saveFile(req,{fetcher}).then(()=>console.log(JSON.stringify({ok:true})))
  .catch(error=>console.log(JSON.stringify({ok:false,message:error.message})));
'''
    result = subprocess.run([shutil.which("node"), "-e", script, str(SCRIPTS / "code-editor-capabilities.js"), mode],
                            capture_output=True, text=True, check=True, timeout=15)
    payload = json.loads(result.stdout)
    assert payload["ok"] is expected, payload
    if not expected:
        assert "hash-matched applied-write receipt" in payload["message"]
