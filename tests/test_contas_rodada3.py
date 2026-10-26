"""
Testes da rodada 3 das correções da revisão final da v2.8 Parte B (servidor).

  1   N4: repetição de bloco só com o bloco repetido de verdade (len > 2k): senhas fortes de 8 caracteres com símbolos
      voltam a valer; os exemplos fracos continuam recusados.
  2   M1: "Sair" só revoga token VIVO (cópia velha não força logout de ninguém nem a queda geral); retenção curta
      (agora + vida do token + 60 s); TOKENS_REVOGADOS_MAX 50000; logout com limite por IP (20/min).
  3   L1: trocar a própria senha começa uma família de sessão (sid) nova.
  4   L2: passagem PIN -> contas sob um lock e idempotente; na partida, conta + PIN ainda gravado = passagem completa.
  5   N5: acima do teto de 50 MB, "erro" e "sem_internet" também deixam de entrar (ok e interrompido nunca).

  * Base: tests/test_login_contas.py `BaseContas` (temporários, PBKDF2 rebaixado por mock). Nada toca som, sobe
    servidor ou usa as portas 8502/8599. Senhas, usuários e nomes são FICTÍCIOS.

    python -m unittest tests.test_contas_rodada3 -v
"""
from __future__ import annotations

import json
import threading
import unittest
from typing import Any
from unittest import mock

import contas
import server
from tests.test_auth import PIN
from tests.test_login_contas import (ADM, SENHA_ADM, SENHA_GER, SENHA_NOVA, BaseContas, carga_crua,
                                     cookie_da_resposta)

COOKIE = server.COOKIE_SESSAO


# ---------------------------------------------------------------------------------------------
# 1 - N4 sem falso positivo
# ---------------------------------------------------------------------------------------------
class TesteSenhasFortesCurtas(unittest.TestCase):
    def test_fortes_de_8_com_simbolos_valem(self) -> None:
        for s in ("Ab3$xY7!", "Xk9!mQ2#", "Rk7#vP3x", "Zt9#kL2@m"):
            self.assertFalse(contas.senha_fraca(s), s)

    def test_fracas_continuam_recusadas(self) -> None:
        for s in ("12345678!", "!12345678", "123456789a", "a12345678", "1q2w3e4r!", "zaq1xsw2", "11112222",
                  "aaaaaaa1", "00000001", "1-2-3-4-5-6-7-8", "!senha123!", "xabcdefghx", "12345678", "123456789",
                  "password", "PASSWORD", "aaaaaaaa", "        ", "senha123", "Mudar123", "brasil2024",
                  "Flamengo@2026", "abcdefgh", "qwertyui", "87654321", "12121212", "abcabcab", "12341234"):
            self.assertTrue(contas.senha_fraca(s), s)


