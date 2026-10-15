@echo off
rem ----------------------------------------------------------------
rem  Diagnostico do som do Proclamare - de um duplo clique neste arquivo.
rem  Gera um relatorio em logs\ para a equipe de suporte. A opcao 1 so le
rem  informacoes do computador. A opcao 2 TAMBEM TOCA SOM nas caixas.
rem ----------------------------------------------------------------
"%SystemRoot%\System32\chcp.com" 65001 >nul
pushd "%~dp0"
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
set "PYEXE=%~dp0.venv\Scripts\python.exe"
set "CODIGO=0"
if not exist "%PYEXE%" (
    echo.
    echo O Proclamare ainda nao foi instalado neste computador.
    echo Rode primeiro o "Instalar Proclamare.bat" e depois tente de novo.
    set "CODIGO=2"
    goto :fim
)
echo.
echo  ==== Diagnostico do som do Proclamare ====
echo.
echo  [1] Diagnostico (so leitura - nao toca som)
echo  [2] Diagnostico + teste audivel (TOCA SOM - faca com a loja FECHADA)
echo  [0] Sair
echo.
rem choice.exe nao expande texto digitado (evita injecao de comando); errorlevel: 1, 2 ou 3 (=0)
"%SystemRoot%\System32\choice.exe" /c 120 /n /m "Escolha 1, 2 ou 0: "
if errorlevel 255 goto :nada
if errorlevel 3 goto :nada
if errorlevel 2 goto :op2
if errorlevel 1 goto :op1
goto :nada

:op1
"%PYEXE%" "%~dp0tools\diagnostico_som.py"
set "CODIGO=%ERRORLEVEL%"
goto :fim

:op2
"%PYEXE%" "%~dp0tools\diagnostico_som.py" --teste-audivel
set "CODIGO=%ERRORLEVEL%"
goto :fim

:nada
echo Nenhum diagnostico foi feito.

:fim
echo.
pause
popd
exit /b %CODIGO%
