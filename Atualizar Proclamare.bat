@echo off
rem ----------------------------------------------------------------
rem  Atualizador do Proclamare - de um duplo clique neste arquivo.
rem
rem  Este arquivo fica na pasta do Proclamare (a que tem o Proclamare.pyw).
rem  Se houver um ZIP do Proclamare ao lado dele (ex.: trazido no pen drive)
rem  mais novo que o instalado, usa o ZIP (e o apaga depois de instalar);
rem  senao, baixa a ultima versao PUBLICADA (release) do GitHub.
rem  Antes de trocar qualquer coisa ele confere o pacote, desliga o
rem  Proclamare e guarda uma copia do codigo atual em backups\. Se algo
rem  falhar, volta sozinho para a versao anterior. Os seus dados
rem  (config.json, contas, listas, horarios, historico, logs) NUNCA sao
rem  tocados.
rem
rem  Funciona sozinho: o programa (PowerShell) esta DENTRO deste arquivo,
rem  depois da linha marcadora la embaixo. O "Bypass" vale somente para
rem  este processo do PowerShell (nao altera o computador). Nao precisa
rem  (nem deve) rodar como administrador. O registro fica em
rem  logs\atualizar.log.
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
    "%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -NonInteractive -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Stop'; try { $t=[IO.File]::ReadAllText($env:PROCLAMARE_ATUALIZAR_BAT,[Text.Encoding]::UTF8) } catch { Write-Host 'ERRO: nao consegui ler o arquivo do atualizador.'; exit 2 }; $m=[string][char]10+[char]35+'PROCLAMARE-POWERSHELL'; $i=$t.IndexOf($m,[StringComparison]::Ordinal); if ($i -lt 0) { Write-Host 'ERRO: o arquivo do atualizador esta incompleto. Copie-o de novo.'; exit 2 }; & ([ScriptBlock]::Create($t.Substring($i+1))); exit 4"
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
#   0. Recusa rodar ELEVADO (com o UAC ligado). Se a ultima atualizacao foi interrompida no meio
#      (logs\atualizar.pendente), primeiro poe de volta a versao anterior a partir da copia dela.
#   1. Acha a pasta do Proclamare (a deste .bat, ou -PastaInstalacao) e confere .venv e Proclamare.pyw.
#   2. Fonte: -Zip, ou o ZIP "proclamare*.zip" mais novo ao lado do .bat, ou a ULTIMA RELEASE publicada no
#      GitHub (api.github.com -> tag vX.Y[.Z] -> codeload.github.com; hosts fixos, HTTPS TLS 1.2+, sem
#      redirecionamento, tetos de tamanho e tempo). Nunca a branch main. -SemRede proibe a internet.
#      v2.8.3: um ZIP ACHADO na pasta (nao o -Zip) de versao menor ou igual a instalada e IGNORADO (avisa e
#      segue pela internet; com -SemRede para sem mudar nada); outro .zip de nome diferente so avisa. Depois do
#      sucesso o ZIP que foi instalado (so o da pasta do .bat) e apagado; falhar ao apagar e so um aviso.
#   3. Confere e extrai o pacote numa pasta temporaria NOVA em %TEMP%: recusa "..", caminho absoluto, letra
#      de disco, link, nome curto do Windows (8.3, "~1"), nome de arquivo de dados, caracteres invisiveis,
#      nomes reservados, duplicados; teto de tamanho e de quantidade; exige UMA raiz (fora de pastas
#      protegidas) com server.py, Proclamare.pyw, static\app.js e requirements.lock; confere o
#      requirements.lock (so pacotes e --hash, nenhuma outra opcao do pip).
#      AINDA NAO HA ASSINATURA DIGITAL: a confianca vem da release publicada pelo dono no GitHub (baixada
#      por HTTPS de hosts fixos) ou do ZIP que o Artur trouxe, mais estas conferencias.
#   4. Mostra versao atual e nova (docstring do server.py; senao a data do arquivo mais novo). Recusa versao
#      MAIS ANTIGA, versao nova ilegivel quando a atual e legivel, e (internet) avisa "ja esta na versao X".
#   5. Desliga o Proclamare pelo proprio lancador (Proclamare.pyw --parar: so o PID validado) e espera a
#      porta fechar (15 s).
#   6. Copia o codigo atual para backups\AAAA-MM-DD_HHMMSS\ (fica so com as 3 mais novas) e grava o plano em
#      logs\atualizar.pendente (apagado no fim; se sobrar, a proxima execucao volta a versao anterior).
#   7. Instala: arquivos da raiz sobrescritos; pastas de codigo ESPELHADAS (o que saiu da versao nova sai).
#      Dados, .venv, logs, backups, audios_temp e __pycache__ nunca sao tocados (listas abaixo); antes de
#      gravar/apagar, o nome REAL do alvo e conferido (nome curto 8.3 nunca chega num arquivo de dados).
#   8. requirements.lock mudou (sem contar CRLF/LF)? pip --isolated --require-hashes com o python do .venv.
#   9. Compila todos os .py/.pyw com o python do .venv.
#  10. Religa pelo lancador e espera GET /api/status = 200 (40 s). Porta = mesma regra do lancador.
#  Falhou da etapa 7 em diante: volta o codigo da copia (1 nova tentativa em 5 s), religa a anterior e explica.
#
# Codigos de saida: 0 = atualizado (ou ja estava na versao publicada); 1 = falhou e voltou para a versao
# anterior; 2 = recusado antes de mudar qualquer coisa; 3 = falhou E a volta tambem falhou (copia em backups\).
# Nunca le nem mostra o config.json (segredos) e nunca encerra processos que nao sejam o PID validado.
# Nunca grava uma copia identica deste .bat (o antivirus congela o processo: ver Eh-Copia-Do-Bat). Por isso
# este .bat nao entra na copia de seguranca e, se a volta automatica acontecer, o .bat novo continua no lugar.
# Adiado (documentado): assinatura do pacote; mesma conferencia de dono/ACL da pasta que o instalador faz.
# ======================================================================================================
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }

# ---- Constantes ---------------------------------------------------------------------------------------
$RepoGitHub = 'arturgabriels779-alt/proclamare'
$HostApi = 'api.github.com'
$HostPacote = 'codeload.github.com'
$UserAgent = 'Proclamare-atualizador'
$ReleaseMaxBytes = [long]256KB        # resposta JSON da release
$DownloadMaxBytes = [long]50MB
$DownloadMaxSeg = 60
$PadraoTag = '\Av[0-9]{1,4}\.[0-9]{1,4}(\.[0-9]{1,4})?\z'
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
$Ord = [System.StringComparison]::Ordinal
$OrdIC = [System.StringComparison]::OrdinalIgnoreCase

# Pastas de primeiro nivel que NUNCA sao copiadas, espelhadas, apagadas nem guardadas na copia.
# (Musicas: o padrao fica fora da pasta do programa, mas uma pasta de musicas aqui dentro tambem e protegida.)
$PastasProtegidas = @('.venv', 'venv', 'env', 'logs', 'audios_temp', 'backups', '__pycache__', '.git', '.claude',
    'node_modules', 'Músicas', 'Musicas', 'Músicas do Proclamare', 'Musicas do Proclamare')

# ARQUIVOS DE DADOS (em qualquer nivel): nunca copiados do pacote, nunca sobrescritos nem apagados; um pacote
# que traga qualquer caminho com um desses nomes e RECUSADO. Quem criar um arquivo de dados novo no Proclamare
# TEM de acrescenta-lo aqui (regra no CLAUDE.md).
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
# Ancoras \A...\z e [0-9] (nunca $ nem \d): "$" casa antes de uma quebra de linha final e \d casa digitos nao ASCII.
# Sempre usado com -cmatch.
$PadraoNomeBackup = '\A[0-9]{4}-[0-9]{2}-[0-9]{2}_[0-9]{6}(_[0-9]{1,3})?\z'
# Inclui COM/LPT com digitos sobrescritos (U+00B9/B2/B3, escapes do regex: nunca literais aqui) e CONIN$/CONOUT$.
$NomesReservados = '\A(con|prn|aux|nul|com[0-9\u00b9\u00b2\u00b3]|lpt[0-9\u00b9\u00b2\u00b3]|conin\$|conout\$)(\..*)?\z'

# ---- Estado (usado pela volta automatica) ----------------------------------------------------------------
$script:Raiz = $null
$script:ArqLog = $null
$script:LogPendente = New-Object System.Collections.Generic.List[string]
$script:Temp = $null
$script:Trava = $null
$script:Fase = 'preparando'           # 'preparando' -> 'alterando' -> 'pronto'; 'voltando' durante a volta
$script:Backup = $null                # copia criada (ou usada na recuperacao) por ESTA execucao
$script:BackupsProtegidos = @()       # copias que a poda nunca apaga (ex.: a de uma atualizacao interrompida)
$script:PastasTocadas = @()           # @{ Nome; Existia }
$script:ArquivosTocados = @()         # @{ Nome; Existia }
$script:PipRodou = $false
$script:VenvPy = $null
$script:VenvPyw = $null
$script:Porta = $PortaPadrao
$script:Opc = $null
$script:Desligou = $false             # o atualizador desligou um Proclamare que estava ligado
$script:BatTamanho = -1               # tamanho e SHA-256 do .bat que o cmd esta executando (ver Eh-Copia-Do-Bat)
$script:BatHash = $null
$script:OutrosZips = @()             # .zip de outro nome na pasta (avisados e ignorados)
$script:ZipsIgnorados = @()           # nomes dos ZIPs da pasta ignorados por serem velhos (nunca apagados)

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

