#!/usr/bin/env python3
"""Generalization smoke: NL -> small compositional DSL -> Solidity -> Forge Docker.

Uses the prior 56/56 expansion report as an immutable prerequisite.
Independent challenge GOLD is hidden from the LLM, but challenges are
assistant-authored, not independently sourced or expert-certified.

Run:
 & $py -m tools.nanojev_contract_composition_smoke --llm-model gemma4:26b-a4b-it-q4_K_M --output runtime/reports/nanojev-contract-composition-smoke-0001.json

--compiler-only bypasses the LLM to exercise the generic lowerer + Forge.
It cannot claim natural-language generalization.
"""
from __future__ import annotations
import argparse
import copy
import json
import re
import shutil
import sys
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from urllib.request import Request, urlopen

from tools import nanojev_contract_solidity_smoke as base
from tools import nanojev_contract_state_machine as machine

FIXTURE = Path(__file__).resolve().parent/'fixtures'/'nanojev_contract_composition_challenges_v1.json'
PIN = 'c1922e6c0d332ab606fad51be1ae9e496485e0ec3dd2fe0741977be959fdc8f8'
PROMPT = '''Translate the user request to a TYPED finite-state contract language.
Reply with exactly ONE JSON object and no markdown or extra keys.
If information is missing, contradictory, uncertain or not representable,
reply {"status":"needs_clarification","questions":["specific question"]}.
Otherwise reply {"status":"contract","contract":{...}}.
Do NOT invent any authorizations, paths, conditions, payouts or deadlines.
The language supports only a single ETH deposit and manually called,
explicit, one-way, acyclic state transitions, not automatic transactions,
external oracles, recurring installments, counters, generic loops or off-chain
verification. A deadline does NOT execute anything on its own.

Required exact contract keys:
"language":"nanojev.contract.state_machine.v1",
"asset":"native_eth", "payer":"deployer",
"worker":"constructor_worker", "reviewer":"constructor_reviewer",
"amount_wei":<positive integer>, "deadline_seconds":<positive integer>,
"rules":[...]. The payer/deployer alone deposits the exact amount once and
funding enters "funded" state, starting the stated deadline. Worker and
reviewer are distinct payable constructor addresses, neither the payer.
Each rule must have exactly:
{"name":"verb", "actor":"payer|worker|reviewer", "from":"state_name",
 "to":"state_name", "when":"always|before_deadline|at_or_after_deadline",
 "payments":[{"to":"payer|worker|reviewer","bps":<integer>},...]}
Use explicit ACTION NAMES from the request (cancel, release, submit, approve,
refund), never invent new verbs or hidden transitions. States are lowercase
snake_case and action names are lowercase. The only initial postfunding state
is "funded". Guard "before_deadline" means strictly less than fundedAt +
deadline_seconds. "at_or_after_deadline" includes the boundary. "always"
means no time restriction. No-payment actions change state only. Payment
transitions distribute all money (exact 10000 bps), atomically, and terminate.
1 ETH = 1000000000000000000 wei, 1 day = 86400 seconds.
No implied decisions, automatic settlement, refund rights or secondary actions.
'''


@dataclass(frozen=True)
class Options:
    output: Path
    baseline_report: Path = Path('runtime/reports/nanojev-contract-language-expansion-smoke-0002.json')
    model: str = base.DEFAULT_MODEL
    ollama_url: str = base.DEFAULT_URL
    image: str = base.DEFAULT_IMAGE
    network: str = 'bridge'
    llm_timeout: float = 240.0
    forge_timeout: float = 360.0
    compiler_only: bool = False
    dry_run: bool = False
    independent_cases: Path | None = None


