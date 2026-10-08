$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '../..')).Path
$run = Join-Path $root '.run'
New-Item -ItemType Directory -Path $run -Force | Out-Null
function Free-Port([int]$start) {
    for ($port = $start; $port -lt ($start + 30); $port++) {
        if (-not (Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue)) { return $port }
    }
    throw 'No preview port available'
}
$apiPort = Free-Port 8002
$webPort = Free-Port 3011
$env:RESEARCHFORGE_ENV = 'local'
$env:RESEARCHFORGE_AUTH_MODE = 'development'
$env:RESEARCHFORGE_STORE_BACKEND = 'json'
$env:RESEARCHFORGE_STORE_PATH = Join-Path $run 'preview-store.json'
$env:RESEARCHFORGE_ARTIFACT_STORE_PATH = Join-Path $run 'preview-artifacts'
$env:RESEARCHFORGE_PERSISTENCE = '1'
$env:RESEARCHFORGE_RATE_LIMIT_BACKEND = 'local'
$env:RESEARCHFORGE_API_KEY = ''
$api = Start-Process -FilePath (Join-Path $root 'backend/.venv312/Scripts/python.exe') -ArgumentList @('-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', $apiPort) -WorkingDirectory (Join-Path $root 'backend') -WindowStyle Hidden -RedirectStandardOutput (Join-Path $run 'preview-api.log') -RedirectStandardError (Join-Path $run 'preview-api-error.log') -PassThru
$env:RESEARCHFORGE_BACKEND_URL = "http://127.0.0.1:$apiPort"
$node = (Get-Command node).Source
$next = '"' + (Join-Path $root 'frontend/node_modules/next/dist/bin/next') + '"'
$web = Start-Process -FilePath $node -ArgumentList @($next, 'dev', '--hostname', '127.0.0.1', '--port', $webPort) -WorkingDirectory (Join-Path $root 'frontend') -WindowStyle Hidden -RedirectStandardOutput (Join-Path $run 'preview-web.log') -RedirectStandardError (Join-Path $run 'preview-web-error.log') -PassThru
Write-Output "API_PID=$($api.Id) WEB_PID=$($web.Id) API_URL=http://127.0.0.1:$apiPort WEB_URL=http://127.0.0.1:$webPort"
