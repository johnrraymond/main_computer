[CmdletBinding()]
param(
    [string]$RepoRoot = (Get-Location).Path,

    [ValidateSet("Unleashed", "Debug", "Safe")]
    [string]$Mode = "Debug",

    [int]$TimeoutSeconds = 8,

    [int]$BootstrapProbeTimeoutSeconds = 90,

    [switch]$RunBootstrapPrecheck
)

$ErrorActionPreference = "Continue"
$script:DefaultTimeoutSeconds = $TimeoutSeconds

$diagRoot = Join-Path $RepoRoot "runtime\diagnostics"
New-Item -ItemType Directory -Force -Path $diagRoot | Out-Null

$script:LogFile = Join-Path $diagRoot ("bootstrap-precheck-hang-{0}.log" -f (Get-Date -Format "yyyyMMdd-HHmmss"))

function Write-Log {
    param([string]$Message = "")
    $line = if ([string]::IsNullOrEmpty($Message)) {
        ""
    }
    else {
        "[{0:HH:mm:ss}] {1}" -f (Get-Date), $Message
    }
    Write-Host $line
    Add-Content -LiteralPath $script:LogFile -Value $line
}

function Write-JsonBlock {
    param(
        [string]$Title,
        [object]$Object
    )

    Write-Log ""
    Write-Log $Title
    Write-Log ("-" * $Title.Length)

    try {
        $json = $Object | ConvertTo-Json -Depth 12
        Add-Content -LiteralPath $script:LogFile -Value $json
        Write-Host $json
    }
    catch {
        $text = $Object | Out-String
        Add-Content -LiteralPath $script:LogFile -Value $text
        Write-Host $text
    }
}

function Invoke-TimedJob {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][scriptblock]$ScriptBlock,
        [object[]]$ArgumentList = @(),
        [int]$Seconds = $script:DefaultTimeoutSeconds
    )

    Write-Log "START timed probe: $Name timeout=${Seconds}s"
    $started = Get-Date
    $job = $null

    try {
        $job = Start-Job -ScriptBlock $ScriptBlock -ArgumentList $ArgumentList

        if (-not (Wait-Job -Job $job -Timeout $Seconds)) {
            Stop-Job -Job $job -ErrorAction SilentlyContinue | Out-Null
            $elapsed = [Math]::Round(((Get-Date) - $started).TotalSeconds, 2)

            return [pscustomobject]@{
                Name = $Name
                Status = "TIMEOUT"
                Seconds = $elapsed
                Output = @()
                Errors = "Timed out after ${Seconds}s"
            }
        }

        $output = @(Receive-Job -Job $job -ErrorAction SilentlyContinue)
        $errors = @(
            $job.ChildJobs |
                ForEach-Object { $_.Error } |
                ForEach-Object { $_.ToString() }
        )

        $elapsed = [Math]::Round(((Get-Date) - $started).TotalSeconds, 2)

        return [pscustomobject]@{
            Name = $Name
            Status = if ($errors.Count -gt 0) { "ERROR" } else { "OK" }
            Seconds = $elapsed
            Output = $output
            Errors = ($errors -join "`n")
        }
    }
    finally {
        if ($null -ne $job) {
            Remove-Job -Job $job -Force -ErrorAction SilentlyContinue
        }
    }
}

Write-Log "Bootstrap precheck hang diagnostic"
Write-Log "RepoRoot: $RepoRoot"
Write-Log "Mode: $Mode"
Write-Log "LogFile: $script:LogFile"

$bootstrap = Join-Path $RepoRoot "bootstrap-main-computer-windows.ps1"

Write-Log ""
Write-Log "Checking bootstrap source around suspected hang point..."

if (Test-Path -LiteralPath $bootstrap) {
    $sourceHits = Select-String -LiteralPath $bootstrap `
        -Pattern "Side-by-side policy|Test-ModePortAvailability|Get-NetTCPConnection" `
        -Context 2, 4 |
        ForEach-Object { $_.ToString() }

    Write-JsonBlock "Bootstrap source matches" $sourceHits
}
else {
    Write-Log "WARN bootstrap-main-computer-windows.ps1 not found at: $bootstrap"
}

