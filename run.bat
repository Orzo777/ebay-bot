@echo off
rem ---------------------------------------------------------------------------
rem  eBay price+liquidity monitor — постійний запуск.
rem  Крутить `python main.py --interval`; якщо процес упав — перезапуск через 30 с.
rem  Зупинка: stop.bat (створює stop.flag → цикл виходить замість перезапуску).
rem  Автозапуск при вході в систему: ebay-bot.vbs у теці "Автозавантаження".
rem  Лог: bot.log
rem ---------------------------------------------------------------------------
cd /d D:\ebay-bot

set "PY=%LOCALAPPDATA%\Programs\Python\Python313\python.exe"
if not exist "%PY%" set "PY=python"
set PYTHONIOENCODING=utf-8

if exist stop.flag del stop.flag

:run
echo [%date% %time%] --- start --- >> bot.log
"%PY%" main.py --interval >> bot.log 2>&1
if exist stop.flag ( del stop.flag & echo [%date% %time%] --- stopped by stop.bat --- >> bot.log & goto end )
echo [%date% %time%] --- exited (code %errorlevel%), restart in 30s --- >> bot.log
timeout /t 30 /nobreak >nul
goto run

:end
