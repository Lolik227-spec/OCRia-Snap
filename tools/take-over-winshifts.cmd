@echo off
setlocal enabledelayedexpansion
rem Отдаём Win+Shift+S нашему софту: отключаем системную комбинацию ножниц.
set "KEY=HKCU\Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced"
set "BAK=%~dp0winshifts-backup.txt"

set "CUR="
for /f "tokens=2,*" %%A in ('reg query "%KEY%" /v DisabledHotkeys 2^>nul') do set "CUR=%%B"
if not exist "%BAK%" echo(!CUR!> "%BAK%"

set "NEW=!CUR!S"
echo(!CUR!| findstr /i /c:S >nul && set "NEW=!CUR!"
reg add "%KEY%" /v DisabledHotkeys /t REG_SZ /d "!NEW!" /f >nul
if errorlevel 1 (
  echo Не удалось записать в реестр.
  exit /b 1
)
echo Готово: DisabledHotkeys="!NEW!"  (Win+Shift+S отключён у винды)
echo Побочный эффект: Win+S ^(поиск^) тоже перестанет работать. Откат — restore-winshifts.cmd
echo.
echo Перезапускаю проводник, чтобы винда отпустила комбинацию...
taskkill /f /im explorer.exe >nul 2>&1
timeout /t 2 /nobreak >nul
start "" explorer.exe
timeout /t 2 /nobreak >nul
echo.
echo Проверка:
py "%~dp0check-hotkey.py"
echo.
echo Теперь перезапустите OCRia Snap (start-ocria-snap.cmd в корне проекта).
