# MCEL Applications Shell — first-hover diagnostic

The `mcel-shell-launcher` diagnostic plugin records the first left-rail hover
**after switching from the desktop to a mounted MCEL application**. It records
only app IDs, launcher geometry, CSS/open/dismissed states, and timestamps.
It does not record app contents, fields, prompts, keystrokes, or network traffic.
It is opt-in and uses the existing `MCELDiagnosticPlugins` registry.

On `/applications/mcel-semantic-shell`, open browser DevTools and run:

```js
await MCELDiagnosticPlugins.enable('mcel-shell-launcher');
// Click a desktop app icon, then enter the collapsed left Apps rail once.
const report = await MCELDiagnosticPlugins.run('mcel-shell-launcher');
console.log(report.result);
console.log(MCELDiagnosticPlugins.findings().filter(x => x.pluginId === 'mcel-shell-launcher'));
await MCELDiagnosticPlugins.disable('mcel-shell-launcher');
```

Expected trace: `application-selected`, `launcher-closed` with
`suppressHover:false`, `first-pointer-enter`, `first-hover-expanded`.

An actual stuck first hover emits `first-hover-did-not-expand` as an **error**
in the diagnostic plugin findings buffer. The trace also records whether
`data-dismissed` was stuck `true`. No findings are transmitted automatically.

Run `python -m tools.mcel_applications_shell_oracle --mode hosted --host-url
http://127.0.0.1:8765 --screenshots` after restarting the Main Computer
service. Oracle V3+ now gates this app-transition sequence at 390, 500,
1040, and 1440 pixels as well as its original fresh-page hover checks.
