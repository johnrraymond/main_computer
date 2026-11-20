#!/usr/bin/env python3
"""NanoJev v3 progressive escrow: IR graph -> Solidity -> optional Forge Docker.

No Ollama, secrets, RPC, deployment or production mutations. v2 is unchanged.
Runs with any bounded valid v3 JSON; an additional independent adversarial
Foundry suite is generated ONLY for the pinned 12-ETH regression fixture.

  python -m tools.nanojev_contract_v3_smoke --output runtime/reports/v3-0001.json
  python -m tools.nanojev_contract_v3_smoke --forge --output runtime/reports/v3-0002.json
  python -m tools.nanojev_contract_v3_smoke --spec my-v3.json --forge --output new.json
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import shutil
import traceback
from typing import Any, Callable

from tools import nanojev_contract_v3_language as lang
from tools import nanojev_contract_v3_verifier as graph
from tools import nanojev_contract_v3_solidity as solidity
from tools import nanojev_contract_v3_foundry as foundry

FIXTURE = Path(__file__).resolve().parent / 'fixtures' / 'nanojev_contract_v3_progressive_escrow_v1.json'
PINNED_TEST = FIXTURE.with_suffix('.t.sol')
IMAGE = 'ghcr.io/foundry-rs/foundry:latest'


def canon(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False)


def sha(value: str | bytes) -> str:
    return hashlib.sha256(value.encode('utf-8') if isinstance(value, str) else value).hexdigest()


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def execute(spec_path: Path, output_path: Path, *, forge: bool = False,
            image: str = IMAGE, network: str = 'bridge', timeout: float = 360.0,
            docker_command: Callable[[list[str], float], dict] | None = None) -> dict:
    """Run with injectable Docker only for controlled tests; injection cannot certify Forge."""
    from tools import nanojev_contract_solidity_smoke as base
    out = output_path.resolve()
    workspace = out.with_name(out.stem + '.workspace')
    result = {'schema': 'nanojev.contract.v3.progressive_smoke.v1',
              'status': 'BLOCKED_NOT_VERIFIED', 'started_at': now(),
              'mode': 'NO_LLM_NO_KEYS_NO_RPC_NO_DEPLOYMENT',
              'spec_path': str(spec_path), 'workspace': str(workspace),
              'forge': 'NOT_REQUESTED' if not forge else 'NOT_RUN',
              'v2_files_changed': False,
              'stages': {}, 'blockers': [], 'generated': {}}
    try:
        if not spec_path.is_file():
            raise lang.V3Error('preflight', 'Contract spec path not found')
        if out == spec_path.resolve() or out.exists() or workspace.exists():
            raise lang.V3Error('preflight', 'Input/output conflict, existing report or workspace')
        if forge and (timeout <= 0 or network not in ('bridge','none')):
            raise lang.V3Error('preflight', 'Invalid Forge timeout or network')
        if forge and docker_command is None and shutil.which('docker') is None:
            raise lang.V3Error('preflight', 'Docker command unavailable; rerun without --forge for IR-only')
        document = json.loads(spec_path.read_text(encoding='utf-8'))
        spec = lang.validate(document)
        result['spec_sha256'] = sha(canon(spec))
        result['stages']['schema'] = 'PASS_V3_VERSIONED_IR'
        audit = graph.verify(spec)
        result['graph_audit'] = audit
        result['stages']['graph'] = audit['status']
        if not audit['emission_authorized_by_graph_audit']:
            result['blockers'].append({'stage':'graph','reasons':audit['blockers']})
            raise lang.V3Error('graph', 'IR graph refused Solidity generation')
        name = 'NanoJevProgressive01'
        source = solidity.source(spec,name)
        suite, n = foundry.tests(spec,name)
        pinned = lang.validate(json.loads(FIXTURE.read_text(encoding='utf-8')))
        additional = spec == pinned
        static_suite = PINNED_TEST.read_text(encoding='utf-8') if additional else ''
        extra_n = len(re.findall(r'\bfunction\s+test\w*\s*\(',static_suite))
        if additional and extra_n < 15:
            raise lang.V3Error('oracle', 'Missing pinned adversarial Forge coverage')
        if n != len(re.findall(r'\bfunction\s+test\w*\s*\(',suite)):
            raise lang.V3Error('oracle', 'Generated Foundry test count mismatch')
        workspace.mkdir(parents=True)
        (workspace/'src').mkdir()
        (workspace/'test').mkdir()
        (workspace/'foundry.toml').write_text(
            '[profile.default]\nsrc = "src"\ntest = "test"\nsolc = "0.8.24"\noptimizer = true\noptimizer_runs = 200\n',encoding='utf-8')
        (workspace/'src'/f'{name}.sol').write_text(source,encoding='utf-8')
        (workspace/'test'/f'{name}Generated.t.sol').write_text(suite,encoding='utf-8')
        if additional:
            (workspace/'test'/f'{name}Adversarial.t.sol').write_text(static_suite,encoding='utf-8')
        result['generated'] = {'class':name,'solidity_sha256':sha(source),
                               'generic_forge_test_count':n,
                               'pinned_adversarial_forge_test_count':extra_n,
                               'expected_forge_tests':n+extra_n,
                               'pinned_fixture_tested':additional}
        result['stages']['solidity_emit'] = 'PASS_V3_PARTIAL_CREDIT_CODEGEN_IR_GATED'
        result['stages']['foundry_tests_generated'] = n+extra_n
        if forge:
            for operation in ('build','test'):
                print('[V3] Docker Forge '+operation,flush=True)
                args = base.docker_args(workspace,image,network,operation)
                proc = docker_command(args,timeout) if docker_command else base.run_command(args,timeout)
                result['stages']['forge_'+operation] = proc
                if not isinstance(proc,dict) or type(proc.get('exit_code')) is not int or proc['exit_code'] != 0:
                    raise lang.V3Error('forge_'+operation,'Forge '+operation+' did not finish successfully')
            if docker_command is None:
                output = result['stages']['forge_test'].get('stdout_tail','')
                counts = re.findall(r'Ran\s+\d+\s+test suites?[^\n]*:\s*(\d+)\s+tests?\s+passed,\s*(\d+)\s+failed,\s*(\d+)\s+skipped',output)
                if not counts or tuple(map(int,counts[-1])) != (n+extra_n,0,0):
                    raise lang.V3Error('forge_test','Forge test count mismatch')
                artifact = workspace/'out'/f'{name}.sol'/f'{name}.json'
                if not artifact.is_file():
                    raise lang.V3Error('artifacts','Generated bytecode ABI artifact missing')
                parsed = json.loads(artifact.read_text(encoding='utf-8'))
                methods = {x.get('name') for x in parsed.get('abi',[]) if x.get('type') == 'function'}
                needed = {'fund', 'withdraw', 'withdrawTo', 'escrowConserved',
                          'unallocated', 'totalPending','totalWithdrawn', 'pendingWithdrawals'}
                if not needed <= methods or not parsed.get('bytecode',{}).get('object'):
                    raise lang.V3Error('artifacts','v3 credit API or bytecode missing from artifact')
                result['generated']['compiled_artifact_sha256'] = sha(artifact.read_bytes())
                result['status'] = 'PASS_FORGE_V3_PROGRESSIVE_SMOKE_NOT_CERTIFIED'
                result['forge'] = 'PASS_LOCAL_DOCKER_FORGE'
            else:
                result['status'] = 'PASS_INJECTED_FORGE_NOT_CERTIFIED'
                result['forge'] = 'INJECTED_NOT_ACTUAL_FORGE'
        else:
            result['status'] = 'PASS_V3_IR_AND_SOLIDITY_GENERATED_FORGE_NOT_RUN'
            result['forge'] = 'NOT_REQUESTED'
    except (lang.V3Error, OSError, ValueError) as exc:
        if not result['blockers']:
            result['blockers'].append({'stage':getattr(exc,'stage','internal'),
                                       'reason':getattr(exc,'reason',str(exc))[:500]})
    except Exception as exc:
        result['blockers'].append({'stage':'internal','reason':type(exc).__name__+': '+str(exc)[:400]})
        result['traceback_tail'] = traceback.format_exc()[-2500:]
    finally:
        result['finished_at'] = now()
        if not out.exists() and out != spec_path.resolve():
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps(result,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--spec',type=Path,default=FIXTURE)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--forge',action='store_true',help='Run Docker Forge build and test')
    parser.add_argument('--foundry-image',default=IMAGE)
    parser.add_argument('--network',choices=('bridge','none'),default='bridge')
    parser.add_argument('--forge-timeout-s',type=float,default=360.0)
    opts = parser.parse_args(argv)
    report = execute(opts.spec,opts.output,forge=opts.forge,image=opts.foundry_image,
                     network=opts.network,timeout=opts.forge_timeout_s)
    print(json.dumps({'status':report['status'], 'blockers':report['blockers'],
                      'graph':report['stages'].get('graph'),
                      'expected_foundry_tests':report.get('generated',{}).get('expected_forge_tests'),
                      'forge':report['forge'], 'report':str(opts.output)},separators=(',',':')))
    return 0 if report['status'].startswith('PASS_') else 1


if __name__ == '__main__':
    raise SystemExit(main())
