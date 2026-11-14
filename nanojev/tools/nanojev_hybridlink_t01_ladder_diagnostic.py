#!/usr/bin/env python3
"""Read-only T00/T01 training-ladder forensics and hard-case atlas.

Inspects existing run artifacts without loading a Qwen model or altering a
checkpoint. Does not assume the T01 runner's schema, which may be unavailable.
A separate SQLite atlas is diagnostic evidence, never a training lookup table.
"""
from __future__ import annotations
import argparse
import ast
import csv
import hashlib
import json
import math
from collections import Counter, defaultdict
from pathlib import Path
import sqlite3

SCHEMA = 'nanojev.hybridlink.t01-ladder-forensics.v1'
RECOGNIZED = ('history.json','progress.json','result.json','run_config.json','metrics.json',
              'evaluations.json','diagnostics.json','rollouts.json','cases.json',
              'history.jsonl','progress.jsonl','evaluations.jsonl','rollouts.jsonl','cases.jsonl',
              'case_records.csv','cases.csv','rollouts.csv')
CASE_FIELDS = ('source','target','expected','prediction','predicted','actual','case_id','id',
               'correct','correct_stop','generated_exit','false_stop','early_false_stop',
               'p_generated','p_gold','p_zero','final_to_gold_cosine',
               'generated_to_gold_cosine','status','steps','expected_steps','rung')


def finite(v):
    if isinstance(v,bool):return None
    try:
        x=float(v)
        return x if math.isfinite(x) else None
    except (TypeError,ValueError,OverflowError):return None


def digest(path):
    h=hashlib.sha256()
    with path.open('rb') as f:
        for b in iter(lambda:f.read(1024*1024),b''):h.update(b)
    return h.hexdigest()


def rung_of(row, fallback=None):
    x=row.get('rung',row.get('stage',fallback))
    if isinstance(x,int):return 'T%02d'%x
    if isinstance(x,str):
        x=x.upper().strip()
        if x.startswith('T') and x[1:].isdigit():return 'T%02d'%int(x[1:])
        if x in ('B0','Z0','Z1','Z2','R0','R1','R2'):return x
    return fallback


def scan(run, label, max_files=1000, max_bytes=16_000_000):
    """Conservative artifact read, never traverses model cache/checkpoints."""
    run=Path(run)
    if not run.is_dir():raise FileNotFoundError('T01_DIAG_RUN_NOT_DIRECTORY: '+str(run))
    paths=[]
    for name in RECOGNIZED:
        x=run/name
        if x.is_file():paths.append(x)
    # A rung/stage can publish its own metrics; don't walk arbitrary caches.
    for pattern in ('T*/progress.json','T*/history.json','T*/result.json',
                    'rung_*/progress.json','rung_*/history.json','rung_*/result.json',
                    'stages/*/progress.json','stages/*/history.json','stages/*/result.json',
                    'diagnostics/*.json','diagnostics/*.jsonl','reports/*.json',
                    'reports/*.jsonl','reports/*.csv'):
        paths.extend(x for x in run.glob(pattern) if x.is_file())
    paths=sorted(set(paths))[:max_files]
    docs=[];skipped=[]
    for path in paths:
        rel=path.relative_to(run).as_posix()
        if path.stat().st_size>max_bytes:
            skipped.append({'file':rel,'reason':'file_too_large'});continue
        try:
            if path.suffix=='.json':
                data=json.loads(path.read_text(encoding='utf8'))
            elif path.suffix=='.jsonl':
                data=[json.loads(x) for x in path.read_text(encoding='utf8').splitlines() if x.strip()]
            elif path.suffix=='.csv':
                with path.open(newline='',encoding='utf8-sig') as f:data=list(csv.DictReader(f))
            else:continue
            docs.append({'file':rel,'sha256':digest(path),'data':data,'label':label})
        except (OSError,UnicodeError,ValueError,csv.Error) as e:
            skipped.append({'file':rel,'reason':type(e).__name__})
    return docs,skipped


