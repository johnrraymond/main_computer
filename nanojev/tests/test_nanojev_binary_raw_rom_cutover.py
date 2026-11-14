"""CPU/SQLite contract tests for the original structured-ROM binary cutover."""
from __future__ import annotations
import json
from pathlib import Path
import sqlite3
import sys

import pytest

TOOLS=Path(__file__).resolve().parents[1]/'tools'
sys.path.insert(0,str(TOOLS))
from nanojev_binary_consensus_runtime import load_bank as nl_load_bank, shard_examples, TRAINING_POLICY
from nanojev_binary_raw_rom_bank import create, load_raw_bank, RAW_VARIANTS, raw_question
from nanojev_binary_raw_rom_continuous_train import train, parse as train_parse, DEFAULT_OUTPUT, SCHEMA
from nanojev_binary_raw_rom_probe import run as probe_run,parse as probe_parse
from nanojev_training_task_store import TrainingTaskStore,sha


def fixture_source(tmp_path):
    bank=tmp_path/'original'
    bank.mkdir()
    with TrainingTaskStore(bank/'training-tasks.sqlite') as st:
        for split in ('train','dev','predev'):
            cid='cohort:'+split
            st.add_cohort(cid,family='clef_binary_consensus',split=split,
                          source={'sourceModel':'Qwen/Qwen3-0.6B','sourceRevision':'pinned-revision'})
            for topo in ('a','b','c','none'):
                programs={letter:('x = 2' if letter==topo else 'x = 1') for letter in 'abc'}
                for pair in ('ab','ac','bc'):
                    a,b=pair
                    label='SAME' if (topo=='none' or topo not in pair) else 'DIFFERENT'
                    idx=0 if label=='SAME' else 1
                    def q(orientation):
                        first,second=(a,b) if not orientation else (b,a)
                        prompt=(f'Program A:\n```python\n{programs[first]}\n```\n'
                                f'Program B:\n```python\n{programs[second]}\n```\n'
                                'Determine normalized Python AST equivalence.\nAnswer:')
                        return {'question_id':f'{split}:{topo}:{pair}:{orientation}',
                                'task':'consensus_pairwise','gold_index':idx,
                                'stratum':'','candidates':[{'candidate_id':ans.lower(),
                                    'paths':[{'prompt':prompt,'answer':' '+ans}]}
                                    for ans in ('SAME','DIFFERENT')]}
                    v={'natural':q(False),'natural_reverse':q(True),
                       'natural_rom':q(False),'natural_rom_reverse':q(True)}
                    st.put(cohort_id=cid,family='clef_binary_consensus',task='consensus_pairwise',
                           split=split,fingerprint=sha([split,topo,pair]),
                           oracle='normalized-python-ast-equivalence-v1',label_status='verified',
                           target={'label':label,'goldIndex':idx,'pair':pair,'sourceTopology':topo},
                           provenance={'parentFingerprint':f'{split}:{topo}'},variants=v)
        st.conn.commit()
    (bank/'manifest.json').write_text(json.dumps({'ok':True,'binaryConsensusOnly':True,
        'schema':'nanojev.training-task-generator.v1'}),encoding='utf-8')
    return bank


