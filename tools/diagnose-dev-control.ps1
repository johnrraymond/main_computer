$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$out = ".\hang-probe-$stamp.out.log"
$err = ".\hang-probe-$stamp.err.log"
$psExe = (Get-Process -Id $PID).Path
if (-not $psExe) { $psExe = "powershell.exe" }

$args = @(
  "-NoProfile",
  "-ExecutionPolicy", "Bypass",
  "-File", (Resolve-Path ".\control-main-computer.ps1").Path,
  "start",
  "-BindHost", "0.0.0.0",
  "-Port", "8765",
  "-Workspace", (Get-Location).Path,
  "-StartTimeoutSeconds", "8",
  "-MathicsApiTimeoutSeconds", "30",
  "--auto-allow"
)

Write-Host "Starting probe child process..."
$p = Start-Process -FilePath $psExe -ArgumentList $args -RedirectStandardOutput $out -RedirectStandardError $err -PassThru -WindowStyle Hidden

if (-not (Wait-Process -Id $p.Id -Timeout 25 -ErrorAction SilentlyContinue)) {
  Write-Host "PROBE TIMED OUT; killing child PowerShell pid $($p.Id)"
  Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue
} else {
  Write-Host "PROBE EXITED with code $($p.ExitCode)"
}

Write-Host "`n==== probe stdout ===="
Get-Content $out -Tail 200 -ErrorAction SilentlyContinue

Write-Host "`n==== probe stderr ===="
Get-Content $err -Tail 200 -ErrorAction SilentlyContinue

Write-Host "`n==== viewport stderr ===="
Get-Content ".\main_computer_viewport.err.log" -Tail 120 -ErrorAction SilentlyContinue

Write-Host "`n==== heartbeat stderr ===="
Get-Content ".\main_computer_heartbeat.err.log" -Tail 120 -ErrorAction SilentlyContinue

Write-Host "`n==== python processes ===="
Get-CimInstance Win32_Process |
  Where-Object { $_.CommandLine -like "*main_computer.cli*" } |
  Select-Object ProcessId, Name, ExecutablePath, CommandLine |
  Format-List

Write-Host "`n==== listeners ===="
Get-NetTCPConnection -LocalPort 8765,8766 -State Listen -ErrorAction SilentlyContinue |
  ForEach-Object {
    $proc = Get-CimInstance Win32_Process -Filter "ProcessId = $($_.OwningProcess)" -ErrorAction SilentlyContinue
    [pscustomobject]@{
      Port = $_.LocalPort
      PID = $_.OwningProcess
      Name = $proc.Name
      CommandLine = $proc.CommandLine
    }
  } |
  Format-List