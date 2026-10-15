# Proclamare v2 — anúncio de voz + música (Spotify / YouTube), com controle remoto

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
arquitetura. O YouTube é a única exceção: como ele não tem um recurso
parecido com o Spotify Connect, **um computador precisa ficar com a aba do
Proclamare aberta e marcada como "O tocador"** — normalmente o próprio
computador da loja, tocando em segundo plano. Os demais ficam em "Controle
remoto". Isso é explicado com mais detalhe na seção 4.

---

## 1. Instalação (no computador da loja)

Requer **Python 3.9+**.

```bash
cd proclamare   # pasta do projeto (clonada do GitHub)
python -m venv venv
# Windows: venv\Scripts\activate
# Linux/Mac: source venv/bin/activate
pip install -r requirements.txt
```

### Instalar e ligar com duplo clique (Windows)

Para quem não quer usar o terminal:

1. **Escolha o lugar da pasta:** deixe a pasta do Proclamare **dentro da sua pasta
   de usuário** (ex.: Documentos), não em `C:\` nem em pastas públicas. Se mover
   a pasta de lugar depois, **rode o instalador de novo** (os atalhos guardam o
   caminho antigo).
2. **Instalar (uma vez só):** instale o Python 3.9+ de [python.org](https://www.python.org/downloads/)
   marcando **"Add python.exe to PATH"**. Depois dê duplo clique em
   **`Instalar Proclamare.bat`**. Ele cria o ambiente `.venv`, instala as
   dependências (precisa de internet) e cria os atalhos **Proclamare** (Área de
   Trabalho e Menu Iniciar), **Desligar Proclamare** e **Desinstalar Proclamare**
   (Menu Iniciar). Se o instalador avisar em amarelo que a pasta é gravável por
   todos os usuários do computador, mova-a para a sua pasta de usuário.
3. **Ligar:** dê duplo clique no ícone **Proclamare**. Não aparece janela preta:
   em alguns segundos o painel abre no navegador. Se já estiver ligado, o duplo
   clique só abre o painel (nunca sobe dois servidores). Se aparecer "está
   demorando para ligar", espere alguns segundos e dê duplo clique de novo.
4. **Desligar:** Menu Iniciar → **Desligar Proclamare**. Uma mensagem confirma
   "Proclamare desligado". O atalho só desliga o servidor iniciado pelo próprio
   Proclamare, nunca outros programas. Se ele foi ligado de outra forma (ex.:
   `python server.py` no terminal), feche a janela onde está rodando.
5. **Iniciar junto com o Windows:** o instalador pergunta (S/N). O atalho na
   pasta de Inicialização usa `--silencioso` (liga o servidor sem abrir o
   navegador). Para mudar de ideia, rode o instalador de novo e responda: **N**
   remove o atalho de Inicialização.
6. **Logs:** `logs/proclamare.log` (log do servidor, rotativo: 5 MB × 3 cópias) e
   `logs/inicio.log` (saída bruta do último início, útil para erros antes de o
   log começar).
7. **Se não abrir:** a mensagem de erro cita os arquivos de log — abra-os e veja
   as últimas linhas. Causas comuns: o instalador ainda não foi executado
   (mensagem "Execute primeiro o Instalar Proclamare.bat"); a porta 8502 está
   ocupada por outro programa (use `PROCLAMARE_PORT`); ou o Firewall do Windows
   bloqueou (escolha "Permitir").
8. **Desinstalar:** Menu Iniciar → **Desinstalar Proclamare** (ou, no terminal,
   `powershell -NoProfile -ExecutionPolicy Bypass -File tools\instalar.ps1 -Desinstalar`).
   Remove todos os atalhos, inclusive o de Inicialização, e pergunta se deve
   apagar o `.venv`. A pasta do programa e o `config.json` são mantidos; para
   apagar tudo, exclua a pasta depois.

O lançador sempre conversa com `127.0.0.1`. Se você definiu `PROCLAMARE_HOST`
com um IP específico que não seja de loopback, use `0.0.0.0` ou abra o painel
direto pelo navegador nesse endereço.

Para definir o PIN (6 a 8 dígitos) sem abrir o painel, rode no terminal, dentro
da pasta do projeto: `.venv\Scripts\python server.py --definir-pin`.

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
| `PROCLAMARE_CONFIAR_LOOPBACK` | `1` | `0` = nenhuma conexão é "local": todos precisam de PIN (use com proxy/túnel no PC da loja; veja "Acesso pela rede (PIN)") |
| `PROCLAMARE_ALLOWED_HOSTS` | (vazio) | Nomes de host extras aceitos, separados por vírgula (ex.: `proclamare.loja.local`). IPs, `localhost` e o nome deste computador já são aceitos; qualquer outro nome recebe erro 421 (proteção contra DNS rebinding) |

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
  **Revogar sessões** em Configurações → Acesso pela rede (no PC da loja).
- **Sem PIN, o acesso pela rede fica desligado**: outros aparelhos conseguem abrir
  a página, mas não anunciar nem controlar a música. Remover o PIN também
  desconecta todos.
- **Bloqueio por tentativas:** 5 PINs errados seguidos bloqueiam aquele aparelho
  por 5 minutos (a 5ª tentativa errada já mostra o bloqueio). Se muitos erros
  vierem de aparelhos diferentes (20 em 10 minutos), o login pela rede fica
  bloqueado para todos por 10 minutos; se isso se repetir, o bloqueio dobra
  (20 min, 40 min... até 24 h). Um login certo **não** apaga essa escalada: ela
  só volta ao zero ao trocar/remover o PIN ou revogar as sessões no PC da loja,
  ou desce um nível a cada 24 h sem novo bloqueio global. O computador da loja não é
  afetado.
- **Tentativas suspeitas:** em Configurações → Acesso pela rede, o PC da loja
  mostra quantos PINs errados houve nas últimas 24 horas e se há um bloqueio
  global ativo. Esses números não aparecem para os outros aparelhos.
- **ATENÇÃO — proxy, túnel ou redirecionamento de porta no PC da loja:** se
  houver proxy reverso (Caddy/nginx), túnel (SSH, cloudflared) ou
  redirecionamento de porta (`netsh portproxy`, WSL) no PC da loja, **qualquer
  aparelho consegue se passar pelo PC da loja e ignorar o PIN**, porque todas as
  conexões chegam ao Proclamare como se viessem do próprio PC. Nesses casos
  `PROCLAMARE_CONFIAR_LOOPBACK=0` é **OBRIGATÓRIO** (defina o PIN pela linha de
  comando). Com ele, **ninguém** é "local": todos digitam o PIN, e trocar o PIN
  e configurar o Spotify só são possíveis pela linha de comando / editando o
  `config.json` com o servidor parado.
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
  Não rode serviços de terceiros no mesmo endereço, ou use "Revogar sessões" se
  desconfiar.
- O PIN é guardado apenas como hash (PBKDF2) no `config.json`, junto de uma chave
  de sessão; o PIN em si nunca aparece nos logs. Lembre: a rede da loja usa
  `http` (sem criptografia), então o PIN protege contra curiosos, não contra
  quem consegue espionar o tráfego da rede — mais um motivo para manter o Wi-Fi
  de clientes separado.

---

## 3. Conectar o Spotify (passo a passo, só precisa fazer uma vez)

Diferente da v1, a música do Spotify **não toca pelo navegador** — ela toca
pelo **aplicativo oficial do Spotify**, instalado e logado no computador da
loja. O Proclamare só manda comandos para ele (tocar, pausar, trocar de
música, ajustar volume), usando o recurso do Spotify chamado **Spotify
Connect**. É exatamente por isso que o controle funciona de qualquer
computador: quem manda o comando não precisa ser quem está com o Spotify
aberto.

**Requisitos:**
- Conta Spotify **Premium** (o Spotify recusa comandos de reprodução para
  contas gratuitas).
- O aplicativo oficial do Spotify instalado e **logado** no computador da
  loja (pode ser o app de desktop do Windows/Mac/Linux).

**Passo a passo:**

1. Entre em **[developer.spotify.com/dashboard](https://developer.spotify.com/dashboard)** com a conta Spotify que vai tocar a música na loja.
2. Clique em **Create app**. Dê qualquer nome (ex.: "Proclamare").
3. Em **Redirect URIs**, cadastre exatamente `http://127.0.0.1:8502/spotify/callback`
   (o mesmo endereço mostrado no painel **Configurar Spotify**). O Spotify só
   aceita `http` para o IP de loopback `127.0.0.1` — `localhost` e o IP da rede
   (ex.: `192.168.0.23`) são recusados. Por isso a conexão (passos 5 e 6) deve
   ser feita **no computador da loja, abrindo `http://127.0.0.1:8502`**. Se
   precisar conectar de outro aparelho, use HTTPS (um proxy como Caddy/nginx com
   certificado), cadastre o URI `https://...` e informe-o em
   `PROCLAMARE_SPOTIFY_REDIRECT`. **Atenção:** um proxy no PC da loja faz todo
   aparelho parecer "o PC da loja" e ignorar o PIN; nesse caso
   `PROCLAMARE_CONFIAR_LOOPBACK=0` é obrigatório (veja "Acesso pela rede (PIN)").
