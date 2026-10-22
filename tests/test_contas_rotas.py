"""
Testes das rotas de contas e da troca da própria senha (v2.8 B5): GET/POST /api/contas, POST /api/contas/<conta_id>
e POST /api/conta/senha.

  * Base: tests/test_login_contas.py `BaseContas` (config.json/contas.json temporários, PBKDF2 rebaixado para 1000
    iterações). Contas fictícias: ADM (primeiro-adm, pelo PC das caixas), gerente e funcionário; as sessões da rede
    são abertas pelo login de verdade.
  * Senhas, usuários e nomes são FICTÍCIOS. Nenhum teste toca som, sobe servidor nem usa as portas 8502/8599.

    python -m unittest tests.test_contas_rotas -v
"""
from __future__ import annotations

import unittest
from typing import Any
from unittest import mock

import contas
import server
from tests.test_auth import PIN
from tests.test_login_contas import (SENHA_ADM, SENHA_FUNC, SENHA_GER, SENHA_NOVA, BaseContas, carga_crua,
                                     cookie_da_resposta, max_age)

COOKIE = server.COOKIE_SESSAO
SENHA_OUTRA = "senha-outra-ficticia-7"
NOVO_FUNC = {"usuario": "rui.caixa", "nome": "Rui Fictício", "funcao": "Caixa", "perfil": "funcionario",
             "senha": SENHA_OUTRA}
CAMPOS_PROIBIDOS = ("senha_hash", "sessao_v")


class BaseRotas(BaseContas):
    def setUp(self) -> None:
        super().setUp()
        with server._conta_senha_lock:          # erros de "senha atual" de testes anteriores não vazam
            server._conta_senha_erros.clear()
        self.adm = self.criar_adm()
        self.ger = self.criar("gil.gerente", "gerente", SENHA_GER)
        self.func = self.criar("fia.func", "funcionario", SENHA_FUNC)
        self.tok = {"adm": self.sessao("ana.adm", SENHA_ADM, 60), "gerente": self.sessao("gil.gerente", SENHA_GER, 61),
                    "funcionario": self.sessao("fia.func", SENHA_FUNC, 62)}

    # --- utilidades ---------------------------------------------------------------------------
    def sessao(self, usuario: str, senha: str, ip: int) -> str:
        r = self.login_conta(usuario, senha, ip=ip)
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        return cookie_da_resposta(r)

    def como(self, perfil_ou_token: str, metodo: str, caminho: str, ip: int = 70, **kw: Any) -> Any:
        """Chamada pela rede com a sessão do perfil (ou com o token dado)."""
        token = self.tok.get(perfil_ou_token, perfil_ou_token)
        kw.setdefault("headers", {})["Cookie"] = f"{COOKIE}={token}"
        return self.remoto(metodo, caminho, ip=ip, **kw)

    def versao_de(self, conta_id: str) -> int:
        return server.conta_por_id(conta_id)["versao"]

    def alterar(self, quem: str, conta_id: str, **mudancas: Any) -> Any:
        corpo = {"versao": self.versao_de(conta_id), **mudancas}
        return self.como(quem, "post", f"/api/contas/{conta_id}", json=corpo)

    def assert_codigo(self, r: Any, status: int, codigo: str, campo: "str | None" = None) -> None:
        self.assertEqual(r.status_code, status, r.get_data(as_text=True))
        corpo = r.get_json()
        self.assertEqual((corpo["ok"], corpo.get("codigo")), (False, codigo), corpo)
        if campo is not None:
            self.assertEqual(corpo.get("campo"), campo, corpo)

    def assert_sem_sessao(self, token: str) -> None:
        r = self.como(token, "get", "/api/mensagens")
        self.assertEqual(r.status_code, 401, r.get_data(as_text=True))
        self.assertEqual(r.get_json()["codigo"], "login_requerido")


