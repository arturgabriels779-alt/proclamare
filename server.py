"""
Proclamare — servidor local (v2.3 F1: saídas de som; base v2.2.1 e v2.2 com PIN)
=========================================================================
Backend Flask que roda no computador ligado ao amplificador/caixas de som
da loja. Serve a interface (static/) e cuida de três coisas:

  POST /api/announce        — converte texto em voz e TOCA na saída de som
                               deste computador (bloqueia até terminar).
                               Só um anúncio por vez: se já houver um tocando,
                               responde 409 na hora (não enfileira).

  /spotify/* e /api/spotify/* — controla o Spotify do próprio computador da
                               loja via Spotify Connect (o app oficial do
                               Spotify, instalado e logado nesse PC, vira um
                               "dispositivo" controlável pela API). Por isso
                               qualquer computador da rede pode comandar a
                               música sem precisar manter nenhuma aba aberta
                               com login próprio.

  /api/auth/*                — acesso pela rede com PIN (v2.2): status, login,
                               logout, definir/trocar/remover o PIN e revogar
                               sessões. O computador da loja (loopback) nunca
                               pede PIN; sem PIN definido o acesso remoto à API
                               fica desligado.

  /api/youtube/*             — o YouTube não tem um protocolo equivalente ao
                               Spotify Connect, então uma aba precisa ficar
                               aberta TOCANDO no computador da loja; esta
                               rota é só um relé de comandos (tocar, pausar,
                               trocar, volume) para essa aba, para que outros
                               computadores também consigam controlá-la.
                               A aba tocadora recebe os comandos por
                               long-poll (latência quase instantânea).

Contrato v2.3 (F1: saídas de som)
---------------------------------
  Detalhes e motivos: docs/PLANO-SAIDAS-DE-SOM.md. Pacote som/ (modelo, local, motor, vigia).
  * Sem a chave "som" no config.json (ou sem "saida_anuncio") tudo funciona EXATAMENTE
    como na v2.2.1: pygame.mixer.init() padrão, voz offline direta pelo SAPI.
  * Todas as rotas /api/som/* exigem PIN para aparelhos remotos (nenhuma é pública).
    POST: JSON (415) e Origin do mesmo host (403); esquema estrito (chave desconhecida
    em qualquer nível = 400). Erros: {"ok": false, "error", "erro", "codigo"?}.
  * GET  /api/som/estado       tile: motor_ok, saida_anuncio, padrao_windows, alertas, preferencias...
  * GET  /api/som/saidas       lista de saídas (visão com lista branca; sem IDs internos)
  * POST /api/som/descobrir    {"tipos": ["local","spotify"]} -> 202 (varredura em 2º plano)
  * POST /api/som/saida        {"sid", aprovada?, apelido?, confirmada?, esquecer?,
                               usar_para_anuncios?, aceitar_nova_ref?, rack?}. Campos que
                               só valem no PC (BT, HDMI, fone) = 403; troca remota exige
                               /testar da MESMA sessão nos 5 min anteriores (412
                               "teste_necessario"); 409 com anúncio tocando. Auditoria e
                               alerta "saida_alterada_remoto" nas trocas remotas.
  * POST /api/som/testar       {"sid", "abaixar_musica"?} bipe + "Teste do Proclamare: ..."
                               (nunca o nome do sistema). 3/min por IP e 10/10 min no total
                               (429); 409 com anúncio/teste tocando; 503 com "codigo"
                               ausente | em_uso_exclusivo | motor_contaminado.
  * POST /api/som/preferencias merge parcial: se_indisponivel, abaixar.spotify_api/youtube,
                               teste, monitor.ativo/alerta_silencio_min/horario.
  * POST /api/announce         com "saida_anuncio": resolve e ABRE a saída antes de
                               abaixar a música; a música é restaurada sempre; fallback
                               conforme se_indisponivel; 503 com "codigo" sem saída. A
                               resposta ganha saidas_usadas, parcial, sinal_detectado
                               (sessão do próprio processo; null se não deu para medir) e,
                               no fallback, aviso. Voz offline: SAPI -> WAV -> mesmo motor.
  * Vigia de progresso: teto da reprodução min(duração + 3 s, 80 s) < TIMEOUT_AUDIO_S; se o
    executor de áudio for substituído, o motor fica "contaminado" (503 até reiniciar).
  * /api/som/zonas NÃO existe (F3); 409 tarefa_agendada_tocando e "forcar" são F2.

Contrato v2.2.1 (fila do YouTube)
---------------------------------
  * O servidor interpreta o link (parse_youtube_url): vídeo, playlist, índice
    (&index=, 1-based no link -> 0-based) e início (t=/start=: 90, 90s, 1m30s...).
    Mix automático (list=RD... ou start_radio) é ignorado e toca só o vídeo; link
    só de Mix é recusado (400). Link inválido -> 400 em português.
  * POST /api/youtube/command  "load" recebe {"url"} e repassa ao tocador SÓ
    {"video_id","list_id","index","start"} validados (nunca a URL crua). Nova ação
    "play_index" {"index": 0..199} (pula para um item da playlist em reprodução).
  * POST /api/youtube/queue    fila do Proclamare (máx. 50, SÓ em memória: some se o
    servidor reiniciar). {"op": "add","url"} | {"op":"remove","item_id"} |
    {"op":"clear"} | {"op":"move","item_id","to"} | {"op":"play_now","item_id"}
    (tira da fila e manda "load" ao tocador) | {"op":"pop"} (tira e devolve o 1º).
    Item: {id, kind, video_id, list_id, index, start, title, added_at}.
    Erros: 400 (op/link), 404 (item), 409 (fila cheia).
  * POST /api/youtube/state    (aba tocadora) aceita também video_id, list_id,
    playlist_ids (até 200) e playlist_index; valores inválidos -> 400.
  * GET /api/youtube/state     devolve também video_id, list_id, queue,
    queue_version e playlist {index, items:[{video_id,title}]} | null (até 15
    próximas). Títulos vêm do oEmbed público do YouTube, buscados em segundo plano
    (title=null até resolver; cache LRU ~500, falhas ~10 min).
  * Todas as rotas novas ficam atrás do PIN, como o resto de /api/*.

O que mudou na v2.2 (resumo)
----------------------------
  * Autenticação por PIN (6 a 8 dígitos) para aparelhos remotos: hash PBKDF2,
    cookie de sessão assinado (HMAC, 14 dias com renovação), limite de
    tentativas por IP e global (com escalada), revogação de sessões.
  * "Local" = loopback + Host de loopback + sem cabeçalho Via (proxy/túnel no
    PC da loja não vira "local"). PROCLAMARE_CONFIAR_LOOPBACK=0 desliga o "local".
  * CLI sem subir o servidor:  python server.py --definir-pin | --remover-pin
  * Lançador de duplo clique (Proclamare.pyw): log rotativo (PROCLAMARE_LOG_FILE)
    e arquivo de PID (PROCLAMARE_PID_FILE) para desligar o processo certo.
  * Porta de escuta exclusiva (SO_EXCLUSIVEADDRUSE no Windows): um 2º servidor
    na mesma porta falha em vez de roubar conexões.
  * config.json ilegível é preservado (cópia "corrompido") e, no Windows, grava
    com permissões só para o usuário atual, SYSTEM e Administradores.

Da v2.1 (continua valendo)
----------------------------
  * Configuração (config.json) em cache na memória, com escrita atômica.
  * Sessão HTTP única (pool de conexões/keep-alive) para a API do Spotify,
    com timeouts curtos, renovação de token sem corrida e tratamento de 429.
  * Estado do player do Spotify em cache compartilhado (~2 s): N clientes
    abertos geram no máximo ~1 chamada a cada 2 s à API do Spotify.
  * TODO o áudio (pygame + pyttsx3) roda em UMA thread dedicada, com o mixer
    inicializado uma única vez (e reaproveitado).
  * Anúncio: o mp3 é gerado ANTES de abaixar a música (menos tempo abaixada).
  * Relé do YouTube com deque + threading.Condition (long-poll).
  * Servidor de produção (waitress) quando instalado.
  * Cabeçalhos de segurança, lista branca de Host (anti DNS rebinding),
    checagem de Origin/Sec-Fetch-Site/Content-Type (CSRF),
    validação rigorosa de todos os campos e erros genéricos ao cliente.

Variáveis de ambiente (todas opcionais)
---------------------------------------
  (os nomes antigos ANUNCIADOR_* continuam valendo como alternativa; se os dois
   existirem, PROCLAMARE_* tem prioridade)
  PROCLAMARE_HOST        endereço de escuta (padrão 0.0.0.0)
  PROCLAMARE_PORT        porta de escuta (padrão 8502)
  PROCLAMARE_LOG_LEVEL   nível de log: DEBUG, INFO, WARNING... (padrão INFO)
  PROCLAMARE_LOG_FILE    caminho de um arquivo de log (com rotação: 5 MB, 3 antigos).
                         Sem ela, o log vai para o console.
  PROCLAMARE_PID_FILE    caminho de um arquivo JSON onde o servidor registra
                         {"pid", "porta", "criado_em", "executavel"} ao começar a
                         servir (usado pelo lançador para desligar o processo certo);
                         é removido ao encerrar normalmente.
  PROCLAMARE_SPOTIFY_REDIRECT
                         Redirect URI do Spotify (padrão
                         http://127.0.0.1:<porta>/spotify/callback). O Spotify só
                         aceita http para IP de loopback; fora disso use https://.
  PROCLAMARE_ALLOWED_HOSTS
                         nomes de host extras aceitos no cabeçalho Host, separados
                         por vírgula (defesa contra DNS rebinding). IPs literais,
                         localhost e o nome deste computador já são aceitos.
  PROCLAMARE_CONFIAR_LOOPBACK
                         "1" (padrão): conexões de loopback com Host 127.0.0.1/
                         localhost/::1 e sem cabeçalho Via são "locais" (sem PIN).
                         "0": ninguém é local — todos precisam de PIN e a troca do
                         PIN / config do Spotify pela web ficam indisponíveis (use
                         a CLI). OBRIGATÓRIO se houver proxy reverso (Caddy/nginx), túnel
                         (SSH, cloudflared) ou redirecionamento de porta (netsh portproxy,
                         WSL) no PC da loja: sem isso qualquer aparelho se passa pelo PC
                         da loja e ignora o PIN.

Veja docs/GUIA.md para instalação, execução, acesso remoto e PIN.
"""

from __future__ import annotations

import asyncio
import atexit
import base64
import contextlib
import functools
import getpass
import hashlib
import hmac
import ipaddress
import logging
import logging.handlers
import signal
import sys
import os
import re
import secrets
import socket
import tempfile
import threading
import time
import uuid
from collections import OrderedDict, deque
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturoTimeout
from contextlib import contextmanager
from datetime import datetime
from http.cookiejar import DefaultCookiePolicy
from pathlib import Path
from typing import Any, Callable, Iterator
from urllib.parse import parse_qs, urlencode, urlsplit

import json

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from flask import Flask, Response, jsonify, redirect, request, send_from_directory
from werkzeug.exceptions import HTTPException

# Subsistema "Saídas de som" (contrato v2.3). O pacote só importa stdlib na carga;
# pycaw/pygame entram de forma tardia e tolerante dentro dele.
import som as som_pkg
from som import modelo as som_modelo
from som.motor import frase_de_teste, gerar_bipe, texto_para_sapi

log = logging.getLogger("proclamare")


def _env(nome: str, padrao: str = "") -> str:
    """Lê PROCLAMARE_<nome>; se ausente, cai para o nome antigo ANUNCIADOR_<nome>."""
    # "or": variável vazia (comum em .env/systemd) também cai para o nome antigo
    valor = os.environ.get(f"PROCLAMARE_{nome}") or os.environ.get(f"ANUNCIADOR_{nome}")
    return padrao if valor is None else valor

APP_DIR = Path(__file__).resolve().parent
STATIC_DIR = APP_DIR / "static"
AUDIO_DIR = APP_DIR / "audios_temp"
AUDIO_DIR.mkdir(exist_ok=True)
MAX_ARQUIVOS_TEMP = 20
INTERVALO_LIMPEZA_S = 3600.0  # a limpeza de mp3 órfãos é só uma "rede de segurança"
CONFIG_PATH = APP_DIR / "config.json"


_PORTA_INVALIDA = False


def _porta_do_ambiente() -> int:
    """Lê PROCLAMARE_PORT (1..65535); valor inválido volta ao padrão 8502."""
    bruto = _env("PORT", "8502").strip()
    try:
        porta = int(bruto)
    except ValueError:
        porta = 0
    if not 1 <= porta <= 65535:
        global _PORTA_INVALIDA
        _PORTA_INVALIDA = True  # o aviso é emitido em main(), depois de configurar o log
        return 8502
    return porta


PORT = _porta_do_ambiente()

app = Flask(__name__, static_folder=str(STATIC_DIR), static_url_path="/static")
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024  # 16 KB: todos os corpos JSON são minúsculos
app.config["SEND_FILE_MAX_AGE_DEFAULT"] = None  # estáticos: ETag + "no-cache" (revalida com 304)
app.json.sort_keys = False  # não gasta CPU ordenando chaves de cada resposta


# ---------------------------------------------------------------------------
# Erros de API, validação de entrada e cabeçalhos de segurança
# ---------------------------------------------------------------------------
class ErroApi(Exception):
    """Erro de validação/uso: a mensagem (já genérica) vai ao cliente."""

    def __init__(self, mensagem: str, status: int = 400) -> None:
        super().__init__(mensagem)
        self.mensagem = mensagem
        self.status = status


def _resp_erro(mensagem: str, status: int) -> tuple[Response, int]:
    return jsonify({"ok": False, "error": mensagem}), status


@app.errorhandler(ErroApi)
def _tratar_erro_api(e: ErroApi) -> tuple[Response, int]:
    return _resp_erro(e.mensagem, e.status)


@app.errorhandler(HTTPException)
def _tratar_http(e: HTTPException) -> Any:
    # Redirecionamentos (ex.: barra final) são "exceções" com código < 400.
    if e.code is None or e.code < 400:
        return e
    return _resp_erro("Requisição inválida." if e.code < 500 else "Erro interno.", e.code)


@app.errorhandler(Exception)
def _tratar_excecao(e: Exception) -> tuple[Response, int]:
    # Detalhe e stack trace só no log do servidor; o cliente recebe algo genérico.
    # %r: o path vem do cliente; repr escapa quebras de linha (evita linha forjada no log).
    log.exception("Erro não tratado em %r %r", request.method, request.path)
    return _resp_erro("Erro interno do servidor.", 500)


def corpo_json() -> dict:
    """Devolve o corpo JSON da requisição, que obrigatoriamente é um objeto."""
    dados = request.get_json(silent=True)
    if not isinstance(dados, dict):
        raise ErroApi("Corpo JSON inválido.")
    return dados


def validar_int(valor: Any, nome: str, minimo: int, maximo: int, *, limitar: bool = False) -> int:
    """
    Converte `valor` em int de forma segura (sem exceções soltas -> 500).
    Aceita int, float inteiro ou string de dígitos; bool é rejeitado.
    Fora do intervalo: limita (limitar=True) ou responde 400.
    """
    if isinstance(valor, bool):
        raise ErroApi(f"Campo '{nome}' inválido.")
    if isinstance(valor, int):
        numero = valor
    elif isinstance(valor, float) and valor.is_integer():
        numero = int(valor)
    elif isinstance(valor, str) and re.fullmatch(r"-?\d{1,18}", valor.strip()):
        numero = int(valor.strip())
    else:
        raise ErroApi(f"Campo '{nome}' inválido.")
    if limitar:
        return max(minimo, min(maximo, numero))
    if not minimo <= numero <= maximo:
        raise ErroApi(f"Campo '{nome}' fora do intervalo permitido.")
    return numero


def validar_str(valor: Any, nome: str, max_len: int, *, min_len: int = 0, strip: bool = True) -> str:
    """Garante que `valor` é string e respeita o tamanho (após strip, se pedido)."""
    if not isinstance(valor, str):
        raise ErroApi(f"Campo '{nome}' inválido.")
    if strip:
        valor = valor.strip()
    if not min_len <= len(valor) <= max_len:
        raise ErroApi(f"Campo '{nome}' com tamanho inválido.")
    return valor


CSP = (
    "default-src 'self'; "
    "script-src 'self' https://www.youtube.com https://s.ytimg.com; "
    "frame-src https://www.youtube.com https://www.youtube-nocookie.com; "
    "style-src 'self' https://fonts.googleapis.com; "
    "font-src https://fonts.gstatic.com; "
    "img-src 'self' data: https://i.scdn.co https://*.scdn.co "
    "https://*.spotifycdn.com https://i.ytimg.com; "
    "connect-src 'self'; base-uri 'self'; form-action 'self'; "
    "frame-ancestors 'self'; object-src 'none'"
)
CABECALHOS_SEGURANCA = {
    "X-Content-Type-Options": "nosniff",
    # Com "same-origin" o iframe do YouTube (cross-origin) não recebe Referer e
    # o player mostra o erro 153; esta política envia só a ORIGEM (sem path/query).
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "X-Frame-Options": "SAMEORIGIN",
    "Content-Security-Policy": CSP,
}
METODOS_ESCRITA = frozenset({"POST", "PUT", "PATCH", "DELETE"})


# --- Lista branca de Host (defesa contra DNS rebinding) -----------------------
# O navegador envia "Host: <nome digitado>". Num ataque de rebinding o nome é do
# atacante (evil.com) mas resolve para o IP da loja; Origin e Host passam a bater,
# então comparar os dois NÃO basta. Aceitamos: IPs literais (rebinding sempre usa
# um nome), localhost, o nome deste computador e extras via variável de ambiente.
def _hosts_extras_do_ambiente() -> frozenset[str]:
    bruto = _env("ALLOWED_HOSTS", "")
    return frozenset(h.strip().lower() for h in bruto.split(",") if h.strip())


def _montar_hosts_permitidos() -> frozenset[str]:
    nomes = {"localhost"} | set(_hosts_extras_do_ambiente())
    with contextlib.suppress(OSError):
        nomes.add(socket.gethostname().lower())
    return frozenset(nomes)


HOSTS_PERMITIDOS = _montar_hosts_permitidos()


def host_permitido(host_header: str) -> bool:
    """True se o cabeçalho Host (com ou sem porta) é um nome/IP aceito."""
    try:
        nome = urlsplit("//" + host_header).hostname  # remove porta e colchetes de IPv6
    except ValueError:
        return False
    if not nome:
        return False
    if nome in HOSTS_PERMITIDOS:
        return True
    try:
        ipaddress.ip_address(nome.split("%", 1)[0])  # IP literal (v4 ou v6)
        return True
    except ValueError:
        return False


# --- Limite de taxa simples por IP (janela deslizante, em memória) -------------
_taxa_lock = threading.Lock()
_taxa_hist: dict[str, deque] = {}
_taxa_janela: dict[str, float] = {}      # chave -> janela (s) usada por ela
_TAXA_MAX_CHAVES = 512
_TAXA_GLOBAIS = frozenset({"testar:global"})   # tetos da loja toda: nunca são descartados


