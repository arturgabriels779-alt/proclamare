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
cd proclamare
python -m venv venv
# Windows: venv\Scripts\activate
# Linux/Mac: source venv/bin/activate
pip install -r requirements.txt
```

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
| `PROCLAMARE_SPOTIFY_REDIRECT` | `http://127.0.0.1:8502/spotify/callback` | Redirect URI do Spotify. O Spotify só aceita `http` para o IP de loopback `127.0.0.1`/`[::1]`; para outro endereço é preciso `https://` (ex.: proxy com certificado) |
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
- **Segurança da rede:** o Proclamare não tem login — quem alcança a porta
  8502 consegue anunciar e controlar a música. Restrinja a regra do Firewall do
  Windows à sub-rede da equipe e **não deixe o Wi-Fi de visitantes/clientes na
  mesma rede** do computador da loja. O Client Secret só pode ser salvo no
  próprio computador da loja (por isso o painel "Configurar Spotify" recusa
  salvar a partir de outro aparelho).
- **Proteja o `config.json`** (guarda o Client Secret e os tokens). No Windows,
  restrinja o acesso ao seu usuário: `icacls config.json /inheritance:r /grant:r "%USERNAME%:F"`.

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
   `PROCLAMARE_SPOTIFY_REDIRECT`.
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
("Tocador ativo" em verde, ou "Nenhum tocador aberto" em vermelho).

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

### Windows — Agendador de Tarefas
Crie um atalho para `venv\Scripts\python.exe server.py` (com "Iniciar em"
apontando para a pasta do projeto) e agende para rodar "ao fazer logon".
Depois, abra o navegador nesse mesmo computador, acesse o Proclamare e
deixe a aba do YouTube marcada como **"O tocador"**, se for usar o YouTube.

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
