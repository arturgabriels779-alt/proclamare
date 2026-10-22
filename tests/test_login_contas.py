"""
Testes do login por usuário (v2.8 B3): primeiro ADM, sessão por conta, bloqueio por conta, sessão local de 5 min
sem uso, status com conta/permissões e a CLI --redefinir-senha.

  * Base: tests/test_auth.py `BaseAuth` (config.json temporário, relógio injetável `RelogioFalso`, cliente local
    `self.c` com cookies e cliente remoto `self.r` sem cookies). O contas.json acompanha o CONFIG_PATH temporário.
  * O PBKDF2 das contas é rebaixado para 1000 iterações só aqui (velocidade); usuário inexistente continua pagando
    o HASH_FICTICIO (200.000), como em produção.
  * Senhas, usuários e o PIN são FICTÍCIOS. Nenhum teste toca som, sobe servidor nem usa a porta 8502/8599; a CLI
    roda num subprocesso sobre uma cópia temporária do programa, com stdin em pipe e timeout.

    python -m unittest tests.test_login_contas -v
"""
from __future__ import annotations

import base64
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

import contas
import server
from tests.test_auth import HOST_REMOTO, PIN, BaseAuth, ip_remoto

COOKIE = server.COOKIE_SESSAO
SENHA_ADM = "senha-adm-ficticia-1"
SENHA_FUNC = "senha-func-ficticia-2"
SENHA_GER = "senha-ger-ficticia-3"
SENHA_NOVA = "senha-nova-ficticia-9"
ADM = {"usuario": "ana.adm", "nome": "Ana", "funcao": "Dona", "senha": SENHA_ADM}
RAIZ = Path(__file__).resolve().parent.parent


def cookie_da_resposta(resp: Any, nome: str = COOKIE) -> "str | None":
    """Valor do cookie `nome` no Set-Cookie da resposta (None se não veio)."""
    for c in resp.headers.getlist("Set-Cookie"):
        if c.startswith(nome + "="):
            return c.split(";", 1)[0].split("=", 1)[1]
    return None


def max_age(resp: Any, nome: str = COOKIE) -> "int | None":
    for c in resp.headers.getlist("Set-Cookie"):
        if c.startswith(nome + "="):
            for parte in c.split(";"):
                parte = parte.strip()
                if parte.lower().startswith("max-age="):
                    return int(parte.split("=", 1)[1])
    return None


def carga_crua(token: str) -> dict:
    """Carga do token SEM conferir a assinatura (só para ver os campos)."""
    parte = token.split(".")[0]
    return json.loads(base64.urlsafe_b64decode(parte + "=" * (-len(parte) % 4)))


class BaseContas(BaseAuth):
    def setUp(self) -> None:
        super().setUp()
        p = mock.patch.object(contas, "ITERACOES", 1000)
        p.start()
        self.addCleanup(p.stop)
        server._contas_arquivo.resetar()
        self.addCleanup(server._contas_arquivo.resetar)
        # PC da loja SEM cookies automáticos (o Cookie vai explícito): para os testes de relógio da sessão local.
        self.l = server.app.test_client(use_cookies=False)

    # --- utilidades ---------------------------------------------------------------------------
    def criar_adm(self) -> dict:
        """Cria o primeiro ADM pelo PC da loja; guarda o token da sessão em self.token_adm."""
        r = self.c.post("/api/auth/primeiro-adm", json=ADM)
        self.assertEqual(r.status_code, 201, r.get_data(as_text=True))
        self.token_adm = cookie_da_resposta(r)
        self.assertIsNotNone(self.token_adm)
        return r.get_json()["conta"]

    def criar(self, usuario: str, perfil: str, senha: str) -> dict:
        adm = server.conta_por_usuario("ana.adm")
        return server.criar_conta({"usuario": usuario, "nome": usuario.title(), "funcao": "Equipe",
                                   "perfil": perfil, "senha": senha}, adm)

    def login_conta(self, usuario: str, senha: str, ip: "int | str" = 50, **kw: Any) -> Any:
        return self.remoto("post", "/api/auth/login", ip=ip, json={"usuario": usuario, "senha": senha}, **kw)

    def login_local(self, usuario: str, senha: str) -> Any:
        return self.l.post("/api/auth/login", json={"usuario": usuario, "senha": senha})

    def local_get(self, caminho: str, token: "str | None" = None, atividade: bool = False) -> Any:
        headers = {}
        if token:
            headers["Cookie"] = f"{COOKIE}={token}"
        if atividade:
            headers["X-Proclamare-Atividade"] = "1"
        return self.l.get(caminho, headers=headers)


