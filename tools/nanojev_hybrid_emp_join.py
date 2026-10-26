#!/usr/bin/env python3
"""Train HybridLink's numerical Head 2 using the EMP/zero-line curriculum.

Not a calculator at inference. Only A/B raw-source literal bits are inputs;
TRAIN delta labels are supervision. Existing Head 1 is used for localization.
The frozen Qwen input embedding matrix is edited only at Head-1 positions.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
import statistics
from pathlib import Path

import torch
from torch import nn
from torch.nn import functional as F

from tools import nanojev_hybrid_lexical_alignment as lex
from tools import nanojev_hybrid_emp_curriculum as curriculum

SCHEMA='nanojev.hybrid-emp-join-head2.v1'
ROOT=Path.home()/'NanoJev'/'runs'/'qwen3_06b_hybrid_emp_join_head2_v1'
HEAD1=lex.ROOT


def checksum(path):
    h=hashlib.sha256()
    with open(path,'rb') as f:
        for buf in iter(lambda:f.read(1024*1024),b''):h.update(buf)
    return h.hexdigest()


def signed_bitfield(value,width):
    """One sign and width-1 big-endian magnitude bits; exactly one zero."""
    if width<7 or abs(value)>=(1<<(width-1)):
        raise ValueError('HYBRID_BITFIELD_OVERFLOW')
    return [int(value<0)]+[(abs(value)>>i)&1 for i in range(width-2,-1,-1)]


def bits_to_int(bits):
    magnitude=sum(int(b)*2**i for i,b in enumerate(reversed(bits[1:])))
    return -magnitude if bits[0] and magnitude else magnitude


class NumericalHead2(nn.Module):
    """Learned operand-bit writer, edited-embedding reader and δ resolver.

    No δ arithmetic function or numeric answer is called in forward().
    The learned resolver must map recovered operand identities to delta bits.
    """
    def __init__(self,hidden=1024,sparse_dims=64,width=24,channels=128):
        super().__init__()
        if not (7<=width<=32 and 2<=sparse_dims<hidden):
            raise ValueError('HYBRID_HEAD2_BAD_SHAPE')
        self.hidden=hidden;self.sparse_dims=sparse_dims;self.width=width
        self.writer=nn.Sequential(nn.Linear(width,channels),nn.GELU(),
                                  nn.Linear(channels,sparse_dims),nn.Tanh())
        self.reader=nn.Sequential(nn.Linear(sparse_dims,channels),nn.GELU(),
                                  nn.Linear(channels,width))
        self.resolver=nn.Sequential(nn.Linear(2*width,channels),nn.GELU(),
                                    nn.Linear(channels,channels),nn.GELU(),
                                    nn.Linear(channels,width))

    def forward(self,base,edit_map,positions,source_bits,indices,enabled=True,swap=False):
        if base.ndim!=2 or base.shape!=edit_map.shape or base.shape[1]!=self.hidden:
            raise ValueError('HYBRID_HEAD2_EDIT_SHAPE')
        if len(positions)!=2 or source_bits.shape!=(2,self.width):
            raise ValueError('HYBRID_HEAD2_EXPECTS_TWO_OPERANDS')
        if indices.numel()!=self.sparse_dims:
            raise ValueError('HYBRID_HEAD2_DIMENSION_DRIFT')
        if any(not p or max(p)>=base.shape[0] or min(p)<0 for p in positions):
            raise ValueError('HYBRID_HEAD2_POSITION_DRIFT')
        if set(positions[0]) & set(positions[1]):
            raise ValueError('HYBRID_HEAD2_OVERLAP')
        correction=torch.zeros_like(base,dtype=torch.float32)
        values=self.writer(source_bits) * 0.2
        if swap:values=values.flip(0)
        for i,tokens in enumerate(positions):
            for t in tokens:
                # Replace only selected channels of numeric tokens. This is
                # a correction, NOT an edit to the pretrained embedding table.
                if enabled:
                    correction[t,indices]=values[i]-base[t,indices].float()
        corrected=lex.apply_edit(base,edit_map,correction)
        recovered=[]
        for tokens in positions:
            local=corrected[tokens].float().mean(0)[indices]
            recovered.append(self.reader(local))
        recovered=torch.stack(recovered)
        answer=self.resolver(torch.sigmoid(recovered).reshape(1,-1))[0]
        return {'operand':recovered,'delta':answer,'edited':corrected}


def load_head1(output,device,hidden):
    root=Path(output)
    result=json.loads((root/'gate_result.json').read_text('utf-8'))
    config=json.loads((root/'config.json').read_text('utf-8'))
    if result.get('schema')!=lex.SCHEMA or not result.get('gate_pass') or result.get('status')!='ALIGNMENT_PASS':
        raise RuntimeError('HYBRID_HEAD1_CERTIFICATION_REQUIRED')
    if config.get('identity',{}).get('hidden')!=hidden:
        raise RuntimeError('HYBRID_HEAD1_EMBEDDING_MISMATCH')
    cp=root/'best.pt'
    state=torch.load(cp,map_location='cpu',weights_only=True)
    if state.get('schema')!=lex.SCHEMA:
        raise RuntimeError('HYBRID_HEAD1_CHECKPOINT_SCHEMA')
    head=lex.LexicalAlignmentHead(hidden,config['width'],config['sparse_dims']).to(device)
    head.load_state_dict(state['head'],strict=True)
    head.eval().requires_grad_(False)
    return head,{'sha256':checksum(cp),'config_sha256':checksum(root/'config.json'),
                 'gate_sha256':checksum(root/'gate_result.json'),'schema':lex.SCHEMA,
                 'sparse_dims':config['sparse_dims']}


def extract(card,style,tokenizer,head1,embedding,device,width):
    """Only the predicted spans identify input numbers; gold delta not read."""
    if max(abs(card.a),abs(card.b))>16:
        raise RuntimeError('HYBRID_LEXICAL_RADIUS_NOT_CERTIFIED')
    text=card.render(style)
    ids,offsets,membership=lex.byte_token_map(text,tokenizer)
    data=text.encode('utf-8')
    with torch.no_grad():
        flags,spans,_=head1.structured_byte_logits(
            torch.tensor(list(data),device=device,dtype=torch.long),lex.digit_runs(data),True)
        mask=head1.edit_map(flags,membership).detach()
        base=embedding(torch.tensor(ids,device=device)).detach()
    if len(spans)!=2:raise RuntimeError('HYBRID_HEAD1_TWO_SPANS_REQUIRED')
    # Exact lexical positions are checked against source truth but NEVER fixed
    # or replaced with oracle positions in the model's input.
    values=[int(data[a:b].decode('ascii')) for a,b in spans]
    if values != [card.a,card.b]:raise RuntimeError('HYBRID_HEAD1_LITERAL_MISMATCH')
    tokens=[[i for i,(lo,hi) in enumerate(offsets) if hi>a and lo<b] for a,b in spans]
    if any(not t for t in tokens) or set(tokens[0])&set(tokens[1]):
        raise RuntimeError('HYBRID_HEAD1_TOKEN_OVERLAP')
    indices=head1.selected_dimensions()
    if (mask[tokens[0]].amax(dim=0)[indices].min()<0.5 or
        mask[tokens[1]].amax(dim=0)[indices].min()<0.5):
        raise RuntimeError('HYBRID_HEAD1_EDIT_MAP_INCOMPLETE')
    source_bits=torch.tensor([signed_bitfield(v,width) for v in values],
                             device=device,dtype=torch.float32)
    return {'text':text,'base':base,'gate':mask,'positions':tokens,
            'bits':source_bits,'dimensions':indices,'values':values}


def forward_case(model,example,enabled=True,swap=False):
    return model(example['base'],example['gate'],example['positions'],
                 example['bits'],example['dimensions'],enabled,swap)


def target_bits(card,width,device):
    return torch.tensor(signed_bitfield(card.delta,width),device=device,dtype=torch.float32)


def loss_for(model,example,card,contrastive=True):
    out=forward_case(model,example)
    true_operand=example['bits'];true_sum=target_bits(card,model.width,out['delta'].device)
    op=F.binary_cross_entropy_with_logits(out['operand'],true_operand)
    summation=F.binary_cross_entropy_with_logits(out['delta'],true_sum)
    # Separate mismatched/empty edit controls; gradients only through intact.
    with torch.no_grad():
        empty=forward_case(model,example,enabled=False)
        wrong=forward_case(model,example,swap=True)
        empty_loss=F.binary_cross_entropy_with_logits(empty['delta'],true_sum)
        wrong_loss=F.binary_cross_entropy_with_logits(wrong['delta'],true_sum)
    ranking=(F.relu(0.15+summation-empty_loss)+F.relu(0.15+summation-wrong_loss)) if contrastive else summation*0
    loss=op+summation+0.25*ranking
    return loss,{'operand_ce':op.detach(),'delta_ce':summation.detach(),
                 'contrastive':ranking.detach()},out


def decode_logits(logits):
    return bits_to_int((logits.detach()>=0).int().tolist())


def exact_logits(logits,expected):
    """Canonical signed-bit exactness, including zero's sign bit."""
    got=(logits.detach()>=0).to(torch.float32)
    return bool(torch.equal(got,expected.to(torch.float32)))


