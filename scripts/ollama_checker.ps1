$diag = "$env:USERPROFILE\Desktop\ollama_diag_$(Get-Date -Format yyyyMMdd_HHmmss).txt"
Start-Transcript -Path $diag -Force

Write-Host "`n=== OLLAMA PATH / VERSION WITHOUT RUNNING OLLAMA ==="
where.exe ollama
Get-Command ollama -ErrorAction SilentlyContinue | Format-List Source,Version
$ollamaExe = (Get-Command ollama -ErrorAction SilentlyContinue).Source
if ($ollamaExe) {
  (Get-Item $ollamaExe).VersionInfo | Format-List FileName,ProductVersion,FileVersion
}

Write-Host "`n=== WINDOWS / CPU / GPU ==="
systeminfo | findstr /B /C:"OS Name" /C:"OS Version" /C:"System Type"
Get-CimInstance Win32_Processor | Select-Object Name,Manufacturer,NumberOfCores,NumberOfLogicalProcessors
Get-CimInstance Win32_VideoController | Select-Object Name,DriverVersion,AdapterRAM

Write-Host "`n=== OLLAMA ENV VARS ==="
"User OLLAMA_HOST    = $([Environment]::GetEnvironmentVariable('OLLAMA_HOST','User'))"
"Machine OLLAMA_HOST = $([Environment]::GetEnvironmentVariable('OLLAMA_HOST','Machine'))"
"Process OLLAMA_HOST = $env:OLLAMA_HOST"
"User OLLAMA_MODELS  = $([Environment]::GetEnvironmentVariable('OLLAMA_MODELS','User'))"
"Machine OLLAMA_MODELS = $([Environment]::GetEnvironmentVariable('OLLAMA_MODELS','Machine'))"

Write-Host "`n=== PROCESSES / PORTS ==="
Get-Process -Name ollama -ErrorAction SilentlyContinue | Select-Object Id,Path,StartTime
Get-NetTCPConnection -LocalPort 11434,18034 -State Listen -ErrorAction SilentlyContinue |
  Select-Object LocalAddress,LocalPort,State,OwningProcess
netstat -ano | findstr ":11434"
netstat -ano | findstr ":18034"

Write-Host "`n=== API CHECKS ==="
curl.exe --max-time 5 http://127.0.0.1:11434/api/version
curl.exe --max-time 5 http://127.0.0.1:11434/api/tags
curl.exe --max-time 5 http://127.0.0.1:18034/api/version
curl.exe --max-time 5 http://127.0.0.1:18034/api/tags

Write-Host "`n=== OLLAMA LOG FILES ==="
Get-ChildItem "$env:LOCALAPPDATA\Ollama" -Filter "*.log" -ErrorAction SilentlyContinue |
  Select-Object Name,Length,LastWriteTime

Write-Host "`n=== SERVER LOG TAIL ==="
Get-Content "$env:LOCALAPPDATA\Ollama\server.log" -Tail 120 -ErrorAction SilentlyContinue

Write-Host "`n=== APP LOG TAIL ==="
Get-Content "$env:LOCALAPPDATA\Ollama\app.log" -Tail 120 -ErrorAction SilentlyContinue

Write-Host "`n=== UPGRADE LOG TAIL ==="
Get-Content "$env:LOCALAPPDATA\Ollama\upgrade.log" -Tail 80 -ErrorAction SilentlyContinue

Stop-Transcript
Write-Host "Saved diagnostic file to: $diag"