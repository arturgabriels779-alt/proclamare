# Proclamare v2 — anúncio de voz + música (Spotify / YouTube / Minhas músicas), com controle remoto

O Proclamare continua convertendo texto em anúncio de voz
nas caixas da loja, e agora também toca música de fundo — do Spotify ou do
YouTube — abaixando o volume sozinho sempre que um anúncio é feito. **Tudo
isso pode ser controlado de qualquer computador da rede da loja**, mesmo
que o som sempre saia pelas caixas ligadas a um único computador.

A interface é um painel limpo em **Bento Grid** (estilo Apple), com os
módulos Anúncio e Música lado a lado, tema **claro, escuro ou automático**
(seletor no topo) e layout adaptado para celular.

---

## 0. Entenda os dois papéis: "computador da loja" x "computador de controle"

Esse é o ponto mais importante para não se confundir:

- **Computador da loja** — é o único computador ligado ao amplificador/caixas
  de som. Nele você roda o `server.py` (ele fica ligado o tempo todo) e nele
  o som realmente sai. É nele também que o Spotify (o aplicativo oficial)
  fica aberto e logado.
- **Computador(es) de controle** — qualquer outro computador ou celular na
  mesma rede Wi-Fi/cabo da loja. Neles você só abre o navegador em
  `http://IP-DO-COMPUTADOR-DA-LOJA:8502` — não precisa instalar nada.

Anúncios e o Spotify já funcionam remotamente "de graça" com essa
arquitetura. O YouTube e as **Minhas músicas** (arquivos de áudio) são a
exceção: eles tocam numa aba do navegador, então **um computador precisa ficar
com a aba do Proclamare aberta e marcada como "Tocador da loja"** — normalmente o
próprio computador da loja, tocando em segundo plano. Os demais ficam em
"Controle remoto". Isso é explicado com mais detalhe nas seções 4 e 4b.

---

## 1. Instalação (no computador da loja)

Requer **Python 3.10+**.

```bash
cd proclamare   # pasta do projeto (clonada do GitHub)
python -m venv venv
# Windows: venv\Scripts\activate
# Linux/Mac: source venv/bin/activate
pip install --only-binary :all: -r requirements.txt
```

O `requirements.txt` tem **versões exatas** (as mesmas do `requirements.lock`), mas sem hashes
e sem as dependências indiretas fixadas: serve para Linux/macOS. `--only-binary :all:` impede
que o pip compile código-fonte baixado da internet.

No Windows, o instalador (abaixo) usa o `requirements.lock`, que trava a versão de
cada pacote **com hashes**
(`pip install --require-hashes --only-binary :all: --no-deps -r requirements.lock`):
o pip recusa qualquer arquivo diferente do que foi testado e nunca compila código-fonte.
Se você instalar à mão no Windows, prefira essa forma. O lock vale para Python 3.10 a
3.13. O instalador do Windows usa um Python 3.10 a 3.13 que já esteja no computador; se
só existir o 3.14 ou mais novo, ele **não mexe no seu Python** e baixa um Python só para
o Proclamare (veja "Python: o instalador resolve sozinho", abaixo).

### Instalar e ligar com duplo clique (Windows)

Para quem não quer usar o terminal:

1. **Escolha o lugar da pasta:** deixe a pasta do Proclamare **dentro da sua pasta
   de usuário** (ex.: Documentos), não em `C:\` nem em pastas públicas. Se mover
   a pasta de lugar depois, **rode o instalador de novo** (os atalhos guardam o
   caminho antigo).
2. **Instalar (uma vez só):** dê duplo clique em **`Instalar Proclamare.bat`**.
   Você **não precisa instalar o Python antes**: o instalador resolve isso sozinho
   (veja a seção abaixo). Ele cria o ambiente `.venv`, instala as
   dependências pelo `requirements.lock` (precisa de internet) e cria os atalhos **Proclamare** (Área de
   Trabalho e Menu Iniciar), **Desligar Proclamare** e **Desinstalar Proclamare**
   (Menu Iniciar). Se o instalador avisar em amarelo que a pasta é gravável por
   todos os usuários do computador, mova-a para a sua pasta de usuário.
3. **Ligar:** dê duplo clique no ícone **Proclamare**. Não aparece janela preta:
   em alguns segundos o painel abre no navegador. Se já estiver ligado, o duplo
   clique só abre o painel (nunca sobe dois servidores). Se aparecer "está
   demorando para ligar", espere alguns segundos e dê duplo clique de novo.
4. **Desligar:** pelo botão **⏻** no topo do painel (só no computador da loja;
   veja "Desligar pelo painel") ou pelo Menu Iniciar → **Desligar Proclamare**. Uma mensagem confirma
   "Proclamare desligado". O atalho só desliga o servidor iniciado pelo próprio
   Proclamare, nunca outros programas. Se ele foi ligado de outra forma (ex.:
   `python server.py` no terminal), feche a janela onde está rodando.
5. **Iniciar junto com o Windows:** o instalador pergunta (S/N). O atalho na
   pasta de Inicialização usa `--silencioso` (liga o servidor sem abrir o
   navegador). Para mudar de ideia, rode o instalador de novo e responda: **N**
   remove o atalho de Inicialização.
6. **Logs:** `logs/proclamare.log` (log do servidor, rotativo: 5 MB × 3 cópias),
   `logs/inicio.log` (saída bruta do último início, útil para erros antes de o
   log começar) e `logs/inicio.anterior.log` (a do início anterior: se o programa
   caiu e alguém o religou, **a causa da queda está aqui**). O servidor roda em
   modo isolado do Python (`python -I`): variáveis como `PYTHONPATH` são ignoradas
   e arquivos soltos na pasta do programa não substituem os pacotes instalados.
7. **Se não abrir:** a mensagem de erro cita os arquivos de log — abra-os e veja
   as últimas linhas. Causas comuns: o instalador ainda não foi executado
   (mensagem "Execute primeiro o Instalar Proclamare.bat"); a porta 8502 está
   ocupada por outro programa (use `PROCLAMARE_PORT`); ou o Firewall do Windows
   bloqueou (escolha "Permitir").
8. **Desinstalar:** Menu Iniciar → **Desinstalar Proclamare** (ou, no terminal,
   `powershell -NoProfile -ExecutionPolicy Bypass -File tools\instalar.ps1 -Desinstalar`).
   Remove todos os atalhos, inclusive o de Inicialização, a tarefa "religar sozinho"
   (se você a criou) e pergunta se deve apagar o `.venv` e o Python que o próprio
   Proclamare baixou (se baixou). **Por padrão a pasta do programa, os seus dados
   (`config.json`, `listas.json`, `programacao*.json`, `logs/`) e a pasta de músicas
   são mantidos.** O instalador também pergunta "Apagar também os DADOS do
   Proclamare?" (padrão: não). Se responder **S**, ele lista o que será apagado e
   só apaga depois de você digitar `APAGAR`; só mexe em arquivos **dentro da pasta
   do programa** (nunca na pasta de músicas, no Python ou em outros programas). Faça
   isso se o computador for vendido, devolvido ou descartado (o `config.json` guarda o
   hash do PIN e o segredo do Spotify). Para pular a primeira pergunta:
   `-Desinstalar -ApagarDados` (a confirmação `APAGAR` continua sendo pedida).
   Depois, exclua a pasta do programa se quiser tirar tudo.

O lançador sempre conversa com `127.0.0.1`. Se você definiu `PROCLAMARE_HOST`
com um IP específico que não seja de loopback, use `0.0.0.0` ou abra o painel
direto pelo navegador nesse endereço.

### Python: o instalador resolve sozinho

Ao rodar `Instalar Proclamare.bat`, o instalador faz isto:

1. **Procura um Python no computador** (versões 3.10 a 3.13): o `py` (launcher), o PATH, o registro do Windows (só leitura) e as pastas padrão (como `%LOCALAPPDATA%\Programs\Python`). Se o `.venv` do Proclamare já está funcionando, nem procura.
2. **Se achar, usa esse Python e não baixa nada.** Nada é mudado nele. Por segurança, só é usado um Python cuja pasta seja **sua ou dos administradores** e que **não possa ser alterada por qualquer usuário do computador**; o instalador avisa quando ignora um por esse motivo (por exemplo, um `C:\Python312` criado por outra conta).
3. **Se só existir Python 3.14 ou mais novo**, ele não serve (uma das peças do Proclamare ainda não existe para o 3.14). O instalador avisa, deixa o seu Python como está e segue para o passo 4. O mesmo vale para um Python instalado sem o módulo `venv`/pip.
4. **Se não houver Python compatível, baixa um Python só para o Proclamare** (3.12.10, cerca de 14 MB, do site oficial nuget.org, pacote mantido pela Python Software Foundation). Ele fica em `%LOCALAPPDATA%\Proclamare\python-3.12.10` (uns 40 MB). **Não pede senha de administrador, não mexe no PATH, no registro nem em outros programas**, e continua lá quando você baixar uma versão nova do Proclamare. A pasta é criada **protegida**: só você, o sistema e os administradores podem alterá-la.
5. Nas próximas vezes ele reaproveita esse Python (confere a assinatura digital de novo) e não baixa outra vez.

**Segurança do download:** só HTTPS do endereço fixo do nuget.org; versão, tamanho e impressões digitais (SHA-256 e SHA-512) fixos no instalador; se não conferir, o arquivo é apagado e nada é instalado; a assinatura digital da Python Software Foundation é conferida antes de qualquer arquivo do Python ser executado; tudo é preparado numa pasta temporária protegida e só depois colocado no lugar (nunca fica pela metade).

**Sem internet?** O instalador avisa. Conecte à internet e rode de novo, ou instale o Python 3.12 manualmente em [python.org](https://www.python.org/downloads/) e, na primeira tela, **marque "Add python.exe to PATH"**.

#### Problemas comuns

- *"O caminho da pasta é longo demais"*: o Windows limita caminhos a 260 letras. Use `-PastaPython C:\Proclamare\python` (ou, melhor, uma pasta dentro do seu perfil); a pasta é protegida automaticamente.
- *"Este Windows é de 32 bits"*: o Python automático é de 64 bits; instale o Python 3.12 manualmente (veja acima).
- *"O disco não guarda permissões (não é NTFS)"*: use `-PastaPython` com uma pasta de um disco NTFS.
- *Antivírus, proxy ou Windows sem atualizações*: se o arquivo baixado for alterado ou a assinatura não puder ser conferida, nada é instalado. Tente em outra rede, rode o Windows Update ou instale o Python 3.12 manualmente.
- *Espaço em disco*: são necessários cerca de 250 MB livres durante a preparação.
- *Outra conta do Windows*: como a pasta do Python do Proclamare é fechada para outros usuários, o Proclamare instalado por uma conta não funciona em outra; cada conta roda o instalador. O mesmo vale para um Python instalado "só para o usuário".

**Desinstalar:** o atalho "Desinstalar Proclamare" remove os atalhos e pergunta se deve apagar o `.venv` e **o Python que o próprio Proclamare baixou**. Ele só apaga uma pasta marcada como do Proclamare (arquivo `.proclamare-python.json`), **nunca um Python que você instalou**. Se você tiver outra cópia do Proclamare neste PC, ela vai precisar rodar o instalador de novo. Para pular a pergunta: `-Desinstalar -RemoverPythonPrivado`. Se você apagar o Python do Proclamare e mantiver o `.venv`, o `.venv` deixa de funcionar até você rodar o instalador de novo (ele reconstrói sozinho).

**Opções avançadas** (linha de comando: `powershell -ExecutionPolicy Bypass -File tools\instalar.ps1 ...`):

- `-PastaPython <pasta>`: onde guardar o Python do Proclamare (padrão `%LOCALAPPDATA%\Proclamare\python-3.12.10`). Prefira uma pasta dentro do seu perfil; o instalador a protege e recusa uma pasta-mãe que pertença a outro usuário.
- `-IgnorarPythonDoSistema`: ignora os Pythons do PC e força o uso/baixa do Python do Proclamare (serve para testar um PC sem Python).
- `-RemoverPythonPrivado`: com `-Desinstalar`, apaga o Python do Proclamare sem perguntar.
- `-SemDependencias`: nunca baixa Python (usa só o que já existe).

**Qual Python o instalador escolhe:** prefere o **3.12** (é o que o Proclamare baixa e testa), depois 3.13, 3.11 e 3.10, e só aceita um Python do PC com o **patch mínimo exigido** da série (3.10.11, 3.11.9, 3.12.10, 3.13.5: um piso conservador; um Python mais velho que isso é ignorado e o instalador explica por quê). Se só houver 3.10 ou 3.11, ele usa e **avisa** que já não recebem correções de segurança em programa pronto (o 3.10 chega ao fim de vida em out/2026). A tabela fica em `$PatchMinimo` no `tools/instalar.ps1`.

**Modo isolado (`python -I`):** o Proclamare roda, e os atalhos e a tarefa chamam o lançador, com `python -I`. Isso ignora `PYTHONPATH`, a pasta atual e o **site do usuário**. Consequência: pacotes instalados com `pip install --user` **não são vistos**. No modo `-SemDependencias` o instalador confere isso e avisa; instale os pacotes no próprio Python (sem `--user`) ou use o modo normal, que cria o `.venv`.

**Pasta do programa:** o instalador **recusa instalar** se a pasta do programa puder ser alterada por qualquer usuário do computador (ele não cria atalhos nem a tarefa "religar sozinho" que executariam código trocável por outros). Prefira `%LOCALAPPDATA%\Programs\Proclamare` ou uma pasta dentro do seu perfil. O instalador não muda as permissões de uma pasta que ele não criou; isso é um **risco aceito** quando você mesmo libera a pasta para outros usuários.

**Versão do Python do Proclamare:** o 3.12.10 é a última 3.12 com binários para Windows e não recebe mais correções de segurança do CPython. O app não usa o módulo `tarfile` e o pip roda só com wheels (`--only-binary`), então o risco hoje é baixo. A troca (3.13.x, ou 3.14.x quando o lock liberar o pygame no 3.14) fica para revisão até 2027-04.

Para definir o PIN (6 a 8 dígitos) sem abrir o painel, rode no terminal, dentro
da pasta do projeto: `.venv\Scripts\python server.py --definir-pin`.

Se o módulo **pip-audit** já estiver instalado no Python de base (opcional; instale-o
fora do `.venv` do Proclamare, ex.: `py -m pip install pip-audit`), o instalador
também confere se as versões fixadas têm vulnerabilidades conhecidas. Ele só roda o
módulo desse Python (que já passou pela checagem de dono e pasta); **nunca executa um
`pip-audit` achado no PATH**. É só um **aviso**: nunca bloqueia a instalação. No GitHub,
o `requirements.lock` também é auditado a cada mudança (workflow `testes`). Se o `requirements.lock` não estiver na
pasta, o instalador **para** e pede o projeto completo; só instala pelo
`requirements.txt` (sem verificação de hashes) se você passar `-PermitirSemHashes`.

Opções de teste/automação: com `PROCLAMARE_NO_BROWSER=1` o lançador não abre o
navegador nem mostra caixas de mensagem (escreve na saída padrão). O instalador
aceita `-PastaAtalhos <pasta>`, `-SemDependencias`, `-IniciarComWindows`,
`-NaoIniciarComWindows` e `-Desinstalar` (rode `tools\instalar.ps1` direto no
PowerShell).

### Voz offline (sem internet) — só no Linux precisa instalar algo

- **Windows e macOS:** nada a fazer, usam a voz já embutida no sistema.
- **Linux:**
  ```bash
  sudo apt-get install espeak-ng
  ```

---

## 2. Rodando e acessando de outros computadores

No computador da loja:

```bash
python server.py
```

Isso sobe o servidor em `http://0.0.0.0:8502`. O terminal mostra o endereço
de rede local do computador (algo como `http://192.168.0.23:8502`) — **anote
esse IP**, é ele que os outros computadores vão usar.

