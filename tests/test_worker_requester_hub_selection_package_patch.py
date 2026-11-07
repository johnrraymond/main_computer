"""Regression: Patch 3 CLI uses mainnet registry + installed Mother wallet authority."""
from __future__ import annotations

import argparse
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace

import pytest

from main_computer.cli import _compute_hub_url, build_parser, cmd_worker, cmd_requester
from main_computer.hub_networks import load_hub_network_registry


def parse(*argv):
    return build_parser().parse_args(list(argv))


def test_worker_and_requester_share_existing_mainnet_profile():
    configured = load_hub_network_registry().get('mainnet').hub_url
    for args in (parse('worker','--model','nanojev-clef','--via','hub'),
                 parse('requester','--model','nanojev-clef','--to','hub')):
        assert _compute_hub_url(args) == configured
        assert not _compute_hub_url(args).startswith('http://127.0.0.1')


def test_explicit_https_allowed_but_insecure_and_testnet_refused():
    args = parse('worker','--model','nanojev-clef','--via','hub','--hub-url','https://mainnet-hub.greatlibrary.io/')
    assert _compute_hub_url(args) == 'https://mainnet-hub.greatlibrary.io'
    args.hub_url = 'http://127.0.0.1:8770'
    with pytest.raises(ValueError,match='HTTPS'):
        _compute_hub_url(args)
    args.hub_url = 'https://testnet-hub.greatlibrary.io'
    with pytest.raises(ValueError,match='Testnet'):
        _compute_hub_url(args)


def test_no_profile_refuses_localhost_fallback(monkeypatch):
    from main_computer import hub_networks
    monkeypatch.setattr(hub_networks,'load_hub_network_registry',lambda: {})
    with pytest.raises(RuntimeError,match='refusing localhost fallback'):
        _compute_hub_url(parse('worker','--model','nanojev-clef','--via','hub'))


def test_worker_passes_resolved_mainnet_url(monkeypatch):
    from main_computer import config
    m=ModuleType('main_computer.operational_compute')
    calls=[]
    m.run_worker=lambda **kw: (calls.append(kw) or 0)
    monkeypatch.setitem(sys.modules,m.__name__,m)
    assert cmd_worker(parse('worker','--model','nanojev-clef','--via','hub')) == 0
    assert calls[0]['hub_url']==load_hub_network_registry().get('mainnet').hub_url
    assert calls[0]['officer']=='o0'
    assert calls[0]['count']==1


def test_requester_passes_resolved_mainnet_url_and_preview(monkeypatch):
    m=ModuleType('main_computer.operational_compute')
    calls=[]
    m.run_requester=lambda **kw: (calls.append(kw) or 0)
    monkeypatch.setitem(sys.modules,m.__name__,m)
    assert cmd_requester(parse('requester','--model','nanojev-clef','--to','hub')) == 0
    assert calls[0]['hub_url']==load_hub_network_registry().get('mainnet').hub_url
    assert calls[0]['officer']=='o3'
    assert calls[0]['submit'] is False


def test_mother_authority_modules_packaged_and_wallet_requires_verification(monkeypatch,tmp_path):
    from setuptools import find_packages
    import tomllib
    from main_computer import captain_cli
    from tools.mother.common.ethereum_identity import private_key_to_address
    from tools.mother.common import private_state
    from tools.mother.common.private_state import read_private_state
    from tools.mother.common.models import OperationIdentity
    from tools.mother.common.paths import MotherPaths
    project=Path(__file__).resolve().parents[1]
    packages=set(find_packages(where=str(project),include=['main_computer*','tools','tools.mother','tools.mother.common']))
    assert {'tools','tools.mother','tools.mother.common'}.issubset(packages)
    # Existing Mother's read is still invoked, and signing key is checked against
    # the claimed address. Do not add an alternate file parser or key fallback.
    key = '0x'+'1'.zfill(64)
    addr = private_key_to_address(key)
    fake_doc={'networks':{'mainnet':{'wallets':{'captain':{'address':addr,'private_key':key}}}}}
    import json
    calls=[]
    def validated_read(paths,*,operation):
        calls.append((paths,operation))
        return SimpleNamespace(canonical_object_bytes=json.dumps(fake_doc).encode())
    monkeypatch.setattr(private_state,'read_private_state',validated_read)
    wallet=captain_cli._mother_officer_wallet('o0',network='mainnet',deployment_path=tmp_path/'runtime'/'deployments'/'mainnet'/'latest.json')
    assert wallet.address.lower() == addr.lower() and wallet.office=='O0'
    assert len(calls)==1 and calls[0][1].network=='mainnet'
    monkeypatch.setattr(private_state,'read_private_state',lambda *a,**kw: (_ for _ in ()).throw(ValueError('invalid closure')))
    with pytest.raises(captain_cli.CaptainCliError,match='Cannot verify Mother private state'):
        captain_cli._mother_officer_wallet('o0',network='mainnet',deployment_path=tmp_path/'runtime'/'deployments'/'mainnet'/'latest.json')
