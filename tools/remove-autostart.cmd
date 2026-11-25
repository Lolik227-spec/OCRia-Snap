@echo off
setlocal
set "STARTUP=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup"
if exist "%STARTUP%\OCRia Snap.lnk" del "%STARTUP%\OCRia Snap.lnk"
if exist "%STARTUP%\ocria-snap.vbs" del "%STARTUP%\ocria-snap.vbs"
echo Автозапуск выключен.
pause
