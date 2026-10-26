"""
Testes da rodada 4 (últimos ajustes da revisão da v2.8 Parte B, servidor).

  I1  bloco de 4 caracteres escrito 2 vezes (qwerqwer, lojaloja, Ab1!Ab1!) é senha fraca; as fortes curtas continuam.
  I3  o 429 do logout (limite por IP) nunca impede revogar um token AINDA VIVO: quem divide o IP com a vítima não
      consegue, gastando o limite com logouts vazios, fazer o "Sair" dela deixar a cópia valendo.

  * Base: tests/test_login_contas.py `BaseContas`. Nada toca som, sobe servidor ou usa as portas 8502/8599. Senhas,
    usuários e nomes são FICTÍCIOS.

    python -m unittest tests.test_contas_rodada4 -v
"""
from __future__ import annotations

import unittest

import contas
import server
from tests.test_login_contas import SENHA_ADM, BaseContas, cookie_da_resposta


class TesteBlocoDuasVezes(unittest.TestCase):
    FRACAS = ("qwerqwer", "asdfasdf", "abcdabcd", "lojaloja", "1qaz1qaz", "ab12ab12", "Ab1!Ab1!", "abc!abc!")
    FORTES = ("Ab3$xY7!", "Xk9!mQ2#", "Rk7#vP3x", "Zt9#kL2@m", "a$B2c%D4", "Q!w2E#r4")

    def test_bloco_de_4_duas_vezes_e_fraca(self) -> None:
        for s in self.FRACAS:
            self.assertTrue(contas.senha_fraca(s), s)

    def test_fortes_curtas_continuam_valendo(self) -> None:
        for s in self.FORTES:
            self.assertFalse(contas.senha_fraca(s), s)


class TesteLogoutNoLimiteAindaRevoga(BaseContas):
    def setUp(self) -> None:
        super().setUp()
        with server._tokens_revogados_lock:
            server._tokens_revogados.clear()
        self.addCleanup(server._tokens_revogados.clear)
        self.criar_adm()

    def test_vizinho_gasta_o_limite_e_o_sair_da_vitima_ainda_revoga(self) -> None:
        copia = cookie_da_resposta(self.login_conta("ana.adm", SENHA_ADM, ip=90))   # a sessão que alguém copiou
        for _ in range(server.LOGOUT_MAX_POR_MIN):                                # o vizinho no mesmo IP
            self.assertEqual(self.remoto("post", "/api/auth/logout", ip=90, json={}).status_code, 200)
        self.assertEqual(self.remoto("post", "/api/auth/logout", ip=90, json={}).status_code, 429)
        r = self.remoto("post", "/api/auth/logout", ip=90, json={}, headers=self.com_cookie(copia))
        self.assertEqual((r.status_code, r.get_json()), (200, {"ok": True}))
        self.assertEqual(cookie_da_resposta(r), "")
        self.assertEqual(self.remoto("get", "/api/mensagens", ip=91, headers=self.com_cookie(copia)).status_code, 401)

    def test_no_limite_token_morto_ou_ausente_leva_429(self) -> None:
        copia = cookie_da_resposta(self.login_conta("ana.adm", SENHA_ADM, ip=90))
        self.remoto("post", "/api/auth/logout", ip=91, json={}, headers=self.com_cookie(copia))   # já saiu
        for _ in range(server.LOGOUT_MAX_POR_MIN):
            self.remoto("post", "/api/auth/logout", ip=90, json={})
        for cab in ({}, self.com_cookie(copia), self.com_cookie("lixo.lixo")):
            r = self.remoto("post", "/api/auth/logout", ip=90, json={}, headers=cab)
            self.assertEqual((r.status_code, r.get_json()["codigo"]), (429, "limite"))
            self.assertEqual(cookie_da_resposta(r), "")


if __name__ == "__main__":
    unittest.main()