def source_audit(path):
    path=Path(path)
    if not path.is_file():return {'present':False,'path':str(path),'warning':'T01_RUNNER_NOT_IN_SNAPSHOT_OR_PATH'}
    try:
        tree=ast.parse(path.read_text(encoding='utf8'))
    except (UnicodeError,SyntaxError) as e:
        return {'present':True,'path':str(path),'parse_error':type(e).__name__}
    options=set();functions=[]
    for node in ast.walk(tree):
        if isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef)):functions.append(node.name)
        if isinstance(node,ast.Call) and isinstance(node.func,ast.Attribute) and node.func.attr=='add_argument':
            for arg in node.args:
                if isinstance(arg,ast.Constant) and isinstance(arg.value,str) and arg.value.startswith('--'):
                    options.add(arg.value)
    return {'present':True,'path':str(path),'sha256':digest(path),
            'cli_options':sorted(options),'functions':sorted(set(functions)),
            'source_inspected_without_import':True}


def extract_events(doc):
    """Extract actual T00/T01 metrics, and source-level *losses* from retention."""
    data=doc['data'];file=doc['file'];label=doc['label']
    if isinstance(data,dict):
        seq=data.get('history') if isinstance(data.get('history'),list) else [data]
    elif isinstance(data,list):seq=data
    else:return [],[]
    events=[];cases=[]
    for i,item in enumerate(seq):
        if not isinstance(item,dict):continue
        rung=rung_of(item,'T00' if label=='t00' else 'T01')
        step=item.get('step',item.get('global_step'))
        # T01 real trainer emits {t01: {...}, t00: {...}, retention: {...}};
        # T00 curriculum trainer emits {dev: {...}, t00_retention: {...}}.
        metrics=item.get('t01') if label=='t01' and isinstance(item.get('t01'),dict) else item.get('dev',item.get('metrics',{}))
        if not isinstance(metrics,dict):metrics={}
        retention=item.get('t00_retention',item.get('retention',{}))
        if not isinstance(retention,dict):retention={}
        t00metrics=item.get('t00') if isinstance(item.get('t00'),dict) else {}
        if not retention and t00metrics:retention=t00metrics
        fields=('loss','gate_pass','gate_reasons','window_contains_answer','steps_in_rung',
                'qwen_updated','detector_updated','candidate_score','train_mode',
                'geometry_feedback_causal','training_loss')
        vals={k:item[k] for k in fields if k in item}
        shared=('correct_stop_depth','early_false_stops','generated_exit_rate',
                'gold_exit_rate','mean_generated_to_gold_cosine',
                'mean_gain_over_source_cosine','mean_intact_minus_zero_cosine',
                'mean_direct_code_abs_error','mean_complex_code_abs_error','cases')
        for name in shared:
            if name in metrics:vals['current_'+name]=metrics[name]
            if name in t00metrics:vals['t00_'+name]=t00metrics[name]
            if name in retention:vals['t00_'+name]=retention[name]
        for name in ('correct_exit_rate','answer_exits','first_step_continued',
                     'second_step_answer','budget_exhausted','mean_final_gold_cosine',
                     'mean_first_step_gold_cosine','mean_first_step_p_answer',
                     'mean_second_step_p_answer','detector_frozen','qwen_frozen'):
            if name in metrics:vals['t01_'+name]=metrics[name]
        for name in ('baseline_cases','baseline_correct','current_correct',
                     'mean_gold_cosine_drop','retention_pass','max_allowed_cosine_drop'):
            if name in retention:vals['retention_'+name]=retention[name]
        if isinstance(retention.get('lost_sources'),list):
            vals['retention_lost_sources']=list(retention['lost_sources'])
        if isinstance(retention.get('gold_baseline_lost_sources'),list):
            vals['retention_gold_baseline_lost_sources']=list(retention['gold_baseline_lost_sources'])
        if vals or 'step' in item or 'rung' in item:
            events.append({'file':file,'label':label,'rung':rung,'step':step,'row_index':i,**vals})
        collections=[]
        if any(k in item for k in CASE_FIELDS) and any(k in item for k in ('source','case_id','id','expected','target')):
            collections.append(item)
        for key in ('rows','records','examples','cases','rollouts','failure_cases','case_records'):
            x=item.get(key)
            if isinstance(x,list):collections.extend(v for v in x if isinstance(v,dict))
        for j,c in enumerate(collections):
            if not any(k in c for k in CASE_FIELDS):continue
            cr=rung_of(c,rung)
            obs=dict(c); obs.update(_provenance={'file':file,'label':label,'step':step,'row_index':i,'case_index':j},
                                    _rung=cr)
            cases.append(obs)
        # A lost source is *actual per-source failure evidence*, even if the
        # training run did not emit full per-case trajectory/target/probability.
        # Do not manufacture a correct case for a later clean aggregate.
        for kind in ('lost_sources','gold_baseline_lost_sources'):
            for j,src in enumerate(retention.get(kind,[]) if isinstance(retention.get(kind),list) else []):
                if not isinstance(src,str) or not src.strip():continue
                cases.append({'source':src,'correct':False,'status':'RETENTION_LOST',
                    '_retention_loss_kind':kind,'_rung':'T00',
                    '_provenance':{'file':file,'label':label,'step':step,
                                  'row_index':i,'case_index':len(collections)+j,
                                  'retention_field':kind}})
    return events,cases