# ---------------------------------------------------------------------------------------------
# Criar
# ---------------------------------------------------------------------------------------------
class TesteCriar(BaseRotas):
    def test_gerente_cria_funcionario(self) -> None:
        r = self.como("gerente", "post", "/api/contas", json=NOVO_FUNC)
        self.assertEqual(r.status_code, 201, r.get_data(as_text=True))
        corpo = r.get_json()
        self.assertTrue(corpo["ok"])
        conta = corpo["conta"]
        self.assertEqual((conta["usuario"], conta["perfil"], conta["ativa"], conta["nome"], conta["funcao"]),
                         ("rui.caixa", "funcionario", True, "Rui Fictício", "Caixa"))
        self.assertEqual(conta["criada_por"], self.ger["id"])
        self.assertTrue(conta["pode_editar"])
        for proibido in CAMPOS_PROIBIDOS:
            self.assertNotIn(proibido, conta)
        # a conta nova entra com a senha que o gerente definiu
        self.assertEqual(self.login_conta("rui.caixa", SENHA_OUTRA, ip=90).status_code, 200)

    def test_gerente_nao_cria_gerente_403(self) -> None:
        for perfil in ("gerente", "adm"):
            with self.subTest(perfil=perfil):
                r = self.como("gerente", "post", "/api/contas", json=dict(NOVO_FUNC, perfil=perfil))
                self.assert_codigo(r, 403, "sem_permissao")
        self.assertIsNone(server.conta_por_usuario("rui.caixa"))
        # o ADM pode criar qualquer perfil
        r = self.como("adm", "post", "/api/contas", json=dict(NOVO_FUNC, perfil="gerente"))
        self.assertEqual((r.status_code, r.get_json()["conta"]["perfil"]), (201, "gerente"))

    def test_corpo_estrito_e_erros_de_validacao(self) -> None:
        r = self.como("adm", "post", "/api/contas", json=dict(NOVO_FUNC, ativa=False))
        self.assertEqual((r.status_code, r.get_json()["error"]), (400, "Campo desconhecido."))
        corpo = dict(NOVO_FUNC)
        del corpo["senha"]
        self.assertEqual(self.como("adm", "post", "/api/contas", json=corpo).status_code, 400)
        r = self.como("adm", "post", "/api/contas", json=dict(NOVO_FUNC, senha="curta"))
        self.assert_codigo(r, 400, "tamanho", "senha")
        r = self.como("adm", "post", "/api/contas", json=dict(NOVO_FUNC, usuario="FIA.Func "))
        self.assert_codigo(r, 409, "usuario_repetido", "usuario")
        r = self.como("adm", "post", "/api/contas", json=dict(NOVO_FUNC, senha="rui.caixa"))
        self.assert_codigo(r, 400, "igual_usuario", "senha")

    def test_limite_de_taxa_antes_do_pbkdf2(self) -> None:
        """20 criações/min por conta + IP; a 21ª é 429 sem calcular hash nenhum."""
        with mock.patch.object(contas, "hash_senha", wraps=contas.hash_senha) as hs:
            for i in range(20):
                r = self.como("adm", "post", "/api/contas", json=dict(NOVO_FUNC, usuario=f"rui.caixa{i}"))
                self.assertEqual(r.status_code, 201, r.get_data(as_text=True))
            self.assertEqual(hs.call_count, 20)
            r = self.como("adm", "post", "/api/contas", json=dict(NOVO_FUNC, usuario="rui.caixa99"))
            self.assertEqual(r.status_code, 429)
            self.assertEqual(hs.call_count, 20)
            # trocar de IP não zera: o limite também é por conta
            r = self.como("adm", "post", "/api/contas", ip=71, json=dict(NOVO_FUNC, usuario="rui.caixa98"))
            self.assertEqual(r.status_code, 429)
        self.assertIsNone(server.conta_por_usuario("rui.caixa99"))


