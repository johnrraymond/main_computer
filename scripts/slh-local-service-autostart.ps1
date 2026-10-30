# Opt-in OS boot registration for the localhost-only SLH control plane.
# Run PowerShell as administrator. Does not enable organizational network serving.
[CmdletBinding()]
param(
    [ValidateSet('install', 'uninstall', 'status')]
    [string]$Action = 'status',
    [string]$Root = (Split-Path -Parent $PSScriptRoot),
    [string]$Python = ''
)
$ErrorActionPreference = 'Stop'
$taskName = 'MainComputer-SLH-Local'
$rootPath = (Resolve-Path -LiteralPath $Root).Path
if ($Action -eq 'status') {
    $task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    if ($null -eq $task) { Write-Output 'SLH_LOCAL_BOOT_TASK_NOT_INSTALLED' }
    else { $task | Select-Object TaskName, State, TaskPath | Format-List }
    exit 0
}
$admin = [Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
if (-not $admin.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Run as an elevated Windows administrator'
}
if ($Action -eq 'uninstall') {
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction SilentlyContinue
    Write-Output 'SLH_LOCAL_BOOT_TASK_REMOVED'
    exit 0
}
if ([string]::IsNullOrWhiteSpace($Python)) {
    $Python = Join-Path $rootPath '.venv\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
        throw 'Specify -Python with the installed absolute python.exe path (venv not found)'
    }
}
$Python = (Resolve-Path -LiteralPath $Python).Path
$launcher = Join-Path $rootPath 'scripts\slh-local-service-boot.ps1'
$arguments = '-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "' + $launcher + '" -Root "' + $rootPath + '" -Python "' + $Python + '"'
$taskAction = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument $arguments -WorkingDirectory $rootPath
$trigger = New-ScheduledTaskTrigger -AtStartup
$principal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest
$settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 5 -RestartInterval (New-TimeSpan -Minutes 1) -MultipleInstances IgnoreNew -StartWhenAvailable
Register-ScheduledTask -TaskName $taskName -Action $taskAction -Trigger $trigger -Principal $principal -Settings $settings -Force | Out-Null
Write-Output 'SLH_LOCAL_BOOT_TASK_INSTALLED (starts at next Windows boot; only loopback:8897)'
