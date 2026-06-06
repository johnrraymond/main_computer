param(
  [Parameter(Mandatory = $true, Position = 0)]
  [ValidateSet("install", "start", "stop", "status", "doctor", "bridge-start", "bridge-status", "bridge-stop", "bridge-start-elevated", "bridge-stop-elevated")]
  [string]$Action,

  [ValidateSet("wsl", "docker")]
  [string]$Mode = "wsl",

  [int]$Port = 18084,

  [int]$AppPort = 8765,

  [string]$Distro = "",

  [string]$JwtSecret = "",

  [string]$ComposeFile = "docker-compose.onlyoffice.yml",

  [string]$ProjectName = "",

  [int]$ReadyTimeoutSeconds = 300,

  [int]$ReadyPollSeconds = 5,

  [switch]$NoElevate,

  [string]$WslIp = "",

  [string]$WslGatewayIp = "",

  [switch]$ForceApiProxyRefresh,

  [switch]$ForceCallbackProxyRefresh
)

$ErrorActionPreference = "Stop"

$scriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Split-Path -Parent (Split-Path -Parent $scriptRoot)

if (-not $JwtSecret) {
  if ($env:MAIN_COMPUTER_ONLYOFFICE_JWT_SECRET) {
    $JwtSecret = $env:MAIN_COMPUTER_ONLYOFFICE_JWT_SECRET
  } else {
    $JwtSecret = "main-computer-onlyoffice-local-secret"
  }
}

if (-not $ProjectName) {
  if ($env:MAIN_COMPUTER_ONLYOFFICE_PROJECT) {
    $ProjectName = $env:MAIN_COMPUTER_ONLYOFFICE_PROJECT
  } elseif ($env:COMPOSE_PROJECT_NAME) {
    $ProjectName = $env:COMPOSE_PROJECT_NAME
  } else {
    $ProjectName = "main-computer-onlyoffice"
  }
}

if ([string]::IsNullOrWhiteSpace($Distro)) {
  if ($env:MAIN_COMPUTER_ONLYOFFICE_WSL_DISTRO) {
    $Distro = $env:MAIN_COMPUTER_ONLYOFFICE_WSL_DISTRO
  } else {
    $Distro = "Ubuntu"
  }
}

$script:OnlyOfficeApiFirewallRuleName = "Main Computer ONLYOFFICE WSL API bridge $Port"
$script:OnlyOfficeCallbackFirewallRuleName = "Main Computer ONLYOFFICE WSL callback bridge $AppPort"
$script:WslDistroReadyCache = @{}

function Write-Section {
  param([string]$Title)
  Write-Host ""
  Write-Host $Title
  Write-Host ("-" * $Title.Length)
}

function Convert-WindowsPathToWslPath {
  param([Parameter(Mandatory = $true)][string]$WindowsPath)

  $resolved = [System.IO.Path]::GetFullPath($WindowsPath)
  if ($resolved -match '^([A-Za-z]):\\(.*)$') {
    $drive = $matches[1].ToLowerInvariant()
    $rest = $matches[2] -replace '\\', '/'
    return "/mnt/$drive/$rest"
  }

  # Fallback for non-drive-letter paths. Keep quoting intact when asking WSL.
  $converted = (& wsl.exe -d $Distro -- wslpath -a "$resolved" 2>$null)
  if ($LASTEXITCODE -ne 0 -or -not $converted) {
    throw "Could not convert Windows path to WSL path: $resolved"
  }
  return ($converted | Select-Object -First 1).Trim()
}

function Quote-Bash {
  param([Parameter(Mandatory = $true)][string]$Value)
  return "'" + ($Value -replace "'", "'\''") + "'"
}

function Get-WslDistroNames {
  $wsl = Get-Command wsl.exe -CommandType Application -ErrorAction SilentlyContinue
  if ($null -eq $wsl) {
    throw "wsl.exe was not found. Install or enable Windows Subsystem for Linux, then rerun start_v2.bat."
  }

  $raw = @(& wsl.exe -l -q 2>$null)
  if ($LASTEXITCODE -ne 0) {
    Write-Warning "Could not list WSL distributions before install. wsl.exe -l -q failed with exit code $LASTEXITCODE."
    return @()
  }

  $names = @()
  foreach ($line in $raw) {
    $clean = ([string]$line) -replace "`0", ""
    $clean = $clean.Trim()
    if (-not [string]::IsNullOrWhiteSpace($clean)) {
      $names += $clean
    }
  }
  return $names
}

function Test-WslDistroExists {
  param([Parameter(Mandatory = $true)][string]$DistroName)

  foreach ($name in @(Get-WslDistroNames)) {
    if ($name -eq $DistroName) {
      return $true
    }
  }
  return $false
}

function Install-WslDistro {
  param([Parameter(Mandatory = $true)][string]$DistroName)

  Write-Host "WSL distro '$DistroName' is not installed."
  Write-Host "Installing WSL distro '$DistroName' for ONLYOFFICE WSL mode..."
  & wsl.exe --install -d $DistroName
  if ($LASTEXITCODE -ne 0) {
    throw "Could not install WSL distro '$DistroName'. wsl.exe --install -d $DistroName failed with exit code $LASTEXITCODE."
  }
}

function Assert-WslDistroReady {
  param([Parameter(Mandatory = $true)][string]$DistroName)

  & wsl.exe -d $DistroName -u root -- bash -lc "true" 2>$null
  if ($LASTEXITCODE -ne 0) {
    throw "WSL distro '$DistroName' is installed but is not ready for ONLYOFFICE commands. A Windows restart or first-run WSL initialization may be required. After that completes, rerun .\start_v2.bat."
  }
}

function Ensure-WslDistroInstalledAndReady {
  param([Parameter(Mandatory = $true)][string]$DistroName)

  if ([string]::IsNullOrWhiteSpace($DistroName)) {
    throw "ONLYOFFICE WSL distro name is empty. Set MAIN_COMPUTER_ONLYOFFICE_WSL_DISTRO or pass -Distro."
  }

  if ($script:WslDistroReadyCache.ContainsKey($DistroName)) {
    return
  }

  if (-not (Test-WslDistroExists -DistroName $DistroName)) {
    Install-WslDistro -DistroName $DistroName
    if (-not (Test-WslDistroExists -DistroName $DistroName)) {
      throw "WSL distro '$DistroName' installation was requested, but the distro is not registered yet. A Windows restart or first-run WSL initialization may be required. After that completes, rerun .\start_v2.bat."
    }
  }

  Assert-WslDistroReady -DistroName $DistroName
  $script:WslDistroReadyCache[$DistroName] = $true
  Write-Host "WSL distro '$DistroName' is ready for ONLYOFFICE WSL mode."
}

