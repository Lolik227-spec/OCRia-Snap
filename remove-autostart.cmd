@echo off
set DST=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\ocria-snap.vbs
if exist "%DST%" del "%DST%"
echo Автозапуск выключен.
