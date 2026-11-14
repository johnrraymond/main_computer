"""Offline LM-head packet control tests; no Qwen download or real GPU required."""
from types import SimpleNamespace

import pytest
import torch
from torch import nn

from tools import nanojev_hybridlink_lm_packet_probe as packet


class ToyTokenizer:
    eos_token_id=255
    def __call__(self,text,add_special_tokens=False):
        return {'input_ids':[ord(c) for c in text]}
    def decode(self,ids,skip_special_tokens=True):
        return ''.join(chr(x) for x in ids if x!=255)


class ToyDecoder(nn.Module):
    def forward(self,inputs_embeds=None,attention_mask=None,return_dict=True,use_cache=False):
        assert inputs_embeds is not None and use_cache is False
        return SimpleNamespace(last_hidden_state=inputs_embeds)


class ToyQwen(nn.Module):
    def __init__(self):
        super().__init__()
        self.embedding=nn.Embedding(256,256)
        self.decoder=ToyDecoder()
        self.head=nn.Linear(256,256,bias=False)
        with torch.no_grad():
            self.embedding.weight.copy_(torch.eye(256))
            self.head.weight.zero_()
            # Deliberately deterministic greedy terminal packet from preceding
            # newline, then after label a signed integer, then newline.
            sequence='\nANSWER: -8\n'
            for before,after in zip(sequence,sequence[1:]):
                self.head.weight[ord(after),ord(before)]=20
    def get_decoder(self):return self.decoder
    def get_input_embeddings(self):return self.embedding
    def get_output_embeddings(self):return self.head


def test_lm_head_finishes_unprompted_full_response_packet():
    result=packet.completion(ToyQwen(),ToyTokenizer(),'cpu',64,32,text='PROBLEM: 1+2\n')
    assert result['status']=='observed'
    assert result['completion']=='ANSWER: -8'
    assert result['stop']=='answer_line'
    assert packet.parse_answer(result['completion'],'qwen_free')['number']==-8


def test_prefilled_head_emits_only_number_without_being_given_target():
    model=ToyQwen()
    result=packet.completion(model,ToyTokenizer(),'cpu',64,12,text='PROBLEM: 1+2\nANSWER: ',stop_after_first_line=True)
    assert result['completion']=='-8'
    assert packet.parse_answer(result['completion'],'qwen_prefilled')['number']==-8
    assert packet.parse_answer(result['completion'],'hybrid_prefilled')['number']==-8


def test_lm_head_reads_generated_latent_hidden_directly_first():
    model=ToyQwen();tok=ToyTokenizer()
    # The terminal latent is a Qwen-shaped tensor; first token must be read
    # directly from it rather than recomputing a different hidden state.
    h=model.get_input_embeddings()(torch.tensor([[ord('\n')]]))[:,-1,:]
    source=model.get_input_embeddings()(torch.tensor([[ord('X')]]))
    result=packet.completion(model,tok,'cpu',64,32,embeddings=source,first_hidden=h)
    assert result['completion']=='ANSWER: -8'


def test_answer_packet_parsing_is_strict_and_distinguishes_protocols():
    for text in ('ANSWER: -8\n','ANSWER: -8'):
        assert packet.parse_answer(text,'qwen_free')['number']==-8
    assert packet.parse_answer('ANSWER: -8','qwen_prefilled')['valid_packet'] is False
    assert packet.parse_answer('-8','hybrid_free')['valid_packet'] is False
    assert packet.parse_answer('-8','hybrid_prefilled')['number']==-8
    for invalid in ('ANSWER: -8 garbage','ANSWER: -8+0','ANSWER: 3.5','ANSWER: 0 1','unknown'):
        assert packet.parse_answer(invalid,'qwen_free')['valid_packet'] is False
    assert packet.parse_answer('ANSWER: 7\nANSWER: 8','qwen_free')['valid_packet'] is False
    assert packet.parse_answer('PROBLEM: 3+4\nANSWER: 7','qwen_free')['number']==7
    assert packet.parse_answer('','qwen_free')['valid_packet'] is False


def test_missing_lm_head_and_context_fail_closed():
    model=ToyQwen();tok=ToyTokenizer()
    model.get_output_embeddings=lambda:None
    assert packet.completion(model,tok,'cpu',100,10,text='PROBLEM: 1+2')['status']=='lm_head_unavailable'
    assert packet.completion(model,tok,'cpu',4,10,text='PROBLEM: 1+2')['status']=='lm_head_unavailable'
    model.get_output_embeddings=lambda:model.head
    assert packet.completion(model,tok,'cpu',4,10,text='PROBLEM: 1+2')['status']=='context_exhausted'


