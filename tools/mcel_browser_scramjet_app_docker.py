"""Read-only production Scramjet Docker inspection and explicit emergency cleanup.

Production browser Docker starts automatically from the MCEL Browser app.

    python -m tools.mcel_browser_scramjet_app_docker status
    python -m tools.mcel_browser_scramjet_app_docker stop

Do not stop while the Browser UI is open; it will reconnect when a lease expires.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
from main_computer.mcel_scramjet_docker import ScramjetDockerManager, ScramjetError


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', nargs='?', choices=['status','stop'], default='status')
    args = parser.parse_args()
    root = Path.cwd().resolve()
    if not (root/'main_computer'/'viewport_server.py').is_file():
        parser.error('Run from the Main Computer repository root')
    # The real manager runs in the ViewportServer process. Inspect the same
    # state without manufacturing a second, independent lease registry.
    state_file = root/'runtime'/'reports'/'mcel-browser-scramjet-app'/'container-state.json'
    try:
        state = json.loads(state_file.read_text(encoding='utf8'))
    except (ValueError, OSError):
        print('SCRAMJET_APP_DOCKER NOT_RUNNING')
        return 1 if args.action == 'status' else 0
    name = state.get('container','')
    try:
        import re
        if not isinstance(name,str) or not re.fullmatch(r'mcel-browser-scramjet-app-[a-f0-9]{8}',name):
            raise ScramjetError('Refusing to manage unverified container name')
        inspect = ScramjetDockerManager._docker('inspect','--format',
            '{{index .Config.Labels "mcel.browser.scramjet.app"}}|{{.State.Running}}',name,check_ok=False)
        if inspect.returncode or inspect.stdout.strip() not in ('true|true','true|false'):
            print('SCRAMJET_APP_DOCKER NOT_RUNNING (ownership not verified)')
            return 1
        if args.action == 'status':
            print('SCRAMJET_APP_DOCKER '+json.dumps({'running':inspect.stdout.strip()=='true|true',
                'url':state.get('url'),'container':name,'image':state.get('image')},sort_keys=True))
            return 0
        ScramjetDockerManager._docker('rm','-f',name,timeout=45)
        state_file.unlink(missing_ok=True)
        print('SCRAMJET_APP_DOCKER STOPPED '+name)
        return 0
    except ScramjetError as exc:
        print('SCRAMJET_APP_DOCKER ERROR '+str(exc))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
