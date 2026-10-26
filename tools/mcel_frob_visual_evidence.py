"""Shared MCEL Frob browser-screenshot, inspection gallery, and review artifacts.

All screenshots are captured from actual Chromium pages. A geometry PASS is not a
human visual-design verdict. Paths are relative and remain usable offline.
"""
from __future__ import annotations

import csv
import hashlib
import html
import json
from pathlib import Path
import re
import struct

SCHEMA = 'main-computer.mcel.frob-visual-evidence.v1'
REVIEW_COLUMNS = ('id', 'viewport', 'view', 'state', 'screenshot',
                  'review', 'hierarchy', 'readability', 'density',
                  'responsive_quality', 'visual_polish', 'notes')


def png_size(path: Path) -> tuple[int, int]:
    with path.open('rb') as stream:
        header = stream.read(24)
    if header[:8] != b'\x89PNG\r\n\x1a\n' or header[12:16] != b'IHDR':
        raise ValueError(f'Not a PNG: {path}')
    return struct.unpack('>II', header[16:24])


def safe_token(text: object) -> str:
    value = re.sub(r'[^a-z0-9_-]+', '-', str(text).lower()).strip('-')
    if not value or len(value) > 90:
        raise ValueError(f'Invalid visual evidence identifier: {text!r}')
    return value


def capture(page, output: Path, *, viewport: int, view: str, state: str,
            label: str, filename: str | None = None, kind: str = 'generated-app') -> dict:
    """Capture the **entire** rendered page, not only the viewport.

    `page` must already be in the requested state. Only objective screenshot and
    DOM measurements are recorded here; application assertions belong elsewhere.
    """
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    name = filename or f'visual-{int(viewport)}-{safe_token(view)}-{safe_token(state)}.png'
    if Path(name).name != name or not name.endswith('.png'):
        raise ValueError('Invalid screenshot basename')
    dest = output / name
    observed = page.evaluate('''() => ({
        viewport: innerWidth,
        documentHeight: Math.ceil(document.documentElement.scrollHeight),
        horizontalOverflow: document.documentElement.scrollWidth > innerWidth + 1,
        selectedView: document.querySelector('[role="tab"][aria-selected="true"]')?.dataset.view || null,
        visibleRows: [...document.querySelectorAll('.record')].filter(el => !el.hidden && el.getClientRects().length).length,
        componentCount: document.querySelectorAll('[data-mcel-element-id]').length,
        layoutStatus: window.__MCEL_LAYOUT_PROOF?.status || window.__layoutWitness?.status || null,
        visualFixture: document.querySelector('#generated-root')?.dataset.visualFixtureState || null
    })''')
    page.screenshot(path=str(dest), full_page=True, timeout=30000, animations='disabled')
    pixel_width, pixel_height = png_size(dest)
    if pixel_width != observed['viewport']:
        raise ValueError(f'Screenshot width mismatch: {name}: {pixel_width} vs {observed["viewport"]}')
    if pixel_height + 2 < observed['documentHeight']:
        raise ValueError(f'Screenshot truncated: {name}: {pixel_height} < {observed["documentHeight"]}')
    return {'id': safe_token(name[:-4]), 'kind': kind, 'label': label,
            'viewport': viewport, 'view': view, 'state': state,
            'screenshot': name, 'screenshot_sha256': hashlib.sha256(dest.read_bytes()).hexdigest(),
            'pixel_width': pixel_width, 'pixel_height': pixel_height,
            'full_page': True, 'observed': observed,
            'visual_approval': 'UNREVIEWED'}


def verify_capture(output: Path, item: dict) -> None:
    name = item['screenshot']
    if not isinstance(name, str) or Path(name).name != name:
        raise ValueError('Unsafe visual evidence path')
    dest = Path(output)/name
    if not dest.is_file():
        raise ValueError(f'Missing screenshot: {dest}')
    if hashlib.sha256(dest.read_bytes()).hexdigest() != item['screenshot_sha256']:
        raise ValueError(f'Screenshot changed: {name}')
    if list(png_size(dest)) != [item['pixel_width'], item['pixel_height']]:
        raise ValueError(f'Screenshot dimensions changed: {name}')
    if not item.get('full_page'):
        raise ValueError(f'Not a full-page capture: {name}')


