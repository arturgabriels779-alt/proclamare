"""
Testes do contrato v2.5 (Desligar pelo painel): POST /api/sistema/desligar.

  * TesteDesligarRota / TesteDesligarPecas: em processo, sem desligar nada (o agendador é
    trocado por um espião e o servidor "ativo" por um objeto falso).
  * TesteDesligarIntegracao: sobe o servidor REAL (e o lançador Proclamare.pyw) numa pasta
    temporária própria, numa porta livre de 8900 a 8940, chama a rota e confirma que o processo
    termina sozinho, que o arquivo de PID some e que o lançador consegue ligar de novo.
    Nunca usa a porta 8502, nunca lê o config.json do projeto e só encerra processos que ele
    mesmo iniciou (conferidos pelo caminho da pasta temporária).
"""
from __future__ import annotations

import http.client
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

try:
    import psutil
except ImportError:  # pragma: no cover
    psutil = None  # type: ignore[assignment]

RAIZ = Path(__file__).resolve().parent.parent
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))
os.environ.setdefault("PROCLAMARE_NO_BROWSER", "1")
os.environ.setdefault("PROCLAMARE_MUSICAS_DIR", os.path.join(tempfile.gettempdir(), "proclamare-testes-musicas"))

import server  # noqa: E402

PIN = "481926"
HOST_REMOTO = "http://192.168.0.10:8502"
IP_REMOTO = {"REMOTE_ADDR": "192.168.0.50"}
ROTA = "/api/sistema/desligar"
LOG_SERVER = "proclamare"


def _zerar_estado_de_desligamento() -> None:
    """Desfaz o que a rota deixa de propósito (lock de anúncios preso, flags) entre os testes."""
    with server._desligar_lock:
        server._desligamento_agendado = False
    server._desligando.clear()
    server._reaplic_acordar.clear()   # a thread do desligamento o deixa aceso e acordaria cedo demais a reaplicação do Spotify
    if server._anuncio_lock.locked():
        server._anuncio_lock.release()


class BaseRota(unittest.TestCase):
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
        _zerar_estado_de_desligamento()
        self.c = server.app.test_client()
        self.r = server.app.test_client()
        # Espião no lugar da thread: NADA é desligado de verdade nestes testes.
        self.agendar = mock.patch.object(server, "_agendar_desligamento")
        self.espiao = self.agendar.start()
        self.addCleanup(self.agendar.stop)

    def tearDown(self) -> None:
        _zerar_estado_de_desligamento()
        server.CONFIG_PATH, server._config_cache, server._config_assinatura = self._salvo
        self._tmp.cleanup()

    def remoto(self, metodo, caminho, **kw):
        return getattr(self.r, metodo)(caminho, base_url=HOST_REMOTO, environ_base=IP_REMOTO, **kw)


