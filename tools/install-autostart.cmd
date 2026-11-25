@echo off
setlocal
rem Автозапуск OCRia Snap при входе в систему: ярлык с иконкой в папке «Автозагрузка».
rem Скрипт лежит в tools\, поэтому корень проекта — на уровень выше.
set "HERE=%~dp0"
set "ROOT=%HERE%..\"
set "SCRIPT=%ROOT%ocria_snap.pyw"
set "ICON=%ROOT%assets\ocria-snap.ico"
set "STARTUP=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup"

set "PYW="
for /f "delims=" %%A in ('py -c "import os,sys;print(os.path.join(os.path.dirname(sys.executable),'pythonw.exe'))" 2^>nul') do set "PYW=%%A"
if not defined PYW (
  echo Не найден pythonw.exe. Установите Python и повторите.
  pause
  exit /b 1
)

if not exist "%SCRIPT%" (
  echo Не найден ocria_snap.pyw: "%SCRIPT%"
  pause
  exit /b 1
)

rem убираем старый способ (через .vbs), если он остался
if exist "%STARTUP%\ocria-snap.vbs" del "%STARTUP%\ocria-snap.vbs"

cscript //nologo "%HERE%install-shortcut.vbs" "%PYW%" "%SCRIPT%" "%ICON%" startup
if errorlevel 1 (
  echo Не удалось создать ярлык.
  pause
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
pause
