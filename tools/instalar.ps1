<#
.SYNOPSIS
    Instala (ou desinstala) o Proclamare no Windows: Python, ambiente, atalhos e inicio com o Windows.
    Usa um Python 3.10 a 3.13 que ja exista no computador (nada e baixado); so executa Pythons cuja
    pasta seja sua ou dos administradores e que nao possa ser alterada por qualquer usuario. Se nao houver, baixa
    sozinho um "Python do Proclamare" (3.12, fonte oficial, com hash e assinatura conferidos), numa
    pasta propria, sem pedir administrador e sem mexer no PATH nem no registro do Windows.
    As dependencias vem de requirements.lock com verificacao de hashes; se pip-audit estiver
    disponivel, a auditoria de vulnerabilidades roda em MODO AVISO (nunca bloqueia a instalacao).

.PARAMETER PastaAtalhos
    (Teste/automacao) Cria/remove TODOS os atalhos nesta pasta em vez de Area de Trabalho,
    Menu Iniciar e Inicializar.

.PARAMETER SemDependencias
    Pula a criacao do .venv, o pip install e a auditoria; usa o Python atual encontrado.
    Neste modo o instalador NAO baixa Python.

.PARAMETER PermitirSemHashes
    So se requirements.lock estiver faltando: aceita instalar pelo requirements.txt SEM verificacao
    de hashes (menos seguro). Sem este parametro, a instalacao para e pede o projeto completo.

.PARAMETER IniciarComWindows
    Cria o atalho de inicializacao sem perguntar.

.PARAMETER NaoIniciarComWindows
    Nao cria (e REMOVE, se existir) o atalho de inicializacao, sem perguntar.

.PARAMETER Desinstalar
    Remove os atalhos e pergunta se deve apagar o .venv e o Python baixado pelo Proclamare.
    Nao apaga o programa nem o config.json, e NUNCA mexe em um Python instalado por voce.

.PARAMETER PastaPython
    (Avancado) Pasta do Python do Proclamare. Padrao: %LOCALAPPDATA%\Proclamare\python-<versao>.
    So e usada quando nenhum Python 3.10 a 3.13 foi encontrado no computador. A pasta e criada
    protegida (so voce, o SYSTEM e os administradores podem alterar) e o disco precisa ser NTFS.

.PARAMETER IgnorarPythonDoSistema
    (Teste/avancado) Ignora os Pythons instalados no computador (py launcher, PATH, registro e
    pastas padrao) e considera so o Python do Proclamare. Serve para simular um PC sem Python.

.PARAMETER RemoverPythonPrivado
    Com -Desinstalar: apaga tambem o Python baixado pelo Proclamare, sem perguntar.
