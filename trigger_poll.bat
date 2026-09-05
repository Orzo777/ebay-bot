@echo off
REM Triggers an immediate poll run on GitHub Actions (supplements the sparse cron).
REM Safe to run often: concurrency group in poll.yml queues duplicates instead of
REM running them in parallel, and state is always shared via the Actions cache.
set "LOG=D:\ebay-bot\trigger.log"
>>"%LOG%" echo [%DATE% %TIME%] poll: start (user=%USERNAME%)
"C:\Program Files\GitHub CLI\gh.exe" workflow run poll --repo Orzo777/ebay-bot >>"%LOG%" 2>&1
>>"%LOG%" echo [%DATE% %TIME%] poll: exit=%ERRORLEVEL%