class TesteDesligarRota(BaseRota):
    def test_local_responde_202_e_agenda_uma_vez(self) -> None:
        with self.assertLogs(LOG_SERVER, level="INFO") as cap:
            r = self.c.post(ROTA, json={})
        self.assertEqual(r.status_code, 202)
        self.assertEqual(r.get_json(), {"ok": True})
        self.assertEqual(self.espiao.call_count, 1)
        self.assertIn("Desligado pelo painel (PC local)", "\n".join(cap.output))
        self.assertEqual(r.headers["Cache-Control"], "no-store")

    def test_segundo_pedido_repete_202_sem_agendar_de_novo(self) -> None:
        self.assertEqual(self.c.post(ROTA, json={}).status_code, 202)
        r = self.c.post(ROTA, json={})
        self.assertEqual((r.status_code, r.get_json()), (202, {"ok": True}))
        self.assertEqual(self.espiao.call_count, 1)

    def test_depois_de_aceito_novos_anuncios_dao_409_sem_tocar(self) -> None:
        self.c.post(ROTA, json={})
        r = self.c.post("/api/announce", json={"text": "Teste"})
        self.assertEqual(r.status_code, 409)

    def test_409_com_anuncio_tocando_e_nada_e_desligado(self) -> None:
        self.assertTrue(server._anuncio_lock.acquire(blocking=False))   # "anúncio tocando"
        try:
            r = self.c.post(ROTA, json={})
            self.assertEqual(r.status_code, 409)
            self.assertFalse(r.get_json()["ok"])
            self.assertEqual(r.get_json()["error"], "Espere o anúncio terminar.")
            self.espiao.assert_not_called()
            self.assertFalse(server._desligamento_agendado)
        finally:
            server._anuncio_lock.release()
        # terminou o anúncio: agora desliga
        self.assertEqual(self.c.post(ROTA, json={}).status_code, 202)
        self.assertEqual(self.espiao.call_count, 1)

    def test_remoto_sem_login_nao_desliga(self) -> None:
        server.aplicar_pin(PIN)
        r = self.remoto("post", ROTA, json={}, headers={"Origin": HOST_REMOTO})
        self.assertEqual(r.status_code, 401)
        self.espiao.assert_not_called()
        self.assertFalse(server._desligamento_agendado)

    def test_remoto_sem_pin_configurado_nao_desliga(self) -> None:
        r = self.remoto("post", ROTA, json={}, headers={"Origin": HOST_REMOTO})
        self.assertEqual(r.status_code, 403)
        self.espiao.assert_not_called()

    def test_remoto_com_pin_e_sessao_valida_tambem_403(self) -> None:
        server.aplicar_pin(PIN)
        self.assertEqual(self.remoto("post", "/api/auth/login", json={"pin": PIN}).status_code, 200)
        cab = {"Origin": HOST_REMOTO}
        self.assertEqual(self.remoto("get", "/api/mensagens").status_code, 200)   # a sessão vale...
        r = self.remoto("post", ROTA, json={}, headers=cab)                      # ...mas aqui não
        self.assertEqual(r.status_code, 403)
        self.assertEqual(r.get_json()["error"], server.MSG_SOMENTE_LOCAL)
        self.espiao.assert_not_called()
        self.assertFalse(server._desligamento_agendado)
        self.assertFalse(server._anuncio_lock.locked())

    def test_proxy_com_via_nao_e_local(self) -> None:
        server.aplicar_pin(PIN)
        r = self.c.post(ROTA, json={}, headers={"Via": "1.1 tunel"})
        self.assertEqual(r.status_code, 401)
        self.espiao.assert_not_called()

    def test_qualquer_cabecalho_de_proxy_tira_o_carater_local(self) -> None:
        # Apache mod_proxy & cia: reescrevem o Host para loopback e só acrescentam X-Forwarded-*.
        server.aplicar_pin(PIN)
        for nome in server.CABECALHOS_DE_PROXY:
            with self.subTest(cabecalho=nome):
                r = self.c.post(ROTA, json={}, headers={nome: "1.2.3.4"})
                self.assertIn(r.status_code, (401, 403))
                self.espiao.assert_not_called()
                self.assertFalse(server._desligamento_agendado)
        self.assertEqual(set(server.CABECALHOS_DE_PROXY),
                         {"Via", "Forwarded", "X-Forwarded-For", "X-Forwarded-Host", "X-Real-IP"})

    def test_cabecalho_de_proxy_sem_pin_configurado_tambem_nao_desliga(self) -> None:
        for nome in server.CABECALHOS_DE_PROXY:
            with self.subTest(cabecalho=nome):
                r = self.c.post(ROTA, json={}, headers={nome: "1.2.3.4"})
                self.assertEqual(r.status_code, 403)
        self.espiao.assert_not_called()

    def test_filtro_de_corpo_do_waitress_tambem_olha_os_cabecalhos_de_proxy(self) -> None:
        permitir = server._corpo_grande_permitido
        rota = server.ROTA_ENVIO_MUSICAS
        base = {"HOST": "127.0.0.1"}
        with mock.patch.object(server, "MUSICAS_ENVIO_HABILITADO", True):
            self.assertTrue(permitir("POST", rota, base, "127.0.0.1"))
            for nome in server.CABECALHOS_DE_PROXY:
                chave = nome.upper().replace("-", "_")      # formato do waitress
                with self.subTest(cabecalho=chave):
                    self.assertFalse(permitir("POST", rota, {**base, chave: "1.2.3.4"}, "127.0.0.1"))

    def test_pausa_para_o_tocador_de_outra_aba_ou_aparelho(self) -> None:
        antes = server._yt_ultimo_id
        self.assertEqual(self.c.post(ROTA, json={}).status_code, 202)
        with server._yt_cond:
            novos = [c for c in server._yt_comandos if c["id"] > antes]
        self.assertEqual([c["action"] for c in novos], ["pause"])
        # e o long-poll de uma aba tocadora recebe a pausa mesmo com o desligamento em curso
        server._desligando.set()
        r = self.c.get(f"/api/youtube/commands?since={antes}&wait=5")
        self.assertEqual([c["action"] for c in r.get_json()["commands"]], ["pause"])

    def test_409_com_envio_de_musicas_em_andamento(self) -> None:
        server._envio_iniciar()
        try:
            r = self.c.post(ROTA, json={})
            self.assertEqual(r.status_code, 409)
            self.assertEqual(r.get_json()["error"], "Há músicas sendo enviadas. Espere terminar.")
            self.espiao.assert_not_called()
            self.assertFalse(server._desligamento_agendado)
            self.assertFalse(server._anuncio_lock.locked())
        finally:
            server._envio_terminar()
        self.assertEqual(self.c.post(ROTA, json={}).status_code, 202)

    def test_depois_de_aceito_novos_envios_de_musica_dao_503(self) -> None:
        self.assertEqual(self.c.post(ROTA, json={}).status_code, 202)
        r = self.c.post("/api/musicas/enviar", data={}, content_type="multipart/form-data",
                        headers={"Origin": "http://localhost"})
        self.assertEqual(r.status_code, 503, r.get_data(as_text=True))
        self.assertEqual(server._envios_ativos, 0)

    def test_host_de_rede_vindo_de_loopback_nao_e_local(self) -> None:
        server.aplicar_pin(PIN)
        r = self.c.post(ROTA, json={}, base_url=HOST_REMOTO)       # REMOTE_ADDR 127.0.0.1, Host de rede
        self.assertEqual(r.status_code, 401)
        self.espiao.assert_not_called()

    def test_confiar_loopback_desligado_ninguem_e_local(self) -> None:
        with mock.patch.dict(os.environ, {"PROCLAMARE_CONFIAR_LOOPBACK": "0"}):
            r = self.c.post(ROTA, json={})
        self.assertEqual(r.status_code, 401 if server.pin_configurado() else 403)
        self.espiao.assert_not_called()

    def test_415_nao_json(self) -> None:
        for tipo in ("text/plain", "application/x-www-form-urlencoded", "multipart/form-data"):
            r = self.c.post(ROTA, data="{}", content_type=tipo)
            self.assertEqual(r.status_code, 415, tipo)
        self.espiao.assert_not_called()

    def test_403_origin_de_outro_host(self) -> None:
        r = self.c.post(ROTA, json={}, headers={"Origin": "http://evil.example"})
        self.assertEqual(r.status_code, 403)
        r = self.c.post(ROTA, json={}, headers={"Sec-Fetch-Site": "cross-site"})
        self.assertEqual(r.status_code, 403)
        self.espiao.assert_not_called()

    def test_get_nao_existe(self) -> None:
        self.assertEqual(self.c.get(ROTA).status_code, 405)

    def test_corpo_so_aceita_objeto_vazio(self) -> None:
        for corpo in ({"x": 1}, {"confirmar": True}, [], "x", 5):
            r = self.c.post(ROTA, json=corpo)
            self.assertEqual(r.status_code, 400, corpo)
        r = self.c.post(ROTA, data="{nao e json", content_type="application/json")
        self.assertEqual(r.status_code, 400)
        self.espiao.assert_not_called()
        self.assertFalse(server._desligamento_agendado)

    def test_falha_ao_agendar_desfaz_o_lock(self) -> None:
        self.espiao.side_effect = RuntimeError("sem threads")
        r = self.c.post(ROTA, json={})
        self.assertEqual(r.status_code, 500)
        self.assertNotIn("sem threads", r.get_data(as_text=True))
        self.assertFalse(server._anuncio_lock.locked())
        self.assertFalse(server._desligamento_agendado)

    def test_auth_status_pode_desligar(self) -> None:
        self.assertIs(self.c.get("/api/auth/status").get_json()["pode_desligar"], True)
        d = self.remoto("get", "/api/auth/status").get_json()
        self.assertIs(d["pode_desligar"], False)
        self.assertIs(d["local"], False)
        with mock.patch.dict(os.environ, {"PROCLAMARE_CONFIAR_LOOPBACK": "0"}):
            self.assertIs(self.c.get("/api/auth/status").get_json()["pode_desligar"], False)