def terminal_gate(docs,events):
    """A terminal result or accepted rung supersedes transient failed gates."""
    results=[]
    for d in docs:
        if d['label']!='t01' or not d['file'].endswith('result.json') or not isinstance(d['data'],dict):continue
        x=d['data']
        if x.get('accepted_rung')=='T01' and x.get('checkpoint') and 'ACCEPTED' in str(x.get('status','')):
            results.append({'accepted':True,'source':d['file'],'step':x.get('trained_steps'),
                            'status':x.get('status'),'checkpoint':x['checkpoint'],
                            't00_retention':x.get('last_t00_retention'),
                            't01':x.get('last_t01'),'full_latent_certified':False})
        elif x.get('accepted_rung')=='T01' and x.get('checkpoint'):
            results.append({'accepted':True,'source':d['file'],'step':x.get('trained_steps'),
                            'status':x.get('status'),'checkpoint':x['checkpoint'],
                            't00_retention':x.get('last_t00_retention'),
                            't01':x.get('last_t01'),'full_latent_certified':False})
        elif 'status' in x or 'accepted_rung' in x:
            results.append({'accepted':False,'source':d['file'],'step':x.get('trained_steps'),
                            'status':x.get('status'),'checkpoint':x.get('checkpoint'),
                            'full_latent_certified':False})
    if results:return results[-1]
    history=[e for e in events if e['label']=='t01' and isinstance(e.get('step'),int) and e.get('gate_pass') is not None]
    if history:
        latest=max(history,key=lambda e:e['step'])
        return {'accepted':any(e.get('gate_pass') is True for e in history),
                'source':latest['file'],'step':latest['step'],
                'status':'HISTORY_GATE_PASS' if any(e.get('gate_pass') is True for e in history) else 'HISTORY_NO_PASS',
                'checkpoint':None,'full_latent_certified':False}
    return {'accepted':None,'source':None,'step':None,'status':'NOT_OBSERVED',
            'checkpoint':None,'full_latent_certified':False}

def normalize_case(case):
    source=case.get('source',case.get('case_id',case.get('id')))
    target=case.get('target',case.get('expected'))
    pred=case.get('predicted',case.get('prediction',case.get('actual')))
    identity=(str(source),str(target)) if source is not None else (str(case.get('id','')),str(target))
    if not identity[0]:return None
    status=case.get('status')
    depth=finite(case.get('steps'));expected_depth=finite(case.get('expected_steps'))
    explicit=case.get('correct_stop',case.get('correct'))
    if isinstance(explicit,bool):correct=explicit
    elif target is not None and pred is not None:correct=str(target).strip()==str(pred).strip()
    elif status=='ANSWER' and depth is not None and expected_depth is not None:correct=(depth==expected_depth)
    else:correct=None
    false_stop=case.get('false_stop',case.get('early_false_stop'))
    if false_stop is None and status=='ANSWER' and depth is not None and expected_depth is not None:
        false_stop=depth<expected_depth
    prob=finite(case.get('p_generated',case.get('answer_probability')))
    cosine=finite(case.get('generated_to_gold_cosine',case.get('final_to_gold_cosine')))
    return {'case_id':hashlib.sha256(json.dumps(identity).encode()).hexdigest()[:20],
            'source':None if source is None else str(source),
            'target':None if target is None else str(target),
            'observed':None if pred is None else str(pred),
            'rung':case['_rung'],'correct':correct,'false_stop':bool(false_stop),
            'probability':prob,'cosine':cosine,'status':status,
            'provenance':case['_provenance']}


