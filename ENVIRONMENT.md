# Test Install

This folder is the test install of `main_computer`.

Default runtime:

- provider: `ollama`
- model: `qwen2.5:1.5b`
- workspace: `$env:USERPROFILE\dsl` on Windows, or the value of `MAIN_COMPUTER_WORKSPACE`
- optional debug passcode: `MAIN_COMPUTER_OLLAMA_DEBUG_PASSCODE`

This install includes the test suite so changes can be verified before copying or promoting them into `main_copmputer_production`.

Run tests:

```powershell
cd "$env:USERPROFILE\dsl\main_computer_test"
python -m unittest discover -s tests -v
```

Start the console viewport:

```powershell
python -m main_computer.cli viewport --port 8765
```

Quiet server signals:

```powershell
python -m main_computer.cli viewport --port 8765 -noverbose
```

Dev control helper for the test backend:

```powershell
cd "$env:USERPROFILE\dsl\main_computer_test"
.\dev-control.ps1 status
.\dev-control.ps1 start -Mode local
.\dev-control.ps1 shutdown -Mode local
.\dev-control.ps1 restart -Mode local
```

Use `dev-control.ps1` directly for manual work. `control-main-computer.ps1` is a legacy local-mode helper for automated callers and refuses manual runs unless the caller passes `--auto-allow`.


## ONLYOFFICE local workbook editor

The `ONLYOFFICE` application is separate from the existing `Spreadsheet` app. It stores native `.xlsx`
workbooks and opens them through ONLYOFFICE Docs.

Windows local installs default to ONLYOFFICE Docs running natively in WSL, not Docker. The control wrapper runs WSL service actions as root with `wsl.exe -u root` so the native package install does not stop for a Linux sudo password prompt:

```powershell
cd "$env:USERPROFILE\dsl\main_computer_test"
.\tools\onlyoffice\onlyoffice-control.ps1 install -Mode wsl -Port 18084
.\tools\onlyoffice\onlyoffice-control.ps1 start -Mode wsl -Port 18084
.\tools\onlyoffice\onlyoffice-control.ps1 doctor -Mode wsl -Port 18084
```

Main Computer defaults to:

```text
MAIN_COMPUTER_ONLYOFFICE_PUBLIC_URL=http://127.0.0.1:18084
MAIN_COMPUTER_ONLYOFFICE_INTERNAL_URL=http://127.0.0.1:18084
MAIN_COMPUTER_ONLYOFFICE_CALLBACK_BASE_URL=<auto-detected for local WSL unless explicitly set>
MAIN_COMPUTER_ONLYOFFICE_JWT_SECRET=main-computer-onlyoffice-local-secret
```

For the default Windows/WSL topology, the browser still opens Main Computer at
`http://127.0.0.1:8765`, but ONLYOFFICE receives workbook file and callback URLs using WSL's
Windows-host gateway address. The local dev controller binds the Windows viewport on `0.0.0.0` by
default so the WSL-native Document Server can download and save workbooks without manual env vars.

Docker remains available for containerized development or production-style deployments. Local Docker ONLYOFFICE binds to `127.0.0.1` by default so the editor port is not exposed to the LAN:


```powershell
$env:MAIN_COMPUTER_ONLYOFFICE_JWT_SECRET = "main-computer-onlyoffice-local-secret"
.\tools\onlyoffice\onlyoffice-control.ps1 start -Mode docker -Port 18084
```

For a Dockerized Main Computer service, set the callback base to the service URL that the ONLYOFFICE
container can reach, for example `http://main-computer:8765` inside the Compose network.


## Dev contract deployment runtime

Dev contract deployments now publish the same app-facing runtime shape that
production should use:

```text
runtime/deployments/current.json
```

`dev-chain-reset.py` still writes the legacy dev-chain files for local operator
scripts:

```text
runtime/dev-chain/latest.json
runtime/dev-chain/latest.env
```

