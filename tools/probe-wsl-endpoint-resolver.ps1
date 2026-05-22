[CmdletBinding()]
param(
    [string]$WslPath = "wsl.exe",
    [string]$Distribution = "MainComputer-maincomputer-debug",
    [int]$TimeoutSeconds = 15
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = "Stop"

function Test-IPv4Literal {
    param([AllowNull()][string]$Value)

    if ([string]::IsNullOrWhiteSpace($Value)) {
        return $false
    }

    $candidate = $Value.Trim()

    # Must be exactly one dotted IPv4 token, no stderr text, no newlines.
    if ($candidate -notmatch '^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$') {
        return $false
    }

    foreach ($part in $candidate.Split(".")) {
        $n = 0
        if (-not [int]::TryParse($part, [ref]$n)) {
            return $false
        }
        if ($n -lt 0 -or $n -gt 255) {
            return $false
        }
    }

    return $true
}

function Convert-WslRouteGatewayHexToIPv4 {
    param([Parameter(Mandatory = $true)][string]$GatewayHex)

    $hex = $GatewayHex.Trim()

    if ($hex -notmatch '^[0-9A-Fa-f]{8}$') {
        return $null
    }

    # /proc/net/route stores the gateway as little-endian hex.
    # Example: 010715AC => 172.21.7.1
    $bytes = @(
        [Convert]::ToInt32($hex.Substring(6, 2), 16)
        [Convert]::ToInt32($hex.Substring(4, 2), 16)
        [Convert]::ToInt32($hex.Substring(2, 2), 16)
        [Convert]::ToInt32($hex.Substring(0, 2), 16)
    )

    return ($bytes -join ".")
}

function Quote-NativeArgument {
    param([AllowNull()][string]$Argument)

    if ($null -eq $Argument) {
        return '""'
    }

    if ($Argument -eq "") {
        return '""'
    }

    if ($Argument -notmatch '[\s"]') {
        return $Argument
    }

    return '"' + ($Argument -replace '\\(?=\\*")', '$0$0' -replace '"', '\"') + '"'
}

function Invoke-NativeSeparated {
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [Parameter(Mandatory = $true)][string[]]$Arguments,
        [int]$TimeoutSeconds = 15
    )

    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = $FilePath
    $psi.Arguments = (($Arguments | ForEach-Object { Quote-NativeArgument $_ }) -join " ")
    $psi.UseShellExecute = $false
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $psi.CreateNoWindow = $true

    $process = New-Object System.Diagnostics.Process
    $process.StartInfo = $psi

    [void]$process.Start()

    $stdoutTask = $process.StandardOutput.ReadToEndAsync()
    $stderrTask = $process.StandardError.ReadToEndAsync()

    if (-not $process.WaitForExit($TimeoutSeconds * 1000)) {
        try { $process.Kill() } catch {}
        return [pscustomobject]@{
            ExitCode = -999
            Stdout = ""
            Stderr = "Timed out after $TimeoutSeconds seconds."
        }
    }

    return [pscustomobject]@{
        ExitCode = $process.ExitCode
        Stdout = $stdoutTask.Result
        Stderr = $stderrTask.Result
    }
}

