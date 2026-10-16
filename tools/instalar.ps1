<#
.SYNOPSIS
    Instala (ou desinstala) o Proclamare no Windows: ambiente Python, atalhos e inicio com o Windows.
    Requer Python 3.10 ou mais novo (lock de dependencias testado em 3.10 a 3.13).
    As dependencias vem de requirements.lock com verificacao de hashes; se pip-audit estiver
    disponivel, a auditoria de vulnerabilidades roda em MODO AVISO (nunca bloqueia a instalacao).

.PARAMETER PastaAtalhos
    (Teste/automacao) Cria/remove TODOS os atalhos nesta pasta em vez de Area de Trabalho,
    Menu Iniciar e Inicializar.

.PARAMETER SemDependencias
    Pula a criacao do .venv, o pip install e a auditoria; usa o Python atual encontrado.

.PARAMETER PermitirSemHashes
    So se requirements.lock estiver faltando: aceita instalar pelo requirements.txt SEM verificacao
    de hashes (menos seguro). Sem este parametro, a instalacao para e pede o projeto completo.

.PARAMETER IniciarComWindows
    Cria o atalho de inicializacao sem perguntar.

.PARAMETER NaoIniciarComWindows
    Nao cria (e REMOVE, se existir) o atalho de inicializacao, sem perguntar.

.PARAMETER Desinstalar
    Remove os atalhos e pergunta se deve apagar o .venv. Nao apaga o programa nem o config.json.
#>
[CmdletBinding()]
param(
    [string]$PastaAtalhos = '',
    [switch]$SemDependencias,
    [switch]$PermitirSemHashes,
    [switch]$IniciarComWindows,
    [switch]$NaoIniciarComWindows,
    [switch]$Desinstalar
)

$ErrorActionPreference = 'Stop'
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }

# Raiz do projeto = pasta acima de tools\
$Raiz = Split-Path -Parent $PSScriptRoot
$Pyw = Join-Path $Raiz 'Proclamare.pyw'
$Icone = Join-Path $Raiz 'assets\proclamare.ico'
$Requisitos = Join-Path $Raiz 'requirements.txt'
# Dependencias travadas com hashes (pip --require-hashes). Se faltar, cai no requirements.txt com aviso.
$TravaHashes = Join-Path $Raiz 'requirements.lock'
$Script = Join-Path $Raiz 'tools\instalar.ps1'
$PowerShellExe = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'

function Escrever-Passo([string]$Texto) { Write-Host "`n==> $Texto" -ForegroundColor Cyan }
function Escrever-Ok([string]$Texto) { Write-Host "    OK: $Texto" -ForegroundColor Green }

function Falhar([string]$Texto) {
    Write-Host "`nERRO: $Texto" -ForegroundColor Red
    exit 1
}

# ---- Destinos dos atalhos (pastas e nomes) -----------------------------
if ($PastaAtalhos) {
    $dest = [System.IO.Path]::GetFullPath($PastaAtalhos)
    $Destinos = @{
        Desktop = @{ Pasta = $dest; Nome = 'Proclamare' }
        Menu = @{ Pasta = $dest; Nome = 'Proclamare (Menu Iniciar)' }
        Parar = @{ Pasta = $dest; Nome = 'Desligar Proclamare' }
        Desinstalar = @{ Pasta = $dest; Nome = 'Desinstalar Proclamare' }
        Startup = @{ Pasta = $dest; Nome = 'Proclamare (Inicializar)' }
    }
} else {
    $menu = [Environment]::GetFolderPath('Programs')
    $Destinos = @{
        Desktop = @{ Pasta = [Environment]::GetFolderPath('Desktop'); Nome = 'Proclamare' }
        Menu = @{ Pasta = $menu; Nome = 'Proclamare' }
        Parar = @{ Pasta = $menu; Nome = 'Desligar Proclamare' }
        Desinstalar = @{ Pasta = $menu; Nome = 'Desinstalar Proclamare' }
        Startup = @{ Pasta = [Environment]::GetFolderPath('Startup'); Nome = 'Proclamare' }
    }
}

