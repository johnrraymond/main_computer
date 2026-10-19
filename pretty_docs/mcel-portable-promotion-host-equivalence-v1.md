# MCEL Patch 20: Portable Package Promotion and Host-Equivalence Gate

## Scope and truth conditions

Patch 19 certifies a bounded 19-intent **independent standalone-package execution**. It does not prove general equivalence to the existing host application, change the application registry maturity, or authorize a production route change.

Patch 20 adds a separate **portable package-release promotion** operation to the canonical `mcel_app_promote` dispatch. Calculator and Code Editor can release a generated standalone package into an atomic active slot only if all of the following pass **at execution time**:

1. The current Patch 19 certificate passes independent source and byte-bound verification and covers both apps.
2. The canonical truth audit passes with fresh canonical runtime and acceptance reports, with **exact** source fingerprint bindings and successful portable certification.
3. A fresh host-bound Chromium/effect proof executes for the target app: the Calculator's generated adapter and all 11 intents, or the Code Editor's actual generated adapter, eight intents, real isolated filesystem writes, stale-source protection, preview nonmutation, and authorization refusal.
4. The host and portable compiled sources have the **same semantic fingerprint**, the complete expected intent set, and the same byte-identical shared pure domain core (`calculator-core.js` or `code-editor-core.js`).
5. The newly generated standalone package is rehashed byte-for-byte against the **current** source-generated binding before committing the active release pointer.
6. No source changes during the proof or staging; promotion is locked and the pointer is replaced atomically.

The two browser suites intentionally run different safe, bounded fixture inputs. Their successful results establish common intent/effect contracts and shared core implementation for tested scenarios; **they do not prove exhaustive equivalence, arbitrary provider behavior, arbitrary workspace mutations, CSS/layout pixel parity, or all production behavior.** No test doubles may grant an execution-time host proof.

## Authority boundary

`--portable` is a separate branch of the canonical `tools/mcel_app_promote.py` command. It is not the existing Calculator DSL-authority promotion and does not replace Code Editor's host facade, production scripts, route, registry claims or source authority. It creates a **deployable portable release** under `runtime/build/mcel/portable-promotions/`, not a live production host-route cutover. Production host routing remains unchanged. A future route cutover requires a separately designed and certified deployment gate; Patch 20 makes no such claim.

`runtime/build/mcel/portable-promotions/releases/<app>/<transaction-id>/` contains the complete generated standalone package and the release's current source, certificate and host evidence. `active/<app>.json` is the single atomic release-slot activation pointer. Neither location is an MCEL source-authority manifest. A rollback restores the previous validated pointer or deactivates the slot; it never undoes Code Editor source edits because no production source edit is performed.

## Windows commands (run from repository root)

Applying Patch 20 changes tracked source; **the old Patch 19 certificate is now stale**. Regenerate the live proof and its certificate, then regenerate canonical runtime/acceptance evidence against the same checkout:

```powershell
python tools/mcel_portable_intent_probe.py `
  --authorize-external `
  --model gemma4:26b-a4b-it-q4_K_M `
  --external-timeout-s 90 `
  --output .\runtime\state\mcel\port-proof-live-v5.json `
  --json
python tools/mcel_portable_certify.py `
  --proof .\runtime\state\mcel\port-proof-live-v5.json `
  --json
