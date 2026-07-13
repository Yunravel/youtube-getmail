$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if (-not (Test-Path ".venv\Scripts\python.exe")) {
    try {
        python -m venv .venv
    }
    catch {
        py -3 -m venv .venv
    }
}

& ".venv\Scripts\python.exe" -m pip install -r requirements.txt pyinstaller
if ($LASTEXITCODE -ne 0) { throw "Dependency installation failed." }
& ".venv\Scripts\python.exe" -m PyInstaller `
    --noconfirm `
    --clean `
    --onefile `
    --windowed `
    --collect-all playwright `
    --name "YouTubeCollector" `
    main.py
if ($LASTEXITCODE -ne 0) { throw "PyInstaller build failed. Close a running YouTubeCollector.exe and retry." }

Copy-Item -Force README.md dist\README.md
Copy-Item -Force config.example.json dist\config.example.json
Write-Host "Build complete: dist\YouTubeCollector.exe"
