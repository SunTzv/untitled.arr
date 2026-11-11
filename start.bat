@echo off
setlocal enabledelayedexpansion
title untitled.arr — Spotify Auto-Recorder

echo =======================================================
echo untitled.arr — Automated Spotify Audio Recorder
echo =======================================================
echo.

:: 1. Check for Python
set "PYTHON_EXE="
where python >nul 2>&1 && set "PYTHON_EXE=python"
if not defined PYTHON_EXE (
    where py >nul 2>&1 && set "PYTHON_EXE=py"
)
if not defined PYTHON_EXE (
    for /d %%D in ("%LOCALAPPDATA%\Programs\Python\Python3*") do (
        if exist "%%D\python.exe" set "PYTHON_EXE=%%D\python.exe"
    )
)

if not defined PYTHON_EXE (
    echo [!] Python is not installed on your system.
    echo.
    echo Windows 10 and 11 have 'winget' built-in. Would you like to automatically
    echo install Python 3.12 now?
    set /p "INSTALL_PY=Install Python via winget? [Y/n]: "
    if /i "!INSTALL_PY!"=="n" (
        echo Please install Python 3.8+ from https://www.python.org/downloads/
        pause
        exit /b 1
    )
    echo [*] Installing Python via winget...
    winget install -e --id Python.Python.3.12 --scope user
    echo [*] Please restart this script once the installation finishes.
    pause
    exit /b 0
)

:: 2. Check for FFmpeg
set "HAS_FFMPEG="
where ffmpeg >nul 2>&1 && set "HAS_FFMPEG=1"
if exist "ffmpeg.exe" set "HAS_FFMPEG=1"
if exist "bin\ffmpeg.exe" set "HAS_FFMPEG=1"

if not defined HAS_FFMPEG (
    echo [!] FFmpeg is required for audio capture and conversion.
    echo.
    echo Would you like to automatically download/install FFmpeg now?
    echo [1] Install via winget (system-wide)
    echo [2] Download portable ffmpeg into bin/ folder (no admin required)
    echo [3] Skip
    set /p "FF_CHOICE=Select an option [1/2/3]: "
    if "!FF_CHOICE!"=="1" (
        echo [*] Installing FFmpeg via winget...
        winget install -e --id Gyan.FFmpeg
        echo [*] Installation finished.
    ) else if "!FF_CHOICE!"=="2" (
        echo [*] Downloading portable FFmpeg via PowerShell...
        if not exist "bin" mkdir "bin"
        powershell -NoProfile -Command ^
            "$ProgressPreference = 'SilentlyContinue'; " ^
            "Write-Host 'Downloading ffmpeg essentials...'; " ^
            "Invoke-WebRequest -Uri 'https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip' -OutFile 'bin\\ffmpeg.zip'; " ^
            "Write-Host 'Extracting...'; " ^
            "Expand-Archive -Path 'bin\\ffmpeg.zip' -DestinationPath 'bin\\tmp' -Force; " ^
            "Move-Item 'bin\\tmp\\ffmpeg-*\\bin\\*.exe' 'bin\\' -Force; " ^
            "Remove-Item 'bin\\tmp' -Recurse -Force; " ^
            "Remove-Item 'bin\\ffmpeg.zip' -Force; " ^
            "Write-Host 'FFmpeg downloaded successfully to bin\\.'"
    )
)

:: 3. Check and install Python dependencies
echo [*] Checking Python dependencies...
!PYTHON_EXE! -c "import mutagen" >nul 2>&1
if errorlevel 1 (
    echo [*] Installing dependencies from requirements.txt...
    !PYTHON_EXE! -m pip install -r "%~dp0requirements.txt"
)

:: 4. Start the application
echo [*] Launching untitled.arr...
echo.
!PYTHON_EXE! "%~dp0start.py"

pause
