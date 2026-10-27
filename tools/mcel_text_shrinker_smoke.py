"""One command: verify experimental surrounding API and produce human previews."""
from __future__ import annotations

import json
import sys
from pathlib import Path

try:
    from tools.mcel_text_shrinker_adapter import adapt_widget, load_flat_file, render_html
except ModuleNotFoundError:
    from mcel_text_shrinker_adapter import adapt_widget, load_flat_file, render_html


def main() -> int:
    fixture_path = Path(__file__).with_name('fixtures') / 'mcel_text_shrinker_widgets.json'
    fixture = load_flat_file(fixture_path)
    output = Path('runtime/reports/mcel-text-shrinker')
    output.mkdir(parents=True, exist_ok=True)
    results = {}
    for widget in ('downloads', 'inventory'):
        for width in (320, 390, 1100):
            run = adapt_widget(fixture['widgets'][widget], fixture['value_sets'], viewport_px=width)
            for row in run['rows']:
                for col in run['columns']:
                    key = col['key']
                    raw = next(r for r in fixture['widgets'][widget]['rows'] if str(r['id']) == row['id'])[key]
                    assert row['cells'][key]['raw'] == raw
                assert len(row['cells']) == len(run['columns'])
            assert not run['collisions'], (widget, width, run['collisions'])
            name = f'{widget}-{width}'
            (output / f'{name}.html').write_text(render_html(run, widget), encoding='utf-8')
            (output / f'{name}.json').write_text(json.dumps(run, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
            results[name] = {'layout': run['layout'], 'rows': len(run['rows'])}
    assert 'nanojev_zero_line_distribution_convergence' == adapt_widget(
        fixture['widgets']['downloads'], fixture['value_sets'], viewport_px=1100)['rows'][0]['cells']['name']['display']
    print('MCEL_TEXT_SHRINKER_SMOKE_PASS', json.dumps(results, sort_keys=True))
    print('human preview:', output / 'downloads-1100.html')
    print('mobile preview:', output / 'downloads-320.html')
    print('inventory preview:', output / 'inventory-1100.html')
    return 0


if __name__ == '__main__':
    sys.exit(main())
