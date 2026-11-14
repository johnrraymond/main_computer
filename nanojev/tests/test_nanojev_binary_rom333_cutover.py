"""ROM3.3.3 English-like compilation, model visibility, and shared training contracts."""
from __future__ import annotations
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import pytest

TOOLS=Path(__file__).resolve().parents[1]/'tools'
sys.path.insert(0,str(TOOLS))
from nanojev_binary_rom333_bank import (
    create, load_rom333_bank, transform, render, unpack, validate_sample,
    audit_token_visibility, VARIANTS, ANSWERS, SCHEMA,
)
from nanojev_binary_raw_rom_bank import create as create_prerom
from nanojev_binary_raw_rom_continuous_train import (
    parse as train_parse, train, DEFAULT_ROM333_CONTROL_OUTPUT, DEFAULT_ROM333_OUTPUT,
    DEFAULT_CONTROL_OUTPUT,
)
from nanojev_binary_raw_rom_probe import parse as probe_parse, run as probe_run
from nanojev_binary_consensus_runtime import deps, load_bank
from test_nanojev_binary_raw_rom_cutover import fixture_source


def fixtures(tmp_path):
    original=fixture_source(tmp_path)
    prerom=tmp_path/'prerom'
    create_prerom(source_bank=original,output_dir=prerom)
    compiled=tmp_path/'rom333'
    event=create(source_bank=prerom,output_dir=compiled)
    return original,prerom,compiled,event


def test_english_three_views_and_triple_verdict(tmp_path):
    _,_,compiled,event=fixtures(tmp_path)
    assert event['ok'] and event['groups']==36 and event['presentations']==72
    bank=load_rom333_bank(compiled,split='train')
    assert bank['count']==12
    for _,group in bank['groups']:
        assert set(group['variants'])==set(VARIANTS)
        forward=group['variants']['rom333']
        reverse=group['variants']['rom333_reverse']
        p=forward['candidates'][0]['paths'][0]['prompt']
        q=reverse['candidates'][0]['paths'][0]['prompt']
        a,b=unpack(p)
        assert (b,a)==unpack(q)
        assert p.startswith('ROM3.3.3\n')
        assert p.count('VIEW 1')==p.count('VIEW 2')==p.count('VIEW 3')==1
        assert 'PROGRAM A' in p and 'PROGRAM B' in p
        assert 'ROM_OUTPUT:' not in p and '"op"' not in p and 'COMMIT' not in p
        assert '\nANSWER:' in p
        for v in (forward,reverse):
            validate_sample(v,expected_label=group['label'])
            assert [c['paths'][0]['answer'] for c in v['candidates']]==[ANSWERS['SAME'],ANSWERS['DIFFERENT']]
    with pytest.raises(FileExistsError):
        create(source_bank=compiled,output_dir=compiled)


def test_training_and_probe_same_sealed_rom333_bank(tmp_path,capsys):
    _,prerom,compiled,_=fixtures(tmp_path)
    args=train_parse(['--rom333','--bank-dir',str(compiled),'--dry-run'])
    assert args.output_dir==DEFAULT_ROM333_OUTPUT
    assert train_parse(['--rom333','--arithmetic-control']).output_dir==DEFAULT_ROM333_CONTROL_OUTPUT
    assert train_parse(['--arithmetic-control']).output_dir==DEFAULT_CONTROL_OUTPUT
    assert probe_parse(['--rom333','--arithmetic-control']).training_dir == DEFAULT_ROM333_CONTROL_OUTPUT
    assert probe_parse(['--rom333']).training_dir == DEFAULT_ROM333_OUTPUT
    train(args)
    plan=json.loads(capsys.readouterr().out)
    assert plan['config']['variant']=='rom333-both-orientations'
    assert plan['config']['training_policy'].endswith('adamw-v2')
    assert plan['examples_per_pass']==24 and not plan['accuracy_computed']
    probe_run(probe_parse(['--rom333','--bank-dir',str(compiled),'--champion-only','--dry-run','--split','dev']))
    probe=json.loads(capsys.readouterr().out)
    assert probe['presentations']==24 and probe['no_training']
    # New representation cannot masquerade as a legacy raw-ROM bank.
    from nanojev_binary_raw_rom_bank import load_raw_bank
    with pytest.raises(RuntimeError,match='RAW_ROM_BANK_TEMPLATE_CONTRACT_MISMATCH'):
        load_raw_bank(compiled,split='train')
    with pytest.raises(RuntimeError,match='ROM333_BANK_SCHEMA_MISMATCH'):
        load_rom333_bank(prerom,split='train')
    with pytest.raises(RuntimeError,match='BINARY_TRAIN_REQUIRES_BOTH_ORIENTATIONS'):
        load_bank(compiled,split='train',variants=('rom333',))


def test_changed_answers_and_leakage_are_rejected(tmp_path):
    _,_,compiled,_=fixtures(tmp_path)
    bank=load_rom333_bank(compiled,split='dev')
    s=bank['groups'][0][1]['variants']['rom333']
    s['candidates'][0]['paths'][0]['answer']=' SAME'
    with pytest.raises(RuntimeError,match='ROM333_TRIPLE_ANSWER_INVALID'):
        validate_sample(s,expected_label=bank['groups'][0][1]['label'])
    assert 'SAME' not in render('x = 1','x = 2').split('VERDICT:')[0]
    assert 'DIFFERENT' not in render('x = 1','x = 2').split('VERDICT:')[0]
    # Delimiters inside program sources would make inverse extraction ambiguous.
    with pytest.raises(RuntimeError,match='ROM333_PROGRAM_CANNOT_BE_RENDERED'):
        render('x = "```"','x = 1')
    with pytest.raises(RuntimeError,match='ROM333_PROMPT_GRAMMAR_DRIFT'):
        unpack('json{}')


class Tokenizer:
    def __init__(self, split_chars=False):
        self.split_chars=split_chars
    def encode(self,text,add_special_tokens=False):
        return list(text) if self.split_chars else text.split()


class Champion:
    class base:
        @staticmethod
        def deserialize_question(sample,_objective):
            return SimpleNamespace(
                candidates=tuple(SimpleNamespace(paths=tuple(SimpleNamespace(**p) for p in c['paths']))
                                 for c in sample['candidates']))


def test_full_token_visibility_checks_every_backbone_and_fails_closed(tmp_path):
    _,_,compiled,_=fixtures(tmp_path)
    bank=load_rom333_bank(compiled,split='train')
    bundles={label:SimpleNamespace(label=label,tokenizer=Tokenizer(),max_positions=2048)
             for label in ('qwen','pythia','tinystories')}
    class Logger:
        def __init__(self):self.events=[]
        def emit(self,e,**fields):self.events.append((e,fields))
    logger=Logger()
    args=SimpleNamespace(max_prompt_tokens=512)
    result=audit_token_visibility(bank=bank,bundles=bundles,champion=Champion(),
                                  objective=None,args=args,logger=logger)
    assert result['tinystories']['prompt_tokens']>0
    assert logger.events[0][0]=='rom333_token_visibility_verified'
    assert logger.events[0][1]['presentations']==24
    bundles['pythia'].tokenizer=Tokenizer(split_chars=True)
    args.max_prompt_tokens=200
    with pytest.raises(RuntimeError,match='ROM333_WOULD_TRUNCATE: model=pythia'):
        audit_token_visibility(bank=bank,bundles=bundles,champion=Champion(),
                               objective=None,args=args,logger=logger)
