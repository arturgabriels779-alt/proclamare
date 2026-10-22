"""
Testes do armazenamento de contas (v2.8 B2): contas.json temporário, sem rotas, sem porta, sem som.
Só usuários e senhas fictícios; o PBKDF2 é rebaixado para poucas iterações só nestes testes (velocidade).

    python -m unittest tests.test_contas_armazem -v
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import contas
import server
from tests import isolamento

SENHA = "senha-ficticia-1"       # fictícia
SENHA2 = "outra-senha-ficticia-2"  # fictícia


def _dados(usuario="ana.adm", perfil="adm", **kw):
    base = {"usuario": usuario, "nome": "Ana", "funcao": "Dona", "perfil": perfil, "senha": SENHA}
    base.update(kw)
    return base


class Base(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self._salvo = server.CONFIG_PATH
        server.CONFIG_PATH = self.tmp / "config.json"
        server._contas_arquivo.resetar()
        p = mock.patch.object(contas, "ITERACOES", 1000)
        p.start()
        self.addCleanup(p.stop)

    def tearDown(self) -> None:
        server.CONFIG_PATH = self._salvo
        server._contas_arquivo.resetar()
        self._tmp.cleanup()

    def arquivo(self) -> Path:
        return self.tmp / "contas.json"


class ArmazemTests(Base):
    def test_sem_arquivo_modo_pin(self):
        self.assertFalse(self.arquivo().exists())
        self.assertFalse(server.modo_contas())
        self.assertIsNone(server.conta_por_usuario("ana.adm"))

    def test_criar_ativa_modo_contas(self):
        c = server.criar_conta(_dados(usuario=" Ana.ADM "), None, primeiro_adm=True)
        self.assertTrue(server.modo_contas())
        self.assertRegex(c["id"], r"^[0-9a-f]{8}$")
        self.assertEqual(c["usuario"], "ana.adm")
        self.assertTrue(c["ativa"])
        self.assertEqual(server.conta_por_usuario("ANA.adm")["id"], c["id"])
        self.assertEqual(server.conta_por_id(c["id"])["usuario"], "ana.adm")
        self.assertTrue(contas.conferir_senha(SENHA, c["senha_hash"]))
        # O arquivo existe e não guarda a senha em texto.
        self.assertNotIn(SENHA, self.arquivo().read_text(encoding="utf-8"))
        with self.assertRaises(server.ErroContas) as e:
            server.criar_conta(_dados(usuario="ANA.adm"), c)
        self.assertEqual((e.exception.status, e.exception.codigo), (409, "usuario_repetido"))

    def test_arquivo_corrompido_vira_copia_e_nao_quebra(self):
        self.arquivo().write_text("{nao e json", encoding="utf-8")
        self.assertFalse(server.modo_contas())
        self.assertTrue(list(self.tmp.glob("contas.json.corrompido-*")))

    def test_conta_torta_e_descartada(self):
        boa = server.criar_conta(_dados(), None, primeiro_adm=True)
        doc = json.loads(self.arquivo().read_text(encoding="utf-8"))
        doc["contas"].append({"id": "zz", "usuario": "x"})
        doc["contas"].append("lixo")
        self.arquivo().write_text(json.dumps(doc), encoding="utf-8")
        self.assertEqual([c["id"] for c in server._contas_arquivo.ler()["contas"]], [boa["id"]])

    def test_conta_publica_sem_hash(self):
        c = server.criar_conta(_dados(), None, primeiro_adm=True)
        pub = server.conta_publica(c)
        self.assertNotIn("senha_hash", pub)
        self.assertNotIn("sessao_v", pub)
        self.assertEqual(pub["usuario"], "ana.adm")
        self.assertNotIn(SENHA, json.dumps(pub))

    def test_sessao_v_sobe_ao_trocar_senha(self):
        adm = server.criar_conta(_dados(), None, primeiro_adm=True)
        f = server.criar_conta(_dados("bia.func", "funcionario"), adm)
        self.assertEqual(f["sessao_v"], 0)
        novo = server.alterar_conta(f["id"], {"senha": SENHA2}, f["versao"], adm)
        self.assertEqual((novo["sessao_v"], novo["versao"]), (1, 2))
        self.assertTrue(contas.conferir_senha(SENHA2, novo["senha_hash"]))
        # nome não derruba sessão; desativar derruba.
        novo = server.alterar_conta(f["id"], {"nome": "Bia"}, novo["versao"], adm)
        self.assertEqual(novo["sessao_v"], 1)
        novo = server.alterar_conta(f["id"], {"ativa": False}, novo["versao"], adm)
        self.assertEqual(novo["sessao_v"], 2)

    def test_ultimo_adm_409(self):
        adm = server.criar_conta(_dados(), None, primeiro_adm=True)
        for mud in ({"ativa": False}, {"perfil": "gerente"}):
            with self.assertRaises(server.ErroContas) as e:
                server.alterar_conta(adm["id"], mud, adm["versao"], None, sistema=True)
            self.assertEqual((e.exception.status, e.exception.codigo), (409, "ultimo_adm"))
        outro = server.criar_conta(_dados("cris.adm"), adm)
        server.alterar_conta(adm["id"], {"ativa": False}, adm["versao"], outro)   # agora há outro ADM

    def test_versao_mudou_409(self):
        adm = server.criar_conta(_dados(), None, primeiro_adm=True)
        f = server.criar_conta(_dados("bia.func", "funcionario"), adm)
        server.alterar_conta(f["id"], {"nome": "Bia"}, f["versao"], adm)
        with self.assertRaises(server.ErroContas) as e:
            server.alterar_conta(f["id"], {"nome": "Outra"}, f["versao"], adm)
        self.assertEqual((e.exception.status, e.exception.codigo), (409, "versao_mudou"))

    def test_hierarquia_e_senha_igual_usuario(self):
        adm = server.criar_conta(_dados(), None, primeiro_adm=True)
        ger = server.criar_conta(_dados("gil.ger", "gerente"), adm)
        f = server.criar_conta(_dados("bia.func", "funcionario"), ger)
        for acao in (lambda: server.criar_conta(_dados("x.ger", "gerente"), ger),
                     lambda: server.alterar_conta(adm["id"], {"nome": "Z"}, adm["versao"], ger),
                     lambda: server.alterar_conta(f["id"], {"perfil": "gerente"}, f["versao"], ger)):
            with self.assertRaises(server.ErroContas) as e:
                acao()
            self.assertEqual(e.exception.status, 403)
        with self.assertRaises(server.ErroContas) as e:
            server.alterar_conta(f["id"], {"senha": "bia.func"}, f["versao"], ger)
        self.assertEqual((e.exception.status, e.exception.campo), (400, "senha"))
        with self.assertRaises(server.ErroContas) as e:
            server.alterar_conta("00000000", {"nome": "Z"}, 1, adm)
        self.assertEqual(e.exception.status, 404)

    def test_ator_none_so_para_o_primeiro_adm(self):
        for kw in ({}, {"primeiro_adm": True}):
            with self.assertRaises(server.ErroContas) as e:
                server.criar_conta(_dados("bia.func", "funcionario"), None, **kw)
            self.assertEqual(e.exception.status, 403)
        self.assertFalse(server.modo_contas())
        server.criar_conta(_dados(), None, primeiro_adm=True)
        with self.assertRaises(server.ErroContas) as e:
            server.criar_conta(_dados("cris.adm"), None, primeiro_adm=True)
        self.assertEqual((e.exception.status, e.exception.codigo), (403, "sem_permissao"))

    def test_ator_none_sem_marca_e_403(self):
        """Fix B3: None sozinho nunca vira permissão (criar sem primeiro_adm, alterar sem sistema)."""
        with self.assertRaises(server.ErroContas) as e:
            server.criar_conta(_dados(), None)                            # nem o primeiro ADM sem a marca
        self.assertEqual((e.exception.status, e.exception.codigo), (403, "sem_permissao"))
        self.assertFalse(server.modo_contas())
        adm = server.criar_conta(_dados(), None, primeiro_adm=True)
        with self.assertRaises(server.ErroContas) as e:
            server.criar_conta(_dados("bia.func", "funcionario"), adm, primeiro_adm=True)   # ator + marca
        self.assertEqual(e.exception.status, 403)
        with self.assertRaises(server.ErroContas) as e:
            server.alterar_conta(adm["id"], {"nome": "Z"}, adm["versao"], None)
        self.assertEqual((e.exception.status, e.exception.codigo), (403, "sem_permissao"))
        with self.assertRaises(server.ErroContas) as e:
            server.alterar_conta(adm["id"], {"nome": "Z"}, adm["versao"], adm, sistema=True)
        self.assertEqual(e.exception.status, 403)
        self.assertEqual(server.conta_por_id(adm["id"])["nome"], "Ana")

    def test_403_antes_de_duplicado(self):
        adm = server.criar_conta(_dados(), None, primeiro_adm=True)
        ger = server.criar_conta(_dados("gil.ger", "gerente"), adm)
        with self.assertRaises(server.ErroContas) as e:
            server.criar_conta(_dados("ana.adm", "gerente"), ger)
        self.assertEqual(e.exception.status, 403)

    def test_hash_fora_do_lock(self):
        adm = server.criar_conta(_dados(), None, primeiro_adm=True)
        vistos = []
        real = contas.hash_senha

        def espiao(s):
            vistos.append(server._contas_arquivo.lock._is_owned())
            return real(s)
        with mock.patch.object(contas, "hash_senha", espiao):
            f = server.criar_conta(_dados("bia.func", "funcionario"), adm)
            server.alterar_conta(f["id"], {"senha": SENHA2}, f["versao"], adm)
        self.assertEqual(vistos, [False, False])

    def test_editar_nome_nao_exige_adm_ativo(self):
        adm = server.criar_conta(_dados(), None, primeiro_adm=True)
        doc = json.loads(self.arquivo().read_text(encoding="utf-8"))
        doc["contas"][0]["ativa"] = False
        self.arquivo().write_text(json.dumps(doc), encoding="utf-8")
        novo = server.alterar_conta(adm["id"], {"nome": "Ana Maria"}, adm["versao"], None, sistema=True)
        self.assertEqual(novo["nome"], "Ana Maria")

    def test_limite_de_contas(self):
        adm = server.criar_conta(_dados(), None, primeiro_adm=True)
        for i in range(contas.CONTAS_MAX - 1):
            server.criar_conta(_dados(f"func{i:03d}", "funcionario"), adm)
        with self.assertRaises(server.ErroContas) as e:
            server.criar_conta(_dados("ultimo.func", "funcionario"), adm)
        self.assertEqual((e.exception.status, e.exception.codigo), (409, "limite_contas"))

    def test_erro_contas_no_handler(self):
        with server.app.test_request_context():
            resp, status = server._tratar_erro_contas(server.ErroContas("x", 409, "ultimo_adm", "perfil"))
        self.assertEqual(status, 409)
        self.assertEqual(resp.get_json(), {"ok": False, "error": "x", "codigo": "ultimo_adm", "campo": "perfil"})


class IsolamentoTests(unittest.TestCase):
    def test_isolamento_usa_pasta_temporaria(self):
        """O sandbox dos testes leva o contas.json junto e nunca aponta para a pasta real."""
        salvo = (server.CONFIG_PATH, server.AUDIO_DIR, server._config_cache, server._config_assinatura)
        try:
            base = isolamento.sandbox_de_dados(server)
            self.assertEqual(server._contas_path(), base / "contas.json")
            self.assertTrue(isolamento.dentro_do_temporario(server._contas_path()))
        finally:
            (server.CONFIG_PATH, server.AUDIO_DIR, server._config_cache, server._config_assinatura) = salvo
            server._contas_arquivo.resetar()


if __name__ == "__main__":
    unittest.main()
