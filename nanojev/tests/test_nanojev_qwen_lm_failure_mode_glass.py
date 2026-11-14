"""CPU tests proving known-error correction without a target at inference."""
import json
from types import SimpleNamespace
import torch
from tools import nanojev_qwen_lm_failure_mode_glass as f


class Tokenizer:
    chars='PQ\nab'
    vocab={c:i for i,c in enumerate(chars)}
    pad_token_id=0
    eos_token_id=None
    def __call__(self,text,add_special_tokens=False):
        return {'input_ids':[self.vocab[ch] for ch in text]}
    def decode(self,ids,skip_special_tokens=True):
        return ''.join(self.chars[int(i)] for i in ids)


class Decoder(torch.nn.Module):
    def __init__(self):
        super().__init__();self.anchor=torch.nn.Parameter(torch.ones(1))
    def forward(self,input_ids,attention_mask=None,use_cache=False,
                return_dict=True,output_hidden_states=True):
        h=torch.zeros((1,len(input_ids[0]),4))
        src=int(input_ids[0,0]); last=int(input_ids[0,-1])
        if last in (Tokenizer.vocab['a'],Tokenizer.vocab['b']):h[0,-1,2]=1
        elif src==Tokenizer.vocab['P']:h[0,-1,0]=1
        else:h[0,-1,1]=1
        return SimpleNamespace(last_hidden_state=h,
              hidden_states=(h,h*.95,h*1.05,h))


class Backbone(torch.nn.Module):
    def __init__(self):
        super().__init__();self.decoder=Decoder();self.head=torch.nn.Linear(4,len(Tokenizer.chars),bias=False)
        self.head.weight.data.zero_()
        self.head.weight.data[Tokenizer.vocab['b'],0]=2.
        self.head.weight.data[Tokenizer.vocab['a'],1]=2.
        self.head.weight.data[Tokenizer.vocab['\n'],2]=2.
    def get_decoder(self):return self.decoder
    def get_output_embeddings(self):return self.head


def args():
    return SimpleNamespace(device='cpu',max_new_tokens=4,max_tokens=50,
          expected_failures=1,progress_every=0,match_threshold=.99999,
          max_passes=2,max_token_fixes_per_case=4,token_train_steps=60,
          lr=.3,logit_margin=1.,l2=0.,grad_clip=10.,checkpoint_every=1,harvest_checkpoint_every=1)


def corpus():
    return [{'id':'p','source':'P','target':'a','rung':0,'residual':0},
            {'id':'q','source':'Q','target':'a','rung':0,'residual':1}]


def test_no_oracle_at_inference_and_zero_default():
    bb=Backbone();tok=Tokenizer();a=args()
    glass=f.FailureModeGlass(4,device='cpu')
    assert f.decode(bb,tok,'P',a,glass)['output']=='b\n'
    assert f.decode(bb,tok,'Q',a,glass)['output']=='a\n'
    state=f.decode(bb,tok,'P',a,expected_ids=tok('a\n')['input_ids'])['first']
    assert state['gold']==Tokenizer.vocab['a']
    mode,created=glass.add(state['feature'])
    assert created and mode==0
    assert torch.count_nonzero(glass.corrections[0])==0
    r=f.train_one_token(bb.head,glass,mode,state['h'],state['gold'],a)
    assert r['ok'],r
    # Inference gets ONLY the prompt, frozen weights and learned glass.
    assert f.decode(bb,tok,'P',a,glass)['output']=='a\n'
    assert f.decode(bb,tok,'Q',a,glass)['output']=='a\n'
    assert f.decode(bb,tok,'P',a)['output']=='b\n'
    restored=f.FailureModeGlass.restore(glass.state())
    assert f.decode(bb,tok,'P',a,restored)['output']=='a\n'


def test_harvest_train_and_audit_only_known_failure(tmp_path,monkeypatch):
    monkeypatch.setattr(f.base.ladder,'correctness',
                        lambda source,target,output:(output.strip()==target,None))
    bb=Backbone();tok=Tokenizer();a=args();rows=corpus()
    h=f.harvest(bb,tok,rows,a,tmp_path,{'test':1})
    assert h['failure_count']==1
    assert h['failures'][0]['source']=='P'
    assert h['failures'][0]['baseline_output']=='b\n'
    assert (tmp_path/'known_failure_prompts.txt').read_text()=='P\n'
    frozen=[p.detach().clone() for p in bb.parameters()]
    glass=f.FailureModeGlass(4,device='cpu')
    glass,history=f.fit(bb,tok,bb.head,glass,rows,a,tmp_path,{'test':1})
    assert history[-1]['remaining']==0,history
    aud=f.audit(bb,tok,rows,glass,a,tmp_path,{'test':1})
    assert aud['passed']==2 and aud['failed']==0 and aud['new_errors']==0
    assert all(torch.equal(before,p) for before,p in zip(frozen,bb.parameters()))


