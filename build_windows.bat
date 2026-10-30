@echo off
REM PDF Page Editor を単一 exe にビルドし、Inno Setup があればインストーラーも作る（Windows 用）
REM 事前に Python 3.10 以上をインストールしておくこと。Inno Setup 6 は任意: https://jrsoftware.org/isdl.php
cd /d %~dp0
python -m venv .venv
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip
pip install -r requirements.txt
REM PyMuPDF 2.x のレイアウト解析モデル(onnx)は本アプリでは不要なので除外して軽量化する
pyinstaller --noconfirm --clean --onefile --windowed --name PdfPageEditor --icon assets\icon.ico --add-data "assets\icon.ico;assets" --exclude-module pymupdf.layout --exclude-module onnxruntime --exclude-module pymupdf_layout app.py
if errorlevel 1 goto :fail
echo.
echo 完了: dist\PdfPageEditor.exe

set ISCC="%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if not exist %ISCC% set ISCC="%ProgramFiles%\Inno Setup 6\ISCC.exe"
if not exist %ISCC% (
  echo Inno Setup 6 が見つからないため、インストーラーは作成しません（exe 単体は使えます）。
  goto :end
)
for /f "tokens=2 delims== " %%v in ('findstr /c:"__version__ = " app.py') do set VER=%%~v
%ISCC% /DAppVersion=%VER% installer\PdfPageEditor.iss
if errorlevel 1 goto :fail
echo.
echo 完了: dist\PdfPageEditor-Setup-%VER%.exe
goto :end

:fail
echo ビルドに失敗しました。
:end
pause