# ---------------------------------------------------------------------------------------------
# Permissões e listagem
# ---------------------------------------------------------------------------------------------
class TestePermissoes(BaseRotas):
    def test_funcionario_403(self) -> None:
        for metodo, caminho, corpo in (("get", "/api/contas", None), ("post", "/api/contas", NOVO_FUNC),
                                       ("post", f"/api/contas/{self.func['id']}",
                                        {"versao": self.func["versao"], "nome": "Outro"})):
            with self.subTest(metodo=metodo, caminho=caminho):
                kw = {} if corpo is None else {"json": corpo}
                self.assert_codigo(self.como("funcionario", metodo, caminho, **kw), 403, "sem_permissao")
        self.assertIsNone(server.conta_por_usuario("rui.caixa"))
        self.assertEqual(server.conta_por_id(self.func["id"])["nome"], self.func["nome"])

    def test_listagem_gerente_ve_todas_e_edita_so_funcionario(self) -> None:
        r = self.como("gerente", "get", "/api/contas")
        self.assertEqual(r.status_code, 200)
        corpo = r.get_json()
        self.assertTrue(corpo["ok"])
        self.assertIsInstance(corpo["versao"], int)
        por_id = {c["id"]: c for c in corpo["contas"]}
        self.assertEqual(set(por_id), {self.adm["id"], self.ger["id"], self.func["id"]})
        self.assertEqual({k: c["pode_editar"] for k, c in por_id.items()},
                         {self.adm["id"]: False, self.ger["id"]: False, self.func["id"]: True})
        # o ADM edita todas, menos a própria conta
        por_id = {c["id"]: c for c in self.como("adm", "get", "/api/contas").get_json()["contas"]}
        self.assertEqual({k: c["pode_editar"] for k, c in por_id.items()},
                         {self.adm["id"]: False, self.ger["id"]: True, self.func["id"]: True})

    def test_adm_promove_e_rebaixa(self) -> None:
        r = self.alterar("adm", self.func["id"], perfil="gerente")
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        self.assertEqual(r.get_json()["conta"]["perfil"], "gerente")
        self.assertEqual(server.conta_por_id(self.func["id"])["perfil"], "gerente")
        # mudar o perfil derruba a sessão da conta (o perfil novo vale no próximo login)
        self.assert_sem_sessao(self.tok["funcionario"])
        r = self.alterar("adm", self.func["id"], perfil="funcionario")
        self.assertEqual((r.status_code, r.get_json()["conta"]["perfil"]), (200, "funcionario"))
        r = self.alterar("adm", self.ger["id"], perfil="adm")
        self.assertEqual((r.status_code, r.get_json()["conta"]["perfil"]), (200, "adm"))
        r = self.alterar("adm", self.ger["id"], perfil="gerente")
        self.assertEqual((r.status_code, r.get_json()["conta"]["perfil"]), (200, "gerente"))

    def test_gerente_nao_promove_nem_altera_gerente_ou_adm(self) -> None:
        self.assert_codigo(self.alterar("gerente", self.func["id"], perfil="gerente"), 403, "sem_permissao")
        self.assert_codigo(self.alterar("gerente", self.adm["id"], nome="Outro"), 403, "sem_permissao")
        outro = self.criar("gui.gerente", "gerente", SENHA_OUTRA)
        self.assert_codigo(self.alterar("gerente", outro["id"], ativa=False), 403, "sem_permissao")
        self.assertEqual(server.conta_por_id(self.func["id"])["perfil"], "funcionario")
        self.assertTrue(server.conta_por_id(outro["id"])["ativa"])
        # nome/função do funcionário: pode
        r = self.alterar("gerente", self.func["id"], nome="Fia Fictícia", funcao="Balcão")
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        self.assertEqual((r.get_json()["conta"]["nome"], r.get_json()["conta"]["funcao"]), ("Fia Fictícia", "Balcão"))
        # editar nome não derruba a sessão
        self.assertEqual(self.como("funcionario", "get", "/api/mensagens").status_code, 200)

    def test_conta_inexistente_ou_id_torto_404_e_corpo_estrito(self) -> None:
        for conta_id in ("abcd1234", "ABCD1234", "x" * 8):
            with self.subTest(conta_id=conta_id):
                r = self.como("adm", "post", f"/api/contas/{conta_id}", json={"versao": 1, "nome": "Outro"})
                self.assert_codigo(r, 404, "nao_encontrada")
        r = self.alterar("adm", self.func["id"], usuario="outro.login")
        self.assertEqual((r.status_code, r.get_json()["error"]), (400, "Campo desconhecido."))
        r = self.como("adm", "post", f"/api/contas/{self.func['id']}", json={"nome": "Outro"})
        self.assert_codigo(r, 400, "ausente", "versao")
        self.assert_codigo(self.alterar("adm", self.func["id"]), 400, "vazio")

    def test_nao_desativa_a_si_mesmo(self) -> None:
        for perfil, conta, mudanca in (("adm", self.adm, {"ativa": False}), ("adm", self.adm, {"perfil": "gerente"}),
                                       ("adm", self.adm, {"nome": "Outro"}),
                                       ("gerente", self.ger, {"ativa": False}),
                                       ("gerente", self.ger, {"perfil": "funcionario"})):
            with self.subTest(perfil=perfil, mudanca=mudanca):
                self.assert_codigo(self.alterar(perfil, conta["id"], **mudanca), 409, "propria_conta")
        self.assertTrue(server.conta_por_id(self.adm["id"])["ativa"])
        self.assertEqual(server.conta_por_id(self.ger["id"])["perfil"], "gerente")
        self.assertEqual(self.como("adm", "get", "/api/mensagens").status_code, 200)   # sessão continua

    def test_ultimo_adm_409(self) -> None:
        """Dois ADMs rebaixando um ao outro ao mesmo tempo: o segundo pedido é 409 "ultimo_adm"."""
        bia = self.criar("bia.adm", "adm", SENHA_OUTRA)
        original = server.alterar_conta
        feito: list[bool] = []

        def corrida(*a: Any, **kw: Any) -> dict:
            if not feito:   # "outro aparelho": a Bia rebaixa a Ana logo antes do pedido da Ana chegar ao lock
                feito.append(True)
                original(self.adm["id"], {"perfil": "gerente"}, self.versao_de(self.adm["id"]),
                         server.conta_por_id(bia["id"]))
            return original(*a, **kw)

        with mock.patch.object(server, "alterar_conta", corrida):
            r = self.alterar("adm", bia["id"], perfil="gerente")
        self.assert_codigo(r, 409, "ultimo_adm")
        self.assertEqual(server.conta_por_id(bia["id"])["perfil"], "adm")
        ativos = [c for c in server._contas_arquivo.ler()["contas"] if c["perfil"] == "adm" and c["ativa"]]
        self.assertEqual(len(ativos), 1)

    def test_edicao_concorrente_409_versao_mudou(self) -> None:
        versao = self.versao_de(self.func["id"])
        r = self.como("adm", "post", f"/api/contas/{self.func['id']}", json={"versao": versao, "nome": "Primeiro"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json()["conta"]["versao"], versao + 1)
        r = self.como("gerente", "post", f"/api/contas/{self.func['id']}",
                      json={"versao": versao, "funcao": "Segundo"})
        self.assert_codigo(r, 409, "versao_mudou")
        conta = server.conta_por_id(self.func["id"])
        self.assertEqual((conta["nome"], conta["funcao"]), ("Primeiro", "Equipe"))


# ---------------------------------------------------------------------------------------------
# Sessões derrubadas por quem administra
# ---------------------------------------------------------------------------------------------
class TesteDerrubarSessao(BaseRotas):
    def test_desativar_derruba_sessao_na_hora(self) -> None:
        self.assertEqual(self.como("funcionario", "get", "/api/mensagens").status_code, 200)
        r = self.alterar("gerente", self.func["id"], ativa=False)
        self.assertEqual((r.status_code, r.get_json()["conta"]["ativa"]), (200, False))
        self.assert_sem_sessao(self.tok["funcionario"])
        r = self.login_conta("fia.func", SENHA_FUNC, ip=91)
        self.assertEqual((r.status_code, r.get_json()["error"]), (401, server.MSG_LOGIN_ERRADO))
        # reativar: volta a entrar (a sessão antiga continua morta)
        r = self.alterar("gerente", self.func["id"], ativa=True)
        self.assertEqual(r.status_code, 200)
        self.assert_sem_sessao(self.tok["funcionario"])
        self.assertEqual(self.login_conta("fia.func", SENHA_FUNC, ip=92).status_code, 200)

    def test_trocar_senha_por_gerente_derruba_sessao(self) -> None:
        r = self.alterar("gerente", self.func["id"], senha=SENHA_NOVA)
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        self.assert_sem_sessao(self.tok["funcionario"])
        self.assertEqual(self.login_conta("fia.func", SENHA_FUNC, ip=93).status_code, 401)
        self.assertEqual(self.login_conta("fia.func", SENHA_NOVA, ip=94).status_code, 200)
        # quem trocou continua logado
        self.assertEqual(self.como("gerente", "get", "/api/contas").status_code, 200)


# ---------------------------------------------------------------------------------------------
# Minha senha
# ---------------------------------------------------------------------------------------------
class TesteMinhaSenha(BaseRotas):
    def trocar(self, token: str, atual: Any, nova: Any, ip: int = 70) -> Any:
        return self.como(token, "post", "/api/conta/senha", ip=ip, json={"senha_atual": atual, "senha_nova": nova})

    def test_minha_senha_exige_atual(self) -> None:
        tok = self.tok["funcionario"]
        self.assert_codigo(self.trocar(tok, "senha-errada-ficticia", SENHA_NOVA), 400, "senha_errada", "senha_atual")
        r = self.como(tok, "post", "/api/conta/senha", json={"senha_nova": SENHA_NOVA})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.get_json().get("campo"), "senha_atual")
        r = self.como(tok, "post", "/api/conta/senha", json={"senha_atual": SENHA_FUNC, "senha_nova": SENHA_NOVA,
                                                              "usuario": "x"})
        self.assertEqual((r.status_code, r.get_json()["error"]), (400, "Campo desconhecido."))
        self.assert_codigo(self.trocar(tok, SENHA_FUNC, "curta"), 400, "tamanho", "senha_nova")
        self.assert_codigo(self.trocar(tok, SENHA_FUNC, "FIA.FUNC"), 400, "igual_usuario", "senha_nova")
        self.assert_codigo(self.trocar(tok, SENHA_FUNC, SENHA_FUNC), 400, "mesma_senha", "senha_nova")
        self.assert_codigo(self.trocar(tok, SENHA_FUNC, 12345678), 400, "tipo", "senha_nova")
        # nada mudou e a sessão segue viva
        self.assertTrue(contas.conferir_senha(SENHA_FUNC, server.conta_por_id(self.func["id"])["senha_hash"]))
        self.assertEqual(self.como(tok, "get", "/api/mensagens").status_code, 200)
        # sem sessão: 401 (nunca chega à rota)
        r = self.remoto("post", "/api/conta/senha", json={"senha_atual": SENHA_FUNC, "senha_nova": SENHA_NOVA})
        self.assertEqual(r.status_code, 401)

    def test_minha_senha_5_erros_em_15_min_429(self) -> None:
        with self.relogio():
            tok = self.sessao("fia.func", SENHA_FUNC, 64)   # sessão aberta já no relógio de mentira
            for _ in range(5):
                self.assert_codigo(self.trocar(tok, "senha-errada-ficticia", SENHA_NOVA), 400, "senha_errada")
            with mock.patch.object(contas, "conferir_senha", wraps=contas.conferir_senha) as cs:
                r = self.trocar(tok, SENHA_FUNC, SENHA_NOVA, ip=72)   # mesmo a certa, de outro IP
                self.assertEqual(r.status_code, 429, r.get_data(as_text=True))
                cs.assert_not_called()                                 # nenhum PBKDF2 enquanto bloqueado
            self.rel.avancar(15 * 60 + 1)
            r = self.trocar(tok, SENHA_FUNC, SENHA_NOVA, ip=73)
            self.assertEqual(r.status_code, 200, r.get_data(as_text=True))

    def test_minha_senha_limite_de_taxa(self) -> None:
        tok = self.tok["gerente"]
        codigos = [self.trocar(tok, "", SENHA_NOVA).status_code for _ in range(11)]
        self.assertEqual(codigos, [400] * 10 + [429])

    def test_minha_senha_derruba_outras_sessoes(self) -> None:
        outra = self.sessao("fia.func", SENHA_FUNC, 63)            # o mesmo funcionário noutro aparelho
        atual = self.tok["funcionario"]
        iat = carga_crua(atual)["iat"]
        r = self.trocar(atual, SENHA_FUNC, SENHA_NOVA)
        self.assertEqual((r.status_code, r.get_json()), (200, {"ok": True}))
        novo = cookie_da_resposta(r)
        self.assertIsNotNone(novo)
        self.assertNotEqual(novo, atual)
        carga = carga_crua(novo)
        self.assertEqual((carga["c"], carga["iat"]), (self.func["id"], iat))   # mesma conta, mesmo login original
        self.assertNotIn("l", carga)
        self.assertGreater(max_age(r), server.SESSAO_DURACAO_S - 120)
        # a sessão atual continua (com o cookie novo); as outras caem
        self.assertEqual(self.como(novo, "get", "/api/mensagens").status_code, 200)
        self.assert_sem_sessao(outra)
        self.assert_sem_sessao(atual)
        self.assertEqual(self.login_conta("fia.func", SENHA_FUNC, ip=95).status_code, 401)
        self.assertEqual(self.login_conta("fia.func", SENHA_NOVA, ip=96).status_code, 200)

    def test_minha_senha_no_pc_das_caixas_continua_local(self) -> None:
        r = self.l.post("/api/conta/senha", json={"senha_atual": SENHA_ADM, "senha_nova": SENHA_NOVA},
                        headers={"Cookie": f"{COOKIE}={self.token_adm}"})
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        novo = cookie_da_resposta(r)
        self.assertEqual(carga_crua(novo)["l"], 1)
        self.assertLessEqual(max_age(r), server.SESSAO_LOCAL_OCIOSA_S)
        self.assertEqual(self.local_get("/api/contas", novo).status_code, 200)
        self.assertEqual(self.local_get("/api/contas", self.token_adm).status_code, 401)
        self.assert_sem_sessao(self.tok["adm"])                    # a sessão da rede do ADM caiu