def test_mode_activation_needs_qwen_signature_not_prompt_string():
    glass=f.FailureModeGlass(4,device='cpu')
    a=torch.tensor([1.,0.,0.,0.]);b=torch.tensor([0.,1.,0.,0.])
    mode,_=glass.add(a)
    with torch.no_grad():glass.corrections[mode][0]=2.
    h=torch.zeros(1,4)
    corrected,hit=glass.apply(h,a)
    untouched,miss=glass.apply(h,b)
    assert hit==0 and miss==-1
    assert corrected[0,0].item()==2. and torch.equal(untouched,h)


def test_conflicting_activation_is_reportable_not_silently_disambiguated():
    glass=f.FailureModeGlass(4,device='cpu')
    first,_=glass.add(torch.tensor([1.,0.,0.,0.]))
    second,created=glass.add(torch.tensor([1.,0.,0.,0.]))
    assert first==second and not created


def test_repairs_real_greedy_first_divergence_then_next_token():
    """A correction can expose a SECOND wrong token; fix that new state too."""
    class CascadingDecoder(torch.nn.Module):
        def forward(self,input_ids,attention_mask=None,use_cache=False,
                    return_dict=True,output_hidden_states=True):
            h=torch.zeros((1,input_ids.shape[1],4))
            step=input_ids.shape[1]-2  # initial 'P\n', then generated chars
            h[0,-1,min(step,2)]=1.
            return SimpleNamespace(last_hidden_state=h,hidden_states=(h,h,h,h))
    bb=Backbone();bb.decoder=CascadingDecoder()
    with torch.no_grad():
        bb.head.weight.zero_()
        bb.head.weight[Tokenizer.vocab['b'],0]=2.
        bb.head.weight[Tokenizer.vocab['b'],1]=2.
        bb.head.weight[Tokenizer.vocab['\n'],2]=2.
        bb.head.weight[Tokenizer.vocab['a'],3]=2.
    tok=Tokenizer();a=args();glass=f.FailureModeGlass(4,device='cpu')
    original=f.decode(bb,tok,'P',a)['output']
    assert original=='bb\n'
    original_weights=bb.head.weight.detach().clone()
    expected=tok('aa\n')['input_ids']
    steps=[]
    for _ in range(3):
        trace=f.decode(bb,tok,'P',a,glass,expected_ids=expected)
        if trace['output']=='aa\n':break
        fault=trace['first']
        assert fault is not None
        steps.append(fault['step'])
        mode,_=glass.add(fault['feature'])
        assert f.train_one_token(bb.head,glass,mode,fault['h'],fault['gold'],a)['ok']
    assert steps==[0,1]
    assert f.decode(bb,tok,'P',a,glass)['output']=='aa\n'
    assert f.decode(bb,tok,'P',a)['output']=='bb\n'
    assert torch.equal(bb.head.weight,original_weights)


def test_harvest_resume_and_training_idempotence(tmp_path,monkeypatch):
    monkeypatch.setattr(f.base.ladder,'correctness',
                        lambda source,target,output:(output.strip()==target,None))
    bb=Backbone();tok=Tokenizer();a=args();rows=corpus();lineage={'test':2}
    first={'id':'p','source':'P','target':'a','baseline_output':'b\n',
           'rung':0,'residual':0}
    (tmp_path/'harvest_partial.json').write_text(json.dumps({
        'schema':f.SCHEMA,'lineage':lineage,'cases':2,
        'next_index':1,'failures':[first]}))
    h=f.harvest(bb,tok,rows,a,tmp_path,lineage)
    assert h['failure_count']==1
    assert not (tmp_path/'harvest_partial.json').exists()
    glass=f.FailureModeGlass(4,device='cpu')
    g,history=f.fit(bb,tok,bb.head,glass,rows,a,tmp_path,lineage)
    assert history[-1]['remaining']==0
    before=g.state()['corrections'].clone()
    g2,history2=f.fit(bb,tok,bb.head,f.FailureModeGlass(4),rows,a,tmp_path,lineage)
    assert history==history2
    assert torch.equal(before,g2.state()['corrections'])
