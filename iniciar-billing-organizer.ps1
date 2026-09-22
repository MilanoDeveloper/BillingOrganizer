$ErrorActionPreference = 'Stop'

$root = $PSScriptRoot
$backend = Join-Path $root 'backend'
$frontend = Join-Path $root 'frontend\frontBillingOrganizer'
$python = Join-Path $backend 'venv\Scripts\python.exe'
$npm = 'C:\Program Files\nodejs\npm.cmd'

if (-not (Test-Path $python)) {
    Write-Host 'Backend nao configurado: backend\venv\Scripts\python.exe nao encontrado.' -ForegroundColor Red
    Write-Host 'Execute a instalacao das dependencias primeiro.'
    Read-Host 'Pressione Enter para sair'
    exit 1
}

if (-not (Test-Path $npm)) {
    $npm = (Get-Command npm.cmd -ErrorAction SilentlyContinue).Source
}

if (-not $npm -or -not (Test-Path $npm)) {
    Write-Host 'Node.js/npm nao encontrado.' -ForegroundColor Red
    Read-Host 'Pressione Enter para sair'
    exit 1
}

$nodePath = Split-Path $npm -Parent
$backendCommand = "Set-Location '$backend'; & '$python' -m uvicorn main:app --reload --port 8000"
$frontendCommand = "`$env:Path = '$nodePath;C:\Users\$env:USERNAME\AppData\Roaming\npm;' + `$env:Path; Set-Location '$frontend'; & '$npm' start"

Start-Process powershell.exe -ArgumentList '-NoExit', '-ExecutionPolicy', 'Bypass', '-Command', $backendCommand -WindowStyle Normal
Start-Process powershell.exe -ArgumentList '-NoExit', '-ExecutionPolicy', 'Bypass', '-Command', $frontendCommand -WindowStyle Normal
Start-Process 'http://localhost:4200'

Write-Host 'Billing Organizer iniciado.' -ForegroundColor Green
Write-Host 'Frontend: http://localhost:4200'
Write-Host 'API:      http://localhost:8000/docs'
