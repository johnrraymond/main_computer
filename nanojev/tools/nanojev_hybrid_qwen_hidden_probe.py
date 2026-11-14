#!/usr/bin/env python3
"""Read-only Qwen hidden-state probe for frozen HybridLink operand corrections.

Frozen: certified Head 1, Head 2, Qwen backbone (and optional existing LoRA).
Trained: a small diagnostic classifier on FINAL or selected intermediate Qwen
hidden states; never on raw operand bits or arithmetic targets.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

import torch
from torch import nn
from torch.nn import functional as F

from tools import nanojev_hybrid_emp_backbone_zero_line as prior
from tools import nanojev_hybrid_emp_backbone_operand_consumption as consume
from tools import nanojev_hybrid_emp_join as join
from tools import nanojev_hybrid_emp_operand_stage as operand

SCHEMA = 'nanojev.hybrid-qwen-hidden-operand-probe.v1'
ROOT = Path.home() / 'NanoJev' / 'runs' / 'qwen3_06b_hybrid_hidden_operand_probe_v1'
SOURCES = ('base', 'consumption-last', 'consumption-best')
MODES = ('intact', 'zero', 'wrong')


def load_frozen_stack(*, device, head1_dir, operand_dir, consumption_dir,
                      source, model_loader=None, head1_override=None, allow_download=False):
    if source not in SOURCES:
        raise ValueError('HIDDEN_PROBE_BAD_SOURCE')
    backbone, tokenizer, embedding, head1, sender, identity, hashes = prior.load_certified_stack(
        device=device, head1_dir=head1_dir, operand_dir=operand_dir,
        allow_download=allow_download, model_loader=model_loader, head1_override=head1_override)
    evidence = {'source': source, 'identity': identity, 'operand_hashes': hashes}
    if source != 'base':
        root = Path(consumption_dir)
        config = json.loads((root / 'config.json').read_text('utf8'))
        if (config.get('schema') != consume.SCHEMA or config.get('identity') != identity or
            config.get('operand_hashes') != hashes):
            raise RuntimeError('HIDDEN_PROBE_CONSUMPTION_PROVENANCE_DRIFT')
        rank, alpha = int(config['rank']), float(config['alpha'])
        prior.lora_tools.install_lora(backbone,rank,alpha,
            required_layers=getattr(backbone.config,'num_hidden_layers',None))
        checkpoint_name = 'last.pt' if source.endswith('last') else 'best.pt'
        checkpoint = torch.load(root/checkpoint_name,map_location='cpu',weights_only=True)
        if checkpoint.get('schema') != consume.SCHEMA:
            raise RuntimeError('HIDDEN_PROBE_CONSUMPTION_CHECKPOINT_DRIFT')
        prior.lora_tools.restore_lora(backbone,checkpoint['lora'])
        evidence.update({'consumption_config_sha256':prior.digest(root/'config.json'),
                         'consumption_checkpoint_sha256':prior.digest(root/checkpoint_name),
                         'consumption_step':checkpoint['step'],
                         'consumption_rank':rank,'consumption_alpha':alpha})
    backbone.eval().requires_grad_(False)
    sender.eval().requires_grad_(False)
    if any(p.requires_grad for p in backbone.parameters()):
        raise RuntimeError('HIDDEN_PROBE_BACKBONE_NOT_FROZEN')
    return backbone,tokenizer,embedding,head1,sender,evidence


def hidden_for_vectors(backbone, vectors, ids, layer):
    """Read the same causal suffix state used by the native 33-class output."""
    outputs=backbone(inputs_embeds=vectors.unsqueeze(0),
                     attention_mask=torch.ones((1,len(vectors)),device=vectors.device,dtype=torch.long),
                     use_cache=False,return_dict=True,output_hidden_states=True)
    states=getattr(outputs,'hidden_states',None)
    if states is None or not (-len(states)<=layer<len(states)):
        raise RuntimeError('HIDDEN_PROBE_HIDDEN_STATES_UNAVAILABLE')
    vector=states[layer][0,-1].detach().float()
    if vector.ndim!=1 or not bool(torch.isfinite(vector).all()):
        raise RuntimeError('HIDDEN_PROBE_INVALID_HIDDEN_STATE')
    native=outputs.logits[0,-1].index_select(0,ids).argmax().item()
    return vector.cpu(),int(native)


def extract_split(backbone,tokenizer,embedding,head1,sender,cards,device,ids,*,
                  layer=-1,styles=(0,),modes=('intact',),split='train',progress_every=50):
    """No gradients and no raw operand code delivered to the Qwen probe."""
    if not cards or not set(modes).issubset(MODES):
        raise RuntimeError('HIDDEN_PROBE_BAD_EXTRACTION')
    if any(p.requires_grad for p in backbone.parameters()):
        raise RuntimeError('HIDDEN_PROBE_BACKBONE_NOT_FROZEN')
    features={k:[] for k in modes};native={k:[] for k in modes};labels=[]
    with torch.inference_mode():
        for idx,card in enumerate(cards,1):
            for style in styles:
                ex=join.extract(card,style,tokenizer,head1,embedding,device,7)
                if ex['values']!=[card.a,card.b]:
                    raise RuntimeError('HIDDEN_PROBE_ALIGNMENT_DRIFT')
                ex['bits']=operand.bits(ex['values'],device)
                labels.append([card.a+16,card.b+16])
                for mode in modes:
                    states=[];preds=[]
                    for suffix in consume.SUFFIXES:
                        vectors=prior.sanitize_numerical_embeddings(
                            ex,sender,tokenizer,embedding,mode=mode,suffix_text=suffix)
                        hidden,pred=hidden_for_vectors(backbone,vectors,ids,layer)
                        states.append(hidden);preds.append(pred)
                    features[mode].append(torch.stack(states));native[mode].append(preds)
            if progress_every and (idx%progress_every==0 or idx==len(cards)):
                print('HYBRID_HIDDEN_EXTRACT='+json.dumps({'split':split,'cards':idx,
                    'total_cards':len(cards),'modes':list(modes)},sort_keys=True),flush=True)
    return {'features':{k:torch.stack(v) for k,v in features.items()},
            'native':{k:torch.tensor(v,dtype=torch.long) for k,v in native.items()},
            'labels':torch.tensor(labels,dtype=torch.long)}


class Readout(nn.Module):
    def __init__(self,hidden,kind='linear'):
        super().__init__()
        if kind not in ('linear','mlp'):
            raise ValueError('HIDDEN_PROBE_BAD_PROBE_KIND')
        self.net=(nn.Linear(hidden,33) if kind=='linear' else nn.Sequential(
            nn.Linear(hidden,128),nn.GELU(),nn.Linear(128,33)))
    def forward(self,x):return self.net(x)


def normalization(features):
    # Computed from intact TRAIN only; no DEV or TEST normalization leakage.
    train=features.reshape(-1,features.shape[-1]).float()
    mean=train.mean(0);std=train.std(0,unbiased=False).clamp_min(.01)
    return mean,std


def logits_and_metrics(model,features,labels,mean,std,*,native=None):
    n=labels.shape[0]
    with torch.no_grad():
        logits=model((features.float()-mean)/std)
        guess=logits.argmax(-1)
        hits=guess.eq(labels)
        value_hits={str(v-16):{'correct':int(hits[labels==v].sum()),
                               'total':int((labels==v).sum())} for v in range(33)}
        for v in value_hits.values():v['accuracy']=v['correct']/v['total'] if v['total'] else 0.
        result={'pairs':n,'pair_exact':float(hits.all(-1).float().mean()),
                'operand_exact':float(hits.float().mean()),
                'role_a_exact':float(hits[:,0].float().mean()),
                'role_b_exact':float(hits[:,1].float().mean()),
                'cross_entropy':float(F.cross_entropy(logits.reshape(-1,33),labels.reshape(-1))),
                'minimum_value_accuracy':min(v['accuracy'] for v in value_hits.values()),
                'by_value':value_hits,
                'predicted_classes':{str(v-16):int((guess==v).sum()) for v in range(33)}}
        if native is not None:
            native_hit=native.eq(labels)
            result['native_operand_exact']=float(native_hit.float().mean())
            result['native_pair_exact']=float(native_hit.all(-1).float().mean())
    return result


def passes(intact,zero,wrong):
    cells=intact['by_value']
    if len(cells)!=33 or any(v['total']<2 or v['accuracy']<.98 for v in cells.values()):
        return False
    return (intact['operand_exact']>=.98 and intact['pair_exact']>=.98 and
            intact['pair_exact']-zero['pair_exact']>=.10 and
            intact['pair_exact']-wrong['pair_exact']>=.10)


def assess(model,records,mean,std):
    labels=records['labels'];result={}
    for mode,features in records['features'].items():
        result[mode]=logits_and_metrics(model,features,labels,mean,std,
                                       native=records['native'][mode])
    if set(result)==set(MODES):
        result['gate_pass']=passes(result['intact'],result['zero'],result['wrong'])
    return result


def train_probe(train,dev,*,kind='linear',steps=400,lr=.003,eval_every=50,seed=20261026):
    if steps<1 or eval_every<1 or lr<=0:
        raise ValueError('HIDDEN_PROBE_BAD_TRAINING_CONFIG')
    torch.manual_seed(seed)
    features=train['features']['intact'];labels=train['labels']
    mean,std=normalization(features)
    model=Readout(features.shape[-1],kind=kind).float()
    opt=torch.optim.AdamW(model.parameters(),lr=lr,weight_decay=.0001)
    n=labels.shape[0];best=-1.;best_state=None;best_step=0;passing=0;best_eval=None
    for step in range(1,steps+1):
        model.train()
        batch=torch.randint(n,(min(128,n),))
        x=(features[batch].float()-mean)/std
        y=labels[batch]
        pred=model(x)
        loss=F.cross_entropy(pred.reshape(-1,33),y.reshape(-1))
        if not bool(torch.isfinite(loss)):
            raise RuntimeError('HIDDEN_PROBE_NONFINITE_LOSS')
        opt.zero_grad(set_to_none=True);loss.backward()
        torch.nn.utils.clip_grad_norm_(model.parameters(),1.)
        opt.step()
        if step==1 or step%eval_every==0 or step==steps:
            model.eval();report=assess(model,dev,mean,std)
            passed=bool(report['gate_pass']);passing=passing+1 if passed else 0
            intact=report['intact']
            objective=intact['pair_exact']+intact['operand_exact']+0.001*max(
                0.,intact['pair_exact']-max(report['zero']['pair_exact'],report['wrong']['pair_exact']))
            if objective>best:
                best=objective;best_step=step;best_eval=report
                best_state={k:v.detach().cpu().clone() for k,v in model.state_dict().items()}
            print('HYBRID_HIDDEN_STEP='+json.dumps({
                'kind':kind,'step':step,'loss':round(float(loss.detach()),6),
                'dev_pair_exact':intact['pair_exact'],
                'dev_operand_exact':intact['operand_exact'],
                'dev_zero_pair_exact':report['zero']['pair_exact'],
                'dev_wrong_pair_exact':report['wrong']['pair_exact'],
                'dev_native_operand_exact':intact['native_operand_exact'],
                'dev_minimum_value_accuracy':intact['minimum_value_accuracy'],
                'consecutive_passes':passing,'dev_pass':passed},sort_keys=True),flush=True)
            if passing>=2:break
    if best_state is None:raise RuntimeError('HIDDEN_PROBE_NO_CHECKPOINT')
    model.load_state_dict(best_state);model.eval()
    return model,mean,std,{'step':best_step,'dev':best_eval,'consecutive_dev_passes':passing}


def _config(*,device,source,layer,kind,steps,eval_every,lr,seed,styles,
            evidence,source_hash):
    return {'schema':SCHEMA,'device':device,'source':source,'layer':layer,'kind':kind,
            'steps':steps,'eval_every':eval_every,'lr':lr,'seed':seed,'styles':list(styles),
            'evidence':evidence,'source_sha256':source_hash}


def run(*,device='cuda',output=ROOT,head1_dir=join.HEAD1,operand_dir=operand.ROOT,
        consumption_dir=consume.ROOT,source='consumption-last',layer=-1,
        kind='linear',steps=400,eval_every=50,lr=.003,seed=20261026,
        model_loader=None,head1_override=None,allow_download=False):
    if device not in ('cpu','cuda') or source not in SOURCES:
        raise ValueError('HIDDEN_PROBE_BAD_CONFIG')
    root=Path(output)
    if root.exists() and any(root.iterdir()):
        raise RuntimeError('HIDDEN_PROBE_OUTPUT_EXISTS')
    pop=prior.stratified_provider(seed)
    backbone,tokenizer,embedding,head1,sender,evidence=load_frozen_stack(
        device=device,head1_dir=head1_dir,operand_dir=operand_dir,
        consumption_dir=consumption_dir,source=source,
        model_loader=model_loader,head1_override=head1_override,allow_download=allow_download)
    ids=prior.code_indices(prior.class_codes(tokenizer,16),16,device)
    styles=(0,)
    train=extract_split(backbone,tokenizer,embedding,head1,sender,pop.decks['train'],
                        device,ids,layer=layer,styles=styles,modes=('intact',),split='train')
    dev=extract_split(backbone,tokenizer,embedding,head1,sender,pop.decks['dev'],
                      device,ids,layer=layer,styles=styles,modes=MODES,split='dev')
    model,mean,std,best=train_probe(train,dev,kind=kind,steps=steps,eval_every=eval_every,
                                   lr=lr,seed=seed)
    root.mkdir(parents=True,exist_ok=True)
    config=_config(device=device,source=source,layer=layer,kind=kind,steps=steps,
                   eval_every=eval_every,lr=lr,seed=seed,styles=styles,evidence=evidence,
                   source_hash=prior.digest(__file__))
    prior.atomic_json(root/'config.json',config)
    prior.atomic_torch(root/'best.pt',{'schema':SCHEMA,'probe':model.state_dict(),
                        'mean':mean,'std':std,'step':best['step']})
    full_dev=assess(model,dev,mean,std)
    report={'schema':SCHEMA,'status':'HIDDEN_PROBE_DEV_FAIL',
            'dev':full_dev,'test_read':False,'checkpoint_step':best['step'],
            'source':source,'source_evidence':evidence,
            'backbone_trainable_parameters':0,'arithmetic_trained':False}
    if full_dev['gate_pass']:
        # TEST is never even extracted until DEV has passed.
        test=extract_split(backbone,tokenizer,embedding,head1,sender,pop.decks['test'],
                           device,ids,layer=layer,styles=(0,1),modes=MODES,split='test')
        test_metrics=assess(model,test,mean,std)
        report.update({'test':test_metrics,'test_read':True,
            'status':'HIDDEN_PROBE_PASS' if test_metrics['gate_pass'] else 'HIDDEN_PROBE_TEST_FAIL'})
    prior.atomic_json(root/'result.json',report)
    print('HYBRID_HIDDEN_RESULT='+json.dumps({
        'schema':SCHEMA,'status':report['status'],'source':source,
        'checkpoint_step':best['step'],'dev':{k:{key:value for key,value in v.items() if key in (
            'pair_exact','operand_exact','native_operand_exact','native_pair_exact','minimum_value_accuracy')}
            for k,v in full_dev.items() if isinstance(v,dict)},
        'test_read':report['test_read'],'backbone_trainable_parameters':0,
        'test':({k:{key:value for key,value in v.items() if key in (
            'pair_exact','operand_exact','native_operand_exact','native_pair_exact','minimum_value_accuracy')}
            for k,v in report['test'].items() if isinstance(v,dict)} if report['test_read'] else None)
        },sort_keys=True),flush=True)
    return report


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stage',choices=('preview','run'),default='run')
    parser.add_argument('--device',choices=('cpu','cuda'),default='cuda')
    parser.add_argument('--output',type=Path,default=ROOT)
    parser.add_argument('--head1-output',type=Path,default=join.HEAD1)
    parser.add_argument('--operand-output',type=Path,default=operand.ROOT)
    parser.add_argument('--consumption-output',type=Path,default=consume.ROOT)
    parser.add_argument('--source',choices=SOURCES,default='consumption-last')
    parser.add_argument('--layer',type=int,default=-1)
    parser.add_argument('--kind',choices=('linear','mlp'),default='linear')
    parser.add_argument('--steps',type=int,default=400)
    parser.add_argument('--eval-every',type=int,default=50)
    parser.add_argument('--lr',type=float,default=.003)
    parser.add_argument('--seed',type=int,default=20261026)
    parser.add_argument('--allow-model-download',action='store_true')
    args=parser.parse_args(argv)
    if args.stage=='preview':
        pop=prior.stratified_provider(args.seed)
        print('HYBRID_HIDDEN_PREVIEW='+json.dumps({'schema':SCHEMA,
             'source':args.source,'layer':args.layer,'kind':args.kind,
             'split_sizes':{k:len(v) for k,v in pop.decks.items()},
             'trainable_scope':'probe readout only','backbone_frozen':True,
             'head1_frozen':True,'head2_frozen':True,'arithmetic_targets_used':False,
             'test_sealed_until_dev':True,'controls':list(MODES)},sort_keys=True))
        return 0
    run(device=args.device,output=args.output,head1_dir=args.head1_output,
        operand_dir=args.operand_output,consumption_dir=args.consumption_output,
        source=args.source,layer=args.layer,kind=args.kind,steps=args.steps,
        eval_every=args.eval_every,lr=args.lr,seed=args.seed,
        allow_download=args.allow_model_download)
    return 0

if __name__=='__main__':
    try:sys.exit(main())
    except (RuntimeError,ValueError,FileNotFoundError,OSError,AssertionError) as e:
        print('HYBRID_HIDDEN_REFUSED='+str(e),file=sys.stderr);sys.exit(2)