def build_atlas(raw, low_prob=.55,low_cosine=.70):
    cases=[x for c in raw if (x:=normalize_case(c)) is not None]
    groups=defaultdict(list)
    for c in cases:groups[(c['rung'],c['case_id'])].append(c)
    atlas=[]
    for (rung,case_id),sequence in sorted(groups.items()):
        seq=sorted(sequence,key=lambda x:(str(x['provenance']['label']),
                        x['provenance']['step'] if isinstance(x['provenance']['step'],int) else -1,
                        x['provenance']['file'],x['provenance']['case_index']))
        tags=set();bad_seen=False;good_seen=False
        for c in seq:
            if c['correct'] is False:tags.add('observed_failure');bad_seen=True
            if c['correct'] is True:good_seen=True
            if c['false_stop']:tags.add('early_false_stop')
            if c['probability'] is not None and c['probability']<low_prob:
                tags.add('low_answer_probability')
            if c['cosine'] is not None and c['cosine']<low_cosine:
                tags.add('low_gold_alignment')
        if any(c.get('status')=='RETENTION_LOST' for c in seq):tags.add('recorded_t00_retention_loss')
        if good_seen and bad_seen:tags.add('inconsistent_or_regressed')
        if not tags:continue
        rank=40*int('recorded_t00_retention_loss' in tags)+20*int('observed_failure' in tags)+15*int('inconsistent_or_regressed' in tags)+10*int('early_false_stop' in tags)+4*int('low_gold_alignment' in tags)+3*int('low_answer_probability' in tags)
        atlas.append({**seq[-1],'tags':sorted(tags),'priority':rank,'observations':len(seq),
                      'ever_correct':good_seen,'ever_incorrect':bad_seen})
    return sorted(atlas,key=lambda x:(-x['priority'],x['rung'],x['case_id']))


def findings(events,atlas,gate=None,case_count=None):
    result=[]
    t01=[x for x in events if x['label']=='t01']
    if not t01:
        result.append({'code':'T01_PROGRESS_NOT_VISIBLE','severity':'unknown',
            'suggestion':'Provide T01 run history/progress and per-case rollout traces; no T01 performance conclusion is possible.'})
    historical_failures=[e for e in t01 if e.get('gate_pass') is False]
    loss_events=[e for e in t01 if e.get('retention_lost_sources')]
    gate=gate or {'accepted':None}
    if gate.get('accepted') is True:
        result.append({'code':'T01_GATE_ACCEPTED','severity':'measured',
            'suggestion':'T01 passed the recorded gate; do not confuse earlier failed evaluations with the final result. Acceptance does not certify full latent competence.'})
    elif gate.get('accepted') is False or (gate.get('accepted') is None and historical_failures):
        result.append({'code':'T01_GATE_NOT_PASSED','severity':'measured',
            'gate_reasons':dict(Counter(str(v) for e in historical_failures for v in
                (e.get('gate_reasons') or []) if isinstance(e.get('gate_reasons'),list))),
            'suggestion':'No accepted T01 result observed. Localize by rung and exact source before altering training.'})
    if loss_events:
        sources=sorted(set(src for e in loss_events for src in e['retention_lost_sources']))
        result.append({'code':'TRANSIENT_T00_RETENTION_LOSS' if gate.get('accepted') else 'T00_RETENTION_LOSS_OBSERVED',
            'severity':'measured','sources':sources,
            'steps':[e['step'] for e in loss_events],
            'suggestion':'Preserve the historically lost T00 sources in a replay bank. The final accepted gate is separate evidence; missing individual recovered traces cannot be manufactured.'})
    if any(x.get('t00_generated_exit_rate') is not None and finite(x.get('t00_generated_exit_rate')) is not None
           and finite(x.get('t00_generated_exit_rate')) < .5 for x in t01):
        result.append({'code':'T00_EXIT_RETENTION_RISK','severity':'measured',
            'suggestion':'Replay historically correct T00 final-hop cases during T01 updates; preserve detector and Qwen; reject T00 exit regression.'})
    if any('T00_RETENTION_FAILED' in str(x.get('gate_reasons','')) or
           'T00_RETENTION_REGRESSED' in str(x.get('gate_reasons','')) for x in t01):
        result.append({'code':'T00_RETENTION_GATE_FAILED','severity':'historical' if gate.get('accepted') else 'measured',
            'suggestion':'Earlier T00 retention checks failed. Treat T00 preservation as a hard invariant even after T01 acceptance.'})
    if any(x.get('window_contains_answer') is False for x in events):
        result.append({'code':'NON_ANSWER_ANCHORED_WINDOWS','severity':'measured',
            'suggestion':'Measure answer-anchored versus earlier-transition T01 windows separately; earlier windows cannot stand in for a final-answer gate.'})
    if any(finite(x.get('current_early_false_stops')) is not None and x['current_early_false_stops']>0 for x in events):
        result.append({'code':'EARLY_FALSE_ANSWER_EXITS','severity':'measured',
            'suggestion':'Protect intermediate states from false ANSWER stops; log exact stop depth and detector probability.'})
    if any(finite(x.get('current_mean_intact_minus_zero_cosine')) is not None and x['current_mean_intact_minus_zero_cosine']<=0 for x in events):
        result.append({'code':'NO_GENERATED_FEEDBACK_GAIN','severity':'measured',
            'suggestion':'Compare intact and zero-latent feedback on identical cases; detector confidence alone is not causal proof.'})
    if any('inconsistent_or_regressed' in x['tags'] or 'recorded_t00_retention_loss' in x['tags'] for x in atlas):
        result.append({'code':'CASE_LEVEL_RETENTION_RISK','severity':'measured',
            'suggestion':'Prioritize source-level T00 regressions and hard examples for zero-regression rehearsal.'})
    if case_count == 0 or (case_count is None and not atlas):
        result.append({'code':'PER_CASE_EVIDENCE_MISSING_OR_ALL_CLEAN','severity':'unknown',
            'suggestion':'Emit paired source, target, stop depth, probability and cosine for each individual rung; aggregates alone do not localize failures.'})
    elif atlas and all('recorded_t00_retention_loss' in x['tags'] for x in atlas):
        result.append({'code':'DETAILED_PER_CASE_TRAJECTORIES_MISSING','severity':'unknown',
            'suggestion':'Named lost T00 sources are recovered from retention summaries, but targets and per-hop vectors are not recorded. Add paired trajectory instrumentation.'})
    result.append({'code':'FRONTIER_METHOD_TRANSFER','severity':'proposal',
        'suggestion':'Use hard-case banking, deterministic case generation, progressive rung probes, scaled reversible microedits and zero-regression replay. Do not import arithmetic weights or equate cancellation accuracy with T01 latent competence.'})
    return result

