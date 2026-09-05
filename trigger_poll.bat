@echo off
REM Triggers an immediate poll run on GitHub Actions (supplements the sparse cron).
REM Safe to run often: concurrency group in poll.yml queues duplicates instead of
REM running them in parallel, and state is always shared via the Actions cache.
REM
REM trigger_secret.bat (gitignored) sets GH_TOKEN so gh works even when Task
REM Scheduler runs this in a restricted logon where the credential keyring is
REM locked. Generate it once:
REM   'set "GH_TOKEN=' + (gh auth token).Trim() + '"' | Set-Content D:\ebay-bot\trigger_secret.bat -Encoding ascii
set "LOG=D:\ebay-bot\trigger.log"
if exist "%~dp0trigger_secret.bat" call "%~dp0trigger_secret.bat"
>>"%LOG%" echo [%DATE% %TIME%] poll: start (user=%USERNAME%)
"C:\Program Files\GitHub CLI\gh.exe" workflow run poll --repo Orzo777/ebay-bot >>"%LOG%" 2>&1
>>"%LOG%" echo [%DATE% %TIME%] poll: exit=%ERRORLEVEL%