def diagnostics(model,cards,tokenizer,head1,embedding,device,styles=(0,1),limit=0):
    model.eval()
    errors=[];correct=0;correct_integer=0;within5=0;operand=0;empty=0;swapped=0;align=0
    n=0;signs={'negative':[0,0],'zero':[0,0],'positive':[0,0]}
    cells={};residuals={};magnitudes={}
    with torch.inference_mode():
        for card in cards[:limit or len(cards)]:
            for style in styles:
                n+=1
                try:ex=extract(card,style,tokenizer,head1,embedding,device,model.width)
                except (RuntimeError,ValueError):
                    errors.append(None);continue
                align+=1
                raw=forward_case(model,ex)
                p=decode_logits(raw['delta']);a=decode_logits(raw['operand'][0]);b=decode_logits(raw['operand'][1])
                truth=target_bits(card,model.width,device)
                correct+=int(exact_logits(raw['delta'],truth))
                correct_integer+=int(p==card.delta)
                within5+=int(p==card.delta if card.delta==0 else abs(p-card.delta)<=0.05*abs(card.delta))
                operand+=int(all(exact_logits(raw['operand'][i],ex['bits'][i]) for i in range(2)))
                empty+=int(exact_logits(forward_case(model,ex,enabled=False)['delta'],truth))
                swapped+=int(exact_logits(forward_case(model,ex,swap=True)['delta'],truth))
                errors.append(p-card.delta)
                label='zero' if card.delta==0 else ('negative' if card.delta<0 else 'positive')
                signs[label][0]+=int(p==card.delta);signs[label][1]+=1
                bucket=(label,'frontier' if abs(card.delta)>16 else 'core')
                cell=cells.setdefault(bucket,[0,0]);cell[0]+=int(p==card.delta);cell[1]+=1
                rc=residuals.setdefault(str(card.delta),[0,0]);rc[0]+=int(p==card.delta);rc[1]+=1
                mag=('0' if card.magnitude==0 else '1-4' if card.magnitude<=4 else
                     '5-8' if card.magnitude<=8 else '9-16')
                mc=magnitudes.setdefault(mag,[0,0]);mc[0]+=int(p==card.delta);mc[1]+=1
    finite=[x for x in errors if x is not None]
    return {'cases':n,'aligned':align,'alignment_fraction':align/max(1,n),
            'operand_exact':operand/max(1,n),'delta_exact':correct/max(1,n),
            'delta_integer_exact':correct_integer/max(1,n),
            'within_5pct':within5/max(1,n),
            'zero_control_exact':empty/max(1,n),'shuffled_control_exact':swapped/max(1,n),
            'error_mean':statistics.mean(finite) if finite else None,
            'error_stddev':statistics.pstdev(finite) if finite else None,
            'errors_counted':len(finite),
            'by_sign':{k:{'correct':v[0],'total':v[1]} for k,v in signs.items()},
            'by_cell':{':'.join(k):{'correct':v[0],'total':v[1]} for k,v in cells.items()},
            'by_residual':{k:{'correct':v[0],'total':v[1]} for k,v in residuals.items()},
            'by_magnitude':{k:{'correct':v[0],'total':v[1]} for k,v in magnitudes.items()}}


