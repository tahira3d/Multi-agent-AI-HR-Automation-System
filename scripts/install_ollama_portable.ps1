param([string]$ProjectRoot = (Split-Path -Parent $PSScriptRoot), [switch]$PullModel)
$ErrorActionPreference = 'Stop'
$ProjectRoot = [IO.Path]::GetFullPath($ProjectRoot)
$toolRoot = [IO.Path]::GetFullPath((Join-Path $ProjectRoot 'storage\tools\ollama'))
$models = [IO.Path]::GetFullPath((Join-Path $ProjectRoot 'storage\models\ollama'))
if (-not $toolRoot.StartsWith($ProjectRoot + '\', [StringComparison]::OrdinalIgnoreCase) -or -not $models.StartsWith($ProjectRoot + '\', [StringComparison]::OrdinalIgnoreCase)) { throw 'Installation targets must remain inside the project.' }
[IO.Directory]::CreateDirectory($toolRoot) | Out-Null
[IO.Directory]::CreateDirectory($models) | Out-Null
$executable = Join-Path $toolRoot 'ollama.exe'
if (-not (Test-Path -LiteralPath $executable)) {
    $python = Join-Path $ProjectRoot '.venv-local\Scripts\python.exe'
    & $python (Join-Path $PSScriptRoot 'download_ollama_cpu.py') --destination $toolRoot
    if ($LASTEXITCODE -ne 0) { throw 'CPU-only Ollama installation failed. The official Ollama installer is an alternative.' }
}
$env:OLLAMA_HOST = '127.0.0.1:11434'
$env:OLLAMA_MODELS = $models
$env:OLLAMA_NUM_PARALLEL = '1'
$env:OLLAMA_MAX_LOADED_MODELS = '1'
$env:OLLAMA_KEEP_ALIVE = '0'
$env:OLLAMA_NO_CLOUD = '1'
$ready = $false
try { Invoke-RestMethod 'http://127.0.0.1:11434/api/tags' -TimeoutSec 2 | Out-Null; $ready = $true } catch {}
if (-not $ready) {
    $server = Start-Process -FilePath $executable -ArgumentList 'serve' -WorkingDirectory $ProjectRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $toolRoot 'server.stdout.log') -RedirectStandardError (Join-Path $toolRoot 'server.stderr.log') -PassThru
    $server.Id | Set-Content -LiteralPath (Join-Path $toolRoot 'server.pid')
    for ($attempt=0; $attempt -lt 30; $attempt++) {
        try { Invoke-RestMethod 'http://127.0.0.1:11434/api/tags' -TimeoutSec 1 | Out-Null; $ready = $true; break } catch {}
        Start-Sleep -Milliseconds 500
    }
}
if (-not $ready) { throw 'Ollama did not start; inspect the project-local server error log.' }
if ($PullModel) {
    & $executable pull 'qwen2.5:1.5b'
    if ($LASTEXITCODE -ne 0) { throw 'The local Qwen model download failed.' }
}
Write-Output 'Local Ollama service: http://127.0.0.1:11434 (no account or cloud provider required).'
