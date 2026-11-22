"""Offline negative-control for Bob's correction-lab attack probe (no Docker).

The verifier must reject a candidate whose Heartbeat read-side gate is
intentionally reverted. It must not accidentally count a missing endpoint,
network failure, or a leaked test capability as a security success.
"""
from __future__ import annotations

import os
from pathlib import Path
import re
import secrets
import subprocess
import sys
import tempfile
import time

from tools import mcel_security_correction_lab as lab


def test_bob_detects_reopened_heartbeat_read_bypass() -> None:
    with tempfile.TemporaryDirectory(prefix='mc-bob-negative-control-') as temp:
        root = Path(temp)
        lab.generate(lab.ROOT, root)
        (root / 'viewport_routes_onlyoffice.py').write_bytes((lab.ROOT / 'main_computer' / 'viewport_routes_onlyoffice.py').read_bytes())
        candidate = root / 'candidate' / 'heartbeat.py'
        source = candidate.read_text(encoding='utf-8')
        before = '''        if self.path in {"/api/heartbeat/control", "/api/heartbeat/status", "/healthz"}:
            if not heartbeat_service_authorized(self):
                self._send_json({"ok": False, "error": "heartbeat-service-authority-required"},
                                status=HTTPStatus.FORBIDDEN)
                return
            self._send_json(status_payload(self.server.config))'''
        after = '''        if self.path in {"/api/heartbeat/control", "/api/heartbeat/status", "/healthz"}:
            self._send_json(status_payload(self.server.config))'''
        assert source.count(before) == 1, 'test source drift'
        candidate.write_text(source.replace(before, after, 1), encoding='utf-8')
        base_port = secrets.randbelow(8000) + 40000
        env = {**os.environ, 'MC_CORRECTION_ROOT': str(root),
               'MC_CORRECTION_CAPABILITY': secrets.token_urlsafe(40),
               'MC_CORRECTION_LOGIN_KEY': secrets.token_urlsafe(40),
               'MC_CORRECTION_PORT_BASE': str(base_port)}
        fixture = lab.ROOT / 'tools' / 'mcel_security_correction_fixture.py'
        bob = lab.ROOT / 'tools' / 'mcel_security_bob_probe.py'
        target = subprocess.Popen([sys.executable,'-u',str(fixture),'serve'],env=env,
                                  stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
        try:
            time.sleep(1.2)
            assert target.poll() is None, 'fixture did not start'
            bob_env = {k:v for k,v in os.environ.items() if k not in {'MC_CORRECTION_CAPABILITY','MC_CORRECTION_LOGIN_KEY','MC_CORRECTION_ROOT'}}
            bob_env['MC_BOB_PORT_BASE'] = str(base_port)
            result = subprocess.run([sys.executable,'-u',str(bob),'run'],env=bob_env,
                                    capture_output=True,text=True,timeout=85)
            lines = result.stdout
            assert result.returncode == 1, lines[-2000:]
            assert 'heartbeat-private-status FAIL' in lines, lines[-2000:]
            assert 'heartbeat-private-health FAIL' in lines, lines[-2000:]
            assert '"status": "FAIL"' in lines
            print('MC_BOB_SELFTEST reopened-heartbeat-read-gate-detected PASS')
        finally:
            target.terminate()
            try: target.wait(timeout=4)
            except subprocess.TimeoutExpired:target.kill();target.wait(timeout=3)
            if target.stderr:target.stderr.close()


def test_bob_container_has_no_secret_or_target_source() -> None:
    secret = secrets.token_urlsafe(40)
    with tempfile.TemporaryDirectory(prefix='mc-bob-compose-shape-') as temp:
        manifest = lab.compose_manifest(Path(temp),secret)
        bob = manifest['services']['bob']
        assert 'MC_CORRECTION_CAPABILITY' not in bob['environment']
        assert 'MC_CORRECTION_LOGIN_KEY' not in bob['environment']
        assert secret not in str(bob)
        assert 'MC_CORRECTION_ROOT' not in bob['environment']
        assert bob['network_mode'] == 'service:targets'
        assert bob['user'] == '65534:65534'
        assert bob['read_only'] is True
        assert bob['cap_drop'] == ['ALL']
        assert 'no-new-privileges:true' in bob['security_opt']
        assert not any('candidate' in item['source'] or 'baseline' in item['source'] for item in bob['volumes'])
        assert not any('/var/run/docker.sock' in str(x) for x in bob['volumes'])
        assert 'ports' not in bob and 'ports' not in manifest['services']['targets']
        assert manifest['networks']['sandbox']['internal'] is True
        print('MC_BOB_SELFTEST docker-attacker-isolation-contract PASS')


def main() -> int:
    test_bob_container_has_no_secret_or_target_source()
    test_bob_detects_reopened_heartbeat_read_bypass()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