def _json(path,record):
    path=Path(path);tmp=path.with_suffix('.tmp');tmp.write_text(json.dumps(record,indent=2,sort_keys=True)+'\n',encoding='utf8');tmp.replace(path)


def _weights(model):
    return {k:v.detach().cpu().clone() for k,v in model.state_dict().items()}


def train(*,device='cuda',output=ROOT,head1_dir=HEAD1,radius=16,bit_width=24,
          steps=1200,eval_every=100,seed=20261026,learning_rate=0.001,
          resume=False,stop_after=None,allow_download=False,loader=None,
          head1_override=None):
    if device not in ('cpu','cuda') or radius>16 or radius<1 or steps<1 or eval_every<1:
        raise ValueError('HYBRID_JOIN_BAD_CONFIG')
    if device=='cuda' and not torch.cuda.is_available():raise RuntimeError('HYBRID_JOIN_CUDA_UNAVAILABLE')
    output=Path(output)
    if output.exists() and not resume and any(output.iterdir()):
        raise RuntimeError('HYBRID_JOIN_OUTPUT_EXISTS')
    tokenizer,embedding,ident=lex.load_embeddings(device,allow_download=allow_download,loader=loader)
    if head1_override is None: head1,hid=load_head1(head1_dir,device,ident['hidden'])
    else:head1,hid=head1_override
    population=curriculum.LocalCurriculum(radius,seed)
    sources={'emp_sampler':checksum(curriculum.outward.__file__),
             'zero_line_r16':checksum(curriculum.r16.__file__),
             'outward_sampler':checksum(curriculum.sampled.__file__),
             'provider':checksum(curriculum.__file__)}
    config={'schema':SCHEMA,'device':device,'radius':radius,'bit_width':bit_width,
            'steps':steps,'eval_every':eval_every,'seed':seed,'learning_rate':learning_rate,
            'embedding':ident,'head1':hid,'sources':sources,
            'train_fingerprint':curriculum.signature(population.decks['train']),
            'dev_fingerprint':curriculum.signature(population.decks['dev']),
            'test_fingerprint':curriculum.signature(population.decks['test'])}
    output.mkdir(parents=True,exist_ok=True)
    config_path=output/'config.json'
    torch.manual_seed(seed)
    if device=='cuda':torch.cuda.manual_seed_all(seed)
    model=NumericalHead2(ident['hidden'],hid['sparse_dims'],bit_width).to(device)
    optim=torch.optim.AdamW(model.parameters(),lr=learning_rate,weight_decay=0.)
    best=-1.;start=1
    if resume:
        if (output/'gate_result.json').exists():raise RuntimeError('HYBRID_JOIN_ALREADY_GATED')
        if json.loads(config_path.read_text('utf8'))!=config:raise RuntimeError('HYBRID_JOIN_RESUME_CONFIG_DRIFT')
        state=torch.load(output/'last.pt',map_location='cpu',weights_only=True)
        if state.get('schema')!=SCHEMA or state['step']>=steps:raise RuntimeError('HYBRID_JOIN_BAD_RESUME')
        model.load_state_dict(state['head']);optim.load_state_dict(state['optimizer']);population.load(state['curriculum'])
        torch.set_rng_state(state['torch_rng'])
        if device=='cuda' and state['cuda_rng'] is not None:torch.cuda.set_rng_state_all(state['cuda_rng'])
        start=state['step']+1;best=state['best']
    else:_json(config_path,config)
    for step in range(start,steps+1):
        model.train();card=population.next();style=(step-1)%5
        ex=extract(card,style,tokenizer,head1,embedding,device,bit_width)
        optim.zero_grad(set_to_none=True)
        loss,parts,out=loss_for(model,ex,card)
        loss.backward()
        if step==start:
            assert all(p.grad is None for p in embedding.parameters()),'HYBRID_JOIN_FROZEN_BACKBONE_GRAD'
            for name,module in [('writer',model.writer),('reader',model.reader),('resolver',model.resolver)]:
                if not any(p.grad is not None and p.grad.detach().abs().sum().item()>0 for p in module.parameters()):
                    raise RuntimeError('HYBRID_JOIN_NO_GRAD_'+name)
        nn.utils.clip_grad_norm_(model.parameters(),2.,error_if_nonfinite=True)
        optim.step()
        population.emphasize(card.delta,decode_logits(out['delta'])!=card.delta)
        if step==start or step%eval_every==0 or step==steps:
            report=diagnostics(model,population.decks['dev'],tokenizer,head1,embedding,device,styles=(0,1))
            # DEV only. Require source alignment, operand identity and
            # arithmetic improvement; no TEST checkpoint selection.
            score=(report['alignment_fraction']+2*report['operand_exact']+
                   3*report['delta_exact']+
                   max(0,report['delta_exact']-report['zero_control_exact'])+
                   max(0,report['delta_exact']-report['shuffled_control_exact']))
            if score>best:
                best=score;torch.save({'schema':SCHEMA,'step':step,'head':_weights(model),'objective':score},output/'best.pt')
            event={'step':step,'total':steps,'objective':round(score,5),'best_objective':round(best,5),
                   'loss':round(loss.item(),5),**{k:round(v.item(),5) for k,v in parts.items()},'dev':report}
            if device=='cuda':event['cuda_peak_mib']=round(torch.cuda.max_memory_allocated()/1048576,1)
            print('HYBRID_JOIN_STEP='+json.dumps(event,sort_keys=True),flush=True)
        torch.save({'schema':SCHEMA,'step':step,'head':_weights(model),'optimizer':optim.state_dict(),
                    'curriculum':population.state(),'best':best,'torch_rng':torch.get_rng_state(),
                    'cuda_rng':torch.cuda.get_rng_state_all() if device=='cuda' else None},output/'last.pt')
        if stop_after and step>=stop_after:break
    result={'schema':SCHEMA,'status':'TRAINED_UNCERTIFIED' if step==steps else 'PAUSED',
            'step':step,'steps':steps,'best_objective':best,
            'test_evaluated':False,'existing_emp_models_modified':False,'backbone_mutations':0}
    _json(output/'training_result.json',result)
    print('HYBRID_JOIN_TRAIN_RESULT='+json.dumps(result,sort_keys=True),flush=True)
    return result


