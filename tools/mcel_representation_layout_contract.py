"""Patch 2: semantic representation -> actual collection layout authority.

The DOM is measured by the browser, not sized from name length guesses. Only
registered MCEL elements are rendered. This is Frob-only and side-effect-free.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any

try:
    from tools.mcel_semantic_information_contract import project_collection
    from tools.mcel_information_quality_frob import page_for
except ModuleNotFoundError:
    from mcel_semantic_information_contract import project_collection
    from mcel_information_quality_frob import page_for

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = 'mcel.representation-layout-link.experiment.v1'


def project_layout(widget: dict[str, Any], value_sets: dict[str, list], *,
                   viewport_px: int, enabled: bool) -> dict[str, Any]:
    """Expose the semantic obligations to the browser's column-fit planner."""
    original = deepcopy(widget)
    result = project_collection(widget, value_sets, viewport_px=viewport_px, enabled=enabled)
    columns = result['field_contracts']
    rows = result['projection']['rows']
    for column in columns:
        key = column['key']
        if column.get('kind') != 'identifier':
            continue
        full = [str(row.get(key, '')) for row in result['source_rows']]
        shown = [str(row['cells'][key]['display']) for row in rows]
        if len(set(full)) == len(full) and len(set(shown)) != len(shown):
            raise ValueError(f'Identity collision after compression in field {key!r}')
    if result['projection']['collisions']:
        raise ValueError('Full record presentation collision; cannot claim identity preservation')
    result['layout_link'] = {
        'schema': SCHEMA,
        'enabled': enabled,
        'source': 'semantic-information-contract.v1 / registered MCEL collection',
        'layout_authority': 'MCEL shared layout engine · browser-measured field text + semantic required/identity contracts',
        'layout_engine_schema': 'mcel.semantic-collection-layout-evaluation.v1',
        'required_keys': [c['key'] for c in columns if c.get('required')],
        'identifier_keys': [c['key'] for c in columns if c.get('kind') == 'identifier'],
        'compact_breakpoint_px': 500,
        'min_visible_cell_px': 56,
        'protected_operations': ['raw-value search', 'raw-type sort', 'exact inspector'],
        'production_integrated': False,
    }
    assert original == widget, 'Input widget was changed by layout planning'
    return result


STYLES = '''
/* Layout policy is shared by both arms; only the rendered text differs. */
#panel{overflow:hidden}
#panel mcel-row{width:100%;min-width:0;overflow:visible;align-items:center}
#panel mcel-field{display:block;min-width:0;overflow:hidden;overflow-wrap:normal}
#panel mcel-field .field-value{display:block;overflow:hidden;text-overflow:clip;white-space:nowrap}
#panel mcel-field .field-label{display:block;white-space:nowrap;overflow:hidden}
#panel[data-layout-mode="stacked"] mcel-row{grid-template-columns:minmax(0,1fr) !important}
#panel[data-layout-mode="stacked"] mcel-field{display:flex;gap:6px;align-items:baseline}
#panel[data-layout-mode="stacked"] mcel-field .field-label{flex:0 0 80px}
#panel[data-layout-mode="stacked"] mcel-field .field-value{white-space:normal;overflow-wrap:anywhere;flex:1;min-width:0}
/* The exact-value inspector is not a scroll/clip escape hatch: long, unbroken
   originals must wrap *inside the viewport*, independently of shrinker state. */
main,#mcel-inspector,#mcel-inspector *{min-width:0;max-width:100%}
#mcel-inspector{box-sizing:border-box;width:100%;overflow:visible;overflow-wrap:anywhere;word-break:normal;white-space:normal}
#mcel-inspector p,#mcel-inspector h2{white-space:normal;overflow-wrap:anywhere;word-break:normal}
'''


def page_for_layout(data: dict, widget_name: str, mode: str) -> str:
    if data.get('layout_link', {}).get('schema') != SCHEMA:
        raise ValueError('Representation layout link missing')
    html = page_for(data, widget_name, mode)
    engine = (ROOT/'main_computer/web/applications/scripts/mcel-semantic-layout-inference.js').read_text(encoding='utf-8')
    js = (ROOT/'tools/mcel_representation_layout_runtime.js').read_text(encoding='utf-8')
    # page_for embeds the projection; the browser measures genuine rendered text
    # on real registered element tags and assigns track widths on each resize.
    return html.replace('</html>', f'<style>{STYLES}</style><script>{engine}</script><script>{js}</script></html>')
