#!/usr/bin/env python3
"""Qwen Stage1 -> dual-real/complex numerical boundary CUTOVER (v2).

Requires the existing, *installed* tools.nanojev_qwen_problem_answer_stage1
from the user's successful prior run; it does not replace or monkeypatch it.

Sources: stage1 best.pt + original Stage1 text-checkpoint best.pt.
Outputs: separate versioned Qwen LoRA/probe/numeric-head checkpoint + contract.
No LM head, HybridLink recursion, final math head or automatic promotion.
"""
from __future__ import annotations
import argparse
import ast
import hashlib
import json
from pathlib import Path
import random

from tools import nanojev_numeric_complex_contract as nc
from tools import nanojev_hybridlink_rung_reduction as base

SCHEMA='nanojev.qwen.numeric-dual-cutover.v2'
PARENT_SCHEMA='nanojev.qwen.problem-answer-vector-stage1.v1'


def stage1():
    try:
        from tools import nanojev_qwen_problem_answer_stage1 as s
    except ImportError as e:
        raise RuntimeError('NUMERIC_CUTOVER_REQUIRES_INSTALLED_STAGE1_SCRIPT') from e
    if s.SCHEMA!=PARENT_SCHEMA:raise RuntimeError('NUMERIC_CUTOVER_STAGE1_SCHEMA_DRIFT')
    return s


def file_hash(path):
    h=hashlib.sha256()
    with open(path,'rb') as f:
        for chunk in iter(lambda:f.read(1024*1024),b''):h.update(chunk)
    return h.hexdigest()


def load_parent(args):
    """Fail closed on mismatched or unverified stage1 lineage."""
    import torch
    p=Path(args.stage1_checkpoint)
    if not p.is_file():raise ValueError('NUMERIC_STAGE1_CHECKPOINT_MISSING')
    cpath=p.parent/'run_config.json'
    if not cpath.is_file():raise RuntimeError('NUMERIC_STAGE1_CONFIG_MISSING')
    cfg=json.loads(cpath.read_text(encoding='utf8'))
    if cfg.get('schema')!=PARENT_SCHEMA or cfg.get('stage')!='train-qwen':
        raise RuntimeError('NUMERIC_STAGE1_CONFIG_WRONG_FAMILY')
    if not Path(args.text_checkpoint).is_file():raise ValueError('NUMERIC_TEXT_CHECKPOINT_MISSING')
    if cfg.get('text_parent_sha256')!=file_hash(args.text_checkpoint):
        raise RuntimeError('NUMERIC_STAGE1_TEXT_PARENT_MISMATCH')
    ck=torch.load(p,weights_only=True,map_location='cpu')
    if ck.get('schema')!=PARENT_SCHEMA or ck.get('config_hash')!=base.digest(cfg):
        raise RuntimeError('NUMERIC_STAGE1_CHECKPOINT_DRIFT')
    if not isinstance(ck.get('lora'),dict) or not isinstance(ck.get('probe'),dict):
        raise RuntimeError('NUMERIC_STAGE1_MISSING_WEIGHTS')
    return cfg,ck


def _integer(node):
    if isinstance(node,ast.Constant) and type(node.value) is int:return node.value
    if isinstance(node,ast.UnaryOp) and isinstance(node.op,(ast.UAdd,ast.USub)):
        n=_integer(node.operand)
        return n if isinstance(node.op,ast.UAdd) else -n
    raise ValueError('NUMERIC_EXPRESSION_NOT_INTEGER')


def _next_sum(node):
    """Find the next genuine leftmost reducible addition, never eval()."""
    if not isinstance(node,ast.BinOp) or not isinstance(node.op,ast.Add):
        raise ValueError('NUMERIC_ONLY_ADDITION_SUPPORTED')
    try:return _integer(node.left)+_integer(node.right)
    except ValueError:
        for child in (node.left,node.right):
            if isinstance(child,ast.BinOp):return _next_sum(child)
        raise ValueError('NUMERIC_MALFORMED_REDUCTION')


def numeric_label(problem):
    """Gold value of ONE next numerical reduction (not entire unfinished sum)."""
    if problem.startswith('ANSWER: '):return _integer(ast.parse(problem[8:],mode='eval').body)
    if not problem.startswith('PROBLEM: '):raise ValueError('NUMERIC_UNKNOWN_STATE_TYPE')
    expr=ast.parse(problem[9:],mode='eval').body
    return _next_sum(expr)


