"""Build the paired-source native Document Surface probe as an isolated local page.

No application server, files API, or AI port is used. The sole capability is
an in-memory observation of a submitted draft; it neither saves nor transmits it.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import webbrowser
from main_computer.mcel_dsl_compiler import compile_dsl_application

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'tools/fixtures/mcel_document_surface_native/application.js'
WEB = ROOT / 'main_computer/web/applications'
SCRIPTS = ('mcel-document-engine.js', 'mcel-element-registry.js', 'mcel-elements-core.js',
           'mcel-document-surface-element.js', 'mcel-semantic-operational-runtime.js')
STYLES = ('document.css', 'mcel-document-surface-element.css')


def render_preview() -> str:
    result = compile_dsl_application(SOURCE)
    if not result.valid:
        raise ValueError(f'Native Document Surface compilation failed: {result.diagnostics}')
    payload = json.dumps(result.normalized_ir, ensure_ascii=True).replace('<', '\\u003c')

    def asset(kind, file):
        tag = 'script' if kind == 'scripts' else 'style'
        content = (WEB / kind / file).read_text(encoding='utf-8')
        if f'</{tag}' in content.lower():
            raise ValueError(f'Unsafe inline {tag} asset: {file}')
        return f'<{tag}>\n{content}\n</{tag}>'

    return '''<!doctype html><html lang="en"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>MCEL Document Surface — native probe</title>
''' + '\n'.join(asset('styles', f) for f in STYLES) + '''
<style>body{margin:16px;font-family:system-ui,sans-serif} [data-mcel-region]{min-width:0} h2{font-size:1rem} [data-mcel-region="interface-region:application"]{display:grid;gap:12px} [data-mcel-rich-document-host]{border:1px solid #8886}</style>
<body><h1>MCEL Document Surface — native probe</h1>
<p>Two independent compiled MCEL surfaces. In-memory only. Edit either document, switch the first document's page mode, or inspect the first draft through a declared read capability.</p>
<div id="app"></div><p id="diagnostic" role="status"></p>
<script type="application/json" id="ir">''' + payload + '</script>\n' + '\n'.join(asset('scripts', f) for f in SCRIPTS) + '''
<script>
(function(){"use strict";
  const ir=JSON.parse(document.getElementById('ir').textContent);
  const allowed=req=>req.capabilityId==='capability:inspector' && req.runtimeMethod==='inspectDraft' && req.risk==='external-read';
  try{
    window.nativeDocumentProbe=McelSemanticOperationalRuntime.mount(document.getElementById('app'),ir,{capabilities:{
      authorize:allowed,invoke:async req=>{if(!allowed(req))throw Error('REFUSED');return `Observed ${req.arguments.html.length} HTML characters in memory`;}}});
    document.getElementById('diagnostic').textContent='Native MCEL probe mounted';
  }catch(e){document.getElementById('diagnostic').textContent='Mount refused: '+String(e);throw e;}
})();
</script></body></html>'''


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--open', action='store_true', help='Open the generated page in the default browser')
    args = parser.parse_args()
    out = ROOT / 'runtime/reports/mcel_document_surface_native/preview.html'
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render_preview(), encoding='utf-8')
    print(f'MCEL_DOCUMENT_SURFACE_NATIVE_PREVIEW {out}')
    if args.open:
        webbrowser.open(out.as_uri())


if __name__ == '__main__':
    main()
