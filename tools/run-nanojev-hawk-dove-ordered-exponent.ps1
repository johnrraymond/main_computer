# Ordered exponent / log magnitude A/B cutover (CUDA); does not touch donor.
[CmdletBinding()]
param(
    [ValidateSet('Fork','Baseline','TrainControl','TrainOrdered','TrainBoth','ProbeBoth','TrainProbe','Compare','All')]
    [string]$Stage = 'Fork',
    [string]$Python = 'C:\Users\subsi\NanoJev\.venv\Scripts\python.exe',
    [string]$Donor = "$HOME\NanoJev\runs\hawk_dove_million_scientific_v2",
    [string]$Root = "$HOME\NanoJev\runs\hawk_dove_million_ordered_exponent_ab_v1",
    [string]$Teacher = 'runtime\hawk-dove-million\teacher-scientific-v2.sqlite3',
    [string]$Source = 'runtime\game-transform-experiment\source-v4.pt',
    [long]$AdditionalSteps = 12000,
    [int]$ChunkSteps = 300,
    [int]$CacheMemoryMB = 1024,
    [double]$ExponentWeight = 0.25,
    [double]$MagnitudeWeight = 0.50,
    [ValidateSet('T00','T01','T02','T03','T04','T05','T06','T07','T08')]
    [string]$Tier = 'T02',
    [int]$TrainPerMask = 20
)
Set-StrictMode -Version Latest
$ErrorActionPreference='Stop'
if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {throw "Python not found: $Python"}
if ($AdditionalSteps -lt 1 -or $ChunkSteps -lt 1 -or $CacheMemoryMB -lt 0) {throw 'Invalid training/cache budget'}
function Invoke-Python([string[]]$Arguments) {
    & $Python @Arguments
    if ($LASTEXITCODE -ne 0) {throw "NanoJev failed: exit code $LASTEXITCODE"}
}
$module='tools.nanojev_hawk_dove_ordered_exponent_experiment'
$probe='tools.nanojev_hawk_dove_ordered_exponent_probe'
$base=@('--root',$Root,'--teacher',$Teacher,'--source',$Source)
function Make-Fork {
    if (Test-Path -LiteralPath $Root) {throw "Fork already exists; refusing to overwrite: $Root"}
    Invoke-Python (@('-m',$module,'fork','--donor',$Donor) + $base +
        @('--exponent-weight',"$ExponentWeight",'--magnitude-weight',"$MagnitudeWeight"))
}
function Train-Branch([string]$Branch) {
    Invoke-Python (@('-m',$module,'train') + $base +
        @('--branch',$Branch,'--additional-steps',"$AdditionalSteps",
          '--chunk-steps',"$ChunkSteps",'--cache-memory-mb',"$CacheMemoryMB",'--device','cuda'))
}
function Probe-Branch([string]$Branch,[string]$Split,[int]$PerMask) {
    Invoke-Python (@('-m',$probe,'probe') + $base +
        @('--branch',$Branch,'--split',$Split,'--tier',$Tier,'--per-mask',"$PerMask",'--device','cuda'))
}
function Probe-Both([string]$Split,[int]$PerMask) {
    Probe-Branch 'control' $Split $PerMask
    Probe-Branch 'ordered' $Split $PerMask
}
function Compare-Both([string]$Split) {
    Invoke-Python (@('-m',$probe,'compare') + $base + @('--split',$Split,'--tier',$Tier))
}
switch ($Stage) {
    'Fork'         {Make-Fork}
    'Baseline'     {Probe-Both 'dev' 0;Compare-Both 'dev'}
    'TrainControl' {Train-Branch 'control'}
    'TrainOrdered' {Train-Branch 'ordered'}
    'TrainBoth'    {Train-Branch 'control';Train-Branch 'ordered'}
    'ProbeBoth'    {Probe-Both 'dev' 0}
    'TrainProbe'   {Probe-Both 'train' $TrainPerMask;Compare-Both 'train'}
    'Compare'      {Compare-Both 'dev'}
    'All' {
        Make-Fork
        Probe-Both 'dev' 0
        Compare-Both 'dev'
        Train-Branch 'control'
        Train-Branch 'ordered'
        Probe-Both 'dev' 0
        Compare-Both 'dev'
        Probe-Both 'train' $TrainPerMask
        Compare-Both 'train'
    }
}