def _rows(s,meta,split):
    d=s.make_deck(meta['seed'],meta['train_count'],meta['dev_count'],meta['ood_count'],meta['test_count'])
    if s.deck_hash(d)!=meta['deck_sha256']:
        raise RuntimeError('NUMERIC_STAGE1_DATASET_DRIFT')
    return d[split]


def _load_qwen(s,args,parent_config,parent_ck):
    from tools import nanojev_qwen_zero_line_backbone as zero
    model,tok,sources,frozen,dirs,parent=s._load_model(args)
    if sources!=parent_config['source_sha256'] or frozen!=parent_config['frozen_sha256']:
        raise RuntimeError('NUMERIC_STAGE1_BACKBONE_SOURCE_DRIFT')
    if file_hash(args.text_checkpoint)!=parent_config['text_parent_sha256']:
        raise RuntimeError('NUMERIC_STAGE1_TEXT_PARENT_DRIFT')
    model.backbone.requires_grad_(False)
    model.head.requires_grad_(False)
    zero.restore_lora(model.backbone,parent_ck['lora'])
    probe=s._heads(int(model.backbone.config.hidden_size),args.device)
    probe.load_state_dict(parent_ck['probe'],strict=True)
    return model,tok,probe,sources,frozen,dirs


def _evaluate(s,bb,tok,probe,numeric,rows,teacher,device,encode_batch,max_tokens,scale):
    import torch
    probe.eval();numeric.eval();bb.eval()
    trans=s._evaluation(bb,tok,probe,rows,teacher,device,encode_batch,max_tokens)
    sums=[]
    with torch.no_grad():
        for i in range(0,len(rows),encode_batch):
            part=rows[i:i+encode_batch]
            inp=s._encode(bb,tok,[r['source'] for r in part],device,encode_batch,max_tokens)
            out=numeric(inp,scale)
            sums.append((nc.diagnostics(out,[numeric_label(r['source']) for r in part],scale),len(part)))
    keys=('direct_code_mae','complex_code_mae','agreement_code_mae',
          'direct_code_within_0_1','complex_code_within_0_1')
    agg={k:round(sum(t[k]*n for t,n in sums)/len(rows),6) for k in keys}
    agg['count']=len(rows)
    return {**{k:v for k,v in trans.items() if k!='errors'},'numeric':agg,
            'transition_errors':trans['errors']}


def preview(args):
    s=stage1()
    meta,ck=load_parent(args)
    counts={}
    for split in ('train','dev','ood','test'):
        rows=_rows(s,meta,split)
        nums=[numeric_label(r['source']) for r in rows]
        counts[split]={'transitions':len(rows),'answers':sum(r['action']=='ANSWER' for r in rows),
                       'numerical_examples':len(nums),'zero':sum(n==0 for n in nums),
                       'unity':sum(n==1 for n in nums),'negative':sum(n<0 for n in nums),
                       'max_absolute':max(abs(n) for n in nums)}
    result={'schema':SCHEMA,'model_loaded':False,'cutover_parent':str(args.stage1_checkpoint),
            'parent_sha256':file_hash(args.stage1_checkpoint),'counts':counts,
            'unit_circle_represents_one':True,'separate_real_complex_readouts':True,
            'lm_head_called':False,'hybridlink_running':False,'run_config_verified':True}
    print('NUMERIC_CUTOVER_PREVIEW='+json.dumps(result,sort_keys=True),flush=True)
    return result


def _config(args,parent_cfg,parent_hash,sources,frozen):
    return {'schema':SCHEMA,'stage':'train-dual-readouts','stage1_parent_sha256':parent_hash,
            'stage1_run_config_hash':base.digest(parent_cfg),
            'source_sha256':sources,'frozen_sha256':frozen,
            'scale':args.scale,'steps':args.steps,'lr':args.lr,'numeric_lr':args.numeric_lr,
            'batch_size':args.batch_size,'encoder_batch':args.encoder_batch,
            'max_tokens':args.max_tokens,'eval_every':args.eval_every,'seed':args.seed,
            'stage1_data_sha256':parent_cfg['deck_sha256'],
            'selection':'weighted DEV control / teacher-target retrieval / direct and complex code accuracy',
            'contract_schema':nc.SCHEMA,'lm_head_used':False,'final_math_head':False,
            'hybridlink_running':False,'test_evaluated':False}


def _output_safe(output, protected):
    path=Path(output).resolve()
    for raw in protected:
        other=Path(raw).resolve()
        if path==other or path in other.parents or other in path.parents:
            raise RuntimeError('NUMERIC_OUTPUT_OVERLAPS_SOURCE')
    if path.exists() and any(path.iterdir()):
        raise RuntimeError('NUMERIC_REFUSES_NONEMPTY_OUTPUT')


