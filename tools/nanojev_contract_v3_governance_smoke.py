#!/usr/bin/env python3
"""NanoJev v3 governed milestone, dispute and nonresponse recovery smoke.

Versioned governance extension; existing v2/v3 files are frozen. Docker Forge
optional and *never simulated as real certification*. Does not call LLM, use
keys, RPC, or deploy contracts. Output/workspace must be fresh.

  python -m tools.nanojev_contract_v3_governance_smoke --output runtime/reports/v3-gov-0001.json
  python -m tools.nanojev_contract_v3_governance_smoke --forge --output runtime/reports/v3-gov-0002.json
"""
from __future__ import annotations
import argparse
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
import re
import shutil
import traceback
from typing import Callable
from tools import nanojev_contract_v3_governance_language as lang
from tools import nanojev_contract_v3_governance_verifier as graph
from tools import nanojev_contract_v3_governance_solidity as solidity
from tools import nanojev_contract_v3_governance_foundry as foundry
from tools.nanojev_contract_v3_language import V3Error

DIR=Path(__file__).resolve().parent
FIXTURE=DIR/'fixtures'/'nanojev_contract_v3_governed_escrow_v1.json'
IMAGE='ghcr.io/foundry-rs/foundry:latest'
KERNEL_SHA256={
 'nanojev_contract_v3_language.py':'a52922369eb85934bc55801fe17ae9f15fa998443983852e34f014f7b9549239',
 'nanojev_contract_v3_verifier.py':'01df7dafc4e20d6120eefc7ad672c2067e3af0d9e11e7beb508d2c95f139b167',
 'nanojev_contract_v3_reference.py':'c67fce8586a0dfbb408501e2b26b948e71e610fefd997a2f5ae30b5c4e1020b0',
 'nanojev_contract_v3_solidity.py':'664453aba7ec2938f9f66b5769b9d4a444490d1097511c71a108558a45feba52',
 'nanojev_contract_v3_foundry.py':'bbb4803f8b5c01ebcb062c6f7056c8b520e93855756a7e73e1237c6f248b1f02',
 'nanojev_contract_v3_smoke.py':'8095e15879665fe2f041bfb5652e9d9dfac62235c6fa66fcfc7d03bcfbe023e3'
}

def hash_data(data):
    return hashlib.sha256(data if isinstance(data,bytes) else data.encode('utf8')).hexdigest()

def clock():return datetime.now(timezone.utc).isoformat()

