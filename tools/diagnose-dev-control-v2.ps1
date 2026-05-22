Write-Host "1. Testing normal Get-Process..."
Measure-Command { Get-Process | Select-Object -First 1 | Out-Null }

Write-Host "`n2. Testing CIM/WMI Win32_Process with 8 second timeout..."
$job = Start-Job -ScriptBlock {
  Get-CimInstance Win32_Process | Select-Object -First 1 | Out-Null
}

if (Wait-Job $job -Timeout 8) {
  Receive-Job $job
  Remove-Job $job
  Write-Host "RESULT: CIM/WMI Win32_Process works."
} else {
  Stop-Job $job -ErrorAction SilentlyContinue
  Remove-Job $job -Force -ErrorAction SilentlyContinue
  Write-Host "RESULT: CIM/WMI Win32_Process is hanging."
}

Write-Host "`n3. Native non-WMI process/port check..."
cmd /c "tasklist | findstr /i python"
cmd /c "netstat -ano | findstr /r /c:':8765 .*LISTENING' /c:':8766 .*LISTENING' /c:':18765 .*LISTENING'"