def train(args):
    import torch
    from tools import nanojev_qwen_zero_line_backbone as zero
    s=stage1()
    parent_cfg,parent=load_parent(args)
    if args.steps<1 or args.eval_every<1 or args.batch_size<2 or args.encoder_batch<1 or args.lr<=0 or args.numeric_lr<=0:
        raise ValueError('NUMERIC_INVALID_TRAIN_ARGUMENTS')
    _output_safe(args.output,[Path(args.stage1_checkpoint).parent,Path(args.text_checkpoint).parent])
    model,tok,probe,sources,frozen,dirs=_load_qwen(s,args,parent_cfg,parent)
    _output_safe(args.output,(*dirs,Path(args.stage1_checkpoint).parent,Path(args.text_checkpoint).parent))
    bb=model.backbone
    parent_lora=zero.lora_state(bb)
    teacher_lora={k:v.clone() for k,v in parent_lora.items()}
    # Teacher embeddings are original text-Qwen, matching the Stage1 target protocol.
    old_cfg=json.loads((Path(args.text_checkpoint).parent/'run_config.json').read_text())
    old=torch.load(args.text_checkpoint,weights_only=True,map_location='cpu')
    if old.get('schema')!=old_cfg.get('schema') or old.get('config_hash')!=base.digest(old_cfg):
        raise RuntimeError('NUMERIC_TEXT_PARENT_CONFIG_DRIFT')
    zero.restore_lora(bb,old['lora'])
    deck={split:_rows(s,parent_cfg,split) for split in ('train','dev')}
    print('NUMERIC_CUTOVER_TEACHER='+json.dumps({'train':len(deck['train']),
          'dev':len(deck['dev']),'backbone_lm_head_used':False},sort_keys=True),flush=True)
    teacher={}
    for split,rows in deck.items():
        teacher[split]=s._encode(bb,tok,[r['target'] for r in rows],args.device,args.encoder_batch,args.max_tokens).detach().cpu()
    zero.restore_lora(bb,teacher_lora)
    params=[p for _,p in zero.lora_named_parameters(bb)]
    if not params:raise RuntimeError('NUMERIC_BACKBONE_LORA_MISSING')
    for p in params:p.requires_grad_(True)
    bb.eval();model.head.requires_grad_(False)
    torch.manual_seed(args.seed)
    numeric=nc._numeric_head(int(bb.config.hidden_size),args.device)
    optim=torch.optim.AdamW([{'params':params,'lr':args.lr},
                             {'params':probe.parameters(),'lr':args.lr},
                             {'params':numeric.parameters(),'lr':args.numeric_lr}],weight_decay=0)
    cfg=_config(args,parent_cfg,file_hash(args.stage1_checkpoint),sources,frozen)
    out=Path(args.output);out.mkdir(parents=True,exist_ok=True)
    base._atomic_json(out/'run_config.json',cfg)
    rng=random.Random(args.seed);best=-1e20;beststep=None
    for step in range(1,args.steps+1):
        ind=rng.sample(range(len(deck['train'])),min(args.batch_size,len(deck['train'])))
        rows=[deck['train'][i] for i in ind]
        vec=s._encode(bb,tok,[r['source'] for r in rows],args.device,args.encoder_batch,args.max_tokens,grad=True)
        actions=torch.tensor([s.ACTIONS.index(r['action']) for r in rows],device=args.device)
        ploss,_=s._loss(probe,vec,teacher['train'][ind].to(args.device),actions,[r['target'] for r in rows])
        nloss,nparts=nc.loss(numeric(vec,args.scale),[numeric_label(r['source']) for r in rows],args.scale)
        # Terminal ANSWER states are Qwen final-layer outputs too. Teach both
        # boundaries, not merely the source problem's next-reduction readout.
        terminal=[r for r in rows if r['action']=='ANSWER']
        if terminal:
            answered=s._encode(bb,tok,[r['target'] for r in terminal],args.device,
                               args.encoder_batch,args.max_tokens,grad=True)
            terminal_loss,_=nc.loss(numeric(answered,args.scale),
                                    [numeric_label(r['target']) for r in terminal],args.scale)
            nloss=nloss+.5*terminal_loss
        total=ploss+args.numeric_weight*nloss
        if not torch.isfinite(total):raise RuntimeError('NUMERIC_NONFINITE_TRAIN_LOSS')
        optim.zero_grad(set_to_none=True);total.backward()
        torch.nn.utils.clip_grad_norm_([*params,*probe.parameters(),*numeric.parameters()],1.)
        optim.step()
        if step%args.eval_every and step!=args.steps:continue
        metrics=_evaluate(s,bb,tok,probe,numeric,deck['dev'],teacher['dev'].to(args.device),
                          args.device,args.encoder_batch,args.max_tokens,args.scale)
        n=metrics['numeric']
        score=(.4*metrics['payload_accuracy']+.2*metrics['control_accuracy']+
               .2*n['direct_code_within_0_1']+.2*n['complex_code_within_0_1'])
        if score>best:
            best=score;beststep=step
            base._atomic_torch(out/'best.pt',{'schema':SCHEMA,'status':'NUMERIC_CUTOVER_UNCERTIFIED',
                'config_hash':base.digest(cfg),'step':step,'parent_sha256':cfg['stage1_parent_sha256'],
                'lora':zero.lora_state(bb),
                'probe':{k:v.detach().cpu().clone() for k,v in probe.state_dict().items()},
                'numeric':{k:v.detach().cpu().clone() for k,v in numeric.state_dict().items()},
                'best_dev_score':score})
        print('NUMERIC_CUTOVER_PROGRESS='+json.dumps({'step':step,
             'train_transition_loss':round(float(ploss.detach()),6),
             'train_numeric_loss':round(float(nloss.detach()),6),'train_numeric_parts':nparts,
             'dev_control_accuracy':metrics['control_accuracy'],
             'dev_payload_accuracy':metrics['payload_accuracy'],
             'dev_direct_code_mae':n['direct_code_mae'],
             'dev_complex_code_mae':n['complex_code_mae'],
             'dev_readout_agreement_mae':n['agreement_code_mae'],
             'best_step':beststep,'score':round(score,6),'lm_head_used':False},sort_keys=True),flush=True)
    updated=zero.lora_state(bb)
    if not any(not torch.equal(parent_lora[k],updated[k]) for k in updated):
        raise RuntimeError('NUMERIC_BACKBONE_DID_NOT_UPDATE')
    if zero.frozen_backbone_sha256(bb)!=frozen:raise RuntimeError('NUMERIC_QWEN_FROZEN_WEIGHTS_CHANGED')
    result={'schema':SCHEMA,'status':'TRAINED_UNCERTIFIED','best_step':beststep,
            'best_score':best,'parent_checkpoint_sha256':cfg['stage1_parent_sha256'],
            'new_checkpoint':str(out/'best.pt'),'original_checkpoint_unchanged':file_hash(args.stage1_checkpoint)==cfg['stage1_parent_sha256'],
            'lm_head_used':False,'hybridlink_running':False,'final_math_head':False}
    base._atomic_json(out/'result.json',result)
    print('NUMERIC_CUTOVER_TRAIN_RESULT='+json.dumps(result,sort_keys=True),flush=True)
    return result


