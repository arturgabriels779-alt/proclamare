"""
Testes da rodada 2 das correções da revisão final da v2.8 Parte B (servidor).

  R1  ordem dos locks (config ANTES de contas): nada lê o config.json segurando o lock das contas; marcador do 1º ADM
      que falhou ao gravar é logado e regravado.
  R2  linhas de login na auditoria: conta=None na falha e o token da conta que ACABOU de entrar no sucesso (nunca a do
      cookie antigo).
  R3  Retry-After: 5 no 429 do "pop" do Tocador.
  N1  "Sair" revoga a FAMÍLIA da sessão (sid), não só o token exato: a renovação deslizante não escapa.
  N2  lista de revogados cheia: nunca descarta revogação viva; encerra as sessões da conta (falha fechada).
  N3  hash antigo (menos iterações) paga a diferença: mesmo custo do hash fictício.
  N4  senhas fracas com 1-2 caracteres nas pontas, símbolos no meio ou só 2 caracteres diferentes.
  N5  linhas "erro"/"sem_internet" do histórico limitadas por IP e por conta (10/min).
  N6  ligação aparelho-conta amarrada à sessao_v: depois de "Sair de todos"/senha nova o aparelho perde o contador.

  * Base: tests/test_login_contas.py `BaseContas` (temporários, PBKDF2 rebaixado por mock). Nada toca som, sobe
    servidor ou usa as portas 8502/8599. Senhas, usuários e nomes são FICTÍCIOS.

    python -m unittest tests.test_contas_rodada2 -v
"""
from __future__ import annotations

import base64
import hashlib
import json
import unittest
from typing import Any
from unittest import mock

import contas
import server
from tests import test_historico as th
from tests.test_login_contas import ADM, SENHA_ADM, SENHA_FUNC, SENHA_GER, BaseContas, carga_crua, cookie_da_resposta

COOKIE = server.COOKIE_SESSAO
ERRADA = "senha-errada-ficticia"


def _aparelho(resp: Any) -> "str | None":
    return cookie_da_resposta(resp, server.COOKIE_APARELHO)


# ---------------------------------------------------------------------------------------------
# R1 - ordem dos locks e marcador que falhou
# ---------------------------------------------------------------------------------------------
class TesteOrdemDosLocks(BaseContas):
    def test_nada_le_o_config_segurando_o_lock_das_contas(self) -> None:
        violacoes: list[str] = []
        real = server.load_config

        def vigia() -> dict:
            if server._contas_arquivo.lock._is_owned():
                violacoes.append("load_config dentro do lock das contas")
            return real()

        with mock.patch.object(server, "load_config", vigia):
            self.criar_adm()                                                     # primeiro ADM (criar_conta)
            ger = self.criar("gil.gerente", "gerente", SENHA_GER)                # criar_conta com ator
            server.alterar_conta(ger["id"], {"funcao": "Gerente"}, ger["versao"],
                                 server.conta_por_usuario("ana.adm"))            # alterar_conta
            tok = cookie_da_resposta(self.login_conta("gil.gerente", SENHA_GER))
            with mock.patch.object(contas, "ITERACOES", 2000):                   # login com regravação do hash
                self.assertEqual(self.login_conta("ana.adm", SENHA_ADM, ip=51).status_code, 200)
            self.remoto("post", "/api/conta/sair-de-todos", json={}, headers=self.com_cookie(tok))
        self.assertEqual(violacoes, [])

    def _falhar_gravacoes(self, n: int) -> Any:
        real = server._gravar_config_atomico
        estado = {"falhas": 0}

        def gravar(cfg: dict) -> None:
            if estado["falhas"] < n:
                estado["falhas"] += 1
                raise OSError("disco ocupado (simulado)")
            real(cfg)

        return mock.patch.object(server, "_gravar_config_atomico", gravar)

    def test_marcador_falhou_uma_vez_e_regravado_na_hora(self) -> None:
        with self._falhar_gravacoes(1), self.assertLogs(server.log.name, level="ERROR"):
            r = self.c.post("/api/auth/primeiro-adm", json=ADM)
        self.assertEqual(r.status_code, 201, r.get_data(as_text=True))
        cfg = server.load_config()
        self.assertIsInstance(cfg.get(server.CONTAS_MARCADOR), str)
        self.assertNotIn("acesso_pin_hash", cfg)

    def test_marcador_que_nao_gravou_e_regravado_na_proxima_requisicao(self) -> None:
        with self._falhar_gravacoes(10), self.assertLogs(server.log.name, level="ERROR"):
            r = self.c.post("/api/auth/primeiro-adm", json=ADM)
        self.assertEqual(r.status_code, 500, r.get_data(as_text=True))
        self.assertTrue(server.modo_contas())                                    # a conta existe
        self.assertIsNone(server.load_config().get(server.CONTAS_MARCADOR))
        self.assertTrue(server._passagem_contas_pendente)
        self.c.get("/api/status")                                                # próxima requisição
        self.assertIsInstance(server.load_config().get(server.CONTAS_MARCADOR), str)
        self.assertFalse(server._passagem_contas_pendente)


