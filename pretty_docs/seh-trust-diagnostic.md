# SEH trust diagnostic (read-only)

Run after an SEH `bind-slh` refusal to identify which trust field fails:

```powershell
python .\seh_mutate_harness.py diagnostic
```

The command rejects operator-supplied SEH names or host selectors. It resolves the sole legacy registration from Mother's verified state, refuses when multiple registrations exist, and requires an existing application UUID. It uses the SEH's existing Mother-verified Coolify identity, verifies that the queried Coolify application has the registered UUID, name, and ownership marker, and reads only that application's environment variable inventory. It also loads the *existing* SLH signing identity when readable; it does not create or change an identity. It does not use SQL.

The active relay trust anchor is **only** `SEH_SLH_PUBLIC_KEY_B64`: the
existing enrolled SLH P-256 public signing key. The diagnostic reports SHA-256
digests of valid public PEM bytes, not the key contents. `MATCH`, `MISMATCH`,
`MISSING`, `MASKED`, and malformed-key findings are read-only observations;
masked values never count as a verified match. Legacy Coolify fields such as
`SEH_SLH_ID` and `SEH_ORGANIZATION_ID` are obsolete, confer no authority,
and are not required for a successful trust diagnostic.

`TRUST_MATCH` means the registered Coolify public key matches the expected
public key. It does not prove a completed deployment, a running relay, or an
authenticated WSS tunnel. The guarded `bind-slh --replace-existing-trust`
operation remains required for an authorized change of the signing key.
