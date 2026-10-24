"""Fail-closed orchestration of existing independent portable proof authorities."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.mcel_portable_refresh import (
    PROOF_ATTEMPT, REFRESH_REPORT, refresh,
)
from main_computer.mcel_portable_certification import DEFAULT_CERTIFICATE


def test_full_live_chain_admits_only_independently_verified_certificate(tmp_path):
    seen = []
    def fake_exec(repo, command, timeout):
        seen.append(command)
        target = Path(command[command.index('--output') + 1])
        target.parent.mkdir(parents=True, exist_ok=True)
        if 'mcel_portable_intent_probe.py' in command[1]:
            assert '--authorize-external' in command
            assert command[command.index('--model') + 1] == 'local:test-model'
            target.write_text(json.dumps({'passed': True,
                    'fullSemanticRuntimeProven': True, 'scope': 'independent-package-domain-operations'}))
        else:
            assert 'mcel_portable_certify.py' in command[1]
            target.write_text(json.dumps({'schema': 'certificate-test'}))
        return {'status': 'pass', 'exitCode': 0}

    report = refresh(tmp_path, model='local:test-model', execute=fake_exec,
                     verify=lambda document, repo, **kw: {'status':'pass','passed':True,
                         'eligibleAppIds':['calculator','code-editor']})
    assert report['status'] == 'pass'
    assert report['certificateUpdated'] is True
    assert len(seen) == 2
    assert (tmp_path / DEFAULT_CERTIFICATE).exists()
    assert json.loads((tmp_path / REFRESH_REPORT).read_text())['status'] == 'pass'
    assert (tmp_path / PROOF_ATTEMPT).is_file()


@pytest.mark.parametrize('failure', ['probe-command', 'probe-verdict',
                                       'certifier-command', 'tampered-verdict', 'incomplete-apps'])
def test_failed_live_refresh_never_replaces_existing_certificate(tmp_path, failure):
    existing = tmp_path / DEFAULT_CERTIFICATE
    existing.parent.mkdir(parents=True)
    existing.write_bytes(b'previous-certificate-bytes')
    called = []
    def fake_exec(repo, command, timeout):
        called.append(command[1])
        target = Path(command[command.index('--output') + 1])
        if 'mcel_portable_intent_probe.py' in command[1]:
            target.write_text(json.dumps({'passed': failure != 'probe-verdict',
                    'fullSemanticRuntimeProven': failure != 'probe-verdict'}))
            return {'status': 'refused' if failure == 'probe-command' else 'pass'}
        target.write_text(json.dumps({'schema': 'candidate'}))
        return {'status': 'refused' if failure == 'certifier-command' else 'pass'}
    def verify(document, repo, **kwargs):
        if failure == 'tampered-verdict':
            return {'status': 'refused','passed':False,'eligibleAppIds':[],
                    'reasons':['certificate-proof-hash-mismatch']}
        if failure == 'incomplete-apps':
            return {'status':'pass','passed':True,'eligibleAppIds':['calculator']}
        return {'status':'pass','passed':True,'eligibleAppIds':['calculator','code-editor']}

    report = refresh(tmp_path, model='local:test-model', execute=fake_exec, verify=verify)
    assert report['status'] == 'refused'
    assert report['certificateUpdated'] is False
    assert existing.read_bytes() == b'previous-certificate-bytes'
    assert (tmp_path / REFRESH_REPORT).is_file()
    if failure in ('probe-command', 'probe-verdict'):
        assert len(called) == 1


def test_invalid_external_authority_cannot_start(tmp_path):
    for kwargs in ({'model': ''}, {'model': 'something','ollama_url':'https://untrusted.example'},
                   {'model': 'test','ollama_url':'http://localhost:11434@evil.example'},
                   {'model':'test','timeout_s':0}, {'model':'test','max_age_hours':-1}):
        with pytest.raises(ValueError):
            refresh(tmp_path, **kwargs)
    assert not (tmp_path / DEFAULT_CERTIFICATE).exists()
