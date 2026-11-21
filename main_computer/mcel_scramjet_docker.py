"""On-demand, reference-counted Docker owner for the untrusted MCEL Browser origin.

Browser surfaces are leased by the trusted localhost Browser API. The Docker
container is NOT started during MCEL startup, and is removed when the last
lease closes (or is reaped after an abandoned page's heartbeat expires).
"""
from __future__ import annotations

from contextlib import suppress
import json
from pathlib import Path
import re
import secrets
import threading
import time
from urllib.parse import urlsplit


class ScramjetError(Exception):
    pass


# A distinct tag guarantees that a cached SMOKE image cannot be promoted to
# production when security-sensitive source patches have changed.
PRODUCTION_IMAGE = 'mcel-browser-scramjet-app:f6f83cbc9309-v4'
LABEL = 'mcel.browser.scramjet.app'
NAME_PREFIX = 'mcel-browser-scramjet-app-'
IP = '127.0.0.3'
MAX_LEASES = 8
LEASE_TIMEOUT = 8 * 60  # Suspended/hidden browser tabs get a generous grace period.
REAP_INTERVAL = 25


class ScramjetDockerManager:
    def __init__(self, root: Path) -> None:
        self.root = Path(root).resolve()
        self.state_file = self.root / 'runtime' / 'reports' / 'mcel-browser-scramjet-app' / 'container-state.json'
        self._lock = threading.RLock()
        self._leases: dict[str, float] = {}
        self._container: dict | None = self._read_state()
        self._closed = False
        self._wake = threading.Event()
        self._janitor: threading.Thread | None = None
        if self._container is not None:
            self._start_reaper()

    def _start_reaper(self) -> None:
        if self._janitor is None:
            self._janitor = threading.Thread(target=self._reaper, daemon=True, name='mcel-scramjet-reaper')
            self._janitor.start()

    @staticmethod
    def _docker(*args: str, check_ok: bool = True, timeout: int = 75):
        from tools.mcel_browser_scramjet_smoke import docker_cmd
        try:
            return docker_cmd(*args, timeout=timeout, check_ok=check_ok)
        except Exception as exc:
            raise ScramjetError(f'Docker {args[0]} failed: {str(exc)[:700]}') from exc

    def _read_state(self) -> dict | None:
        try:
            data = json.loads(self.state_file.read_text(encoding='utf8'))
            return data if isinstance(data, dict) else None
        except (OSError, ValueError):
            return None

    def _owned(self, state: dict) -> bool:
        name = state.get('container', '')
        if not isinstance(name, str) or not re.fullmatch(r'mcel-browser-scramjet-app-[a-f0-9]{8}', name):
            return False
        info = self._docker('inspect', '--format', '{{index .Config.Labels "'+LABEL+'"}}|{{.Config.Image}}|{{.State.Running}}',
                            name, check_ok=False)
        return (info.returncode == 0 and info.stdout.strip().startswith('true|mcel-browser-scramjet-app:f6f83cbc9309-v')
                and info.stdout.strip().endswith('|true'))

    def _healthy(self, state: dict) -> bool:
        if state.get('image') != PRODUCTION_IMAGE or not self._owned(state):
            return False
        url = state.get('url', '')
        p = urlsplit(url)
        if p.scheme != 'http' or p.hostname != IP or not p.port or p.path not in ('/', '') or p.username or p.password:
            return False
        from tools.mcel_browser_scramjet_smoke import fetch_status
        try:
            # A working Scramjet homepage is insufficient: the Browser embeds
            # MCEL-specific assets that may be absent in a stale cached image.
            for resource, minimum in (('', 100), ('mcel-surface.html', 150),
                                      ('mcel-surface.js', 500), ('mcel-sw-probe.js', 80), ('mcel-scramjet-db.js', 500), ('sw.js', 200),
                                      ('scram/scramjet.all.js', 1000)):
                code, length = fetch_status(url + resource, timeout=3)
                if code != 200 or length < minimum:
                    return False
            return True
        except Exception:
            return False

    def _stop(self) -> None:
        state = self._container or self._read_state()
        if state and isinstance(state.get('container'), str):
            # Do not touch any container that cannot be positively authenticated.
            if self._owned(state):
                self._docker('rm', '-f', state['container'], timeout=45)
        with suppress(OSError):
            self.state_file.unlink()
        self._container = None

    def _start(self) -> dict:
        from tools.mcel_browser_scramjet_smoke import (
            COMMIT, prepare_build_context, port_for, require_docker, fetch_status,
        )
        existing = self._read_state()
        if existing and self._healthy(existing):
            self._container = existing
            return existing
        if existing:
            self._stop()
        try:
            require_docker()
        except Exception as exc:
            raise ScramjetError('Docker Desktop Linux engine required for MCEL Browser: ' + str(exc)) from exc
        # Build only on the first Browser open (not at MCEL startup).
        if self._docker('image','inspect',PRODUCTION_IMAGE,check_ok=False).returncode:
            context = prepare_build_context(self.root, no_bootstrap=False)
            self.state_file.parent.mkdir(parents=True, exist_ok=True)
            build_log = self.state_file.parent / 'build.log'
            import subprocess
            with build_log.open('w', encoding='utf8') as stream:
                command = ['docker','build','--progress=plain','--tag',PRODUCTION_IMAGE,
                           '--file',str(context/'Dockerfile'),str(context)]
                try:
                    build = subprocess.run(command, stdout=stream, stderr=subprocess.STDOUT, timeout=900, check=False)
                except (OSError, subprocess.TimeoutExpired) as exc:
                    raise ScramjetError('Scramjet image build failed; inspect ' + str(build_log) + ': ' + str(exc)) from exc
            if build.returncode:
                tail = '\n'.join(build_log.read_text(encoding='utf8',errors='replace').splitlines()[-12:])
                raise ScramjetError('Scramjet Docker image build failed: ' + tail[-800:])
        # Docker Desktop publishes a single isolated host address; it may have
        # multiple Browser leases but MUST NOT publish on trusted 127.0.0.1.
        port = port_for(IP)
        name = NAME_PREFIX + secrets.token_hex(4)
        args = ('run','--detach','--name',name,'--label',LABEL+'=true',
                '--publish',f'{IP}:{port}:8080','--network','bridge',
                '--cap-drop=ALL','--security-opt=no-new-privileges',
                '--pids-limit=256','--memory=768m','--cpus=2','--read-only',
                '--tmpfs','/tmp:rw,noexec,nosuid,size=64m','--user','node',
                '--env','MCEL_SCRAMJET_SMOKE=0','--env','MCEL_SCRAMJET_BROWSER=1',
                PRODUCTION_IMAGE)
        created = self._docker(*args,timeout=75)
        state = {'container':name,'id':created.stdout.strip(),'url':f'http://{IP}:{port}/',
                 'image':PRODUCTION_IMAGE,'commit':COMMIT}
        self._container = state
        self.state_file.parent.mkdir(parents=True,exist_ok=True)
        self.state_file.write_text(json.dumps(state,indent=2)+'\n',encoding='utf8')
        end = time.monotonic()+45
        while time.monotonic()<end:
            if self._healthy(state):
                return state
            time.sleep(.5)
        self._stop()
        raise ScramjetError('Scramjet sidecar did not become healthy; use docker logs for diagnosis')

    def _expire(self) -> None:
        now = time.monotonic()
        for token, touched in list(self._leases.items()):
            if now - touched > LEASE_TIMEOUT:
                del self._leases[token]
        if not self._leases and self._container is not None:
            self._stop()

    def _reaper(self) -> None:
        while not self._wake.wait(REAP_INTERVAL):
            with self._lock:
                if self._closed:
                    return
                with suppress(Exception):
                    self._expire()

    def open(self) -> dict:
        with self._lock:
            if self._closed:
                raise ScramjetError('MCEL Browser is shutting down')
            self._expire()
            if len(self._leases) >= MAX_LEASES:
                raise ScramjetError('MCEL Browser session limit reached')
            state = self._container
            if state is None or not self._healthy(state):
                try:
                    state = self._start()
                except ScramjetError:
                    raise
                except Exception as exc:
                    raise ScramjetError('Unable to start isolated Docker Browser: ' + str(exc)[:600]) from exc
            token = secrets.token_urlsafe(24)
            self._leases[token] = time.monotonic()
            self._start_reaper()
            return {'ok':True,'session':token,'sidecar_url':state['url'],'engine':'scramjet-docker'}

    def touch(self, token: str) -> dict:
        with self._lock:
            self._expire()
            if token not in self._leases:
                raise ScramjetError('Browser session expired')
            if self._container is None or not self._healthy(self._container):
                # Fail closed. The frontend must establish a fresh isolated frame.
                self._leases.pop(token,None)
                raise ScramjetError('Scramjet sidecar stopped; reopen the Browser session')
            self._leases[token] = time.monotonic()
            self._start_reaper()
            return {'ok':True,'session':token,'sidecar_url':self._container['url']}

    def release(self, token: str) -> dict:
        with self._lock:
            self._leases.pop(token,None)
            if not self._leases and self._container is not None:
                self._stop()
            return {'ok':True,'active_sessions':len(self._leases)}

    def close(self) -> None:
        with self._lock:
            self._closed = True
            self._wake.set()
            self._leases.clear()
            try:
                self._stop()
            except ScramjetError as exc:
                print(f'WARNING: MCEL Scramjet Docker cleanup failed: {exc}', flush=True)
        if self._janitor is not None and threading.current_thread() is not self._janitor:
            self._janitor.join(timeout=2)