# O unico texto de "traga o ZIP" (usado sem internet, sem release e com -SemRede).
function Texto-ColoqueZip {
    $dica = ''
    if ($script:OutrosZips.Count -gt 0) {
        $dica = ("    Se o arquivo " + $script:OutrosZips[0] + " for a versão nova do Proclamare, renomeie para`n" +
            "    'proclamare-nova.zip' e dê dois cliques de novo.`n")
    }
    return ($dica + "    Coloque o arquivo ZIP do Proclamare (o do pen drive) nesta pasta, ao lado do`n" +
        "    'Atualizar Proclamare.bat', e dê dois cliques nele de novo.`n" +
        '    Nada foi alterado: o Proclamare continua como estava.')
}

function Iniciar-Log {
    $pastaLogs = Join-Path $script:Raiz 'logs'
    try {
        if (-not [System.IO.Directory]::Exists($pastaLogs) -and -not [System.IO.File]::Exists($pastaLogs)) {
            [void][System.IO.Directory]::CreateDirectory($pastaLogs)
        }
        if (-not [System.IO.Directory]::Exists($pastaLogs)) { return }
        if (Eh-Link (New-Object System.IO.DirectoryInfo($pastaLogs))) { return }   # nunca grava atraves de um link
        $arq = Join-Path $pastaLogs 'atualizar.log'
        if ([System.IO.File]::Exists($arq) -and ((New-Object System.IO.FileInfo($arq)).Length -gt 1MB)) {
            [System.IO.File]::Copy($arq, $arq + '.1', $true)
            [System.IO.File]::Delete($arq)
        }
        $script:ArqLog = $arq
        foreach ($l in $script:LogPendente) {
            [System.IO.File]::AppendAllText($arq, $l + "`r`n", (New-Object System.Text.UTF8Encoding($false)))
        }
        $script:LogPendente.Clear()
    } catch { $script:ArqLog = $null }
}

# ---- Utilidades de arquivo -----------------------------------------------------------------------------
# Link = SO link simbolico ou juncao (ponto de montagem). Outros pontos de nova analise (ex.: os arquivos
# "so na nuvem" do OneDrive) sao arquivos/pastas comuns. Sem como saber o tipo: trata como link (seguro).
function Eh-Link($Info) {
    if ((([int]$Info.Attributes) -band [int][System.IO.FileAttributes]::ReparsePoint) -eq 0) { return $false }
    if ($null -eq $Info.PSObject.Properties['LinkType']) { return $true }
    $tipo = [string]$Info.LinkType
    return ($tipo -eq 'SymbolicLink' -or $tipo -eq 'Junction')
}

# N3: comparacao de nomes INVARIANTE de cultura (e sem diferenca de maiusculas): o -like do PowerShell segue a
# cultura do PC (em tr-TR 'CONFIG.JSON' nao casava com 'config.json*'). Tudo que decide seguranca passa por aqui.
function Casa-Nome([string]$Nome, [string]$Padrao) {
    return ($Nome.ToLowerInvariant() -clike $Padrao.ToLowerInvariant())
}
function Casa-Algum([string]$Nome, [string[]]$Padroes) {
    foreach ($p in $Padroes) { if (Casa-Nome $Nome $p) { return $true } }
    return $false
}
function Eh-Ignorado([string]$Nome) { return (Casa-Algum $Nome $PadroesIgnorados) }
function Eh-Dado([string]$Nome) { return (Casa-Algum $Nome $PadroesDeDados) }
function Eh-Protegida([string]$Nome) {
    foreach ($p in $PastasProtegidas) { if ([string]::Equals($Nome, $p, $OrdIC)) { return $true } }
    return $false
}
function Nova-Tabela { return (New-Object System.Collections.Hashtable ([System.StringComparer]::OrdinalIgnoreCase)) }

function Filhos([string]$Pasta) {
    if (-not [System.IO.Directory]::Exists($Pasta)) { return @() }
    return @((New-Object System.IO.DirectoryInfo($Pasta)).GetFileSystemInfos())
}

