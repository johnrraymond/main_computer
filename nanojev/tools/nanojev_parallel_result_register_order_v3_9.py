#!/usr/bin/env python3
"""NanoJev v3.9: corrected, isolated X/Y prompt-order intervention.

The shared fresh stream and replay buffer stay ORIGINAL order. Only sampled
INTERMEDIATE updates are recaptured from a balanced label-preserving X/Y prompt
ordering. RESULT/FINAL never consume reversed Qwen features directly. The
underlying v3.7 losses, optimizers, 16:3:1 phase cycle, and best selection
remain unchanged. Paired-order diagnostics use the SAME held-out pairs in both
orders, before training and at each validation. Always fork from CLEAN v3.7.
All existing v3.7 and damaged v3.8 artifacts remain untouched.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import uuid

import torch
import numpy as np

import nanojev_parallel_result_asinh_squared_v3_7 as v37
from nanojev_register_stability_v3_generate import FIRST, SECOND, number_repr, OPERATIONS, render

CUTOVER_SCHEMA = 'nanojev.register-prompt-order-isolated-guarded.v3.9.1'  # existing fork/checkpoint compatible
LEARNING_POLICY = 'v3.9.2-train-from-high-finite-errors'
ORDER_VALUE = 'intermediate-only-even-original-odd-reverse-recapture;shared-replay-original'
MARKER = 'register-order-isolated-cutover.json'
PREFLIGHT = 'paired-order-preflight.json'
# Poor reversed-order accuracy is the STARTING CONDITION of this experiment.
# The preflight protects data integrity and finite computation, not accuracy.
# Alert thresholds remain descriptive and cannot block learning.
MAX_REVERSED_RELATIVE_MSE_RATIO = 10.0
MAX_REVERSED_ABSOLUTE_XY_MSE = 5.0
MIN_REVERSED_ACCURACY_RATIO = 0.50
MIN_OPERATION_PROBE_ACCURACY = 0.98
PAIRED_PREFLIGHT_ROWS = 1024
OP_BY_NAME = {op[0]: op for op in OPERATIONS}


def reorder_registration(row, reverse):
    """Change only the first two lines, never X=A, Y=B or the operation.

    A malformed/unknown registration format must fail closed rather than
    accidentally perturb the operation or reverse noncommutative operands.
    """
    original = row['prompt']
    first = f'{FIRST} = {number_repr(int(row["a"]))}'
    second = f'{SECOND} = {number_repr(int(row["b"]))}'
    prefix = first + '\n' + second + '\n'
    if not isinstance(original, str) or not original.startswith(prefix):
        raise RuntimeError('ALTREG_UNEXPECTED_ORIGINAL_PROMPT_FORMAT')
    # Frozen Qwen suffix capture depends on the operation/Answer tail, which
    # must remain byte-for-byte identical in both variants.
    if not original[len(prefix):].startswith('operation: '):
        raise RuntimeError('ALTREG_UNEXPECTED_OPERATION_TAIL')
    if not reverse:
        return row
    swapped = dict(row)
    swapped['prompt'] = second + '\n' + first + '\n' + original[len(prefix):]
    return swapped


def swapped_prompts_for_replay(rows, reverse_mask):
    """Render exact known prompts from immutable replay labels. No target swap."""
    if len(reverse_mask) != len(rows):
        raise RuntimeError('ALTREG_BAD_MASK_LENGTH')
    prompts = []
    for row, reverse in zip(rows, reverse_mask):
        _, a, b, op, *_ = row
        if op not in OP_BY_NAME:
            raise RuntimeError('ALTREG_UNKNOWN_OPERATION:' + str(op))
        data = {'a': int(a), 'b': int(b), 'operation': op,
                'prompt': render(int(a), int(b), OP_BY_NAME[op])}
        prompts.append(reorder_registration(data, bool(reverse))['prompt'])
    return prompts


def replace_intermediate_features(rows, capture, reverse_first=False):
    """New feature tensors for half of INTERMEDIATE samples only.

    Copies the tuple, preserving every target, operation and frozen-source
    field. The original replay buffer is never mutated. Inference recapture
    happens under torch.inference_mode(); no Qwen optimizer is introduced.
    """
    selected = [i for i in range(len(rows)) if (i + int(reverse_first)) % 2 == 1]
    if not selected:
        return rows
    prompts = swapped_prompts_for_replay([rows[i] for i in selected], [True]*len(selected))
    with torch.inference_mode():
        features = capture(prompts)
    if len(features) != len(selected):
        raise RuntimeError('ALTREG_CAPTURE_COUNT_MISMATCH')
    if not bool(torch.isfinite(features).all()):
        raise RuntimeError('ALTREG_NONFINITE_CAPTURE')
    result = list(rows)
    for j, i in enumerate(selected):
        original = rows[i]
        result[i] = (features[j].detach().cpu().to(dtype=original[0].dtype).clone(), *original[1:])
    return result


def operand_pair_report(operand_decoder, source, aux, op_probe, original_rows,
                        reversed_features, device, metrics_fn=None):
    """Evaluate original/reversed on SAME labelled samples, never rebind targets."""
    original_features, truth, operations, families = original_rows
    if len(truth) != len(reversed_features):
        raise RuntimeError('ALTREG_EVAL_ALIGNMENT_MISMATCH')
    correct = np.asarray(truth, dtype=np.float64)
    reverse_rows = (np.asarray(reversed_features,dtype=np.float32),truth,operations,families)
    measure = metrics_fn or v37.operand_metrics
    old = measure(operand_decoder,aux,source,op_probe,original_rows,device)
    reverse = measure(operand_decoder,aux,source,op_probe,reverse_rows,device)
    originals, reversals, baseline, ops0, ops1 = [], [], [], [], []
    for start in range(0,len(truth),64):
        f0=torch.as_tensor(original_features[start:start+64],dtype=torch.float32,device=device)
        f1=torch.as_tensor(reversed_features[start:start+64],dtype=torch.float32,device=device)
        with torch.inference_mode():
            originals.append(v37.current_xy(operand_decoder,f0))
            reversals.append(v37.current_xy(operand_decoder,f1))
            baseline.append(v37.base.source_decode(source,f1))
            ops0.extend(op_probe(f0).argmax(1).detach().cpu().tolist())
            ops1.extend(op_probe(f1).argmax(1).detach().cpu().tolist())
    x0=np.concatenate(originals).astype(np.float64)
    x1=np.concatenate(reversals).astype(np.float64)
    f1=np.concatenate(baseline).astype(np.float64)
    both=(x0+x1)/2.0
    denom=np.maximum(np.abs(correct),1.)
    def metrics(p, compare=correct):
        rel=np.square((p-compare)/np.maximum(np.abs(compare),1.))
        relabs=np.abs(p-compare)/np.maximum(np.abs(compare),1.)
        return {'x_mse':float(rel[:,0].mean()),'y_mse':float(rel[:,1].mean()),
                'xy_mse':float(rel.mean(axis=0).sum()),
                'x_5pct':float(np.mean(relabs[:,0]<=.05)),
                'y_5pct':float(np.mean(relabs[:,1]<=.05))}
    gold_ops=[v37.base.OP_IDS[o] for o in operations]
    return {'rows':len(correct),'original':metrics(x0),
            'reversed_label_aligned':metrics(x1),
            'reversed_position_aligned':metrics(x1,correct[:,::-1]),
            'arithmetic_average_label_aligned':metrics(both),
            'reversed_frozen_source_label_aligned':metrics(f1),
            'frozen_operation_probe_accuracy_original':float(np.mean(np.asarray(ops0)==gold_ops)),
            'frozen_operation_probe_accuracy_reversed':float(np.mean(np.asarray(ops1)==gold_ops)),
            'frozen_operation_probe_accuracy_delta':float(np.mean(np.asarray(ops1)==gold_ops)-np.mean(np.asarray(ops0)==gold_ops)),
            'original_operands':old['hierarchy_refined'],
            'reversed_operands':reverse['hierarchy_refined']}


def diagnostic_capture(benchmark, manifest, tasks, args, frozen, tokenizer, model_device,
                       operand_decoder, source, aux, op_probe, device, count=PAIRED_PREFLIGHT_ROWS):
    rows=v37.base.previous.load_eval(benchmark,manifest,tasks,'val',count)
    pairs=np.asarray(rows[1],dtype=np.int64)
    ops=rows[2]
    prompts=swapped_prompts_for_replay(
        [(None,int(a),int(b),op,0,0) for (a,b),op in zip(pairs,ops)], [True]*len(pairs))
    with torch.inference_mode():
        captures=v37.base.previous.extract_batched(
            torch,frozen,tokenizer,prompts,model_device,args.max_prompt_tokens)
    flipped=captures.detach().cpu().numpy()
    return operand_pair_report(operand_decoder,source,aux,op_probe,rows,flipped,device)


def evaluate_preflight_gate(result):
    """Approve valid, finite paired data even when reversed accuracy is awful.

    High but finite regression error is the learning problem, not a reason to
    prevent training. Block only missing/malformed/nonfinite diagnostic values.
    Record quality concerns as warnings, never eligibility blockers.
    """
    fail=[]
    warn=[]
    o=result.get('original',{})
    r=result.get('reversed_label_aligned',{})
    if not isinstance(o,dict) or not isinstance(r,dict):
        return {'eligible':False,'reasons':['ALTREG_GATE_MISSING_PAIRED_METRICS'],
                'warnings':[],'policy':LEARNING_POLICY}
    values={}
    for order,part in (('original',o),('reversed',r)):
        for key in ('x_mse','y_mse','xy_mse','x_5pct','y_5pct'):
            try:
                value=float(part[key])
            except (TypeError,ValueError,KeyError):
                fail.append('ALTREG_GATE_MISSING_METRIC:'+order+'.'+key)
                continue
            values[(order,key)]=value
            if (not np.isfinite(value) or
                    (key.endswith('mse') and value<0.) or
                    (key.endswith('5pct') and not 0.<=value<=1.)):
                fail.append('ALTREG_GATE_INVALID_METRIC:'+order+'.'+key)
    for key in ('frozen_operation_probe_accuracy_original',
                'frozen_operation_probe_accuracy_reversed'):
        try:
            value=float(result[key])
        except (TypeError,ValueError,KeyError):
            fail.append('ALTREG_GATE_MISSING_METRIC:'+key)
            continue
        values[('operation',key)]=value
        if not np.isfinite(value) or not 0.<=value<=1.:
            fail.append('ALTREG_GATE_INVALID_METRIC:'+key)
    if not fail:
        om=values[('original','xy_mse')]
        rm=values[('reversed','xy_mse')]
        if rm>MAX_REVERSED_ABSOLUTE_XY_MSE and rm>MAX_REVERSED_RELATIVE_MSE_RATIO*max(om,1e-12):
            warn.append('ALTREG_WARN_REVERSED_NUMERIC_FAILURE_EXPECTED_DURING_LEARNING')
        for name in ('x_5pct','y_5pct'):
            if (values[('original',name)]>=.05 and
                    values[('reversed',name)] < MIN_REVERSED_ACCURACY_RATIO*values[('original',name)]):
                warn.append('ALTREG_WARN_REVERSED_'+name.upper()+'_COLLAPSE_EXPECTED_DURING_LEARNING')
        if values[('operation','frozen_operation_probe_accuracy_reversed')]<MIN_OPERATION_PROBE_ACCURACY:
            warn.append('ALTREG_WARN_REVERSED_OPERATION_PROBE_LOW')
    return {'eligible':not fail,'reasons':fail,'warnings':warn,
            'policy':LEARNING_POLICY,
            'original_xy_relative_mse':values.get(('original','xy_mse')),
            'reversed_xy_relative_mse':values.get(('reversed','xy_mse')),
            'thresholds':{'warning_mse_ratio_if_over_abs_floor':MAX_REVERSED_RELATIVE_MSE_RATIO,
                          'warning_reversed_mse_absolute_floor':MAX_REVERSED_ABSOLUTE_XY_MSE,
                          'warning_min_relative_within5_accuracy':MIN_REVERSED_ACCURACY_RATIO,
                          'warning_min_operation_probe_accuracy':MIN_OPERATION_PROBE_ACCURACY},
            'metric_definition':'mean squared ((prediction-target)/max(abs(target),1)), not signed-asinh MSE'}


def preflight(args, root):
    """Mandatory frozen-checkpoint paired diagnostic BEFORE any new updates."""
    ckfile=root/'latest.pt'
    state=torch.load(ckfile,map_location='cpu',weights_only=True)
    if state['config'].get('register_prompt_order')!=ORDER_VALUE:
        raise RuntimeError('ALTREG_PREFLIGHT_CHECKPOINT_MODE')
    device=torch.device(args.device)
    benchmark=Path(args.v3_root).expanduser().resolve()
    manifest,tasks=v37.base.previous.read_benchmark(benchmark)
    identity=json.loads((benchmark/'model_identity.json').read_text(encoding='utf-8'))
    source,_=v37.base.source_factory(args.source_checkpoint,identity,device)
    operand_decoder=copy.deepcopy(source).to(device)
    operand_decoder.load_state_dict(state['bottom_operand_decoder']);operand_decoder.eval()
    aux=v37.OperandAux(args.aux_width).to(device)
    aux.load_state_dict(state['bottom_operand_aux']);aux.eval()
    op_probe=v37.base.SharedOperationProbe().to(device)
    op_probe.load_state_dict(state['operation_probe']);op_probe.eval()
    import nanojev_numerate_spine_cassette_smoke as cassette
    _,frozen,tokenizer,model_device,model_info=cassette.load_source('qwen',args)
    if (model_info.get('resolved_commit')!=identity['model_info'].get('resolved_commit') or
        model_info.get('source')!=identity['model_info'].get('source') or
        str(model_info.get('dtype'))!=str(identity['model_info'].get('dtype'))):
        raise RuntimeError('ALTREG_BACKBONE_IDENTITY_DRIFT')
    frozen.eval()
    v37.base.previous.verify_capture_alignment(
        torch,frozen,tokenizer,model_device,benchmark,tasks,args.max_prompt_tokens)
    result=diagnostic_capture(benchmark,manifest,tasks,args,frozen,tokenizer,model_device,
                              operand_decoder,source,aux,op_probe,device)
    assert result['rows']==PAIRED_PREFLIGHT_ROWS
    gate=evaluate_preflight_gate(result)
    data={'safety_gate':gate,'schema':CUTOVER_SCHEMA,'checkpoint_sha256':_sha256(ckfile),
          'checkpoint_step':int(state['step']),'baseline':result,
          'training_state_unchanged':True,
          'result_and_final_replay':'original-order-only',
          'position_order_measured':True}
    v37.base.atomic_json(root/PREFLIGHT,data)
    v37.base.emit('altreg_paired_preflight',step=int(state['step']),safety_gate=gate,**result)
    if not gate['eligible']:
        raise RuntimeError('ALTREG_PREFLIGHT_INVALID_DIAGNOSTIC:' + ','.join(gate['reasons']))
    return data


def with_order_config(original_config):
    def generate(*args, **kwargs):
        cfg = original_config(*args, **kwargs)
        if 'register_prompt_order' in cfg:
            raise RuntimeError('ALTREG_CONFIG_KEY_COLLISION')
        return {**cfg, 'register_prompt_order': ORDER_VALUE}
    return generate


def _sha256(path):
    h = hashlib.sha256()
    with open(path,'rb') as f:
        for chunk in iter(lambda:f.read(1024 * 1024),b''):
            h.update(chunk)
    return h.hexdigest()


def _load_with_mode(path):
    state = torch.load(path, map_location='cpu', weights_only=True)
    if state.get('schema') != v37.SCHEMA:
        raise RuntimeError('ALTREG_REQUIRES_V3_7_CHECKPOINT')
    if not isinstance(state.get('config'), dict):
        raise RuntimeError('ALTREG_INVALID_PARENT_CONFIG')
    if 'register_prompt_order' in state['config']:
        raise RuntimeError('ALTREG_PARENT_ALREADY_ALTERNATING')
    state['config'] = {**state['config'], 'register_prompt_order': ORDER_VALUE}
    return state


def _write_checkpoint(path, state):
    path.parent.mkdir(parents=True,exist_ok=True)
    torch.save(state,path)


def create_fork(parent_root, result_root, checkpoint='latest.pt'):
    parent = Path(parent_root).expanduser().resolve()
    root = Path(result_root).expanduser().resolve()
    if (root == parent or parent in root.parents or root in parent.parents):
        raise RuntimeError('ALTREG_UNSAFE_FORK_ROOT')
    if checkpoint not in ('latest.pt','asinh_intermediary/best.pt',
                          'asinh_result/best.pt','asinh_final/best.pt'):
        raise ValueError('ALTREG_INVALID_FORK_CHECKPOINT')
    source_file = parent / checkpoint
    if not source_file.is_file():
        raise FileNotFoundError(f'ALTREG_SOURCE_CHECKPOINT_MISSING:{source_file}')
    if root.exists():
        raise RuntimeError('ALTREG_TARGET_ROOT_NOT_EMPTY')
    source_hash = _sha256(source_file)
    state = _load_with_mode(source_file)
    marker = {
        'schema': CUTOVER_SCHEMA,
        'order': ORDER_VALUE,
        'source_checkpoint': str(source_file),
        'source_sha256': source_hash,
        'fork_step': int(state['step']),
        'preserves': ['weights','optimizer_moments','stream_rng','stream_drawn',
                      'replay','phase_tick','phase_counts','regression_targets',
                      '16:3:1_phase_schedule','original_order_benchmark_validation','paired_validation','original_replay_for_result_final'],
        'validation_prompt_order': 'paired_original_and_reversed_with_original_champion_selection',
    }
    # Build the new root off to one side; publish it only after all required
    # snapshots and the manifest have been durably written.
    stage = root.with_name(root.name + '.altreg-stage-' + uuid.uuid4().hex)
    stage.mkdir(parents=True,exist_ok=False)
    try:
        _write_checkpoint(stage/'latest.pt', state)
        # Seed all existing best-checkpoint tracks so eval-best is usable even
        # before the fork trains a new champion. Avoid read/write to parent.
        for name in ('asinh_result','asinh_final','asinh_intermediary'):
            previous = parent/name/'best.pt'
            if previous.is_file():
                _write_checkpoint(stage/name/'best.pt',_load_with_mode(previous))
        (stage/MARKER).write_text(json.dumps(marker,indent=2,sort_keys=True)+'\n',encoding='utf-8')
        stage.replace(root)
    except Exception:
        shutil.rmtree(stage,ignore_errors=True)
        raise
    return marker


def parser():
    p = v37.parser()
    p.description = __doc__
    p.set_defaults(results_root=Path.home()/'NanoJev'/'runs'/'parallel_register_order_v3_9')
    p.add_argument('--preflight-only',action='store_true',help='Compute baseline paired-order diagnostics without any optimizer update.')
    p.add_argument('--fork-v3-7-root',type=Path,default=None,
                   help='Unmodified v3.7 results root to fork once; omitted for --resume or --eval-only.')
    p.add_argument('--fork-v3-7-checkpoint',default='latest.pt',
                   choices=('latest.pt','asinh_intermediary/best.pt',
                            'asinh_result/best.pt','asinh_final/best.pt'))
    return p


def self_test():
    from nanojev_register_stability_v3_generate import OPERATIONS, render
    for op in OPERATIONS:
        row={'a':17,'b':-3,'operation':op[0], 'prompt':render(17,-3,op)}
        swapped=reorder_registration(row,True)
        assert swapped['prompt'].splitlines()[:2]==[
            f'{SECOND} = {number_repr(-3)}', f'{FIRST} = {number_repr(17)}']
        assert swapped['prompt'].splitlines()[2:]==row['prompt'].splitlines()[2:]
    from nanojev_register_stability_v3_generate import OPERATIONS
    replay=[(torch.zeros(4),17,-3,OPERATIONS[0][0],17,-3)]*4
    calls=[]
    def capture(prompts):
        calls.append(prompts)
        return torch.arange(len(prompts)*4,dtype=torch.float32).reshape(-1,4)
    output=replace_intermediate_features(replay,capture)
    assert len(calls)==1 and len(calls[0])==2
    assert output[0] is replay[0] and output[2] is replay[2]
    assert output[1][1:]==replay[1][1:] and output[3][1:]==replay[3][1:]
    assert output[1] is not replay[1]
    assert [phase for phase in v37.PHASE_NAMES].count('intermediate')==16
    assert v37.PHASE_NAMES.count('result')==3 and v37.PHASE_NAMES.count('final')==1
    v37.base.emit('altreg_v39_self_test_pass',schedule='16:3:1',
                  intermediate_only=True, shared_replay_original=True,
                  paired_eval=True, target_swap=False, preflight_finite_gate=True,
                  quality_regression_diagnostic_only=True)
    return 0


def execute(args):
    if args.self_test:
        return self_test()
    if args.fork_v3_7_root is not None and (args.resume or args.eval_only):
        raise ValueError('ALTREG_FORK_AND_RESUME_EXCLUSIVE')
    if args.fork_v3_7_root is None and not (args.resume or args.preflight_only):
        raise ValueError('ALTREG_REQUIRES_FORK_OR_RESUME')
    if args.fork_v3_5_root is not None or args.fork_v3_4_root is not None or args.fork_parent_best:
        raise ValueError('ALTREG_FORK_ONLY_FROM_CLEAN_V3_7')
    if args.eval_only or args.eval_best:
        raise ValueError('ALTREG_USE_--preflight-only_OR_NORMAL_TRAINING')
    root=Path(args.results_root).expanduser().resolve()
    if args.fork_v3_7_root is not None:
        marker=create_fork(args.fork_v3_7_root,root,args.fork_v3_7_checkpoint)
        args.resume=True
        v37.base.emit('altreg_v39_forked',**marker,results_root=str(root))
    else:
        path=root/MARKER
        if not path.is_file():
            raise RuntimeError('ALTREG_RESUME_REQUIRES_V39_MARKER')
        marker=json.loads(path.read_text(encoding='utf-8'))
        if marker.get('schema')!=CUTOVER_SCHEMA or marker.get('order')!=ORDER_VALUE:
            raise RuntimeError('ALTREG_CUTOVER_MARKER_DRIFT')
    if args.preflight_only:
        preflight(args,root)
        return 0
    report_file=root/PREFLIGHT
    if not report_file.is_file():
        raise RuntimeError('ALTREG_PAIRED_PREFLIGHT_REQUIRED_RUN_--preflight-only_BEFORE_TRAINING')
    verified=json.loads(report_file.read_text(encoding='utf-8'))
    if verified.get('schema')!=CUTOVER_SCHEMA or verified.get('baseline',{}).get('rows')!=PAIRED_PREFLIGHT_ROWS:
        raise RuntimeError('ALTREG_PREFLIGHT_INVALID')
    # Re-evaluate the stored diagnostic under the learning policy. This allows
    # the user's existing v3.9.1 guarded fork, whose preflight *correctly*
    # measured enormous but finite reversed errors, to train without another
    # frozen-backbone capture or a new fork. The checkpoint SHA is still checked.
    gate=evaluate_preflight_gate(verified['baseline'])
    if not gate['eligible']:
        raise RuntimeError('ALTREG_PREFLIGHT_INVALID_DIAGNOSTIC:' + ','.join(gate['reasons']))
    if verified.get('safety_gate')!=gate:
        verified['safety_gate']=gate
        verified['learning_policy']=LEARNING_POLICY
        v37.base.atomic_json(report_file,verified)
    v37.base.emit('altreg_learning_preflight_accepted',
                  policy=LEARNING_POLICY,step=int(verified.get('checkpoint_step',marker['fork_step'])),
                  initial_original_xy_mse=gate['original_xy_relative_mse'],
                  initial_reversed_xy_mse=gate['reversed_xy_relative_mse'],
                  warnings=gate['warnings'])
    checkpoint=torch.load(root/'latest.pt',map_location='cpu',weights_only=True)
    if int(checkpoint['step'])==int(marker['fork_step']) and verified.get('checkpoint_sha256')!=_sha256(root/'latest.pt'):
        raise RuntimeError('ALTREG_PREFLIGHT_CHECKPOINT_CHANGED_BEFORE_TRAINING')
    if checkpoint['config'].get('register_prompt_order')!=ORDER_VALUE:
        raise RuntimeError('ALTREG_CHECKPOINT_MODE_DRIFT')

    # Do not change the fresh stream, replay population, RESULT, or FINAL.
    # The INTERMEDIATE update recaptures only odd-index replay samples.
    original_update=v37.update_intermediaries
    original_metrics=v37.operand_metrics
    original_extract=v37.base.previous.extract_batched
    original_config=v37.config_for
    context={}
    metrics_calls=[0]

    def capture_and_record(*capture_args,**kwargs):
        if len(capture_args)>=6:
            _,frozen,tokenizer,_prompts,model_device,max_tokens=capture_args[:6]
            context.update({'frozen':frozen,'tokenizer':tokenizer,
                            'model_device':model_device,'max_tokens':max_tokens})
        return original_extract(*capture_args,**kwargs)

    def training_capture(prompts):
        if 'frozen' not in context:
            raise RuntimeError('ALTREG_QWEN_NOT_LOADED')
        return original_extract(torch,context['frozen'],context['tokenizer'],prompts,
                                context['model_device'],context['max_tokens'])

    def alternating_intermediaries(operand_decoder,decoder_optimizer,aux,aux_optimizer,rows,device,
                                   continuous_weight=1.0):
        # Mask uses sample position within each replay batch; zero independent
        # state => deterministic across resume, including even batch sizes.
        alternate=replace_intermediate_features(rows,training_capture)
        return original_update(operand_decoder,decoder_optimizer,aux,aux_optimizer,
                               alternate,device,continuous_weight)

    def paired_metrics(operand_decoder,aux,frozen_source,op_probe,rows,device,batch_size=64):
        result=original_metrics(operand_decoder,aux,frozen_source,op_probe,rows,device,batch_size)
        if 'frozen' not in context:
            raise RuntimeError('ALTREG_PAIRED_VALIDATION_BACKBONE_NOT_LOADED')
        pairs=np.asarray(rows[1],dtype=np.int64)
        prompts=swapped_prompts_for_replay(
            [(None,int(a),int(b),op,0,0) for (a,b),op in zip(pairs,rows[2])],
            [True]*len(pairs))
        with torch.inference_mode():
            captured=training_capture(prompts)
        pairs_report=operand_pair_report(operand_decoder,frozen_source,aux,op_probe,
                                         rows,captured.detach().cpu().numpy(),device,original_metrics)
        result['paired_order']=pairs_report
        metrics_calls[0]+=1
        # During adaptation, original-order accuracy may deteriorate. This is
        # observed but NOT treated as a terminal condition. v3.7's finite-loss
        # and finite-gradient checks remain active in update_intermediaries.
        old_xy=float(pairs_report['original']['xy_mse'])
        reverse_xy=float(pairs_report['reversed_label_aligned']['xy_mse'])
        if not (np.isfinite(old_xy) and np.isfinite(reverse_xy)):
            raise FloatingPointError('ALTREG_NONFINITE_VALIDATION')
        baseline_xy=float(verified['baseline']['original']['xy_mse'])
        base_reversed=float(verified['baseline']['reversed_label_aligned']['xy_mse'])
        retention_degraded=old_xy>3.0*max(baseline_xy,1.e-8) and old_xy>1.0
        v37.base.emit('altreg_paired_validation',pass_number=metrics_calls[0],
                      retention_degraded=retention_degraded,
                      original_xy_over_baseline=old_xy/max(baseline_xy,1.e-12),
                      reversed_xy_over_baseline=reverse_xy/max(base_reversed,1.e-12),
                      **{k:v for k,v in pairs_report.items() if k not in ('original_operands','reversed_operands')})
        return result

    v37.update_intermediaries=alternating_intermediaries
    v37.operand_metrics=paired_metrics
    v37.base.previous.extract_batched=capture_and_record
    v37.config_for=with_order_config(original_config)
    try:
        return v37.execute(args)
    finally:
        v37.update_intermediaries=original_update
        v37.operand_metrics=original_metrics
        v37.base.previous.extract_batched=original_extract
        v37.config_for=original_config


if __name__=='__main__':
    raise SystemExit(execute(parser().parse_args()))
