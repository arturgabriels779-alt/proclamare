"""
Testes da v2.8.10: "Travar a voz da loja para gerentes" (config.json["voz_loja"]["travar_gerentes"]).

  * Padrão False; só o ADM grava (capacidade `voz_volume`); só bool (400); modo PIN: só o PC das caixas.
  * Ligada, o perfil `gerente` perde a capacidade `voz` em todo lugar (rotas, `permissoes`, modo PIN com o remoto = gerente):
    POST /api/voz e POST /api/voz/ouvir = 403; `teste:true` do gerente vira anúncio comum. O ADM nunca é afetado.
  * Música, Programação, Mensagens e o resto do gerente seguem como sempre.
NUNCA há som real (os registradores de tests/__init__.py e os falsos de tests/test_permissoes.py).

    python -m unittest tests.test_voz_travar_gerentes -v
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

RAIZ = Path(__file__).resolve().parent.parent
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

import contas  # noqa: E402
import server  # noqa: E402
from tests import test_permissoes as tpm  # noqa: E402
from tests.test_auth import HOST_REMOTO, PIN, ip_remoto  # noqa: E402
from tests.test_login_contas import BaseContas, cookie_da_resposta  # noqa: E402


def travado() -> bool:
    return server.voz_loja()["travar_gerentes"]


class TesteContas(tpm.BasePermissoes):
    def voz(self, perfil: str, corpo: dict, local: bool = False) -> Any:
        tok = (self.local if local else self.rede)[perfil]
        return self.chamar("POST", "/api/voz", tok, local=local, corpo=corpo)

    def ouvir(self, perfil: str, local: bool = False) -> Any:
        tok = (self.local if local else self.rede)[perfil]
        return self.chamar("POST", "/api/voz/ouvir", tok, local=local, corpo={"aviso": "aeroporto"})

    def travar(self, valor: bool = True) -> None:
        r = self.voz("adm", {"travar_gerentes": valor})
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))

    def permissoes(self, perfil: str) -> list[str]:
        r = self.chamar("GET", "/api/auth/status", self.rede[perfil], local=False)
        return r.get_json()["permissoes"]

    # --- gravar --------------------------------------------------------------------------------
    def test_padrao_false_e_o_get_devolve(self) -> None:
        self.assertIs(server.VOZ_LOJA_PADRAO["travar_gerentes"], False)
        for perfil in ("adm", "gerente", "funcionario"):
            r = self.chamar("GET", "/api/voz", self.rede[perfil], local=False)
            self.assertIs(r.get_json()["voz_loja"]["travar_gerentes"], False)

    def test_adm_grava_e_o_get_mostra(self) -> None:
        for local in (False, True):
            r = self.voz("adm", {"travar_gerentes": not local}, local=local)
            self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
            self.assertIs(r.get_json()["voz_loja"]["travar_gerentes"], not local)
        self.assertIs(travado(), False)
        self.travar(True)
        self.assertIs(travado(), True)
        r = self.chamar("GET", "/api/voz", self.rede["gerente"], local=False)   # leitura segue liberada (gerente vê)
        self.assertEqual(r.status_code, 200)
        self.assertIs(r.get_json()["voz_loja"]["travar_gerentes"], True)

    def test_nao_bool_400(self) -> None:
        for ruim in (1, 0, "true", "sim", None, [True], {"v": 1}, 1.0):
            with self.subTest(v=ruim):
                r = self.voz("adm", {"travar_gerentes": ruim})
                self.assertEqual(r.status_code, 400, r.get_data(as_text=True))
                self.assertEqual(r.get_json()["campo"], "travar_gerentes")
        self.assertIs(travado(), False)

    def test_gerente_e_funcionario_403_sem_gravar_nada(self) -> None:
        for perfil in ("gerente", "funcionario"):
            for local in (False, True):
                with self.subTest(perfil=perfil, local=local):
                    r = self.voz(perfil, {"travar_gerentes": True, "velocidade": 7}, local=local)
                    self.assertEqual(r.status_code, 403)
                    self.assertEqual(r.get_json()["codigo"], "sem_permissao")
                    v = server.voz_loja()
                    self.assertEqual((v["travar_gerentes"], v["velocidade"], v["definida"]), (False, 0, False))

    def test_a_ordem_400_429_403(self) -> None:
        # 400 antes de tudo (mesmo do gerente: o esquema é conferido primeiro)
        self.assertEqual(self.voz("gerente", {"travar_gerentes": "x"}).status_code, 400)
        # 403 repetido gasta a cota e vira 429, como o limitador/aviso_volume
        codigos = [self.voz("gerente", {"travar_gerentes": True}).status_code for _ in range(22)]
        self.assertEqual(codigos[0], 403)
        self.assertIn(429, codigos)

    # --- efeito --------------------------------------------------------------------------------
    def test_ligada_gerente_perde_a_voz_em_todo_lugar(self) -> None:
        self.assertIn("voz", self.permissoes("gerente"))
        self.travar(True)
        for local in (False, True):
            self.assert_negado(self.voz("gerente", {"velocidade": 5}, local=local))
            self.assert_negado(self.ouvir("gerente", local=local))
        self.assertEqual(server.voz_loja()["velocidade"], 0)        # nada foi gravado
        self.assertNotIn("voz", self.permissoes("gerente"))
        self.assertNotIn("voz", self.chamar("GET", "/api/auth/status", self.local["gerente"], local=True).get_json()["permissoes"])
        self.assertEqual(self.permissoes("funcionario"), ["anunciar"])
        # o login também devolve a lista efetiva
        r = self.login_conta("gil.gerente", tpm.SENHA_GER, ip=90)
        self.assertEqual(r.status_code, 200)
        self.assertNotIn("voz", r.get_json()["permissoes"])
        # a mensagem manda pedir a um administrador (a trava é do ADM)
        self.assertTrue(self.voz("gerente", {"velocidade": 5}).get_json()["error"].endswith("Peça a um administrador."))

    def test_adm_nunca_e_afetado(self) -> None:
        self.travar(True)
        self.assertIn("voz", self.permissoes("adm"))
        self.assertEqual(self.voz("adm", {"velocidade": 9}).status_code, 200)
        self.assertEqual(self.ouvir("adm").status_code, 200)
        self.assertEqual(self.voz("adm", {"travar_gerentes": False}).status_code, 200)

    def test_desligada_o_gerente_volta(self) -> None:
        self.travar(True)
        self.assert_negado(self.voz("gerente", {"velocidade": 5}))
        self.travar(False)
        self.assertEqual(self.voz("gerente", {"velocidade": 5}).status_code, 200)
        self.assertEqual(self.ouvir("gerente").status_code, 200)
        self.assertIn("voz", self.permissoes("gerente"))

    def test_teste_true_do_gerente_travado_e_403_e_nao_toca(self) -> None:
        def anunciar(perfil: str) -> Any:
            self.falas.clear()
            return self.chamar("POST", "/api/announce", self.rede[perfil], local=False,
                               corpo={"text": "Atenção, clientes.", "teste": True})

        r = anunciar("gerente")
        self.assertEqual((r.status_code, self.falas[-1]["aviso"]), (200, "nenhum"))   # sem trava: "Testar caixas" sem sineta
        self.travar(True)
        with self.assertLogs(server.auditoria.name, level="INFO") as cap:
            r = anunciar("gerente")
        self.assertEqual(r.status_code, 403, r.get_data(as_text=True))
        self.assertEqual((r.get_json()["codigo"], r.get_json()["error"]),
                         ("sem_permissao", "A voz da loja foi travada pelo administrador."))
        self.assertEqual(self.falas, [])                       # nada tocou
        saida = "\n".join(cap.output)
        self.assertIn("acao=anuncio", saida)
        self.assertIn("resultado=sem_permissao", saida)
        self.assertNotIn("Atenção", saida)
        self.assertEqual(self.linhas_do_historico(), [])       # 403 não entra no histórico
        # gerente SEM teste:true segue anunciando; funcionário com teste:true segue o contrato antigo (ignorado)
        self.falas.clear()
        r = self.chamar("POST", "/api/announce", self.rede["gerente"], local=False, corpo={"text": "Oi"})
        self.assertEqual(r.status_code, 200)
        r = anunciar("funcionario")
        self.assertEqual((r.status_code, self.falas[-1]["aviso"]), (200, server.voz_loja()["aviso"]))
        self.assertNotEqual(server.voz_loja()["aviso"], "nenhum")
        r = anunciar("adm")                                     # o ADM segue testando sem sineta
        self.assertEqual((r.status_code, self.falas[-1]["aviso"]), (200, "nenhum"))
        self.travar(False)
        r = anunciar("gerente")                                 # destravada: volta ao normal
        self.assertEqual((r.status_code, self.falas[-1]["aviso"]), (200, "nenhum"))

    def linhas_do_historico(self) -> list:
        caminho = server._anuncios_path()
        return [x for x in caminho.read_text(encoding="utf-8").splitlines() if "sem_permissao" in x] if caminho.exists() else []

    def test_o_resto_do_gerente_nao_muda(self) -> None:
        self.travar(True)
        for metodo, rota, corpo in (("GET", "/api/programacao", None), ("GET", "/api/mensagens", None),
                                    ("GET", "/api/musicas", None), ("GET", "/api/listas", None),
                                    ("GET", "/api/historico", None), ("GET", "/api/teste-caixas", None),
                                    ("POST", "/api/announce", {"text": "Oi"})):
            with self.subTest(rota=rota):
                r = self.chamar(metodo, rota, self.rede["gerente"], local=False, corpo=corpo)
                self.assertNotIn(r.status_code, (401, 403), r.get_data(as_text=True))
        for cap in ("musica", "programacao", "mensagens_editar", "anunciar", "contas", "desligar", "historico_todos"):
            self.assertIn(cap, self.permissoes("gerente"))
        self.assertEqual(self.permissoes("gerente"),
                         sorted(c for c in contas.PODE["gerente"] if c != "voz"))   # só a `voz` saiu

    def test_item_anuncio_da_programacao_continua_com_voz_propria(self) -> None:
        self.travar(True)
        itens = [{"tipo": "anuncio", "alvo": "Bom dia", "motor": "offline", "voz": "remy",
                  "velocidade": 10, "dias": [1], "hora": "08:00", "ativo": True}]
        r = self.chamar("GET", "/api/programacao", self.rede["gerente"], local=False)
        versao = r.get_json()["versao"]
        r = self.chamar("POST", "/api/programacao", self.rede["gerente"], local=False,
                        corpo={"versao": versao, "itens": itens})
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        item = r.get_json()["itens"][0]
        self.assertEqual((item["motor"], item["voz"], item["velocidade"]), ("offline", "remy", 10))

    # --- auditoria -----------------------------------------------------------------------------
    def test_auditoria_so_quando_muda_e_sem_texto(self) -> None:
        with self.assertLogs(server.auditoria.name, level="INFO") as cap:
            self.travar(True)
            self.travar(True)      # igual: sem linha nova de voz_travar_gerentes
            self.travar(False)
            self.travar(False)
        linhas = [x for x in cap.output if "acao=voz_travar_gerentes" in x]
        self.assertEqual(len(linhas), 2, cap.output)
        self.assertIn("valor=1", linhas[0])
        self.assertIn("valor=0", linhas[1])
        for x in linhas:
            self.assertRegex(x, r"conta=c[0-9a-f]{10}")
            for proibido in ("ana.adm", "Ana", "travar_gerentes=", "True", "False"):
                self.assertNotIn(proibido, x)


class TesteModoPin(BaseContas):
    def setUp(self) -> None:
        super().setUp()
        server.aplicar_pin(PIN)
        self.assertFalse(server.modo_contas())
        token = cookie_da_resposta(self.login())
        self.cab = {"Cookie": f"{server.COOKIE_SESSAO}={token}"}
        p = mock.patch.object(server, "_agendar_desligamento", mock.Mock())
        p.start()
        self.addCleanup(p.stop)

    def remoto_post(self, rota: str, corpo: dict) -> Any:
        return self.r.post(rota, json=corpo, base_url=HOST_REMOTO, environ_base=ip_remoto(50), headers=self.cab)

    def test_remoto_com_pin_nao_trava_e_o_pc_local_sim(self) -> None:
        r = self.remoto_post("/api/voz", {"travar_gerentes": True, "velocidade": 9})
        self.assertEqual(r.status_code, 403, r.get_data(as_text=True))
        self.assertEqual(r.get_json()["codigo"], "sem_permissao")
        self.assertFalse(server.voz_loja()["definida"])             # nada gravado
        r = self.c.post("/api/voz", json={"travar_gerentes": True})
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        self.assertTrue(travado())

    def test_travada_o_remoto_com_pin_perde_a_voz_mas_o_pc_nao(self) -> None:
        self.assertEqual(self.remoto_post("/api/voz", {"velocidade": 5}).status_code, 200)   # sem trava: como sempre
        self.assertEqual(self.c.post("/api/voz", json={"travar_gerentes": True}).status_code, 200)
        r = self.remoto_post("/api/voz", {"velocidade": 9})
        self.assertEqual(r.status_code, 403, r.get_data(as_text=True))
        self.assertEqual(server.voz_loja()["velocidade"], 5)
        self.assertEqual(self.remoto_post("/api/voz/ouvir", {"aviso": "aeroporto"}).status_code, 403)
        st = self.r.get("/api/auth/status", base_url=HOST_REMOTO, environ_base=ip_remoto(50), headers=self.cab)
        self.assertNotIn("voz", st.get_json()["permissoes"])
        self.assertIn("voz", self.c.get("/api/auth/status").get_json()["permissoes"])   # PC das caixas = adm
        self.assertEqual(self.c.post("/api/voz", json={"velocidade": 7}).status_code, 200)
        # leitura segue liberada e a música do remoto não muda
        r = self.r.get("/api/voz", base_url=HOST_REMOTO, environ_base=ip_remoto(50), headers=self.cab)
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.get_json()["voz_loja"]["travar_gerentes"])
        r = self.r.get("/api/programacao", base_url=HOST_REMOTO, environ_base=ip_remoto(50), headers=self.cab)
        self.assertEqual(r.status_code, 200)

    def test_teste_true_do_remoto_com_pin_travado_e_403(self) -> None:
        falas: list[str] = []

        def anunciar(texto: str, *a: Any, aviso: str = "nenhum", **kw: Any) -> tuple[str, None]:
            falas.append(aviso)
            return "offline", None

        with mock.patch.object(server, "_anunciar", anunciar), mock.patch.object(server, "tem_internet", lambda *a, **k: False), \
                mock.patch.object(server, "obter_vozes_offline", lambda: None):
            self.assertEqual(self.remoto_post("/api/announce", {"text": "Oi", "teste": True}).status_code, 200)
            self.c.post("/api/voz", json={"travar_gerentes": True})
            r = self.remoto_post("/api/announce", {"text": "Oi", "teste": True})
            self.assertEqual(r.status_code, 403, r.get_data(as_text=True))
            self.assertEqual(r.get_json()["error"], "A voz da loja foi travada pelo administrador.")
            self.assertEqual(self.remoto_post("/api/announce", {"text": "Oi"}).status_code, 200)   # sem teste: normal
            # o PC das caixas (adm) segue testando sem sineta
            self.assertEqual(self.c.post("/api/announce", json={"text": "Oi", "teste": True}).status_code, 200)
        self.assertEqual(falas, ["nenhum", "microfone", "nenhum"])

    def test_voz_ouvir_403_audita_sem_texto(self) -> None:
        """Modo PIN: o 403 é da própria rota (em modo contas é da camada de permissões, que já tem o seu registro)."""
        self.c.post("/api/voz", json={"travar_gerentes": True})
        with self.assertLogs(server.auditoria.name, level="INFO") as cap:
            r = self.remoto_post("/api/voz/ouvir", {"aviso": "aeroporto"})
        self.assertEqual(r.status_code, 403)
        saida = chr(10).join(cap.output)
        self.assertIn("acao=voz_ouvir", saida)
        self.assertIn("resultado=sem_permissao", saida)
        self.assertNotIn("aeroporto", saida)

    def test_mensagem_do_403_no_modo_pin_aponta_o_pc_das_caixas(self) -> None:
        self.c.post("/api/voz", json={"travar_gerentes": True})
        for rota, corpo in (("/api/voz", {"velocidade": 9}), ("/api/voz/ouvir", {"aviso": "aeroporto"})):
            with self.subTest(rota=rota):
                r = self.remoto_post(rota, corpo)
                self.assertEqual((r.status_code, r.get_json()["codigo"]), (403, "sem_permissao"))
                self.assertEqual(r.get_json()["error"], "Só o PC das caixas pode mudar a voz da loja.")

    def test_trava_ligada_no_meio_do_voz_salvar_recusa_sem_gravar(self) -> None:
        """A checagem de fora passa (perfil_pode simulado), mas a trava já está no config: o lock recusa."""
        self.c.post("/api/voz", json={"travar_gerentes": True})
        original = server._perfil_pode
        with mock.patch.object(server, "_perfil_pode", lambda cap: True if cap == "voz" else original(cap)):
            r = self.remoto_post("/api/voz", {"velocidade": 33})
        self.assertEqual(r.status_code, 403, r.get_data(as_text=True))
        self.assertEqual(r.get_json()["codigo"], "sem_permissao")
        self.assertEqual(server.voz_loja()["velocidade"], 0)


class TesteLeituraTorta(unittest.TestCase):
    def test_config_torta_vale_o_padrao(self) -> None:
        for torto in ("sim", 1, None, [], {"x": 1}):
            with self.subTest(torto=torto), mock.patch.object(
                    server, "load_config", lambda t=torto: {"voz_loja": {"travar_gerentes": t}}):
                self.assertIs(server.voz_loja()["travar_gerentes"], False)
                self.assertIs(server._travar_gerentes_ligado(), False)
        with mock.patch.object(server, "load_config", side_effect=OSError("disco")):
            self.assertIs(server._travar_gerentes_ligado(), False)
            self.assertIs(server._pode_seguro("gerente", "voz"), True)
        with mock.patch.object(server, "load_config", lambda: {"voz_loja": {"travar_gerentes": True}}):
            self.assertIs(server._pode_seguro("gerente", "voz"), False)
            self.assertIs(server._pode_seguro("adm", "voz"), True)
            self.assertIs(server._pode_seguro("gerente", "musica"), True)
            self.assertNotIn("voz", server._permissoes("gerente"))


if __name__ == "__main__":
    unittest.main()
