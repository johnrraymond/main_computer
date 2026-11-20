# MCEL Patch 3: canonical source authority shadow

This patch performs **no production route switch**. It is a read-only, opt-in
comparison of `mcel_apps/<app>/application.js` (and its required HTML companion)
with the paired semantic source currently selected by
`main_computer/mcel_semantic_other_apps_host.py`. It uses the existing DSL
compiler, incumbent host contract and application-IR comparator; there is no
new compiler, renderer, or capability adapter.

Run from the repository root:

```powershell
python -m tools.mcel_source_authority_shadow --app code-editor --output runtime/reports/mcel-source-authority-shadow/code-editor.json
```

Without `--output`, the report prints to stdout and **no files are written**.
With `--output`, only the requested report under `runtime/reports/` is created.
Running the command does not mount an app, perform a source write, execute a
capability, replace a route, change process state, or promote a candidate.

The report includes exact source locations, compiler validity and hashes,
application identity, collection ID deltas, the incumbent host policy check,
and blockers. Existing JS-only packages report `MISSING_CANONICAL_HTML_PAIR`.
Missing canonical files are never silently replaced by fixture sources.

An IR match is only a source-equality observation, not evidence of live
behavioral, permission, state, or visual equivalence. **Promotion is always
refused** in Patch 3. Future promotion requires a distinct, explicit
acceptance step proving operational receipts, state, and browser parity.

Only the reviewed semantic-host applications (`code-editor`, `task-manager`,
`file-manager`) are in scope. SLH, Calculator and other production host paths
are deliberately not rerouted or inferred to be equivalent.
