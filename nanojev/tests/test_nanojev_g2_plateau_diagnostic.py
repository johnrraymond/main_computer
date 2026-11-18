"""G2-0063: frozen-checkpoint plateau probe tests, no real training or CUDA."""
import json
import math
from pathlib import Path

import pytest

from tools import nanojev_g2_plateau_diagnostic as d


def metrics(*, tp, gold, pred, graphs=400, reachable=384, exact=0):
    return {'true_positive':tp,'gold_edges':gold,'predicted_edges':pred,
            'missing':gold-tp,'extra':pred-tp,'graphs':graphs,
            'exact':exact,'candidate_reachable':reachable,
            'families':{},'modes':{}}


def test_target95_budget_and_exact_ceiling_distinguish_objectives():
    final=metrics(tp=11554,gold=14046,pred=15769)
    result=d.gap_to_f1_target(final,.95)
    assert result['current_edge_errors']==6707
    assert result['necessary_max_edge_errors']==1478
    assert result['necessary_error_reduction']==5229
    ceiling=d.candidate_exact_ceiling(final)
    assert ceiling['max_exact_graph_rate']==.96
    assert ceiling['target_exact_graphs']==380
    assert ceiling['necessary_reachable_exact_rate']==pytest.approx(380/384)
    assert ceiling['feasible_by_candidate_count'] is True
    assert d.candidate_exact_ceiling(metrics(tp=1,gold=2,pred=1,reachable=370))['feasible_by_candidate_count'] is False


def test_plateau_not_declared_when_fourth_epoch_accelerates():
    rows=[(0,5977,14399),(1225,10379,15143), # synthetic valid 14,046 gold
          (2450,10949,15635),(3675,11138,15689),(4900,11554,15769)]
    snap={'status':'HAS_OPTIMIZER_CHECKPOINTS',
          'history':[{'step':step,'validation':metrics(tp=tp,gold=14046,pred=pred)}
                    for step,tp,pred in rows]}
    result=d.classify_receipts(snap)
    assert result['status']=='LEARNING_WITH_LATEST_GAIN'
    assert result['current_f1']==pytest.approx(23108/29815)
    assert result['changes'][-1]['gain_f1_pp']>0
    message=d.format_receipt_diagnosis(result)
    assert 'cause is not identifiable from receipts' in message
    assert '95.00%' in message
    assert '6,707' in message


def test_receipts_mode_loads_only_committed_receipts(monkeypatch,tmp_path,capsys):
    m0=metrics(tp=5977,gold=14046,pred=14399)
    m1=metrics(tp=11554,gold=14046,pred=15769)
    for step,m in ((0,m0),(4900,m1)):
        (tmp_path/f'checkpoint-{step:09d}.json').write_text(json.dumps({'step':step,'validation':m}))
    # An uncommitted weight file must not be used or imported.
    (tmp_path/'checkpoint-000004901.safetensors').write_bytes(b'ignored')
    d.main(['--training-dir',str(tmp_path)])
    report=capsys.readouterr().out
    assert 'Step 4900' in report and '77.50%' in report
    assert '6,707' in report


def test_deep_slot_audit_matches_actual_decoder_without_gold_inference():
    import torch
    from tools import nanojev_gamer_v1_g2_literature_bakeoff_0055 as p55

    class Dummy(torch.nn.Module):
        def forward(self,sample):
            assert 'positive' not in sample and 'truth_count' not in sample
            rel=torch.full((p55.SLOTS,3),-9.)
            rel[:,0]=9.
            rel[0,0]=0.;rel[0,1]=9.
            rel[1,0]=0.;rel[1,1]=9.
            src=torch.zeros((p55.SLOTS,3));dst=torch.zeros((p55.SLOTS,3))
            src[0,0]=10.;dst[0,1]=10.
            src[1,1]=10.;dst[1,2]=10.
            return rel,src,dst

    sample={'src':torch.tensor([0,1,0]),'dst':torch.tensor([1,2,2]),
            'relation':torch.tensor([0,0,0]),'positive':torch.tensor([0,1]),
            'truth_count':2,'edge_count':3,'family':'allocation','mode':'renaming',
            'prompt_sha256':'a'*64}
    case=d._sample_case(Dummy(),sample,p55)
    assert case['exact']==1 and case['errors']==0
    assert case['decoder']['admitted_unique_edges']==2
    assert case['oracle_count_rank']['exact']==1
    assert case['admission_margin_sweep']['0.0']['tp']==2
    result=d._aggregate_cases([case])
    assert result['f1']==1.0
    assert result['admission_margin_sweep']['0.0']['f1']==1.0
    summary=d.format_deep_diagnosis({'relation_names':['owns'],
                                     'checkpoint_step':123,'split_audits':{'validation':result}})
    assert 'admission-margin sweep' in summary
    assert 'NOT a selected inference policy' in summary
