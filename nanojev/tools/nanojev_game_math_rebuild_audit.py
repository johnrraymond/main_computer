"""Independent cold-rebuild determinism check from source-only Python definitions.

Run: python -m tools.nanojev_game_math_rebuild_audit --instances-per-game 4
For the complete size use --instances-per-game 16.
Does not touch an active training checkpoint, previous generated corpus or DB.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import tempfile

from . import nanojev_game_math_smoke as smoke


def filehash(path:Path)->str:
 return hashlib.sha256(path.read_bytes()).hexdigest()


def audit(instances:int=4, seed:int=20261016)->dict:
 if instances<4:raise ValueError('instances must be >=4')
 with tempfile.TemporaryDirectory(prefix='nanojev-full-rebuild-audit-') as temp:
  roots=[Path(temp)/'fresh_a',Path(temp)/'fresh_b']
  for root in roots:
   if root.exists():raise AssertionError('NOT_FRESH')
   smoke.generate(root,seed,instances)
   smoke._safe_replace(root) # Independently verifies generated hashes and layout
  a=json.loads((roots[0]/'manifest.json').read_text())
  b=json.loads((roots[1]/'manifest.json').read_text())
  if a!=b:raise AssertionError('NONDETERMINISTIC_MANIFEST')
  for file_name in smoke.FILES:
   if filehash(roots[0]/file_name)!=filehash(roots[1]/file_name):
    raise AssertionError('NONDETERMINISTIC_ARTIFACT '+file_name)
  if not {'nanojev_game_mutation_brain_v7.py', 'nanojev_deterministic_brain_v8.py'} <= set(a['source_sha256']):
   raise AssertionError('NATIVE_SOURCE_MISSING_FROM_PROVENANCE')
  if a['version']!=smoke.VERSION:raise AssertionError('VERSION_MISMATCH')
  return {'status':'PASS','proof_scope':'two_fresh_source_only_builds','instances_per_game':instances,
          'seed':seed,'artifacts_compared':len(smoke.FILES),
          'identical_manifest':True,'identical_bytes':True,
          'mutation_brain_source_provenance':True,'output_modified':False}


def main(argv=None):
 p=argparse.ArgumentParser(description=__doc__)
 p.add_argument('--instances-per-game',type=int,default=4)
 p.add_argument('--seed',type=int,default=20261016)
 args=p.parse_args(argv)
 try:
  print(json.dumps(audit(args.instances_per_game,args.seed),indent=2,sort_keys=True))
  return 0
 except Exception as exc:
  print(json.dumps({'status':'FAIL','error':str(exc),'type':type(exc).__name__},indent=2))
  return 1

if __name__=='__main__':raise SystemExit(main())
