"""Offline guardrails for the deliberately destructive isolated crash campaign."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.stable_hub_lab.crash_recovery import _is_loopback, run_isolated_process_death


def test_crash_runner_refuses_nonlocal_and_non_smoke_topology(tmp_path):
    baseline = json.loads(Path('deploy/hub-topology/smoke-topology.json').read_text())
    for field, value in [('network', dict(baseline['network'], network_key='testnet')),
                         ('contract', dict(baseline['contract'], topology_scope='production')),
                         ('storage', dict(baseline['storage'], backend='local-json'))]:
        config = dict(baseline, **{field: value})
        file = tmp_path / f'{field}.json'
        file.write_text(json.dumps(config))
        with pytest.raises(ValueError, match='refusing nonlocal'):
            run_isolated_process_death(topology_path=file, out=tmp_path)


def test_crash_runner_rejects_remote_hub_even_on_dev_network(tmp_path):
    baseline = json.loads(Path('deploy/hub-topology/smoke-topology.json').read_text())
    baseline['hubs'][1]['hub_url'] = 'https://production.example.org'
    file = tmp_path / 'bad.json'
    file.write_text(json.dumps(baseline))
    with pytest.raises(ValueError, match='refusing nonlocal'):
        run_isolated_process_death(topology_path=file, out=tmp_path)


def test_crash_runner_only_accepts_http_loopback():
    assert _is_loopback('http://127.0.0.1:19170')
    assert not _is_loopback('https://127.0.0.1:19170')
    assert not _is_loopback('http://192.168.0.2:19170')
    assert not _is_loopback('http://example.com:19170')


def test_crash_runner_refuses_unavailable_local_foundationdb_without_spawning(tmp_path):
    baseline = json.loads(Path('deploy/hub-topology/smoke-topology.json').read_text())
    baseline['storage']['cluster_file'] = 'unavailable-disposable-fdb.cluster'
    file = tmp_path / 'unavailable.json'
    file.write_text(json.dumps(baseline))
    with pytest.raises(FileNotFoundError, match='local FDB cluster file missing'):
        run_isolated_process_death(topology_path=file, out=tmp_path)