def excedeu_limite(chave: str, maximo: int, janela_s: float) -> bool:
    """Registra uma ocorrência de `chave`; True se passou de `maximo` na janela."""
    agora = time.monotonic()
    with _taxa_lock:
        if len(_taxa_hist) >= _TAXA_MAX_CHAVES:  # evita crescer sem limite
            # Limpa pela janela de CADA chave (não pela da chamada atual) e nunca as globais.
            for k in [k for k, h in _taxa_hist.items()
                      if k not in _TAXA_GLOBAIS
                      and (not h or agora - h[-1] > _taxa_janela.get(k, janela_s))]:
                del _taxa_hist[k]
                _taxa_janela.pop(k, None)
            if len(_taxa_hist) >= _TAXA_MAX_CHAVES:
                # ainda cheio (inundação por muitos IPs): descarta as mais antigas, sem clear() geral
                antigas = sorted((k for k in _taxa_hist if k not in _TAXA_GLOBAIS),
                                 key=lambda k: _taxa_hist[k][-1] if _taxa_hist[k] else 0.0)
                for k in antigas[: _TAXA_MAX_CHAVES // 4]:
                    del _taxa_hist[k]
                    _taxa_janela.pop(k, None)
        hist = _taxa_hist.setdefault(chave, deque())
        _taxa_janela[chave] = max(janela_s, _taxa_janela.get(chave, 0.0))
        while hist and agora - hist[0] > janela_s:
            hist.popleft()
        if len(hist) >= maximo:
            return True
        hist.append(agora)
        return False


HOSTS_LOOPBACK = frozenset({"127.0.0.1", "localhost", "::1"})


def _confiar_loopback() -> bool:
    """PROCLAMARE_CONFIAR_LOOPBACK (padrão "1"); "0" = nenhuma requisição é local."""
    return _env("CONFIAR_LOOPBACK", "1").strip() != "0"


def _requisicao_local() -> bool:
    """
    True só se a requisição vem MESMO do próprio computador: endereço de origem
    em loopback E cabeçalho Host de loopback E sem cabeçalho Via. Um proxy, túnel
    ou "netsh portproxy" no PC da loja faz todo mundo chegar por loopback, mas
    normalmente com Host de rede/nome ou Via; sem isso o PIN seria contornado.
    """
    if not _confiar_loopback():
        return False
    try:
        if not ipaddress.ip_address(request.remote_addr or "").is_loopback:
            return False
        nome = urlsplit("//" + request.host).hostname
    except ValueError:
        return False
    return (nome or "").lower() in HOSTS_LOOPBACK and "Via" not in request.headers


@app.before_request
def _verificar_requisicao() -> tuple[Response, int] | None:
    """
    Defesas baratas, nesta ordem: Host na lista branca (anti DNS rebinding);
    Origin igual ao host (CSRF); Sec-Fetch-Site cross-site bloqueado em /api/* e
    /spotify/login (GET "no-cors" não envia Origin); POST /api/* só JSON.
    """
    if not host_permitido(request.host):
        return _resp_erro("Host não permitido.", 421)
    origin = request.headers.get("Origin")
    if origin is not None:
        try:
            origem_ok = urlsplit(origin).netloc.lower() == request.host.lower()
        except ValueError:
            origem_ok = False
        if not origem_ok:
            return _resp_erro("Origem não permitida.", 403)
    if request.headers.get("Sec-Fetch-Site") == "cross-site" and (
        request.path.startswith("/api/") or request.path == "/spotify/login"
    ):
        return _resp_erro("Requisição entre sites não permitida.", 403)
    if (
        request.method in METODOS_ESCRITA
        and request.path.startswith("/api/")
        and request.mimetype != "application/json"
    ):
        return _resp_erro("Content-Type deve ser application/json.", 415)
    # v2.2: depois de todas as checagens acima, /api/* (exceto as públicas)
    # exige PIN quando o cliente não está no próprio computador.
    return _exigir_sessao()


@app.after_request
def _aplicar_cabecalhos(resp: Response) -> Response:
    """Cabeçalhos de segurança em toda resposta; cache correto por tipo de rota."""
    for chave, valor in CABECALHOS_SEGURANCA.items():
        resp.headers[chave] = valor
    if request.path.startswith("/api/"):
        resp.headers["Cache-Control"] = "no-store"
    elif request.endpoint in ("static", "home"):
        # ETag + no-cache: o navegador revalida e recebe 304 (barato) se não mudou.
        resp.headers["Cache-Control"] = "no-cache"
    return resp


# ---------------------------------------------------------------------------
# Configuração persistida (client id/secret do Spotify, tokens, dispositivo)
# Cache em memória + escrita atômica (arquivo temporário + os.replace).
# ---------------------------------------------------------------------------
_config_lock = threading.RLock()
_config_cache: dict = {}
_config_assinatura: tuple[int, int] | None = None  # (mtime_ns, tamanho) do arquivo em cache


def _assinatura_arquivo() -> tuple[int, int] | None:
    try:
        st = CONFIG_PATH.stat()
    except OSError:
        return None
    return (st.st_mtime_ns, st.st_size)


_corrompidos_copiados: set[tuple[int, int]] = set()  # (hash do conteúdo, tamanho)


def _preservar_config_corrompido() -> None:
    """
    Se o config.json existe mas não é um JSON-objeto válido, copia-o UMA vez para
    config.json.corrompido-<timestamp> (antes de qualquer gravação por cima).
    """
    if _assinatura_arquivo() is None:
        return
    try:
        dados = CONFIG_PATH.read_bytes()
        # chave inclui o conteúdo: duas corrupções seguidas de mesmo tamanho/mtime não se confundem
        assinatura = (hash(dados), len(dados))
        if assinatura in _corrompidos_copiados:
            return
        try:
            if isinstance(json.loads(dados.decode("utf-8")), dict):
                return  # está íntegro
        except ValueError:
            pass
        destino = CONFIG_PATH.with_name(f"config.json.corrompido-{time.strftime('%Y%m%d-%H%M%S')}")
        n = 1
        while destino.exists():  # nunca sobrescreve uma cópia anterior
            destino = destino.with_name(f"{destino.name.split('.corrompido-')[0]}.corrompido-"
                                        f"{time.strftime('%Y%m%d-%H%M%S')}-{n}")
            n += 1
        destino.write_bytes(dados)
        with contextlib.suppress(OSError):
            os.chmod(destino, 0o600)
        _restringir_acl_windows(destino)
        _corrompidos_copiados.add(assinatura)
        log.error("config.json corrompido: cópia guardada em %s.", destino.name)
    except OSError as exc:
        log.error("Não foi possível copiar o config.json corrompido (%s).", type(exc).__name__)


def _restringir_acl_windows(caminho: Path) -> None:
    """
    (Windows, best-effort) Deixa o arquivo acessível só ao usuário atual, ao SYSTEM
    e aos Administradores, sem herdar permissões da pasta. Falhas só são logadas.
    """
    if os.name != "nt":
        return
    try:
        import ntsecuritycon as con  # type: ignore[import-not-found]
        import win32api  # type: ignore[import-not-found]
        import win32security  # type: ignore[import-not-found]

        token = win32security.OpenProcessToken(win32api.GetCurrentProcess(), con.TOKEN_QUERY)
        sid_usuario = win32security.GetTokenInformation(token, win32security.TokenUser)[0]
        dacl = win32security.ACL()
        for sid in (
            sid_usuario,
            win32security.ConvertStringSidToSid("S-1-5-18"),      # SYSTEM
            win32security.ConvertStringSidToSid("S-1-5-32-544"),  # Administradores
        ):
            dacl.AddAccessAllowedAce(win32security.ACL_REVISION, con.FILE_ALL_ACCESS, sid)
        win32security.SetNamedSecurityInfo(
            str(caminho), win32security.SE_FILE_OBJECT,
            win32security.DACL_SECURITY_INFORMATION | win32security.PROTECTED_DACL_SECURITY_INFORMATION,
            None, None, dacl, None,
        )
    except Exception as exc:  # noqa: BLE001  (pywin32 ausente, sem permissão...)
        log.warning("Não foi possível restringir as permissões de %s (%s).", caminho.name, type(exc).__name__)


def load_config() -> dict:
    """
    Devolve uma CÓPIA da configuração. Só relê o disco se o arquivo mudou
    (um os.stat por chamada é muito mais barato que ler + parsear o JSON).
    """
    global _config_cache, _config_assinatura
    with _config_lock:
        assinatura = _assinatura_arquivo()
        if assinatura != _config_assinatura:
            novo: dict = {}
            if assinatura is not None:
                try:
                    lido = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
                    if not isinstance(lido, dict):
                        raise ValueError("config.json não é um objeto")
                    novo = lido
                except (ValueError, OSError):  # JSONDecodeError/UnicodeDecodeError são ValueError
                    # Arquivo ilegível: guarda uma cópia (uma vez), registra ERROR e
                    # MANTÉM o último dicionário válido em memória (não zera segredos).
                    _preservar_config_corrompido()
                    log.error(
                        "config.json ilegível; mantendo a última configuração válida em memória. "
                        "Uma cópia do arquivo foi guardada como config.json.corrompido-*."
                    )
                    _config_assinatura = assinatura
                    return dict(_config_cache)
            _config_cache = novo
            _config_assinatura = assinatura
        return dict(_config_cache)


def _gravar_config_atomico(cfg: dict) -> None:
    """Grava em arquivo temporário (0600) na mesma pasta e troca com os.replace."""
    global _config_cache, _config_assinatura
    _preservar_config_corrompido()  # nunca sobrescreve um arquivo ilegível sem guardar cópia
    fd, tmp_nome = tempfile.mkstemp(dir=str(CONFIG_PATH.parent), prefix=".config.", suffix=".tmp")
    try:
        # DACL restrita ANTES de escrever os dados; o os.replace mantém a DACL do temporário.
        _restringir_acl_windows(Path(tmp_nome))
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        # No Windows o replace pode falhar por instantes (antivírus/indexador).
        for tentativa in range(5):
            try:
                os.replace(tmp_nome, CONFIG_PATH)
                break
            except PermissionError:
                if tentativa == 4:
                    raise
                time.sleep(0.05)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp_nome)
        raise
    with contextlib.suppress(OSError):  # no Windows o chmod é quase um no-op
        os.chmod(CONFIG_PATH, 0o600)
    _config_cache = dict(cfg)
    _config_assinatura = _assinatura_arquivo()


def save_config(cfg: dict) -> None:
    with _config_lock:
        _gravar_config_atomico(cfg)


def update_config(**changes: Any) -> dict:
    """Aplica alterações e grava só se algo realmente mudou."""
    with _config_lock:
        cfg = load_config()
        if any(cfg.get(k) != v for k, v in changes.items()):
            cfg.update(changes)
            _gravar_config_atomico(cfg)
        return cfg


# ---------------------------------------------------------------------------
# Autenticação por PIN (contrato v2.2)
# O computador da loja (local: veja _requisicao_local) NUNCA pede PIN. Aparelhos
# remotos precisam de uma sessão (cookie assinado com HMAC) obtida digitando o
# PIN. Sem PIN configurado, o acesso remoto à API fica desligado.
# config.json guarda: acesso_pin_hash, acesso_chave (segredo HMAC) e
# acesso_versao (incrementa ao trocar/remover o PIN ou ao revogar; derruba todas
# as sessões). Esses campos NUNCA são devolvidos em respostas nem vão para o log.
# ---------------------------------------------------------------------------
COOKIE_SESSAO = "proclamare_sessao"
SESSAO_DURACAO_S = 14 * 24 * 3600  # 14 dias
SESSAO_TETO_S = 90 * 24 * 3600  # teto absoluto desde o login (a renovação não passa disso)
SESSAO_RENOVAR_ABAIXO_S = 7 * 24 * 3600  # renovação deslizante: reemite se faltar menos que isto
PBKDF2_ITERACOES = 200_000
RE_PIN = re.compile(r"[0-9]{6,8}")
MSG_PIN_FORMATO = "PIN inválido: use de 6 a 8 números."
# Rotas /api/ abertas mesmo para aparelhos remotos sem sessão. /api/auth/pin e
# /api/auth/revogar ficam aqui só para o remoto receber o 403 "somente local" da
# própria rota (e não um 401 que sugira que o PIN resolveria).
API_PUBLICAS = frozenset({
    "/api/status", "/api/auth/status", "/api/auth/login", "/api/auth/logout",
    "/api/auth/pin", "/api/auth/revogar",
})

# Limite de tentativas de login (em memória, com limpeza)
LOGIN_IP_MAX_ERROS = 5
LOGIN_IP_BLOQUEIO_S = 300.0
LOGIN_GLOBAL_MAX_ERROS = 20
LOGIN_GLOBAL_JANELA_S = 600.0  # janela de contagem e 1º bloqueio; dobra a cada bloqueio seguido
LOGIN_GLOBAL_BLOQUEIO_MAX_S = 24 * 3600.0
LOGIN_IP_MAX_CHAVES = 1024
FALHAS_JANELA_S = 24 * 3600.0
_login_lock = threading.Lock()
_login_ips: dict[str, dict[str, Any]] = {}  # ip -> {"t": deque[monotonic], "bloq": monotonic}
_login_global: deque = deque()
_login_global_bloq = 0.0  # monotonic até quando o login remoto está bloqueado para todos
_login_global_nivel = 0  # nível de escalada do bloqueio global (define a duração)
_login_global_fim = 0.0  # monotonic do fim do último bloqueio global (base do decaimento)
LOGIN_GLOBAL_DECAI_S = 24 * 3600.0  # desce um nível a cada período sem bloqueio global
_login_falhas: deque = deque(maxlen=5000)  # time.time() de cada PIN errado (remoto), p/ o painel
_versao_vista: int | None = None


def _b64u(dados: bytes) -> str:
    return base64.urlsafe_b64encode(dados).decode("ascii").rstrip("=")


def _b64u_dec(texto: str) -> bytes:
    return base64.urlsafe_b64decode(texto + "=" * (-len(texto) % 4))


def _hash_pin(pin: str, salt: bytes, iteracoes: int = PBKDF2_ITERACOES) -> bytes:
    return hashlib.pbkdf2_hmac("sha256", pin.encode("utf-8"), salt, iteracoes)


def _novo_hash_pin(pin: str) -> str:
    salt = secrets.token_bytes(16)
    return f"pbkdf2_sha256${PBKDF2_ITERACOES}${_b64u(salt)}${_b64u(_hash_pin(pin, salt))}"


def _ler_hash_pin(armazenado: Any) -> tuple[int, bytes, bytes] | None:
    """Decodifica 'pbkdf2_sha256$it$salt$hash'; None se ausente/malformado."""
    if not isinstance(armazenado, str):
        return None
    partes = armazenado.split("$")
    if len(partes) != 4 or partes[0] != "pbkdf2_sha256":
        return None
    try:
        it = int(partes[1])
        salt, esperado = _b64u_dec(partes[2]), _b64u_dec(partes[3])
    except (ValueError, TypeError):
        return None
    if not 1 <= it <= 5_000_000 or not salt or not esperado:
        return None
    return it, salt, esperado


def _chave_acesso(cfg: dict) -> bytes | None:
    try:
        chave = _b64u_dec(cfg.get("acesso_chave", ""))
    except (ValueError, TypeError):
        return None
    return chave if len(chave) >= 16 else None


def pin_configurado(cfg: dict | None = None) -> bool:
    cfg = load_config() if cfg is None else cfg
    return _ler_hash_pin(cfg.get("acesso_pin_hash")) is not None and _chave_acesso(cfg) is not None


def _versao_acesso(cfg: dict) -> int:
    v = cfg.get("acesso_versao", 0)
    return v if isinstance(v, int) and not isinstance(v, bool) else 0


def _assinar(chave: bytes, parte: str) -> str:
    return _b64u(hmac.new(chave, parte.encode("ascii"), hashlib.sha256).digest())


def _emitir_token(cfg: dict, iat: float | None = None) -> tuple[str, int]:
    """
    Emite o token e devolve (token, segundos_de_vida). `iat` = momento do login
    original (mantido nas renovações); exp = min(agora + 14 dias, iat + 90 dias).
    """
    chave = _chave_acesso(cfg)
    assert chave is not None
    agora = int(time.time())
    iat_i = agora if iat is None else int(iat)
    exp = min(agora + SESSAO_DURACAO_S, iat_i + SESSAO_TETO_S)
    carga = json.dumps({"exp": exp, "iat": iat_i, "v": _versao_acesso(cfg)}, separators=(",", ":"))
    parte = _b64u(carga.encode("utf-8"))
    return parte + "." + _assinar(chave, parte), max(0, exp - agora)


def _expiracao_token(token: str | None, cfg: dict) -> float | None:
    carga = _carga_token(token, cfg)
    return None if carga is None else carga[0]


def _carga_token(token: str | None, cfg: dict) -> tuple[float, float] | None:
    """
    Confere formato, assinatura (tempo constante), expiração, versão e o teto de
    90 dias; devolve (exp, iat). Tokens sem `iat` são inválidos.
    """
    if not token or len(token) > 400 or not pin_configurado(cfg):
        return None
    chave = _chave_acesso(cfg)
    partes = token.split(".")
    if chave is None or len(partes) != 2:
        return None
    try:
        if not hmac.compare_digest(partes[1].encode("ascii"), _assinar(chave, partes[0]).encode("ascii")):
            return None
        carga = json.loads(_b64u_dec(partes[0]))
    except (ValueError, TypeError, UnicodeError):
        return None
    if not isinstance(carga, dict):
        return None
    exp, iat, v = carga.get("exp"), carga.get("iat"), carga.get("v")
    for n in (exp, iat):
        if isinstance(n, bool) or not isinstance(n, (int, float)):
            return None
    agora = time.time()
    if agora >= exp or agora >= iat + SESSAO_TETO_S or iat > agora + 300:
        return None
    if not (isinstance(v, int) and not isinstance(v, bool) and v == _versao_acesso(cfg)):
        return None
    return float(exp), float(iat)


def _token_valido_sessao(token: str | None, cfg: dict) -> bool:
    return _expiracao_token(token, cfg) is not None


def _sessao_ok(cfg: dict | None = None) -> bool:
    """True se o cliente é local OU traz um cookie de sessão válido."""
    if _requisicao_local():
        return True
    cfg = load_config() if cfg is None else cfg
    return _token_valido_sessao(request.cookies.get(COOKIE_SESSAO), cfg)


def _exigir_sessao() -> tuple[Response, int] | None:
    """Chamado no before_request: bloqueia /api/* protegidas para remotos sem sessão."""
    if not request.path.startswith("/api/") or request.path in API_PUBLICAS:
        return None
    if _requisicao_local():
        return None
    cfg = load_config()
    if _token_valido_sessao(request.cookies.get(COOKIE_SESSAO), cfg):
        return None
    return _resp_sem_sessao(cfg)


def _resp_sem_sessao(cfg: dict) -> tuple[Response, int]:
    if not pin_configurado(cfg):
        return jsonify({
            "ok": False, "auth": "pin_nao_configurado",
            "error": "Acesso pela rede desativado. Defina um PIN no computador da loja.",
        }), 403
    return jsonify({"ok": False, "auth": "pin_requerido", "error": "Digite o PIN para continuar."}), 401


def _definir_cookie_sessao(resp: Response, token: str, max_age: int = SESSAO_DURACAO_S) -> None:
    resp.set_cookie(
        COOKIE_SESSAO, token, max_age=max_age,
        httponly=True, samesite="Strict", path="/",
    )


@app.after_request
def _renovar_sessao(resp: Response) -> Response:
    """
    Renovação deslizante: numa requisição /api/ remota com sessão válida, se faltar
    menos de 7 dias para expirar, reemite o cookie (mesmos atributos) por mais 14 dias.
    """
    if (
        request.path.startswith("/api/")
        and request.path not in ("/api/auth/login", "/api/auth/logout")
        and resp.status_code < 400
        and not _requisicao_local()
    ):
        cfg = load_config()
        carga = _carga_token(request.cookies.get(COOKIE_SESSAO), cfg)
        if carga is not None and carga[0] - time.time() < SESSAO_RENOVAR_ABAIXO_S:
            novo, vida = _emitir_token(cfg, iat=carga[1])
            if vida > carga[0] - time.time() + 60:  # no teto de 90 dias não há o que renovar
                _definir_cookie_sessao(resp, novo, vida)
    return resp


# --- PIN: validação e gravação (usadas pela API e pela CLI) -----------------------
# Sequências de teclado/padrões muito comuns (além das regras gerais de _pin_trivial)
PINS_TECLADO = frozenset({
    "112233", "147258", "159753", "102030", "246810", "000123", "258369", "147369",
    "741852", "852963", "963852", "789456", "321654", "135790",
})