def save_atlas(path,atlas):
    con=sqlite3.connect(path)
    try:
        con.executescript('''CREATE TABLE IF NOT EXISTS hard_examples(
            rung TEXT NOT NULL, case_id TEXT NOT NULL, source TEXT, target TEXT,
            observed TEXT, priority INTEGER NOT NULL, tags_json TEXT NOT NULL,
            observations INTEGER NOT NULL, ever_correct INTEGER NOT NULL,
            ever_incorrect INTEGER NOT NULL, provenance_json TEXT NOT NULL,
            PRIMARY KEY(rung,case_id));
        CREATE INDEX IF NOT EXISTS idx_hard_examples_rank ON hard_examples(priority DESC,rung);''')
        # Deterministic, transactional refresh. This DB is never read by trainer.
        con.execute('DELETE FROM hard_examples')
        con.executemany('''INSERT INTO hard_examples VALUES(?,?,?,?,?,?,?,?,?,?,?)''',[
            (x['rung'],x['case_id'],x['source'],x['target'],x['observed'],x['priority'],
             json.dumps(x['tags']),x['observations'],int(x['ever_correct']),
             int(x['ever_incorrect']),json.dumps(x['provenance'],sort_keys=True)) for x in atlas])
        con.commit()
    finally:con.close()


def markdown(report):
    lines=['# HybridLink T01 ladder diagnostic','',
           'Read-only evidence assessment. This is not a T01 certification or training patch.','',
           '## Provenance and coverage','',
           '- T01 runner present: '+str(report['t01_source'].get('present',False)),
           '- Evidence files: '+str(len(report['evidence'])),
           '- Final T01 gate: '+str(report['terminal_gate']['accepted'])+' ('+str(report['terminal_gate']['status'])+')',
           '- Event records: '+str(report['event_count']),
           '- Per-case observations: '+str(report['case_count']),
           '- Hard examples with actual evidence: '+str(len(report['hard_cases'])),
           '- Coverage note: named retention losses count as source-level evidence, not full per-hop observations','',
           '## Observed rung trajectory','',
           '| Rung | Steps observed | Last exit rate | Last T00 retention exit rate | Gate |',
           '|---|---:|---:|---:|---|']
    for r in report['rungs']:
        def fmt(x):return 'unavailable' if x is None else str(x)
        lines.append('| '+r['rung']+' | '+str(r['events'])+' | '+fmt(r['last_current_exit_rate'])+' | '+fmt(r['last_t00_exit_rate'])+' | '+fmt(r['last_gate_pass'])+' |')
    lines+=['','## T00 retention counterexamples','']
    named=[x for x in report['hard_cases'] if 'recorded_t00_retention_loss' in x['tags']]
    if named:
        for x in named:lines.append('- '+str(x['source'])+' — '+str(x['observations'])+' logged loss observation(s); target and latent trajectory not assumed')
    else:lines.append('- No named retention losses in the inspected artifacts')
    lines+=['','## T01 transition details','']
    for e in sorted((x for x in report['events'] if x['label']=='t01' and isinstance(x.get('step'),int)),key=lambda x:x['step']):
        lines.append('- Step '+str(e['step'])+': T01 stop depth '+str(e.get('t01_correct_stop_depth','unavailable'))+
          '; T00 retained '+str(e.get('retention_current_correct','unavailable'))+'/'+
          str(e.get('retention_baseline_correct','unavailable'))+
          '; first-step cosine '+str(e.get('t01_mean_first_step_gold_cosine','unavailable'))+
          '; final cosine '+str(e.get('t01_mean_final_gold_cosine','unavailable'))+
          '; gate '+str(e.get('gate_pass','unavailable')))
    lines+=['','## Where previous research may help','']
    for x in report['findings']:
        lines.append('### '+x['code']+' ('+x['severity']+')')
        lines.append(x['suggestion']);lines.append('')
    lines+=['## Evidence limits','',
       '- T00/T01 latent-stop correctness and T00 retention must be measured independently of arithmetic cancellation.',
       '- A positive cosine or detector exit alone is not a correct numeric result.',
       '- Diagnostic cases are not training targets unless a future explicitly reviewed cutover imports them.',
       '- No unseen rung or missing per-case outcome has been inferred as a passing result.','']
    return '\n'.join(lines)