# ---------------------------------------------------------------------------------------------
# Modo PIN (sem contas): nada muda
# ---------------------------------------------------------------------------------------------
class TesteModoPin(BaseContas):
    def test_modo_pin_inalterado_sem_contas(self) -> None:
        server.aplicar_pin(PIN)
        self.assertFalse(server.modo_contas())
        r = self.login()
        self.assertEqual((r.status_code, r.get_json()), (200, {"ok": True}))
        token = cookie_da_resposta(r)
        self.assertNotIn("c", carga_crua(token))                          # token de PIN: sem conta
        self.assertEqual(self.remoto("get", "/api/programacao", headers=self.com_cookie(token)).status_code, 200)
        self.assertEqual(self.l.get("/api/programacao").status_code, 200)  # o PC da loja segue livre
        self.assertEqual(self.remoto("get", "/api/programacao").status_code, 401)
        st = self.l.get("/api/auth/status").get_json()
        self.assertEqual((st["modo_contas"], st["precisa_criar_adm"], st["conta"], st["pode_desligar"]),
                         (False, True, None, True))
        self.assertEqual(st["permissoes"], sorted(contas.PODE["gerente"]))
        st = self.remoto("get", "/api/auth/status", headers=self.com_cookie(token)).get_json()
        self.assertEqual((st["autenticado"], st["precisa_criar_adm"], st["pode_desligar"]), (True, False, False))
        self.assertEqual(st["permissoes"], sorted(contas.PODE["gerente"]))
        st = self.remoto("get", "/api/auth/status").get_json()
        self.assertEqual((st["autenticado"], st["permissoes"]), (False, []))
        # {usuario, senha} em modo PIN continua sendo "PIN inválido"
        self.assertEqual(self.login_conta("ana.adm", SENHA_ADM).status_code, 400)
        with server.app.test_request_context("/", base_url="http://localhost",
                                             environ_base={"REMOTE_ADDR": "127.0.0.1"}):
            self.assertEqual(server.perfil_da_requisicao(), "gerente")
            self.assertIsNone(server.conta_da_requisicao())