def write_gallery(output: Path, entries: list[dict], *, title: str = 'MCEL Frob visual inspection',
                  provenance: dict | None = None, manifest_name: str = 'visual-manifest.json') -> dict:
    """One gallery implementation used by layout-machine and semantic Frobs."""
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    ids = [item['id'] for item in entries]
    if len(ids) != len(set(ids)):
        raise ValueError('Duplicate visual-evidence identifiers')
    for item in entries:
        verify_capture(output, item)
    manifest = {'schema': SCHEMA, 'title': title, 'provenance': provenance or {},
                'review_status': 'UNREVIEWED', 'count': len(entries), 'captures': entries}
    (output/manifest_name).write_text(json.dumps(manifest, indent=2, ensure_ascii=False)+'\n',encoding='utf-8')
    cards=[]
    for item in entries:
        e = lambda value: html.escape(str(value), quote=True)
        cards.append('<article data-view="'+e(item['view'])+'" data-width="'+e(item['viewport'])+
                     '" data-state="'+e(item['state'])+'"><h2>'+e(item['label'])+'</h2><p>'+e(item['viewport'])+
                     'px · '+e(item['view'])+' · '+e(item['state'])+' · full page '+
                     f'{item["pixel_width"]} × {item["pixel_height"]}'+'</p><a href="'+e(item['screenshot'])+
                     '" title="Open original screenshot"><img loading="lazy" alt="'+e(item['label'])+
                     '" src="'+e(item['screenshot'])+'"></a><p class="review">Human visual review: not yet performed</p></article>')
    widths = sorted({int(x['viewport']) for x in entries})
    views = sorted({str(x['view']) for x in entries})
    states = sorted({str(x['state']) for x in entries})
    def options(values):
        return ''.join('<option value="'+html.escape(str(v),quote=True)+'">'+html.escape(str(v))+'</option>' for v in values)
    page = '''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>'''+html.escape(title)+'''</title>
<style>body{margin:24px auto;padding:0 18px;max-width:1600px;font:15px/1.5 system-ui,sans-serif;color:#1c2b40;background:#f6f8fc}h1{margin-bottom:4px}p{margin:5px 0 12px}main{display:grid;grid-template-columns:repeat(auto-fill,minmax(min(100%,340px),1fr));gap:16px}article{min-width:0;padding:14px;background:#fff;border:1px solid #d3dce6;border-radius:12px;box-shadow:0 1px 4px #152b4910}article h2{margin:0;font-size:15px;overflow-wrap:anywhere}article p{color:#627389;font-size:12px}article a{display:block;border:1px solid #d3dce6;border-radius:7px;overflow:hidden;background:white}article img{display:block;width:100%;height:auto;max-height:450px;object-fit:contain;object-position:top center}article a:hover{outline:2px solid #5699db}nav{display:flex;flex-wrap:wrap;gap:12px;padding:13px 0 20px}label{display:flex;gap:8px;align-items:center}select{padding:8px;border:1px solid #bfcbd9;border-radius:7px;background:#fff}.review{font-weight:600;color:#91561a}footer{padding:20px 0;color:#576980}</style>
<h1>'''+html.escape(title)+'''</h1><p>'''+str(len(entries))+''' browser-captured full-page PNGs · Synthetic fixture application · These are review candidates, not a visual-quality PASS.</p>
<nav aria-label="Filter captured screenshots"><label>Viewport<select id="width"><option value="">All</option>'''+options(widths)+'''</select></label><label>View<select id="view"><option value="">All</option>'''+options(views)+'''</select></label><label>State<select id="state"><option value="">All</option>'''+options(states)+'''</select></label><span id="count" aria-live="polite"></span></nav>
<main>'''+''.join(cards)+'''</main><footer>Open any image to view the original full-resolution PNG. Record judgments in visual-review-template.csv. Review is not automated.</footer>
<script>const filters=['width','view','state'];function update(){let visible=0;for(const el of document.querySelectorAll('article')){const ok=filters.every(k=>!document.getElementById(k).value||el.dataset[k]===document.getElementById(k).value);el.hidden=!ok;if(ok)visible++}document.getElementById('count').textContent=visible+' of '+document.querySelectorAll('article').length+' images';}for(const k of filters)document.getElementById(k).addEventListener('change',update);update();</script></html>'''
    (output/'gallery.html').write_text(page, encoding='utf-8')
    review = output/'visual-review-template.csv'
    with review.open('w',encoding='utf-8',newline='') as stream:
        writer = csv.DictWriter(stream,fieldnames=REVIEW_COLUMNS)
        writer.writeheader()
        for item in entries:
            writer.writerow({'id':item['id'],'viewport':item['viewport'],'view':item['view'],
                             'state':item['state'],'screenshot':item['screenshot'],'review':'UNREVIEWED'})
    return manifest