# ---------------------------------------------------------------------------------------------
# R2 - auditoria do login
# ---------------------------------------------------------------------------------------------
class TesteAuditoriaDoLogin(BaseContas):
    def test_login_audita_a_conta_que_entrou_nao_a_do_cookie(self) -> None:
        adm = self.criar_adm()
        ger = self.criar("gil.gerente", "gerente", SENHA_GER)
        cab = {"Cookie": f"{COOKIE}={self.token_adm}"}                           # cookie do ADM no navegador
        token_adm = server._token_conta_auditoria(adm["id"])
        token_ger = server._token_conta_auditoria(ger["id"])
        with self.assertLogs(server.auditoria, level="INFO") as trilha:
            self.l.post("/api/auth/login", json={"usuario": "gil.gerente", "senha": ERRADA}, headers=cab)
        self.assertNotIn("conta=", trilha.output[-1])
        with self.assertLogs(server.auditoria, level="INFO") as trilha:
            r = self.l.post("/api/auth/login", json={"usuario": "gil.gerente", "senha": SENHA_GER}, headers=cab)
        self.assertEqual(r.status_code, 200)
        self.assertIn(f"conta={token_ger}", trilha.output[-1])
        self.assertNotIn(token_adm, trilha.output[-1])
        # pela rede, com o cookie do ADM: a mesma regra
        with self.assertLogs(server.auditoria, level="INFO") as trilha:
            self.remoto("post", "/api/auth/login", json={"usuario": "gil.gerente", "senha": ERRADA},
                        headers=self.com_cookie(self.token_adm))
        self.assertNotIn("conta=", trilha.output[-1])


# ---------------------------------------------------------------------------------------------
# R3 - Retry-After no 429 do pop
# ---------------------------------------------------------------------------------------------
class TesteRetryAfterDoPop(BaseContas):
    def test_pop_429_com_retry_after(self) -> None:
        self.criar_adm()
        with mock.patch.object(server, "POP_TOCADOR_MAX_POR_MIN", 2):
            for _ in range(2):
                self.assertEqual(self.l.post("/api/youtube/queue", json={"op": "pop"}).status_code, 200)
            r = self.l.post("/api/youtube/queue", json={"op": "pop"})
        self.assertEqual(r.status_code, 429)
        self.assertEqual(r.headers.get("Retry-After"), "5")