def execute(spec_path,output_path,*,forge=False,image=IMAGE,network='bridge',timeout=360,
            docker_command:Callable|None=None):
    spec_path=Path(spec_path);out=Path(output_path).resolve();workspace=out.with_name(out.stem+'.workspace')
    report={'schema':'nanojev.contract.v3.governance_smoke.v1','status':'BLOCKED_NOT_VERIFIED',
            'started_at':clock(),'mode':'NO_LLM_NO_KEYS_NO_RPC_NO_DEPLOYMENT',
            'spec_path':str(spec_path),'workspace':str(workspace),
            'forge':'NOT_REQUESTED' if not forge else 'NOT_RUN',
            'kernel_frozen':False,'stages':{},'blockers':[]}
    try:
        if out==spec_path.resolve() or out.exists() or workspace.exists():
            raise V3Error('preflight','Input/output conflict or existing workspace')
        if not spec_path.is_file():raise V3Error('preflight','Specification not found')
        if forge and (timeout<=0 or network not in ('none','bridge')):
            raise V3Error('preflight','Invalid Docker options')
        if forge and docker_command is None and not shutil.which('docker'):
            raise V3Error('preflight','Docker is not available; use IR-only mode')
        digests={k:hash_data((DIR/k).read_bytes()) for k in KERNEL_SHA256}
        if digests!=KERNEL_SHA256:
            raise V3Error('preflight','Frozen v3 accounting kernel digest mismatch')
        report['kernel_frozen']=True
        report['kernel_sha256']=digests
        spec=lang.validate(json.loads(spec_path.read_text(encoding='utf8')))
        report['spec_sha256']=hash_data(json.dumps(spec,sort_keys=True,separators=(',',':')))
        report['stages']['schema']='PASS_GOVERNED_V3_IR'
        audit=graph.verify(spec)
        report['graph_audit']=audit
        report['stages']['graph']=audit['status']
        if not audit['emission_authorized_by_graph_audit']:
            raise V3Error('graph','Governance IR refused: '+','.join(audit['blockers']))
        name='NanoJevGoverned01'
        code=solidity.source(spec,name)
        suite,n=foundry.tests(spec,name)
        actual=len(re.findall(r'\bfunction\s+test\w*\s*\(',suite))
        if n!=actual or n<25:
            raise V3Error('oracle','Generated Foundry suite is missing coverage')
        (workspace/'src').mkdir(parents=True)
        (workspace/'test').mkdir()
        (workspace/'foundry.toml').write_text(
            '[profile.default]\nsrc = "src"\ntest = "test"\nsolc = "0.8.24"\noptimizer = true\noptimizer_runs = 200\n',encoding='utf8')
        (workspace/'src'/f'{name}.sol').write_text(code,encoding='utf8')
        (workspace/'test'/f'{name}Generated.t.sol').write_text(suite,encoding='utf8')
        report['generated']={'class':name,'solidity_sha256':hash_data(code),
                             'foundry_test_count':n,'foundry_suite_sha256':hash_data(suite)}
        report['stages']['solidity_emit']='PASS_GOVERNED_V3_CODEGEN_GRAPH_GATED'
        report['stages']['foundry_tests_generated']=n
        if forge:
            from tools import nanojev_contract_solidity_smoke as base
            for task in ('build','test'):
                print('[V3-GOV] Docker Forge '+task,flush=True)
                args=base.docker_args(workspace,image,network,task)
                response=docker_command(args,timeout) if docker_command else base.run_command(args,timeout)
                report['stages']['forge_'+task]=response
                if not isinstance(response,dict) or type(response.get('exit_code')) is not int or response['exit_code']!=0:
                    raise V3Error('forge_'+task,'Forge '+task+' failed')
            if docker_command is None:
                output=report['stages']['forge_test'].get('stdout_tail','')
                matches=re.findall(r'Ran\s+\d+\s+test suites?[^\n]*:\s*(\d+)\s+tests?\s+passed,\s*(\d+)\s+failed,\s*(\d+)\s+skipped',output)
                if not matches or tuple(map(int,matches[-1]))!=(n,0,0):
                    raise V3Error('forge_test','Forge result/test-count mismatch')
                artifact=workspace/'out'/f'{name}.sol'/f'{name}.json'
                if not artifact.is_file():raise V3Error('artifacts','Missing compiled Solidity ABI/bytecode')
                obj=json.loads(artifact.read_text(encoding='utf8'))
                methods={a.get('name') for a in obj.get('abi',[]) if a.get('type')=='function'}
                required={'fund','withdrawTo','submit','approve','reject','grantExtension',
                          'fileDispute','nominateArbitrator','confirmArbitrator',
                          'awardWorker','awardPayer','resolveUnappointed','resolveArbitratorSilence',
                          'resolveReviewSilence','escrowConserved'}
                if not required<=methods or not obj.get('bytecode',{}).get('object'):
                    raise V3Error('artifacts','Governed escrow ABI/bytecode mismatch')
                report['generated']['artifact_sha256']=hash_data(artifact.read_bytes())
                report['forge']='PASS_LOCAL_DOCKER_FORGE'
                report['status']='PASS_FORGE_V3_GOVERNANCE_SMOKE_NOT_CERTIFIED'
            else:
                report['forge']='INJECTED_NOT_REAL_FORGE'
                report['status']='PASS_INJECTED_FORGE_NOT_CERTIFIED'
        else:
            report['status']='PASS_V3_GOVERNANCE_IR_AND_SOLIDITY_GENERATED_FORGE_NOT_RUN'
    except (V3Error,ValueError,OSError) as exc:
        report['blockers'].append({'stage':getattr(exc,'stage','preflight'),
                                   'reason':getattr(exc,'reason',str(exc))[:500]})
    except Exception as exc:
        report['blockers'].append({'stage':'internal','reason':str(exc)[:500]})
        report['traceback_tail']=traceback.format_exc()[-2200:]
    finally:
        report['finished_at']=clock()
        if not out.exists() and out!=spec_path.resolve():
            out.parent.mkdir(parents=True,exist_ok=True)
            out.write_text(json.dumps(report,indent=2,ensure_ascii=False)+'\n',encoding='utf8')
    return report


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--spec',type=Path,default=FIXTURE)
    parser.add_argument('--forge',action='store_true')
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--foundry-image',default=IMAGE)
    parser.add_argument('--network',choices=('none','bridge'),default='bridge')
    parser.add_argument('--forge-timeout-s',type=float,default=360)
    args=parser.parse_args(argv)
    result=execute(args.spec,args.output,forge=args.forge,image=args.foundry_image,
                   network=args.network,timeout=args.forge_timeout_s)
    print(json.dumps({'status':result['status'],'blockers':result['blockers'],
                      'graph':result['stages'].get('graph'),
                      'expected_forge_tests':result.get('generated',{}).get('foundry_test_count'),
                      'forge':result['forge'],'report':str(args.output)},separators=(',',':')))
    return 0 if result['status'].startswith('PASS_') else 1

if __name__=='__main__':raise SystemExit(main())
