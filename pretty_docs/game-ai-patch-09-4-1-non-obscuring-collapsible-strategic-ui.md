# Game AI Patch 09.4.1 — Non-Obscuring Collapsible Strategic UI

Contract: `game.ai.patch-09-4-1.strategic-panel-layout.v1`

Status: implemented a shared responsive dock for Vela, Solace, and off-screen return reports.

## Problem

The first Solace Reach live test showed the relief-coordination card positioned over the game canvas. The card obscured the scene, competed with navigation and status overlays, and had no way to reclaim the playfield without leaving the system.

## Solution

All player-facing strategic cards now live inside one managed dock:

```text
wide Game Surface
→ scene in the left grid track
→ strategic dock in the right grid track

narrow Game Surface
→ scene in the upper grid track
→ strategic drawer in the lower grid track
```

The game surface and dock have separate layout tracks. Strategic cards no longer use their original absolute positioning while docked.

## Panel modes

Each Vela, Solace, and return-summary card exposes:

- **Compact** — keeps actionable and essential status content while reducing detailed history;
- **Collapse** — reduces the dock to a small AI tab;
- **Expand** — restores the most recent non-collapsed mode;
- **Full** — restores full detail from compact mode.

Preferences persist independently under:

```text
main-computer.strategic-ai.panel-mode.v1:<panel-id>
```

Panel IDs are:

```text
return-summary
vela-gate
solace-reach
```

## Playfield protection

At wide sizes the dock reserves a clamped side lane. A collapsed panel uses a 72-pixel lane.

At narrow sizes the controller switches the dock to the bottom axis. The drawer height is measured from the actual Game Surface host:

```text
expanded height = min(300, max(120, host height - 220))
compact height  = min(180, max(90, host height - 240))
collapsed height = 64
```

This preserves roughly 220 pixels of playable height for an expanded drawer and 240 pixels for compact mode whenever the host is large enough.

There are no timers or polling loops. A `ResizeObserver` updates the axis and measured heights only when the host size changes.

## Multiple visible cards

A return report may be visible at the same time as the current-system interaction. The shared dock stacks both cards and scrolls internally.

Dock detail follows the most detailed visible card:

```text
any expanded card → expanded dock
otherwise any compact card → compact dock
otherwise → collapsed dock
```

## Strategic boundary

This patch changes only presentation and panel preference persistence.

It does not change:

- strategic schema or state versions;
- project definitions;
- actor decisions;
- commitments or trust;
- resources or canonical effects;
- travel progression;
- return-summary receipts;
- Vela or Solace encounter completion state.

## Verification

Headless browser geometry was checked at desktop and narrow sizes.

Desktop expanded:

```text
playfield right edge = dock left edge
768 px playfield + 432 px dock = 1200 px host
```

Desktop collapsed:

```text
1128 px playfield + 72 px dock = 1200 px host
```

Narrow expanded:

```text
300 px playfield above a 300 px drawer
```

Narrow collapsed:

```text
536 px playfield above a 64 px drawer
```

The geometry checks verify non-overlap rather than visual similarity alone.

## Files changed

```text
main_computer/web/applications.html
main_computer/web/applications/apps/webgl.html
main_computer/web/applications/styles/strategic-ai-panel-layout.css
main_computer/web/applications/scripts/strategic-ai-panel-layout.js
tests/test_strategic_ai_panel_layout.py
pretty_docs/game-ai-patch-09-4-1-non-obscuring-collapsible-strategic-ui.md
pretty_docs/game-sophisticated-ai-architecture-plan.md
pretty_docs/game-mother-ship-design-index.md
pretty_docs/index.json
```

## Deferred work

AI-9.5 remains the next behavioral slice: repository-backed campaign save integration and one durable Vela–travel–Solace replay proof.