def probe(args):
    import torch
    from tools import nanojev_qwen_zero_line_backbone as zero
    s=stage1()
    if not args.checkpoint:raise ValueError('NUMERIC_CUTOVER_CHECKPOINT_REQUIRED')
    cfgpath=Path(args.checkpoint).parent/'run_config.json'
    if not cfgpath.is_file():raise RuntimeError('NUMERIC_CUTOVER_CONFIG_MISSING')
    cfg=json.loads(cfgpath.read_text())
    if cfg.get('schema')!=SCHEMA or cfg.get('stage')!='train-dual-readouts':
        raise RuntimeError('NUMERIC_CUTOVER_CONFIG_WRONG_FAMILY')
    if args.split=='test' and (Path(args.checkpoint).parent/'test_consumed.json').exists():
        raise RuntimeError('NUMERIC_TEST_ALREADY_CONSUMED')
    parent_cfg,parent=load_parent(args)
    if cfg['stage1_parent_sha256']!=file_hash(args.stage1_checkpoint):raise RuntimeError('NUMERIC_PARENT_CHANGED')
    if cfg['stage1_run_config_hash']!=base.digest(parent_cfg):raise RuntimeError('NUMERIC_PARENT_CONFIG_CHANGED')
    out=Path(args.output)
    _output_safe(out,[Path(args.stage1_checkpoint).parent,Path(args.text_checkpoint).parent,Path(args.checkpoint).parent])
    model,tok,control,sources,frozen,dirs=_load_qwen(s,args,parent_cfg,parent)
    _output_safe(out,(*dirs,Path(args.stage1_checkpoint).parent,Path(args.text_checkpoint).parent,Path(args.checkpoint).parent))
    if sources!=cfg['source_sha256'] or frozen!=cfg['frozen_sha256']:
        raise RuntimeError('NUMERIC_SOURCE_DRIFT')
    check=torch.load(args.checkpoint,weights_only=True,map_location='cpu')
    if check.get('schema')!=SCHEMA or check.get('config_hash')!=base.digest(cfg):
        raise RuntimeError('NUMERIC_CUTOVER_CHECKPOINT_DRIFT')
    bb=model.backbone
    rows=_rows(s,parent_cfg,args.split)
    # Same teacher as train: original text-Qwen target representations.
    text=torch.load(args.text_checkpoint,weights_only=True,map_location='cpu')
    baseline=zero.lora_state(bb)
    zero.restore_lora(bb,text['lora'])
    with torch.no_grad():
        teacher=s._encode(bb,tok,[r['target'] for r in rows],args.device,cfg['encoder_batch'],cfg['max_tokens']).detach()
    zero.restore_lora(bb,check['lora'])
    control.load_state_dict(check['probe'],strict=True)
    number=nc._numeric_head(int(bb.config.hidden_size),args.device)
    number.load_state_dict(check['numeric'],strict=True)
    metrics=_evaluate(s,bb,tok,control,number,rows,teacher,args.device,cfg['encoder_batch'],cfg['max_tokens'],cfg['scale'])
    zero.restore_lora(bb,baseline)
    result={'schema':SCHEMA,'status':'PROBE_UNCERTIFIED','split':args.split,
            'count':len(rows),'checkpoint_sha256':file_hash(args.checkpoint),
            'stage1_parent_sha256':cfg['stage1_parent_sha256'],'metrics':metrics,
            'headless':True,'final_math_head':False,'recursion_implemented':False}
    out.mkdir(parents=True,exist_ok=True)
    base._atomic_json(out/'result.json',result)
    if args.split=='test':
        base._atomic_json(Path(args.checkpoint).parent/'test_consumed.json',{
            'checkpoint_sha256':file_hash(args.checkpoint),'result_dir':str(out)})
    print('NUMERIC_CUTOVER_PROBE_RESULT='+json.dumps({k:v for k,v in result.items() if k!='metrics'}|
       {'metrics':{k:v for k,v in metrics.items() if k!='transition_errors'}},sort_keys=True),flush=True)
    return result


