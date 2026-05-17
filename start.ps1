$ErrorActionPreference = 'Stop'

$root = Split-Path -Parent $MyInvocation.MyCommand.Path

function Stop-Port {
    param([int]$Port)

    $connections = Get-NetTCPConnection -LocalPort $Port -ErrorAction SilentlyContinue

    foreach ($conn in $connections) {
        try {
            Stop-Process -Id $conn.OwningProcess -Force -ErrorAction SilentlyContinue
            Write-Host "  Освобождён порт $Port (PID $($conn.OwningProcess))"
        }
        catch {
            Write-Host "  Не удалось освободить порт $Port (PID $($conn.OwningProcess))"
        }
    }
}

function Wait-ForHttp {
    param(
        [Parameter(Mandatory = $true)]
        [string] $Uri,
        [int] $TimeoutSeconds = 120
    )

    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)

    while ((Get-Date) -lt $deadline) {
        try {
            Invoke-WebRequest -Uri $Uri -TimeoutSec 3 | Out-Null
            return $true
        }
        catch {
            Start-Sleep -Seconds 2
        }
    }

    return $false
}

Write-Host 'Освобождаем порты...'
Stop-Port 5173
Stop-Port 5174
Stop-Port 8001

Write-Host 'Останавливаем docker compose (backend)...'
$backendDir = Join-Path $root 'backend'

if (Test-Path (Join-Path $backendDir 'docker-compose.dev.yml')) {
    Push-Location $backendDir

    try {
        $oldErrorActionPreference = $ErrorActionPreference
        $ErrorActionPreference = 'Continue'

        docker compose -f docker-compose.dev.yml down

        if ($LASTEXITCODE -ne 0) {
            Write-Host "  docker compose down завершился с кодом $LASTEXITCODE, продолжаем запуск..."
        }
    }
    catch {
        Write-Host "  Не удалось остановить docker compose, продолжаем запуск..."
        Write-Host "  $($_.Exception.Message)"
    }
    finally {
        $ErrorActionPreference = $oldErrorActionPreference
        Pop-Location
    }
}

Write-Host 'Запуск AI-сервиса и backend параллельно...'

Start-Process powershell `
    -WindowStyle Hidden `
    -WorkingDirectory $root `
    -ArgumentList '-NoExit', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', (Join-Path $root 'ai\start.ps1')

Start-Process powershell `
    -WindowStyle Hidden `
    -WorkingDirectory $backendDir `
    -ArgumentList '-NoExit', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', 'start.ps1'

Write-Host 'Проверка backend...'
if (-not (Wait-ForHttp -Uri 'http://localhost:8000/healthcheck' -TimeoutSeconds 120)) {
    Write-Host 'Backend не ответил на /healthcheck.' -ForegroundColor Red
    Write-Host 'Проверь логи:' -ForegroundColor Yellow
    Write-Host '  cd backend'
    Write-Host '  docker compose -f docker-compose.dev.yml logs app --tail=100'
}
else {
    Write-Host 'Backend OK' -ForegroundColor Green
}

Write-Host 'Проверка AI-сервиса...'
if (-not (Wait-ForHttp -Uri 'http://localhost:8001/healthcheck' -TimeoutSeconds 120)) {
    Write-Host 'AI-сервис не ответил на /healthcheck (Ollama может грузиться дольше).' -ForegroundColor Yellow
}
else {
    Write-Host 'AI-сервис OK' -ForegroundColor Green
}

$frontendDir = Join-Path $root 'frontend'

if (-not (Test-Path (Join-Path $frontendDir '.env.local'))) {
    Copy-Item (Join-Path $frontendDir '.env.example') (Join-Path $frontendDir '.env.local')
    Write-Host 'Created frontend .env.local'
}

Write-Host 'Запуск frontend...'
Start-Process powershell `
    -WindowStyle Hidden `
    -WorkingDirectory $frontendDir `
    -ArgumentList '-NoExit', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-Command', 'if (-not (Test-Path node_modules)) { npm install }; npm run dev'

Write-Host ''
Write-Host '✓ Команды запуска выполнены.'
Write-Host '  Frontend : http://localhost:5173'
Write-Host '  Backend  : http://localhost:8000'
Write-Host '  AI       : http://localhost:8001'