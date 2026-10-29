@echo off
REM PDF Page Editor を単一 exe にビルドする（Windows 用）
REM 事前に Python 3.10 以上をインストールしておくこと
cd /d %~dp0
python -m venv .venv
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip
pip install -r requirements.txt
pyinstaller --noconfirm --clean --onefile --windowed --name PdfPageEditor app.py
echo.
echo 完了: dist\PdfPageEditor.exe
pause
