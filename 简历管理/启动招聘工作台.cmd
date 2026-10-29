@echo off
setlocal
set "SCRIPT=%~dp0scripts\start-web.ps1"
if not exist "%SCRIPT%" (
  echo start-web.ps1 not found.
  exit /b 1
)
"C:\Program Files\PowerShell\7\pwsh.exe" -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT%" %*
endlocal