def fixture() -> dict:
    raw = FIXTURE.read_bytes()
    if base.digest(raw) != PIN:
        raise base.SmokeFailure('preflight', 'Pinned generalization corpus changed')
    obj = json.loads(raw.decode('utf-8', 'strict'))
    if obj.get('schema') != 'nanojev.contract-composition.challenges.v1' or len(obj['cases']) != 10:
        raise base.SmokeFailure('preflight', 'Unexpected composition corpus')
    ids = [c['id'] for c in obj['cases']]
    if len(set(ids)) != 10 or sum(c['family']=='positive' for c in obj['cases']) != 6:
        raise base.SmokeFailure('preflight', 'Unexpected case count')
    return obj



def load_independent(path: Path | None, reserved: set[str]) -> tuple[list[dict],str|None]:
    """Optional user-supplied gold; authorship/expertise not automatically verified."""
    if path is None:
        return [], None
    try:
        wire=path.read_bytes()
    except OSError as e:
        raise base.SmokeFailure('preflight','Independent cases file unreadable') from e
    if len(wire)>256000:
        raise base.SmokeFailure('preflight','External corpus too large')
    try:
        obj=json.loads(wire.decode('utf-8','strict'))
    except (ValueError,UnicodeError) as e:
        raise base.SmokeFailure('preflight','External cases must be UTF-8 JSON') from e
    if not isinstance(obj,dict) or obj.get('schema')!='nanojev.contract-composition.external.v1':
        raise base.SmokeFailure('preflight','Bad external corpus schema')
    cases=obj.get('cases')
    if not isinstance(cases,list) or not 2<=len(cases)<=12:
        raise base.SmokeFailure('preflight','External corpus requires 2..12 cases')
    seen=set(reserved)
    positive=negative=0
    for c in cases:
        if not isinstance(c,dict) or set(c)-{'id','family','request','expected','reason'}:
            raise base.SmokeFailure('preflight','Unknown external case fields')
        key=c.get('id')
        if not isinstance(key,str) or not re.fullmatch(r'[a-z][a-z0-9_]{0,30}',key) or key in seen:
            raise base.SmokeFailure('preflight','External case identifier invalid/duplicate')
        seen.add(key)
        request=c.get('request')
        if not isinstance(request,str) or not 100<=len(request)<=8000:
            raise base.SmokeFailure('preflight','External request must be 100..8000 chars')
        if c.get('family')=='positive':
            positive+=1
            if 'expected' not in c or 'reason' in c:
                raise base.SmokeFailure('preflight','External positive needs exact gold')
            machine.validate(c['expected'])
        elif c.get('family')=='needs_clarification':
            negative+=1
            if ('expected' in c or not isinstance(c.get('reason'),str) or
                    not c['reason'].strip() or len(c['reason'])>400):
                raise base.SmokeFailure('preflight','External negative needs a reason, no contract gold')
        else:
            raise base.SmokeFailure('preflight','Invalid external case family')
    if positive==0 or negative==0:
        raise base.SmokeFailure('preflight','External corpus requires positive and negative challenges')
    return cases,base.digest(wire)


def gold(case: dict, cases: dict[str,dict]) -> dict:
    if case['family'] != 'positive':
        raise base.SmokeFailure('preflight', 'Negative case has no contract gold')
    if 'expected' in case:
        result = copy.deepcopy(case['expected'])
    else:
        parent = cases.get(case.get('same_as', ''))
        if not parent or 'expected' not in parent:
            raise base.SmokeFailure('preflight', 'Bad gold parent reference')
        result = copy.deepcopy(parent['expected'])
        change = case.get('delta', {})
        if 'deadline_seconds' in change:
            result['deadline_seconds'] = change['deadline_seconds']
        if 'rule' in change:
            matching = [r for r in result['rules'] if r['name'] == change['rule']]
            if len(matching) != 1:
                raise base.SmokeFailure('preflight', 'No changed rule in gold')
            matching[0]['payments'] = change['payments']
    return machine.validate(result)


