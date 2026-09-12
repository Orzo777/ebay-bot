' Hidden-window launcher for trigger_report.bat (Task Scheduler flashes a
' console otherwise). WshShell.Run's 3rd arg (0 = hidden window style, False = don't wait).
Set WshShell = CreateObject("WScript.Shell")
WshShell.Run """D:\ebay-bot\trigger_report.bat""", 0, False
Set WshShell = Nothing
