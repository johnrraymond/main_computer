#!/usr/bin/env python3
"""Small, configurable frozen-Qwen register-template screening experiment.

Only the LASSO *procedure* transfers from a two-number run: representation,
alpha ratio, hierarchy stage, layer selection and convergence parameters.
EVERY template learns new, independent sparse heads and feature coordinates.
Every template uses the same operand pairs and operation variants.

Use --prepare-only, --capture-only, --analyze-only, and --resume. Raw captured
activations contain all tokens and all available layers; analysis can use
all tokens or a suffix after the operation without pooling.

Dependencies: the existing nanojev_two_number_sparse_decoder.py,
nanojev_two_number_sparse_decoder_fast.py, and their own dependencies.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import threading
import time
from pathlib import Path

import numpy as np
from threadpoolctl import threadpool_limits

import nanojev_two_number_sparse_decoder as base
import nanojev_two_number_sparse_decoder_fast as fast

sparse = base.sparse
SCHEMA = 'nanojev.register-template-screen.v1'
DEFAULT_ROOT = Path.home() / 'NanoJev' / 'runs' / 'register_template_screen_v1'

# The declarations are hypotheses, not privileged syntaxes. Both names are
# referenced by downstream operations; the operand text is never repeated.
TEMPLATES = {
    'ab': ('A', 'B', ' = '),
    'xx': ('XXXXXX', 'YYYYYY', ' = '),
    'r0': ('R0', 'R1', ': '),
    'brackets': ('[0]', '[1]', ' = '),
    'dollar': ('$0', '$1', ' := '),
    'long': ('REGISTER_FIRST', 'REGISTER_SECOND', ' = '),
}
OPERATIONS = {
    'add': '+', 'sub': '-', 'mul': '*', 'gt': '>',
}


def parse_csv(value, allowed, name):
    result = tuple(item.strip() for item in value.split(',') if item.strip())
    if not result or len(set(result)) != len(result) or any(item not in allowed for item in result):
        raise ValueError(f'REGISTER_INVALID_{name}:{value}')
    return result


def number_text(representation, number):
    value = int(number)
    if representation == 'decimal':
        return str(value)
    logbar = sparse.multi.encode(value)
    if representation == 'logbar':
        return logbar
    if representation == 'paired':
        return f'{value}, {logbar}'
    raise ValueError('REGISTER_INVALID_REPRESENTATION')


def render(template, operation, representation, pair):
    a, b, sep = TEMPLATES[template]
    op = OPERATIONS[operation]
    first, second = (number_text(representation, number) for number in pair)
    return f'{a}{sep}{first}\n{b}{sep}{second}\noperation: {a} {op} {b}\nAnswer:'


def choose_hyperparams(args):
    inherited = None
    if args.inherit_summary:
        path = args.inherit_summary.expanduser().resolve()
        source = json.loads(path.read_text(encoding='utf-8'))
        rows = source.get('rows', source.get('results', []))
        candidates = [r for r in rows if r.get('joint_pass_rate') is not None and
                      r.get('all_heads_converged', True)]
        if not candidates:
            raise ValueError(f'REGISTER_SUMMARY_NO_VALID_ROWS:{path}')
        # Choose empirical best among completed/available points, reporting the
        # source so an unfinished current run cannot silently become authoritative.
        inherited_row = max(candidates, key=lambda r: (
            float(r['joint_pass_rate']), -int(r['coefficient_count']),
            -float(r['alpha_ratio'])))
        inherited = {key: inherited_row[key] for key in
                     ('representation','alpha_ratio','stage','joint_pass_rate','coefficient_count')}
        settings = {
            'representation': str(inherited['representation']),
            'alpha_ratio': float(inherited['alpha_ratio']),
            'stage': int(inherited['stage']),
            'layers': ','.join(map(str,source.get('layers',[16,28]))),
        }
    else:
        settings = {'representation': 'paired', 'alpha_ratio': 0.005,
                    'stage': 2, 'layers': '16,28'}
    for k in settings:
        explicit = getattr(args,k)
        if explicit is not None:
            settings[k] = explicit
    if settings['representation'] not in base.KINDS:
        raise ValueError('REGISTER_BAD_REPRESENTATION')
    if not (0 < float(settings['alpha_ratio']) <= 1):
        raise ValueError('REGISTER_BAD_ALPHA_RATIO')
    if not 1 <= int(settings['stage']) <= len(sparse.hier.REGIMES):
        raise ValueError('REGISTER_BAD_STAGE')
    return settings, ({'source':str(args.inherit_summary.resolve()),
                       'selected_row':inherited} if inherited else None)


def make_plan(args):
    hyper, inherited = choose_hyperparams(args)
    templates = parse_csv(args.templates,TEMPLATES,'TEMPLATES')
    operations = parse_csv(args.operations,OPERATIONS,'OPERATIONS')
    pairs = [list(p) for p in base.generate_pairs(args.pair_count,args.seed)]
    prompts = {}
    for template in templates:
        for pair_id,pair in enumerate(pairs):
            for op in operations:
                txt = render(template,op,hyper['representation'],pair)
                digest = hashlib.sha256(txt.encode('utf-8')).hexdigest()
                prompts[f'{template}:{pair_id}:{op}'] = {'digest':digest,'prompt':txt,
                    'template':template,'pair_id':pair_id,'operation':op}
    settings = {
        'schema':SCHEMA,'pairs':pairs,'templates':list(templates),
        'operations':list(operations),'hyperparameters':hyper,
        'pair_count':len(pairs),'seed':args.seed,
        'capture':{'model':args.model,'store_dtype':args.store_dtype,
                   'max_prompt_tokens':args.max_prompt_tokens},
        'readout':{'scope':args.readout_scope or 'suffix',
                   'tail_tokens':args.tail_tokens if args.tail_tokens is not None else 4},
        'source_hyperparameter_summary':inherited,
        'prompts':prompts,
        'note':'New independent LASSO models for every template, shared across operations; no transferred features or weights',
    }
    return settings


def record_path(root,digest):
    return root/'records'/f'{digest}.npz'


def get_record(root, task, identity):
    path = record_path(root,task['digest'])
    if not path.is_file():
        raise FileNotFoundError(f'REGISTER_RECORD_MISSING:{path}')
    with np.load(path,allow_pickle=False) as loaded:
        metadata = json.loads(str(loaded['metadata'].item()))
        tensor = np.asarray(loaded['hidden_states'])
    if (metadata.get('prompt') != task['prompt'] or
        metadata.get('sha256') != task['digest'] or
        metadata.get('identity') != identity or
        metadata.get('shape') != list(tensor.shape) or
        tensor.ndim!=3 or tensor.shape[1]!=len(metadata.get('input_ids',[])) or
        not np.isfinite(tensor).all()):
        raise RuntimeError(f'REGISTER_RECORD_INVALID:{path}')
    return metadata,tensor


def capture(args):
    root = args.output_root.expanduser().resolve()
    manifest = root/'manifest.json'
    if args.analyze_only:
        if not manifest.is_file():
            raise FileNotFoundError(f'REGISTER_MANIFEST_MISSING:{manifest}')
        settings = json.loads(manifest.read_text(encoding='utf-8'))
        if settings.get('schema') != SCHEMA:
            raise ValueError('REGISTER_MANIFEST_WRONG_SCHEMA')
        # A frozen capture can be analyzed again with different regularization,
        # hierarchy, layer selection or observation window. It cannot change the
        # actual operand representation without acquiring different prompts.
        previous=settings['hyperparameters']['representation']
        if args.inherit_summary:
            imported,_ = choose_hyperparams(args)
            if imported['representation'] != previous:
                raise ValueError('REGISTER_REPRESENTATION_CHANGED_RECAPTURE_REQUIRED')
            for field in ('alpha_ratio','stage','layers'):
                settings['hyperparameters'][field]=imported[field]
        if args.representation is not None and args.representation != previous:
            raise ValueError('REGISTER_REPRESENTATION_CHANGED_RECAPTURE_REQUIRED')
        for field in ('alpha_ratio','stage','layers'):
            explicit=getattr(args,field)
            if explicit is not None: settings['hyperparameters'][field]=explicit
        if args.readout_scope is not None:
            settings['readout']['scope']=args.readout_scope
        if args.tail_tokens is not None:
            settings['readout']['tail_tokens']=args.tail_tokens
        if args.tail_tokens is not None and args.tail_tokens<1:
            raise ValueError('REGISTER_BAD_TAIL_TOKENS')
        if (not 0 < settings['hyperparameters']['alpha_ratio'] <= 1 or
            not 1 <= settings['hyperparameters']['stage'] <= len(sparse.hier.REGIMES)):
            raise ValueError('REGISTER_BAD_ANALYSIS_HYPERPARAMETERS')
        return settings
    settings = make_plan(args)
    if args.prepare_only:
        base.emit('register_plan',samples=len(settings['prompts']),
            templates=settings['templates'], operations=settings['operations'],
            hyperparameters=settings['hyperparameters'],readout=settings['readout'],
            example=render(settings['templates'][0],settings['operations'][0],
                settings['hyperparameters']['representation'],settings['pairs'][0]))
        return settings
    if manifest.is_file():
        previous = json.loads(manifest.read_text(encoding='utf-8'))
        if previous != settings:
            raise RuntimeError('REGISTER_MANIFEST_DRIFT')
        if not args.resume:
            raise RuntimeError('REGISTER_EXISTING_CAPTURE_USE_RESUME')
    elif root.exists() and any(root.iterdir()):
        raise RuntimeError('REGISTER_CAPTURE_ROOT_NOT_EMPTY')
    tasks = {task['digest']:task for task in settings['prompts'].values()}
    identity_path=root/'model_identity.json'
    existing_id = json.loads(identity_path.read_text(encoding='utf-8')) if identity_path.is_file() else None
    required = [task for key,task in tasks.items() if not record_path(root,key).exists()]
    if not required:
        if existing_id is None:
            raise RuntimeError('REGISTER_CAPTURE_IDENTITY_MISSING')
        for task in tasks.values():
            get_record(root,task,existing_id)
        base.emit('register_capture_already_complete',prompts=len(tasks))
        return settings
    torch,model,tokenizer,device,info=sparse.cassette.load_source(args.model,args)
    identity={'model':args.model,'model_info':info}
    try:
        if existing_id is not None and existing_id != identity:
            raise RuntimeError('REGISTER_MODEL_IDENTITY_DRIFT')
        (root/'records').mkdir(parents=True,exist_ok=True)
        if not manifest.exists(): sparse.dec.write_json(manifest,settings)
        if not identity_path.exists(): sparse.dec.write_json(identity_path,identity)
        for index,task in enumerate(tasks.values(),1):
            path=record_path(root,task['digest'])
            if path.is_file():
                get_record(root,task,identity)
            else:
                md,tensor=sparse.capture_full(torch,model,tokenizer,task['prompt'],device,
                                              args.max_prompt_tokens,args.store_dtype)
                md['identity']=identity
                tmp=path.with_name(path.name+'.partial')
                with tmp.open('wb') as f:
                    np.savez_compressed(f,metadata=json.dumps(md,sort_keys=True),hidden_states=tensor)
                tmp.replace(path)
            if index % args.report_every == 0 or index == len(tasks):
                base.emit('register_capture_progress',completed=index,total=len(tasks))
    finally:
        del model
    base.emit('register_capture_complete',prompts=len(tasks),root=str(root))
    return settings


def design_matrix(root,settings,template,layers,identity):
    prompts=settings['prompts']; operations=settings['operations']
    scope=settings['readout']['scope']
    limit=settings['readout']['tail_tokens']
    tasks=[prompts[f'{template}:{i}:{op}'] for i in range(len(settings['pairs'])) for op in operations]
    distinct={}; lengths=[]
    for task in tasks:
        if task['digest'] not in distinct:
            with np.load(record_path(root,task['digest']),allow_pickle=False) as f:
                md=json.loads(str(f['metadata'].item()))
            if md['sha256'] != task['digest']:
                raise ValueError('REGISTER_MANIFEST_RECORD_DRIFT')
            distinct[task['digest']]=min(int(md['input_token_count']),limit) if scope=='suffix' else int(md['input_token_count'])
        lengths.append(distinct[task['digest']])
    slots=max(lengths)
    data=None; cache={}; width=None
    for i,task in enumerate(tasks):
        if task['digest'] not in cache:
            _,raw=get_record(root,task,identity)
            selected=np.asarray(raw[layers],dtype=np.float32)
            if scope=='suffix': selected=selected[:,-limit:,:]
            cache[task['digest']]=selected
        selected=cache[task['digest']]
        if data is None:
            width=selected.shape[-1]
            data=np.zeros((len(tasks),len(layers)*slots*width),dtype=np.float32)
        if selected.shape[-1]!=width:
            raise ValueError('REGISTER_HIDDEN_WIDTH_DRIFT')
        data[i]=np.pad(selected,((0,0),(slots-selected.shape[1],0),(0,0))).reshape(-1)
    pairs=np.repeat(np.asarray(settings['pairs'],dtype=np.int64),len(operations),axis=0)
    op_ids=[op for _ in settings['pairs'] for op in operations]
    return data,pairs,op_ids,{'layers':layers,'slots':slots,'neuron_width':width,
        'full_features':data.shape[1],'samples':len(tasks),'alignment':'right aligned',
        'pooling':'none','readout_scope':scope,'template':template,
        'unique_prompts':len(cache)}


def operation_metrics(pairs,pred,operations):
    overall,passing,rel=base.joint_metrics(pairs,pred)
    by_operation={}
    for op in dict.fromkeys(operations):
        idx=np.array([name==op for name in operations],dtype=bool)
        scores,_,_=base.joint_metrics(pairs[idx],pred[idx])
        by_operation[op]={'joint_pass_rate':scores['both_within_5pct_rate'],
            'slot_1_pass_rate':scores['slot_1_pass_rate'],
            'slot_2_pass_rate':scores['slot_2_pass_rate']}
    # Each pair is repeated in operation order. This is the measurement of
    # downstream readout drift for the same register assignment.
    op_count=len(dict.fromkeys(operations))
    arr=pred.reshape((-1,op_count,2))
    true=np.asarray(pairs,dtype=np.float64).reshape((-1,op_count,2))[:,0,:]
    deviation=np.ptp(arr,axis=1) / np.maximum(1.0,np.abs(true))
    per_pair_success=passing.all(axis=1).reshape((-1,op_count)).all(axis=1)
    return overall,by_operation,{
        'all_operations_joint_pass_rate':float(per_pair_success.mean()),
        'normalized_operation_drift_mean':float(np.mean(deviation)),
        'normalized_operation_drift_max':float(np.max(deviation)),
        'worst_operation_joint_pass_rate':float(min(v['joint_pass_rate'] for v in by_operation.values())),
    },passing,rel


def capture_availability(root, settings):
    """Cheap, complete preflight; capture publishes every record atomically."""
    digests = tuple(dict.fromkeys(task['digest'] for task in settings['prompts'].values()))
    available = sum(record_path(root, digest).is_file() for digest in digests)
    return available, len(digests)


def ensure_analysis_capture(root, settings, args):
    """Never start a potentially hours-long fit against unfinished capture."""
    last_report = None
    last_report_at = 0.0
    while True:
        available, total = capture_availability(root, settings)
        if available == total:
            base.emit('register_analysis_capture_ready', captured=available, required=total)
            return
        if not args.wait_for_capture:
            base.emit('register_analysis_capture_incomplete', captured=available,
                      required=total, missing=total-available,
                      advice='Finish CUDA capture, then rerun analysis; or pass --wait-for-capture')
            raise RuntimeError(f'REGISTER_CAPTURE_INCOMPLETE:{available}/{total}:use_--wait-for-capture')
        now = time.monotonic()
        if last_report != available or now-last_report_at >= 30.0:
            base.emit('register_analysis_waiting_for_capture', captured=available,
                      required=total, missing=total-available,
                      poll_seconds=args.capture_poll_seconds)
            last_report = available
            last_report_at = now
        time.sleep(args.capture_poll_seconds)


def continuation_schedule(value, target):
    """Only stronger alpha ratios may seed the target solve; never change target alpha.

    The head's alpha_max and target normalization are recomputed from the NEW
    registration observations. No coefficients or feature IDs transfer from
    the previous two-number experiment or from another template/head.
    """
    import math
    if value.strip().lower() in ('', 'none', 'off'):
        return ()
    ratios = tuple(float(raw.strip()) for raw in value.split(',') if raw.strip())
    if any(not math.isfinite(r) or not 0. < r <= 1. for r in ratios):
        raise ValueError('REGISTER_INVALID_WARMUP_ALPHA')
    if len(ratios) != len(set(ratios)) or any(left <= right for left, right in zip(ratios, ratios[1:])):
        raise ValueError('REGISTER_WARMUP_MUST_DESCEND')
    # Ratios at or below the requested target cannot seed that stronger-to-weaker fit.
    return tuple(r for r in ratios if r > float(target))


class ProgressFastHeadFactory(fast.FastHeadFactory):
    """Head-by-head LASSO continuation with fresh, template-local sparse state.

    The final output is ALWAYS the fit at the requested alpha ratio. Extra
    stronger-penalty fits are internal initialization, not scoring candidates.
    """
    def __init__(self, *arguments, progress=None, progress_every=10,
                 warmup_ratios=(), **keywords):
        super().__init__(*arguments, **keywords)
        self.progress = progress
        self.progress_every = progress_every
        self.warmup_ratios = tuple(warmup_ratios)
        self.continuation_fit_calls = 0

    def fit(self, name, subset, response):
        target_ratio = float(self.ratio)
        if self.progress is not None:
            self.progress['phase'] = 'lasso_fit'
            self.progress['head'] = name
            self.progress['head_started'] = time.perf_counter()
        result = None
        try:
            for ratio in (*self.warmup_ratios, target_ratio):
                super().set_ratio(ratio)
                if self.progress is not None:
                    self.progress['fitting_alpha_ratio'] = float(ratio)
                    self.progress['fitting_target_ratio'] = target_ratio
                try:
                    result = super().fit(name, subset, response)
                except sparse.SparseFitFailure as exc:
                    exc.diagnostics['continuation_alpha_ratio'] = float(ratio)
                    exc.diagnostics['requested_alpha_ratio'] = target_ratio
                    exc.diagnostics['warmup_path'] = list(self.warmup_ratios)
                    raise
                if ratio != target_ratio:
                    self.continuation_fit_calls += 1
        finally:
            super().set_ratio(target_ratio)
            if self.progress is not None:
                self.progress['fitting_alpha_ratio'] = None
        if self.progress is not None:
            self.progress['head_count'] += 1
            count = self.progress['head_count']
            if count == 1 or count % self.progress_every == 0:
                base.emit('register_analysis_head_progress',
                          template=self.progress['template'],
                          position=self.progress['position'],
                          heads_completed=count,
                          latest_head=name,
                          unique_solves=self.unique_solves,
                          warmup_solves=self.continuation_fit_calls,
                          cache_hits=self.cache_hits,
                          elapsed_seconds=round(time.perf_counter()-self.progress['template_started'],2))
        return result


def analysis_heartbeat(state, stop_event, seconds):
    """Heartbeat while a single head is expensive, without per-iteration spam."""
    while not stop_event.wait(seconds):
        base.emit('register_analysis_heartbeat',
                  template=state.get('template'), position=state.get('position'),
                  phase=state.get('phase'), head=state.get('head'),
                  current_alpha_ratio=state.get('fitting_alpha_ratio'),
                  requested_alpha_ratio=state.get('fitting_target_ratio'),
                  heads_completed=state.get('head_count', 0),
                  template_elapsed_seconds=round(time.perf_counter()-state.get('template_started',time.perf_counter()),2))


def analyze(args,settings):
    root=args.output_root.expanduser().resolve()
    result_dir = (args.results_root or root/'analysis').expanduser().resolve()
    if result_dir==root or result_dir.is_relative_to(root/'records'):
        raise ValueError('REGISTER_RESULTS_MUST_NOT_OVERWRITE_CAPTURE')
    # Report immediately; no LASSO work is allowed on a partial capture.
    base.emit('register_analysis_start', root=str(root), results_root=str(result_dir),
              templates=settings['templates'], operations=settings['operations'],
              alpha_ratio=settings['hyperparameters']['alpha_ratio'],
              stage=settings['hyperparameters']['stage'],
              layers=settings['hyperparameters']['layers'],
              readout=settings['readout'], threads=args.threads)
    ensure_analysis_capture(root, settings, args)
    identity=json.loads((root/'model_identity.json').read_text(encoding='utf-8'))
    result_dir.mkdir(parents=True,exist_ok=True)
    first=next(iter(settings['prompts'].values()))
    _,raw=get_record(root,first,identity)
    layers=sparse.parse_layers(settings['hyperparameters']['layers'],raw.shape[0])
    hyper=settings['hyperparameters']; ratio=float(hyper['alpha_ratio'])
    stage=int(hyper['stage']); cuts=sparse.hier.REGIMES[stage-1]
    warmup_ratios=continuation_schedule(args.warmup_ratios,ratio)
    rows=[]; failures=[]
    def checkpoint():
        if rows: sparse.dec.write_csv(result_dir/'register_template_scores.csv',rows,list(rows[0]))
        sparse.dec.write_json(result_dir/'register_template_summary.json',{
            'schema':SCHEMA,'capture_root':str(root),'readout':settings['readout'],
            'hyperparameters':hyper,'source_hyperparameter_summary':settings['source_hyperparameter_summary'],
            'in_sample_only':True,'rows':rows,'failures':failures,
            'warmup_ratios':list(warmup_ratios),
            'score_policy':'joint readback, worst operation, drift, coefficient count; no fused arbitrary score'})
        sparse.dec.write_json(result_dir/'register_template_failures.json',failures)
    for template in settings['templates']:
        started=time.perf_counter()
        state={'template':template,'position':None,'phase':'design_matrix',
               'head':None,'head_count':0,'template_started':started}
        stop_event=threading.Event()
        heartbeat=threading.Thread(target=analysis_heartbeat,
            args=(state,stop_event,args.heartbeat_seconds), daemon=True)
        base.emit('register_analysis_template_start', template=template,
                  position_count=2, alpha_ratio=ratio, stage=stage,
                  warmup_ratios=list(warmup_ratios))
        heartbeat.start()
        try:
            x,pairs,operations,layout=design_matrix(root,settings,template,layers,identity)
            base.emit('register_analysis_design_ready',template=template,
                      samples=layout['samples'],full_features=layout['full_features'],
                      token_slots=layout['slots'],elapsed_seconds=round(time.perf_counter()-started,2))
            state['phase']='standardize'
            z,cols,means,scales=sparse.standardize_design(x)
            del x
            base.emit('register_analysis_standardized',template=template,
                      nonconstant_features=z.shape[1],elapsed_seconds=round(time.perf_counter()-started,2))
            factory_stats=[]; heads={}; pred=np.zeros_like(pairs,dtype=np.float64)
            failed=None
            try:
                for slot in (0,1):
                    state.update(position=slot+1, phase='lasso_fit',head=None,head_count=0)
                    base.emit('register_analysis_position_start',template=template,
                              position=slot+1,alpha_ratio=ratio,stage=stage)
                    factory=ProgressFastHeadFactory(z,max_iter=args.solver_max_iter,
                        retry_multiplier=args.solver_retry_multiplier,kkt_iters=args.kkt_iters,
                        progress=state,progress_every=args.progress_every_heads,
                        warmup_ratios=warmup_ratios)
                    factory.set_ratio(ratio)
                    fitted=sparse.hier.fit_hierarchy(z,pairs[:,slot].astype(np.float64),
                        cuts,ratio,factory=factory)
                    pred[:,slot]=fitted['predicted']
                    for name,head in factory.heads.items():
                        heads[f'position_{slot+1}_{name}']=head
                    factory_stats.append({'solves':factory.unique_solves,'cache_hits':factory.cache_hits,
                                          'warmup_solves':factory.continuation_fit_calls})
                    base.emit('register_analysis_position_complete', template=template,
                              position=slot+1, heads=len(factory.heads),
                              unique_solves=factory.unique_solves,cache_hits=factory.cache_hits,
                              warmup_solves=factory.continuation_fit_calls,
                              seconds=round(time.perf_counter()-started,2))
            except sparse.SparseFitFailure as exc:
                failed={'template':template,'code':exc.code,'details':exc.diagnostics}
                failures.append(failed)
                base.emit('register_analysis_position_failure',template=template,
                          position=state['position'],head=state['head'],failure=exc.code,
                          diagnostics=exc.diagnostics)
            state['phase']='write_results'
            if failed is None:
                overall,op_scores,robust,passing,rel=operation_metrics(pairs,pred,operations)
                row={'template':template,'representation':hyper['representation'],
                    'alpha_ratio':ratio,'stage':stage,'layers':','.join(map(str,layers)),
                    'samples':len(pairs),'joint_pass_rate':overall['both_within_5pct_rate'],
                    'slot_1_pass_rate':overall['slot_1_pass_rate'],
                    'slot_2_pass_rate':overall['slot_2_pass_rate'],
                    'worst_operation_joint_pass_rate':robust['worst_operation_joint_pass_rate'],
                    'all_operations_joint_pass_rate':robust['all_operations_joint_pass_rate'],
                    'normalized_operation_drift_mean':robust['normalized_operation_drift_mean'],
                    'normalized_operation_drift_max':robust['normalized_operation_drift_max'],
                    'coefficient_count':int(sum(h['nonzero'] for h in heads.values())),
                    'full_features':layout['full_features'],'nonconstant_features':z.shape[1],
                    'seconds':round(time.perf_counter()-started,2),
                    'solver_warning_count':sum(h['solver_warning_count'] for h in heads.values()),
                    'continuation_warmup_fit_count':sum(f['warmup_solves'] for f in factory_stats),
                    'all_heads_converged':all(h['solver_converged'] and h['kkt_passed'] for h in heads.values()),
                    'per_operation':op_scores}
                rows.append(row)
                token=f'{ratio:.8g}'.replace('.','p')
                path=result_dir/'models'/template/f'alpha_{token}_stage_{stage}'
                sparse.save_heads(path,heads,cols,means,scales,layout)
                # Per-operand, per-operation predictions support checking leakage
                # and finding which pair/operation caused the signal to collapse.
                sparse.dec.write_csv(result_dir/f'register_predictions_{template}.csv',[
                    {'pair_id':i//len(settings['operations']),'operation':operations[i],
                     'a':int(pairs[i,0]),'b':int(pairs[i,1]),
                     'a_hat':float(pred[i,0]),'b_hat':float(pred[i,1]),
                     'a_pass':bool(passing[i,0]),'b_pass':bool(passing[i,1]),
                     'both_pass':bool(passing[i].all())}
                    for i in range(len(pairs))],
                    ['pair_id','operation','a','b','a_hat','b_hat','a_pass','b_pass','both_pass'])
            checkpoint()
            base.emit('register_template_result',template=template,representation=hyper['representation'],
                alpha_ratio=ratio,stage=stage,seconds=round(time.perf_counter()-started,2),
                joint_pass_rate=rows[-1]['joint_pass_rate'] if failed is None else None,
                worst_operation_pass_rate=rows[-1]['worst_operation_joint_pass_rate'] if failed is None else None,
                coefficients=rows[-1]['coefficient_count'] if failed is None else None,
                failure=failed['code'] if failed else None)
        finally:
            stop_event.set()
            heartbeat.join(timeout=2.)
    return rows


def parser():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--self-test',action='store_true')
    p.add_argument('--prepare-only',action='store_true')
    p.add_argument('--capture-only',action='store_true')
    p.add_argument('--analyze-only',action='store_true')
    p.add_argument('--resume',action='store_true')
    p.add_argument('--model',choices=tuple(sparse.cassette.SOURCE),default='qwen')
    p.add_argument('--device',choices=('auto','cuda','cpu'),default='auto')
    p.add_argument('--dtype',choices=('auto','float32','float16','bfloat16'),default='auto')
    p.add_argument('--store-dtype',choices=('float16','float32'),default='float16')
    p.add_argument('--allow-model-download',action='store_true')
    p.add_argument('--max-prompt-tokens',type=int,default=192)
    p.add_argument('--report-every',type=int,default=40)
    p.add_argument('--pair-count',type=int,default=64)
    p.add_argument('--seed',type=int,default=20261012)
    p.add_argument('--templates',default='ab,xx,r0,brackets')
    p.add_argument('--operations',default='add,sub,gt')
    p.add_argument('--representation',choices=base.KINDS,default=None)
    p.add_argument('--alpha-ratio',type=float,default=None)
    p.add_argument('--stage',type=int,default=None)
    p.add_argument('--layers',default=None)
    p.add_argument('--inherit-summary',type=Path,default=None,
        help='Read only the winning representation, ratio, stage and layers. Never copy model coefficients.')
    p.add_argument('--readout-scope',choices=('suffix','full'),default=None,
        help='suffix observes last --tail-tokens positions after operation; full observes all token states.')
    p.add_argument('--tail-tokens',type=int,default=None)
    p.add_argument('--warmup-ratios',default='0.03,0.015,0.005',
        help='Strong-to-weak LASSO alpha-ratio continuation within each NEW head; '
             'ratios not above target are skipped. Use none to disable.')
    p.add_argument('--solver-max-iter',type=int,default=10000)
    p.add_argument('--solver-retry-multiplier',type=int,default=3)
    p.add_argument('--kkt-iters',type=int,default=12)
    p.add_argument('--threads',type=int,default=2)
    p.add_argument('--wait-for-capture',action='store_true',
        help='Wait until every unique activation record is present before fitting; capture may run concurrently.')
    p.add_argument('--capture-poll-seconds',type=float,default=15.0)
    p.add_argument('--progress-every-heads',type=int,default=10,
        help='Print progress after every N completed LASSO heads; one early head is also reported.')
    p.add_argument('--heartbeat-seconds',type=float,default=30.0,
        help='Periodic status even when a single regression head takes a long time.')
    p.add_argument('--normal-priority',action='store_true')
    p.add_argument('--output-root',type=Path,default=DEFAULT_ROOT,
        help='Unique root, NEVER the original two-number experiment directory.')
    p.add_argument('--results-root',type=Path,default=None)
    return p


def main(argv=None):
    args=parser().parse_args(argv)
    if args.self_test:
        assert len(base.generate_pairs(64))==64
        assert '2000' in render('ab','add','decimal',(2000,-3))
        assert '\n' in render('xx','sub','paired',(2000,-3))
        base.emit('register_template_self_test_passed')
        return
    if args.pair_count<20 or args.pair_count%2 or (args.tail_tokens is not None and args.tail_tokens<1) or args.threads<1:
        raise ValueError('REGISTER_BAD_ARGUMENTS')
    if (args.progress_every_heads<1 or args.heartbeat_seconds<=0 or
        args.capture_poll_seconds<=0):
        raise ValueError('REGISTER_BAD_PROGRESS_ARGUMENTS')
    if args.report_every<1 or args.kkt_iters<1 or args.solver_max_iter<1 or args.solver_retry_multiplier<1:
        raise ValueError('REGISTER_BAD_SOLVER_ARGUMENTS')
    # Validate user-supplied continuation even in dry-run mode.
    continuation_schedule(args.warmup_ratios, args.alpha_ratio or 0.005)
    if args.capture_only and args.analyze_only:
        raise ValueError('REGISTER_CANNOT_CAPTURE_AND_ANALYZE_TOGETHER')
    if args.output_root.expanduser().resolve()==base.DEFAULT_ROOT.resolve() or args.output_root.expanduser().resolve()==fast.DEFAULT_FAST_OUTPUT.resolve():
        raise ValueError('REGISTER_OUTPUT_MUST_BE_SEPARATE')
    background=False if args.normal_priority else fast.set_background_priority()
    with threadpool_limits(limits=args.threads):
        settings=capture(args)
        if not (args.prepare_only or args.capture_only):
            analyze(args,settings)
    if not args.prepare_only:
        base.emit('register_template_complete',output_root=str(args.output_root.resolve()),
            background_priority=background)


if __name__=='__main__':
    try:
        main()
    except (ValueError,RuntimeError,FileNotFoundError,KeyError) as exc:
        base.emit('register_template_error',message=str(exc))
        raise SystemExit(1)
