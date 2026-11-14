"""CPU-only contract tests for HDB measurement correction; no CUDA needed."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from tools import nanojev_qwen_hdb_balance as hdb
from tools import nanojev_qwen_hdb_difficulty as difficulty
from tools import nanojev_qwen_joint_frontier as joint
from tools import nanojev_qwen_lm_radius_ladder as ladder
from tools import nanojev_qwen_competency_diagnostic as competency


class CharacterTokenizer:
    def __call__(self, s, *, add_special_tokens=False):
        return {'input_ids':[ord(c) for c in s]}


def test_hdb_budget_is_based_on_target_not_old_fixed_48():
    tok=CharacterTokenizer()
    policy={'max_tokens':1024,'max_new_tokens':48}
    for tier in (5,6):
        row=hdb._row(tier,0,(9,1,3),label='heldout')
        budget,target_tokens=hdb._inference_budget(tok,row,policy)
        assert budget >= target_tokens + 16
        assert budget>48
        assert target_tokens==len(row['target']+'\n')
    row=hdb._row(8,0,(9,1,3),label='heldout')
    budget,_=hdb._inference_budget(tok,row,policy)
    assert budget>=48
    with pytest.raises(ValueError,match='HDB_DIAGNOSTIC_CONTEXT_TOO_SHORT'):
        hdb._inference_budget(tok,row,{'max_tokens':128,'max_new_tokens':48})


def test_length_safe_scoring_recovers_exact_prefix_and_captures_termination():
    tok=CharacterTokenizer()
    rows=[hdb._row(5,0,(9,1,3),label='heldout'),
          hdb._row(6,-1,(9,1,3),label='heldout'),
          hdb._row(7,0,(9,1,3),label='heldout')]
    budgets=[]
    def stub(bb,tokenizer,sources,device,context,new_tokens):
        budgets.append(new_tokens)
        matched=[next(r for r in rows if r['source']==s) for s in sources]
        result=[]
        for row in matched:
            if row['tier']=='HDB05':
                result.append({'text':row['target'],'finish_reason':'newline',
                               'generated_tokens':len(row['target'])+1})
            elif row['tier']=='HDB06':
                result.append({'text':row['target'][:-3], 'finish_reason':'length',
                               'generated_tokens':new_tokens})
            else:
                result.append({'text':row['target'][:-1], 'finish_reason':'eos',
                               'generated_tokens':2})
        return result
    a=SimpleNamespace(eval_batch=8,device='cpu',competency_progress_every=100)
    grades=hdb._score_hdb(None,tok,rows,a,{'max_tokens':1024,'max_new_tokens':48},greedy=stub)
    good=grades[competency._digest(rows[0]['source'])]
    truncated=grades[competency._digest(rows[1]['source'])]
    eos=grades[competency._digest(rows[2]['source'])]
    assert good['correct'] and good['finish_reason']=='newline'
    assert not truncated['correct'] and truncated['generation_truncated'] and truncated['exact_prefix']
    assert not eos['correct'] and eos['premature_eos']
    assert max(budgets)>48
    summary,errors=difficulty.analyze_hdb(rows,grades,grades)
    assert summary['failure_types']=={'generation_truncated':1,'premature_eos':1}
    assert {e['failure_type'] for e in errors}=={'generation_truncated','premature_eos'}
    assert all('output_budget' in e and 'finish_reason' in e for e in errors)


def test_training_targets_checked_without_training_or_heldout_leakage():
    tok=CharacterTokenizer()
    integrity=joint._training_target_integrity(tok,{'max_seq':768,
                     'hdb':{'max_offset':64}})
    assert integrity['training_target_not_truncated_by_batch_loss']
    assert integrity['rows_checked']==9*(len(hdb.TRAIN_COMMON)*len(hdb.TRAIN_SLOPES)-1)*3
    assert integrity['max_target_tokens']>48
    assert integrity['max_combined_tokens']<=768
    with pytest.raises(RuntimeError,match='HDB_TRAINING_TARGET_INTEGRITY_FAILED'):
        joint._training_target_integrity(tok,{'max_seq':80,'hdb':{'max_offset':64}})


def test_rescore_does_not_overwrite_training_state_or_historical_reports(tmp_path,monkeypatch):
    torch=pytest.importorskip('torch')
    out=tmp_path/'joint';out.mkdir()
    rows=[hdb._row(i,0,(9,1,3),label='heldout') for i in range(9)]
    ladder._atomic_json(out/'hdb_eval_deck.json',rows)
    state={'step':2400,'last_checked_step':2400,'policy':{'hdb':{'max_tokens':1024,
            'max_new_tokens':48,'max_offset':64},'max_seq':768},'parent_sha256':'digest',
            'h_previous':{competency._digest(r['source']):{'correct':False}
                          for r in rows}}
    ladder._atomic_torch(out/'working.pt',{'schema':joint.SCHEMA,'state':state,
                                          'lora':{'w':torch.tensor(5.)},'optimizer':{'untouched':True}})
    (out/'progress.json').write_text('DO_NOT_REWRITE')
    (out/'joint_frontier_report.json').write_text('DO_NOT_REWRITE')
    before=(out/'working.pt').read_bytes()
    monkeypatch.setattr(joint,'_out',lambda args:out)
    monkeypatch.setattr(hdb,'_checkpoint',lambda args,policy:({'state':{}},'digest'))
    class Zero:
        @staticmethod
        def restore_lora(model,lora):assert float(lora['w'])==5
    class Model:
        def eval(self):pass
    monkeypatch.setattr(hdb,'_model',lambda args,parent:(Model(),CharacterTokenizer(),Zero(),[]))
    def stub(model,tok,rows,args,policy):
        return {competency._digest(r['source']):{'correct':True,'emitted':r['target'],
              'finish_reason':'newline','generation_truncated':False,
              'premature_eos':False,'exact_prefix':False} for r in rows}
    monkeypatch.setattr(hdb,'_score_hdb',stub)
    args=SimpleNamespace(eval_batch=4,device='cpu')
    summary=joint._rescore_hdb_checkpoint(args)
    assert summary['checkpoint_step']==2400
    assert summary['optimizer_steps_performed']==0
    assert summary['training_target_integrity']['training_target_not_truncated_by_batch_loss']
    assert summary['legacy_vs_new_outcomes']['previously_wrong_now_correct']==9
    assert (out/'working.pt').read_bytes()==before
    assert (out/'progress.json').read_text()=='DO_NOT_REWRITE'
    assert (out/'joint_frontier_report.json').read_text()=='DO_NOT_REWRITE'
    saved=json.loads((out/'hdb_rescore_report.json').read_text())
    assert saved['scoring_version']==joint.HDB_SCORING_VERSION
    assert sum(v['correct'] for v in saved['tiers'].values())==9
    assert (out/'hdb_rescore_failures.jsonl').read_text()==''