def gate(*,device='cuda',output=ROOT,head1_dir=HEAD1,allow_download=False,loader=None,head1_override=None):
    output=Path(output)
    if (output/'gate_result.json').exists():raise RuntimeError('HYBRID_JOIN_TEST_ALREADY_USED')
    cfg=json.loads((output/'config.json').read_text('utf8'))
    result=json.loads((output/'training_result.json').read_text('utf8'))
    if cfg['schema']!=SCHEMA or result['status']!='TRAINED_UNCERTIFIED' or device!=cfg['device']:
        raise RuntimeError('HYBRID_JOIN_GATE_REQUIRES_COMPLETE_RUN')
    tokenizer,embedding,ident=lex.load_embeddings(device,allow_download=allow_download,loader=loader)
    if head1_override is None:head1,hid=load_head1(head1_dir,device,ident['hidden'])
    else:head1,hid=head1_override
    sources={'emp_sampler':checksum(curriculum.outward.__file__),
             'zero_line_r16':checksum(curriculum.r16.__file__),
             'outward_sampler':checksum(curriculum.sampled.__file__),
             'provider':checksum(curriculum.__file__)}
    if cfg['embedding']!=ident or cfg['head1']!=hid or cfg['sources']!=sources:
        raise RuntimeError('HYBRID_JOIN_PROVENANCE_DRIFT')
    population=curriculum.LocalCurriculum(cfg['radius'],cfg['seed'])
    for split in ('train','dev','test'):
        if cfg[f'{split}_fingerprint']!=curriculum.signature(population.decks[split]):
            raise RuntimeError('HYBRID_JOIN_SPLIT_DRIFT')
    model=NumericalHead2(ident['hidden'],hid['sparse_dims'],cfg['bit_width']).to(device)
    best=torch.load(output/'best.pt',map_location='cpu',weights_only=True)
    if best['schema']!=SCHEMA:raise RuntimeError('HYBRID_JOIN_BAD_CHECKPOINT')
    model.load_state_dict(best['head'],strict=True)
    test=diagnostics(model,population.decks['test'],tokenizer,head1,embedding,device,styles=(0,1,2))
    requirements={'alignment_fraction':0.98,'operand_exact':0.98,'delta_exact':0.80,
                  'min_advantage_over_zero':0.10,'min_advantage_over_shuffled':0.10}
    passed=(test['alignment_fraction']>=requirements['alignment_fraction'] and
            test['operand_exact']>=requirements['operand_exact'] and
            test['delta_exact']>=requirements['delta_exact'] and
            test['delta_exact']-test['zero_control_exact']>=requirements['min_advantage_over_zero'] and
            test['delta_exact']-test['shuffled_control_exact']>=requirements['min_advantage_over_shuffled'])
    final={'schema':SCHEMA,'status':'HEAD2_LOCAL_PASS' if passed else 'HEAD2_LOCAL_FAIL',
           'gate_pass':passed,'criterion':requirements,'test':test,
           'train_checkpoint_step':best['step'],'test_once':True,
           'arithmetic_supervised':True,'oracle_delta_at_inference':False,
           'outward_certified':False,'emp_models_modified':False,'head1_sha256':hid.get('sha256')}
    _json(output/'gate_result.json',final)
    print('HYBRID_JOIN_GATE='+json.dumps(final,sort_keys=True),flush=True)
    return final


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--stage',choices=('all','train','gate','preview'),default='all')
    p.add_argument('--device',choices=('cuda','cpu'),default='cuda')
    p.add_argument('--output',type=Path,default=ROOT)
    p.add_argument('--head1-output',type=Path,default=HEAD1)
    p.add_argument('--radius',type=int,default=16)
    p.add_argument('--bit-width',type=int,default=24)
    p.add_argument('--steps',type=int,default=1200)
    p.add_argument('--eval-every',type=int,default=100)
    p.add_argument('--seed',type=int,default=20261026)
    p.add_argument('--lr',type=float,default=0.001)
    p.add_argument('--allow-model-download',action='store_true')
    p.add_argument('--resume',action='store_true')
    p.add_argument('--stop-after',type=int,default=None)
    args=p.parse_args(argv)
    if args.stage=='preview':
        cards=curriculum.LocalCurriculum(args.radius,args.seed)
        print('HYBRID_JOIN_PROVIDER='+json.dumps({'schema':curriculum.SCHEMA,'radius':args.radius,
            'split_sizes':{k:len(v) for k,v in cards.decks.items()},
            'train_example':cards.next().render(),'target_not_in_input':True},sort_keys=True));return 0
    common={'device':args.device,'output':args.output,'head1_dir':args.head1_output,
            'allow_download':args.allow_model_download}
    if args.stage in ('train','all'):
        result=train(**common,radius=args.radius,bit_width=args.bit_width,steps=args.steps,
                     eval_every=args.eval_every,seed=args.seed,learning_rate=args.lr,
                     resume=args.resume,stop_after=args.stop_after)
        if result['status']!='TRAINED_UNCERTIFIED':return 0
    if args.stage in ('gate','all'):gate(**common)
    return 0


if __name__=='__main__':raise SystemExit(main())
