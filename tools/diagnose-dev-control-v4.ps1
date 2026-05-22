$ErrorActionPreference = "Stop"

$workspace = (Get-Location).Path
$python = Join-Path $workspace ".venv\Scripts\python.exe"
if (-not (Test-Path $python) -and $env:VIRTUAL_ENV) {
  $python = Join-Path $env:VIRTUAL_ENV "Scripts\python.exe"
}
if (-not (Test-Path $python)) {
  $python = "python"
}

$out = ".\main_computer_direct_viewport.out.log"
$err = ".\main_computer_direct_viewport.err.log"

Write-Host "python: $python"
Write-Host "workspace: $workspace"
Write-Host "starting viewport directly, bypassing dev-control/control-main-computer CIM checks..."

$p = Start-Process -FilePath $python `
  -ArgumentList @("-B", "-m", "main_computer.cli", "viewport", "--host", "0.0.0.0", "--port", "8765", "--workspace", $workspace) `
  -WorkingDirectory $workspace `
  -RedirectStandardOutput $out `
  -RedirectStandardError $err `
  -PassThru

Write-Host "started pid $($p.Id)"
Set-Content ".\main_computer_direct_viewport.pid" $p.Id

$ready = $false
for ($i = 1; $i -le 20; $i++) {
  try {
    Invoke-RestMethod "http://127.0.0.1:8765/api/projects" -TimeoutSec 2 | Out-Null
    Write-Host "READY: viewport answered on http://127.0.0.1:8765/api/projects"
    $ready = $true
    break
  } catch {
    Write-Host "not ready yet: attempt $i"
    Start-Sleep -Seconds 1
  }
}

if (-not $ready) {
  Write-Host "`nNOT READY. stdout tail:"
  Get-Content $out -Tail 80 -ErrorAction SilentlyContinue

  Write-Host "`nstderr tail:"
  Get-Content $err -Tail 120 -ErrorAction SilentlyContinue
}

Write-Host "`nListeners without CIM:"
cmd /c "netstat -ano | findstr /r /c:':8765 .*LISTENING'"