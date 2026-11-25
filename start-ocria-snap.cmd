@echo off
rem Запустить OCRia Snap без окна консоли.
set "PYW="
for /f "delims=" %%A in ('py -c "import os,sys;print(os.path.join(os.path.dirname(sys.executable),'pythonw.exe'))" 2^>nul') do set "PYW=%%A"
if not defined PYW (
  echo Не найден pythonw.exe. Установите Python 3.11+ и повторите.
  pause
  exit /b 1
)
start "" "%PYW%" "%~dp0ocria_snap.pyw"