function Invoke-WslShell {
    param(
        [Parameter(Mandatory = $true)][string]$Script
    )

    return Invoke-NativeSeparated `
        -FilePath $WslPath `
        -Arguments @("--distribution", $Distribution, "--exec", "/bin/sh", "-lc", $Script) `
        -TimeoutSeconds $TimeoutSeconds
}

function Resolve-WslEndpoint-Proposed {
    # Host gateway: first try `ip`; if missing, fall back to /proc/net/route.
    $hostGateway = $null
    $hostProbe = Invoke-WslShell "command -v ip >/dev/null 2>&1 && ip -4 route show default | awk '{print `$3; exit}'"

    if ($hostProbe.ExitCode -eq 0 -and (Test-IPv4Literal $hostProbe.Stdout)) {
        $hostGateway = $hostProbe.Stdout.Trim()
    }
    else {
        $routeProbe = Invoke-WslShell "awk '`$2 == `"00000000`" { print `$3; exit }' /proc/net/route"
        if ($routeProbe.ExitCode -eq 0 -and -not [string]::IsNullOrWhiteSpace($routeProbe.Stdout)) {
            $hostGateway = Convert-WslRouteGatewayHexToIPv4 $routeProbe.Stdout
        }
    }

    # Guest IP: hostname -I is usually available even when `ip` is not.
    $guestProbe = Invoke-WslShell "hostname -I 2>/dev/null | awk '{print `$1; exit}'"
    $guestIp = $null

    if ($guestProbe.ExitCode -eq 0 -and (Test-IPv4Literal $guestProbe.Stdout)) {
        $guestIp = $guestProbe.Stdout.Trim()
    }

    if (-not (Test-IPv4Literal $hostGateway)) {
        return [pscustomobject]@{
            Ok = $false
            Reason = "Could not resolve a valid Windows gateway IPv4 address."
            HostGatewayIp = $hostGateway
            GuestIp = $guestIp
            HostProbeExitCode = $hostProbe.ExitCode
            HostProbeStdout = $hostProbe.Stdout.Trim()
            HostProbeStderr = $hostProbe.Stderr.Trim()
        }
    }

    if (-not (Test-IPv4Literal $guestIp)) {
        return [pscustomobject]@{
            Ok = $false
            Reason = "Could not resolve a valid WSL guest IPv4 address."
            HostGatewayIp = $hostGateway
            GuestIp = $guestIp
            GuestProbeExitCode = $guestProbe.ExitCode
            GuestProbeStdout = $guestProbe.Stdout.Trim()
            GuestProbeStderr = $guestProbe.Stderr.Trim()
        }
    }

    return [pscustomobject]@{
        Ok = $true
        Reason = "Resolved valid endpoint."
        HostGatewayIp = $hostGateway
        GuestIp = $guestIp
    }
}

function Test-Case {
    param(
        [string]$Name,
        [bool]$Passed,
        [string]$Detail
    )

    if ($Passed) {
        Write-Host "[PASS] $Name - $Detail"
    }
    else {
        Write-Host "[FAIL] $Name - $Detail" -ForegroundColor Red
        $script:Failed = $true
    }
}

$script:Failed = $false

Write-Host ""
Write-Host "WSL endpoint resolver probe"
Write-Host "---------------------------"
Write-Host "Distribution: $Distribution"
Write-Host "WSL path:     $WslPath"
Write-Host ""

# Synthetic regression test for the exact bug from your precheck:
# old logic accepted merged stderr/stdout like '/bin/sh: 1: ip: not found' as gateway text.
$badMergedGateway = "/bin/sh: 1: ip: not found"
Test-Case `
    -Name "Reject stderr text as gateway" `
    -Passed (-not (Test-IPv4Literal $badMergedGateway)) `
    -Detail "Error text is not accepted as an IPv4 address."

# Validate /proc/net/route fallback conversion.
Test-Case `
    -Name "Decode WSL /proc/net/route gateway" `
    -Passed ((Convert-WslRouteGatewayHexToIPv4 "010715AC") -eq "172.21.7.1") `
    -Detail "010715AC decodes to 172.21.7.1."

# Make sure strict IPv4 validation catches garbage and octet overflow.
Test-Case `
    -Name "Reject invalid IPv4 octets" `
    -Passed (-not (Test-IPv4Literal "999.21.7.1")) `
    -Detail "999.21.7.1 is rejected."

Test-Case `
    -Name "Accept normal IPv4" `
    -Passed (Test-IPv4Literal "172.21.7.123") `
    -Detail "172.21.7.123 is accepted."

Write-Host ""
Write-Host "Live WSL probe"
Write-Host "--------------"

$live = Resolve-WslEndpoint-Proposed

if ($live.Ok) {
    Test-Case `
        -Name "Resolve live WSL endpoint" `
        -Passed $true `
        -Detail "Guest $($live.GuestIp) -> Windows gateway $($live.HostGatewayIp)"
}
else {
    Test-Case `
        -Name "Resolve live WSL endpoint" `
        -Passed $false `
        -Detail $live.Reason

    Write-Host ""
    Write-Host "Live failure details:"
    $live | Format-List | Out-String | Write-Host
}

Write-Host ""
if ($script:Failed) {
    Write-Host "RESULT: FAIL" -ForegroundColor Red
    exit 1
}
else {
    Write-Host "RESULT: PASS"
    exit 0
}