$ports = @(
    [pscustomobject]@{ Mode = "Unleashed"; Kind = "app";       Port = 8765  },
    [pscustomobject]@{ Mode = "Unleashed"; Kind = "heartbeat"; Port = 8766  },
    [pscustomobject]@{ Mode = "Debug";     Kind = "app";       Port = 28865 },
    [pscustomobject]@{ Mode = "Debug";     Kind = "heartbeat"; Port = 28866 },
    [pscustomobject]@{ Mode = "Safe";      Kind = "app";       Port = 38865 },
    [pscustomobject]@{ Mode = "Safe";      Kind = "heartbeat"; Port = 38866 }
)

Write-JsonBlock "Mode lane ports that bootstrap checks immediately after Side-by-side policy" $ports

$moduleProbe = Invoke-TimedJob `
    -Name "Import NetTCPIP module" `
    -Seconds $TimeoutSeconds `
    -ScriptBlock {
        Import-Module NetTCPIP -ErrorAction Stop
        Get-Command Get-NetTCPConnection -ErrorAction Stop | Select-Object Name, ModuleName, Version
    }

Write-JsonBlock "NetTCPIP module probe" $moduleProbe

foreach ($p in $ports) {
    $probe = Invoke-TimedJob `
        -Name ("Get-NetTCPConnection {0} {1} port {2}" -f $p.Mode, $p.Kind, $p.Port) `
        -Seconds $TimeoutSeconds `
        -ArgumentList @($p.Port) `
        -ScriptBlock {
            param([int]$Port)

            $rows = @(
                Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
                    Select-Object LocalAddress, LocalPort, State, OwningProcess
            )

            $enriched = foreach ($row in $rows) {
                $proc = $null
                try {
                    $proc = Get-Process -Id $row.OwningProcess -ErrorAction SilentlyContinue
                }
                catch {}

                [pscustomobject]@{
                    LocalAddress = $row.LocalAddress
                    LocalPort = $row.LocalPort
                    State = $row.State
                    OwningProcess = $row.OwningProcess
                    ProcessName = if ($proc) { $proc.ProcessName } else { "" }
                    Path = if ($proc) { $proc.Path } else { "" }
                }
            }

            if ($enriched.Count -eq 0) {
                [pscustomobject]@{
                    LocalPort = $Port
                    Listener = "none"
                }
            }
            else {
                $enriched
            }
        }

    Write-JsonBlock ("Get-NetTCPConnection result for port {0}" -f $p.Port) $probe
}

$netstatProbe = Invoke-TimedJob `
    -Name "netstat fallback listener scan" `
    -Seconds $TimeoutSeconds `
    -ScriptBlock {
        cmd.exe /c "netstat -ano -p tcp"
    }

Write-JsonBlock "Raw netstat fallback status" @{
    Name = $netstatProbe.Name
    Status = $netstatProbe.Status
    Seconds = $netstatProbe.Seconds
    Errors = $netstatProbe.Errors
}

if ($netstatProbe.Status -eq "OK") {
    $netstatText = ($netstatProbe.Output -join "`n")
    $netstatRows = @()

    foreach ($p in $ports) {
        $regex = "^\s*TCP\s+\S+:$($p.Port)\s+\S+\s+LISTENING\s+(\d+)\s*$"
        $matches = @($netstatText -split "`r?`n" | Where-Object { $_ -match $regex })

        foreach ($m in $matches) {
            $pidText = ([regex]::Match($m, $regex)).Groups[1].Value
            $procName = ""
            $procPath = ""

            try {
                $proc = Get-Process -Id ([int]$pidText) -ErrorAction SilentlyContinue
                if ($proc) {
                    $procName = $proc.ProcessName
                    $procPath = $proc.Path
                }
            }
            catch {}

            $netstatRows += [pscustomobject]@{
                Mode = $p.Mode
                Kind = $p.Kind
                Port = $p.Port
                Pid = $pidText
                ProcessName = $procName
                Path = $procPath
                Raw = $m.Trim()
            }
        }

        if ($matches.Count -eq 0) {
            $netstatRows += [pscustomobject]@{
                Mode = $p.Mode
                Kind = $p.Kind
                Port = $p.Port
                Pid = ""
                ProcessName = ""
                Path = ""
                Raw = "no listener"
            }
        }
    }

    Write-JsonBlock "Parsed netstat listeners for bootstrap mode ports" $netstatRows
}