# ---------------------------------------------------------------------------------------------
# N1 / N2 - revogação por família (sid) e lista cheia
# ---------------------------------------------------------------------------------------------
class TesteRevogacaoPorFamilia(BaseContas):
    def setUp(self) -> None:
        super().setUp()
        with server._tokens_revogados_lock:
            server._tokens_revogados.clear()
        self.addCleanup(server._tokens_revogados.clear)
        self.criar_adm()

    def _renovar(self, token: str, ip: int = 50) -> str:
        """Avança 8 dias (falta < 7 para vencer) e faz uma requisição: a renovação deslizante emite outro token."""
        self.rel.avancar(8 * 24 * 3600)
        r = self.remoto("get", "/api/mensagens", ip=ip, headers=self.com_cookie(token))
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        novo = cookie_da_resposta(r)
        self.assertTrue(novo and novo != token)
        return novo

    def test_tokens_levam_sid_e_a_renovacao_o_mantem(self) -> None:
        with self.relogio():
            t1 = cookie_da_resposta(self.login_conta("ana.adm", SENHA_ADM))
            t3 = self._renovar(t1)
        self.assertIsInstance(carga_crua(t1).get("sid"), str)
        self.assertEqual(carga_crua(t1)["sid"], carga_crua(t3)["sid"])
        t2 = cookie_da_resposta(self.login_conta("ana.adm", SENHA_ADM, ip=51))
        self.assertNotEqual(carga_crua(t2)["sid"], carga_crua(t1)["sid"])   # outro login = outra família

    def test_sair_com_o_token_velho_derruba_o_renovado(self) -> None:
        with self.relogio():
            t1 = cookie_da_resposta(self.login_conta("ana.adm", SENHA_ADM))
            t3 = self._renovar(t1)
            self.remoto("post", "/api/auth/logout", json={}, headers=self.com_cookie(t1))
            self.assertEqual(self.remoto("get", "/api/mensagens", headers=self.com_cookie(t3)).status_code, 401)
            self.assertEqual(self.remoto("get", "/api/mensagens", headers=self.com_cookie(t1)).status_code, 401)

    def test_sair_com_o_renovado_derruba_o_velho(self) -> None:
        with self.relogio():
            t1 = cookie_da_resposta(self.login_conta("ana.adm", SENHA_ADM))
            outro = cookie_da_resposta(self.login_conta("ana.adm", SENHA_ADM, ip=51))
            t3 = self._renovar(t1)
            self.remoto("post", "/api/auth/logout", json={}, headers=self.com_cookie(t3))
            self.assertEqual(self.remoto("get", "/api/mensagens", headers=self.com_cookie(t1)).status_code, 401)
            # a outra família (outro aparelho) continua
            self.assertEqual(self.remoto("get", "/api/mensagens", ip=51, headers=self.com_cookie(outro)).status_code,
                             200)

    def test_filtro_de_corpo_grande_recusa_sid_revogado(self) -> None:
        cab = {"Cookie": f"{COOKIE}={self.token_adm}"}
        with mock.patch.object(server, "MUSICAS_ENVIO_HABILITADO", True):
            pedir = lambda: server._corpo_grande_permitido(   # noqa: E731
                "POST", server.ROTA_ENVIO_MUSICAS, {"HOST": "localhost", "COOKIE": f"{COOKIE}={self.token_adm}"},
                "127.0.0.1")
            self.assertTrue(pedir())
            self.l.post("/api/auth/logout", json={}, headers=cab)
            self.assertFalse(pedir())

    def test_token_sem_sid_antigo_continua_valendo_e_sai_pela_assinatura(self) -> None:
        cfg = server.load_config()
        conta = server.conta_por_usuario("ana.adm")
        agora = int(server.time.time())
        carga = {"exp": agora + 3600, "iat": agora, "v": server._versao_acesso(cfg), "c": conta["id"],
                 "sv": conta["sessao_v"]}
        parte = server._b64u(json.dumps(carga, separators=(",", ":")).encode("utf-8"))
        antigo = parte + "." + server._assinar(server._chave_acesso(cfg), parte)
        self.assertEqual(self.remoto("get", "/api/mensagens", headers=self.com_cookie(antigo)).status_code, 200)
        self.remoto("post", "/api/auth/logout", json={}, headers=self.com_cookie(antigo))
        self.assertEqual(self.remoto("get", "/api/mensagens", headers=self.com_cookie(antigo)).status_code, 401)

    def test_lista_cheia_nunca_descarta_revogacao_viva(self) -> None:
        self.criar("gil.gerente", "gerente", SENHA_GER)
        with mock.patch.object(server, "TOKENS_REVOGADOS_MAX", 2):
            saidos = [cookie_da_resposta(self.login_conta("ana.adm", SENHA_ADM, ip=60 + i)) for i in range(2)]
            for t in saidos:
                self.remoto("post", "/api/auth/logout", json={}, headers=self.com_cookie(t))
            antes = dict(server._tokens_revogados)
            ger_a = cookie_da_resposta(self.login_conta("gil.gerente", SENHA_GER, ip=70))
            ger_b = cookie_da_resposta(self.login_conta("gil.gerente", SENHA_GER, ip=71))
            sv = server.conta_por_usuario("gil.gerente")["sessao_v"]
            with self.assertLogs(server.log.name, level="WARNING") as logs:
                self.remoto("post", "/api/auth/logout", ip=70, json={}, headers=self.com_cookie(ger_a))
            self.assertEqual(server._tokens_revogados, antes)              # nenhuma revogação viva saiu
            self.assertTrue(any("cheia" in x for x in logs.output))
        self.assertEqual(server.conta_por_usuario("gil.gerente")["sessao_v"], sv + 1)   # falha fechada
        for t in (ger_a, ger_b):
            self.assertEqual(self.remoto("get", "/api/mensagens", headers=self.com_cookie(t)).status_code, 401)
        for t in saidos:                                                   # continuam revogados
            self.assertEqual(self.remoto("get", "/api/mensagens", headers=self.com_cookie(t)).status_code, 401)


