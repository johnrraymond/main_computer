"""Generate a changed-files-only SLH target-tabs patch against an INSTALLED repo.

The export accessible to the oracle author predates the accepted first-run and
reactive-action patches. Rather than overwrite those accepted files, this tool
reads current source and emits only the two narrowly modified existing files.
No source files are changed by this builder; apply its output with new_patch.py.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import re
from zipfile import ZipFile, ZIP_DEFLATED

HOST='main_computer/mcel_slh_application.py'
DISPATCH='main_computer/viewport_route_dispatch.py'
TOKEN='mcel.slh.target-tabs.v2'


class PatchBuildError(ValueError):
    pass


def once(source:str, before:str, after:str, where:str)->str:
    if source.count(before)!=1:
        raise PatchBuildError(f'{where}: expected exactly one source anchor, found {source.count(before)}')
    return source.replace(before,after,1)


def upgrade_host(source:str)->str:
    if 'mcel.slh.target-tabs.v2' in source:
        raise PatchBuildError('Target tabs have already been installed in the SLH host')
    routes=re.search(r'^ROUTES\s*=\s*\{[^\n]+\}\s*$',source,re.MULTILINE)
    if not routes:
        raise PatchBuildError('SLH ROUTES declaration not recognized; refusing unsafe overwrite')
    old=routes.group(0)
    if '"/applications/slh"' not in old or '"/applications/slh/workers"' not in old:
        raise PatchBuildError('Existing local canonical routes not found')
    new='''ROUTES = {
    "/applications/slh": "overview", "/applications/slh/local": "overview",
    "/applications/slh/workers": "worker-bank",
    "/applications/slh/local/workers": "worker-bank",
    "/applications/slh/network": "network"
}'''
    source=once(source,old,new,'ROUTES')
    # The network app is separate. This branch precedes the existing local
    # compiled IR, authentication token closure and setup effects.
    source=re.sub(r'(def render_slh_page\(view: str = "overview", repo: Path = ROOT\) -> str:\n)',
        r'''\1    # mcel.slh.target-tabs.v2: target selection precedes local service lifecycle.
    if view == "network":
        from main_computer.mcel_slh_network_host import render_network_slh_page
        return render_network_slh_page(repo)
''',source,count=1)
    if TOKEN not in source:
        raise PatchBuildError('render_slh_page signature not recognized')
    import_stmt='from main_computer.mcel_slh_target_navigation import TAB_STYLE, target_tabs\n'
    if import_stmt not in source:
        source=once(source,'from main_computer.mcel_dsl_compiler import compile_dsl_application\n',
            'from main_computer.mcel_dsl_compiler import compile_dsl_application\n'+import_stmt,'tab navigation import')
    source=once(source,'<title>Secure Local Hub · MCEL</title><style>{css}</style>',
        '<title>Secure Local Hub · MCEL</title><style>{css}\\n{TAB_STYLE}</style>', 'tab style')
    source=once(source,'<header><h1>Secure Local Hub</h1>',
        '<header><h1>Secure Local Hub</h1>{target_tabs("local")}', 'target selector')
    return source


def upgrade_dispatch(source:str)->str:
    if 'mcel.slh.target-tabs.v2' in source:
        raise PatchBuildError('Target tabs have already been installed in the viewport')
    matcher=re.search(r'if route_path in \{[^\n]*"/applications/slh"[^\n]*"/applications/slh/workers"[^\n]*\}:',source)
    if not matcher:
        raise PatchBuildError('SLH canonical GET routing anchor not recognized')
    source=once(source,matcher.group(0),
        '''if route_path in {"/applications/slh", "/applications/slh/local",
                      "/applications/slh/workers", "/applications/slh/local/workers",
                      "/applications/slh/network"}:''','SLH GET routes')
    anchor='def dispatch_post(self) -> None:\n'
    extra='''def dispatch_post(self) -> None:
    # mcel.slh.target-tabs.v2: strictly local bridge, read-only remote TLS probe.
    if urlsplit(self.path).path == "/api/applications/slh/network/inspect":
        from main_computer.mcel_slh_network_route import handle_network_slh_inspect
        handle_network_slh_inspect(self)
        return
'''
    return once(source,anchor,extra,'network SLH POST route')


def generate(root:Path,output:Path)->Path:
    root=root.resolve()
    changes={}
    for name, transform in ((HOST,upgrade_host),(DISPATCH,upgrade_dispatch)):
        old=(root/name).read_text(encoding='utf-8')
        new=transform(old)
        if old==new:raise PatchBuildError(f'No source change for {name}')
        compile(new,name,'exec')
        changes[name]=new
    output=output.resolve()
    output.parent.mkdir(parents=True,exist_ok=True)
    with ZipFile(output,'w',ZIP_DEFLATED,compresslevel=9) as z:
        for name,source in changes.items():
            z.writestr(name,source.encode('utf-8'))
    with ZipFile(output) as z:
        if z.testzip() is not None or z.namelist()!=[HOST,DISPATCH]:
            raise PatchBuildError('Generated ZIP verification failed')
    return output


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,default=Path(__file__).resolve().parents[1])
    parser.add_argument('--output',type=Path,default=Path('runtime/reports/mcel-slh-target-tabs-v2.patch.zip'))
    args=parser.parse_args(argv)
    try:
        result=generate(args.root,args.output)
    except (PatchBuildError,OSError,SyntaxError) as exc:
        parser.exit(2,f'SLH_TARGET_TABS_PATCH_REFUSED: {exc}\n')
    print('SLH_TARGET_TABS_PATCH_READY',result)
    print('  changed existing files: '+HOST+', '+DISPATCH)
    print('  run: python new_patch.py "'+str(result)+'"')
    return 0


if __name__=='__main__':
    raise SystemExit(main())
