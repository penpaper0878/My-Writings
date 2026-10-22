@echo off
REM Build DroneInspectPro.exe locally on Windows (needs Python 3.10+ on PATH).
cd /d "%~dp0"
python -m pip install --upgrade pip || goto :err
python -m pip install -r requirements.txt pyinstaller || goto :err
python -m PyInstaller --noconfirm --clean --onefile --windowed ^
  --name DroneInspectPro --collect-data docx drone_inspector.py || goto :err
echo.
echo Built: %~dp0dist\DroneInspectPro.exe
pause
exit /b 0
:err
echo Build failed.
pause
exit /b 1
