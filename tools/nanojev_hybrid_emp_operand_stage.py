#!/usr/bin/env python3
"""Isolated HybridLink Head-2 operand-transmission qualification.

Uses the existing EMP/zero-line local curriculum and certified lexical Head 1.
Arithmetic resolution is intentionally absent; delta is neither an input nor a
training target.  The decoder sees ONLY the sparse numerical correction.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import torch
from torch import nn
from torch.nn import functional as F

from tools import nanojev_hybrid_emp_curriculum as curriculum
from tools import nanojev_hybrid_emp_join as prior
from tools import nanojev_hybrid_lexical_alignment as lex

SCHEMA = 'nanojev.hybrid-emp-operand-transmission.v2'
ROOT = Path.home() / 'NanoJev' / 'runs' / 'qwen3_06b_hybrid_emp_operand_v2'
WIDTH = 6  # one sign + five magnitude bits: covers [-16,+16]
PASS_PAIR_EXACT = 0.98


class OperandTransmission(nn.Module):
    """Two independently reconstructed operands through one shared bit code.

    No delta input, delta output, arithmetic module, or original bytes at readout.
    The head writes selected embedding dimensions at Head 1's token locations;
    reader consumes precisely those dimensions and nothing else.
    """
    def __init__(self, sparse_dims=64, channels=128, width=WIDTH):
        super().__init__()
        if width != WIDTH or not 2 <= sparse_dims <= 1024:
            raise ValueError('OPERAND_BAD_SHAPE')
        self.width, self.sparse_dims = width, sparse_dims
        self.writer = nn.Sequential(nn.Linear(width, channels), nn.GELU(),
                                    nn.Linear(channels, sparse_dims), nn.Tanh())
        self.reader = nn.Sequential(nn.Linear(sparse_dims, channels), nn.GELU(),
                                    nn.Linear(channels, width))

    def transmit(self, bits, *, mode='intact', wrong_bits=None):
        """Batch-capable exact equivalent of the sparse replacement readout.

        The selected coordinates of each numerical token are REPLACED, not
        added. The resulting readout cannot see unedited Qwen coordinates.
        """
        if bits.ndim != 2 or bits.shape[-1] != self.width:
            raise ValueError('OPERAND_EXPECTS_BIT_PAIRS')
        if mode not in ('intact', 'zero', 'wrong'):
            raise ValueError('OPERAND_BAD_CONTROL')
        if mode == 'wrong':
            if wrong_bits is None or wrong_bits.shape != bits.shape:
                raise ValueError('OPERAND_WRONG_BITS_REQUIRED')
            code = self.writer(wrong_bits)
        elif mode == 'zero':
            code = torch.zeros(bits.shape[0], self.sparse_dims,
                               device=bits.device, dtype=bits.dtype)
        else:
            code = self.writer(bits)
        return self.reader(code)

    def edited_embeddings(self, example, *, mode='intact', wrong_bits=None):
        """Produce the full Qwen input embedding edit, without invoking Qwen.

        Prove the exact equality of the sparse edited coordinates and the
        learned code. Reject incomplete or overlapping Head-1 masks.
        """
        base, gate, positions, indices = (example['base'], example['gate'],
                                          example['positions'], example['dimensions'])
        bits = example['bits']
        if bits.shape != (2, WIDTH) or len(positions) != 2 or indices.numel() != self.sparse_dims:
            raise ValueError('OPERAND_EDIT_INPUT_SHAPE')
        if set(positions[0]) & set(positions[1]):
            raise ValueError('OPERAND_OVERLAPPING_POSITIONS')
        if mode == 'wrong':
            if wrong_bits is None or wrong_bits.shape != bits.shape:
                raise ValueError('OPERAND_WRONG_BITS_REQUIRED')
            codes = self.writer(wrong_bits)
        elif mode == 'zero':
            codes = torch.zeros((2, self.sparse_dims), device=bits.device, dtype=bits.dtype)
        elif mode == 'intact':
            codes = self.writer(bits)
        else:
            raise ValueError('OPERAND_BAD_CONTROL')
        # Qwen input embeddings may be BF16. First quantize the learned code
        # ONCE to the destination dtype, then form the sparse correction in
        # FP32. Forming the correction in BF16 and adding it to BF16 base
        # embeddings double-rounds the update, often losing one BF16 ULP.
        # Invert Head 1's actual soft gate; it need not be identically 1.
        correction = torch.zeros_like(base, dtype=torch.float32)
        for i, positions_i in enumerate(positions):
            if not positions_i or min(positions_i) < 0 or max(positions_i) >= base.shape[0]:
                raise ValueError('OPERAND_INVALID_POSITION')
            code_at_destination = codes[i].to(base.dtype).float()
            for t in positions_i:
                gate_values = gate[t, indices].float()
                if not bool(torch.all(gate_values >= 0.999)):
                    raise RuntimeError('OPERAND_HEAD1_MASK_INCOMPLETE')
                correction[t, indices] = (code_at_destination - base[t, indices].float()) / gate_values
        corrected = lex.apply_edit(base.float(), gate.float(), correction).to(base.dtype)
        # Enforce the declared REPLACEMENT semantics at the exact selected
        # locations. FP32 subtraction/addition can otherwise introduce a few
        # ULPs even when the destination is FP32; BF16 adds a further rounding
        # hazard. We never modify a position or dimension outside Head 1's mask.
        for i, positions_i in enumerate(positions):
            for t in positions_i:
                corrected[t, indices] = codes[i].to(base.dtype)
            actual = corrected[positions_i][:, indices]
            expected = codes[i].to(base.dtype).expand_as(actual)
            if not torch.equal(actual, expected):
                raise RuntimeError('OPERAND_SPARSE_READOUT_MISMATCH')
        return corrected


def bits(values, device):
    if any(abs(int(v))>16 for v in values):
        raise ValueError('OPERAND_BITFIELD_OVERFLOW')
    return torch.tensor([([int(int(v)<0)]+[(abs(int(v))>>i)&1 for i in range(4,-1,-1)]) for v in values],
                        device=device, dtype=torch.float32)


def wrong_pair(values):
    """Change both operand identities, never merely swap A and B."""
    return [(-16 if int(v) == 16 else int(v) + 1) for v in values]


def loss_for(model, operand_bits):
    predictions = model.transmit(operand_bits)
    ce = F.binary_cross_entropy_with_logits(predictions, operand_bits)
    signed = operand_bits.mul(2).sub(1)
    margin = F.softplus(1.0 - signed*predictions).mean()
    return ce + 0.25*margin, {'bit_ce':ce.detach(), 'margin':margin.detach()}


def examples_for(cards, tokenizer, head1, embedding, device, styles=(0,1)):
    """Build source-aligned operand pairs. Never pass delta to the student."""
    pairs=[]; audit=[]
    for card in cards:
        for style in styles:
            ex=prior.extract(card, style, tokenizer, head1, embedding, device, 7)
            ex['bits']=bits(ex['values'],device)
            if ex['values'] != [card.a, card.b]:
                raise RuntimeError('OPERAND_SOURCE_DRIFT')
            pairs.append(ex['bits'])
            audit.append(ex)
    if not pairs:
        raise RuntimeError('OPERAND_EMPTY_SPLIT')
    return torch.stack(pairs), audit


def _ratios(model, examples, wrong):
    n=examples.shape[0]
    with torch.inference_mode():
        intact=model.transmit(examples)
        zero=model.transmit(examples, mode='zero')
        swapped=model.transmit(examples, mode='wrong', wrong_bits=wrong)
        truth=examples.to(torch.bool)
        exact=lambda output: (output.ge(0).eq(truth).all(dim=-1))
        full=exact(intact)
        positive=exact(zero)
        mismatched=exact(swapped)
        pair=lambda v: v.reshape(n//2,2).all(dim=1).float().mean().item()
        operand=full.float().mean().item()
        pair_rate=pair(full)
        return {'operands':n,'pairs':n//2,
                'operand_exact':operand,'pair_exact':pair_rate,
                'zero_pair_exact':pair(positive),
                'mismatched_pair_exact':pair(mismatched),
                'sign_exact':(intact[:,0].ge(0).eq(truth[:,0])).float().mean().item(),
                'bit_accuracy':(intact.ge(0).eq(truth)).float().mean().item()}


def assess(model, batched, *, chunk=512):
    """Return exact metrics, including controls on changed-value bitfields."""
    model.eval()
    inputs=batched.reshape(-1, WIDTH)
    wrong_values=[wrong_pair([prior.bits_to_int(v.tolist()) for v in row]) for row in batched]
    wrong=torch.stack([bits(pair, batched.device) for pair in wrong_values]).reshape(-1, WIDTH)
    if inputs.shape[0]<=chunk:
        return _ratios(model, inputs, wrong)
    # Deterministic aggregation across bounded chunks of whole pairs.
    chunks=[]
    for i in range(0,batched.shape[0],chunk//2):
        j=min(i+chunk//2,batched.shape[0])
        chunks.append((_ratios(model,inputs[i*2:j*2],wrong[i*2:j*2]),j-i))
    total=batched.shape[0]; result={'operands':total*2,'pairs':total}
    for name in ('operand_exact','pair_exact','zero_pair_exact','mismatched_pair_exact','sign_exact','bit_accuracy'):
        result[name]=sum(x[name]*count for x,count in chunks)/total
    return result


def assess_edited_embeddings(model, examples):
    """Assess the REAL quantized sparse readout, never the direct FP32 path.

    Neither the original operand bytes nor unedited Qwen dimensions are
    passed to the reader. This test includes Head 1 masks and BF16 rounding.
    """
    model.eval()
    whole = []
    operand_correct = 0
    pair_correct = 0
    with torch.inference_mode():
        for ex in examples:
            edited = model.edited_embeddings(ex)
            if not torch.equal(edited[ex['gate'] < 0.5], ex['base'][ex['gate'] < 0.5]):
                raise RuntimeError('OPERAND_UNRELATED_COORDINATES_CHANGED')
            exact = []
            for i, positions in enumerate(ex['positions']):
                sparse = edited[positions].float()[:, ex['dimensions']].mean(dim=0)
                prediction = model.reader(sparse)
                correct = bool(prediction.ge(0).eq(ex['bits'][i].bool()).all())
                exact.append(correct)
            operand_correct += sum(exact)
            pair_correct += int(all(exact))
            whole.append(exact)
    pairs = len(examples)
    if not pairs:
        raise RuntimeError('OPERAND_EMPTY_EDIT_AUDIT')
    return {'pairs':pairs, 'operands':pairs*2,
            'pair_exact':pair_correct/pairs,
            'operand_exact':operand_correct/(pairs*2),
            'input_dtype':str(examples[0]['base'].dtype),
            'sparse_coordinates_checked':pairs*2*model.sparse_dims}


def _json(path,obj):
    path=Path(path);tmp=path.with_suffix('.tmp')
    tmp.write_text(json.dumps(obj,indent=2,sort_keys=True)+'\n',encoding='utf8');tmp.replace(path)


def _state(model):
    return {k:v.detach().cpu().clone() for k,v in model.state_dict().items()}


def setup(device,head1_dir,allow_download=False,loader=None,head1_override=None):
    tokenizer,embedding,ident=lex.load_embeddings(device,allow_download=allow_download,loader=loader)
    if head1_override is None: head1,hid=prior.load_head1(head1_dir,device,ident['hidden'])
    else:head1,hid=head1_override
    return tokenizer,embedding,ident,head1,hid


def train(*,device='cuda',output=ROOT,head1_dir=prior.HEAD1,radius=16,
          steps=1800,eval_every=100,batch_size=32,seed=20261026,
          learning_rate=0.003,resume=False,stop_after=None,
          allow_download=False,loader=None,head1_override=None):
    if device not in ('cpu','cuda') or radius != 16 or steps<1 or eval_every<1 or batch_size<2:
        raise ValueError('OPERAND_INVALID_CONFIG')
    if device=='cuda' and not torch.cuda.is_available():
        raise RuntimeError('OPERAND_CUDA_UNAVAILABLE')
    output=Path(output)
    if output.exists() and not resume and any(output.iterdir()):
        raise RuntimeError('OPERAND_OUTPUT_EXISTS')
    tokenizer,embedding,ident,head1,hid=setup(device,head1_dir,allow_download,loader,head1_override)
    curriculum_instance=curriculum.LocalCurriculum(radius,seed)
    source_hashes={'curriculum':prior.checksum(curriculum.__file__),
                   'join_extractor':prior.checksum(prior.__file__),
                   'head1':hid}
    config={'schema':SCHEMA,'width':WIDTH,'radius':radius,'steps':steps,
            'batch_size':batch_size,'eval_every':eval_every,'seed':seed,
            'lr':learning_rate,'device':device,'embedding':ident,'sources':source_hashes,
            'train_fingerprint':curriculum.signature(curriculum_instance.decks['train']),
            'dev_fingerprint':curriculum.signature(curriculum_instance.decks['dev']),
            'test_fingerprint':curriculum.signature(curriculum_instance.decks['test'])}
    # Head1 predictions are checked for every card, and the only data retained
    # for training are original A and B bitfields. No target delta is created.
    train_bits,_=examples_for(curriculum_instance.decks['train'],tokenizer,head1,embedding,device,(0,1,2))
    dev_bits,dev_audit=examples_for(curriculum_instance.decks['dev'],tokenizer,head1,embedding,device,(0,1))
    torch.manual_seed(seed)
    model=OperandTransmission(hid['sparse_dims']).to(device)
    optimizer=torch.optim.AdamW(model.parameters(),lr=learning_rate,weight_decay=0.)
    output.mkdir(parents=True,exist_ok=True)
    cp=output/'config.json';best=-1.;start=1;pass_streak=0
    if resume:
        if (output/'gate_result.json').exists():raise RuntimeError('OPERAND_ALREADY_GATED')
        if json.loads(cp.read_text('utf8'))!=config:raise RuntimeError('OPERAND_RESUME_DRIFT')
        saved=torch.load(output/'last.pt',map_location='cpu',weights_only=True)
        if saved['schema']!=SCHEMA or saved['step']>=steps:raise RuntimeError('OPERAND_BAD_RESUME')
        model.load_state_dict(saved['head'])
        optimizer.load_state_dict(saved['optimizer'])
        torch.set_rng_state(saved['rng'])
        if device=='cuda' and saved['cuda_rng'] is not None:
            torch.cuda.set_rng_state_all(saved['cuda_rng'])
        best=saved['best'];start=saved['step']+1;pass_streak=saved.get('dev_pass_streak',0)
    else:
        if device=='cuda':torch.cuda.manual_seed_all(seed)
        _json(cp,config)
    # Exact edit audit: the corrected Qwen coordinate values MUST be the
    # values the decoder reads. Unrelated coordinates MUST remain unchanged.
    audit_sample=dev_audit[0]
    with torch.no_grad():
        edited=model.edited_embeddings(audit_sample)
        untouched=(audit_sample['gate']<0.5)
        if not torch.equal(edited[untouched],audit_sample['base'][untouched]):
            raise RuntimeError('OPERAND_UNRELATED_COORDINATES_CHANGED')
    train_data=train_bits.reshape(-1,WIDTH)
    for step in range(start,steps+1):
        model.train()
        selection=torch.randint(len(train_data),(batch_size,),device=device)
        batch=train_data[selection]
        optimizer.zero_grad(set_to_none=True)
        loss,parts=loss_for(model,batch)
        loss.backward()
        if step==start:
            for name,part in (('writer',model.writer),('reader',model.reader)):
                if not all(p.grad is not None and bool(p.grad.detach().abs().sum()>0) for p in part.parameters()):
                    raise RuntimeError('OPERAND_MISSING_GRAD_'+name)
            if any(p.grad is not None for p in embedding.parameters()):
                raise RuntimeError('OPERAND_BACKBONE_TRAINED')
        nn.utils.clip_grad_norm_(model.parameters(),2.,error_if_nonfinite=True)
        optimizer.step()
        if step==start or step%eval_every==0 or step==steps:
            dev=assess(model,dev_bits)
            score=dev['pair_exact']+dev['operand_exact']*0.001+dev['bit_accuracy']*0.000001
            if (dev['pair_exact']>=PASS_PAIR_EXACT and
                dev['pair_exact']-dev['zero_pair_exact']>=0.10 and
                dev['pair_exact']-dev['mismatched_pair_exact']>=0.10):
                pass_streak+=1
            else:
                pass_streak=0
            if score>best:
                best=score
                torch.save({'schema':SCHEMA,'step':step,'head':_state(model),
                            'dev_pair_exact':dev['pair_exact'],'objective':score},output/'best.pt')
            event={'step':step,'total':steps,'loss':round(float(loss.item()),6),
                   'best_objective':round(best,7),'dev_pass_streak':pass_streak,'dev':dev,
                   **{k:round(float(v.item()),6) for k,v in parts.items()}}
            if device=='cuda':event['cuda_peak_mib']=round(torch.cuda.max_memory_allocated()/1048576,1)
            print('HYBRID_OPERAND_STEP='+json.dumps(event,sort_keys=True),flush=True)
        torch.save({'schema':SCHEMA,'step':step,'head':_state(model),
                    'optimizer':optimizer.state_dict(),'best':best,'dev_pass_streak':pass_streak,
                    'rng':torch.get_rng_state(),
                    'cuda_rng':torch.cuda.get_rng_state_all() if device=='cuda' else None},output/'last.pt')
        if stop_after and step>=stop_after:break
        if pass_streak>=2:break
    outcome={'schema':SCHEMA,'status':'TRAINED_UNCERTIFIED' if step==steps or (pass_streak>=2 and not (stop_after and step>=stop_after)) else 'PAUSED',
             'dev_consecutive_passes':pass_streak,'step':step,'steps':steps,'best_objective':best,'test_evaluated':False,
             'arithmetic_trained':False,'backbone_mutations':0}
    _json(output/'training_result.json',outcome)
    print('HYBRID_OPERAND_TRAIN_RESULT='+json.dumps(outcome,sort_keys=True),flush=True)
    return outcome


def gate(*,device='cuda',output=ROOT,head1_dir=prior.HEAD1,
         allow_download=False,loader=None,head1_override=None):
    output=Path(output)
    if (output/'gate_result.json').exists():raise RuntimeError('OPERAND_TEST_ALREADY_USED')
    config=json.loads((output/'config.json').read_text('utf8'))
    training=json.loads((output/'training_result.json').read_text('utf8'))
    if config['schema']!=SCHEMA or training['status']!='TRAINED_UNCERTIFIED' or config['device']!=device:
        raise RuntimeError('OPERAND_GATE_REQUIRES_TRAINING')
    tokenizer,embedding,ident,head1,hid=setup(device,head1_dir,allow_download,loader,head1_override)
    provider=curriculum.LocalCurriculum(config['radius'],config['seed'])
    hashes={'curriculum':prior.checksum(curriculum.__file__),
            'join_extractor':prior.checksum(prior.__file__),'head1':hid}
    if config['sources']!=hashes or config['embedding']!=ident:
        raise RuntimeError('OPERAND_PROVENANCE_DRIFT')
    for split in ('train','dev','test'):
        if config[f'{split}_fingerprint']!=curriculum.signature(provider.decks[split]):
            raise RuntimeError('OPERAND_SPLIT_DRIFT')
    best=torch.load(output/'best.pt',map_location='cpu',weights_only=True)
    if best['schema']!=SCHEMA:raise RuntimeError('OPERAND_BAD_CHECKPOINT')
    model=OperandTransmission(hid['sparse_dims']).to(device)
    model.load_state_dict(best['head'],strict=True)
    # DEV preflight before TEST access: if the best checkpoint failed on DEV,
    # do not spend the sealed heldout split.
    dev_bits,dev_examples=examples_for(provider.decks['dev'],tokenizer,head1,embedding,device,(0,1))
    dev=assess(model,dev_bits)
    dev_edit=assess_edited_embeddings(model,dev_examples)
    if dev['pair_exact']<PASS_PAIR_EXACT or dev_edit['pair_exact']<PASS_PAIR_EXACT:
        result={'schema':SCHEMA,'status':'OPERAND_DEV_FAIL','gate_pass':False,
                'dev':dev,'dev_edited':dev_edit,'test_read':False,'checkpoint_step':best['step'],
                'arithmetic_trained':False,'resolver_unlocked':False}
        _json(output/'gate_result.json',result)
        print('HYBRID_OPERAND_GATE='+json.dumps(result,sort_keys=True),flush=True)
        return result
    test_bits,test_examples=examples_for(provider.decks['test'],tokenizer,head1,embedding,device,(0,1,2))
    test=assess(model,test_bits)
    # Certify the ACTUAL edits in the Qwen embedding dtype, not only the
    # direct FP32 writer->reader shortcut checked by assess().
    test_edit=assess_edited_embeddings(model,test_examples)
    passed=(test['pair_exact']>=PASS_PAIR_EXACT and
            test['operand_exact']>=PASS_PAIR_EXACT and
            test_edit['pair_exact']>=PASS_PAIR_EXACT and
            test_edit['operand_exact']>=PASS_PAIR_EXACT and
            test['pair_exact']-test['zero_pair_exact']>=0.10 and
            test['pair_exact']-test['mismatched_pair_exact']>=0.10)
    result={'schema':SCHEMA,'status':'OPERAND_TRANSMISSION_PASS' if passed else 'OPERAND_TRANSMISSION_FAIL',
            'gate_pass':passed,'criterion':{'pair_exact':PASS_PAIR_EXACT,
            'operand_exact':PASS_PAIR_EXACT,'minimum_control_gap':0.10},
            'dev':dev,'dev_edited':dev_edit,'test':test,'test_edited':test_edit,'test_read':True,
            'checkpoint_step':best['step'],'arithmetic_trained':False,
            'resolver_unlocked':passed,'outward_certified':False}
    _json(output/'gate_result.json',result)
    print('HYBRID_OPERAND_GATE='+json.dumps(result,sort_keys=True),flush=True)
    return result


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--stage',choices=('all','train','gate','preview'),default='all')
    p.add_argument('--device',choices=('cpu','cuda'),default='cuda')
    p.add_argument('--output',type=Path,default=ROOT)
    p.add_argument('--head1-output',type=Path,default=prior.HEAD1)
    p.add_argument('--radius',type=int,default=16)
    p.add_argument('--steps',type=int,default=1800)
    p.add_argument('--eval-every',type=int,default=100)
    p.add_argument('--batch-size',type=int,default=32)
    p.add_argument('--lr',type=float,default=0.003)
    p.add_argument('--seed',type=int,default=20261026)
    p.add_argument('--stop-after',type=int)
    p.add_argument('--resume',action='store_true')
    p.add_argument('--allow-model-download',action='store_true')
    args=p.parse_args(argv)
    if args.stage=='preview':
        pop=curriculum.LocalCurriculum(args.radius,args.seed)
        print('HYBRID_OPERAND_PROVIDER='+json.dumps({'schema':SCHEMA,'radius':args.radius,
              'train':len(pop.decks['train']),'dev':len(pop.decks['dev']),
              'test':len(pop.decks['test']), 'delta_used_as_training_input':False},sort_keys=True))
        return 0
    common={'device':args.device,'output':args.output,'head1_dir':args.head1_output,
            'allow_download':args.allow_model_download}
    if args.stage in ('all','train'):
        result=train(**common,radius=args.radius,steps=args.steps,eval_every=args.eval_every,
                     batch_size=args.batch_size,seed=args.seed,learning_rate=args.lr,
                     resume=args.resume,stop_after=args.stop_after)
        if result['status']!='TRAINED_UNCERTIFIED':return 0
    if args.stage in ('all','gate'):gate(**common)
    return 0

if __name__=='__main__':raise SystemExit(main())