function Invoke-WslOnlyOffice {
  param(
    [Parameter(Mandatory = $true)][string]$ScriptName,
    [string[]]$ExtraArgs = @()
  )

  Ensure-WslDistroInstalledAndReady -DistroName $Distro

  $wslRepo = Convert-WindowsPathToWslPath $repoRoot
  $quotedRepo = Quote-Bash $wslRepo
  $quotedScript = Quote-Bash "./tools/onlyoffice/$ScriptName"
  $argParts = @()
  foreach ($arg in $ExtraArgs) {
    $argParts += (Quote-Bash $arg)
  }
  $bash = "cd $quotedRepo && bash $quotedScript " + ($argParts -join " ")

  Write-Host "wsl distro: $Distro"
  Write-Host "wsl user: root"
  Write-Host "wsl repo: $wslRepo"
  Write-Host "command: $bash"
  Write-Host "note: WSL ONLYOFFICE native service actions run as root to install/start system packages without a sudo password prompt."
  & wsl.exe -d $Distro -u root -- bash -lc $bash
  if ($LASTEXITCODE -ne 0) {
    throw "WSL ONLYOFFICE command failed with exit code $LASTEXITCODE."
  }
}


function Test-WslNativeOnlyOfficeInstalled {
  if ($Mode -ne "wsl") {
    return $true
  }

  Ensure-WslDistroInstalledAndReady -DistroName $Distro

  $check = 'dpkg-query -s onlyoffice-documentserver 2>/dev/null | grep -q "^Status: install ok installed$"'
  & wsl.exe -d $Distro -u root -- bash -lc $check
  return ($LASTEXITCODE -eq 0)
}

function Ensure-WslNativeOnlyOfficeInstalled {
  if ($Mode -ne "wsl") {
    return
  }

  if (Test-WslNativeOnlyOfficeInstalled) {
    Write-Host "ONLYOFFICE native package is installed in WSL distro '$Distro'."
    return
  }

  Write-Host "ONLYOFFICE native package is not installed in WSL distro '$Distro'."
  Write-Host "Installing ONLYOFFICE Docs native package for WSL mode before startup..."
  Invoke-WslOnlyOffice "wsl-install-onlyoffice.sh" @("--port", "$Port", "--jwt-secret", "$JwtSecret")

  if (-not (Test-WslNativeOnlyOfficeInstalled)) {
    throw "ONLYOFFICE native install completed, but onlyoffice-documentserver is still not installed in WSL distro '$Distro'."
  }

  Write-Host "ONLYOFFICE native package is installed in WSL distro '$Distro'."
}


function Remove-StaleApplicationsDockerOnlyOffice {
  if ($Mode -ne "wsl") {
    return
  }

  $docker = Get-Command docker -CommandType Application -ErrorAction SilentlyContinue
  if ($null -eq $docker) {
    return
  }

  $rows = @(& docker ps -a `
    --filter "name=main-computer-applications-onlyoffice" `
    --format "{{.ID}}`t{{.Names}}`t{{.Image}}`t{{.Ports}}" 2>$null)

  if ($LASTEXITCODE -ne 0) {
    return
  }

  foreach ($row in $rows) {
    $text = [string]$row
    if ([string]::IsNullOrWhiteSpace($text)) {
      continue
    }

    $parts = $text -split "`t", 4
    if ($parts.Count -lt 3) {
      continue
    }

    $containerId = $parts[0].Trim()
    $containerName = $parts[1].Trim()
    $image = $parts[2].Trim()
    $ports = $(if ($parts.Count -ge 4) { $parts[3].Trim() } else { "" })

    $isMainComputerApplicationsOnlyOffice = $containerName -match '^main-computer-applications-onlyoffice(?:-|$)'
    $isOnlyOfficeImage = $image -match '^onlyoffice/documentserver(?::|$)'
    $publishesManagedPort = $ports -match [regex]::Escape(":$Port->80/tcp")

    if ($containerId -and $isMainComputerApplicationsOnlyOffice -and $isOnlyOfficeImage -and $publishesManagedPort) {
      Write-Host "Removing stale Docker applications ONLYOFFICE container before WSL startup: $containerName"
      & docker rm -f $containerId | Out-Host
      if ($LASTEXITCODE -ne 0) {
        throw "Could not remove stale Docker applications ONLYOFFICE container '$containerName' (exit code $LASTEXITCODE)."
      }
    }
  }
}

function Invoke-DockerOnlyOffice {
  param([Parameter(Mandatory = $true)][string]$DockerAction)

  $composePath = Join-Path $repoRoot $ComposeFile
  if (-not (Test-Path $composePath)) {
    throw "Compose file not found: $composePath"
  }

  $env:MAIN_COMPUTER_ONLYOFFICE_PORT = [string]$Port
  $env:MAIN_COMPUTER_ONLYOFFICE_JWT_SECRET = $JwtSecret
  $env:MAIN_COMPUTER_ONLYOFFICE_PROJECT = $ProjectName
  $env:COMPOSE_PROJECT_NAME = $ProjectName

  Write-Host "compose file: $composePath"
  Write-Host "compose project: $ProjectName"

  if ($DockerAction -eq "start") {
    & docker compose -f $composePath -p $ProjectName up -d onlyoffice
    if ($LASTEXITCODE -ne 0) {
      throw "Docker ONLYOFFICE command failed with exit code $LASTEXITCODE."
    }
  } elseif ($DockerAction -eq "stop") {
    & docker compose -f $composePath -p $ProjectName stop onlyoffice
    if ($LASTEXITCODE -ne 0) {
      throw "Docker ONLYOFFICE command failed with exit code $LASTEXITCODE."
    }
  } elseif ($DockerAction -eq "status") {
    & docker compose -f $composePath -p $ProjectName ps onlyoffice

    $python = $env:MAIN_COMPUTER_PYTHON
    if (-not $python) {
      $pythonCommand = Get-Command py -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
      if ($null -ne $pythonCommand) {
        $python = $pythonCommand.Source
      }
    }

    $checkScript = Join-Path $repoRoot "tools/onlyoffice/check-onlyoffice.py"
    if ($python) {
      & $python $checkScript --url "http://127.0.0.1:$Port" --wait-seconds $ReadyTimeoutSeconds --poll-seconds $ReadyPollSeconds
    } else {
      & python $checkScript --url "http://127.0.0.1:$Port" --wait-seconds $ReadyTimeoutSeconds --poll-seconds $ReadyPollSeconds
    }
    if ($LASTEXITCODE -ne 0) {
      throw "Docker ONLYOFFICE status check failed with exit code $LASTEXITCODE."
    }
  } else {
    throw "Unsupported Docker action: $DockerAction"
  }
}