# ---------------------------------------------------------------------------------------------
# Primeiro ADM
# ---------------------------------------------------------------------------------------------
class TestePrimeiroAdm(BaseContas):
    def test_primeiro_adm_so_local(self) -> None:
        server.aplicar_pin(PIN)
        token = cookie_da_resposta(self.login())
        r = self.remoto("post", "/api/auth/primeiro-adm", json=ADM, headers=self.com_cookie(token))
        self.assertEqual(r.status_code, 403)                              # com sessão de PIN: a rota diz "só local"
        self.assertEqual(self.remoto("post", "/api/auth/primeiro-adm", json=ADM).status_code, 401)
        self.assertFalse(server.modo_contas())
        # esquema estrito: perfil não é do cliente (é sempre adm) e chave desconhecida = 400
        self.assertEqual(self.c.post("/api/auth/primeiro-adm", json=dict(ADM, perfil="adm")).status_code, 400)
        self.assertEqual(self.c.post("/api/auth/primeiro-adm", json={"usuario": "ana.adm"}).status_code, 400)
        self.assertFalse(server.modo_contas())
        r = self.c.post("/api/auth/primeiro-adm", json=ADM)
        self.assertEqual(r.status_code, 201, r.get_data(as_text=True))
        d = r.get_json()
        self.assertTrue(d["ok"])
        self.assertEqual((d["conta"]["usuario"], d["conta"]["perfil"]), ("ana.adm", "adm"))
        self.assertNotIn("senha_hash", d["conta"])
        self.assertNotIn("sessao_v", d["conta"])
        self.assertNotIn(SENHA_ADM, r.get_data(as_text=True))
        token = cookie_da_resposta(r)
        carga = carga_crua(token)
        self.assertEqual((carga["c"], carga["l"]), (d["conta"]["id"], 1))
        self.assertEqual(max_age(r), server.SESSAO_LOCAL_OCIOSA_S)
        self.assertTrue(server.modo_contas())

    def test_primeiro_adm_derruba_sessao_pin(self) -> None:
        server.aplicar_pin(PIN)
        token = cookie_da_resposta(self.login())
        self.assertEqual(self.remoto("get", "/api/programacao", headers=self.com_cookie(token)).status_code, 200)
        versao = server.load_config()["acesso_versao"]
        self.criar_adm()
        cfg = server.load_config()
        self.assertNotIn("acesso_pin_hash", cfg)
        self.assertGreater(cfg["acesso_versao"], versao)
        self.assertEqual(self.remoto("get", "/api/programacao", headers=self.com_cookie(token)).status_code, 401)
        r = self.login()                                                  # o PIN não vale mais
        self.assertEqual((r.status_code, r.get_json().get("codigo")), (400, "modo_contas"))

    def test_primeiro_adm_segunda_vez_409(self) -> None:
        self.criar_adm()
        r = self.c.post("/api/auth/primeiro-adm", json=dict(ADM, usuario="outro.adm"))
        self.assertEqual((r.status_code, r.get_json()["codigo"]), (409, "ja_existe"))
        self.assertIsNone(server.conta_por_usuario("outro.adm"))

    def test_primeiro_adm_sem_pin_cria_a_chave(self) -> None:
        self.assertNotIn("acesso_chave", server.load_config())            # nunca houve PIN
        self.criar_adm()
        self.assertEqual(self.local_get("/api/programacao", self.token_adm).status_code, 200)