# ---------------------------------------------------------------------------------------------
# Privacidade: respostas, log e auditoria
# ---------------------------------------------------------------------------------------------
class TestePrivacidade(BaseRotas):
    def test_resposta_nunca_tem_hash(self) -> None:
        with self.assertLogs(server.log, level="DEBUG") as comum, \
                self.assertLogs(server.auditoria, level="INFO") as trilha:
            server.log.debug("marcador")
            respostas = [
                self.como("gerente", "get", "/api/contas"),
                self.como("gerente", "post", "/api/contas", json=NOVO_FUNC),
                self.alterar("gerente", self.func["id"], nome="Fia Fictícia", senha=SENHA_NOVA),
                self.como("gerente", "post", "/api/contas", json=dict(NOVO_FUNC, perfil="adm")),
                self.trocar_minha("adm", SENHA_ADM, SENHA_OUTRA + "x"),
                self.trocar_minha("gerente", "senha-errada-ficticia", SENHA_OUTRA + "y"),
            ]
        for r in respostas:
            texto = r.get_data(as_text=True)
            with self.subTest(status=r.status_code):
                self.assertLess(r.status_code, 500)
                for proibido in CAMPOS_PROIBIDOS + ("pbkdf2", SENHA_NOVA, SENHA_OUTRA, SENHA_ADM):
                    self.assertNotIn(proibido, texto)
        tudo = "\n".join(comum.output + trilha.output)
        for proibido in ("rui.caixa", "Rui", "fia.func", "Fia", "gil.gerente", "ana.adm", SENHA_NOVA, SENHA_OUTRA,
                         SENHA_ADM, SENHA_GER, self.func["id"], self.ger["id"], self.adm["id"], "pbkdf2"):
            self.assertNotIn(proibido, tudo)
        acoes = "\n".join(trilha.output)
        for esperado in ("acao=contas_criar", "acao=contas_alterar", "acao=conta_senha", "resultado=ok",
                         "resultado=sem_permissao", "resultado=senha_errada", "perfil=funcionario", "conta=c"):
            self.assertIn(esperado, acoes)

    def trocar_minha(self, perfil: str, atual: str, nova: str) -> Any:
        return self.como(perfil, "post", "/api/conta/senha", json={"senha_atual": atual, "senha_nova": nova})