function Test-IsWindowsAdmin {
  try {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
  } catch {
    return $false
  }
}

function Quote-WindowsArgument {
  param([Parameter(Mandatory = $true)][string]$Value)
  if ($Value -notmatch '[\s"]') {
    return $Value
  }
  return '"' + ($Value -replace '"', '\"') + '"'
}

function Invoke-ElevatedSelf {
  param(
    [Parameter(Mandatory = $true)][string]$ElevatedAction,
    [string]$ElevatedWslIp = "",
    [string]$ElevatedWslGatewayIp = "",
    [switch]$ElevatedForceApiProxyRefresh,
    [switch]$ElevatedForceCallbackProxyRefresh
  )

  if ($NoElevate) {
    throw "Administrator rights are required for $ElevatedAction, and -NoElevate was supplied."
  }

  $args = @(
    "-NoProfile",
    "-ExecutionPolicy", "Bypass",
    "-File", $PSCommandPath,
    $ElevatedAction,
    "-Mode", $Mode,
    "-Port", [string]$Port,
    "-AppPort", [string]$AppPort,
    "-Distro", $Distro,
    "-ProjectName", $ProjectName
  )
  if ($JwtSecret) {
    $args += @("-JwtSecret", $JwtSecret)
  }
  if (-not [string]::IsNullOrWhiteSpace($ElevatedWslIp)) {
    $args += @("-WslIp", $ElevatedWslIp)
  }
  if (-not [string]::IsNullOrWhiteSpace($ElevatedWslGatewayIp)) {
    $args += @("-WslGatewayIp", $ElevatedWslGatewayIp)
  }
  if ($ElevatedForceApiProxyRefresh) {
    $args += "-ForceApiProxyRefresh"
  }
  if ($ElevatedForceCallbackProxyRefresh) {
    $args += "-ForceCallbackProxyRefresh"
  }

  Write-Warning "Administrator rights are required to manage Windows portproxy/firewall rules."
  Write-Host "Requesting elevation for ONLYOFFICE $ElevatedAction..."
  $argumentList = ($args | ForEach-Object { Quote-WindowsArgument $_ }) -join " "
  $proc = Start-Process -FilePath "powershell.exe" -ArgumentList $argumentList -Verb RunAs -Wait -PassThru
  if ($proc.ExitCode -ne 0) {
    throw "Elevated ONLYOFFICE $ElevatedAction failed with exit code $($proc.ExitCode)."
  }
}

function Get-FirstIpv4FromText {
  param([string]$Text)
  foreach ($candidate in ($Text -split '\s+')) {
    if ($candidate -match '^\d{1,3}(\.\d{1,3}){3}$' -and $candidate -ne "127.0.0.1") {
      return $candidate
    }
  }
  return ""
}

function Get-WslOnlyOfficeIp {
  if ($env:MAIN_COMPUTER_ONLYOFFICE_WSL_IP) {
    return $env:MAIN_COMPUTER_ONLYOFFICE_WSL_IP
  }

  $raw = (& wsl.exe -d $Distro -- hostname -I 2>$null) -join " "
  $ip = Get-FirstIpv4FromText $raw
  if ($ip) {
    return $ip
  }

  $fallback = (& wsl.exe -d $Distro -- bash -lc "ip -4 addr show eth0 | sed -n 's/.*inet \([0-9.]*\)\/.*/\1/p' | head -1" 2>$null) -join " "
  $ip = Get-FirstIpv4FromText $fallback
  if ($ip) {
    return $ip
  }

  throw "Could not resolve WSL distro IPv4 address for $Distro."
}

function Get-WslGatewayIp {
  if ($env:MAIN_COMPUTER_WSL_GATEWAY_IP) {
    return $env:MAIN_COMPUTER_WSL_GATEWAY_IP
  }

  $adapter = Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
    Where-Object {
      $_.InterfaceAlias -like "*WSL*" -and
      $_.IPAddress -notlike "169.254.*" -and
      $_.IPAddress -ne "127.0.0.1"
    } |
    Sort-Object InterfaceAlias,IPAddress |
    Select-Object -First 1

  if ($null -ne $adapter -and -not [string]::IsNullOrWhiteSpace([string]$adapter.IPAddress)) {
    return [string]$adapter.IPAddress
  }

  $raw = (& wsl.exe -d $Distro -- bash -lc "ip route | awk '/default/ {print `$3; exit}'" 2>$null) -join " "
  $ip = Get-FirstIpv4FromText $raw
  if ($ip) {
    return $ip
  }

  throw "Could not resolve Windows WSL gateway IPv4 address."
}

function Test-TcpPortOpen {
  param(
    [Parameter(Mandatory = $true)][string]$TargetHost,
    [Parameter(Mandatory = $true)][int]$PortNumber,
    [int]$TimeoutMs = 1200
  )

  $client = New-Object System.Net.Sockets.TcpClient
  try {
    $async = $client.BeginConnect($TargetHost, $PortNumber, $null, $null)
    if (-not $async.AsyncWaitHandle.WaitOne($TimeoutMs, $false)) {
      return $false
    }
    $client.EndConnect($async)
    return $true
  } catch {
    return $false
  } finally {
    $client.Close()
  }
}

function Invoke-HttpProbe {
  param(
    [Parameter(Mandatory = $true)][string]$Url,
    [int]$TimeoutSeconds = 5
  )

  try {
    $response = Invoke-WebRequest $Url -UseBasicParsing -TimeoutSec $TimeoutSeconds
    return [pscustomobject]@{
      ok = ($response.StatusCode -ge 200 -and $response.StatusCode -lt 400)
      status = [int]$response.StatusCode
      error = ""
    }
  } catch {
    $status = $null
    try { $status = [int]$_.Exception.Response.StatusCode } catch { $status = $null }
    return [pscustomobject]@{
      ok = $false
      status = $status
      error = $_.Exception.Message
    }
  }
}