# ---------------------------------------------------------------------------------------------
# 2 - M1: só token vivo é revogado; retenção; teto; limite do logout
# ---------------------------------------------------------------------------------------------
class TesteSoTokenVivo(BaseContas):
    def setUp(self) -> None:
        super().setUp()
        with server._tokens_revogados_lock:
            server._tokens_revogados.clear()
        self.addCleanup(server._tokens_revogados.clear)

    def _copia_velha_do_adm(self) -> "tuple[str, str]":
        """(cópia velha, sessão viva): o ADM entra pela rede, a cópia fica com outro e o ADM troca a senha."""
        self.criar_adm()
        velha = cookie_da_resposta(self.login_conta("ana.adm", SENHA_ADM, ip=60))
        r = self.remoto("post", "/api/conta/senha", ip=60, json={"senha_atual": SENHA_ADM, "senha_nova": SENHA_NOVA},
                        headers=self.com_cookie(velha))
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        viva = cookie_da_resposta(r)
        self.assertEqual(self.remoto("get", "/api/mensagens", headers=self.com_cookie(velha)).status_code, 401)
        return velha, viva

    def test_sonda_do_red_team_lista_cheia_e_copia_velha(self) -> None:
        velha, viva = self._copia_velha_do_adm()
        self.criar("gil.gerente", "gerente", SENHA_GER)
        with mock.patch.object(server, "TOKENS_REVOGADOS_MAX", 2):
            for i in range(2):   # enche a lista com revogações de verdade (de outra conta)
                t = cookie_da_resposta(self.login_conta("gil.gerente", SENHA_GER, ip=70 + i))
                self.remoto("post", "/api/auth/logout", ip=70 + i, json={}, headers=self.com_cookie(t))
            antes = dict(server._tokens_revogados)
            sv = server.conta_por_usuario("ana.adm")["sessao_v"]
            for _ in range(3):   # o atacante insiste com a cópia velha
                r = self.remoto("post", "/api/auth/logout", ip=80, json={}, headers=self.com_cookie(velha))
                self.assertEqual(r.status_code, 200)
                self.assertEqual(cookie_da_resposta(r), "")                # o cookie é apagado mesmo assim
        self.assertEqual(server._tokens_revogados, antes)
        self.assertEqual(server.conta_por_usuario("ana.adm")["sessao_v"], sv)
        self.assertEqual(self.remoto("get", "/api/mensagens", headers=self.com_cookie(viva)).status_code, 200)

    def test_modo_pin_token_velho_com_lista_cheia_nao_derruba_todos(self) -> None:
        server.aplicar_pin(PIN)
        velha = cookie_da_resposta(self.login(ip=60))
        with server._config_lock:   # a versão de acesso muda por fora: a cópia fica velha
            cfg = server.load_config()
            cfg["acesso_versao"] = server._versao_acesso(cfg) + 1
            server._gravar_config_atomico(cfg)
        viva = cookie_da_resposta(self.login(ip=61))
        versao = server._versao_acesso(server.load_config())
        with mock.patch.object(server, "TOKENS_REVOGADOS_MAX", 0):        # "cheia" desde já
            self.remoto("post", "/api/auth/logout", ip=80, json={}, headers=self.com_cookie(velha))
        self.assertEqual(server._versao_acesso(server.load_config()), versao)
        self.assertEqual(self.remoto("get", "/api/mensagens", ip=61, headers=self.com_cookie(viva)).status_code, 200)

    def test_retencao_curta(self) -> None:
        self.criar_adm()
        with self.relogio():
            rede = cookie_da_resposta(self.login_conta("ana.adm", SENHA_ADM, ip=60))
            local = self.token_adm_no_relogio()
            agora = self.rel.t
            self.assertTrue(server._revogar_token(rede))
            self.assertTrue(server._revogar_token(local))
        fins = {k: v for k, v in server._tokens_revogados.items()}
        self.assertEqual(fins["s:" + carga_crua(rede)["sid"]], agora + server.SESSAO_DURACAO_S + 60)
        self.assertEqual(fins["s:" + carga_crua(local)["sid"]], agora + server.SESSAO_LOCAL_OCIOSA_S + 60)

    def token_adm_no_relogio(self) -> str:
        r = self.login_local("ana.adm", SENHA_ADM)
        self.assertEqual(r.status_code, 200)
        return cookie_da_resposta(r)

    def test_teto_e_limite_do_logout(self) -> None:
        self.assertEqual(server.TOKENS_REVOGADOS_MAX, 50000)
        self.assertEqual(server.LOGOUT_MAX_POR_MIN, 20)
        for _ in range(server.LOGOUT_MAX_POR_MIN):
            self.assertEqual(self.remoto("post", "/api/auth/logout", ip=90, json={}).status_code, 200)
        r = self.remoto("post", "/api/auth/logout", ip=90, json={})
        self.assertEqual(r.status_code, 429)
        self.assertIn("Aguarde", r.get_json()["error"])
        self.assertEqual(cookie_da_resposta(r), "")                          # o navegador esquece o cookie mesmo assim
        self.assertEqual(self.remoto("post", "/api/auth/logout", ip=91, json={}).status_code, 200)   # outro IP

    def test_logout_no_limite_so_revoga_token_vivo(self) -> None:
        """Rodada 4 (I3) mudou a regra da rodada 3: no limite, token VIVO ainda é revogado (200); morto leva 429."""
        self.criar_adm()
        token = cookie_da_resposta(self.login_conta("ana.adm", SENHA_ADM, ip=60))
        with mock.patch.object(server, "LOGOUT_MAX_POR_MIN", 0):
            r = self.remoto("post", "/api/auth/logout", ip=60, json={}, headers=self.com_cookie(token))
            self.assertEqual(r.status_code, 200)
            self.assertEqual(len(server._tokens_revogados), 1)
            r = self.remoto("post", "/api/auth/logout", ip=60, json={}, headers=self.com_cookie(token))   # já morto
        self.assertEqual(r.status_code, 429)
        self.assertEqual(len(server._tokens_revogados), 1)


# ---------------------------------------------------------------------------------------------
# 3 - L1: senha nova = sid novo
# ---------------------------------------------------------------------------------------------
class TesteSidNovoNaTrocaDeSenha(BaseContas):
    def test_sair_com_a_copia_velha_nao_derruba_a_sessao_nova(self) -> None:
        self.criar_adm()
        velha = cookie_da_resposta(self.login_conta("ana.adm", SENHA_ADM, ip=60))
        r = self.remoto("post", "/api/conta/senha", ip=60, json={"senha_atual": SENHA_ADM, "senha_nova": SENHA_NOVA},
                        headers=self.com_cookie(velha))
        nova = cookie_da_resposta(r)
        self.assertNotEqual(carga_crua(nova)["sid"], carga_crua(velha)["sid"])
        self.assertEqual(carga_crua(nova)["iat"], carga_crua(velha)["iat"])   # o teto de 90 dias continua o mesmo
        self.remoto("post", "/api/auth/logout", ip=81, json={}, headers=self.com_cookie(velha))
        self.assertEqual(self.remoto("get", "/api/mensagens", headers=self.com_cookie(nova)).status_code, 200)