class TesteDesligarPecas(unittest.TestCase):
    """A thread do desligamento e o encerramento de serviços, com peças falsas."""

    def setUp(self) -> None:
        _zerar_estado_de_desligamento()
        self.addCleanup(_zerar_estado_de_desligamento)
        self.addCleanup(setattr, server, "_servidor_ativo", server._servidor_ativo)

    def test_sem_servidor_ativo_nao_faz_nada_e_destrava(self) -> None:
        server._servidor_ativo = None
        with server._desligar_lock:
            server._desligamento_agendado = True
        self.assertTrue(server._anuncio_lock.acquire(blocking=False))
        with mock.patch.object(server, "DESLIGAR_ATRASO_S", 0), \
                mock.patch.object(server._thread, "interrupt_main") as intr, \
                mock.patch.object(server, "_vigia_de_saida") as vigia:
            server._desligar_apos_resposta()
        intr.assert_not_called()
        vigia.assert_not_called()
        self.assertFalse(server._desligando.is_set())
        self.assertFalse(server._desligamento_agendado)
        self.assertFalse(server._anuncio_lock.locked())

    def test_thread_interrompe_o_laco_e_acorda_o_select(self) -> None:
        falso = mock.Mock()
        server._servidor_ativo = falso
        with mock.patch.object(server, "DESLIGAR_ATRASO_S", 0), \
                mock.patch.object(server._thread, "interrupt_main") as intr, \
                mock.patch.object(server, "_vigia_de_saida"):
            server._desligar_apos_resposta()
        intr.assert_called_once_with()
        falso.pull_trigger.assert_called_once_with()
        self.assertTrue(server._desligando.is_set())

    def test_servidor_sem_pull_trigger_nao_quebra(self) -> None:
        server._servidor_ativo = object()          # ex.: servidor de desenvolvimento do Flask
        with mock.patch.object(server, "DESLIGAR_ATRASO_S", 0), \
                mock.patch.object(server._thread, "interrupt_main") as intr, \
                mock.patch.object(server, "_vigia_de_saida"):
            server._desligar_apos_resposta()
        intr.assert_called_once_with()

    def test_long_poll_do_youtube_sai_logo_ao_desligar(self) -> None:
        c = server.app.test_client()
        saida: dict = {}

        def poll() -> None:
            t0 = time.monotonic()
            r = c.get(f"/api/youtube/commands?since={server._yt_ultimo_id}&wait=20")
            saida["status"] = r.status_code
            saida["dados"] = r.get_json()
            saida["tempo"] = time.monotonic() - t0

        t = threading.Thread(target=poll)
        t.start()
        time.sleep(0.6)                              # o poll já está preso esperando
        self.assertTrue(t.is_alive())
        server._servidor_ativo = mock.Mock()
        with mock.patch.object(server, "DESLIGAR_ATRASO_S", 0), \
                mock.patch.object(server._thread, "interrupt_main"), \
                mock.patch.object(server, "_vigia_de_saida"):
            server._desligar_apos_resposta()
        t.join(5)
        self.assertFalse(t.is_alive())
        self.assertEqual(saida["status"], 200)
        self.assertEqual(saida["dados"]["commands"], [])
        self.assertLess(saida["tempo"], 5)

    def test_sinal_do_desligamento_do_painel_nao_remove_o_pid_antes_de_fechar_a_porta(self) -> None:
        # O PID só pode sumir depois que a porta fechar (senão o lançador sobe outro servidor e o
        # bind exclusivo falha). Captura o handler que _instalar_encerramento registra.
        capturado: list = []
        with mock.patch.object(server.atexit, "register"),                 mock.patch.object(server.signal, "signal", side_effect=lambda n, h: capturado.append(h)),                 mock.patch.object(server, "_remover_pid_file") as rem:
            server._instalar_encerramento()
            tratador = capturado[0]
            with self.assertRaises(KeyboardInterrupt):      # Ctrl+C/SIGTERM normal: remove na hora
                tratador(2, None)
            self.assertEqual(rem.call_count, 1)
            with server._desligar_lock:
                server._desligamento_agendado = True
            with self.assertRaises(KeyboardInterrupt):      # desligar pelo painel: deixa para o fim
                tratador(2, None)
            self.assertEqual(rem.call_count, 1)

    def test_encerrar_servicos_fecha_porta_para_servicos_e_so_depois_remove_o_pid(self) -> None:
        pasta = tempfile.TemporaryDirectory()
        self.addCleanup(pasta.cleanup)
        pid_file = Path(pasta.name) / "teste.pid"
        pid_file.write_text(json.dumps({"pid": os.getpid()}), encoding="utf-8")
        ordem: list[str] = []
        falso = mock.Mock()
        falso.close.side_effect = lambda: ordem.append("fechar-porta")
        with mock.patch.dict(os.environ, {"PROCLAMARE_PID_FILE": str(pid_file)}), \
                mock.patch.object(server.SOM, "parar", side_effect=lambda: ordem.append("som")), \
                mock.patch.object(server.ACERVO, "parar", side_effect=lambda: ordem.append("acervo")):
            real = server._remover_pid_file

            def remover() -> None:
                ordem.append("pid-ainda-existe" if pid_file.exists() else "pid-ja-sumiu")
                real()

            with mock.patch.object(server, "_remover_pid_file", side_effect=remover):
                server._encerrar_servicos(falso)
        self.assertEqual(ordem, ["fechar-porta", "som", "acervo", "pid-ainda-existe"])
        self.assertFalse(pid_file.exists())

    def test_encerrar_servicos_sobrevive_a_falhas(self) -> None:
        falso = mock.Mock()
        falso.close.side_effect = OSError("boom")
        with mock.patch.object(server.SOM, "parar", side_effect=RuntimeError("x")), \
                mock.patch.object(server.ACERVO, "parar") as acervo, \
                mock.patch.object(server, "_remover_pid_file") as pid:
            server._encerrar_servicos(falso)        # não pode levantar
        acervo.assert_called_once()
        pid.assert_called_once()

    def test_remover_pid_file_nao_apaga_o_de_outro_processo(self) -> None:
        pasta = tempfile.TemporaryDirectory()
        self.addCleanup(pasta.cleanup)
        pid_file = Path(pasta.name) / "outro.pid"
        pid_file.write_text(json.dumps({"pid": os.getpid() + 12345}), encoding="utf-8")
        with mock.patch.dict(os.environ, {"PROCLAMARE_PID_FILE": str(pid_file)}):
            server._remover_pid_file()
        self.assertTrue(pid_file.exists())