function Get-PortProxyText {
  return ((& netsh interface portproxy show v4tov4 2>$null) -join "`n")
}

function Test-PortProxyEntry {
  param(
    [Parameter(Mandatory = $true)][string]$ListenAddress,
    [Parameter(Mandatory = $true)][int]$ListenPort,
    [Parameter(Mandatory = $true)][string]$ConnectAddress,
    [Parameter(Mandatory = $true)][int]$ConnectPort
  )

  $table = Get-PortProxyText
  $escapedListen = [regex]::Escape($ListenAddress)
  $escapedConnect = [regex]::Escape($ConnectAddress)
  $pattern = "(?m)^\s*$escapedListen\s+$ListenPort\s+$escapedConnect\s+$ConnectPort\s*$"
  return [regex]::IsMatch($table, $pattern)
}

function Set-PortProxyEntry {
  param(
    [Parameter(Mandatory = $true)][string]$ListenAddress,
    [Parameter(Mandatory = $true)][int]$ListenPort,
    [Parameter(Mandatory = $true)][string]$ConnectAddress,
    [Parameter(Mandatory = $true)][int]$ConnectPort,
    [switch]$Force
  )

  $present = Test-PortProxyEntry -ListenAddress $ListenAddress -ListenPort $ListenPort -ConnectAddress $ConnectAddress -ConnectPort $ConnectPort
  if ($present -and -not $Force) {
    Write-Host "Portproxy already present: $ListenAddress`:$ListenPort -> $ConnectAddress`:$ConnectPort"
    return
  }

  if ($present -and $Force) {
    Write-Host "Refreshing existing portproxy because its listener failed verification: $ListenAddress`:$ListenPort -> $ConnectAddress`:$ConnectPort"
  }

  & netsh interface portproxy delete v4tov4 listenaddress=$ListenAddress listenport=$ListenPort 2>$null | Out-Null
  & netsh interface portproxy add v4tov4 listenaddress=$ListenAddress listenport=$ListenPort connectaddress=$ConnectAddress connectport=$ConnectPort | Out-Null
}

function Remove-PortProxyEntry {
  param(
    [Parameter(Mandatory = $true)][string]$ListenAddress,
    [Parameter(Mandatory = $true)][int]$ListenPort
  )

  & netsh interface portproxy delete v4tov4 listenaddress=$ListenAddress listenport=$ListenPort 2>$null | Out-Null
}

function Get-FirewallRulesByDisplayName {
  param([Parameter(Mandatory = $true)][string]$DisplayName)

  if (-not (Get-Command Get-NetFirewallRule -ErrorAction SilentlyContinue)) {
    return @()
  }

  return @(Get-NetFirewallRule -DisplayName $DisplayName -ErrorAction SilentlyContinue | Where-Object { $_.DisplayName -eq $DisplayName })
}

function Test-FirewallRule {
  param(
    [Parameter(Mandatory = $true)][string]$DisplayName,
    [Parameter(Mandatory = $true)][string]$LocalAddress,
    [Parameter(Mandatory = $true)][int]$LocalPort
  )

  $rules = Get-FirewallRulesByDisplayName $DisplayName
  foreach ($rule in $rules) {
    if ([string]$rule.Enabled -ne "True") { continue }
    if ([string]$rule.Direction -ne "Inbound") { continue }
    if ([string]$rule.Action -ne "Allow") { continue }
    if ([string]$rule.Profile -ne "Any") { continue }

    $portFilters = @($rule | Get-NetFirewallPortFilter -ErrorAction SilentlyContinue)
    $addressFilters = @($rule | Get-NetFirewallAddressFilter -ErrorAction SilentlyContinue)
    foreach ($portFilter in $portFilters) {
      $protocol = [string]$portFilter.Protocol
      if ($protocol -ne "TCP" -and $protocol -ne "6") { continue }

      $localPorts = @($portFilter.LocalPort | ForEach-Object { [string]$_ })
      if (-not ($localPorts -contains ([string]$LocalPort))) { continue }

      foreach ($addressFilter in $addressFilters) {
        $localAddresses = @($addressFilter.LocalAddress | ForEach-Object { [string]$_ })
        $remoteAddresses = @($addressFilter.RemoteAddress | ForEach-Object { [string]$_ })
        if (($localAddresses -contains $LocalAddress) -and ($remoteAddresses -contains "LocalSubnet")) {
          return $true
        }
      }
    }
  }

  return $false
}

function Ensure-FirewallRule {
  param(
    [Parameter(Mandatory = $true)][string]$DisplayName,
    [Parameter(Mandatory = $true)][string]$LocalAddress,
    [Parameter(Mandatory = $true)][int]$LocalPort
  )

  if (Test-FirewallRule -DisplayName $DisplayName -LocalAddress $LocalAddress -LocalPort $LocalPort) {
    Write-Host "Firewall rule already present: $DisplayName"
    return
  }

  $existing = Get-FirewallRulesByDisplayName $DisplayName
  if ($existing.Count -gt 0) {
    $existing | Remove-NetFirewallRule
  }

  New-NetFirewallRule `
    -DisplayName $DisplayName `
    -Direction Inbound `
    -Action Allow `
    -Protocol TCP `
    -LocalAddress $LocalAddress `
    -LocalPort $LocalPort `
    -RemoteAddress LocalSubnet `
    -Profile Any | Out-Null
}

function Remove-FirewallRuleIfPresent {
  param([Parameter(Mandatory = $true)][string]$DisplayName)
  $existing = Get-FirewallRulesByDisplayName $DisplayName
  if ($existing.Count -gt 0) {
    $existing | Remove-NetFirewallRule
  }
}

function Invoke-WslCurlProbe {
  param([Parameter(Mandatory = $true)][string]$Url)

  $quotedUrl = Quote-Bash $Url
  $script = "curl -s --connect-timeout 3 --max-time 5 -o /dev/null -w 'http_code=%{http_code} remote=%{remote_ip} connect=%{time_connect} total=%{time_total}' $quotedUrl 2>/dev/null; printf ' curl_exit=%s' `$?"
  $output = ""
  try {
    $output = (& wsl.exe -d $Distro -- bash -lc $script 2>$null) -join " "
  } catch {
    $output = $_.Exception.Message
  }

  $ok = $false
  if ($output -match 'http_code=([0-9]{3})') {
    $code = [int]$matches[1]
    $ok = ($code -ge 200 -and $code -lt 400)
  }
  return [pscustomobject]@{
    ok = $ok
    output = $output
  }
}

