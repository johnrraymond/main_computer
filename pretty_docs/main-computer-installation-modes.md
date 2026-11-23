# Main Computer installation modes and concurrent operation

## Deployment rule

**A raw development checkout and an installed Unleashed instance are mutually
exclusive alternatives for the same primary Main Computer slot.** They both
use the default application port `8765` and must never be expected to run
simultaneously. Do not design or require an HTTPS virtual-host multiplexer just
to make these two choices coexist.

| MC surface | Browser identity (planned) | Default app port | Concurrent policy |
| --- | --- | --- | --- |
| Raw source checkout | `dev.main-computer.local` | `8765` | Not with Unleashed |
| Installed Unleashed | `unleashed.main-computer.local` | `8765` | Not with raw checkout |
| Debug | `debug.main-computer.local` | `28865` | With either primary choice, and Safe |
| Safe | `safe.main-computer.local` | `38865` | With either primary choice, and Debug |
| Shared ONLYOFFICE Document Server | `onlyoffice.main-computer.local` | existing shared `18085` backend | One shared service |

The Development and Unleashed names distinguish the *type of deployment*, not
concurrently running virtual hosts on port 8765. The runtime's current raw
source launch context uses Unleashed defaults. Browser-facing HTTPS and the
`.local` names remain subject to the hostname/security cutover acceptance gate;
they are not a claim that these addresses are already served.

## One authentication authority

For a given Windows user, Debug, Safe and whichever primary installation is
active use **one persistent authentication authority and underlying session**.
Each browser hostname can use its own host-only cookie; an approved single
sign-on handoff must refer back to the same authority. The machine-wide
ONLYOFFICE Document Server is not a second user-login authority: it accepts
narrowly scoped document grants tied to the source installation and document.
Distinct Windows users must not share their private authority or sessions.

## Runtime safety and acceptance

The mutual-exclusion rule is normative, **not a claim that a live conflict
refusal has already passed**. Until the Windows live acceptance gate proves it,
a second start of the 8765 lane must not be assumed to safely replace or attach
to the first installation. A proper test starts one primary mode, attempts the
other, and verifies that the result is a clear refusal or verified controlled
handoff—not two running primary instances, accidental termination of unrelated
processes, or unauthorized access.

The concurrency acceptance should run either
`(Development, Debug, Safe)` or `(Unleashed, Debug, Safe)`, never all four MC
modes at once. It must verify distinct Debug/Safe ports, a single per-user HTTPS
authentication service owner, and one shared ONLYOFFICE Document Server.

Use `python -m tools.mcel_security_ingress_smoke run --hostname-only` to see
which parts of this contract remain unimplemented, or unverified in the actual
Windows runtime. A static naming check does not establish listener ownership,
TLS trust, credential isolation or real process-level exclusivity.
