# BinaryToText 배포 빌드: dist\BinaryToText.exe (GUI) + dist\btt.exe (명령줄)
# 사용: powershell -ExecutionPolicy Bypass -File build.ps1
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$py = ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) {
    py -3.12 -m venv .venv
    & $py -m pip install --upgrade pip
}
& $py -m pip install -r requirements-dev.txt
if ($LASTEXITCODE -ne 0) { throw "pip install 실패" }

& $py -m pytest -q
if ($LASTEXITCODE -ne 0) { throw "테스트 실패: 빌드를 중단합니다" }

New-Item -ItemType Directory -Force build | Out-Null
& $py launchers\make_icon.py build\app.ico
if ($LASTEXITCODE -ne 0) { throw "아이콘 생성 실패" }

& $py -m PyInstaller --noconfirm --clean --onefile --windowed `
    --name BinaryToText --icon build\app.ico --paths . `
    --exclude-module tkinter `
    launchers\gui_main.py
if ($LASTEXITCODE -ne 0) { throw "GUI 빌드 실패" }

& $py -m PyInstaller --noconfirm --onefile --console `
    --name btt --icon build\app.ico --paths . `
    --exclude-module PySide6 --exclude-module shiboken6 --exclude-module tkinter `
    launchers\cli_main.py
if ($LASTEXITCODE -ne 0) { throw "CLI 빌드 실패" }

Get-Item dist\BinaryToText.exe, dist\btt.exe | Format-Table Name, @{n="MB"; e={[math]::Round($_.Length / 1MB, 1)}}
