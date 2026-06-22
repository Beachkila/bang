@echo off
setlocal

echo ============================================
echo  Bang - DEBUG build (console window visible)
echo ============================================
echo.
echo This builds Bang.exe WITH a visible console window, so any error
echo (including the known pywebview "silent crash on launch" issue) will
echo print instead of vanishing. Use this only to diagnose a problem --
echo build.bat is the normal one-click build for everyday use.
echo.

where python >nul 2>nul
if errorlevel 1 (
    echo ERROR: Python was not found on PATH.
    pause
    exit /b 1
)

python -m pip install -r requirements.txt >nul
python -m pip install pyinstaller >nul

if exist build rmdir /s /q build
if exist dist rmdir /s /q dist

echo Building with console=True...
python -m PyInstaller --name Bang --add-data "bang3d.html;." --console app.py
if errorlevel 1 (
    echo ERROR: build failed. See the messages above.
    pause
    exit /b 1
)

echo.
echo Done. Run dist\Bang\Bang.exe from a Command Prompt (not by double
echo click) so the window stays open after it prints whatever error caused
echo the crash.
echo.
pause