# ---------------------------------------------------------------------------------------------
# 4 - L2: passagem PIN -> contas idempotente, sob lock, completa na partida
# ---------------------------------------------------------------------------------------------
class TestePassagemIdempotente(BaseContas):
    def test_passar_duas_vezes_gira_a_chave_uma_vez(self) -> None:
        server.criar_conta(dict(ADM, perfil="adm"), None, primeiro_adm=True)
        server._passar_para_contas()
        cfg1 = server.load_config()
        server._passar_para_contas()
        cfg2 = server.load_config()
        self.assertEqual((cfg2["acesso_chave"], cfg2["acesso_versao"]), (cfg1["acesso_chave"], cfg1["acesso_versao"]))

    def test_retomar_em_paralelo_roda_uma_vez(self) -> None:
        server.aplicar_pin(PIN)
        server.criar_conta(dict(ADM, perfil="adm"), None, primeiro_adm=True)
        versao = server._versao_acesso(server.load_config())
        server._passagem_contas_pendente = True
        self.addCleanup(setattr, server, "_passagem_contas_pendente", False)
        real = server._passar_para_contas
        chamadas: list[int] = []

        def devagar() -> dict:
            chamadas.append(1)
            return real()

        with mock.patch.object(server, "_passar_para_contas", devagar):
            ts = [threading.Thread(target=server._retomar_passagem_contas) for _ in range(4)]
            for t in ts:
                t.start()
            for t in ts:
                t.join(10)
        self.assertEqual(server._versao_acesso(server.load_config()), versao + 1)
        self.assertEqual(len(chamadas), 1)
        self.assertFalse(server._passagem_contas_pendente)

    def test_partida_com_conta_e_pin_faz_a_passagem_completa(self) -> None:
        server.aplicar_pin(PIN)
        server.criar_conta(dict(ADM, perfil="adm"), None, primeiro_adm=True)   # sem a rota: o PIN ficou gravado
        antes = server.load_config()
        self.assertIn("acesso_pin_hash", antes)
        server._garantir_marcador_contas()
        depois = server.load_config()
        self.assertNotIn("acesso_pin_hash", depois)
        self.assertNotEqual(depois["acesso_chave"], antes["acesso_chave"])
        self.assertEqual(depois["acesso_versao"], antes["acesso_versao"] + 1)
        self.assertIsInstance(depois.get(server.CONTAS_MARCADOR), str)

    def test_partida_com_marcador_e_pin_tambem_tira_o_pin(self) -> None:
        server.aplicar_pin(PIN)
        server.criar_conta(dict(ADM, perfil="adm"), None, primeiro_adm=True)
        server.update_config(**{server.CONTAS_MARCADOR: "2026-10-22T10:00:00-03:00"})
        server._garantir_marcador_contas()
        self.assertNotIn("acesso_pin_hash", server.load_config())

    def test_partida_sem_pin_so_grava_o_marcador_e_mantem_as_sessoes(self) -> None:
        self.criar_adm()
        cfg = server.load_config()
        cfg.pop(server.CONTAS_MARCADOR)
        server.save_config(cfg)
        server._garantir_marcador_contas()
        depois = server.load_config()
        self.assertEqual((depois["acesso_chave"], depois["acesso_versao"]), (cfg["acesso_chave"], cfg["acesso_versao"]))
        self.assertIsInstance(depois.get(server.CONTAS_MARCADOR), str)
        self.assertEqual(self.local_get("/api/mensagens", self.token_adm).status_code, 200)


# ---------------------------------------------------------------------------------------------
# 5 - N5: erro/sem_internet também param no teto
# ---------------------------------------------------------------------------------------------
class TesteTetoComFalhas(BaseContas):
    def test_no_teto_so_ok_e_interrompido_entram(self) -> None:
        self.criar_adm()
        conta = server.conta_por_usuario("ana.adm")
        server._historico_cheio_avisado = None
        self.assertEqual(server.HISTORICO_CORTADOS_NO_TETO, frozenset({"ocupado", "limite", "erro", "sem_internet"}))
        with mock.patch.object(server, "_tamanho_arquivo", return_value=server.HISTORICO_TETO_BYTES), \
                self.assertLogs(server.log.name, level="WARNING"):
            for resultado in ("ocupado", "limite", "erro", "sem_internet", "ok", "interrompido"):
                server.registrar_anuncio(conta=conta, texto=resultado, resultado=resultado, origem="rede",
                                         tipo="digitado")
        linhas = server._anuncios_path().read_text(encoding="utf-8").splitlines()
        self.assertEqual([json.loads(x)["resultado"] for x in linhas], ["ok", "interrompido"])


if __name__ == "__main__":
    unittest.main()
