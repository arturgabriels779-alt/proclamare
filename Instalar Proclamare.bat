@echo off
rem ----------------------------------------------------------------
rem  Instalador do Proclamare - de um duplo clique neste arquivo.
rem  Ele apenas chama tools\instalar.ps1. O "Bypass" vale somente
rem  para este processo do PowerShell (nao altera o computador).
rem
rem  Python: o instalador usa o Python 3.10 a 3.13 que ja estiver no
rem  computador. Se nao houver, baixa sozinho um Python so para o
rem  Proclamare (precisa de internet; sem administrador e sem mexer
rem  no PATH ou no registro). Opcoes avancadas: veja tools\instalar.ps1.
rem ----------------------------------------------------------------
"%SystemRoot%\System32\chcp.com" 65001 >nul
echo.
echo  Instalando o Proclamare. Na primeira vez pode levar alguns minutos:
echo  se o Python nao estiver instalado, ele sera baixado automaticamente.
echo.
pushd "%~dp0"
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\instalar.ps1" %*
set "CODIGO=%ERRORLEVEL%"
echo.
pause
popd
exit /b %CODIGO%
