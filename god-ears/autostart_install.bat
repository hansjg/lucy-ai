@echo off
:: Run this ONCE (yourself) to make God Ears start automatically at login.
:: It puts a shortcut to run.bat in your Startup folder.
:: To undo: delete "God Ears.lnk" from  shell:startup
set SCRIPT=%~dp0run.bat
powershell -NoProfile -Command ^
  "$s = (New-Object -ComObject WScript.Shell).CreateShortcut([Environment]::GetFolderPath('Startup') + '\God Ears.lnk');" ^
  "$s.TargetPath = '%SCRIPT%';" ^
  "$s.WorkingDirectory = '%~dp0';" ^
  "$s.Save();" ^
  "Write-Host 'God Ears will now auto-start at login. Shortcut created in ' ([Environment]::GetFolderPath('Startup'))"
pause
