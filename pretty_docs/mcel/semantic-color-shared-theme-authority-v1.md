# MCEL Semantic Color — Shared Theme Authority (Patch 3)

Canonical semantic Color Law now owns **one named palette registry** for the eight existing themes. `McelLabStyleLaw.themePalette(theme)` resolves the same immutable nine-token semantic palette in every canonical host. `default` resolves to `theme-machine` (the existing Lab default), with explicit unknown-theme refusal. The Lab's historical CSS theme swatches are checked against these values in a browser oracle; they are **legacy visual-preview CSS**, not an authority that canonical application rendering samples.

`McelSemanticOperationalRuntime` validates authored color declarations and selects the canonical palette automatically: no swatch, Lab DOM, or host-provided palette is required. A host-provided explicit palette is only accepted when it exactly matches the named Theme, to prevent a second application-owned color authority. `McelSemanticColorPresentation` owns the per-mounted-root Color Law output; `setThemeForRoot(root, theme)` is the general bridge for an external Theme selector, without targeting an app or control by label. The renderer's existing `setColorTheme()` delegates to that same controller. Browser pseudo-classes still handle interaction states without JS repaint loops.

The Lab preview now selects the same named palette and instructs the canonical owner to change Theme before rendering its *legacy-only* compatibility projection. The generic Lab color selectors exclude source-authored semantic color owners, and the prior Evaluate demonstration is suppressed when the canonical renderer already owns that button. The read-only Lab inspector obtains palette values from the registry too. The legacy projector remains available until full Calculator and other application migrations, and does not override source-authored treatments.

**Scope and limits:** This patch equalizes computed colors for equivalent **authored semantic color declarations** in Lab and standalone, including hover. It does not recolor unmigrated controls or guarantee full visual equality of the old Lab projection and the standalone Calculator's legacy CSS. The old E/W/G widget remains on its accepted compatibility path. Those unfinished legacy surfaces belong to the Calculator/application migration and final enforcement patches; no false 100% coverage is claimed here. Form, Layout, Chrome, runtime actions, and the underlying eight preset designs are unchanged.

### Acceptance

- The registry matches all nine existing semantic CSS tokens across all eight presets in real Chromium.
- Default is identical in both hosts; invalid themes or nonmatching supplied palettes are refused without repainting.
- One source-declared canonical Evaluate button gets equal foreground, background and border colors in Lab and standalone under all eight themes at rest and hover; its DOM identity and dimensions are stable.
- Unmigrated Calculator routes gain no semantic color binding or new Theme CSS.
- No new application-specific color mappings, DOM event recoloring, or Theme-managed geometry changes.