# ---------------------------------------------------------------------------------------------
# N3 - custo igual para hash antigo, fictício e conta desativada
# ---------------------------------------------------------------------------------------------
class TesteCustoIgual(BaseContas):
    def _iteracoes_gastas(self, fn: Any) -> int:
        total = {"it": 0}
        real = hashlib.pbkdf2_hmac

        def contar(nome: str, senha: bytes, sal: bytes, it: int, *a: Any) -> bytes:
            total["it"] += it
            return real(nome, senha, sal, it, *a)

        with mock.patch.object(contas.hashlib, "pbkdf2_hmac", contar):
            fn()
        return total["it"]

    def test_hash_antigo_paga_a_diferenca(self) -> None:
        antigo = contas.hash_senha(SENHA_ADM)                                 # 1000 iterações (mock da base)
        with mock.patch.object(contas, "ITERACOES", 3000):
            contas.hash_ficticio()                                           # (calculado fora da contagem)
            self.assertEqual(self._iteracoes_gastas(lambda: contas.conferir_senha(ERRADA, antigo)), 3000)
            self.assertEqual(self._iteracoes_gastas(lambda: contas.conferir_senha(SENHA_ADM, antigo)), 3000)
            self.assertEqual(self._iteracoes_gastas(
                lambda: contas.conferir_senha(ERRADA, contas.hash_ficticio())), 3000)

    def test_login_inexistente_desativada_e_antiga_custam_o_mesmo(self) -> None:
        self.criar_adm()
        func = self.criar("fia.func", "funcionario", SENHA_FUNC)
        server.alterar_conta(func["id"], {"ativa": False}, func["versao"], server.conta_por_usuario("ana.adm"))
        with mock.patch.object(contas, "ITERACOES", 3000):
            contas.hash_ficticio()
            for usuario, senha in (("ninguem.aqui", ERRADA), ("fia.func", SENHA_FUNC), ("ana.adm", ERRADA)):
                gasto = self._iteracoes_gastas(lambda: self.login_conta(usuario, senha, ip=55))
                self.assertEqual(gasto, 3000, usuario)


# ---------------------------------------------------------------------------------------------
# N4 - senhas fracas disfarçadas
# ---------------------------------------------------------------------------------------------
class TesteSenhaFracaDisfarcada(unittest.TestCase):
    def test_recusa(self) -> None:
        for s in ("12345678!", "!12345678", "123456789a", "a12345678", "1q2w3e4r!", "zaq1xsw2", "11112222",
                  "aaaaaaa1", "00000001", "1-2-3-4-5-6-7-8", "!senha123!", "xabcdefghx"):
            self.assertTrue(contas.senha_fraca(s), s)

    def test_senhas_dos_testes_continuam_boas(self) -> None:
        from tests import test_contas_rotas, test_contas_revisao_final, test_login_contas
        for s in (SENHA_ADM, SENHA_FUNC, SENHA_GER, test_login_contas.SENHA_NOVA, test_contas_rotas.SENHA_OUTRA,
                  test_contas_revisao_final.SENHA_OUTRA, "Casa-Azul-Do-Morro-7", "trem 4 bananas pulando"):
            self.assertFalse(contas.senha_fraca(s), s)


