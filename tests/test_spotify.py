"""
Testes do contrato v2.4.2 (Spotify): erros com causa, reconexão, redirects do OAuth, volume do
anúncio (duck/restore/pendente). A API do Spotify é SEMPRE simulada: `server._http` é trocado por
um falso que só aceita as URLs de accounts.spotify.com e api.spotify.com e NUNCA sai para a rede.
Usa config.json temporário com credenciais FALSAS (nunca o real), não toca som e não abre servidor.
"""
from __future__ import annotations

import json
import logging
import math
import os
import re
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest import mock
from urllib.parse import parse_qs, urlsplit

RAIZ = Path(__file__).resolve().parent.parent
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))
os.environ.setdefault("PROCLAMARE_NO_BROWSER", "1")
os.environ.setdefault("PROCLAMARE_MUSICAS_DIR", os.path.join(tempfile.gettempdir(), "proclamare-testes-musicas"))

import requests  # noqa: E402

import server  # noqa: E402

# Credenciais e tokens FALSOS (32 hexadecimais onde o Spotify exige). Nada disto é real.
CLIENT_ID = "0123456789abcdef0123456789abcdef"
CLIENT_SECRET = "fedcba9876543210fedcba9876543210"
REFRESH_1 = "REFRESH-FALSO-1"
ACCESS_1 = "ACCESS-FALSO-1"
CODE_FALSO = "CODE-FALSO-XYZ"
SEGREDOS = (CLIENT_SECRET, REFRESH_1, ACCESS_1, CODE_FALSO, "ACCESS-FALSO-N", "REFRESH-FALSO-N")
PIN = "481926"
HOST_REMOTO = "http://192.168.0.10:8502"
IP_REMOTO = {"REMOTE_ADDR": "192.168.0.50"}

SPOTIFY_CONFIRMA_PADRAO = server.SPOTIFY_RESTORE_CONFIRMA_ESPERA_S   # antes dos patches dos testes

CENARIOS = {
    "premium": (403, {"error": {"status": 403, "message": "Player command failed: Premium required",
                                "reason": "PREMIUM_REQUIRED"}}, None),
    "premium_sem_reason": (403, {"error": {"status": 403, "message":
                                           "Check settings on developer.spotify.com/dashboard, "
                                           "the user may not be registered."}}, None),
    "sem_dispositivo": (404, {"error": {"status": 404, "message": "Player command failed: No active device found",
                                        "reason": "NO_ACTIVE_DEVICE"}}, None),
    "limite": (429, {}, {"Retry-After": "7"}),
    "cinco_xx": (503, {"error": {"status": 503, "message": "Service unavailable"}}, None),
    "timeout": requests.exceptions.ReadTimeout("lento"),
    "conexao": requests.exceptions.ConnectionError("sem rede"),
}
CODIGO_ESPERADO = {
    "premium": "premium", "premium_sem_reason": "premium", "sem_dispositivo": "sem_dispositivo",
    "limite": "limite", "cinco_xx": "indisponivel", "timeout": "indisponivel", "conexao": "indisponivel",
}


def resposta(status: int, corpo: Any = None, cabecalhos: dict | None = None) -> requests.Response:
    """requests.Response real, montada à mão (sem rede)."""
    r = requests.Response()
    r.status_code = status
    r._content = b"" if corpo is None else json.dumps(corpo).encode("utf-8")
    r.headers.update(cabecalhos or {})
    return r