function Caminho-Atalho($Destino) { return Join-Path $Destino.Pasta ($Destino.Nome + '.lnk') }

# Cria um atalho .lnk via WScript.Shell.
function Novo-Atalho($Destino, [string]$Alvo, [string]$Argumentos, [string]$Descricao) {
    if (-not (Test-Path -LiteralPath $Destino.Pasta)) {
        New-Item -ItemType Directory -Path $Destino.Pasta -Force | Out-Null
    }
    $caminho = Caminho-Atalho $Destino
    $shell = New-Object -ComObject WScript.Shell
    $lnk = $shell.CreateShortcut($caminho)
    $lnk.TargetPath = $Alvo
    $lnk.Arguments = $Argumentos
    $lnk.WorkingDirectory = $Raiz
    $lnk.IconLocation = "$Icone,0"
    $lnk.Description = $Descricao
    $lnk.Save()
    [void][System.Runtime.InteropServices.Marshal]::ReleaseComObject($shell)
    Escrever-Ok $caminho
}

function Remover-Atalho($Destino) {
    $caminho = Caminho-Atalho $Destino
    if (Test-Path -LiteralPath $caminho) {
        Remove-Item -LiteralPath $caminho -Force
        Escrever-Ok "removido: $caminho"
        return $true
    }
    return $false
}

Write-Host '=== Proclamare ===' -ForegroundColor White
Write-Host "Pasta do programa: $Raiz"

# ---- Desinstalar --------------------------------------------------------
if ($Desinstalar) {
    Escrever-Passo 'Removendo os atalhos do Proclamare...'
    $algum = $false
    foreach ($chave in @('Desktop', 'Menu', 'Parar', 'Desinstalar', 'Startup')) {
        if (Remover-Atalho $Destinos[$chave]) { $algum = $true }
    }
    if (-not $algum) { Write-Host '    Nenhum atalho encontrado.' }

    $venv = Join-Path $Raiz '.venv'
    if (Test-Path -LiteralPath $venv) {
        $resp = Read-Host 'Apagar também o ambiente .venv (as dependências instaladas)? (S/N)'
        if ($resp -match '^\s*[sSyY]') {
            # Só apaga se for mesmo a pasta .venv dentro da raiz e não for um link.
            $item = Get-Item -LiteralPath $venv -Force
            $ehLink = [bool]($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint)
            if ($item.Name -eq '.venv' -and $item.Parent.FullName -eq $Raiz -and -not $ehLink) {
                # Desliga o servidor antes (senão os arquivos do .venv ficam em uso).
                $pywVenv = Join-Path $venv 'Scripts\pythonw.exe'
                if ((Test-Path -LiteralPath (Join-Path $Raiz 'logs\proclamare.pid')) -and (Test-Path -LiteralPath $pywVenv)) {
                    Escrever-Passo 'Desligando o Proclamare antes de apagar o .venv...'
                    try {
                        Start-Process -FilePath $pywVenv -ArgumentList ('"' + $Pyw + '" --parar') -WorkingDirectory $Raiz -Wait -WindowStyle Hidden
                    } catch { }
                }
                try {
                    Remove-Item -LiteralPath $venv -Recurse -Force -ErrorAction Stop
                    Escrever-Ok '.venv apagado.'
                } catch {
                    Write-Host ("    Não foi possível apagar o .venv: algum arquivo está em uso.`n" +
                        "    Desligue o Proclamare (Menu Iniciar > Desligar Proclamare), feche o painel`n" +
                        "    e execute a desinstalação de novo.") -ForegroundColor Yellow
                }
            } else {
                Write-Host '    .venv não é uma pasta comum; nada foi apagado.' -ForegroundColor Yellow
            }
        } else {
            Write-Host '    .venv mantido.'
        }
    }
    Write-Host "`nProclamare desinstalado. A pasta do programa e o config.json foram mantidos." -ForegroundColor Green
    if (-not $PastaAtalhos) { [void](Read-Host 'Pressione Enter para fechar') }
    exit 0
}