# ---------------------------------------------------------------------------------------------
# Integração: servidor real + lançador, numa cópia mínima fora do projeto
# ---------------------------------------------------------------------------------------------
def _porta_livre() -> int | None:
    """Primeira porta livre de 8900 a 8940 (nunca a 8502 do Proclamare da loja)."""
    for porta in range(8900, 8941):
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1", porta))
            except OSError:
                continue
            return porta
    return None


def _http(porta: int, metodo: str, caminho: str, corpo: dict | None = None, timeout: float = 5.0):
    """(status, json) falando com 127.0.0.1:porta (Host de loopback = "local"). None se não conectar."""
    try:
        con = http.client.HTTPConnection("127.0.0.1", porta, timeout=timeout)
        cab = {"Content-Type": "application/json"} if corpo is not None else {}
        con.request(metodo, caminho, body=json.dumps(corpo) if corpo is not None else None, headers=cab)
        r = con.getresponse()
        dados = r.read()
        con.close()
        try:
            return r.status, json.loads(dados.decode("utf-8"))
        except ValueError:
            return r.status, None
    except (OSError, http.client.HTTPException):
        return None


@unittest.skipUnless(psutil is not None, "precisa do psutil para conferir/limpar os processos de teste")
class TesteDesligarIntegracao(unittest.TestCase):
    PRAZO_DESLIGAR_S = 8.0   # "poucos segundos"

    @classmethod
    def setUpClass(cls) -> None:
        cls.porta = _porta_livre()
        if cls.porta is None:
            raise unittest.SkipTest("nenhuma porta livre entre 8900 e 8940")
        cls.base = Path(tempfile.mkdtemp(prefix="proclamare-desligar-"))
        # Cópia mínima: sem config.json, sem .venv, sem logs do projeto.
        for nome in ("server.py", "musicas.py", "waitress_limites.py", "Proclamare.pyw"):
            shutil.copy2(RAIZ / nome, cls.base / nome)
        shutil.copytree(RAIZ / "som", cls.base / "som", ignore=shutil.ignore_patterns("__pycache__"))
        (cls.base / "musicas").mkdir()
        cls.pid_file = cls.base / "teste.pid"
        cls.log_file = cls.base / "teste.log"

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.base, ignore_errors=True)

    def setUp(self) -> None:
        self.env = {k: v for k, v in os.environ.items()
                    if not k.startswith(("PROCLAMARE_", "ANUNCIADOR_"))}
        self.env.update({
            "PROCLAMARE_HOST": "127.0.0.1", "PROCLAMARE_PORT": str(self.porta),
            "PROCLAMARE_MUSICAS_DIR": str(self.base / "musicas"),
            "PROCLAMARE_NO_BROWSER": "1",
            "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8",
        })
        self.pids_iniciados: set[int] = set()
        self.addCleanup(self._limpar)

    # ---- utilidades -----------------------------------------------------------------
    def _meu(self, p: "psutil.Process") -> bool:
        """True só se o processo é um servidor/lançador DESTA pasta temporária."""
        try:
            return str(self.base).lower() in " ".join(p.cmdline()).lower() or \
                str(self.base).lower() in (p.cwd() or "").lower()
        except (psutil.Error, OSError):
            return False

    def _limpar(self) -> None:
        """Encerra o que ESTE teste iniciou (nunca outro processo)."""
        for pid in list(self.pids_iniciados):
            try:
                p = psutil.Process(pid)
                if self._meu(p):
                    for filho in p.children(recursive=True):
                        if self._meu(filho):
                            filho.kill()
                    p.kill()
            except psutil.Error:
                pass

    def _pid_do_arquivo(self, caminho: Path) -> int | None:
        try:
            return int(json.loads(caminho.read_text(encoding="utf-8"))["pid"])
        except (OSError, ValueError, KeyError, TypeError):
            return None

    def _esperar(self, condicao, limite_s: float, passo: float = 0.2) -> bool:
        fim = time.monotonic() + limite_s
        while time.monotonic() < fim:
            if condicao():
                return True
            time.sleep(passo)
        return bool(condicao())

    def _no_ar(self) -> bool:
        r = _http(self.porta, "GET", "/api/status", timeout=2.0)
        return bool(r and r[0] == 200 and isinstance(r[1], dict) and "internet" in r[1])

    def _desligar_e_medir(self, pid_servidor: int) -> float:
        """Chama a rota, espera o processo terminar sozinho e devolve quantos segundos levou."""
        t0 = time.monotonic()
        r = _http(self.porta, "POST", ROTA, {})
        self.assertIsNotNone(r, "o servidor não respondeu à rota de desligar")
        self.assertEqual((r[0], r[1]), (202, {"ok": True}))
        try:
            psutil.Process(pid_servidor).wait(timeout=20)
        except psutil.TimeoutExpired:
            self.fail("o servidor não terminou sozinho em 20 s")
        except psutil.NoSuchProcess:
            pass
        return time.monotonic() - t0

    def _rodar(self, *args: str, timeout: float = 90.0) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, *args], cwd=str(self.base), env=self.env,
                              stdin=subprocess.DEVNULL, capture_output=True, timeout=timeout)

    # ---- servidor direto --------------------------------------------------------------
    def test_desligar_encerra_o_processo_sozinho_e_apaga_o_pid(self) -> None:
        env = dict(self.env, PROCLAMARE_PID_FILE=str(self.pid_file), PROCLAMARE_LOG_FILE=str(self.log_file))
        saida = open(self.base / "saida.txt", "wb")
        self.addCleanup(saida.close)
        proc = subprocess.Popen([sys.executable, "server.py"], cwd=str(self.base), env=env,
                                stdin=subprocess.DEVNULL, stdout=saida, stderr=subprocess.STDOUT)
        self.pids_iniciados.add(proc.pid)
        self.assertTrue(self._esperar(self._no_ar, 60), "o servidor de teste não subiu")
        pid = self._pid_do_arquivo(self.pid_file)
        self.assertIsNotNone(pid, "o servidor não gravou o arquivo de PID")
        self.pids_iniciados.add(pid)
        info = json.loads(self.pid_file.read_text(encoding="utf-8"))
        self.assertEqual(info["porta"], self.porta)

        # Um long-poll do YouTube pendurado durante o desligamento não pode segurar o processo.
        resultado: dict = {}

        def poll() -> None:
            resultado["r"] = _http(self.porta, "GET", "/api/youtube/commands?since=0&wait=25", timeout=40)

        t = threading.Thread(target=poll, daemon=True)
        t.start()
        time.sleep(0.5)

        self.assertIs(_http(self.porta, "GET", "/api/auth/status")[1]["pode_desligar"], True)
        duracao = self._desligar_e_medir(pid)
        self.assertLess(duracao, self.PRAZO_DESLIGAR_S, f"levou {duracao:.1f} s para terminar")
        print(f"[desligar] processo terminou em {duracao:.2f} s", file=sys.stderr)
        self.assertEqual(proc.wait(timeout=10), 0, "o servidor deveria sair com código 0")
        self.assertFalse(self.pid_file.exists(), "o arquivo de PID deveria ter sumido")
        self.assertFalse(psutil.pid_exists(pid) and self._meu(psutil.Process(pid)))
        self.assertTrue(self._esperar(lambda: not self._no_ar(), 5), "a porta ainda responde")
        t.join(10)
        self.assertFalse(t.is_alive())
        log = self.log_file.read_text(encoding="utf-8", errors="replace")
        self.assertIn("Desligado pelo painel (PC local)", log)
        self.assertIn("Proclamare encerrado.", log)
        self.assertNotIn("forçando a saída", log)
        self.assertNotIn("Traceback", log)

    def test_remoto_nao_desliga_o_servidor_real(self) -> None:
        """Host de rede no cabeçalho: não é "local"; sem PIN o servidor recusa e continua no ar."""
        env = dict(self.env, PROCLAMARE_PID_FILE=str(self.pid_file), PROCLAMARE_LOG_FILE=str(self.log_file))
        saida = open(self.base / "saida2.txt", "wb")
        self.addCleanup(saida.close)
        proc = subprocess.Popen([sys.executable, "server.py"], cwd=str(self.base), env=env,
                                stdin=subprocess.DEVNULL, stdout=saida, stderr=subprocess.STDOUT)
        self.pids_iniciados.add(proc.pid)
        self.assertTrue(self._esperar(self._no_ar, 60), "o servidor de teste não subiu")
        pid = self._pid_do_arquivo(self.pid_file)
        self.assertIsNotNone(pid)
        self.pids_iniciados.add(pid)
        con = http.client.HTTPConnection("127.0.0.1", self.porta, timeout=5)
        con.request("POST", ROTA, body="{}", headers={
            "Content-Type": "application/json", "Host": f"192.168.0.10:{self.porta}"})
        r = con.getresponse()
        r.read()
        con.close()
        self.assertIn(r.status, (401, 403))
        time.sleep(1.5)
        self.assertTrue(self._no_ar())
        self.assertIsNone(proc.poll())
        self.assertLess(self._desligar_e_medir(pid), self.PRAZO_DESLIGAR_S)   # e este é o fim
        proc.wait(timeout=10)

    # ---- lançador -----------------------------------------------------------------------
    @unittest.skipUnless(os.name == "nt", "o lançador (Proclamare.pyw) é só para Windows")
    def test_lancador_liga_de_novo_depois_de_desligar_pelo_painel(self) -> None:
        pid_lancador = self.base / "logs" / "proclamare.pid"

        def ligar() -> int:
            res = self._rodar("Proclamare.pyw")
            self.assertEqual(res.returncode, 0, res.stdout.decode("utf-8", "replace"))
            self.assertTrue(self._no_ar())
            pid = self._pid_do_arquivo(pid_lancador)
            self.assertIsNotNone(pid, "o servidor ligado pelo lançador não gravou o PID")
            self.pids_iniciados.add(pid)
            return pid

        # 1ª vez: liga; desliga pelo painel.
        pid1 = ligar()
        self.assertLess(self._desligar_e_medir(pid1), self.PRAZO_DESLIGAR_S)
        self.assertFalse(pid_lancador.exists(), "o PID deveria ter sumido")
        self.assertTrue(self._esperar(lambda: not self._no_ar(), 5))

        # "Desligar Proclamare" do Menu Iniciar depois disso: não acusa erro.
        res = self._rodar("Proclamare.pyw", "--parar")
        texto = res.stdout.decode("utf-8", "replace")
        self.assertEqual(res.returncode, 0, texto)
        self.assertIn("não está ligado", texto)
        self.assertNotIn("ERRO", texto)

        # Duplo clique de novo: liga normalmente (outro processo) e responde.
        pid2 = ligar()
        self.assertNotEqual(pid1, pid2)
        self.assertTrue(psutil.pid_exists(pid2))

        # Fim: desliga o segundo e confere de novo o "--parar" limpo.
        self.assertLess(self._desligar_e_medir(pid2), self.PRAZO_DESLIGAR_S)
        self.assertFalse(pid_lancador.exists())
        res = self._rodar("Proclamare.pyw", "--parar")
        self.assertEqual(res.returncode, 0, res.stdout.decode("utf-8", "replace"))


if __name__ == "__main__":
    unittest.main()