def parse(raw: str) -> dict:
    if len(raw) > 50000:
        raise base.SmokeFailure('llm', 'LLM output too long')
    def unique(pairs):
        out = {}
        for k,v in pairs:
            if k in out:
                raise ValueError('duplicate JSON key')
            out[k] = v
        return out
    try:
        obj = json.loads(raw, object_pairs_hook=unique)
    except ValueError as exc:
        raise base.SmokeFailure('llm', 'Invalid/duplicate-key JSON') from exc
    if not isinstance(obj, dict):
        raise base.SmokeFailure('llm', 'Response is not a JSON object')
    return obj


def accept(case: dict, response: dict, cases: dict[str,dict]) -> dict | None:
    if case['family']=='needs_clarification':
        if set(response) != {'status', 'questions'} or response['status'] != 'needs_clarification':
            raise base.SmokeFailure('semantic_oracle', 'Unsafe acceptance: '+case['id'])
        questions = response['questions']
        if (not isinstance(questions, list) or not 1 <= len(questions) <= 12 or
                any(not isinstance(q,str) or not q.strip() or len(q)>400 for q in questions)):
            raise base.SmokeFailure('semantic_oracle', 'Malformed clarification in '+case['id'])
        keywords = {
            'ambiguous_split_allocation': ('ratio', 'share', 'split', 'percent', 'allocation'),
            'contradictory_review': ('refund', 'submit', 'contradict', 'conflict'),
            'unsupported_automatic_timeout': ('automatic', 'transaction', 'trigger', 'keeper', 'call'),
            'unsupported_recurring_installments': ('monthly', 'month', 'recurring', 'installment', 'automatic', 'counter'),
        }
        if case['id'] in keywords and not any(w in ' '.join(questions).lower() for w in keywords[case['id']]):
            raise base.SmokeFailure('semantic_oracle', 'Clarification does not address actual problem: '+case['id'])
        return None
    if set(response) != {'status','contract'} or response.get('status') != 'contract':
        raise base.SmokeFailure('semantic_oracle', 'Refused representable contract: '+case['id'])
    inferred = machine.validate(response['contract'])
    oracle = gold(case, cases)
    if inferred != oracle:
        differences = {k:{'expected':oracle[k], 'actual':inferred[k]}
                       for k in oracle if inferred[k] != oracle[k]}
        raise base.SmokeFailure('semantic_oracle', f"Incorrect semantics for {case['id']}: " + base.canonical(differences)[:1400])
    return inferred


def live_head(url: str, model: str, prompt: str, timeout: float) -> str:
    local = base.local_url(url)
    if not model or len(model) > 256:
        raise base.SmokeFailure('preflight', 'Invalid LLM model')
    data = {'model':model, 'stream':False, 'think':False, 'format':'json',
            'options':{'temperature':0,'seed':261118,'num_predict':3000},
            'messages':[{'role':'system','content':PROMPT},{'role':'user','content':prompt}]}
    try:
        req = Request(local+'/api/chat', data=base.canonical(data).encode('utf-8'),
                      headers={'Content-Type':'application/json; charset=utf-8'}, method='POST')
        with urlopen(req, timeout=timeout) as res:
            wire = res.read(400001)
    except Exception as exc:
        raise base.SmokeFailure('llm', f"Local Ollama failed: {type(exc).__name__}: {str(exc)[:250]}") from exc
    if len(wire)>400000:
        raise base.SmokeFailure('llm','Ollama reply too large')
    try:
        content = json.loads(wire.decode('utf-8','strict'))['message']['content']
    except (ValueError, UnicodeError, TypeError, KeyError) as exc:
        raise base.SmokeFailure('llm','Malformed Ollama response') from exc
    if not isinstance(content,str) or not content.strip():
        raise base.SmokeFailure('llm','Empty response')
    return content


