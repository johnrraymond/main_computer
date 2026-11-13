# MCEL Console Debug — Read-only capture v1

On an MCEL Applications, MCEL Lab, or semantic Applications shell page, open the browser developer console and run:

```js
copy(debug())
```

This copies a current JSON-serializable MCEL snapshot to the clipboard. Paste the result into the discussion when a layout, theme, chrome, or mount issue occurs. If another script already owns the global `debug` name, use:

```js
copy(mcelDebug())
```

The command is read-only and does not initiate any requests, change any theme, unmount/reload a frame, run an operational intent, or start a recorder. It traverses same-origin child frames to a bounded depth. Cross-origin or sandbox restrictions are reported rather than circumvented. The capture includes frame identities and sandbox permissions, canonical Calculator root/control/region presence, semantic source and Theme Law availability, the selected theme and chrome, current theme status, computed theme-token availability, and summary information from MCEL's existing debug timeline. The Chrome projection is explicitly identified as observational-only for modern MCEL.

The report excludes form/input values, DOM text outside the two MCEL diagnostic status fields, application source documents, cookies, local storage, and URL query strings. It reports structural evidence and observed absence, not speculative causes. In particular, a script-disabled *legacy rendered-site* iframe does not prove that the isolated Calculator frame is blocked; each iframe is distinguished by its ID and sandbox attributes.

When debugging a failure, capture the report **after** selecting the affected theme and enabling the Calculator preview, so the output reflects the failed condition. If the relevant app is in a different browser frame, select that frame's console context or run the command from the parent MCEL shell (which traverses accessible children). No persistent diagnostic configuration is required.

Schema: `mcel.console-debug.v1`. This adds a developer-console interface to the existing `mcel-diagnostic-plugins.js` asset, loaded by both the legacy Applications host and canonical semantic Applications shell. It does not alter the existing diagnostic plugin lifecycle.
