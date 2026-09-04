@echo off
REM Triggers an immediate discovery-report run on GitHub Actions.
REM Read-only: just rebuilds discovery_report.md from the current cached
REM state, no eBay calls, no writes back to the shared cache.
"C:\Program Files\GitHub CLI\gh.exe" workflow run report.yml --repo Orzo777/ebay-bot
