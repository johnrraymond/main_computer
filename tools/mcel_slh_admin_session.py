"""Issue an explicit ephemeral Organization Service admin session for MCEL SLH.

Development-only helper: the local P-256 identity store uses unencrypted keys.
The access token is shown ONLY because the administrator explicitly requests
this command. It is never logged, persisted, or saved by this tool. Paste the
session into the SLH application's protected entry field; do not paste it into
issue reports, chat, source code, or shell history.

    python -m tools.mcel_slh_admin_session --device-id admin-pc
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from main_computer.mcel_slh_worker_bridge import OrganizationWorkerClient, WorkerBridgeError


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--device-id', required=True, help='Previously enrolled SLH admin device identity')
    p.add_argument('--identity-dir', type=Path, default=None,
                   help='Override local development identity store')
    args = p.parse_args(argv)
    try:
        session = OrganizationWorkerClient().login_from_local_identity(args.device_id, args.identity_dir)
    except WorkerBridgeError as exc:
        print('SLH Organization Service login refused: ' + exc.code, file=sys.stderr)
        return 1
    print(json.dumps({'schema':'mcel.slh.admin-session.v1', **session}, sort_keys=True))
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
