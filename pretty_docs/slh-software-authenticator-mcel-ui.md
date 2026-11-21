# MCEL SLH Software Authenticator

## Scope

This patch implements a software authenticator in the **SLH MCEL application**.
It does not change the Worker application, Worker enrollment interface, existing
P-256 identity cryptography, or SEH deployment behavior.

## Local SLH administrator workflow

1. Open the MCEL SLH application and select **This Computer**.
2. Select the established local administrator P-256 identity and authenticate
   through the existing SLH login process.
3. Open the **Software Authenticator** notebook and its **Authenticator** tab.
4. Choose **Pair** once for the selected administrator and verified local SLH.
   Pairing consumes the first 30-second TOTP step. Wait for the next step.
5. Select **Show current passkey**, then **Show** in the masked modal. **Copy**
   copies the visible eight-digit code. The code expires automatically, is
   accepted only once, and is not saved to MCEL state or operation receipts.
6. **Revoke** disables the current software authenticator on this SLH.
   Pairing and revocation require explicit confirmation.

The **Status** tab reports pairing state without displaying secret material.
Opening the notebook or selecting an identity must not start a new P-256 login.

## Security and authority

- The existing P-256 administrator identity and authenticated ORG-SLH session
  authorize pairing, display, and revocation.
- The software-authenticator seed is held in the local identity store and the
  corresponding server-side factor. It is not returned to the browser.
- Codes are per-administrator and per-verified SLH, rotate every 30 seconds,
  and cannot be accepted twice for the same TOTP step.
- The current code travels only through a credential-gated same-origin local
  route. Responses set Cache-Control: no-store. The browser holds the code
  temporarily in a modal, not MCEL's observable state, activity log, or
  operation receipt. The modal closes on timeout, tab hiding, identity change,
  page dismissal, or session invalidation.
- Only the selected, verified local ORG-SLH can be managed from the initial
  interface. Remote Network SLH and SEH authenticator management have not been
  enabled by this change.
- Existing one-time enrollment codes and P-256 challenge-signature workflows
  remain available.

## Verification performed

Focused pytest tests exercise the authority, API, SLH package, route admission,
UI operation contract and Chromium show/hide/copy/close workflow with mocked
responses. No live remote SLH or real enrollment transaction is claimed.

Run from the repository root:

```powershell
python -m pytest -q `
  tests/test_mcel_slh_software_authenticator_browser.py `
  tests/test_mcel_slh_software_authenticator_ui.py `
  tests/test_slh_software_authenticator.py `
  mcel_apps/slh/tests/test_package.py `
  tests/test_mcel_slh_configure_enable_disable.py
```

## Applying

The cumulative archive includes the prior backend software-authenticator patch
plus the MCEL SLH user interface and route changes, and targets the original
`main_computer_test-20261120-164123.zip` snapshot. Its paths are relative to
repository root. Applying it over an unmodified later snapshot is not verified.
