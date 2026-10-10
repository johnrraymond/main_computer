#!/usr/bin/env python3
"""Inject test NanoJev choices; confirm real JS provider and boarding authority execute them."""
import json
import subprocess
from pathlib import Path
root=Path(__file__).resolve().parents[1]
paths=[root/'web/scripts'/n for n in ('space-captain-multirate-contract.js','bridge-encounter-runtime.js',
       'bridge-captain-decision-policy.js','bridge-captain-live-provider.js')]
probe=Path(__file__).with_name('space_captain_boarding_live_provider_probe.js')
code='''const fs=require('fs'),vm=require('vm');let all=JSON.parse(process.argv[1]);
for(const path of all.slice(0,-1))vm.runInThisContext(fs.readFileSync(path,'utf8'),{filename:path});
(async()=>{let output=await(new Function('return (async()=>{' + fs.readFileSync(all.at(-1),'utf8') + '})()'))();
console.log(JSON.stringify(output));process.exitCode=output.ok?0:1;})().catch(e=>{console.error(e.stack);process.exitCode=2;});'''
result=subprocess.run(['node','-e',code,json.dumps([str(p) for p in (*paths,probe)])],capture_output=True,text=True,timeout=40)
if result.stdout:print(result.stdout.strip())
if result.stderr:print(result.stderr.strip())
raise SystemExit(result.returncode)