$psProbe = Invoke-TimedJob `
    -Name "running PowerShell bootstrap processes" `
    -Seconds $TimeoutSeconds `
    -ScriptBlock {
        Get-CimInstance Win32_Process |
            Where-Object {
                $_.Name -in @("powershell.exe", "pwsh.exe") -and
                $_.CommandLine -match "bootstrap-main-computer-windows|main-computer|new_patch|coolify"
            } |
            Select-Object ProcessId, ParentProcessId, Name, CommandLine
    }

Write-JsonBlock "Relevant PowerShell processes" $psProbe

$dockerProbe = Invoke-TimedJob `
    -Name "Docker Coolify containers and compose ownership" `
    -Seconds 15 `
    -ScriptBlock {
        $docker = Get-Command docker -ErrorAction SilentlyContinue
        if (-not $docker) {
            return [pscustomobject]@{ Docker = "not found" }
        }

        $names = @(
            docker ps -a --filter "name=coolify" --format "{{.Names}}" 2>$null
        )

        if ($names.Count -eq 0) {
            return [pscustomobject]@{ Docker = "found"; CoolifyContainers = "none" }
        }

        foreach ($name in $names) {
            try {
                $c = docker inspect $name | ConvertFrom-Json
                [pscustomobject]@{
                    Name = $c[0].Name
                    Image = $c[0].Config.Image
                    Status = $c[0].State.Status
                    Health = if ($c[0].State.Health) { $c[0].State.Health.Status } else { "" }
                    ComposeProject = $c[0].Config.Labels.'com.docker.compose.project'
                    ComposeService = $c[0].Config.Labels.'com.docker.compose.service'
                    WorkingDir = $c[0].Config.Labels.'com.docker.compose.project.working_dir'
                    ConfigFile = $c[0].Config.Labels.'com.docker.compose.project.config_files'
                    Ports = ($c[0].NetworkSettings.Ports | ConvertTo-Json -Depth 8 -Compress)
                }
            }
            catch {
                [pscustomobject]@{
                    Name = $name
                    Error = $_.Exception.Message
                }
            }
        }
    }

Write-JsonBlock "Docker/Coolify ownership probe" $dockerProbe

if ($RunBootstrapPrecheck) {
    if (Test-Path -LiteralPath $bootstrap) {
        $bootstrapProbe = Invoke-TimedJob `
            -Name "bootstrap -PrecheckOnly reproduction" `
            -Seconds $BootstrapProbeTimeoutSeconds `
            -ArgumentList @($bootstrap, $Mode) `
            -ScriptBlock {
                param([string]$BootstrapPath, [string]$ModeName)

                & powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass `
                    -File $BootstrapPath `
                    -Mode $ModeName `
                    -PrecheckOnly `
                    -LocalCoolifyMode disabled `
                    -SkipOllamaCheck 2>&1 |
                    ForEach-Object { $_.ToString() }

                "EXITCODE=$LASTEXITCODE"
            }

        Write-JsonBlock "Bootstrap -PrecheckOnly reproduction" $bootstrapProbe
    }
    else {
        Write-Log "Skipping bootstrap reproduction because bootstrap file was not found."
    }
}

Write-Log ""
Write-Log "Interpretation:"
Write-Log "1. If any Get-NetTCPConnection probe says TIMEOUT, the bootstrap is likely hanging inside Windows NetTCPIP listener inspection."
Write-Log "2. If Get-NetTCPConnection times out but netstat succeeds, bootstrap should use a timed job or netstat fallback for port checks."
Write-Log "3. If a port has a listener, the log maps it to PID/process so we know whether it is Main Computer, another dev server, Docker, or something else."
Write-Log "4. If Docker/Coolify WorkingDir points to a different repo, a prior install/dev tree owns that Coolify container."
Write-Log ""
Write-Log "DONE. Send this log back if you want me to patch the bootstrap:"
Write-Log $script:LogFile