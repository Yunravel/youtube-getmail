@echo off
cd /d "%~dp0.."
docker compose -f docker-compose.workstation.yml --env-file .env.prod stop
if errorlevel 1 pause
