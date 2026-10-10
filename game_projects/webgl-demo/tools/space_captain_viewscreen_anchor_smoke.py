#!/usr/bin/env python3
"""Test-only observer-anchor gate. Expected RED on legacy detached camera.

Uses production JS modules in Node for fast deterministic smoke probes. Optionally
runs the full authored scene under real Chromium/WebGL. No production state or
renderer source is patched, and no visual-only self-ship masking is accepted.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'web' / 'scripts'
HERE = Path(__file__).resolve().parent
SCRIPT_NAMES = {
    'phase3-projection': ('space-captain-multirate-contract.js', 'bridge-encounter-runtime.js', 'bridge-viewscreen-projection.js', 'bridge-viewscreen-encounter-runtime.js'),
    'phase4-presentation': ('space-captain-multirate-contract.js', 'bridge-encounter-runtime.js', 'bridge-viewscreen-projection.js', 'bridge-viewscreen-encounter-runtime.js', 'bridge-viewscreen-presentation.js'),
    'phase4-renderer': ('space-captain-multirate-contract.js', 'bridge-encounter-runtime.js', 'bridge-viewscreen-projection.js', 'bridge-viewscreen-encounter-runtime.js', 'bridge-viewscreen-presentation.js', 'bridge-viewscreen-renderer.js'),
    'phase4-non-encounter': ('space-captain-multirate-contract.js', 'bridge-viewscreen-projection.js', 'bridge-viewscreen-presentation.js', 'bridge-viewscreen-renderer.js'),
    'scene-physical-wiring': ('space-gravity-runtime.js', 'space-captain-multirate-contract.js', 'bridge-encounter-runtime.js'),
    'tracking-boarding-15m': ('space-captain-multirate-contract.js', 'bridge-encounter-runtime.js', 'bridge-viewscreen-projection.js'),
    'bridge-captain-deterministic-15m': ('space-captain-multirate-contract.js', 'bridge-encounter-runtime.js', 'bridge-captain-decision-policy.js'),
    'boarding-commitment': ('space-captain-multirate-contract.js', 'bridge-encounter-runtime.js', 'bridge-captain-decision-policy.js'),
}
PROBES = {
    'phase3-projection':'space_captain_phase3_viewscreen_projection_probe.js',
    'phase4-presentation':'space_captain_phase4_part1_viewscreen_presentation_probe.js',
    'phase4-renderer':'space_captain_phase4_part2_viewscreen_renderer_probe.js',
    'phase4-non-encounter':'space_captain_phase4_part3_non_encounter_modes_probe.js',
    'scene-physical-wiring':'space_captain_viewscreen_physical_wiring_probe.js',
    'tracking-boarding-15m':'space_captain_viewscreen_tracking_boarding_probe.js',
    'bridge-captain-deterministic-15m':'space_captain_bridge_entry_deterministic_probe.js',
    'boarding-commitment':'space_captain_boarding_commitment_probe.js',
}
# Baseline must fail for actual spatial reasons, not because Node, graphics, or
# the game failed to load. The old non-spatial checks are retained in each probe.
EXPECTED_RED = {
    'phase3-projection':'cameraAtRealShipPosition',
    'phase4-presentation':'playerShipIsNotExternalVisibleVessel',
    'phase4-renderer':'normalRendererDoesNotDrawOwnShip',
    'phase4-non-encounter':'astrometricObserverIsPhysicalMotherShip',
    'scene-physical-wiring':'sceneObserverReadsPhysicsBody',
    'tracking-boarding-15m':'unattendedBoardingRemainsInRange',
    'bridge-captain-deterministic-15m':'firstDecisionAtBridgeTimeZero',
    'boarding-commitment':'bothCaptainsInitiate',
    'phase5-real-chromium':'viewscreenCameraOriginMatchesPhysicalMother',
}
NODE_DRIVER = r'''
const fs=require('fs');
const vm=require('vm');
const files=JSON.parse(process.argv[1]);
try {
 for(const path of files.slice(0,-1)) vm.runInThisContext(fs.readFileSync(path,'utf8'),{filename:path});
 const result = new Function(fs.readFileSync(files[files.length-1],'utf8'))();
 process.stdout.write(JSON.stringify({type:'result',result}));
} catch(e) {
 process.stdout.write(JSON.stringify({type:'error',error:String(e?.stack||e)}));
 process.exitCode=2;
}
'''


def _run_js(name: str) -> dict:
    paths = [str(SCRIPT / path) for path in SCRIPT_NAMES[name]] + [str(HERE / PROBES[name])]
    job = subprocess.run(['node','-e',NODE_DRIVER,json.dumps(paths)],capture_output=True,text=True,timeout=90)
    try: packet = json.loads(job.stdout)
    except ValueError: return {'ok':False,'error':f'Node execution invalid JSON: stdout={job.stdout[-2000:]} stderr={job.stderr[-2000:]}'}
    if packet.get('type') != 'result': return {'ok':False,'error':packet.get('error','unknown Node execution error')}
    result = packet.get('result') or {}
    return {'ok':bool(result.get('ok')),'checks':result.get('checks',{}),
            'failedChecks':result.get('failedChecks',[]),'metrics':result.get('metrics',{}),
            'error':result.get('error')}


def _run_browser() -> dict:
    path = HERE / 'space_captain_phase5_part2_full_game_chromium_smoke.py'
    spec = importlib.util.spec_from_file_location('space_captain_phase5_anchor_browser_smoke', path)
    if not spec or not spec.loader: return {'ok':False,'error':'Could not load browser smoke'}
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    report = module.run(headed=os.environ.get('MC_SMOKE_HEADED') == '1',
                        output_dir=ROOT / 'builds' / 'viewscreen-anchor-smoke',screenshot_enabled=False,
                        chromium_executable=shutil.which('chromium') or shutil.which('chromium-browser'))
    return {'ok':report['ok'],'checks':report['checks'],'failedChecks':report['failedChecks'],
            'metrics':{'spatialSamples':report.get('metrics',{}).get('spatialSamples'),
                       'spatialSampleCount':report.get('metrics',{}).get('spatialSampleCount')},
            'errors':report.get('errors',[]), 'artifacts':report.get('artifacts')}


def main() -> int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--full-game',action='store_true',help='Include real authored Chromium / WebGL end-to-end probe')
    parser.add_argument('--expect-red',action='store_true',help='Baseline-only: success means the known bug is detected at every executed layer')
    parser.add_argument('--output',type=Path,help='Write full JSON diagnostic evidence')
    ns=parser.parse_args()
    runs={name:_run_js(name) for name in SCRIPT_NAMES}
    if ns.full_game: runs['phase5-real-chromium']=_run_browser()
    if ns.expect_red:
        accepted=all(not run['ok'] and not run.get('error') and not run.get('errors')
                     and run['checks'].get(EXPECTED_RED[name]) is False
                     and any(ok for ok in run['checks'].values())
                     for name,run in runs.items())
    else:
        accepted=all(run['ok'] for run in runs.values())
    report={'ok':accepted,'schema':'game.spaceCaptainViewscreenShipAnchorSmoke.v1',
            'mode':'expected-red-baseline' if ns.expect_red else 'required-green',
            'productionCodeModified':True,
            'layers':runs,
            'failedLayers':[name for name,run in runs.items() if not run['ok']]}
    encoded=json.dumps(report,indent=2,allow_nan=False)+'\n'
    if ns.output:
        ns.output.parent.mkdir(parents=True,exist_ok=True)
        ns.output.write_text(encoded,encoding='utf-8')
    print(encoded)
    return 0 if accepted else 1

if __name__=='__main__':
    raise SystemExit(main())
