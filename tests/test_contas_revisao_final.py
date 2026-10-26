"""
Testes das correções da revisão final da v2.8 Parte B (S1 a S12; o painel é a parte 2).

  S1  contas.json sumido/vazio/estragado com o marcador "contas_desde" = 503 "contas_indisponivel" (nunca o modo PIN
      sem PIN); o Tocador do PC das caixas continua; CLI --esquecer-contas (stdin em pipe + timeout).
  S2  senha fraca (código "fraca") em todos os formulários de senha e na CLI; senha errada no PC conta no painel.
  S3  aparelho conhecido que já entrou na conta tem contador próprio de erros.
  S4  "Sair" revoga o token; POST /api/conta/sair-de-todos.
  S5  teto de 50 MB do anuncios.jsonl (tamanho simulado).
  S6  PBKDF2 600.000 nas senhas novas; login certo regrava o hash antigo sem derrubar a sessão.
  S7  CLI com stdin que não é terminal avisa em stderr.
  S8  "Desconectar todos" em modo contas: opções de revisão padrão false; itens com autor não são pendentes.
  S9  teto do "pop" do Tocador = 120/min.
  S10 auditoria de mensagens/listas/programação/som/Spotify/desligar com conta=c<10 hex>.
  S11 modo PIN não grava histórico.
  S12 aviso do túnel por modo.

  * Base: tests/test_login_contas.py `BaseContas` (config.json/contas.json temporários; PBKDF2 rebaixado para 1000
    iterações por mock, como nos outros testes de contas). Senhas, usuários e nomes são FICTÍCIOS. Nenhum teste toca
    som, sobe servidor nem usa as portas 8502/8599; a CLI roda num subprocesso sobre uma cópia temporária do
    programa, com stdin em pipe e timeout.

    python -m unittest tests.test_contas_revisao_final -v
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

import contas
import server
from tests import test_historico as th
from tests import test_programacao as tp
from tests.test_auth import HOST_REMOTO, PIN, ip_remoto
from tests.test_login_contas import (ADM, RAIZ, SENHA_ADM, SENHA_FUNC, SENHA_GER, SENHA_NOVA, BaseContas,
                                     _porta_sem_ninguem, carga_crua, cookie_da_resposta)

COOKIE = server.COOKIE_SESSAO
SENHA_OUTRA = "senha-outra-ficticia-7"


def _cookie_aparelho(resp: Any) -> "str | None":
    return cookie_da_resposta(resp, server.COOKIE_APARELHO)


# ---------------------------------------------------------------------------------------------
# S1 - contas indisponíveis: falha fechada
# ---------------------------------------------------------------------------------------------
class TesteContasIndisponiveis(BaseContas):
    def setUp(self) -> None:
        super().setUp()
        server._contas_indisponiveis_logado = None
        self.adm = self.criar_adm()

    def sumir(self, como: str) -> None:
        caminho = server._contas_path()
        if como == "apagar":
            caminho.unlink()
        elif como == "vazio":
            caminho.write_text('{"versao": 3, "contas": []}', encoding="utf-8")
        elif como == "zero_bytes":
            caminho.write_bytes(b"")
        else:
            caminho.write_text("{ isto nao e json", encoding="utf-8")
        server._contas_arquivo.resetar()   # como um reinício: o cache em memória não salva ninguém

    def test_marcador_gravado_ao_criar_o_primeiro_adm(self) -> None:
        self.assertIsInstance(server.load_config().get(server.CONTAS_MARCADOR), str)
        self.assertFalse(server.contas_indisponiveis())
        self.assertFalse(self.local_get("/api/auth/status", self.token_adm).get_json()["contas_indisponivel"])

    def _confere_fechado(self) -> None:
        self.assertTrue(server.modo_contas())                         # NUNCA volta ao modo PIN
        self.assertTrue(server.contas_indisponiveis())
        cab_adm = {"Cookie": f"{COOKIE}={self.token_adm}"}
        for r in (self.l.get("/api/programacao"), self.l.get("/api/programacao", headers=cab_adm),
                  self.remoto("get", "/api/mensagens"), self.l.post("/api/announce", json={"text": "Oi"}),
                  self.l.post("/api/auth/login", json={"usuario": "ana.adm", "senha": SENHA_ADM}),
                  self.remoto("post", "/api/auth/login", json={"usuario": "ana.adm", "senha": SENHA_ADM}),
                  self.l.post("/api/auth/pin", json={"pin": "739104"}),
                  self.l.post("/api/auth/revogar", json={}),
                  self.l.post("/api/auth/primeiro-adm", json=dict(ADM, usuario="outro.adm")),
                  self.l.post("/api/youtube/queue", json={"op": "clear"}),
                  self.remoto("get", "/api/youtube/state"), self.l.get("/spotify/login")):
            self.assertEqual(r.status_code, 503, r.get_data(as_text=True))
            corpo = r.get_json()
            self.assertEqual((corpo["ok"], corpo["codigo"]), (False, "contas_indisponivel"))
            self.assertIn("contas.json", corpo["error"])
        # nada foi criado nem virou PIN
        self.assertEqual(server._contas_arquivo.ler()["contas"], [])
        self.assertNotIn("acesso_pin_hash", server.load_config())
        with self.assertRaises(server.ErroContas):
            server.criar_conta(dict(ADM, perfil="adm"), None, primeiro_adm=True)
        # o que continua: status, status de auth (com o aviso), sair, o painel e o Tocador do PC das caixas
        self.assertEqual(self.l.get("/api/status").status_code, 200)
        st = self.l.get("/api/auth/status").get_json()
        self.assertEqual((st["contas_indisponivel"], st["modo_contas"], st["precisa_criar_adm"], st["autenticado"],
                          st["permissoes"]), (True, True, False, False, []))
        self.assertTrue(self.remoto("get", "/api/auth/status").get_json()["contas_indisponivel"])
        self.assertEqual(self.l.post("/api/auth/logout", json={}).status_code, 200)
        self.assertEqual(self.l.get("/").status_code, 200)
        self.assertEqual(self.l.get("/api/youtube/state").status_code, 200)
        self.assertEqual(self.l.post("/api/youtube/queue", json={"op": "pop"}).status_code, 200)
        self.assertEqual(self.l.get("/api/youtube/commands?since=0&wait=0").status_code, 200)

    def test_contas_json_apagado(self) -> None:
        self.sumir("apagar")
        with self.assertLogs(server.log.name, level="ERROR"):
            self._confere_fechado()

    def test_contas_json_sem_contas(self) -> None:
        self.sumir("vazio")
        self._confere_fechado()

    def test_contas_json_zero_bytes(self) -> None:
        self.sumir("zero_bytes")
        self._confere_fechado()

    def test_contas_json_estragado_guarda_a_copia(self) -> None:
        self.sumir("estragado")
        self._confere_fechado()
        self.assertTrue(list(server._contas_path().parent.glob("contas.json.corrompido-*")))

    def test_aviso_no_log_no_maximo_a_cada_10_min(self) -> None:
        self.sumir("apagar")
        with self.assertLogs(server.log.name, level="ERROR") as logs:
            for _ in range(5):
                self.l.get("/api/programacao")
        self.assertEqual(len([x for x in logs.output if "contas.json" in x]), 1)

    def test_horarios_continuam_rodando(self) -> None:
        """O relógio da programação roda no servidor: um anúncio agendado toca mesmo com as contas sumidas."""
        falas: list[str] = []
        for alvo, valor in (("_anunciar", lambda t, *a, **k: (falas.append(t), ("offline", None))[1]),
                            ("_anunciar_na_saida", lambda t, *a, **k: (falas.append(t), ("offline", None, {}))[1]),
                            ("tem_internet", lambda *a, **k: False)):
            p = mock.patch.object(server, alvo, valor)
            p.start()
            self.addCleanup(p.stop)
        rel = tp.Relogio(th.datetime(2026, 10, 19, 21, 44, 0))
        servico = server._prog_criar_servico(agora=rel.agora, monotonic=rel.mono, esperar=rel.esperar,
                                             disparar=lambda f: f())
        salvo = server.PROGRAMACAO
        server.PROGRAMACAO = servico
        self.addCleanup(setattr, server, "PROGRAMACAO", salvo)
        self.addCleanup(servico.parar)
        with server._prog_arquivo.lock:
            server._prog_arquivo.gravar({"versao": 1, "itens": [tp.item("abcd0001", "21:45", dias=[1],
                                                                          alvo="Agendado sem contas")]})
        self.sumir("apagar")
        servico.preparar()
        rel.t = th.datetime(2026, 10, 19, 21, 45, 2)
        servico.varrer()
        servico.drenar()
        self.assertEqual(falas, ["Agendado sem contas"])

    def test_marcador_na_partida_para_contas_antigas(self) -> None:
        cfg = server.load_config()
        cfg.pop(server.CONTAS_MARCADOR)
        server.save_config(cfg)
        self.assertNotIn(server.CONTAS_MARCADOR, server.load_config())
        server._garantir_marcador_contas()
        self.assertIsInstance(server.load_config().get(server.CONTAS_MARCADOR), str)


class TesteCliEsquecerContas(unittest.TestCase):
    """--esquecer-contas e o aviso de stdin sem terminal (S7), num subprocesso com stdin em pipe e timeout."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.porta = _porta_sem_ninguem()
        if cls.porta is None:
            raise unittest.SkipTest("nenhuma porta livre entre 8950 e 8990")
        cls.base = Path(tempfile.mkdtemp(prefix="proclamare-cli-esquecer-"))
        for nome in ("server.py", "musicas.py", "programacao.py", "contas.py", "waitress_limites.py"):
            shutil.copy2(RAIZ / nome, cls.base / nome)
        shutil.copytree(RAIZ / "som", cls.base / "som", ignore=shutil.ignore_patterns("__pycache__"))
        (cls.base / "musicas").mkdir()

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.base, ignore_errors=True)

    def setUp(self) -> None:
        self._salvo = (server.CONFIG_PATH, server._config_cache, server._config_assinatura)
        server.CONFIG_PATH = self.base / "config.json"
        server._config_cache = {}
        server._config_assinatura = None
        server._contas_arquivo.resetar()
        self.addCleanup(self._restaurar)
        for arq in ("contas.json", "config.json"):
            (self.base / arq).unlink(missing_ok=True)
        shutil.rmtree(self.base / "logs", ignore_errors=True)
        with mock.patch.object(contas, "ITERACOES", 1000):
            self.conta = server.criar_conta(dict(ADM, perfil="adm"), None, primeiro_adm=True)
        server.update_config(**{server.CONTAS_MARCADOR: "2026-10-22T10:00:00-03:00"})
        self.env = {k: v for k, v in os.environ.items() if not k.startswith(("PROCLAMARE_", "ANUNCIADOR_"))}
        self.env.update({"PROCLAMARE_HOST": "127.0.0.1", "PROCLAMARE_PORT": str(self.porta),
                         "PROCLAMARE_MUSICAS_DIR": str(self.base / "musicas"), "PROCLAMARE_NO_BROWSER": "1",
                         "PYTHONIOENCODING": "utf-8"})

    def _restaurar(self) -> None:
        server.CONFIG_PATH, server._config_cache, server._config_assinatura = self._salvo
        server._contas_arquivo.resetar()

    def rodar(self, *args: str, entrada: str = "") -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, "server.py", *args], cwd=str(self.base), env=self.env,
                              input=entrada.encode("utf-8"), capture_output=True, timeout=60)

    def marcador(self) -> Any:
        return json.loads((self.base / "config.json").read_text(encoding="utf-8")).get(server.CONTAS_MARCADOR)

    def test_recusa_com_as_contas_no_lugar(self) -> None:
        r = self.rodar("--esquecer-contas", entrada="ESQUECER\n")
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIsNotNone(self.marcador())

    def test_esquece_com_confirmacao_e_audita(self) -> None:
        (self.base / "contas.json").unlink()
        r = self.rodar("--esquecer-contas", entrada="esquecer\n")         # confirmação diferente: nada muda
        self.assertEqual(r.returncode, 1)
        self.assertIsNotNone(self.marcador())
        r = self.rodar("--esquecer-contas", entrada="")                   # stdin vazio: não trava
        self.assertEqual(r.returncode, 1)
        r = self.rodar("--esquecer-contas", entrada="ESQUECER\n")
        saida = r.stdout.decode("utf-8", "replace")
        self.assertEqual(r.returncode, 0, saida + r.stderr.decode("utf-8", "replace"))
        self.assertIsNone(self.marcador())
        self.assertIn("Aviso: a entrada nao e um terminal", r.stderr.decode("utf-8", "replace"))   # S7
        trilha = (self.base / "logs" / "auditoria.log").read_text(encoding="utf-8")
        self.assertIn("acao=contas_esquecidas_cli", trilha)
        # o sistema volta a pedir "Criar conta de administrador" (modo PIN sem PIN)
        server._config_cache, server._config_assinatura = {}, None
        server._contas_arquivo.resetar()
        self.assertFalse(server.modo_contas())
        self.assertFalse(server.contas_indisponiveis())
        r = self.rodar("--esquecer-contas", entrada="ESQUECER\n")         # de novo: nada a esquecer
        self.assertEqual(r.returncode, 1)

    def test_definir_pin_recusa_com_as_contas_sumidas(self) -> None:
        (self.base / "contas.json").unlink()
        r = self.rodar("--definir-pin", entrada="739104\n739104\n")
        self.assertEqual(r.returncode, 1)
        self.assertNotIn("acesso_pin_hash", (self.base / "config.json").read_text(encoding="utf-8"))
        self.assertIn("--esquecer-contas", r.stdout.decode("utf-8", "replace"))

    def test_redefinir_senha_avisa_stdin_e_recusa_senha_fraca(self) -> None:
        antes = json.loads((self.base / "contas.json").read_text(encoding="utf-8"))["contas"][0]["senha_hash"]
        for fraca in ("12345678", "senha123", "ana.adm2026!"):
            r = self.rodar("--redefinir-senha", "ana.adm", entrada=f"{fraca}\n{fraca}\n")
            self.assertEqual(r.returncode, 1, fraca)
            self.assertIn("facil demais", r.stdout.decode("utf-8", "replace"))
            self.assertIn("Aviso: a entrada nao e um terminal", r.stderr.decode("utf-8", "replace"))
        depois = json.loads((self.base / "contas.json").read_text(encoding="utf-8"))["contas"][0]["senha_hash"]
        self.assertEqual(depois, antes)


