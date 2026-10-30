# Invoked by MainComputer-SLH-Local startup task under SYSTEM.
[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$Root,
    [Parameter(Mandatory=$true)][string]$Python
)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $Root
& $Python -m main_computer.slh.local_service run
exit $LASTEXITCODE
