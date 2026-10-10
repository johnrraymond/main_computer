#!/usr/bin/env python3
"""Deterministic first encounter: pursuit -> timed deployment -> recall/abandon -> withdraw."""
import json
import subprocess
from pathlib import Path
root=Path(__file__).resolve().parents[1]
paths=[root/'web/scripts'/f for f in ('space-captain-multirate-contract.js','bridge-encounter-runtime.js','bridge-captain-decision-policy.js')]
probe=Path(__file__).with_name('space_captain_boarding_commitment_probe.js')
driver='''const fs=require('fs'),vm=require('vm');
const paths=JSON.parse(process.argv[1]);
for(const p of paths.slice(0,-1))vm.runInThisContext(fs.readFileSync(p,'utf8'),{filename:p});
const result=new Function(fs.readFileSync(paths.at(-1),'utf8'))();console.log(JSON.stringify(result));
process.exitCode=result.ok?0:1;
'''
run=subprocess.run(['node','-e',driver,json.dumps([str(p) for p in [*paths,probe]])],capture_output=True,text=True,timeout=35)
if run.stdout:print(run.stdout.strip())
if run.stderr:print(run.stderr.strip())
raise SystemExit(run.returncode)
