"""Safe, disposable HTTPS readiness diagnostics for MC authentication bootstrap.

Run with python -m tools.mcel_auth_bootstrap_readiness_smoke.
Does not contact, restart, or alter production MC or its auth state.
"""
from __future__ import annotations

import contextlib
import json
import ssl
import tempfile
import threading
from pathlib import Path

from main_computer.auth_bootstrap import (
    BootstrapAuthority, BootstrapServer, generate_tls_certificate, provision,
)
from main_computer.auth_bootstrap_service import probe, probe_detail, wait_for_probe


def main() -> int:
    result: dict[str, bool] = {}
    with tempfile.TemporaryDirectory(prefix='mc-auth-readiness-') as temp:
        state = Path(temp) / 'auth'
        provision(state)
        cert, key, _ = generate_tls_certificate(state)
        result['stopped_port_rejected'] = not probe(state=state, port=1)
        result['missing_certificate_identified'] = probe_detail(state=Path(temp) / 'absent', port=1)[1] == 'TLS_CERT_FILE_MISSING'
        with BootstrapServer(('127.0.0.1', 0), BootstrapAuthority(state), backend_port=8765,
                             cert=cert, key=key) as server:
            t = threading.Thread(target=server.serve_forever, daemon=True)
            t.start()
            try:
                port = server.server_port
                result['real_https_session_endpoint_verified'] = probe(state=state, port=port)
                result['readiness_detail_identifies_success'] = probe_detail(state=state, port=port) == (True, 'SESSION_API_VERIFIED')
                result['wait_for_probe_verified'] = wait_for_probe(state=state, port=port, wait_seconds=0.2)[0]
                other = Path(temp) / 'bob'
                provision(other)
                generate_tls_certificate(other)
                accepted, reason = probe_detail(state=other, port=port)
                result['wrong_state_certificate_rejected'] = not accepted and 'TLS_CERTIFICATE_VERIFY_FAILED' in reason
            finally:
                server.shutdown()
                t.join(5)
        # Verify that an unreachable service cannot be called READY.
        result['stopped_service_rejected'] = not probe(state=state, port=port)
    for name, passed in result.items():
        print(f'MC_AUTH_READY_SMOKE {name} {"PASS" if passed else "FAIL"}', flush=True)
    ok = all(result.values())
    print('MC_AUTH_READY_SMOKE SUMMARY ' + json.dumps({'status': 'PASS' if ok else 'FAIL',
        'tests': len(result), 'failed': [k for k, v in result.items() if not v],
        'production_modified': False}), flush=True)
    return 0 if ok else 1


if __name__ == '__main__':
    raise SystemExit(main())