if (-not (Test-Path -LiteralPath $Pyw)) { Falhar "Não encontrei o Proclamare.pyw em $Raiz." }
if (-not (Test-Path -LiteralPath $Icone)) { Falhar "Não encontrei o ícone em $Icone." }

# ---- Aviso de seguranca: pasta gravavel por grupos amplos ---------------
# SIDs: Authenticated Users, Users, Everyone, INTERACTIVE, Guests (independe do idioma).
$SidsAmplos = @('S-1-5-11', 'S-1-5-32-545', 'S-1-1-0', 'S-1-5-4', 'S-1-5-32-546')

# True se alguma ACE de permissao der escrita a um grupo amplo. Cada ACE tem seu proprio
# try/catch: um SID orfao (que nao traduz) nao pode cancelar a checagem das demais.
function Pasta-Gravavel-Por-Todos([string]$Caminho) {
    try { $acl = Get-Acl -LiteralPath $Caminho } catch { return $false }
    foreach ($r in $acl.Access) {
        try {
            if ($r.AccessControlType -ne 'Allow') { continue }
            $sid = $r.IdentityReference.Translate([System.Security.Principal.SecurityIdentifier]).Value
            # 0x2 = WriteData/CreateFiles, 0x4 = AppendData (Write, Modify e FullControl incluem ambos)
            if (($SidsAmplos -contains $sid) -and (([int]$r.FileSystemRights -band 0x6) -ne 0)) { return $true }
        } catch {
            # SID orfao ou ACE ilegivel: ignora so esta ACE
            if ($r.IdentityReference.Value -match '^S-1-(1-0|5-(4|11)|5-32-(545|546))$' -and
                (([int]$r.FileSystemRights -band 0x6) -ne 0) -and $r.AccessControlType -eq 'Allow') { return $true }
        }
    }
    return $false
}

$pastasChecar = @($Raiz, (Join-Path $Raiz 'tools'), (Join-Path $Raiz 'static'), (Join-Path $Raiz '.venv'))
$pastasPerigo = @()
foreach ($pc in $pastasChecar) {
    if ((Test-Path -LiteralPath $pc) -and (Pasta-Gravavel-Por-Todos $pc)) { $pastasPerigo += $pc }
}
if ($pastasPerigo.Count -gt 0) {
    Write-Host ("`nAVISO: estas pastas podem ser alteradas por qualquer usuário deste computador:`n    " +
        ($pastasPerigo -join "`n    ") +
        "`n       Outra pessoa poderia trocar os arquivos do Proclamare. Recomendado: mova a pasta`n" +
        "       para dentro da sua pasta de usuário (ex.: Documentos) e rode o instalador de novo.") -ForegroundColor Yellow
}

# ---- 1. Python ---------------------------------------------------------
# Testa um interpretador; devolve @{ Caminho; Versao } se for 3.10+ e nao for o alias da Store.
function Testar-Python([string]$Exe, [string[]]$Argumentos) {
    try {
        # aspas simples dentro do codigo: o PowerShell 5.1 perde aspas duplas ao chamar programas nativos
        # Com o lock (hashes), so as versoes em que ele foi testado: 3.10 a 3.13. Em Python mais novo
        # (ex.: 3.14) o pygame nao tem wheel e o pip compilaria o codigo-fonte, com dependencias de
        # build SEM verificacao de hash.
        $limite = if (Test-Path -LiteralPath $TravaHashes) { '(3, 14)' } else { '(99, 0)' }
        $codigo = "import sys; print(sys.executable + '|' + '%d.%d.%d' % sys.version_info[:3]) if (3, 10) <= sys.version_info[:2] < $limite else sys.exit(3)"
        $saida = & $Exe @Argumentos -c $codigo 2>$null
        if ($LASTEXITCODE -eq 0 -and $saida) {
            $partes = ($saida | Select-Object -Last 1).ToString().Trim().Split('|')
            $caminho = $partes[0]
            if ($caminho -like '*\WindowsApps\*') { return $null }   # alias da Microsoft Store
            if (Test-Path -LiteralPath $caminho) { return @{ Caminho = $caminho; Versao = $partes[1] } }
        }
    } catch { }
    return $null
}