- **No computador da loja:** abra `http://127.0.0.1:8502` (use esse endereço,
  e não `localhost`, para conectar o Spotify — veja a seção 3).
- **De qualquer outro computador/celular na mesma rede:** abra
  `http://IP-DO-COMPUTADOR-DA-LOJA:8502` (ex.: `http://192.168.0.23:8502`).

Lembre-se: **o som (anúncio e música) sempre sai nas caixas ligadas ao
computador que está rodando o `server.py`**, não no aparelho de quem está
mexendo na tela.

O servidor usa o **waitress** (instalado pelo `requirements.txt`). Se ele não
estiver instalado, o `server.py` avisa no log e cai para o servidor de
desenvolvimento do Flask — funciona, mas não é indicado para ficar ligado o
dia todo.

**Variáveis de ambiente opcionais** (defina antes de rodar `python server.py`):

As variáveis antigas `ANUNCIADOR_*` (mesmo sufixo) ainda funcionam como
alternativa; se as duas existirem, vale a `PROCLAMARE_*`.

| Variável | Padrão | Para quê |
|---|---|---|
| `PROCLAMARE_HOST` | `0.0.0.0` | Endereço de escuta (`127.0.0.1` = só este computador) |
| `PROCLAMARE_PORT` | `8502` | Porta do servidor (se mudar, ajuste o Redirect URI do Spotify e o Firewall) |
| `PROCLAMARE_LOG_LEVEL` | `INFO` | Detalhe do log (`DEBUG`, `INFO`, `WARNING`...) |
| `PROCLAMARE_LOG_FILE` | (vazio = console) | Arquivo de log. O log **rotaciona em 5 MB** e guarda **3 arquivos antigos** (`.1`, `.2`, `.3`). O lançador de duplo clique já define esta variável (em `logs/`) |
| `PROCLAMARE_PID_FILE` | (vazio) | Arquivo JSON (`pid`, `porta`, `criado_em`, `executavel`) gravado quando o servidor começa a atender e removido ao encerrar; o lançador usa para desligar o processo certo. O lançador de duplo clique já define esta variável (em `logs/`) |
| `PROCLAMARE_SPOTIFY_REDIRECT` | `http://127.0.0.1:8502/spotify/callback` | Redirect URI do Spotify. O Spotify só aceita `http` para o IP de loopback `127.0.0.1`/`[::1]`; para outro endereço é preciso `https://` (ex.: proxy com certificado) |
| `PROCLAMARE_CONFIAR_LOOPBACK` | `auto` | Quem é o "PC da loja" (sem PIN). `auto` (padrão): o próprio PC é local, **exceto** se o Proclamare detectar um túnel/encaminhamento para a porta; aí o PC também passa a pedir PIN. `1`: o PC é sempre local. `0`: ninguém é local, todos usam PIN (necessário com `tailscale serve` e proxies). Veja "Acesso pela rede (PIN)" |
| `PROCLAMARE_AUDIT_FILE` | `logs/auditoria.log` | Arquivo da trilha de auditoria (veja a seção 10). Por padrão fica ao lado do `PROCLAMARE_LOG_FILE` |
| `PROCLAMARE_MUSICAS_DIR` | `Músicas` do Windows`\Músicas do Proclamare` | Pasta das Minhas músicas (veja a seção 4b). Fica fora da pasta do programa de propósito: sobrevive a baixar uma versão nova |
| `PROCLAMARE_MUSICAS_ENVIO` | `1` | `0` desliga o envio de músicas pelo painel (a pasta continua funcionando). Use `0` se a Wi-Fi da loja for compartilhada com clientes — veja "Segurança do envio" na seção 4b |
| `PROCLAMARE_ALLOWED_HOSTS` | (vazio) | Nomes de host extras aceitos, separados por vírgula (ex.: `proclamare.loja.local`). IPs, `localhost` e o nome deste computador já são aceitos; qualquer outro nome recebe erro 421 (proteção contra DNS rebinding) |

### Limites que protegem a loja (v2.7.1)
- Anúncio: no máximo **10 por minuto por aparelho da rede** (o PC da loja não tem esse limite).
- Programação: o teto de **60 minutos por dia** agora soma o áudio **e** a fala estimada dos
  anúncios agendados; no máximo **72 horários ativos por dia** da semana; e no máximo **10
  listas/playlists agendadas por dia criadas ou alteradas por cada aparelho da rede**
  (editar o conteúdo de uma lista que já está agendada também conta).
  Só é recusado o que piora um dia: uma programação já salva continua valendo.

### Dicas para o acesso remoto funcionar sem dor de cabeça

- **IP fixo:** o IP local do computador da loja pode mudar quando ele
  reinicia o roteador o atribui de novo. Configure um **IP fixo** (ou uma
  reserva de DHCP, feita no próprio roteador) para esse computador, assim o
  endereço usado pelos outros computadores nunca muda. Peça ajuda ao seu
  provedor de internet ou a quem cuida da rede da loja se não souber fazer
  isso.
- **Firewall do Windows:** na primeira vez que rodar o `server.py`, o
  Windows pode perguntar se permite o acesso na rede — escolha "Permitir".
  Se outros computadores não conseguirem abrir o endereço, confira se a
  porta **8502** está liberada no Firewall do Windows (redes privadas).
- Isso tudo vale só para a rede local da loja (Wi-Fi/cabo). Não é necessário
  nem recomendado expor essa porta para a internet.
- **Segurança da rede:** outros aparelhos só controlam o Proclamare depois de
  digitar o **PIN** (veja "Acesso pela rede (PIN)" abaixo); sem PIN definido, o
  acesso pela rede fica desligado. Mesmo assim, restrinja a regra do Firewall do
  Windows à sub-rede da equipe e **não deixe o Wi-Fi de visitantes/clientes na
  mesma rede** do computador da loja. O Client Secret só pode ser salvo no
  próprio computador da loja (por isso o painel "Configurar Spotify" recusa
  salvar a partir de outro aparelho).
- **Proteja o `config.json`** (guarda o Client Secret e os tokens). No Windows,
  restrinja o acesso ao seu usuário: `icacls config.json /inheritance:r /grant:r "%USERNAME%:F"`.

### Acesso pela rede (PIN)

- **O computador da loja nunca pede PIN.** Quem usa o painel nele (endereço
  `http://127.0.0.1:8502` ou `http://localhost:8502`) entra direto. Para o
  servidor, "é o computador da loja" só vale se a conexão vier do próprio PC,
  pelo endereço `127.0.0.1`/`localhost`/`::1` e sem passar por proxy.
- **Definir o PIN:** no computador da loja, abra **Configurações → Acesso pela
  rede** e escolha um PIN de **6 a 8 números**. Só dá para definir, trocar ou
  remover o PIN nesse computador. PINs fáceis são recusados: números repetidos
  (`111111`), sequências (`123456`, `654321`), blocos repetidos (`121212`,
  `123123`), espelhos (`123321`), pares/trincas em sequência (`112233`,
  `102030`) e sequências de teclado (`147258`, `159753`).
- **Pela linha de comando** (sem abrir o painel e sem precisar do servidor
  ligado), na pasta do Proclamare:
  `python server.py --definir-pin` (pede o PIN duas vezes, sem mostrar na
  tela) ou `python server.py --remover-pin`.
- **Celulares e outros computadores** digitam o PIN uma vez e ficam lembrados por
  **14 dias** (neste aparelho/navegador). Quem continua usando não precisa
  digitar de novo: quando faltam menos de 7 dias, a sessão é renovada
  sozinha por mais 14. O botão de sair esquece o aparelho.
- **Trocar o PIN desconecta todos os aparelhos**: cada um precisará digitar o
  PIN novo. Se quiser só desconectar todo mundo **sem trocar o PIN**, use
  **Desconectar todos os aparelhos** em Configurações → Acesso pela rede (no PC da loja).
- **Sem PIN, o acesso pela rede fica desligado**: outros aparelhos conseguem abrir
  a página, mas não anunciar nem controlar a música. Remover o PIN também
  desconecta todos.
- **Bloqueio por tentativas (contra quem tenta adivinhar o PIN):**
  - **Por aparelho (IP):** 5 PINs errados seguidos bloqueiam aquele aparelho por 5 minutos (a 5ª
    tentativa errada já mostra o bloqueio). Se o mesmo aparelho continuar errando depois, o
    bloqueio **dobra** a cada vez (5 min, 10, 20... até 24 h). O nível desce **um degrau a cada 24
    horas sem bloqueio**.
  - **Por faixa de rede:** 20 erros em 10 minutos somando vários aparelhos da mesma faixa (`/24` no
    IPv4, como `192.168.0.x`; `/64` no IPv6) bloqueiam a faixa por **10 minutos fixos** (esse
    bloqueio **não escala**), mesmo que o invasor troque de IP. **Aparelhos "conhecidos" (com o cookie de
    aparelho, ver abaixo) não são afetados** por esse bloqueio da faixa.
  - **Teto diário:** se alguém errar o PIN 300 vezes em 24 horas (somando todos os aparelhos), o
    Proclamare trava o login pela rede para aparelhos que ele ainda não conhece. Um aparelho
    "conhecido" é um celular ou computador que já entrou com o PIN certo **naquele navegador**: o
    navegador guarda um cookie de aparelho por 180 dias, renovados enquanto você usa o painel. Esse cookie não dá acesso a nada; só dá
    ao aparelho direito a até 5 tentativas erradas durante a trava. Quem apagou os cookies, usa
    outro navegador ou foi desconectado pela troca do PIN conta como desconhecido.
  - A **primeira trava** acaba sozinha 24 horas depois. Se houver uma **segunda trava em menos de
    7 dias**, ela só acaba quando o dono clicar em **Liberar login pela rede** no PC da loja
    (Configurações > Segurança). Trocar o PIN também destrava e apaga o histórico. Sessões já
    abertas e o PC da loja nunca são afetados.
  - **Logins certos** também têm limite: no máximo 10 por minuto por aparelho.
  - **Liberar login pela rede** zera os bloqueios do momento e a trava do teto diário e não
    derruba ninguém. Por padrão **não zera a escalada** dos aparelhos (o próximo erro volta ao
    nível alto); há uma opção para **também zerar a escalada** (continua sem derrubar ninguém).
    **Trocar ou remover o PIN**, ou **Desconectar todos os aparelhos**, zera **tudo**. Um login
    certo não apaga a escalada. O computador da loja não é afetado.
  - **Reiniciar o Proclamare não perdoa:** o estado do bloqueio e a escalada de cada aparelho
    **sobrevivem a reinício** (do programa e do PC). O PIN longo continua sendo a defesa de
    verdade; o bloqueio só torna o chute lento.
  - **Recomendação:** use PIN de **8 dígitos** (100 vezes mais combinações que 6) e não repita
    o PIN de cartão, celular ou da loja.
- **Tentativas suspeitas e área "Segurança":** no PC da loja (e só nele) o painel tem
  uma área **Segurança** que mostra quantos PINs errados houve nas últimas 24 horas, se há
  bloqueio ativo, os **últimos acessos** (hora e IP; um IP nunca visto antes acende o alerta) e
  se o Proclamare detectou algum túnel. Os contadores de erros e os últimos acessos ficam
  guardados e sobrevivem a reinício, assim como o estado dos bloqueios; nada disso aparece para
  os outros aparelhos. O botão **Liberar login pela rede** desfaz os bloqueios de login do
  momento **sem** derrubar ninguém e **sem** trocar o PIN (veja as regras acima).