# ---------------------------------------------------------------------------------------------
# S2 - senha fraca
# ---------------------------------------------------------------------------------------------
class TesteSenhaFraca(BaseContas):
    FRACAS = ("12345678", "123456789", "password", "PASSWORD", "aaaaaaaa", "        ", "senha123", "Mudar123",
              "brasil2024", "Flamengo@2026", "abcdefgh", "qwertyui", "87654321", "12121212", "abcabcab")

    def test_funcao_pura(self) -> None:
        for s in self.FRACAS:
            self.assertTrue(contas.senha_fraca(s), s)
        for s in (SENHA_ADM, SENHA_FUNC, SENHA_NOVA, "Casa-Azul-Do-Morro-7", "trem 4 bananas pulando"):
            self.assertFalse(contas.senha_fraca(s, "ana.adm", "Ana Souza"), s)
        # usuário, parte dele ou 1º nome (4+ letras, sem acento nem maiúscula) dentro da senha
        self.assertTrue(contas.senha_fraca("Xjoao.silvaXyz99", "joao.silva", "Outro Nome"))
        self.assertFalse(contas.senha_fraca("silva!2026x-azul", "joao.silva", None))   # só o usuário inteiro conta
        self.assertTrue(contas.senha_fraca("minha-JOAO-casa", None, "João Pereira"))
        self.assertFalse(contas.senha_fraca("bananada-azul-7", "ana.adm", "Ana"))   # "ana" tem só 3 letras
        self.assertGreaterEqual(len(contas.SENHAS_COMUNS), 200)
        for s in ("12345678", "123456789", "senha123", "mudar123", "brasil"):
            self.assertIn(s, contas.SENHAS_COMUNS)

    def test_validadores_devolvem_fraca(self) -> None:
        dados = {"usuario": "rui.caixa", "nome": "Rui Fictício", "funcao": "Caixa", "perfil": "funcionario"}
        self.assertEqual(contas.validar_conta_nova(dict(dados, senha="12345678"), [])[1:], ("senha", "fraca"))
        self.assertEqual(contas.validar_conta_nova(dict(dados, senha="caixa-do-rui.caixa"), [])[1:],
                         ("senha", "fraca"))
        self.assertEqual(contas.validar_alteracao({"senha": "password"})[1:], ("senha", "fraca"))
        self.assertEqual(contas.validar_conta_nova(dict(dados, senha="RUI.CAIXA"), [])[1:],
                         ("senha", "igual_usuario"))   # igual ao usuário continua com o código de sempre

    def test_primeiro_adm_recusa_fraca(self) -> None:
        r = self.c.post("/api/auth/primeiro-adm", json=dict(ADM, senha="senha123"))
        self.assertEqual(r.status_code, 400, r.get_data(as_text=True))
        corpo = r.get_json()
        self.assertEqual((corpo["codigo"], corpo["campo"], corpo["error"]), ("fraca", "senha", server.MSG_SENHA_FRACA))
        self.assertFalse(server.modo_contas())

    def test_rotas_de_conta_recusam_fraca(self) -> None:
        self.criar_adm()
        cab = {"Cookie": f"{COOKIE}={self.token_adm}"}
        novo = {"usuario": "rui.caixa", "nome": "Rui Fictício", "funcao": "Caixa", "perfil": "funcionario"}
        r = self.l.post("/api/contas", json=dict(novo, senha="abcdefgh"), headers=cab)
        self.assertEqual((r.status_code, r.get_json()["codigo"], r.get_json()["campo"]), (400, "fraca", "senha"))
        r = self.l.post("/api/contas", json=dict(novo, senha=SENHA_OUTRA), headers=cab)
        self.assertEqual(r.status_code, 201, r.get_data(as_text=True))
        conta = r.get_json()["conta"]
        for fraca in ("11111111", "rui.caixa-2026", "o-RUI-chegou"):   # comum; contém o usuário; contém o 1º nome?
            r = self.l.post(f"/api/contas/{conta['id']}", json={"versao": conta["versao"], "senha": fraca}, headers=cab)
            if fraca == "o-RUI-chegou":   # "rui" tem 3 letras: não conta
                self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
                conta = r.get_json()["conta"]
                continue
            self.assertEqual((r.status_code, r.get_json()["codigo"], r.get_json()["campo"]), (400, "fraca", "senha"),
                             fraca)
        # trocar a própria senha
        r = self.l.post("/api/conta/senha", json={"senha_atual": SENHA_ADM, "senha_nova": "qwerty123"}, headers=cab)
        self.assertEqual((r.status_code, r.get_json()["codigo"], r.get_json()["campo"]), (400, "fraca", "senha_nova"))
        r = self.l.post("/api/conta/senha", json={"senha_atual": SENHA_ADM, "senha_nova": "minha-ana.adm-x"},
                        headers=cab)
        self.assertEqual((r.status_code, r.get_json()["codigo"]), (400, "fraca"))
        # a senha já gravada continua valendo (nada é invalidado)
        self.assertEqual(self.login_conta("ana.adm", SENHA_ADM).status_code, 200)

    def test_senha_errada_no_pc_conta_no_painel_de_seguranca(self) -> None:
        self.criar_adm()
        for _ in range(3):
            self.assertEqual(self.login_local("ana.adm", "senha-errada-ficticia").status_code, 401)
        seg = self.local_get("/api/auth/status", self.token_adm).get_json()["seguranca"]
        self.assertEqual((seg["falhas_24h"], seg["falhas_local_24h"]), (3, 3))
        server._seg_flush_falhas()
        guardado = server._seg_ler()
        self.assertEqual((len(guardado["falhas"]), len(guardado["falhas_local"])), (0, 3))
        # a trava diária da rede, refeita do disco ao ligar, não enxerga as falhas do PC
        server._login_restaurar_do_disco()
        with server._login_lock:
            self.assertEqual(len(server._login_dia), 0)


