@echo off
rem ----------------------------------------------------------------
rem  Atualizador do Proclamare - de um duplo clique neste arquivo.
rem
rem  Este arquivo fica na pasta do Proclamare (a que tem o Proclamare.pyw).
rem  Se houver um ZIP do Proclamare ao lado dele (ex.: trazido no pen drive),
rem  usa o ZIP; senao, baixa a versao nova do GitHub. Antes de trocar
rem  qualquer coisa ele confere o pacote, desliga o Proclamare e guarda
rem  uma copia do codigo atual em backups\. Se algo falhar, volta sozinho
rem  para a versao anterior. Os seus dados (config.json, contas, listas,
rem  horarios, historico, logs) NUNCA sao tocados.
rem
rem  Funciona sozinho: o programa (PowerShell) esta DENTRO deste arquivo,
rem  depois da linha marcadora la embaixo. O "Bypass" vale somente para
rem  este processo do PowerShell (nao altera o computador). Nao precisa
rem  de administrador. O registro fica em logs\atualizar.log.
rem
rem  Opcoes (testes/suporte): -PastaInstalacao "<pasta>" -Zip "<arquivo>"
rem  -SemReiniciar -SemRede -EsperaInicio <segundos>
rem
rem  IMPORTANTE para quem editar: esta parte (cmd) fica so em ASCII, sem
rem  "goto" e sem "%" ou "!" no comando do PowerShell; tudo o que roda
rem  depois do PowerShell fica no bloco ( ... ), que o cmd le inteiro antes
rem  de executar: a atualizacao pode trocar ESTE arquivo enquanto ele roda.
rem ----------------------------------------------------------------
setlocal EnableExtensions DisableDelayedExpansion
"%SystemRoot%\System32\chcp.com" 65001 >nul
rem Caminho deste arquivo e opcoes viajam por variavel de ambiente (nunca coladas no comando do PowerShell).
set "PROCLAMARE_ATUALIZAR_BAT=%~f0"
set "PROCLAMARE_ATUALIZAR_ARGS=%*"
echo.
echo  Atualizando o Proclamare. Nao feche esta janela ate aparecer a mensagem final.
echo.
pushd "%~dp0"
setlocal EnableDelayedExpansion
(
    "%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -NonInteractive -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Stop'; try { $t=[IO.File]::ReadAllText($env:PROCLAMARE_ATUALIZAR_BAT,[Text.Encoding]::UTF8) } catch { Write-Host 'ERRO: nao consegui ler o arquivo do atualizador.'; exit 2 }; $m=[string][char]10+[char]35+'PROCLAMARE-POWERSHELL'; $i=$t.IndexOf($m); if ($i -lt 0) { Write-Host 'ERRO: o arquivo do atualizador esta incompleto. Copie-o de novo.'; exit 2 }; & ([ScriptBlock]::Create($t.Substring($i+1))); exit 4"
    set "CODIGO=!ERRORLEVEL!"
    echo.
    pause
    popd
    exit /b !CODIGO!
)
#PROCLAMARE-POWERSHELL
# ======================================================================================================
# Atualizador do Proclamare - parte PowerShell (Windows PowerShell 5.1).
#
# O trecho .bat acima le ESTE arquivo inteiro e executa o texto a partir da linha marcadora como um
# ScriptBlock (nunca grava um .ps1 em disco). Por isso funciona mesmo numa instalacao antiga que nao tem
# nenhum script de atualizacao: basta copiar este .bat para a pasta do Proclamare.
#
# Etapas (nada na instalacao e alterado antes da etapa 6):
#   1. Acha a pasta do Proclamare (a deste .bat, ou -PastaInstalacao) e confere .venv e Proclamare.pyw.
#   2. Fonte: -Zip, ou o ZIP "proclamare*.zip" mais novo ao lado do .bat, ou o GitHub (host fixo, HTTPS
#      TLS 1.2+, sem redirecionamento, 60 s, 50 MB). -SemRede proibe o download.
#   3. Confere e extrai o pacote numa pasta temporaria NOVA em %TEMP%: recusa "..", caminho absoluto, letra
#      de disco, link/ponto de nova analise, nomes reservados, duplicados; teto de tamanho e de quantidade;
#      exige UMA raiz com server.py, Proclamare.pyw, static\app.js e requirements.lock.
#      NAO ha assinatura digital: a confianca vem do host fixo do GitHub por HTTPS (ou do ZIP que o Artur
#      trouxe) mais esta conferencia da estrutura.
#   4. Mostra versao atual e nova (docstring do server.py; senao a data do arquivo mais novo). Recusa
#      um pacote de versao MAIS ANTIGA que a instalada (um ZIP velho esquecido na pasta).
#   5. Desliga o Proclamare pelo proprio lancador (Proclamare.pyw --parar: so o PID validado) e espera a
#      porta fechar (15 s).
#   6. Copia o codigo atual para backups\AAAA-MM-DD_HHMMSS\ (fica so com as 3 mais novas).
#   7. Instala: arquivos da raiz sobrescritos; pastas de codigo ESPELHADAS (o que saiu da versao nova sai).
#      Dados, .venv, logs, backups, audios_temp e __pycache__ nunca sao tocados (listas abaixo).
#   8. requirements.lock mudou? pip --require-hashes com o python do .venv (como o tools\instalar.ps1).
#   9. Compila todos os .py/.pyw com o python do .venv.
#  10. Religa pelo lancador e espera GET /api/status = 200 (40 s). Porta = mesma regra do lancador.
#  Falhou da etapa 6 em diante: volta o codigo da copia, religa a versao anterior e explica.
#
# Codigos de saida: 0 = atualizado; 1 = falhou e voltou para a versao anterior; 2 = recusado antes de
# mudar qualquer coisa; 3 = falhou E a volta tambem falhou (a copia fica em backups\).
# Nunca le nem mostra o config.json (segredos) e nunca encerra processos que nao sejam o PID validado.
# Nunca grava uma copia identica deste .bat (o antivirus congela o processo: ver Eh-Copia-Do-Bat). Por isso
# este .bat nao entra na copia de seguranca e, se a volta automatica acontecer, o .bat novo continua no lugar.
# ======================================================================================================
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }

# ---- Constantes ---------------------------------------------------------------------------------------
$UrlPacote = 'https://codeload.github.com/arturgabriels779-alt/proclamare/zip/refs/heads/main'
$HostPacote = 'codeload.github.com'
$DownloadMaxBytes = [long]50MB
$DownloadMaxSeg = 60
$ZipMaxEntradasTotal = 20000          # entradas no ZIP inteiro (diretorio central)
$ZipMaxEntradas = 5000                # arquivos extraidos (so os da raiz do Proclamare, sem dados)
$ZipMaxBytes = [long]300MB            # soma descompactada do que e extraido
$ZipMaxArquivoBytes = [long]100MB     # um arquivo so
$BackupsManter = 3
$EsperaParadaSeg = 15
$EsperaInicioSegPadrao = 40
$PipMaxSeg = 900
$CompilarMaxSeg = 180
$PortaPadrao = 8502
$EspacoMinimoMB = 300
$CaminhoMax = 259                     # MAX_PATH do Windows (o PowerShell 5.1 nao usa caminhos longos)
$FolgaBackup = 29                     # '\backups\AAAA-MM-DD_HHMMSS_9' a mais no caminho da copia

# Pastas de primeiro nivel que NUNCA sao copiadas, espelhadas, apagadas nem guardadas na copia.
# (Musicas: o padrao fica fora da pasta do programa, mas uma pasta de musicas aqui dentro tambem e protegida.)
$PastasProtegidas = @('.venv', 'venv', 'env', 'logs', 'audios_temp', 'backups', '__pycache__', '.git', '.claude',
    'node_modules', 'Músicas', 'Musicas', 'Músicas do Proclamare', 'Musicas do Proclamare')

# ARQUIVOS DE DADOS (em qualquer nivel): nunca copiados do pacote, nunca sobrescritos nem apagados.
# Quem criar um arquivo de dados novo no Proclamare TEM de acrescenta-lo aqui (regra no CLAUDE.md).
#   config.json* (PIN, Client Secret e tokens do Spotify, mensagens, voz da loja, copias .corrompido-*),
#   listas.json*, contas.json* (hash das senhas), anuncios.jsonl* (historico de anuncios),
#   programacao*.json* (inclui programacao.visto.json) e os temporarios da gravacao atomica (.<nome>.*.tmp).
$PadroesDeDados = @('config.json*', 'listas.json*', 'contas.json*', 'anuncios.jsonl*', 'programacao*.json*',
    '.config.*', '.listas.*', '.contas.*', '.anuncios.*', '.programacao.*', '*.tmp', '.env', '.env.*')