function Get-LanExposureResults {
  param([int[]]$Ports)

  $results = @()
  $ips = Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
    Where-Object {
      $_.IPAddress -notlike "169.254.*" -and
      $_.IPAddress -ne "127.0.0.1" -and
      $_.InterfaceAlias -notlike "*WSL*"
    } |
    Sort-Object InterfaceAlias,IPAddress

  foreach ($ip in $ips) {
    foreach ($p in $Ports) {
      $open = Test-TcpPortOpen -TargetHost ([string]$ip.IPAddress) -PortNumber $p -TimeoutMs 800
      $results += [pscustomobject]@{
        interface = [string]$ip.InterfaceAlias
        ip = [string]$ip.IPAddress
        port = $p
        open = $open
      }
    }
  }

  return $results
}

function Invoke-WslPythonAsRoot {
  param([Parameter(Mandatory = $true)][string]$PythonSource)

  $bytes = [System.Text.Encoding]::UTF8.GetBytes($PythonSource)
  $encoded = [Convert]::ToBase64String($bytes)
  $command = "import base64; exec(base64.b64decode('$encoded').decode('utf-8'))"
  return & wsl.exe -d $Distro -u root -- python3 -c $command
}

function Ensure-WslPrivateIpDownloadAllowed {
  if ($Mode -ne "wsl") {
    return
  }

  $python = @'
import json
from pathlib import Path

paths = [
    Path("/etc/onlyoffice/documentserver/local.json"),
    Path("/etc/onlyoffice/documentserver/local-production-linux.json"),
]

changed = []
for p in paths:
    if p.exists():
        try:
            data = json.loads(p.read_text())
        except Exception:
            data = {}
    else:
        data = {}

    services = data.setdefault("services", {})
    co = services.setdefault("CoAuthoring", {})
    rfa = co.setdefault("request-filtering-agent", {})

    before = dict(rfa)
    rfa["allowPrivateIPAddress"] = True
    rfa["allowMetaIPAddress"] = False

    if before != rfa or not p.exists():
        p.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
        changed.append(str(p))

print("allowPrivateIPAddress=true")
if changed:
    print("updated=" + ",".join(changed))
else:
    print("updated=")
'@

  Write-Host "Ensuring ONLYOFFICE WSL request filter allows local private callback URLs..."
  Invoke-WslPythonAsRoot $python
  if ($LASTEXITCODE -ne 0) {
    throw "Could not update ONLYOFFICE request-filtering-agent config in WSL."
  }

  # Restart the services that cache the request filtering settings. Keep going if a
  # distribution does not have every unit name.
  & wsl.exe -d $Distro -u root -- bash -lc "systemctl restart ds-docservice ds-converter nginx >/dev/null 2>&1 || true"
}

function Get-WslPrivateIpDownloadStatus {
  if ($Mode -ne "wsl") {
    return [pscustomobject]@{ checked = $false; allowPrivateIPAddress = $null; allowMetaIPAddress = $null; error = "" }
  }

  $python = @'
import json
from pathlib import Path

for p in [Path("/etc/onlyoffice/documentserver/local.json"), Path("/etc/onlyoffice/documentserver/local-production-linux.json"), Path("/etc/onlyoffice/documentserver/default.json")]:
    if not p.exists():
        continue
    try:
        data = json.loads(p.read_text())
    except Exception:
        continue
    rfa = data.get("services", {}).get("CoAuthoring", {}).get("request-filtering-agent", {})
    if "allowPrivateIPAddress" in rfa:
        print("allowPrivateIPAddress=" + str(rfa.get("allowPrivateIPAddress")).lower())
        print("allowMetaIPAddress=" + str(rfa.get("allowMetaIPAddress")).lower())
        print("source=" + str(p))
        raise SystemExit(0)
print("allowPrivateIPAddress=missing")
print("allowMetaIPAddress=missing")
print("source=")
'@
  try {
    $raw = (Invoke-WslPythonAsRoot $python 2>$null) -join "`n"
    $allowPrivate = $null
    $allowMeta = $null
    $source = ""
    if ($raw -match 'allowPrivateIPAddress=(true|false|missing)') {
      if ($matches[1] -ne "missing") { $allowPrivate = ($matches[1] -eq "true") }
    }
    if ($raw -match 'allowMetaIPAddress=(true|false|missing)') {
      if ($matches[1] -ne "missing") { $allowMeta = ($matches[1] -eq "true") }
    }
    if ($raw -match 'source=(.*)') {
      $source = $matches[1].Trim()
    }
    return [pscustomobject]@{
      checked = $true
      allowPrivateIPAddress = $allowPrivate
      allowMetaIPAddress = $allowMeta
      source = $source
      raw = $raw
      error = ""
    }
  } catch {
    return [pscustomobject]@{
      checked = $true
      allowPrivateIPAddress = $null
      allowMetaIPAddress = $null
      source = ""
      raw = ""
      error = $_.Exception.Message
    }
  }
}

