"""Pinned, isolated Scramjet v2 Docker build and promotion metadata.

This is separate from the known-working v1 Docker image and its Git checkout.
Version set verified in the upstream v2 integration matrix on 2026-08-04.
"""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import time

from tools.mcel_browser_scramjet_smoke import docker_cmd, port_for, fetch_status, require_docker

V2_CORE = '2.0.67-alpha.2'
V2_CONTROLLER = '0.0.14'
SOURCE = 'docker/mcel-browser-scramjet-v2'
ASSETS = ('Dockerfile','package.json','server.mjs','sw.js','mcel-surface.html',
          'mcel-surface.js','fixture.mjs')
SMOKE_LABEL = 'mcel.browser.scramjet.v2.smoke'
PROD_LABEL = 'mcel.browser.scramjet.app'
PROMOTED = Path('runtime/state/mcel-browser-scramjet-v2-enabled')
REQUIRED = ('healthz','mcel-surface.html','mcel-surface.js','sw.js',
            'scram/scramjet.js','scram/scramjet.wasm',
            'controller/controller.api.js','controller/controller.sw.js',
            'controller/controller.inject.js','utils/scramjet-utils.js','libcurl/index.mjs',
            'epoxy/index.mjs')

def fingerprint(repo: Path) -> str:
    h=hashlib.sha256()
    for name in ASSETS:
        file=repo/SOURCE/name
        if not file.is_file(): raise RuntimeError('Scramjet v2 source missing: '+str(file))
        h.update(name.encode()+b'\0'+file.read_bytes()+b'\0')
    return h.hexdigest()[:16]

def image(repo: Path, kind: str) -> str:
    assert kind in ('smoke','app')
    return f'mcel-browser-scramjet-{kind}:v2-{V2_CORE}-{fingerprint(repo)}'

def enabled(repo:Path)->bool:
    path=repo/PROMOTED
    try: data=json.loads(path.read_text(encoding='utf-8'))
    except (OSError,ValueError):return False
    return data.get('fingerprint')==fingerprint(repo) and data.get('version')==V2_CORE

def promote(repo:Path):
    path=repo/PROMOTED;path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps({'version':V2_CORE,'controller':V2_CONTROLLER,
                                'fingerprint':fingerprint(repo)},sort_keys=True)+'\n',encoding='utf8')

def rollback(repo:Path):
    (repo/PROMOTED).unlink(missing_ok=True)

def build(repo:Path,kind:str,*,rebuild:bool=False)->str:
    require_docker()
    tag=image(repo,kind)
    if not rebuild and docker_cmd('image','inspect',tag,check_ok=False).returncode==0:
        return tag
    source=repo/SOURCE
    logdir=repo/'runtime/reports/mcel-browser-scramjet-v2'
    logdir.mkdir(parents=True,exist_ok=True)
    with (logdir/f'build-{kind}.log').open('w',encoding='utf8') as output:
        command=['docker','build','--progress=plain','--tag',tag,'--file',str(source/'Dockerfile'),str(source)]
        try:
            result=subprocess.run(command,stdout=output,stderr=subprocess.STDOUT,timeout=900,check=False)
        except (OSError,subprocess.TimeoutExpired) as e:
            raise RuntimeError(f'v2 Docker build failure: {e}; log={output.name}') from e
    if result.returncode:
        tail='\n'.join((logdir/f'build-{kind}.log').read_text(encoding='utf8',errors='replace').splitlines()[-28:])
        raise RuntimeError(f'v2 Docker build failed; log={logdir/f"build-{kind}.log"}\n'+tail)
    return tag

def preflight(url:str)->tuple[bool,str]:
    root=url.rstrip('/')+'/'
    for target in REQUIRED:
        try: code,length=fetch_status(root+target,timeout=3)
        except Exception as e:return False, f'{target}: {type(e).__name__}: {e}'
        if code!=200 or length<12:return False,f'{target}: HTTP {code}, {length} bytes'
    return True,'v2 runtime resources verified'

def wait_for_ready(url:str,container:str,seconds:float=50):
    deadline=time.monotonic()+seconds
    last='pending'
    while time.monotonic()<deadline:
        ok,last=preflight(url)
        if ok:return
        inspection=docker_cmd('inspect','--format','{{.State.Running}}|{{.State.ExitCode}}',container,check_ok=False)
        if inspection.returncode or inspection.stdout.strip().startswith('false|'):
            break
        time.sleep(.5)
    tail=docker_cmd('logs','--tail','100',container,check_ok=False).stdout
    raise RuntimeError('Scramjet v2 Docker not ready: '+last+'\n'+tail[-1800:])

def run_container(repo:Path,kind:str,tag:str,*,ip:str,smoke:bool)->dict:
    import secrets
    port=port_for(ip)
    name=f'mcel-browser-scramjet-v2-{kind}-'+secrets.token_hex(4)
    label=SMOKE_LABEL if smoke else PROD_LABEL
    cmd=('run','--detach','--name',name,'--label',label+'=true',
         '--publish',f'{ip}:{port}:8080','--network','bridge',
         '--cap-drop=ALL','--security-opt=no-new-privileges',
         '--pids-limit=256','--memory=768m','--cpus=2','--read-only',
         '--tmpfs','/tmp:rw,noexec,nosuid,size=64m','--user','node',
         '--env','MCEL_SCRAMJET_SMOKE='+('1' if smoke else '0'),tag)
    result=docker_cmd(*cmd,timeout=75)
    data={'container':name,'id':result.stdout.strip(),
          'image':tag,'url':f'http://{ip}:{port}/','ip':ip,'port':port,
          'fingerprint':fingerprint(repo),'version':V2_CORE}
    try:wait_for_ready(data['url'],name)
    except Exception:
        # Preserve build and process logs separately even on startup failure.
        log=repo/'runtime/reports/mcel-browser-scramjet-v2'/f'startup-{kind}.log'
        log.parent.mkdir(parents=True,exist_ok=True)
        log.write_text(docker_cmd('logs','--tail','150',name,check_ok=False).stdout,encoding='utf8')
        docker_cmd('rm','-f',name,check_ok=False)
        raise
    return data
