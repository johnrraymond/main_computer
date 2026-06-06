<#
.SYNOPSIS
  Runs an isolated Docker ONLYOFFICE browser-load smoke test.

.DESCRIPTION
  This helper intentionally does not exercise the Main Computer ONLYOFFICE
  integration. It answers one narrow question: can Chrome load ONLYOFFICE
  api.js from a throwaway Docker Document Server on localhost?

  Defaults:
    Docker test port:        18085
    Container name:          mc-onlyoffice-docker-twiddle
    JWT/private-IP mode:     JWT disabled, private/meta IP callbacks allowed

  The default port matches Main Computer's local Docker ONLYOFFICE provider.
  Stop the managed provider container before using this isolated throwaway
  smoke test on the same port.

  The helper leaves the container running by default so you can inspect the
  opened Chrome tabs and DevTools console. Pass -RemoveWhenDone to remove the
  container after the curl checks and browser pages are launched.
#>

param(
  [int]$Port = 18085,

  [string]$ContainerName = "mc-onlyoffice-docker-twiddle",

  [string]$Image = "onlyoffice/documentserver:latest",

  [int]$ReadyTimeoutSeconds = 300,

  [int]$ReadyPollSeconds = 5,

  [switch]$NoOpenChrome,

  [switch]$RemoveWhenDone
)

$ErrorActionPreference = "Stop"

$ForbiddenContainerNames = @(
  "main-computer-applications-onlyoffice-1",
  "main-computer-onlyoffice-documentserver"
)

if ($ForbiddenContainerNames -contains $ContainerName) {
  throw "Refusing to reuse reserved container name '$ContainerName'. Use isolated test name 'mc-onlyoffice-docker-twiddle'."
}

function Write-Section {
  param([Parameter(Mandatory = $true)][string]$Title)

  Write-Host ""
  Write-Host $Title
  Write-Host ("-" * $Title.Length)
}

function Assert-Command {
  param([Parameter(Mandatory = $true)][string]$Name)

  $cmd = Get-Command $Name -CommandType Application -ErrorAction SilentlyContinue
  if ($null -eq $cmd) {
    throw "$Name was not found on PATH."
  }
}

function Get-NullDevice {
  if ([System.IO.Path]::DirectorySeparatorChar -eq "\") {
    return "NUL"
  }

  return "/dev/null"
}

function Invoke-Docker {
  param([Parameter(Mandatory = $true)][string[]]$DockerArgs)

  & docker @DockerArgs
  if ($LASTEXITCODE -ne 0) {
    throw "docker $($DockerArgs -join ' ') failed with exit code $LASTEXITCODE."
  }
}

function Get-DockerContainerIdByExactName {
  param([Parameter(Mandatory = $true)][string]$Name)

  $escapedName = [System.Text.RegularExpressions.Regex]::Escape($Name)
  $nameFilter = "name=^/${escapedName}$"
  $containerIds = @(& docker ps -aq --filter $nameFilter)

  if ($LASTEXITCODE -ne 0) {
    throw "docker ps -aq --filter $nameFilter failed with exit code $LASTEXITCODE."
  }

  return @($containerIds | Where-Object { -not [string]::IsNullOrWhiteSpace($_) })
}

function Remove-DockerContainerIfPresent {
  param([Parameter(Mandatory = $true)][string]$Name)

  $containerIds = @(Get-DockerContainerIdByExactName -Name $Name)
  if ($containerIds.Count -eq 0) {
    Write-Host "No existing throwaway container named $Name found."
    return
  }

  Write-Host "Removing existing throwaway container $Name."
  & docker rm -f $Name | Out-Null
  if ($LASTEXITCODE -ne 0) {
    throw "docker rm -f $Name failed with exit code $LASTEXITCODE."
  }
}

