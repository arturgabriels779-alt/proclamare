"""
Testes da v2.8.5: volume da VOZ dos anúncios (config.json["voz_loja"]["voz_volume"], só o ADM muda), separado do
volume do aviso sonoro (`aviso_volume`, v2.8.4).

  * Rota: padrão 100, validação (400), permissão (403 para gerente/funcionário sem gravar nada; ADM 200; modo PIN:
    só o PC das caixas), `permissoes` do /api/auth/status.
  * Reprodução: os TRÊS caminhos da fala (legado online = mp3 no pygame.mixer.music; legado offline = pyttsx3;
    saída fixada = MotorAudio) recebem o volume; 100% não aplica nada; o aviso não muda com `voz_volume` e a fala
    não muda com `aviso_volume`; Programação e "Testar caixas" (teste: true) usam o volume da loja.
NUNCA há som real: `_tocar_aviso` é registrador (tests/__init__.py), a fala é trocada por registradores ou pelo
pygame FALSO do BaseSom.

    python -m unittest tests.test_voz_volume -v
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

import server  # noqa: E402
from tests import test_permissoes as tpm  # noqa: E402
from tests.test_auth import HOST_REMOTO, PIN, ip_remoto  # noqa: E402
from tests.test_login_contas import BaseContas, cookie_da_resposta  # noqa: E402


class TestRotaContas(tpm.BasePermissoes):
    """Modo contas: ADM, gerente e funcionário (cookies do PC `local` e da rede `rede`)."""

    def voz(self, perfil: str, corpo: dict, local: bool = False) -> Any:
        tok = (self.local if local else self.rede)[perfil]
        return self.chamar("POST", "/api/voz", tok, local=local, corpo=corpo)

    def test_padrao_100_e_get_devolve(self) -> None:
        r = self.chamar("GET", "/api/voz", self.rede["adm"], local=False)
        self.assertEqual(r.get_json()["voz_loja"]["voz_volume"], 100)
        self.assertEqual(server.voz_loja()["voz_volume"], 100)
        self.assertEqual(server.VOZ_LOJA_PADRAO["voz_volume"], 100)

    def test_adm_grava_200(self) -> None:
        for local in (False, True):
            r = self.voz("adm", {"voz_volume": 40 if local else 60}, local=local)
            self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
            self.assertEqual(r.get_json()["voz_loja"]["voz_volume"], 40 if local else 60)
        self.assertEqual(server.voz_loja()["voz_volume"], 40)
        r = self.voz("adm", {"voz_volume": 100, "velocidade": 5, "aviso_volume": 30})   # junto com outras chaves
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        v = server.voz_loja()
        self.assertEqual((v["velocidade"], v["voz_volume"], v["aviso_volume"]), (5, 100, 30))

    def test_gravar_voz_volume_nao_mexe_no_aviso_volume(self) -> None:
        self.voz("adm", {"aviso_volume": 30})
        self.voz("adm", {"voz_volume": 50})
        v = server.voz_loja()
        self.assertEqual((v["voz_volume"], v["aviso_volume"]), (50, 30))

    def test_invalidos_400(self) -> None:
        for ruim in (0, 5, 105, 55, "80", True, 80.0, None, [80], 110, -10):
            with self.subTest(v=ruim):
                r = self.voz("adm", {"voz_volume": ruim})
                self.assertEqual(r.status_code, 400, r.get_data(as_text=True))
                self.assertEqual(r.get_json()["campo"], "voz_volume")
        self.assertEqual(server.voz_loja()["voz_volume"], 100)

    def test_gerente_e_funcionario_403_e_nada_e_gravado(self) -> None:
        for perfil in ("gerente", "funcionario"):
            for local in (False, True):
                with self.subTest(perfil=perfil, local=local):
                    r = self.voz(perfil, {"voz_volume": 30, "velocidade": 7}, local=local)
                    self.assertEqual(r.status_code, 403)
                    self.assertEqual(r.get_json()["codigo"], "sem_permissao")
                    v = server.voz_loja()
                    self.assertEqual((v["voz_volume"], v["velocidade"], v["definida"]), (100, 0, False))

    def test_gerente_continua_mudando_as_outras_chaves(self) -> None:
        r = self.voz("gerente", {"velocidade": 7, "aviso": "aviao"})
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        self.assertEqual(r.get_json()["voz_loja"]["voz_volume"], 100)

    def test_volume_ruim_com_gerente_nao_grava(self) -> None:
        r = self.voz("gerente", {"voz_volume": 5})
        self.assertEqual(r.status_code, 400)   # a validação vem antes da permissão
        self.assertFalse(server.voz_loja()["definida"])

    def test_403_repetido_gasta_a_cota_e_vira_429(self) -> None:
        codigos = [self.voz("gerente", {"voz_volume": 20}).status_code
                   for _ in range(server.VOZ_LOJA_ESCRITA_MAX_POR_MIN + 1)]
        self.assertEqual(codigos, [403] * server.VOZ_LOJA_ESCRITA_MAX_POR_MIN + [429])
        self.assertFalse(server.voz_loja()["definida"])

    def test_permissoes_no_status(self) -> None:
        for perfil, esperado in (("adm", True), ("gerente", False), ("funcionario", False)):
            with self.subTest(perfil=perfil):
                r = self.chamar("GET", "/api/auth/status", self.rede[perfil], local=False)
                self.assertEqual(("voz_volume" in r.get_json()["permissoes"]), esperado)

    def test_auditoria_traz_o_numero_sem_nome_de_conta(self) -> None:
        with self.assertLogs(server.auditoria.name, level="INFO") as cap:
            self.voz("adm", {"voz_volume": 70})
        linha = next(x for x in cap.output if "acao=voz_salvar" in x)
        self.assertIn("voz_volume=70", linha)
        self.assertNotIn("ana.adm", linha)


class TestRotaModoPin(BaseContas):
    def test_pc_local_muda_e_remoto_com_pin_nao(self) -> None:
        server.aplicar_pin(PIN)
        self.assertFalse(server.modo_contas())
        token = cookie_da_resposta(self.login())
        cab = {"Cookie": f"{server.COOKIE_SESSAO}={token}"}
        r = self.r.post("/api/voz", json={"voz_volume": 30, "velocidade": 9}, base_url=HOST_REMOTO,
                        environ_base=ip_remoto(50), headers=cab)
        self.assertEqual(r.status_code, 403, r.get_data(as_text=True))
        self.assertEqual(r.get_json()["codigo"], "sem_permissao")
        self.assertFalse(server.voz_loja()["definida"])                 # nem a outra chave foi gravada
        r = self.r.post("/api/voz", json={"velocidade": 9}, base_url=HOST_REMOTO, environ_base=ip_remoto(50),
                        headers=cab)
        self.assertEqual(r.status_code, 200)                              # as outras chaves seguem como antes
        r = self.l.post("/api/voz", json={"voz_volume": 30})
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        self.assertEqual(server.voz_loja()["voz_volume"], 30)

    def test_permissoes_no_status_do_modo_pin(self) -> None:
        server.aplicar_pin(PIN)
        self.assertIn("voz_volume", self.l.get("/api/auth/status").get_json()["permissoes"])
        token = cookie_da_resposta(self.login())
        r = self.r.get("/api/auth/status", base_url=HOST_REMOTO, environ_base=ip_remoto(50),
                       headers={"Cookie": f"{server.COOKIE_SESSAO}={token}"})
        self.assertNotIn("voz_volume", r.get_json().get("permissoes", []))


class TestConfigTorto(unittest.TestCase):
    def test_ausente_ou_invalido_vira_100(self) -> None:
        for bruto in ({}, {"voz_volume": 0}, {"voz_volume": 55}, {"voz_volume": "80"},
                      {"voz_volume": True}, {"voz_volume": 80.0}):
            with self.subTest(bruto=bruto), mock.patch.object(server, "load_config", lambda b=bruto: {"voz_loja": b}):
                self.assertEqual(server.voz_loja()["voz_volume"], 100)
        with mock.patch.object(server, "load_config", lambda: {"voz_loja": {"voz_volume": 30}}):
            self.assertEqual(server.voz_loja()["voz_volume"], 30)
        with mock.patch.object(server, "load_config", side_effect=OSError):
            self.assertEqual(server.voz_loja()["voz_volume"], 100)


if __name__ == "__main__":
    unittest.main()
