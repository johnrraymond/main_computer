param(
  [Parameter(Mandatory = $true)]
  [string]$SiteId,
  [ValidateSet("local", "local-prod", "prod", "production", "dev")]
  [string]$Lane = "local",
  [switch]$DryRun,
  [switch]$NoVerify
)

$ErrorActionPreference = "Stop"

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$Script = Join-Path $RepoRoot "tools\local-platform\publish-website.py"
$argsList = @($Script, $SiteId, "--lane", $Lane, "--repo-root", $RepoRoot)

if ($DryRun) {
  $argsList += "--dry-run"
}
if ($NoVerify) {
  $argsList += "--no-verify"
}

python @argsList