def inspect(args):
    """Inspect real/complex predictions without gold arithmetic or LM logits."""
    import torch
    from tools import nanojev_qwen_zero_line_backbone as zero
    if not args.checkpoint or not args.state or not args.state.strip():
        raise ValueError('NUMERIC_INSPECT_NEEDS_CHECKPOINT_AND_STATE')
    s=stage1();parent_cfg,parent=load_parent(args)
    p=Path(args.checkpoint);cfg=json.loads((p.parent/'run_config.json').read_text())
    if cfg.get('schema')!=SCHEMA or cfg.get('stage')!='train-dual-readouts':
        raise RuntimeError('NUMERIC_INSPECT_CONFIG_WRONG_FAMILY')
    if cfg['stage1_parent_sha256']!=file_hash(args.stage1_checkpoint):
        raise RuntimeError('NUMERIC_INSPECT_PARENT_DRIFT')
    state=torch.load(p,map_location='cpu',weights_only=True)
    if state.get('schema')!=SCHEMA or state.get('config_hash')!=base.digest(cfg):
        raise RuntimeError('NUMERIC_INSPECT_CHECKPOINT_DRIFT')
    model,tok,probe,sources,frozen,dirs=_load_qwen(s,args,parent_cfg,parent)
    if sources!=cfg['source_sha256'] or frozen!=cfg['frozen_sha256']:
        raise RuntimeError('NUMERIC_INSPECT_BACKBONE_DRIFT')
    zero.restore_lora(model.backbone,state['lora']);probe.load_state_dict(state['probe'])
    numeric=nc._numeric_head(int(model.backbone.config.hidden_size),args.device)
    numeric.load_state_dict(state['numeric'])
    model.backbone.eval();probe.eval();numeric.eval()
    with torch.no_grad():
        h=s._encode(model.backbone,tok,[args.state],args.device,1,cfg['max_tokens'])
        ctrl,_=probe(h)
        result=numeric(h,cfg['scale'])
        control=s.ACTIONS[int(ctrl[0].argmax().item())]
        report={'schema':SCHEMA,'state':args.state,'control_probe':control,
                'direct_number':float(result['direct_number'][0]),
                'complex_number':float(result['complex_number'][0]),
                'a':float(result['a'][0]),'b':float(result['b'][0]),
                'log_radius':float(result['complex_rho'][0]),
                'delta_log_radius':float(result['delta_rho'][0]),
                'phase_radians':float(result['phase'][0]),
                'unit_circle_radius_error':float(abs(result['complex_rho'][0])),
                'lm_head_called':False,'oracle_inference':False,
                'final_math_head':False,'recursion_implemented':False}
    print('NUMERIC_CUTOVER_INSPECT='+json.dumps(report,sort_keys=True),flush=True)
    return report


