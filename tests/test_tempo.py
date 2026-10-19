"""
Testes do contrato v2.4.1 (tempo da música): posicao_s/duracao_s no heartbeat,
posicao_idade_ms no GET e a ação "seek" em /api/youtube/command.
Usa config.json temporário (nunca o real) e não toca som.
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))
os.environ.setdefault("PROCLAMARE_NO_BROWSER", "1")
os.environ.setdefault("PROCLAMARE_MUSICAS_DIR", os.path.join(tempfile.gettempdir(), "proclamare-testes-musicas"))

import server  # noqa: E402

PIN = "481926"
HOST_REMOTO = "http://192.168.0.10:8502"
IP_REMOTO = {"REMOTE_ADDR": "192.168.0.50"}
ESTADO_ZERO = dict(last_heartbeat=None, source="youtube", music_id=None, video_id=None, now_playing="",
                   is_playing=False, yt_pronto=True, posicao_s=None, duracao_s=None, posicao_em=None)


class TesteTempo(unittest.TestCase):
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
        self._zerar()
        self.c = server.app.test_client()
        self.r = server.app.test_client()

    def tearDown(self) -> None:
        self._zerar()
        server.CONFIG_PATH, server._config_cache, server._config_assinatura = self._salvo
        self._tmp.cleanup()

    @staticmethod
    def _zerar() -> None:
        with server._yt_cond:
            server._yt_comandos.clear()
        with server._yt_estado_lock:
            server._yt_estado.update(ESTADO_ZERO)

    def push(self, corpo, **kw):
        return self.c.post("/api/youtube/state", json=corpo, **kw)

    def estado(self) -> dict:
        r = self.c.get("/api/youtube/state")
        self.assertEqual(r.status_code, 200)
        return r.get_json()

    def cmd(self, params, action="seek", **kw):
        return self.c.post("/api/youtube/command", json={"action": action, "params": params}, **kw)

    def comandos(self) -> list:
        with server._yt_cond:
            return list(server._yt_comandos)

    def remoto(self, metodo, caminho, **kw):
        return getattr(self.r, metodo)(caminho, base_url=HOST_REMOTO, environ_base=IP_REMOTO, **kw)

    # ---- heartbeat: valores válidos
    def test_posicao_e_duracao_validas(self) -> None:
        self.assertEqual(self.push({"is_playing": True, "posicao_s": 83.25, "duracao_s": 225}).status_code, 200)
        e = self.estado()
        self.assertEqual(e["posicao_s"], 83.25)
        self.assertEqual(e["duracao_s"], 225.0)
        self.assertIsInstance(e["posicao_idade_ms"], int)

    def test_limites_aceitos(self) -> None:
        self.assertEqual(self.push({"posicao_s": 0, "duracao_s": 86400}).status_code, 200)
        self.assertEqual(self.push({"posicao_s": 86400}).status_code, 200)
        self.assertEqual(self.push({"duracao_s": 0.001}).status_code, 200)

    def test_duracao_null_e_ao_vivo(self) -> None:
        self.push({"posicao_s": 10, "duracao_s": 200})
        self.push({"posicao_s": 11, "duracao_s": None})
        e = self.estado()
        self.assertIsNone(e["duracao_s"])
        self.assertEqual(e["posicao_s"], 11.0)

    def test_sem_posicao_tudo_null(self) -> None:
        self.push({"is_playing": True})
        e = self.estado()
        self.assertEqual((e["posicao_s"], e["duracao_s"], e["posicao_idade_ms"]), (None, None, None))

    def test_arredonda_a_milissegundos(self) -> None:
        self.push({"posicao_s": 1.23456})
        self.assertEqual(self.estado()["posicao_s"], 1.235)

    # ---- heartbeat: inválidos (400 e estado intacto)
    def test_posicao_invalida(self) -> None:
        self.push({"posicao_s": 5, "duracao_s": 100})
        for ruim in (True, False, "5", None, [], {}, -1, -0.001, 86400.001, 10**30, -10**30):
            with self.subTest(posicao_s=ruim):
                self.assertEqual(self.push({"posicao_s": ruim, "now_playing": "X"}).status_code, 400)
        e = self.estado()
        self.assertEqual(e["posicao_s"], 5.0)
        self.assertNotEqual(e["now_playing"], "X")  # heartbeat recusado não altera nada

    def test_duracao_invalida(self) -> None:
        for ruim in (True, "100", 0, 0.0, -5, 86401, 0.0004, [], {}, 10**30):
            with self.subTest(duracao_s=ruim):
                self.assertEqual(self.push({"duracao_s": ruim}).status_code, 400)

    def test_nan_e_infinity_crus(self) -> None:
        # o json do Python aceita NaN/Infinity; o servidor tem de recusar (400, nunca 500)
        for lit in ("NaN", "Infinity", "-Infinity"):
            for campo in ("posicao_s", "duracao_s"):
                with self.subTest(campo=campo, lit=lit):
                    r = self.c.post("/api/youtube/state", data='{"%s": %s}' % (campo, lit),
                                    content_type="application/json")
                    self.assertEqual(r.status_code, 400)

    def test_recusado_nao_atualiza_heartbeat(self) -> None:
        self.assertEqual(self.push({"posicao_s": -1}).status_code, 400)
        with server._yt_estado_lock:
            self.assertIsNone(server._yt_estado["last_heartbeat"])

    # ---- coerência temporal / troca de faixa
    def test_posicao_idade_ms_coerente(self) -> None:
        self.push({"posicao_s": 10})
        time.sleep(0.25)
        a = self.estado()["posicao_idade_ms"]
        self.assertGreaterEqual(a, 240)
        self.assertLess(a, 2000)
        time.sleep(0.2)
        self.assertGreater(self.estado()["posicao_idade_ms"], a)

    def test_posicao_velha_some(self) -> None:
        self.push({"posicao_s": 10, "duracao_s": 100})
        with server._yt_estado_lock:
            server._yt_estado["posicao_em"] -= server.YOUTUBE_PLAYER_TIMEOUT + 1
            server._yt_estado["last_heartbeat"] = time.monotonic()  # tocador vivo, posição velha
        e = self.estado()
        self.assertEqual((e["posicao_s"], e["duracao_s"], e["posicao_idade_ms"]), (None, None, None))

    def test_tocador_offline_some(self) -> None:
        self.push({"posicao_s": 10, "duracao_s": 100})
        with server._yt_estado_lock:
            server._yt_estado["last_heartbeat"] -= server.YOUTUBE_PLAYER_TIMEOUT + 1
        self.assertIsNone(self.estado()["posicao_s"])

    def test_heartbeat_sem_posicao_mantem_a_anterior(self) -> None:
        self.push({"video_id": "dQw4w9WgXcQ", "posicao_s": 10, "duracao_s": 100})
        self.push({"video_id": "dQw4w9WgXcQ", "is_playing": True})
        e = self.estado()
        self.assertEqual((e["posicao_s"], e["duracao_s"]), (10.0, 100.0))

    def test_troca_de_faixa_descarta_tempo_antigo(self) -> None:
        self.push({"video_id": "dQw4w9WgXcQ", "posicao_s": 90, "duracao_s": 200})
        self.push({"video_id": "9bZkp7q19f0"})
        e = self.estado()
        self.assertEqual((e["posicao_s"], e["duracao_s"], e["posicao_idade_ms"]), (None, None, None))
        self.push({"video_id": "9bZkp7q19f0", "posicao_s": 2, "duracao_s": 300})
        self.assertEqual(self.estado()["duracao_s"], 300.0)

    def test_troca_de_faixa_com_tempo_novo_vale_o_novo(self) -> None:
        self.push({"video_id": "dQw4w9WgXcQ", "posicao_s": 90, "duracao_s": 200})
        self.push({"video_id": "9bZkp7q19f0", "posicao_s": 1})
        e = self.estado()
        self.assertEqual((e["posicao_s"], e["duracao_s"]), (1.0, None))

    # ---- seek
    def test_seek_valido(self) -> None:
        for v, esperado in ((0, 0.0), (83, 83.0), (12.5, 12.5), (86400, 86400.0), (1.23456, 1.235)):
            with self.subTest(v=v):
                r = self.cmd({"segundos": v})
                self.assertEqual(r.status_code, 200)
                self.assertTrue(r.get_json()["ok"])
                self.assertEqual(self.comandos()[-1]["action"], "seek")
                self.assertEqual(self.comandos()[-1]["params"], {"segundos": esperado})

    def test_seek_invalido(self) -> None:
        for ruim in (True, "10", None, [], -1, 86401, 10**30):
            with self.subTest(segundos=ruim):
                self.assertEqual(self.cmd({"segundos": ruim}).status_code, 400)
        self.assertEqual(self.cmd({}).status_code, 400)
        self.assertEqual(self.c.post("/api/youtube/command", json={"action": "seek"}).status_code, 400)
        self.assertEqual(self.cmd([]).status_code, 400)
        for lit in ("NaN", "Infinity", "-Infinity"):
            r = self.c.post("/api/youtube/command", content_type="application/json",
                            data='{"action":"seek","params":{"segundos":%s}}' % lit)
            self.assertEqual(r.status_code, 400)
        self.assertEqual(self.comandos(), [])

    def test_seek_nao_repassa_campos_extras(self) -> None:
        r = self.cmd({"segundos": 5, "value": 50, "url": "http://x", "action": "duck"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.comandos()[-1]["params"], {"segundos": 5.0})

    def test_outras_acoes_intactas(self) -> None:
        self.assertEqual(self.cmd({"value": 40}, "volume").status_code, 200)
        self.assertEqual(self.comandos()[-1]["params"], {"value": 40})
        self.assertEqual(self.cmd({"segundos": 5}, "pause").status_code, 200)
        self.assertEqual(self.comandos()[-1]["params"], {})   # pause não repassa nada
        for interno in ("duck", "restore"):
            self.assertEqual(self.cmd({}, interno).status_code, 400)

    # ---- limite de taxa e buffer de comandos
    def test_comandos_limite_de_taxa_geral(self) -> None:
        codigos = [self.cmd({"value": 10}, "volume").status_code for _ in range(31)]
        self.assertEqual(codigos[:30], [200] * 30)
        self.assertEqual(codigos[30], 429)

    def test_seek_limite_de_taxa_proprio(self) -> None:
        codigos = [self.cmd({"segundos": i}).status_code for i in range(11)]
        self.assertEqual(codigos[:10], [200] * 10)
        self.assertEqual(codigos[10], 429)
        self.assertEqual(len(self.comandos()), 10)   # o 429 não enfileira nada

    def test_limite_de_taxa_e_por_aparelho(self) -> None:
        for _ in range(31):
            self.cmd({"value": 10}, "volume")
        self.assertEqual(self.cmd({"value": 10}, "volume").status_code, 429)
        server.aplicar_pin(PIN)
        self.remoto("post", "/api/auth/login", json={"pin": PIN})
        r = self.remoto("post", "/api/youtube/command", json={"action": "pause", "params": {}},
                        headers={"Origin": HOST_REMOTO})
        self.assertEqual(r.status_code, 200)   # outro IP tem a própria cota

    def test_buffer_cheio_nunca_descarta_duck_e_restore(self) -> None:
        server.youtube_enqueue("duck")
        for _ in range(120):   # direto no servidor (sem o limite de taxa da rota)
            server.youtube_enqueue("seek", {"segundos": 1.0})
        server.youtube_enqueue("restore")
        for _ in range(120):
            server.youtube_enqueue("seek", {"segundos": 2.0})
        acoes = [c["action"] for c in self.comandos()]
        self.assertLessEqual(len(acoes), 50)
        self.assertIn("duck", acoes)
        self.assertIn("restore", acoes)
        ids = [c["id"] for c in self.comandos()]
        self.assertEqual(ids, sorted(ids))

    # ---- PIN / 415 / 403
    def test_remoto_sem_pin_e_recusado(self) -> None:
        server.aplicar_pin(PIN)
        self.assertEqual(self.remoto("post", "/api/youtube/command",
                                     json={"action": "seek", "params": {"segundos": 5}}).status_code, 401)
        self.assertEqual(self.remoto("post", "/api/youtube/state", json={"posicao_s": 5}).status_code, 401)
        self.assertEqual(self.remoto("get", "/api/youtube/state").status_code, 401)
        self.assertEqual(self.comandos(), [])

    def test_remoto_com_pin_funciona(self) -> None:
        server.aplicar_pin(PIN)
        self.assertEqual(self.remoto("post", "/api/auth/login", json={"pin": PIN}).status_code, 200)
        cab = {"Origin": HOST_REMOTO}
        r = self.remoto("post", "/api/youtube/command",
                        json={"action": "seek", "params": {"segundos": 7}}, headers=cab)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.comandos()[-1]["params"], {"segundos": 7.0})
        r = self.remoto("post", "/api/youtube/state", json={"posicao_s": 7, "duracao_s": 50}, headers=cab)
        self.assertEqual(r.status_code, 200)
        g = self.remoto("get", "/api/youtube/state").get_json()
        self.assertEqual(g["duracao_s"], 50.0)

    def test_415_nao_json(self) -> None:
        for rota, dados in (("/api/youtube/command", '{"action":"seek","params":{"segundos":5}}'),
                            ("/api/youtube/state", '{"posicao_s":5}')):
            r = self.c.post(rota, data=dados, content_type="text/plain")
            self.assertEqual(r.status_code, 415)
        self.assertEqual(self.comandos(), [])

    def test_403_origin_de_outro_host(self) -> None:
        cab = {"Origin": "http://evil.example"}
        r = self.cmd({"segundos": 5}, headers=cab)
        self.assertEqual(r.status_code, 403)
        self.assertEqual(self.push({"posicao_s": 5}, headers=cab).status_code, 403)
        self.assertEqual(self.comandos(), [])
        with server._yt_estado_lock:
            self.assertIsNone(server._yt_estado["posicao_s"])


if __name__ == "__main__":
    unittest.main()