# ---------------------------------------------------------------------------------------------
# Login por usuário e senha
# ---------------------------------------------------------------------------------------------
class TesteLoginContas(BaseContas):
    def setUp(self) -> None:
        super().setUp()
        self.adm = self.criar_adm()
        self.func = self.criar("func.um", "funcionario", SENHA_FUNC)

    def test_login_usuario_senha_ok(self) -> None:
        r = self.login_conta(" Func.UM ", SENHA_FUNC)                     # maiúsculas e espaços nas pontas
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        d = r.get_json()
        self.assertEqual((d["ok"], d["conta"]["id"], d["permissoes"]), (True, self.func["id"], ["anunciar"]))
        self.assertNotIn(SENHA_FUNC, r.get_data(as_text=True))
        token = cookie_da_resposta(r)
        carga = carga_crua(token)
        self.assertEqual((carga["c"], carga["sv"]), (self.func["id"], 0))
        self.assertNotIn("l", carga)
        self.assertEqual(max_age(r), server.SESSAO_DURACAO_S)
        self.assertIsNotNone(cookie_da_resposta(r, server.COOKIE_APARELHO))
        st = self.remoto("get", "/api/auth/status", headers=self.com_cookie(token)).get_json()
        self.assertEqual((st["autenticado"], st["conta"]["usuario"]), (True, "func.um"))
        # o PC da loja também entra (e recebe a sessão curta)
        r = self.login_local("ana.adm", SENHA_ADM)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(max_age(r), server.SESSAO_LOCAL_OCIOSA_S)
        self.assertIsNone(cookie_da_resposta(r, server.COOKIE_APARELHO))

    def test_erro_identico_usuario_inexistente_senha_errada_conta_desativada(self) -> None:
        inativa = self.criar("func.dois", "funcionario", SENHA_FUNC)
        server.alterar_conta(inativa["id"], {"ativa": False}, inativa["versao"], None)
        respostas = [
            self.login_conta("ninguem.aqui", SENHA_FUNC, ip=61),          # usuário inexistente
            self.login_conta("func.um", "senha-errada-ficticia", ip=62),  # senha errada
            self.login_conta("func.dois", SENHA_FUNC, ip=63),             # conta desativada, senha CERTA
            self.login_conta("a b", SENHA_FUNC, ip=64),                    # usuário com formato impossível
        ]
        corpos = [(r.status_code, r.get_data(as_text=True)) for r in respostas]
        self.assertEqual(corpos[0][0], 401)
        self.assertEqual(set(corpos), {corpos[0]})
        self.assertEqual(respostas[0].get_json()["error"], "Usuário ou senha incorretos.")
        for r in respostas:
            self.assertIsNone(cookie_da_resposta(r))

    def test_login_pin_em_modo_contas_400(self) -> None:
        r = self.login(PIN)
        self.assertEqual((r.status_code, r.get_json()["codigo"]), (400, "modo_contas"))
        # chave fora do esquema = 400
        r = self.remoto("post", "/api/auth/login", json={"usuario": "func.um", "senha": SENHA_FUNC, "x": 1})
        self.assertEqual(r.status_code, 400)
        r = self.remoto("post", "/api/auth/login", json={"usuario": "func.um"})
        self.assertEqual(r.status_code, 400)

    def test_bloqueio_por_conta_10_erros(self) -> None:
        with self.relogio():
            # 10 IPs de faixas diferentes: nem o limite por IP (5) nem o da faixa (20) disparam
            codigos = [self.login_conta("func.um", "senha-errada-ficticia", ip=f"10.0.{i}.5").status_code
                       for i in range(10)]
            self.assertEqual(codigos, [401] * 9 + [429])
            r = self.login_conta("func.um", SENHA_FUNC, ip="10.9.9.9")    # senha certa, conta travada
            self.assertEqual(r.status_code, 429)
            corpo = r.get_json()
            # mesmo formato do bloqueio por IP (não revela que é a conta)
            for _ in range(5):
                r_ip = self.login_conta("ana.adm", "senha-errada-ficticia", ip="10.8.8.8")
            self.assertEqual(r_ip.status_code, 429)
            self.assertEqual(set(corpo), set(r_ip.get_json()))
            self.assertTrue(corpo["error"].startswith("Muitas tentativas. Tente novamente em"))
            self.assertEqual(corpo["bloqueado_por_s"], int(server.CONTA_BLOQUEIO_S))
            self.assertNotIn("conta", json.dumps(corpo).lower())
            # usuário inexistente também trava igual (não revela se existe)
            codigos = [self.login_conta("ninguem.aqui", "x" * 8, ip=f"10.1.{i}.5").status_code for i in range(10)]
            self.assertEqual(codigos, [401] * 9 + [429])
            self.rel.avancar(server.CONTA_BLOQUEIO_S + 1)
            self.assertEqual(self.login_conta("func.um", SENHA_FUNC, ip="10.9.9.9").status_code, 200)

    def test_acerto_zera_o_contador_da_conta(self) -> None:
        with self.relogio():
            for i in range(9):
                self.assertEqual(self.login_conta("func.um", "senha-errada", ip=f"10.2.{i}.5").status_code, 401)
            self.assertEqual(self.login_conta("func.um", SENHA_FUNC, ip="10.3.0.5").status_code, 200)
            for i in range(9):
                self.assertEqual(self.login_conta("func.um", "senha-errada", ip=f"10.4.{i}.5").status_code, 401)

    def test_bloqueio_por_ip_continua(self) -> None:
        with self.relogio():
            codigos = [self.login_conta("func.um", "senha-errada-ficticia").status_code for _ in range(5)]
            self.assertEqual(codigos, [401, 401, 401, 401, 429])
            self.assertEqual(self.login_conta("func.um", SENHA_FUNC).status_code, 429)
            self.assertEqual(self.login_conta("func.um", SENHA_FUNC, ip=51).status_code, 200)

    def test_pc_da_loja_tem_limite_local(self) -> None:
        with mock.patch.object(server, "LOGIN_LOCAL_MAX", 3):
            codigos = [self.login_local("func.um", "senha-errada").status_code for _ in range(4)]
        self.assertEqual(codigos, [401, 401, 401, 429])

    def test_token_de_conta_desativada_ou_senha_trocada_cai(self) -> None:
        token = cookie_da_resposta(self.login_conta("func.um", SENHA_FUNC))
        self.assertEqual(self.remoto("get", "/api/mensagens", headers=self.com_cookie(token)).status_code, 200)
        atual = server.conta_por_id(self.func["id"])
        server.alterar_conta(self.func["id"], {"senha": SENHA_NOVA}, atual["versao"], None)   # sessao_v + 1
        self.assertEqual(self.remoto("get", "/api/mensagens", headers=self.com_cookie(token)).status_code, 401)
        token = cookie_da_resposta(self.login_conta("func.um", SENHA_NOVA))
        atual = server.conta_por_id(self.func["id"])
        server.alterar_conta(self.func["id"], {"ativa": False}, atual["versao"], None)
        self.assertEqual(self.remoto("get", "/api/mensagens", headers=self.com_cookie(token)).status_code, 401)


