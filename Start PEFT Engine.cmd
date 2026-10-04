@echo off
title OmniGrapher PEFT Engine
cd /d "%~dp0"

powershell -ExecutionPolicy Bypass -NoProfile -File "scripts\start-peft-engine.ps1"

pause
