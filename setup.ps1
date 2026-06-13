# One-time setup for TBH Gold Meter on Windows. Run from PowerShell:
#   .\setup.ps1
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

# Tesseract: either on PATH or in the default install dir (the meter
# auto-detects the default dir, so no PATH edit is needed).
$tesseractOnPath = Get-Command tesseract -ErrorAction SilentlyContinue
$tesseractDefault = Join-Path $env:ProgramFiles "Tesseract-OCR\tesseract.exe"
if (-not $tesseractOnPath -and -not (Test-Path $tesseractDefault)) {
    Write-Host ">> Tesseract OCR not found. Install it with:"
    Write-Host "   winget install --id UB-Mannheim.TesseractOCR"
    Write-Host "   (or download the installer from https://github.com/UB-Mannheim/tesseract/wiki)"
    Write-Host "   Then re-run .\setup.ps1"
}

python -m venv .venv
& .\.venv\Scripts\python.exe -m pip install -q -r requirements.txt
Write-Host ">> Python deps installed."
Write-Host ">> Next: start the game, then:"
Write-Host "   .\.venv\Scripts\python.exe -m goldmeter calibrate"
Write-Host "   .\.venv\Scripts\python.exe -m goldmeter"
