# open-main-computer-browser.ps1
#
# Wait for the tree-local Main Computer web UI to answer, then open it in the
# user's default browser. start_v2.bat calls this only when passed -OpenBrowser.

[CmdletBinding()]
param(
    [string]$Root = (Get-Location).Path,

    [int]$TimeoutSeconds = 120,

    [string]$Url = ""
)

$ErrorActionPreference = "Stop"

function Resolve-FullPath {
    param([Parameter(Mandatory = $true)][string]$Path)

    return [System.IO.Path]::GetFullPath($Path).TrimEnd([char[]]@('\', '/'))
}

function Get-ObjectPropertyValue {
    param(
        [object]$Object,
        [string]$Name,
        [object]$Default = $null
    )

    if ($null -eq $Object) {
        return $Default
    }

    if ($Object -is [System.Collections.IDictionary]) {
        if ($Object.Contains($Name)) {
            return $Object[$Name]
        }
        return $Default
    }

    $property = $Object.PSObject.Properties[$Name]
    if ($null -eq $property) {
        return $Default
    }

    return $property.Value
}

function ConvertTo-NonEmptyString {
    param(
        [object]$Value,
        [string]$Default = ""
    )

    if ($null -eq $Value) {
        return $Default
    }

    $text = [string]$Value
    if ([string]::IsNullOrWhiteSpace($text)) {
        return $Default
    }

    return $text.Trim()
}

function Get-ModeDefaultPort {
    param([string]$Mode)

    $normalized = (ConvertTo-NonEmptyString $Mode "unleashed").ToLowerInvariant()
    switch ($normalized) {
        "safe" { return "38865" }
        "safe mode" { return "38865" }
        "debug" { return "28865" }
        default { return "8765" }
    }
}

function Get-MainComputerLauncherManifest {
    param([Parameter(Mandatory = $true)][string]$RootPath)

    $manifestPath = Join-Path $RootPath "runtime\start_stop\main-computer-launcher.json"
    if (-not (Test-Path -LiteralPath $manifestPath -PathType Leaf)) {
        return $null
    }

    try {
        return (Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json)
    } catch {
        Write-Warning "Could not read launcher manifest $manifestPath`: $($_.Exception.Message)"
        return $null
    }
}

function Resolve-MainComputerBrowserUrl {
    param(
        [Parameter(Mandatory = $true)][string]$RootPath,
        [string]$ExplicitUrl = ""
    )

    $explicit = ConvertTo-NonEmptyString $ExplicitUrl ""
    if (-not [string]::IsNullOrWhiteSpace($explicit)) {
        return $explicit
    }

    $manifest = Get-MainComputerLauncherManifest $RootPath
    $port = ""

    if ($null -ne $manifest) {
        $environment = Get-ObjectPropertyValue $manifest "environment" $null
        if ($null -ne $environment) {
            $port = ConvertTo-NonEmptyString (Get-ObjectPropertyValue $environment "MAIN_COMPUTER_CONTROL_PORT" "") ""
        }

        if ([string]::IsNullOrWhiteSpace($port)) {
            $port = ConvertTo-NonEmptyString (Get-ObjectPropertyValue $manifest "port" "") ""
        }

        if ([string]::IsNullOrWhiteSpace($port)) {
            $mode = ConvertTo-NonEmptyString (Get-ObjectPropertyValue $manifest "mode" "unleashed") "unleashed"
            $port = Get-ModeDefaultPort $mode
        }
    }

    if ([string]::IsNullOrWhiteSpace($port)) {
        $port = "8765"
    }

    $parsedPort = 0
    if (-not [int]::TryParse($port, [ref]$parsedPort) -or $parsedPort -lt 1 -or $parsedPort -gt 65535) {
        throw "Invalid Main Computer browser port resolved from launcher manifest: $port"
    }

    return "http://127.0.0.1:$parsedPort/"
}

function Get-HttpStatusCode {
    param([Parameter(Mandatory = $true)][string]$TargetUrl)

    $request = [System.Net.WebRequest]::Create($TargetUrl)
    $request.Method = "GET"
    $request.Timeout = 3000
    $request.AllowAutoRedirect = $true

    $response = $null
    try {
        $response = $request.GetResponse()
        return [int]$response.StatusCode
    } catch [System.Net.WebException] {
        if ($null -ne $_.Exception.Response) {
            try {
                return [int]$_.Exception.Response.StatusCode
            } finally {
                $_.Exception.Response.Close()
            }
        }
        return 0
    } finally {
        if ($null -ne $response) {
            $response.Close()
        }
    }
}

function Wait-MainComputerBrowserReady {
    param(
        [Parameter(Mandatory = $true)][string]$TargetUrl,
        [int]$WaitSeconds = 120
    )

    $deadline = (Get-Date).AddSeconds([Math]::Max(1, $WaitSeconds))
    do {
        $statusCode = Get-HttpStatusCode $TargetUrl
        if ($statusCode -ge 200 -and $statusCode -lt 500) {
            return $true
        }

        Start-Sleep -Seconds 2
    } while ((Get-Date) -lt $deadline)

    return $false
}

$resolvedRoot = Resolve-FullPath $Root
$targetUrl = Resolve-MainComputerBrowserUrl -RootPath $resolvedRoot -ExplicitUrl $Url

Write-Host "Waiting for Main Computer browser URL: $targetUrl"
if (-not (Wait-MainComputerBrowserReady -TargetUrl $targetUrl -WaitSeconds $TimeoutSeconds)) {
    throw "Timed out waiting for Main Computer to answer at $targetUrl"
}

Write-Host "Opening Main Computer: $targetUrl"
Start-Process $targetUrl
