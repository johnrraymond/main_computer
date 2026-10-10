#!/usr/bin/env python3
"""Smoke the actual browser captain adapter and authority; does not claim a live model call.

Live NanoJev evaluation must be verified separately on a prepared workstation.
"""
import json
import subprocess
from pathlib import Path

root = Path(__file__).resolve().parents[1]
paths = [root / 'web/scripts' / name for name in (
    'space-captain-multirate-contract.js',
    'bridge-encounter-runtime.js',
    'bridge-captain-decision-policy.js',
    'bridge-captain-live-provider.js',
)]
probe = Path(__file__).with_suffix('.js')
code = """const fs=require('fs'),vm=require('vm');
for(const p of JSON.parse(process.argv[1])) vm.runInThisContext(fs.readFileSync(p,'utf8'),{filename:p});
(async()=>{const result=await(new Function('return (async()=>{' + fs.readFileSync(process.argv[2],'utf8') + '})()'))();
console.log(JSON.stringify(result));process.exitCode=result.ok?0:1;})().catch(e=>{console.error(e.stack);process.exitCode=2;});"""
result = subprocess.run(['node', '-e', code, json.dumps([str(p) for p in paths]), str(probe)],
                        capture_output=True, text=True, timeout=30)
print(result.stdout.strip() or result.stderr.strip())
raise SystemExit(result.returncode)