4. Copie o **Client ID** e o **Client Secret** mostrados no painel do
   Spotify (clique em "View client secret" para revelar o segredo).
5. No computador da loja, abra `http://127.0.0.1:8502`, clique em **Configurar
   Spotify** (rodapé da página), cole o Client ID e o Client Secret e salve.
6. Ainda no computador da loja, clique em **Conectar Spotify** e faça login
   normalmente — isso só precisa ser feito uma vez. Depois que o token está
   salvo, todos os outros computadores de controle usam o Spotify normalmente.
7. **No computador da loja**, abra o app oficial do Spotify e dê play em
   qualquer música uma vez (só para ele "aparecer" como dispositivo).
8. De volta no Proclamare, clique em **Atualizar** ao lado de "Dispositivo"
   e escolha o Spotify do computador da loja na lista.

Pronto — a partir daqui, qualquer computador de controle que abrir o
Proclamare já enxerga esse mesmo dispositivo conectado e pode buscar
músicas, tocar, pausar e ajustar o volume.

**Importante sobre o Client Secret:** ele fica guardado só no computador da
loja (arquivo `config.json`, que nunca deve ser compartilhado ou enviado
para a internet) — é o que permite o servidor renovar sozinho o acesso ao
Spotify sem pedir login de novo a cada uso.

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
`https://www.youtube.com/playlist?list=...`) e clique em **Carregar**.