# ---------------------------------------------------------------------------------------------
# Modo PIN: as rotas de contas não existem para ninguém (nem para o PC das caixas)
# ---------------------------------------------------------------------------------------------
class TesteModoPin(BaseContas):
    def test_modo_pin_409_em_todas_as_rotas(self) -> None:
        server.aplicar_pin(PIN)
        self.assertFalse(server.modo_contas())
        token = cookie_da_resposta(self.login())
        chamadas = (("get", "/api/contas", None), ("post", "/api/contas", dict(NOVO_FUNC, perfil="adm")),
                    ("post", "/api/contas/abcd1234", {"versao": 1, "nome": "Outro"}),
                    ("post", "/api/conta/senha", {"senha_atual": SENHA_FUNC, "senha_nova": SENHA_NOVA}))
        for metodo, caminho, corpo in chamadas:
            kw = {} if corpo is None else {"json": corpo}
            with self.subTest(caminho=caminho, origem="pc"):
                self.assert_modo_pin(getattr(self.l, metodo)(caminho, **kw))
            with self.subTest(caminho=caminho, origem="rede-pin"):
                self.assert_modo_pin(self.remoto(metodo, caminho, headers=self.com_cookie(token), **kw))
            with self.subTest(caminho=caminho, origem="rede-sem-sessao"):
                self.assertEqual(self.remoto(metodo, caminho, **kw).status_code, 401)
        self.assertFalse(server.modo_contas())   # nenhum ADM nasceu pela rede (nem pelo PC) fora do primeiro-adm

    def assert_modo_pin(self, r: Any) -> None:
        self.assertEqual(r.status_code, 409, r.get_data(as_text=True))
        self.assertEqual(r.get_json()["codigo"], "modo_pin")


if __name__ == "__main__":
    unittest.main()