def baseline(path: Path) -> str:
    if not path.is_file():
        raise base.SmokeFailure('preflight', 'Missing passing expansion baseline: '+str(path))
    wire = path.read_bytes()
    try:
        obj = json.loads(wire.decode('utf-8','strict'))
    except (UnicodeError, ValueError) as exc:
        raise base.SmokeFailure('preflight','Invalid baseline report') from exc
    s = obj.get('stages',{})
    if (obj.get('status') != 'PASS_SMOKE_NOT_CERTIFIED' or
            obj.get('expected_foundry_tests') != 56 or
            s.get('forge_tests_passed') != 56 or s.get('compiled_artifacts') != 'PASS' or
            not all(isinstance(s.get('forge_'+op),dict) and s['forge_'+op].get('exit_code') == 0
                    for op in ('build','test'))):
        raise base.SmokeFailure('preflight','Baseline must record 56/56, valid artifacts and no failures')
    return base.digest(wire)


def run(options: Options, *, head: Callable[[str],str] | None = None,
        docker: Callable[[list[str],float],dict] | None = None,
        docker_available: bool | None = None) -> dict:
    target = options.output.resolve()
    workspace = target.with_name(target.stem+'.workspace')
    result = {'schema':'nanojev.contract-composition-smoke.v1',
              'status':'BLOCKED_NOT_VERIFIED', 'started_at':base.now(),
              'mode':'READ_ONLY_NO_KEYS_NO_DEPLOYMENT_NO_MODEL_MUTATION',
              'implementation':'live_Ollama_head_+_bounded_generic_state_machine_lowerer',
              'model':options.model, 'foundry_image':options.image,
              'provenance':'assistant-authored gold, not independent expert corpus',
              'assurance_limit':'Bounded acyclic ETH state machine, NOT a general certified compiler',
              'baseline_path':str(options.baseline_report), 'baseline_sha256':None,
              'fixture_sha256':base.digest(FIXTURE.read_bytes()),
              'runner_sha256':base.digest(Path(__file__).read_bytes()),
              'compiler_only':options.compiler_only,
              'external_corpus_sha256':None, 'external_cases':0,
              'workspace':str(workspace), 'stages':{}, 'cases':[], 'generated':{}, 'blockers':[]}
    try:
        corpus = fixture()
        extra,external_sha=load_independent(options.independent_cases, {c['id'] for c in corpus['cases']})
        result['external_corpus_sha256']=external_sha
        result['external_cases']=len(extra)
        all_cases=corpus['cases']+extra
        byid = {c['id']:c for c in all_cases}
        for c in all_cases:
            if c['family']=='positive':
                gold(c, byid)
        result['baseline_sha256'] = baseline(options.baseline_report)
        if options.dry_run:
            result['status']='PLAN_ONLY_NOT_EXECUTED'
            result['stages']['preflight']='PLAN_ONLY'
            result['planned_cases']=[c['id'] for c in all_cases]
            return result
        if target.exists() or workspace.exists():
            raise base.SmokeFailure('preflight','Report/workspace exists; choose a new output filename')
        if options.llm_timeout <= 0 or options.forge_timeout <= 0:
            raise base.SmokeFailure('preflight','Timeouts must be positive')
        if docker_available is None:
            docker_available = shutil.which('docker') is not None
        if not docker_available:
            raise base.SmokeFailure('preflight','Docker CLI unavailable; no Forge run')
        base.local_url(options.ollama_url)
        (workspace/'src').mkdir(parents=True)
        (workspace/'test').mkdir()
        (workspace/'foundry.toml').write_text(
            '[profile.default]\nsrc="src"\ntest="test"\nout="out"\nlibs=[]\nsolc_version="0.8.24"\noptimizer=true\noptimizer_runs=200\n',
            encoding='utf-8')
        result['stages']['preflight']='PASS_56_TEST_BASELINE_VERIFIED'
        accepted={}
        for c in all_cases:
            item={'id':c['id'], 'family':c['family'],
                  'origin':'supplied_external' if c in extra else 'authored_diagnostic',
                  'request_sha256':base.digest(c['request']), 'gold_excluded_from_head':True}
            result['cases'].append(item)
            if options.compiler_only:
                item['result']='BYPASSED_LLM_GOLD_TO_COMPILER'
                if c['family']=='positive':
                    accepted[c['id']]=gold(c,byid)
                continue
            print('[COMPOSITION] LLM '+c['id'], flush=True)
            raw=head(c['request']) if head else live_head(options.ollama_url,options.model,c['request'],options.llm_timeout)
            item['llm_output_sha256']=base.digest(raw)
            item['llm_output']=raw[:4000]
            interpreted=accept(c,parse(raw),byid)
            if interpreted is None:
                item['result']='CORRECT_REFUSAL'
            else:
                item['result']='EXACT_GOLD_MATCH'
                item['contract_sha256']=base.digest(base.canonical(interpreted))
                accepted[c['id']]=interpreted
        expected_positive=6+sum(c['family']=='positive' for c in extra)
        if len(accepted)!=expected_positive:
            raise base.SmokeFailure('semantic_oracle',f'{expected_positive} positive cases required')
        for a,b in [('reversible_split_base','reversible_split_paraphrase'),
                    ('review_workflow_base','review_workflow_paraphrase')]:
            if accepted[a]!=accepted[b]:
                raise base.SmokeFailure('semantic_oracle','Paraphrase meanings differ')
        result['stages']['head']='BYPASSED_COMPILER_ONLY' if options.compiler_only else 'PASS_LIVE' if head is None else 'PASS_INJECTED'
        result['stages']['semantic_oracle']='NOT_TESTED' if options.compiler_only else 'PASS_6_POSITIVE_4_REFUSAL'
        for i,(caseid,spec) in enumerate(accepted.items(),1):
            name='NanoJevComposition'+f'{i:02d}'
            solidity=machine.source(spec,name)
            oracle_tests=machine.tests(gold(byid[caseid],byid),name)
            (workspace/'src'/f'{name}.sol').write_text(solidity,encoding='utf-8')
            (workspace/'test'/f'{name}.t.sol').write_text(oracle_tests,encoding='utf-8')
            result['generated'][name]={'case':caseid,'solidity_sha256':base.digest(solidity),
                                       'independent_gold_test_sha256':base.digest(oracle_tests),
                                       'number_of_rules':len(spec['rules'])}
        count=sum(len(re.findall(r'\bfunction\s+test\w*\s*\(',p.read_text(encoding='utf-8')))
                  for p in (workspace/'test').glob('*.t.sol'))
        if count<60 or len(result['generated'])!=6:
            raise base.SmokeFailure('solidity_emit',f'Unexpected contract/test counts: {len(result["generated"])}/{count}')
        result['stages']['solidity_emit']='PASS_GENERIC_COMPOSITION'
        result['expected_foundry_tests']=count
        invoke=docker if docker else base.run_command
        for op in ('build','test'):
            print('[COMPOSITION] Docker Forge '+op,flush=True)
            proc=invoke(base.docker_args(workspace,options.image,options.network,op),options.forge_timeout)
            result['stages']['forge_'+op]=proc
            if not isinstance(proc,dict) or type(proc.get('exit_code')) is not int:
                raise base.SmokeFailure('forge_'+op,'Malformed subprocess outcome')
            if proc['exit_code']!=0:
                raise base.SmokeFailure('forge_'+op,'Forge '+op+' exited '+str(proc['exit_code']))
            if op=='test':
                text=proc.get('stdout_tail','')
                matches=re.findall(r'Ran\s+\d+\s+test suites?[^\n]*:\s*(\d+)\s+tests?\s+passed,\s*(\d+)\s+failed,\s*(\d+)\s+skipped',text)
                if not matches:
                    raise base.SmokeFailure('forge_test','No complete Forge final suite summary')
                passed,failed,skipped=map(int,matches[-1])
                if passed!=count or failed or skipped:
                    raise base.SmokeFailure('forge_test',f'Expected {count}, got {passed} pass, {failed} fail, {skipped} skip')
                result['stages']['forge_tests_passed']=passed
        for name,entry in result['generated'].items():
            file=workspace/'out'/f'{name}.sol'/f'{name}.json'
            if docker is None and not file.is_file():
                raise base.SmokeFailure('compiled_artifacts','Missing Forge artifact: '+name)
            if file.exists():
                artifact=json.loads(file.read_text(encoding='utf-8'))
                abi={e.get('name') for e in artifact.get('abi',[]) if e.get('type')=='function'}
                needed={'fund','state','fundedAt'} | {r['name'] for r in accepted[entry['case']]['rules']}
                bytecode=artifact.get('bytecode',{}).get('object')
                if not isinstance(bytecode,str) or not bytecode or not needed<=abi:
                    raise base.SmokeFailure('compiled_artifacts','Invalid ABI/bytecode: '+name)
                entry['artifact_sha256']=base.digest(file.read_bytes())
        result['stages']['compiled_artifacts']='PASS' if docker is None else 'INJECTED_NO_ARTIFACT_VERIFICATION'
        result['status']=('PASS_COMPILER_ONLY_NOT_LLM_GENERALIZATION' if options.compiler_only else
                          'PASS_SMOKE_NOT_CERTIFIED' if head is None and docker is None else
                          'PASS_INJECTED_OFFLINE_TEST_ONLY')
    except base.SmokeFailure as e:
        result['blockers'].append({'stage':e.stage,'reason':e.reason})
    except Exception as e:
        result['blockers'].append({'stage':'internal','reason':f'{type(e).__name__}: {str(e)[:320]}'})
        result['traceback_tail']=traceback.format_exc()[-2000:]
    finally:
        result['finished_at']=base.now()
        if not options.dry_run and not target.exists():
            target.parent.mkdir(parents=True,exist_ok=True)
            target.write_text(json.dumps(result,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
    return result


def main(argv=None):
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--output',type=Path,default=Path('runtime/reports/nanojev-contract-composition-smoke-0001.json'))
    ap.add_argument('--baseline-report',type=Path,default=Options.__dataclass_fields__['baseline_report'].default)
    ap.add_argument('--llm-model',default=base.DEFAULT_MODEL)
    ap.add_argument('--ollama-url',default=base.DEFAULT_URL)
    ap.add_argument('--foundry-image',default=base.DEFAULT_IMAGE)
    ap.add_argument('--docker-network',choices=('bridge','none'),default='bridge')
    ap.add_argument('--llm-timeout-s',type=float,default=240.0)
    ap.add_argument('--forge-timeout-s',type=float,default=360.0)
    ap.add_argument('--compiler-only',action='store_true')
    ap.add_argument('--independent-cases',type=Path,default=None,
                    help='Optional separately annotated external corpus; provenance unverified')
    ap.add_argument('--dry-run',action='store_true')
    a=ap.parse_args(argv)
    report=run(Options(output=a.output,baseline_report=a.baseline_report,model=a.llm_model,
                       ollama_url=a.ollama_url,image=a.foundry_image,network=a.docker_network,
                       llm_timeout=a.llm_timeout_s,forge_timeout=a.forge_timeout_s,
                       compiler_only=a.compiler_only,dry_run=a.dry_run,independent_cases=a.independent_cases))
    print(json.dumps({'status':report['status'],'blockers':report['blockers'],
                      'stages':base.summarize_stages(report['stages']),
                      'expected_foundry_tests':report.get('expected_foundry_tests'),
                      'report':str(a.output)},ensure_ascii=False))
    return 0 if report['status'] in {'PASS_SMOKE_NOT_CERTIFIED','PLAN_ONLY_NOT_EXECUTED',
                                    'PASS_COMPILER_ONLY_NOT_LLM_GENERALIZATION'} else 1

if __name__=='__main__':
    sys.exit(main())