# ---------------------------------------------------------------------------------------------
# S3 - aparelho conhecido com contador próprio
# ---------------------------------------------------------------------------------------------
class TesteAparelhoDoDono(BaseContas):
    def test_dono_no_aparelho_conhecido_entra_depois_de_10_erros_de_outros(self) -> None:
        self.criar_adm()
        r = self.login_conta("ana.adm", SENHA_ADM, ip=40)
        aparelho = _cookie_aparelho(r)
        self.assertIsNotNone(aparelho)
        conta = server.conta_por_usuario("ana.adm")
        self.assertIn((conta["id"], conta["sessao_v"]),   # rodada 2 (N6): a ligação leva a sessao_v
                      server._aparelho_contas(aparelho, server.load_config()))
        # 10 senhas erradas de 3 IPs desconhecidos (abaixo do limite por IP e da faixa)
        for ip, n in ((51, 4), (52, 3), (53, 3)):
            for _ in range(n):
                self.assertIn(self.login_conta("ana.adm", "senha-errada-ficticia", ip=ip).status_code, (401, 429))
        # um aparelho desconhecido (até com a senha certa) continua barrado pelo bloqueio do usuário
        self.assertEqual(self.login_conta("ana.adm", SENHA_ADM, ip=54).status_code, 429)
        # o dono, no aparelho dele e com a senha certa, entra
        cab = {"Cookie": f"{server.COOKIE_APARELHO}={aparelho}"}
        r = self.login_conta("ana.adm", SENHA_ADM, ip=41, headers=cab)
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))

    def test_aparelho_conhecido_de_outra_conta_nao_ganha_contador_proprio(self) -> None:
        self.criar_adm()
        self.criar("gil.gerente", "gerente", SENHA_GER)
        aparelho = _cookie_aparelho(self.login_conta("gil.gerente", SENHA_GER, ip=40))
        for ip, n in ((51, 4), (52, 3), (53, 3)):
            for _ in range(n):
                self.login_conta("ana.adm", "senha-errada-ficticia", ip=ip)
        cab = {"Cookie": f"{server.COOKIE_APARELHO}={aparelho}"}
        self.assertEqual(self.login_conta("ana.adm", SENHA_ADM, ip=41, headers=cab).status_code, 429)

    def test_contador_do_aparelho_tambem_trava_em_10(self) -> None:
        self.criar_adm()
        aparelho = _cookie_aparelho(self.login_conta("ana.adm", SENHA_ADM, ip=40))
        cab = {"Cookie": f"{server.COOKIE_APARELHO}={aparelho}"}
        for i in range(10):   # IPs diferentes: o limite por IP não interfere
            self.login_conta("ana.adm", "senha-errada-ficticia", ip=60 + (i // 4), headers=cab)
        self.assertEqual(self.login_conta("ana.adm", SENHA_ADM, ip=70, headers=cab).status_code, 429)

    def test_cookie_de_aparelho_guarda_no_maximo_5_contas_e_renova_com_elas(self) -> None:
        cfg = {"acesso_chave": server._b64u(b"k" * 32), "acesso_versao": 1, "acesso_pin_hash": "x"}
        with mock.patch.object(server, "_sessoes_possiveis", return_value=True):
            ids = tuple((f"{i:08x}", i) for i in range(7))
            ligadas: tuple[tuple[str, int], ...] = ()
            for c, sv in ids:
                ligadas = server._ligar_conta(ligadas, c, sv)
            self.assertEqual(ligadas, ids[-5:])
            self.assertEqual(server._ligar_conta(ligadas, ids[-1][0], 9)[-1], (ids[-1][0], 9))   # uma por conta
            token = server._emitir_cookie_aparelho(cfg, "a" * 16, ligadas)
            self.assertEqual(server._aparelho_contas(token, cfg), ids[-5:])
            self.assertEqual(server._aparelho_valido(token, cfg), "a" * 16)


# ---------------------------------------------------------------------------------------------
# S4 - sair revoga o token; sair de todos
# ---------------------------------------------------------------------------------------------
class TesteSair(BaseContas):
    def setUp(self) -> None:
        super().setUp()
        with server._tokens_revogados_lock:
            server._tokens_revogados.clear()
        self.addCleanup(server._tokens_revogados.clear)

    def test_logout_invalida_o_token_copiado(self) -> None:
        self.criar_adm()
        token = cookie_da_resposta(self.login_conta("ana.adm", SENHA_ADM))
        cab = {"Cookie": f"{COOKIE}={token}"}
        self.assertEqual(self.remoto("get", "/api/mensagens", headers=cab).status_code, 200)
        r = self.remoto("post", "/api/auth/logout", json={}, headers=cab)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(cookie_da_resposta(r), "")
        self.assertEqual(self.remoto("get", "/api/mensagens", headers=cab).status_code, 401)   # cópia do token
        self.assertEqual(self.remoto("get", "/api/auth/status", headers=cab).get_json()["autenticado"], False)
        # outra sessão da mesma conta continua
        outro = cookie_da_resposta(self.login_conta("ana.adm", SENHA_ADM, ip=51))
        self.assertEqual(self.remoto("get", "/api/mensagens", headers=self.com_cookie(outro)).status_code, 200)

    def test_logout_em_modo_pin_tambem_revoga(self) -> None:
        server.aplicar_pin(PIN)
        token = cookie_da_resposta(self.login())
        self.remoto("post", "/api/auth/logout", json={}, headers=self.com_cookie(token))
        self.assertEqual(self.remoto("get", "/api/mensagens", headers=self.com_cookie(token)).status_code, 401)

    def test_lista_de_revogados_tem_teto_e_descarta_vencidos(self) -> None:
        self.criar_adm()
        with mock.patch.object(server, "TOKENS_REVOGADOS_MAX", 3):
            tokens = [cookie_da_resposta(self.login_conta("ana.adm", SENHA_ADM, ip=60 + i)) for i in range(4)]
            for t in tokens:
                self.assertTrue(server._revogar_token(t))
            self.assertLessEqual(len(server._tokens_revogados), 3)
        self.assertFalse(server._revogar_token("lixo.lixo"))
        self.assertFalse(server._revogar_token(None))
        server._tokens_revogados["vencido"] = server.time.time() - 1      # um que já passou do exp
        self.assertTrue(server._revogar_token(cookie_da_resposta(self.login_conta("ana.adm", SENHA_ADM, ip=70))))
        self.assertNotIn("vencido", server._tokens_revogados)

    def test_sair_de_todos_derruba_todas_as_sessoes_da_conta(self) -> None:
        self.criar_adm()
        self.criar("gil.gerente", "gerente", SENHA_GER)
        celular = cookie_da_resposta(self.login_conta("ana.adm", SENHA_ADM, ip=60))
        tablet = cookie_da_resposta(self.login_conta("ana.adm", SENHA_ADM, ip=61))
        gerente = cookie_da_resposta(self.login_conta("gil.gerente", SENHA_GER, ip=62))
        antes = server.conta_por_usuario("ana.adm")
        with self.assertLogs(server.auditoria, level="INFO") as trilha:
            r = self.remoto("post", "/api/conta/sair-de-todos", ip=60, json={}, headers=self.com_cookie(celular))
        self.assertEqual((r.status_code, r.get_json()), (200, {"ok": True}))
        self.assertEqual(cookie_da_resposta(r), "")                    # o cookie desta sessão é apagado
        depois = server.conta_por_usuario("ana.adm")
        self.assertEqual((depois["sessao_v"], depois["versao"]), (antes["sessao_v"] + 1, antes["versao"]))
        for tok in (celular, tablet):
            self.assertEqual(self.remoto("get", "/api/mensagens", headers=self.com_cookie(tok)).status_code, 401)
        self.assertEqual(self.local_get("/api/mensagens", self.token_adm).status_code, 401)   # a do PC também
        self.assertEqual(self.remoto("get", "/api/mensagens", headers=self.com_cookie(gerente)).status_code, 200)
        self.assertIn("acao=conta_sair_de_todos", trilha.output[-1])
        self.assertIn("resultado=ok", trilha.output[-1])
        self.assertIn("conta=c", trilha.output[-1])
        # a senha continua a mesma: entra de novo
        self.assertEqual(self.login_conta("ana.adm", SENHA_ADM, ip=63).status_code, 200)

    def test_sair_de_todos_regras(self) -> None:
        r = self.l.post("/api/conta/sair-de-todos", json={})          # modo PIN
        self.assertEqual((r.status_code, r.get_json()["codigo"]), (409, "modo_pin"))
        self.criar_adm()
        self.assertEqual(self.remoto("post", "/api/conta/sair-de-todos", json={}).status_code, 401)
        cab = self.com_cookie(cookie_da_resposta(self.login_conta("ana.adm", SENHA_ADM)))
        r = self.remoto("post", "/api/conta/sair-de-todos", json={"x": 1}, headers=cab)
        self.assertEqual(r.status_code, 400)
        self.assertEqual(server.PERMISSOES_ROTAS[("POST", "/api/conta/sair-de-todos")], None)


# ---------------------------------------------------------------------------------------------
# S5 - teto do anuncios.jsonl
# ---------------------------------------------------------------------------------------------
class TesteTetoDoHistorico(BaseContas):
    def test_no_teto_tentativas_e_falhas_deixam_de_entrar(self) -> None:
        """S5 + rodada 3 (N5): no teto, ocupado/limite/erro/sem_internet param; ok e interrompido nunca."""
        self.criar_adm()
        conta = server.conta_por_usuario("ana.adm")
        server._historico_cheio_avisado = None
        cheio = server.HISTORICO_TETO_BYTES
        self.assertEqual(cheio, 50 * 1024 * 1024)
        with mock.patch.object(server, "_tamanho_arquivo", return_value=cheio):
            with self.assertLogs(server.log.name, level="WARNING") as logs:
                for resultado in ("ocupado", "limite", "ocupado"):
                    server.registrar_anuncio(conta=conta, texto="x", resultado=resultado, origem="rede",
                                             tipo="digitado")
            self.assertEqual(len([x for x in logs.output if "cheio" in x]), 1)   # um aviso por hora
            for resultado in ("ok", "erro", "sem_internet", "interrompido"):
                server.registrar_anuncio(conta=conta, texto=resultado, resultado=resultado, origem="rede",
                                         tipo="digitado")
        linhas = server._anuncios_path().read_text(encoding="utf-8").splitlines()
        self.assertEqual([json.loads(x)["resultado"] for x in linhas], ["ok", "interrompido"])
        # abaixo do teto, "ocupado" volta a entrar
        with mock.patch.object(server, "_tamanho_arquivo", return_value=cheio - 1):
            server.registrar_anuncio(conta=conta, texto="y", resultado="ocupado", origem="rede", tipo="digitado")
        self.assertEqual(len(server._anuncios_path().read_text(encoding="utf-8").splitlines()), 3)


# ---------------------------------------------------------------------------------------------
# S6 - PBKDF2 600.000 e regravação do hash antigo
# ---------------------------------------------------------------------------------------------
class TesteIteracoes(BaseContas):
    def test_constantes(self) -> None:
        self.assertEqual(contas.ITERACOES, 1000)   # (rebaixado por mock só nos testes; o valor real: test_contas)
        antigo = f"pbkdf2_sha256$200000${contas._b64u(b'x' * 16)}${contas._b64u(b'y' * 32)}"
        with mock.patch.object(contas, "ITERACOES", 600_000):
            self.assertTrue(contas.precisa_atualizar_hash(antigo))
            self.assertFalse(contas.precisa_atualizar_hash(antigo.replace("$200000$", "$600000$")))
            self.assertFalse(contas.precisa_atualizar_hash("lixo"))
        self.assertEqual(server.PBKDF2_ITERACOES, 200_000)   # o PIN não muda

    def test_hash_ficticio_acompanha_as_iteracoes(self) -> None:
        self.assertEqual(contas.hash_ficticio().split("$")[1], "1000")
        with mock.patch.object(contas, "ITERACOES", 1500):
            self.assertEqual(contas.hash_ficticio().split("$")[1], "1500")
            self.assertEqual(contas.HASH_FICTICIO.split("$")[1], "1500")

    def test_login_certo_regrava_hash_antigo_sem_derrubar(self) -> None:
        self.criar_adm()                                              # hash com 1000 iterações ("antigas")
        antes = server.conta_por_usuario("ana.adm")
        token = cookie_da_resposta(self.login_conta("ana.adm", SENHA_ADM, ip=60))
        with mock.patch.object(contas, "ITERACOES", 2000):            # as iterações "novas"
            self.assertTrue(contas.precisa_atualizar_hash(antes["senha_hash"]))
            self.assertEqual(self.login_conta("ana.adm", "senha-errada-ficticia", ip=61).status_code, 401)
            self.assertEqual(server.conta_por_usuario("ana.adm")["senha_hash"], antes["senha_hash"])  # erro: nada
            r = self.login_conta("ana.adm", SENHA_ADM, ip=62)
            self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
            depois = server.conta_por_usuario("ana.adm")
            self.assertEqual(depois["senha_hash"].split("$")[1], "2000")
            self.assertFalse(contas.precisa_atualizar_hash(depois["senha_hash"]))
        self.assertTrue(contas.conferir_senha(SENHA_ADM, depois["senha_hash"]))
        self.assertEqual((depois["sessao_v"], depois["versao"]), (antes["sessao_v"], antes["versao"]))
        # as sessões abertas antes continuam valendo
        self.assertEqual(self.remoto("get", "/api/mensagens", headers=self.com_cookie(token)).status_code, 200)
        self.assertEqual(self.local_get("/api/mensagens", self.token_adm).status_code, 200)


# ---------------------------------------------------------------------------------------------
# S8 - "Desconectar todos" em modo contas
# ---------------------------------------------------------------------------------------------
class TesteRevisaoEmModoContas(th.BaseComContas):
    def test_revogar_nao_desativa_horario_de_gerente(self) -> None:
        r = self.salvar_prog(self.tok["gerente"], [self.anuncio_item()])
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        item = server._prog_arquivo.ler()["itens"][0]
        self.assertEqual((item["origem"], item["autor_id"]), ("remoto", self.ger["id"]))
        self.assertEqual(server._prog_pendentes(), [])
        cab = {"Cookie": f"{COOKIE}={self.token_adm}"}
        pend = self.l.get("/api/seguranca/remotos", headers=cab).get_json()
        self.assertEqual(pend["pendentes"]["horarios"], 0)
        r = self.l.post("/api/auth/revogar", json={}, headers=cab)
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        self.assertEqual(r.get_json()["revisao"]["horarios_desativados"], 0)
        self.assertTrue(server._prog_arquivo.ler()["itens"][0]["ativo"])

    def test_opcoes_padrao_false_mas_true_explicito_vale_para_itens_sem_autor(self) -> None:
        antigo = dict(tp.item("abcd0001", "21:45", dias=[1], alvo="Antigo"), origem="remoto", criado_por="remoto")
        with server._prog_arquivo.lock:
            server._prog_arquivo.gravar({"versao": 1, "itens": [antigo]})
        self.assertEqual(len(server._prog_pendentes()), 1)            # sem autor (do tempo do PIN): pendente
        with server.app.test_request_context("/"):
            self.assertEqual(server._opcoes_de_revisao({}), dict.fromkeys(server._OPCOES_REVISAO_PADRAO, False))
        cab = {"Cookie": f"{COOKIE}={self.token_adm}"}
        r = self.l.post("/api/auth/revogar", json={}, headers=cab)
        self.assertEqual(r.get_json()["revisao"]["horarios_desativados"], 0)
        self.assertTrue(server._prog_arquivo.ler()["itens"][0]["ativo"])
        token = cookie_da_resposta(self.login_local("ana.adm", SENHA_ADM))   # revogar derrubou a sessão
        r = self.l.post("/api/auth/revogar", json={"desativar_remotos": True},
                        headers={"Cookie": f"{COOKIE}={token}"})
        self.assertEqual(r.get_json()["revisao"]["horarios_desativados"], 1)

    def test_listas_e_mensagens_com_autor_nao_sao_pendentes(self) -> None:
        r = self.remoto("post", "/api/listas", ip=61, json={"nome": "Lista do gerente", "musicas": []},
                        headers=self.com_cookie(self.tok["gerente"]))
        self.assertIn(r.status_code, (200, 201), r.get_data(as_text=True))
        lista = server.listas_ler()["listas"][0]
        self.assertEqual((lista["alterado_por"], lista.get("autor_id")), ("remoto", self.ger["id"]))
        contagem = server._revisao_contar()
        self.assertEqual((contagem["listas"], contagem["apagaveis_pela_rede"]), (0, 0))

    def test_modo_pin_mantem_o_padrao_antigo(self) -> None:
        # sem contas, o padrão continua "desativar os horários remotos"
        server._contas_path().unlink()
        cfg = server.load_config()
        cfg.pop(server.CONTAS_MARCADOR)
        server.save_config(cfg)
        server._contas_arquivo.resetar()
        self.assertFalse(server.modo_contas())
        with server.app.test_request_context("/"):
            self.assertTrue(server._opcoes_de_revisao({})["desativar_remotos"])


# ---------------------------------------------------------------------------------------------
# S9, S10, S11, S12
# ---------------------------------------------------------------------------------------------
class TesteMiudos(BaseContas):
    def test_s9_teto_do_pop(self) -> None:
        self.assertEqual(server.POP_TOCADOR_MAX_POR_MIN, 120)

    def test_s10_auditoria_com_conta(self) -> None:
        self.criar_adm()
        cab = {"Cookie": f"{COOKIE}={self.token_adm}"}
        versao = self.l.get("/api/mensagens", headers=cab).get_json()["versao"]
        with self.assertLogs(server.auditoria, level="INFO") as trilha:
            self.l.post("/api/mensagens/restaurar", json={"versao": versao}, headers=cab)
            self.l.post("/api/listas", json={"nome": "Lista", "musicas": []}, headers=cab)
            self.l.post("/api/programacao", json={"versao": server._prog_arquivo.ler()["versao"], "itens": []},
                        headers=cab)
            self.l.post("/api/spotify/config", json={"client_id": "a" * 32}, headers=cab)
        acoes = {linha.split("acao=")[1].split()[0]: linha for linha in trilha.output if "acao=" in linha}
        for acao in ("mensagens_restaurar", "lista_criar", "programacao_salvar", "spotify_config"):
            self.assertIn(acao, acoes, trilha.output)
            self.assertRegex(acoes[acao], r"conta=c[0-9a-f]{10}", acao)
        conta = server.conta_por_usuario("ana.adm")
        for linha in trilha.output:
            for segredo in (conta["id"], "ana.adm", conta["nome"]):
                self.assertNotIn(segredo, linha.split("acao=")[1])
        # sem conta (modo PIN / fora de requisição): sem o campo
        with self.assertLogs(server.auditoria, level="INFO") as trilha:
            server.auditar("teste_fora_de_requisicao")
        self.assertNotIn("conta=", trilha.output[-1])

    def test_s11_modo_pin_nao_grava_historico(self) -> None:
        self.assertFalse(server.modo_contas())
        server.registrar_anuncio(conta=None, texto="Modo PIN", resultado="ok", origem="local", tipo="digitado")
        self.assertFalse(server._anuncios_path().exists())
        self.criar_adm()
        server.registrar_anuncio(conta=None, texto="Antigo", resultado="ok", origem="local", tipo="agendado")
        linha = json.loads(server._anuncios_path().read_text(encoding="utf-8"))
        self.assertEqual((linha["texto"], linha["nome"]), ("Antigo", server.HISTORICO_SEM_CONTA))

    def test_s12_texto_do_tunel_por_modo(self) -> None:
        self.assertIn("--definir-pin", server._msg_loopback_suspenso())
        self.criar_adm()
        msg = server._msg_loopback_suspenso()
        self.assertNotIn("--definir-pin", msg)
        self.assertIn("entre com uma conta", msg)
        with server.app.test_request_context("/"):
            corpo = server._resp_429(60, suspenso=True)[0].get_json()
        self.assertEqual(corpo["codigo"], "loopback_suspenso")
        self.assertIn("entre com uma conta", corpo["error"])
        server._tuneis_estado.update(fortes=[], fracos=[])
        with mock.patch.object(server, "detectar_tuneis", return_value={"fortes": ["portproxy"], "fracos": []}), \
                self.assertLogs(server.log.name, level="WARNING") as logs:
            server._atualizar_tuneis()
        server._tuneis_estado.update(fortes=[], fracos=[])
        self.assertIn("entre com uma conta", logs.output[-1])
        self.assertNotIn("--definir-pin", logs.output[-1])


if __name__ == "__main__":
    unittest.main()
