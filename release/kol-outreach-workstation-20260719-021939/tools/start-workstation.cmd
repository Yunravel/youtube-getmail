@echo off
cd /d "%~dp0.."
docker compose -f docker-compose.workstation.yml --env-file .env.prod up -d
if errorlevel 1 pause
start http://localhost:8080
