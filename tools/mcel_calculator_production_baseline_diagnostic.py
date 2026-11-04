"""Read-only Calculator production baseline inspection.

The incumbent Calculator's HTML is projected from the canonical host-bound
``mcel_apps/calculator/application.js`` by ``mcel_calculator_host_projection``.
A checked-in calculator.html is *not* required for the real server route.
Opt-in HTTP inspection is restricted to the exact loopback incumbent route.
"""
from __future__ import annotations

import argparse
import codecs
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import re
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

ROOT = Path(__file__).resolve().parents[1]
PRODUCTION_HTML = Path('main_computer/web/applications/apps/calculator.html')
PRODUCTION_PROJECTION_SOURCE = Path('mcel_apps/calculator/application.js')
PRODUCTION_BINDINGS = Path('main_computer/web/applications/scripts/dom-bindings/calculator.js')
PRODUCTION_JS = Path('main_computer/web/applications/scripts/calculator.js')
OUTPUT = Path('runtime/reports/mcel-calculator-production-baseline/report.json')
# This is a *whole applications page*, not a dedicated Calculator page.  Only
# retain the bounded prefix needed to observe the Calculator subtree.
MAX_HOST_BYTES = 32_000_000
READ_CHUNK_BYTES = 64_000
_VOID = frozenset({'area', 'base', 'br', 'col', 'embed', 'hr', 'img',
                   'input', 'link', 'meta', 'param', 'source', 'track', 'wbr'})