# ---------------------------------------------------------------------------------------------
# Sessão: PC das caixas (5 min sem uso) x rede (14 dias)
# ---------------------------------------------------------------------------------------------
class TesteSessao(BaseContas):
    def test_local_exige_login_em_modo_contas(self) -> None:
        self.criar_adm()
        r = self.l.get("/api/programacao")                                # PC da loja SEM cookie
        self.assertEqual((r.status_code, r.get_json()["auth"]), (401, "pin_requerido"))
        self.assertEqual(self.local_get("/api/programacao", self.token_adm).status_code, 200)
        st = self.l.get("/api/auth/status").get_json()
        self.assertEqual((st["local"], st["autenticado"], st["conta"], st["permissoes"], st["pode_desligar"],
                          st["precisa_criar_adm"]), (True, False, None, [], False, False))
        self.assertNotIn("seguranca", st)                                 # sinais de ataque: só para o ADM
        # as rotas "só local" continuam só locais, mesmo para o ADM logado pela rede
        token = cookie_da_resposta(self.login_conta("ana.adm", SENHA_ADM))
        r = self.remoto("post", "/api/auth/liberar", json={}, headers=self.com_cookie(token))
        self.assertEqual(r.status_code, 403)

    def test_sessao_local_expira_em_300s_sem_atividade(self) -> None:
        with self.relogio():
            self.criar_adm()
            self.rel.avancar(server.SESSAO_LOCAL_OCIOSA_S - 1)
            r = self.local_get("/api/programacao", self.token_adm)        # uso sem atividade (sondagem)
            self.assertEqual(r.status_code, 200)
            self.assertIsNone(cookie_da_resposta(r))                      # não renova
            self.rel.avancar(2)
            self.assertEqual(self.local_get("/api/programacao", self.token_adm).status_code, 401)

    def test_sessao_local_renovada_com_cabecalho_atividade(self) -> None:
        with self.relogio():
            self.criar_adm()
            self.rel.avancar(200)
            r = self.local_get("/api/programacao", self.token_adm, atividade=True)
            self.assertEqual(r.status_code, 200)
            novo = cookie_da_resposta(r)
            self.assertIsNotNone(novo)
            self.assertEqual(max_age(r), server.SESSAO_LOCAL_OCIOSA_S)
            self.assertEqual(carga_crua(novo)["iat"], carga_crua(self.token_adm)["iat"])   # o login original fica
            self.rel.avancar(200)                                         # 400 s desde o login
            self.assertEqual(self.local_get("/api/programacao", self.token_adm).status_code, 401)
            self.assertEqual(self.local_get("/api/programacao", novo).status_code, 200)
            # erro (4xx) não renova
            r = self.local_get("/api/musicas/zzzzzzzzzzzzzzzz/audio", novo, atividade=True)
            self.assertGreaterEqual(r.status_code, 400)
            self.assertIsNone(cookie_da_resposta(r))

    def test_token_local_nao_vale_na_rede_e_vice_versa(self) -> None:
        self.criar_adm()
        self.assertEqual(self.remoto("get", "/api/programacao", headers=self.com_cookie(self.token_adm)).status_code,
                         401)
        token_rede = cookie_da_resposta(self.login_conta("ana.adm", SENHA_ADM))
        self.assertEqual(self.local_get("/api/programacao", token_rede).status_code, 401)

    def test_sessao_rede_14_dias(self) -> None:
        with self.relogio():
            self.criar_adm()
            r = self.login_conta("ana.adm", SENHA_ADM)
            token = cookie_da_resposta(r)
            self.assertEqual(max_age(r), server.SESSAO_DURACAO_S)
            self.rel.avancar(3600)                                        # 1 h sem uso: continua (não é o PC)
            r = self.remoto("get", "/api/programacao", headers=self.com_cookie(token))
            self.assertEqual(r.status_code, 200)
            self.assertIsNone(cookie_da_resposta(r))
            self.rel.avancar(server.SESSAO_DURACAO_S - server.SESSAO_RENOVAR_ABAIXO_S)   # faltam < 7 dias
            r = self.remoto("get", "/api/programacao", headers=self.com_cookie(token))
            self.assertEqual(r.status_code, 200)
            novo = cookie_da_resposta(r)                                  # renovação deslizante, sem cabeçalho
            self.assertIsNotNone(novo)
            carga = carga_crua(novo)
            self.assertEqual((carga["c"], carga["iat"]), (carga_crua(token)["c"], carga_crua(token)["iat"]))
            self.rel.avancar(server.SESSAO_RENOVAR_ABAIXO_S)              # o token antigo venceu; o novo vale
            self.assertEqual(self.remoto("get", "/api/programacao", headers=self.com_cookie(token)).status_code, 401)
            self.assertEqual(self.remoto("get", "/api/programacao", headers=self.com_cookie(novo)).status_code, 200)