# Alem dos dados, arquivos que nunca vem do pacote nem entram na copia (ZIPs, logs, compilados, lixo do Windows).
$PadroesIgnorados = $PadroesDeDados + @('*.zip', '*.log', '*.log.*', '*.pyc', '*.pyo', 'Thumbs.db', 'desktop.ini')
# Pastas de codigo conhecidas (entram na copia mesmo que o pacote novo nao as traga).
$PastasDeCodigo = @('static', 'som', 'tools', 'docs', 'tests', 'assets', '.github')
# Arquivos da raiz que entram na copia mesmo que o pacote novo nao os traga.
$PadroesCodigoRaiz = @('*.py', '*.pyw', '*.bat', '*.cmd', '*.ps1', '*.md', '*.txt', '*.lock', 'LICENSE', '.gitignore', '.gitattributes')

# ---- Estado (usado pela volta automatica) ----------------------------------------------------------------
$script:Raiz = $null
$script:ArqLog = $null
$script:LogPendente = New-Object System.Collections.Generic.List[string]
$script:Temp = $null
$script:Trava = $null
$script:Fase = 'preparando'           # 'preparando' -> 'alterando' (a instalacao ja foi mexida) -> 'pronto'
$script:Backup = $null
$script:PastasTocadas = @()           # @{ Nome; Existia }
$script:ArquivosTocados = @()         # @{ Nome; Existia }
$script:PipRodou = $false
$script:VenvPy = $null
$script:VenvPyw = $null
$script:Porta = $PortaPadrao
$script:Opc = $null
$script:Desligou = $false           # o atualizador desligou um Proclamare que estava ligado
$script:BatTamanho = -1               # tamanho e SHA-256 do .bat que o cmd esta executando (ver Eh-Copia-Do-Bat)
$script:BatHash = $null

# ---- Mensagens e registro (logs\atualizar.log) -----------------------------------------------------------
function Registrar([string]$Texto) {
    $linha = (Get-Date).ToString('yyyy-MM-dd HH:mm:ss') + '  ' + $Texto
    if ($script:ArqLog) {
        try { [System.IO.File]::AppendAllText($script:ArqLog, $linha + "`r`n", (New-Object System.Text.UTF8Encoding($false))) } catch { }
    } else {
        $script:LogPendente.Add($linha)
    }
}
function Dizer([string]$Texto, [string]$Cor = '') {
    if ($Cor) { Write-Host $Texto -ForegroundColor $Cor } else { Write-Host $Texto }
    Registrar $Texto
}
function Passo([string]$Texto) { Dizer "`n==> $Texto" 'Cyan' }
function Ok([string]$Texto) { Dizer "    OK: $Texto" 'Green' }
function Aviso([string]$Texto) { Dizer "    AVISO: $Texto" 'Yellow' }

# Falha esperada, com texto para o usuario (o catch principal decide se precisa voltar a versao).
function Recusar([string]$Texto) {
    $e = New-Object System.Exception $Texto
    $e.Data['proclamare'] = 'recusa'
    throw $e
}

function Iniciar-Log {
    $pastaLogs = Join-Path $script:Raiz 'logs'
    try {
        if (-not (Test-Path -LiteralPath $pastaLogs)) { [void][System.IO.Directory]::CreateDirectory($pastaLogs) }
        if (Eh-Link (Get-Item -LiteralPath $pastaLogs -Force)) { return }   # nunca grava atraves de um link
        $arq = Join-Path $pastaLogs 'atualizar.log'
        if ((Test-Path -LiteralPath $arq) -and ((Get-Item -LiteralPath $arq -Force).Length -gt 1MB)) {
            Move-Item -LiteralPath $arq -Destination ($arq + '.1') -Force
        }
        $script:ArqLog = $arq
        foreach ($l in $script:LogPendente) {
            [System.IO.File]::AppendAllText($arq, $l + "`r`n", (New-Object System.Text.UTF8Encoding($false)))
        }
        $script:LogPendente.Clear()
    } catch { $script:ArqLog = $null }
}

# ---- Utilidades de arquivo -----------------------------------------------------------------------------
function Eh-Link($Info) { return (([int]$Info.Attributes -band [int][System.IO.FileAttributes]::ReparsePoint) -ne 0) }

function Casa-Algum([string]$Nome, [string[]]$Padroes) {
    foreach ($p in $Padroes) { if ($Nome -like $p) { return $true } }
    return $false
}
function Eh-Ignorado([string]$Nome) { return (Casa-Algum $Nome $PadroesIgnorados) }
function Eh-Protegida([string]$Nome) {
    foreach ($p in $PastasProtegidas) { if ($Nome -ieq $p) { return $true } }
    return $false
}

function Filhos([string]$Pasta) {
    if (-not [System.IO.Directory]::Exists($Pasta)) { return @() }
    return @((New-Object System.IO.DirectoryInfo($Pasta)).GetFileSystemInfos())
}

