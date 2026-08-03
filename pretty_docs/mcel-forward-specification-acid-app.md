# MCEL Dynamic Application Acid Test

## Status

`mcel_apps/contract-workbench/` is now the first fully dynamic, asynchronous, independently browser-proven MCEL application. It began as the forward specification that forced the shared platform to implement each missing bridge without weakening the application definition.

The completed progression is:

```text
human-owned application.js
→ deterministic normalized definition
→ explicit domain, intent, adapter, surface, layout, acceptance, and observation contracts
→ shared renderer-local, derived, provisional, and canonical state runtime
→ typed static and item payloads
→ properties, conditionals, and keyed collections
→ capability streams, cancellation, and latest-per-item-key concurrency
→ 14 contract-driven Chromium scenarios
→ two-instance isolation proof
→ enforceable package acceptance
→ intent-complete proof convergence
→ semantic-runtime-proven
```

## Authority

The primary human-owned source remains:

```text
mcel_apps/contract-workbench/application.js
```

`tools/mcel_application_definition.py` deterministically projects that source into the generated contract set. Manual edits to generated contracts are drift and must fail `--check`.

## Proven application surface

The Workbench proves:

- canonical, renderer-local, derived, and provisional state;
- typed form and item-control payload extraction;
- validation refusals and structured receipts;
- safe dynamic properties and conditional templates;
- stable keyed collection creation, update, sorting, filtering, clearing, and removal;
- streamed capability progress followed by one canonical commit;
- explicit cancellation and late-event suppression;
- same-key supersession and different-key parallel operations;
- prohibited, stale, and duplicate-operation refusals;
- independent Chromium observation of 14 scenarios;
- isolation across two simultaneous application instances;
- complete coverage of every declared operation.

## Constitutional rule retained

The app was not promoted because its files were structurally valid. Promotion required enforceable package-local acceptance, passing independent browser evidence, exact package/catalog/projection/repository alignment, and an intent-complete convergence report.

Future changes must preserve this authority chain. A unit test, DOM snapshot, or successful operation alone is not semantic-runtime proof.

## Verification

```powershell
python tools/mcel_application_definition.py `
  --app contract-workbench `
  --check

python tools/mcel_application_runtime_projection.py --check
python tools/mcel_application_package_browser_catalog.py --check
python main_computer/mcel_acceptance_runner.py --app contract-workbench --check
python main_computer/mcel_application_observation_runner.py --app contract-workbench --check
python main_computer/mcel_app_prove.py --app contract-workbench --check
```

The final command must report:

```text
truth_status: semantic-runtime-proven
```
