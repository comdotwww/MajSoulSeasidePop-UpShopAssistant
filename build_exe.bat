@echo off
REM Build ShopAssistant single-file exe (output: dist\ShopAssistant.exe)
REM Requires: Python 3.10+ with tkinter

python -m pip install -r requirements.txt pyinstaller || goto :err
python -m PyInstaller shop_assistant.spec --noconfirm || goto :err

echo.
echo Build OK: dist\ShopAssistant.exe
goto :eof

:err
echo Build FAILED.
exit /b 1