- **Feito por aparelhos da rede:** a mesma área lista o que celulares e outros computadores
  criaram ou alteraram: horários, listas, atalhos do Spotify, mensagens prontas, **músicas
  enviadas pelo celular** e mudanças no **Som da loja**. Se você reconhece um item, use **Aprovar**
  (ele passa a ser seu; nas músicas, a aprovação é **música por música**); se não reconhece, use
  **Apagar o que os aparelhos da rede criaram**. Esse botão apaga **só o que foi CRIADO por aparelhos da
  rede** (horários, mensagens, listas, atalhos) e **nunca apaga músicas**: músicas enviadas só
  saem se você as remover à mão. O que é seu e foi apenas **mudado** pela rede também **não é
  apagado**: fica na lista para você olhar e aprovar (ou corrigir à mão).
- **ATENÇÃO — proxy, túnel ou redirecionamento de porta no PC da loja:** com um proxy
  (Caddy/nginx), túnel (SSH, cloudflared, ngrok) ou redirecionamento de porta
  (`netsh portproxy`, WSL, `tailscale serve`), **qualquer aparelho poderia se passar pelo PC da
  loja e ignorar o PIN**, porque todas as conexões chegam ao Proclamare como se viessem do próprio
  PC. `PROCLAMARE_CONFIAR_LOOPBACK` tem 3 modos:
  - `auto` (**padrão**): o PC é "local" normalmente. O Proclamare **detecta ALGUNS
    encaminhamentos**: entradas do `netsh interface portproxy` para a porta dele e programas
    `cloudflared`, `ngrok`, `frpc`, `bore`, `chisel`, `socat`, `localtunnel` ou `ssh -R/-L/-D`
    **com a porta na linha de comando** (vigia a cada 60 s, só leitura; janela de até 1 minuto). Se achar um
    encaminhamento "forte", o PC da loja **deixa de ser livre**, com ou sem PIN definido:
    - **Com PIN:** o PC passa a **pedir o PIN também** (a tela de PIN explica o motivo).
    - **Sem PIN:** o acesso fica **bloqueado** e o painel mostra isso. Saídas:
      1. **Feche o encaminhamento** (o túnel, o `portproxy`): o acesso **volta sozinho em até 1
         minuto**.
      2. **Defina o PIN pela linha de comando.** Passo a passo: **desligue o Proclamare** (Menu
         Iniciar → Desligar Proclamare); abra a **pasta do programa** no Explorador de Arquivos;
         clique numa área vazia **segurando Shift e com o botão direito** e escolha **Abrir no
         Terminal** (ou **Abrir janela do PowerShell aqui**); cole
         `.venv\Scripts\python.exe server.py --definir-pin`, tecle Enter e digite o PIN duas
         vezes (não aparece na tela; 8 números); **ligue o Proclamare de novo** e entre com esse PIN.
      3. **Iniciar com `PROCLAMARE_CONFIAR_LOOPBACK=1`** só sabendo que **quem entra pelo
         encaminhamento terá acesso total, sem PIN** (trocar PIN, Spotify, desligar o sistema).
         Use apenas se você mesmo controla o túnel.
    A vigia olha a cada 60 s (uma janela de **até 1 minuto** para detectar e para liberar) e
    **também avisa quando não consegue ler um processo suspeito** (por falta de permissão): nesse
    caso o aviso aparece, mas ela não consegue decidir sozinha.
    Quando o encaminhamento some, volta ao normal.
  - `1`: o PC é sempre local (não recomendado com túnel).
  - `0`: **ninguém** é local; todos digitam o PIN, e trocar o PIN e configurar o Spotify só são
    possíveis pela linha de comando / editando o `config.json` com o servidor parado.
  **NÃO são detectados:** `cloudflared` rodando como serviço do Windows, `tailscale serve`, e
  proxies (nginx/Caddy/Apache). Com qualquer um deles use
  **`PROCLAMARE_CONFIAR_LOOPBACK=0`** (defina o PIN antes, pela linha de comando). Qualquer outro
  programa ou conta do próprio PC continua sendo "o PC da loja" (risco aceito).
- **Limite de conexões:** cada aparelho da rede pode ter até **64 conexões** ao mesmo tempo, os
  aparelhos da rede somados até **300**, e **400 no total**; o PC da loja não entra na conta dos
  300 e sempre tem lugar. Atrás de um repetidor
  Wi-Fi ou NAT, **todos os celulares aparecem com o mesmo IP e contam como um só aparelho**
  (também nos limites de PIN e de anúncios por minuto).
- **Comando de PIN:** `--definir-pin` e `--remover-pin` recusam rodar com o
  Proclamare ligado (desligue antes: Menu Iniciar → Desligar Proclamare). Rode-os
  com o **mesmo usuário do Windows** que roda o Proclamare (não como outro
  administrador), senão o `config.json` fica com permissões que o Proclamare não
  consegue ler.
- **Duração da sessão:** a renovação automática nunca passa de **90 dias** desde
  o login; depois disso o aparelho pede o PIN de novo.
- **O cookie vale para o endereço, não para a porta:** os navegadores não separam
  cookies por porta. Outro programa que você rode no mesmo PC/endereço (por
  exemplo em `http://192.168.0.10:3000`) recebe o cookie de sessão do Proclamare.
  Não rode serviços de terceiros no mesmo endereço, ou use "Desconectar todos os aparelhos" se
  desconfiar.
- O PIN é guardado apenas como hash (PBKDF2) no `config.json`, junto de uma chave
  de sessão; o PIN em si nunca aparece nos logs. Lembre: a rede da loja usa
  `http` (sem criptografia), então o PIN protege contra curiosos, não contra
  quem consegue espionar o tráfego da rede — mais um motivo para manter o Wi-Fi
  de clientes separado.

---

## 2b. Mensagens prontas e desligar pelo painel

### Mensagens prontas: editar
Os botões do bloco **Mensagens prontas** (na aba Anúncio) são seus: dá para
trocar, apagar e criar os que a sua loja usa de verdade.

1. No bloco **Mensagens prontas**, toque em **Editar**. Funciona no computador da
   loja e também no celular (entrando com o PIN).
2. Cada mensagem tem dois campos: **Nome do botão** (até 40 letras, é o que
   aparece no botão) e **Texto anunciado** (até 500 letras, é o que as caixas
   falam). Os contadores mostram quanto falta.
3. Use as setas para **subir** ou **descer** uma mensagem, a lixeira para
   **remover** (aparece "Desfazer" logo em seguida) e **Adicionar mensagem** para
   criar outra. O limite é de **24 mensagens**.
4. Toque em **Salvar mensagens**. Os botões do painel mudam na hora e, nos outros
   aparelhos, em até 1 minuto. **Cancelar** descarta o que foi digitado (o painel pergunta antes,
   se você mudou alguma coisa).
5. **Restaurar mensagens de fábrica** devolve as 6 mensagens originais do
   Proclamare (pede confirmação e vale na hora, sem precisar salvar). O botão
   só aparece depois que você mudou alguma coisa.

Se aparecer **"Outro aparelho mudou as mensagens"**, alguém salvou uma alteração
antes de você. Toque em **Recarregar** para ver a versão atual (isso descarta o
que você digitou) ou em **Manter o que digitei** para guardar o seu texto: ao
salvar, ele fica por cima do que o outro aparelho gravou. Se a janela de edição
fechar sozinha com texto ainda não salvo (por exemplo, ao apertar Esc duas
vezes), o painel oferece **Continuar de onde parei** na próxima vez que você
tocar em Editar. Se faltar o nome ou o texto, o painel marca o campo e diz o que
falta. Quebras de linha no texto são aceitas; caracteres invisíveis ou de
controle (que às vezes vêm ao copiar de outros programas) são removidos ao salvar.

As mensagens ficam no `config.json` (chave `mensagens`) do computador da loja.

### Desligar pelo painel
No computador da loja, o botão **⏻** (ao lado do tema e das configurações)
desliga o Proclamare, como nos aplicativos de sempre.

1. Toque em **⏻**. O painel pergunta: *Desligar o Proclamare? Os anúncios e o
   Tocador da loja param até você ligar de novo pelo ícone Proclamare. Se o
   Spotify estiver tocando, ele continua no aplicativo Spotify.*
2. Toque em **Desligar**. O Tocador da loja (YouTube e Minhas músicas, em qualquer
   aba ou aparelho) é pausado, o servidor encerra em instantes e a aba mostra
   *Proclamare desligado*. A música do **Spotify** toca no aplicativo Spotify do
   computador, e não pelo Proclamare: se ela estiver tocando, pause-a no próprio
   aplicativo Spotify.
3. Para ligar de novo, dê **duplo clique no ícone Proclamare** na Área de
   Trabalho.

Detalhes que valem saber:
- O botão **só aparece no computador da loja**. No celular ou em outro
  computador da rede ele não existe (e o servidor também recusa, mesmo com PIN).
- Se **um anúncio estiver tocando**, o painel avisa *Espere o anúncio terminar* e
  não desliga; tente de novo quando acabar. O mesmo vale se alguém estiver
  **enviando músicas** (*Há músicas sendo enviadas. Espere terminar.*).
- Os outros aparelhos que estavam com o painel aberto passam a mostrar "sem
  conexão" até o Proclamare ser ligado de novo.
- O atalho **Desligar Proclamare** do Menu Iniciar continua valendo; se o
  Proclamare já foi desligado pelo painel, ele só avisa que não está ligado.

---

## 2c. Aviso sonoro e voz da loja

Antes de cada anúncio falado, o Proclamare toca um **aviso sonoro curto** (como o "ding-dong" do microfone da loja) para
chamar a atenção. Ele toca depois de a música abaixar e **antes** da fala. Vale para o anúncio que você digita, para
as **Mensagens prontas** e para os **anúncios da Programação**. **Não** toca no **Testar caixas**, nem nos áudios
(músicas) marcados na Programação.

**Como escolher.** No bloco **Voz da loja** (aba Anúncio) escolha o aviso:

- **Nenhum**: o anúncio sai direto, sem sineta.
- **Microfone (início e fim)** (o padrão): três notas que **sobem antes** da fala, no estilo do "ding-dong" de
  microfone do sistema de som, e as **mesmas notas ao contrário** (descendo) **depois** dela. "Ao contrário" quer dizer
  as notas na ordem inversa, e não o som tocado de trás para frente (que soaria "sugado").
- **Aeroporto**: três notas subindo, como a sineta clássica de aeroporto (só antes da fala).
- **Supermercado (início e fim)**: quatro notas um pouco mais lentas que sobem antes da fala e descem depois dela,
  como o microfone de supermercado.
- **Ding-dong (2 vezes)**: um "ding-dong" (a nota aguda primeiro) tocado duas vezes seguidas, com uma pausa curta,
  só antes da fala.

Nos avisos com **início e fim**, a música só volta depois da sineta do fim. Se você apertar **Parar** na Programação
no meio da fala, a fala é cortada e a sineta do fim não toca. A exceção é a **voz do computador** (motor Offline,
sem saída de som escolhida): essa fala não tem como ser cortada, então o **Parar** durante ela não faz efeito e a
sineta do fim toca normalmente; apertando **Parar** de novo durante a sineta do fim, ela é cortada. Parar só na
sineta do fim não conta como "interrompido" no histórico (a fala inteira já saiu).

Quem usava um aviso que saiu da lista nesta versão (Ding-dong de uma vez, Suave, Sino, Avião (comissário) ou
Aeroporto (início e fim)) passa automaticamente para o **Microfone (início e fim)**; dá para escolher outro a
qualquer momento.

Cada sineta dura no máximo 3 segundos. Toque em **Ouvir** para escutar: o aviso sai **nas caixas da loja** (não no
celular) e a música não abaixa; nos avisos com **início e fim** você ouve a sineta de início, uma pausa curta e a do
fim. Para ninguém ficar tocando sinetas sem
parar, o **Ouvir** aceita até 6 toques por minuto em cada celular e 20 a cada 10 minutos na loja toda.

**A voz é da loja inteira.** O motor (Auto, Online ou Offline), a voz (Francisca ou Antônio), a velocidade e o aviso
valem para **todos os aparelhos**: o que você escolhe no celular muda também no computador da loja. Antes, cada
aparelho guardava a sua voz. Se a loja ainda não tinha uma voz salva, o computador da loja passa a usar a escolha
que ele guardava antes (motor, voz e velocidade), uma única vez. Se outro aparelho já escolheu a voz da loja, a escolha
antiga é descartada.
Por enquanto qualquer aparelho com acesso (PC da loja ou PIN) pode mudar; numa versão futura só o gerente poderá.

Se a voz da loja estiver em **Online** e a internet cair, o anúncio não sai e o painel avisa: escolha **Auto** para
usar a voz do computador enquanto isso. No limite de 60 minutos de som por dia da **Programação**, cada anúncio
conta 6 segundos de sineta (a reserva para o caso mais longo: a de início mais a do fim), mesmo
com o aviso em **Nenhum**: assim trocar o aviso depois nunca faz os horários já marcados passarem do limite.

Os sons do aviso são criados pelo próprio Proclamare: não usam música nem áudio de terceiros, então não há
direitos autorais envolvidos. O **Microfone**, o **Supermercado** e o **Ding-dong (2 vezes)** são sons originais, apenas
no mesmo estilo dos exemplos que a loja mandou (nada foi baixado nem copiado).

