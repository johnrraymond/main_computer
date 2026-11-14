"""Answer-free teacher-consumption controls: no model download, no optimizer."""
import json
import math
import pytest
import torch

from tools import nanojev_qwen_hybrid_consumption_diagnostic as diag
from tools import nanojev_qwen_hybrid_alignment_repair as align
from tools import nanojev_qwen_hybrid_interface_repair as interface
from tools import nanojev_qwen_zero_line_backbone as zero
from tools import nanojev_qwen_hybridagent_format_activation as fmt
from test_nanojev_qwen_hybrid_interface_repair import loader, sources


def test_teacher_control_is_exactly_format_teacher_thought_without_answer():
    model,tok,_=loader()
    for row in zero.make_cases(71,6,6,6)['test']:
        thought=diag.answer_free_thought(row)
        assert not any(ch.isdigit() for ch in thought)
        form=fmt.encode_format(tok,model,row,512)
        assert diag.teacher_tokens(tok,row)==form['ids'][form['entry']+1:form['exit']]
    assert diag.answer_free_thought({'family':'direct','answer':800})==diag.answer_free_thought({'family':'direct','answer':-700})
    with pytest.raises(AssertionError,match='CONSUMPTION_UNKNOWN_FAMILY'):
        diag.answer_free_thought({'family':'bad'})


def test_all_interventions_preserve_model_and_slot_counts():
    model,tok,_=loader()
    model.eval()
    row=zero.make_cases(71,6,6,6)['test'][0]
    inp=torch.tensor([tok('Question: Compute '+row['expression']+'.\nReasoning: ',add_special_tokens=False)['input_ids']])
    prior=model.head
    with torch.inference_mode():
        for cond in diag.CONDITIONS:
            logits,cache,mask,n=diag._prefix(model,tok,inp,cond,row)
            assert logits.shape[-1]==256
            assert cache is not None and mask.shape[1]==cache[0]
            assert n==0 if cond in ('text','marker_only') else n>=1
            generated=diag._generate_from_prefix(model,tok,logits,cache,mask,2)
            assert generated['tokens']<=2
            score,gold=diag._first_answer_logprob(model,tok,inp,cond,row)
            assert math.isfinite(score) and isinstance(gold,int)
    assert model.head is prior


def test_identical_teacher_one_across_answers_is_not_mistaken_for_reasoning_signal():
    model,tok,_=loader()
    cases=zero.make_cases(71,6,6,6)['test']
    firsts={diag.teacher_tokens(tok,row)[0] for row in cases}
    assert len(firsts)==1


def test_summary_preserves_negative_results_and_cautions():
    records=[]
    for i in range(6):
        variants={k:{'answer':0 if k=='intact' else 5,
                     'parseable':True,'first_answer_logprob':-1.2} for k in diag.CONDITIONS}
        records.append({'expected':5,'variants':variants,
                        'geometry':{'cosine_to_teacher_one':0.8,'teacher_first_token_id':10}})
    s=diag.summarize(records)
    assert s['score']['intact']['correct']==0
    assert s['score']['teacher_one']['correct']==6
    assert s['evidence']['teacher_first_token_unique_ids']==1
    assert 'head generation' in s['interpretation']
    with pytest.raises(AssertionError,match='CONSUMPTION_MISSING_CONDITIONS'):
        diag.summarize([])


def test_output_overlap_and_preflight_fail_closed(tmp_path):
    src=tmp_path/'existing';src.mkdir()
    with pytest.raises(AssertionError,match='CONSUMPTION_OUTPUT_SOURCE_OVERLAP'):
        diag.run(base=src,numerical=tmp_path/'b',interface_dir=tmp_path/'i',
                 repair_root=tmp_path/'r',output=src/'child',device='cpu')
    with pytest.raises((FileNotFoundError,AssertionError)):
        diag._preflight(tmp_path/'missing',tmp_path/'missing2')


def test_full_checkpoint_path_read_only(tmp_path,monkeypatch):
    """Actual format/alignment/checkpoint restore and output; no fake tool output."""
    def mockload(**kwargs):
        model,tok,details=loader(**kwargs)
        with torch.no_grad():model.backbone.get_input_embeddings().weight.mul_(0.03)
        return model,tok,details
    base,num,initial=sources(tmp_path,monkeypatch)
    inter=tmp_path/'interface'
    interface.train_format(device='cpu',base=base,numerical=num,initial_head=initial,
       output=inter,steps=1,eval_every=1,train_count=6,dev_count=6,test_count=6,
       max_seq=512,loader=mockload)
    interface.realign(device='cpu',base=base,numerical=num,output=inter,
       steps=1,eval_every=1,max_seq=512,loader=mockload)
    f=inter/'format'/'best.pt';h=inter/'align'/'best.pt'
    hashes={'format':interface.fingerprint(f),'head':interface.fingerprint(h)}
    zero.atomic_json(inter/'gate_result.json',{'schema':interface.SCHEMA,
       'marker_checkpoint_roundtrip':True,'tied_output_marker_override':True,
       'markers_pass':True,'format_checkpoint_sha256':hashes['format'],
       'aligned_head_sha256':hashes['head']})
    root=tmp_path/'repair'
    assert align.run(stage='calibrate',device='cpu',base=base,numerical=num,
        interface_dir=inter,output=root,loader=mockload)==0
    assert align.run(stage='train',device='cpu',base=base,numerical=num,
        interface_dir=inter,output=root,loader=mockload,steps=2,eval_every=1)==0
    sources_now=align._sources(inter)
    best=interface.fingerprint(root/'alignment'/'best.pt')
    zero.atomic_json(root/'recurrence.json',{'schema':align.SCHEMA,'pass':True,
        'repair_head_sha256':best,'source_checkpoints':sources_now})
    zero.atomic_json(root/'geometry_gate.json',{'schema':align.SCHEMA,'pass':True})
    zero.atomic_json(root/'gate_result.json',{'schema':align.SCHEMA,'sources':sources_now,
        'new_head_checkpoint_sha256':best,'status':'ONE_STEP_FAIL'})
    out=tmp_path/'new_consumption'
    res=diag.run(device='cpu',base=base,numerical=num,interface_dir=inter,
        repair_root=root,output=out,cases=6,budget=2,loader=mockload)
    assert res['source_mutations']==res['optimizer_steps']==0
    assert res['teacher_references_answer_free']
    assert res['oracle_gold_answer_used_as_model_input'] is False
    assert len(res['records'])==6
    assert (out/'result.json').is_file()
    assert set(res['score'])==set(diag.CONDITIONS)
    assert interface.fingerprint(root/'alignment'/'best.pt')==best
    assert interface.fingerprint(f)==hashes['format']
    with pytest.raises(AssertionError,match='CONSUMPTION_OUTPUT_EXISTS'):
        diag.run(device='cpu',base=base,numerical=num,interface_dir=inter,
                 repair_root=root,output=out,cases=6,budget=2,loader=mockload)
    (root/'recurrence.json').write_text('{"pass":false}')
    with pytest.raises((KeyError,AssertionError)):
        diag._preflight(inter,root)
