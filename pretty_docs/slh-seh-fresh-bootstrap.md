# Fresh SLH relay identity bootstrap (no migration / no SQL)

> **Historical manual bootstrap diagnostic, not the current desired setup path.**
> Production SLH setup must durably prepare and preserve its signing identity.
> The `identity` command cannot repair an established SLH missing a signing
> key and must not be used as a substitute for a governed identity migration.

`python -m tools.slh_seh_connector fresh-smoke` checks directory ACL, new P-256 signing-key ACL, read-back and reloaded identity in a disposable directory. It leaves `runtime/state/slh` unchanged, and does not contact Coolify or create a database.

For an intentional clean start (not an upgrade), stop running SLH services before retiring `runtime/state/slh`. Preserve the old directory in a private, non-Git backup; do not delete it. Recreating a local identity revokes the previous relay identity: **the existing SEH must separately be authorized to trust the new public key before `/readyz` can pass.** This patch does not silently mutate the existing Coolify trust binding.

Then run `python -m tools.slh_seh_connector identity` on a fresh empty `runtime/state/slh` and rerun the same command to verify the public key is unchanged. The command cannot create a missing key alongside existing SLH state, will not overwrite an existing key, and refuses success if file ACL protection or read-back fails. No `organization.sqlite3` is required for SEH identity creation; infrastructure `deploy-seh` remains independent of local organizational databases.

Windows ACL policy: SLH directory (operator SID, SYSTEM, Administrators; inheritance disabled); P-256 private key file (same principals, explicit file-level grant). Directory hardening is **not recursive** and does not rewrite ACLs on other existing files. Non-Windows uses `0700` directory and `0600` file.

The disposable test does not prove external SEH trust, relay authentication, or remote WSS readiness. For operational acceptance check that the new public key is explicitly bound on the already-deployed SEH, `seh_outbound_authenticated` appears, and `/readyz` reports `slh_connected: true`.
