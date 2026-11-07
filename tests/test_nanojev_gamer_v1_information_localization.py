"""Strict train/test numerical-information localization acceptance tests."""
import json
import numpy as np
import pytest

from tools import nanojev_gamer_v1_information_localization as info
from tools import nanojev_gamer_v1_number_distance as dist


def synthetic(kind='curve',games=8,width=2048,seed=71):
    rng=np.random.default_rng(seed)
    values=np.arange(-8,9,dtype=np.int32)
    x=np.zeros((games,3,len(values),width),dtype=np.float32)
    pos=1*1024+10*64
    for game in range(games):
        for form in range(3):
            common=rng.normal(size=width).astype(np.float32)
            phase=(game*0.67+form*.8)
            arbitrary=rng.normal(size=(17,width)).astype(np.float32)*.2
            for vi,n in enumerate(values):
                z=common+arbitrary[vi]
                if kind=='curve':
                    z[pos] += 32*np.cos(.13*n+phase)
                    z[pos+1] += 32*np.sin(.13*n+phase)
                elif kind=='linear':z[pos]+=15*n
                elif kind=='lexical':pass
                elif kind=='constant':z=common.copy()
                else:raise ValueError(kind)
                x[game,form,vi]=z
    counts=np.full(x.shape[:3],38,dtype=np.int32)
    return x,counts,values,[f'game-{i}' for i in range(games)]


def test_raw_100_by_1024_layout_and_no_random_sketch():
    assert info._layout(102400)==(100,1024,'sampled_qwen_token')
    assert info._layout(4096)==(4,1024,'activation_block_not_token')
    assert info._layout(128)==(1,128,'activation_block_not_token')


def test_numeric_geometry_localizes_shared_curved_signal_across_games_words_signs():
    a,c,v,g=synthetic('curve')
    out=info.analyze_localization(a,c,v,g,folds=2,top=1,permutations=0,
                                arms=('unseen_games_values_words',))
    arm=out['arms']['unseen_games_values_words']
    assert arm['heldout_top1_ordering']>.95,arm
    assert set(f['top_blocks'][0] for f in arm['fold_details'])=={1},arm
    assert all(f['nested_top1_tile']==10 for f in arm['fold_details']),arm
    assert arm['heldout_nested_tile_ordering']>.95,arm


def test_lexical_noise_and_constant_cases_do_not_earn_information():
    for kind in ('lexical','constant'):
        a,c,v,g=synthetic(kind)
        out=info.analyze_localization(a,c,v,g,folds=2,top=1,permutations=0,
                                  arms=('unseen_games_values_words',))
        val=out['arms']['unseen_games_values_words']['heldout_top1_ordering']
        assert val<.75,(kind,val)


def test_unseen_zero_crossing_includes_token_count_mismatch_and_exact_spelling_controls():
    a,c,v,g=synthetic('curve')
    c[:,2,np.where(v==0)[0][0]]=100
    minus=a[:,0,np.where(v==0)[0][0]].copy()
    out=info.analyze_localization(a,c,v,g,folds=2,top=1,permutations=0,
                              minus_zero=minus,arms=('unseen_zero_crossing_words',))
    arm=out['arms']['unseen_zero_crossing_words']
    assert arm['heldout_top1_ordering']>.80,arm
    assert all(f['token_count_changed_test_pairs']>0 for f in arm['fold_details'])
    assert 'minus_zero' in out['zero_spelling_distances_on_selected_blocks']['by_spelling']


def test_permutation_control_and_outer_inner_no_overlap():
    a,c,v,g=synthetic('curve')
    assert {a for i,j,d,a,b in info._pairs(v,'outer','negative')}.isdisjoint(
           {a for i,j,d,a,b in info._pairs(v,'inner','negative')})
    out=info.analyze_localization(a,c,v,g,folds=2,top=1,permutations=19,
                             arms=('unseen_games_values_words',))
    r=out['arms']['unseen_games_values_words']
    assert r['heldout_top1_ordering']>.95
    assert r['permutation_p']==.05
    assert r['null_max_ordering']<r['heldout_top1_ordering']


def test_run_creates_sibling_and_does_not_modify_capture(tmp_path):
    a,c,v,g=synthetic('curve')
    src=tmp_path/'capture';src.mkdir()
    (src/'final_report.json').write_text(json.dumps({
       'schema':dist.SCHEMA,'status':'COMPLETE_READ_ONLY',
       'source_run':str(tmp_path/'source'),'expanded_run':str(tmp_path/'expanded'),
       'contexts':[{'game_id':x} for x in g],'checkpoint_step':500}))
    (src/'interventions.jsonl').write_text('synthetic\n')
    np.savez_compressed(src/'activations.npz',values=v,token_counts=c,
                        qwen_final_mean100=a)
    saved={p.name:p.read_bytes() for p in src.iterdir()}
    r=info.run_localization(src,stages=('qwen_final_mean100',),folds=2,top=1,permutations=0)
    assert r['status']=='COMPLETE_READ_ONLY'
    assert r['by_stage']['qwen_final_mean100']['arms']['unseen_games_values_words']['heldout_top1_ordering']>.95
    assert all(p.read_bytes()==saved[p.name] for p in src.iterdir())
    with pytest.raises(ValueError,match='OVERLAPS'):
        info.run_localization(src,output=src/'invalid',stages=('qwen_final_mean100',),folds=2)


def test_cli_registered_and_readonly():
    from tools.nanojev_gamer_v1_staircase import parser,main
    args=parser().parse_args(['head-100-localize-information','--run','X','--info-top','3'])
    assert args.info_top==3
    with pytest.raises(ValueError,match='READ_ONLY'):
        main(['head-100-localize-information','--run','X','--steps','1'])
    with pytest.raises(ValueError,match='ONLY_LOCALIZE_INFORMATION'):
        main(['head-100-number-distance','--run','X','--info-top','3'])


def test_actual_100_by_1024_path_preserves_original_position_indices():
    values=np.arange(-8,9,dtype=np.int32)
    x=np.zeros((1,3,17,102400),dtype=np.float32)
    counts=np.full(x.shape[:3],20,dtype=np.int32)
    # At sampled position 99 only, encode numerical distance. This would be
    # invisible to any 256D slice of the beginning of the flattened input.
    for vi,n in enumerate(values):x[0,0,vi,99*1024+333]=float(n)
    records,blocks,kind=info._records(x,counts,values,[0],('digits',),('positive',),'outer')
    assert kind=='sampled_qwen_token'
    assert len(blocks)==100 and records[0][1].shape==(6,100)
    score=info._group_scores(records)[0]
    assert score[99]==1.0 and np.all(score[:99]==0.5)
    tiles,_,_=info._records(x,counts,values,[0],('digits',),('positive',),'outer',
                             selected_blocks=(99,),subtiles=True,tile_width=64)
    assert np.argmax(info._group_scores(tiles)[0])==5