Os comandos de um computador remoto chegam à aba tocadora de forma quase
instantânea: ela fica conectada ao servidor por *long-poll* (a requisição
espera até 20 segundos por novos comandos e responde no mesmo instante em
que um chega), sem consultar o servidor a cada segundo.

---

## 5. Sobre direitos autorais (ECAD) — leia antes de usar em loja

Contas pessoais do Spotify e do YouTube são licenciadas para **uso
pessoal**, não para execução pública em um estabelecimento comercial. Tocar
música para os clientes dentro da loja normalmente exige licenciamento à
parte — no Brasil, isso passa pelo **ECAD**. Isso é independente de você ter
Spotify Premium ou não. Não sou advogado — isto é só um alerta factual;
vale confirmar com o ECAD ou um serviço de música ambiente licenciado antes
de colocar em uso contínuo na loja. (Foi por isso, inclusive, que os
concorrentes pesquisados antes — Rádio Mercado, ListenPlay — anunciam
"playlists licenciadas" como diferencial.)

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

## 7. Solução de problemas

- **"Motor offline indisponível" / erro de eSpeak:** falta instalar o
  `espeak-ng` (só Linux, passo 1).
- **Outro computador não abre o endereço:** confirme que está na mesma
  rede da loja, que usou o IP certo (não `localhost`) e que a porta 8502
  não está bloqueada pelo firewall do computador da loja (seção 2).
- **Spotify não toca / dispositivo não aparece:** confirme que (1) a conta é
  Premium, (2) o app oficial do Spotify está aberto e logado no computador
  da loja, (3) você já deu play nele uma vez, (4) o Redirect URI cadastrado
  no painel do Spotify é *idêntico* ao mostrado em "Configurar Spotify" —
  com ou sem a barra `/` no final já derruba o login — e (5) clique em
  "Atualizar" para buscar o dispositivo de novo. Lembre-se de que o URI
  cadastrado deve ser `http://127.0.0.1:8502/spotify/callback` (ou o valor de
  `PROCLAMARE_SPOTIFY_REDIRECT`) e a conexão feita pelo computador da loja.
- **YouTube não obedece comandos de outro computador:** confira se algum
  computador está com a aba aberta e marcada como "O tocador" — sem isso,
  não existe ninguém para executar os comandos (a lâmpada mostra "Nenhum
  tocador aberto" nesse caso).
- **YouTube não carrega:** confira se o link é de um vídeo ou playlist do
  YouTube (`watch?v=`, `youtu.be/`, `shorts/`, `live/`, `embed/` ou `list=`).
  Se o player mostrar o "Erro 153", o navegador não está enviando o Referer ao
  YouTube — o servidor já usa `Referrer-Policy: strict-origin-when-cross-origin`
  para isso; confira se não há extensão bloqueando referrer.
- **Erro "Host não permitido" (421):** o endereço usado no navegador não é um
  IP, `localhost` nem o nome do computador. Acesse por IP ou adicione o nome em
  `PROCLAMARE_ALLOWED_HOSTS`.
- **Anúncios param de tocar e dão erro:** a voz do Windows (SAPI) pode ter
  travado. O servidor já se recupera sozinho na maioria dos casos (log
  "executor substituído"); se repetir, reinicie o `server.py`.
- **Sem som nenhum:** confira se a saída de áudio padrão do computador da
  loja é mesmo a placa ligada ao amplificador.
