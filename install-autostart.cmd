@echo off
setlocal
rem Автозапуск OCRia Snap при входе в систему: ярлык с иконкой в папке «Автозагрузка».
rem Пути подставляются автоматически, поэтому перенос папки не сломает автозапуск.
set "HERE=%~dp0"
set "SCRIPT=%HERE%ocria_snap.pyw"
set "ICON=%HERE%assets\ocria-snap.ico"
set "STARTUP=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup"

set "PYW="
for /f "delims=" %%A in ('py -c "import os,sys;print(os.path.join(os.path.dirname(sys.executable),'pythonw.exe'))" 2^>nul') do set "PYW=%%A"
if not defined PYW set "PYW=C:\Users\Administrator\AppData\Local\Python\pythoncore-3.14-64\pythonw.exe"

if not exist "%PYW%" (
  echo Не найден pythonw.exe: "%PYW%"
  exit /b 1
)
if not exist "%SCRIPT%" (
  echo Не найден ocria_snap.pyw: "%SCRIPT%"
  exit /b 1
)

rem убираем старый способ (через .vbs), если он остался
if exist "%STARTUP%\ocria-snap.vbs" del "%STARTUP%\ocria-snap.vbs"

cscript //nologo "%HERE%tools-install-shortcut.vbs" "%PYW%" "%SCRIPT%" "%ICON%" startup
if errorlevel 1 (
  echo Не удалось создать ярлык.
  exit /b 1
)

echo.
echo Автозапуск включён:
echo   ярлык:   %STARTUP%\OCRia Snap.lnk
echo   pythonw: %PYW%
echo   скрипт:  %SCRIPT%
echo   иконка:  %ICON%
echo.
echo Проверяю запуск прямо сейчас...
start "" "%PYW%" "%SCRIPT%"
timeout /t 5 /nobreak >nul
tasklist /fi "imagename eq pythonw.exe" 2>nul | find /i "pythonw.exe" >nul && (
  echo   приложение поднялось, значок — в трее ^(стрелка ^ рядом с часами^).
) || (
  echo   ВНИМАНИЕ: pythonw.exe не найден. Проверьте пути выше.
)
