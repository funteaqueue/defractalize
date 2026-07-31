$ErrorActionPreference = "Stop"

$baseUrl = if ($env:RESTORATION_URL) { $env:RESTORATION_URL } else { "http://localhost:8080" }
$health = Invoke-RestMethod -Uri "$baseUrl/api/health"

if ($health.backends.cleaner.status -ne "ok" -or $health.backends.seedvr2.status -ne "ok") {
    throw "One or more GPU services are unavailable: $($health | ConvertTo-Json -Depth 5)"
}

Write-Host "Gateway and both GPU backends are healthy."