---

## 2d. Contas e perfis (cada pessoa com o próprio usuário)

Desde a versão 2.8 o Proclamare pode funcionar com **contas**: cada pessoa da equipe entra com o próprio
**usuário e senha** e o painel mostra só o que o cargo dela permite. Assim o **Histórico** mostra quem fez cada
anúncio. Enquanto ninguém criar a primeira conta, tudo continua como antes (PIN para os aparelhos da rede e o
computador da loja livre).

### Criar a primeira conta (administrador)

1. No **computador da loja**, abra o painel. No topo aparece o aviso **Criar conta de administrador** (ou vá em
   **Configurações → Acesso pela rede → Criar conta de administrador**).
2. Preencha seu nome, a função (por exemplo, "Dona da loja"), o usuário (3 a 30 letras minúsculas, números,
   ponto ou sublinhado; ex.: `maria.souza`) e uma senha de 8 a 128 caracteres, diferente do usuário (veja
   "Escolha uma senha boa", abaixo).
3. Pronto: essa conta é o **administrador** e pode tudo. **A partir daí o PIN deixa de valer**: os celulares que
   estavam conectados com PIN saem e todos, **inclusive o computador da loja**, passam a entrar com usuário e senha.
   A aba **Tocador da loja** e os horários programados continuam tocando normalmente.
4. Só dá para criar o primeiro administrador **no computador da loja**. Se o Proclamare foi iniciado com
   `PROCLAMARE_CONFIAR_LOOPBACK=0`, nenhum aparelho é "o computador da loja" e **não é possível criar o primeiro
   administrador**: inicie sem essa opção, crie a conta e depois volte a usá-la se precisar.
5. Depois, crie as contas de todo mundo: **Configurações → Contas → Nova conta**.
6. **O histórico de anúncios começa aqui**: enquanto não existe nenhuma conta (modo PIN), o Proclamare não guarda
   o histórico (sem conta não dá para saber quem anunciou).

### Escolha uma senha boa

- **Use uma senha só para o Proclamare**, diferente da do e-mail, do banco ou do celular.
- O Proclamare **recusa senhas fáceis de adivinhar**: as mais comuns (`12345678`, `123456789`, `senha123`,
  `mudar123`, `brasil2024`, `password`...), letras ou números todos iguais (`aaaaaaaa`), sequências
  (`abcdefgh`, `qwertyui`, `87654321`, `12121212`), só espaços e senhas que contêm o usuário ou o primeiro nome da
  pessoa. Uma frase com 3 ou 4 palavras e um número (ex.: "trem azul 4 bananas") é fácil de lembrar e difícil de
  adivinhar. Quem já tinha uma senha continua entrando com ela.
- **No computador da loja, não deixe o navegador salvar a senha** (quando ele perguntar "Salvar senha?", clique
  em **Nunca**). Se já salvou: no **Chrome**, menu ⋮ → Configurações → Preenchimento automático e senhas →
  Gerenciador de senhas → Configurações, desligue **Oferecer para salvar senhas** e apague a senha do Proclamare
  da lista; no **Edge**, menu ⋯ → Configurações → Perfis → Senhas, desligue **Oferecer para salvar senhas** e
  apague a do Proclamare. Senha salva no PC da loja é a porta aberta para qualquer um entrar com a conta de outra
  pessoa.

### O que cada perfil pode fazer

| Ação | Funcionário | Gerente | Administrador |
|---|:-:|:-:|:-:|
| Fazer anúncios (digitando ou pelas mensagens prontas) | sim | sim | sim |
| Ver o histórico de anúncios | só os próprios | todos | todos |
| Trocar a própria senha | sim | sim | sim |
| Editar as mensagens prontas | não | sim | sim |
| Escolher a voz e o aviso sonoro | não | sim | sim |
| Música (YouTube, Minhas músicas, listas, Spotify) | não | sim | sim |
| Programação de horários | não | sim | sim |
| Criar e editar contas | não | só de funcionários | todas |
| Desligar o Proclamare pelo painel | não | sim (no PC da loja) | sim (no PC da loja) |
| Saídas de som, segurança da rede e conexão com o Spotify | não | não | sim |

Se alguém tentar algo que o perfil não permite, o Proclamare recusa e avisa quem deve pedir (um gerente ou um
administrador). Isso vale mesmo que a pessoa tente por fora do painel.

### Criar, editar e desativar contas

- **Configurações → Contas → Nova conta:** nome, função, usuário, perfil e senha. Combine a senha com a pessoa;
  ela pode trocar depois em **Trocar minha senha** (no topo do painel).
- **Editar:** nome, função, perfil e situação da conta. O **usuário (o nome de entrada) não muda**. Ninguém edita
  a própria conta por essa tela (a própria senha é em **Trocar minha senha**).
- **Definir uma senha nova para outra pessoa** (ela esqueceu): um **gerente** (para funcionários) ou um
  **administrador** (para qualquer um, inclusive gerentes) abre a conta e usa **Definir senha nova**. As sessões
  abertas dessa pessoa são encerradas e ela entra com a senha nova.
- **Desativar:** quem saiu da equipe é **desativado**, não apagado. A pessoa não consegue mais entrar e a sessão
  dela cai. **Contas nunca são apagadas**, de propósito: o histórico continua mostrando o nome de quem anunciou.
  Dá para **Reativar** depois.
- Sempre fica pelo menos **um administrador ativo**: o Proclamare não deixa desativar ou rebaixar o último.
- Limite de 50 contas. Quem erra a senha muitas vezes é bloqueado por alguns minutos (10 erros em 15 minutos).
  No **celular ou computador onde a pessoa já entrou antes**, a conta tem um limite próprio: se alguém na Wi-Fi
  errar a senha dela de propósito, ela continua conseguindo entrar no aparelho dela.

### Perdi o celular: "Sair de todos os aparelhos"

Na barra da sua conta (no topo do painel), **Sair de todos os aparelhos** encerra **todas** as sessões da sua
conta: no celular perdido, nos outros aparelhos e neste também (você entra de novo em seguida). Use também se
desconfiar que alguém viu a sua senha, e depois troque a senha. O botão **Sair** comum encerra só o aparelho em
que você está. **Se alguém pode ter copiado a sua sessão** (mexeu no seu celular ou no navegador desbloqueado),
use **Sair de todos os aparelhos**, não o **Sair** comum.

O **Sair** comum fica guardado na memória do Proclamare: se o Proclamare for **desligado ou reiniciado**, ele
"esquece" esses avisos e uma sessão copiada antes de você sair voltaria a valer. O **Sair de todos os aparelhos**
não tem esse problema: ele vale mesmo depois de reiniciar.

### Esqueci a senha

- **Funcionário:** peça a um gerente ou administrador para definir uma senha nova (veja acima).
- **Gerente:** peça a um **administrador** (um gerente não mexe na conta de outro gerente).
- **Administrador:** peça a outro administrador, se houver.
- **Era o último administrador:** no **computador da loja**, com o Proclamare **desligado** (Menu Iniciar →
  *Desligar Proclamare*), abra um terminal na pasta do Proclamare e rode (troque `maria.souza` pelo seu usuário):

  ```
  .venv\Scripts\python.exe server.py --redefinir-senha maria.souza
  ```

  Ele pede a senha nova duas vezes (sem mostrar na tela, como no `--definir-pin`). As sessões abertas dessa conta
  são encerradas. Depois é só ligar o Proclamare e entrar. A senha nova segue as mesmas regras do painel (nada de
  senha fácil). Rode o comando direto no Prompt de Comando: se a senha vier de um arquivo ou de outro programa, ele
  avisa que ela pode aparecer na tela.

### O computador da loja sai sozinho (5 minutos)

Com contas, o computador da loja também precisa de login. Depois de **5 minutos sem nenhum clique ou tecla**,
ele sai sozinho, para ninguém usar o painel com a conta de outra pessoa. **Só rolar a página não conta** como
atividade. Nos celulares e outros computadores a pessoa fica lembrada por 14 dias, como antes.

**O som não para:** com ninguém logado, a aba **Tocador da loja** e os **horários da programação** continuam
tocando; só não dá para anunciar nem mexer em nada até alguém entrar.

Com `PROCLAMARE_CONFIAR_LOOPBACK=0`, nenhum navegador é "o computador da loja": o Tocador só toca com alguém
logado e o PC ganha sessão longa (14 dias), como um celular.

### Histórico de anúncios

No bloco **Histórico** do painel, **Ver tudo** abre a lista de anúncios com data e hora, quem fez (nome e função),
o texto, o resultado (tocou, ocupado, erro, sem internet, interrompido) e se foi feito "pelo painel" (digitado ou
com uma mensagem pronta), "tocar agora" ou agendado. Dá para filtrar por período (De/Até), por pessoa (gerente e administrador) e por trecho do texto, e carregar mais
páginas. O **funcionário vê só os próprios anúncios**. Anúncios de antes das contas aparecem como "antes das
contas".

Os horários, as listas, as playlists salvas do Spotify e as mensagens prontas também mostram **quem mexeu por
último** ("Por Maria Souza"); o que foi feito antes das contas aparece como "Antes das contas".

### Privacidade do histórico

O Proclamare guarda, para cada anúncio, o **nome, a função e o texto** falado (e a data, a hora e o resultado), no
arquivo `anuncios.jsonl` ao lado do `config.json`. **Depois de 90 dias isso é apagado automaticamente.** **Pelo
painel, ninguém pode editar nem apagar** esses registros (nem o administrador). Avise a equipe de que isso existe e
evite nomes completos, CPF e placas nos anúncios (veja a seção 10). Os registros de segurança (`auditoria.log`)
continuam sem o texto dos anúncios.

### Cuidados importantes

- **Faça backup do `contas.json`** (fica ao lado do `config.json`, nunca vai para o Git).
- **Se aparecer "As contas do Proclamare sumiram"**: o `contas.json` foi apagado ou estragou. Por segurança o
  Proclamare **não volta ao modo antigo**: ninguém entra (nem no computador da loja) até resolver, mas a aba
  **Tocador da loja** e os **horários** continuam tocando. Para resolver:
  1. **Melhor:** desligue o Proclamare, copie o seu backup do `contas.json` de volta para a pasta do Proclamare e
     ligue de novo. Todos entram como antes. (A cópia do arquivo estragado fica guardada como
     `contas.json.corrompido-...`.)
  2. **Sem backup:** no **computador da loja**, com o Proclamare **desligado**, abra um terminal na pasta do
     Proclamare e rode `.venv\Scripts\python.exe server.py --esquecer-contas`. Ele pede para digitar `ESQUECER`
     para confirmar. Depois ligue o Proclamare: o painel volta a pedir **Criar conta de administrador** e as contas
     antigas não valem mais (crie todas de novo).
- Com contas, `--definir-pin` e `--remover-pin` não funcionam mais: o PIN não vale.
- **Quem usa o Windows do computador da loja consegue mexer nos arquivos do Proclamare** (contas, histórico,
  configuração) e, com isso, se tornar administrador ou apagar o histórico. O Proclamare não tem como impedir quem
  já está dentro do Windows. Por isso: **bloqueie o Windows** (tecla Windows + L) quando sair do computador e, se
  possível, crie um **usuário do Windows separado para os funcionários** (usuário padrão, **sem** ser
  administrador), só para usar o navegador, deixando o Proclamare rodando no usuário do dono.