function Get-OnlyOfficeBridgeStatus {
  $wslIp = ""
  $wslGateway = ""

  try { $wslIp = Get-WslOnlyOfficeIp } catch { $wslIp = "" }
  try { $wslGateway = Get-WslGatewayIp } catch { $wslGateway = "" }

  $apiProxyExists = $false
  if ($wslIp) {
    $apiProxyExists = Test-PortProxyEntry -ListenAddress "127.0.0.1" -ListenPort $Port -ConnectAddress $wslIp -ConnectPort $Port
  }

  $callbackProxyExists = $false
  $callbackFirewallExists = $false
  if ($wslGateway) {
    $callbackProxyExists = Test-PortProxyEntry -ListenAddress $wslGateway -ListenPort $AppPort -ConnectAddress "127.0.0.1" -ConnectPort $AppPort
    $callbackFirewallExists = Test-FirewallRule -DisplayName $script:OnlyOfficeCallbackFirewallRuleName -LocalAddress $wslGateway -LocalPort $AppPort
  }

  $apiHealth = Invoke-HttpProbe "http://127.0.0.1:$Port/healthcheck"
  $apiJs = Invoke-HttpProbe "http://127.0.0.1:$Port/web-apps/apps/api/documents/api.js"

  $localAppOpen = Test-TcpPortOpen -TargetHost "127.0.0.1" -PortNumber $AppPort
  $callbackBridgeOpen = $false
  $wslProbe = [pscustomobject]@{ ok = $false; output = "" }
  if ($wslGateway) {
    $callbackBridgeOpen = Test-TcpPortOpen -TargetHost $wslGateway -PortNumber $AppPort
    if ($callbackBridgeOpen) {
      $wslProbe = Invoke-WslCurlProbe "http://$wslGateway`:$AppPort/"
    } else {
      $wslProbe = [pscustomobject]@{
        ok = $false
        output = "skipped because Windows cannot open the callback bridge listener"
      }
    }
  }

  $lanExposure = Get-LanExposureResults @($AppPort, $Port)
  $lanOpen = @($lanExposure | Where-Object { $_.open }).Count -gt 0

  $privateStatus = Get-WslPrivateIpDownloadStatus

  $apiReady = [bool]($apiProxyExists -and $apiHealth.ok -and $apiJs.ok)
  $callbackReady = [bool]($callbackProxyExists -and $callbackFirewallExists -and $localAppOpen -and $callbackBridgeOpen -and $wslProbe.ok)
  $privateReady = [bool]($privateStatus.checked -and $privateStatus.allowPrivateIPAddress -eq $true)
  $ready = [bool]($apiReady -and $callbackReady -and $privateReady -and -not $lanOpen)

  return [pscustomobject]@{
    ready = $ready
    api_ready = $apiReady
    callback_ready = $callbackReady
    private_ip_ready = $privateReady
    mode = $Mode
    port = $Port
    app_port = $AppPort
    wsl_ip = $wslIp
    wsl_gateway_ip = $wslGateway
    onlyoffice_api_proxy = [pscustomobject]@{
      listen = "127.0.0.1:$Port"
      connect = $(if ($wslIp) { "$wslIp`:$Port" } else { "" })
      present = $apiProxyExists
      healthcheck = $apiHealth
      api_js = $apiJs
    }
    callback_proxy = [pscustomobject]@{
      listen = $(if ($wslGateway) { "$wslGateway`:$AppPort" } else { "" })
      connect = "127.0.0.1:$AppPort"
      present = $callbackProxyExists
      local_app_open = $localAppOpen
      windows_bridge_open = $callbackBridgeOpen
      wsl_probe = $wslProbe
    }
    callback_firewall = [pscustomobject]@{
      display_name = $script:OnlyOfficeCallbackFirewallRuleName
      local_address = $wslGateway
      local_port = $AppPort
      remote_address = "LocalSubnet"
      present = $callbackFirewallExists
    }
    private_ip_download = $privateStatus
    lan_exposure = $lanExposure
    lan_exposed = $lanOpen
  }
}

function Write-OnlyOfficeBridgeStatus {
  param([Parameter(Mandatory = $true)][object]$Status)

  Write-Section "Main Computer ONLYOFFICE WSL bridges"
  Write-Host ("mode: " + $Status.mode)
  Write-Host ("ONLYOFFICE API port: " + $Status.port)
  Write-Host ("Main Computer callback app port: " + $Status.app_port)
  Write-Host ("WSL distro IP: " + $Status.wsl_ip)
  Write-Host ("Windows WSL gateway IP: " + $Status.wsl_gateway_ip)

  Write-Host ""
  Write-Host "Browser/Windows -> ONLYOFFICE API bridge"
  Write-Host ("  " + $Status.onlyoffice_api_proxy.listen + " -> " + $Status.onlyoffice_api_proxy.connect)
  Write-Host ("  portproxy present: " + $Status.onlyoffice_api_proxy.present)
  Write-Host ("  healthcheck: " + $Status.onlyoffice_api_proxy.healthcheck.ok + " status=" + $Status.onlyoffice_api_proxy.healthcheck.status + " error=" + $Status.onlyoffice_api_proxy.healthcheck.error)
  Write-Host ("  api.js: " + $Status.onlyoffice_api_proxy.api_js.ok + " status=" + $Status.onlyoffice_api_proxy.api_js.status + " error=" + $Status.onlyoffice_api_proxy.api_js.error)

  Write-Host ""
  Write-Host "ONLYOFFICE/WSL -> Main Computer callback bridge"
  Write-Host ("  " + $Status.callback_proxy.listen + " -> " + $Status.callback_proxy.connect)
  Write-Host ("  portproxy present: " + $Status.callback_proxy.present)
  Write-Host ("  local app target open: " + $Status.callback_proxy.local_app_open)
  Write-Host ("  Windows can reach callback bridge: " + $Status.callback_proxy.windows_bridge_open)
  Write-Host ("  WSL probe: " + $Status.callback_proxy.wsl_probe.ok + " " + $Status.callback_proxy.wsl_probe.output)
  Write-Host ("  firewall rule present: " + $Status.callback_firewall.present + " name=" + $Status.callback_firewall.display_name)

  Write-Host ""
  Write-Host "ONLYOFFICE private-IP download setting"
  Write-Host ("  allowPrivateIPAddress: " + $Status.private_ip_download.allowPrivateIPAddress)
  Write-Host ("  allowMetaIPAddress: " + $Status.private_ip_download.allowMetaIPAddress)
  Write-Host ("  source: " + $Status.private_ip_download.source)
  if ($Status.private_ip_download.error) {
    Write-Host ("  error: " + $Status.private_ip_download.error)
  }

  Write-Host ""
  if (@($Status.lan_exposure).Count -eq 0) {
    Write-Host "LAN/VPN exposure check: no non-WSL IPv4 interfaces found"
  } else {
    $open = @($Status.lan_exposure | Where-Object { $_.open })
    if ($open.Count -gt 0) {
      foreach ($item in $open) {
        Write-Warning ("LAN/VPN exposure open: {0} {1}:{2}" -f $item.interface, $item.ip, $item.port)
      }
    } else {
      Write-Host "LAN/VPN exposure check: closed"
    }
  }

  Write-Host ""
  Write-Host ("bridge status: " + $(if ($Status.ready) { "ready" } else { "not ready" }))
  if (-not $Status.ready) {
    if (-not $Status.api_ready) { Write-Warning "ONLYOFFICE API bridge is not ready." }
    if (-not $Status.callback_ready) { Write-Warning "ONLYOFFICE callback bridge is not ready." }
    if (-not $Status.private_ip_ready) { Write-Warning "ONLYOFFICE private-IP download setting is not ready." }
    if ($Status.lan_exposed) { Write-Warning "A LAN/VPN interface is exposed on a managed port." }
  }
}