function Achar-Python {
    if (Get-Command py -ErrorAction SilentlyContinue) {
        foreach ($v in @('-3.12', '-3.11', '-3.13', '-3.10', '-3')) {
            $p = Testar-Python 'py' @($v)
            if ($p) { return $p }
        }
    }
    $cmd = Get-Command python -ErrorAction SilentlyContinue
    if ($cmd -and ($cmd.Source -notlike '*\WindowsApps\*')) {
        $p = Testar-Python $cmd.Source @()
        if ($p) { return $p }
    }
    return $null
}

Escrever-Passo 'Procurando o Python 3.10 ou mais novo...'
$py = Achar-Python
if (-not $py) {
    Falhar ("Python 3.10 a 3.13 não encontrado (com o requirements.lock o Proclamare aceita 3.10 a 3.13).`n" +
        "    Instale o Python 3.12 em https://www.python.org/downloads/ e, na primeira tela do instalador,`n" +
        "    MARQUE a opção 'Add python.exe to PATH'. Depois execute este instalador de novo.")
}
$python = $py.Caminho
Escrever-Ok "$python (Python $($py.Versao))"

# ---- Auditoria de vulnerabilidades (MODO AVISO) ------------------------
# Procura o pip-audit (comando no PATH ou modulo do Python escolhido). Nao instala nada:
# so roda se o usuario ja o tiver. Devolve @{ Exe; Args } ou $null.
function Achar-PipAudit([string]$PythonBase) {
    $cmd = Get-Command pip-audit -ErrorAction SilentlyContinue
    if ($cmd) { return @{ Exe = $cmd.Source; Args = @() } }
    try {
        & $PythonBase -I -m pip_audit --version *> $null
        if ($LASTEXITCODE -eq 0) { return @{ Exe = $PythonBase; Args = @('-I', '-m', 'pip_audit') } }
    } catch { }
    return $null
}