function Invoke-CurlCheck {
  param(
    [Parameter(Mandatory = $true)][string]$Url,
    [string]$ExpectedContentTypePrefix = ""
  )

  Write-Host ""
  Write-Host "curl.exe -v `"$Url`" -o $(Get-NullDevice)"
  & curl.exe -v --fail "$Url" -o (Get-NullDevice)
  if ($LASTEXITCODE -ne 0) {
    throw "curl.exe failed for $Url with exit code $LASTEXITCODE."
  }

  $response = Invoke-WebRequest `
    -Uri $Url `
    -Method GET `
    -TimeoutSec 30 `
    -UseBasicParsing `
    -ErrorAction Stop

  if ([int]$response.StatusCode -ne 200) {
    throw "Expected HTTP 200 from $Url but got $($response.StatusCode)."
  }

  if (-not [string]::IsNullOrWhiteSpace($ExpectedContentTypePrefix)) {
    $contentType = [string]$response.Headers["Content-Type"]
    if (-not $contentType.StartsWith($ExpectedContentTypePrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
      throw "Expected Content-Type starting with '$ExpectedContentTypePrefix' from $Url but got '$contentType'."
    }
  }

  return $response
}

function Wait-OnlyOfficeHealth {
  param(
    [Parameter(Mandatory = $true)][string]$HealthUrl,
    [int]$TimeoutSeconds,
    [int]$PollSeconds
  )

  $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
  $lastError = $null

  while ((Get-Date) -lt $deadline) {
    try {
      $response = Invoke-WebRequest `
        -Uri $HealthUrl `
        -Method GET `
        -TimeoutSec 10 `
        -UseBasicParsing `
        -ErrorAction Stop

      $body = ""
      if ($null -ne $response.Content) {
        $body = ([string]$response.Content).Trim()
      }

      if ([int]$response.StatusCode -eq 200 -and $body -eq "true") {
        Write-Host "ONLYOFFICE healthcheck returned HTTP 200 and true."
        return
      }

      $lastError = "Status=$($response.StatusCode) Body='$body'"
    }
    catch {
      $lastError = $_.Exception.Message
    }

    Write-Host "Waiting for ONLYOFFICE healthcheck at $HealthUrl ..."
    Start-Sleep -Seconds $PollSeconds
  }

  throw "Timed out waiting for ONLYOFFICE healthcheck at $HealthUrl. Last error: $lastError"
}

function Find-ChromeExecutable {
  $candidates = @()

  if ($env:ProgramFiles) {
    $candidates += (Join-Path $env:ProgramFiles "Google\Chrome\Application\chrome.exe")
  }

  if (${env:ProgramFiles(x86)}) {
    $candidates += (Join-Path ${env:ProgramFiles(x86)} "Google\Chrome\Application\chrome.exe")
  }

  if ($env:LOCALAPPDATA) {
    $candidates += (Join-Path $env:LOCALAPPDATA "Google\Chrome\Application\chrome.exe")
  }

  foreach ($candidate in $candidates) {
    if (Test-Path -LiteralPath $candidate) {
      return $candidate
    }
  }

  $chromeCommand = Get-Command chrome.exe -CommandType Application -ErrorAction SilentlyContinue
  if ($null -ne $chromeCommand) {
    return $chromeCommand.Source
  }

  return $null
}

function Open-BrowserUrl {
  param([Parameter(Mandatory = $true)][string]$Url)

  $chrome = Find-ChromeExecutable
  if ($chrome) {
    Start-Process -FilePath $chrome -ArgumentList @($Url)
    return
  }

  Write-Warning "chrome.exe was not found. Opening with the default browser instead: $Url"
  Start-Process $Url
}

function New-BrowserHarness {
  param([Parameter(Mandatory = $true)][string]$ApiJsUrl)

  $html = @"
<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>ONLYOFFICE Docker browser-speak smoke test</title>
  <style>
    body { font-family: system-ui, sans-serif; margin: 2rem; }
    pre { border: 1px solid #ccc; padding: 1rem; white-space: pre-wrap; }
  </style>
</head>
<body>
  <h1>ONLYOFFICE Docker browser-speak smoke test</h1>
  <p>Expected console output: <code>Docker api.js loaded true</code></p>
  <pre id="log">Loading $ApiJsUrl ...</pre>
  <script>
(() => {
  const logEl = document.getElementById("log");
  const write = (message) => {
    logEl.textContent += "\n" + message;
  };

  delete window.DocsAPI;

  const s = document.createElement("script");
  s.src = "$ApiJsUrl";
  s.async = true;
  s.referrerPolicy = "no-referrer";

  s.onload = () => {
    const ok = Boolean(window.DocsAPI && window.DocsAPI.DocEditor);
    write("Docker api.js loaded " + ok);
    document.body.dataset.onlyofficeDockerApiLoaded = String(ok);
    console.log("Docker api.js loaded", ok);
  };

  s.onerror = (e) => {
    write("Docker api.js script.onerror");
    document.body.dataset.onlyofficeDockerApiLoaded = "false";
    console.error("Docker api.js script.onerror", e);
  };

  document.head.appendChild(s);
})();
  </script>
</body>
</html>
"@

  $path = Join-Path ([System.IO.Path]::GetTempPath()) "main-computer-onlyoffice-docker-browser-speak.html"
  Set-Content -LiteralPath $path -Value $html -Encoding UTF8
  return $path
}

function Write-DevToolsSnippet {
  param([Parameter(Mandatory = $true)][string]$ApiJsUrl)

  $snippet = @"
(() => {
  delete window.DocsAPI;

  const s = document.createElement("script");
  s.src = "$ApiJsUrl";
  s.async = true;
  s.referrerPolicy = "no-referrer";

  s.onload = () => {
    console.log("Docker api.js loaded", Boolean(window.DocsAPI && window.DocsAPI.DocEditor));
  };

  s.onerror = (e) => {
    console.error("Docker api.js script.onerror", e);
  };

  document.head.appendChild(s);
})();
"@

  Write-Section "Chrome DevTools script-injection snippet"
  Write-Host $snippet
}

Assert-Command "docker"
Assert-Command "curl.exe"

$BaseUrl = "http://127.0.0.1:${Port}"
$HealthUrl = "$BaseUrl/healthcheck"
$ApiJsUrl = "$BaseUrl/web-apps/apps/api/documents/api.js"
$ApiJsQueryUrl = "${ApiJsUrl}?twiddle=1"
$Publish = "127.0.0.1:${Port}:80"

Write-Section "ONLYOFFICE Docker browser-speak test"
Write-Host "Docker test port: $Port"
Write-Host "Container name: $ContainerName"
Write-Host "Image: $Image"
Write-Host "JWT enabled: false"
Write-Host "Allow private IP downloads: true"
Write-Host "Allow meta IP downloads: true"

try {
  Write-Section "Start throwaway container"
  Remove-DockerContainerIfPresent -Name $ContainerName

  Invoke-Docker -DockerArgs @(
    "run",
    "-d",
    "--name", $ContainerName,
    "--restart", "no",
    "-p", $Publish,
    "-e", "JWT_ENABLED=false",
    "-e", "ALLOW_PRIVATE_IP_ADDRESS=true",
    "-e", "ALLOW_META_IP_ADDRESS=true",
    $Image
  )

  Invoke-Docker -DockerArgs @(
    "ps",
    "--filter", "name=$ContainerName",
    "--format", "table {{.ID}}`t{{.Names}}`t{{.Image}}`t{{.Ports}}`t{{.Status}}"
  )

  Write-Section "Wait for readiness"
  Wait-OnlyOfficeHealth -HealthUrl $HealthUrl -TimeoutSeconds $ReadyTimeoutSeconds -PollSeconds $ReadyPollSeconds

  Write-Section "Windows curl checks"
  Invoke-CurlCheck -Url $HealthUrl | Out-Null
  Invoke-CurlCheck -Url $ApiJsUrl -ExpectedContentTypePrefix "application/javascript" | Out-Null
  Invoke-CurlCheck -Url $ApiJsQueryUrl -ExpectedContentTypePrefix "application/javascript" | Out-Null

  Write-Host ""
  Write-Host "curl checks passed: healthcheck, api.js, and api.js?twiddle=1 all returned HTTP 200."

  $harnessPath = New-BrowserHarness -ApiJsUrl $ApiJsUrl
  $harnessUrl = ([System.Uri]$harnessPath).AbsoluteUri

  if (-not $NoOpenChrome) {
    Write-Section "Chrome direct and script-injection tests"
    Write-Host "Opening direct api.js URL in Chrome/default browser:"
    Write-Host $ApiJsUrl
    Open-BrowserUrl -Url $ApiJsUrl

    Write-Host ""
    Write-Host "Opening local browser harness:"
    Write-Host $harnessUrl
    Open-BrowserUrl -Url $harnessUrl

    Write-Host ""
    Write-Host "Expected harness/console result: Docker api.js loaded true"
  } else {
    Write-Section "Chrome direct and script-injection tests"
    Write-Host "Skipped browser launch because -NoOpenChrome was passed."
    Write-Host "Direct api.js URL:"
    Write-Host $ApiJsUrl
    Write-Host "Browser harness:"
    Write-Host $harnessUrl
  }

  Write-DevToolsSnippet -ApiJsUrl $ApiJsUrl

  Write-Section "Cleanup"
  if ($RemoveWhenDone) {
    Write-Host "Removing throwaway container $ContainerName."
  } else {
    Write-Host "Container left running for browser inspection."
    Write-Host "When done, clean up with:"
    Write-Host "docker rm -f $ContainerName"
  }
}
finally {
  if ($RemoveWhenDone) {
    Remove-DockerContainerIfPresent -Name $ContainerName
  }
}
