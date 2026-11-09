# P4 — Local-only Viewport binding and launcher consolidation

**Scope.** The Viewport's trusted local interface and its heartbeat control
listener. This phase does not alter Stable Hub, Dev-Test Hub, Secure Local Hub,
Secure External Hub, or any production network contract. No HTTP login is added.

## Implementation

- `dev-control.ps1` and the compatibility `control-main-computer.ps1` default
  to `-BindHost 127.0.0.1` and reject any other bind host before service
  detection, process shutdown, PID cleanup, or Docker operations. The legacy
  argument parser is validated **after** interpreting its remaining arguments.
- `main_computer.app_control` validates before replacing the previously running
  application or heartbeat. Direct CLI Viewport/heartbeat entrypoints and the
  underlying Viewport/heartbeat server constructors also fail closed.
- The accepted local host is the numeric IPv4 literal `127.0.0.1`, not
  `localhost`, `0.0.0.0`, `::`, or a LAN IP. Alternate **ports** remain valid.
- The optional Docker Compose development Viewport binds to `0.0.0.0` **inside
  the container only**. Its host port maps
  `127.0.0.1:${MAIN_COMPUTER_DOCKER_VIEWPORT_PORT:-18765}:8765`. A container
  marker and explicit environment setting are both required for the internal
  Viewport exception; heartbeat always binds to `127.0.0.1` even in Docker.

## Windows acceptance (no infrastructure mutation)

Run the focused test suite:

```powershell
python -m pytest -q tests/test_viewport_local_bind_p4.py tests/test_app_control.py
```

Start Main Computer in the normal way (your normal Dev-Test Hub / SLH choices
are independent), then inspect the actual host listening addresses:

```powershell
Get-NetTCPConnection -State Listen |
  Where-Object { $_.LocalPort -in 8765, 8766, 18765 } |
  Select-Object LocalAddress, LocalPort, OwningProcess
```

The Windows Viewport and heartbeat listeners must be `127.0.0.1` (not
`0.0.0.0`, `::`, or a LAN IP). In Docker mode confirm host publication:

```powershell
docker compose -f docker-compose.dev.yml --profile app ps
```

Host publication must explicitly show `127.0.0.1:18765->8765/tcp` (or the
selected alternate port). Containers sharing the Docker network are **not**
covered by that host publication restriction.

Before a negative start test, start the normal local Viewport and record its
PID. Then check **without stopping it**:

```powershell
python -m main_computer.cli viewport --host 0.0.0.0
python -m main_computer.cli heartbeat --host 0.0.0.0
python -m main_computer.app_control --host 0.0.0.0 run
.\dev-control.ps1 start -Mode local -BindHost 0.0.0.0
.\control-main-computer.ps1 start -BindHost 0.0.0.0 --auto-allow
```

All five must refuse before mutating process state. Confirm the original PID
and listener are still present. Repeat with a LAN IP and `::` if needed.

**Limitations.** Source tests run on non-Windows hosts cannot prove live
PowerShell behavior, Docker's host port publication, firewall visibility, or
actual listener ownership. Those require Windows operational acceptance.
Do not infer that fixing the Viewport's local binding changes the security
of other services or containers.