# Tamanho do maior caminho relativo (a partir de $Base, com a barra) dentro de $Pasta, sem entrar em links.
function Maior-Caminho([string]$Pasta, [string]$Base) {
    $maior = 0
    if (-not [System.IO.Directory]::Exists($Pasta)) { return 0 }
    foreach ($f in (Filhos $Pasta)) {
        $n = $f.FullName.Length - $Base.TrimEnd('\').Length
        if ($n -gt $maior) { $maior = $n }
        if ($f -is [System.IO.DirectoryInfo] -and -not (Eh-Link $f)) {
            $m = Maior-Caminho $f.FullName $Base
            if ($m -gt $maior) { $maior = $m }
        }
    }
    return $maior
}

# Primeiro link/juncao encontrado dentro de $Pasta (sem nunca entrar num link); $null se nao houver.
function Achar-Link([string]$Pasta) {
    if (-not [System.IO.Directory]::Exists($Pasta)) { return $null }
    if (Eh-Link (New-Object System.IO.DirectoryInfo($Pasta))) { return $Pasta }
    foreach ($f in (Filhos $Pasta)) {
        if (Eh-Link $f) { return $f.FullName }
        if ($f -is [System.IO.DirectoryInfo]) {
            $achado = Achar-Link $f.FullName
            if ($achado) { return $achado }
        }
    }
    return $null
}

# ANTIVIRUS (medido com o Kaspersky Endpoint Security): um processo filho de um .bat que grava uma copia
# IDENTICA (byte a byte) desse .bat e CONGELADO pelo antivirus (parece um script se replicando) e a janela fica
# parada para sempre. Por isso nenhum arquivo igual ao .bat em uso e gravado: nem na extracao, nem na copia de
# seguranca, nem na instalacao (ele ja esta no lugar). Gravar POR CIMA dele um conteudo diferente nao congela.
function Hash-De-Bytes([byte[]]$Bytes) {
    $sha = [System.Security.Cryptography.SHA256]::Create()
    try { return [System.BitConverter]::ToString($sha.ComputeHash($Bytes)) } finally { $sha.Dispose() }
}
function Eh-Copia-Do-Bat([string]$Caminho) {
    if ($script:BatTamanho -lt 0) { return $false }
    try {
        if ((New-Object System.IO.FileInfo($Caminho)).Length -ne $script:BatTamanho) { return $false }
        return ((Hash-De-Bytes ([System.IO.File]::ReadAllBytes($Caminho))) -eq $script:BatHash)
    } catch { return $false }
}

# Antivirus e indexador seguram arquivos recem-gravados por alguns instantes: tenta de novo, com pausas.
function Copiar-Arquivo([string]$De, [string]$Para) {
    if (Eh-Copia-Do-Bat $De) {
        Registrar "não copiado (igual ao atualizador que está rodando): $De"
        return
    }
    for ($i = 1; $i -le 6; $i++) {
        try {
            if ([System.IO.File]::Exists($Para)) {
                $at = [System.IO.File]::GetAttributes($Para)
                if (($at -band [System.IO.FileAttributes]::ReadOnly) -ne 0) {
                    [System.IO.File]::SetAttributes($Para, ($at -band (-bnot [System.IO.FileAttributes]::ReadOnly)))
                }
            }
            [System.IO.File]::Copy($De, $Para, $true)
            return
        } catch {
            if ($i -eq 6) { throw }
            Start-Sleep -Milliseconds (300 * $i)
        }
    }
}
function Apagar-Arquivo([string]$Caminho) {
    for ($i = 1; $i -le 6; $i++) {
        try {
            if (-not [System.IO.File]::Exists($Caminho)) { return }
            [System.IO.File]::SetAttributes($Caminho, [System.IO.FileAttributes]::Normal)
            [System.IO.File]::Delete($Caminho)
            return
        } catch {
            if ($i -eq 6) { throw }
            Start-Sleep -Milliseconds (300 * $i)
        }
    }
}

# Espelha $Origem em $Destino: copia tudo o que esta na origem e APAGA do destino o que nao esta nela.
# Nunca toca arquivos de dados (PadroesIgnorados) nem __pycache__ e nunca entra num link. $Origem vazia/$null
# = apagar o conteudo de codigo de $Destino (e a pasta, se ficar vazia).
function Espelhar([string]$Origem, [string]$Destino) {
    $temOrigem = $Origem -and [System.IO.Directory]::Exists($Origem)
    if ($temOrigem -and -not [System.IO.Directory]::Exists($Destino)) {
        if ([System.IO.File]::Exists($Destino)) { Apagar-Arquivo $Destino }   # era arquivo, virou pasta
        [void][System.IO.Directory]::CreateDirectory($Destino)
    }
    if ([System.IO.Directory]::Exists($Destino) -and (Eh-Link (New-Object System.IO.DirectoryInfo($Destino)))) {
        throw "a pasta $Destino é um link (atalho de pasta); por segurança nada foi feito nela"
    }
    $vistos = @{}   # nomes da origem (a hashtable do PowerShell ignora maiusculas/minusculas, como o Windows)
    if ($temOrigem) {
        foreach ($f in (Filhos $Origem)) {
            if (Eh-Link $f) { throw "o pacote tem um link inesperado: $($f.FullName)" }
            $alvo = Join-Path $Destino $f.Name
            if ($f -is [System.IO.DirectoryInfo]) {
                if ($f.Name -ieq '__pycache__') { continue }
                $vistos[$f.Name] = $true
                Espelhar $f.FullName $alvo
            } else {
                if (Eh-Ignorado $f.Name) { continue }
                $vistos[$f.Name] = $true
                if ([System.IO.Directory]::Exists($alvo)) { Espelhar $null $alvo }   # era pasta, virou arquivo
                Copiar-Arquivo $f.FullName $alvo
            }
        }
    }
    foreach ($f in (Filhos $Destino)) {
        if ($vistos.ContainsKey($f.Name)) { continue }
        if (Eh-Link $f) { continue }                      # nunca segue nem apaga links
        if ($f -is [System.IO.DirectoryInfo]) {
            if ($temOrigem -and $f.Name -ieq '__pycache__') { continue }
            Espelhar $null $f.FullName
        } else {
            # Espelhando: nao mexe em nada ignorado. Apagando uma pasta que saiu: so os DADOS ficam.
            if ($temOrigem) { if (Eh-Ignorado $f.Name) { continue } }
            elseif (Casa-Algum $f.Name $PadroesDeDados) { continue }
            Apagar-Arquivo $f.FullName
        }
    }
    if (-not $temOrigem -and [System.IO.Directory]::Exists($Destino) -and (Filhos $Destino).Count -eq 0) {
        try { [System.IO.Directory]::Delete($Destino, $false) } catch { }
    }
}

# ---- Processos ------------------------------------------------------------------------------------------
# Coloca aspas num argumento de linha de comando do Windows (regras do CommandLineToArgvW).
function Citar-Argumento([string]$Arg) {
    if ($Arg -ne '' -and $Arg -notmatch '[\s"]') { return $Arg }
    $r = $Arg -replace '(\\*)"', '$1$1\"'
    $r = $r -replace '(\\+)$', '$1$1'
    return '"' + $r + '"'
}

# Roda um programa SEM janela, com stdin fechado (nada pode ficar esperando teclado) e tempo maximo.
function Rodar-Processo([string]$Exe, [string[]]$Argumentos, [int]$SegundosMax, [hashtable]$Ambiente = @{}) {
    $res = @{ Codigo = -1; Saida = ''; Erro = ''; Estourou = $false }
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = $Exe
    $psi.Arguments = (@($Argumentos | ForEach-Object { Citar-Argumento ([string]$_) }) -join ' ')
    $psi.UseShellExecute = $false
    $psi.CreateNoWindow = $true
    $psi.WorkingDirectory = [Environment]::SystemDirectory   # pasta neutra: nada da pasta atual e carregado
    $psi.RedirectStandardInput = $true
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $psi.StandardOutputEncoding = [System.Text.Encoding]::UTF8
    $psi.StandardErrorEncoding = [System.Text.Encoding]::UTF8
    foreach ($k in $Ambiente.Keys) { $psi.EnvironmentVariables[$k] = [string]$Ambiente[$k] }
    $proc = New-Object System.Diagnostics.Process
    $proc.StartInfo = $psi
    try {
        [void]$proc.Start()
        $proc.StandardInput.Close()
        $tSaida = $proc.StandardOutput.ReadToEndAsync()
        $tErro = $proc.StandardError.ReadToEndAsync()
        if ($proc.WaitForExit($SegundosMax * 1000)) {
            $proc.WaitForExit()
            $res.Codigo = $proc.ExitCode
        } else {
            $res.Estourou = $true
            try { $proc.Kill() } catch { }
        }
        if ($tSaida.Wait(3000)) { $res.Saida = [string]$tSaida.Result }
        if ($tErro.Wait(3000)) { $res.Erro = [string]$tErro.Result }
    } catch {
        $res.Erro = $_.Exception.Message
    } finally {
        $proc.Dispose()
    }
    return $res
}

# Ultimas linhas de uma saida longa (vai para o log; nunca le config.json, entao nao ha segredo nela).
function Ultimas-Linhas([string]$Texto, [int]$N = 15) {
    $linhas = @($Texto -split "`r?`n" | Where-Object { $_.Trim() })
    if ($linhas.Count -le $N) { return ($linhas -join "`n") }
    return ($linhas[($linhas.Count - $N)..($linhas.Count - 1)] -join "`n")
}

# ---- Porta e servidor -----------------------------------------------------------------------------------
# Mesma regra do Proclamare.pyw (ler_porta): PROCLAMARE_PORT, senao ANUNCIADOR_PORT, senao 8502 (1..65535).
function Ler-Porta {
    $bruto = $env:PROCLAMARE_PORT
    if (-not $bruto) { $bruto = $env:ANUNCIADOR_PORT }
    if (-not $bruto) { return $PortaPadrao }
    $n = 0
    if (-not [int]::TryParse($bruto.Trim(), [ref]$n)) { return $PortaPadrao }
    if ($n -ge 1 -and $n -le 65535) { return $n }
    return $PortaPadrao
}

# True se GET http://127.0.0.1:<porta>/api/status responder 200 (sem proxy: o endereco e sempre local).
function Servidor-Responde([int]$TimeoutMs = 3000) {
    $resp = $null
    try {
        $req = [System.Net.HttpWebRequest][System.Net.WebRequest]::Create("http://127.0.0.1:$($script:Porta)/api/status")
        $req.Proxy = $null
        $req.Timeout = $TimeoutMs
        $req.ReadWriteTimeout = $TimeoutMs
        $req.KeepAlive = $false
        $req.AllowAutoRedirect = $false
        $resp = $req.GetResponse()
        return ([int]$resp.StatusCode -eq 200)
    } catch {
        return $false
    } finally {
        if ($resp) { try { $resp.Close() } catch { } }
    }
}

function Esperar-Status([int]$Segundos) {
    $fim = (Get-Date).AddSeconds($Segundos)
    while ((Get-Date) -lt $fim) {
        if (Servidor-Responde) { return $true }
        Start-Sleep -Milliseconds 800
    }
    return (Servidor-Responde)
}

# Desliga pelo proprio lancador (--parar so encerra o PID validado por caminho + hora de criacao).
# Devolve $true se o Proclamare ficou desligado.
function Parar-Proclamare {
    $pid_ = Join-Path $script:Raiz 'logs\proclamare.pid'
    if (-not (Test-Path -LiteralPath $pid_) -and -not (Servidor-Responde)) {
        Ok 'o Proclamare já estava desligado.'
        return $true
    }
    $pyw = Join-Path $script:Raiz 'Proclamare.pyw'
    # python.exe (e nao pythonw) so para guardar a resposta do lancador no log; PROCLAMARE_NO_BROWSER=1 troca
    # as caixas de mensagem por texto. A pasta de trabalho e neutra (o lancador se acha pelo proprio caminho).
    $r = Rodar-Processo $script:VenvPy @('-I', $pyw, '--parar') 40 @{ PROCLAMARE_NO_BROWSER = '1' }
    Registrar ("lancador --parar: codigo $($r.Codigo); " + (Ultimas-Linhas ($r.Saida + "`n" + $r.Erro) 5))
    $fim = (Get-Date).AddSeconds($EsperaParadaSeg)
    while ((Get-Date) -lt $fim -and (Servidor-Responde 1500)) { Start-Sleep -Milliseconds 500 }
    if (Servidor-Responde 1500) { return $false }
    $script:Desligou = $true
    Ok 'Proclamare desligado.'
    return $true
}

# Liga como o atalho da Area de Trabalho (pythonw -I Proclamare.pyw), mas com PROCLAMARE_NO_BROWSER=1 para
# o lancador nunca abrir uma caixa de mensagem que prenda a trava dele; o painel e aberto aqui no fim.
function Ligar-Proclamare([int]$Segundos) {
    $pyw = Join-Path $script:Raiz 'Proclamare.pyw'
    $anterior = $env:PROCLAMARE_NO_BROWSER
    $proc = $null
    try {
        $env:PROCLAMARE_NO_BROWSER = '1'
        $proc = Start-Process -FilePath $script:VenvPyw -ArgumentList ('-I "' + $pyw + '"') -WorkingDirectory $script:Raiz -PassThru
        $null = $proc.Handle
    } catch {
        Registrar ('falha ao chamar o lancador: ' + $_.Exception.Message)
    } finally {
        if ($null -eq $anterior) { Remove-Item Env:\PROCLAMARE_NO_BROWSER -ErrorAction SilentlyContinue }
        else { $env:PROCLAMARE_NO_BROWSER = $anterior }
    }
    $okStatus = Esperar-Status $Segundos
    # O lancador termina sozinho (ate ~35 s); espera-o para a trava dele nao atrapalhar uma volta de versao.
    if ($proc -and -not $okStatus) { try { [void]$proc.WaitForExit(40000) } catch { } }
    return $okStatus
}

# ---- Opcoes da linha de comando (vem do .bat em PROCLAMARE_ATUALIZAR_ARGS; nunca sao executadas) ---------
function Dividir-Argumentos([string]$Texto) {
    $lista = New-Object System.Collections.Generic.List[string]
    if (-not $Texto) { return }
    $atual = New-Object System.Text.StringBuilder
    $aspas = $false
    $tem = $false
    foreach ($c in $Texto.ToCharArray()) {
        if ($c -eq [char]34) { $aspas = -not $aspas; $tem = $true; continue }
        if (-not $aspas -and [char]::IsWhiteSpace($c)) {
            if ($tem) { $lista.Add($atual.ToString()); [void]$atual.Clear(); $tem = $false }
            continue
        }
        [void]$atual.Append($c)
        $tem = $true
    }
    if ($tem) { $lista.Add($atual.ToString()) }
    return $lista.ToArray()
}

function Ler-Opcoes([string]$Texto) {
    $o = @{ PastaInstalacao = ''; Zip = ''; SemReiniciar = $false; SemRede = $false; EsperaInicio = $EsperaInicioSegPadrao }
    $a = @(Dividir-Argumentos $Texto)
    $i = 0
    while ($i -lt $a.Count) {
        $nome = $a[$i]
        $temValor = ($i + 1) -lt $a.Count
        switch -Regex ($nome) {
            '^-PastaInstalacao$' { if (-not $temValor) { Recusar 'Falta a pasta depois de -PastaInstalacao.' }; $o.PastaInstalacao = $a[$i + 1]; $i += 2; continue }
            '^-Zip$' { if (-not $temValor) { Recusar 'Falta o arquivo depois de -Zip.' }; $o.Zip = $a[$i + 1]; $i += 2; continue }
            '^-EsperaInicio$' {
                $n = 0
                if (-not $temValor -or -not [int]::TryParse($a[$i + 1], [ref]$n) -or $n -lt 3 -or $n -gt 300) { Recusar '-EsperaInicio precisa de um número de 3 a 300 (segundos).' }
                $o.EsperaInicio = $n; $i += 2; continue
            }
            '^-SemReiniciar$' { $o.SemReiniciar = $true; $i += 1; continue }
            '^-SemRede$' { $o.SemRede = $true; $i += 1; continue }
            default { Recusar "Opção desconhecida: $nome" }
        }
    }
    return $o
}

# ---- Versao -----------------------------------------------------------------------------------------------
# A versao mora no docstring do topo do server.py: 'Proclamare - servidor local (v2.8 "..."; v2.7.1 ...'.
function Ler-Versao([string]$ServerPy) {
    try {
        $fs = [System.IO.File]::Open($ServerPy, 'Open', 'Read', 'ReadWrite')
        try {
            $buf = New-Object byte[] 4096
            $n = $fs.Read($buf, 0, $buf.Length)
        } finally { $fs.Dispose() }
        $texto = [System.Text.Encoding]::UTF8.GetString($buf, 0, $n)
        if ($texto -match 'servidor local \(v(\d+(?:\.\d+){1,2})') { return $Matches[1] }
        if ($texto -match '\bv(\d+\.\d+(?:\.\d+)?)\b') { return $Matches[1] }
    } catch { }
    return $null
}

function Versao-Comparavel([string]$V) {
    $p = @($V.Split('.') | ForEach-Object { [int]$_ })
    while ($p.Count -lt 3) { $p += 0 }
    return New-Object System.Version($p[0], $p[1], $p[2])
}

# Data do arquivo de codigo mais novo de uma pasta do Proclamare (para quando nao ha numero de versao).
function Data-Mais-Nova([string]$Pasta) {
    $mais = $null
    foreach ($rel in @('server.py', 'Proclamare.pyw', 'static\app.js', 'static\index.html', 'requirements.lock')) {
        $c = Join-Path $Pasta $rel
        if ([System.IO.File]::Exists($c)) {
            $d = [System.IO.File]::GetLastWriteTime($c)
            if ($null -eq $mais -or $d -gt $mais) { $mais = $d }
        }
    }
    return $mais
}

function Descrever-Versao([string]$Versao, $Data) {
    $txt = if ($Versao) { "versão $Versao" } else { 'versão sem número' }
    if ($Data) { $txt += ' (arquivos de ' + $Data.ToString('dd/MM/yyyy') + ')' }
    return $txt
}

# ---- Fonte: ZIP ao lado do .bat ou GitHub ----------------------------------------------------------------
function Mensagem-SemPacote {
    return ("Não achei a versão nova.`n" +
        "    Coloque o arquivo ZIP do Proclamare (o do pen drive) nesta pasta, ao lado do`n" +
        "    'Atualizar Proclamare.bat', e dê dois cliques nele de novo.`n" +
        "    Nada foi alterado: o Proclamare continua como estava.")
}

function Baixar-Pacote([string]$Destino) {
    # TLS 1.2 ou 1.3 (nunca SSL3/TLS 1.0/1.1)
    try {
        [System.Net.ServicePointManager]::SecurityProtocol = [System.Net.SecurityProtocolType]::Tls12 -bor ([System.Net.SecurityProtocolType]12288)
    } catch {
        [System.Net.ServicePointManager]::SecurityProtocol = [System.Net.SecurityProtocolType]::Tls12
    }
    $uri = [System.Uri]$UrlPacote
    if ($uri.Scheme -ne 'https' -or $uri.Host -ne $HostPacote) { Recusar 'Endereço de download inválido.' }
    $resp = $null
    $entrada = $null
    $saida = $null
    try {
        try {
            $req = [System.Net.HttpWebRequest][System.Net.WebRequest]::Create($uri)
            $req.UserAgent = 'Proclamare-atualizador'
            $req.Timeout = 30000
            $req.ReadWriteTimeout = 30000
            $req.AllowAutoRedirect = $false     # host fixo: nenhum desvio para outro endereco
            $req.KeepAlive = $false
            $req.Proxy = [System.Net.WebRequest]::GetSystemWebProxy()
            $req.Proxy.Credentials = [System.Net.CredentialCache]::DefaultCredentials
            $resp = [System.Net.HttpWebResponse]$req.GetResponse()
        } catch {
            $ex = $_.Exception
            while ($ex.InnerException -and -not ($ex -is [System.Net.WebException])) { $ex = $ex.InnerException }
            Registrar ('download falhou: ' + $ex.Message)
            Recusar ("Não consegui baixar a versão nova pela internet (sem internet ou a rede bloqueia o GitHub).`n" +
                "    Coloque o arquivo ZIP do Proclamare (o do pen drive) nesta pasta, ao lado do`n" +
                "    'Atualizar Proclamare.bat', e dê dois cliques nele de novo.`n" +
                "    Nada foi alterado: o Proclamare continua como estava.")
        }
        if ([int]$resp.StatusCode -ne 200) { Recusar "O GitHub respondeu com o código $([int]$resp.StatusCode); por segurança o download foi cancelado. Nada foi alterado." }
        $final = $resp.ResponseUri
        if ($final.Scheme -ne 'https' -or $final.Host -ne $HostPacote) { Recusar 'O download foi desviado para outro endereço; por segurança ele foi cancelado. Nada foi alterado.' }
        if ($resp.ContentLength -gt $DownloadMaxBytes) { Recusar 'O arquivo no GitHub é grande demais; por segurança o download foi cancelado. Nada foi alterado.' }
        $entrada = $resp.GetResponseStream()
        $saida = [System.IO.File]::Open($Destino, 'CreateNew', 'Write', 'None')
        $buf = New-Object byte[] 65536
        $total = [long]0
        $relogio = [System.Diagnostics.Stopwatch]::StartNew()
        while ($true) {
            try { $n = $entrada.Read($buf, 0, $buf.Length) } catch {
                Registrar ('download interrompido: ' + $_.Exception.Message)
                Recusar ("O download parou no meio (internet instável).`n" + (Mensagem-SemPacote))
            }
            if ($n -le 0) { break }
            $total += $n
            if ($total -gt $DownloadMaxBytes) { Recusar 'O arquivo recebido é maior que o esperado; por segurança o download foi cancelado. Nada foi alterado.' }
            $saida.Write($buf, 0, $n)
            if ($relogio.Elapsed.TotalSeconds -gt $DownloadMaxSeg) {
                Recusar ("O download está demorando demais e foi cancelado.`n" + (Mensagem-SemPacote))
            }
        }
        Ok ('baixado: {0:N1} MB' -f ($total / 1MB))
    } finally {
        if ($saida) { $saida.Dispose() }
        if ($entrada) { $entrada.Dispose() }
        if ($resp) { $resp.Close() }
    }
}

# ---- Conferir e extrair o pacote -----------------------------------------------------------------------
$NomesReservados = '^(con|prn|aux|nul|com[0-9]|lpt[0-9])(\..*)?$'

# Nome de entrada do ZIP normalizado ('/' como separador) ou $null se for suspeito.
function Nome-Seguro([string]$Bruto) {
    $n = $Bruto.Replace('\', '/')
    if (-not $n) { return $null }
    if ($n.StartsWith('/') -or $n.Contains(':')) { return $null }        # absoluto, letra de disco ou fluxo alternativo
    $corpo = $n.TrimEnd('/')
    if (-not $corpo) { return $null }
    foreach ($seg in $corpo.Split('/')) {
        if ($seg -eq '' -or $seg -eq '.' -or $seg -eq '..') { return $null }
        if ($seg -match '[<>"|?*\x00-\x1f]') { return $null }
        if ($seg.EndsWith('.') -or $seg.EndsWith(' ')) { return $null }
        if ($seg -match $NomesReservados) { return $null }
    }
    return $n
}

# Devolve @{ Raiz = pasta extraida; Data = data mais nova do pacote }.
function Extrair-Pacote([string]$ArquivoZip, [string]$Destino) {
    Add-Type -AssemblyName System.IO.Compression
    $fs = $null
    $za = $null
    try {
        try { $fs = [System.IO.File]::Open($ArquivoZip, 'Open', 'Read', 'Read') } catch {
            Recusar "Não consegui abrir o arquivo $([System.IO.Path]::GetFileName($ArquivoZip)) (ele está aberto em outro programa?). Nada foi alterado."
        }
        try {
            $za = New-Object System.IO.Compression.ZipArchive($fs, [System.IO.Compression.ZipArchiveMode]::Read, $true)
            $entradas = @($za.Entries)
        } catch {
            Recusar ("O arquivo $([System.IO.Path]::GetFileName($ArquivoZip)) não é um ZIP válido (incompleto ou estragado).`n" +
                '    Copie o ZIP de novo do pen drive (ou baixe de novo) e tente outra vez. Nada foi alterado.')
        }
        if ($entradas.Count -gt $ZipMaxEntradasTotal) { Recusar "O ZIP tem arquivos demais ($($entradas.Count)); não parece o Proclamare. Nada foi alterado." }

        # 1) Todos os nomes conferidos ANTES de extrair qualquer coisa.
        $arquivos = @{}       # nome normalizado (minusculas) -> entrada
        foreach ($e in $entradas) {
            $n = Nome-Seguro $e.FullName
            if (-not $n) { Recusar "O ZIP traz um nome de arquivo perigoso ($($e.FullName)); por segurança ele foi recusado. Nada foi alterado." }
            $ext = 0
            try { $ext = [int]$e.ExternalAttributes } catch { $ext = 0 }
            if (((($ext -shr 16) -band 0xF000) -eq 0xA000) -or (($ext -band 0x400) -ne 0)) {
                Recusar "O ZIP traz um link ($($e.FullName)); por segurança ele foi recusado. Nada foi alterado."
            }
            if ($n.EndsWith('/')) { continue }
            $chave = $n.ToLowerInvariant()
            if ($arquivos.ContainsKey($chave)) { Recusar "O ZIP traz o mesmo arquivo duas vezes ($n); por segurança ele foi recusado. Nada foi alterado." }
            $arquivos[$chave] = $e
        }

        # 2) Uma unica raiz com os arquivos-chave do Proclamare.
        $raizes = @()
        foreach ($k in @($arquivos.Keys)) {
            if ($k -ne 'server.py' -and -not $k.EndsWith('/server.py')) { continue }
            $pre = $k.Substring(0, $k.Length - 'server.py'.Length)
            if ($arquivos.ContainsKey($pre + 'proclamare.pyw') -and $arquivos.ContainsKey($pre + 'static/app.js') -and
                $arquivos.ContainsKey($pre + 'requirements.lock')) { $raizes += $pre }
        }
        if ($raizes.Count -eq 0) {
            Recusar ("Este ZIP não é do Proclamare (faltam server.py, Proclamare.pyw, static\app.js ou requirements.lock).`n" +
                '    Confira se copiou o arquivo certo. Nada foi alterado.')
        }
        if ($raizes.Count -gt 1) { Recusar 'Este ZIP tem mais de uma cópia do Proclamare dentro; use um ZIP com uma só. Nada foi alterado.' }
        $pre = [string]$raizes[0]

        # 3) O que sera extraido: so a raiz do Proclamare, sem dados nem pastas protegidas.
        $escolhidas = @()
        $soma = [long]0
        $dataMaisNova = $null
        foreach ($k in @($arquivos.Keys)) {
            if (-not $k.StartsWith($pre)) { continue }
            $e = $arquivos[$k]
            $rel = (Nome-Seguro $e.FullName).Substring($pre.Length)
            $segs = $rel.Split('/')
            if (Eh-Protegida $segs[0]) { continue }
            if (@($segs | Where-Object { $_ -ieq '__pycache__' }).Count -gt 0) { continue }
            if (Eh-Ignorado $segs[-1]) { continue }
            if ($e.Length -gt $ZipMaxArquivoBytes) { Recusar "O ZIP traz um arquivo grande demais ($rel). Nada foi alterado." }
            $soma += $e.Length
            $escolhidas += , @($e, $rel)
            try {
                $d = $e.LastWriteTime.DateTime
                if ($d.Year -ge 2000 -and ($segs.Count -eq 1 -or $segs[0] -ieq 'static') -and ($null -eq $dataMaisNova -or $d -gt $dataMaisNova)) { $dataMaisNova = $d }
            } catch { }
        }
        if ($escolhidas.Count -gt $ZipMaxEntradas) { Recusar "O ZIP tem arquivos demais ($($escolhidas.Count)); não parece o Proclamare. Nada foi alterado." }
        if ($soma -gt $ZipMaxBytes) { Recusar 'O conteúdo do ZIP é grande demais; não parece o Proclamare. Nada foi alterado.' }

        # 4) Extrai numa pasta NOVA, conferindo de novo que cada caminho final fica dentro dela.
        [void][System.IO.Directory]::CreateDirectory($Destino)
        $base = [System.IO.Path]::GetFullPath($Destino).TrimEnd('\') + '\'
        $buf = New-Object byte[] 81920
        $gravados = [long]0
        foreach ($par in $escolhidas) {
            $e = $par[0]
            $rel = $par[1]
            $alvo = [System.IO.Path]::GetFullPath($base + $rel.Replace('/', '\'))
            if (-not $alvo.StartsWith($base, [System.StringComparison]::OrdinalIgnoreCase)) {
                Recusar "O ZIP tentou gravar fora da pasta temporária ($rel). Nada foi alterado."
            }
            if ($alvo.Length -gt $CaminhoMax) {
                Recusar ("O caminho da pasta temporária do Windows é comprido demais para extrair $rel.`n" +
                    '    Nada foi alterado. Avise o suporte e mande o arquivo logs\atualizar.log.')
            }
            if ($e.Length -eq $script:BatTamanho) {
                # Mesmo tamanho do .bat em uso: confere em memoria ANTES de gravar (ver Eh-Copia-Do-Bat).
                $ms = New-Object System.IO.MemoryStream
                $in = $e.Open()
                try { $in.CopyTo($ms) } catch [System.IO.InvalidDataException] {
                    Recusar "O ZIP está estragado ($rel). Copie-o de novo e tente outra vez. Nada foi alterado."
                } finally { $in.Dispose() }
                if ((Hash-De-Bytes $ms.ToArray()) -eq $script:BatHash) {
                    Registrar "não extraído (igual ao atualizador que está rodando): $rel"
                    continue
                }
            }
            [void][System.IO.Directory]::CreateDirectory([System.IO.Path]::GetDirectoryName($alvo))
            $in = $e.Open()
            try {
                $out = [System.IO.File]::Open($alvo, 'CreateNew', 'Write', 'None')
                try {
                    $lidos = [long]0
                    while (($n = $in.Read($buf, 0, $buf.Length)) -gt 0) {
                        $lidos += $n
                        $gravados += $n
                        if ($lidos -gt $e.Length -or $gravados -gt $ZipMaxBytes) { Recusar "O arquivo $rel é maior do que o declarado no ZIP. Nada foi alterado." }
                        $out.Write($buf, 0, $n)
                    }
                    if ($lidos -ne $e.Length) { Recusar "O arquivo $rel está incompleto no ZIP. Nada foi alterado." }
                } finally { $out.Dispose() }
            } catch [System.IO.InvalidDataException] {
                Recusar "O ZIP está estragado ($rel). Copie-o de novo e tente outra vez. Nada foi alterado."
            } finally { $in.Dispose() }
            try { [System.IO.File]::SetLastWriteTime($alvo, $e.LastWriteTime.DateTime) } catch { }
        }
        # Defesa extra: nada extraido pode ser link.
        $link = Achar-Link $Destino
        if ($link) { Recusar "O pacote extraído tem um link ($link); por segurança ele foi recusado. Nada foi alterado." }
        Ok "$($escolhidas.Count) arquivos conferidos e extraídos."
        return @{ Raiz = $Destino; Data = $dataMaisNova }
    } finally {
        if ($za) { $za.Dispose() }
        if ($fs) { $fs.Dispose() }
    }
}

# ---- Copia de seguranca (backups\AAAA-MM-DD_HHMMSS) ----------------------------------------------------------
$PadraoNomeBackup = '^\d{4}-\d{2}-\d{2}_\d{6}(_\d+)?$'

function Fazer-Backup([string[]]$PastasDoPacote, [string[]]$ArquivosDoPacote) {
    $pastaBackups = Join-Path $script:Raiz 'backups'
    if ([System.IO.Directory]::Exists($pastaBackups) -and (Eh-Link (New-Object System.IO.DirectoryInfo($pastaBackups)))) {
        Recusar 'A pasta backups é um link (atalho de pasta); por segurança não vou gravar nela. Nada foi alterado.'
    }
    [void][System.IO.Directory]::CreateDirectory($pastaBackups)
    $nome = (Get-Date).ToString('yyyy-MM-dd_HHmmss')
    $destino = Join-Path $pastaBackups $nome
    $i = 2
    while (Test-Path -LiteralPath $destino) { $destino = Join-Path $pastaBackups ($nome + '_' + $i); $i++ }
    [void][System.IO.Directory]::CreateDirectory($destino)
    try {
        foreach ($f in (Filhos $script:Raiz)) {
            if ($f -is [System.IO.DirectoryInfo] -or (Eh-Link $f) -or (Eh-Ignorado $f.Name) -or (Eh-Protegida $f.Name)) { continue }
            if (($ArquivosDoPacote -contains $f.Name) -or (Casa-Algum $f.Name $PadroesCodigoRaiz)) {
                Copiar-Arquivo $f.FullName (Join-Path $destino $f.Name)
            }
        }
        foreach ($p in (@($PastasDoPacote) + $PastasDeCodigo | Select-Object -Unique)) {
            $origem = Join-Path $script:Raiz $p
            if ([System.IO.Directory]::Exists($origem)) { Espelhar $origem (Join-Path $destino $p) }
        }
    } catch {
        try { [System.IO.Directory]::Delete($destino, $true) } catch { }
        Recusar ("Não consegui fazer a cópia de segurança do código atual: $($_.Exception.Message)`n" +
            '    (disco cheio ou arquivo em uso?). Nada foi alterado; o Proclamare continua como estava.')
    }
    return $destino
}

function Podar-Backups {
    $pastaBackups = Join-Path $script:Raiz 'backups'
    if (-not [System.IO.Directory]::Exists($pastaBackups)) { return }
    $nossas = @(Filhos $pastaBackups | Where-Object {
            $_ -is [System.IO.DirectoryInfo] -and -not (Eh-Link $_) -and $_.Name -match $PadraoNomeBackup } |
        Sort-Object Name -Descending)
    if ($nossas.Count -le $BackupsManter) { return }
    foreach ($velha in $nossas[$BackupsManter..($nossas.Count - 1)]) {
        # Nunca apaga a copia desta execucao (ela pode ser preciosa se a volta falhou).
        if ($script:Backup -and $velha.FullName -ieq $script:Backup) { continue }
        if (Achar-Link $velha.FullName) { continue }   # nunca segue um link ao apagar
        try { [System.IO.Directory]::Delete($velha.FullName, $true); Registrar "cópia antiga apagada: $($velha.Name)" } catch { }
    }
}

# ---- Instalar e voltar ------------------------------------------------------------------------------------
function Instalar-Codigo([string]$Novo) {
    $script:Fase = 'alterando'
    foreach ($f in (Filhos $Novo)) {
        if (-not ($f -is [System.IO.DirectoryInfo]) -or (Eh-Protegida $f.Name)) { continue }
        $alvo = Join-Path $script:Raiz $f.Name
        $script:PastasTocadas += , @{ Nome = $f.Name; Existia = [System.IO.Directory]::Exists($alvo) }
        Espelhar $f.FullName $alvo
    }
    foreach ($f in (Filhos $Novo)) {
        if ($f -is [System.IO.DirectoryInfo] -or (Eh-Ignorado $f.Name) -or (Eh-Protegida $f.Name)) { continue }
        $alvo = Join-Path $script:Raiz $f.Name
        $script:ArquivosTocados += , @{ Nome = $f.Name; Existia = [System.IO.File]::Exists($alvo) }
        Copiar-Arquivo $f.FullName $alvo
    }
}

function Rodar-Pip([string]$Lock) {
    $r = Rodar-Processo $script:VenvPy @('-I', '-m', 'pip', 'install', '--require-hashes', '--only-binary', ':all:',
        '--no-deps', '--disable-pip-version-check', '-r', $Lock) $PipMaxSeg
    Registrar ("pip: codigo $($r.Codigo)`n" + (Ultimas-Linhas ($r.Saida + "`n" + $r.Erro) 20))
    return (-not $r.Estourou -and $r.Codigo -eq 0)
}

# Devolve $true se o codigo antigo voltou inteiro.
function Voltar-Codigo {
    $b = $script:Backup
    $falhas = 0
    foreach ($p in $script:PastasTocadas) {
        try {
            $origem = if ($p.Existia) { Join-Path $b $p.Nome } else { $null }
            Espelhar $origem (Join-Path $script:Raiz $p.Nome)
        } catch { $falhas++; Registrar "volta: falhou a pasta $($p.Nome): $($_.Exception.Message)" }
    }
    foreach ($a in $script:ArquivosTocados) {
        try {
            $alvo = Join-Path $script:Raiz $a.Nome
            $copia = Join-Path $b $a.Nome
            if ($a.Existia -and [System.IO.File]::Exists($copia)) { Copiar-Arquivo $copia $alvo }
            elseif (-not $a.Existia) { Apagar-Arquivo $alvo }
        } catch { $falhas++; Registrar "volta: falhou o arquivo $($a.Nome): $($_.Exception.Message)" }
    }
    if ($script:PipRodou) {
        $lock = Join-Path $script:Raiz 'requirements.lock'
        if ([System.IO.File]::Exists($lock)) {
            Dizer '    Reinstalando os componentes da versão anterior (precisa de internet)...'
            if (-not (Rodar-Pip $lock)) { Aviso 'não consegui reinstalar os componentes antigos; se o Proclamare não ligar, rode o "Instalar Proclamare.bat".' }
        }
    }
    return ($falhas -eq 0)
}

# Volta a versao anterior e (sem -SemReiniciar) liga de novo. Devolve o codigo de saida (1 ou 3).
function Voltar-Versao([string]$Motivo, $Opc) {
    Dizer "`nERRO: $Motivo" 'Red'
    Passo 'Voltando para a versão anterior (cópia de segurança)...'
    # Sempre pede ao lancador: uma versao nova lenta pode estar viva sem responder ainda (segurando a porta).
    [void](Parar-Proclamare)
    $ok = Voltar-Codigo
    if (-not $ok) {
        Dizer ("`nATENÇÃO: não consegui voltar todos os arquivos da versão anterior.`n" +
            "    A cópia do código antigo está em: $($script:Backup)`n" +
            "    Seus dados NÃO foram mexidos. Peça ajuda ao suporte e mostre o arquivo logs\atualizar.log.") 'Red'
        return 3
    }
    Ok 'arquivos da versão anterior de volta.'
    if ($Opc.SemReiniciar) {
        Dizer "`nA atualização NÃO foi feita: o Proclamare voltou para a versão anterior. Seus dados foram mantidos." 'Yellow'
        return 1
    }
    Passo 'Ligando a versão anterior...'
    if (Ligar-Proclamare $Opc.EsperaInicio) {
        Ok 'a versão anterior está funcionando.'
        Dizer ("`nA atualização NÃO foi feita: o Proclamare voltou para a versão anterior e está ligado.`n" +
            '    Seus dados foram mantidos. Avise o suporte e mande o arquivo logs\atualizar.log.') 'Yellow'
    } else {
        Dizer ("`nA atualização NÃO foi feita e a versão anterior não respondeu ao ligar.`n" +
            "    Dê dois cliques no ícone 'Proclamare' da Área de Trabalho. Se não ligar, avise o suporte`n" +
            '    e mande o arquivo logs\atualizar.log. Seus dados foram mantidos.') 'Yellow'
    }
    return 1
}

# Script Python da conferencia: compila (sem gravar .pyc) os .py/.pyw da raiz e das pastas de codigo.
$CodigoConferir = @'
import os, sys
raiz = sys.argv[1]
falhas = 0
total = 0
def eh_link(p):
    try:
        j = getattr(os.path, 'isjunction', None)
        return os.path.islink(p) or bool(j and j(p))
    except OSError:
        return True
def conferir(caminho):
    global falhas, total
    total += 1
    try:
        with open(caminho, 'rb') as f:
            compile(f.read(), caminho, 'exec', dont_inherit=True)
    except Exception as exc:
        falhas += 1
        print('FALHOU: %s: %s' % (os.path.relpath(caminho, raiz), exc))
for nome in sorted(os.listdir(raiz)):
    p = os.path.join(raiz, nome)
    if os.path.isfile(p) and nome.lower().endswith(('.py', '.pyw')):
        conferir(p)
for alvo in sys.argv[2:]:
    base = os.path.join(raiz, alvo)
    if not os.path.isdir(base) or eh_link(base):
        continue
    for pasta, dirs, arqs in os.walk(base):
        dirs[:] = [d for d in dirs if d != '__pycache__' and not eh_link(os.path.join(pasta, d))]
        for a in sorted(arqs):
            if a.lower().endswith(('.py', '.pyw')):
                conferir(os.path.join(pasta, a))
print('CONFERIDOS: %d; FALHAS: %d' % (total, falhas))
sys.exit(1 if falhas else 0)
'@

function Conferir-Codigo([string[]]$Pastas) {
    $script_ = Join-Path $script:Temp 'conferir.py'
    [System.IO.File]::WriteAllText($script_, $CodigoConferir, (New-Object System.Text.UTF8Encoding($false)))
    $r = Rodar-Processo $script:VenvPy (@('-I', '-X', 'utf8', $script_, $script:Raiz) + $Pastas) $CompilarMaxSeg
    Registrar ("conferencia: codigo $($r.Codigo)`n" + (Ultimas-Linhas ($r.Saida + "`n" + $r.Erro) 20))
    if ($r.Estourou -or $r.Codigo -ne 0) {
        return @{ Ok = $false; Detalhe = (Ultimas-Linhas ($r.Saida + "`n" + $r.Erro) 6) }
    }
    return @{ Ok = $true; Detalhe = (Ultimas-Linhas $r.Saida 1) }
}

function Espaco-Livre-MB([string]$Pasta) {
    try {
        $raizDisco = [System.IO.Path]::GetPathRoot([System.IO.Path]::GetFullPath($Pasta))
        if (-not $raizDisco -or $raizDisco.StartsWith('\\')) { return $null }
        return [long]((New-Object System.IO.DriveInfo($raizDisco)).AvailableFreeSpace / 1MB)
    } catch { return $null }
}

# Sobras de execucoes anteriores interrompidas (so pastas com o NOSSO prefixo e com mais de 1 dia).
function Limpar-Temporarios-Antigos {
    try {
        $limite = (Get-Date).AddDays(-1)
        foreach ($d in (Filhos ([System.IO.Path]::GetTempPath()))) {
            if ($d -is [System.IO.DirectoryInfo] -and $d.Name -like 'proclamare-atualizar-*' -and $d.LastWriteTime -lt $limite -and
                -not (Eh-Link $d) -and -not (Achar-Link $d.FullName)) {
                try { [System.IO.Directory]::Delete($d.FullName, $true) } catch { }
            }
        }
    } catch { }
}

# ---- Fluxo principal -------------------------------------------------------------------------------------
function Atualizar {
    $bat = [string]$env:PROCLAMARE_ATUALIZAR_BAT
    $argsTexto = [string]$env:PROCLAMARE_ATUALIZAR_ARGS
    # O servidor religado aqui herda o ambiente: estas variaveis nao precisam ir junto.
    Remove-Item Env:\PROCLAMARE_ATUALIZAR_BAT, Env:\PROCLAMARE_ATUALIZAR_ARGS -ErrorAction SilentlyContinue
    if (-not $bat) { Recusar 'Rode o atualizador pelo arquivo "Atualizar Proclamare.bat".' }
    try {
        $bytesBat = [System.IO.File]::ReadAllBytes($bat)   # so leitura (ler nao incomoda o antivirus)
        $script:BatTamanho = $bytesBat.Length
        $script:BatHash = Hash-De-Bytes $bytesBat
    } catch { Recusar 'Não consegui ler o arquivo do atualizador.' }
    $pastaBat = Split-Path -Parent $bat
    $opc = Ler-Opcoes $argsTexto
    $script:Opc = $opc

    $raiz = if ($opc.PastaInstalacao) { $opc.PastaInstalacao } else { $pastaBat }
    $raiz = [System.IO.Path]::GetFullPath($raiz).TrimEnd('\')
    $script:Raiz = $raiz
    Dizer '=== Atualizar o Proclamare ===' 'White'
    Dizer "Pasta do Proclamare: $raiz"
    if (-not [System.IO.File]::Exists((Join-Path $raiz 'Proclamare.pyw')) -or -not [System.IO.File]::Exists((Join-Path $raiz 'server.py'))) {
        Recusar ("Esta pasta não é a do Proclamare (não achei o Proclamare.pyw e o server.py aqui).`n" +
            "    Copie o 'Atualizar Proclamare.bat' (e o ZIP, se tiver) para a pasta do Proclamare no computador`n" +
            '    da loja, a que tem o arquivo Proclamare.pyw, e dê dois cliques nele lá. Nada foi alterado.')
    }
    Iniciar-Log
    Registrar "opcoes: SemReiniciar=$($opc.SemReiniciar) SemRede=$($opc.SemRede) Zip=$([bool]$opc.Zip) EsperaInicio=$($opc.EsperaInicio)"

    # Uma atualizacao por vez (a trava some sozinha se esta janela for fechada).
    try {
        $script:Trava = [System.IO.File]::Open((Join-Path $raiz 'logs\atualizar.lock'), 'OpenOrCreate', 'ReadWrite', 'None')
    } catch {
        Recusar 'Já tem uma atualização do Proclamare rodando (outra janela). Espere ela terminar. Nada foi alterado.'
    }

    $script:VenvPy = Join-Path $raiz '.venv\Scripts\python.exe'
    $script:VenvPyw = Join-Path $raiz '.venv\Scripts\pythonw.exe'
    if (-not [System.IO.File]::Exists($script:VenvPy) -or -not [System.IO.File]::Exists($script:VenvPyw)) {
        Recusar ("O Proclamare desta pasta não está instalado por completo (falta a pasta .venv).`n" +
            "    Rode primeiro o 'Instalar Proclamare.bat' e depois esta atualização. Nada foi alterado.")
    }
    $script:Porta = Ler-Porta
    Registrar "porta: $($script:Porta)"

    Limpar-Temporarios-Antigos
    $script:Temp = Join-Path ([System.IO.Path]::GetTempPath()) ('proclamare-atualizar-' + [guid]::NewGuid().ToString('N').Substring(0, 8))
    [void][System.IO.Directory]::CreateDirectory($script:Temp)
    $livreTemp = Espaco-Livre-MB $script:Temp
    if ($null -ne $livreTemp -and $livreTemp -lt $EspacoMinimoMB) { Recusar "Pouco espaço livre no disco ($livreTemp MB). Libere uns $EspacoMinimoMB MB e tente de novo. Nada foi alterado." }
    $livre = Espaco-Livre-MB $raiz
    if ($null -ne $livre -and $livre -lt $EspacoMinimoMB) { Recusar "Pouco espaço livre no disco ($livre MB). Libere uns $EspacoMinimoMB MB e tente de novo. Nada foi alterado." }

    # ---- 2. Fonte da versao nova
    Passo 'Procurando a versão nova...'
    $zip = $null
    $zipDaPasta = $null
    if ($opc.Zip) {
        $zip = [System.IO.Path]::GetFullPath($opc.Zip)
        if (-not [System.IO.File]::Exists($zip)) { Recusar "Não achei o arquivo $zip. Nada foi alterado." }
        Ok "usando o ZIP informado: $([System.IO.Path]::GetFileName($zip))"
    } else {
        $zips = @(Filhos $pastaBat | Where-Object { -not ($_ -is [System.IO.DirectoryInfo]) -and -not (Eh-Link $_) -and $_.Name -like 'proclamare*.zip' } |
            Sort-Object LastWriteTime -Descending)
        if ($zips.Count -gt 0) {
            $zip = $zips[0].FullName
            $zipDaPasta = $zip
            if ($zips.Count -gt 1) {
                Ok ("achei $($zips.Count) arquivos ZIP do Proclamare; vou usar o mais novo: $($zips[0].Name) (de " +
                    $zips[0].LastWriteTime.ToString('dd/MM/yyyy HH:mm') + ')')
            } else {
                Ok "usando o ZIP desta pasta: $($zips[0].Name)"
            }
        } elseif ($opc.SemRede) {
            Recusar (Mensagem-SemPacote)
        } else {
            Dizer '    Nenhum ZIP nesta pasta: baixando a versão nova do GitHub (precisa de internet)...'
            $zip = Join-Path $script:Temp 'proclamare-github.zip'
            Baixar-Pacote $zip
        }
    }

    # ---- 3. Conferir e extrair
    Passo 'Conferindo o pacote (antes de mexer em qualquer coisa)...'
    $pacote = Extrair-Pacote $zip (Join-Path $script:Temp 'n')
    $novo = $pacote.Raiz

    # ---- 4. Versoes
    $vAtual = Ler-Versao (Join-Path $raiz 'server.py')
    $vNova = Ler-Versao (Join-Path $novo 'server.py')
    $dNova = $pacote.Data
    if (-not $dNova) { $dNova = Data-Mais-Nova $novo }
    $descAtual = Descrever-Versao $vAtual (Data-Mais-Nova $raiz)
    $descNova = Descrever-Versao $vNova $dNova
    Dizer "    Instalada agora: $descAtual"
    Dizer "    Versão nova:     $descNova"
    if ($vAtual -and $vNova -and ((Versao-Comparavel $vNova) -lt (Versao-Comparavel $vAtual))) {
        Recusar ("O pacote é de uma versão MAIS ANTIGA ($vNova) do que a instalada ($vAtual).`n" +
            '    Se for um ZIP velho esquecido nesta pasta, apague-o e tente de novo. Nada foi alterado.')
    }

    # O que o pacote traz (pastas espelhadas e arquivos da raiz).
    $pastasNovas = @(Filhos $novo | Where-Object { $_ -is [System.IO.DirectoryInfo] -and -not (Eh-Protegida $_.Name) } | ForEach-Object { $_.Name })
    $arquivosNovos = @(Filhos $novo | Where-Object { -not ($_ -is [System.IO.DirectoryInfo]) } | ForEach-Object { $_.Name })

    # Limite de 260 letras do Windows: o arquivo mais fundo (do pacote ou da instalacao) tem de caber na
    # instalacao E na copia (backups\AAAA-MM-DD_HHMMSS\...), conferido ANTES de mexer em qualquer coisa.
    $maisFundo = [Math]::Max((Maior-Caminho $novo $novo), [int]0)
    foreach ($p in (@($pastasNovas) + $PastasDeCodigo | Select-Object -Unique)) {
        $maisFundo = [Math]::Max($maisFundo, (Maior-Caminho (Join-Path $raiz $p) $raiz))
    }
    if (($raiz.Length + $FolgaBackup + $maisFundo) -gt $CaminhoMax) {
        Recusar ("A pasta do Proclamare está num caminho comprido demais para o Windows (limite de 260 letras)`n" +
            "    e a cópia de segurança não caberia. Nada foi alterado. Avise o suporte.")
    }

    # Nenhuma pasta de codigo da instalacao pode ser link (o espelho apagaria/gravaria do outro lado).
    foreach ($p in (@($pastasNovas) + $PastasDeCodigo | Select-Object -Unique)) {
        $link = Achar-Link (Join-Path $raiz $p)
        if ($link) { Recusar "A pasta $link é um link (atalho de pasta); por segurança não vou atualizar. Nada foi alterado." }
    }

    # ---- 5. Desligar
    Passo 'Desligando o Proclamare...'
    if (-not (Parar-Proclamare)) {
        Recusar ("O Proclamare não desligou (talvez ele tenha sido ligado por outro jeito, por exemplo numa janela preta).`n" +
            "    Feche essa janela ou use 'Desligar Proclamare' no Menu Iniciar e tente de novo. Nada foi alterado.")
    }

    # ---- 6. Copia de seguranca
    Passo 'Guardando uma cópia do código atual...'
    $script:Backup = Fazer-Backup $pastasNovas $arquivosNovos
    Ok "cópia em backups\$(Split-Path -Leaf $script:Backup)"
    $hashAntigo = $null
    $lockCopia = Join-Path $script:Backup 'requirements.lock'
    if ([System.IO.File]::Exists($lockCopia)) { $hashAntigo = (Get-FileHash -LiteralPath $lockCopia -Algorithm SHA256).Hash }

    # ---- 7. Instalar
    Passo 'Instalando a versão nova (seus dados não são tocados)...'
    Instalar-Codigo $novo
    Ok 'arquivos copiados.'

    # ---- 8. Dependencias
    $lockNovo = Join-Path $raiz 'requirements.lock'
    $hashNovo = (Get-FileHash -LiteralPath $lockNovo -Algorithm SHA256).Hash
    if ($hashNovo -ne $hashAntigo) {
        Passo 'A versão nova precisa de componentes novos: instalando (precisa de internet, pode levar alguns minutos)...'
        if ($opc.SemRede) { return (Voltar-Versao 'os componentes novos precisam de internet (-SemRede).' $opc) }
        $script:PipRodou = $true
        if (-not (Rodar-Pip $lockNovo)) {
            return (Voltar-Versao ("não consegui instalar os componentes novos (sem internet?).`n" +
                    '    Conecte o computador da loja à internet e rode a atualização de novo.') $opc)
        }
        Ok 'componentes instalados.'
    }

    # ---- 9. Conferir o codigo
    Passo 'Conferindo o código novo...'
    $conf = Conferir-Codigo $pastasNovas
    if (-not $conf.Ok) {
        return (Voltar-Versao ("o código novo tem erro e não pode ser usado:`n" + $conf.Detalhe) $opc)
    }
    Ok $conf.Detalhe

    # ---- 10. Ligar e confirmar
    if ($opc.SemReiniciar) {
        Dizer '    (-SemReiniciar: o Proclamare não foi ligado de novo.)'
    } else {
        Passo "Ligando o Proclamare (até $($opc.EsperaInicio) segundos)..."
        if (-not (Ligar-Proclamare $opc.EsperaInicio)) {
            return (Voltar-Versao 'a versão nova não respondeu ao ligar.' $opc)
        }
        Ok "Proclamare ligado e respondendo (porta $($script:Porta))."
        if ($env:PROCLAMARE_NO_BROWSER -ne '1') {
            try { Start-Process ("http://127.0.0.1:$($script:Porta)/") } catch { }
        }
    }
    $script:Fase = 'pronto'
    $rotulo = if ($vNova) { "a versão $vNova" } else { $descNova }
    Dizer ("`nProclamare atualizado para $rotulo. Seus dados (contas, listas, horários, mensagens," +
        ' configuração) foram mantidos.') 'Green'
    Dizer "    Cópia da versão anterior: backups\$(Split-Path -Leaf $script:Backup)" 'Gray'
    if ($zipDaPasta) {
        Dizer ("    O arquivo $([System.IO.Path]::GetFileName($zipDaPasta)) já pode ser apagado desta pasta" +
            ' (na próxima vez, sem ZIP aqui, a versão nova vem da internet).') 'Gray'
    }
    return 0
}

$codigo = 4
try {
    $codigo = [int](@(Atualizar)[-1])
} catch {
    $ex = $_.Exception
    $esperado = $ex.Data.Contains('proclamare')
    if (-not $esperado) { Registrar ('erro inesperado: ' + $_.ToString() + ' @ ' + $_.InvocationInfo.PositionMessage) }
    if ($script:Fase -eq 'alterando') {
        $msg = if ($esperado) { $ex.Message } else { "falha inesperada: $($ex.Message)" }
        # Falhou no meio da instalacao: volta a copia e (sem -SemReiniciar) religa a versao anterior.
        try { $codigo = [int](@(Voltar-Versao $msg $script:Opc)[-1]) } catch { Registrar ('volta falhou: ' + $_.ToString()); $codigo = 3 }
    } else {
        if ($esperado) { Dizer "`nERRO: $($ex.Message)" 'Red' }
        else { Dizer "`nERRO inesperado: $($ex.Message)`n    Nada foi alterado. Mande o arquivo logs\atualizar.log ao suporte." 'Red' }
        $codigo = 2
        # Nada mudou, mas o Proclamare ja tinha sido desligado (ex.: a copia falhou por disco cheio): liga de novo.
        if ($script:Desligou -and $script:Opc -and -not $script:Opc.SemReiniciar) {
            try {
                Passo 'Ligando o Proclamare de novo (a versão de antes)...'
                if (Ligar-Proclamare $script:Opc.EsperaInicio) { Ok 'Proclamare ligado.' }
                else { Aviso "o Proclamare não respondeu; dê dois cliques no ícone 'Proclamare' da Área de Trabalho." }
            } catch { Registrar ('religar falhou: ' + $_.ToString()) }
        }
    }
} finally {
    if ($script:Raiz) { try { Podar-Backups } catch { Registrar ('poda falhou: ' + $_.ToString()) } }
    if ($script:Temp -and [System.IO.Directory]::Exists($script:Temp)) {
        try { [System.IO.Directory]::Delete($script:Temp, $true) } catch { Registrar "não consegui apagar a pasta temporária $($script:Temp)" }
    }
    if ($script:Trava) { $script:Trava.Dispose() }
    Registrar "fim: codigo $codigo"
}
exit $codigo
