"""Testes do módulo contas.py (v2.8 B1). Só usuários e senhas fictícios; sem Flask, sem disco."""
import unittest
from unittest import mock

import contas

SENHA = "senha-ficticia-1"  # fictícia


def _conta(i, usuario, perfil="funcionario", ativa=True):
    return {"id": i, "usuario": usuario, "perfil": perfil, "ativa": ativa}


def _dados(**kw):
    base = {"usuario": "maria.souza", "nome": "Maria Souza", "funcao": "Caixa",
            "perfil": "funcionario", "senha": SENHA}
    base.update(kw)
    return base


class SurrogateTests(unittest.TestCase):
    def test_senha_com_surrogate_solto_recusada(self):
        self.assertEqual(contas.validar_conta_nova(_dados(senha="abcdefgh\ud800x"), [])[1:], ("senha", "caractere"))
        self.assertEqual(contas.validar_alteracao({"senha": "abcdefgh\udc00x"})[1:], ("senha", "caractere"))


class MatrizTests(unittest.TestCase):
    def test_matriz_funcionario_so_anuncia(self):
        for cap in contas.CAPACIDADES:
            self.assertEqual(contas.pode("funcionario", cap), cap == "anunciar", cap)

    def test_gerente_sem_som_seguranca_spotify(self):
        for cap in contas.CAPACIDADES:
            esperado = cap not in {"som", "seguranca", "spotify_config"}
            self.assertEqual(contas.pode("gerente", cap), esperado, cap)

    def test_adm_tudo(self):
        for cap in contas.CAPACIDADES:
            self.assertTrue(contas.pode("adm", cap), cap)

    def test_capacidade_desconhecida_falha_fechado(self):
        with self.assertRaises((KeyError, ValueError)):
            contas.pode("adm", "inexistente")
        with self.assertRaises((KeyError, ValueError)):
            contas.pode("root", "anunciar")

    def test_pode_gerenciar(self):
        for alvo in contas.PERFIS:
            self.assertTrue(contas.pode_gerenciar("adm", alvo))
            self.assertEqual(contas.pode_gerenciar("gerente", alvo), alvo == "funcionario")
            self.assertFalse(contas.pode_gerenciar("funcionario", alvo))
        self.assertFalse(contas.pode_gerenciar("xxx", "funcionario"))


