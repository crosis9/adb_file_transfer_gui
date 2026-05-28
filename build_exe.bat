@echo off
cd /d %~dp0

if not exist .venv (
    py -m venv .venv
)

call .venv\Scripts\activate

python -m pip install --upgrade pip
pip install -r requirements.txt

pyinstaller ^
  --noconsole ^
  --onefile ^
  --name AdbFileTransfer ^
  --add-binary "platform-tools\adb.exe;platform-tools" ^
  --add-binary "platform-tools\AdbWinApi.dll;platform-tools" ^
  --add-binary "platform-tools\AdbWinUsbApi.dll;platform-tools" ^
  adb_gui.py

echo.
echo build complete.
echo output: dist\AdbFileTransfer.exe
pause
