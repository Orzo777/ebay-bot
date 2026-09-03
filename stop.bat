@echo off
rem Коректна зупинка бота: ставить прапорець stop.flag (щоб run.bat не
rem перезапускав) і завершує процес python.
cd /d D:\ebay-bot
echo stop > stop.flag
taskkill /F /IM python.exe >nul 2>&1
echo Бот зупинено. Перезапуск: run.bat  (або перезайти в систему).