def _pin_trivial(pin: str) -> bool:
    """
    PIN fácil de adivinhar: todos iguais; sequência crescente/decrescente (com
    volta 9->0, ex.: 123456, 654321, 789012); repetição de bloco de 1 a 3 dígitos
    (121212, 123123, 112112); espelho/palíndromo (123321, 112211).
    """
    if len(set(pin)) == 1:
        return True
    passos = {(int(b) - int(a)) % 10 for a, b in zip(pin, pin[1:])}
    if passos == {1} or passos == {9}:
        return True
    for k in (1, 2, 3):
        if k < len(pin) and pin == (pin[:k] * len(pin))[:len(pin)]:
            return True
    if pin == pin[::-1] or pin in PINS_TECLADO:
        return True
    # Grupos de 2 ou 3 posições em progressão: 112233 / 222333 (um dígito repetido por
    # grupo, em passo fixo) e 102030 / 121416 (números de 2 ou 3 dígitos em passo fixo).
    for k in (2, 3):
        if len(pin) % k or len(pin) // k < 3:
            continue
        grupos = [pin[i:i + k] for i in range(0, len(pin), k)]
        if all(len(set(g)) == 1 for g in grupos):
            nums = [int(g[0]) for g in grupos]
            if len({(b - a) % 10 for a, b in zip(nums, nums[1:])}) == 1:
                return True
        nums = [int(g) for g in grupos]
        if nums[1] != nums[0] and len({b - a for a, b in zip(nums, nums[1:])}) == 1:
            return True
    return False


def validar_pin_novo(pin: Any) -> str:
    """Valida o formato e rejeita PINs triviais (ErroApi com mensagem amigável)."""
    if not isinstance(pin, str) or not RE_PIN.fullmatch(pin):
        raise ErroApi(MSG_PIN_FORMATO)
    if _pin_trivial(pin):
        raise ErroApi(
            "PIN fácil demais de adivinhar. Evite números repetidos (111111), "
            "sequências (123456), blocos repetidos (121212) e espelhos (123321)."
        )
    return pin


def aplicar_pin(pin: str | None) -> bool:
    """
    Define/troca (str já validada) ou remove (None) o PIN: nova chave HMAC e
    acesso_versao + 1 (desconecta todos os aparelhos). Devolve pin_configurado.
    """
    novo_hash = _novo_hash_pin(pin) if pin is not None else None  # custo fora do lock
    with _config_lock:
        cfg = load_config()
        if novo_hash is None:
            cfg.pop("acesso_pin_hash", None)
            cfg.pop("acesso_chave", None)
        else:
            cfg["acesso_pin_hash"] = novo_hash
            cfg["acesso_chave"] = _b64u(secrets.token_bytes(32))
        cfg["acesso_versao"] = _versao_acesso(cfg) + 1
        _gravar_config_atomico(cfg)
        configurado = pin_configurado(cfg)
    _login_resetar_global()
    return configurado


# --- Limite de tentativas ---------------------------------------------------------
def _login_resetar_global() -> None:
    """
    Zera TODOS os bloqueios e contadores de login (global, escalada e por IP) em
    troca/remoção do PIN, revogação local ou mudança de acesso_versao por fora.
    """
    global _login_global_bloq, _login_global_nivel, _login_global_fim
    with _login_lock:
        _login_ips.clear()
        _login_global.clear()
        _login_global_bloq = 0.0
        _login_global_nivel = 0
        _login_global_fim = 0.0


def _sincronizar_versao(cfg: dict) -> None:
    """Se a versão de acesso mudou por fora (CLI), zera o bloqueio global."""
    global _versao_vista
    v = _versao_acesso(cfg)
    if _versao_vista is not None and v != _versao_vista:
        _login_resetar_global()
    _versao_vista = v


def _login_limpar(agora: float) -> None:
    """(com _login_lock) Descarta registros vencidos; limita o tamanho do dicionário."""
    while _login_global and agora - _login_global[0] > LOGIN_GLOBAL_JANELA_S:
        _login_global.popleft()
    for ip in list(_login_ips):
        reg = _login_ips[ip]
        t = reg["t"]
        while t and agora - t[0] > LOGIN_IP_BLOQUEIO_S:
            t.popleft()
        if not t and agora >= reg["bloq"]:
            del _login_ips[ip]
    while len(_login_ips) >= LOGIN_IP_MAX_CHAVES:  # só sob ataque de muitos IPs
        del _login_ips[min(_login_ips, key=lambda k: _login_ips[k]["bloq"])]
    while _login_falhas and time.time() - _login_falhas[0] > FALHAS_JANELA_S:
        _login_falhas.popleft()


def _segundos(restante: float) -> int:
    return max(1, int(restante + 0.999))


