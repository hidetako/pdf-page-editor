@echo off
REM PDF Page Editor を単一 exe にビルドする（Windows 用）
REM 事前に Python 3.10 以上をインストールしておくこと
cd /d %~dp0
python -m venv .venv
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip
pip install -r requirements.txt
REM PyMuPDF 2.x のレイアウト解析モデル(onnx)は本アプリでは不要なので除外して軽量化する
pyinstaller --noconfirm --clean --onefile --windowed --name PdfPageEditor --icon assets\icon.ico --add-data "assets\icon.ico;assets" --exclude-module pymupdf.layout --exclude-module onnxruntime --exclude-module pymupdf_layout app.py
echo.
echo 完了: dist\PdfPageEditor.exe
pause
