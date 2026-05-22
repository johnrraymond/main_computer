param(
    [Parameter(Mandatory = $true)]
    [string]$InstallRoot,

    [string]$InstanceName = "maincomputer",

    [ValidateSet("Unleashed", "Debug", "Safe")]
    [string]$Mode = "Debug",

    [switch]$FullScan,

    [switch]$StopWsl,

    [switch]$StopDockerContainersUsingInstallRoot,

    [int]$TimeoutSeconds = 60
)

$ErrorActionPreference = "Stop"

function Write-Section($Title) {
    Write-Host ""
    Write-Host $Title
    Write-Host ("-" * $Title.Length)
}

function Convert-ToFullPath([string]$Path) {
    return [System.IO.Path]::GetFullPath($Path)
}

function Test-ReadableFile([string]$Path) {
    $result = [ordered]@{
        Path = $Path
        Exists = $false
        Kind = "unknown"
        Readable = $false
        Error = ""
    }

    try {
        if (-not (Test-Path -LiteralPath $Path)) {
            $result.Error = "missing"
            return [pscustomobject]$result
        }

        $item = Get-Item -LiteralPath $Path -Force
        $result.Exists = $true

        if ($item.PSIsContainer) {
            $result.Kind = "directory"
            $result.Readable = $true
            return [pscustomobject]$result
        }

        $result.Kind = "file"

        $stream = [System.IO.File]::Open(
            $item.FullName,
            [System.IO.FileMode]::Open,
            [System.IO.FileAccess]::Read,
            [System.IO.FileShare]::Read
        )

        try {
            $buffer = New-Object byte[] 1
            [void]$stream.Read($buffer, 0, 1)
        }
        finally {
            $stream.Dispose()
        }

        $result.Readable = $true
        return [pscustomobject]$result
    }
    catch {
        $result.Error = $_.Exception.Message
        return [pscustomobject]$result
    }
}

function Get-FocusPaths([string]$Root) {
    $paths = New-Object System.Collections.Generic.List[string]

    if (-not (Test-Path -LiteralPath $Root)) {
        return $paths
    }

    Get-ChildItem -LiteralPath $Root -Recurse -Force -ErrorAction SilentlyContinue |
        Where-Object {
            $_.Name -ieq "mux" -or
            $_.Extension -iin @(".vhd", ".vhdx") -or
            $_.FullName -like "*\runtime\coolify-local-docker\*"
        } |
        ForEach-Object {
            $paths.Add($_.FullName)
        }

    return $paths
}

function Test-ArchiveReadiness([string]$Root, [switch]$FullScan) {
    $failures = New-Object System.Collections.Generic.List[object]

    if ($FullScan) {
        $items = Get-ChildItem -LiteralPath $Root -Recurse -Force -File -ErrorAction SilentlyContinue
        foreach ($item in $items) {
            $check = Test-ReadableFile $item.FullName
            if (-not $check.Readable) {
                $failures.Add($check)
            }
        }
    }
    else {
        $paths = Get-FocusPaths $Root
        foreach ($path in $paths) {
            $check = Test-ReadableFile $path
            if (-not $check.Readable) {
                $failures.Add($check)
            }
        }
    }

    return $failures
}

function Get-WslDistroName([string]$InstanceName, [string]$Mode) {
    return "MainComputer-$InstanceName-$($Mode.ToLowerInvariant())"
}

function Stop-SelectedWsl([string]$DistroName) {
    Write-Host "Terminating WSL distro: $DistroName"
    & wsl.exe --terminate $DistroName 2>$null
    Start-Sleep -Seconds 2
}

function Get-DockerContainersUsingRoot([string]$Root) {
    $docker = Get-Command docker.exe -ErrorAction SilentlyContinue
    if (-not $docker) {
        return @()
    }

    $rootFull = Convert-ToFullPath $Root
    $ids = & docker.exe ps -q 2>$null

    $matches = @()

    foreach ($id in $ids) {
        if (-not $id) {
            continue
        }

        try {
            $json = & docker.exe inspect $id 2>$null
            if (-not $json) {
                continue
            }

            $inspect = $json | ConvertFrom-Json
            $container = $inspect[0]
            $name = $container.Name.TrimStart("/")

            foreach ($mount in $container.Mounts) {
                if (-not $mount.Source) {
                    continue
                }

                $source = Convert-ToFullPath $mount.Source

                if ($source.StartsWith($rootFull, [System.StringComparison]::OrdinalIgnoreCase)) {
                    $matches += [pscustomobject]@{
                        Id = $id
                        Name = $name
                        MountSource = $source
                    }
                }
            }
        }
        catch {
            Write-Host "WARN: Could not inspect Docker container ${id}: $($_.Exception.Message)"
        }
    }

    return $matches
}

