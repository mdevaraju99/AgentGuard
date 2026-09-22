# Hands-free click-to-talk bubble (WhisperFlow-style).
# Usage:
#   powershell -ExecutionPolicy Bypass -File .\start_voice.ps1

$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot

$python = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"

if (-not (Test-Path $python)) {
    Write-Host "Creating virtual environment..."
    py -3 -m venv .venv
}

if (-not (Test-Path $python)) {
    throw "Could not create .venv. Install Python 3, then run this script again."
}

& $python -c "import streamlit, dateparser, speech_recognition, pyttsx3, sounddevice" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "Installing requirements into .venv..."
    & $python -m pip install -r (Join-Path $PSScriptRoot "requirements.txt")
}

Write-Host "Starting voice bubble. Tap the floating button to talk."
& $python -m desktop_agent.voice
