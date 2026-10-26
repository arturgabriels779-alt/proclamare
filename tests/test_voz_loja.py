"""
Testes do contrato v2.8 (Parte A2): voz da loja guardada no servidor (GET/POST /api/voz).
Sem som e sem rede: só config.json temporário (server.CONFIG_PATH aponta para a pasta temporária).

    python -m unittest tests.test_voz_loja -v
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))
os.environ.setdefault("PROCLAMARE_NO_BROWSER", "1")

import server  # noqa: E402

PIN = "481926"
HOST_REMOTO = "http://192.168.0.10:8502"
IP_REMOTO = {"REMOTE_ADDR": "192.168.0.50"}


class Base(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self._salvo = (server.CONFIG_PATH, server._config_cache, server._config_assinatura)
        server.CONFIG_PATH = self.tmp / "config.json"   # NUNCA o config.json real
        server._config_cache = {}
        server._config_assinatura = None
        with server._taxa_lock:
            server._taxa_hist.clear()
            server._taxa_janela.clear()
        server._login_resetar_global()
        self.c = server.app.test_client()

    def tearDown(self) -> None:
        (server.CONFIG_PATH, server._config_cache, server._config_assinatura) = self._salvo
        self._tmp.cleanup()

    def gravar_config(self, cfg: dict) -> None:
        server.CONFIG_PATH.write_text(json.dumps(cfg), encoding="utf-8")
        server._config_cache = {}
        server._config_assinatura = None


class TestVozLoja(Base):
    def test_get_sem_chave_devolve_padrao(self) -> None:
        r = self.c.get("/api/voz")
        self.assertEqual(r.status_code, 200)
        v = r.get_json()["voz_loja"]
        self.assertEqual(v, {"motor": "auto", "voz": "francisca", "velocidade": 0,
                             "aviso": "microfone", "definida": False})   # v2.8: padrão de fábrica = Microfone

    def test_post_parcial_grava_e_preserva_resto(self) -> None:
        self.assertEqual(self.c.post("/api/voz", json={"voz": "antonio", "velocidade": 10}).status_code, 200)
        r = self.c.post("/api/voz", json={"aviso": "aeroporto"})
        v = r.get_json()["voz_loja"]
        self.assertEqual(v, {"motor": "auto", "voz": "antonio", "velocidade": 10, "aviso": "aeroporto",
                             "definida": True})
        self.assertEqual(self.c.get("/api/voz").get_json()["voz_loja"], v)
        self.assertEqual(server.voz_loja()["voz"], "antonio")

    def test_post_valor_invalido_400_com_campo(self) -> None:
        for corpo, campo in (({"motor": "x"}, "motor"), ({"velocidade": 51}, "velocidade"),
                             ({"aviso": "buzina"}, "aviso"), ({"voz": 3}, "voz")):
            r = self.c.post("/api/voz", json=corpo)
            self.assertEqual(r.status_code, 400, corpo)
            self.assertEqual(r.get_json()["campo"], campo)
        self.assertFalse(server.CONFIG_PATH.exists() and "voz_loja" in server.load_config())

    def test_aviso_removido_no_config_cai_no_padrao_microfone(self) -> None:
        # v2.8: ding_dong, suave, sino, comissario e aeroporto_inicio_fim saíram. Quem tinha um deles guardado no
        # config.json cai no padrão novo (Microfone), sem erro, e o resto da voz da loja é preservado.
        for removido in ("sino", "ding_dong", "suave", "comissario", "aeroporto_inicio_fim"):
            with self.subTest(aviso=removido):
                self.gravar_config({"voz_loja": {"motor": "offline", "voz": "antonio", "velocidade": 10,
                                                 "aviso": removido}})
                self.assertEqual(server.voz_loja()["aviso"], "microfone")
                r = self.c.get("/api/voz")
                self.assertEqual(r.status_code, 200)
                self.assertEqual(r.get_json()["voz_loja"], {"motor": "offline", "voz": "antonio", "velocidade": 10,
                                                            "aviso": "microfone", "definida": True})
                # gravar outro campo não ressuscita o id antigo: a resposta já mostra o padrão
                r = self.c.post("/api/voz", json={"velocidade": 5})
                self.assertEqual((r.status_code, r.get_json()["voz_loja"]["aviso"]), (200, "microfone"))

    def test_post_aviso_removido_400(self) -> None:
        for removido in ("sino", "ding_dong", "suave", "comissario", "aeroporto_inicio_fim", "campainha_dupla"):
            with self.subTest(aviso=removido):
                r = self.c.post("/api/voz", json={"aviso": removido})
                self.assertEqual(r.status_code, 400)
                self.assertEqual(r.get_json()["campo"], "aviso")
                r = self.c.post("/api/voz/ouvir", json={"aviso": removido})
                self.assertEqual(r.status_code, 400)
        self.assertFalse(server.CONFIG_PATH.exists() and "voz_loja" in server.load_config())

    def test_post_campo_desconhecido_400(self) -> None:
        r = self.c.post("/api/voz", json={"aviso": "aeroporto", "extra": 1})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.get_json()["error"], "Campo desconhecido.")

    def test_post_vazio_400(self) -> None:
        self.assertEqual(self.c.post("/api/voz", json={}).status_code, 400)

    def test_config_torto_cai_no_padrao(self) -> None:
        self.gravar_config({"voz_loja": {"velocidade": "rapido", "aviso": 3, "voz": "antonio"}})
        r = self.c.get("/api/voz")
        self.assertEqual(r.status_code, 200)
        v = r.get_json()["voz_loja"]
        self.assertEqual((v["velocidade"], v["aviso"], v["voz"], v["definida"]), (0, "microfone", "antonio", True))
        self.gravar_config({"voz_loja": "lixo"})
        self.assertEqual(self.c.get("/api/voz").status_code, 200)

    def test_remoto_sem_pin_401(self) -> None:
        server.aplicar_pin(PIN)
        r = server.app.test_client().get("/api/voz", base_url=HOST_REMOTO, environ_base=IP_REMOTO)
        self.assertEqual(r.status_code, 401)
        r = server.app.test_client().post("/api/voz", json={"aviso": "aeroporto"}, base_url=HOST_REMOTO,
                                          environ_base=IP_REMOTO)
        self.assertEqual(r.status_code, 401)

    def test_limite_20_por_minuto_429(self) -> None:
        codigos = [self.c.post("/api/voz", json={"velocidade": i % 5}).status_code for i in range(21)]
        self.assertEqual(codigos[:20], [200] * 20)
        self.assertEqual(codigos[20], 429)

    def test_opcoes_trazem_nenhum_e_os_quatro_avisos(self) -> None:
        # v2.8: a lista final é "nenhum" + 4 sinetas (as antigas saíram a pedido da loja)
        o = self.c.get("/api/voz").get_json()["opcoes"]
        self.assertEqual(o["avisos"], [
            {"id": "nenhum", "nome": "Nenhum"},
            {"id": "microfone", "nome": "Microfone (início e fim)"},
            {"id": "aeroporto", "nome": "Aeroporto"},
            {"id": "supermercado", "nome": "Supermercado (início e fim)"},
            {"id": "ding_dong_duplo", "nome": "Ding-dong (2 vezes)"},
        ])
        self.assertEqual(o["motores"], ["auto", "online", "offline"])
        self.assertEqual([v["id"] for v in o["vozes"]], ["francisca", "antonio"])


if __name__ == "__main__":
    unittest.main()
