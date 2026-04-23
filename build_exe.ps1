$ErrorActionPreference = "Stop"

Set-Location $PSScriptRoot

if (-not (Test-Path ".\.venv\Scripts\Activate.ps1")) {
  Write-Host "Venv .venv not found. Create it: python -m venv .venv"
  exit 1
}

.\.venv\Scripts\Activate.ps1

python -m pip install --upgrade pip | Out-Null
pip install -r requirements.txt
pip install pyinstaller

pyinstaller --noconfirm faceguard.spec

Write-Host ""
Write-Host "Done: dist\\FaceGuard.exe"