class _CalculatorIds(HTMLParser):
    """Inventory only #calculator-app; unrelated application IDs are excluded."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.ids: set[str] = set()
        self.duplicate_ids: set[str] = set()
        self.found = False
        self.complete = False
        self._depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self.complete:
            return
        value = next((v for k, v in attrs if k == 'id'), None)
        if not self.found and value != 'calculator-app':
            return
        if not self.found:
            self.found = True
            if tag in _VOID:
                self.complete = True
                return
        if self._depth > 0 or value == 'calculator-app':
            if value is not None:
                if value in self.ids:
                    self.duplicate_ids.add(value)
                self.ids.add(value)
            if tag not in _VOID:
                self._depth += 1

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag not in _VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        if self.found and not self.complete and tag not in _VOID:
            self._depth -= 1
            if self._depth == 0:
                self.complete = True


def _projected_host_markup(repo: Path) -> tuple[str | None, str | None]:
    """Generate, rather than fabricate, the markup used by the production host."""
    if not (repo / PRODUCTION_PROJECTION_SOURCE).is_file():
        return None, None
    try:
        from main_computer.mcel_calculator_host_projection import project
        return project(repo), None
    except Exception as exc:
        # An invalid compiled production source cannot be silently treated as
        # an absent baseline, nor can an unrelated hosted DOM repair it.
        return None, f'{type(exc).__name__}: {exc}'


def inspect_baseline(repo: Path = ROOT, hosted_html: str | None = None) -> dict:
    markup_file = repo / PRODUCTION_HTML
    bindings_file = repo / PRODUCTION_BINDINGS
    js_file = repo / PRODUCTION_JS
    if not bindings_file.is_file() or not js_file.is_file():
        raise FileNotFoundError('Production Calculator bindings or JavaScript missing')
    bindings = bindings_file.read_text(encoding='utf-8')
    expected_ids = sorted(set(re.findall(r'querySelector\(["\']#([A-Za-z0-9_-]+)["\']\)', bindings)))
    static_markup = markup_file.read_text(encoding='utf-8') if markup_file.is_file() else None
    projected_markup, projection_error = _projected_host_markup(repo)
    local_markup = static_markup if static_markup is not None else projected_markup
    observed = hosted_html if hosted_html is not None else local_markup
    dom = _CalculatorIds()
    if observed is not None:
        dom.feed(observed)
        dom.close()
    missing = sorted(set(expected_ids) - dom.ids) if observed is not None else expected_ids
    hosted_equivalent = (projected_markup.strip() in hosted_html
                         if projected_markup is not None and hosted_html is not None else None)
    if hosted_html is not None:
        provenance = 'opt-in-loopback-host'
    elif static_markup is not None:
        provenance = 'local-production-source'
    elif projected_markup is not None:
        provenance = 'canonical-compiled-production-projection'
    else:
        provenance = 'missing'
    valid_dom = bool(observed is not None and dom.found and dom.complete and
                     not missing and not dom.duplicate_ids and not projection_error)
    if not valid_dom or hosted_equivalent is False:
        status = 'BASELINE_INCOMPLETE'
    elif hosted_html is not None and hosted_equivalent is True:
        status = 'HOSTED_PRODUCTION_PROJECTION_VERIFIED'
    elif hosted_html is not None:
        status = 'HOSTED_DOM_OBSERVED_SOURCE_NOT_RECOVERED'
    elif static_markup is not None:
        status = 'SOURCE_BASELINE_READY_FOR_PARITY_REVIEW'
    else:
        status = 'PROJECTED_SOURCE_BASELINE_READY_FOR_PARITY_REVIEW'
    return {
        'schema': 'mcel-calculator-production-baseline-diagnostic.v2',
        'production_source': str(PRODUCTION_HTML),
        'canonical_projection_source': str(PRODUCTION_PROJECTION_SOURCE),
        'baseline_provenance': provenance,
        'source_present': static_markup is not None,
        'canonical_projection_present': projected_markup is not None,
        'projection_error': projection_error,
        'hosted_matches_canonical_projection': hosted_equivalent,
        'html_sha256': hashlib.sha256(observed.encode('utf-8')).hexdigest() if observed is not None else None,
        'canonical_projection_sha256': hashlib.sha256(projected_markup.encode('utf-8')).hexdigest()
            if projected_markup is not None else None,
        'calculator_subtree_found': dom.found,
        'calculator_subtree_complete': dom.complete,
        'required_binding_ids': expected_ids,
        'observed_ids': sorted(dom.ids),
        'missing_binding_ids': missing,
        'duplicate_dom_ids': sorted(dom.duplicate_ids),
        'status': status,
        'semantic_promotion_authorized': False,
        'note': 'Production DOM/source verification is not full functional parity or human design approval',
    }


def read_loopback(url: str) -> str:
    uri = urlsplit(url)
    if (uri.scheme != 'http' or uri.hostname not in {'127.0.0.1', 'localhost', '::1'} or
            uri.username is not None or uri.password is not None):
        raise ValueError('Only an explicit loopback HTTP URL is accepted')
    if uri.path != '/applications/calculator' or uri.query or uri.fragment:
        raise ValueError('Expected the incumbent /applications/calculator route')
    class _NoRedirect(HTTPRedirectHandler):
        def redirect_request(self, request, fp, code, message, headers, newurl):
            raise ValueError('Calculator diagnostic refuses HTTP redirects')

    request = Request(url, headers={'Accept': 'text/html', 'Accept-Encoding': 'identity'})
    with build_opener(_NoRedirect).open(request, timeout=5) as response:
        content_type = response.headers.get_content_type()
        if content_type not in ('text/html', 'application/xhtml+xml'):
            raise ValueError(f'Expected HTML response, got {content_type}')
        # Process the response in bounded chunks and stop as soon as the
        # Calculator subtree closes. No unrelated inline script payload needs
        # to be downloaded, parsed, or included in the result.
        parts: list[str] = []
        size = 0
        decoder = codecs.getincrementaldecoder('utf-8')(errors='replace')
        dom = _CalculatorIds()
        while True:
            chunk = response.read(min(READ_CHUNK_BYTES, MAX_HOST_BYTES-size+1))
            if not chunk:
                break
            size += len(chunk)
            if size > MAX_HOST_BYTES:
                raise ValueError('Hosted applications page exceeded diagnostic read limit before Calculator subtree')
            decoded = decoder.decode(chunk, final=False)
            parts.append(decoded)
            dom.feed(decoded)
            if dom.complete:
                break
        if not dom.complete:
            raise ValueError('Hosted page does not contain a complete Calculator subtree')
        return ''.join(parts)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', type=Path, default=ROOT)
    parser.add_argument('--host-url', help='Optional explicit loopback production Calculator route; GET only')
    parser.add_argument('--report', type=Path, default=OUTPUT)
    args = parser.parse_args()
    try:
        html = read_loopback(args.host_url) if args.host_url else None
    except (OSError, ValueError) as exc:
        parser.exit(2, f'CALCULATOR_BASELINE HOST_CHECK_FAILED: {exc}\n'
                       'The local compiled production baseline can be checked without --host-url.\n')
    result = inspect_baseline(args.repo, html)
    dest = args.report if args.report.is_absolute() else args.repo / args.report
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(result, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    print(f"CALCULATOR_BASELINE {result['status']}")
    print(f"provenance: {result['baseline_provenance']}; projection_present: {result['canonical_projection_present']}")
    print(f"binding_ids: {len(result['required_binding_ids'])}; missing: {len(result['missing_binding_ids'])}")
    if args.host_url:
        print(f"hosted_matches_canonical_projection: {result['hosted_matches_canonical_projection']}")
    print(f"report: {dest}")
    raise SystemExit(0 if result['status'] != 'BASELINE_INCOMPLETE' else 2)


if __name__ == '__main__':
    main()