function Stop-DockerContainersUsingRoot([string]$Root) {
    $containers = Get-DockerContainersUsingRoot $Root

    if (-not $containers -or $containers.Count -eq 0) {
        Write-Host "No running Docker containers appear to mount this install root."
        return
    }

    Write-Host "Stopping Docker containers that mount this install root:"
    foreach ($container in $containers) {
        Write-Host "  $($container.Name) [$($container.Id)] -> $($container.MountSource)"
        & docker.exe stop $container.Id | Out-Null
    }

    Start-Sleep -Seconds 2
}

function Wait-UntilReadable([string]$Root, [int]$TimeoutSeconds, [switch]$FullScan) {
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)

    while ((Get-Date) -lt $deadline) {
        $failures = Test-ArchiveReadiness -Root $Root -FullScan:$FullScan

        if ($failures.Count -eq 0) {
            return @()
        }

        Write-Host "Still blocked: $($failures.Count) path(s). Waiting..."
        Start-Sleep -Seconds 2
    }

    return Test-ArchiveReadiness -Root $Root -FullScan:$FullScan
}

$InstallRoot = Convert-ToFullPath $InstallRoot
$DistroName = Get-WslDistroName -InstanceName $InstanceName -Mode $Mode

Write-Section "Install archive blocker diagnostic"
Write-Host "Install root: $InstallRoot"
Write-Host "Mode:         $Mode"
Write-Host "Instance:     $InstanceName"
Write-Host "WSL distro:   $DistroName"
Write-Host "Full scan:    $FullScan"

Write-Section "Initial focused blocker check"
$initial = Test-ArchiveReadiness -Root $InstallRoot -FullScan:$FullScan

if ($initial.Count -eq 0) {
    Write-Host "PASS: No archive blockers found."
}
else {
    Write-Host "FAIL: Found archive blockers:"
    $initial | Select-Object Path, Kind, Error | Format-Table -AutoSize
}

Write-Section "Running Docker containers using this install root"
$containers = Get-DockerContainersUsingRoot $InstallRoot
if ($containers.Count -eq 0) {
    Write-Host "None found."
}
else {
    $containers | Format-Table Name, Id, MountSource -AutoSize
}

if ($StopWsl) {
    Write-Section "Stopping selected WSL blocker"
    Stop-SelectedWsl -DistroName $DistroName
}

if ($StopDockerContainersUsingInstallRoot) {
    Write-Section "Stopping Docker blockers"
    Stop-DockerContainersUsingRoot -Root $InstallRoot
}

if ($StopWsl -or $StopDockerContainersUsingInstallRoot) {
    Write-Section "Waiting for archive blockers to release"
    $remaining = Wait-UntilReadable -Root $InstallRoot -TimeoutSeconds $TimeoutSeconds -FullScan:$FullScan

    if ($remaining.Count -eq 0) {
        Write-Host "PASS: Blockers released."

        Write-Section "Double-check pass"
        Start-Sleep -Seconds 2
        $doubleCheck = Test-ArchiveReadiness -Root $InstallRoot -FullScan:$FullScan

        if ($doubleCheck.Count -eq 0) {
            Write-Host "PASS: Second check also clean. Archive should now be able to read the install root."
        }
        else {
            Write-Host "FAIL: Second check found blockers again:"
            $doubleCheck | Select-Object Path, Kind, Error | Format-Table -AutoSize
            exit 2
        }
    }
    else {
        Write-Host "FAIL: Blockers did not release:"
        $remaining | Select-Object Path, Kind, Error | Format-Table -AutoSize
        exit 1
    }
}
else {
    Write-Section "No stop switches were supplied"
    Write-Host "This was read-only. To test the preservation-safe unlock path, rerun with:"
    Write-Host "  -StopWsl -StopDockerContainersUsingInstallRoot"
}