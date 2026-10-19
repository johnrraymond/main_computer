# Run from C:\Users\subsi\main_computer, after applying this patch.
# Stages: Build, Cutover, Train, Probe, Test, All. All intentionally omits Test.
[CmdletBinding()]
param(
    [ValidateSet('Build','Cutover','Train','Probe','Test','All')]
    [string]$Stage = 'All',
    [string]$Python = 'C:\Users\subsi\NanoJev\.venv\Scripts\python.exe',
    [string]$OldRun = "$HOME\NanoJev\runs\hawk_dove_million_v1",
    [string]$NewRun = "$HOME\NanoJev\runs\hawk_dove_million_scientific_v2",
    [string]$OldDb = 'runtime\hawk-dove-million\teacher.sqlite3',
    [string]$NewDb = 'runtime\hawk-dove-million\teacher-scientific-v2.sqlite3',
    [string]$Fixture = 'examples\hawk_dove_million_DEMO_ONLY.json',
    [string]$Source = 'runtime\game-transform-experiment\source-v4.pt',
    [int]$Instances = 201,
    [Nullable[int]]$Steps = $null,
    [Nullable[int]]$AdditionalSteps = $null,
    [int]$ChunkSteps = 300,
    [string]$DiagnoseTier = 'T01',
    [switch]$DiagnoseBoth,
    [string[]]$MaskWeights = @(),
    [switch]$AllowModelDownload
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
    throw "NanoJev Python does not exist: $Python"
}
function Invoke-Python([string[]]$Arguments) {
    Write-Host ('> ' + $Python + ' ' + ($Arguments -join ' '))
    & $Python @Arguments
    if ($LASTEXITCODE -ne 0) { throw "Python failed ($LASTEXITCODE): $($Arguments -join ' ')" }
}
$Build = @('Build','All') -contains $Stage
$Fork = @('Cutover','All') -contains $Stage
$Train = @('Train','All') -contains $Stage
$Probe = @('Probe','All') -contains $Stage
$Test = @('Test') -contains $Stage
if ($PSBoundParameters.ContainsKey('Steps') -and
    $PSBoundParameters.ContainsKey('AdditionalSteps')) {
    throw 'Specify either -Steps (absolute checkpoint target) OR -AdditionalSteps (relative budget), never both.'
}
function Get-LocalCheckpointStep {
    $checkpoint = Join-Path $NewRun 'latest.pt'
    if (-not (Test-Path -LiteralPath $checkpoint -PathType Leaf)) {
        throw "No scientific checkpoint at $checkpoint. Run -Stage Cutover once before training."
    }
    # This reads a small checkpoint metadata field on CPU, without loading Qwen.
    # Avoid embedded quoted Python string literals: Windows PowerShell may strip
    # them when passing the -c argument to native executables.
    $code = 'import sys,torch; p=torch.load(sys.argv[1],map_location=sys.argv[2],weights_only=True); print(int(p[sys.argv[3]]))'
    $response = & $Python -c $code $checkpoint 'cpu' 'step'
    if ($LASTEXITCODE -ne 0) { throw "Cannot read scientific checkpoint step: $checkpoint" }
    $number = @($response | Where-Object { "$_" -match '^[0-9]+$' } | Select-Object -Last 1)
    if ($number.Count -ne 1) {
        throw "Cannot parse scientific checkpoint step from: $($response -join ', ')"
    }
    return [int]$number[0]
}
if ($Build) {
    # No overwrites. Existing v2 database requires explicit separate audit/next stages.
    if (Test-Path -LiteralPath $NewDb) {
        throw "Scientific database exists; refusing overwrite: $NewDb"
    }
    Invoke-Python -Arguments @('-m','unittest','tests.test_nanojev_hawk_dove_scientific','-v')
    Invoke-Python -Arguments @('-m','tools.nanojev_hawk_dove_million','build',
        '--fixture',$Fixture,'--out',$NewDb,'--instances',"$Instances",
        '--mutation-mode','lattice','--representation','raw_logbar_scientific')
    Invoke-Python -Arguments @('-m','tools.nanojev_hawk_dove_million_test',
        '--db',$NewDb,'--fixture',$Fixture)
    Invoke-Python -Arguments @('-m','tools.nanojev_hawk_dove_scientific_train','preflight',
        '--teacher',$NewDb)
}
if ($Fork) {
    if (Test-Path -LiteralPath $NewRun) {
        throw "Scientific run exists; refusing overwrite: $NewRun"
    }
    Invoke-Python -Arguments @('-m','tools.nanojev_hawk_dove_scientific_cutover',
        '--old-run',$OldRun,'--new-run',$NewRun,'--old-db',$OldDb,
        '--new-db',$NewDb,'--fixture',$Fixture,'--source',$Source)
    $Probe=$true
}
$Extra=@()
if ($AllowModelDownload) { $Extra+= '--allow-model-download' }
function Invoke-DiagnosticProbe([switch]$CompareLegacy) {
    # Each tier diagnosis is exhaustive on DEV and remains read-only.
    Invoke-Python -Arguments (@('-m','tools.nanojev_hawk_dove_scientific_train','probe',
        '--teacher',$NewDb,'--source',$Source,'--output',$NewRun,'--device','cuda')+$Extra)
    $tiers = if ($DiagnoseBoth) { @('T00','T01') } else { @($DiagnoseTier) }
    foreach ($tier in $tiers) {
        Invoke-Python -Arguments (@('-m','tools.nanojev_hawk_dove_scientific_train','diagnose',
            '--teacher',$NewDb,'--source',$Source,'--output',$NewRun,
            '--device','cuda','--diagnose-tier',$tier)+$Extra)
        $latest=Get-ChildItem -LiteralPath $NewRun -Filter "diagnose-$tier-step-*.json" |
            Sort-Object Name -Descending | Select-Object -First 1
        if ($null -eq $latest) { throw "No saved full dev diagnosis for $tier" }
        $before = Get-ChildItem -LiteralPath $NewRun -Filter "diagnose-$tier-step-*.json" |
            Sort-Object Name | Select-Object -First 1
        $analyze=@('-m','tools.nanojev_hawk_dove_scientific_probe',
            '--report',$latest.FullName,'--teacher',$NewDb)
        if ($null -ne $before -and $before.FullName -ne $latest.FullName) {
            $analyze+=@('--compare-to',$before.FullName)
        } elseif ($CompareLegacy) {
            $donorReport=Join-Path $OldRun $latest.Name
            if (Test-Path -LiteralPath $donorReport -PathType Leaf) {
                $analyze+=@('--compare-to',$donorReport)
            }
        }
        Invoke-Python -Arguments $analyze
    }
}
if ($Fork) {
    # Capture before-training scientific baseline. Critical to measure transfer.
    Invoke-DiagnosticProbe -CompareLegacy
}
if ($Train) {
    $current = Get-LocalCheckpointStep
    if ($PSBoundParameters.ContainsKey('Steps')) {
        $target = [long]$Steps
        if ($target -le $current) {
            throw ("Absolute -Steps $target is not greater than scientific checkpoint step $current. " +
                'Use -AdditionalSteps 6000 or choose an absolute -Steps larger than the checkpoint.')
        }
    } else {
        $delta = if ($PSBoundParameters.ContainsKey('AdditionalSteps')) { [long]$AdditionalSteps } else { [long]3000 }
        if ($delta -le 0) { throw '-AdditionalSteps must be a positive integer.' }
        $target = [long]$current + $delta
    }
    if ($target -gt [int]::MaxValue) { throw 'Training target exceeds supported 32-bit step counter.' }
    Write-Host "Scientific training: current step=$current, absolute target=$target, additional steps=$($target - $current)"
    $TrainArguments=@('-m','tools.nanojev_hawk_dove_scientific_train','train',
        '--teacher',$NewDb,'--source',$Source,'--output',$NewRun,
        '--device','cuda','--steps',"$target",'--chunk-steps',"$ChunkSteps")
    foreach ($m in $MaskWeights) { $TrainArguments+=@('--mask-weight',$m) }
    Invoke-Python -Arguments ($TrainArguments+$Extra)
    $Probe=$true
}
if ($Train -or ($Probe -and -not $Fork)) {
    Invoke-DiagnosticProbe
}
if ($Test) {
    Write-Warning 'This is the one-time withheld test. Do not tune hyperparameters based on test results.'
    Invoke-Python -Arguments (@('-m','tools.nanojev_hawk_dove_scientific_train','test',
        '--teacher',$NewDb,'--source',$Source,'--output',$NewRun,
        '--device','cuda')+$Extra)
}
