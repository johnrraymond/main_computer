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
    return '<nav role="tablist" aria-label="SLH administration target" class="slh-target-tabs">' + "".join(links) + '</nav>'


TAB_STYLE = '''.slh-target-tabs{display:flex;gap:.65rem;border-bottom:1px solid #64748b;padding:.5rem 0}
.slh-target-tabs [role=tab]{padding:.55rem .9rem;border-radius:.4rem .4rem 0 0;
text-decoration:none;border:1px solid transparent}
.slh-target-tabs [aria-selected=true]{border-color:#64748b;border-bottom-color:transparent;
font-weight:700;text-decoration:underline}
.slh-target-tabs [role=tab]:focus-visible{outline:2px solid currentColor;outline-offset:2px}'''