#>
[CmdletBinding()]
param(
    [string]$PastaAtalhos = '',
    [switch]$SemDependencias,
    [switch]$PermitirSemHashes,
    [switch]$IniciarComWindows,
    [switch]$NaoIniciarComWindows,
    [switch]$Desinstalar,
    [string]$PastaPython = '',
    [switch]$IgnorarPythonDoSistema,
    [switch]$RemoverPythonPrivado
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

# ---- Python do Proclamare: fonte oficial, versao e hashes FIXOS ---------
# Pacote oficial "python" do NuGet (publicado pela Python Software Foundation): o Python completo
# (com venv e pip), sem instalador, sem registro e sem PATH. 3.12.10 e a ULTIMA 3.12 com binarios
# para Windows (as 3.12.11 em diante so tem codigo-fonte). Conferido em 2026-10: o SHA-512 abaixo e o
# publicado no catalogo do nuget.org, e os binarios do pacote sao byte a byte iguais aos do
# "embeddable package" do python.org (3.12.10, MD5 fe8ef205f2e9c3ba44d0cf9954e1abd3).
# ATENCAO (revisar ate 2027-04): como a 3.12.10 nao recebe mais correcoes de seguranca em binario, trocar
# para uma versao que ainda receba (3.13.x; ou 3.14.x quando o requirements.lock liberar o pygame no 3.14).
# Antes de trocar, validar o fluxo inteiro em um PC limpo e conferir o SHA-512 no catalogo do nuget.org.
# Para trocar de versao: mude juntas as linhas de versao, tamanho, SHA-256, SHA-512 e caminho mais longo.
$PyVersao = '3.12.10'
$PyUrl = "https://api.nuget.org/v3-flatcontainer/python/$PyVersao/python.$PyVersao.nupkg"
$PyTamanho = [long]14515433
$PySha256 = '0eb85c2dfccccf1b17352de4c397f69194035b7d37149eacc16f1147d93de3b8'
$PySha512Base64 = 'u9pNz2iKlCEbYtUJaKkbOPMF0LjR7NkCafdKhvigpPzrt8oWKgdTpHaR6z3wyWQAm9PYGUxv0Zr66NX9AeHMDw=='
$PyAssinante = 'Python Software Foundation'
$PyCaminhoMaisLongo = 77   # maior caminho relativo dentro do pacote (muda com a versao; so serve p/ avisar cedo)
$PyDll = 'python' + (($PyVersao.Split('.')[0..1]) -join '') + '.dll'
$PyMarcador = '.proclamare-python.json'   # marca as pastas criadas por este instalador (so elas sao apagadas)
$PyLimiteDownloadSeg = 900                 # tempo maximo do download inteiro (15 min)

if ($PastaPython) {
    $PastaPythonPriv = [System.IO.Path]::GetFullPath($PastaPython).TrimEnd('\')
} else {
    $baseLocal = $env:LOCALAPPDATA
    if (-not $baseLocal) { $baseLocal = [Environment]::GetFolderPath('LocalApplicationData') }
    $PastaPythonPriv = Join-Path $baseLocal "Proclamare\python-$PyVersao"
}
# Raiz de disco ('C:') nao tem pasta-mae: Split-Path daria erro tecnico (a validacao amigavel vem logo abaixo).
$PastaPythonPai = if ($PastaPythonPriv.Length -ge 4) { Split-Path -Parent $PastaPythonPriv } else { $PastaPythonPriv }

function Escrever-Passo([string]$Texto) { Write-Host "`n==> $Texto" -ForegroundColor Cyan }
function Escrever-Ok([string]$Texto) { Write-Host "    OK: $Texto" -ForegroundColor Green }

function Falhar([string]$Texto) {
    Write-Host "`nERRO: $Texto" -ForegroundColor Red
    exit 1
}

# -PastaPython que e a raiz de um disco (ex.: C:\) nao serve: para na hora, com mensagem clara.
if ($PastaPython -and ($PastaPythonPriv.Length -lt 4 -or $PastaPythonPriv -eq [System.IO.Path]::GetPathRoot($PastaPythonPriv))) {
    Falhar 'A pasta informada em -PastaPython não é válida: ela não pode ser a raiz de um disco. Use uma pasta dentro da sua pasta de usuário.'
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

# ---- Seguranca de pastas e executaveis (usada na instalacao e na desinstalacao) ----
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

# Donos em quem confiamos para um programa que vamos EXECUTAR: o proprio usuario, SYSTEM, Administradores
# e TrustedInstaller. Quem cria uma pasta e dono dela (e pode soltar as permissoes e continuar gravando),
# por isso o dono vale mais do que a lista de permissoes.
$SidUsuario = [System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value
$SidsDonos = @($SidUsuario, 'S-1-5-18', 'S-1-5-32-544', 'S-1-5-80-956008885-3418522649-1831038044-1853292631-2271478464')

function Dono-Eh-Confiavel([string]$Caminho) {
    try {
        $acl = Get-Acl -LiteralPath $Caminho -ErrorAction Stop
        $dono = $acl.GetOwner([System.Security.Principal.SecurityIdentifier])
        return [bool]($dono -and ($SidsDonos -contains $dono.Value))
    } catch { return $false }
}

# Antes de EXECUTAR um programa que ja estava no computador: $null se pode, ou o motivo da recusa.
# Recusa quando o arquivo ou a pasta e de outro usuario, ou quando a pasta pode ser alterada por qualquer
# usuario do computador (assim ninguem consegue "plantar" um python.exe falso, ex.: C:\Python312).
function Motivo-Inseguro([string]$Exe) {
    try { $pasta = Split-Path -Parent $Exe } catch { return 'o caminho é inválido' }
    if (-not $pasta) { return 'o caminho é inválido' }
    if (-not (Dono-Eh-Confiavel $Exe)) { return 'o arquivo pertence a outro usuário do computador' }
    if (-not (Dono-Eh-Confiavel $pasta)) { return 'a pasta pertence a outro usuário do computador' }
    if (Pasta-Gravavel-Por-Todos $pasta) { return 'a pasta pode ser alterada por qualquer usuário do computador' }
    return $null
}

# Permissoes "so voce, o SYSTEM e os administradores" (sem herdar do pai), para as pastas do Python do Proclamare.
function Nova-Dacl-Protegida {
    $sec = New-Object System.Security.AccessControl.DirectorySecurity
    $sec.SetAccessRuleProtection($true, $false)
    $heranca = [System.Security.AccessControl.InheritanceFlags]::ContainerInherit -bor [System.Security.AccessControl.InheritanceFlags]::ObjectInherit
    foreach ($s in @(@($SidUsuario, 'S-1-5-18', 'S-1-5-32-544') | Select-Object -Unique)) {
        $regra = New-Object System.Security.AccessControl.FileSystemAccessRule(
            (New-Object System.Security.Principal.SecurityIdentifier($s)),
            [System.Security.AccessControl.FileSystemRights]::FullControl, $heranca,
            [System.Security.AccessControl.PropagationFlags]::None,
            [System.Security.AccessControl.AccessControlType]::Allow)
        $sec.AddAccessRule($regra)
    }
    return $sec
}

# Cria uma pasta NOVA ja protegida (nasce com as permissoes certas, sem janela em que outro usuario consiga
# gravar nela). Falha em vez de seguir com uma pasta aberta a todos.
function Criar-PastaProtegida([string]$Caminho) {
    $sec = Nova-Dacl-Protegida
    try {
        try {
            [void][System.IO.Directory]::CreateDirectory($Caminho, $sec)
        } catch {
            # versao do .NET sem essa sobrecarga: cria e aplica as permissoes logo em seguida
            if (-not (Test-Path -LiteralPath $Caminho -PathType Container)) { [void][System.IO.Directory]::CreateDirectory($Caminho) }
            Set-Acl -LiteralPath $Caminho -AclObject $sec
        }
        if (-not (Dono-Eh-Confiavel $Caminho) -or (Pasta-Gravavel-Por-Todos $Caminho)) {
            throw 'as permissões continuaram abertas a outros usuários'
        }
    } catch {
        throw ("Não consegui proteger a pasta $Caminho (só você e os administradores devem poder alterá-la): " +
            $_.Exception.Message + "`n    Use -PastaPython com uma pasta de um disco NTFS dentro da sua pasta de usuário.")
    }
}

# Tenta mover uma pasta (mesmo disco). Antivirus e SmartScreen costumam segurar arquivos recem-gravados
# ou recem-executados por alguns segundos e o renome falha com "acesso negado": tenta de novo, com pausas.
# Se a origem sumiu ou o destino ja existe, esperar nao adianta: falha na hora.
function Mover-Pasta([string]$De, [string]$Para, [int]$Tentativas = 8) {
    for ($i = 1; $i -le $Tentativas; $i++) {
        try {
            [System.IO.Directory]::Move($De, $Para)
            return
        } catch [System.IO.IOException], [System.UnauthorizedAccessException] {
            if ($i -eq $Tentativas -or -not (Test-Path -LiteralPath $De) -or (Test-Path -LiteralPath $Para)) { throw }
            Start-Sleep -Milliseconds (500 * $i)
        }
    }
}

# Confere as assinaturas digitais dos arquivos principais de uma pasta com o Python do Proclamare.
# Devolve $null se estiver tudo certo, ou o texto do problema.
function Conferir-Assinaturas([string]$Pasta) {
    $itens = @(
        @('python.exe', $PyAssinante), @('pythonw.exe', $PyAssinante), @($PyDll, $PyAssinante),
        @('vcruntime140.dll', 'Microsoft Corporation')
    )
    # Os "launchers" que o venv copia para o .venv (o nome muda entre versoes do Python): todos conferidos.
    $pastaLaunchers = Join-Path $Pasta 'Lib\venv\scripts\nt'
    $launchers = @()
    if (Test-Path -LiteralPath $pastaLaunchers -PathType Container) {
        $launchers = @(Get-ChildItem -LiteralPath $pastaLaunchers -Filter '*.exe' -File -ErrorAction SilentlyContinue)
    }
    if ($launchers.Count -eq 0) { return 'faltaram os arquivos do ambiente virtual (venv)' }
    foreach ($l in $launchers) { $itens += , @(('Lib\venv\scripts\nt\' + $l.Name), $PyAssinante) }
    foreach ($item in $itens) {
        $arq = Join-Path $Pasta $item[0]
        if (-not (Test-Path -LiteralPath $arq -PathType Leaf)) { return "faltou o arquivo $($item[0])" }
        $sig = Get-AuthenticodeSignature -LiteralPath $arq
        if ($sig.Status -ne 'Valid') { return "a assinatura de $($item[0]) não é válida ($($sig.Status))" }
        # Organizacao (O=) do certificado de quem assinou, ex.: "CN=Python Software Foundation, O=Python Software Foundation, ..."
        $assunto = [string]$sig.SignerCertificate.Subject
        if ($assunto -notmatch ('(^|,\s*)O=' + [regex]::Escape($item[1]) + '(,|$)')) {
            return "$($item[0]) está assinado por '$assunto' e não por '$($item[1])'"
        }
    }
    return $null
}

# ---- Ferramentas do Python do Proclamare (usadas na instalacao e na desinstalacao) ----
# True se o caminho for um link/juncao (nunca seguimos links ao apagar ou usar pastas).
function Pasta-Eh-Link([string]$Caminho) {
    try {
        $item = Get-Item -LiteralPath $Caminho -Force -ErrorAction Stop
        return [bool]($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint)
    } catch { return $false }
}

# Le o marcador (.proclamare-python.json) de uma pasta; $null se nao existir ou estiver ilegivel.
function Ler-Marcador([string]$Pasta) {
    $arq = Join-Path $Pasta $PyMarcador
    if (-not (Test-Path -LiteralPath $arq -PathType Leaf)) { return $null }
    try {
        $m = Get-Content -LiteralPath $arq -Raw -Encoding UTF8 -ErrorAction Stop | ConvertFrom-Json
        if ($m -and $m.produto -eq 'Proclamare') { return $m }
    } catch { }
    return $null
}

# Apaga uma pasta SO se ela tiver o marcador do Proclamare e nao for link. Primeiro renomeia
# (se algum arquivo estiver em uso o renome falha e nada e apagado), depois apaga.
function Apagar-Pasta-Marcada([string]$Pasta) {
    if (-not (Test-Path -LiteralPath $Pasta -PathType Container)) { return $false }
    if (Pasta-Eh-Link $Pasta) {
        Write-Host "    $Pasta é um atalho (link); por segurança nada foi apagado." -ForegroundColor Yellow
        return $false
    }
    if (-not (Ler-Marcador $Pasta)) {
        Write-Host "    $Pasta não foi criada pelo Proclamare; por segurança nada foi apagado." -ForegroundColor Yellow
        return $false
    }
    $lixo = Join-Path (Split-Path -Parent $Pasta) ('.removendo-' + [guid]::NewGuid().ToString('N').Substring(0, 8))
    try {
        Mover-Pasta $Pasta $lixo 4   # poucas tentativas: se o Proclamare estiver ligado, o aviso vem logo
    } catch {
        Write-Host ("    Não foi possível apagar $Pasta : algum arquivo está em uso.`n" +
            "    Desligue o Proclamare (Menu Iniciar > Desligar Proclamare), feche o painel`n" +
            "    e execute a desinstalação de novo.") -ForegroundColor Yellow
        return $false
    }
    try { [System.IO.Directory]::Delete($lixo, $true) } catch {
        Write-Host "    Aviso: sobraram arquivos em $lixo ; pode apagar essa pasta à mão." -ForegroundColor Yellow
    }
    Escrever-Ok "removido: $Pasta"
    return $true
}

# Pastas python-* com o marcador do Proclamare na pasta-mae do Python do Proclamare
# (inclui a atual e versoes antigas deixadas por atualizacoes do instalador).
function Listar-PythonsDoProclamare {
    $lista = @()
    if (-not (Test-Path -LiteralPath $PastaPythonPai -PathType Container)) { return $lista }
    $candidatas = @($PastaPythonPriv)
    foreach ($d in @(Get-ChildItem -LiteralPath $PastaPythonPai -Directory -Filter 'python-*' -Force -ErrorAction SilentlyContinue)) {
        $candidatas += $d.FullName
    }
    foreach ($c in ($candidatas | Select-Object -Unique)) {
        if ((Test-Path -LiteralPath $c -PathType Container) -and -not (Pasta-Eh-Link $c) -and (Ler-Marcador $c)) {
            $lista += $c
        }
    }
    return $lista
}

# Pede ao lancador para desligar o servidor (so encerra o processo validado por nome + hora de criacao).
function Desligar-Servidor([string]$PythonwExe) {
    if (-not (Test-Path -LiteralPath (Join-Path $Raiz 'logs\proclamare.pid'))) { return }
    if (-not $PythonwExe -or -not (Test-Path -LiteralPath $PythonwExe)) { return }
    Escrever-Passo 'Desligando o Proclamare antes de apagar arquivos...'
    $anterior = $env:PROCLAMARE_NO_BROWSER
    try {
        $env:PROCLAMARE_NO_BROWSER = '1'   # sem caixa de mensagem: o aviso vai so para a saida oculta
        $proc = Start-Process -FilePath $PythonwExe -ArgumentList ('"' + $Pyw + '" --parar') `
            -WorkingDirectory $Raiz -WindowStyle Hidden -PassThru
        $null = $proc.Handle
        if (-not $proc.WaitForExit(30000)) { try { $proc.Kill() } catch { } }
    } catch { } finally {
        if ($null -eq $anterior) { Remove-Item Env:\PROCLAMARE_NO_BROWSER -ErrorAction SilentlyContinue }
        else { $env:PROCLAMARE_NO_BROWSER = $anterior }
    }
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
    $venvApagado = $false
    if (Test-Path -LiteralPath $venv) {
        $resp = Read-Host 'Apagar também o ambiente .venv (as dependências instaladas)? (S/N)'
        if ($resp -match '^\s*[sSyY]') {
            # Só apaga se for mesmo a pasta .venv dentro da raiz e não for um link.
            $item = Get-Item -LiteralPath $venv -Force
            $ehLink = [bool]($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint)
            if ($item.Name -eq '.venv' -and $item.Parent.FullName -eq $Raiz -and -not $ehLink) {
                # Desliga o servidor antes (senão os arquivos do .venv ficam em uso).
                Desligar-Servidor (Join-Path $venv 'Scripts\pythonw.exe')
                try {
                    Remove-Item -LiteralPath $venv -Recurse -Force -ErrorAction Stop
                    Escrever-Ok '.venv apagado.'
                    $venvApagado = $true
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

    # Python que o proprio Proclamare baixou (nunca um Python instalado pelo usuario).
    $privados = @(Listar-PythonsDoProclamare)
    if ($privados.Count -gt 0) {
        $queRemover = [bool]$RemoverPythonPrivado
        if (-not $queRemover) {
            Write-Host ''
            Write-Host 'Este computador tem uma cópia do Python baixada pelo Proclamare (só para ele; não afeta outros programas):'
            foreach ($p in $privados) { Write-Host "    $p" }
            Write-Host 'Se você tiver outra cópia do Proclamare neste computador, ela precisará rodar o instalador de novo.'
            $resp = Read-Host 'Apagar também esse Python do Proclamare? (S/N)'
            $queRemover = $resp -match '^\s*[sSyY]'
        }
        if ($queRemover) {
            # O servidor usa os arquivos do Python: desliga antes (pelo .venv, se ainda existir, ou pelo proprio Python).
            if (-not $venvApagado -and (Test-Path -LiteralPath $venv)) {
                Desligar-Servidor (Join-Path $venv 'Scripts\pythonw.exe')
            } else {
                # So executa o pythonw.exe dessa pasta depois de conferir a assinatura digital dele.
                $problemaAss = Conferir-Assinaturas $privados[0]
                if ($problemaAss) {
                    Write-Host ("    Não vou executar o Python do Proclamare para desligar o servidor ($problemaAss).`n" +
                        "    Se o Proclamare estiver ligado, desligue-o antes pelo atalho 'Desligar Proclamare'.") -ForegroundColor Yellow
                } else {
                    Desligar-Servidor (Join-Path $privados[0] 'pythonw.exe')
                }
            }
            Escrever-Passo 'Removendo o Python do Proclamare...'
            foreach ($p in $privados) { [void](Apagar-Pasta-Marcada $p) }
            if ((Test-Path -LiteralPath $venv) -and -not $venvApagado) {
                Write-Host ("    O .venv foi mantido, mas dependia desse Python e deixa de funcionar até você rodar`n" +
                    '    o instalador de novo (ele reconstrói o .venv sozinho).') -ForegroundColor Yellow
            }
            # Remove a pasta-mae se ficou vazia (e so se ficou vazia).
            try {
                if ((Test-Path -LiteralPath $PastaPythonPai -PathType Container) -and
                    -not (Get-ChildItem -LiteralPath $PastaPythonPai -Force -ErrorAction SilentlyContinue | Select-Object -First 1)) {
                    [System.IO.Directory]::Delete($PastaPythonPai, $false)
                }
            } catch { }
        } else {
            Write-Host '    Python do Proclamare mantido.'
        }
    }
    Write-Host "`nProclamare desinstalado. A pasta do programa e o config.json foram mantidos." -ForegroundColor Green
    if (-not $PastaAtalhos) { [void](Read-Host 'Pressione Enter para fechar') }
    exit 0
}

if (-not (Test-Path -LiteralPath $Pyw)) { Falhar "Não encontrei o Proclamare.pyw em $Raiz." }
if (-not (Test-Path -LiteralPath $Icone)) { Falhar "Não encontrei o ícone em $Icone." }

# ---- Aviso de seguranca: pasta do programa gravavel por grupos amplos ---
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

# ---- 1. Python: procurar o que ja existe e, so se nao houver, baixar -----
# Coloca aspas num argumento de linha de comando do Windows (regras do CommandLineToArgvW).
function Citar-Argumento([string]$Arg) {
    if ($Arg -ne '' -and $Arg -notmatch '[\s"]') { return $Arg }
    $r = $Arg -replace '(\\*)"', '$1$1\"'
    $r = $r -replace '(\\+)$', '$1$1'
    return '"' + $r + '"'
}

# Roda um programa SEM janela, com stdin fechado (nenhuma pergunta pode travar) e com tempo maximo.
# Devolve @{ Codigo; Saida; Erro; Estourou }.
function Rodar-Processo([string]$Exe, [string[]]$Argumentos, [int]$SegundosMax) {
    $res = @{ Codigo = -1; Saida = ''; Erro = ''; Estourou = $false }
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = $Exe
    $psi.Arguments = (@($Argumentos | ForEach-Object { Citar-Argumento ([string]$_) }) -join ' ')
    $psi.UseShellExecute = $false
    $psi.CreateNoWindow = $true
    # Pasta de trabalho neutra (do sistema): nenhum arquivo solto na pasta atual (Downloads, o proprio
    # projeto...) consegue ser carregado no lugar de um modulo do Python.
    $psi.WorkingDirectory = [Environment]::SystemDirectory
    $psi.RedirectStandardInput = $true
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $psi.StandardOutputEncoding = [System.Text.Encoding]::UTF8
    $psi.StandardErrorEncoding = [System.Text.Encoding]::UTF8
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

# Pythons do computador que foram vistos mas NAO servem, para explicar ao usuario por que vai baixar outro.
# Cada item: @{ Tipo = 'versao' | 'semvenv' | 'inseguro'; Texto = '...' }
$script:PythonsRejeitados = @()

function Registrar-Rejeitado([string]$Tipo, [string]$Texto) {
    foreach ($r in $script:PythonsRejeitados) { if ($r.Tipo -eq $Tipo -and $r.Texto -eq $Texto) { return } }
    $script:PythonsRejeitados += , @{ Tipo = $Tipo; Texto = $Texto }
}

# Testa um interpretador; devolve @{ Caminho; Versao } se for 3.10+ (ate 3.13 com o lock), com venv e
# pip, e nao for o alias da Microsoft Store. O Python mais novo (ex.: 3.14) e recusado SEM ser alterado.
# O programa so e EXECUTADO se o arquivo e a pasta forem de um dono confiavel e a pasta nao puder ser
# alterada por qualquer usuario (Motivo-Inseguro): nunca executa um python.exe "plantado" por outra conta.
function Testar-Python([string]$Exe) {
    if (-not $Exe -or $Exe -like '*\WindowsApps\*') { return $null }   # nunca executa o alias da Store
    if (-not (Test-Path -LiteralPath $Exe -PathType Leaf)) { return $null }
    $motivo = Motivo-Inseguro $Exe
    if ($motivo) {
        Registrar-Rejeitado 'inseguro' "$Exe ($motivo)"
        return $null
    }
    # Com o lock (hashes), so as versoes em que ele foi testado: 3.10 a 3.13. Em Python mais novo
    # (ex.: 3.14) o pygame nao tem wheel e o pip compilaria o codigo-fonte, com dependencias de
    # build SEM verificacao de hash.
    $limite = if (Test-Path -LiteralPath $TravaHashes) { '(3, 14)' } else { '(99, 0)' }
    $exigeVenv = if ($SemDependencias) { 'False' } else { 'True' }
    # So aspas simples dentro do codigo (quoting mais simples). A saida vai em bytes UTF-8 para nao
    # estragar caminhos com acento.
    $codigo = "import sys, importlib.util as u; v = sys.version_info[:3]; " +
        "dentro = (3, 10) <= v[:2] < $limite; " +
        "venv = (not $exigeVenv) or (u.find_spec('venv') is not None and u.find_spec('ensurepip') is not None); " +
        "sys.stdout.buffer.write((sys.executable + '|%d.%d.%d|' % v + " +
        "('ok' if dentro and venv else ('versao' if not dentro else 'semvenv'))).encode('utf-8'))"
    # -I (modo isolado): ignora PYTHONHOME/PYTHONPATH/sitecustomize do usuario (que fariam um Python bom
    # falhar a sondagem e o instalador baixar outro sem necessidade) e a pasta atual.
    $r = Rodar-Processo $Exe @('-I', '-c', $codigo) 30
    if ($r.Estourou -or $r.Codigo -ne 0 -or -not $r.Saida) { return $null }
    $linha = ($r.Saida -split "`r?`n" | Where-Object { $_.Trim() } | Select-Object -Last 1)
    if (-not $linha) { return $null }
    $partes = $linha.Trim().Split('|')
    if ($partes.Count -lt 3) { return $null }
    $caminho = $partes[0]
    if ($caminho -like '*\WindowsApps\*') { return $null }            # alias da Microsoft Store
    if (-not (Test-Path -LiteralPath $caminho)) { return $null }
    if ($partes[2] -eq 'versao') {
        Registrar-Rejeitado 'versao' "Python $($partes[1]) em $caminho"
        return $null
    }
    if ($partes[2] -eq 'semvenv') {
        # Ex.: instalado sem a opcao "pip" ou sem o modulo venv: nao da para criar o .venv com ele.
        Registrar-Rejeitado 'semvenv' "Python $($partes[1]) em $caminho"
        return $null
    }
    if ($partes[2] -ne 'ok') { return $null }
    return @{ Caminho = $caminho; Versao = $partes[1]; Privado = $false }
}

# Preferencia entre versoes (a mesma de antes): 3.12, 3.11, 3.13, 3.10.
function Ordem-Versao([string]$Tag) {
    $ordem = @('3.12', '3.11', '3.13', '3.10')
    for ($i = 0; $i -lt $ordem.Count; $i++) {
        if ($Tag -match ('(^|[^0-9])' + [regex]::Escape($ordem[$i]) + '([^0-9]|$)')) { return $i }
    }
    return 9
}

function Ordenar-Candidatos($Lista) {
    return @($Lista | Sort-Object @{ Expression = { Ordem-Versao $_.Tag } }, @{ Expression = { $_.Seq } })
}

# Fonte 1: py launcher / Python install manager (so LISTA os instalados com "py -0p"; nao instala nada).
function Candidatos-Py {
    $lista = @()
    $cmd = Get-Command py.exe -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $cmd) { return $lista }
    # O proprio py.exe tambem e executado: so se for de dono confiavel e numa pasta que ninguem mais altera.
    if (Motivo-Inseguro $cmd.Source) { return $lista }
    $r = Rodar-Processo $cmd.Source @('-0p') 20
    if ($r.Estourou) { return $lista }
    $seq = 0
    foreach ($linha in (($r.Saida + "`n" + $r.Erro) -split "`r?`n")) {
        # formatos: " -V:3.12 *   C:\...\python.exe" (novo) e " -3.12-64   C:\...\python.exe *" (antigo)
        if ($linha -match '^\s*-\S+\s+\*?\s*([A-Za-z]:\\.+?\.[eE][xX][eE])(\s+\*)?\s*$') {
            $seq++
            $lista += @{ Exe = $Matches[1].Trim(); Tag = ($linha.Trim() -split '\s+')[0]; Seq = $seq }
        }
    }
    return (Ordenar-Candidatos $lista)
}

# Fonte 2: todos os "python.exe" do PATH (o alias da Store e ignorado em Testar-Python).
function Candidatos-Path {
    $lista = @()
    $seq = 0
    foreach ($c in @(Get-Command python.exe -All -CommandType Application -ErrorAction SilentlyContinue)) {
        $seq++
        $lista += @{ Exe = $c.Source; Tag = ''; Seq = $seq }
    }
    return $lista
}

# Fonte 3: registro (PEP 514), SOMENTE LEITURA. Ex.: HKCU\Software\Python\PythonCore\3.12\InstallPath.
function Candidatos-Registro {
    $lista = @()
    $seq = 0
    $raizes = @('HKCU:\Software\Python\PythonCore', 'HKLM:\Software\Python\PythonCore',
        'HKLM:\Software\WOW6432Node\Python\PythonCore')
    foreach ($raiz in $raizes) {
        if (-not (Test-Path -LiteralPath $raiz)) { continue }
        foreach ($chave in @(Get-ChildItem -LiteralPath $raiz -ErrorAction SilentlyContinue)) {
            try {
                $props = Get-ItemProperty -LiteralPath ($chave.PSPath + '\InstallPath') -ErrorAction Stop
                $exe = $props.ExecutablePath
                if (-not $exe -and $props.'(default)') { $exe = Join-Path $props.'(default)' 'python.exe' }
                if ($exe) { $seq++; $lista += @{ Exe = [string]$exe; Tag = $chave.PSChildName; Seq = $seq } }
            } catch { }
        }
    }
    return (Ordenar-Candidatos $lista)
}

# Fonte 4: pastas padrao dos instaladores (python.org e Python install manager). A raiz do disco
# (C:\Python312...) NAO entra: o instalador do python.org nao a usa por padrao e, nela, qualquer usuario
# do computador costuma poder criar uma pasta e "plantar" um python.exe falso.
function Candidatos-Pastas {
    $lista = @()
    $seq = 0
    foreach ($m in 12, 11, 13, 10) {
        $tag = "3.$m"
        $exes = @()
        if ($env:LOCALAPPDATA) {
            $exes += Join-Path $env:LOCALAPPDATA "Programs\Python\Python3$m\python.exe"
            $exes += Join-Path $env:LOCALAPPDATA "Python\pythoncore-3.$m-64\python.exe"
        }
        if ($env:ProgramFiles) { $exes += Join-Path $env:ProgramFiles "Python3$m\python.exe" }
        foreach ($e in $exes) { $seq++; $lista += @{ Exe = $e; Tag = $tag; Seq = $seq } }
    }
    return $lista
}

# Python do Proclamare ja baixado: marcador + versao + executa + assinaturas. $null se nao servir.
function Testar-PythonPrivado {
    $exe = [System.IO.Path]::Combine($PastaPythonPriv, 'python.exe')
    if (-not (Test-Path -LiteralPath $exe -PathType Leaf)) { return $null }
    if (Pasta-Eh-Link $PastaPythonPriv) { return $null }
    $m = Ler-Marcador $PastaPythonPriv
    if (-not $m -or $m.versao -ne $PyVersao) { return $null }
    # Falha fechada (nada e executado antes de conferir): a pasta precisa ser do proprio usuario ou dos
    # administradores e nao pode estar aberta para escrita a outros usuarios (o instalador a cria assim).
    if (Motivo-Inseguro $exe) { return $null }
    # A assinatura digital e conferida ANTES de executar qualquer coisa dessa pasta.
    if (Conferir-Assinaturas $PastaPythonPriv) { return $null }
    $r = Rodar-Processo $exe @('-I', '-B', '-c', "import sys, ssl, venv, ensurepip; sys.stdout.write('%d.%d.%d' % sys.version_info[:3])") 30
    if ($r.Estourou -or $r.Codigo -ne 0 -or $r.Saida.Trim() -ne $PyVersao) { return $null }
    return @{ Caminho = $exe; Versao = $PyVersao; Privado = $true }
}

# Procura um Python compativel: primeiro os do computador, por ultimo o Python do Proclamare.
function Achar-Python {
    $script:PythonsRejeitados = @()
    if (-not $IgnorarPythonDoSistema) {
        $vistos = @{}
        $todos = @()
        $todos += Candidatos-Py
        $todos += Candidatos-Path
        $todos += Candidatos-Registro
        $todos += Candidatos-Pastas
        foreach ($c in $todos) {
            $chave = ([string]$c.Exe).ToLowerInvariant()
            if ($vistos.ContainsKey($chave)) { continue }
            $vistos[$chave] = $true
            $p = Testar-Python $c.Exe
            if ($p) { return $p }
        }
    }
    return (Testar-PythonPrivado)
}

# ---- Baixar e preparar o Python do Proclamare ---------------------------
function Mensagem-Manual {
    return ("    Se não for possível, instale o Python 3.12 manualmente em https://www.python.org/downloads/`n" +
        "    e, na primeira tela do instalador, MARQUE a opção 'Add python.exe to PATH'.`n" +
        '    Depois execute este instalador de novo.')
}

# Dica para quando o Python baixado e recusado ou nao roda: a causa mais comum e o antivirus.
function Mensagem-Antivirus {
    return ("    Se você usa antivírus, ele pode ter bloqueado o Python baixado; tente de novo.`n" +
        '    Em um Windows sem atualizações recentes a conferência da assinatura digital também pode falhar: rode o Windows Update.')
}

function Mensagem-SemInternet([System.Exception]$Ex) {
    # O PowerShell embrulha erros de metodos .NET (MethodInvocationException): desembrulha ate o WebException
    while ($Ex.InnerException -and -not ($Ex -is [System.Net.WebException])) { $Ex = $Ex.InnerException }
    $detalhe = $Ex.Message
    if ($Ex -is [System.Net.WebException]) {
        if ($Ex.Status -eq [System.Net.WebExceptionStatus]::ProtocolError -and $Ex.Response) {
            $detalhe = "o servidor respondeu com erro $([int]$Ex.Response.StatusCode)"
        } else {
            $detalhe = "$($Ex.Status): $($Ex.Message)"
        }
    }
    return ("Não consegui baixar o Python (sem internet, ou a rede bloqueia o endereço nuget.org).`n" +
        "    Conecte-se à internet e execute este instalador de novo.`n" +
        (Mensagem-Manual) + "`n    (Detalhe técnico: $detalhe)")
}

function Host-Permitido([string]$Servidor) {
    $s = $Servidor.ToLowerInvariant()
    return ($s -eq 'nuget.org') -or $s.EndsWith('.nuget.org')
}

# Baixa $Url para $Destino (arquivo novo). So HTTPS, so hosts do nuget.org, tamanho exato, tempo maximo.
function Baixar-Arquivo([string]$Url, [string]$Destino, [long]$TamanhoEsperado, [string]$Rotulo) {
    # TLS 1.2 ou mais novo (nunca SSL3/TLS 1.0/1.1)
    $proto = [System.Net.SecurityProtocolType]::Tls12
    try {
        [System.Net.ServicePointManager]::SecurityProtocol = $proto -bor ([System.Net.SecurityProtocolType]12288)
    } catch {
        [System.Net.ServicePointManager]::SecurityProtocol = $proto
    }
    $uri = [System.Uri]$Url
    if ($uri.Scheme -ne 'https' -or -not (Host-Permitido $uri.Host)) { throw "Endereço de download não permitido: $Url" }

    $req = [System.Net.HttpWebRequest][System.Net.WebRequest]::Create($uri)
    $req.UserAgent = 'Proclamare-instalador'
    $req.Timeout = 30000            # conectar / receber o cabecalho
    $req.ReadWriteTimeout = 30000   # tempo maximo parado sem receber dados
    $req.AllowAutoRedirect = $true
    $req.MaximumAutomaticRedirections = 3
    $req.KeepAlive = $false
    # Respeita o proxy configurado no Windows (e as credenciais do usuario, se o proxy pedir)
    $req.Proxy = [System.Net.WebRequest]::GetSystemWebProxy()
    $req.Proxy.Credentials = [System.Net.CredentialCache]::DefaultCredentials

    try { $resp = $req.GetResponse() } catch { throw (Mensagem-SemInternet $_.Exception) }
    $entrada = $null
    $saida = $null
    try {
        $final = $resp.ResponseUri
        if ($final.Scheme -ne 'https' -or -not (Host-Permitido $final.Host)) {
            throw "O download foi desviado para um endereço não permitido ($($final.Host)); por segurança ele foi cancelado."
        }
        if ($resp.ContentLength -ge 0 -and $resp.ContentLength -ne $TamanhoEsperado) {
            throw "O arquivo no servidor tem tamanho diferente do esperado ($($resp.ContentLength) bytes em vez de $TamanhoEsperado); por segurança o download foi cancelado."
        }
        $entrada = $resp.GetResponseStream()
        $saida = [System.IO.File]::Open($Destino, [System.IO.FileMode]::CreateNew, [System.IO.FileAccess]::Write, [System.IO.FileShare]::None)
        $buf = New-Object byte[] 65536
        $total = [long]0
        $relogio = [System.Diagnostics.Stopwatch]::StartNew()
        $ultimaTela = [long]-1000
        $proximoMarco = 25
        $totalMB = '{0:N1}' -f ($TamanhoEsperado / 1MB)
        while ($true) {
            try { $n = $entrada.Read($buf, 0, $buf.Length) } catch { throw (Mensagem-SemInternet $_.Exception) }
            if ($n -le 0) { break }
            $total += $n
            if ($total -gt $TamanhoEsperado) {
                throw 'O arquivo recebido é maior que o esperado; por segurança o download foi cancelado.'
            }
            try { $saida.Write($buf, 0, $n) } catch {
                throw ("Não foi possível gravar o arquivo no disco (disco cheio ou sem permissão): " + $_.Exception.Message)
            }
            if ($relogio.Elapsed.TotalSeconds -gt $PyLimiteDownloadSeg) {
                throw ("O download está demorando demais e foi cancelado.`n" + (Mensagem-SemInternet (New-Object System.TimeoutException 'tempo total esgotado')))
            }
            if ($relogio.ElapsedMilliseconds - $ultimaTela -ge 200) {
                $ultimaTela = $relogio.ElapsedMilliseconds
                $pct = [int](100 * $total / $TamanhoEsperado)
                Write-Progress -Activity "Baixando $Rotulo" -Status ('{0:N1} de {1} MB' -f ($total / 1MB), $totalMB) -PercentComplete $pct
                if ($pct -ge $proximoMarco -and $pct -lt 100) {
                    Write-Host ("    ... {0}% ({1:N1} de {2} MB)" -f $pct, ($total / 1MB), $totalMB)
                    $proximoMarco = (([int][math]::Floor($pct / 25)) + 1) * 25
                }
            }
        }
        Write-Progress -Activity "Baixando $Rotulo" -Completed
        if ($total -ne $TamanhoEsperado) {
            throw "O download terminou incompleto ($total de $TamanhoEsperado bytes). Execute o instalador de novo."
        }
        Write-Host ("    ... 100% ({0} MB)" -f $totalMB)
    } finally {
        if ($saida) { $saida.Dispose() }
        if ($entrada) { $entrada.Dispose() }
        $resp.Close()
    }
}

# Abre o arquivo baixado UMA vez, so para leitura e sem deixar ninguem gravar nele enquanto estiver aberto
# (FileShare.Read). O mesmo fluxo e usado para conferir os hashes e para extrair: o arquivo conferido e
# exatamente o que e extraido (nao da para troca-lo no meio). Tenta de novo se o antivirus o estiver segurando.
function Abrir-Arquivo-Leitura([string]$Arquivo) {
    for ($tentativa = 1; $tentativa -le 5; $tentativa++) {
        try {
            return [System.IO.File]::Open($Arquivo, [System.IO.FileMode]::Open, [System.IO.FileAccess]::Read, [System.IO.FileShare]::Read)
        } catch [System.IO.IOException] {
            if ($tentativa -eq 5) { throw }
            Start-Sleep -Seconds 1
        }
    }
}

# SHA-256 (hex minusculo) e SHA-512 (base64, como o nuget.org publica) de um fluxo (do inicio ao fim).
# No fim o fluxo volta para o comeco.
function Calcular-Hashes([System.IO.Stream]$Fluxo) {
    $sha256 = [System.Security.Cryptography.SHA256]::Create()
    $sha512 = [System.Security.Cryptography.SHA512]::Create()
    try {
        $Fluxo.Position = 0
        $buf = New-Object byte[] 262144
        while (($n = $Fluxo.Read($buf, 0, $buf.Length)) -gt 0) {
            [void]$sha256.TransformBlock($buf, 0, $n, $null, 0)
            [void]$sha512.TransformBlock($buf, 0, $n, $null, 0)
        }
        [void]$sha256.TransformFinalBlock($buf, 0, 0)
        [void]$sha512.TransformFinalBlock($buf, 0, 0)
        $Fluxo.Position = 0
        return @{
            Sha256 = ([System.BitConverter]::ToString($sha256.Hash) -replace '-', '').ToLowerInvariant()
            Sha512 = [System.Convert]::ToBase64String($sha512.Hash)
        }
    } finally {
        $sha256.Dispose()
        $sha512.Dispose()
    }
}

# Extrai SO a pasta tools\ do .nupkg (zip, ja aberto como fluxo) para $Destino (pasta nova, criada ja
# protegida), sem Zip Slip: cada caminho e recalculado e precisa ficar dentro de $Destino; nomes suspeitos,
# tamanhos absurdos e duplicados param tudo.
function Extrair-Pacote([System.IO.Stream]$Fluxo, [string]$Destino) {
    Add-Type -AssemblyName System.IO.Compression
    Criar-PastaProtegida $Destino
    $raizDest = [System.IO.Path]::GetFullPath($Destino).TrimEnd('\') + '\'
    $Fluxo.Position = 0
    $zip = New-Object System.IO.Compression.ZipArchive($Fluxo, [System.IO.Compression.ZipArchiveMode]::Read, $true)
    try {
        $entradas = @($zip.Entries | Where-Object {
                $_.FullName.StartsWith('tools/', [System.StringComparison]::Ordinal) -and
                -not $_.FullName.EndsWith('/', [System.StringComparison]::Ordinal) })
        if ($entradas.Count -lt 200 -or $entradas.Count -gt 5000) { throw "O pacote tem $($entradas.Count) arquivos (esperado cerca de 1300)." }
        $somaDeclarada = [long]0
        foreach ($e in $entradas) {
            if ($e.Length -gt 50MB) { throw "O pacote traz um arquivo grande demais ($($e.FullName))." }
            $somaDeclarada += $e.Length
        }
        if ($somaDeclarada -gt 150MB) { throw 'O conteúdo do pacote é maior que o esperado.' }

        $buf = New-Object byte[] 81920
        $feitos = 0
        foreach ($e in $entradas) {
            $rel = $e.FullName.Substring(6)
            $suspeito = $rel.Contains('\') -or $rel.Contains(':') -or $rel.StartsWith('/') -or
                ($rel.Split('/') -contains '..') -or ($rel.Split('/') -contains '.') -or ($rel.Split('/') -contains '')
            if ($suspeito) { throw "O pacote traz um nome de arquivo suspeito ($($e.FullName))." }
            $alvo = [System.IO.Path]::GetFullPath((Join-Path $raizDest $rel.Replace('/', '\')))
            if (-not $alvo.StartsWith($raizDest, [System.StringComparison]::OrdinalIgnoreCase)) {
                throw "O pacote tentou gravar fora da pasta de destino ($($e.FullName))."
            }
            if ($alvo.Length -ge 260) {
                throw ("O caminho da pasta escolhida é longo demais para o Python (o Windows limita a 260 letras).`n" +
                    '    Use uma pasta mais curta (ex.: C:\Proclamare\python) com -PastaPython.')
            }
            [void][System.IO.Directory]::CreateDirectory([System.IO.Path]::GetDirectoryName($alvo))
            $in = $e.Open()
            try {
                $out = [System.IO.File]::Open($alvo, [System.IO.FileMode]::CreateNew, [System.IO.FileAccess]::Write, [System.IO.FileShare]::None)
                try {
                    $lidos = [long]0
                    while (($n = $in.Read($buf, 0, $buf.Length)) -gt 0) {
                        $lidos += $n
                        if ($lidos -gt $e.Length) { throw "O arquivo $($e.FullName) é maior do que o declarado no pacote." }
                        try { $out.Write($buf, 0, $n) } catch {
                            throw ('Não foi possível gravar os arquivos do Python no disco (disco cheio ou sem permissão): ' + $_.Exception.Message)
                        }
                    }
                    if ($lidos -ne $e.Length) { throw "O arquivo $($e.FullName) está incompleto no pacote." }
                } finally { $out.Dispose() }
            } finally { $in.Dispose() }
            $feitos++
            if (($feitos % 100) -eq 0) {
                Write-Progress -Activity 'Descompactando o Python' -Status "$feitos de $($entradas.Count) arquivos" -PercentComplete ([int](100 * $feitos / $entradas.Count))
            }
        }
        Write-Progress -Activity 'Descompactando o Python' -Completed
    } finally {
        $zip.Dispose()
    }
}

function Espaco-Livre-MB([string]$Pasta) {
    try {
        $raiz = [System.IO.Path]::GetPathRoot([System.IO.Path]::GetFullPath($Pasta))
        if (-not $raiz -or $raiz.StartsWith('\\')) { return $null }
        return [long]((New-Object System.IO.DriveInfo($raiz)).AvailableFreeSpace / 1MB)
    } catch { return $null }
}

# False so se o disco for conhecido e NAO guardar permissoes de arquivo (ex.: FAT32/exFAT de um pendrive).
# Em pasta de rede (\\servidor\...) ou se nao der para saber, deixa a conferencia das permissoes decidir.
function Disco-Suporta-Permissoes([string]$Pasta) {
    try {
        $raiz = [System.IO.Path]::GetPathRoot([System.IO.Path]::GetFullPath($Pasta))
        if (-not $raiz -or $raiz.StartsWith('\\')) { return $true }
        $formato = (New-Object System.IO.DriveInfo($raiz)).DriveFormat
        return ($formato -eq 'NTFS' -or $formato -eq 'ReFS')
    } catch { return $true }
}

# Baixa e prepara o Python do Proclamare. Tudo acontece numa pasta temporaria ao lado do destino; so
# depois de hash, assinatura e execucao conferidos ela e RENOMEADA para o destino final (de uma vez):
# nunca fica uma instalacao pela metade. Em qualquer erro lanca uma mensagem (e apaga o temporario).
function Instalar-PythonPrivado {
    if (-not [Environment]::Is64BitOperatingSystem) {
        throw ("Este Windows é de 32 bits e o Python automático do Proclamare é de 64 bits.`n" + (Mensagem-Manual))
    }
    if ($PastaPythonPriv.Length -lt 4 -or $PastaPythonPriv -eq [System.IO.Path]::GetPathRoot($PastaPythonPriv) -or
        $Raiz.StartsWith($PastaPythonPriv.TrimEnd('\') + '\', [System.StringComparison]::OrdinalIgnoreCase) -or
        $PastaPythonPriv -eq $Raiz) {
        throw "A pasta do Python ($PastaPythonPriv) não é válida: ela não pode ser a raiz de um disco nem conter o Proclamare."
    }
    # O arquivo mais fundo do pacote tem $PyCaminhoMaisLongo letras de caminho; a pasta temporaria soma mais 22. O Windows
    # (sem "caminhos longos" ligados) limita tudo a 260: melhor avisar antes de baixar.
    if (($PastaPythonPai.Length + 23 + $PyCaminhoMaisLongo) -ge 260) {
        throw ("O caminho da pasta do Python é longo demais (o Windows limita a 260 letras).`n" +
            '    Use uma pasta mais curta (ex.: C:\Proclamare\python) com -PastaPython; ela será protegida' +
            ' automaticamente (só você e os administradores poderão alterá-la).')
    }
    $mb = [int][math]::Round($PyTamanho / 1MB)
    $criouPai = -not (Test-Path -LiteralPath $PastaPythonPai)

    # O Python do Proclamare e EXECUTADO a cada vez que o servidor liga: a pasta dele so pode ser alterada
    # por voce e pelos administradores. Isso exige um disco com permissoes (NTFS/ReFS) e que, se a
    # pasta-mae ja existir, ela seja sua ou dos administradores (quem e dono dela poderia trocar a pasta do Python depois).
    if (-not (Disco-Suporta-Permissoes $PastaPythonPai)) {
        throw ("O disco de $PastaPythonPai não guarda permissões de arquivo (não é NTFS), então não dá para proteger o Python ali.`n" +
            '    Use -PastaPython com uma pasta de um disco NTFS (ex.: dentro da sua pasta de usuário).')
    }
    if (-not $criouPai -and -not (Dono-Eh-Confiavel $PastaPythonPai)) {
        throw ("Não consegui confirmar que a pasta $PastaPythonPai pertence a você ou aos administradores; por segurança não vou guardar o Python nela.`n" +
            '    Use -PastaPython com outra pasta (ex.: dentro da sua pasta de usuário).')
    }

    # Sobras de uma tentativa anterior interrompida (so pastas com o NOSSO nome temporario e paradas ha
    # mais de meia hora, para nunca atrapalhar outra instalacao que esteja rodando agora)
    if (-not $criouPai) {
        $limiteSobra = (Get-Date).AddMinutes(-30)
        foreach ($sobra in @(Get-ChildItem -LiteralPath $PastaPythonPai -Directory -Force -ErrorAction SilentlyContinue |
                Where-Object { ($_.Name -like '.baixando-*' -or $_.Name -like '.removendo-*') -and $_.LastWriteTime -lt $limiteSobra })) {
            if (-not (Pasta-Eh-Link $sobra.FullName)) { try { [System.IO.Directory]::Delete($sobra.FullName, $true) } catch { } }
        }
    }

    $livre = Espaco-Livre-MB $PastaPythonPai
    if ($null -ne $livre -and $livre -lt 250) {
        throw "Não há espaço suficiente no disco: o Python precisa de uns 250 MB livres e há só $livre MB."
    }

    # O destino so pode ser: inexistente, vazio, ou uma pasta nossa (com marcador) que nao serviu.
    $substituir = $false
    if (Test-Path -LiteralPath $PastaPythonPriv) {
        if (-not (Test-Path -LiteralPath $PastaPythonPriv -PathType Container) -or (Pasta-Eh-Link $PastaPythonPriv)) {
            throw "Já existe algo em $PastaPythonPriv que não é uma pasta comum; por segurança não vou usá-lo. Apague-o ou use -PastaPython."
        }
        $conteudo = @(Get-ChildItem -LiteralPath $PastaPythonPriv -Force -ErrorAction SilentlyContinue | Select-Object -First 1)
        if ($conteudo.Count -eq 0) {
            [System.IO.Directory]::Delete($PastaPythonPriv, $false)
        } elseif (Ler-Marcador $PastaPythonPriv) {
            Write-Host '    A cópia do Python baixada antes está incompleta ou foi alterada; vou baixar de novo.' -ForegroundColor Yellow
            $substituir = $true
        } else {
            throw ("A pasta $PastaPythonPriv já existe e não foi criada pelo Proclamare; por segurança não vou mexer nela.`n" +
                '    Apague-a ou escolha outra com -PastaPython.')
        }
    }

    $id = [guid]::NewGuid().ToString('N').Substring(0, 8)
    $temp = Join-Path $PastaPythonPai ".baixando-$id"
    $velha = $null
    try {
        # As pastas do Python nascem PROTEGIDAS (so voce, o SYSTEM e os administradores gravam): outro usuario
        # do computador nao consegue trocar o arquivo baixado nem os arquivos extraidos antes de eles serem usados.
        if ($criouPai) { Criar-PastaProtegida $PastaPythonPai }
        Criar-PastaProtegida $temp
        $nupkg = Join-Path $temp 'python.nupkg'

        Escrever-Passo "Baixando o Python $PyVersao (cerca de $mb MB) do site oficial (nuget.org)..."
        Baixar-Arquivo $PyUrl $nupkg $PyTamanho "o Python $PyVersao"

        # O arquivo e aberto UMA vez: o que tem os hashes conferidos e exatamente o que e extraido.
        Escrever-Passo 'Conferindo se o arquivo baixado é o original (impressão digital SHA-256 e SHA-512)...'
        $pasta = Join-Path $temp 'py'
        $confere = $false
        $fluxo = Abrir-Arquivo-Leitura $nupkg
        try {
            $h = Calcular-Hashes $fluxo
            $confere = ($h.Sha256 -ceq $PySha256) -and ($h.Sha512 -ceq $PySha512Base64)
            if ($confere) {
                Escrever-Ok 'impressão digital confere com a do site oficial.'
                Escrever-Passo 'Descompactando o Python (pode levar até um minuto)...'
                Extrair-Pacote $fluxo $pasta
            }
        } finally {
            $fluxo.Dispose()
        }
        Remove-Item -LiteralPath $nupkg -Force -ErrorAction SilentlyContinue
        if (-not $confere) {
            throw ("O arquivo baixado NÃO é o esperado (a impressão digital não confere). Ele foi apagado e nada foi instalado.`n" +
                "    Pode ser uma falha de rede, um antivírus ou um proxy que altera downloads. Tente de novo em outra rede.`n" +
                (Mensagem-Manual))
        }

        Escrever-Passo 'Conferindo a assinatura digital da Python Software Foundation...'
        $problema = Conferir-Assinaturas $pasta
        if ($problema) {
            throw ("A assinatura digital do Python não confere: $problema. Nada foi instalado.`n" +
                (Mensagem-Antivirus) + "`n" + (Mensagem-Manual))
        }
        Escrever-Ok "python.exe, pythonw.exe e $PyDll assinados por '$PyAssinante'."

        Escrever-Passo 'Testando o Python baixado...'
        $exeTeste = Join-Path $pasta 'python.exe'
        $r = Rodar-Processo $exeTeste @('-I', '-B', '-c', "import sys, ssl, venv, ensurepip; sys.stdout.write('%d.%d.%d' % sys.version_info[:3])") 60
        if ($r.Estourou -or $r.Codigo -ne 0 -or $r.Saida.Trim() -ne $PyVersao) {
            throw ("O Python baixado não funcionou neste computador (esperado $PyVersao). $($r.Erro)`n" +
                (Mensagem-Antivirus) + "`n" + (Mensagem-Manual))
        }
        Escrever-Ok "Python $PyVersao funcionando."

        # Marcador: so pastas com ele podem ser apagadas depois (desinstalar / reinstalar).
        $marcador = [ordered]@{
            produto = 'Proclamare'
            versao = $PyVersao
            origem = $PyUrl
            sha256 = $PySha256
            instalado_em = (Get-Date).ToString('s')
        } | ConvertTo-Json
        [System.IO.File]::WriteAllText((Join-Path $pasta $PyMarcador), $marcador, (New-Object System.Text.UTF8Encoding($false)))

        # Publica de uma vez (renomear dentro do mesmo disco). Mover-Pasta tenta de novo por alguns segundos:
        # o antivirus costuma segurar os arquivos recem-executados e o renome falharia por "acesso negado".
        if ($substituir) {
            $velha = Join-Path $PastaPythonPai ".removendo-$id"
            try {
                Mover-Pasta $PastaPythonPriv $velha
            } catch {
                $velha = $null   # nada foi movido: o finally nao deve apagar a pasta antiga
                throw ("Não consegui trocar a cópia antiga do Python em $PastaPythonPriv : $($_.Exception.Message)`n" +
                    "    Se o Proclamare estiver ligado, desligue-o (Menu Iniciar > Desligar Proclamare) e tente de novo.`n" +
                    (Mensagem-Antivirus))
            }
        }
        try {
            Mover-Pasta $pasta $PastaPythonPriv
        } catch {
            # Outra execucao do instalador pode ter terminado primeiro: se a pasta final ja esta boa, usa ela.
            $motivoMover = $_.Exception.Message
            if (-not (Testar-PythonPrivado)) {
                throw ("Não consegui colocar o Python em $PastaPythonPriv : $motivoMover`n" +
                    (Mensagem-Antivirus) + "`n" + (Mensagem-Manual))
            }
        }
    } finally {
        foreach ($sobra in @($temp, $velha)) {
            if ($sobra -and (Test-Path -LiteralPath $sobra)) { try { [System.IO.Directory]::Delete($sobra, $true) } catch { } }
        }
        # Se esta execucao criou a pasta-mae e nada foi instalado, nao deixa pasta vazia para tras.
        if ($criouPai) {
            try {
                if ((Test-Path -LiteralPath $PastaPythonPai) -and
                    -not (Get-ChildItem -LiteralPath $PastaPythonPai -Force -ErrorAction SilentlyContinue | Select-Object -First 1)) {
                    [System.IO.Directory]::Delete($PastaPythonPai, $false)
                }
            } catch { }
        }
    }

    $final = Testar-PythonPrivado
    if (-not $final) {
        throw ("O Python foi baixado, mas não passou na conferência final em $PastaPythonPriv.`n" +
            (Mensagem-Antivirus) + "`n" + (Mensagem-Manual))
    }
    Escrever-Ok "Python $PyVersao pronto em $PastaPythonPriv (uso exclusivo do Proclamare)."
    return $final
}

# Acha um Python (ou baixa o do Proclamare, se permitido) e escreve as mensagens para o usuario.
function Obter-Python([bool]$PermitirBaixar) {
    Escrever-Passo 'Procurando o Python no computador (versões 3.10 a 3.13)...'
    $achado = Achar-Python
    if ($achado) {
        if ($achado.Privado) {
            Escrever-Ok "Python do Proclamare (baixado antes): $($achado.Caminho) (Python $($achado.Versao)). Não é preciso baixar nada."
        } else {
            Escrever-Ok "Python encontrado: $($achado.Caminho) (Python $($achado.Versao)). Não é preciso baixar nada."
        }
        return $achado
    }
    foreach ($rej in $script:PythonsRejeitados) {
        switch ($rej.Tipo) {
            'versao' {
                Write-Host ("    Encontrei o $($rej.Texto), mas o Proclamare ainda não funciona com ele (precisa do 3.10 a 3.13).`n" +
                    '    Ele será mantido como está; não vou alterá-lo.') -ForegroundColor Yellow
            }
            'semvenv' {
                Write-Host ("    Encontrei o $($rej.Texto), mas ele foi instalado sem o módulo venv/pip, e o Proclamare precisa deles.`n" +
                    '    Ele será mantido como está; não vou alterá-lo.') -ForegroundColor Yellow
            }
            default {
                Write-Host ("    Encontrei um Python em $($rej.Texto), mas por segurança não vou usá-lo.`n" +
                    '    Ele será mantido como está; não vou alterá-lo.') -ForegroundColor Yellow
            }
        }
    }
    if (-not $PermitirBaixar) {
        Falhar ("Python 3.10 a 3.13 não encontrado (com o requirements.lock o Proclamare aceita 3.10 a 3.13).`n" +
            "    Com -SemDependencias o instalador não baixa Python.`n" +
            "    Instale o Python 3.12 em https://www.python.org/downloads/ e, na primeira tela do instalador,`n" +
            "    MARQUE a opção 'Add python.exe to PATH'. Depois execute este instalador de novo.")
    }
    Write-Host '    Não encontrei um Python compatível neste computador.'
    Write-Host ("    O Proclamare precisa do Python (um programa gratuito). Vou baixar uma cópia só para ele:`n" +
        "    não precisa de senha de administrador e não muda nada no resto do Windows.")
    try {
        $feito = @(Instalar-PythonPrivado) | Select-Object -Last 1
    } catch {
        # Todo erro termina dizendo ao leigo como seguir: instalar o Python 3.12 a mao.
        $msg = $_.Exception.Message
        if ($msg -notmatch 'python\.org') { $msg += "`n" + (Mensagem-Manual) }
        Falhar $msg
    }
    return $feito
}

# ---- Auditoria de vulnerabilidades (MODO AVISO) ------------------------
# Python de base de um .venv que ja existe (linha "home = ..." do pyvenv.cfg). Devolve '' se nao der para
# saber, se nao for um python.exe de verdade, se for o alias da Store ou se nao passar na checagem de seguranca.
function Python-Base-Do-Venv([string]$VenvDir) {
    try {
        $cfg = Join-Path $VenvDir 'pyvenv.cfg'
        if (-not (Test-Path -LiteralPath $cfg -PathType Leaf)) { return '' }
        foreach ($linha in @(Get-Content -LiteralPath $cfg -Encoding UTF8 -ErrorAction SilentlyContinue)) {
            if ($linha -match '^\s*home\s*=\s*(.+?)\s*$') {
                $exe = Join-Path $Matches[1] 'python.exe'
                if ($exe -like '*\WindowsApps\*' -or -not (Test-Path -LiteralPath $exe -PathType Leaf)) { return '' }
                if (Motivo-Inseguro $exe) { return '' }
                return $exe
            }
        }
    } catch { }
    return ''
}

# Procura o pip-audit (comando no PATH ou modulo do Python escolhido). Nao instala nada:
# so roda se o usuario ja o tiver. Devolve @{ Exe; Args } ou $null.
function Achar-PipAudit([string]$PythonBase) {
    $cmd = Get-Command pip-audit -ErrorAction SilentlyContinue
    if ($cmd) { return @{ Exe = $cmd.Source; Args = @() } }
    if (-not $PythonBase) { return $null }
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
    $py = Obter-Python $false
    $python = $py.Caminho
    Escrever-Passo 'Pulando .venv e pip (-SemDependencias): usando o Python atual.'
    $pythonw = Join-Path (Split-Path -Parent $python) 'pythonw.exe'
} else {
    $venvDir = Join-Path $Raiz '.venv'
    $venvPython = Join-Path $venvDir 'Scripts\python.exe'
    $existe = Test-Path -LiteralPath $venvDir
    $saudavel = $false
    if (Test-Path -LiteralPath $venvPython) {
        try { & $venvPython -I -c 'import sys' *> $null; $saudavel = ($LASTEXITCODE -eq 0) } catch { $saudavel = $false }
    }
    $py = $null
    $python = ''
    $versaoPython = ''
    if ($saudavel) {
        # O .venv funciona (o Python de base dele existe): nao e preciso procurar nem baixar nada.
        Escrever-Passo 'Ambiente .venv já existe e está saudável (não é preciso procurar nem baixar Python).'
    } else {
        $py = Obter-Python $true
        $python = $py.Caminho
        $versaoPython = $py.Versao
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
            Falhar "Não foi possível criar o ambiente .venv com o Python $versaoPython."
        }
    }
    $rotuloPy = if ($versaoPython) { "Python $versaoPython" } else { 'Python do .venv' }
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
            Falhar ("A instalação das dependências falhou ($rotuloPy).`n" +
                '    Verifique a internet. Se o erro citar "hash" ou "THESE PACKAGES DO NOT MATCH THE HASHES",' +
                "`n    o arquivo requirements.lock foi alterado ou corrompido: baixe o projeto de novo." +
                "`n    Se citar um pacote sem versão compatível, o lock foi testado nos Pythons 3.10 a 3.13:" +
                ' instale o Python 3.12 e tente de novo.')
        }
        Falhar ("A instalação das dependências falhou ($rotuloPy).`n" +
            '    Verifique a internet; se o erro citar um pacote sem versão compatível,' +
            ' instale o Python 3.12 e tente de novo.')
    }
    Escrever-Ok 'Dependências instaladas.'
    if ($usaLock) {
        # O pip-audit (opcional) roda no Python de base. Se o .venv ja existia (nenhuma busca foi feita) e o
        # pip-audit nao e um comando, pega o Python de base na propria configuracao do .venv (pyvenv.cfg):
        # sem varrer o computador de novo e sem baixar nada.
        if (-not $python -and -not (Get-Command pip-audit -ErrorAction SilentlyContinue)) {
            $python = Python-Base-Do-Venv $venvDir
        }
        Auditar-Dependencias $python $TravaHashes
    }
    $pythonw = Join-Path $venvDir 'Scripts\pythonw.exe'
}
if (-not (Test-Path -LiteralPath $pythonw)) { Falhar "Não encontrei o pythonw.exe em $pythonw." }

# ---- 3. Atalhos --------------------------------------------------------
Escrever-Passo 'Criando os atalhos...'
$argLigar = '"' + $Pyw + '"'
$argSilencioso = '"' + $Pyw + '" --silencioso'
$argParar = '"' + $Pyw + '" --parar'
$argDesinstalar = '-NoProfile -ExecutionPolicy Bypass -File "' + $Script + '" -Desinstalar'
if ($PastaPython) { $argDesinstalar += ' -PastaPython "' + $PastaPythonPriv + '"' }   # o desinstalador acha o mesmo Python
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

if ($py -and $py.Privado) {
    Write-Host "`nO Python usado é a cópia do Proclamare em: $PastaPythonPriv" -ForegroundColor Gray
    Write-Host "(ela é apagada pelo atalho 'Desinstalar Proclamare', se você quiser)" -ForegroundColor Gray
}
Write-Host "`nPronto! Dê dois cliques no ícone Proclamare para ligar." -ForegroundColor Green
Write-Host 'Para desligar: Menu Iniciar > Desligar Proclamare.'
Write-Host 'Se mover esta pasta de lugar, rode o instalador de novo.'
