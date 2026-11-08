"""V30 regression: role-span accuracy does not imply numerical zero-line accuracy."""
from fractions import Fraction
import numpy as np
import pytest

from tools import nanojev_gamer_v1_zero_line_decoder_v30 as v30


class CharTok:
    def decode(self,ids,skip_special_tokens=False,clean_up_tokenization_spaces=False):
        return ''.join(chr(int(t)) for t in ids)


def test_same_actual_number_decoding_differently_exposes_zero_line_error():
    # Both source candidates contain the SAME token, '8' (ASCII 56).
    # Frozen contextual activations cause baseline to read the second as '9'.
    meta=[{'case_index':0,'start':0,'stop':1,'token_ids':[56]},
          {'case_index':0,'start':1,'stop':2,'token_ids':[56]}]
    vectors=np.asarray([[1.,0.],[0.,1.]],dtype=np.float32)
    frozen=([56,57],np.asarray([[1.,0.],[0.,1.]],dtype=np.float64))
    a=v30._selected(meta[0],vectors,frozen,CharTok())
    b=v30._selected(meta[1],vectors,frozen,CharTok())
    assert a['value']==Fraction(8) and b['value']==Fraction(9)
    assert a['token_exact'] and not b['token_exact']
    assert not a['oov'] and not b['oov']
    assert a['value']-b['value']==-1
    # Source truth is never substituted into either prediction.
    assert b['true_ids']==[56] and b['predicted_ids']==[57]


def test_blend_baseline_exact_at_zero_weight_and_can_add_train_known_class():
    base=([49,50],np.asarray([[1.,0.,0.],[0.,1.,0.]]))
    extra={49:np.asarray([0.,1.,0.]),51:np.asarray([0.,0.,1.])}
    vocab,centers=v30._blended(base,extra,0)
    assert vocab==base[0] and np.array_equal(centers,base[1])
    vocab,centers=v30._blended(base,extra,1)
    assert vocab==[49,50,51]
    assert np.argmax(centers[2])==2
    with pytest.raises(ValueError,match='V30_BAD_BLEND_ALPHA'):
        v30._blended(base,extra,-.01)


def test_train_only_selection_protects_acquired_correct_token_decoding():
    base=([49,50],np.asarray([[1.,0.],[0.,1.]]))
    # The proposed decoder would gain on the new corpus, but flip protected 49.
    extra={49:np.asarray([0.,1.]),50:np.asarray([1.,0.])}
    fit_x=np.asarray([[0.,1.],[0.,1.],[0.,1.]])
    fit_y=np.asarray([49,49,49])
    protect_x=np.asarray([[1.,0.]])
    protect_y=np.asarray([49])
    chosen,alpha,history=v30._choose(base,extra,(fit_x,fit_y),
        (protect_x,protect_y),alphas=(0.,1.))
    assert alpha==0
    assert history[-1]['protected_token_losses']==1
    assert not history[-1]['accepted']
    assert v30._predict_tokens(protect_x,chosen).tolist()==[49]


def test_train_only_decoder_can_accept_improvement_without_retention_loss():
    base=([49,50],np.asarray([[1.,0.,0.],[0.,1.,0.]]))
    extra={49:np.asarray([1.,0.,0.]),50:np.asarray([0.,1.,0.]),
           51:np.asarray([0.,0.,1.])}
    fit_x=np.asarray([[0.,0.,1.]])
    fit_y=np.asarray([51])
    protect_x=np.asarray([[1.,0.,0.],[0.,1.,0.]])
    protect_y=np.asarray([49,50])
    chosen,alpha,history=v30._choose(base,extra,(fit_x,fit_y),
        (protect_x,protect_y),alphas=(0.,.25,1.))
    assert alpha==.25
    assert history[1]['accepted']
    assert v30._predict_tokens(protect_x,chosen).tolist()==[49,50]
    assert v30._predict_tokens(fit_x,chosen).tolist()==[51]


def test_case_evaluation_uses_v29_selector_and_measures_decoder_separately(monkeypatch):
    from tools import nanojev_gamer_v1_selector_retention_training_v28 as v28
    original=v28._case_features
    def synthetic_cases(*args,**kwargs):
        return [{'case_index':0,'indices':[0,1],
                'base':np.array([[1.,0.],[0.,1.]]),
                'x':np.zeros((2,3)), 'truth':(0,1)}]
    monkeypatch.setattr(v28,'_case_features',synthetic_cases)
    try:
        entries=[{'id':'dev.offset.0003.zero.spine_difference::role_reordered',
                  'kind':'role_reordered','target':Fraction(0),
                  'prompt':'DC=8, CC=8.','_oracle':((3,4),(9,10))}]
        meta=[{'case_index':0,'start':0,'stop':1,'token_ids':[56]},
              {'case_index':0,'start':1,'stop':2,'token_ids':[56]}]
        v=np.array([[1.,0.],[0.,1.]],dtype=np.float32)
        baseline=([56,57],np.array([[1.,0.],[0.,1.]]))
        repaired=([56,57],np.array([[.7,.7],[.7,.7]]))
        # One-selected-pair case does not need the production selector shape.
        rows=v30._evaluation(entries,meta,v,{0:[0,1]},[(0,1)],
           np.zeros((2,2)),np.zeros((2,3)),baseline,repaired,CharTok(),[0],
           'stress_dev')
        assert rows[0]['role_spans_correct']==1
        assert rows[0]['zero_exact']==0
        assert rows[0]['both_operand_tokens_exact']==0
        assert rows[0]['target_sign']==0
        assert rows[0]['oov']==0
        assert rows[0]['first_prediction']=='8'
        assert rows[0]['second_prediction']=='9'
    finally:
        monkeypatch.setattr(v28,'_case_features',original)


def test_v30_requires_all_lineage_inputs_and_is_explicitly_nonproduction():
    flags={option for action in v30.parser()._actions for option in action.option_strings}
    assert {'--v27-run','--v28-run','--v29-run','--output','--case-id',
            '--tokenizer-path'}<=flags
    assert 'NO_PROMOTION' in v30.__doc__ or 'no production' in v30.__doc__.lower()