def test_modes_and_synthetic_cases_inherit_bounded_zero_line_deck(monkeypatch):
    from tools import nanojev_qwen_numeric_dual_cutover as parent
    monkeypatch.setattr(parent,'load_parent',lambda args: ({'deck_sha256':'fixture'},{}))
    args=SimpleNamespace(generate_zero_line=True,max_rung=8,radius=8,
         operand_limit=1024,generated_per_cell=1,seed=42257,
         lm_packet_cases_per_rung=0,split='dev')
    rows,fingerprint,origin=packet._cases(args)
    assert len(rows)==459 and origin=='synthetic_diagnostic_only'
    assert len(fingerprint)==64
    assert all(r['full_chain'] for r in rows)
    assert {r['answer'] for r in rows if r['rung']==8}==set(range(-8,9))
    args.lm_packet_cases_per_rung=2
    small,_,_=packet._cases(args)
    assert len(small)==18 and all(sum(x['rung']==r for x in small)==2 for r in range(9))


def test_cli_supports_packet_stage_and_reports_modes(monkeypatch):
    from tools import nanojev_hybridlink_latent_diagnostics as diag
    monkeypatch.setattr(packet,'run',lambda args: {'stage':'lm-packet','modes':args.lm_packet_modes})
    args=diag.main # avoid calling argparse by using mock object for dispatch
    result=diag.run(SimpleNamespace(stage='lm-packet',lm_packet_modes='qwen_free'))
    assert result=={'stage':'lm-packet','modes':'qwen_free'}


def test_marker_is_explicit_counterfactual_not_hidden_label():
    model=ToyQwen();tok=ToyTokenizer()
    emb=model.get_input_embeddings()(torch.tensor([[ord('X')]]))
    marked=packet._append_marker(model,tok,emb,'cpu')
    assert marked.shape[1]==1+len('\nANSWER: ')
    assert not hasattr(packet,'arithmetic_executor')


def test_integrated_packet_stage_uses_verified_hybrid_weights_and_writes_csv(tmp_path,monkeypatch):
    from tools import nanojev_hybridlink_t00_curriculum_repair as t00
    from tools import nanojev_hybridlink_frontier_repair as frontier
    from tools import nanojev_qwen_numeric_dual_cutover as parent
    from tools import nanojev_hybridlink_latent_loop_cutover as loop
    model=ToyQwen();model.config=SimpleNamespace(hidden_size=256)
    tok=ToyTokenizer()
    det=nn.Identity()
    cfg={'schema':'fixture'}
    monkeypatch.setattr(parent,'load_parent',lambda args: (cfg,{}))
    captured={}
    def prepare(args):
        args.checkpoint=args.detector_checkpoint
        return {'bb':model,'tok':tok},None,det,{}
    def verify(path,args,prepared):
        captured['path']=path
        assert prepared['det'] is det
        return {'link':{}}
    monkeypatch.setattr(t00,'_prepare',prepare)
    monkeypatch.setattr(frontier,'verify_input',verify)
    monkeypatch.setattr(loop,'make_link',lambda hidden,embed:nn.Identity())
    monkeypatch.setattr(loop,'stopping_action',lambda *values:('ANSWER',0,{'relative_drift':1.0}))
    args=SimpleNamespace(stage='lm-packet',split='dev',device='cpu',
         stage1_checkpoint=str(tmp_path/'stage1'/'best.pt'),
         text_checkpoint=str(tmp_path/'text'/'best.pt'),
         detector_checkpoint=str(tmp_path/'detector'/'best.pt'),
         checkpoint=str(tmp_path/'active'/'pending.pt'),
         output=str(tmp_path/'output'),cache_dir=None,
         max_rung=0,radius=0,operand_limit=1024,generated_per_cell=1,
         generate_zero_line=True,seed=42257,max_cases_per_rung=24,
         lm_packet_cases_per_rung=0,lm_packet_max_new_tokens=22,
         lm_packet_modes='qwen_free,qwen_prefilled,hybrid_free,hybrid_prefilled',
         min_per_cell=1,max_tokens=192,min_gold_cosine=.60,
         answer_threshold=.9,drift_threshold=1e-5,stall_patience=2,
         overrun_steps=1,qwen_text_probe_per_rung=0,qwen_text_max_new_tokens=48)
    report=packet.run(args)
    assert captured['path']==str(tmp_path/'active'/'pending.pt')
    assert args.checkpoint==captured['path']
    assert report['no_training'] is True and len(report['cases'])==3
    assert report['target']['radius']==0
    assert report['per_rung']['0']['cases']==3
    assert report['cases'][0]['hybrid_exit']['status']=='ANSWER'
    assert (tmp_path/'output'/'lm_packet_cases.csv').exists()
    assert len((tmp_path/'output'/'lm_packet_cases.csv').read_text().splitlines())==13
    saved=__import__('json').loads((tmp_path/'output'/'lm_packet_report.json').read_text())
    assert len(saved['cases'])==3
    assert all('completion' in row for c in saved['cases'] for row in c['modes'].values())
    assert saved['evidence']['gold_never_fed_at_inference'] is True
