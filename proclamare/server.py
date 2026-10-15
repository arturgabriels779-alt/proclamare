"""
Proclamare — servidor local (v2.1, focada em desempenho e segurança)
====================================================================
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

  /api/youtube/*             — o YouTube não tem um protocolo equivalente ao
                               Spotify Connect, então uma aba precisa ficar
                               aberta TOCANDO no computador da loja; esta
                               rota é só um relé de comandos (tocar, pausar,
                               trocar, volume) para essa aba, para que outros
                               computadores também consigam controlá-la.
                               A aba tocadora recebe os comandos por
                               long-poll (latência quase instantânea).

O que mudou na v2.1 (resumo)
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
  PROCLAMARE_SPOTIFY_REDIRECT
                         Redirect URI do Spotify (padrão
                         http://127.0.0.1:<porta>/spotify/callback). O Spotify só
                         aceita http para IP de loopback; fora disso use https://.
  PROCLAMARE_ALLOWED_HOSTS
                         nomes de host extras aceitos no cabeçalho Host, separados
                         por vírgula (defesa contra DNS rebinding). IPs literais,
                         localhost e o nome deste computador já são aceitos.

Veja o README.md para instruções de instalação, execução e acesso remoto.
"""

from __future__ import annotations

import asyncio
import atexit
import contextlib
import ipaddress
import logging
import os
import re
import secrets
import socket
import tempfile
import threading
import time
import uuid
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturoTimeout
from contextlib import contextmanager
from http.cookiejar import DefaultCookiePolicy
from pathlib import Path
from typing import Any, Callable, Iterator
from urllib.parse import urlencode, urlsplit

import json

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from flask import Flask, Response, jsonify, redirect, request, send_from_directory
from werkzeug.exceptions import HTTPException

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


def _porta_do_ambiente() -> int:
    """Lê PROCLAMARE_PORT (1..65535); valor inválido volta ao padrão 8502."""
    bruto = _env("PORT", "8502").strip()
    try:
        porta = int(bruto)
    except ValueError:
        porta = 0
    if not 1 <= porta <= 65535:
        logging.getLogger("proclamare").warning(
            "PROCLAMARE_PORT inválida; usando a porta padrão 8502."
        )
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
    log.exception("Erro não tratado em %s %s", request.method, request.path)
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
_TAXA_MAX_CHAVES = 512


def excedeu_limite(chave: str, maximo: int, janela_s: float) -> bool:
    """Registra uma ocorrência de `chave`; True se passou de `maximo` na janela."""
    agora = time.monotonic()
    with _taxa_lock:
        if len(_taxa_hist) >= _TAXA_MAX_CHAVES:  # evita crescer sem limite
            for k in [k for k, h in _taxa_hist.items() if not h or agora - h[-1] > janela_s]:
                del _taxa_hist[k]
            if len(_taxa_hist) >= _TAXA_MAX_CHAVES:
                _taxa_hist.clear()
        hist = _taxa_hist.setdefault(chave, deque())
        while hist and agora - hist[0] > janela_s:
            hist.popleft()
        if len(hist) >= maximo:
            return True
        hist.append(agora)
        return False


def _requisicao_local() -> bool:
    """True se o cliente está no próprio computador (loopback)."""
    try:
        return ipaddress.ip_address(request.remote_addr or "").is_loopback
    except ValueError:
        return False


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
    return None


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
                    if isinstance(lido, dict):
                        novo = lido
                except (json.JSONDecodeError, OSError, UnicodeDecodeError):
                    log.warning("config.json ilegível; usando configuração vazia.")
            _config_cache = novo
            _config_assinatura = assinatura
        return dict(_config_cache)


