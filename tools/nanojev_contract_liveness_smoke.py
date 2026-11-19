#!/usr/bin/env python3
"""Contract Language liveness & blind semantic-gold generalization diagnostic.

Requires successful live 105/105 composition report and 56/56 expansion report.
Gold is never placed in the LLM prompt. The bundled gold is assistant-authored,
not independently expert annotated; `--independent-cases` can provide an
external corpus, whose authorship is NOT automatically verified.

The liveness policy is conditional, not smart-contract safety certification:
for each reachable funded state/time, settlement must remain possible even if
any ONE role refuses every future call, assuming remaining actors act and
recipient transfers succeed. It does NOT prove adversarial scheduler fairness,
payout acceptance, economic fairness, real-time deadlines, or gas properties.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from tools import nanojev_contract_solidity_smoke as base
from tools import nanojev_contract_composition_smoke as composition
from tools import nanojev_contract_state_machine as machine
from tools import nanojev_contract_liveness as liveness

FIXTURE = Path(__file__).resolve().parent / 'fixtures' / 'nanojev_contract_liveness_challenges_v1.json'
FIXTURE_SHA256 = '4ad044d90195ad9715459860c1e743c73cb8ffc03e4fb8ac349c94e5ba6d0b66'


@dataclass(frozen=True)
class Options:
    output: Path
    composition_report: Path = Path('runtime/reports/nanojev-contract-composition-smoke-0001.json')
    expansion_report: Path = Path('runtime/reports/nanojev-contract-language-expansion-smoke-0002.json')
    llm_model: str = base.DEFAULT_MODEL
    ollama_url: str = base.DEFAULT_URL
    foundry_image: str = base.DEFAULT_IMAGE
    docker_network: str = 'bridge'
    llm_timeout: float = 240.0
    forge_timeout: float = 360.0
    compiler_only: bool = False
    dry_run: bool = False
    independent_cases: Path | None = None


def load_fixture() -> dict:
    wire = FIXTURE.read_bytes()
    if base.digest(wire) != FIXTURE_SHA256:
        raise base.SmokeFailure('preflight', 'Liveness diagnostic fixture SHA-256 changed')
    try:
        obj = json.loads(wire.decode('utf-8', 'strict'))
    except (UnicodeError, ValueError) as e:
        raise base.SmokeFailure('preflight', 'Invalid liveness fixture') from e
    if obj.get('schema') != 'nanojev.contract-liveness.challenges.v1':
        raise base.SmokeFailure('preflight', 'Unexpected liveness corpus schema')
    cases = obj.get('cases')
    if not isinstance(cases, list) or len(cases) != 7:
        raise base.SmokeFailure('preflight', 'Expected seven pinned liveness diagnostic cases')
    families = [x['family'] for x in cases]
    if (families.count('safe_positive') != 4 or families.count('unsafe_compilable') != 2
            or families.count('needs_clarification') != 1 or
            len({x['id'] for x in cases}) != 7):
        raise base.SmokeFailure('preflight', 'Unexpected liveness case distribution')
    return obj


def composition_baseline(path: Path) -> str:
    if not path.is_file():
        raise base.SmokeFailure('preflight', 'Missing 105/105 live composition baseline: ' + str(path))
    wire = path.read_bytes()
    try:
        obj = json.loads(wire.decode('utf-8','strict'))
    except (UnicodeError, ValueError) as e:
        raise base.SmokeFailure('preflight', 'Invalid composition baseline JSON') from e
    s = obj.get('stages', {})
    if (obj.get('status') != 'PASS_SMOKE_NOT_CERTIFIED'
            or obj.get('compiler_only') is not False
            or obj.get('expected_foundry_tests') != 105
            or obj.get('fixture_sha256') != composition.PIN
            or obj.get('blockers')
            or s.get('head') != 'PASS_LIVE'
            or s.get('semantic_oracle') != 'PASS_6_POSITIVE_4_REFUSAL'
            or s.get('forge_tests_passed') != 105
            or s.get('compiled_artifacts') != 'PASS'
            or any(not isinstance(s.get('forge_' + op), dict) or
                   s['forge_' + op].get('exit_code') != 0 for op in ('build','test'))):
        raise base.SmokeFailure('preflight','Require live 105/105 composition report, not mocked/partial result')
    found = {c.get('id'):c.get('result') for c in obj.get('cases',[]) if isinstance(c,dict)}
    required = {'reversible_split_base':'EXACT_GOLD_MATCH',
                'review_workflow_base':'EXACT_GOLD_MATCH'}
    if any(found.get(key) != val for key,val in required.items()):
        raise base.SmokeFailure('preflight', 'Composition semantic evidence missing')
    return base.digest(wire)


def load_independent(path: Path | None, reserved: set[str]) -> tuple[list[dict],str | None]:
    if path is None:
        return [], None
    try:
        wire = path.read_bytes()
    except OSError as e:
        raise base.SmokeFailure('preflight', 'Cannot read external gold corpus') from e
    if not 0 < len(wire) <= 256000:
        raise base.SmokeFailure('preflight', 'External gold size outside 1..256000 bytes')
    try:
        obj = json.loads(wire.decode('utf-8','strict'))
    except (ValueError, UnicodeError) as e:
        raise base.SmokeFailure('preflight','External gold requires valid UTF-8 JSON') from e
    if not isinstance(obj,dict) or set(obj) != {'schema','cases'} or obj['schema'] != 'nanojev.contract-liveness.external.v1':
        raise base.SmokeFailure('preflight','Unsupported external gold schema')
    items = obj['cases']
    if not isinstance(items,list) or not 2 <= len(items) <= 12:
        raise base.SmokeFailure('preflight','External corpus requires 2..12 cases')
    seen=set(reserved)
    families=[]
    for case in items:
        if not isinstance(case,dict) or set(case)-{'id','family','request','expected','reason'}:
            raise base.SmokeFailure('preflight','Unexpected external gold case keys')
        ident=case.get('id')
        if not isinstance(ident,str) or not re.fullmatch(r'[a-z][a-z0-9_]{0,31}',ident) or ident in seen:
            raise base.SmokeFailure('preflight','Invalid/duplicate external case ID')
        seen.add(ident)
        request=case.get('request')
        if not isinstance(request,str) or not 80<=len(request)<=8000:
            raise base.SmokeFailure('preflight','External request must be 80..8000 characters')
        kind=case.get('family')
        if kind in ('safe_positive','unsafe_compilable'):
            if 'expected' not in case or 'reason' in case:
                raise base.SmokeFailure('preflight','External representable case requires exact contract gold')
            spec=machine.validate(case['expected'])
            live=liveness.evaluate(spec)['status']=='PASS_CONDITIONAL'
            if live != (kind=='safe_positive'):
                raise base.SmokeFailure('preflight','External gold liveness label contradicts symbolic oracle')
        elif kind=='needs_clarification':
            if ('expected' in case or not isinstance(case.get('reason'),str) or
                    not 1<=len(case['reason'].strip())<=400):
                raise base.SmokeFailure('preflight','External clarification must provide a reason and no gold')
        else:
            raise base.SmokeFailure('preflight','Unknown external case family')
        families.append(kind)
    if 'safe_positive' not in families or not any(x != 'safe_positive' for x in families):
        raise base.SmokeFailure('preflight','External set needs safe case plus unsafe/ambiguous control')
    return items,base.digest(wire)


def prepare_cases() -> list[dict]:
    fixture=load_fixture()
    prior=composition.fixture()
    prior_by_id={c['id']:c for c in prior['cases']}
    cases=[]
    for c in fixture['cases']:
        item=dict(c)
        if item['family']=='unsafe_compilable':
            source=prior_by_id.get(item.pop('composition_case'))
            if not source or source['family']!='positive':
                raise base.SmokeFailure('preflight','Missing prior unsafe diagnostic case')
            item['request']=source['request']
            item['expected']=composition.gold(source,prior_by_id)
        if item['family'] in ('safe_positive','unsafe_compilable'):
            item['expected']=machine.validate(item['expected'])
            got=liveness.evaluate(item['expected'])['status']
            expect='PASS_CONDITIONAL' if item['family']=='safe_positive' else 'FAIL_TRAPPED_FUNDS_RISK'
            if got!=expect:
                raise base.SmokeFailure('preflight','Pinned liveness control label mismatch: '+item['id'])
        item['origin']='assistant_authored_diagnostic'
        cases.append(item)
    return cases


def validate_llm(case: dict, raw: str) -> dict | None:
    parsed=composition.parse(raw)
    if case['family']=='needs_clarification':
        if set(parsed)!={'status','questions'} or parsed.get('status')!='needs_clarification':
            raise base.SmokeFailure('semantic_gold','Unsafe acceptance of unresolved intent: '+case['id'])
        q=parsed['questions']
        if (not isinstance(q,list) or not 1<=len(q)<=12 or
                any(not isinstance(x,str) or not x.strip() or len(x)>400 for x in q)):
            raise base.SmokeFailure('semantic_gold','Malformed clarification: '+case['id'])
        if case['id']=='ambiguous_recovery':
            joined=' '.join(q).lower()
            groups=[('day','deadline','time','duration'),
                    ('caller','actor','who','authorization','trigger'),
                    ('refund','release','recipient','payment','payout')]
            if sum(any(t in joined for t in g) for g in groups)<2:
                raise base.SmokeFailure('semantic_gold','Clarification missed key underspecified terms')
        return None
    if set(parsed)!={'status','contract'} or parsed.get('status')!='contract':
        raise base.SmokeFailure('semantic_gold','Representable contract incorrectly refused: '+case['id'])
    actual=machine.validate(parsed['contract'])
    wanted=machine.validate(case['expected'])
    if actual!=wanted:
        changes={k:{'expected':wanted[k],'actual':actual[k]} for k in wanted if wanted[k]!=actual[k]}
        raise base.SmokeFailure('semantic_gold',case['id']+': wrong semantics: '+base.canonical(changes)[:1350])
    return actual


def run(options: Options, *, head: Callable[[str],str] | None = None,
        docker: Callable[[list[str],float],dict] | None = None,
        docker_available: bool | None = None) -> dict:
    output=options.output.resolve()
    workspace=output.with_name(output.stem+'.workspace')
    result={
        'schema':'nanojev.contract-liveness-smoke.v1',
        'status':'BLOCKED_NOT_VERIFIED','started_at':base.now(),
        'mode':'READ_ONLY_NO_KEYS_NO_DEPLOYMENT_NO_MODEL_MUTATION',
        'provenance':'bundled assistant-authored gold, separate from LLM prompt; not independent expert annotations',
        'assurance_limit':'Conditional one-unavailable-role ETH liveness only; not certification',
        'model':options.llm_model,'foundry_image':options.foundry_image,
        'compiler_only':options.compiler_only,
        'composition_baseline_path':str(options.composition_report),
        'composition_baseline_sha256':None,
        'expansion_baseline_sha256':None,
        'fixture_sha256':base.digest(FIXTURE.read_bytes()),
        'runner_sha256':base.digest(Path(__file__).read_bytes()),
        'external_corpus_sha256':None, 'external_cases':0,
        'externally_attested_gold':'NOT_PROVIDED_OR_VERIFIED',
        'workspace':str(workspace),
        'stages':{},'cases':[],'generated':{},'blockers':[]}
    try:
        cases=prepare_cases()
        extras,ext_sha=load_independent(options.independent_cases,{c['id'] for c in cases})
        result['external_corpus_sha256']=ext_sha
        result['external_cases']=len(extras)
        if extras:
            result['externally_attested_gold']='SUPPLIED_AUTHORSHIP_NOT_VERIFIABLE'
            cases += [{**c,'origin':'supplied_external_unverified_authorship'} for c in extras]
        result['expansion_baseline_sha256']=composition.baseline(options.expansion_report)
        result['composition_baseline_sha256']=composition_baseline(options.composition_report)
        if options.dry_run:
            result['status']='PLAN_ONLY_NOT_EXECUTED'
            result['stages']['preflight']='PLAN_ONLY'
            result['planned_cases']=[{'id':c['id'],'family':c['family']} for c in cases]
            return result
        if output.exists() or workspace.exists():
            raise base.SmokeFailure('preflight','Output report or workspace exists; choose a new filename')
        if options.llm_timeout<=0 or options.forge_timeout<=0:
            raise base.SmokeFailure('preflight','Timeouts must be positive')
        if docker_available is None:
            docker_available=shutil.which('docker') is not None
        if not docker_available:
            raise base.SmokeFailure('preflight','Docker CLI unavailable; cannot run actual Forge')
        base.local_url(options.ollama_url)
        result['stages']['preflight']='PASS_56_AND_105_LIVE_BASELINES_VERIFIED'
        (workspace/'src').mkdir(parents=True)
        (workspace/'test').mkdir()
        (workspace/'foundry.toml').write_text(
            '[profile.default]\nsrc="src"\ntest="test"\nout="out"\nlibs=[]\nsolc_version="0.8.24"\noptimizer=true\noptimizer_runs=200\n',encoding='utf-8')
        accepted={}
        for case in cases:
            item={'id':case['id'],'family':case['family'],'origin':case['origin'],
                  'request_sha256':base.digest(case['request']),
                  'gold_excluded_from_head':True}
            result['cases'].append(item)
            if options.compiler_only:
                inferred=(case.get('expected') if case['family']!='needs_clarification' else None)
                item['semantic_result']='LLM_BYPASSED'
            else:
                print('[LIVENESS] LLM '+case['id'],flush=True)
                raw=head(case['request']) if head else composition.live_head(
                    options.ollama_url,options.llm_model,case['request'],options.llm_timeout)
                item['llm_output_sha256']=base.digest(raw)
                item['llm_output']=raw[:4000]
                inferred=validate_llm(case,raw)
                item['semantic_result']='EXACT_GOLD_MATCH' if inferred is not None else 'CORRECT_REFUSAL'
            if inferred is None:
                item['policy_action']='REFUSE_AMBIGUITY'
                continue
            verdict=liveness.evaluate(inferred)
            item['liveness']=verdict
            item['contract_sha256']=base.digest(base.canonical(inferred))
            if case['family']=='unsafe_compilable':
                if verdict['status']!='FAIL_TRAPPED_FUNDS_RISK' or not verdict['single_role_traps']:
                    raise base.SmokeFailure('liveness_gate','Missed unsafe-but-compilable contract: '+case['id'])
                item['policy_action']='BLOCK_SOLIDITY_EMISSION_TRAPPED_FUNDS'
            else:
                if verdict['status']!='PASS_CONDITIONAL':
                    raise base.SmokeFailure('liveness_gate','Rejected safe gold / unsafe translated program: '+case['id'])
                accepted[case['id']]=inferred
                item['policy_action']='EMIT_CONDITIONAL_LIVENESS_ONLY'
        if len(accepted)!=4+sum(c['family']=='safe_positive' for c in extras):
            raise base.SmokeFailure('liveness_gate','Unexpected safe contract count')
        if accepted['safe_reversible_split'] != accepted['safe_reversible_paraphrase']:
            raise base.SmokeFailure('semantic_gold','Equivalent safe paraphrases gave different semantics')
        result['stages']['head']='BYPASSED_COMPILER_ONLY' if options.compiler_only else 'PASS_LIVE' if head is None else 'PASS_INJECTED'
        result['stages']['semantic_gold']=(
            'NOT_TESTED' if options.compiler_only else
            f'PASS_EXACT_GOLD_{6+sum(c["family"] != "needs_clarification" for c in extras)}_REPRESENTABLE_'
            f'{1+sum(c["family"] == "needs_clarification" for c in extras)}_CLARIFICATION')
        result['stages']['independent_semantic_correctness']=(
            'NOT_TESTED_COMPILER_ONLY' if options.compiler_only else
            'NOT_ESTABLISHED_ASSISTANT_AUTHORED_GOLD' if not extras else
            'EXTERNAL_GOLD_TESTED_AUTHORSHIP_UNATTESTED')
        result['stages']['liveness_gate']='PASS_UNSAFE_BLOCKED_SAFE_CONDITIONAL'
        result['diagnostic_counts']={
            'semantically_representable':sum(c['family']!='needs_clarification' for c in cases),
            'gold_safe_emitted':len(accepted),
            'unsafe_semantics_blocked':sum(x['policy_action']=='BLOCK_SOLIDITY_EMISSION_TRAPPED_FUNDS'
                                            for x in result['cases']),
            'unresolved_intent_refused':sum(x['policy_action']=='REFUSE_AMBIGUITY'
                                            for x in result['cases']),
            'single_actor_recovery_witnesses':sum(
                3*liveness.evaluate(spec)['checked_reachable_nodes'] for spec in accepted.values()),
            'independent_expert_gold_cases_verified':0,
        }
        for i,(ident,spec) in enumerate(accepted.items(),1):
            if i>99:
                raise base.SmokeFailure('solidity_emit','Too many contracts for safe naming convention')
            name=f'NanoJevComposition{i:02d}'
            solidity=machine.source(spec,name)
            # The generated tests are based on separately pinned GOLD, not LLM output.
            oracle=next(x['expected'] for x in cases if x['id']==ident)
            foundry, recovery_count=liveness.forge_recovery_tests(oracle,name)
            (workspace/'src'/f'{name}.sol').write_text(solidity,encoding='utf-8')
            (workspace/'test'/f'{name}.t.sol').write_text(foundry,encoding='utf-8')
            result['generated'][name]={
                'case':ident,'solidity_sha256':base.digest(solidity),
                'gold_derived_forge_sha256':base.digest(foundry),
                'recovery_witness_tests':recovery_count,
                'number_of_rules':len(spec['rules'])}
        count=sum(len(re.findall(r'\bfunction\s+test\w*\s*\(',p.read_text(encoding='utf-8')))
                  for p in (workspace/'test').glob('*.t.sol'))
        if count<100 or len(result['generated'])!=len(accepted):
            raise base.SmokeFailure('solidity_emit','Missing behavioral test coverage / contracts')
        result['stages']['solidity_emit']='PASS_CONDITIONAL_SAFE_ONLY'
        result['expected_foundry_tests']=count
        invoke=docker or base.run_command
        for op in ('build','test'):
            print('[LIVENESS] Docker Forge '+op,flush=True)
            proc=invoke(base.docker_args(workspace,options.foundry_image,options.docker_network,op),options.forge_timeout)
            result['stages']['forge_'+op]=proc
            if not isinstance(proc,dict) or type(proc.get('exit_code')) is not int:
                raise base.SmokeFailure('forge_'+op,'Malformed Forge execution result')
            if proc['exit_code']!=0:
                raise base.SmokeFailure('forge_'+op,'Forge '+op+' exited '+str(proc['exit_code']))
            if op=='test':
                text=proc.get('stdout_tail','')
                matches=re.findall(r'Ran\s+\d+\s+test suites?[^\n]*:\s*(\d+)\s+tests?\s+passed,\s*(\d+)\s+failed,\s*(\d+)\s+skipped',text)
                if not matches:
                    raise base.SmokeFailure('forge_test','No complete Forge final suite summary')
                passed,failed,skipped=map(int,matches[-1])
                if (passed,failed,skipped)!=(count,0,0):
                    raise base.SmokeFailure('forge_test',f'Expected {count}/0/0, got {passed}/{failed}/{skipped}')
                result['stages']['forge_tests_passed']=passed
        for name,entry in result['generated'].items():
            artifact_path=workspace/'out'/f'{name}.sol'/f'{name}.json'
            if docker is None and not artifact_path.is_file():
                raise base.SmokeFailure('compiled_artifacts','Missing compiled artifact: '+name)
            if artifact_path.is_file():
                artifact=json.loads(artifact_path.read_text(encoding='utf-8'))
                abi={x.get('name') for x in artifact.get('abi',[]) if x.get('type')=='function'}
                wanted={'fund','state','fundedAt'} | {r['name'] for r in accepted[entry['case']]['rules']}
                bytecode=artifact.get('bytecode',{}).get('object')
                if not isinstance(bytecode,str) or not bytecode or not wanted<=abi:
                    raise base.SmokeFailure('compiled_artifacts','Invalid ABI/bytecode for '+name)
                entry['artifact_sha256']=base.digest(artifact_path.read_bytes())
        result['stages']['compiled_artifacts']='PASS' if docker is None else 'INJECTED_NO_ARTIFACT_VERIFICATION'
        result['status']=('PASS_INJECTED_OFFLINE_TEST_ONLY' if head is not None or docker is not None else
                          'PASS_COMPILER_ONLY_NOT_LLM_GENERALIZATION' if options.compiler_only else
                          'PASS_SMOKE_NOT_CERTIFIED')
    except base.SmokeFailure as exc:
        result['blockers'].append({'stage':exc.stage,'reason':exc.reason})
    except Exception as exc:
        result['blockers'].append({'stage':'internal','reason':f'{type(exc).__name__}: {str(exc)[:350]}'})
        result['traceback_tail']=traceback.format_exc()[-2000:]
    finally:
        result['finished_at']=base.now()
        if not options.dry_run and not output.exists():
            output.parent.mkdir(parents=True,exist_ok=True)
            output.write_text(json.dumps(result,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
    return result


def main(argv=None):
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--output',type=Path,default=Path('runtime/reports/nanojev-contract-liveness-smoke-0001.json'))
    ap.add_argument('--composition-report',type=Path,default=Options.__dataclass_fields__['composition_report'].default)
    ap.add_argument('--expansion-report',type=Path,default=Options.__dataclass_fields__['expansion_report'].default)
    ap.add_argument('--llm-model',default=base.DEFAULT_MODEL)
    ap.add_argument('--ollama-url',default=base.DEFAULT_URL)
    ap.add_argument('--foundry-image',default=base.DEFAULT_IMAGE)
    ap.add_argument('--docker-network',choices=('bridge','none'),default='bridge')
    ap.add_argument('--llm-timeout-s',type=float,default=240.0)
    ap.add_argument('--forge-timeout-s',type=float,default=360.0)
    ap.add_argument('--compiler-only',action='store_true')
    ap.add_argument('--independent-cases',type=Path,default=None)
    ap.add_argument('--dry-run',action='store_true')
    a=ap.parse_args(argv)
    result=run(Options(output=a.output,composition_report=a.composition_report,
                       expansion_report=a.expansion_report,llm_model=a.llm_model,
                       ollama_url=a.ollama_url,foundry_image=a.foundry_image,
                       docker_network=a.docker_network,llm_timeout=a.llm_timeout_s,
                       forge_timeout=a.forge_timeout_s,compiler_only=a.compiler_only,
                       independent_cases=a.independent_cases,dry_run=a.dry_run))
    stages={k:(v.get('exit_code') if isinstance(v,dict) else v) for k,v in result['stages'].items()}
    print(base.canonical({'status':result['status'],'blockers':result['blockers'],
                          'stages':stages,'expected_foundry_tests':result.get('expected_foundry_tests'),
                          'report':str(a.output)}))
    return 0 if result['status'].startswith('PASS_') or result['status']=='PLAN_ONLY_NOT_EXECUTED' else 1


if __name__=='__main__':
    raise SystemExit(main())
