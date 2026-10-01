@echo off
REM Triggers an immediate discovery-report run on GitHub Actions.
REM Read-only: just rebuilds discovery_report.md from the current cached
REM state, no eBay calls, no writes back to the shared cache.
REM
REM trigger_secret.bat (gitignored) sets GH_TOKEN so gh works even when Task
REM Scheduler runs this in a restricted logon where the credential keyring is
REM locked. Generate it once:
REM   'set "GH_TOKEN=' + (gh auth token).Trim() + '"' | Set-Content D:\ebay-bot\trigger_secret.bat -Encoding ascii
set "LOG=D:\ebay-bot\trigger.log"
if exist "%~dp0trigger_secret.bat" call "%~dp0trigger_secret.bat"
>>"%LOG%" echo [%DATE% %TIME%] report: start (user=%USERNAME%)
"C:\Program Files\GitHub CLI\gh.exe" workflow run report.yml --repo Orzo777/ebay-bot >>"%LOG%" 2>&1
>>"%LOG%" echo [%DATE% %TIME%] report: exit=%ERRORLEVEL%
