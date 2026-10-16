<div align="center">

<img src="static/apple-touch-icon.png" alt="Logo do Proclamare" width="96" height="96">

# Proclamare

**A central de som da sua loja: anúncios de voz e música ambiente, controlados de qualquer tela da rede.**

![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white)
![Flask](https://img.shields.io/badge/Flask-3.1-000000?logo=flask&logoColor=white)
![Spotify Connect](https://img.shields.io/badge/Spotify-Connect-1DB954?logo=spotify&logoColor=white)
![YouTube](https://img.shields.io/badge/YouTube-player-FF0000?logo=youtube&logoColor=white)
![Sem build](https://img.shields.io/badge/frontend-HTML%20%2B%20CSS%20%2B%20JS%20puros-0071e3)

</div>

---

*Proclamare*, do latim, significa **anunciar em público** — o que o arauto fazia com o clarim.
Este projeto faz o mesmo nas caixas de som de uma loja ou supermercado: você digita a mensagem
em qualquer computador ou celular da rede, e ela sai em voz natural nas caixas da loja. A música
de fundo **abaixa sozinha** durante o anúncio e volta ao volume normal no final.

<p align="center">
  <img src="docs/screenshots/painel-claro.png" alt="Painel do Proclamare no tema claro" width="49%">
  <img src="docs/screenshots/painel-escuro.png" alt="Painel do Proclamare no tema escuro" width="49%">
</p>

## ✨ O que ele faz

### 📢 Anúncios de voz
- **Texto vira fala nas caixas da loja** com vozes neurais em português (Francisca ou Antonio, via Microsoft Edge TTS).
- **Funciona sem internet:** se a conexão cair, usa a voz do próprio sistema operacional (modo *Auto*), ou você escolhe *Online* / *Offline* manualmente.
- **Velocidade ajustável** da fala (−50% a +50%).
- **Mensagens prontas** com um clique (farol aceso, caixa rápido, chamado a colaborador, loja fechando, criança no balcão, teste de som).
- **Histórico** dos anúncios do dia, com um toque para repetir.
- Atalho **Ctrl + Enter** para anunciar e **botão de teste** para conferir as caixas.
- Proteção contra atropelo: um segundo anúncio não toca por cima do que já está no ar.

### 🎵 Música ambiente
- **Spotify (via Spotify Connect):** busca músicas e playlists, toca, pausa, avança, volta e ajusta volume do app oficial do Spotify no PC da loja — com capa do álbum e barra de progresso.
- **YouTube:** toca vídeos ou playlists por link (começando na música do link, até em links de "Mix"), com **fila visível** — adicionar, reordenar, remover e ver as próximas da playlist; uma aba fica como "tocador" no PC da loja e os demais aparelhos viram controle remoto, com comandos chegando quase na hora.
- **Minhas músicas:** baixou a música, ela aparece no painel — é só soltar os arquivos (MP3, M4A, AAC, OGG, OPUS, WAV, FLAC) na pasta *Músicas do Proclamare* do PC da loja, ou **enviar pelo celular**, direto da galeria. Busca, pastas, **Tocar pasta** com *Embaralhar* e a mesma fila do YouTube; toca na aba "Tocador da loja".
- **Abaixa a música automaticamente** durante cada anúncio (Spotify, YouTube e Minhas músicas) e restaura o volume no final.

### 🖥️ Controle de qualquer lugar da rede
- O som sempre sai no computador ligado ao amplificador; **qualquer PC, tablet ou celular** da rede local abre o painel no navegador, sem instalar nada.
- **Outros aparelhos pedem um PIN** (6 a 8 números, definido no PC da loja em Configurações → Acesso pela rede, ou por `python server.py --definir-pin`). O PC da loja nunca pede PIN; sem PIN definido, o acesso pela rede fica desligado.
- Indicadores ao vivo: servidor online, anúncio no ar, Spotify conectado, tocador do YouTube ativo.
- **Som da loja:** reconhece tudo o que está ligado ao PC e garante que o anúncio saiu no lugar certo — você testa a saída com um bipe, e o painel avisa se o padrão do Windows mudou, se a saída está mutada ou em 0%, ou se outro app a tomou (seção "Som da loja" do [guia](docs/GUIA.md)).

### 🎨 Interface
- Layout **Bento Grid** limpo, inspirado no estilo Apple.
- **Tema claro, escuro ou automático** (segue o sistema), salvo por aparelho.
- **Responsivo** do celular ao monitor largo, com navegação por abas no celular, transições suaves e respeito a "reduzir movimento".
- Acessível: navegação por teclado, foco visível, contraste AA e avisos lidos por leitores de tela.

<p align="center">
  <img src="docs/screenshots/celular.png" alt="Proclamare no celular" width="260">
</p>

## 🧠 Como funciona

```mermaid
flowchart LR
    subgraph Rede["Rede local da loja"]
        A["💻 PC do caixa"] -->|navegador| S
        B["📱 Celular do gerente"] -->|navegador| S
    end
    subgraph Loja["PC ligado às caixas de som"]
        S["Proclamare<br/>server.py (Flask + waitress)"]
        S -->|edge-tts / voz do sistema| V["🔊 Caixas de som"]
        T["Aba 'tocador' do YouTube"] --> V
        P["App oficial do Spotify"] --> V
    end
    S <-->|Web API / Spotify Connect| SP["☁️ Spotify"]
    S <-->|relé de comandos| T
```

## 🚀 Começando

**Caminho fácil (Windows):** baixe ou clone o projeto → dê duplo clique em **Instalar Proclamare.bat** → depois é só dar duplo clique no ícone **Proclamare** na Área de Trabalho. Para desligar: Menu Iniciar → *Desligar Proclamare*. Detalhes na seção "Instalar e ligar com duplo clique" do [guia](docs/GUIA.md).

**Pelo terminal (qualquer sistema):**

```bash
git clone https://github.com/arturgabriels779-alt/proclamare.git
cd proclamare
python -m venv venv
venv\Scripts\activate          # Linux/Mac: source venv/bin/activate
pip install -r requirements.txt   # Windows: pip install --require-hashes --only-binary :all: --no-deps -r requirements.lock
python server.py
```

Abra **http://127.0.0.1:8502** no PC da loja. Nos outros aparelhos, use **http://IP-DO-PC-DA-LOJA:8502**.

> O guia completo — conectar o Spotify, configurar o YouTube, IP fixo, firewall, iniciar junto com o Windows/Linux e solução de problemas — está em **[docs/GUIA.md](docs/GUIA.md)**.

## ⚙️ Configuração

| Variável | Padrão | Para quê |
|---|---|---|
| `PROCLAMARE_HOST` | `0.0.0.0` | Endereço de escuta (`127.0.0.1` = só este computador) |
| `PROCLAMARE_PORT` | `8502` | Porta do servidor |
| `PROCLAMARE_LOG_LEVEL` | `INFO` | Nível de detalhe do log |
| `PROCLAMARE_LOG_FILE` | *(vazio = console)* | Arquivo de log (rotativo: 5 MB, 3 antigos) |
| `PROCLAMARE_PID_FILE` | *(vazio)* | Arquivo JSON com o PID do servidor (usado pelo lançador para desligar o processo certo) |
| `PROCLAMARE_CONFIAR_LOOPBACK` | `1` | `0` = nenhuma conexão é "local": todos precisam de PIN (use com proxy/túnel no PC da loja) |
| `PROCLAMARE_SPOTIFY_REDIRECT` | `http://127.0.0.1:8502/spotify/callback` | Redirect URI cadastrado no painel do Spotify |
| `PROCLAMARE_MUSICAS_DIR` | *Músicas do Windows*`\Músicas do Proclamare` | Pasta das Minhas músicas |
| `PROCLAMARE_MUSICAS_ENVIO` | `1` | `0` desliga o envio de músicas pelo painel. Ligado, só o PC da loja e aparelhos com PIN podem mandar corpo grande (até 100 MB por envio); o resto da API aceita no máximo 64 KB |
| `PROCLAMARE_ALLOWED_HOSTS` | *(vazio)* | Nomes de host extras aceitos, separados por vírgula |

## 🛡️ Segurança e desempenho

- **Credenciais só no servidor:** o Client Secret do Spotify fica no `config.json` local (ignorado pelo Git) e nunca é enviado ao navegador; só pode ser alterado a partir do próprio PC da loja.
- **Acesso pela rede com PIN:** aparelhos remotos só controlam o painel após digitar o PIN (hash PBKDF2, sessão por cookie assinado de 14 dias, bloqueio por tentativas). O PC da loja (loopback) não pede PIN; sem PIN o acesso remoto fica desligado. O PIN protege contra curiosos, mas a rede usa `http`: mantenha o Wi-Fi de clientes separado.
- **Proteções web:** Content-Security-Policy rígida, checagem de `Origin`/`Content-Type` contra CSRF, lista de hosts contra DNS rebinding, `state` de uso único no login OAuth e validação de todas as entradas.
- **Leve para ficar ligado o dia todo:** servidor waitress, conexões reaproveitadas com a API do Spotify, cache compartilhado entre os aparelhos, todo o áudio numa única thread dedicada e *long-polling* em vez de consultas a cada segundo.

## 🧰 Tecnologias

**Backend:** Python · Flask · waitress · requests · edge-tts · pyttsx3 · pygame
**Frontend:** HTML, CSS e JavaScript puros (sem framework e sem build) · YouTube IFrame API
**Integrações:** Spotify Web API (Spotify Connect)

## ⚠️ Direitos autorais da música

Contas pessoais de Spotify e YouTube são licenciadas para uso pessoal. Tocar música para clientes
em um estabelecimento comercial normalmente exige licenciamento à parte — no Brasil, pelo **ECAD**.
Consulte antes de usar em operação contínua.
