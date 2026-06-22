@echo off
setlocal

echo ============================================
echo  Bang - building Bang.exe
echo ============================================
echo.

REM ---- find Python ----
where python >nul 2>nul
if errorlevel 1 (
    echo ERROR: Python was not found on PATH.
    echo Install it from python.org and check "Add Python to PATH" during
    echo setup, then run this script again.
    pause
    exit /b 1
)

echo Installing/updating build dependencies...
python -m pip install --upgrade pip >nul
python -m pip install -r requirements.txt
python -m pip install pyinstaller
if errorlevel 1 (
    echo ERROR: failed to install dependencies. See the messages above.
    pause
    exit /b 1
)

echo.
echo Cleaning previous build output...
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist

echo.
echo Building Bang.exe (this can take a minute or two)...
python -m PyInstaller Bang.spec
if errorlevel 1 (
    echo ERROR: PyInstaller build failed. See the messages above.
    pause
    exit /b 1
)

echo.
echo ============================================
echo  Done. Bang.exe is in the "dist" folder.
echo ============================================
echo.
echo You can copy dist\Bang.exe anywhere and run it directly --
echo no Python install needed on the machine that runs it.
echo.
echo If Bang.exe ever opens then immediately closes with no error message,
echo that's a known historical issue with pywebview + a hidden console
echo window. Run build_debug.bat instead to get a version that shows a
echo console with real error output, to see what's actually happening.
echo.
pause
