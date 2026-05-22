Write-Host "A: before Start-Job" -ForegroundColor Cyan

$job = Start-Job -ScriptBlock {
  Write-Output "B: inside job before Get-CimInstance"
  Get-CimInstance Win32_Process | Select-Object -First 1 | Out-Null
  Write-Output "C: inside job after Get-CimInstance"
}

Write-Host "D: after Start-Job, job id = $($job.Id)" -ForegroundColor Cyan
Write-Host "E: before Wait-Job 8s" -ForegroundColor Cyan

$done = Wait-Job $job -Timeout 8

Write-Host "F: after Wait-Job" -ForegroundColor Cyan

if ($done) {
  Receive-Job $job
  Remove-Job $job
  Write-Host "RESULT: job completed"
} else {
  Write-Host "RESULT: job did not complete; CIM/WMI is hanging inside job" -ForegroundColor Yellow
  Stop-Job $job -ErrorAction SilentlyContinue
  Remove-Job $job -Force -ErrorAction SilentlyContinue
}