function Test-OnlyOfficeBridgeConfigurationReady {
  param([Parameter(Mandatory = $true)][object]$Status)

  return [bool](
    $Status.onlyoffice_api_proxy.present -and
    $Status.callback_proxy.present -and
    $Status.callback_firewall.present -and
    $Status.callback_proxy.windows_bridge_open -and
    $Status.callback_proxy.wsl_probe.ok -and
    $Status.private_ip_ready -and
    -not $Status.lan_exposed
  )
}

function Start-OnlyOfficeWindowsBridgeEntries {
  param(
    [Parameter(Mandatory = $true)][string]$ResolvedWslIp,
    [Parameter(Mandatory = $true)][string]$ResolvedWslGatewayIp,
    [switch]$RefreshApiProxy,
    [switch]$RefreshCallbackProxy
  )

  if ([string]::IsNullOrWhiteSpace($ResolvedWslIp)) {
    throw "Cannot start ONLYOFFICE Windows bridge because the WSL distro IP is empty."
  }
  if ([string]::IsNullOrWhiteSpace($ResolvedWslGatewayIp)) {
    throw "Cannot start ONLYOFFICE Windows callback bridge because the Windows WSL gateway IP is empty."
  }

  $iphlpsvc = Get-Service iphlpsvc -ErrorAction SilentlyContinue
  if ($null -ne $iphlpsvc -and $iphlpsvc.Status -ne "Running") {
    Start-Service iphlpsvc
  }

  Write-Host "Ensuring Windows -> ONLYOFFICE API bridge:"
  Write-Host "  127.0.0.1:$Port -> $ResolvedWslIp`:$Port"
  Set-PortProxyEntry -ListenAddress "127.0.0.1" -ListenPort $Port -ConnectAddress $ResolvedWslIp -ConnectPort $Port -Force:$RefreshApiProxy

  Write-Host "Ensuring ONLYOFFICE/WSL -> Main Computer callback bridge:"
  Write-Host "  $ResolvedWslGatewayIp`:$AppPort -> 127.0.0.1:$AppPort"
  Set-PortProxyEntry -ListenAddress $ResolvedWslGatewayIp -ListenPort $AppPort -ConnectAddress "127.0.0.1" -ConnectPort $AppPort -Force:$RefreshCallbackProxy
  Ensure-FirewallRule -DisplayName $script:OnlyOfficeCallbackFirewallRuleName -LocalAddress $ResolvedWslGatewayIp -LocalPort $AppPort
}

function Remove-OnlyOfficeWindowsBridgeEntries {
  param([string]$ResolvedWslGatewayIp = "")

  Remove-PortProxyEntry -ListenAddress "127.0.0.1" -ListenPort $Port
  if (-not [string]::IsNullOrWhiteSpace($ResolvedWslGatewayIp)) {
    Remove-PortProxyEntry -ListenAddress $ResolvedWslGatewayIp -ListenPort $AppPort
  }
  Remove-FirewallRuleIfPresent $script:OnlyOfficeApiFirewallRuleName
  Remove-FirewallRuleIfPresent $script:OnlyOfficeCallbackFirewallRuleName
}

function Ensure-OnlyOfficeBridges {
  if ($Mode -ne "wsl") {
    Write-Host "Bridge management is only required for WSL mode."
    return
  }

  Ensure-WslDistroInstalledAndReady -DistroName $Distro

  # WSL-side ONLYOFFICE configuration must be done by the already-working
  # non-elevated WSL context. The UAC child process is Windows-only and only
  # manages portproxy/firewall state.
  Ensure-WslPrivateIpDownloadAllowed

  $initialStatus = Get-OnlyOfficeBridgeStatus
  if ($initialStatus.ready) {
    Write-Host "ONLYOFFICE WSL bridges are already ready; no elevated changes are needed."
    Write-OnlyOfficeBridgeStatus $initialStatus
    return
  }

  if (Test-OnlyOfficeBridgeConfigurationReady $initialStatus) {
    Write-Host "ONLYOFFICE WSL bridge configuration is already installed; no elevated firewall or portproxy changes are needed."
    Write-OnlyOfficeBridgeStatus $initialStatus
    return
  }

  $resolvedWslIp = [string]$initialStatus.wsl_ip
  if ([string]::IsNullOrWhiteSpace($resolvedWslIp)) {
    $resolvedWslIp = Get-WslOnlyOfficeIp
  }

  $resolvedWslGatewayIp = [string]$initialStatus.wsl_gateway_ip
  if ([string]::IsNullOrWhiteSpace($resolvedWslGatewayIp)) {
    $resolvedWslGatewayIp = Get-WslGatewayIp
  }

  $forceApiProxyRefresh = [bool](
    $initialStatus.onlyoffice_api_proxy.present -and
    (-not $initialStatus.onlyoffice_api_proxy.healthcheck.ok -or -not $initialStatus.onlyoffice_api_proxy.api_js.ok)
  )
  $forceCallbackProxyRefresh = [bool](
    $initialStatus.callback_proxy.present -and
    (-not $initialStatus.callback_proxy.windows_bridge_open -or -not $initialStatus.callback_proxy.wsl_probe.ok)
  )

  if (-not (Test-IsWindowsAdmin)) {
    Invoke-ElevatedSelf "bridge-start-elevated" `
      -ElevatedWslIp $resolvedWslIp `
      -ElevatedWslGatewayIp $resolvedWslGatewayIp `
      -ElevatedForceApiProxyRefresh:$forceApiProxyRefresh `
      -ElevatedForceCallbackProxyRefresh:$forceCallbackProxyRefresh
    $status = Get-OnlyOfficeBridgeStatus
    Write-OnlyOfficeBridgeStatus $status
    return
  }

  Start-OnlyOfficeWindowsBridgeEntries `
    -ResolvedWslIp $resolvedWslIp `
    -ResolvedWslGatewayIp $resolvedWslGatewayIp `
    -RefreshApiProxy:$forceApiProxyRefresh `
    -RefreshCallbackProxy:$forceCallbackProxyRefresh

  $status = Get-OnlyOfficeBridgeStatus
  Write-OnlyOfficeBridgeStatus $status
}