def _login_bloquear_global(agora: float) -> int:
    """(com _login_lock) Inicia o bloqueio global com duração escalonada; devolve os segundos."""
    global _login_global_bloq, _login_global_nivel, _login_global_fim
    if _login_global_nivel > 0 and _login_global_fim:  # decaimento: -1 nível por 24 h sem bloqueio
        decaido = int((agora - _login_global_fim) // LOGIN_GLOBAL_DECAI_S)
        _login_global_nivel = max(0, _login_global_nivel - max(0, decaido))
    duracao = min(LOGIN_GLOBAL_JANELA_S * (2 ** _login_global_nivel), LOGIN_GLOBAL_BLOQUEIO_MAX_S)
    _login_global_nivel += 1
    _login_global_bloq = agora + duracao
    _login_global_fim = _login_global_bloq
    _login_global.clear()
    log.warning("Muitos PINs errados vindos da rede: login remoto bloqueado para "
                "todos por %d min (bloqueio global nº %d).", int(duracao // 60), _login_global_nivel)
    return int(duracao)


def _login_reservar(ip: str) -> tuple[bool, int]:
    """
    Registra uma tentativa ANTES de verificar o PIN (uma rajada paralela não passa
    do limite). Devolve (permitida, segundos_bloqueado). Tentativa já bloqueada
    não é contada nem logada (sem inundar o log).
    """
    agora = time.monotonic()
    with _login_lock:
        _login_limpar(agora)
        if agora < _login_global_bloq:
            return False, _segundos(_login_global_bloq - agora)
        if len(_login_global) >= LOGIN_GLOBAL_MAX_ERROS:
            # Limite global já esgotado (inclui tentativas ainda em verificação):
            # nega ANTES do PBKDF2, sem depender de _login_falhou.
            return False, _login_bloquear_global(agora)
        reg = _login_ips.setdefault(ip, {"t": deque(), "bloq": 0.0})
        if agora < reg["bloq"]:
            return False, _segundos(reg["bloq"] - agora)
        if len(reg["t"]) >= LOGIN_IP_MAX_ERROS:  # tentativas pendentes já esgotaram o limite
            return False, _segundos(LOGIN_IP_BLOQUEIO_S)
        reg["t"].append(agora)
        _login_global.append(agora)
        return True, 0


def _login_falhou(ip: str) -> tuple[int, int]:
    """
    PIN errado. Devolve (segundos_de_bloqueio, tentativas_restantes). Se ESTA
    tentativa dispara o bloqueio (por IP ou global), os segundos vêm > 0 e o
    cliente recebe 429 já agora. Loga uma vez por bloqueio, nunca por requisição.
    """
    agora = time.monotonic()
    bloqueado = 0
    with _login_lock:
        _login_falhas.append(time.time())
        reg = _login_ips.setdefault(ip, {"t": deque(), "bloq": 0.0})
        restantes = max(0, LOGIN_IP_MAX_ERROS - len(reg["t"]))
        if len(reg["t"]) >= LOGIN_IP_MAX_ERROS and agora >= reg["bloq"]:
            reg["bloq"] = agora + LOGIN_IP_BLOQUEIO_S
            reg["t"].clear()  # a contagem recomeça depois do bloqueio
            bloqueado = int(LOGIN_IP_BLOQUEIO_S)
            log.warning("Login remoto bloqueado por %d min: muitos PINs errados (IP %s).",
                        int(LOGIN_IP_BLOQUEIO_S // 60), ip)
        if len(_login_global) >= LOGIN_GLOBAL_MAX_ERROS and agora >= _login_global_bloq:
            bloqueado = max(bloqueado, _login_bloquear_global(agora))
    return bloqueado, restantes


def _login_sucesso(ip: str) -> None:
    """
    Acerto remoto: zera o contador DAQUELE IP e devolve a tentativa reservada ao
    contador global. NÃO zera a escalada global (só ação local ou decaimento).
    """
    with _login_lock:
        _login_ips.pop(ip, None)
        if _login_global:
            _login_global.pop()


def _apagar_cookie(resp: Response) -> Response:
    resp.set_cookie(COOKIE_SESSAO, "", max_age=0, expires=0, httponly=True, samesite="Strict", path="/")
    return resp


def _resp_429(segundos: int) -> tuple[Response, int]:
    minutos = max(1, -(-segundos // 60))
    return jsonify({
        "ok": False, "bloqueado_por_s": segundos,
        "error": f"Muitas tentativas. Tente novamente em {minutos} min.",
    }), 429


@app.get("/api/auth/status")
def auth_status() -> Response:
    cfg = load_config()
    _sincronizar_versao(cfg)
    local = _requisicao_local()
    resp: dict[str, Any] = {
        "local": local,
        "pin_configurado": pin_configurado(cfg),
        "autenticado": local or _token_valido_sessao(request.cookies.get(COOKIE_SESSAO), cfg),
    }
    if local:  # só o PC da loja vê sinais de ataque
        agora = time.monotonic()
        with _login_lock:
            _login_limpar(agora)
            resp["falhas_recentes"] = len(_login_falhas)
            resp["bloqueio_global_s"] = _segundos(_login_global_bloq - agora) if agora < _login_global_bloq else 0
    return jsonify(resp)


@app.post("/api/auth/login")
def auth_login() -> Any:
    pin = corpo_json().get("pin")
    if not isinstance(pin, str) or not RE_PIN.fullmatch(pin):
        raise ErroApi(MSG_PIN_FORMATO)
    cfg = load_config()
    _sincronizar_versao(cfg)
    if not pin_configurado(cfg):
        return _resp_sem_sessao(cfg)
    ip = request.remote_addr or "?"
    local = _requisicao_local()
    restantes = LOGIN_IP_MAX_ERROS
    if not local:  # o computador da loja não precisa de PIN: sem limite de tentativas
        permitida, bloqueado_s = _login_reservar(ip)
        if not permitida:
            return _resp_429(bloqueado_s)
    it, salt, esperado = _ler_hash_pin(cfg["acesso_pin_hash"])  # type: ignore[misc]
    # Mesmo custo de PBKDF2 em acerto e erro; comparação em tempo constante.
    certo = hmac.compare_digest(_hash_pin(pin, salt, it), esperado)
    if not certo:
        if not local:
            bloqueado_s, restantes = _login_falhou(ip)
            if bloqueado_s:
                return _resp_429(bloqueado_s)
        log.warning("Login com PIN incorreto (IP %s).", ip)
        return jsonify({"ok": False, "error": "PIN incorreto.", "tentativas_restantes": restantes}), 401
    if not local:
        _login_sucesso(ip)
    log.info("Login com PIN bem-sucedido (IP %s).", ip)
    resp = jsonify({"ok": True})
    novo, vida = _emitir_token(cfg)
    _definir_cookie_sessao(resp, novo, vida)
    return resp


@app.post("/api/auth/logout")
def auth_logout() -> Response:
    return _apagar_cookie(jsonify({"ok": True}))


@app.post("/api/auth/pin")
def auth_pin() -> Response:
    """Define/troca (str) ou remove (null) o PIN. Somente no computador da loja."""
    if not _requisicao_local():
        raise ErroApi("Defina o PIN no computador da loja.", 403)
    dados = corpo_json()
    if "pin" not in dados:
        raise ErroApi("Campo 'pin' ausente.")
    pin = dados["pin"]
    configurado = aplicar_pin(None if pin is None else validar_pin_novo(pin))
    log.info("PIN de acesso pela rede %s.", "definido/trocado" if pin is not None else "removido")
    return jsonify({"ok": True, "pin_configurado": configurado})


@app.post("/api/auth/revogar")
def auth_revogar() -> Response:
    """Derruba todas as sessões (acesso_versao + 1) sem trocar o PIN. Somente local."""
    if not _requisicao_local():
        raise ErroApi("Revogue as sessões no computador da loja.", 403)
    corpo_json()
    with _config_lock:
        cfg = load_config()
        cfg["acesso_versao"] = _versao_acesso(cfg) + 1
        _gravar_config_atomico(cfg)
    _login_resetar_global()
    log.info("Todas as sessões de acesso pela rede foram revogadas.")
    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# Utilidades de rede
# ---------------------------------------------------------------------------
_internet_lock = threading.Lock()
_internet_cache: dict[str, Any] = {"ok": False, "ts": None}  # ts em time.monotonic()
INTERNET_TTL_S = 10.0


def tem_internet(timeout: float = 1.5) -> bool:
    """
    Testa conectividade (TCP em 8.8.8.8:53) com cache de ~10 s.
    Só UMA thread testa por vez; as demais usam o último valor em cache.
    Usa timeout por conexão (sem socket.setdefaulttimeout, que é global) e
    fecha o socket.
    """
    def valido() -> bool:
        ts = _internet_cache["ts"]
        return ts is not None and (time.monotonic() - ts) < INTERNET_TTL_S

    if valido():
        return bool(_internet_cache["ok"])
    # Na primeiríssima checagem não há valor em cache: espera a outra thread.
    if not _internet_lock.acquire(blocking=_internet_cache["ts"] is None):
        return bool(_internet_cache["ok"])
    try:
        if valido():  # outra thread pode ter atualizado enquanto esperávamos
            return bool(_internet_cache["ok"])
        try:
            with socket.create_connection(("8.8.8.8", 53), timeout=timeout):
                ok = True
        except OSError:
            ok = False
        _internet_cache["ok"] = ok
        _internet_cache["ts"] = time.monotonic()
        return ok
    finally:
        _internet_lock.release()


def _invalidar_cache_internet() -> None:
    """Força um novo teste na próxima chamada (ex.: depois de uma falha online)."""
    _internet_cache["ts"] = -1e9  # muito no passado: o TTL já expirou


def ip_local() -> str:
    """IP da rede local (para mostrar na inicialização). Não envia pacotes."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))
            return s.getsockname()[0]
    except OSError:
        try:
            return socket.gethostbyname(socket.gethostname())
        except OSError:
            return "127.0.0.1"


# ---------------------------------------------------------------------------
# Áudio — TUDO (pygame + pyttsx3) numa única thread dedicada.
# No Windows, SAPI/COM exige CoInitialize por thread; com uma thread só,
# o mixer é iniciado uma vez e reaproveitado entre anúncios.
# ---------------------------------------------------------------------------
os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
# Tetos: o cliente espera ~180 s por /api/announce; a soma dos tetos do servidor
# (geração 30 s + duck/restore do Spotify + áudio) precisa ficar abaixo disso.
TIMEOUT_AUDIO_S = 90.0  # teto de espera por um anúncio (fala + reprodução)
TIMEOUT_REPRODUCAO_S = 80.0  # teto do laço de reprodução do mp3 (< TIMEOUT_AUDIO_S)
TIMEOUT_GERACAO_S = 30.0  # teto para o edge-tts gerar o mp3


def _iniciar_thread_audio() -> None:
    """Initializer do executor: prepara COM nesta thread (só Windows)."""
    try:
        import pythoncom  # type: ignore[import-not-found]

        pythoncom.CoInitialize()
    except ImportError:
        pass  # Linux/macOS (ou pywin32 ausente): nada a fazer
    except Exception:  # noqa: BLE001
        log.warning("Não foi possível inicializar COM na thread de áudio.", exc_info=True)


def _novo_executor_audio() -> ThreadPoolExecutor:
    return ThreadPoolExecutor(
        max_workers=1, thread_name_prefix="audio", initializer=_iniciar_thread_audio
    )


_executor_lock = threading.Lock()
_executor_audio = _novo_executor_audio()


def _encerrar_executor_audio() -> None:
    with _executor_lock:
        _executor_audio.shutdown(wait=False, cancel_futures=True)


atexit.register(_encerrar_executor_audio)


def _substituir_executor_audio() -> None:
    """
    Descarta o executor cujo único worker está preso (SAPI/driver travado) e cria
    outro, para os próximos anúncios não ficarem enfileirados atrás da tarefa
    travada. A thread presa não pode ser morta; fica órfã até o processo reiniciar.
    """
    global _executor_audio
    with _executor_lock:
        _executor_audio.shutdown(wait=False, cancel_futures=True)
        _executor_audio = _novo_executor_audio()
    log.error(
        "Thread de áudio travada: executor substituído. Se o problema se repetir, "
        "reinicie o servidor (a thread antiga continua presa até lá)."
    )
    _sinalizar_executor_substituido()


def _sinalizar_executor_substituido() -> None:
    """
    Com o modo "saídas de som" em uso, a thread órfã pode estar dentro do pygame: o motor
    fica CONTAMINADO (ninguém mexe no pygame; 503 motor_contaminado) até reiniciar. Sem
    "saida_anuncio" e sem o motor ter sido usado, nada muda em relação à v2.2.1.
    """
    try:
        som = SOM.config_som()
        if SOM.motor_em_uso or (som is not None and som["saida_anuncio"] is not None):
            SOM.marcar_contaminado()
            log.error("Motor de áudio contaminado: reinicie o Proclamare.")
    except Exception:  # noqa: BLE001 - nunca atrapalha a troca do executor
        log.warning("Não foi possível marcar o motor de áudio como contaminado.", exc_info=True)


def executar_no_audio(func: Callable[..., Any], *args: Any, timeout: float = TIMEOUT_AUDIO_S) -> Any:
    """Roda `func` na thread de áudio e espera o resultado (propaga exceções)."""
    with _executor_lock:
        futuro = _executor_audio.submit(func, *args)
    try:
        return futuro.result(timeout=timeout)
    except FuturoTimeout:
        # cancel() só funciona se a tarefa ainda não começou (estava na fila).
        # Se já estava rodando, o worker está preso: troca o executor.
        if not futuro.cancel():
            if futuro.done():  # terminou no instante do timeout: aproveita o resultado
                return futuro.result()
            _substituir_executor_audio()
        raise TimeoutError("A reprodução de áudio excedeu o tempo limite.") from None


def _garantir_mixer(reiniciar: bool = False) -> Any:
    """(Thread de áudio) Inicia o mixer uma vez; `reiniciar` descarta e recria."""
    import pygame

    # Se o mixer ficou aberto pelo motor de "saídas de som" (teste ou anúncio anterior em
    # outra saída), fecha: o caminho antigo SEMPRE usa o dispositivo padrão do Windows.
    if SOM.motor_em_uso and SOM.motor.aberto:
        SOM.motor.fechar()
    if not SOM.motor_ok:
        raise RuntimeError("motor de áudio contaminado")
    if reiniciar and pygame.mixer.get_init():
        with som_pkg.bloqueio_sdl:   # a vigia lê a lista do SDL: o quit não pode correr junto
            pygame.mixer.quit()
    if not pygame.mixer.get_init():
        # O lock evita que a varredura de saídas (som/local.py) esteja com o SDL de
        # áudio inicializado (e SDL_AUDIODRIVER temporário) no mesmo instante.
        with som_pkg.bloqueio_sdl:
            pygame.mixer.init()
    return pygame


def _tocar_mp3(caminho: Path) -> None:
    """(Thread de áudio) Toca o mp3 até o fim e apaga o arquivo em seguida."""
    pygame = None
    try:
        for tentativa in (0, 1):
            try:
                pygame = _garantir_mixer(reiniciar=tentativa == 1)
                pygame.mixer.music.load(str(caminho))
                pygame.mixer.music.play()
                break
            except Exception:  # noqa: BLE001  (pygame.error, dispositivo sumiu...)
                if tentativa == 1:
                    raise
                log.warning("Falha ao tocar; reiniciando o mixer e tentando de novo.")
        limite = time.monotonic() + TIMEOUT_REPRODUCAO_S
        while pygame.mixer.music.get_busy() and time.monotonic() < limite:
            time.sleep(0.05)
    finally:
        if pygame is not None:
            with contextlib.suppress(Exception):
                pygame.mixer.music.stop()
                pygame.mixer.music.unload()  # libera o arquivo (Windows trava a exclusão)
        apagar_audio(caminho)


def apagar_audio(caminho: Path | None) -> None:
    if caminho is not None:
        with contextlib.suppress(OSError):
            caminho.unlink(missing_ok=True)


_ultima_limpeza = 0.0


def limpar_audios_antigos(forcar: bool = False) -> None:
    """
    Rede de segurança: mantém no máximo MAX_ARQUIVOS_TEMP mp3/wav na pasta
    (normalmente cada mp3 já é apagado logo após tocar). Roda raramente.
    """
    global _ultima_limpeza
    agora = time.monotonic()
    if not forcar and (agora - _ultima_limpeza) < INTERVALO_LIMPEZA_S:
        return
    _ultima_limpeza = agora
    arquivos: list[tuple[float, Path]] = []
    for p in [*AUDIO_DIR.glob("anuncio_*.mp3"), *AUDIO_DIR.glob("anuncio_*.wav")]:
        try:
            arquivos.append((p.stat().st_mtime, p))
        except OSError:
            continue
    arquivos.sort()
    for _, p in arquivos[: max(len(arquivos) - MAX_ARQUIVOS_TEMP, 0)]:
        apagar_audio(p)


async def _gerar_audio_online_async(texto: str, voz: str, taxa: str, caminho: Path) -> None:
    import edge_tts

    communicate = edge_tts.Communicate(texto, voz, rate=taxa)
    await asyncio.wait_for(communicate.save(str(caminho)), timeout=TIMEOUT_GERACAO_S)


def gerar_audio_online(texto: str, voz: str, velocidade_pct: int) -> Path:
    """Gera o mp3 com edge-tts (nome único); em caso de falha não deixa lixo."""
    taxa = f"{velocidade_pct:+d}%"
    caminho = AUDIO_DIR / f"anuncio_{uuid.uuid4().hex}.mp3"
    try:
        asyncio.run(_gerar_audio_online_async(texto, voz, taxa, caminho))
        if not caminho.exists() or caminho.stat().st_size == 0:
            raise RuntimeError("edge-tts não gerou áudio.")
    except BaseException:
        apagar_audio(caminho)
        raise
    return caminho


# --- Vozes offline (pyttsx3) -------------------------------------------------
def escolher_voz_offline_padrao(vozes: list[Any]) -> str | None:
    """Prefere voz pt-BR, depois qualquer pt; senão None (padrão do sistema)."""
    candidatos_pt_br, candidatos_pt = [], []
    for v in vozes:
        nome = (v.name or "").lower()
        try:
            idiomas = " ".join(
                lang.decode() if isinstance(lang, bytes) else str(lang)
                for lang in (v.languages or [])
            ).lower()
        except Exception:  # noqa: BLE001
            idiomas = ""
        texto = f"{nome} {idiomas} {v.id.lower()}"
        if "brazil" in texto or "pt_br" in texto or "pt-br" in texto:
            candidatos_pt_br.append(v.id)
        elif "portug" in texto or texto.strip().startswith("pt"):
            candidatos_pt.append(v.id)
    return (candidatos_pt_br or candidatos_pt or [None])[0]


def _listar_vozes_offline() -> dict:
    """(Thread de áudio) Consulta o pyttsx3 e devolve {"voices": [...], "default": ...}."""
    import pyttsx3

    engine = pyttsx3.init()
    try:
        vozes = list(engine.getProperty("voices"))
    finally:
        with contextlib.suppress(Exception):
            engine.stop()
    return {
        "voices": [{"id": str(v.id), "name": str(v.name or v.id)} for v in vozes],
        "default": escolher_voz_offline_padrao(vozes),
    }


def _falar_offline(texto: str, velocidade_wpm: int, voz_id: str | None) -> None:
    """(Thread de áudio) Fala o texto com pyttsx3 e espera terminar."""
    import pyttsx3

    engine = pyttsx3.init()
    try:
        engine.setProperty("rate", velocidade_wpm)
        if voz_id:
            engine.setProperty("voice", voz_id)
        engine.say(texto_para_sapi(texto))   # sem XML/SSML do SAPI (<audio src>, <silence>)
        engine.runAndWait()
    finally:
        with contextlib.suppress(Exception):
            engine.stop()


_vozes_lock = threading.Lock()
_vozes_cache: dict | None = None  # só guarda sucesso
_vozes_falha_ts: float | None = None
VOZES_RETRY_S = 30.0  # após falha, não tenta de novo antes disso


def obter_vozes_offline() -> dict | None:
    """Lista de vozes offline (cacheada após a 1ª chamada). None se indisponível."""
    global _vozes_cache, _vozes_falha_ts
    if _vozes_cache is not None:
        return _vozes_cache
    with _vozes_lock:
        if _vozes_cache is not None:
            return _vozes_cache
        if _vozes_falha_ts is not None and (time.monotonic() - _vozes_falha_ts) < VOZES_RETRY_S:
            return None
        try:
            _vozes_cache = executar_no_audio(_listar_vozes_offline, timeout=30.0)
        except Exception:  # noqa: BLE001
            log.warning("Motor de voz offline indisponível.", exc_info=True)
            _vozes_falha_ts = time.monotonic()
            return None
        return _vozes_cache


def resolver_voz_offline(voz_id: Any) -> str | None:
    """Só aceita um id que exista na lista de vozes; qualquer outra coisa é ignorada."""
    if not isinstance(voz_id, str) or not voz_id or len(voz_id) > 512:
        return None
    vozes = obter_vozes_offline()
    if vozes and any(v["id"] == voz_id for v in vozes["voices"]):
        return voz_id
    return None


VOZES_ONLINE = {
    "francisca": "pt-BR-FranciscaNeural",
    "antonio": "pt-BR-AntonioNeural",
}
MOTORES = frozenset({"auto", "online", "offline"})
VELOCIDADE_OFFLINE_WPM = 165


# ---------------------------------------------------------------------------
# Spotify — Authorization Code (com client secret, guardado só no servidor)
# controlando o dispositivo via Spotify Connect, sem navegador aberto
# ---------------------------------------------------------------------------
SPOTIFY_AUTH_URL = "https://accounts.spotify.com/authorize"
SPOTIFY_TOKEN_URL = "https://accounts.spotify.com/api/token"
SPOTIFY_API = "https://api.spotify.com/v1"
SPOTIFY_SCOPES = (
    "user-read-playback-state user-modify-playback-state user-read-currently-playing"
)
SPOTIFY_TIMEOUT = (3.05, 8.0)  # (conexão, leitura) em segundos
SPOTIFY_DUCK_VOLUME = 12  # volume (%) da música durante o anúncio
PLAYER_CACHE_TTL_S = 2.0
BACKOFF_MAX_S = 120.0  # teto para respeitar Retry-After (429)

RE_DEVICE_ID = re.compile(r"[A-Za-z0-9]{1,128}")
RE_URI_TRACK = re.compile(r"spotify:track:[A-Za-z0-9]{1,64}")
RE_URI_CONTEXTO = re.compile(r"spotify:(?:playlist|album|artist):[A-Za-z0-9]{1,64}")
RE_CREDENCIAL = re.compile(r"[\x21-\x7E]*")  # ASCII imprimível, sem espaços/controle


def _criar_sessao_http() -> requests.Session:
    """
    Sessão única: pool de conexões + keep-alive para os hosts do Spotify.
    Repete só falhas de CONEXÃO (seguro para qualquer método). Sem cookies.
    """
    sessao = requests.Session()
    retry = Retry(total=2, connect=2, read=0, status=0, backoff_factor=0.2)
    adaptador = HTTPAdapter(pool_connections=4, pool_maxsize=16, max_retries=retry)
    sessao.mount("https://api.spotify.com", adaptador)
    sessao.mount("https://accounts.spotify.com", adaptador)
    sessao.cookies.set_policy(DefaultCookiePolicy(allowed_domains=[]))
    sessao.headers["User-Agent"] = "Proclamare/2.2"
    return sessao


_http = _criar_sessao_http()


def _redirect_uri_do_ambiente() -> str:
    """
    Redirect URI FIXO (não derivado do Host da requisição). O Spotify só aceita
    https, ou http com IP de loopback (127.0.0.1 / [::1]; "localhost" não vale).
    Por isso o padrão é http://127.0.0.1:<porta>/spotify/callback e a conexão do
    Spotify deve ser feita no computador da loja, em http://127.0.0.1:<porta>.
    """
    padrao = f"http://127.0.0.1:{PORT}/spotify/callback"
    bruto = _env("SPOTIFY_REDIRECT", "").strip()
    if not bruto:
        return padrao
    ok = False
    try:
        partes = urlsplit(bruto)
        host = partes.hostname or ""
        _ = partes.port  # valida a porta
        ok = bool(host) and not partes.query and not partes.fragment and partes.username is None
        if ok and partes.scheme == "http":
            ok = host in ("127.0.0.1", "::1")
        elif ok:
            ok = partes.scheme == "https"
    except ValueError:
        ok = False
    if not ok:
        log.warning(
            "PROCLAMARE_SPOTIFY_REDIRECT inválida (use https:// ou http://127.0.0.1); "
            "usando o padrão."
        )
        return padrao
    return bruto


SPOTIFY_REDIRECT_URI = _redirect_uri_do_ambiente()


def spotify_redirect_uri() -> str:
    return SPOTIFY_REDIRECT_URI


def _redirect_e_loopback() -> bool:
    """True se o Redirect URI aponta para o próprio computador (loopback)."""
    return urlsplit(SPOTIFY_REDIRECT_URI).hostname in ("127.0.0.1", "::1")


# --- Token (renovação sem corrida) -------------------------------------------
_token_lock = threading.Lock()
_token_falha_ate = 0.0  # monotonic: após falha de renovação, não martela o accounts.spotify.com
TOKEN_FALHA_BACKOFF_S = 15.0


def _token_valido(cfg: dict) -> str | None:
    token = cfg.get("spotify_access_token")
    if token and time.time() < cfg.get("spotify_expires_at", 0):
        return token
    return None


def spotify_get_token(stale: str | None = None) -> str | None:
    """
    Devolve um access token válido, renovando se preciso. `stale` é um token
    que a API acabou de rejeitar (401): força a renovação.
    Caminho rápido sem lock; renovação sob lock dedicado com double-check.
    """
    if stale is None:
        token = _token_valido(load_config())
        if token:
            return token
    with _token_lock:
        cfg = load_config()
        token = _token_valido(cfg)
        if token and token != stale:  # outra thread já renovou enquanto esperávamos
            return token
        return _spotify_refresh_token(cfg)


def _spotify_refresh_token(cfg: dict) -> str | None:
    """Renova o token (chamar SEMPRE com _token_lock segurado)."""
    global _token_falha_ate
    refresh = cfg.get("spotify_refresh_token")
    client_id = cfg.get("spotify_client_id")
    client_secret = cfg.get("spotify_client_secret")
    if not (refresh and client_id and client_secret):
        return None
    if time.monotonic() < _token_falha_ate:
        return None
    try:
        res = _http.post(
            SPOTIFY_TOKEN_URL,
            data={"grant_type": "refresh_token", "refresh_token": refresh},
            auth=(client_id, client_secret),
            timeout=SPOTIFY_TIMEOUT,
        )
        data = res.json() if res.status_code == 200 else {}
    except (requests.RequestException, ValueError):
        log.warning("Falha de rede/resposta ao renovar o token do Spotify.")
        _token_falha_ate = time.monotonic() + TOKEN_FALHA_BACKOFF_S
        return None
    if not isinstance(data, dict) or not isinstance(data.get("access_token"), str):
        log.warning("Renovação do token do Spotify recusada (HTTP %s).", res.status_code)
        _token_falha_ate = time.monotonic() + TOKEN_FALHA_BACKOFF_S
        return None
    mudancas: dict[str, Any] = {
        "spotify_access_token": data["access_token"],
        "spotify_expires_at": time.time() + int(data.get("expires_in", 3600)) - 60,
    }
    if data.get("refresh_token"):
        mudancas["spotify_refresh_token"] = data["refresh_token"]
    update_config(**mudancas)
    return data["access_token"]


# --- Chamadas à API (com tratamento de 401/429) ------------------------------
_backoff_ate = 0.0  # monotonic: enquanto no futuro, não chama a API (429 recente)


def _em_backoff() -> bool:
    return time.monotonic() < _backoff_ate


def _registrar_429(res: requests.Response) -> None:
    global _backoff_ate
    try:
        espera = float(res.headers.get("Retry-After", "5"))
    except ValueError:
        espera = 5.0
    espera = max(1.0, min(espera, BACKOFF_MAX_S))
    _backoff_ate = time.monotonic() + espera
    log.warning("Spotify respondeu 429; pausando chamadas por %.0f s.", espera)


def spotify_api(
    method: str,
    path: str,
    *,
    params: dict[str, Any] | None = None,
    json_body: dict | None = None,
) -> tuple[dict, int]:
    """
    Chama a API do Spotify. Devolve (json, status); status 401 = sem token,
    0 = falha de rede, 429 = limite (respeita Retry-After). Parâmetros de
    query SEMPRE via `params` (nunca concatenados na URL).
    """
    if _em_backoff():
        return {}, 429
    token = spotify_get_token()
    if not token:
        return {}, 401
    res: requests.Response | None = None
    for tentativa in (0, 1):
        try:
            res = _http.request(
                method,
                SPOTIFY_API + path,
                params=params,
                json=json_body,
                headers={"Authorization": f"Bearer {token}"},
                timeout=SPOTIFY_TIMEOUT,
            )
        except requests.RequestException:
            log.warning("Falha de rede ao chamar o Spotify (%s %s).", method, path)
            return {}, 0
        if res.status_code == 401 and tentativa == 0:
            novo = spotify_get_token(stale=token)  # token revogado/expirado antes da hora
            if novo and novo != token:
                token = novo
                continue
        break
    assert res is not None
    if res.status_code == 429:
        _registrar_429(res)
        return {}, 429
    if res.status_code == 204 or not res.content:
        return {}, res.status_code
    try:
        corpo = res.json()
    except ValueError:
        return {}, res.status_code
    return (corpo if isinstance(corpo, dict) else {}), res.status_code


def spotify_active_device_id() -> str | None:
    return load_config().get("spotify_device_id")


# --- Cache compartilhado do estado do player ---------------------------------
_player_lock = threading.Lock()  # serializa a busca: N clientes -> 1 chamada
_player_estado: dict[str, Any] = {"data": None, "ts": None, "gen": 0}


def spotify_invalidar_cache() -> None:
    """Descarta o cache (chamar após qualquer comando de controle)."""
    _player_estado["gen"] += 1
    _player_estado["ts"] = None


def _cache_player_fresco() -> bool:
    ts = _player_estado["ts"]
    return ts is not None and (time.monotonic() - ts) < PLAYER_CACHE_TTL_S


def spotify_player_state(force: bool = False) -> dict | None:
    """
    GET /me/player com cache de ~2 s compartilhado entre todos os clientes.
    Em 429 (ou backoff ativo) devolve o último valor em cache em vez de
    martelar a API. Devolve {} se não há player ativo e None se indisponível.
    """
    if not force and _cache_player_fresco():
        return _player_estado["data"]
    with _player_lock:
        if not force and _cache_player_fresco():  # double-check
            return _player_estado["data"]
        if _em_backoff():
            return _player_estado["data"]
        geracao = _player_estado["gen"]
        data, status = spotify_api("GET", "/me/player")
        if status == 429:
            return _player_estado["data"]
        resultado: dict | None = data if status == 200 and data else ({} if status in (200, 204) else None)
        # Só grava se nenhum comando invalidou o cache durante a chamada.
        if geracao == _player_estado["gen"]:
            _player_estado["data"] = resultado
            _player_estado["ts"] = time.monotonic()
        return resultado


def spotify_comando(
    method: str,
    path: str,
    *,
    params: dict[str, Any] | None = None,
    json_body: dict | None = None,
) -> bool:
    """Executa um comando de controle e invalida o cache do player."""
    _, status = spotify_api(method, path, params=params, json_body=json_body)
    spotify_invalidar_cache()
    return status in (200, 204)


# --- Imagens e objetos enxutos -------------------------------------------------
def escolher_imagem(imagens: Any, largura_min: int = 64) -> str | None:
    """Menor imagem com largura >= largura_min; senão a última da lista."""
    if not isinstance(imagens, list):
        return None
    validas = [i for i in imagens if isinstance(i, dict) and i.get("url")]
    if not validas:
        return None
    grandes = [i for i in validas if isinstance(i.get("width"), int) and i["width"] >= largura_min]
    if grandes:
        return min(grandes, key=lambda i: i["width"])["url"]
    return validas[-1]["url"]


def _nomes_artistas(artistas: Any) -> str:
    if not isinstance(artistas, list):
        return ""
    return ", ".join(a["name"] for a in artistas if isinstance(a, dict) and a.get("name"))


# --- Cache pequeno de buscas (evita repetir a mesma busca digitada) ----------
_busca_lock = threading.Lock()
_busca_cache: dict[str, tuple[float, dict]] = {}
BUSCA_TTL_S = 60.0
BUSCA_MAX_ENTRADAS = 64


# --- Fluxo OAuth (state aleatório, uso único, só em memória) -----------------
_oauth_lock = threading.Lock()
_oauth_states: dict[str, tuple[float, str]] = {}  # state -> (expira em monotonic, redirect_uri)
OAUTH_STATE_TTL_S = 600.0
OAUTH_STATES_MAX = 20


def _oauth_novo_state(redirect_uri: str) -> str:
    state = secrets.token_urlsafe(24)
    agora = time.monotonic()
    with _oauth_lock:
        for k in [k for k, (exp, _) in _oauth_states.items() if exp < agora]:
            del _oauth_states[k]
        while len(_oauth_states) >= OAUTH_STATES_MAX:  # descarta o mais antigo
            del _oauth_states[min(_oauth_states, key=lambda k: _oauth_states[k][0])]
        _oauth_states[state] = (agora + OAUTH_STATE_TTL_S, redirect_uri)
    return state


def _oauth_consumir_state(recebido: str) -> str | None:
    """Valida (compare_digest) e CONSOME o state; devolve o redirect_uri usado no login."""
    agora = time.monotonic()
    achado: str | None = None
    with _oauth_lock:
        for k in list(_oauth_states):
            # compare_digest em todos, sem curto-circuito, para não vazar por tempo
            if secrets.compare_digest(k.encode(), recebido.encode()):
                achado = k
        if achado is None:
            return None
        expira, redirect_uri = _oauth_states.pop(achado)
    return redirect_uri if expira >= agora else None


@app.get("/spotify/login")
def spotify_login() -> Any:
    cfg = load_config()
    if not _sessao_ok(cfg):  # local OU sessão válida (o callback não exige sessão: cookie Strict)
        if pin_configurado(cfg):
            return "Digite o PIN no painel antes de conectar o Spotify.", 401
        return "Acesso pela rede desativado. Defina um PIN no computador da loja.", 401
    client_id = cfg.get("spotify_client_id")
    if not client_id:
        return "Configure o Client ID do Spotify primeiro (botão 'Configurar Spotify').", 400
    if _redirect_e_loopback() and not _requisicao_local():
        # O Spotify devolveria o navegador para 127.0.0.1 DESTE aparelho, não da loja.
        return (
            f"Conecte o Spotify no computador da loja, abrindo http://127.0.0.1:{PORT}",
            403,
        )
    if excedeu_limite("login:" + (request.remote_addr or "?"), 5, 60.0):
        return "Muitas tentativas. Aguarde um minuto.", 429
    redirect_uri = spotify_redirect_uri()
    params = {
        "client_id": client_id,
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "scope": SPOTIFY_SCOPES,
        "state": _oauth_novo_state(redirect_uri),
    }
    return redirect(SPOTIFY_AUTH_URL + "?" + urlencode(params))


@app.get("/spotify/callback")
def spotify_callback() -> Any:
    global _token_falha_ate
    state = request.args.get("state", "")
    redirect_uri = _oauth_consumir_state(state) if 0 < len(state) <= 128 else None
    if redirect_uri is None:  # state ausente/errado/expirado/já usado
        log.warning("Callback do Spotify com state inválido.")
        return redirect("/?spotify=error")

    code = request.args.get("code", "")
    if request.args.get("error") or not code or len(code) > 1024:
        return redirect("/?spotify=error")

    cfg = load_config()
    client_id = cfg.get("spotify_client_id")
    client_secret = cfg.get("spotify_client_secret")
    if not (client_id and client_secret):
        return redirect("/?spotify=error")

    try:
        res = _http.post(
            SPOTIFY_TOKEN_URL,
            data={"grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri},
            auth=(client_id, client_secret),
            timeout=SPOTIFY_TIMEOUT,
        )
        data = res.json() if res.status_code == 200 else {}
    except (requests.RequestException, ValueError):
        log.warning("Falha de rede/resposta na troca do code do Spotify.")
        return redirect("/?spotify=error")
    if not isinstance(data, dict) or not isinstance(data.get("access_token"), str):
        log.warning("Troca do code do Spotify recusada (HTTP %s).", res.status_code)
        return redirect("/?spotify=error")

    update_config(
        spotify_access_token=data["access_token"],
        spotify_refresh_token=data.get("refresh_token") or cfg.get("spotify_refresh_token"),
        spotify_expires_at=time.time() + int(data.get("expires_in", 3600)) - 60,
    )
    _token_falha_ate = 0.0
    spotify_invalidar_cache()
    return redirect("/?spotify=connected")


@app.get("/api/spotify/config")
def spotify_config_get() -> Response:
    cfg = load_config()
    # O client_secret NUNCA é devolvido: só um booleano informando se existe.
    return jsonify(
        {
            "client_id": cfg.get("spotify_client_id", ""),
            "has_secret": bool(cfg.get("spotify_client_secret")),
            "redirect_uri": spotify_redirect_uri(),
        }
    )


@app.post("/api/spotify/config")
def spotify_config_set() -> Response:
    global _token_falha_ate
    # O client_secret trafega em texto claro (HTTP): só aceita configurar no
    # próprio computador da loja (loopback), nunca a partir de outro aparelho.
    if not _requisicao_local():
        raise ErroApi("Configure o Spotify no computador da loja.", 403)
    data = corpo_json()
    changes: dict[str, str] = {}
    if "client_id" in data:
        valor = validar_str(data["client_id"], "client_id", 128)
        if not RE_CREDENCIAL.fullmatch(valor):
            raise ErroApi("Campo 'client_id' contém caracteres inválidos.")
        if valor:  # vazio = manter o atual (nunca apaga o client_id e derruba a renovação)
            changes["spotify_client_id"] = valor
    if data.get("client_secret"):  # vazio = manter o segredo atual
        valor = validar_str(data["client_secret"], "client_secret", 128)
        if not RE_CREDENCIAL.fullmatch(valor):
            raise ErroApi("Campo 'client_secret' contém caracteres inválidos.")
        if valor:
            changes["spotify_client_secret"] = valor
    if changes:
        update_config(**changes)
        _token_falha_ate = 0.0  # credenciais novas: permite tentar renovar já
    return jsonify({"ok": True})


@app.get("/api/spotify/status")
def spotify_status() -> Response:
    cfg = load_config()
    connected = bool(cfg.get("spotify_refresh_token")) and spotify_get_token() is not None
    return jsonify({"connected": connected, "device_id": cfg.get("spotify_device_id")})


@app.get("/api/spotify/devices")
def spotify_devices() -> Response:
    data, status = spotify_api("GET", "/me/player/devices")
    if status != 200:
        return jsonify({"ok": False, "devices": []})
    lista = []
    for d in data.get("devices") or []:
        if isinstance(d, dict) and d.get("id"):
            lista.append(
                {
                    "id": d.get("id"),
                    "name": d.get("name"),
                    "type": d.get("type"),
                    "is_active": bool(d.get("is_active")),
                    "volume_percent": d.get("volume_percent"),
                }
            )
    return jsonify({"ok": True, "devices": lista})


@app.post("/api/spotify/activate")
def spotify_activate() -> Response:
    device_id = validar_str(corpo_json().get("device_id"), "device_id", 128, min_len=1)
    if not RE_DEVICE_ID.fullmatch(device_id):
        raise ErroApi("Campo 'device_id' inválido.")
    update_config(spotify_device_id=device_id)
    # A transferência de reprodução é "melhor esforço" (o dispositivo já fica
    # salvo mesmo que o app do Spotify ainda não esteja ativo).
    spotify_comando("PUT", "/me/player", json_body={"device_ids": [device_id], "play": False})
    return jsonify({"ok": True})


@app.get("/api/spotify/search")
def spotify_search() -> Response | tuple[Response, int]:
    q = validar_str(request.args.get("q", ""), "q", 100, min_len=1)
    agora = time.monotonic()
    if excedeu_limite("busca:" + (request.remote_addr or "?"), 4, 2.0):  # protege a cota do Spotify
        return jsonify({"ok": False, "tracks": [], "playlists": [], "error": "Muitas buscas seguidas."}), 429
    with _busca_lock:
        hit = _busca_cache.get(q)
    if hit and (agora - hit[0]) < BUSCA_TTL_S:
        return jsonify(hit[1])

    data, status = spotify_api(
        "GET", "/search", params={"q": q, "type": "track,playlist", "limit": 8}
    )
    if status != 200:
        return jsonify({"ok": False, "tracks": [], "playlists": []})

    tracks = []
    for t in (data.get("tracks") or {}).get("items") or []:
        if isinstance(t, dict) and t.get("uri"):
            tracks.append(
                {
                    "name": t.get("name") or "",
                    "artists": _nomes_artistas(t.get("artists")),
                    "uri": t["uri"],
                    "image": escolher_imagem((t.get("album") or {}).get("images")),
                    "duration_ms": t.get("duration_ms") if isinstance(t.get("duration_ms"), int) else 0,
                }
            )
    playlists = []
    for p in (data.get("playlists") or {}).get("items") or []:
        if isinstance(p, dict) and p.get("uri"):
            dono = p.get("owner") or {}
            playlists.append(
                {
                    "name": p.get("name") or "",
                    "uri": p["uri"],
                    "image": escolher_imagem(p.get("images")),
                    "owner": dono.get("display_name") or dono.get("id") or "",
                }
            )
    resultado = {"ok": True, "tracks": tracks, "playlists": playlists}
    with _busca_lock:
        if len(_busca_cache) >= BUSCA_MAX_ENTRADAS:
            _busca_cache.pop(min(_busca_cache, key=lambda k: _busca_cache[k][0]), None)
        _busca_cache[q] = (agora, resultado)
    return jsonify(resultado)


@app.post("/api/spotify/play")
def spotify_play() -> Response | tuple[Response, int]:
    body = corpo_json()
    uri = body.get("uri") or None
    context_uri = body.get("context_uri") or None
    payload: dict[str, Any] = {}
    if uri is not None:
        if not isinstance(uri, str) or not RE_URI_TRACK.fullmatch(uri):
            raise ErroApi("Campo 'uri' inválido.")
        payload["uris"] = [uri]
    if context_uri is not None:
        if not isinstance(context_uri, str) or not RE_URI_CONTEXTO.fullmatch(context_uri):
            raise ErroApi("Campo 'context_uri' inválido.")
        payload["context_uri"] = context_uri
    if uri is not None and context_uri is not None:
        raise ErroApi("Envie 'uri' ou 'context_uri', não os dois.")
    device_id = spotify_active_device_id()
    if not device_id:
        return _resp_erro("Nenhum dispositivo Spotify selecionado.", 400)
    ok = spotify_comando("PUT", "/me/player/play", params={"device_id": device_id}, json_body=payload)
    return jsonify({"ok": ok})


def _comando_simples(method: str, path: str) -> Response:
    """pause/next/previous: mesmo formato, só muda o método e o caminho."""
    corpo_json()  # valida que veio um objeto JSON
    ok = spotify_comando(method, path, params={"device_id": spotify_active_device_id()})
    return jsonify({"ok": ok})


@app.post("/api/spotify/pause")
def spotify_pause() -> Response:
    return _comando_simples("PUT", "/me/player/pause")


@app.post("/api/spotify/next")
def spotify_next() -> Response:
    return _comando_simples("POST", "/me/player/next")


@app.post("/api/spotify/previous")
def spotify_previous() -> Response:
    return _comando_simples("POST", "/me/player/previous")


@app.post("/api/spotify/volume")
def spotify_volume() -> Response:
    value = validar_int(corpo_json().get("value"), "value", 0, 100)
    ok = spotify_comando(
        "PUT",
        "/me/player/volume",
        params={"volume_percent": value, "device_id": spotify_active_device_id()},
    )
    return jsonify({"ok": ok})


@app.get("/api/spotify/now-playing")
def spotify_now_playing() -> Response:
    data = spotify_player_state()
    vazio = {
        "ok": True, "playing": False, "track": None, "title": None, "artists": None,
        "image": None, "progress_ms": None, "duration_ms": None, "volume": None, "device": None,
    }
    if not data:
        return jsonify(vazio)
    item = data.get("item") or {}
    dispositivo = data.get("device") or {}
    titulo = item.get("name") if item else None
    artistas = (_nomes_artistas(item.get("artists")) or (item.get("show") or {}).get("publisher") or "") if item else ""
    imagens = (item.get("album") or {}).get("images") or item.get("images")
    return jsonify(
        {
            "ok": True,
            "playing": bool(data.get("is_playing")),
            "track": (f"{titulo} — {artistas}" if artistas else titulo) if titulo else None,
            "title": titulo,
            "artists": artistas or None,
            "image": escolher_imagem(imagens, largura_min=300),  # capa média (300 px)
            "progress_ms": data.get("progress_ms") if isinstance(data.get("progress_ms"), int) else None,
            "duration_ms": item.get("duration_ms") if isinstance(item.get("duration_ms"), int) else None,
            "volume": dispositivo.get("volume_percent"),
            "device": dispositivo.get("name"),
        }
    )


def spotify_duck() -> int | None:
    """Abaixa o volume do Spotify antes do anúncio; devolve o volume anterior (ou None)."""
    device_id = spotify_active_device_id()
    if not device_id:
        return None
    data = spotify_player_state(force=True)  # leitura fresca: precisamos do volume real
    if not data or not data.get("is_playing"):
        return None  # nada tocando: não há o que abaixar
    anterior = (data.get("device") or {}).get("volume_percent")
    if not isinstance(anterior, int) or anterior <= SPOTIFY_DUCK_VOLUME:
        return None
    spotify_comando(
        "PUT", "/me/player/volume",
        params={"volume_percent": SPOTIFY_DUCK_VOLUME, "device_id": device_id},
    )
    return anterior


def spotify_restore(volume_anterior: int | None) -> None:
    if volume_anterior is None:
        return
    device_id = spotify_active_device_id()
    if device_id:
        spotify_comando(
            "PUT", "/me/player/volume",
            params={"volume_percent": volume_anterior, "device_id": device_id},
        )


# ---------------------------------------------------------------------------
# YouTube — relé de comandos (precisa de uma aba "tocadora" aberta na loja)
# deque limitado + Condition: a aba tocadora usa long-poll e recebe o comando
# no instante em que ele é enfileirado.
# v2.2.1: parser de link no servidor, fila do Proclamare e títulos via oEmbed.
# ---------------------------------------------------------------------------
YOUTUBE_PLAYER_TIMEOUT = 6.0  # segundos sem heartbeat = considera tocador offline
YOUTUBE_WAIT_MAX = 25
YOUTUBE_ACOES_CLIENTE = frozenset(
    {"load", "play", "pause", "next", "prev", "volume", "play_index"}
)
YOUTUBE_HOSTS = frozenset(
    {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com", "youtu.be"}
)
YOUTUBE_FILA_MAX = 50           # itens na fila do Proclamare
YOUTUBE_PLAYLIST_MAX = 200      # ids de playlist aceitos no heartbeat
YOUTUBE_OEMBED_PENDENTES_MAX = 32  # buscas de título em andamento/esperando (anti-inflação)
YOUTUBE_OEMBED_TAXA_MAX = 30         # consultas ao oEmbed por minuto (global)
YOUTUBE_PROXIMAS_MAX = 15       # "próximas da playlist" devolvidas no estado
YOUTUBE_OEMBED_TIMEOUT = 4.0    # segundos
YOUTUBE_OEMBED_MAX_BYTES = 64 * 1024
YOUTUBE_OEMBED_CACHE_MAX = 500
YOUTUBE_OEMBED_FALHA_TTL = 600.0  # falhas ficam em cache ~10 min (não martelar o YouTube)
_RE_VIDEO_ID = re.compile(r"[A-Za-z0-9_-]{11}")
_RE_LIST_ID = re.compile(r"[A-Za-z0-9_-]{2,64}")
_RE_TEMPO = re.compile(r"(?:(\d{1,5})h)?(?:(\d{1,5})m)?(?:(\d{1,6})s?)?")

_yt_cond = threading.Condition()
YOUTUBE_POLLS_MAX = 4  # long-polls simultâneos (uso normal: 1 aba tocadora)
_yt_poll_sem = threading.BoundedSemaphore(YOUTUBE_POLLS_MAX)
_yt_comandos: deque[dict] = deque(maxlen=50)
_yt_ultimo_id = 0
_yt_estado_lock = threading.Lock()
_yt_estado: dict[str, Any] = {
    "now_playing": "", "is_playing": False, "volume": 70, "last_heartbeat": None,
    "video_id": None, "list_id": None, "playlist_ids": [], "playlist_index": None,
}

# Fila do Proclamare: SÓ em memória (não sobrevive a reinício do servidor).
_yt_fila_lock = threading.Lock()
_yt_fila: list[dict[str, Any]] = []
_yt_fila_prox_id = 0
_yt_fila_versao = 0

# Cache de títulos (oEmbed): chave = URL canônica -> (título|None, expira_em|None).
# Sucesso não expira (só sai por LRU); falha expira em ~10 min.
_yt_titulos_lock = threading.Lock()
_yt_titulos: OrderedDict[str, tuple[str | None, float | None]] = OrderedDict()
_yt_titulos_pendentes: set[str] = set()
_yt_titulos_pool: ThreadPoolExecutor | None = None


def youtube_enqueue(action: str, params: dict | None = None) -> int:
    """Enfileira um comando para a aba tocadora e acorda os long-polls."""
    global _yt_ultimo_id
    with _yt_cond:
        _yt_ultimo_id += 1
        _yt_comandos.append({"id": _yt_ultimo_id, "action": action, "params": params or {}})
        _yt_cond.notify_all()
        return _yt_ultimo_id


# --- A) Parser de link ------------------------------------------------------
def _segundos_do_tempo(texto: str) -> int | None:
    """Converte '90', '90s', '1m30s', '1h2m3s' em segundos (0..86400) ou None."""
    texto = texto.strip().lower()
    if not texto:
        return None
    m = _RE_TEMPO.fullmatch(texto)
    if not m or not any(m.groups()):
        return None
    h, mi, s = (int(g) if g else 0 for g in m.groups())
    total = h * 3600 + mi * 60 + s
    return total if 0 <= total <= 86400 else None


def parse_youtube_url(valor: Any) -> dict[str, Any]:
    """
    Interpreta um link do YouTube e devolve
    {"video_id", "list_id", "index" (0-based), "start" (segundos)} (None se ausente).
    Mix/rádio automático (list=RD... ou start_radio) é IGNORADO e toca só o vídeo;
    link que é só de Mix é recusado. Levanta ErroApi(400) para link inválido.
    """
    url = validar_str(valor, "url", 300, min_len=1)
    if any(ord(c) < 33 or ord(c) == 127 for c in url):
        raise ErroApi("Campo 'url' inválido.")
    try:
        partes = urlsplit(url)
        porta = partes.port
    except ValueError:
        raise ErroApi("Campo 'url' inválido.") from None
    host = partes.hostname or ""
    if (
        partes.scheme != "https"
        or host not in YOUTUBE_HOSTS
        or partes.username is not None
        or partes.password is not None
        or porta not in (None, 443)
    ):
        raise ErroApi("Somente links https do YouTube são aceitos.")

    consulta = parse_qs(partes.query, keep_blank_values=True)

    def _q(nome: str) -> str | None:
        """Primeiro valor do parâmetro; vazio (v=, list=) conta como ausente."""
        v = consulta.get(nome)
        return v[0] if v and v[0] != "" else None

    # --- id do vídeo: ?v= (watch) ou segmento do caminho (youtu.be, shorts...) ---
    segmentos = [s for s in partes.path.split("/") if s]
    bruto_video: str | None = None
    if host == "youtu.be":
        bruto_video = segmentos[0] if segmentos else None
    elif segmentos and segmentos[0] in ("shorts", "live", "embed"):
        bruto_video = segmentos[1] if len(segmentos) > 1 else None
    elif segmentos and segmentos[0] == "watch":
        bruto_video = _q("v")
    video_id: str | None = None
    if bruto_video is not None:
        if not _RE_VIDEO_ID.fullmatch(bruto_video):
            raise ErroApi("Link do YouTube inválido: não encontrei o vídeo.")
        video_id = bruto_video

    # --- lista: Mix automático é ignorado ---
    list_id: str | None = None
    bruto_lista = _q("list")
    mix = "start_radio" in consulta or (bruto_lista or "").upper().startswith("RD")
    if bruto_lista is not None and not mix:
        if _RE_LIST_ID.fullmatch(bruto_lista):
            list_id = bruto_lista
        elif video_id is None:
            raise ErroApi("Link do YouTube inválido: lista não reconhecida.")
    if video_id is None and list_id is None:
        if mix:
            raise ErroApi(
                "Esse link é um Mix automático do YouTube; abra uma música dele "
                "e copie o link dela."
            )
        raise ErroApi("Link do YouTube inválido: não encontrei vídeo nem playlist.")

    # --- índice (1-based no link -> 0-based) e início ---
    indice: int | None = None
    bruto_idx = _q("index")
    if list_id is not None and bruto_idx and re.fullmatch(r"\d{1,4}", bruto_idx):
        n = int(bruto_idx) - 1
        if 0 <= n < YOUTUBE_PLAYLIST_MAX:
            indice = n
    inicio: int | None = None
    frag = parse_qs(partes.fragment) if "=" in partes.fragment else {}
    bruto_t = _q("t") or _q("start") or (frag.get("t") or [None])[0]
    if bruto_t:
        inicio = _segundos_do_tempo(bruto_t)
    return {"video_id": video_id, "list_id": list_id, "index": indice, "start": inicio}


# --- B) Títulos via oEmbed (fora da thread da requisição) -------------------
def _yt_url_canonica(video_id: str | None, list_id: str | None) -> str | None:
    """URL montada SÓ a partir de ids já validados (nunca da URL do usuário)."""
    if video_id:
        return f"https://www.youtube.com/watch?v={video_id}"
    if list_id:
        return f"https://www.youtube.com/playlist?list={list_id}"
    return None


def _limpar_titulo(valor: Any) -> str | None:
    """Título seguro: str, sem caracteres de controle, até 200 caracteres."""
    if not isinstance(valor, str):
        return None
    limpo = "".join(c for c in valor if c.isprintable()).strip()[:200]
    return limpo or None


def _oembed_buscar(url: str) -> str | None:
    """Consulta o oEmbed público (sem chave). Qualquer problema -> None."""
    try:
        with requests.get(
            "https://www.youtube.com/oembed",
            params={"format": "json", "url": url},
            timeout=YOUTUBE_OEMBED_TIMEOUT,
            allow_redirects=False,  # não segue para outro host
            stream=True,
            headers={"User-Agent": "Proclamare/2.2"},
        ) as r:
            if r.status_code != 200:
                return None
            dados = bytearray()
            for pedaco in r.iter_content(chunk_size=8192):
                dados += pedaco
                if len(dados) > YOUTUBE_OEMBED_MAX_BYTES:
                    return None  # resposta grande demais
        obj = json.loads(bytes(dados).decode("utf-8"))
        return _limpar_titulo(obj.get("title")) if isinstance(obj, dict) else None
    except Exception:  # noqa: BLE001 — rede/JSON/encoding: o título é opcional
        log.debug("oEmbed falhou.")
        return None


def _titulo_em_cache(url: str | None) -> tuple[bool, str | None]:
    """(achou, título). Falhas vencidas saem do cache."""
    if url is None:
        return True, None
    with _yt_titulos_lock:
        entrada = _yt_titulos.get(url)
        if entrada is None:
            return False, None
        titulo, expira = entrada
        if expira is not None and expira <= time.monotonic():
            del _yt_titulos[url]
            return False, None
        _yt_titulos.move_to_end(url)
        return True, titulo


def _resolver_titulo(url: str) -> None:
    """Tarefa de segundo plano: busca e guarda o título no cache."""
    global _yt_fila_versao
    titulo = _oembed_buscar(url)
    with _yt_titulos_lock:
        expira = None if titulo else time.monotonic() + YOUTUBE_OEMBED_FALHA_TTL
        _yt_titulos[url] = (titulo, expira)
        _yt_titulos.move_to_end(url)
        while len(_yt_titulos) > YOUTUBE_OEMBED_CACHE_MAX:
            _yt_titulos.popitem(last=False)
        _yt_titulos_pendentes.discard(url)
    if titulo:
        with _yt_fila_lock:  # título novo muda o que o front mostra
            _yt_fila_versao += 1


def _pedir_titulo(url: str | None) -> None:
    """Agenda a busca do título se ainda não está em cache nem em andamento."""
    global _yt_titulos_pool
    if url is None or _titulo_em_cache(url)[0]:
        return
    with _yt_titulos_lock:
        if url in _yt_titulos_pendentes:
            return
        if len(_yt_titulos_pendentes) >= YOUTUBE_OEMBED_PENDENTES_MAX:
            return  # fila de buscas cheia: descarta sem gravar falha; o próximo GET tenta de novo
        if excedeu_limite("oembed", YOUTUBE_OEMBED_TAXA_MAX, 60.0):
            return  # teto global de consultas ao YouTube por minuto
        _yt_titulos_pendentes.add(url)
        if _yt_titulos_pool is None:
            _yt_titulos_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="yt-titulo")
        pool = _yt_titulos_pool
    try:
        pool.submit(_resolver_titulo, url)
    except RuntimeError:  # pool encerrado (desligando o servidor)
        with _yt_titulos_lock:
            _yt_titulos_pendentes.discard(url)


# --- B) Fila do Proclamare --------------------------------------------------
def _item_url(item: dict[str, Any]) -> str | None:
    return _yt_url_canonica(item["video_id"], item["list_id"])


def _item_publico(item: dict[str, Any]) -> dict[str, Any]:
    """Cópia do item com o título do cache (se já resolvido)."""
    return {**item, "title": _titulo_em_cache(_item_url(item))[1]}


def _params_load(item: dict[str, Any]) -> dict[str, Any]:
    return {k: item.get(k) for k in ("video_id", "list_id", "index", "start")}


def _achar_item(item_id: int) -> int:
    """Posição do item na fila (chamar COM _yt_fila_lock). 404 se não existir."""
    for pos, it in enumerate(_yt_fila):
        if it["id"] == item_id:
            return pos
    raise ErroApi("Item não encontrado na fila.", 404)


@app.post("/api/youtube/queue")
def youtube_queue() -> Response:
    """Operações da fila: add, remove, clear, move, play_now, pop."""
    global _yt_fila_prox_id, _yt_fila_versao
    body = corpo_json()
    op = body.get("op")
    if not isinstance(op, str) or op not in {"add", "remove", "clear", "move", "play_now", "pop"}:
        raise ErroApi("Operação inválida.")

    if op == "add":
        if excedeu_limite("ytq:" + (request.remote_addr or "?"), 20, 10.0):
            raise ErroApi("Muitas adições seguidas. Aguarde alguns segundos.", 429)
        alvo = parse_youtube_url(body.get("url"))  # valida ANTES de pegar o lock
        with _yt_fila_lock:
            if len(_yt_fila) >= YOUTUBE_FILA_MAX:
                raise ErroApi(f"A fila está cheia (máximo {YOUTUBE_FILA_MAX}).", 409)
            _yt_fila_prox_id += 1
            item = {
                "id": _yt_fila_prox_id,
                "kind": "playlist" if alvo["list_id"] else "video",
                "video_id": alvo["video_id"],
                "list_id": alvo["list_id"],
                "index": alvo["index"],  # extra do contrato: necessário para tocar o item
                "start": alvo["start"],
                "title": None,
                "added_at": int(time.time()),
            }
            _yt_fila.append(item)
            _yt_fila_versao += 1
            versao = _yt_fila_versao
        _pedir_titulo(_item_url(item))
        return jsonify({"ok": True, "item": _item_publico(item), "queue_version": versao})

    if op == "clear":
        with _yt_fila_lock:
            _yt_fila.clear()
            _yt_fila_versao += 1
            versao = _yt_fila_versao
        return jsonify({"ok": True, "queue_version": versao})

    if op == "pop":
        with _yt_fila_lock:
            item = _yt_fila.pop(0) if _yt_fila else None
            if item is not None:
                _yt_fila_versao += 1
            versao = _yt_fila_versao
        return jsonify({
            "ok": True, "item": _item_publico(item) if item else None,
            "queue_version": versao,
        })

    item_id = validar_int(body.get("item_id"), "item_id", 0, 2**62)
    if op == "move":
        with _yt_fila_lock:
            pos = _achar_item(item_id)
            destino = validar_int(body.get("to"), "to", 0, len(_yt_fila) - 1, limitar=True)
            _yt_fila.insert(destino, _yt_fila.pop(pos))
            _yt_fila_versao += 1
            versao = _yt_fila_versao
        return jsonify({"ok": True, "queue_version": versao})

    # remove / play_now: tiram o item da fila
    if op == "play_now":
        # Só consome o item se há tocador vivo (senão o comando ficaria retido e tocaria
        # muito depois). Ordem de locks: estado -> fila (único lugar que aninha os dois).
        with _yt_estado_lock:
            beat = _yt_estado["last_heartbeat"]
            vivo = beat is not None and (time.monotonic() - beat) < YOUTUBE_PLAYER_TIMEOUT
            if not vivo:
                raise ErroApi("Nenhum tocador aberto. Abra o modo Tocador da loja primeiro.", 409)
            with _yt_fila_lock:
                item = _yt_fila.pop(_achar_item(item_id))
                _yt_fila_versao += 1
                versao = _yt_fila_versao
    else:
        with _yt_fila_lock:
            item = _yt_fila.pop(_achar_item(item_id))
            _yt_fila_versao += 1
            versao = _yt_fila_versao
    if op == "play_now":
        cmd_id = youtube_enqueue("load", _params_load(item))
        return jsonify({"ok": True, "item": _item_publico(item), "id": cmd_id,
                        "queue_version": versao})
    return jsonify({"ok": True, "queue_version": versao})


@app.post("/api/youtube/command")
def youtube_command() -> Response:
    body = corpo_json()
    action = body.get("action")
    # "duck"/"restore" são internos do servidor: o cliente nunca pode enviá-los.
    if not isinstance(action, str) or action not in YOUTUBE_ACOES_CLIENTE:
        raise ErroApi("Ação inválida.")
    bruto = body.get("params", {})
    if bruto is None:
        bruto = {}
    if not isinstance(bruto, dict):
        raise ErroApi("Campo 'params' inválido.")
    params: dict[str, Any] = {}  # lista branca: só repassa o que foi validado
    if action == "load":
        # O servidor interpreta o link e repassa só os campos validados (nunca a URL crua).
        params = parse_youtube_url(bruto.get("url"))
        _pedir_titulo(_yt_url_canonica(params["video_id"], params["list_id"]))
    elif action == "volume":
        params["value"] = validar_int(bruto.get("value"), "value", 0, 100)
    elif action == "play_index":
        params["index"] = validar_int(bruto.get("index"), "index", 0, YOUTUBE_PLAYLIST_MAX - 1)
    return jsonify({"ok": True, "id": youtube_enqueue(action, params)})


@app.get("/api/youtube/commands")
def youtube_commands_poll() -> Response:
    """Long-poll: responde já se houver comandos novos; senão espera até `wait` s."""
    since = validar_int(request.args.get("since", "0"), "since", 0, 2**62)
    espera = validar_int(request.args.get("wait", "0"), "wait", 0, YOUTUBE_WAIT_MAX)
    # Limita os long-polls simultâneos (cada um prende uma thread do waitress).
    # Sem vaga: responde já (sem esperar), com uma pausa curta para a aba
    # tocadora legítima não girar em laço apertado.
    segurando = espera > 0 and _yt_poll_sem.acquire(blocking=False)
    if espera > 0 and not segurando:
        espera = 0
        time.sleep(0.5)
    try:
        limite = time.monotonic() + espera
        with _yt_cond:
            if since > _yt_ultimo_id:  # servidor reiniciou (contador voltou a zero)
                since = 0
            while True:
                pendentes = [c for c in _yt_comandos if c["id"] > since]
                restante = limite - time.monotonic()
                if pendentes or restante <= 0:
                    break
                _yt_cond.wait(timeout=restante)
            ultimo = _yt_ultimo_id
    finally:
        if segurando:
            _yt_poll_sem.release()
    return jsonify({"ok": True, "commands": pendentes, "last_id": ultimo})


def _id_obrigatorio(valor: Any, nome: str, regex: re.Pattern[str]) -> str:
    """Como _id_opcional, mas None também é inválido."""
    if not isinstance(valor, str) or not regex.fullmatch(valor):
        raise ErroApi(f"Campo '{nome}' inválido.")
    return valor


def _id_opcional(valor: Any, nome: str, regex: re.Pattern[str]) -> str | None:
    """Id do YouTube validado por regex, ou None. Qualquer outra coisa -> 400."""
    if valor is None:
        return None
    if not isinstance(valor, str) or not regex.fullmatch(valor):
        raise ErroApi(f"Campo '{nome}' inválido.")
    return valor


@app.post("/api/youtube/state")
def youtube_state_push() -> Response:
    # Heartbeat legítimo: ~1 a cada 3 s (+ mudanças de estado). Teto folgado anti-abuso.
    if excedeu_limite("ytstate:" + (request.remote_addr or "?"), 60, 10.0):
        raise ErroApi("Muitas atualizações seguidas.", 429)
    body = corpo_json()
    novo: dict[str, Any] = {}
    if "now_playing" in body:
        novo["now_playing"] = validar_str(body["now_playing"], "now_playing", 300, strip=False)
    if "is_playing" in body:
        if not isinstance(body["is_playing"], bool):
            raise ErroApi("Campo 'is_playing' inválido.")
        novo["is_playing"] = body["is_playing"]
    if "volume" in body:
        novo["volume"] = validar_int(body["volume"], "volume", 0, 100)
    if "video_id" in body:
        novo["video_id"] = _id_opcional(body["video_id"], "video_id", _RE_VIDEO_ID)
    if "list_id" in body:
        novo["list_id"] = _id_opcional(body["list_id"], "list_id", _RE_LIST_ID)
    if "playlist_ids" in body:
        ids = body["playlist_ids"]
        if ids is None:
            ids = []
        if not isinstance(ids, list) or len(ids) > YOUTUBE_PLAYLIST_MAX:
            raise ErroApi("Campo 'playlist_ids' inválido.")
        novo["playlist_ids"] = [
            _id_obrigatorio(i, "playlist_ids", _RE_VIDEO_ID) for i in ids
        ]
    if "playlist_index" in body:
        pi = body["playlist_index"]
        novo["playlist_index"] = (
            None if pi is None
            else validar_int(pi, "playlist_index", 0, YOUTUBE_PLAYLIST_MAX - 1)
        )
    with _yt_estado_lock:
        _yt_estado.update(novo)
        _yt_estado["last_heartbeat"] = time.monotonic()
    return jsonify({"ok": True})


@app.get("/api/youtube/state")
def youtube_state_get() -> Response:
    with _yt_estado_lock:
        beat = _yt_estado["last_heartbeat"]
        vivo = beat is not None and (time.monotonic() - beat) < YOUTUBE_PLAYER_TIMEOUT
        snap = {k: _yt_estado[k] for k in (
            "now_playing", "is_playing", "volume", "video_id", "list_id",
            "playlist_ids", "playlist_index",
        )}
    # Fila (com títulos já resolvidos) e versão lidas juntas, de forma consistente.
    with _yt_fila_lock:
        itens = list(_yt_fila)
        versao = _yt_fila_versao
    fila = [_item_publico(i) for i in itens]
    for i in itens:  # itens sem título em cache: resolve em segundo plano
        _pedir_titulo(_item_url(i))

    # Próximas da playlist em reprodução (até 15 a partir do índice atual).
    playlist = None
    if not vivo:  # tocador offline: não mostra (nem busca título de) dados velhos
        snap["video_id"] = None
        snap["list_id"] = None
        snap["playlist_ids"] = []
    ids, idx = snap["playlist_ids"], snap["playlist_index"]
    if vivo and ids and idx is not None and idx < len(ids):
        proximos = ids[idx + 1: idx + 1 + YOUTUBE_PROXIMAS_MAX]
        playlist = {
            "index": idx,
            "items": [
                {"video_id": v,
                 "title": _titulo_em_cache(_yt_url_canonica(v, None))[1]}
                for v in proximos
            ],
        }
        for v in proximos:
            _pedir_titulo(_yt_url_canonica(v, None))
    return jsonify(
        {
            "now_playing": snap["now_playing"],
            "is_playing": snap["is_playing"],
            "volume": snap["volume"],
            "player_alive": vivo,
            "video_id": snap["video_id"],
            "list_id": snap["list_id"],
            "queue": fila,
            "queue_version": versao,
            "playlist": playlist,
        }
    )


# ---------------------------------------------------------------------------
# Rotas principais
# ---------------------------------------------------------------------------
@app.get("/")
def home() -> Response:
    return send_from_directory(STATIC_DIR, "index.html", max_age=0)


@app.get("/api/status")
def status() -> Response:
    return jsonify({"internet": tem_internet()})


@app.get("/api/voices")
def voices() -> Response:
    vozes = obter_vozes_offline()  # cacheado após a 1ª chamada
    if vozes is None:
        return jsonify({"ok": False, "error": "Motor de voz offline indisponível.",
                        "voices": [], "default": None})
    return jsonify({"ok": True, "voices": vozes["voices"], "default": vozes["default"]})


_anuncio_lock = threading.Lock()  # um anúncio por vez (não bloqueante -> 409)


@contextmanager
def musica_abaixada(spotify: bool = True, youtube: bool = True) -> Iterator[None]:
    """
    Abaixa Spotify + YouTube durante o bloco e SEMPRE restaura ao sair. `spotify` e
    `youtube` (som.abaixar) desligam cada camada; os padrões mantêm o comportamento da v2.2.1.
    """
    volume_anterior = spotify_duck() if spotify else None
    if youtube:
        youtube_enqueue("duck")
    try:
        yield
    finally:
        spotify_restore(volume_anterior)
        if youtube:
            youtube_enqueue("restore")


def _anunciar(texto: str, modo: str, voz_online: str, velocidade: int,
              voz_offline_id: Any, usar_online: bool) -> tuple[str, str | None]:
    """
    Executa o anúncio e devolve (engine_used, warning).
    Online: gera o mp3 PRIMEIRO (música ainda alta), só então abaixa, toca e restaura.
    Offline: abaixa antes da fala. No modo auto, falha online cai para offline.
    """
    aviso: str | None = None
    if usar_online:
        caminho: Path | None = None
        try:
            # Fase 1 — geração (edge-tts/rede): SÓ falhas daqui acionam o fallback.
            try:
                caminho = gerar_audio_online(texto, voz_online, velocidade)
            except Exception:  # noqa: BLE001
                log.exception("Falha no motor online de voz.")
                _invalidar_cache_internet()
                if modo != "auto":
                    raise
                aviso = "O motor online falhou; foi usado o motor offline."
            # Fase 2 — reprodução: falha aqui NÃO repete o anúncio em voz offline
            # (o cliente poderia ouvir duas vezes); sobe como erro 500.
            if caminho is not None:
                with musica_abaixada():
                    executar_no_audio(_tocar_mp3, caminho)  # apaga o mp3 ao terminar
                return "online", None
        finally:
            apagar_audio(caminho)  # no-op se já foi apagado
    voz_id = resolver_voz_offline(voz_offline_id)
    with musica_abaixada():
        executar_no_audio(_falar_offline, texto, VELOCIDADE_OFFLINE_WPM, voz_id)
    return ("offline_fallback" if aviso else "offline"), aviso


@app.post("/api/announce")
def announce() -> Response | tuple[Response, int]:
    data = corpo_json()
    texto = validar_str(data.get("text"), "text", 500, min_len=1)
    modo = data.get("engine", "auto")
    if not isinstance(modo, str) or modo not in MOTORES:
        raise ErroApi("Campo 'engine' inválido.")
    chave_voz = data.get("voice", "francisca")
    if not isinstance(chave_voz, str) or chave_voz not in VOZES_ONLINE:
        raise ErroApi("Campo 'voice' inválido.")
    velocidade = validar_int(data.get("speed", 0), "speed", -50, 50, limitar=True)

    # Não enfileira: se já há um anúncio tocando, responde 409 imediatamente.
    if not _anuncio_lock.acquire(blocking=False):
        return _resp_erro("Já existe um anúncio tocando. Aguarde terminar.", 409)
    try:
        # v2.3: com "saida_anuncio" configurada o anúncio vai para a saída escolhida.
        # Sem a chave "som" (ou sem saída) o fluxo abaixo é o da v2.2.1, sem mudança.
        som = SOM.config_som()
        if not SOM.motor_ok:   # contaminado (executor trocado com o pygame preso): vale com ou sem "som"
            raise ErroSom("O áudio do Proclamare travou. Reinicie o Proclamare.", 503, "motor_contaminado")
        internet_ok = tem_internet()
        if modo == "online" and not internet_ok:
            return _resp_erro("Sem internet — não é possível usar o motor online.", 503)
        usar_online = internet_ok and modo != "offline"
        log.info("Anúncio: %d caracteres, motor=%s, online=%s", len(texto), modo, usar_online)
        extra: dict[str, Any] | None = None
        try:
            if som is not None and som["saida_anuncio"] is not None:
                engine_usado, aviso, extra = _anunciar_na_saida(
                    texto, modo, VOZES_ONLINE[chave_voz], velocidade,
                    data.get("offline_voice_id"), usar_online, som,
                )
            else:
                engine_usado, aviso = _anunciar(
                    texto, modo, VOZES_ONLINE[chave_voz], velocidade,
                    data.get("offline_voice_id"), usar_online,
                )
        except ErroSom:
            raise  # 503 com "codigo" (sem saída, em uso exclusivo, motor contaminado)
        except Exception:  # noqa: BLE001
            log.exception("Falha ao reproduzir o anúncio.")
            return _resp_erro("Não foi possível reproduzir o anúncio.", 500)
        limpar_audios_antigos()  # raramente faz algo (a cada ~1 h)
        resposta: dict[str, Any] = {"ok": True, "engine_used": engine_usado}
        if aviso:
            resposta["warning"] = aviso
        if extra:
            resposta.update(extra)
        return jsonify(resposta)
    finally:
        _anuncio_lock.release()


# ---------------------------------------------------------------------------
# Saídas de som (contrato v2.3, F1 — parte que LÊ)
# Todas as rotas /api/som/* exigem PIN para remotos (_exigir_sessao) e NENHUMA
# está em API_PUBLICAS. As respostas vêm de som/modelo.py (visões com lista
# branca): nunca devolvem endpoint_id, MAC, host, UUID nem segredos.
# Os handlers só leem o cache do SomService; a varredura roda em outra thread.
# ---------------------------------------------------------------------------
MSG_SOMENTE_LOCAL = "Disponível só no PC do Proclamare"
DESCOBRIR_MAX_POR_MIN = 6  # por IP


class ErroSom(ErroApi):
    """Erro de /api/som/*: além de "error" leva "erro" (contrato v2.3) e, se útil, "codigo"."""

    def __init__(self, mensagem: str, status: int = 400, codigo: str | None = None) -> None:
        super().__init__(mensagem, status)
        self.codigo = codigo


def _resp_erro_som(mensagem: str, status: int, codigo: str | None = None) -> tuple[Response, int]:
    corpo: dict[str, Any] = {"ok": False, "error": mensagem, "erro": mensagem}
    if codigo:
        corpo["codigo"] = codigo
    return jsonify(corpo), status


@app.errorhandler(ErroSom)
def _tratar_erro_som(e: ErroSom) -> tuple[Response, int]:
    return _resp_erro_som(e.mensagem, e.status, e.codigo)


@app.errorhandler(som_pkg.ErroEsquema)
def _tratar_erro_esquema(e: som_pkg.ErroEsquema) -> tuple[Response, int]:
    # A mensagem é montada por nós (nunca repete texto do cliente).
    return _resp_erro_som(str(e) or "Requisição inválida.", 400)


def somente_local(func: Callable[..., Any]) -> Callable[..., Any]:
    """
    Decorador (ponto único nº 1 da regra "somente local"): a rota inteira só atende o
    PC do Proclamare, MESMO com PIN (403). O critério é _requisicao_local(); com
    PROCLAMARE_CONFIAR_LOOPBACK=0 ninguém é local e a rota fica indisponível para todos.
    """
    @functools.wraps(func)
    def envoltorio(*args: Any, **kwargs: Any) -> Any:
        if not _requisicao_local():
            return _resp_erro_som(MSG_SOMENTE_LOCAL, 403)
        return func(*args, **kwargs)

    return envoltorio


def campos_somente_local(corpo_validado: dict, alvo: Any = None) -> None:
    """
    Ponto único nº 2: chamada DEPOIS de validar o esquema estrito e ANTES de qualquer
    efeito. Se o corpo traz campos que só valem no PC do Proclamare (aprovar/usar BT,
    rede, HDMI ou fone; se_indisponivel=padrao_windows; sessoes_locais; rede; microfone)
    e a requisição não é local, responde 403. `alvo`: Saida da rota /saida (ou lista de
    saídas nas zonas, F3). A regra em si mora em som_modelo.campos_locais_exigidos.
    """
    if som_modelo.campos_locais_exigidos(corpo_validado, alvo) and not _requisicao_local():
        raise ErroSom(MSG_SOMENTE_LOCAL, 403)


def _aparelhos_spotify_para_som() -> list[dict] | None:
    """Fonte da descoberta "spotify": aparelhos Connect da conta (None = sem token/sem rede)."""
    dados, status = spotify_api("GET", "/me/player/devices")
    if status != 200:
        return None
    return [
        {"id": d.get("id"), "name": d.get("name"), "type": d.get("type"),
         "volume_percent": d.get("volume_percent"), "is_restricted": bool(d.get("is_restricted"))}
        for d in (dados.get("devices") or []) if isinstance(d, dict) and d.get("id")
    ]


# O serviço é criado já, mas só liga a varredura em main() (SOM.iniciar()).
# Armazém = o MESMO _config_lock/load_config/_gravar_config_atomico do servidor,
# para o read-modify-write da chave "som" ser atômico com o resto do config.
SOM = som_pkg.SomService(
    armazem=som_pkg.Armazem(lock=_config_lock, carregar=load_config, salvar=save_config),
    fonte_spotify=_aparelhos_spotify_para_som,
    motor=som_pkg.MotorAudio(teto_reproducao_s=TIMEOUT_REPRODUCAO_S),
)


@app.get("/api/som/estado")
def som_estado() -> Response:
    """Estado para o tile (poll de 10 s). Só lê o cache."""
    return jsonify(SOM.estado_publico(anuncio_em_andamento=_anuncio_lock.locked()))


@app.get("/api/som/saidas")
def som_saidas() -> Response:
    """Lista de saídas (poll de 1 s só com o assistente aberto). Só lê o cache + config."""
    return jsonify(SOM.saidas_publicas(remoto=not _requisicao_local()))


@app.post("/api/som/descobrir")
def som_descobrir() -> tuple[Response, int]:
    """Dispara a varredura em segundo plano (202). Ordem: 400, 403, 503, 429, 409."""
    tipos = som_modelo.validar_descobrir(corpo_json())  # 400: tipo fora da lista ou "rede" (F5)
    campos_somente_local({"tipos": tipos})
    if not SOM.disponivel:
        raise ErroSom("Saídas de som indisponíveis neste computador.", 503, "indisponivel")
    ip = request.remote_addr or "?"
    if excedeu_limite(f"descobrir:{ip}", DESCOBRIR_MAX_POR_MIN, 60.0):
        resp, status = _resp_erro_som("Muitas buscas seguidas. Aguarde um minuto.", 429)
        resp.headers["Retry-After"] = "10"
        return resp, status
    try:
        varredura = SOM.iniciar_varredura(tipos)
    except som_pkg.VarreduraEmAndamento:
        raise ErroSom("Já existe uma busca em andamento.", 409, "varredura_em_andamento") from None
    except som_pkg.SomIndisponivel:
        raise ErroSom("Saídas de som indisponíveis neste computador.", 503, "indisponivel") from None
    log.info("Busca de saídas de som iniciada (%s).", ",".join(tipos))
    return jsonify({"ok": True, "varredura": varredura}), 202


# ---------------------------------------------------------------------------
# Saídas de som — reprodução e rotas que ESCREVEM (contrato v2.3, F1 etapa 2)
# O áudio roda SEMPRE na thread de áudio (executar_no_audio). A saída é resolvida e
# ABERTA antes de abaixar a música; o finally de musica_abaixada() restaura sempre.
# ---------------------------------------------------------------------------
auditoria = logging.getLogger("proclamare.som.auditoria")  # quem mudou o quê (sem segredo, sem ref)
TESTAR_MAX_POR_MIN = 3             # por IP
TESTAR_GLOBAL_MAX = 10             # na loja toda...
TESTAR_GLOBAL_JANELA_S = 600.0     # ...a cada 10 minutos
ESCRITA_SOM_MAX_POR_MIN = 30       # POST /saida e /preferencias, por IP
SOM_ABRIR_TIMEOUT_S = 30.0         # teto para resolver + abrir o dispositivo


def _id_sessao() -> str:
    """
    Identificador da sessão do cliente para `testes_recentes` ("a MESMA sessão"). O PC da
    loja é "local"; aparelho remoto = hash do momento do login (`iat`, que as renovações
    do cookie preservam) + IP. Nunca o cookie. Estas rotas já exigem sessão válida.
    """
    if _requisicao_local():
        return "local"
    carga = _carga_token(request.cookies.get(COOKIE_SESSAO), load_config())
    if carga is None:
        return ""
    base = f"{carga[1]!r}|{request.remote_addr}"
    return "r:" + hashlib.sha256(base.encode("utf-8")).hexdigest()[:16]


def _resp_429_som(mensagem: str, espera_s: int) -> tuple[Response, int]:
    resp, status = _resp_erro_som(mensagem, 429)
    resp.headers["Retry-After"] = str(espera_s)
    return resp, status


def _rotulo_saida(sid: str | None, som: dict) -> str:
    """Apelido (texto humano) ou rótulo genérico do tipo; nunca o nome do sistema."""
    if sid is None:
        return "Padrão do Windows"
    entrada = som["saidas"].get(sid) or {}
    if entrada.get("apelido"):
        return str(entrada["apelido"])
    saida = SOM.obter_saida(sid)
    return som_modelo.rotulo_do_subtipo(saida.subtipo if saida else entrada.get("subtipo"))


def _musica_abaixada_som(som: dict, ativo: bool = True) -> Any:
    """`musica_abaixada()` conforme som.abaixar (spotify_api/youtube); sem abaixar se `ativo` é falso."""
    if not ativo:
        return contextlib.nullcontext()
    return musica_abaixada(spotify=som["abaixar"]["spotify_api"], youtube=som["abaixar"]["youtube"])


# --- funções que rodam NA THREAD DE ÁUDIO ------------------------------------------
def _som_abrir_saida(candidatas: list[Any]) -> Any:
    """(Thread de áudio) Resolve o devicename na hora e abre a 1ª candidata que funcionar."""
    return SOM.motor.abrir_primeira(candidatas, SOM.resolver_devicename, SOM.classificar_falha)


def _som_sintetizar(texto: str, voz_id: str | None, destino: Path) -> Path:
    """(Thread de áudio) Voz offline para WAV (pyttsx3.save_to_file); quem toca é o motor."""
    return SOM.motor.sintetizar(texto, VELOCIDADE_OFFLINE_WPM, voz_id, destino)


def _som_medir(caminhos: list[Path]) -> list[float | None]:
    """(Thread de áudio) Durações dos arquivos, medidas ANTES de abaixar a música (mp3 é decodificado)."""
    return [SOM.motor.medir(c) for c in caminhos]


def _som_tocar(caminhos: list[Path], duracoes: list[float | None] | None = None) -> float:
    """(Thread de áudio) Toca os arquivos em sequência com o vigia de progresso; devolve segundos."""
    motor = SOM.motor
    prazo = motor.agora() + TIMEOUT_AUDIO_S - 5.0   # sempre antes do teto do executor
    medidas = duracoes if duracoes and len(duracoes) == len(caminhos) else [None] * len(caminhos)
    return sum(motor.tocar(c, prazo=prazo, duracao=d) for c, d in zip(caminhos, medidas))


def _abrir_saida_som(candidatas: list[Any]) -> Any:
    """Abre a saída ANTES de abaixar a música; falha vira 503 com "codigo" (nada foi tocado)."""
    try:
        return executar_no_audio(_som_abrir_saida, candidatas, timeout=SOM_ABRIR_TIMEOUT_S)
    except som_pkg.SaidaIndisponivel as exc:
        raise ErroSom(exc.mensagem, 503, exc.codigo) from None
    except TimeoutError:
        codigo = "ausente" if SOM.motor_ok else "motor_contaminado"
        raise ErroSom("A saída de som não respondeu.", 503, codigo) from None


def _anunciar_na_saida(texto: str, modo: str, voz_online: str, velocidade: int,
                       voz_offline_id: Any, usar_online: bool, som: dict) -> tuple[str, str | None, dict]:
    """
    Anúncio na saída configurada (v2.3). Devolve (engine_used, warning, extra da resposta).
    Ordem: gera o áudio (música ainda alta) -> resolve e ABRE a saída -> só então abaixa a
    música -> toca -> restaura (finally). Voz offline: pyttsx3 -> WAV -> o mesmo motor.
    Sem saída utilizável: ErroSom 503 com "codigo" e a música nem chega a ser abaixada.
    """
    aviso: str | None = None
    caminho: Path | None = None
    try:
        if usar_online:
            try:
                caminho = gerar_audio_online(texto, voz_online, velocidade)
            except Exception:  # noqa: BLE001
                log.exception("Falha no motor online de voz.")
                _invalidar_cache_internet()
                if modo != "auto":
                    raise
                aviso = "O motor online falhou; foi usado o motor offline."
        engine = "online" if caminho is not None else ("offline_fallback" if aviso else "offline")
        usada = _abrir_saida_som(SOM.candidatas_anuncio(som))   # ANTES de abaixar a música
        if caminho is None:
            caminho = AUDIO_DIR / f"anuncio_{uuid.uuid4().hex}.wav"
            executar_no_audio(_som_sintetizar, texto, resolver_voz_offline(voz_offline_id), caminho)
        sid_medicao = usada.sid if usada.sid is not None else SOM.padrao_sid()
        duracoes = executar_no_audio(_som_medir, [caminho])   # fora da janela com a música baixa
        with SOM.medir_sinal(sid_medicao) as medida, _musica_abaixada_som(som):
            executar_no_audio(_som_tocar, [caminho], duracoes)
        sinal = medida.resultado
    finally:
        apagar_audio(caminho)  # mp3 ou wav; no-op se já foi
    SOM.registrar_sinal(sinal)
    rotulo = _rotulo_saida(usada.sid, som)
    extra: dict[str, Any] = {
        "saidas_usadas": [{"zona_id": "loja", "apelido": rotulo, "resultado": "tocado"}],
        "parcial": False,
        "sinal_detectado": sinal,
    }
    if usada.sid != som["saida_anuncio"]:
        extra["aviso"] = f"A saída dos anúncios não estava disponível; o anúncio saiu em: {rotulo}."
        log.warning("Anúncio desviado para outra saída aprovada (fallback %s).", som["se_indisponivel"])
    return engine, aviso, extra


# --- POST /api/som/testar -------------------------------------------------------------
def _voz_offline_do_teste() -> str | None:
    """Voz offline padrão (pt-BR se houver) para a frase do teste; None = voz do sistema."""
    try:
        vozes = obter_vozes_offline()
    except Exception:  # noqa: BLE001
        return None
    return vozes["default"] if vozes else None


def _executar_teste(sid: str, corpo: dict, ip: str) -> tuple[Response, int] | Response:
    """(Com _anuncio_lock) Abre a saída, toca bipe + frase e mede o sinal da sessão própria."""
    som = SOM.config_som() or som_modelo.som_padrao()
    if not SOM.motor_ok:
        raise ErroSom("O áudio do Proclamare travou. Reinicie o Proclamare.", 503, "motor_contaminado")
    entrada = som["saidas"].get(sid) or {}
    estado = som_modelo.estado_da_saida(SOM.obter_saida(sid))
    if estado == "em_uso_exclusivo":
        raise ErroSom("Outro programa está usando esta saída em modo exclusivo.", 503, "em_uso_exclusivo")
    if estado in ("ausente", "desconectada", "nao_suportada"):
        raise ErroSom("Esta saída não está disponível agora.", 503, "ausente")
    abaixar = corpo.get("abaixar_musica")
    if abaixar is None:
        abaixar = som["teste"]["abaixar_musica_local" if _requisicao_local() else "abaixar_musica_remoto"]
    frase = frase_de_teste(entrada.get("apelido"), SOM.numero_da_saida(sid))
    arquivos: list[Path] = []
    try:
        _abrir_saida_som([som_pkg.Candidata(sid, entrada.get("apelido"))])
        bipe = AUDIO_DIR / f"anuncio_{uuid.uuid4().hex}.wav"
        arquivos.append(bipe)
        gerar_bipe(bipe)
        tocar: list[Path] = [bipe]
        fala = AUDIO_DIR / f"anuncio_{uuid.uuid4().hex}.wav"
        arquivos.append(fala)
        try:
            executar_no_audio(_som_sintetizar, frase, _voz_offline_do_teste(), fala)
            tocar.append(fala)
        except TimeoutError:   # síntese presa: o executor foi trocado; não segue tocando
            raise ErroSom("O áudio do Proclamare não respondeu.", 503,
                          "ausente" if SOM.motor_ok else "motor_contaminado") from None
        except Exception:  # noqa: BLE001 - sem voz offline: o bipe sozinho já testa a saída
            log.warning("Voz offline indisponível no teste; tocando só o bipe.")
        with SOM.medir_sinal(sid) as medida, _musica_abaixada_som(som, bool(abaixar)):
            segundos = executar_no_audio(_som_tocar, tocar)
    except som_pkg.ErroReproducao as exc:
        raise ErroSom(str(exc), 503, "ausente") from None
    except som_pkg.SaidaIndisponivel as exc:   # ex.: motor contaminado no meio do teste
        raise ErroSom(exc.mensagem, 503, exc.codigo) from None
    finally:
        for arquivo in arquivos:
            apagar_audio(arquivo)
        # O teste NUNCA muda por onde os anúncios saem: se a saída testada não é a dos anúncios
        # (ou não há saída de anúncio), solta o dispositivo para o fluxo antigo/outra saída
        # não herdarem o mixer aberto no HDMI/fone/BT testado.
        if sid != som.get("saida_anuncio") and SOM.motor_em_uso and SOM.motor_ok:
            try:
                executar_no_audio(SOM.motor.fechar, timeout=10.0)
            except Exception:  # noqa: BLE001
                log.warning("Não foi possível soltar a saída testada.", exc_info=True)
    SOM.registrar_teste(_id_sessao(), sid)
    auditoria.info("acao=testar ip=%s sid=%s sinal=%s", ip, sid, medida.resultado)
    return jsonify({"ok": True, "duracao_ms": int(round(segundos * 1000)), "sinal_detectado": medida.resultado})


@app.post("/api/som/testar")
def som_testar() -> tuple[Response, int] | Response:
    """
    Bipe + "Teste do Proclamare: <apelido ou 'saída N'>" numa saída. Permitido a remotos
    com PIN, de propósito. Ordem: 400, 404, 503 (indisponível), 429, 409, 503 (saída).
    """
    corpo = som_modelo.validar_testar(corpo_json())               # 400
    sid = corpo["sid"]
    if not SOM.sid_conhecido(sid):
        raise ErroSom("Saída não encontrada.", 404)               # 404: só vale sid que já existe
    saida = SOM.obter_saida(sid)
    if saida is not None and saida.tipo == "spotify_connect":
        raise ErroSom("Aparelhos do Spotify não recebem teste de anúncio.", 400)
    if not SOM.disponivel:
        raise ErroSom("Saídas de som indisponíveis neste computador.", 503, "indisponivel")
    ip = request.remote_addr or "?"
    if (excedeu_limite(f"testar:{ip}", TESTAR_MAX_POR_MIN, 60.0)
            or excedeu_limite("testar:global", TESTAR_GLOBAL_MAX, TESTAR_GLOBAL_JANELA_S)):
        return _resp_429_som("Muitos testes seguidos. Aguarde um pouco.", 20)
    if not _anuncio_lock.acquire(blocking=False):                 # 409: anúncio ou outro teste
        raise ErroSom("Já existe um anúncio ou teste tocando. Aguarde terminar.", 409)
    try:
        return _executar_teste(sid, corpo, ip)
    finally:
        _anuncio_lock.release()


# --- POST /api/som/saida --------------------------------------------------------------
def _fechar_mixer_som() -> None:
    """A saída dos anúncios foi esquecida: solta o dispositivo aberto pelo motor."""
    try:
        executar_no_audio(SOM.motor.fechar, timeout=10.0)
    except Exception:  # noqa: BLE001
        log.warning("Não foi possível fechar o mixer ao esquecer a saída.", exc_info=True)


def _adotar_nova_ref(som: dict, sid_novo: str, nova: Any, origem: str, agora: str) -> None:
    """
    `aceitar_nova_ref`: a saída NOVA (sid_novo, já descoberta) assume o lugar da saída
    aprovada que sumiu (a dos anúncios, ou a única candidata do mesmo tipo). A referência
    antiga some do config; apelido e perfil do rack passam para a nova, que precisa ser
    testada de novo (confirmada_em volta a nulo). Nunca é automático.
    """
    if nova is None or nova.estado_dispositivo != "disponivel":
        raise ErroSom("A saída nova não está disponível agora.", 400, "ausente")
    if sid_novo in som["saidas"]:
        raise ErroSom("Esta saída já está cadastrada.", 400)
    cache = SOM.instantaneo()
    candidatos = [
        s for s, e in som["saidas"].items()
        if e.get("aprovada") and e.get("tipo") == nova.tipo and e.get("subtipo") == nova.subtipo
        and (cache.get(s) is None or cache[s].estado_dispositivo == "ausente")
    ]
    if som["saida_anuncio"] in candidatos:
        velho = som["saida_anuncio"]
    elif len(candidatos) == 1:
        velho = candidatos[0]
    else:
        raise ErroSom("Não há uma saída aprovada ausente para substituir.", 400, "nada_a_adotar")
    antiga = som["saidas"].pop(velho)
    entrada = som_modelo.entrada_de_saida(nova, agora)
    entrada.update(apelido=antiga.get("apelido"), rack=antiga.get("rack") or entrada["rack"],
                   aprovada=True, aprovada_por=origem)
    som["saidas"][sid_novo] = entrada
    if som["saida_anuncio"] == velho:
        som["saida_anuncio"] = sid_novo
        som["padrao_referencia"] = SOM.padrao_sid()


@app.post("/api/som/saida")
def som_saida() -> tuple[Response, int] | Response:
    """
    Nomear, aprovar, confirmar, esquecer ou usar uma saída para os anúncios. Não toca som.
    Ordem: 400, 404, 403 (campos só locais), 429, 412 (teste recente), 409 (anúncio
    tocando), efeito. Troca REMOTA do destino: teste + "ouviu" da mesma sessão, auditoria e alerta.
    """
    corpo = som_modelo.validar_saida(corpo_json())                # 400
    sid = corpo["sid"]
    ip = request.remote_addr or "?"
    saida = SOM.obter_saida(sid)
    som_atual = SOM.config_som()
    entrada_atual = ((som_atual or {}).get("saidas") or {}).get(sid)
    if saida is None and entrada_atual is None:
        raise ErroSom("Saída não encontrada.", 404)               # 404: só vale sid que já existe
    if saida is not None and saida.tipo == "spotify_connect":
        raise ErroSom("Aparelhos do Spotify não são saídas de anúncio.", 400)
    alvo = saida if saida is not None else entrada_atual
    campos_somente_local(corpo, alvo)                             # 403, antes de qualquer efeito
    local = _requisicao_local()
    anuncio_atual = (som_atual or {}).get("saida_anuncio")
    if corpo.get("esquecer") and not local and (som_modelo.alvo_exige_local(alvo) or sid == anuncio_atual):
        raise ErroSom(MSG_SOMENTE_LOCAL, 403)
    if excedeu_limite(f"escrita-som:{ip}", ESCRITA_SOM_MAX_POR_MIN, 60.0):
        return _resp_429_som("Muitas alterações seguidas. Aguarde um minuto.", 10)
    sessao = _id_sessao()
    recente, ouviu = SOM.teste_recente(sessao, sid)
    if corpo.get("confirmada") and not recente:
        raise ErroSom("Teste esta saída e confirme que ouviu antes de usá-la.", 412, "teste_necessario")
    troca = bool(corpo.get("usar_para_anuncios") or corpo.get("aceitar_nova_ref"))
    if not local and (troca or corpo.get("aprovada") is True) and not (
            recente and (ouviu or corpo.get("confirmada"))):
        raise ErroSom("Teste esta saída e confirme que ouviu antes de usá-la.", 412, "teste_necessario")
    muda_destino = troca or (sid == anuncio_atual and (bool(corpo.get("esquecer")) or corpo.get("aprovada") is False))
    if muda_destino and not _anuncio_lock.acquire(blocking=False):
        raise ErroSom("Já existe um anúncio tocando. Aguarde terminar para trocar a saída.", 409)
    origem = "local" if local else "remoto"
    agora = datetime.now().astimezone().isoformat(timespec="seconds")

    def mutador(som: dict) -> None:
        saidas = som["saidas"]
        if corpo.get("esquecer"):
            saidas.pop(sid, None)
            if som["saida_anuncio"] == sid:
                som["saida_anuncio"] = None
            return
        if corpo.get("aceitar_nova_ref"):
            _adotar_nova_ref(som, sid, saida, origem, agora)
        entrada = saidas.get(sid)
        if entrada is None:
            if saida is None:
                raise ErroSom("Saída não encontrada.", 404)
            if len(saidas) >= som_modelo.MAX_SAIDAS:
                raise ErroSom("Limite de saídas guardadas atingido.", 400)
            entrada = saidas[sid] = som_modelo.entrada_de_saida(saida, agora)
        if "apelido" in corpo:
            entrada["apelido"] = corpo["apelido"]
        if "rack" in corpo:
            entrada["rack"].update(corpo["rack"])
        if "aprovada" in corpo:
            if not corpo["aprovada"] and som["saida_anuncio"] == sid:
                raise ErroSom("Esta é a saída dos anúncios; escolha outra antes de desaprovar.", 400, "saida_em_uso")
            entrada["aprovada"] = corpo["aprovada"]
            entrada["aprovada_por"] = origem if corpo["aprovada"] else None
        if corpo.get("confirmada"):
            entrada["confirmada_em"] = agora
        if corpo.get("usar_para_anuncios"):
            if saida is not None and saida.estado_dispositivo == "nao_suportada":
                raise ErroSom("O Proclamare não consegue tocar nesta saída.", 400, "nao_suportada")
            entrada["aprovada"] = True
            entrada["aprovada_por"] = origem
            som["saida_anuncio"] = sid
            som["padrao_referencia"] = SOM.padrao_sid()

    try:
        SOM.atualizar_config(mutador)
    finally:
        if muda_destino:
            _anuncio_lock.release()
    if corpo.get("confirmada"):
        SOM.marcar_ouviu(sessao, sid)
    if muda_destino:
        SOM.dispositivos_mudaram()                                # recria o stream no próximo uso
        if corpo.get("esquecer") and sid == anuncio_atual and SOM.motor_em_uso:
            _fechar_mixer_som()
    if troca:
        if local:
            SOM.limpar_alerta("saida_alterada_remoto")
        else:
            SOM.definir_alerta("saida_alterada_remoto", f"Saída de anúncio alterada por aparelho remoto (IP {ip}).")
    elif not local and ("aprovada" in corpo or (corpo.get("esquecer") and (entrada_atual or {}).get("aprovada"))):
        # aprovar/desaprovar/esquecer uma saída aprovada mexe nas reservas do fallback: avisa no tile
        SOM.definir_alerta("saida_alterada_remoto", f"Saídas aprovadas alteradas por aparelho remoto (IP {ip}).")
    auditoria.info("acao=saida ip=%s sid=%s origem=%s campos=%s", ip, sid, origem,
                   ",".join(sorted(k for k in corpo if k != "sid")))
    visoes = SOM.saidas_publicas(remoto=not local)["saidas"]
    return jsonify({"ok": True, "saida": next((v for v in visoes if v["sid"] == sid), None)})


# --- POST /api/som/preferencias --------------------------------------------------------
@app.post("/api/som/preferencias")
def som_preferencias() -> tuple[Response, int] | Response:
    """Merge parcial das preferências da F1 (esquema estrito). Ordem: 400, 403, 429, efeito."""
    corpo = som_modelo.validar_preferencias(corpo_json())         # 400 (inclui chaves das fases F2+)
    campos_somente_local(corpo)                                   # 403: se_indisponivel=padrao_windows
    ip = request.remote_addr or "?"
    if excedeu_limite(f"escrita-som:{ip}", ESCRITA_SOM_MAX_POR_MIN, 60.0):
        return _resp_429_som("Muitas alterações seguidas. Aguarde um minuto.", 10)
    som = SOM.atualizar_config(lambda s: som_modelo.aplicar_preferencias(s, corpo))
    if not _requisicao_local() and (
            "se_indisponivel" in corpo or "ativo" in (corpo.get("monitor") or {})):
        # fallback e monitor decidem se uma falha aparece: mudança remota fica visível no tile
        SOM.definir_alerta("saida_alterada_remoto", f"Preferências de saída alteradas por aparelho remoto (IP {ip}).")
    auditoria.info("acao=preferencias ip=%s origem=%s campos=%s", ip,
                   "local" if _requisicao_local() else "remoto", ",".join(sorted(corpo)))
    return jsonify({"ok": True, "preferencias": som_modelo.visao_preferencias(som)})


# ---------------------------------------------------------------------------
# Inicialização
# ---------------------------------------------------------------------------
LOG_FORMATO = "%(asctime)s %(levelname)s [%(threadName)s] %(name)s: %(message)s"
LOG_MAX_BYTES = 5 * 1024 * 1024
LOG_BACKUPS = 3


def _configurar_log() -> None:
    """
    Log no arquivo PROCLAMARE_LOG_FILE (rotativo) quando definido; senão no console.
    O handler vai no logger RAIZ, então cobre também waitress/werkzeug. Com pythonw
    sys.stderr é None: sem arquivo, usamos NullHandler (nada quebra).
    """
    handlers: list[logging.Handler] = []
    erro_arquivo: str | None = None
    caminho = _env("LOG_FILE", "").strip()
    if caminho:
        try:
            destino = Path(caminho)
            destino.parent.mkdir(parents=True, exist_ok=True)
            handlers.append(
                logging.handlers.RotatingFileHandler(
                    destino, maxBytes=LOG_MAX_BYTES, backupCount=LOG_BACKUPS,
                    encoding="utf-8", delay=True,
                )
            )
        except (OSError, ValueError) as exc:
            erro_arquivo = type(exc).__name__
    if not handlers:
        handlers.append(logging.StreamHandler() if sys.stderr is not None else logging.NullHandler())
    for h in handlers:
        h.setFormatter(logging.Formatter(LOG_FORMATO))
    nivel = logging.getLevelName(_env("LOG_LEVEL", "INFO").upper())
    logging.basicConfig(
        level=nivel if isinstance(nivel, int) else logging.INFO, handlers=handlers, force=True
    )
    if erro_arquivo:
        log.warning("Não foi possível abrir PROCLAMARE_LOG_FILE (%s); usando o console.", erro_arquivo)


def _criado_em_filetime() -> int | None:
    """Horário de criação do PRÓPRIO processo (FILETIME: 100 ns desde 1601); só Windows."""
    if os.name != "nt":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.GetCurrentProcess.restype = wintypes.HANDLE
        k32.GetCurrentProcess.argtypes = []
        k32.GetProcessTimes.restype = wintypes.BOOL
        k32.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
        criacao, saida, kernel, usuario = (wintypes.FILETIME() for _ in range(4))
        if not k32.GetProcessTimes(
            k32.GetCurrentProcess(), ctypes.byref(criacao), ctypes.byref(saida),
            ctypes.byref(kernel), ctypes.byref(usuario),
        ):
            return None
        return (criacao.dwHighDateTime << 32) | criacao.dwLowDateTime
    except Exception:  # noqa: BLE001
        return None


def _caminho_pid_file() -> Path | None:
    bruto = _env("PID_FILE", "").strip()
    return Path(bruto) if bruto else None


def _gravar_pid_file(caminho: Path, porta: int) -> bool:
    """Grava o JSON de identificação de forma atômica (temporário + os.replace)."""
    info = {
        "pid": os.getpid(), "porta": porta,
        "criado_em": _criado_em_filetime(), "executavel": sys.executable,
    }
    tmp = caminho.with_name(f"{caminho.name}.{os.getpid()}.tmp")
    try:
        caminho.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(info), encoding="utf-8")
        os.replace(tmp, caminho)
        return True
    except OSError as exc:
        with contextlib.suppress(OSError):
            tmp.unlink(missing_ok=True)
        log.error("Não foi possível gravar PROCLAMARE_PID_FILE (%s).", type(exc).__name__)
        return False


def _remover_pid_file() -> None:
    """Remove o arquivo SOMENTE se ele ainda contém o pid deste processo."""
    caminho = _caminho_pid_file()
    if caminho is None:
        return
    try:
        dados = json.loads(caminho.read_text(encoding="utf-8"))
        if isinstance(dados, dict) and dados.get("pid") == os.getpid():
            caminho.unlink(missing_ok=True)
    except (OSError, ValueError):
        pass  # ausente, ilegível ou de outro processo: não mexe


def _instalar_encerramento() -> None:
    """atexit + sinais (SIGINT/SIGTERM/SIGBREAK): garantem a remoção do pid file."""
    atexit.register(_remover_pid_file)

    def _ao_sinal(_numero: int, _frame: Any) -> None:
        _remover_pid_file()
        raise KeyboardInterrupt  # waitress/werkzeug encerram o laço normalmente

    for nome in ("SIGINT", "SIGTERM", "SIGBREAK"):
        sinal = getattr(signal, nome, None)
        if sinal is not None:
            with contextlib.suppress(ValueError, OSError):  # fora da thread principal etc.
                signal.signal(sinal, _ao_sinal)


def _boas_vindas(linhas: list[str]) -> None:
    """Mensagem de início: no log e, se houver console (não é pythonw), na tela."""
    for linha in linhas:
        log.info(linha)
        if sys.stdout is not None and sys.stderr is not None and not _log_no_console():
            with contextlib.suppress(Exception):
                print(linha)


def _log_no_console() -> bool:
    """True se o log já sai no console (evita mensagem duplicada na tela)."""
    return any(type(h) is logging.StreamHandler for h in logging.getLogger().handlers)


def _criar_socket_escuta(host: str, porta: int) -> socket.socket:
    """
    Cria o socket de escuta já com bind+listen. No Windows usa SO_EXCLUSIVEADDRUSE
    (SO_REUSEADDR permitiria dois servidores na mesma porta, com o 2º roubando
    conexões); nos outros sistemas, SO_REUSEADDR (que lá não permite isso).
    Falha com OSError se a porta estiver em uso.
    """
    familia, tipo, proto, _, endereco = socket.getaddrinfo(
        host, porta, type=socket.SOCK_STREAM, flags=socket.AI_PASSIVE
    )[0]
    sock = socket.socket(familia, tipo, proto)
    try:
        if os.name == "nt":
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        else:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(endereco)
        sock.listen(1024)
    except BaseException:
        sock.close()
        raise
    return sock


def _sondar_porta_livre(host: str, porta: int) -> None:
    """
    Só para o fallback werkzeug (que cria o próprio socket com SO_REUSEADDR): no
    Windows tenta antes um bind exclusivo e falha com OSError se a porta estiver em uso.
    """
    if os.name != "nt":
        return
    familia, tipo, proto, _, endereco = socket.getaddrinfo(
        host, porta, type=socket.SOCK_STREAM, flags=socket.AI_PASSIVE
    )[0]
    with socket.socket(familia, tipo, proto) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        s.bind(endereco)


def main() -> None:
    _configurar_log()
    if _PORTA_INVALIDA:
        log.warning("PROCLAMARE_PORT inválida; usando a porta padrão 8502.")
    host = _env("HOST", "0.0.0.0").strip() or "0.0.0.0"
    limpar_audios_antigos(forcar=True)

    _boas_vindas([
        f"Proclamare rodando — abra http://127.0.0.1:{PORT} neste computador",
        f"ou http://{ip_local()}:{PORT} de outro computador na mesma rede.",
    ])

    try:
        from waitress import create_server
    except ImportError:
        log.warning(
            "waitress não instalado (pip install waitress): usando o servidor de "
            "desenvolvimento do Flask, que não é recomendado para uso contínuo."
        )
        from werkzeug.serving import make_server

        def criar() -> Any:
            _sondar_porta_livre(host, PORT)  # servidor de desenvolvimento: só a sondagem protege
            return make_server(host, PORT, app, threaded=True)

        def rodar(srv: Any) -> None:
            srv.serve_forever()
    else:
        def criar() -> Any:
            # Socket próprio e EXCLUSIVO (sem janela entre a checagem e o bind).
            # threads altas: cada long-poll do YouTube e cada anúncio segura uma thread.
            return create_server(
                app, sockets=[_criar_socket_escuta(host, PORT)], threads=32, channel_timeout=300,
                max_request_body_size=64 * 1024, ident="proclamare",
            )

        def rodar(srv: Any) -> None:
            srv.run()

    # O bind acontece aqui; se falhar, sai com erro SEM tocar no pid file de outro servidor.
    try:
        servidor = criar()
    except OSError as exc:
        log.error("Não foi possível escutar em %r porta %s (%s). A porta já está em uso?",
                  host, PORT, exc.strerror or type(exc).__name__)
        sys.exit(1)

    _instalar_encerramento()
    atexit.register(SOM.parar)
    SOM.iniciar()  # primeira varredura LOCAL em segundo plano (só lê; não toca som)
    caminho_pid = _caminho_pid_file()
    if caminho_pid is not None and not _gravar_pid_file(caminho_pid, PORT):
        log.error("Seguindo sem arquivo de PID (já registrado acima); o lançador pode não "
                  "conseguir desligar este servidor sozinho.")
    try:
        rodar(servidor)
    except KeyboardInterrupt:
        pass
    finally:
        SOM.parar()  # desregistra o callback do Windows (também registrado no atexit)
        _remover_pid_file()


def _processo_vivo(pid: int) -> bool:
    """True se existe um processo com este pid (outro que não o atual)."""
    if pid <= 0 or pid == os.getpid():
        return False
    if os.name == "nt":
        try:
            import ctypes

            k32 = ctypes.WinDLL("kernel32", use_last_error=True)
            k32.OpenProcess.restype = ctypes.c_void_p
            h = k32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
            if not h:
                return False
            try:
                codigo = ctypes.c_ulong()
                return bool(k32.GetExitCodeProcess(ctypes.c_void_p(h), ctypes.byref(codigo))) and codigo.value == 259
            finally:
                k32.CloseHandle(ctypes.c_void_p(h))
        except Exception:  # noqa: BLE001
            return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except OSError:
        return True  # existe, mas sem permissão para sinalizar


def _servidor_no_ar() -> bool:
    """
    True se há um Proclamare rodando: pid file válido (PROCLAMARE_PID_FILE ou
    logs/proclamare.pid) com processo vivo, OU /api/status respondendo em
    127.0.0.1:<porta>. A CLI de PIN não deve gravar o config.json com o servidor no ar.
    """
    for caminho in (_caminho_pid_file(), APP_DIR / "logs" / "proclamare.pid"):
        if caminho is None:
            continue
        try:
            dados = json.loads(caminho.read_text(encoding="utf-8"))
            if isinstance(dados, dict) and isinstance(dados.get("pid"), int) and _processo_vivo(dados["pid"]):
                return True
        except (OSError, ValueError):
            continue
    try:
        with requests.Session() as sessao:
            sessao.trust_env = False  # sem proxy do ambiente: o endereço é sempre local
            r = sessao.get(f"http://127.0.0.1:{PORT}/api/status", timeout=1.5)
            return r.status_code == 200 and isinstance(r.json(), dict) and "internet" in r.json()
    except (requests.RequestException, ValueError):
        return False


def _ler_pin(pergunta: str) -> str:
    """Lê o PIN sem eco no terminal; com stdin redirecionado (script/teste) lê uma linha."""
    if sys.stdin is not None and sys.stdin.isatty():
        return getpass.getpass(pergunta)
    linha = sys.stdin.readline() if sys.stdin is not None else ""
    if not linha:
        raise EOFError
    return linha.strip()


def _cli_pin(remover: bool) -> int:
    """
    CLI: `python server.py --definir-pin` (pede o PIN duas vezes, sem eco) ou
    `--remover-pin`. Funciona sem subir o servidor, com as mesmas funções de
    validação e gravação da API. Um servidor já rodando percebe a mudança sozinho.
    """
    if _servidor_no_ar():
        print("Desligue o Proclamare antes (Menu Iniciar → Desligar Proclamare) ou use as "
              "Configurações no PC da loja.")
        return 1
    try:
        if remover:
            aplicar_pin(None)
            print("PIN removido: o acesso pela rede está desligado e todas as sessões foram encerradas.")
            return 0
        pin = _ler_pin("Novo PIN (6 a 8 números): ")
        validar_pin_novo(pin)
        if _ler_pin("Repita o PIN: ") != pin:
            print("Os dois PINs não são iguais. Nada foi alterado.")
            return 1
        aplicar_pin(pin)
        print("PIN definido. Todos os aparelhos conectados precisarão digitar o PIN de novo.")
        return 0
    except ErroApi as exc:
        print(exc.mensagem)
        return 1
    except (OSError, EOFError, KeyboardInterrupt) as exc:
        print(f"Não foi possível concluir ({type(exc).__name__}).")
        return 1


if __name__ == "__main__":
    _args = sys.argv[1:]
    if _args == ["--definir-pin"] or _args == ["--remover-pin"]:
        sys.exit(_cli_pin(remover=_args[0] == "--remover-pin"))
    if _args:
        print("Uso: python server.py [--definir-pin | --remover-pin]   (sem argumentos: inicia o servidor)")
        sys.exit(2)
    main()