# Roda o pip-audit sobre o lock, com limite de 2 minutos. NUNCA falha a instalacao:
# qualquer problema (sem internet, achados, tempo esgotado) vira so um aviso.
function Auditar-Dependencias([string]$PythonBase, [string]$Lock) {
    $audit = Achar-PipAudit $PythonBase
    if (-not $audit) {
        Write-Host ("    pip-audit não encontrado (opcional): a verificação de vulnerabilidades foi pulada.`n" +
            "    Para ativá-la, instale-o FORA do ambiente do Proclamare (ex.: py -m pip install --user pip-audit)`n" +
            "    e rode o instalador de novo.")
        return
    }
    Escrever-Passo 'Verificando vulnerabilidades conhecidas nas dependências (pip-audit, só aviso)...'
    $saida = Join-Path ([System.IO.Path]::GetTempPath()) ('proclamare-audit-' + [guid]::NewGuid().ToString('N') + '.txt')
    try {
        # --no-deps --disable-pip: usa só as versões fixadas do lock (não resolve nem instala nada)
        $argumentos = @($audit.Args) + @('-r', $Lock, '--no-deps', '--disable-pip', '--progress-spinner', 'off')
        $proc = Start-Process -FilePath $audit.Exe -ArgumentList ($argumentos | ForEach-Object { '"' + $_ + '"' }) `
            -NoNewWindow -PassThru -RedirectStandardOutput $saida -RedirectStandardError ($saida + '.err')
        $null = $proc.Handle   # guarda o handle: sem isto o ExitCode vem vazio no Windows PowerShell 5.1
        if (-not $proc.WaitForExit(120000)) {
            try { $proc.Kill() } catch { }
            Write-Host '    AVISO: a verificação demorou demais e foi cancelada (a instalação segue normalmente).' -ForegroundColor Yellow
            return
        }
        $proc.WaitForExit()
        $texto = ''
        foreach ($arq in @($saida, ($saida + '.err'))) {
            if (Test-Path -LiteralPath $arq) { $texto += (Get-Content -LiteralPath $arq -Raw -ErrorAction SilentlyContinue) }
        }
        if ($proc.ExitCode -eq 0) {
            Escrever-Ok 'nenhuma vulnerabilidade conhecida nas versões fixadas.'
        } else {
            Write-Host '    AVISO: o pip-audit apontou algo (ou não conseguiu consultar a internet). A instalação NÃO foi bloqueada:' -ForegroundColor Yellow
            if ($texto) { Write-Host $texto }
            Write-Host '    Se houver vulnerabilidades, atualize o Proclamare quando sair uma versão nova.' -ForegroundColor Yellow
        }
    } catch {
        Write-Host '    AVISO: não foi possível rodar o pip-audit; seguindo sem ele.' -ForegroundColor Yellow
    } finally {
        Remove-Item -LiteralPath $saida, ($saida + '.err') -Force -ErrorAction SilentlyContinue
    }
}

# ---- 2. Ambiente virtual e dependencias --------------------------------
if ($SemDependencias) {
    Escrever-Passo 'Pulando .venv e pip (-SemDependencias): usando o Python atual.'
    $pythonw = Join-Path (Split-Path -Parent $python) 'pythonw.exe'
} else {
    $venvDir = Join-Path $Raiz '.venv'
    $venvPython = Join-Path $venvDir 'Scripts\python.exe'
    $existe = Test-Path -LiteralPath $venvDir
    $saudavel = $false
    if (Test-Path -LiteralPath $venvPython) {
        try { & $venvPython -c 'import sys' *> $null; $saudavel = ($LASTEXITCODE -eq 0) } catch { $saudavel = $false }
    }
    if ($saudavel) {
        Escrever-Passo 'Ambiente .venv já existe e está saudável.'
    } else {
        if ($existe) {
            Escrever-Passo 'O .venv existente está quebrado; recriando...'
            $argsVenv = @('-I', '-m', 'venv', '--clear', $venvDir)
        } else {
            Escrever-Passo 'Criando o ambiente .venv (pode levar um minuto)...'
            $argsVenv = @('-I', '-m', 'venv', $venvDir)
        }
        $ErrorActionPreference = 'Continue'
        & $python @argsVenv
        $codigoVenv = $LASTEXITCODE
        $ErrorActionPreference = 'Stop'
        if ($codigoVenv -ne 0 -or -not (Test-Path -LiteralPath $venvPython)) {
            Falhar "Não foi possível criar o ambiente .venv com o Python $($py.Versao)."
        }
    }
    # Preferido: requirements.lock com hashes (cada arquivo baixado e conferido; versoes fixas).
    $usaLock = Test-Path -LiteralPath $TravaHashes
    if ($usaLock) {
        Escrever-Passo 'Instalando as dependências com verificação de hashes (precisa de internet)...'
        # --only-binary: nunca compila codigo-fonte (dependencias de build nao tem hash);
        # --no-deps: o lock e fechado, o pip nao resolve nada fora dele.
        $argsPip = @('-I', '-m', 'pip', 'install', '--require-hashes', '--only-binary', ':all:', '--no-deps', '-r', $TravaHashes)
    } else {
        if (-not $PermitirSemHashes) {
            Falhar ("Não encontrei o requirements.lock em $TravaHashes.`n" +
                "    Baixe o projeto completo de novo (ele traz o requirements.lock com as verificações de segurança).`n" +
                '    Para instalar mesmo assim, SEM verificação de hashes (menos seguro), rode com -PermitirSemHashes.')
        }
        Write-Host ("`nAVISO: não encontrei o requirements.lock em $TravaHashes.`n" +
            '       Vou instalar pelo requirements.txt, SEM verificação de hashes (menos seguro).' +
            "`n       Baixe o projeto completo de novo para ter o requirements.lock.") -ForegroundColor Yellow
        Escrever-Passo 'Instalando as dependências SEM verificação de hashes (precisa de internet)...'
        $argsPip = @('-I', '-m', 'pip', 'install', '-r', $Requisitos)
    }
    $ErrorActionPreference = 'Continue'   # avisos do pip no stderr não devem abortar
    & $venvPython @argsPip
    $codigoPip = $LASTEXITCODE
    $ErrorActionPreference = 'Stop'
    if ($codigoPip -ne 0) {
        if ($usaLock) {
            Falhar ("A instalação das dependências falhou (Python $($py.Versao)).`n" +
                '    Verifique a internet. Se o erro citar "hash" ou "THESE PACKAGES DO NOT MATCH THE HASHES",' +
                "`n    o arquivo requirements.lock foi alterado ou corrompido: baixe o projeto de novo." +
                "`n    Se citar um pacote sem versão compatível, o lock foi testado nos Pythons 3.10 a 3.13:" +
                ' instale o Python 3.12 e tente de novo.')
        }
        Falhar ("A instalação das dependências falhou (Python $($py.Versao)).`n" +
            '    Verifique a internet; se o erro citar um pacote sem versão compatível,' +
            ' instale o Python 3.12 e tente de novo.')
    }
    Escrever-Ok 'Dependências instaladas.'
    if ($usaLock) { Auditar-Dependencias $python $TravaHashes }
    $pythonw = Join-Path $venvDir 'Scripts\pythonw.exe'
}
if (-not (Test-Path -LiteralPath $pythonw)) { Falhar "Não encontrei o pythonw.exe em $pythonw." }

