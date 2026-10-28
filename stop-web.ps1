param(
  [Parameter(Mandatory = $true)]
  [string]$ProjectRoot
)

$ErrorActionPreference = 'Stop'
$serverScript = [System.IO.Path]::GetFullPath((Join-Path $ProjectRoot 'web_server.py'))
$pidFile = Join-Path $ProjectRoot '.runtime\web.pid'

$servers = Get-CimInstance Win32_Process | Where-Object {
  ($_.Name -eq 'python.exe' -or $_.Name -eq 'pythonw.exe') -and
  $_.CommandLine -and
  $_.CommandLine.IndexOf($serverScript, [System.StringComparison]::OrdinalIgnoreCase) -ge 0 -and
  $_.CommandLine -match '(?i)--port\s+\d+'
}

if (-not $servers) {
  if (Test-Path $pidFile) { Remove-Item $pidFile -Force }
  Write-Output 'No confluence-dl Web service is running from this project.'
  exit 0
}

foreach ($server in $servers) {
  Write-Output "Stopping confluence-dl Web service (PID $($server.ProcessId))..."
  & taskkill.exe /PID $server.ProcessId /T /F | Out-Null
  if ($LASTEXITCODE -ne 0) {
    throw "Could not stop Web service process $($server.ProcessId)."
  }
}

if (Test-Path $pidFile) { Remove-Item $pidFile -Force }
Write-Output 'Web service stopped.'
