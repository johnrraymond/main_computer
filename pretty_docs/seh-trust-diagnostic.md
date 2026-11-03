# SEH trust diagnostic (read-only)

Run after an SEH `bind-slh` refusal to identify which trust field fails:

```powershell
python .\seh_mutate_harness.py diagnostic
```

The command rejects operator-supplied SEH names or host selectors. It resolves the sole legacy registration from Mother's verified state, refuses when multiple registrations exist, and requires an existing application UUID. It uses the SEH's existing Mother-verified Coolify identity, verifies that the queried Coolify application has the registered UUID, name, and ownership marker, and reads only that application's environment variable inventory. It also loads the *existing* SLH signing identity when readable; it does not create or change an identity. It does not use SQL.

The output reports `SEH_ORGANIZATION_ID` and `SEH_SLH_ID` independently, showing expected and observed identifiers when they are ordinary valid IDs. For `SEH_SLH_PUBLIC_KEY_B64`, it shows only SHA-256 digests of valid public PEM bytes, never the key contents. Each field distinguishes `MATCH`, `MISMATCH`, `MISSING`, `EMPTY`, `MASKED`, and `DUPLICATE` where applicable. A masked value is not proof of a mismatch. When the local signing key is inaccessible, organization and SLH ID checks still run.

A `TRUST_MATCH` means only that Coolify's visible configured trust fields match the local expected values. It does not verify a finished redeploy, a running relay's environment, or authenticated public WSS traffic. A `TRUST_DIAGNOSTIC_FINDINGS` report is not authorization to change an organization/SLH ID. The separate `bind-slh --replace-existing-trust` permission authorizes key rotation only when both organization and SLH IDs already agree.
