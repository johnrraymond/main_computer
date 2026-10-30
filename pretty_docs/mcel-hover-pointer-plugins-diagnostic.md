# MCEL Hover/Pointer Diagnostic Plugin

The `mcel-hover-pointer` plugin is an **opt-in**, local-only recorder for the single
L18-01 acid-test specimen. Its purpose is to verify which presentation owns the
pointer and scroll interaction when an original `mcel-row` is promoted to the
top layer. It does **not** replace the row, generate a replica, alter hover timing,
change geometry, or send telemetry across the network.

The MCEL presentation host emits `mcel-hover-trace` events with a row ID and a
structured reason for geometry plans, commits, close decisions, timer boundaries,
replanning and scroll-ownership transfers. The diagnostics plugin additionally
observes browser pointer events, hit-tested elements, row/list scroll, and the
actual top-layer geometry in animation frames. All records have elapsed time
and are bounded to a configurable in-memory buffer (default 2400 events).

On MCEL Lab -> Lifecycle Acid Test L18-01, choose **Record cursor + hover**,
perform the faulty hover, choose **Stop cursor + hover**, then **Export cursor
trace**. The normal review-and-trace JSON export also includes these events.
The standalone file is `MCEL-L18-01-cursor-hover-trace.json`. Export is explicit.
The recording does not capture record field text, keystrokes, form values or
clipboard contents; it records DOM roles/IDs and cursor positions only.

Browser console API (when Applications host has loaded the plugin script):

```javascript
await MCELDiagnosticPlugins.enable('mcel-hover-pointer');
// Reproduce the hover/scroll behavior.
const trace = MCELDiagnosticPlugins.exportHoverTrace();
await MCELDiagnosticPlugins.disable('mcel-hover-pointer');
MCELDiagnosticPlugins.clearHoverTrace();
```

The same registry advertises optional `rrweb` and `chrome-performance` plugins.
They are independent; rrweb is not required and no third-party session recorder
is injected. Trace export reports their availability and enablement status but
does not mix external recordings into cursor events.

Regression gate: `python -m tools.mcel_hover_pointer_plugin_frob`.
The core hover gate remains `python -m tools.mcel_row_atomic_hover_20_frob`.