def test_raw_rom_offline_cutover_and_probe_dry_run(tmp_path,capsys):
    src=fixture_source(tmp_path)
    dest=tmp_path/'raw'
    emitted=create(source_bank=src,output_dir=dest)
    assert emitted['ok'] and emitted['groups']==36 and emitted['presentations']==72
    result=load_raw_bank(dest,split='train')
    assert result['count']==12
    from nanojev_binary_consensus_runtime import deps
    _,rom,_=deps()
    assert len(shard_examples(result['groups'],22,0,0,variants=RAW_VARIANTS))==24
    for _,g in result['groups']:
        assert set(g['variants'])==set(RAW_VARIANTS)
        for variant,q in g['variants'].items():
            prompt=q['candidates'][0]['paths'][0]['prompt']
            assert prompt.endswith('\nROM_OUTPUT:')
            assert 'Begin a consensus' not in prompt
            payload=json.loads(prompt[:-len('\nROM_OUTPUT:')])
            assert payload['task']=='triad'
            assert payload['template_id']==rom.rom_template_id('triad')
            assert payload['decision'] is None
            assert q['candidates'][0]['paths'][0]['answer']==' '+rom.canonical_json({'ast_equivalence_relation':'SAME'})
            assert q['gold_index']==(0 if g['label']=='SAME' else 1)
    args=train_parse(['--bank-dir',str(dest),'--output-dir',str(tmp_path/'run'),'--dry-run'])
    train(args)
    plan=json.loads(capsys.readouterr().out)
    assert plan['ok'] and plan['config']['variant']=='raw_rom-both-orientations'
    assert plan['config']['training_policy']==TRAINING_POLICY
    assert plan['examples_per_pass']==24 and not plan['accuracy_computed']
    assert 'raw_rom' in str(DEFAULT_OUTPUT) and SCHEMA.endswith('v1')
    probe_run(probe_parse(['--bank-dir',str(dest),'--champion-only','--dry-run','--split','dev']))
    probe=json.loads(capsys.readouterr().out)
    assert probe['ok'] and probe['presentations']==24
    assert probe['no_training'] is True  # must remain probe-only
    with pytest.raises(FileExistsError):
        create(source_bank=src,output_dir=dest)


def test_raw_rom_oracle_rejects_mislabeled_input(tmp_path):
    src=fixture_source(tmp_path)
    with sqlite3.connect(src/'training-tasks.sqlite') as conn:
        r=conn.execute("SELECT p.presentation_id,p.sample_json FROM presentations p JOIN task_groups g USING(group_id) WHERE p.variant='natural' AND g.split='train' AND json_extract(g.target_json, '$.label')='SAME' LIMIT 1").fetchone()
        obj=json.loads(r[1]);obj['candidates'][0]['paths'][0]['prompt']=obj['candidates'][0]['paths'][0]['prompt'].replace('x = 1','x = 3',1)
        # Re-seal this malicious presentation to make the AST oracle—not the hash—the detection layer.
        from nanojev_training_task_store import canonical
        import hashlib
        txt=canonical(obj)
        # both candidates must agree, otherwise prompt drift protects the bank first.
        obj['candidates'][1]['paths'][0]['prompt']=obj['candidates'][0]['paths'][0]['prompt']
        txt=canonical(obj)
        conn.execute('UPDATE presentations SET sample_json=?,sample_sha256=? WHERE presentation_id=?',
            (txt,hashlib.sha256(txt.encode()).hexdigest(),r[0]))
    with pytest.raises(RuntimeError,match='RAW_ROM_AST_ORACLE_MISMATCH'):
        create(source_bank=src,output_dir=tmp_path/'raw')


def test_missing_source_orientation_and_probe_training_split_forbidden(tmp_path):
    src=fixture_source(tmp_path)
    with sqlite3.connect(src/'training-tasks.sqlite') as conn:
        conn.execute("DELETE FROM presentations WHERE presentation_id IN (SELECT presentation_id FROM presentations WHERE variant='natural_reverse' LIMIT 1)")
    with pytest.raises(RuntimeError,match='RAW_ROM_SOURCE_ORIENTATION_MISSING'):
        create(source_bank=src,output_dir=tmp_path/'raw')
    with pytest.raises(SystemExit):
        probe_parse(['--split','train'])


def test_previous_natural_rom_policy_is_untouched(tmp_path):
    src=fixture_source(tmp_path)
    b=nl_load_bank(src,split='train')
    assert b['count']==12
    assert set(b['groups'][0][1]['variants']) == {'natural_rom','natural_rom_reverse'}
    from nanojev_binary_consensus_continuous_train import parse
    assert parse([]).output_dir != train_parse([]).output_dir