# Nome REAL de um caminho que existe ($null se nao existe). Um nome curto do Windows (8.3, ex.: CONFIG~1.JSO)
# abre o arquivo de nome longo (config.json): por isso o nome real e conferido antes de gravar ou apagar.
function Nome-Real([string]$Caminho) {
    $pai = [System.IO.Path]::GetDirectoryName($Caminho)
    $nome = [System.IO.Path]::GetFileName($Caminho)
    if (-not $pai -or -not $nome -or -not [System.IO.Directory]::Exists($pai)) { return $null }
    if ($nome.IndexOfAny([char[]]'*?') -ge 0) { throw "nome inválido: $nome" }
    $achados = @((New-Object System.IO.DirectoryInfo($pai)).GetFileSystemInfos($nome))
    if ($achados.Count -eq 0) { return $null }
    return $achados[0].Name
}
function Conferir-Alvo([string]$Caminho) {
    $real = Nome-Real $Caminho
    if ($null -eq $real) { return }
    $nome = [System.IO.Path]::GetFileName($Caminho)
    if (-not [string]::Equals($real, $nome, $OrdIC)) {
        throw "o nome $nome abre outro arquivo ($real, nome curto do Windows); por segurança nada foi feito nele"
    }
    if (Eh-Dado $real) { throw "$real é um arquivo de dados; por segurança ele não foi mexido" }
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
function Descrever-Link([string]$Caminho) {
    if ([System.IO.Directory]::Exists($Caminho)) { return "A pasta $Caminho é um link (atalho de pasta)" }
    return "O arquivo $Caminho é um link (atalho)"
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
    Conferir-Alvo $Para
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
    if (-not [System.IO.File]::Exists($Caminho)) { return }
    Conferir-Alvo $Caminho        # nunca apaga um arquivo de dados (nem por um nome curto 8.3)
    for ($i = 1; $i -le 6; $i++) {
        try {
            if (-not [System.IO.File]::Exists($Caminho)) { return }
            # So tira o somente-leitura se ele estiver ligado (como Copiar-Arquivo): um arquivo que nao deu para
            # apagar nao pode ficar com os outros atributos (oculto, sistema) trocados.
            $at = [System.IO.File]::GetAttributes($Caminho)
            if (($at -band [System.IO.FileAttributes]::ReadOnly) -ne 0) {
                [System.IO.File]::SetAttributes($Caminho, ($at -band (-bnot [System.IO.FileAttributes]::ReadOnly)))
            }
            [System.IO.File]::Delete($Caminho)
            return
        } catch {
            if ($i -eq 6) { throw }
            Start-Sleep -Milliseconds (300 * $i)
        }
    }
}

# Espelha $Origem em $Destino: copia tudo o que esta na origem e APAGA do destino o que nao esta nela.
# Nunca toca arquivos de dados nem __pycache__ e nunca entra num link. $Origem vazia/$null = apagar o
# conteudo de codigo de $Destino (e a pasta, se ficar vazia).
function Espelhar([string]$Origem, [string]$Destino) {
    $temOrigem = $Origem -and [System.IO.Directory]::Exists($Origem)
    if ([System.IO.Directory]::Exists($Destino) -or [System.IO.File]::Exists($Destino)) { Conferir-Alvo $Destino }
    if ($temOrigem -and -not [System.IO.Directory]::Exists($Destino)) {
        if ([System.IO.File]::Exists($Destino)) { Apagar-Arquivo $Destino }   # era arquivo, virou pasta
        [void][System.IO.Directory]::CreateDirectory($Destino)
    }
    if ([System.IO.Directory]::Exists($Destino) -and (Eh-Link (New-Object System.IO.DirectoryInfo($Destino)))) {
        throw ((Descrever-Link $Destino) + '; por segurança nada foi feito nela')
    }
    $vistos = Nova-Tabela   # nomes da origem (sem diferenca de maiusculas/minusculas, como o Windows)
    if ($temOrigem) {
        foreach ($f in (Filhos $Origem)) {
            if (Eh-Link $f) { throw "o pacote tem um link inesperado: $($f.FullName)" }
            $alvo = Join-Path $Destino $f.Name
            if ($f -is [System.IO.DirectoryInfo]) {
                if ([string]::Equals($f.Name, '__pycache__', $OrdIC)) { continue }
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
            if ($temOrigem -and [string]::Equals($f.Name, '__pycache__', $OrdIC)) { continue }
            Espelhar $null $f.FullName
        } else {
            # Espelhando: nao mexe em nada ignorado. Apagando uma pasta que saiu: so os DADOS ficam.
            if ($temOrigem) { if (Eh-Ignorado $f.Name) { continue } }
            elseif (Eh-Dado $f.Name) { continue }
            Apagar-Arquivo $f.FullName
        }
    }
    if (-not $temOrigem -and [System.IO.Directory]::Exists($Destino) -and (Filhos $Destino).Count -eq 0) {
        try { [System.IO.Directory]::Delete($Destino, $false) } catch { }
    }
}

# Hash do texto do arquivo com CRLF/LF iguais ($null se nao existe): o lock "mudou" so se o conteudo mudou.
function Hash-Texto([string]$Caminho) {
    if (-not [System.IO.File]::Exists($Caminho)) { return $null }
    $t = [System.IO.File]::ReadAllText($Caminho, [System.Text.Encoding]::UTF8).Replace("`r`n", "`n").TrimEnd()
    return (Hash-De-Bytes ([System.Text.Encoding]::UTF8.GetBytes($t)))
}

# N4: o requirements.lock e conferido por LISTA BRANCA, linha logica por linha logica (as continuacoes "\" juntam
# as linhas; comentarios "#" e linhas vazias sao ignorados). Cada linha logica tem de ser exatamente
#   nome[extras]==versao[ ; marcador] --hash=sha256:<64 hex> [--hash=sha256:<64 hex> ...]
# Nada de caminho, "@ url", opcao do pip (--index-url, -e, -r, -f, --global-option...) nem versao solta.
# O marcador so tem letras, numeros, espacos e . _ ' " < > = ! ~ , ( ) * - (sem "--" seguido, sem : / \ @,
# e nenhuma palavra comeca com "-": o pip leria " -e x" como opcao).
$LockMarcador = '(?:[A-Za-z0-9 ._''"<>=!~,()*]|(?<! )-(?!-))+'
$PadraoLinhaLock = '\A[A-Za-z0-9][A-Za-z0-9._-]*(\[[A-Za-z0-9._,-]+\])?==[0-9][A-Za-z0-9._!+-]*( ?; ?' + $LockMarcador + ')?( --hash=sha256:[0-9a-fA-F]{64})+\z'
# $null se ok, ou a linha logica recusada.
function Conferir-Lock([string]$Caminho) {
    $acum = ''
    $linhasLogicas = New-Object System.Collections.Generic.List[string]
    foreach ($bruta in [System.IO.File]::ReadAllLines($Caminho, [System.Text.Encoding]::UTF8)) {
        # Como o pip: so ASCII visivel e espaco (TAB, NBSP etc. recusados ANTES de normalizar) e "#" so e
        # comentario no comeco da linha ou depois de um espaco; qualquer outro "#" recusa o arquivo.
        if ($bruta -cmatch '[^\x20-\x7e]') { return $bruta }
        $linha = $bruta
        $h = $linha.IndexOf('#', $Ord)
        if ($h -ge 0) {
            if ($h -gt 0 -and $linha[$h - 1] -ne ' ') { return $bruta }
            $linha = $linha.Substring(0, $h)
        }
        $linha = $linha.Trim()
        if ($linha.EndsWith('\', $Ord)) {
            $acum += ' ' + $linha.Substring(0, $linha.Length - 1)
            continue
        }
        $acum += ' ' + $linha
        $acum = ($acum -replace '\s+', ' ').Trim()
        if ($acum) { $linhasLogicas.Add($acum) }
        $acum = ''
    }
    $acum = ($acum -replace '\s+', ' ').Trim()
    if ($acum) { $linhasLogicas.Add($acum) }   # arquivo terminado em "\"
    foreach ($l in $linhasLogicas) {
        if ($l -cnotmatch $PadraoLinhaLock) { return $l }
    }
    return $null
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

# Rodando ELEVADO (Executar como administrador) com o UAC ligado: recusa. Com o UAC desligado, todo programa
# de um administrador ja roda elevado e recusar deixaria a loja sem atualizar: segue.
function Deve-Recusar-Elevado([bool]$Elevado, [bool]$UacLigado) { return ($Elevado -and $UacLigado) }
function Esta-Elevado {
    try {
        $p = New-Object System.Security.Principal.WindowsPrincipal([System.Security.Principal.WindowsIdentity]::GetCurrent())
        return $p.IsInRole([System.Security.Principal.WindowsBuiltInRole]::Administrator)
    } catch { return $false }
}
function Uac-Ligado {
    try {
        $v = Get-ItemProperty -LiteralPath 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System' -Name EnableLUA -ErrorAction Stop
        return ([int]$v.EnableLUA -ne 0)
    } catch { return $true }
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

# True se ALGUM programa escuta na porta (mesmo sem responder 200: um servidor travado segura a porta). Le a
# tabela de portas do Windows (sem conectar: no Windows, conectar numa porta fechada do loopback demora ~2 s).
function Porta-Ocupada {
    try {
        $ouvintes = [System.Net.NetworkInformation.IPGlobalProperties]::GetIPGlobalProperties().GetActiveTcpListeners()
        foreach ($o in $ouvintes) { if ($o.Port -eq $script:Porta) { return $true } }
        return $false
    } catch {
        return (Servidor-Responde 1500)
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

# Desliga pelo proprio lancador da pasta (--parar so encerra o PID validado por caminho + hora de criacao) e
# espera a porta fechar. Devolve $true se o Proclamare ficou desligado.
function Parar-Proclamare {
    $arqPid = Join-Path $script:Raiz 'logs\proclamare.pid'
    if (-not [System.IO.File]::Exists($arqPid) -and -not (Porta-Ocupada)) {
        Ok 'o Proclamare já estava desligado.'
        return $true
    }
    $pyw = Join-Path $script:Raiz 'Proclamare.pyw'
    # python.exe (e nao pythonw) so para guardar a resposta do lancador no log; PROCLAMARE_NO_BROWSER=1 troca
    # as caixas de mensagem por texto. A pasta de trabalho e neutra (o lancador se acha pelo proprio caminho).
    $r = Rodar-Processo $script:VenvPy @('-I', $pyw, '--parar') 40 @{ PROCLAMARE_NO_BROWSER = '1' }
    Registrar ("lancador --parar: codigo $($r.Codigo); " + (Ultimas-Linhas ($r.Saida + "`n" + $r.Erro) 5))
    $fim = (Get-Date).AddSeconds($EsperaParadaSeg)
    while ((Get-Date) -lt $fim -and (Porta-Ocupada)) { Start-Sleep -Milliseconds 500 }
    if (Porta-Ocupada) { return $false }
    $script:Desligou = $true
    Ok 'Proclamare desligado.'
    return $true
}

# Liga como o atalho da Area de Trabalho (pythonw -I Proclamare.pyw), mas com PROCLAMARE_NO_BROWSER=1 para
# o lancador nunca abrir uma caixa de mensagem que prenda a trava dele; o painel e aberto aqui no fim.
# Process.Start (sem Start-Process): "[" e "]" no caminho da pasta nao viram curinga. Nao espera o lancador.
function Ligar-Proclamare([int]$Segundos) {
    $pyw = Join-Path $script:Raiz 'Proclamare.pyw'
    $proc = $null
    try {
        $psi = New-Object System.Diagnostics.ProcessStartInfo
        $psi.FileName = $script:VenvPyw
        $psi.Arguments = '-I ' + (Citar-Argumento $pyw)
        $psi.UseShellExecute = $false
        $psi.CreateNoWindow = $true
        $psi.WorkingDirectory = $script:Raiz
        $psi.EnvironmentVariables['PROCLAMARE_NO_BROWSER'] = '1'
        $proc = [System.Diagnostics.Process]::Start($psi)
    } catch {
        Registrar ('falha ao chamar o lancador: ' + $_.Exception.Message)
    }
    $okStatus = Esperar-Status $Segundos
    # O lancador termina sozinho (ate ~35 s); espera-o para a trava dele nao atrapalhar uma volta de versao.
    if ($proc -and -not $okStatus) { try { [void]$proc.WaitForExit(40000) } catch { } }
    if ($proc) { try { $proc.Dispose() } catch { } }
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
        if ($texto -match 'servidor local \(v([0-9]+(?:\.[0-9]+){1,2})') { return $Matches[1] }
        if ($texto -match '\bv([0-9]+\.[0-9]+(?:\.[0-9]+)?)\b') { return $Matches[1] }
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

# ---- Fonte: ZIP ao lado do .bat ou a ultima release do GitHub ------------------------------------------------
# Testes automaticos: PROCLAMARE_ATUALIZAR_TESTE_GITHUB=http://127.0.0.1:<porta> troca OS DOIS hosts por um
# servidor falso local (so loopback; qualquer outro valor e recusado). Sem a variavel: HTTPS nos hosts fixos.
# N1 (Red Team): a variavel so VALE se existir o arquivo comum (nao link) logs\teste-github.token DENTRO da pasta
# de instalacao: quem o cria ja controla o codigo. Sem o arquivo a variavel e IGNORADA (e o caminho real, HTTPS nos
# hosts fixos, segue; o fluxo registra o aviso no log). Escolha: ignorar e nao recusar, para que uma variavel
# esquecida no ambiente nunca impeca a atualizacao de quem nao sabe de nada disso.
function Teste-Github-Liberado {
    if (-not $script:Raiz) { return $false }
    $pastaLogs = Join-Path $script:Raiz 'logs'
    $token = Join-Path $pastaLogs 'teste-github.token'
    try {
        if (-not [System.IO.Directory]::Exists($pastaLogs) -or (Eh-Link (New-Object System.IO.DirectoryInfo($pastaLogs)))) { return $false }
        if (-not [System.IO.File]::Exists($token)) { return $false }
        return (-not (Eh-Link (New-Object System.IO.FileInfo($token))))
    } catch { return $false }
}
function Url-De-Teste {
    $u = [string]$env:PROCLAMARE_ATUALIZAR_TESTE_GITHUB
    if (-not $u) { return $null }
    if (-not (Teste-Github-Liberado)) { return $null }
    if ($u -cmatch '\Ahttp://127\.0\.0\.1:[0-9]{1,5}\z') { return $u }
    Recusar 'A variável PROCLAMARE_ATUALIZAR_TESTE_GITHUB só aceita http://127.0.0.1:<porta>. Nada foi alterado.'
}

function Texto-SemInternet([string]$Detalhe) {
    Registrar ('internet: ' + $Detalhe)
    return ("Não consegui falar com o GitHub (sem internet, ou a rede bloqueia o endereço).`n" + (Texto-ColoqueZip))
}

# Abre uma requisicao GET num host FIXO, sem seguir redirecionamento. Devolve a resposta (qualquer status).
function Abrir-Url([string]$Url, [string]$HostFixo, [string]$Aceitar) {
    $teste = Url-De-Teste
    $esquema = if ($teste) { 'http' } else { 'https' }
    $hostEsperado = if ($teste) { '127.0.0.1' } else { $HostFixo }
    $uri = [System.Uri]$Url
    if ($uri.Scheme -ne $esquema -or -not [string]::Equals($uri.Host, $hostEsperado, $OrdIC)) {
        Recusar 'Endereço de download inválido. Nada foi alterado.'
    }
    try {   # TLS 1.2 ou 1.3 (nunca SSL3/TLS 1.0/1.1)
        [System.Net.ServicePointManager]::SecurityProtocol = [System.Net.SecurityProtocolType]::Tls12 -bor ([System.Net.SecurityProtocolType]12288)
    } catch {
        [System.Net.ServicePointManager]::SecurityProtocol = [System.Net.SecurityProtocolType]::Tls12
    }
    $req = [System.Net.HttpWebRequest][System.Net.WebRequest]::Create($uri)
    $req.UserAgent = $UserAgent
    $req.Accept = $Aceitar
    $req.Timeout = 30000
    $req.ReadWriteTimeout = 30000
    $req.AllowAutoRedirect = $false     # host fixo: nenhum desvio para outro endereco
    $req.KeepAlive = $false
    # N6: usa o proxy do sistema, mas SEM mandar as credenciais do Windows do usuario (NTLM) a quem pedir:
    # um proxy falso na rede (WPAD) as capturaria. Proxy que exige senha = a conexao falha e cai no "sem internet".
    if ($teste) { $req.Proxy = $null } else { $req.Proxy = [System.Net.WebRequest]::GetSystemWebProxy() }
    try {
        $resp = [System.Net.HttpWebResponse]$req.GetResponse()
    } catch {
        $ex = $_.Exception
        while ($ex -and -not ($ex -is [System.Net.WebException])) { $ex = $ex.InnerException }
        if ($ex -and $ex.Response) { $resp = [System.Net.HttpWebResponse]$ex.Response }
        else { Recusar (Texto-SemInternet $_.Exception.Message) }
    }
    $final = $resp.ResponseUri
    if ($final.Scheme -ne $esquema -or -not [string]::Equals($final.Host, $hostEsperado, $OrdIC)) {
        $resp.Close()
        Recusar 'O download foi desviado para outro endereço; por segurança ele foi cancelado. Nada foi alterado.'
    }
    $st = [int]$resp.StatusCode
    if ($st -ge 300 -and $st -lt 400) {
        $resp.Close()
        Recusar 'O GitHub mandou ir para outro endereço; por segurança o download foi cancelado. Nada foi alterado.'
    }
    return $resp
}

# Le o corpo da resposta com teto de bytes e de tempo (para um arquivo novo, ou em memoria se $Destino vazio).
function Ler-Corpo($Resp, [long]$Max, [string]$Destino) {
    if ($Resp.ContentLength -gt $Max) { Recusar 'A resposta do GitHub é grande demais; por segurança o download foi cancelado. Nada foi alterado.' }
    $entrada = $null
    $saida = $null
    try {
        $entrada = $Resp.GetResponseStream()
        $saida = if ($Destino) { [System.IO.File]::Open($Destino, 'CreateNew', 'Write', 'None') } else { New-Object System.IO.MemoryStream }
        $buf = New-Object byte[] 65536
        $total = [long]0
        $relogio = [System.Diagnostics.Stopwatch]::StartNew()
        while ($true) {
            try { $n = $entrada.Read($buf, 0, $buf.Length) } catch {
                Recusar ("O download parou no meio (internet instável).`n" + (Texto-ColoqueZip))
            }
            if ($n -le 0) { break }
            $total += $n
            if ($total -gt $Max) { Recusar 'O arquivo recebido é maior que o esperado; por segurança o download foi cancelado. Nada foi alterado.' }
            $saida.Write($buf, 0, $n)
            if ($relogio.Elapsed.TotalSeconds -gt $DownloadMaxSeg) {
                Recusar ("O download está demorando demais e foi cancelado.`n" + (Texto-ColoqueZip))
            }
        }
        if (-not $Destino) { return , $saida.ToArray() }
        return $total
    } finally {
        if ($saida) { $saida.Dispose() }
        if ($entrada) { $entrada.Dispose() }
        $Resp.Close()
    }
}

# Ultima release publicada: devolve a tag (vX.Y[.Z]) ja validada.
function Ler-Release {
    $teste = Url-De-Teste
    $base = if ($teste) { $teste } else { "https://$HostApi" }
    $resp = Abrir-Url "$base/repos/$RepoGitHub/releases/latest" $HostApi 'application/vnd.github+json'
    $st = [int]$resp.StatusCode
    if ($st -eq 404) {
        $resp.Close()
        Recusar ("Ainda não há nenhuma versão publicada do Proclamare no GitHub.`n" + (Texto-ColoqueZip))
    }
    if ($st -ne 200) {
        $resp.Close()
        Recusar ("O GitHub respondeu com o código $st.`n" + (Texto-ColoqueZip))
    }
    $bytes = Ler-Corpo $resp $ReleaseMaxBytes ''
    try {
        $info = [System.Text.Encoding]::UTF8.GetString($bytes) | ConvertFrom-Json
        $tag = [string]$info.tag_name
    } catch { $tag = '' }
    if ($tag -cnotmatch $PadraoTag) {
        Registrar ('tag recusada: ' + ($tag -replace '[^\x20-\x7e]', '?'))
        Recusar 'A versão publicada no GitHub tem um nome inesperado; por segurança nada foi baixado. Nada foi alterado.'
    }
    return $tag
}

function Baixar-Tag([string]$Tag, [string]$Destino) {
    $teste = Url-De-Teste
    $base = if ($teste) { $teste } else { "https://$HostPacote" }
    $resp = Abrir-Url "$base/$RepoGitHub/zip/refs/tags/$Tag" $HostPacote 'application/zip'
    $st = [int]$resp.StatusCode
    if ($st -ne 200) {
        $resp.Close()
        Recusar ("O GitHub respondeu com o código $st ao baixar a versão $Tag.`n" + (Texto-ColoqueZip))
    }
    $total = Ler-Corpo $resp $DownloadMaxBytes $Destino
    Ok ('baixado: {0:N1} MB' -f ($total / 1MB))
}

# ---- Conferir e extrair o pacote -----------------------------------------------------------------------
# Nome de entrada do ZIP normalizado ('/' como separador) ou $null se for suspeito.
function Nome-Seguro([string]$Bruto) {
    $n = $Bruto.Replace('\', '/')
    if (-not $n) { return $null }
    if ($n.StartsWith('/', $Ord) -or $n.Contains(':')) { return $null }    # absoluto, letra de disco ou fluxo alternativo
    if ($n -match '[\p{Cf}\p{Co}]') { return $null }                        # invisiveis (bidi, largura zero) e uso privado
    $corpo = $n.TrimEnd('/')
    if (-not $corpo) { return $null }
    foreach ($seg in $corpo.Split('/')) {
        if ($seg -eq '' -or $seg -eq '.' -or $seg -eq '..') { return $null }
        if ($seg -match '[<>"|?*\x00-\x1f]') { return $null }
        if ($seg.EndsWith('.', $Ord) -or $seg.EndsWith(' ', $Ord)) { return $null }
        if ($seg -match $NomesReservados) { return $null }
    }
    return $n
}
# Nome curto do Windows (8.3), ex.: CONFIG~1.JSO: abriria outro arquivo, de nome longo.
function Parece-Nome-Curto([string]$Seg) { return ($Seg -match '~[0-9]') }

# Devolve @{ Raiz = pasta extraida; Data = data mais nova do pacote }.
function Extrair-Pacote([string]$ArquivoZip, [string]$Destino) {
    Add-Type -AssemblyName System.IO.Compression
    $fs = $null
    $za = $null
    $nomeZip = [System.IO.Path]::GetFileName($ArquivoZip)
    # A pasta de destino tem de ser NOVA (nunca se mistura um pacote com sobras de outro).
    if ([System.IO.Directory]::Exists($Destino) -or [System.IO.File]::Exists($Destino)) {
        Recusar 'A pasta temporária do pacote já existia; por segurança não vou misturar. Tente de novo. Nada foi alterado.'
    }
    try {
        try { $fs = [System.IO.File]::Open($ArquivoZip, 'Open', 'Read', 'Read') } catch {
            Recusar "Não consegui abrir o arquivo $nomeZip (ele está aberto em outro programa?). Nada foi alterado."
        }
        try {
            $za = New-Object System.IO.Compression.ZipArchive($fs, [System.IO.Compression.ZipArchiveMode]::Read, $true)
            $entradas = @($za.Entries)
        } catch {
            Recusar ("O arquivo $nomeZip não é um ZIP válido (incompleto ou estragado).`n" +
                '    Copie o ZIP de novo do pen drive (ou baixe de novo) e tente outra vez. Nada foi alterado.')
        }
        if ($entradas.Count -gt $ZipMaxEntradasTotal) { Recusar "O ZIP tem arquivos demais ($($entradas.Count)); não parece o Proclamare. Nada foi alterado." }

        # 1) Todos os nomes conferidos ANTES de extrair qualquer coisa.
        $arquivos = Nova-Tabela       # nome normalizado (minusculas) -> entrada
        foreach ($e in $entradas) {
            $n = Nome-Seguro $e.FullName
            if (-not $n) { Recusar "O ZIP traz um nome de arquivo perigoso ($($e.FullName)); por segurança ele foi recusado. Nada foi alterado." }
            foreach ($seg in $n.TrimEnd('/').Split('/')) {
                if (Parece-Nome-Curto $seg) { Recusar "O ZIP traz um nome perigoso ($($n): parece um nome curto do Windows); por segurança ele foi recusado. Nada foi alterado." }
            }
            $ext = 0
            try { $ext = [int]$e.ExternalAttributes } catch { $ext = 0 }
            if (((($ext -shr 16) -band 0xF000) -eq 0xA000) -or (($ext -band 0x400) -ne 0)) {
                Recusar "O ZIP traz um link ($($e.FullName)); por segurança ele foi recusado. Nada foi alterado."
            }
            if ($n.EndsWith('/', $Ord)) { continue }
            $chave = $n.ToLowerInvariant()
            if ($arquivos.ContainsKey($chave)) { Recusar "O ZIP traz o mesmo arquivo duas vezes ($n); por segurança ele foi recusado. Nada foi alterado." }
            $arquivos[$chave] = $e
        }

        # 2) Uma unica raiz com os arquivos-chave do Proclamare (fora de pastas protegidas, ex.: backups\...).
        $raizes = @()
        foreach ($k in @($arquivos.Keys)) {
            if ($k -ne 'server.py' -and -not $k.EndsWith('/server.py', $Ord)) { continue }
            $pre = $k.Substring(0, $k.Length - 'server.py'.Length)
            $protegida = $false
            foreach ($seg in $pre.TrimEnd('/').Split('/')) { if ($seg -and (Eh-Protegida $seg)) { $protegida = $true } }
            if ($protegida) { continue }
            if ($arquivos.ContainsKey($pre + 'proclamare.pyw') -and $arquivos.ContainsKey($pre + 'static/app.js') -and
                $arquivos.ContainsKey($pre + 'requirements.lock')) { $raizes += $pre }
        }
        if ($raizes.Count -eq 0) {
            Recusar ("Este ZIP não é do Proclamare (faltam server.py, Proclamare.pyw, static\app.js ou requirements.lock).`n" +
                '    Confira se copiou o arquivo certo. Nada foi alterado.')
        }
        if ($raizes.Count -gt 1) { Recusar 'Este ZIP tem mais de uma cópia do Proclamare dentro; use um ZIP com uma só. Nada foi alterado.' }
        $pre = [string]$raizes[0]

        # 3) O que sera extraido: so a raiz do Proclamare, sem pastas protegidas. Nome de DADO em qualquer
        #    parte do caminho = pacote recusado (um ZIP nunca traz config.json, contas.json etc.).
        $escolhidas = @()
        $soma = [long]0
        $dataMaisNova = $null
        foreach ($k in @($arquivos.Keys)) {
            if (-not $k.StartsWith($pre, $Ord)) { continue }
            $e = $arquivos[$k]
            $rel = (Nome-Seguro $e.FullName).Substring($pre.Length)
            $segs = $rel.Split('/')
            if (Eh-Protegida $segs[0]) { continue }
            foreach ($seg in $segs) {
                if (Eh-Dado $seg) { Recusar "O ZIP traz um arquivo de dados ($rel); por segurança ele foi recusado (um pacote nunca traz dados). Nada foi alterado." }
            }
            if (@($segs | Where-Object { [string]::Equals($_, '__pycache__', $OrdIC) }).Count -gt 0) { continue }
            if (Eh-Ignorado $segs[-1]) { continue }
            if ($e.Length -gt $ZipMaxArquivoBytes) { Recusar "O ZIP traz um arquivo grande demais ($rel). Nada foi alterado." }
            $soma += $e.Length
            $escolhidas += , @($e, $rel)
            try {
                $d = $e.LastWriteTime.DateTime
                if ($d.Year -ge 2000 -and ($segs.Count -eq 1 -or [string]::Equals($segs[0], 'static', $OrdIC)) -and ($null -eq $dataMaisNova -or $d -gt $dataMaisNova)) { $dataMaisNova = $d }
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
            if (-not $alvo.StartsWith($base, $OrdIC)) {
                Recusar "O ZIP tentou gravar fora da pasta temporária ($rel). Nada foi alterado."
            }
            if ($alvo.Length -gt $CaminhoMax) {
                Recusar ("O caminho da pasta temporária do Windows é comprido demais para extrair $rel.`n" +
                    '    Nada foi alterado. Avise o suporte e mande o arquivo logs\atualizar.log.')
            }
            if ($e.Length -eq $script:BatTamanho) {
                # Mesmo tamanho do .bat em uso: confere em memoria ANTES de gravar (ver Eh-Copia-Do-Bat),
                # lendo no maximo o tamanho declarado + 1 byte.
                $ms = New-Object System.IO.MemoryStream
                $in = $e.Open()
                try {
                    $lidos = [long]0
                    while (($n = $in.Read($buf, 0, $buf.Length)) -gt 0) {
                        $lidos += $n
                        if ($lidos -gt $e.Length) { Recusar "O arquivo $rel é maior do que o declarado no ZIP. Nada foi alterado." }
                        $ms.Write($buf, 0, $n)
                    }
                } catch [System.IO.InvalidDataException] {
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
function Fazer-Backup([string[]]$PastasDoPacote, [string[]]$ArquivosDoPacote) {
    $pastaBackups = Join-Path $script:Raiz 'backups'
    if ([System.IO.Directory]::Exists($pastaBackups) -and (Eh-Link (New-Object System.IO.DirectoryInfo($pastaBackups)))) {
        Recusar 'A pasta backups é um link (atalho de pasta); por segurança não vou gravar nela. Nada foi alterado.'
    }
    [void][System.IO.Directory]::CreateDirectory($pastaBackups)
    $nome = (Get-Date).ToString('yyyy-MM-dd_HHmmss')
    $destino = Join-Path $pastaBackups $nome
    $i = 2
    while ([System.IO.Directory]::Exists($destino) -or [System.IO.File]::Exists($destino)) { $destino = Join-Path $pastaBackups ($nome + '_' + $i); $i++ }
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
            '    (disco cheio ou arquivo em uso?). Nada foi alterado.')
    }
    return $destino
}

# So poda depois de ESTA execucao ter criado uma copia; nunca apaga a desta execucao nem as protegidas.
function Podar-Backups {
    if (-not $script:Backup -or -not $script:Raiz) { return }
    $pastaBackups = Join-Path $script:Raiz 'backups'
    if (-not [System.IO.Directory]::Exists($pastaBackups) -or (Eh-Link (New-Object System.IO.DirectoryInfo($pastaBackups)))) { return }
    $nossas = @(Filhos $pastaBackups | Where-Object {
            $_ -is [System.IO.DirectoryInfo] -and -not (Eh-Link $_) -and $_.Name -cmatch $PadraoNomeBackup } |
        Sort-Object Name -Descending)
    if ($nossas.Count -le $BackupsManter) { return }
    foreach ($velha in $nossas[$BackupsManter..($nossas.Count - 1)]) {
        if ([string]::Equals($velha.FullName, $script:Backup, $OrdIC)) { continue }
        if (@($script:BackupsProtegidos | Where-Object { [string]::Equals($_, $velha.FullName, $OrdIC) }).Count -gt 0) { continue }
        if (Achar-Link $velha.FullName) { continue }   # nunca segue um link ao apagar
        try { [System.IO.Directory]::Delete($velha.FullName, $true); Registrar "cópia antiga apagada: $($velha.Name)" } catch { }
    }
}

# ---- Plano da instalacao (logs\atualizar.pendente) -------------------------------------------------------
# N2(d): logs\ que e link (atalho de pasta) nunca e usada para ler nem gravar a trava ou o plano.
function Logs-Eh-Link {
    $pastaLogs = Join-Path $script:Raiz 'logs'
    if (-not [System.IO.Directory]::Exists($pastaLogs)) { return $false }
    return (Eh-Link (New-Object System.IO.DirectoryInfo($pastaLogs)))
}
function Arq-Pendente {
    if (Logs-Eh-Link) { throw 'a pasta logs é um link (atalho de pasta); por segurança não uso o registro da atualização' }
    return (Join-Path $script:Raiz 'logs\atualizar.pendente')
}

function Gravar-Pendente {
    $obj = [ordered]@{
        backup = [System.IO.Path]::GetFileName($script:Backup)
        pip = [bool]$script:PipRodou
        pastas = @($script:PastasTocadas | ForEach-Object { [ordered]@{ nome = $_.Nome; existia = [bool]$_.Existia } })
        arquivos = @($script:ArquivosTocados | ForEach-Object { [ordered]@{ nome = $_.Nome; existia = [bool]$_.Existia } })
    }
    $arq = Arq-Pendente
    $tmp = $arq + '.novo'
    [System.IO.File]::WriteAllText($tmp, ($obj | ConvertTo-Json -Depth 4), (New-Object System.Text.UTF8Encoding($false)))
    [System.IO.File]::Copy($tmp, $arq, $true)
    [System.IO.File]::Delete($tmp)
}
# 3 tentativas com pausa (antivirus/indexador seguram arquivo recem-gravado), como Apagar-Arquivo.
function Remover-Pendente {
    if (Logs-Eh-Link) { Registrar 'pendente: a pasta logs é um link; não mexi no registro'; return }   # a causa nao passa com nova tentativa
    for ($i = 1; $i -le 3; $i++) {
        try {
            $arq = Arq-Pendente
            if ([System.IO.File]::Exists($arq)) { [System.IO.File]::Delete($arq) }
            return
        } catch {
            if ($i -eq 3) { Registrar ('pendente: ' + $_.Exception.Message); return }
            Start-Sleep -Milliseconds (300 * $i)
        }
    }
}
# Nome de pasta/arquivo de primeiro nivel vindo do plano: so um nome simples, de codigo.
function Nome-De-Plano-Ok($Nome) {
    $n = [string]$Nome
    if (-not $n -or $n -eq '.' -or $n -eq '..' -or $n.IndexOfAny([char[]]'\/:*?"<>|') -ge 0) { return $false }
    if ((Eh-Protegida $n) -or (Eh-Ignorado $n) -or (Parece-Nome-Curto $n)) { return $false }
    return $true
}

# N2(c): o plano e um arquivo em logs\ e nao e de confianca: na recuperacao so entram nomes que estao na copia
# ou que sao de codigo conhecido ($PastasDeCodigo / $PadroesCodigoRaiz). Outro nome (ex.: MeusArquivos) e ignorado.
function Nome-De-Pasta-Do-Plano-Ok([string]$Nome, [string]$Copia) {
    foreach ($p in $PastasDeCodigo) { if ([string]::Equals($Nome, $p, $OrdIC)) { return $true } }
    return [System.IO.Directory]::Exists((Join-Path $Copia $Nome))
}
function Nome-De-Arquivo-Do-Plano-Ok([string]$Nome, [string]$Copia) {
    if (Casa-Algum $Nome $PadroesCodigoRaiz) { return $true }
    return [System.IO.File]::Exists((Join-Path $Copia $Nome))
}

# A ultima atualizacao parou no meio (falta de luz, janela fechada): poe de volta a versao anterior.
function Recuperar-Pendente {
    $arq = Arq-Pendente
    if (-not [System.IO.File]::Exists($arq)) { return }
    Passo 'A última atualização foi interrompida; pondo de volta a versão anterior...'
    $plano = $null
    try { $plano = [System.IO.File]::ReadAllText($arq, [System.Text.Encoding]::UTF8) | ConvertFrom-Json } catch { }
    $copia = $null
    if ($plano -and ([string]$plano.backup) -cmatch $PadraoNomeBackup) {
        $c = Join-Path $script:Raiz ('backups\' + [string]$plano.backup)
        if ([System.IO.Directory]::Exists($c) -and -not (Achar-Link (Join-Path $script:Raiz 'backups'))) { $copia = $c }
    }
    if (-not $copia) {
        Aviso 'não achei a cópia daquela atualização; sigo com o que está na pasta.'
        Remover-Pendente
        return
    }
    if (-not (Parar-Proclamare)) {
        Recusar ("O Proclamare não desligou (talvez ele tenha sido ligado por outro jeito, por exemplo numa janela preta).`n" +
            "    Feche essa janela ou use 'Desligar Proclamare' no Menu Iniciar e tente de novo.")
    }
    $script:Backup = $copia
    # Incoerente = "nao existia" mas o nome ESTA na copia de seguranca (a copia so tem o que existia): ignorado.
    $script:PastasTocadas = @(@($plano.pastas) | Where-Object { $_ -and (Nome-De-Plano-Ok $_.nome) -and (Nome-De-Pasta-Do-Plano-Ok $_.nome $copia) -and
            ([bool]$_.existia -or -not [System.IO.Directory]::Exists((Join-Path $copia ([string]$_.nome)))) } | ForEach-Object { @{ Nome = [string]$_.nome; Existia = [bool]$_.existia } })
    $script:ArquivosTocados = @(@($plano.arquivos) | Where-Object { $_ -and (Nome-De-Plano-Ok $_.nome) -and (Nome-De-Arquivo-Do-Plano-Ok $_.nome $copia) -and
            ([bool]$_.existia -or -not [System.IO.File]::Exists((Join-Path $copia ([string]$_.nome)))) } | ForEach-Object { @{ Nome = [string]$_.nome; Existia = [bool]$_.existia } })
    $script:PipRodou = [bool]$plano.pip
    $script:Fase = 'voltando'
    if (-not (Voltar-Codigo-Com-Nova-Tentativa)) {
        $e = New-Object System.Exception 'a volta da atualização interrompida falhou'
        $e.Data['proclamare'] = 'volta'
        throw $e
    }
    Remover-Pendente
    $script:BackupsProtegidos += $copia
    $script:Backup = $null
    $script:PastasTocadas = @()
    $script:ArquivosTocados = @()
    $script:PipRodou = $false
    $script:Fase = 'preparando'
    Ok 'a versão anterior está de volta; agora sigo com a atualização.'
}

# ---- Instalar e voltar ------------------------------------------------------------------------------------
# O plano (o que sera tocado e se ja existia) e montado ANTES de mexer, para a volta saber o que desfazer.
function Planejar-Instalacao([string]$Novo) {
    $script:PastasTocadas = @()
    $script:ArquivosTocados = @()
    foreach ($f in (Filhos $Novo)) {
        if ($f -is [System.IO.DirectoryInfo]) {
            if (Eh-Protegida $f.Name) { continue }
            $script:PastasTocadas += , @{ Nome = $f.Name; Existia = [System.IO.Directory]::Exists((Join-Path $script:Raiz $f.Name)) }
        } else {
            if ((Eh-Ignorado $f.Name) -or (Eh-Protegida $f.Name)) { continue }
            $script:ArquivosTocados += , @{ Nome = $f.Name; Existia = [System.IO.File]::Exists((Join-Path $script:Raiz $f.Name)) }
        }
    }
}

function Instalar-Codigo([string]$Novo) {
    $script:Fase = 'alterando'
    foreach ($p in $script:PastasTocadas) { Espelhar (Join-Path $Novo $p.Nome) (Join-Path $script:Raiz $p.Nome) }
    foreach ($a in $script:ArquivosTocados) { Copiar-Arquivo (Join-Path $Novo $a.Nome) (Join-Path $script:Raiz $a.Nome) }
}

function Rodar-Pip([string]$Lock) {
    $r = Rodar-Processo $script:VenvPy @('-I', '-m', 'pip', 'install', '--isolated', '--require-hashes', '--only-binary', ':all:',
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
            # N2(b): "existia" sem a pasta na copia e FALHA; nunca cai no modo apagar (origem vazia).
            if ($p.Existia -and -not [System.IO.Directory]::Exists($origem)) { throw 'a pasta não está na cópia de segurança' }
            Espelhar $origem (Join-Path $script:Raiz $p.Nome)
        } catch { $falhas++; Registrar "volta: falhou a pasta $($p.Nome): $($_.Exception.Message)" }
    }
    foreach ($a in $script:ArquivosTocados) {
        try {
            $alvo = Join-Path $script:Raiz $a.Nome
            $copia = Join-Path $b $a.Nome
            if ($a.Existia -and [System.IO.File]::Exists($copia)) { Copiar-Arquivo $copia $alvo }
            elseif ($a.Existia) { Registrar "volta: $($a.Nome) não tem cópia (fica o arquivo novo; ex.: o próprio atualizador em uso)" }
            else { Apagar-Arquivo $alvo }
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

# Uma nova tentativa depois de 5 s (antivirus ou indexador segurando um arquivo). Nunca engole a excecao: registra.
function Voltar-Codigo-Com-Nova-Tentativa {
    $ok = $false
    try { $ok = [bool](@(Voltar-Codigo)[-1]) } catch { Registrar ('volta: ' + $_.ToString()) }
    if ($ok) { return $true }
    Aviso 'alguns arquivos não voltaram; tentando de novo em 5 segundos...'
    Start-Sleep -Seconds 5
    try { $ok = [bool](@(Voltar-Codigo)[-1]) } catch { Registrar ('volta (2ª tentativa): ' + $_.ToString()) }
    return $ok
}

function Dizer-VoltaFalhou {
    Dizer ("`nATENÇÃO: não consegui voltar todos os arquivos da versão anterior.`n" +
        "    A cópia do programa antigo está em: $($script:Backup)`n" +
        "    Seus dados NÃO foram mexidos. Não ligue o Proclamare: peça ajuda ao suporte e mande o arquivo`n" +
        '    logs\atualizar.log. Ao rodar o atualizador de novo, ele tenta pôr a versão anterior de volta primeiro.') 'Red'
}

# Volta a versao anterior e (sem -SemReiniciar) liga de novo. Devolve o codigo de saida (1 ou 3).
function Voltar-Versao([string]$Motivo, $Opc) {
    $script:Fase = 'voltando'
    Dizer "`nERRO: $Motivo" 'Red'
    Passo 'Voltando para a versão anterior (cópia de segurança)...'
    # Sempre pede ao lancador: uma versao nova lenta pode estar viva sem responder ainda (segurando a porta).
    try { [void](Parar-Proclamare) } catch { Registrar ('parar (volta): ' + $_.ToString()) }
    if (-not (Voltar-Codigo-Com-Nova-Tentativa)) {
        Dizer-VoltaFalhou
        return 3
    }
    Remover-Pendente
    Ok 'arquivos da versão anterior de volta.'
    # Se o lancador NOVO nao conseguiu soltar a porta, o lancador da copia (agora de volta na pasta) tenta.
    if (Porta-Ocupada) { try { [void](Parar-Proclamare) } catch { Registrar ('parar (copia): ' + $_.ToString()) } }
    if ($Opc.SemReiniciar) {
        $script:Fase = 'pronto'
        Dizer "`nA atualização NÃO foi feita: o Proclamare voltou para a versão anterior. Seus dados foram mantidos." 'Yellow'
        return 1
    }
    Passo 'Ligando a versão anterior...'
    # A fase so vira 'pronto' DEPOIS de religar: uma excecao aqui cai no ramo 'voltando' (aviso de volta), nunca
    # no "Nada foi alterado" do ramo 'preparando'.
    # Os arquivos JA voltaram: uma excecao ao ligar vale como "nao respondeu ao ligar", nunca como "nao voltou".
    $ligou = $false
    try { $ligou = [bool](@(Ligar-Proclamare $Opc.EsperaInicio)[-1]) } catch { Registrar ('ligar (volta): ' + $_.ToString()) }
    $script:Fase = 'pronto'
    if ($ligou) {
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
        if (-not $raizDisco -or $raizDisco.StartsWith('\\', $Ord)) { return $null }
        return [long]((New-Object System.IO.DriveInfo($raizDisco)).AvailableFreeSpace / 1MB)
    } catch { return $null }
}

# Sobras de execucoes anteriores interrompidas (so pastas com o NOSSO prefixo e com mais de 1 dia).
function Limpar-Temporarios-Antigos {
    try {
        $limite = (Get-Date).AddDays(-1)
        foreach ($d in (Filhos ([System.IO.Path]::GetTempPath()))) {
            if ($d -is [System.IO.DirectoryInfo] -and (Casa-Nome $d.Name 'proclamare-atualizar-*') -and $d.LastWriteTime -lt $limite -and
                -not (Eh-Link $d) -and -not (Achar-Link $d.FullName)) {
                try { [System.IO.Directory]::Delete($d.FullName, $true) } catch { }
            }
        }
    } catch { }
}

# Trava "uma atualizacao por vez" em logs\atualizar.lock (some sozinha se a janela fechar).
function Abrir-Trava {
    if (Logs-Eh-Link) {
        Recusar ("A pasta logs do Proclamare é um link (atalho de pasta); por segurança não vou usá-la.`n" +
            '    Troque-a por uma pasta comum. Nada foi alterado.')
    }
    $arq = Join-Path $script:Raiz 'logs\atualizar.lock'
    try {
        $script:Trava = [System.IO.File]::Open($arq, 'OpenOrCreate', 'ReadWrite', 'None')
    } catch {
        $ex = $_.Exception
        while ($ex.InnerException) { $ex = $ex.InnerException }
        # 0x80070020 = arquivo em uso por outro processo (outra janela do atualizador)
        if ($ex -is [System.IO.IOException] -and ($ex.HResult -band 0xFFFF) -eq 0x20) {
            Recusar 'Já tem uma atualização do Proclamare rodando (outra janela). Espere ela terminar. Nada foi alterado.'
        }
        Recusar ("Não consegui gravar na pasta logs do Proclamare ($($ex.Message)).`n" +
            '    Confira se a pasta logs existe e se você pode gravar nela. Nada foi alterado.')
    }
}

# v2.8.3: depois do sucesso, apaga o ZIP que FOI INSTALADO quando veio da pasta do .bat. Reconferido na hora:
# arquivo comum (nunca link), proclamare*.zip, DENTRO da pasta do .bat, e o nome real e o mesmo (nada de 8.3).
function Pode-Apagar-Zip($Caminho, [string]$PastaBat) {
    try {
        if (-not $Caminho -or -not [System.IO.File]::Exists([string]$Caminho)) { return $false }
        $info = New-Object System.IO.FileInfo([string]$Caminho)
        if (Eh-Link $info) { return $false }
        # As duas pastas passam pelo mesmo DirectoryInfo (o .NET pode expandir nomes curtos 8.3 de um lado so).
        $paiZip = $info.Directory.FullName.TrimEnd('\')
        $paiBat = (New-Object System.IO.DirectoryInfo($PastaBat)).FullName.TrimEnd('\')
        if (-not [string]::Equals($paiZip, $paiBat, $OrdIC)) { return $false }
        if (-not (Casa-Nome $info.Name 'proclamare*.zip')) { return $false }
        $real = Nome-Real $info.FullName
        return ($null -ne $real -and [string]::Equals($real, $info.Name, $OrdIC))
    } catch { return $false }
}
function Dizer-ZipsAntigos {
    if ($script:ZipsIgnorados.Count -gt 0) {
        Dizer ('    Pode apagar os ZIPs antigos desta pasta: ' + ($script:ZipsIgnorados -join ', ') + '.') 'Gray'
    }
}

# ==== FLUXO PRINCIPAL (os testes de funcao usam so o que vem ACIMA desta linha) ====
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
    $pastaBat = [System.IO.Path]::GetDirectoryName($bat)
    $opc = Ler-Opcoes $argsTexto
    $script:Opc = $opc

    $raiz = if ($opc.PastaInstalacao) { $opc.PastaInstalacao } else { $pastaBat }
    $raiz = [System.IO.Path]::GetFullPath($raiz).TrimEnd('\')
    Dizer '=== Atualizar o Proclamare ===' 'White'
    Dizer "Pasta do Proclamare: $raiz"
    if (Deve-Recusar-Elevado (Esta-Elevado) (Uac-Ligado)) {
        Recusar ("O atualizador NÃO precisa de administrador (e não deve rodar assim).`n" +
            "    Feche esta janela e dê dois cliques no 'Atualizar Proclamare.bat' normalmente,`n" +
            "    sem 'Executar como administrador'. Nada foi alterado.")
    }
    if (-not [System.IO.File]::Exists((Join-Path $raiz 'Proclamare.pyw')) -or -not [System.IO.File]::Exists((Join-Path $raiz 'server.py'))) {
        Recusar ("Esta pasta não é a do Proclamare (não achei o Proclamare.pyw e o server.py aqui).`n" +
            "    Copie o 'Atualizar Proclamare.bat' (e o ZIP, se tiver) para a pasta do Proclamare no computador`n" +
            '    da loja, a que tem o arquivo Proclamare.pyw, e dê dois cliques nele lá. Nada foi alterado.')
    }
    $script:Raiz = $raiz
    # logs como link: recusa ANTES de qualquer log/trava/plano (nada e gravado atraves do link).
    if (Logs-Eh-Link) {
        Recusar ("A pasta logs do Proclamare é um link (atalho de pasta); por segurança não vou usá-la.`n" +
            '    Troque-a por uma pasta comum. Nada foi alterado.')
    }
    Iniciar-Log
    Registrar "opcoes: SemReiniciar=$($opc.SemReiniciar) SemRede=$($opc.SemRede) Zip=$([bool]$opc.Zip) EsperaInicio=$($opc.EsperaInicio)"
    Abrir-Trava
    # N1: gancho de teste so com o arquivo-sinal; sem ele a variavel e ignorada (e o log diz).
    if ([string]$env:PROCLAMARE_ATUALIZAR_TESTE_GITHUB) {
        if (Teste-Github-Liberado) {
            $urlTeste = Url-De-Teste
            Dizer "MODO DE TESTE: GitHub falso em $urlTeste" 'Yellow'
        } else {
            Registrar 'variável PROCLAMARE_ATUALIZAR_TESTE_GITHUB ignorada (falta logs\teste-github.token na pasta de instalação)'
        }
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

    # ---- 0. Atualizacao interrompida antes? Primeiro a versao anterior volta.
    Recuperar-Pendente
    $vAtual = Ler-Versao (Join-Path $raiz 'server.py')

    # ---- 2. Fonte da versao nova
    Passo 'Procurando a versão nova...'
    $zip = $null
    $zipDaPasta = $null
    $daInternet = $false
    $buscarNaInternet = $false
    $pacote = $null
    $script:ZipsIgnorados = @()
    if ($opc.Zip) {
        $zip = [System.IO.Path]::GetFullPath($opc.Zip)
        if (-not [System.IO.File]::Exists($zip)) { Recusar "Não achei o arquivo $zip. Nada foi alterado." }
        Ok "usando o ZIP informado: $([System.IO.Path]::GetFileName($zip))"
    } else {
        $todos = @(Filhos $pastaBat | Where-Object { -not ($_ -is [System.IO.DirectoryInfo]) -and -not (Eh-Link $_) -and (Casa-Nome $_.Name '*.zip') })
        $zips = @($todos | Where-Object { Casa-Nome $_.Name 'proclamare*.zip' } | Sort-Object LastWriteTime -Descending)
        # Um .zip de outro nome nao e do Proclamare: avisa e ignora, mas NAO bloqueia mais a internet.
        $script:OutrosZips = @($todos | Where-Object { -not (Casa-Nome $_.Name 'proclamare*.zip') } | ForEach-Object { $_.Name })
        foreach ($o in $script:OutrosZips) { Aviso "Ignorei o arquivo ${o}: ele não é do Proclamare." }
        if ($zips.Count -gt 0) {
            # "Usando o ZIP" so e dito DEPOIS da comparacao de versao, se ele for mesmo usado (ver etapa 3).
            $zip = $zips[0].FullName
            $zipDaPasta = $zip
        } else {
            $buscarNaInternet = $true
        }
    }

    # ---- 3. Conferir e extrair
    if ($zipDaPasta) {
        Passo 'Conferindo o pacote (antes de mexer em qualquer coisa)...'
        $pacote = Extrair-Pacote $zip (Join-Path $script:Temp 'n')
        # v2.8.3: ZIP ACHADO na pasta que nao e mais novo que o instalado (ex.: o da versao ja instalada,
        # esquecido ao lado do .bat) NAO e instalado: vai-se para a internet. Sem versao legivel: regras de sempre.
        $vZip = Ler-Versao (Join-Path $pacote.Raiz 'server.py')
        if ($vAtual -and $vZip -and ((Versao-Comparavel $vZip).CompareTo((Versao-Comparavel $vAtual)) -le 0)) {
            $nomeZip = [System.IO.Path]::GetFileName($zipDaPasta)
            if ($opc.SemRede) {
                Recusar ("O ZIP $nomeZip é da versão $vZip, que não é mais nova que a instalada ($vAtual): o ZIP desta pasta é antigo.`n" +
                    '    Com -SemRede não procuro na internet: coloque o ZIP da versão nova ou rode sem -SemRede. Nada foi alterado.')
            }
            Aviso "O ZIP $nomeZip é da versão $vZip, que não é mais nova que a instalada ($vAtual). Vou ignorá-lo e procurar na internet."
            if ($zips.Count -gt 1) { Aviso "Ignorei $($zips.Count) ZIPs desta pasta: nenhum é mais novo que a instalada ($vAtual)." }
            $script:ZipsIgnorados = @($zips | ForEach-Object { $_.Name })
            $zip = $null
            $zipDaPasta = $null
            $pacote = $null
            try { [System.IO.Directory]::Delete((Join-Path $script:Temp 'n'), $true) } catch { }
            $buscarNaInternet = $true
        } else {
            # Vai ser usado: agora sim diz qual; os DEMAIS ZIPs da pasta so aparecem como "pode apagar".
            if ($zips.Count -gt 1) {
                Ok ("achei $($zips.Count) arquivos ZIP do Proclamare; vou usar o mais novo: $($zips[0].Name) (de " +
                    $zips[0].LastWriteTime.ToString('dd/MM/yyyy HH:mm') + ')')
            } else {
                Ok "usando o ZIP desta pasta: $($zips[0].Name)"
            }
            $script:ZipsIgnorados = @($zips | Select-Object -Skip 1 | ForEach-Object { $_.Name })
        }
    }
    if ($buscarNaInternet) {
        if ($opc.SemRede) { Recusar ("Não achei a versão nova (e -SemRede proíbe a internet).`n" + (Texto-ColoqueZip)) }
        if ($script:ZipsIgnorados.Count -gt 0) {
            Dizer '    Procurando a última versão publicada no GitHub (precisa de internet)...'
        } else {
            Dizer '    Nenhum ZIP nesta pasta: procurando a última versão publicada no GitHub (precisa de internet)...'
        }
        $tag = Ler-Release
        $vTag = $tag.Substring(1)
        Ok "versão publicada: $vTag"
        if ($vAtual) {
            $cmp = (Versao-Comparavel $vTag).CompareTo((Versao-Comparavel $vAtual))
            if ($cmp -eq 0) {
                Dizer "`nO Proclamare já está na versão $vAtual. Nada foi alterado." 'Green'
                Dizer-ZipsAntigos
                return 0
            }
            if ($cmp -lt 0) {
                Recusar "A versão publicada no GitHub ($vTag) é mais antiga que a instalada ($vAtual). Nada foi alterado."
            }
        }
        $zip = Join-Path $script:Temp 'proclamare-github.zip'
        Baixar-Tag $tag $zip
        $daInternet = $true
    }
    if (-not $pacote) {
        Passo 'Conferindo o pacote (antes de mexer em qualquer coisa)...'
        $pacote = Extrair-Pacote $zip (Join-Path $script:Temp 'g')   # pasta NOVA (a 'n' foi do ZIP da pasta)
    }
    $novo = $pacote.Raiz

    # ---- 4. Versoes
    $vNova = Ler-Versao (Join-Path $novo 'server.py')
    $dNova = $pacote.Data
    if (-not $dNova) { $dNova = Data-Mais-Nova $novo }
    $descNova = Descrever-Versao $vNova $dNova
    if ($vAtual -and -not $vNova) {
        Recusar ("Não consegui ler o número da versão no pacote novo (a instalada é a $vAtual).`n" +
            '    Por segurança ele foi recusado. Confira se o ZIP é mesmo do Proclamare. Nada foi alterado.')
    }
    if ($vAtual -and $vNova) {
        $cmp = (Versao-Comparavel $vNova).CompareTo((Versao-Comparavel $vAtual))
        if ($cmp -lt 0) {
            Recusar ("O pacote é de uma versão MAIS ANTIGA ($vNova) do que a instalada ($vAtual).`n" +
                '    Se for um ZIP velho esquecido nesta pasta, apague-o e tente de novo. Nada foi alterado.')
        }
        if ($cmp -eq 0 -and $daInternet) {
            Dizer "`nO Proclamare já está na versão $vAtual. Nada foi alterado." 'Green'
            Dizer-ZipsAntigos
            return 0
        }
    }

    # requirements.lock: so pacotes e --hash; mudou (sem contar CRLF/LF) = precisa de internet para o pip.
    $lockNovoPacote = Join-Path $novo 'requirements.lock'
    $linhaRuim = Conferir-Lock $lockNovoPacote
    if ($null -ne $linhaRuim) {
        Registrar ('lock recusado: ' + ($linhaRuim -replace '[^\x20-\x7e]', '?'))
        Recusar ("O requirements.lock do pacote tem uma opção que não é permitida (só pacotes e --hash).`n" +
            '    Por segurança ele foi recusado. Nada foi alterado.')
    }
    $precisaPip = (Hash-Texto $lockNovoPacote) -ne (Hash-Texto (Join-Path $raiz 'requirements.lock'))
    if ($precisaPip -and $opc.SemRede) {
        Recusar ("A versão nova precisa de componentes novos, e isso precisa de internet (-SemRede proíbe).`n" +
            '    Nada foi alterado.')
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
        if ($link) { Recusar ((Descrever-Link $link) + '; por segurança não vou atualizar. Nada foi alterado.') }
    }
    foreach ($a in $arquivosNovos) {
        $c = Join-Path $raiz $a
        if ([System.IO.File]::Exists($c) -and (Eh-Link (New-Object System.IO.FileInfo($c)))) {
            Recusar ((Descrever-Link $c) + '; por segurança não vou atualizar. Nada foi alterado.')
        }
    }

    # ---- Versoes bem claras na tela, ANTES de desligar o Proclamare
    $txtAtual = if ($vAtual) { $vAtual } else { Descrever-Versao $null (Data-Mais-Nova $raiz) }
    $txtNova = if ($vNova) { $vNova } else { $descNova }
    $deOnde = if ($daInternet) { 'internet' } else { 'ZIP ' + [System.IO.Path]::GetFileName($zip) }
    Dizer ''
    Dizer '  ============================================================' 'Cyan'
    Dizer "  Versão instalada agora: $txtAtual" 'White'
    Dizer "  Versão que será instalada: $txtNova  (de onde: $deOnde)" 'White'
    Dizer '  ============================================================' 'Cyan'
    Dizer ''

    # ---- 5. Desligar
    Passo 'Desligando o Proclamare...'
    if (-not (Parar-Proclamare)) {
        Recusar ("O Proclamare não desligou (talvez ele tenha sido ligado por outro jeito, por exemplo numa janela preta).`n" +
            "    Feche essa janela ou use 'Desligar Proclamare' no Menu Iniciar e tente de novo. Nada foi alterado.")
    }

    # ---- 6. Copia de seguranca + plano gravado (se a janela fechar no meio, a proxima execucao desfaz)
    Passo 'Guardando uma cópia do código atual...'
    $script:Backup = Fazer-Backup $pastasNovas $arquivosNovos
    Ok "cópia em backups\$([System.IO.Path]::GetFileName($script:Backup))"
    Planejar-Instalacao $novo
    try { Gravar-Pendente } catch { Recusar "Não consegui gravar o registro da atualização em logs ($($_.Exception.Message)). Nada foi alterado." }

    # ---- 7. Instalar
    Passo 'Instalando a versão nova (seus dados não são tocados)...'
    Instalar-Codigo $novo
    Ok 'arquivos copiados.'

    # ---- 8. Dependencias
    if ($precisaPip) {
        Passo 'A versão nova precisa de componentes novos: instalando (precisa de internet, pode levar alguns minutos)...'
        $script:PipRodou = $true
        try { Gravar-Pendente } catch { Registrar ('pendente: ' + $_.Exception.Message) }
        if (-not (Rodar-Pip (Join-Path $raiz 'requirements.lock'))) {
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
        Ok 'Proclamare ligado e respondendo.'
        if ($env:PROCLAMARE_NO_BROWSER -ne '1') {
            try { Start-Process ("http://127.0.0.1:$($script:Porta)/") } catch { }
        }
    }
    Remover-Pendente
    $script:Fase = 'pronto'
    $rotulo = if ($vNova) { "a versão $vNova" } else { $descNova }
    $quem = if ($vAtual -and $vNova -and (Versao-Comparavel $vAtual).CompareTo((Versao-Comparavel $vNova)) -eq 0) { "reinstalado na versão $vNova" }
        elseif ($vAtual -and $vNova) { "atualizado da versão $vAtual para a versão $vNova" } else { "atualizado para $rotulo" }
    Dizer ("`nProclamare $quem. Seus dados (contas, listas, horários, mensagens," +
        ' configuração) foram mantidos.') 'Green'
    Dizer "    Se o painel já estava aberto (inclusive a aba 'Tocador da loja' e nos celulares), aperte F5 nele." 'Green'
    Dizer "    Cópia da versão anterior: backups\$([System.IO.Path]::GetFileName($script:Backup))" 'Gray'
    # So agora (codigo instalado, conferido e, se religou, respondendo) o ZIP que FOI instalado sai da pasta.
    # Falhar aqui e so um aviso: nunca erro e nunca volta de versao.
    if ($zipDaPasta) {
        $nomeZip = [System.IO.Path]::GetFileName($zipDaPasta)
        try {
            if (-not (Pode-Apagar-Zip $zipDaPasta $pastaBat)) { throw 'o arquivo não é mais um ZIP comum desta pasta' }
            Apagar-Arquivo $zipDaPasta
            Dizer "    O ZIP $nomeZip foi apagado desta pasta (já foi instalado)." 'Gray'
        } catch {
            Registrar ('zip nao apagado: ' + $_.Exception.Message)
            Aviso "não consegui apagar o ZIP $nomeZip; pode apagar o ZIP à mão (a versão nova já está instalada)."
        }
    }
    Dizer-ZipsAntigos
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
        try { $codigo = [int](@(Voltar-Versao $msg $script:Opc)[-1]) } catch {
            Registrar ('volta falhou: ' + $_.ToString())
            Dizer-VoltaFalhou
            $codigo = 3
        }
    } elseif ($script:Fase -eq 'voltando') {
        # A excecao aconteceu DURANTE uma volta (nunca tenta voltar duas vezes).
        Registrar ('volta interrompida: ' + $_.ToString())
        Dizer-VoltaFalhou
        $codigo = 3
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
    try { Podar-Backups } catch { Registrar ('poda falhou: ' + $_.ToString()) }
    if ($script:Temp -and [System.IO.Directory]::Exists($script:Temp)) {
        try { [System.IO.Directory]::Delete($script:Temp, $true) } catch { Registrar "não consegui apagar a pasta temporária $($script:Temp)" }
    }
    if ($script:Trava) { $script:Trava.Dispose() }
    Registrar "fim: codigo $codigo"
}
exit $codigo
