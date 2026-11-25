@echo off
setlocal
rem Возвращаем системную Win+Shift+S.
set "KEY=HKCU\Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced"
set "BAK=%~dp0winshifts-backup.txt"
set "CUR="
if exist "%BAK%" for /f "usebackq delims=" %%A in ("%BAK%") do set "CUR=%%A"
if "%CUR%"=="" (
  reg delete "%KEY%" /v DisabledHotkeys /f >nul 2>&1
) else (
  reg add "%KEY%" /v DisabledHotkeys /t REG_SZ /d "%CUR%" /f >nul
)
echo Возвращено DisabledHotkeys="%CUR%"
taskkill /f /im explorer.exe >nul 2>&1
timeout /t 2 /nobreak >nul
start "" explorer.exe
echo Готово.