python main_computer/flog_mcel_runtime_smoke.py
python main_computer/mcel_acceptance_runner.py
python main_computer/mcel_truth_audit.py --require-portable-certification --check
```

Rehearse the fresh host-bound proof (no publication), then execute the **portable release** for each app:

```powershell
python tools/mcel_app_promote.py --app calculator --portable --rehearse
python tools/mcel_app_promote.py --app code-editor --portable --rehearse
python tools/mcel_app_promote.py --app calculator --portable --execute
python tools/mcel_app_promote.py --app code-editor --portable --execute
python main_computer/mcel_truth_audit.py --require-portable-certification --require-portable-promotion --check
```

`--execute` is explicit and requires an independent, fresh host proof again; it cannot reuse the rehearsal verdict. Inspect active release slots with `--portable --check`. To roll back a specific active release, use the printed transaction identifier:

```powershell
python tools/mcel_app_promote.py --app calculator --portable --rollback <transaction-id>
```

The rollback command refuses noncurrent, altered or invalid predecessor transactions. After rollback, rerun the canonical audit. Optional `--require-portable-promotion` blocks until **both** portable release slots are active and independently revalidated. An ordinary truth audit may report an inactive portable slot without making that an application maturity violation. `semantic_runtime_proven` and `promotion_ready` remain the existing, distinct host-runtime maturity measures.

## Failure modes

Missing or stale Patch 19 certificates, source drift, malformed or mismatched generated assets, host proof failures, missing safety refusal, stale filesystem hashes, incomplete host intent coverage, failed canonical audit, inaccurate shared core bytes, concurrent transactions, corrupted active pointers and mismatched rollback IDs all refuse activation. No failure falls back to the existing application promotion function or assumes a preexisting Code Editor DSL profile.

Active releases are checked against the current source and certificate each time. Source changes, certificate expiry or byte alteration revoke **eligibility** even if the old artifacts remain physically on disk. The runtime consumers are not automatically redirected to an expired release.

## Patch 20 Windows Calculator witness compatibility correction

The canonical Calculator host-bound observer emits two independent schema
identifiers: `schema: mcel.calculator-browser-parity-observation.v1` (the
application-specific witness), and
`genericSchema: mcel.host-bound-browser-observation.v1` (its generic host
observation contract). Both identifiers are preserved in the portable release
witness and checked independently. The earlier promotion verifier incorrectly
compared the application-specific `schema` to the generic identifier, causing
`host-witness-unverified` even for a passing Calculator host proof.

Changing the source invalidates source-bound certification and makes any
previously activated release ineligible until new evidence is generated.
Existing release directories/pointers are not deleted or bypassed; Code Editor
must be re-promoted under the new repository fingerprint. Regenerate the live
19-intent proof and independent certificate, then refresh canonical runtime
and acceptance reports, run both `--portable --rehearse` commands, and only
execute new portable releases after those checks pass. The production host
routes remain unchanged.

## Patch 20 Calculator semantic-source witness correction

A Calculator portable-release rehearsal previously verified the browser intent
and effect witness but deferred checking `semanticFingerprint` against the
staged portable manifest until `--execute`. The browser observer derives its
optional field from `catalogRecord.semanticFingerprint`, but the browser-safe
package catalog does not publish that top-level field. Hence an otherwise
successful browser observation yields `semanticFingerprint: ""` and execution
fails `host-and-portable-semantic-fingerprint-mismatch`, even though the
Calculator's host parity report has independently compiled the authoritative
DSL and reports the same semantic fingerprint as the portable compiler.

The host witness now records the independently compiled **host parity report**
`semanticFingerprint`, not the optional browser catalog field. The browser
value is retained separately as `browserSemanticFingerprint` for inspection.
Any nonempty browser value must agree with the host compile; an absent, empty,
or conflicting **host compile** fingerprint refuses the witness. The host
observation must still pass fresh Chromium intent/effect and schema checks.

The semantic fingerprint is checked against the independently generated
portable source binding during both `--rehearse` and `--execute`, before any
release staging or pointer mutation. The existing staging comparison and active
release revalidation remain in place. This does **not** substitute a successful
browser result with a static compile: both are required; only the provenance
of the semantic fingerprint is corrected. Code Editor's witness behavior and
the production routes are unchanged.

The canonical repository fingerprint still covers eligible source throughout
the repo, including unrelated Mother or test files. When another development
process modifies such source between live certification and promotion, the
certificate and runtime/acceptance bindings must be renewed against a stable
checkout. Do not remove those sources from the provenance scope or discard
rollback records to make the gate pass.