class ValidacaoTests(unittest.TestCase):
    def setUp(self):
        # Acelera o PBKDF2 nos testes (exceto no teste de formato).
        p = mock.patch.object(contas, "ITERACOES", 1000)
        p.start()
        self.addCleanup(p.stop)

    def test_usuario_normalizado(self):
        self.assertEqual(contas.normalizar_usuario("  Joao.Silva "), "joao.silva")
        self.assertIsNone(contas.normalizar_usuario("jo"))
        self.assertIsNone(contas.normalizar_usuario("joão"))
        self.assertIsNone(contas.normalizar_usuario(None))
        self.assertIsNone(contas.normalizar_usuario("a" * 31))

    def test_conta_valida(self):
        limpa, campo, motivo = contas.validar_conta_nova(_dados(usuario=" Maria.Souza "), [])
        self.assertIsNone(campo)
        self.assertIsNone(motivo)
        self.assertEqual(set(limpa), {"usuario", "nome", "funcao", "perfil", "senha"})
        self.assertEqual(limpa["usuario"], "maria.souza")

    def test_duplicado_ignora_maiusculas(self):
        limpa, campo, motivo = contas.validar_conta_nova(
            _dados(usuario="MARIA.souza"), [_conta("1", "maria.souza")])
        self.assertIsNone(limpa)
        self.assertEqual((campo, motivo), ("usuario", "duplicado"))

    def test_senha_curta_e_igual_usuario_recusadas(self):
        _, campo, motivo = contas.validar_conta_nova(_dados(senha="curta12"), [])
        self.assertEqual((campo, motivo), ("senha", "tamanho"))
        _, campo, motivo = contas.validar_conta_nova(_dados(senha="x" * 129), [])
        self.assertEqual((campo, motivo), ("senha", "tamanho"))
        _, campo, motivo = contas.validar_conta_nova(
            _dados(usuario="maria.souza", senha="Maria.Souza"), [])
        self.assertEqual((campo, motivo), ("senha", "igual_usuario"))

    def test_nome_com_quebra_de_linha_recusado(self):
        _, campo, motivo = contas.validar_conta_nova(_dados(nome="Maria\nSouza"), [])
        self.assertEqual(campo, "nome")
        self.assertEqual(motivo, "caractere")
        _, campo, _ = contas.validar_conta_nova(_dados(funcao="a" * 41), [])
        self.assertEqual(campo, "funcao")

    def test_perfil_e_chaves_desconhecidas(self):
        _, campo, _ = contas.validar_conta_nova(_dados(perfil="root"), [])
        self.assertEqual(campo, "perfil")
        _, campo, motivo = contas.validar_conta_nova(_dados(extra=1), [])
        self.assertEqual((campo, motivo), ("extra", "desconhecido"))
        d = _dados()
        del d["nome"]
        _, campo, motivo = contas.validar_conta_nova(d, [])
        self.assertEqual((campo, motivo), ("nome", "ausente"))

    def test_limite_50_contas(self):
        existentes = [_conta(str(i), f"user{i:03d}", ativa=bool(i % 2)) for i in range(contas.CONTAS_MAX)]
        _, campo, motivo = contas.validar_conta_nova(_dados(), existentes)
        self.assertEqual((campo, motivo), ("usuario", "limite"))
        limpa, _, _ = contas.validar_conta_nova(_dados(), existentes[:-1])
        self.assertIsNotNone(limpa)

    def test_validar_alteracao(self):
        limpa, campo, motivo = contas.validar_alteracao({"nome": " Ana ", "ativa": False})
        self.assertEqual(limpa, {"nome": "Ana", "ativa": False})
        self.assertIsNone(campo)
        self.assertEqual(contas.validar_alteracao({})[1:], ("corpo", "vazio"))
        self.assertEqual(contas.validar_alteracao({"usuario": "x"})[1:], ("usuario", "desconhecido"))
        self.assertEqual(contas.validar_alteracao({"ativa": 1})[1:], ("ativa", "tipo"))
        self.assertEqual(contas.validar_alteracao({"perfil": "root"})[1:], ("perfil", "invalido"))
        self.assertEqual(contas.validar_alteracao({"senha": "curta"})[1:], ("senha", "tamanho"))
        self.assertEqual(contas.validar_alteracao({"senha": SENHA})[0], {"senha": SENHA})

    def test_hash_e_conferencia(self):
        h = contas.hash_senha(SENHA)
        self.assertTrue(contas.conferir_senha(SENHA, h))
        self.assertFalse(contas.conferir_senha("outra-senha-1", h))
        self.assertNotEqual(h, contas.hash_senha(SENHA))  # sal aleatório
        for ruim in (None, "", "lixo", "pbkdf2_sha256$x$y$z", "pbkdf2_sha256$0$AA$AA",
                     "pbkdf2_sha256$9999999$AA$AA", "md5$1$AA$AA", 123):
            self.assertFalse(contas.conferir_senha(SENHA, ruim))
        self.assertFalse(contas.conferir_senha(SENHA, contas.HASH_FICTICIO))
        self.assertFalse(contas.conferir_senha(123, h))


class HashFormatoTests(unittest.TestCase):
    def test_hash_formato_pbkdf2_200k(self):
        partes = contas.hash_senha(SENHA).split("$")
        self.assertEqual(len(partes), 4)
        self.assertEqual(partes[0], "pbkdf2_sha256")
        self.assertEqual(partes[1], "200000")
        self.assertEqual(len(contas._b64u_dec(partes[2])), 16)
        self.assertEqual(len(contas._b64u_dec(partes[3])), 32)
        self.assertNotIn("=", partes[2] + partes[3])


class RestariaAdmTests(unittest.TestCase):
    def test_restaria_adm_impede_tirar_ultimo(self):
        cs = [_conta("1", "chefe", "adm"), _conta("2", "ger", "gerente"),
              _conta("3", "velho", "adm", ativa=False)]
        self.assertFalse(contas.restaria_adm(cs, "1", None, False))
        self.assertFalse(contas.restaria_adm(cs, "1", "gerente", None))
        self.assertTrue(contas.restaria_adm(cs, "2", "funcionario", None))
        self.assertTrue(contas.restaria_adm(cs, "1", None, None))
        # Reativar o 3º ou promover o 2º permite tirar o 1º.
        self.assertTrue(contas.restaria_adm(cs, "3", None, True))
        cs2 = cs + [_conta("4", "outro", "adm")]
        self.assertTrue(contas.restaria_adm(cs2, "1", "gerente", False))
        # Conta inexistente: nada muda.
        self.assertTrue(contas.restaria_adm(cs, "99", "funcionario", False))
        self.assertFalse(contas.restaria_adm([], "1", None, None))


if __name__ == "__main__":
    unittest.main()
