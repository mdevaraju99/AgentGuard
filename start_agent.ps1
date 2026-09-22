# Start the AI Desktop Agent in the browser.
# Usage (from this folder):
#   powershell -ExecutionPolicy Bypass -File .\start_agent.ps1

$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot

$python = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
$port = 8501
$url = "http://localhost:$port"

function Test-PortOpen {
    try {
        $conn = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
        return [bool]$conn
    } catch {
        return $false
    }
}

if (-not (Test-Path $python)) {
    Write-Host "Creating virtual environment..."
    py -3 -m venv .venv
}

if (-not (Test-Path $python)) {
    throw "Could not create .venv. Install Python 3, then run this script again."
}

$streamlitCheck = & $python -c "import streamlit, dateparser" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "Installing requirements into .venv..."
    & $python -m pip install -r (Join-Path $PSScriptRoot "requirements.txt")
}

if (Test-PortOpen) {
    Write-Host "Agent already running at $url"
    Start-Process $url
    exit 0
}

Write-Host "Starting Streamlit at $url"
& $python -m streamlit run (Join-Path $PSScriptRoot "app.py") --server.port $port
