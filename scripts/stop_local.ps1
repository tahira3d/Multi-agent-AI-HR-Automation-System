$ErrorActionPreference = 'Stop'
$projectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$statePath = Join-Path $projectRoot 'storage\local-services.json'
if (-not (Test-Path -LiteralPath $statePath)) { Write-Output 'No recorded local service processes.'; exit 0 }
$services = Get-Content -Raw -LiteralPath $statePath | ConvertFrom-Json
foreach ($service in $services) {
    $processId = [int]$service.process_id
    $processInfo = Get-CimInstance Win32_Process -Filter "ProcessId=$processId"
    if (-not $processInfo) { continue }
    if (-not $processInfo.CommandLine -or $processInfo.CommandLine.IndexOf($projectRoot, [StringComparison]::OrdinalIgnoreCase) -lt 0) {
        throw "Process $processId no longer belongs to this project; it was not stopped."
    }
    & taskkill.exe /PID $processId /T /F
    if ($LASTEXITCODE -ne 0) { throw "Process $processId could not be stopped." }
}
Set-Content -LiteralPath $statePath -Value '[]' -Encoding utf8
Write-Output 'Recorded hiring workspace services stopped.'
