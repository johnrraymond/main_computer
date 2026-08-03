from __future__ import annotations

import re
from pathlib import Path


PACKAGE = Path(__file__).resolve().parents[1]


def test_html_supplies_static_hosts_and_dynamic_templates() -> None:
    html = (PACKAGE / "src/index.html").read_text(encoding="utf-8")
    assert 'data-mcel-collection-host' in html
    assert html.count('data-mcel-conditional-host') == 2
    assert 'data-mcel-template-id="contract-workbench.item"' in html
    assert 'data-mcel-template-id="contract-workbench.validation-message"' in html
    assert 'data-mcel-template-id="contract-workbench.empty-state-template"' in html
    assert set(re.findall(r'data-mcel-item-intent="([^"]+)"', html)) == {
        "update-quantity",
        "remove-contract",
        "request-quote",
        "cancel-quote",
    }


def test_surface_contract_declares_dynamic_projection_vocabulary() -> None:
    source = (PACKAGE / "contracts/surface.js").read_text(encoding="utf-8")
    for kind in ("input", "property", "conditional", "collection", "operation-evidence"):
        assert f'"kind": "{kind}"' in source
    assert '"keyPath": "id"' in source
    assert '"fromItemKey": true' in source
    assert '"fromItemField": "quantity"' in source
    assert '"property": "disabled"' in source
    assert '"statePath": "canSubmit"' in source
    assert '"transform": "not"' in source