The app prefers `runtime/deployments/current.json` and only falls back to the
legacy dev-chain file when the production-shaped deployment publication is
missing. The deployment publication is sanitized: it contains RPC URL, chain ID,
public contract addresses, public office addresses, and deployment metadata, but
not mnemonic or private-key material.

Deploy a local dev chain and publish the current deployment:

```powershell
python .\dev-chain-reset.py --yes --run-id any-user-frobber-v1 --port-strategy auto
```

Smoke-check the published deployment:

```powershell
python .\dev-chain-smoke.py
```

The smoke command defaults to `runtime/deployments/current.json`, matching the
runtime the viewport reads.

Export helper for ChatGPT:

```powershell
cd "$env:USERPROFILE\dsl\main_computer_test"
.\export-main-computer-test.ps1
```

It writes a timestamped zip to `$env:USERPROFILE\dsl\archive` and keeps `aider.log` in the bundle.

Default text console:

```text
http://127.0.0.1:8765
```

Graphical widget test:

```text
http://127.0.0.1:8765/graphical
```

Text and graphical modes share the same browser session, so switching modes preserves the transcript and prompt draft.

The graphical mode includes a Buddhabrot widget rendered in the browser. Axis convention: x is imaginary, y is real. It runs continuously with controls for orbits per slice and delay between displayed slices.

Prompt sends show a spinner and progress strip while the computer is working.

The viewport polls `/api/workspace-timestamp` every few seconds and warns when the loaded console is older than the local directory timestamp.

Ollama Debug Mode is separate from the normal command viewports:

```text
http://127.0.0.1:8765/debug/text
http://127.0.0.1:8765/debug/graphical
```

Enable it from either debug interface to talk directly to local Ollama with `qwen2.5:1.5b` and to read, write, or ask the local model to revise files inside the running project. Leave `MAIN_COMPUTER_OLLAMA_DEBUG_PASSCODE` unset for local open debug mode, or set it to require the passcode before activation.

Debug assets are stored in `debug_assets` under the running project. Use them for scan logs, model notes, generated snippets, and other debug artifacts that need to be listed and reloaded later.

Run the widget harness:

```powershell
python -m main_computer.cli harness
```

Run layered diagnostics:

```powershell
python -m main_computer.cli diagnostics --level widgets
```

The viewport has five diagnostic buttons:

```text
Level 1 functional
Level 2 live
Level 3 widgets
Level 4 server
Level 5 health
```

## ONLYOFFICE local workbook editor

The standalone `ONLYOFFICE` app uses native `.xlsx` files and keeps the existing
`Spreadsheet` app unchanged.

Windows local installs default to **WSL-native ONLYOFFICE Docs** rather than
Docker. The default local Document Server URL is:

```text
http://127.0.0.1:18084
```

Install and control the WSL-native server:

```powershell
cd "$env:USERPROFILE\dsl\main_computer_test"

.\tools\onlyoffice\onlyoffice-control.ps1 install
.\tools\onlyoffice\onlyoffice-control.ps1 start
.\tools\onlyoffice\onlyoffice-control.ps1 status
```

Run the viewport with the matching local defaults; no ONLYOFFICE environment variables are
required for the standard Windows/WSL setup:

```powershell
.\dev-control.ps1 restart -Mode local
```

Doctor check:

```powershell
.\tools\onlyoffice\onlyoffice-control.ps1 doctor
```

Docker remains available as a fallback, but is not the default Windows local
path:

```powershell
.\tools\onlyoffice\onlyoffice-control.ps1 start -Mode docker
```

For production, set stable public/internal URLs and a long-lived JWT secret:

```text
MAIN_COMPUTER_ONLYOFFICE_PUBLIC_URL=https://office.example.com
MAIN_COMPUTER_ONLYOFFICE_INTERNAL_URL=http://onlyoffice:80
MAIN_COMPUTER_ONLYOFFICE_CALLBACK_BASE_URL=https://main.example.com
MAIN_COMPUTER_ONLYOFFICE_JWT_SECRET=<stable-secret>
```



ONLYOFFICE Docs uses port `18084` by default because local platform site publishing owns ports `18080`-`18083`.
