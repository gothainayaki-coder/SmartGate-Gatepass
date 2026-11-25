@echo off
title SmartGate - Sri Ramakrishna College of Arts & Science for Women
echo ======================================================================
echo SMARTGATE - COLLEGE GATE PASS MANAGEMENT SYSTEM
echo Sri Ramakrishna College of Arts & Science for Women (SNR Sons Trust)
echo ======================================================================
echo.

cd /d "%~dp0"

if exist venv\Scripts\python.exe (
    set PYTHON=venv\Scripts\python.exe
) else if exist .venv\Scripts\python.exe (
    set PYTHON=.venv\Scripts\python.exe
) else (
    set PYTHON=python
)

echo [1/3] Checking Database...
%PYTHON% database.py

echo.
echo [2/3] Starting SmartGate Application Server...
echo Portal URL: http://127.0.0.1:5000/
echo Press Ctrl+C to stop the server.
echo ======================================================================
echo.
%PYTHON% app.py

pause
