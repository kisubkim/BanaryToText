# BinaryToText 배포 빌드
#   dist\BinaryToText.exe                      GUI 단일 exe (개인 사용)
#   dist\btt.exe                               명령줄 단일 exe (Qt 없음)
#   dist\BinaryToText\                         GUI 폴더형 (Qt DLL 교체 가능, 라이선스 고지 포함, btt.exe 포함)
#   dist\BinaryToText-<버전>-win64.zip         폴더형을 압축한 배포본
# 사용: powershell -ExecutionPolicy Bypass -File build.ps1 [-SkipTests] [-SkipOneFile]
# 코드 서명(선택): 환경 변수 BTT_SIGN_THUMBPRINT (Windows 인증서 저장소의 인증서 지문) 또는
#   BTT_SIGN_PFX (+ BTT_SIGN_PASSWORD) 를 설정하면 exe 를 서명합니다. 타임스탬프 서버는 BTT_SIGN_TIMESTAMP.
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

# Version info (exe Properties > Details): publisher, product, version. Unknown files with no
# metadata look more suspicious to antivirus heuristics.
& $py launchers\make_version.py build\version_gui.txt BinaryToText.exe "BinaryToText - 바이너리 파일을 텍스트로 변환"
& $py launchers\make_version.py build\version_cli.txt btt.exe "BinaryToText 명령줄 (btt)"
if ($LASTEXITCODE -ne 0) { throw "버전 정보 생성 실패" }

# --noupx: UPX-packed executables are a classic antivirus false-positive trigger.
$common = @("--noconfirm", "--clean", "--noupx", "--icon", "build\app.ico", "--paths", ".", "--exclude-module", "tkinter")

# Optional Authenticode signing. Without a certificate the build still works, unsigned.
$signtool = Get-ChildItem "${env:ProgramFiles(x86)}\Windows Kits\10\bin\*\x64\signtool.exe" -ErrorAction SilentlyContinue |
    Sort-Object FullName -Descending | Select-Object -First 1
$signing = [bool]($env:BTT_SIGN_THUMBPRINT -or $env:BTT_SIGN_PFX)
function Sign-Exe([string]$file) {
    if (-not $signing) { return }
    if (-not $signtool) { throw "signtool.exe 가 없습니다 (Windows SDK 설치 필요)" }
    $ts = if ($env:BTT_SIGN_TIMESTAMP) { $env:BTT_SIGN_TIMESTAMP } else { "http://timestamp.digicert.com" }
    $sargs = @("sign", "/fd", "sha256", "/tr", $ts, "/td", "sha256", "/d", "BinaryToText",
               "/du", "https://github.com/kisubkim/BanaryToText")
    if ($env:BTT_SIGN_THUMBPRINT) { $sargs += @("/sha1", $env:BTT_SIGN_THUMBPRINT) }
    else {
        $sargs += @("/f", $env:BTT_SIGN_PFX)
        if ($env:BTT_SIGN_PASSWORD) { $sargs += @("/p", $env:BTT_SIGN_PASSWORD) }
    }
    & $signtool.FullName @sargs $file
    if ($LASTEXITCODE -ne 0) { throw "코드 서명 실패: $file" }
}

if (-not $SkipOneFile) {
    & $py -m PyInstaller @common --onefile --windowed --name BinaryToText `
        --version-file build\version_gui.txt --workpath build\onefile launchers\gui_main.py
    if ($LASTEXITCODE -ne 0) { throw "GUI 단일 exe 빌드 실패" }
    Sign-Exe dist\BinaryToText.exe
}

& $py -m PyInstaller @common --onefile --console --name btt `
    --exclude-module PySide6 --exclude-module shiboken6 `
    --version-file build\version_cli.txt --workpath build\cli launchers\cli_main.py
if ($LASTEXITCODE -ne 0) { throw "CLI 빌드 실패" }
Sign-Exe dist\btt.exe

# Folder build: Qt stays as separate DLLs under _internal, so users can replace them (LGPL-3.0).
& $py -m PyInstaller @common --onedir --windowed --name BinaryToText `
    --version-file build\version_gui.txt --workpath build\onedir launchers\gui_main.py
if ($LASTEXITCODE -ne 0) { throw "GUI 폴더형 빌드 실패" }
Sign-Exe dist\BinaryToText\BinaryToText.exe

$dir = "dist\BinaryToText"
Copy-Item dist\btt.exe, LICENSE, README.md $dir
$lic = New-Item -ItemType Directory -Force "$dir\licenses"
Copy-Item licenses\* $lic
$base = & $py -c "import sys; print(sys.base_prefix)"
Copy-Item (Join-Path $base "LICENSE.txt") "$lic\Python-LICENSE.txt"
$ver = & $py -c "import binarytotext; print(binarytotext.__version__)"
$info = & $py -c "import sys, PySide6, PyInstaller; from PySide6 import QtCore; print(f'BinaryToText $ver'); print(f'Python {sys.version.split()[0]}'); print(f'PySide6 {PySide6.__version__} / Qt {QtCore.qVersion()}'); print(f'PyInstaller {PyInstaller.__version__}')"
$signer = (Get-AuthenticodeSignature "$dir\BinaryToText.exe").SignerCertificate
$signed = if ($signer) { "코드 서명: $($signer.Subject)" } else { "코드 서명: 없음" }
($info + $signed + "빌드 시각 $(Get-Date -Format 'yyyy-MM-dd HH:mm')") | Set-Content -Encoding utf8 "$dir\BUILD_INFO.txt"

$zip = "dist\BinaryToText-$ver-win64.zip"
if (Test-Path $zip) { Remove-Item $zip }
# Python zipfile instead of Compress-Archive: the latter fails while antivirus still scans new files.
& $py -c "import shutil; shutil.make_archive('dist/BinaryToText-$ver-win64', 'zip', 'dist', 'BinaryToText')"
if ($LASTEXITCODE -ne 0) { throw "zip 생성 실패" }

Get-Item dist\*.exe, $zip | Format-Table Name, @{n="MB"; e={[math]::Round($_.Length / 1MB, 1)}}
Get-Item dist\*.exe, "$dir\BinaryToText.exe" | ForEach-Object { Get-AuthenticodeSignature $_.FullName } |
    Format-Table @{n="파일"; e={$_.Path.Replace("$PWD\", "")}}, Status,
        @{n="서명"; e={if ($_.SignerCertificate) { $_.SignerCertificate.Subject } else { "없음" }}}