# ---------------------------------------------------------------------------------------------
# Status, PIN antigo, revogar/pin/liberar em modo contas
# ---------------------------------------------------------------------------------------------
class TesteStatusERotasDeAcesso(BaseContas):
    def test_status_traz_conta_e_permissoes(self) -> None:
        self.criar_adm()
        self.criar("func.um", "funcionario", SENHA_FUNC)
        self.criar("ger.um", "gerente", SENHA_GER)
        st = self.local_get("/api/auth/status", self.token_adm).get_json()
        self.assertEqual((st["modo_contas"], st["precisa_criar_adm"], st["autenticado"], st["pode_desligar"]),
                         (True, False, True, True))
        self.assertEqual(st["conta"]["usuario"], "ana.adm")
        self.assertNotIn("senha_hash", st["conta"])
        self.assertNotIn("sessao_v", st["conta"])
        self.assertEqual(st["permissoes"], sorted(contas.CAPACIDADES))
        self.assertIn("seguranca", st)                                    # ADM no PC: vê os alertas
        token = cookie_da_resposta(self.login_conta("func.um", SENHA_FUNC))
        st = self.remoto("get", "/api/auth/status", headers=self.com_cookie(token)).get_json()
        self.assertEqual((st["conta"]["perfil"], st["permissoes"], st["pode_desligar"]),
                         ("funcionario", ["anunciar"], False))
        token = cookie_da_resposta(self.login_local("ger.um", SENHA_GER))
        st = self.local_get("/api/auth/status", token).get_json()
        self.assertEqual((st["conta"]["perfil"], st["pode_desligar"]), ("gerente", True))
        self.assertNotIn("seguranca", st)                                 # gerente não vê segurança/rede
        token = cookie_da_resposta(self.login_local("func.um", SENHA_FUNC))
        st = self.local_get("/api/auth/status", token).get_json()
        self.assertEqual(st["pode_desligar"], False)                      # funcionário no PC: não desliga

    def test_cookie_pin_antigo_401_apos_adm(self) -> None:
        server.aplicar_pin(PIN)
        token = cookie_da_resposta(self.login())
        self.criar_adm()
        self.assertEqual(self.remoto("get", "/api/mensagens", headers=self.com_cookie(token)).status_code, 401)
        st = self.remoto("get", "/api/auth/status", headers=self.com_cookie(token)).get_json()
        self.assertEqual((st["autenticado"], st["modo_contas"], st["conta"]), (False, True, None))
        # nem um token "de PIN" assinado com a chave ATUAL (sem conta) vale em modo contas
        forjado = server._emitir_token(server.load_config())[0]
        self.assertEqual(self.remoto("get", "/api/mensagens", headers=self.com_cookie(forjado)).status_code, 401)
        self.assertEqual(self.local_get("/api/mensagens", forjado).status_code, 401)

    def test_pin_revogar_liberar_em_modo_contas(self) -> None:
        self.criar_adm()
        self.criar("ger.um", "gerente", SENHA_GER)
        r = self.c.post("/api/auth/pin", json={"pin": "739104"})
        self.assertEqual((r.status_code, r.get_json()["codigo"]), (409, "modo_contas"))
        token_ger = cookie_da_resposta(self.login_local("ger.um", SENHA_GER))
        cab_ger = {"Cookie": f"{COOKIE}={token_ger}"}
        for rota in ("/api/auth/revogar", "/api/auth/liberar", "/api/auth/alertas/visto"):
            self.assertEqual(self.l.post(rota, json={}).status_code in (401, 403), True, rota)   # sem login
            self.assertEqual(self.l.post(rota, json={}, headers=cab_ger).status_code, 403, rota)  # gerente
        token_rede = cookie_da_resposta(self.login_conta("ana.adm", SENHA_ADM))
        r = self.remoto("post", "/api/auth/revogar", json={}, headers=self.com_cookie(token_rede))
        self.assertEqual(r.status_code, 403)                              # ADM, mas pela rede
        cab_adm = {"Cookie": f"{COOKIE}={self.token_adm}"}
        self.assertEqual(self.l.post("/api/auth/liberar", json={}, headers=cab_adm).status_code, 200)
        self.assertEqual(self.l.post("/api/auth/alertas/visto", json={}, headers=cab_adm).status_code, 200)
        versao = server.load_config()["acesso_versao"]
        r = self.l.post("/api/auth/revogar", json={}, headers=cab_adm)
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        self.assertGreater(server.load_config()["acesso_versao"], versao)
        # TODAS as sessões caíram (a da rede, a do gerente e a do próprio ADM)
        self.assertEqual(self.remoto("get", "/api/mensagens", headers=self.com_cookie(token_rede)).status_code, 401)
        self.assertEqual(self.l.get("/api/mensagens", headers=cab_ger).status_code, 401)
        self.assertEqual(self.l.get("/api/mensagens", headers=cab_adm).status_code, 401)


