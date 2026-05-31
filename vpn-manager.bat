@echo off
setlocal

REM Always resolve script path relative to this .bat location,
REM so it works even when called from another working directory.
set "SCRIPT_DIR=%~dp0"
set "SCRIPT_FILE=%SCRIPT_DIR%vpn_proxy_manager.py"

if not exist "%SCRIPT_FILE%" (
  echo [ERROR] Cannot find "%SCRIPT_FILE%"
  echo Please keep vpn-manager.bat and vpn_proxy_manager.py in the same folder.
  exit /b 1
)

REM No args -> open persistent status console with operation hints.
if "%~1"=="" (
  python "%SCRIPT_FILE%" console
  goto :eof
)

python "%SCRIPT_FILE%" %*

REM Keep window visible for double-click usage.
echo.
pause
endlocal