class SpotifyFalso:
    """Substitui `server._http`: simula accounts.spotify.com e api.spotify.com, com estado."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.api: list[tuple[str, str, dict | None, Any]] = []      # (método, caminho, params, json)
        self.token_posts: list[dict] = []                           # corpo (data) de cada POST de token
        self.token_atraso = 0.0
        self.token_resposta: Any = None      # None = padrão; (status, corpo) | Exception | callable
        self.api_resposta: Any = None        # vale para TODA chamada da API (prioridade máxima)
        self.rotas: dict[tuple[str, str], list[Any]] = {}   # (método, caminho) -> respostas, na ordem
        self.volume = 80
        self.tocando = True
        self.ativo = True
        self.volume_atraso = 0.0
        self.ignorar_volume = 0              # próximos N PUT de volume: 204 sem efeito
        self.contagem_tokens = 0
        self.dispositivos = [{"id": "DEV123", "name": "PC da loja", "type": "Computer", "is_active": True,
                              "is_restricted": False, "supports_volume": True}]
        self.item: dict = {"name": "Musica Teste", "artists": [{"name": "Banda"}], "duration_ms": 200000,
                           "album": {"images": [{"url": "https://i.scdn.co/a.jpg", "width": 300}]}}

    # -- entrega de respostas roteirizadas
    @staticmethod
    def _entregar(r: Any) -> requests.Response:
        if isinstance(r, BaseException):
            raise r
        if callable(r):
            return SpotifyFalso._entregar(r())
        status, corpo, *resto = r
        return resposta(status, corpo, resto[0] if resto else None)

    # -- accounts.spotify.com
    def post(self, url: str, data: dict | None = None, auth: Any = None, timeout: Any = None,
             **_kw: Any) -> requests.Response:
        assert url == server.SPOTIFY_TOKEN_URL, f"POST fora do Spotify simulado: {url}"
        assert auth == (CLIENT_ID, CLIENT_SECRET) or auth is not None
        with self.lock:
            self.token_posts.append(dict(data or {}))
            self.contagem_tokens += 1
            n = self.contagem_tokens
        if self.token_atraso:
            time.sleep(self.token_atraso)
        if self.token_resposta is not None:
            return self._entregar(self.token_resposta)
        if (data or {}).get("grant_type") == "authorization_code":
            return resposta(200, {"access_token": "ACCESS-FALSO-N", "refresh_token": "REFRESH-FALSO-N",
                                  "expires_in": 3600, "token_type": "Bearer"})
        return resposta(200, {"access_token": f"ACCESS-FALSO-N{n}", "expires_in": 3600, "token_type": "Bearer"})

    # -- api.spotify.com
    def request(self, method: str, url: str, params: dict | None = None, json: Any = None,
                headers: dict | None = None, timeout: Any = None, **_kw: Any) -> requests.Response:
        assert url.startswith(server.SPOTIFY_API + "/"), f"chamada fora do Spotify simulado: {url}"
        assert (headers or {}).get("Authorization", "").startswith("Bearer ")
        caminho = url[len(server.SPOTIFY_API):]
        with self.lock:
            self.api.append((method, caminho, dict(params) if params else None, json))
            fila = self.rotas.get((method, caminho))
            roteirizada = fila.pop(0) if fila else None
        if self.api_resposta is not None:
            return self._entregar(self.api_resposta)
        if roteirizada is not None:
            return self._entregar(roteirizada)
        return self._padrao(method, caminho, params or {})

    def _padrao(self, method: str, caminho: str, params: dict) -> requests.Response:
        if (method, caminho) == ("GET", "/me/player"):
            if not self.ativo:
                return resposta(204)
            disp = {**self.dispositivos[0], "volume_percent": self.volume}
            return resposta(200, {"is_playing": self.tocando, "progress_ms": 1000, "device": disp,
                                  "item": self.item})
        if (method, caminho) == ("GET", "/me/player/devices"):
            return resposta(200, {"devices": [{**d, "volume_percent": self.volume} for d in self.dispositivos]})
        if (method, caminho) == ("PUT", "/me/player/volume"):
            if self.volume_atraso:
                time.sleep(self.volume_atraso)
            with self.lock:
                if self.ignorar_volume > 0:
                    self.ignorar_volume -= 1
                else:
                    self.volume = int(params["volume_percent"])
            return resposta(204)
        if (method, caminho) == ("GET", "/search"):
            return resposta(200, {"tracks": {"items": []}, "playlists": {"items": []}})
        return resposta(204)

    # -- consultas
    def chamadas(self, method: str, caminho: str) -> list[tuple]:
        return [c for c in self.api if c[0] == method and c[1] == caminho]


class BaseSpotify(unittest.TestCase):
    """Config temporário com credenciais falsas + Spotify falso + captura de logs e respostas."""

    conectado = True

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._salvo = (server.CONFIG_PATH, server._config_cache, server._config_assinatura)
        server.CONFIG_PATH = Path(self._tmp.name) / "config.json"
        server._config_cache = {}
        server._config_assinatura = None
        with server._taxa_lock:
            server._taxa_hist.clear()
            server._taxa_janela.clear()
        server._login_resetar_global()
        self.zerar_modulo()
        self.f = SpotifyFalso()
        # v2.8.7: o aparelho falso toca em 80%; os testes antigos supõem que o volume normal da loja é 80, então o limite do
        # Spotify (70, ou a liberação do painel) começa liberado em 100 aqui. test_seguranca_som.py testa o 70 de verdade.
        server._definir_liberacao_spotify(100)
        self.addCleanup(server._definir_liberacao_spotify, 70)
        patcher = mock.patch.object(server, "_http", self.f)
        patcher.start()
        self.addCleanup(patcher.stop)
        cfg = {"spotify_client_id": CLIENT_ID, "spotify_client_secret": CLIENT_SECRET,
               "spotify_device_id": "DEV123"}
        if self.conectado:
            cfg.update(spotify_refresh_token=REFRESH_1, spotify_access_token=ACCESS_1,
                       spotify_expires_at=time.time() + 3000)
        server.save_config(cfg)
        self.c = server.app.test_client()
        self.r = server.app.test_client()
        self.respostas: list[str] = []
        # captura de logs do servidor
        self.logs: list[str] = []
        teste = self

        class Captura(logging.Handler):
            def emit(self, rec: logging.LogRecord) -> None:
                teste.logs.append(rec.getMessage() + (rec.exc_text or ""))

        self._h = Captura(level=logging.DEBUG)
        self._logger = logging.getLogger("proclamare")
        self._nivel = self._logger.level
        self._logger.addHandler(self._h)
        self._logger.setLevel(logging.DEBUG)

    def tearDown(self) -> None:
        try:
            self.parar_reaplicacao()   # antes de olhar os logs: as threads de 2º plano também contam
            # nenhum segredo em log nem em resposta (vale para TODOS os testes)
            blob = "\n".join(self.logs + self.respostas)
            for segredo in SEGREDOS:
                self.assertNotIn(segredo, blob, f"segredo vazou: {segredo[:8]}...")
        finally:
            self._logger.removeHandler(self._h)
            self._logger.setLevel(self._nivel)
            server.CONFIG_PATH, server._config_cache, server._config_assinatura = self._salvo
            self.zerar_modulo()
            self._tmp.cleanup()

    @staticmethod
    def zerar_modulo() -> None:
        server._token_falha_ate = 0.0
        server._backoff_ate = 0.0
        server._rebusca_ate = 0.0
        server._critico_ate = 0.0
        server._player_estado.update(data=None, ts=None, erro=None)
        server._teto_spotify.update(proxima=0.0, de=None, ate=0.0)   # v2.8.7
        server._duck_aparelho_definir(None)
        with server._api_resultado_lock:
            server._api_resultado.clear()
        server._busca_cache.clear()
        server._oauth_states.clear()

    def parar_reaplicacao(self) -> None:
        # a restauração em 2º plano de um teste não pode terminar dentro do config do próximo
        for fio in threading.enumerate():
            if fio.name == "spotify-restore":
                fio.join(10)
                self.assertFalse(fio.is_alive(), "a thread spotify-restore não encerrou")
        t = server._reaplic_thread
        if t is not None and t.is_alive():
            server._limpar_volume_pendente()
            server._reaplic_acordar.set()
            t.join(3)
            self.assertFalse(t.is_alive(), "a thread do volume pendente não encerrou")

    def rapido(self) -> None:
        """Reaplicação do volume pendente a cada 50 ms (o padrão dos testes é 30 s: determinístico)."""
        p = mock.patch.object(server, "SPOTIFY_PENDENTE_INTERVALO_S", 0.05)
        p.start()
        self.addCleanup(p.stop)
        server._reaplic_acordar.set()

    # -- ajudantes
    def anotar(self, r: Any) -> Any:
        if r.mimetype == "application/json" or r.status_code in (301, 302, 303, 307):
            self.respostas.append(r.get_data(as_text=True) + str(r.headers.get("Location", "")))
        return r

    def get(self, caminho: str, **kw: Any) -> Any:
        return self.anotar(self.c.get(caminho, **kw))

    def post(self, caminho: str, corpo: dict | None = None, **kw: Any) -> Any:
        return self.anotar(self.c.post(caminho, json=corpo if corpo is not None else {}, **kw))

    def cfg(self) -> dict:
        return server.load_config()

    def expirar_access(self) -> None:
        server.update_config(spotify_expires_at=time.time() - 10)

    def esperar(self, condicao: Any, limite: float = 5.0) -> bool:
        fim = time.monotonic() + limite
        while time.monotonic() < fim:
            if condicao():
                return True
            time.sleep(0.02)
        return bool(condicao())


# ---------------------------------------------------------------------------
# B8 — credenciais
# ---------------------------------------------------------------------------
class TesteConfig(BaseSpotify):
    def test_espacos_e_aspas_sao_removidos(self) -> None:
        novo_id = "a1b2c3d4e5f60718293a4b5c6d7e8f90"
        novo_secret = "90f8e7d6c5b4a3928170f6e5d4c3b2a1"
        r = self.post("/api/spotify/config", {"client_id": f'  "{novo_id}" \n',
                                              "client_secret": f"“{novo_secret.upper()}” "})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.cfg()["spotify_client_id"], novo_id)
        self.assertEqual(self.cfg()["spotify_client_secret"], novo_secret)   # minúsculas, sem aspas

    def test_formato_errado_recusado_com_mensagem(self) -> None:
        ruins = ["curto", CLIENT_ID + "0", CLIENT_ID[:-1] + "g", CLIENT_ID[:10] + " " + CLIENT_ID[11:],
                 "x" * 300, 12345, ["a"], {"a": 1}]
        for ruim in ruins:
            for campo in ("client_id", "client_secret"):
                with self.subTest(campo=campo, valor=str(ruim)[:12]):
                    r = self.post("/api/spotify/config", {campo: ruim})
                    self.assertEqual(r.status_code, 400)
                    j = r.get_json()
                    self.assertFalse(j["ok"])
                    self.assertEqual(j["error"], "Cole só o código (32 letras e números).")
                    self.assertEqual(j["campo"], campo)
        self.assertEqual(self.cfg()["spotify_client_id"], CLIENT_ID)         # nada foi gravado
        self.assertEqual(self.cfg()["spotify_client_secret"], CLIENT_SECRET)

    def test_tudo_ou_nada(self) -> None:
        r = self.post("/api/spotify/config", {"client_id": "a" * 32, "client_secret": "inválido"})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(self.cfg()["spotify_client_id"], CLIENT_ID)

    def test_vazio_mantem_o_atual(self) -> None:
        r = self.post("/api/spotify/config", {"client_id": "  ", "client_secret": ""})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.cfg()["spotify_client_id"], CLIENT_ID)
        self.assertEqual(self.cfg()["spotify_client_secret"], CLIENT_SECRET)

    def test_remoto_nao_configura_e_get_nao_vaza_o_secret(self) -> None:
        server.aplicar_pin(PIN)
        self.r.post("/api/auth/login", json={"pin": PIN}, base_url=HOST_REMOTO, environ_base=IP_REMOTO)
        r = self.r.post("/api/spotify/config", json={"client_id": "b" * 32}, base_url=HOST_REMOTO,
                        environ_base=IP_REMOTO)
        self.assertEqual(r.status_code, 403)
        g = self.get("/api/spotify/config").get_json()
        self.assertEqual(g["client_id"], CLIENT_ID)
        self.assertTrue(g["has_secret"] and g["configured"])
        self.assertNotIn("client_secret", g)

    def test_configured_falso_sem_secret(self) -> None:
        server._remover_chaves_config("spotify_client_secret")
        g = self.get("/api/spotify/config").get_json()
        self.assertFalse(g["configured"])


# ---------------------------------------------------------------------------
# A1 / B6 / B9 — fluxo OAuth
# ---------------------------------------------------------------------------
class TesteOAuth(BaseSpotify):
    conectado = False

    def login(self) -> str:
        r = self.get("/spotify/login")
        self.assertEqual(r.status_code, 302)
        loc = r.headers["Location"]
        self.assertTrue(loc.startswith(server.SPOTIFY_AUTH_URL + "?"), loc)
        return parse_qs(urlsplit(loc).query)["state"][0]

    def destino(self, r: Any) -> str:
        self.assertEqual(r.status_code, 302)
        return r.headers["Location"]

    def test_login_sem_config_vai_para_nocfg(self) -> None:
        server._remover_chaves_config("spotify_client_id")
        self.assertEqual(self.destino(self.get("/spotify/login")), "/?spotify=nocfg")
        server.update_config(spotify_client_id=CLIENT_ID)
        server._remover_chaves_config("spotify_client_secret")
        self.assertEqual(self.destino(self.get("/spotify/login")), "/?spotify=nocfg")

    def test_login_nunca_devolve_texto(self) -> None:
        server._remover_chaves_config("spotify_client_id")
        r = self.get("/spotify/login")
        self.assertNotIn("text/plain", r.headers.get("Content-Type", ""))
        self.assertNotIn(b"Configure", r.get_data())

    def test_login_remoto_vai_para_local(self) -> None:
        # sem PIN definido / sem sessão
        r = self.anotar(self.r.get("/spotify/login", base_url=HOST_REMOTO, environ_base=IP_REMOTO))
        self.assertEqual(self.destino(r), "/?spotify=local")
        # com sessão válida, mas o Redirect URI é de loopback: só vale no PC da loja
        server.aplicar_pin(PIN)
        self.r.post("/api/auth/login", json={"pin": PIN}, base_url=HOST_REMOTO, environ_base=IP_REMOTO)
        r = self.anotar(self.r.get("/spotify/login", base_url=HOST_REMOTO, environ_base=IP_REMOTO))
        self.assertEqual(self.destino(r), "/?spotify=local")

    def test_login_rate_limit(self) -> None:
        for _ in range(5):
            self.login()
        self.assertEqual(self.destino(self.get("/spotify/login")), "/?spotify=rate")

    def test_destinos_so_da_lista_fixa(self) -> None:
        for _ in range(7):
            loc = self.get("/spotify/login").headers["Location"]
            self.assertTrue(loc.startswith(server.SPOTIFY_AUTH_URL) or re.fullmatch(r"/\?spotify=(nocfg|local|rate)", loc))

    def test_callback_state_invalido(self) -> None:
        for estado in ("", "inventado", "x" * 200):
            r = self.get(f"/spotify/callback?state={estado}&code={CODE_FALSO}")
            self.assertEqual(self.destino(r), "/?spotify=error&motivo=state")
        self.assertEqual(self.f.contagem_tokens, 0)

    def test_callback_state_so_vale_uma_vez(self) -> None:
        estado = self.login()
        self.assertEqual(self.destino(self.get(f"/spotify/callback?state={estado}&code={CODE_FALSO}")),
                         "/?spotify=connected")
        self.assertEqual(self.destino(self.get(f"/spotify/callback?state={estado}&code={CODE_FALSO}")),
                         "/?spotify=error&motivo=state")

    def test_callback_negado(self) -> None:
        estado = self.login()
        r = self.get(f"/spotify/callback?state={estado}&error=access_denied")
        self.assertEqual(self.destino(r), "/?spotify=error&motivo=negado")
        self.assertEqual(self.f.contagem_tokens, 0)

    def test_callback_invalid_client(self) -> None:
        self.f.token_resposta = (400, {"error": "invalid_client", "error_description": "Invalid client secret"})
        r = self.get(f"/spotify/callback?state={self.login()}&code={CODE_FALSO}")
        self.assertEqual(self.destino(r), "/?spotify=error&motivo=credenciais")
        self.assertNotIn("spotify_refresh_token", self.cfg())

    def test_callback_invalid_redirect(self) -> None:
        self.f.token_resposta = (400, {"error": "invalid_grant", "error_description": "Invalid redirect URI"})
        r = self.get(f"/spotify/callback?state={self.login()}&code={CODE_FALSO}")
        self.assertEqual(self.destino(r), "/?spotify=error&motivo=redirect")

    def test_callback_code_expirado_vira_state(self) -> None:
        self.f.token_resposta = (400, {"error": "invalid_grant", "error_description": "Invalid authorization code"})
        r = self.get(f"/spotify/callback?state={self.login()}&code={CODE_FALSO}")
        self.assertEqual(self.destino(r), "/?spotify=error&motivo=state")

    def test_callback_rede_e_5xx(self) -> None:
        for falha in (requests.exceptions.ConnectionError("x"), requests.exceptions.ReadTimeout("x"),
                      (503, {"error": "server_error"}), (502, None)):
            with self.subTest(falha=str(falha)[:30]):
                self.f.token_resposta = falha
                r = self.get(f"/spotify/callback?state={self.login()}&code={CODE_FALSO}")
                self.assertEqual(self.destino(r), "/?spotify=error&motivo=rede")
                server._oauth_states.clear()
                with server._taxa_lock:
                    server._taxa_hist.clear()

    def test_callback_200_sem_refresh_token_e_erro(self) -> None:
        self.f.token_resposta = (200, {"access_token": "ACCESS-FALSO-N", "expires_in": 3600})
        r = self.get(f"/spotify/callback?state={self.login()}&code={CODE_FALSO}")
        self.assertTrue(self.destino(r).startswith("/?spotify=error&motivo="))
        self.assertNotIn("spotify_access_token", self.cfg())     # nada foi gravado

    def test_callback_sem_credenciais(self) -> None:
        estado = self.login()
        server._remover_chaves_config("spotify_client_secret")
        r = self.get(f"/spotify/callback?state={estado}&code={CODE_FALSO}")
        self.assertEqual(self.destino(r), "/?spotify=error&motivo=credenciais")

    def test_callback_sucesso_grava_e_limpa_reconectar(self) -> None:
        server.update_config(spotify_auth_erro="reconectar")
        r = self.get(f"/spotify/callback?state={self.login()}&code={CODE_FALSO}")
        self.assertEqual(self.destino(r), "/?spotify=connected")
        cfg = self.cfg()
        self.assertEqual(cfg["spotify_refresh_token"], "REFRESH-FALSO-N")
        self.assertNotIn("spotify_auth_erro", cfg)
        s = self.get("/api/spotify/status").get_json()
        self.assertEqual((s["connected"], s["reason"]), (True, "ok"))
        # a troca usou o redirect_uri fixo
        self.assertEqual(self.f.token_posts[0]["redirect_uri"], server.spotify_redirect_uri())

    def test_motivos_nunca_vem_do_cliente(self) -> None:
        estado = self.login()
        r = self.get(f"/spotify/callback?state={estado}&error=%3Cscript%3E&motivo=hack")
        self.assertEqual(self.destino(r), "/?spotify=error&motivo=negado")


# ---------------------------------------------------------------------------
# A2 / B5 — renovação do token
# ---------------------------------------------------------------------------
class TesteRenovacao(BaseSpotify):
    def test_invalid_grant_descarta_e_para(self) -> None:
        self.f.token_resposta = (400, {"error": "invalid_grant", "error_description": "Refresh token revoked"})
        self.expirar_access()
        s = self.get("/api/spotify/status").get_json()
        self.assertEqual((s["connected"], s["reason"]), (False, "reconectar"))
        cfg = self.cfg()
        for chave in ("spotify_refresh_token", "spotify_access_token", "spotify_expires_at"):
            self.assertNotIn(chave, cfg)
        self.assertEqual(cfg["spotify_auth_erro"], "reconectar")
        self.assertEqual(cfg["spotify_client_id"], CLIENT_ID)    # credenciais ficam
        # sem novas tentativas: nem pelo status, nem pela API, nem direto
        for _ in range(3):
            self.get("/api/spotify/status")
            self.assertIsNone(server.spotify_get_token())
        d = self.get("/api/spotify/devices").get_json()
        self.assertEqual((d["ok"], d["codigo"]), (False, "reconectar"))
        self.assertEqual(self.f.contagem_tokens, 1)
        self.assertEqual(self.f.api, [])                          # nenhuma chamada à API sem token

    def test_invalid_client_tambem_descarta(self) -> None:
        self.f.token_resposta = (401, {"error": "invalid_client"})
        self.expirar_access()
        s = self.get("/api/spotify/status").get_json()
        self.assertEqual((s["connected"], s["reason"]), (False, "reconectar"))
        self.assertNotIn("spotify_refresh_token", self.cfg())

    def test_reautorizacao_concorrente_nao_e_apagada(self) -> None:
        server.update_config(spotify_refresh_token="REFRESH-FALSO-N")   # outro refresh já guardado
        server._descartar_autorizacao(REFRESH_1, "invalid_grant")       # falha do refresh ANTIGO
        self.assertEqual(self.cfg()["spotify_refresh_token"], "REFRESH-FALSO-N")
        self.assertNotIn("spotify_auth_erro", self.cfg())

    def test_falha_de_rede_mantem_token_e_conectado(self) -> None:
        self.f.token_resposta = requests.exceptions.ConnectionError("sem rede")
        self.expirar_access()
        s = self.get("/api/spotify/status").get_json()
        self.assertEqual((s["connected"], s["reason"]), (True, "rede"))
        self.assertEqual(self.cfg()["spotify_refresh_token"], REFRESH_1)
        self.assertNotIn("spotify_auth_erro", self.cfg())
        # backoff: não martela o accounts.spotify.com
        for _ in range(3):
            self.get("/api/spotify/status")
        self.assertEqual(self.f.contagem_tokens, 1)
        d = self.get("/api/spotify/devices").get_json()
        self.assertEqual((d["ok"], d["codigo"]), (False, "indisponivel"))
        # a internet volta: tudo normaliza sem reconectar
        self.f.token_resposta = None
        server._token_falha_ate = 0.0
        s = self.get("/api/spotify/status").get_json()
        self.assertEqual((s["connected"], s["reason"]), (True, "ok"))
        self.assertTrue(self.get("/api/spotify/devices").get_json()["ok"])

    def test_5xx_e_429_no_accounts_nao_descartam(self) -> None:
        for falha in ((503, {"error": "server_error"}), (429, None), (400, {"error": "invalid_request"}),
                      (200, {"semtoken": 1})):
            with self.subTest(falha=falha[0]):
                server._token_falha_ate = 0.0
                self.f.token_resposta = falha
                self.expirar_access()
                s = self.get("/api/spotify/status").get_json()
                self.assertEqual((s["connected"], s["reason"]), (True, "rede"))
                self.assertEqual(self.cfg()["spotify_refresh_token"], REFRESH_1)

    def test_sem_config_sem_token_reasons(self) -> None:
        server._remover_chaves_config("spotify_refresh_token", "spotify_access_token")
        s = self.get("/api/spotify/status").get_json()
        self.assertEqual((s["connected"], s["reason"]), (False, "sem_token"))
        server._remover_chaves_config("spotify_client_secret")
        s = self.get("/api/spotify/status").get_json()
        self.assertEqual((s["connected"], s["reason"]), (False, "sem_config"))
        self.assertEqual(self.f.contagem_tokens, 0)

    def test_status_ok_traz_campos(self) -> None:
        s = self.get("/api/spotify/status").get_json()
        self.assertEqual(s["reason"], "ok")
        self.assertEqual(s["device_id"], "DEV123")
        self.assertIsNone(s["api_ok"])
        self.assertFalse(s["volume_pendente"])
        self.assertNotIn("spotify_refresh_token", json.dumps(s))

    def test_refresh_concorrente_faz_uma_chamada(self) -> None:
        self.f.token_atraso = 0.2
        self.expirar_access()
        resultados: list[Any] = []
        barreira = threading.Barrier(8)

        def uma() -> None:
            barreira.wait()
            resultados.append(server.spotify_get_token())

        ts = [threading.Thread(target=uma) for _ in range(8)]
        [t.start() for t in ts]
        [t.join(10) for t in ts]
        self.assertEqual(self.f.contagem_tokens, 1)
        self.assertEqual(len(set(resultados)), 1)
        self.assertTrue(all(isinstance(x, str) for x in resultados))

    def test_refresh_concorrente_pela_api(self) -> None:
        self.f.token_atraso = 0.2
        self.expirar_access()
        saidas: list[int] = []
        barreira = threading.Barrier(6)

        def uma() -> None:
            barreira.wait()
            saidas.append(server.spotify_api("GET", "/me/player/devices")[1])

        ts = [threading.Thread(target=uma) for _ in range(6)]
        [t.start() for t in ts]
        [t.join(10) for t in ts]
        self.assertEqual(self.f.contagem_tokens, 1)
        self.assertEqual(saidas, [200] * 6)

    def test_401_da_api_renova_e_repete_uma_vez(self) -> None:
        self.f.rotas[("GET", "/me/player/devices")] = [(401, {"error": {"status": 401, "message": "expired"}})]
        d = self.get("/api/spotify/devices").get_json()
        self.assertTrue(d["ok"])
        self.assertEqual(self.f.contagem_tokens, 1)
        self.assertEqual(len(self.f.chamadas("GET", "/me/player/devices")), 2)

    def test_renovacao_respeita_refresh_novo(self) -> None:
        self.f.token_resposta = (200, {"access_token": "ACCESS-FALSO-N", "refresh_token": "REFRESH-FALSO-N",
                                       "expires_in": 3600})
        self.expirar_access()
        self.assertEqual(server.spotify_get_token(), "ACCESS-FALSO-N")
        self.assertEqual(self.cfg()["spotify_refresh_token"], "REFRESH-FALSO-N")


# ---------------------------------------------------------------------------
# A3 / B2 / B3 / B4 / B7 / B9 — erros com causa nos endpoints
# ---------------------------------------------------------------------------
ENDPOINTS = [
    ("get", "/api/spotify/devices", None),
    ("get", "/api/spotify/now-playing", None),
    ("get", "/api/spotify/search?q=teste", None),
    ("post", "/api/spotify/play", {"uri": "spotify:track:abc123"}),
    ("post", "/api/spotify/pause", {}),
    ("post", "/api/spotify/next", {}),
    ("post", "/api/spotify/previous", {}),
    ("post", "/api/spotify/volume", {"value": 30}),
    ("post", "/api/spotify/activate", {"device_id": "DEV123"}),
]


class TesteErrosComCausa(BaseSpotify):
    def chamar(self, metodo: str, caminho: str, corpo: dict | None) -> Any:
        if metodo == "get":
            return self.get(caminho)
        return self.post(caminho, corpo)

    def test_cenarios_em_todos_os_endpoints(self) -> None:
        for nome, cenario in CENARIOS.items():
            for metodo, caminho, corpo in ENDPOINTS:
                with self.subTest(cenario=nome, endpoint=caminho):
                    self.zerar_modulo()
                    server._busca_cache.clear()
                    with server._taxa_lock:
                        server._taxa_hist.clear()
                    self.f.api_resposta = cenario
                    r = self.chamar(metodo, caminho, corpo)
                    self.assertEqual(r.status_code, 200)
                    j = r.get_json()
                    esperado = CODIGO_ESPERADO[nome]
                    if caminho.endswith("/activate"):
                        # o aparelho fica salvo; só a transferência falha, e o aviso diz por quê
                        self.assertTrue(j["ok"])
                        self.assertFalse(j["transferred"])
                        self.assertTrue(j["warning"])
                        trecho = {"premium": "Premium", "premium_sem_reason": "User Management",
                                  "sem_dispositivo": "Abra o Spotify", "limite": "Aguarde",
                                  "cinco_xx": "indisponível", "timeout": "indisponível",
                                  "conexao": "indisponível"}[nome]
                        self.assertIn(trecho, j["warning"])
                        continue
                    self.assertFalse(j["ok"])
                    self.assertEqual(j["codigo"], esperado)
                    self.assertIn(j["codigo"], server.CODIGOS_SPOTIFY)
                    self.assertTrue(j["error"])
                    if esperado == "limite":
                        self.assertRegex(j["error"], r"^Aguarde \d+ s\.$")
                        self.assertTrue(1 <= j["espera_s"] <= 7)
                    if caminho.endswith(("devices", "now-playing", "search?q=teste")):
                        if caminho.endswith("devices"):
                            self.assertEqual(j["devices"], [])
                        if caminho.endswith("search?q=teste"):
                            self.assertEqual((j["tracks"], j["playlists"]), ([], []))

    def test_mensagens_por_causa(self) -> None:
        esperado = {
            "premium": "Premium", "premium_sem_reason": "User Management",
            "sem_dispositivo": "Abra o Spotify no PC da loja e dê play uma vez",
            "cinco_xx": "Spotify indisponível agora", "timeout": "Spotify indisponível agora",
        }
        for nome, trecho in esperado.items():
            with self.subTest(nome):
                self.zerar_modulo()
                self.f.api_resposta = CENARIOS[nome]
                j = self.post("/api/spotify/next").get_json()
                self.assertIn(trecho, j["error"])

    def test_sem_token_reconecte(self) -> None:
        server._remover_chaves_config("spotify_refresh_token", "spotify_access_token")
        for metodo, caminho, corpo in ENDPOINTS[:2] + ENDPOINTS[3:8]:
            with self.subTest(caminho):
                j = self.chamar(metodo, caminho, corpo).get_json()
                self.assertFalse(j["ok"])
                self.assertEqual(j["codigo"], "reconectar")
                self.assertEqual(j["error"], "Reconecte o Spotify.")
        self.assertEqual(self.f.api, [])

    def test_now_playing_204_e_ok_vazio(self) -> None:
        self.f.ativo = False
        j = self.get("/api/spotify/now-playing").get_json()
        self.assertTrue(j["ok"])
        self.assertFalse(j["playing"])
        self.assertIsNone(j["title"])

    def test_now_playing_403_nao_finge_vazio(self) -> None:
        self.f.rotas[("GET", "/me/player")] = [CENARIOS["premium"]]
        j = self.get("/api/spotify/now-playing").get_json()
        self.assertFalse(j["ok"])
        self.assertEqual(j["codigo"], "premium")
        for campo in ("playing", "track", "title", "artists", "image", "progress_ms", "duration_ms",
                      "volume", "device"):
            self.assertIn(campo, j)

    def test_now_playing_normal(self) -> None:
        j = self.get("/api/spotify/now-playing").get_json()
        self.assertTrue(j["ok"] and j["playing"])
        self.assertEqual(j["title"], "Musica Teste")
        self.assertEqual(j["volume"], 80)

    def test_now_playing_podcast_usa_show_name(self) -> None:
        self.f.item = {"name": "Episódio 1", "duration_ms": 1000,
                       "show": {"name": "Meu Podcast", "publisher": "NAO-DEVE-USAR"}, "images": []}
        j = self.get("/api/spotify/now-playing").get_json()
        self.assertEqual(j["artists"], "Meu Podcast")
        self.assertNotIn("NAO-DEVE-USAR", json.dumps(j))

    def test_comandos_ok(self) -> None:
        for caminho, corpo in (("/api/spotify/play", {"uri": "spotify:track:abc123"}),
                               ("/api/spotify/pause", {}), ("/api/spotify/next", {}),
                               ("/api/spotify/previous", {}), ("/api/spotify/volume", {"value": 40})):
            with self.subTest(caminho):
                self.assertEqual(self.post(caminho, corpo).get_json(), {"ok": True})
        self.assertEqual(self.f.volume, 40)

    def test_play_sem_dispositivo_selecionado(self) -> None:
        server._remover_chaves_config("spotify_device_id")
        r = self.post("/api/spotify/play", {"uri": "spotify:track:abc123"})
        self.assertEqual(r.status_code, 400)
        self.assertFalse(r.get_json()["ok"])

    def test_403_de_restricao_nao_vira_premium(self) -> None:
        self.f.api_resposta = (403, {"error": {"status": 403, "message": "Player command failed: Restriction violated",
                                               "reason": "UNKNOWN"}})
        j = self.post("/api/spotify/pause").get_json()
        self.assertEqual(j["codigo"], "indisponivel")
        self.f.api_resposta = (403, {"error": {"status": 403, "reason": "VOLUME_CONTROL_DISALLOW",
                                               "message": "Cannot control device volume"}})
        j = self.post("/api/spotify/volume", {"value": 20}).get_json()
        self.assertEqual(j["codigo"], "indisponivel")
        self.assertIn("volume", j["error"])

    # ---- B4: backoff 429
    def test_pause_ignora_backoff_mas_next_nao(self) -> None:
        server._backoff_ate = time.monotonic() + 60
        antes = len(self.f.api)
        j = self.post("/api/spotify/next").get_json()
        self.assertEqual(j["codigo"], "limite")
        self.assertEqual(len(self.f.api), antes)                 # nem chamou a API
        j = self.post("/api/spotify/pause").get_json()
        self.assertEqual(j, {"ok": True})                         # crítico: passou pelo backoff
        self.assertEqual(len(self.f.chamadas("PUT", "/me/player/pause")), 1)

    def test_retry_after_estranho_nao_quebra(self) -> None:
        for cab in ("nan", "inf", "-5", "abc", "1e999", ""):
            with self.subTest(cab):
                self.zerar_modulo()
                self.f.api_resposta = (429, {}, {"Retry-After": cab})
                j = self.get("/api/spotify/devices").get_json()
                self.assertEqual(j["codigo"], "limite")
                self.assertTrue(math.isfinite(server._backoff_ate))
                self.assertLessEqual(server._backoff_ate - time.monotonic(), server.BACKOFF_MAX_S + 1)

    def test_quota_exceeded_e_logado(self) -> None:
        self.f.api_resposta = (403, {"error": {"status": 403, "reason": "QUOTA_EXCEEDED", "message": "q"}})
        self.get("/api/spotify/devices")
        self.assertTrue(any("QUOTA_EXCEEDED" in m for m in self.logs))

    # ---- api_ok no status
    def test_api_ok_acompanha_o_ultimo_resultado(self) -> None:
        self.assertEqual(self.post("/api/spotify/next").get_json(), {"ok": True})
        s = self.get("/api/spotify/status").get_json()
        self.assertEqual((s["api_ok"], s["api_codigo"]), (True, None))
        self.f.api_resposta = CENARIOS["premium"]
        self.post("/api/spotify/next")
        self.f.api_resposta = None
        # leituras bem-sucedidas NÃO apagam a falha do comando
        self.get("/api/spotify/now-playing")
        s = self.get("/api/spotify/status").get_json()
        self.assertEqual((s["api_ok"], s["api_codigo"]), (False, "premium"))
        self.assertIn("Premium", s["api_error"])
        # um comando que dá certo limpa
        self.post("/api/spotify/next")
        s = self.get("/api/spotify/status").get_json()
        self.assertEqual((s["api_ok"], s["api_codigo"]), (True, None))

    def test_api_ok_expira(self) -> None:
        self.f.api_resposta = CENARIOS["sem_dispositivo"]
        self.get("/api/spotify/now-playing")
        self.assertEqual(self.get("/api/spotify/status").get_json()["api_codigo"], "sem_dispositivo")
        with mock.patch.object(server, "SPOTIFY_API_LEITURA_VALIDADE_S", 0.0):
            time.sleep(0.05)  # > 15 ms: o time.monotonic() do Windows anda de 15,6 em 15,6 ms
            s = self.get("/api/spotify/status").get_json()
        self.assertIsNone(s["api_ok"])

    # ---- B3: activate
    def test_activate_ok(self) -> None:
        j = self.post("/api/spotify/activate", {"device_id": "DEV123"}).get_json()
        self.assertEqual(j, {"ok": True, "transferred": True, "warning": None})
        put = self.f.chamadas("PUT", "/me/player")[0]
        self.assertEqual(put[3], {"device_ids": ["DEV123"], "play": False})
        self.assertEqual(self.cfg()["spotify_device_nome"], "PC da loja")
        self.assertEqual(self.cfg()["spotify_device_tipo"], "Computer")

    def test_activate_valida_id(self) -> None:
        for ruim in ("", "a b", "x" * 200, "id/../x", 5, None):
            with self.subTest(ruim=str(ruim)[:10]):
                self.assertEqual(self.post("/api/spotify/activate", {"device_id": ruim}).status_code, 400)

    def test_activate_transferencia_falha_mas_salva(self) -> None:
        server.update_config(spotify_device_id="OUTRO")
        self.f.rotas[("PUT", "/me/player")] = [CENARIOS["sem_dispositivo"]]
        j = self.post("/api/spotify/activate", {"device_id": "DEV123"}).get_json()
        self.assertTrue(j["ok"])
        self.assertFalse(j["transferred"])
        self.assertIn("Abra o Spotify", j["warning"])
        self.assertEqual(self.cfg()["spotify_device_id"], "DEV123")

    # ---- B7: aparelhos
    def test_devices_expoe_restrito_e_volume(self) -> None:
        self.f.dispositivos = [
            {"id": "DEV123", "name": "PC", "type": "Computer", "is_active": True, "is_restricted": False,
             "supports_volume": True},
            {"id": "TV9", "name": "TV", "type": "TV", "is_active": False, "is_restricted": True,
             "supports_volume": False},
            {"id": "ANT", "name": "Antigo", "type": "Speaker", "is_active": False},   # sem os campos novos
        ]
        j = self.get("/api/spotify/devices").get_json()
        por_id = {d["id"]: d for d in j["devices"]}
        self.assertEqual((por_id["DEV123"]["is_restricted"], por_id["DEV123"]["supports_volume"]), (False, True))
        self.assertEqual((por_id["TV9"]["is_restricted"], por_id["TV9"]["supports_volume"]), (True, False))
        self.assertEqual((por_id["ANT"]["is_restricted"], por_id["ANT"]["supports_volume"]), (False, True))
        self.assertEqual(set(por_id["TV9"]), {"id", "name", "type", "is_active", "volume_percent",
                                              "is_restricted", "supports_volume"})

    def test_activate_restrito_nao_transfere(self) -> None:
        self.f.dispositivos = [{"id": "TV9", "name": "TV", "type": "TV", "is_active": False,
                                "is_restricted": True, "supports_volume": False}]
        j = self.post("/api/spotify/activate", {"device_id": "TV9"}).get_json()
        self.assertTrue(j["ok"])
        self.assertFalse(j["transferred"])
        self.assertTrue(j["warning"])
        self.assertEqual(self.f.chamadas("PUT", "/me/player"), [])

    def test_activate_sem_volume_avisa(self) -> None:
        self.f.dispositivos = [{"id": "SPK1", "name": "Caixa", "type": "Speaker", "is_active": False,
                                "is_restricted": False, "supports_volume": False}]
        j = self.post("/api/spotify/activate", {"device_id": "SPK1"}).get_json()
        self.assertTrue(j["transferred"])
        self.assertIn("volume", j["warning"])

    def test_trocar_de_aparelho_esquece_nome_antigo(self) -> None:
        self.post("/api/spotify/activate", {"device_id": "DEV123"})
        self.f.dispositivos = [{"id": "NOVO1", "name": "Outro", "type": "Speaker", "is_active": False}]
        self.f.api_resposta = CENARIOS["cinco_xx"]    # a releitura também falha: nome não é aprendido
        self.post("/api/spotify/activate", {"device_id": "NOVO1"})
        self.assertNotIn("spotify_device_nome", self.cfg())

    def test_404_device_not_found_reencontra_por_nome(self) -> None:
        self.post("/api/spotify/activate", {"device_id": "DEV123"})       # guarda nome/tipo
        self.f.dispositivos = [{"id": "NOVO456", "name": "PC da loja", "type": "Computer", "is_active": True}]
        self.f.rotas[("PUT", "/me/player/pause")] = [(404, {"error": {"status": 404, "message": "Device not found"}})]
        j = self.post("/api/spotify/pause").get_json()
        self.assertEqual(j, {"ok": True})
        pausas = self.f.chamadas("PUT", "/me/player/pause")
        self.assertEqual([p[2]["device_id"] for p in pausas], ["DEV123", "NOVO456"])
        self.assertEqual(self.cfg()["spotify_device_id"], "NOVO456")

    def test_404_ambiguo_ou_sem_nome_nao_adivinha(self) -> None:
        self.post("/api/spotify/activate", {"device_id": "DEV123"})
        self.f.dispositivos = [{"id": "A1", "name": "PC da loja", "type": "Computer"},
                               {"id": "A2", "name": "PC da loja", "type": "Computer"}]
        self.f.rotas[("PUT", "/me/player/pause")] = [(404, {"error": {"message": "Device not found"}})]
        j = self.post("/api/spotify/pause").get_json()
        self.assertEqual(j["codigo"], "sem_dispositivo")
        self.assertEqual(self.cfg()["spotify_device_id"], "DEV123")
        # e a releitura não é repetida em rajada
        self.f.rotas[("PUT", "/me/player/pause")] = [(404, {"error": {"message": "Device not found"}})]
        n = len(self.f.chamadas("GET", "/me/player/devices"))
        self.post("/api/spotify/pause")
        self.assertEqual(len(self.f.chamadas("GET", "/me/player/devices")), n)

    def test_404_nao_reencontra_dispositivo_restrito(self) -> None:
        self.post("/api/spotify/activate", {"device_id": "DEV123"})
        self.f.dispositivos = [{"id": "R1", "name": "PC da loja", "type": "Computer", "is_restricted": True}]
        self.f.rotas[("PUT", "/me/player/pause")] = [(404, {"error": {"message": "Device not found"}})]
        j = self.post("/api/spotify/pause").get_json()
        self.assertFalse(j["ok"])
        self.assertEqual(self.cfg()["spotify_device_id"], "DEV123")

    def test_classificador_direto(self) -> None:
        c = server.classificar_erro_spotify
        self.assertEqual(c(401, None)[0], "reconectar")
        self.assertEqual(c(0, "rede")[0], "indisponivel")
        self.assertEqual(c(500, None)[0], "indisponivel")
        self.assertEqual(c(400, None)[0], "indisponivel")
        self.assertEqual(c(403, "PREMIUM_REQUIRED")[0], "premium")
        self.assertEqual(c(404, None)[0], "sem_dispositivo")
        self.assertEqual(c(200, "NO_ACTIVE_DEVICE")[0], "sem_dispositivo")
        for status in (0, 400, 401, 403, 404, 429, 500, 502):
            for reason in (None, "UNKNOWN", "rede", "sem_token", "PREMIUM_REQUIRED"):
                self.assertIn(c(status, reason)[0], server.CODIGOS_SPOTIFY)


# ---------------------------------------------------------------------------
# B1 / B4 — duck, restore e volume pendente
# ---------------------------------------------------------------------------
class TesteDuckRestore(BaseSpotify):
    def setUp(self) -> None:
        super().setUp()
        for nome, valor in (("SPOTIFY_RESTORE_PAUSA_S", 0.0), ("SPOTIFY_RESTORE_CONFIRMA_ESPERA_S", 0.0),
                            ("SPOTIFY_PENDENTE_INTERVALO_S", 30.0)):
            p = mock.patch.object(server, nome, valor)
            p.start()
            self.addCleanup(p.stop)
        server._avisos_musica_iniciar()
        self.addCleanup(server._avisos_musica_coletar)

    def avisos(self) -> list[str]:
        return list(server._aviso_musica_tl.avisos or [])

    def volumes_put(self) -> list[int]:
        return [c[2]["volume_percent"] for c in self.f.chamadas("PUT", "/me/player/volume")]

    def test_duck_e_restore_ok(self) -> None:
        anterior = server.spotify_duck()
        self.assertEqual(anterior, 80)
        self.assertEqual(self.f.volume, server.SPOTIFY_DUCK_VOLUME)
        self.assertTrue(server.spotify_restore(anterior))
        self.assertEqual(self.f.volume, 80)
        self.assertEqual(self.avisos(), [])
        self.assertNotIn("spotify_volume_pendente", self.cfg())

    def test_nada_tocando_nao_abaixa(self) -> None:
        self.f.tocando = False
        self.assertIsNone(server.spotify_duck())
        self.assertEqual(self.volumes_put(), [])
        self.assertEqual(self.avisos(), [])

    def test_duck_que_falha_nao_devolve_anterior(self) -> None:
        self.f.rotas[("PUT", "/me/player/volume")] = [CENARIOS["premium"]]
        self.assertIsNone(server.spotify_duck())
        self.assertEqual(self.avisos(), ["musica_nao_abaixada"])
        self.assertEqual(self.f.volume, 80)

    def test_duck_em_backoff_429_avisa(self) -> None:
        server.spotify_player_state(force=True)           # cache com o player tocando
        server._backoff_ate = time.monotonic() + 60
        self.assertIsNone(server.spotify_duck())
        self.assertEqual(self.avisos(), ["musica_nao_abaixada"])
        self.assertEqual(self.f.chamadas("PUT", "/me/player/volume"), [])

    def test_duck_sem_player_ou_dispositivo_nao_avisa(self) -> None:
        self.f.ativo = False
        self.assertIsNone(server.spotify_duck())
        server._remover_chaves_config("spotify_device_id")
        self.assertIsNone(server.spotify_duck())
        self.assertEqual(self.avisos(), [])

    def test_restore_tenta_de_novo_ate_dar_certo(self) -> None:
        anterior = server.spotify_duck()
        self.f.rotas[("PUT", "/me/player/volume")] = [CENARIOS["cinco_xx"], CENARIOS["timeout"]]
        self.assertTrue(server.spotify_restore(anterior))
        self.assertEqual(self.volumes_put(), [12, 80, 80, 80])       # duck + 3 tentativas
        self.assertEqual(self.f.volume, 80)
        self.assertEqual(self.avisos(), [])

    def test_restore_confirma_pelo_estado(self) -> None:
        anterior = server.spotify_duck()
        self.f.ignorar_volume = 1          # o PUT retorna 204, mas o volume não muda: repete uma vez
        self.assertTrue(server.spotify_restore(anterior))
        self.assertEqual(self.volumes_put(), [12, 80, 80])
        self.assertEqual(self.f.volume, 80)

    def test_restore_leitura_defasada_nao_alarma(self) -> None:
        # REV-R2: 2 PUTs aceitos (204) e a leitura do player ainda difere = estado defasado do Spotify
        anterior = server.spotify_duck()
        self.f.ignorar_volume = 9
        self.assertTrue(server.spotify_restore(anterior))
        self.assertEqual(self.volumes_put(), [12, 80, 80])          # não insiste até o teto
        self.assertEqual(self.avisos(), [])                         # e não avisa "não restaurada"
        self.assertNotIn("spotify_volume_pendente", self.cfg())
        self.assertTrue(any("defasado" in m for m in self.logs))

    def test_confirmacao_espera_o_player_refletir(self) -> None:
        self.assertGreaterEqual(SPOTIFY_CONFIRMA_PADRAO, 1.0)       # o padrão (não o patch dos testes)
        anterior = server.spotify_duck()
        self.assertTrue(server.spotify_restore(anterior))
        chamadas = [(c[0], c[1]) for c in self.f.api]
        i = chamadas.index(("PUT", "/me/player/volume"), 2)          # o PUT do restore (depois do duck)
        self.assertEqual(chamadas[i + 1], ("GET", "/me/player"))     # a leitura vem depois do PUT

    def test_restore_que_nunca_confirma_vira_pendente(self) -> None:
        anterior = server.spotify_duck()
        self.f.api_resposta = None
        self.f.rotas[("PUT", "/me/player/volume")] = [CENARIOS["cinco_xx"]] * 10
        self.assertFalse(server.spotify_restore(anterior))
        self.assertEqual(self.volumes_put(), [12] + [80] * server.SPOTIFY_RESTORE_TENTATIVAS)
        self.assertEqual(self.avisos(), ["musica_nao_restaurada"])
        pend = self.cfg()["spotify_volume_pendente"]
        self.assertEqual(pend["volume"], 80)
        self.assertTrue(any("restaurado" in m or "restaurar" in m for m in self.logs))
        self.assertTrue(self.get("/api/spotify/status").get_json()["volume_pendente"])

    def test_restore_ignora_backoff_429(self) -> None:
        anterior = server.spotify_duck()
        server._backoff_ate = time.monotonic() + 60
        self.assertTrue(server.spotify_restore(anterior))
        self.assertEqual(self.f.volume, 80)

    def test_restore_para_cedo_sem_premium(self) -> None:
        self.f.api_resposta = CENARIOS["premium"]
        self.assertFalse(server.spotify_restore(80))
        self.assertEqual(len(self.f.chamadas("PUT", "/me/player/volume")), 1)    # repetir não resolve

    def test_restore_none_nao_faz_nada(self) -> None:
        self.assertTrue(server.spotify_restore(None))
        self.assertEqual(self.f.api, [])

    def test_pendente_reaplicado_depois(self) -> None:
        self.rapido()
        self.f.volume = 12                                   # volume preso no duck
        server._gravar_volume_pendente(80)
        self.assertTrue(self.esperar(lambda: self.f.volume == 80))
        self.assertTrue(self.esperar(lambda: "spotify_volume_pendente" not in self.cfg()))
        self.assertTrue(self.esperar(lambda: server._reaplic_thread is None))   # a thread encerra e se zera

    def test_pendente_nao_mexe_se_o_volume_ja_mudou(self) -> None:
        self.rapido()
        self.f.volume = 55                                   # alguém ajustou à mão
        server._gravar_volume_pendente(80)
        self.assertTrue(self.esperar(lambda: "spotify_volume_pendente" not in self.cfg()))
        self.assertEqual(self.f.volume, 55)
        self.assertEqual(self.f.chamadas("PUT", "/me/player/volume"), [])

    def test_pendente_espera_app_fechado(self) -> None:
        self.rapido()
        self.f.volume = 12
        self.f.ativo = False
        server._gravar_volume_pendente(80)
        time.sleep(0.3)
        self.assertIn("spotify_volume_pendente", self.cfg())
        self.f.ativo = True
        self.assertTrue(self.esperar(lambda: self.f.volume == 80))

    def test_pendente_expirado_e_descartado(self) -> None:
        self.rapido()
        server.update_config(spotify_volume_pendente={"volume": 80, "ts": time.time() - 99999})
        self.assertIsNone(server._volume_pendente())
        server._iniciar_reaplicacao()
        self.assertTrue(self.esperar(lambda: "spotify_volume_pendente" not in self.cfg()))

    def test_pendente_malformado_e_ignorado(self) -> None:
        for lixo in ("x", 5, {"volume": "80", "ts": time.time()}, {"volume": 500, "ts": time.time()},
                     {"volume": True, "ts": time.time()}, {"volume": 80}, {"volume": 80, "ts": "agora"}):
            with self.subTest(lixo=str(lixo)[:30]):
                server.update_config(spotify_volume_pendente=lixo)
                self.assertIsNone(server._volume_pendente())

    def test_duck_do_proximo_anuncio_conserta_volume_preso(self) -> None:
        self.f.volume = 12                                   # preso por um restore que falhou
        server.update_config(spotify_volume_pendente={"volume": 80, "ts": time.time()})
        anterior = server.spotify_duck()
        self.assertEqual(anterior, 80)                       # não "aprende" 12 como o normal
        self.assertTrue(server.spotify_restore(anterior))
        self.assertEqual(self.f.volume, 80)

    def test_duck_descarta_pendente_se_volume_normal(self) -> None:
        server.update_config(spotify_volume_pendente={"volume": 60, "ts": time.time()})
        self.assertEqual(server.spotify_duck(), 80)
        self.assertNotIn("spotify_volume_pendente", self.cfg())

    def test_sem_avisos_fora_de_anuncio(self) -> None:
        server._avisos_musica_coletar()
        self.f.rotas[("PUT", "/me/player/volume")] = [CENARIOS["premium"]]
        self.assertIsNone(server.spotify_duck())             # sem lista ativa: não anota, não quebra
        self.assertEqual(server._avisos_musica_coletar(), [])


class TesteAnuncio(BaseSpotify):
    """POST /api/announce com áudio, voz e YouTube FALSOS (nada toca) e o Spotify simulado."""

    def setUp(self) -> None:
        super().setUp()
        self.eventos: list[Any] = []
        trocas = {
            "SOM": SimpleNamespace(config_som=lambda: None, motor_ok=True),
            "tem_internet": lambda timeout=1.5: False,
            "resolver_voz_offline": lambda voz: None,
            "executar_no_audio": lambda fn, *a, **k: self.eventos.append("fala"),
            "youtube_enqueue": lambda acao, params=None: self.eventos.append(("yt", acao)) or 0,
            "limpar_audios_antigos": lambda *a, **k: None,
            "SPOTIFY_RESTORE_PAUSA_S": 0.0,
            "SPOTIFY_RESTORE_CONFIRMA_ESPERA_S": 0.0,
            "SPOTIFY_PENDENTE_INTERVALO_S": 30.0,
        }
        for nome, valor in trocas.items():
            p = mock.patch.object(server, nome, valor)
            p.start()
            self.addCleanup(p.stop)

    def anunciar(self) -> Any:
        # sem internet (tem_internet falso) a voz da loja "auto" fala offline; "engine" não é mais lido
        return self.post("/api/announce", {"text": "Atenção, clientes"})

    def test_anuncio_normal_sem_aviso(self) -> None:
        j = self.anunciar().get_json()
        self.assertTrue(j["ok"])
        self.assertNotIn("musica_aviso", j)
        self.assertEqual(self.f.volume, 80)
        self.assertIn("fala", self.eventos)
        self.assertEqual([c[2]["volume_percent"] for c in self.f.chamadas("PUT", "/me/player/volume")], [12, 80])

    def test_aviso_musica_nao_abaixada(self) -> None:
        self.f.rotas[("PUT", "/me/player/volume")] = [CENARIOS["premium"]]
        j = self.anunciar().get_json()
        self.assertTrue(j["ok"])                              # o anúncio sai mesmo assim
        self.assertEqual(j["musica_aviso"], "musica_nao_abaixada")
        self.assertTrue(j["musica_aviso_texto"])
        self.assertIn("fala", self.eventos)

    def test_aviso_musica_nao_restaurada_e_pendente(self) -> None:
        # o duck funciona; todas as tentativas do restore falham
        self.f.rotas[("PUT", "/me/player/volume")] = [(200, None)] + [CENARIOS["cinco_xx"]] * 4
        self.f.ignorar_volume = 0
        # a 1ª resposta roteirizada (200 sem corpo) conta como sucesso do duck, mas não muda o volume
        self.f.volume = 80
        j = self.anunciar().get_json()
        self.assertTrue(j["ok"])
        self.assertEqual(j["musica_aviso"], "musica_nao_restaurada")
        self.assertIn("spotify_volume_pendente", self.cfg())
        self.assertEqual(self.get("/api/spotify/status").get_json()["volume_pendente"], True)

    def test_restore_nao_segura_o_anuncio(self) -> None:
        with mock.patch.object(server, "SPOTIFY_RESTORE_ESPERA_MAX_S", 0.4):
            self.f.volume_atraso = 0.0
            # duck normal (rápido); depois cada PUT de restore demora 0.3 s e falha

            def devagar() -> tuple:
                time.sleep(0.3)
                return CENARIOS["cinco_xx"]

            self.f.rotas[("PUT", "/me/player/volume")] = [(204, None)] + [devagar] * 8
            ini = time.monotonic()
            j = self.anunciar().get_json()
            dur = time.monotonic() - ini
        self.assertLess(dur, 1.1)                       # 4 tentativas lentas levariam > 1,2 s
        self.assertTrue(j["ok"])
        self.assertEqual(j["musica_aviso"], "musica_nao_restaurada")
        self.assertIn("spotify_volume_pendente", self.cfg())
        self.assertFalse(server._anuncio_lock.locked())  # o lock foi liberado
        # a thread de fundo termina o que começou e a reaplicação corrige o volume depois
        self.f.volume = 12
        self.f.rotas.clear()
        self.rapido()
        self.assertTrue(self.esperar(lambda: self.f.volume == 80, 8.0))
        self.assertTrue(self.esperar(lambda: "spotify_volume_pendente" not in self.cfg(), 8.0))

    def test_aviso_nao_vaza_para_o_proximo_anuncio(self) -> None:
        self.f.rotas[("PUT", "/me/player/volume")] = [CENARIOS["premium"]]
        self.assertEqual(self.anunciar().get_json()["musica_aviso"], "musica_nao_abaixada")
        self.assertNotIn("musica_aviso", self.anunciar().get_json())

    def test_anuncio_sem_spotify_configurado_nao_avisa(self) -> None:
        server._remover_chaves_config("spotify_device_id", "spotify_refresh_token", "spotify_access_token")
        j = self.anunciar().get_json()
        self.assertTrue(j["ok"])
        self.assertNotIn("musica_aviso", j)
        self.assertEqual(self.f.api, [])


# ---------------------------------------------------------------------------
# Correções da revisão (REV-R1..R9 e SEC-RT-SPOT-01)
# ---------------------------------------------------------------------------
class TesteCorrecoesRevisao(BaseSpotify):
    def setUp(self) -> None:
        super().setUp()
        for nome, valor in (("SPOTIFY_RESTORE_PAUSA_S", 0.0), ("SPOTIFY_RESTORE_CONFIRMA_ESPERA_S", 0.0),
                            ("SPOTIFY_PENDENTE_INTERVALO_S", 30.0)):
            p = mock.patch.object(server, nome, valor)
            p.start()
            self.addCleanup(p.stop)
        server._avisos_musica_iniciar()
        self.addCleanup(server._avisos_musica_coletar)

    def volumes_put(self) -> list[int]:
        return [c[2]["volume_percent"] for c in self.f.chamadas("PUT", "/me/player/volume")]

    # ---- REV-R1: o restore nunca lança nem trava o anúncio
    def test_restore_nao_lanca_se_gravar_pendente_falha(self) -> None:
        anterior = server.spotify_duck()
        self.f.rotas[("PUT", "/me/player/volume")] = [CENARIOS["cinco_xx"]] * 10
        with mock.patch.object(server, "_gravar_volume_pendente", side_effect=OSError("disco cheio")):
            ini = time.monotonic()
            ok = server.spotify_restore(anterior)             # não pode lançar
            dur = time.monotonic() - ini
        self.assertFalse(ok)
        self.assertLess(dur, 3.0)                              # e não espera os 8 s à toa
        self.assertEqual(server._avisos_musica_coletar(), ["musica_nao_restaurada"])
        self.assertTrue(any("volume pendente" in m for m in self.logs))

    def test_restore_nao_lanca_no_ramo_de_timeout(self) -> None:
        anterior = server.spotify_duck()

        def devagar() -> tuple:
            time.sleep(0.3)
            return CENARIOS["cinco_xx"]

        self.f.rotas[("PUT", "/me/player/volume")] = [devagar] * 6
        with mock.patch.object(server, "SPOTIFY_RESTORE_ESPERA_MAX_S", 0.2), \
                mock.patch.object(server, "_gravar_volume_pendente", side_effect=OSError("antivírus")):
            ok = server.spotify_restore(anterior)             # a thread da requisição não pode lançar
        self.assertFalse(ok)

    # ---- REV-R3: falha transitória de comando some rápido; a persistente fica
    def test_api_ok_comando_transitorio_expira_cedo(self) -> None:
        self.f.api_resposta = CENARIOS["cinco_xx"]
        self.post("/api/spotify/next")
        self.f.api_resposta = None
        self.assertEqual(self.get("/api/spotify/status").get_json()["api_codigo"], "indisponivel")
        with mock.patch.object(server, "SPOTIFY_API_COMANDO_TRANSITORIO_S", 0.0):
            time.sleep(0.05)  # idem
            self.assertIsNone(self.get("/api/spotify/status").get_json()["api_ok"])
        self.assertGreaterEqual(server.SPOTIFY_API_COMANDO_VALIDADE_S, 600.0)

    def test_api_ok_comando_persistente_nao_expira_no_transitorio(self) -> None:
        for chave in ("premium", "sem_dispositivo"):
            with self.subTest(chave):
                self.zerar_modulo()
                self.f.api_resposta = CENARIOS[chave]
                self.post("/api/spotify/next")
                self.f.api_resposta = None
                with mock.patch.object(server, "SPOTIFY_API_COMANDO_TRANSITORIO_S", 0.0):
                    time.sleep(0.05)
                    s = self.get("/api/spotify/status").get_json()
                self.assertEqual((s["api_ok"], s["api_codigo"]), (False, CODIGO_ESPERADO[chave]))

    # ---- REV-R5: um anúncio novo cancela o restore de fundo do anterior
    def test_duck_incrementa_a_geracao_e_cancela_o_restore_antigo(self) -> None:
        g0 = server._geracao_restore()
        anterior = server.spotify_duck()
        self.assertGreater(server._geracao_restore(), g0)
        antes = len(self.f.chamadas("PUT", "/me/player/volume"))
        self.assertIsNone(server._restaurar_volume(anterior, gen=g0))      # geração velha: desiste
        self.assertEqual(len(self.f.chamadas("PUT", "/me/player/volume")), antes)
        self.assertEqual(self.f.volume, server.SPOTIFY_DUCK_VOLUME)
        self.assertTrue(server._restaurar_volume(anterior, gen=server._geracao_restore()))
        self.assertEqual(self.f.volume, 80)

    def test_restore_cancelado_nao_mexe_na_pendencia(self) -> None:
        server.update_config(spotify_volume_pendente={"volume": 80, "ts": time.time()})
        with mock.patch.object(server, "_restaurar_volume", return_value=None):
            self.assertTrue(server.spotify_restore(80))
        self.assertIn("spotify_volume_pendente", self.cfg())
        self.assertEqual(server._avisos_musica_coletar(), [])

    # ---- REV-R6: a reaplicação não apaga pendência nova nem deixa de religar
    def test_reaplicacao_nao_apaga_pendencia_nova(self) -> None:
        self.rapido()
        chamadas: list[int] = []

        def reaplicar(volume: int) -> bool:
            chamadas.append(volume)
            if len(chamadas) == 1:     # enquanto tratava a antiga, um restore novo grava outra
                server.update_config(spotify_volume_pendente={"volume": 70, "ts": time.time()})
            return True

        with mock.patch.object(server, "_reaplicar_pendente", side_effect=reaplicar):
            server._gravar_volume_pendente(80)
            self.assertTrue(self.esperar(lambda: len(chamadas) >= 2))      # a nova também foi tratada
            self.assertEqual(chamadas[:2], [80, 70])
            self.assertTrue(self.esperar(lambda: "spotify_volume_pendente" not in self.cfg()))
            self.assertTrue(self.esperar(lambda: server._reaplic_thread is None))

    def test_pendencia_depois_da_saida_da_thread_religa(self) -> None:
        self.rapido()
        with mock.patch.object(server, "_reaplicar_pendente", return_value=True):
            server._gravar_volume_pendente(80)
            self.assertTrue(self.esperar(lambda: server._reaplic_thread is None))
            server._gravar_volume_pendente(75)                  # thread anterior já saiu: tem de religar
            self.assertTrue(self.esperar(lambda: "spotify_volume_pendente" not in self.cfg()))

    # ---- REV-R7: Retry-After do 429
    def test_restore_desiste_se_retry_after_nao_cabe_no_orcamento(self) -> None:
        self.f.rotas[("PUT", "/me/player/volume")] = [(429, {}, {"Retry-After": "60"})]
        self.assertFalse(server._restaurar_volume(80))
        self.assertEqual(len(self.f.chamadas("PUT", "/me/player/volume")), 1)   # não insistiu

    def test_restore_espera_o_retry_after_curto(self) -> None:
        self.f.volume = 12
        self.f.rotas[("PUT", "/me/player/volume")] = [(429, {}, {"Retry-After": "1"})]
        ini = time.monotonic()
        self.assertTrue(server._restaurar_volume(80))
        self.assertGreaterEqual(time.monotonic() - ini, 0.9)                     # esperou o 429 passar
        self.assertEqual(self.volumes_put(), [80, 80])

    def test_reaplicacao_pula_o_ciclo_em_backoff(self) -> None:
        self.f.volume = 12
        server._backoff_ate = time.monotonic() + 60
        self.assertFalse(server._reaplicar_pendente(80))
        self.assertEqual(self.f.api, [])

    # ---- REV-R8: reencontro de aparelho
    def test_devices_aprende_nome_do_aparelho_ja_salvo(self) -> None:
        self.assertNotIn("spotify_device_nome", self.cfg())      # instalação da versão anterior
        self.get("/api/spotify/devices")
        self.assertEqual(self.cfg()["spotify_device_nome"], "PC da loja")
        self.assertEqual(self.cfg()["spotify_device_tipo"], "Computer")
        # e então o 404 reencontra o aparelho pelo nome
        self.f.dispositivos = [{"id": "NOVO456", "name": "PC da loja", "type": "Computer", "is_active": True}]
        self.f.rotas[("PUT", "/me/player/pause")] = [(404, {"error": {"message": "Device not found"}})]
        self.assertEqual(self.post("/api/spotify/pause").get_json(), {"ok": True})
        self.assertEqual(self.cfg()["spotify_device_id"], "NOVO456")

    def test_devices_nao_aprende_de_outro_aparelho(self) -> None:
        self.f.dispositivos = [{"id": "OUTRO", "name": "Caixa", "type": "Speaker", "is_active": True}]
        self.get("/api/spotify/devices")
        self.assertNotIn("spotify_device_nome", self.cfg())

    def test_rebusca_com_429_nao_gasta_a_janela(self) -> None:
        self.post("/api/spotify/activate", {"device_id": "DEV123"})
        self.f.rotas[("PUT", "/me/player/pause")] = [(404, {"error": {"message": "Device not found"}})]
        self.f.rotas[("GET", "/me/player/devices")] = [(429, {}, {"Retry-After": "1"})]
        self.assertFalse(self.post("/api/spotify/pause").get_json()["ok"])
        self.assertEqual(server._rebusca_ate, 0.0)               # a releitura nem aconteceu: sem espera de 20 s

    # ---- REV-R9: aparelho restrito não vira o salvo
    def test_activate_restrito_nao_grava_o_id(self) -> None:
        self.f.dispositivos = [{"id": "TV9", "name": "TV", "type": "TV", "is_active": False,
                                "is_restricted": True, "supports_volume": False}]
        j = self.post("/api/spotify/activate", {"device_id": "TV9"}).get_json()
        self.assertFalse(j["transferred"])
        self.assertEqual(self.cfg()["spotify_device_id"], "DEV123")
        self.assertNotIn("spotify_device_nome", self.cfg())

    # ---- SEC-RT-SPOT-01
    def test_activate_grava_o_config_uma_vez(self) -> None:
        with mock.patch.object(server, "_gravar_config_atomico", wraps=server._gravar_config_atomico) as g:
            self.post("/api/spotify/activate", {"device_id": "DEV123"})
        self.assertEqual(g.call_count, 1)
        self.assertEqual(self.cfg()["spotify_device_nome"], "PC da loja")

    def test_limite_por_ip_nos_comandos(self) -> None:
        rotas = [("/api/spotify/next", {}), ("/api/spotify/previous", {}), ("/api/spotify/pause", {}),
                 ("/api/spotify/volume", {"value": 30}), ("/api/spotify/activate", {"device_id": "DEV123"}),
                 ("/api/spotify/play", {"uri": "spotify:track:abc123"})]
        maximo = server.SPOTIFY_COMANDO_IP_MAX
        for caminho, corpo in rotas:
            with self.subTest(caminho):
                with server._taxa_lock:
                    server._taxa_hist.clear()
                    server._taxa_janela.clear()
                codigos = [self.post(caminho, corpo).status_code for _ in range(maximo + 3)]
                self.assertEqual(codigos[:maximo], [200] * maximo)
                self.assertEqual(codigos[maximo:], [429] * 3)

    def test_bypass_do_pause_em_backoff_e_limitado(self) -> None:
        server._backoff_ate = time.monotonic() + 60
        self.assertEqual(self.post("/api/spotify/pause").get_json(), {"ok": True})        # 1º passa
        j = self.post("/api/spotify/pause").get_json()                                   # 2º logo depois, não
        self.assertEqual(j["codigo"], "limite")
        self.assertEqual(len(self.f.chamadas("PUT", "/me/player/pause")), 1)
        server._critico_ate = 0.0                                                         # passado o intervalo
        self.assertEqual(self.post("/api/spotify/pause").get_json(), {"ok": True})
        self.assertEqual(len(self.f.chamadas("PUT", "/me/player/pause")), 2)

    def test_pause_sem_backoff_nao_e_limitado_pelo_bypass(self) -> None:
        for _ in range(3):
            self.assertEqual(self.post("/api/spotify/pause").get_json(), {"ok": True})
        self.assertEqual(len(self.f.chamadas("PUT", "/me/player/pause")), 3)


if __name__ == "__main__":
    unittest.main()
