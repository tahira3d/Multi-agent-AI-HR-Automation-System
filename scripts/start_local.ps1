param()
$ErrorActionPreference = 'Stop'
$projectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$interpreter = Join-Path $projectRoot '.venv-local\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $interpreter)) { throw 'Create .venv-local and install the project requirements first.' }
$runtimeDirectory = Join-Path $projectRoot 'storage'
[IO.Directory]::CreateDirectory($runtimeDirectory) | Out-Null
Push-Location -LiteralPath $projectRoot
try {
    & $interpreter -m alembic -c backend\alembic.ini upgrade head
    if ($LASTEXITCODE -ne 0) { throw 'Database migration failed. Check DATABASE_URL and PostgreSQL availability.' }
    # After explicit initial installation, start the project-local model service offline.
    if (Test-Path -LiteralPath (Join-Path $projectRoot 'storage\tools\ollama\ollama.exe')) {
        & (Join-Path $PSScriptRoot 'install_ollama_portable.ps1') -ProjectRoot $projectRoot
    }
    $backendReady = $false
    try { $health = Invoke-RestMethod 'http://127.0.0.1:8000/health' -TimeoutSec 2; $backendReady = $health.version -eq '0.2.0' } catch {}
    $services = @()
    $statePath = Join-Path $runtimeDirectory 'local-services.json'
    if (Test-Path -LiteralPath $statePath) {
        $savedServices = Get-Content -Raw -LiteralPath $statePath | ConvertFrom-Json
        $services += $savedServices
    }
    if (-not $backendReady) {
        $backend = Start-Process -FilePath $interpreter -ArgumentList @('-m','uvicorn','app.main:app','--app-dir','backend','--host','127.0.0.1','--port','8000') -WorkingDirectory $projectRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $runtimeDirectory 'backend.stdout.log') -RedirectStandardError (Join-Path $runtimeDirectory 'backend.stderr.log') -PassThru
        $services += @{ name='backend'; process_id=$backend.Id }
    }
    $frontendReady = $false
    try { $response = Invoke-WebRequest 'http://127.0.0.1:8501/_stcore/health' -UseBasicParsing -TimeoutSec 2; $frontendReady = $response.StatusCode -eq 200 } catch {}
    if (-not $frontendReady) {
        $frontend = Start-Process -FilePath $interpreter -ArgumentList @('-m','streamlit','run','frontend/app.py','--server.address','127.0.0.1','--server.port','8501','--server.headless','true','--browser.gatherUsageStats','false') -WorkingDirectory $projectRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $runtimeDirectory 'frontend.stdout.log') -RedirectStandardError (Join-Path $runtimeDirectory 'frontend.stderr.log') -PassThru
        $services += @{ name='frontend'; process_id=$frontend.Id }
    }
    $services | ConvertTo-Json | Set-Content -LiteralPath $statePath -Encoding utf8
    for ($attempt=0; $attempt -lt 40; $attempt++) {
        try {
            $health = Invoke-RestMethod 'http://127.0.0.1:8000/health' -TimeoutSec 1
            $ui = Invoke-WebRequest 'http://127.0.0.1:8501/_stcore/health' -UseBasicParsing -TimeoutSec 1
            if ($health.version -eq '0.2.0' -and $ui.StatusCode -eq 200) {
                Write-Output 'Dashboard: http://127.0.0.1:8501'
                Write-Output 'API docs: http://127.0.0.1:8000/docs'
                exit 0
            }
        } catch {}
        Start-Sleep -Milliseconds 500
    }
    throw 'Services did not become ready. Inspect the backend/frontend stderr logs under storage.'
} finally {
    Pop-Location
}
