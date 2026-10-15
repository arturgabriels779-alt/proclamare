@echo off
rem ----------------------------------------------------------------
rem  Instalador do Proclamare - de um duplo clique neste arquivo.
rem  Ele apenas chama tools\instalar.ps1. O "Bypass" vale somente
rem  para este processo do PowerShell (nao altera o computador).
rem ----------------------------------------------------------------
"%SystemRoot%\System32\chcp.com" 65001 >nul
pushd "%~dp0"
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\instalar.ps1" %*
set "CODIGO=%ERRORLEVEL%"
echo.
pause
popd
exit /b %CODIGO%