- **A Wi-Fi dos clientes deve ser separada** da Wi-Fi da equipe (a maioria dos roteadores tem a opção "rede de
  convidados"). Assim um cliente nem chega a ver o Proclamare.

---

## 3. Conectar o Spotify (passo a passo)

Diferente da v1, a música do Spotify **não toca pelo navegador** — ela toca
pelo **aplicativo oficial do Spotify**, instalado e logado no computador da
loja. O Proclamare só manda comandos para ele (tocar, pausar, trocar de
música, ajustar volume), usando o recurso do Spotify chamado **Spotify
Connect**. É exatamente por isso que o controle funciona de qualquer
computador: quem manda o comando não precisa ser quem está com o Spotify
aberto.

**Requisitos (regras do Spotify em 2026):**
- A conta que **cria o aplicativo** no painel de desenvolvedores precisa ser
  **Premium**. Um aplicativo em *Development Mode* (o modo de qualquer app novo)
  só funciona enquanto a conta dona dele for Premium.
- Só quem está cadastrado pode usar o aplicativo: a **dona** do app e **até 5
  contas** adicionadas em **User Management** (no painel do app, em *Settings*).
  Qualquer outra conta recebe erro **403** em tudo — o Proclamare mostra isso
  como "A conta não tem acesso ao Spotify".
- Para tocar, trocar de música e mudar o dispositivo, a conta conectada
  também precisa ser **Premium**.
- O aplicativo oficial do Spotify instalado e **logado** no computador da
  loja (pode ser o app de desktop do Windows/Mac/Linux).

**Passo a passo (uma vez só):**

1. Entre em **[developer.spotify.com/dashboard](https://developer.spotify.com/dashboard)** com a conta Spotify Premium que vai tocar a música na loja.
2. Clique em **Create app**. Dê qualquer nome (ex.: "Proclamare") e uma
   descrição curta. Em **Which API/SDKs are you planning to use?** marque
   **Web API**.
3. Em **Redirect URIs**, cadastre exatamente `http://127.0.0.1:8502/spotify/callback`
   (o mesmo endereço mostrado no painel **Configurar Spotify**, com o botão de
   copiar) e salve. O Spotify só aceita `http` para o IP de loopback `127.0.0.1`
   — `localhost` e o IP da rede (ex.: `192.168.0.23`) são recusados. Por isso a
   conexão (passo 6) deve ser feita **no computador da loja, abrindo
   `http://127.0.0.1:8502`**. Se precisar conectar de outro aparelho, use HTTPS
   (um proxy como Caddy/nginx com certificado), cadastre o URI `https://...` e
   informe-o em `PROCLAMARE_SPOTIFY_REDIRECT`. **Atenção:** um proxy no PC da
   loja faz todo aparelho parecer "o PC da loja" e ignorar o PIN; nesse caso
   `PROCLAMARE_CONFIAR_LOOPBACK=0` é obrigatório (veja "Acesso pela rede (PIN)").
4. No painel do app, abra **Settings**. Copie o **Client ID** e, em **View
   client secret**, o **Client Secret**. Cada um tem **32 letras e números**.
5. No computador da loja, abra `http://127.0.0.1:8502`, clique em **Configurar
   Spotify**, cole o Client ID e o Client Secret e salve. Se vier algum espaço
   ou aspas junto na cópia, o Proclamare tira sozinho; se o código não tiver 32
   letras e números, ele avisa "Cole só o código (32 letras e números)".
6. Ainda no computador da loja, clique em **Conectar Spotify**, faça login e
   aceite. Se você clicar em Conectar antes de salvar o Client ID/Secret, o
   Proclamare abre o **Configurar Spotify** com um aviso, em vez de tentar o login.
7. **No computador da loja**, abra o app oficial do Spotify e dê play em
   qualquer música uma vez (só para ele "aparecer" como dispositivo).
8. De volta no Proclamare, clique em **Atualizar** ao lado de "Dispositivo"
   e escolha o Spotify do computador da loja na lista. Aparelhos marcados como
   *restritos* ficam desabilitados (o Spotify não deixa controlá-los) e os que
   não aceitam volume tocam normalmente, mas a música deles **não é abaixada**
   durante os anúncios.

Pronto — a partir daqui, qualquer computador de controle que abrir o
Proclamare já enxerga esse mesmo dispositivo conectado e pode buscar
músicas, tocar, pausar e ajustar o volume.

**Importante sobre o Client Secret:** ele fica guardado só no computador da
loja (arquivo `config.json`, que nunca deve ser compartilhado ou enviado
para a internet) — é o que permite o servidor renovar sozinho o acesso ao
Spotify sem pedir login de novo a cada uso.

### Playlists do Spotify salvas (atalhos)

Se a loja toca sempre a mesma playlist (ou álbum) do Spotify, salve como um **atalho**: um nome + a playlist, e ela toca com
um toque.

1. No Spotify, em uma playlist ou álbum, escolha *Compartilhar → Copiar link* (algo como
   `https://open.spotify.com/playlist/...`). Vale também o formato `spotify:playlist:...`.
2. No Proclamare, em *Música → Spotify*, toque em **Salvar playlist do Spotify**, dê um nome (até 40 letras) e cole o link.
   Se a playlist **já estiver tocando** no Spotify, o painel oferece **Salvar a playlist que está tocando** (e sugere o nome).
3. Para tocar, toque no atalho. Se a aba **Tocador da loja** estiver tocando alguma coisa, ela é **pausada** (a música do
   Spotify toma o lugar).

Só entram **playlists e álbuns** (não músicas avulsas, artistas ou podcasts). Qualquer outro link recebe a mensagem *"Cole o
link de uma playlist ou álbum do Spotify"*. São até 50 atalhos. Tocar o atalho precisa do Spotify conectado, do aplicativo
aberto no computador da loja e de conta Premium, igual aos botões Play do Spotify; se faltar algo, o painel mostra a mesma
mensagem de sempre (por exemplo *"Abra o Spotify no PC da loja e dê play uma vez"*). Os atalhos também ficam em `listas.json`.

### A conexão vale por cerca de 6 meses

O Spotify faz a autorização expirar mais ou menos **6 meses** depois de você
aceitar (e ela também some se você remover o acesso do app na sua conta
Spotify ou trocar o Client Secret). Quando isso acontece:

- a lâmpada **Spotify**, no topo, fica âmbar com **Reconectar** e o painel
  mostra o cartão **Reconecte o Spotify**;
- o servidor para de tentar renovar sozinho (não adianta esperar);
- é só clicar em **Reconectar Spotify**, no computador da loja, e aceitar de
  novo. O Client ID e o Client Secret continuam salvos.

### O que a lâmpada do Spotify quer dizer

| Lâmpada / aviso | Significa | O que fazer |
|---|---|---|
| **Conectado** (verde) | Tudo certo. | — |
| **Configurar** (apagada) | Falta o Client ID/Client Secret. | **Configurar Spotify** (passos 4 e 5). |
| **Desconectado** (apagada) | Credenciais salvas, falta autorizar. | **Conectar Spotify** (passo 6). |
| **Reconectar** (âmbar) | A autorização venceu ou foi revogada. | **Reconectar Spotify** (acima). |
| **Sem acesso** (âmbar) | O Spotify recusou (403): a conta não é Premium, não é a dona do app ou não está em *User Management*. | Confira a conta (requisitos acima). Para outra conta usar o app, adicione o nome e o e-mail dela em *User Management* (até 5). |
| **App fechado** (âmbar) | Nenhum aparelho Spotify ativo (404). | Abra o Spotify no PC da loja e dê play uma vez; depois **Atualizar aparelhos**. |
| **Limite** (âmbar) | O Spotify pediu uma pausa por excesso de pedidos (429). | Espere o tempo indicado (alguns segundos). |
| **Instável** / **Sem conexão** (âmbar) | O Spotify ou a internet do PC da loja não responderam. | Nada, o painel tenta de novo sozinho. Se durar, confira a internet. |

Quando um anúncio toca, o painel também pode avisar que **a música não
abaixou** ou que **o volume não voltou ao normal**. No segundo caso o
Proclamare continua tentando sozinho por até 30 minutos (aparece o aviso "O
volume da música ainda está baixo"); se preferir, ajuste o volume no próprio
painel.

### Se o Spotify mostrar "INVALID_CLIENT: Invalid redirect URI"

Esse é o texto exato do erro, e ele aparece na própria tela do Spotify: nesse
caso o Spotify **não volta** ao Proclamare. (Se o painel mostrar esse mesmo
título ao voltar do login, a causa é a mesma.) Quer dizer que o **Redirect URI** cadastrado no
painel de desenvolvedores não é idêntico ao do Proclamare. Confira, caractere a
caractere (com ou sem a `/` no final já derruba o login), o endereço mostrado em
**Configurar Spotify** — normalmente `http://127.0.0.1:8502/spotify/callback` —
com o que está em **Settings → Redirect URIs** do app, salve lá e tente
conectar de novo. Outros motivos que o painel mostra ao voltar do login:
conexão **recusada** (você cancelou), **pedido expirado** (tela do Spotify
aberta por muito tempo: tente de novo), **Client ID ou Secret não aceitos** e
**falha de rede** com o Spotify.

---

## 4. Usando o YouTube (modo tocador x controle remoto)

O YouTube não tem um recurso equivalente ao Spotify Connect, então alguém
precisa manter uma aba realmente tocando o vídeo. É para isso que existe o
seletor **"Este computador é…"** dentro do módulo Música, aba YouTube:

- **"O tocador (ligado às caixas)"** — escolha essa opção **só no
  computador da loja**. A aba precisa continuar aberta (pode minimizar a
  janela, mas não feche a aba nem o navegador) — é dali que o som realmente
  sai.
- **"Controle remoto"** — é a opção padrão, use em todos os outros
  computadores/celulares. Colar o link, apertar play/pause, pular faixa e
  ajustar volume aqui só manda o comando para a aba tocadora executar.

A lâmpada ao lado do seletor mostra se existe um "tocador" ativo agora
("Tocador ativo" em verde; "Nenhum aberto" é só um aviso neutro — significa que nenhuma aba está tocando agora; "Sem conexão" indica que o servidor não respondeu).

Não precisa de login nem de chave de API: cole o link de um vídeo ou de uma
playlist do YouTube (`https://www.youtube.com/watch?v=...` ou
`https://www.youtube.com/playlist?list=...`) e clique em **Tocar agora**
(ou aperte Enter).

- **Link de uma música dentro de uma playlist** (`watch?v=...&list=PL...`):
  a playlist começa **nessa música**, não na primeira.
- **Link de "Mix" do YouTube** (aqueles com `list=RD...` ou `start_radio=1`,
  que o YouTube monta sozinho): toca **só a música do link** — o Mix é
  personalizado e não funciona fora do site do YouTube. Um link só de Mix,
  sem `v=`, é recusado com uma mensagem explicando.
- **Tempo de início** (`t=90`, `t=1m30s`): respeitado.

### Fila

O botão **Adicionar à fila** guarda o link para tocar em seguida. O bloco
**Fila** mostra o que está tocando agora, os próximos da fila (com botões para
tocar já, subir, descer e remover, além de **Limpar fila**) e, quando uma
playlist está tocando, as próximas músicas dela (clique para pular).

- A fila passa na frente da playlist: quando a música atual termina, toca o
  próximo da fila; quando a fila esvazia, a playlist continua de onde parou.
- O botão **Próximo** também toca o próximo da fila, se houver.
- Item da fila que não pode tocar fora do YouTube (privado ou com reprodução
  externa bloqueada pelo dono) mostra um aviso e a fila segue para o próximo,
  sem travar. Se for algo que você mandou **tocar agora**, aparece só o aviso.
- Celulares e outros computadores (controle remoto) veem e mexem na mesma fila.
- A fila fica na memória do Proclamare: se o programa for reiniciado, ela some.
- Os títulos e miniaturas vêm do próprio YouTube (sem login nem chave de API).

Os comandos de um computador remoto chegam à aba tocadora de forma quase
instantânea: ela fica conectada ao servidor por *long-poll* (a requisição
espera até 20 segundos por novos comandos e responde no mesmo instante em
que um chega), sem consultar o servidor a cada segundo.

---

## 4b. Minhas músicas (arquivos do computador e do celular)

É para quem **baixa a música** e quer tocá-la na loja sem depender de internet,
Spotify ou YouTube. Baixou, a música aparece no Proclamare; escolheu, ela toca.

No painel, vá em **Música → Minhas músicas**.

### Onde ficam as músicas

Existe uma pasta chamada **Músicas do Proclamare** dentro da pasta *Músicas* do
Windows do computador da loja (se o Windows guarda suas Músicas no OneDrive,
é lá que ela fica). Ela é criada sozinha na primeira vez.

- Para abri-la, clique em **Abrir pasta** (esse botão só existe no computador da
  loja). Se a pasta estiver vazia, o painel mostra o endereço dela.
- **Tudo que cair nessa pasta aparece no painel sozinho**, em poucos segundos
  (o Proclamare confere a pasta a cada ~15 segundos). Se quiser na hora, clique no botão de
  **atualizar** (as duas setas). Um arquivo que ainda está sendo copiado espera
  uns 2 segundos para entrar na lista.
- Você pode criar **subpastas** (até 3 níveis, por exemplo *Samba* ou
  *Rock\Anos 80*): elas viram botões de pasta acima da lista.
- A pasta fica **fora da pasta do programa**: baixar uma versão nova do
  Proclamare não apaga suas músicas. Para guardá-la em outro lugar, defina
  `PROCLAMARE_MUSICAS_DIR` (seção 2).

### Mandar música pelo celular (ou por outro computador)

1. Abra o Proclamare no aparelho (com o PIN) e vá em **Música → Minhas músicas**.
2. Toque em **Enviar músicas** e escolha os arquivos — no celular dá para pegar
   da **galeria**, de *Downloads* ou do app de arquivos. Pode escolher vários.
3. Uma barra mostra o andamento; no fim, a lista diz **o que foi enviado e o que
   foi recusado, com o motivo** de cada arquivo. Se precisar, toque em **Cancelar**.

As músicas enviadas ficam na subpasta **Enviadas** e já entram na lista.

**Limites do envio:**

| O quê | Limite |
|---|---|
| Formatos | MP3, M4A, AAC, OGG, OPUS, WAV e FLAC (os que o navegador toca) |
| Tamanho | até **100 MB por arquivo** |
| Quantidade | até 20 arquivos por vez e **no máximo 100 MB somados em cada envio** (se escolher mais, o painel divide em lotes sozinho) |
| Espaço | o computador da loja precisa ter pelo menos 1 GB livre; a pasta aceita até 20 GB no total (medidos no disco, **incluindo a subpasta *_removidas***) |
| Subpasta *Enviadas* | até 2000 arquivos |

O Proclamare confere se o arquivo é mesmo um áudio (não basta mudar a
extensão) e **nunca sobrescreve**: se já existir uma música com o mesmo nome, a
nova entra como *"nome (2)"*. Arquivos com informações embutidas (título, capa…)
absurdamente grandes ou estranhas são recusados — um MP3 normal, mesmo com uma capa
grande, passa sem problema.

**Segurança do envio (por que existe um PIN e um limite):** o envio é a única parte
do Proclamare que recebe um arquivo grande. Só o **computador da loja** e os aparelhos
que **já digitaram o PIN** podem mandar mais de 64 KB; qualquer outro aparelho da rede
é cortado logo no começo da conexão, sem que o computador da loja grave nada. Se a
Wi-Fi da loja é usada por clientes, defina um PIN forte (seção 2) ou desligue o envio
com `PROCLAMARE_MUSICAS_ENVIO=0` — a pasta *Músicas do Proclamare* continua valendo.

### Tocar

Cada música tem dois botões: **▶** (tocar agora) e **＋ Fila** (entrar na fila; em
telas bem estreitas aparece só o ＋). Acima da lista:

- **Buscar** por título, artista, álbum, pasta ou nome do arquivo. Não diferencia
  maiúsculas nem acentos e aceita **várias palavras em qualquer ordem**: *garota
  ipanema* e *ipanema garota* acham "Garota de Ipanema"; *tom jobim* acha as músicas
  do artista. Todas as palavras digitadas precisam aparecer.
- **Pastas**: toque numa pasta para ver só ela.
- **Tocar pasta** (ou *Tocar tudo*, ou *Tocar resultados*, se estiver buscando)
  começa a tocar a lista na ordem que você vê. Ela **substitui a fila**: se já houver
  itens na fila, o painel pede um segundo toque para confirmar.
- **Adicionar pasta à fila** coloca tudo no fim da fila sem interromper o que toca.
- **Embaralhar** vale para os dois botões acima. A fila comporta até 200 itens
  por vez.

**Importante — onde a música toca:** as músicas tocam no **Tocador da loja**, a
mesma aba do YouTube (seção 4). Por isso:

- O computador das caixas precisa estar com o painel aberto e em **Tocador da loja**.
  O painel avisa em *Minhas músicas* se não houver nenhum aberto, e no computador da
  loja o botão **Usar este computador** resolve em um toque.
- Músicas e vídeos do YouTube entram **na mesma fila** e se alternam. Quando um
  termina, toca o próximo, seja música ou vídeo.
- Tudo funciona também pelo celular: tocar, pausar, próxima, volume e a fila. No
  celular, **voltar** reinicia a música que está tocando.
- Como no YouTube, **a música abaixa sozinha durante o anúncio** e volta ao
  volume normal no final.
- Se o navegador bloquear o início automático (acontece depois de reabrir a aba
  sem clicar em nada), aparece um aviso: clique em **Tocar** nesta aba uma vez.
- **Sem internet** (ou com o player do YouTube que não carregou), as músicas
  continuam tocando normalmente; um item do **YouTube** da fila avisa que o player
  não carregou e **continua na fila** em vez de sumir.
- As músicas saem pela **saída de som que o navegador usa** no computador da loja
  (normalmente o som padrão do Windows) — a mesma do YouTube. A saída escolhida em
  *Som da loja* vale só para os anúncios por voz.

### Listas de músicas

Além das subpastas do Windows (que continuam funcionando como filtro), dá para montar **listas** no próprio painel, sem
mexer em arquivos. Elas aparecem em *Música → Minhas músicas*, na seção **Listas**.

- **Nova lista**: dê um nome (até 40 letras), toque em **Adicionar músicas**, busque (por título, artista ou pasta) e
  toque nas músicas. Em cada uma use **▲ ▼** para mudar a ordem e a lixeira para tirar (dá para **Desfazer**). Uma lista
  tem até 500 músicas e você pode ter até 50 listas.
- **＋ Lista** em cada música da biblioteca: coloca aquela música numa lista que já existe, ou cria uma lista nova com ela.
- **Tocar** toca a lista na ordem; **Embaralhar** toca em ordem aleatória. Tocar uma lista **troca a música e a fila**
  (se algo estiver tocando, o painel pede um segundo toque). A fila comporta 200 músicas por vez.
- **Editar** muda o nome, as músicas e a ordem; **Apagar** pede um segundo toque e **não apaga os arquivos**, só a lista.
- Se um arquivo sair da pasta, ele continua na lista marcado como **Indisponível** e é pulado ao tocar.
- Se outro aparelho mexer na mesma lista ao mesmo tempo, o painel avisa e pede para salvar de novo.
- Como as músicas, as listas tocam no **Tocador da loja** (aba aberta no computador das caixas). Os dados ficam em
  `listas.json`, ao lado do `config.json`.

#### Listas com YouTube (a "playlist salva" da loja)

Uma lista agora pode misturar **músicas da pasta** com **vídeos e playlists do YouTube**, na ordem que você quiser. Dá para
criar de duas formas:

- **Salvar fila como lista**: monte a fila do jeito que a loja gosta (links do YouTube e músicas da pasta) e toque em
  *Salvar fila como lista*, dando um nome. A lista guarda o que está tocando agora (se você deixar marcado) e tudo o que
  está na fila, na ordem; repetidos são ignorados. A fila em si não muda.
- **Colando links**: ao criar ou editar uma lista, cole links do YouTube (um por linha). Vale link de vídeo, de playlist ou
  de vídeo dentro de playlist; o tempo de início (`t=90`) e a posição na playlist (`index=3`) são respeitados. Link de
  *Mix* automático do YouTube toca só o vídeo (igual à fila); link que é só de Mix é recusado.

Detalhes que ajudam:

- O Proclamare guarda só o **vídeo/playlist** (nunca o link colado) e busca o **título** no YouTube em segundo plano; até
  aparecer, a lista mostra *"Vídeo do YouTube"* ou *"Playlist do YouTube"*. Depois o título fica guardado.
- Uma lista tem até **500 itens** (músicas + YouTube), sem repetir o mesmo item, e a fila comporta **200** por vez (se a lista
  for maior, os últimos não entram ao tocar).
- Uma lista **só com YouTube** não precisa da pasta de músicas. Item do YouTube é sempre contado como disponível; se a
  internet cair, ele simplesmente não toca naquele momento.
- Se o player do YouTube da aba Tocador ainda não carregou (sem internet), a lista toca só as músicas da pasta e avisa que os
  itens do YouTube ficaram de fora.
- Dá para **agendar** uma lista com YouTube na Programação como qualquer outra.

### Remover uma música

No computador da loja, cada linha tem uma lixeira (toque duas vezes para
confirmar). A música **não é apagada**: ela vai para a subpasta **_removidas**
da própria pasta de músicas, de onde você pode recuperá-la. Pelo celular não
dá para remover (só enviar).

- Como a música só **muda de lugar**, ela continua ocupando espaço no disco e
  conta no limite de 20 GB. Para liberar espaço de verdade, esvazie a pasta
  *_removidas* no Explorer.
- Uma música que está **tocando agora** pode não poder ser movida ("não consegui
  mover a música agora"). Espere a música terminar (ou passe para a próxima) e tente
  de novo — nada fica pela metade nem vira um arquivo vazio.

### Se algo não funcionar

- **A música não aparece:** confira se o formato é um dos aceitos e se não está
  numa pasta oculta ou numa subpasta com mais de 3 níveis. Atalhos, pastas
  ocultas e a pasta *_removidas* são ignorados. Clique em atualizar.
- **O envio foi recusado:** a lista de resultados mostra o motivo (formato,
  arquivo maior que 100 MB, arquivo que não é áudio, pouco espaço no disco…).
- **"Falta um componente do Proclamare" / as músicas só mostram o nome do
  arquivo:** o Proclamare foi atualizado sem rodar o instalador. Rode **Instalar
  Proclamare** de novo no computador da loja (ele instala o que falta); o painel
  também avisa isso embaixo do número de músicas.
- **Copiei um arquivo e ele demorou a aparecer:** arquivos recém-copiados (ou com a
  data do arquivo no futuro, comum em ZIPs e pendrives com relógio errado) esperam
  uma conferência de tamanho antes de entrar — alguns segundos. O botão de atualizar
  (as duas setas) espera a conferência terminar antes de dizer que atualizou.
- **Arquivos do OneDrive "só na nuvem":** aparecem na lista só com o nome; ao
  tocar, o Windows baixa o arquivo primeiro (pode demorar um pouco).
- **A música toca mas o título está estranho:** o Proclamare usa os dados gravados
  dentro do arquivo (título, artista, capa); sem eles, usa o nome do arquivo.

> **Direitos autorais:** ter o arquivo no computador não dá o direito de tocá-lo
> para os clientes. Música em estabelecimento comercial exige licenciamento — no
> Brasil, pelo **ECAD** (veja a seção 5).

---

## 4c. Programação (tocar em horários)

Em vez do Agendador de Tarefas do Windows, o próprio Proclamare toca na hora marcada. Cada horário tem **o que
tocar**, **a hora** (relógio do computador da loja) e **quando repete** (todos os dias, dias úteis, fim de semana,
dias escolhidos ou uma data só). São até 100 horários.

**Como marcar um horário no painel.** No computador, o bloco **Programação** fica abaixo do Anúncio; no celular é a aba
**Horários**. Ele mostra o *Horário do PC da loja*, os próximos horários, a lista dos seus horários e as últimas execuções.

1. Toque em **Novo horário** e escolha **Áudio**, **Lista**, **Playlist do Spotify** ou **Anúncio**.
2. Escolha o alvo: busque a música na biblioteca (os formatos que não tocam em horário aparecem desativados), escolha uma
   lista ou um atalho de playlist do Spotify, ou escreva o texto do anúncio (ou toque numa das *Mensagens prontas*; em *Voz do anúncio* dá para mudar a voz).
3. Marque a **hora** e a repetição: *Todos os dias*, *Dias úteis*, *Fim de semana*, *Escolher dias* (D S T Q Q S S) ou *Uma data*.
4. Salve. Cada horário tem um **interruptor** (ativo/desativado), **Tocar agora** (pede um segundo toque, porque interrompe a
   música da loja), **Editar** e **Remover**.

Quando algo toca, o painel mostra **Tocando agora: …** com o botão **Parar**. Se uma lista não tocou porque o Tocador da loja
estava fechado (ou qualquer outro motivo), aparece um aviso, por exemplo *"Lista não tocou: Tocador da loja fechado"*, com o
que fazer; o botão **Entendi** dispensa o aviso. Em **Últimas execuções** ficam as 50 mais recentes (tocou, não tocou, perdido
ou erro, com o motivo); o histórico fica na memória e some se o Proclamare for reiniciado.

> **Dica:** isto substitui o **Agendador do Windows** que muita loja usa para tocar um MP3 em certos horários (por exemplo, a
> Ave Maria às 18:00): o horário agora fica no painel, vale também pelo celular e não depende de um player aberto.

| O que tocar | Como funciona |
|---|---|
| **Áudio** (uma música da pasta, ex.: *Ave Maria* às 18:00) | Toca pelo mesmo caminho dos anúncios (a saída fixada em "Som da loja"), **mesmo com o navegador fechado**. Só MP3, OGG, WAV e FLAC, até 10 minutos. Arquivos M4A/AAC/OPUS não tocam em horário marcado: converta para MP3. |
| **Anúncio de texto** (até 500 caracteres, ex.: aviso de fechamento às 21:45) | Fala com a voz e o motor que você escolheu no horário, **mesmo com o navegador fechado**. |
| **Lista** (ex.: *Manhã calma* de segunda a sexta às 8:00) | **Troca** a música atual pela lista. Precisa da aba **Tocador da loja** aberta; se estiver fechada, o painel avisa "não tocou: Tocador da loja fechado". A lista pode ter itens do YouTube junto das músicas da pasta. |
| **Playlist do Spotify** (um atalho salvo, ex.: *Rock da loja* às 9:00) | **Troca** a música atual pela playlist/álbum no Spotify do computador da loja; se a aba Tocador estava tocando, ela é pausada. Precisa do Spotify conectado e aberto no PC (e Premium). Se não der, o histórico diz o motivo: *Spotify desconectado*, *sem aparelho* (abra o Spotify e dê play uma vez), *conta sem Premium*, *limite do Spotify* ou *indisponível*. Se o atalho for apagado, o horário fica marcado como sem alvo e não toca. Não entra no limite de 60 minutos de áudio por dia. |

**O que acontece com a música que estava tocando.** No horário de um áudio ou anúncio, o Proclamare **pausa** a música
(a da aba Tocador e a do Spotify), toca, e **retoma de onde parou** — só o que estava tocando; o que já estava pausado
continua pausado. Isso vale mesmo se der erro no meio. Se o Spotify não obedecer, o histórico mostra um aviso.

**Horário de uma lista com o Spotify tocando.** A lista **troca** a música: se o Spotify estiver tocando, ele é
**pausado** e **não volta sozinho** (a lista toma o lugar dele). Se a lista não chegar a tocar (por exemplo, a aba
Tocador está fechada), o Spotify continua como estava. Quem preferir outro comportamento deve avisar o desenvolvedor.

**Dois horários no mesmo minuto.** Tocam um depois do outro (uma coisa por vez nas caixas). O segundo espera o primeiro
terminar; só é descartado ("atrasado") se, depois disso, ainda ficar mais de 1 minuto esperando ou se já passaram mais
de 12 minutos do horário dele.

**Limite de áudio por dia.** A soma dos áudios **ativos** não pode passar de **60 minutos** em um mesmo dia da semana
(o painel avisa ao salvar; desativar ou apagar horários sempre funciona). Isso evita que um celular com o PIN deixe
áudio tocando horas nas caixas. Horários criados ou alterados por um celular aparecem com a etiqueta **Criado pelo
celular**; ao trocar o PIN ou desconectar os aparelhos, o painel lembra de revisar a Programação, porque os horários
continuam valendo.

**Parar e testar.** *Tocar agora* executa o horário na hora (mesmo desligado). Durante um áudio agendado, **Parar**
interrompe o áudio e a música volta. A voz do Windows falando sem internet não pode ser cortada no meio; ela termina
em instantes. Enquanto a programação toca, um anúncio digitado recebe "Programação tocando: ..." e o botão de
desligar pede para parar antes.

**Se o computador estava desligado.** O Proclamare não toca horários que já passaram: eles aparecem no histórico
como **perdido**. Se o horário passou há menos de 1 minuto, ainda toca. Mudou o relógio do computador (horário de verão,
ajuste manual)? Nada toca em dobro, e horários que o relógio "pulou" ficam como *perdido*.

Os dados ficam em `programacao.json`, ao lado do `config.json` (não vão para o GitHub).

## 5. Sobre direitos autorais (ECAD e termos do Spotify) — leia antes de usar em loja

Contas pessoais do Spotify e do YouTube são licenciadas para **uso
pessoal**, não para execução pública em um estabelecimento comercial. Tocar
música para os clientes dentro da loja normalmente exige licenciamento à
parte — no Brasil, isso passa pelo **ECAD**. Isso é independente de você ter
Spotify Premium ou não.

Há um segundo ponto, separado do ECAD: os próprios **termos do Spotify**
(os termos de uso e a *Developer Policy* da Web API) não permitem usar uma
conta pessoal/Premium para tocar música em um estabelecimento comercial
nem criar, com a API, um serviço de música ambiente para esse fim. Ou seja,
mesmo pagando o ECAD, o Spotify pessoal não é a fonte apropriada para a loja.
Os termos mudam; confira os vigentes em spotify.com/legal.

Caminhos que costumam ser seguros:

- **Minhas músicas** (seção 4b) com arquivos que a loja **tem licença para
  tocar** (músicas compradas ou liberadas para uso comercial; o ECAD continua
  valendo para a execução pública).
- Um **serviço de música ambiente licenciado** para comércio (os concorrentes
  pesquisados antes — Rádio Mercado, ListenPlay — anunciam "playlists
  licenciadas" como diferencial).

Não sou advogado — isto é só um alerta factual; vale confirmar com o ECAD e
com o fornecedor da música antes de colocar em uso contínuo na loja. O
Spotify continua útil no Proclamare para **testes** e uso pessoal.

---

## 6. Deixando sempre ligado (inicia com o computador da loja)

### Windows — iniciar com o Windows
O jeito mais simples é o instalador (`Instalar Proclamare.bat`): responda **S**
à pergunta "Iniciar o Proclamare junto com o Windows?". Ele cria, na pasta de
Inicialização do seu usuário, um atalho que usa o `.venv` da pasta do projeto e
o argumento `--silencioso` (liga o servidor sem abrir o navegador). Para
desfazer, rode o instalador de novo e responda **N**, ou use **Desinstalar
Proclamare**. Se mover a pasta, rode o instalador de novo.

Depois, abra o navegador nesse mesmo computador, acesse o Proclamare e
deixe a aba do YouTube marcada como **"O tocador"**, se for usar o YouTube.
(Alternativa manual: Agendador de Tarefas rodando `.venv\Scripts\pythonw.exe Proclamare.pyw --silencioso`
com "Iniciar em" apontando para a pasta do projeto.)

Se usar o Agendador de Tarefas, configure a tarefa para rodar **com a mesma conta de quem
instalou o Proclamare**: o `.venv` depende do Python (o do computador ou o que o Proclamare
baixou), e a pasta do Python do Proclamare, em `%LOCALAPPDATA%` da conta que instalou, não é
acessível por outra conta.

### Windows — religar sozinho se cair (opcional)
O atalho de Inicialização só liga o programa quando alguém entra no Windows. Se o
Proclamare **travar** ou o Windows reiniciar sozinho de madrugada, as caixas ficam
mudas e os horários não tocam. O instalador pergunta "Religar sozinho se cair?"
(ou use `-ReiniciarAutomatico` / `-NaoReiniciarAutomatico`). Ele cria uma **tarefa do
seu usuário** (sem administrador, sem serviço, sem privilégios) chamada
"Proclamare - religar sozinho", que no logon e a cada 5 minutos roda
`pythonw -I Proclamare.pyw --vigia` (modo isolado, como os atalhos). O `--vigia` **só religa se o programa caiu** (sobrou o arquivo
`logs/proclamare.pid` sem processo vivo); se você desligou de propósito
("Desligar Proclamare" ou o botão do painel), ele não faz nada. Para o Windows entrar
sozinho na sua conta depois de reiniciar, configure o logon automático (`netplwiz`)
**sabendo que isso deixa o PC aberto para quem estiver diante dele**. Para remover a
tarefa, rode o instalador respondendo **N** ou use "Desinstalar Proclamare".

### Linux — systemd
`/etc/systemd/system/proclamare.service`:
```ini
[Unit]
Description=Proclamare
After=network.target sound.target

[Service]
Type=simple
WorkingDirectory=/caminho/para/proclamare
ExecStart=/caminho/para/proclamare/venv/bin/python server.py
Restart=always
User=SEU_USUARIO

[Install]
WantedBy=multi-user.target
```
Se você já tinha um serviço da versão anterior, desative-o antes para que os dois
não disputem a porta 8502 (troque `NOME_DO_SERVICO_ANTIGO` pelo nome usado na
instalação; `systemctl list-units --type=service` lista os serviços):
```bash
sudo systemctl disable --now NOME_DO_SERVICO_ANTIGO
sudo systemctl enable --now proclamare
```

### Diagnóstico do som (para suporte)

Para a equipe de suporte entender como o som da loja está ligado (qual saída é
a das caixas, volume, mudo, rede e firewall), dê duplo clique em
`Diagnosticar Som.bat` e escolha:

- **[1] Diagnóstico (só leitura):** apenas lê informações do computador. Não
  toca som, não muda volume, mudo, saída padrão nem qualquer configuração.
- **[2] Diagnóstico + teste audível:** além do acima, **TOCA SOM nas caixas**
  (um bipe em cada saída que você escolher e a voz "Teste do Proclamare").
  Faça **somente com a loja fechada**; o programa pede confirmação S/N antes.

Ao terminar, abre a pasta `logs\` com dois arquivos,
`diagnostico-AAAAMMDD-HHMMSS.txt` (para ler) e `.json`. Envie o `.txt` à
equipe. O relatório **não contém** endereço IP, nome do Wi-Fi, senhas, o
`config.json`, IDs completos de dispositivos nem endereço Bluetooth completo
(só os 4 últimos dígitos). Ele **contém** nomes de tarefas agendadas e de
arquivos de áudio (sem pastas nem argumentos) e nomes de aparelhos Bluetooth
de áudio; dê uma olhada antes de enviar. Deixe o Spotify tocando durante o
diagnóstico para ele ver em qual saída o som sai. Se aparecer "Rode primeiro o Instalar
Proclamare.bat", faça a instalação antes.

---

## 7. Som da loja (saídas de som)

O Proclamare **reconhece tudo o que está ligado ao PC da loja** (a saída de linha
que vai para a mesa, placas USB, a saída HDMI do monitor, caixas Bluetooth já
pareadas) e **garante que o anúncio saiu no lugar certo**: você escolhe uma vez
qual saída é a das caixas da loja, testa com um bipe, e daí em diante ele avisa
se alguma coisa mudar no caminho.

> Em lojas que **não** configuram nada aqui, o Proclamare continua tocando no
> som padrão do Windows, exatamente como antes. Esta seção é opcional.

**O que ele não faz:** equipamentos ligados só por cabo (mesa de som,
amplificadores) não conversam com o computador, então nenhum programa os
"enxerga". Para eles o Proclamare entrega um passo a passo de conferência
(canal aberto na mesa, chave do amplificador, cabo na saída certa) e o monitor
de alertas descrito abaixo.

### O tile "Som da loja"

No painel principal, junto dos indicadores (servidor, anúncio, Spotify,
YouTube), aparece o indicador **Som da loja**. Ele só aparece quando o
computador é Windows e o recurso está disponível.

- **Verde:** "*nome da saída* · tocando" (o anúncio/teste saiu com sinal).
- **Cinza:** "*nome da saída* · em silêncio" (nada tocando no momento).
- **Vermelho:** "Cabo desconectado", "Saída mutada no Windows", "Volume em 0% no
  Windows", "Em uso exclusivo por outro app" ou "Proclamare precisa ser
  reiniciado".
- **Âmbar:** "Padrão do Windows" (nenhuma saída foi escolhida ainda; use o link
  **Configurar**) ou um selo de aviso (por exemplo, o padrão do Windows mudou).

Um clique no tile abre o assistente. Os alertas aparecem também como aviso
discreto, uma vez cada.

### O assistente "Minhas saídas de som"

Em **Configurações → Minhas saídas de som**:

1. **Procurar:** mostra o que o Proclamare já conhece. **Procurar de novo** lê o
   computador outra vez (só leitura: não toca som e não muda nada). Para uma
   caixa Bluetooth, **pareie nas Configurações do Windows** e ela aparece aqui
   sozinha; o Proclamare não faz pareamento.
2. **Lista:** agrupada em "Neste PC", "Bluetooth" e "Música do Spotify". Cada
   linha mostra o seu apelido (ou um nome sugerido), o nome que o Windows dá,
   e selos como **Sugerida**, **Padrão do Windows**, **Mutada**,
   **Desconectada**. Nomes sugeridos: "Cabo para a mesa" para a saída de linha,
   "Monitor (HDMI) - normalmente não vai para as caixas", "Placa USB 1".
   O final do endereço de aparelhos Bluetooth aparece separado do nome para
   diferenciar caixas iguais.
3. **Testar:** toca um **bipe** e a frase "Teste do Proclamare" seguida do seu
   apelido (nunca o nome técnico do Windows) e pergunta **"Ouviu na loja?"**.
   - **Sim:** dê um apelido e toque em **Usar para anúncios**.
   - **Não:** o painel mostra se o PC enviou sinal e uma lista de conferência
     do equipamento (canal do PC aberto na mesa, amplificador em LINE, chave do
     canal em AUX1/USB, cabo na saída verde/de linha, saída não mutada).

   Há um interruptor **Abaixar a música durante o teste** (ligado por padrão
   no celular, desligado no PC da loja).
4. **Preferências** (recolhido): o que fazer se a saída sumir, alerta de
   silêncio e se a música deve abaixar no Spotify e no YouTube.

Zonas separadas (estacionamento, elevador etc.) **ainda não existem**: por
enquanto há uma saída para a loja toda.

### Limitações conhecidas da F1

- **Caixa Bluetooth ociosa:** o 1º anúncio depois de um longo silêncio pode perder o
  começo da fala (a caixa "acorda" devagar). O Proclamare ainda não toca um trecho de
  silêncio antes do anúncio; isso será decidido depois de medir numa caixa real.
- Um aviso de "saída alterada por aparelho remoto" some quando alguém escolhe o destino
  no PC da loja ou quando o Proclamare reinicia.

### Testar pelo celular

Dá para fazer o teste de dentro da loja: vá até a área das caixas, abra o
painel no celular, entre com o PIN e toque em **Testar**. Como o bipe sai nas
caixas, o painel avisa antes de tocar (principalmente em horário de
funcionamento). Limites: 3 testes por minuto por aparelho e 10 a cada 10
minutos no total.

Para **trocar a saída dos anúncios** pelo celular (cabo ou USB), é preciso ter
testado e confirmado "Ouviu: sim" **no mesmo aparelho, nos 5 minutos
anteriores**. Toda troca feita por um aparelho remoto fica registrada em log e
deixa um aviso no tile até alguém olhar.

### O que só dá para fazer no PC da loja

Por segurança, estas ações **não funcionam pelo celular**, mesmo com PIN:

- aprovar ou usar para anúncios uma saída **Bluetooth, HDMI ou fone**;
- escolher "usar o som padrão do Windows" como plano B (`padrao_windows`);
- esquecer a saída usada nos anúncios.

No celular os botões dessas ações ficam escondidos, com a nota "aprovar no PC do
Proclamare".

### O que o Proclamare nunca faz

- **Nunca troca a saída padrão do Windows**, nem muda o **volume** ou o **mudo**:
  só lê. Quem decide o padrão, o volume e o mudo é você, no Windows.
- Nunca pareia, reconecta ou desconecta aparelhos Bluetooth.
- Nunca toca em saída que você não aprovou e nunca cai em HDMI ou fone por
  conta própria.
- Nunca toca um bipe sozinho: som de teste só com clique seu.
- Nunca abre a saída em modo exclusivo (não "tranca" o dispositivo).
- Nunca fala o nome técnico do Windows pela voz.

Se a saída escolhida sumir, a preferência **"Se a saída sumir"** define o plano
B: *usar outra saída aprovada, ou avisar que falhou* (padrão), *só falhar*, ou
*usar o padrão do Windows* (só no PC da loja). Quando não há saída possível, o
anúncio **não toca**, o painel mostra o motivo e a música não é abaixada à toa.

### Alertas e como corrigir

Os alertas aparecem no tile e em avisos. O Proclamare **só avisa**; a correção
é sempre feita por você no Windows.

| Alerta | O que significa | Como corrigir |
|---|---|---|
| **O padrão do Windows mudou** (ex.: para "Monitor (HDMI)") | Algo (muitas vezes o driver de vídeo ao ligar o monitor) trocou a saída padrão. A **música e as tarefas agendadas** foram para lá; os anúncios continuam na saída escolhida. | Windows: Configurações → Sistema → Som → em "Saída", escolha de novo a saída das caixas (ou botão direito no ícone de som da barra de tarefas → Configurações de som). |
| **Saída mutada** | A saída dos anúncios está no mudo no Windows. | Clique no ícone de alto-falante na barra de tarefas e tire do mudo (ou use o Mixer de volume). |
| **Volume em 0%** | O volume daquela saída no Windows está zerado. | Suba o volume no Windows. O volume do anúncio nas caixas é ajustado na mesa/amplificador. |
| **Em uso exclusivo por outro app** | Outro programa "trancou" a saída em modo exclusivo e o Proclamare não consegue tocar. | Painel de Controle → Som → aba Reprodução → duplo clique na saída → aba **Avançado** → desmarque **"Permitir que aplicativos assumam o controle exclusivo deste dispositivo"**. Se persistir, feche o programa que está usando a saída (o Spotify também: confira as opções de áudio dele). |
| **Cabo desconectado / saída ausente** | O Windows não vê mais a saída (cabo solto, USB retirada, Bluetooth desligado). | Reconecte. Uma saída aprovada que volta igual é reativada sozinha. |
| **O anúncio não gerou sinal** | O PC tocou, mas o Windows não mostrou som saindo naquela saída. | Confira mudo, volume, cabo e se a saída certa está escolhida. |
| **Silêncio** | Nada toca há mais tempo que o limite configurado (dentro do horário configurado). | Veja se a música/playlist parou. O limite e o horário ficam em Preferências. |
| **Saída mudou / placa USB mudou, testar de novo** | Uma placa USB apareceu com outra identidade (outra porta, driver). As placas USB aprovadas ficam **suspensas** até você testar de novo. | Refaça o teste (bipe → "Ouviu?") e aceite a nova placa, ou esqueça a antiga. |
| **Proclamare precisa ser reiniciado** | O áudio travou de um jeito que só reiniciando resolve. | Menu Iniciar → Desligar Proclamare e ligue de novo. |

### `PROCLAMARE_CONFIAR_LOOPBACK=0` e o som

Com `PROCLAMARE_CONFIAR_LOOPBACK=0` (necessário com proxy, túnel ou
redirecionamento de porta no PC da loja) **ninguém é "o PC da loja"**: todos os
aparelhos, inclusive o próprio PC, precisam de PIN, e tudo que é "só no PC da
loja" desta seção fica **indisponível pela tela**. Na prática, aprovar
Bluetooth/HDMI/fone, usar o padrão do Windows como plano B e esquecer a saída
dos anúncios só podem ser feitos com o Proclamare desligado, editando a chave
`som` do `config.json`. Saídas por cabo e USB continuam podendo ser testadas e
escolhidas com PIN, pela regra do teste recente.

### Privacidade

O Proclamare não grava nem escuta áudio: o "sinal detectado" vem do medidor do
próprio Windows para o processo do Proclamare. O `config.json` guarda só a sua
escolha de saída (apelido e uma referência interna) e continua fora do Git.

---

## 8. Solução de problemas

- **"Motor offline indisponível" / erro de eSpeak:** falta instalar o
  `espeak-ng` (só Linux, passo 1).
- **Outro computador não abre o endereço:** confirme que está na mesma
  rede da loja, que usou o IP certo (não `localhost`) e que a porta 8502
  não está bloqueada pelo firewall do computador da loja (seção 2).
- **Spotify não toca / dispositivo não aparece:** veja a tabela "O que a
  lâmpada do Spotify quer dizer" (seção 3). Resumo: (1) a conta que criou o app
  precisa ser Premium e quem usa precisa ser a dona ou estar em *User
  Management* (até 5 contas); (2) o app oficial do Spotify está aberto e logado
  no computador da loja; (3) você já deu play nele uma vez; (4) o Redirect URI
  cadastrado no painel do Spotify é *idêntico* ao mostrado em "Configurar
  Spotify" (erro "INVALID_CLIENT: Invalid redirect URI"); (5) a conexão vale
  cerca de 6 meses — se a lâmpada estiver âmbar com "Reconectar", clique em
  **Reconectar Spotify**; e (6) clique em **Atualizar aparelhos** para buscar o
  dispositivo de novo.
- **YouTube não obedece comandos de outro computador:** confira se algum
  computador está com a aba aberta e marcada como "O tocador" — sem isso,
  não existe ninguém para executar os comandos (a lâmpada mostra "Nenhum
  tocador aberto" nesse caso).
- **YouTube não carrega:** confira se o link é de um vídeo ou playlist do
  YouTube (`watch?v=`, `youtu.be/`, `shorts/`, `live/`, `embed/` ou `list=`).
  Link só de Mix (`list=RD...` sem `v=`) não é aceito: abra uma música do Mix
  e copie o link dela.
  Se o player mostrar o "Erro 153", o navegador não está enviando o Referer ao
  YouTube — o servidor já usa `Referrer-Policy: strict-origin-when-cross-origin`
  para isso; confira se não há extensão bloqueando referrer.
- **Minhas músicas não tocam:** elas tocam no **Tocador da loja** (como o YouTube):
  confira se o computador das caixas está com o painel aberto em "Tocador da
  loja" (a lâmpada mostra "Tocador ativo"). Veja a seção 4b.
- **Minhas músicas está vazio:** coloque os arquivos na pasta *Músicas do
  Proclamare* (botão **Abrir pasta** no PC da loja) ou use **Enviar músicas**.
- **Erro "Host não permitido" (421):** o endereço usado no navegador não é um
  IP, `localhost` nem o nome do computador. Acesse por IP ou adicione o nome em
  `PROCLAMARE_ALLOWED_HOSTS`.
- **Anúncios param de tocar e dão erro:** a voz do Windows (SAPI) pode ter
  travado. O servidor já se recupera sozinho na maioria dos casos (log
  "executor substituído"); se repetir, reinicie o `server.py`.
- **Sem som nenhum:** confira se a saída de áudio padrão do computador da
  loja é mesmo a placa ligada ao amplificador.

---

## 9. Se algo der errado (segurança)

Respire: o Proclamare só toca som e controla música; o pior caso é alguém anunciar ou programar
algo indevido. Faça os passos na ordem. **Os passos de troca de PIN só funcionam no computador
da loja.**

### O PIN vazou ou alguém desconhecido está usando o painel
1. No computador da loja, abra a área **Segurança** do painel: veja os **últimos acessos** (hora e
   IP) e **Feito por aparelhos da rede** (o que foi criado ou alterado por celulares).
2. **Configurações → Acesso pela rede → trocar o PIN** (6 a 8 números, nada óbvio). Trocar o PIN
   **desconecta todos os aparelhos**; avise a equipe do PIN novo. Antes de confirmar, o painel
   pergunta o que fazer com o que aparelhos da rede deixaram (o botão **Apagar o que os aparelhos da rede criaram**
   apaga só o que foi **criado** por eles; o que é seu e foi só **mudado** fica para você aprovar):
   - **desativar os horários** criados/alterados por aparelhos da rede (já vem marcado) e
     interromper o que estiver tocando;
   - **apagar** as mensagens prontas, listas e atalhos do Spotify **criados** por eles (músicas
     enviadas pelo celular **nunca** são apagadas sozinhas: confira e aprove ou remova uma a uma);
   - **restaurar as mensagens de fábrica** e **limpar a fila** do Tocador.
   Na dúvida, marque tudo; o que for seu você refaz em um minuto.
3. Confira o que sobrou: **Programação** (itens com o selo "Criado pelo celular"), **Mensagens
   prontas**, **Minhas músicas → Listas**, atalhos do Spotify e a pasta **Enviadas** (apague
   arquivos estranhos). Use **Aprovar** na área Segurança nos itens que forem seus.
4. Olhe o `logs/auditoria.log` (linhas `acao=login` com `resultado=pin_errado` e IPs que não são
   dos aparelhos da loja).
5. Se a Wi-Fi é compartilhada com clientes: troque a senha dela e separe a rede dos clientes. Para
   impedir envios de música pelo painel, defina `PROCLAMARE_MUSICAS_ENVIO=0` e reinicie.

### Um celular ou computador da equipe foi perdido/roubado
1. No computador da loja: **Configurações → Acesso pela rede → Desconectar todos os aparelhos**. Todos os
   aparelhos terão de digitar o PIN de novo.
2. Se o aparelho perdido podia ficar com o PIN anotado ou salvo, **troque também o PIN** (passo
   acima) e revise a área **Segurança** e a Programação. **Desconectar todos os aparelhos** também oferece
   desativar/apagar o que aparelhos da rede deixaram.
3. Se o aparelho tinha a sua conta do Spotify aberta, troque a senha do Spotify.

### O Client Secret do Spotify vazou (ou o `config.json` foi copiado/publicado)
O `config.json` guarda o Client Secret, o token do Spotify e o hash do PIN.
1. No computador da loja, use **Desconectar Spotify** no painel: ele apaga deste PC o token e o
   aparelho escolhido (marque também a opção de apagar o Client ID e o Client Secret se quiser
   sumir com eles do `config.json`). Isso **só vale neste PC**; o Spotify continua com o app
   autorizado até o passo 3.
2. Em [developer.spotify.com/dashboard](https://developer.spotify.com/dashboard), abra o seu app e
   **gere um novo Client Secret** (o botão pode se chamar "Rotate secret"/"Reset"); o antigo deixa
   de valer.
3. Em [spotify.com/account/apps](https://www.spotify.com/account/apps/), **remova o acesso** do
   app. Isso invalida o token que ficou no arquivo copiado (quem copiou o arquivo ainda o tem).
4. No computador da loja: **Configurar Spotify**, cole o Client Secret novo e conecte de novo.
5. **Troque o PIN** e use **Desconectar todos os aparelhos**: quem copiou o arquivo pode tentar descobrir um PIN
   fraco por tentativa e erro no próprio computador dele.
6. Se o arquivo foi parar num repositório público (GitHub), apagar o arquivo não basta: os passos 2
   e 3 são o que realmente protege.

### O computador da loja foi roubado, vendido ou está com suspeita de vírus
Quem usa o computador da loja controla o painel sem PIN. Faça os passos do Spotify acima (o botão **Desconectar Spotify** com a opção de apagar o Client ID/Secret ajuda), troque a
senha do Spotify e, ao descartar o PC, rode o desinstalador com a opção de apagar os dados
(seção 1, item 8).

### Quero avisar sobre uma falha de segurança
Veja o arquivo `SECURITY.md` do repositório (relato privado pelo GitHub).

---

## 10. Privacidade e LGPD (o que sai do computador e o que fica guardado)

Os anúncios podem conter dados pessoais ("Senhora Maria Souza, sua encomenda chegou", placa de
carro). Quem decide o que anunciar é a loja; estas são as informações para decidir bem:

**O que sai do computador da loja**
- **Texto dos anúncios no modo online** (a voz neural, padrão no modo *Auto* quando há internet):
  o texto é enviado à **Microsoft** (serviço de leitura em voz alta do Edge, usado pela biblioteca
  `edge-tts`), que o processa fora do Brasil, **sem contrato próprio com a sua loja**. Para que
  nada saia, escolha o modo **Offline** (voz do Windows). Em qualquer modo, evite nomes completos,
  CPF, telefone e placas; prefira "cliente do caixa 3" ou o número da senha.
- **Músicas:** o Spotify e o YouTube recebem o que a conta/tocador pede (títulos de vídeo vão ao
  YouTube). O Proclamare não grava nem escuta áudio.
- **Fonte do painel:** a fonte Inter vem do próprio Proclamare (pasta `static/fonts`, licença OFL);
  o painel não consulta o Google Fonts.
- O Proclamare **não envia nada aos seus autores**: não há telemetria.

**O que fica guardado no computador da loja** (tudo na pasta do programa)
- `config.json`: hash do PIN, chave de sessão, Client Secret e tokens do Spotify, saídas de som.
- `contas.json` (v2.8): as contas da equipe (usuário, nome, função, perfil e o **hash** da senha, nunca a senha).
- `anuncios.jsonl` (v2.8): o **histórico de anúncios** com **nome, função e texto** de cada um; **apagado
  automaticamente após 90 dias**; ninguém edita nem apaga (seção 2d).
- `programacao.json`: inclui o **texto** dos anúncios agendados. `listas.json`: listas e atalhos.
- `logs/proclamare.log`: log comum do servidor (rotativo por tamanho: 5 MB × 3 cópias), sem textos
  de anúncios e **sem IP dos aparelhos** (o IP fica só na auditoria, abaixo).
- `logs/auditoria.log` (pode mudar com `PROCLAMARE_AUDIT_FILE`): a **trilha de auditoria**, uma
  linha por ação (`acao=`, `ip=`, `origem=local|remoto`): logins e PINs errados, troca de PIN,
  anúncios (só o **número de caracteres** e o resultado, **nunca o texto**), envio de músicas,
  configuração do Spotify etc. É o **único lugar com o IP dos aparelhos**, com data e fuso. Tem **rotação
  diária e guarda 90 dias**; o que passa disso é apagado sozinho. Comandos de música e fila
  entram só como resumo por minuto.
  Para apagar antes (PC vendido/descartado), use a purga do desinstalador. `audios_temp/` guarda
  temporariamente o áudio dos anúncios.

**O que fica guardado em cada celular/computador da equipe**
- O **histórico dos últimos 20 anúncios** fica no armazenamento do navegador daquele aparelho
  (não no servidor). **Sair** num aparelho da rede **apaga esse histórico e os dados do painel
  naquele aparelho**. No PC da loja (que não tem "Sair"), use **Limpar** no histórico. Em
  aparelhos que mudem de dono, limpe também os dados do site no navegador.

Este texto é informativo e não substitui orientação jurídica. Em dúvida sobre base legal,
aviso aos clientes ou funcionários, consulte um profissional.