def handoff(args):
    """Write a versioned, consumable interface contract for Stage2.

    This does not rewire or activate the existing HybridLink runtime.
    """
    import torch
    ck=Path(args.checkpoint)
    if not ck.is_file():raise ValueError('NUMERIC_CUTOVER_CHECKPOINT_REQUIRED')
    c=json.loads((ck.parent/'run_config.json').read_text())
    b=torch.load(ck,map_location='cpu',weights_only=True)
    if c.get('schema')!=SCHEMA or b.get('config_hash')!=base.digest(c):
        raise RuntimeError('NUMERIC_HANDOFF_NOT_VERIFIED')
    target=Path(args.output)
    if target.exists() and any(target.iterdir()):raise RuntimeError('NUMERIC_HANDOFF_NONEMPTY_OUTPUT')
    target.mkdir(parents=True,exist_ok=True)
    payload={'schema':'nanojev.hybridlink.numeric-handoff.v1','numerical_contract':nc.SCHEMA,
             'checkpoint':str(ck.resolve()),'checkpoint_sha256':file_hash(ck),
             'parent_sha256':c['stage1_parent_sha256'],
             'feature_names':['direct_rho','complex_rho','a','b','delta_rho'],
             'feature_dtype':'float32','geometry_dtype':'float64',
             'value_one_unit_circle':True,'scale':c['scale'],
             'status':'PREPARED_NOT_ACTIVATED','requires_hybridlink_stage2':True,
             'requires_answer_detector':True,'final_math_head':False}
    base._atomic_json(target/'numeric_handoff.json',payload)
    print('NUMERIC_CUTOVER_HANDOFF='+json.dumps(payload,sort_keys=True),flush=True)
    return payload


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--stage',choices=('preview','train','probe','inspect','handoff'),default='preview')
    p.add_argument('--text-checkpoint',required=True)
    p.add_argument('--stage1-checkpoint',required=True)
    p.add_argument('--checkpoint')
    p.add_argument('--state',help='Input state for headless inspect, e.g. PROBLEM: 6+6')
    p.add_argument('--output',default=str(Path.home()/'NanoJev'/'runs'/'qwen_numeric_dual_cutover_v2'))
    p.add_argument('--device',choices=('cpu','cuda'),default='cuda')
    p.add_argument('--steps',type=int,default=1200)
    p.add_argument('--eval-every',type=int,default=100)
    p.add_argument('--batch-size',type=int,default=12)
    p.add_argument('--encoder-batch',type=int,default=12)
    p.add_argument('--max-tokens',type=int,default=192)
    p.add_argument('--lr',type=float,default=0.00002)
    p.add_argument('--numeric-lr',type=float,default=0.0002)
    p.add_argument('--numeric-weight',type=float,default=0.5)
    p.add_argument('--scale',type=float,default=64.)
    p.add_argument('--seed',type=int,default=42257)
    p.add_argument('--split',choices=('dev','ood','test'),default='dev')
    p.add_argument('--baseline');p.add_argument('--backbone');p.add_argument('--activation');p.add_argument('--format')
    p.add_argument('--allow-model-download',action='store_true')
    args=p.parse_args(argv)
    if args.scale<=0 or not args.scale<1e16:p.error('NUMERIC_BAD_SCALE')
    if args.numeric_weight<=0:p.error('NUMERIC_BAD_NUMERIC_WEIGHT')
    if args.stage in ('probe','inspect','handoff') and not args.checkpoint:p.error('NUMERIC_CHECKPOINT_REQUIRED')
    if args.stage=='preview':preview(args)
    elif args.stage=='train':train(args)
    elif args.stage=='probe':probe(args)
    elif args.stage=='inspect':inspect(args)
    else:handoff(args)

if __name__=='__main__':main()
