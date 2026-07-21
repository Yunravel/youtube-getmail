[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$projectDir = Split-Path -Parent $PSScriptRoot
$backupDir = Join-Path $projectDir "database\manual-backups"
$container = "kol-workstation-db"
$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$target = Join-Path $backupDir "kol_outreach-$stamp.dump"

New-Item -ItemType Directory -Force -Path $backupDir | Out-Null
docker exec $container pg_dump -U kol -d kol_outreach -Fc -f /tmp/kol_outreach.dump
if ($LASTEXITCODE -ne 0) { throw "Database dump failed." }
docker cp "${container}:/tmp/kol_outreach.dump" $target
if ($LASTEXITCODE -ne 0) { throw "Failed to copy the database dump." }
docker exec $container rm -f /tmp/kol_outreach.dump | Out-Null
Write-Host "Backup complete: $target" -ForegroundColor Green