# ---------------------------------------------------------------------------------------------
# N5 - erro/sem_internet limitados no histórico
# ---------------------------------------------------------------------------------------------
class TesteHistoricoDeFalhas(th.BaseComContas):
    def test_erros_em_rajada_tem_teto_ok_nunca(self) -> None:
        cab = {"Cookie": f"{COOKIE}={self.token_adm}"}
        self.falhar = True
        with self.assertLogs(server.log.name, level="ERROR"):
            for _ in range(server.HISTORICO_FALHA_MAX_POR_MIN + 3):
                self.assertEqual(self.l.post("/api/announce", json={"text": "Falha"}, headers=cab).status_code, 500)
        self.falhar = False
        for _ in range(3):
            self.assertEqual(self.l.post("/api/announce", json={"text": "Certo"}, headers=cab).status_code, 200)
        resultados = [e["resultado"] for e in self.linhas()]
        self.assertEqual(resultados.count("erro"), server.HISTORICO_FALHA_MAX_POR_MIN)
        self.assertEqual(resultados.count("ok"), 3)
        self.assertEqual(server.HISTORICO_FALHA_MAX_POR_MIN, 10)


# ---------------------------------------------------------------------------------------------
# N6 - ligação do aparelho amarrada à sessao_v
# ---------------------------------------------------------------------------------------------
class TesteLigacaoComSessao(BaseContas):
    def _ataque(self) -> None:
        for ip, n in ((51, 4), (52, 3), (53, 3)):
            for _ in range(n):
                self.login_conta("ana.adm", ERRADA, ip=ip)

    def test_cookie_guarda_conta_e_sessao(self) -> None:
        self.criar_adm()
        aparelho = _aparelho(self.login_conta("ana.adm", SENHA_ADM, ip=40))
        conta = server.conta_por_usuario("ana.adm")
        self.assertEqual(server._aparelho_contas(aparelho, server.load_config()),
                         ((conta["id"], conta["sessao_v"]),))

    def test_sair_de_todos_tira_o_contador_do_aparelho_roubado(self) -> None:
        self.criar_adm()
        r = self.login_conta("ana.adm", SENHA_ADM, ip=40)
        aparelho, token = _aparelho(r), cookie_da_resposta(r)
        cab_aparelho = {"Cookie": f"{server.COOKIE_APARELHO}={aparelho}"}
        # o dono, de outro aparelho, encerra todas as sessões
        outro = cookie_da_resposta(self.login_conta("ana.adm", SENHA_ADM, ip=41))
        self.remoto("post", "/api/conta/sair-de-todos", ip=41, json={}, headers=self.com_cookie(outro))
        self._ataque()
        # o aparelho roubado (ligação velha) não tem mais contador próprio
        self.assertEqual(self.login_conta("ana.adm", SENHA_ADM, ip=42, headers=cab_aparelho).status_code, 429)
        self.assertEqual(self.remoto("get", "/api/mensagens", headers=self.com_cookie(token)).status_code, 401)

    def test_ligacao_atual_continua_valendo(self) -> None:
        self.criar_adm()
        aparelho = _aparelho(self.login_conta("ana.adm", SENHA_ADM, ip=40))
        self._ataque()
        cab = {"Cookie": f"{server.COOKIE_APARELHO}={aparelho}"}
        self.assertEqual(self.login_conta("ana.adm", SENHA_ADM, ip=42, headers=cab).status_code, 200)

    def test_cookie_com_formato_antigo_sem_sessao_e_ignorado(self) -> None:
        cfg = {"acesso_chave": server._b64u(b"k" * 32), "acesso_versao": 1, "acesso_pin_hash": "x"}
        with mock.patch.object(server, "_sessoes_possiveis", return_value=True):
            dados = {"id": "a" * 16, "exp": int(server.time.time()) + 3600, "v": 1, "c": ["0123abcd"]}
            parte = server._b64u(json.dumps(dados).encode("utf-8"))
            token = parte + "." + server._assinar(server._chave_aparelho(cfg), parte)
            self.assertEqual(server._aparelho_valido(token, cfg), "a" * 16)
            self.assertEqual(server._aparelho_contas(token, cfg), ())


if __name__ == "__main__":
    unittest.main()