def _gravar_config_atomico(cfg: dict) -> None:
    """Grava em arquivo temporário (0600) na mesma pasta e troca com os.replace."""
    global _config_cache, _config_assinatura
    fd, tmp_nome = tempfile.mkstemp(dir=str(CONFIG_PATH.parent), prefix=".config.", suffix=".tmp")
    try:
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

    if reiniciar and pygame.mixer.get_init():
        pygame.mixer.quit()
    if not pygame.mixer.get_init():
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
    Rede de segurança: mantém no máximo MAX_ARQUIVOS_TEMP mp3 na pasta
    (normalmente cada mp3 já é apagado logo após tocar). Roda raramente.
    """
    global _ultima_limpeza
    agora = time.monotonic()
    if not forcar and (agora - _ultima_limpeza) < INTERVALO_LIMPEZA_S:
        return
    _ultima_limpeza = agora
    arquivos: list[tuple[float, Path]] = []
    for p in AUDIO_DIR.glob("anuncio_*.mp3"):
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
        engine.say(texto)
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
    sessao.headers["User-Agent"] = "Proclamare/2.1"
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
    client_id = load_config().get("spotify_client_id")
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
# ---------------------------------------------------------------------------
YOUTUBE_PLAYER_TIMEOUT = 6.0  # segundos sem heartbeat = considera tocador offline
YOUTUBE_WAIT_MAX = 25
YOUTUBE_ACOES_CLIENTE = frozenset({"load", "play", "pause", "next", "prev", "volume"})
YOUTUBE_HOSTS = frozenset(
    {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com", "youtu.be"}
)

_yt_cond = threading.Condition()
YOUTUBE_POLLS_MAX = 4  # long-polls simultâneos (uso normal: 1 aba tocadora)
_yt_poll_sem = threading.BoundedSemaphore(YOUTUBE_POLLS_MAX)
_yt_comandos: deque[dict] = deque(maxlen=50)
_yt_ultimo_id = 0
_yt_estado_lock = threading.Lock()
_yt_estado: dict[str, Any] = {
    "now_playing": "", "is_playing": False, "volume": 70, "last_heartbeat": None,
}


def youtube_enqueue(action: str, params: dict | None = None) -> int:
    """Enfileira um comando para a aba tocadora e acorda os long-polls."""
    global _yt_ultimo_id
    with _yt_cond:
        _yt_ultimo_id += 1
        _yt_comandos.append({"id": _yt_ultimo_id, "action": action, "params": params or {}})
        _yt_cond.notify_all()
        return _yt_ultimo_id


def _validar_url_youtube(valor: Any) -> str:
    url = validar_str(valor, "url", 300, min_len=1)
    if any(ord(c) < 33 or ord(c) == 127 for c in url):
        raise ErroApi("Campo 'url' inválido.")
    try:
        partes = urlsplit(url)
        porta = partes.port
    except ValueError:
        raise ErroApi("Campo 'url' inválido.") from None
    if (
        partes.scheme != "https"
        or (partes.hostname or "") not in YOUTUBE_HOSTS
        or partes.username is not None
        or porta not in (None, 443)
    ):
        raise ErroApi("Somente links https do YouTube são aceitos.")
    return url


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
        params["url"] = _validar_url_youtube(bruto.get("url"))
    elif action == "volume":
        params["value"] = validar_int(bruto.get("value"), "value", 0, 100)
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


@app.post("/api/youtube/state")
def youtube_state_push() -> Response:
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
    with _yt_estado_lock:
        _yt_estado.update(novo)
        _yt_estado["last_heartbeat"] = time.monotonic()
    return jsonify({"ok": True})


@app.get("/api/youtube/state")
def youtube_state_get() -> Response:
    with _yt_estado_lock:
        beat = _yt_estado["last_heartbeat"]
        vivo = beat is not None and (time.monotonic() - beat) < YOUTUBE_PLAYER_TIMEOUT
        return jsonify(
            {
                "now_playing": _yt_estado["now_playing"],
                "is_playing": _yt_estado["is_playing"],
                "volume": _yt_estado["volume"],
                "player_alive": vivo,
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
def musica_abaixada() -> Iterator[None]:
    """Abaixa Spotify + YouTube durante o bloco e SEMPRE restaura ao sair."""
    volume_anterior = spotify_duck()
    youtube_enqueue("duck")
    try:
        yield
    finally:
        spotify_restore(volume_anterior)
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
        internet_ok = tem_internet()
        if modo == "online" and not internet_ok:
            return _resp_erro("Sem internet — não é possível usar o motor online.", 503)
        usar_online = internet_ok and modo != "offline"
        log.info("Anúncio: %d caracteres, motor=%s, online=%s", len(texto), modo, usar_online)
        try:
            engine_usado, aviso = _anunciar(
                texto, modo, VOZES_ONLINE[chave_voz], velocidade,
                data.get("offline_voice_id"), usar_online,
            )
        except Exception:  # noqa: BLE001
            log.exception("Falha ao reproduzir o anúncio.")
            return _resp_erro("Não foi possível reproduzir o anúncio.", 500)
        limpar_audios_antigos()  # raramente faz algo (a cada ~1 h)
        resposta: dict[str, Any] = {"ok": True, "engine_used": engine_usado}
        if aviso:
            resposta["warning"] = aviso
        return jsonify(resposta)
    finally:
        _anuncio_lock.release()


# ---------------------------------------------------------------------------
# Inicialização
# ---------------------------------------------------------------------------
def main() -> None:
    logging.basicConfig(
        level=_env("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s [%(threadName)s] %(name)s: %(message)s",
    )
    host = _env("HOST", "0.0.0.0").strip() or "0.0.0.0"
    limpar_audios_antigos(forcar=True)

    # Mensagem de boas-vindas (único print do programa).
    print(f"Proclamare rodando — abra http://127.0.0.1:{PORT} neste computador")
    print(f"ou http://{ip_local()}:{PORT} de outro computador na mesma rede.")

    try:
        from waitress import serve
    except ImportError:
        log.warning(
            "waitress não instalado (pip install waitress): usando o servidor de "
            "desenvolvimento do Flask, que não é recomendado para uso contínuo."
        )
        app.run(host=host, port=PORT, debug=False, threaded=True)
        return
    # threads altas: cada long-poll do YouTube e cada anúncio segura uma thread.
    serve(
        app, host=host, port=PORT, threads=32, channel_timeout=300,
        max_request_body_size=64 * 1024, ident="proclamare",
    )


if __name__ == "__main__":
    main()
