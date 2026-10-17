﻿# BinaryToText 배포 빌드
#   dist\BinaryToText.exe                      GUI 단일 exe (개인 사용)
#   dist\btt.exe                               명령줄 단일 exe (Qt 없음)
#   dist\BinaryToText\                         GUI 폴더형 (Qt DLL 교체 가능, 라이선스 고지 포함, btt.exe 포함)
#   dist\BinaryToText-<버전>-win64.zip         폴더형을 압축한 배포본
# 사용: powershell -ExecutionPolicy Bypass -File build.ps1 [-SkipTests] [-SkipOneFile]
param([switch]$SkipTests, [switch]$SkipOneFile)
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$py = ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) {
    py -3.12 -m venv .venv
    & $py -m pip install --upgrade pip
}
& $py -m pip install -r requirements-dev.txt
if ($LASTEXITCODE -ne 0) { throw "pip install 실패" }

if (-not $SkipTests) {
    & $py -m pytest -q
    if ($LASTEXITCODE -ne 0) { throw "테스트 실패: 빌드를 중단합니다" }
}

New-Item -ItemType Directory -Force build | Out-Null
& $py launchers\make_icon.py build\app.ico
if ($LASTEXITCODE -ne 0) { throw "아이콘 생성 실패" }

$common = @("--noconfirm", "--clean", "--icon", "build\app.ico", "--paths", ".", "--exclude-module", "tkinter")

if (-not $SkipOneFile) {
    & $py -m PyInstaller @common --onefile --windowed --name BinaryToText `
        --workpath build\onefile launchers\gui_main.py
    if ($LASTEXITCODE -ne 0) { throw "GUI 단일 exe 빌드 실패" }
}

& $py -m PyInstaller @common --onefile --console --name btt `
    --exclude-module PySide6 --exclude-module shiboken6 `
    --workpath build\cli launchers\cli_main.py
if ($LASTEXITCODE -ne 0) { throw "CLI 빌드 실패" }

# Folder build: Qt stays as separate DLLs under _internal, so users can replace them (LGPL-3.0).
& $py -m PyInstaller @common --onedir --windowed --name BinaryToText `
    --workpath build\onedir launchers\gui_main.py
if ($LASTEXITCODE -ne 0) { throw "GUI 폴더형 빌드 실패" }

$dir = "dist\BinaryToText"
Copy-Item dist\btt.exe, LICENSE, README.md $dir
$lic = New-Item -ItemType Directory -Force "$dir\licenses"
Copy-Item licenses\* $lic
$base = & $py -c "import sys; print(sys.base_prefix)"
Copy-Item (Join-Path $base "LICENSE.txt") "$lic\Python-LICENSE.txt"
$ver = & $py -c "import binarytotext; print(binarytotext.__version__)"
$info = & $py -c "import sys, PySide6, PyInstaller; from PySide6 import QtCore; print(f'BinaryToText $ver'); print(f'Python {sys.version.split()[0]}'); print(f'PySide6 {PySide6.__version__} / Qt {QtCore.qVersion()}'); print(f'PyInstaller {PyInstaller.__version__}')"
($info + "빌드 시각 $(Get-Date -Format 'yyyy-MM-dd HH:mm')") | Set-Content -Encoding utf8 "$dir\BUILD_INFO.txt"

$zip = "dist\BinaryToText-$ver-win64.zip"
if (Test-Path $zip) { Remove-Item $zip }
# Python zipfile instead of Compress-Archive: the latter fails while antivirus still scans new files.
& $py -c "import shutil; shutil.make_archive('dist/BinaryToText-$ver-win64', 'zip', 'dist', 'BinaryToText')"
if ($LASTEXITCODE -ne 0) { throw "zip 생성 실패" }

Get-Item dist\*.exe, $zip | Format-Table Name, @{n="MB"; e={[math]::Round($_.Length / 1MB, 1)}}