# ---- 3. Atalhos --------------------------------------------------------
Escrever-Passo 'Criando os atalhos...'
$argLigar = '"' + $Pyw + '"'
$argSilencioso = '"' + $Pyw + '" --silencioso'
$argParar = '"' + $Pyw + '" --parar'
$argDesinstalar = '-NoProfile -ExecutionPolicy Bypass -File "' + $Script + '" -Desinstalar'
$descLigar = 'Liga o Proclamare (anúncios de voz e música da loja) e abre o painel.'
$descParar = 'Desliga o Proclamare.'
$descDesinstalar = 'Remove os atalhos do Proclamare.'

Novo-Atalho $Destinos.Desktop $pythonw $argLigar $descLigar
if ($PastaAtalhos -or ($Destinos.Menu.Pasta -ne $Destinos.Desktop.Pasta)) {
    Novo-Atalho $Destinos.Menu $pythonw $argLigar $descLigar
}
Novo-Atalho $Destinos.Parar $pythonw $argParar $descParar
Novo-Atalho $Destinos.Desinstalar $PowerShellExe $argDesinstalar $descDesinstalar

# ---- 4. Iniciar com o Windows -----------------------------------------
$quer = $false
if ($IniciarComWindows) {
    $quer = $true
} elseif (-not $NaoIniciarComWindows) {
    Write-Host ''
    $resp = Read-Host 'Iniciar o Proclamare junto com o Windows? (S/N)'
    $quer = $resp -match '^\s*[sSyY]'
}
if ($quer) {
    Escrever-Passo 'Criando o atalho de início com o Windows...'
    # --silencioso: liga o servidor sem abrir o navegador a cada logon
    Novo-Atalho $Destinos.Startup $pythonw $argSilencioso $descLigar
} else {
    if (Remover-Atalho $Destinos.Startup) {
        Write-Host "`nO Proclamare não iniciará mais junto com o Windows (atalho removido)."
    } else {
        Write-Host "`nO Proclamare NÃO iniciará junto com o Windows."
    }
}

Write-Host "`nPronto! Dê dois cliques no ícone Proclamare para ligar." -ForegroundColor Green
Write-Host 'Para desligar: Menu Iniciar > Desligar Proclamare.'
Write-Host 'Se mover esta pasta de lugar, rode o instalador de novo.'