function Remove-OnlyOfficeBridges {
  if ($Mode -ne "wsl") {
    Write-Host "Bridge management is only required for WSL mode."
    return
  }

  $resolvedWslGatewayIp = ""
  try { $resolvedWslGatewayIp = Get-WslGatewayIp } catch { $resolvedWslGatewayIp = "" }

  if (-not (Test-IsWindowsAdmin)) {
    Invoke-ElevatedSelf "bridge-stop-elevated" -ElevatedWslGatewayIp $resolvedWslGatewayIp
    Write-Host "Removed ONLYOFFICE WSL bridge portproxies/firewall rules."
    $status = Get-OnlyOfficeBridgeStatus
    Write-OnlyOfficeBridgeStatus $status
    return
  }

  Remove-OnlyOfficeWindowsBridgeEntries -ResolvedWslGatewayIp $resolvedWslGatewayIp

  Write-Host "Removed ONLYOFFICE WSL bridge portproxies/firewall rules."
  $status = Get-OnlyOfficeBridgeStatus
  Write-OnlyOfficeBridgeStatus $status
}

function Show-OnlyOfficeBridgeStatus {
  $status = Get-OnlyOfficeBridgeStatus
  Write-OnlyOfficeBridgeStatus $status
  if (-not $status.ready) {
    exit 2
  }
}

Write-Section "ONLYOFFICE control"
Write-Host "mode: $Mode"
Write-Host "port: $Port"
Write-Host "project: $ProjectName"
Write-Host "reserved ONLYOFFICE local URL: http://127.0.0.1:$Port"
Write-Host "Main Computer callback app port: $AppPort"

if ($Mode -eq "wsl") {
  switch ($Action) {
    "install" {
      Remove-StaleApplicationsDockerOnlyOffice
      Invoke-WslOnlyOffice "wsl-install-onlyoffice.sh" @("--port", "$Port", "--jwt-secret", "$JwtSecret")
      Ensure-OnlyOfficeBridges
    }
    "start" {
      Remove-StaleApplicationsDockerOnlyOffice
      Ensure-WslNativeOnlyOfficeInstalled
      Invoke-WslOnlyOffice "wsl-start-onlyoffice.sh" @("--port", "$Port")
      Ensure-OnlyOfficeBridges
    }
    "stop" {
      Invoke-WslOnlyOffice "wsl-stop-onlyoffice.sh" @("--port", "$Port")
      Remove-OnlyOfficeBridges
    }
    "status" {
      Invoke-WslOnlyOffice "wsl-status-onlyoffice.sh" @("--port", "$Port")
      Show-OnlyOfficeBridgeStatus
    }
    "doctor" {
      Invoke-WslOnlyOffice "wsl-status-onlyoffice.sh" @("--port", "$Port", "--verbose")
      Show-OnlyOfficeBridgeStatus
    }
    "bridge-start" {
      Remove-StaleApplicationsDockerOnlyOffice
      Ensure-OnlyOfficeBridges
    }
    "bridge-start-elevated" {
      if (-not (Test-IsWindowsAdmin)) {
        throw "bridge-start-elevated must run with Administrator rights."
      }
      Start-OnlyOfficeWindowsBridgeEntries `
        -ResolvedWslIp $WslIp `
        -ResolvedWslGatewayIp $WslGatewayIp `
        -RefreshApiProxy:$ForceApiProxyRefresh `
        -RefreshCallbackProxy:$ForceCallbackProxyRefresh
    }
    "bridge-status" {
      Show-OnlyOfficeBridgeStatus
    }
    "bridge-stop" {
      Remove-OnlyOfficeBridges
    }
    "bridge-stop-elevated" {
      if (-not (Test-IsWindowsAdmin)) {
        throw "bridge-stop-elevated must run with Administrator rights."
      }
      Remove-OnlyOfficeWindowsBridgeEntries -ResolvedWslGatewayIp $WslGatewayIp
      Write-Host "Removed ONLYOFFICE WSL bridge portproxies/firewall rules."
    }
  }
} else {
  switch ($Action) {
    "install" {
      Write-Host "Docker mode does not need a native install step; pulling/starting service instead."
      Invoke-DockerOnlyOffice "start"
    }
    "start" {
      Invoke-DockerOnlyOffice "start"
    }
    "stop" {
      Invoke-DockerOnlyOffice "stop"
    }
    "status" {
      Invoke-DockerOnlyOffice "status"
    }
    "doctor" {
      Invoke-DockerOnlyOffice "status"
    }
    "bridge-start" {
      Write-Host "Docker mode does not use WSL bridge-start."
    }
    "bridge-start-elevated" {
      Write-Host "Docker mode does not use WSL bridge-start-elevated."
    }
    "bridge-status" {
      Write-Host "Docker mode does not use WSL bridge-status."
    }
    "bridge-stop" {
      Write-Host "Docker mode does not use WSL bridge-stop."
    }
    "bridge-stop-elevated" {
      Write-Host "Docker mode does not use WSL bridge-stop-elevated."
    }
  }
}

Write-Host ""
Write-Host "Use these Main Computer env vars for local Windows/WSL mode:"
Write-Host "  MAIN_COMPUTER_ONLYOFFICE_PUBLIC_URL=http://127.0.0.1:$Port"
Write-Host "  MAIN_COMPUTER_ONLYOFFICE_INTERNAL_URL=http://127.0.0.1:$Port"
if ($Action -eq "bridge-start-elevated" -or $Action -eq "bridge-stop-elevated") {
  $callbackHostForHelp = $(if (-not [string]::IsNullOrWhiteSpace($WslGatewayIp)) { $WslGatewayIp } else { "127.0.0.1" })
} else {
  $callbackHostForHelp = try { Get-WslGatewayIp } catch { "127.0.0.1" }
}
Write-Host "  MAIN_COMPUTER_ONLYOFFICE_CALLBACK_BASE_URL=http://$callbackHostForHelp`:$AppPort"
Write-Host "  MAIN_COMPUTER_ONLYOFFICE_JWT_SECRET=<same secret used above>"
