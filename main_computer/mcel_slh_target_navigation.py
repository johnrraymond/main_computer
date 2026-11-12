"""Semantic administration-target navigation. Distinct targets, browser-native links.

This is navigation chrome, not an alternate HTML implementation of the MCEL
application surface. The local and network bodies are synthesized from their
separate semantic application descriptions.
"""
from __future__ import annotations

from html import escape

TARGET_ROUTES = {
    "local": "/applications/slh/local",
    "network": "/applications/slh/network",
}
LABELS = {"local": "This Computer", "network": "Network SLH"}


def target_tabs(selected: str) -> str:
    if selected not in TARGET_ROUTES:
        raise ValueError("Unknown SLH administrative target")
    links = []
    for target, route in TARGET_ROUTES.items():
        active = target == selected
        links.append(
            f'<a role="tab" href="{escape(route)}" data-slh-target="{target}" '
            f'aria-selected="{str(active).lower()}" tabindex="0">'
            f'{escape(LABELS[target])}</a>'
        )
        if active and target == "local":
            links.append('<section role="tabpanel" data-slh-target-context="local">'
                         '<p id="mcel-slh-auth-status" role="status">Organization session not established</p>'
                         '<details data-mcel-navigation-note><summary>About local administration</summary>'
                         '<p>Start SLH on demand. No Windows boot startup.</p></details></section>')
        elif active and target == "network":
            links.append('<section role="tabpanel" data-slh-target-context="network">'
                         '<details data-mcel-navigation-note><summary>About network verification</summary>'
                         '<p>The remote TLS fingerprint must be verified independently. '
                         'Inspection does not authenticate the selected local identity.</p></details>'
                         '</section>')
    return '<nav role="tablist" aria-label="SLH administration target" class="slh-target-tabs">' + "".join(links) + '</nav>'


TAB_STYLE = '.slh-target-tabs{display:grid;grid-template-columns:minmax(0,1fr);gap:4px;padding:2px 0}\n.slh-target-tabs [role=tab]{display:block;min-height:32px;padding:.4rem .6rem;border-radius:.4rem;\ntext-decoration:none;border:1px solid #456175;overflow-wrap:anywhere}\n.slh-target-tabs [aria-selected=true]{border-color:#76bcd2;background:#234353;font-weight:700}\n.slh-target-tabs [role=tab]:focus-visible{outline:2px solid currentColor;outline-offset:2px}\n.slh-target-tabs [role=tabpanel]{padding:0 .45rem .35rem;font-size:.85rem}\n.slh-target-tabs [role=tabpanel] p{margin:.3rem 0}\n.slh-target-tabs [role=tabpanel] details{margin:.2rem 0}'
