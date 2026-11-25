# SmartGate Launcher for Sri Ramakrishna College of Arts & Science for Women
Write-Host "======================================================================" -ForegroundColor Cyan
Write-Host "SMARTGATE - COLLEGE GATE PASS MANAGEMENT SYSTEM" -ForegroundColor White
Write-Host "Sri Ramakrishna College of Arts & Science for Women (SNR Sons Trust)" -ForegroundColor Yellow
Write-Host "======================================================================" -ForegroundColor Cyan

$CurrentDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
Set-Location $CurrentDir

$Python = "python"
if (Test-Path "$CurrentDir\venv\Scripts\python.exe") {
    $Python = "$CurrentDir\venv\Scripts\python.exe"
} elseif (Test-Path "$CurrentDir\.venv\Scripts\python.exe") {
    $Python = "$CurrentDir\.venv\Scripts\python.exe"
}

Write-Host "`n[1/2] Verifying SQLite database..." -ForegroundColor Green
& $Python database.py

Write-Host "`n[2/2] Launching SmartGate on http://127.0.0.1:5000/ ..." -ForegroundColor Green
Write-Host "Press Ctrl+C to terminate the application.`n" -ForegroundColor Gray

& $Python app.py
