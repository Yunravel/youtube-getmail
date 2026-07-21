[CmdletBinding()]
param(
    [switch]$ForceRestore,
    [switch]$SkipRestore
)

$ErrorActionPreference = "Stop"
$projectDir = Split-Path -Parent $PSScriptRoot
$composeFile = Join-Path $projectDir "docker-compose.workstation.yml"
$envFile = Join-Path $projectDir ".env.prod"
$dumpFile = Join-Path $projectDir "database\kol_outreach.dump"
$dbContainer = "kol-workstation-db"

function Invoke-Checked {
    param([scriptblock]$Command, [string]$FailureMessage)
    & $Command
    if ($LASTEXITCODE -ne 0) {
        throw $FailureMessage
    }
}

Write-Host "[1/6] Checking Docker Desktop..."
Invoke-Checked { docker info *> $null } "Docker Desktop is not running. Start Docker Desktop first."

if (-not (Test-Path -LiteralPath $envFile)) {
    throw "Missing environment file: $envFile"
}
if (-not $SkipRestore -and -not (Test-Path -LiteralPath $dumpFile)) {
    throw "Missing database dump: $dumpFile"
}

Push-Location $projectDir
try {
    Write-Host "[2/6] Starting PostgreSQL 16..."
    Invoke-Checked {
        docker compose -f $composeFile --env-file $envFile up -d db
    } "Failed to start the PostgreSQL container."

    Write-Host "[3/6] Waiting for PostgreSQL health check..."
    $healthy = $false
    for ($i = 0; $i -lt 60; $i++) {
        $status = docker inspect --format "{{.State.Health.Status}}" $dbContainer 2>$null
        if ($status -eq "healthy") {
            $healthy = $true
            break
        }
        Start-Sleep -Seconds 2
    }
    if (-not $healthy) {
        throw "PostgreSQL did not become healthy within 120 seconds."
    }

    if (-not $SkipRestore) {
        $tableQuery = "SELECT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema='public' AND table_name='kol');"
        $hasTables = (docker exec $dbContainer psql -U kol -d kol_outreach -tAc $tableQuery).Trim()
        if ($hasTables -eq "t" -and -not $ForceRestore) {
            throw "The target database already contains business tables. Re-run with -ForceRestore to overwrite it."
        }

        Write-Host "[4/6] Restoring the complete database..."
        Invoke-Checked {
            docker cp $dumpFile "${dbContainer}:/tmp/kol_outreach.dump"
        } "Failed to copy the database dump into the container."
        Invoke-Checked {
            docker exec $dbContainer pg_restore -U kol -d kol_outreach --clean --if-exists --no-owner --no-privileges /tmp/kol_outreach.dump
        } "Database restore failed."
        docker exec $dbContainer rm -f /tmp/kol_outreach.dump | Out-Null
    } else {
        Write-Host "[4/6] Database restore skipped by parameter."
    }

    Write-Host "[5/6] Building and starting frontend and backend..."
    Invoke-Checked {
        docker compose -f $composeFile --env-file $envFile up -d --build
    } "Application image build or startup failed."

    Write-Host "[6/6] Checking application health..."
    $ready = $false
    for ($i = 0; $i -lt 60; $i++) {
        try {
            $response = Invoke-WebRequest -UseBasicParsing -Uri "http://localhost:8080/health" -TimeoutSec 3
            if ($response.StatusCode -eq 200) {
                $ready = $true
                break
            }
        } catch {
            Start-Sleep -Seconds 2
        }
    }
    if (-not $ready) {
        throw "The application was not ready within 120 seconds. Check docker compose logs."
    }

    $kolCount = (docker exec $dbContainer psql -U kol -d kol_outreach -tAc "SELECT COUNT(*) FROM kol;").Trim()
    Write-Host ""
    Write-Host "Deployment complete. KOL rows: $kolCount" -ForegroundColor Green
    Write-Host "Dashboard: http://localhost:8080"
    Write-Host "API docs: http://localhost:8000/docs"
} finally {
    Pop-Location
}