def inspect(args):
    source=source_audit(Path(args.t01_source) if args.t01_source else Path(__file__).with_name('nanojev_hybridlink_t01_retention_cutover.py'))
    docs=[];skipped=[]
    for label,loc in [('t00',args.t00_run),('t01',args.t01_run)]:
        if loc:
            a,b=scan(loc,label,args.max_files,args.max_bytes)
            docs.extend(a);skipped.extend([{'label':label,**x} for x in b])
    events=[];raw_cases=[]
    for doc in docs:
        ev,ca=extract_events(doc);events.extend(ev);raw_cases.extend(ca)
    # Avoid double counting history and progress duplicates; prefer history.
    def ident(x):return (x['label'],x['rung'],str(x['step']))
    ordered=sorted(events,key=lambda x:(0 if x['file'].endswith('history.json') else 1,x['file']))
    unique={}
    for e in ordered:unique.setdefault(ident(e),e)
    steps=list(unique.values())
    grouped=defaultdict(list)
    for e in steps:grouped[e['rung']].append(e)
    rungs=[]
    for rung,values in sorted(grouped.items()):
        last=sorted(values,key=lambda x:(x['step'] if isinstance(x['step'],int) else -1))[-1]
        rungs.append({'rung':rung,'events':len(values),
            'last_step':last.get('step'),'last_current_exit_rate':last.get('current_generated_exit_rate',last.get('t01_correct_exit_rate')),
            'last_t00_exit_rate':last.get('t00_generated_exit_rate'),
            'last_gate_pass':last.get('gate_pass'),'last_gate_reasons':last.get('gate_reasons'),
            'last_gold_cosine':last.get('current_mean_generated_to_gold_cosine',last.get('t01_mean_final_gold_cosine')),
            'last_early_false_stops':last.get('current_early_false_stops')})
    atlas=build_atlas(raw_cases,args.low_probability,args.low_cosine)
    gate=terminal_gate(docs,steps)
    report={'schema':SCHEMA,'status':'diagnostic_only','t01_source':source,
            'evidence':[{'label':d['label'],'file':d['file'],'sha256':d['sha256']} for d in docs],
            'skipped':skipped,'event_count':len(steps),'case_count':len(raw_cases),
            'rungs':rungs,'events':steps,'hard_cases':atlas,'terminal_gate':gate,
            'findings':findings(steps,atlas,gate,len(raw_cases)),'no_training_mutation':True,
            't01_per_case_coverage':'observed' if raw_cases else 'not_present_in_artifacts',
            'no_model_weights_loaded':True}
    output=Path(args.output).resolve()
    for loc in (args.t00_run,args.t01_run):
        if loc:
            src=Path(loc).resolve()
            if output==src or src in output.parents or output in src.parents:
                raise ValueError('T01_DIAG_OUTPUT_OVERLAPS_SOURCE')
    output.mkdir(parents=True,exist_ok=True)
    (output/'diagnostic.json').write_text(json.dumps(report,indent=2,sort_keys=True,default=str)+'\n',encoding='utf8')
    (output/'diagnostic.md').write_text(markdown(report)+'\n',encoding='utf8')
    with (output/'rung_metrics.csv').open('w',newline='',encoding='utf8') as f:
        w=csv.DictWriter(f,fieldnames=['rung','events','last_step','last_current_exit_rate','last_t00_exit_rate',
                                       'last_gate_pass','last_gate_reasons','last_gold_cosine','last_early_false_stops'])
        w.writeheader();w.writerows(rungs)
    # Every recorded rung evaluation is preserved separately, not just final metrics.
    timeline_fields=['label','rung','step','gate_pass','gate_reasons','train_mode',
       't01_correct_stop_depth','t01_correct_exit_rate','t01_first_step_continued',
       't01_second_step_answer','t01_budget_exhausted',
       't01_mean_first_step_gold_cosine','t01_mean_final_gold_cosine',
       't01_mean_first_step_p_answer','t01_mean_second_step_p_answer',
       't00_correct_stop_depth','t00_generated_exit_rate',
       'retention_baseline_correct','retention_current_correct',
       'retention_mean_gold_cosine_drop','retention_pass','retention_lost_sources']
    with (output/'ladder_timeline.csv').open('w',newline='',encoding='utf8') as f:
        w=csv.DictWriter(f,fieldnames=timeline_fields)
        w.writeheader()
        for row in sorted(steps,key=lambda x:(x['label'],x['step'] if isinstance(x['step'],int) else -1)):
            w.writerow({k:json.dumps(row[k]) if isinstance(row.get(k),(list,dict)) else row.get(k)
                        for k in timeline_fields})
    with (output/'hard_examples.csv').open('w',newline='',encoding='utf8') as f:
        w=csv.DictWriter(f,fieldnames=['rung','case_id','source','target','observed','priority','tags','observations','ever_correct','ever_incorrect'])
        w.writeheader();w.writerows({k:x[k] for k in w.fieldnames} for x in atlas)
    save_atlas(output/'hard_examples.sqlite3',atlas)
    summary={'schema':SCHEMA,'t01_runner_present':source.get('present',False),
             'rungs':[x['rung'] for x in rungs], 'events':len(steps),
             'case_observations':len(raw_cases),'hard_cases':len(atlas),'t01_accepted':gate['accepted'],
             'retention_loss_sources':sum('recorded_t00_retention_loss' in x['tags'] for x in atlas),
             'findings':[x['code'] for x in report['findings']],
             'output':str(output),'status':'diagnostic_only'}
    print('HYBRIDLINK_T01_LADDER_DIAGNOSTIC='+json.dumps(summary,sort_keys=True),flush=True)
    return report


def parser():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--t01-run',required=True,help='T01 output directory; never modified')
    p.add_argument('--t00-run',help='Prior T00 output directory for retention baseline')
    p.add_argument('--t01-source',help='Optional path to T01 runner .py for AST-only interface audit')
    p.add_argument('--output',required=True,help='Separate diagnostic output directory')
    p.add_argument('--low-probability',type=float,default=.55)
    p.add_argument('--low-cosine',type=float,default=.70)
    p.add_argument('--max-files',type=int,default=1000)
    p.add_argument('--max-bytes',type=int,default=16_000_000)
    return p


def main(argv=None):
    args=parser().parse_args(argv)
    if args.max_files<1 or args.max_bytes<1024 or not 0<=args.low_probability<=1 or not -1<=args.low_cosine<=1:
        raise ValueError('T01_DIAG_INVALID_LIMITS')
    return inspect(args)

if __name__=='__main__':main()