# ---------------------------------------------------------------------------------------------
# CLI --redefinir-senha (subprocesso, stdin em pipe, servidor desligado, cópia temporária do programa)
# ---------------------------------------------------------------------------------------------
def _porta_sem_ninguem() -> "int | None":
    """Uma porta de 8950 a 8990 em que nada escuta (o _servidor_no_ar da CLI consulta 127.0.0.1:<porta>)."""
    for porta in range(8950, 8991):
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1", porta))
            except OSError:
                continue
            return porta
    return None


class TesteCliRedefinirSenha(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.porta = _porta_sem_ninguem()
        if cls.porta is None:
            raise unittest.SkipTest("nenhuma porta livre entre 8950 e 8990")
        cls.base = Path(tempfile.mkdtemp(prefix="proclamare-cli-senha-"))
        for nome in ("server.py", "musicas.py", "programacao.py", "contas.py", "waitress_limites.py"):
            shutil.copy2(RAIZ / nome, cls.base / nome)
        shutil.copytree(RAIZ / "som", cls.base / "som", ignore=shutil.ignore_patterns("__pycache__"))
        (cls.base / "musicas").mkdir()

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.base, ignore_errors=True)

    def setUp(self) -> None:
        # O contas.json da cópia temporária é criado por ESTE processo (mesmas funções do servidor).
        self._salvo = server.CONFIG_PATH
        server.CONFIG_PATH = self.base / "config.json"
        server._contas_arquivo.resetar()
        self.addCleanup(self._restaurar)
        for arq in ("contas.json", "config.json"):
            (self.base / arq).unlink(missing_ok=True)
        with mock.patch.object(contas, "ITERACOES", 1000):
            self.conta = server.criar_conta(dict(ADM, perfil="adm"), None)
        self.env = {k: v for k, v in os.environ.items() if not k.startswith(("PROCLAMARE_", "ANUNCIADOR_"))}
        self.env.update({"PROCLAMARE_HOST": "127.0.0.1", "PROCLAMARE_PORT": str(self.porta),
                         "PROCLAMARE_MUSICAS_DIR": str(self.base / "musicas"), "PROCLAMARE_NO_BROWSER": "1",
                         "PYTHONIOENCODING": "utf-8"})

    def _restaurar(self) -> None:
        server.CONFIG_PATH = self._salvo
        server._contas_arquivo.resetar()

    def rodar(self, usuario: str, entrada: str) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, "server.py", "--redefinir-senha", usuario], cwd=str(self.base),
                              env=self.env, input=entrada.encode("utf-8"), capture_output=True, timeout=60)

    def conta_no_disco(self) -> dict:
        doc = json.loads((self.base / "contas.json").read_text(encoding="utf-8"))
        return next(c for c in doc["contas"] if c["id"] == self.conta["id"])

    def test_cli_redefinir_senha_pipe(self) -> None:
        r = self.rodar("Ana.Adm", f"{SENHA_NOVA}\n{SENHA_NOVA}\n")
        saida = (r.stdout + r.stderr).decode("utf-8", "replace")
        self.assertEqual(r.returncode, 0, saida)
        self.assertNotIn(SENHA_NOVA, saida)
        disco = self.conta_no_disco()
        self.assertTrue(contas.conferir_senha(SENHA_NOVA, disco["senha_hash"]))
        self.assertFalse(contas.conferir_senha(SENHA_ADM, disco["senha_hash"]))
        self.assertEqual(disco["sessao_v"], self.conta["sessao_v"] + 1)   # derruba as sessões abertas

    def test_cli_senhas_diferentes_ou_usuario_inexistente_nao_muda_nada(self) -> None:
        antes = self.conta_no_disco()["senha_hash"]
        r = self.rodar("ana.adm", f"{SENHA_NOVA}\noutra-coisa-ficticia\n")
        self.assertEqual(r.returncode, 1)
        r = self.rodar("ninguem.aqui", f"{SENHA_NOVA}\n{SENHA_NOVA}\n")
        self.assertEqual(r.returncode, 1)
        r = self.rodar("ana.adm", "curta\ncurta\n")                       # senha fora das regras
        self.assertEqual(r.returncode, 1)
        r = self.rodar("ana.adm", "")                                     # stdin vazio: não trava
        self.assertEqual(r.returncode, 1)
        self.assertEqual(self.conta_no_disco()["senha_hash"], antes)

    def test_cli_pin_recusa_em_modo_contas(self) -> None:
        for opcao, entrada in (("--definir-pin", "739104\n739104\n"), ("--remover-pin", "")):
            r = subprocess.run([sys.executable, "server.py", opcao], cwd=str(self.base), env=self.env,
                               input=entrada.encode("utf-8"), capture_output=True, timeout=60)
            self.assertEqual(r.returncode, 1, opcao)
        cfg = self.base / "config.json"
        self.assertFalse(cfg.exists() and "acesso_pin_hash" in cfg.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
