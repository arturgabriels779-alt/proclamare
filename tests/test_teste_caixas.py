"""
Testes da v2.8.10: "Teste das caixas em loop" (GET /api/teste-caixas, POST .../iniciar e .../parar).

  * Permissões: capacidade `teste_caixas` só do ADM (contas); modo PIN: só o PC das caixas; todos veem (GET).
  * Contrato: esquema estrito (400), 404 `mensagem_inexistente`, 409 `teste_tocando`, 409 do /api/announce e do desligar
    com a mensagem do teste, Programação "não tocou" com motivo `teste_caixas` (todos os tipos, sem esperar),
    "Parar" corta na hora, teto de 1 h 30 com relógio INJETÁVEL, uma síntese para N voltas (e nova ao mudar a voz),
    auditoria sem texto e UMA linha no histórico por loop.
NUNCA há som real: a fala é um falso controlável (`_anunciar`/`_anunciar_na_saida`) ou, no teste de síntese, o
`_anunciar` de verdade com os registradores globais de tests/__init__.py (`_tocar_aviso`, `_tocar_mp3`, `_ler_pcm_legado`)
e o edge-tts trocado por um gerador de bytes. Nenhum teste espera tempo real (relógio do teto injetado; pausa de 3 s
reduzida).

    python -m unittest tests.test_teste_caixas -v
"""
from __future__ import annotations

import contextlib
import json
import sys
import threading
import time
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

RAIZ = Path(__file__).resolve().parent.parent
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

import contas  # noqa: E402
import programacao  # noqa: E402
import server  # noqa: E402
from tests import test_permissoes as tpm  # noqa: E402
from tests.test_auth import HOST_REMOTO, PIN, ip_remoto  # noqa: E402
from tests.test_login_contas import BaseContas, cookie_da_resposta  # noqa: E402

_MUSICA_PAUSADA_REAL = server._musica_pausada   # o de verdade (as classes abaixo o trocam por um gravador)
MSG = server.MENSAGENS_DE_FABRICA[0]      # {"id": "fab-1", "titulo", "texto"}
ROTA = "/api/teste-caixas"
INICIAR = ROTA + "/iniciar"
PARAR = ROTA + "/parar"


class Simulacao:
    """
    Mistura para as classes de teste: troca a fala por um falso controlável, o relógio do teto por um relógio de mentira
    e a pausa de 3 s por 20 ms. `self.bloquear` = a volta espera o "Parar" (ou `self.libera`); `self.passo` = quanto o
    relógio avança a cada volta; `self.falha` = exceção que a volta levanta.
    """

    def simular(self) -> None:
        self.tempo = [5000.0]
        self.dia = ["2026-11-11"]                   # o dia do PC da loja (injetável)
        self.passo = 0.0
        self.bloquear = False
        self.ignora_parar = False                   # fala que NÃO dá para cortar (voz do Windows no legado)
        self.falha: BaseException | None = None
        self.libera = threading.Event()
        self.chegou = threading.Semaphore(0)       # um release a cada volta que começa
        self.chamadas: list[dict[str, Any]] = []
        self.eventos: list[str] = []                # "pausa" / "volta" da música

        def _volta(texto: str, aviso: str, parar: Any, contexto: Any) -> None:
            self.chamadas.append({"texto": texto, "aviso": aviso, "contexto": contexto})
            self.chegou.release()
            if self.falha is not None:
                raise self.falha
            if self.bloquear:
                limite = time.monotonic() + 10.0
                while not (parar is not None and parar.is_set() and not self.ignora_parar) and not self.libera.is_set():
                    if time.monotonic() > limite:
                        raise AssertionError("a volta nunca foi cortada")
                    time.sleep(0.005)
            self.tempo[0] += self.passo

        def fala(texto: str, *a: Any, aviso: str = "nenhum", parar: Any = None, contexto: Any = None,
                 **kw: Any) -> tuple[str, None]:
            _volta(texto, aviso, parar, contexto)
            return "offline", None

        def fala_na_saida(texto: str, *a: Any, aviso: str = "nenhum", parar: Any = None, contexto: Any = None,
                          **kw: Any) -> tuple[str, None, dict]:
            _volta(texto, aviso, parar, contexto)
            return "offline", None, {}

        @contextlib.contextmanager
        def musica(avisos: Any = None) -> Any:
            self.eventos.append("pausa")
            try:
                yield
            finally:
                self.eventos.append("volta")

        for alvo, valor in (("_anunciar", fala), ("_anunciar_na_saida", fala_na_saida),
                            ("_teste_relogio", lambda: self.tempo[0]), ("_teste_data", lambda: self.dia[0]),
                            ("TESTE_CAIXAS_PAUSA_S", 0.02),
                            ("_musica_pausada", musica), ("tem_internet", lambda *a, **k: True)):
            p = mock.patch.object(server, alvo, valor)
            p.start()
            self.addCleanup(p.stop)
        self._zerar_teste()
        self.addCleanup(self._zerar_teste)

    @staticmethod
    def _zerar_teste() -> None:
        """Para qualquer loop vivo (sem marcar o servidor como 'desligando') e deixa o estado limpo."""
        thread = server._teste["thread"]
        if server._teste["ativo"] and server._teste["parar"] is not None:
            server._teste["sinal"].parar()
            server._teste["parar"].set()
        if thread is not None:
            thread.join(timeout=5.0)
        server._teste_desligando = False
        server._teste.update(ativo=False, titulo=None, repeticao=0, fim_mono=None, fim_motivo=None, parar=None,
                             sinal=None, thread=None, pedido=None, entregar_trava=False)
        server._teste_uso.update(data=None, s=0.0, mono=None, gravado_mono=None)
        if server._anuncio_lock.locked():
            server._anuncio_lock.release()

    def esperar_volta(self, n: int = 1) -> None:
        for _ in range(n):
            if not self.chegou.acquire(timeout=5.0):
                self.fail("o loop não começou uma volta a tempo")

    def esperar_fim(self) -> None:
        thread = server._teste["thread"]
        self.assertIsNotNone(thread)
        thread.join(timeout=5.0)
        self.assertFalse(thread.is_alive(), "o loop não terminou")

    def linhas_historico(self) -> list[dict]:
        caminho = server._anuncios_path()
        if not caminho.exists():
            return []
        return [json.loads(x) for x in caminho.read_text(encoding="utf-8").splitlines() if x.strip()]


# ---------------------------------------------------------------------------------------------
# Modo contas
# ---------------------------------------------------------------------------------------------
class TesteContas(Simulacao, tpm.BasePermissoes):
    def setUp(self) -> None:
        super().setUp()
        self.simular()

    def iniciar(self, perfil: str = "adm", local: bool = False, corpo: Any = None) -> Any:
        tok = (self.local if local else self.rede)[perfil]
        return self.chamar("POST", INICIAR, tok, local=local, corpo={"mensagem_id": MSG["id"]} if corpo is None else corpo)

    def parar(self, perfil: str = "adm", local: bool = False) -> Any:
        tok = (self.local if local else self.rede)[perfil]
        return self.chamar("POST", PARAR, tok, local=local, corpo={})

    def estado(self, perfil: str = "adm", local: bool = False) -> Any:
        return self.chamar("GET", ROTA, (self.local if local else self.rede)[perfil], local=local)

    # --- permissões --------------------------------------------------------------------------
    def test_capacidade_so_do_adm(self) -> None:
        self.assertIn("teste_caixas", contas.PODE["adm"])
        self.assertNotIn("teste_caixas", contas.PODE["gerente"])
        self.assertNotIn("teste_caixas", contas.PODE["funcionario"])
        self.assertEqual(server.PERMISSOES_ROTAS[("POST", INICIAR)], "teste_caixas")
        self.assertEqual(server.PERMISSOES_ROTAS[("POST", PARAR)], "teste_caixas")
        self.assertIsNone(server.PERMISSOES_ROTAS[("GET", ROTA)])

    def test_adm_inicia_202_local_e_rede(self) -> None:
        self.bloquear = True
        r = self.iniciar("adm", local=True)
        self.assertEqual(r.status_code, 202, r.get_data(as_text=True))
        corpo = r.get_json()
        self.assertEqual((corpo["tocando"], corpo["titulo"], corpo["teto_s"], corpo["fim_motivo"]),
                         (True, MSG["titulo"], 5400, None))
        self.assertEqual(corpo["restante_s"], 5400)
        self.esperar_volta()
        self.assertEqual(self.parar("adm", local=False).status_code, 200)
        self.esperar_fim()
        self.libera.set()
        self.assertEqual(self.iniciar("adm", local=False).status_code, 202)   # e pela rede também
        self.assertEqual(self.parar("adm", local=True).status_code, 200)

    def test_gerente_e_funcionario_403_no_iniciar_e_no_parar_e_200_no_get(self) -> None:
        self.bloquear = True
        for perfil in ("gerente", "funcionario"):
            for local in (False, True):
                with self.subTest(perfil=perfil, local=local):
                    self.assert_negado(self.iniciar(perfil, local))
                    self.assert_negado(self.parar(perfil, local))
                    r = self.estado(perfil, local)
                    self.assertEqual(r.status_code, 200)
                    self.assertFalse(r.get_json()["tocando"])
        self.assertFalse(server._anuncio_lock.locked())
        # com o loop tocando, todos veem; ninguém além do ADM para
        self.assertEqual(self.iniciar("adm").status_code, 202)
        self.esperar_volta()
        for perfil in ("gerente", "funcionario"):
            r = self.estado(perfil)
            self.assertEqual((r.status_code, r.get_json()["tocando"], r.get_json()["titulo"]), (200, True, MSG["titulo"]))
            self.assert_negado(self.parar(perfil))
        self.assertTrue(server._teste_caixas_ativo())
        self.parar("adm")

    def test_sem_sessao_401(self) -> None:
        for metodo, rota in (("get", ROTA), ("post", INICIAR), ("post", PARAR)):
            r = getattr(self.r, metodo)(rota, base_url=HOST_REMOTO, environ_base=ip_remoto(70),
                                        **({"json": {}} if metodo == "post" else {}))
            self.assertEqual(r.status_code, 401, rota)

    def test_permissoes_no_status(self) -> None:
        r = self.chamar("GET", "/api/auth/status", self.rede["adm"], local=False)
        self.assertIn("teste_caixas", r.get_json()["permissoes"])
        r = self.chamar("GET", "/api/auth/status", self.rede["gerente"], local=False)
        self.assertNotIn("teste_caixas", r.get_json()["permissoes"])

    # --- esquema, 404, 409 -------------------------------------------------------------------
    def test_esquema_400(self) -> None:
        ruins = [{}, {"mensagem_id": MSG["id"], "texto": "x"}, {"mensagem_id": 1}, {"mensagem_id": None},
                 {"mensagem_id": ["fab-1"]}, {"mensagem_id": ""}, {"mensagem_id": "x" * 65}, {"texto": "oi"}]
        for corpo in ruins:
            with self.subTest(corpo=corpo):
                r = self.iniciar(corpo=corpo)
                self.assertEqual(r.status_code, 400, r.get_data(as_text=True))
        self.assertFalse(server._teste_caixas_ativo())
        self.assertFalse(server._anuncio_lock.locked())
        self.assertEqual(self.chamar("POST", PARAR, self.rede["adm"], local=False, corpo={"x": 1}).status_code, 400)

    def test_id_de_64_caracteres_e_aceito_pelo_esquema_e_vira_404(self) -> None:
        r = self.iniciar(corpo={"mensagem_id": "x" * 64})
        self.assertEqual(r.status_code, 404)

    def test_mensagem_inexistente_404(self) -> None:
        r = self.iniciar(corpo={"mensagem_id": "nao-existe"})
        self.assertEqual(r.status_code, 404, r.get_data(as_text=True))
        self.assertEqual(r.get_json()["codigo"], "mensagem_inexistente")
        self.assertFalse(server._teste_caixas_ativo())
        self.assertFalse(server._anuncio_lock.locked())

    def test_usa_a_mensagem_da_lista_atual_e_o_texto_vem_do_servidor(self) -> None:
        cfg = server.load_config()
        cfg["mensagens"] = [{"id": "m-meu", "titulo": "Minha", "texto": "Texto da loja para o teste."}]
        server.save_config(cfg)
        self.assertEqual(self.iniciar(corpo={"mensagem_id": MSG["id"]}).status_code, 404)   # a de fábrica sumiu
        self.bloquear = True
        r = self.iniciar(corpo={"mensagem_id": "m-meu"})
        self.assertEqual(r.status_code, 202)
        self.assertEqual(r.get_json()["titulo"], "Minha")
        self.esperar_volta()
        self.assertEqual(self.chamadas[0]["texto"], "Texto da loja para o teste.")
        self.parar()

    def test_ja_tocando_409(self) -> None:
        self.bloquear = True
        self.assertEqual(self.iniciar().status_code, 202)
        self.esperar_volta()
        r = self.iniciar()
        self.assertEqual(r.status_code, 409, r.get_data(as_text=True))
        self.assertEqual(r.get_json()["codigo"], "teste_tocando")
        self.parar()

    def test_trava_ocupada_409_com_a_mensagem_de_ocupado(self) -> None:
        self.assertTrue(server._anuncio_lock.acquire(blocking=False))
        try:
            r = self.iniciar()
        finally:
            server._anuncio_lock.release()
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.get_json()["error"], "Já existe um anúncio tocando. Aguarde terminar.")
        self.assertFalse(server._teste_caixas_ativo())

    def test_desligando_409(self) -> None:
        with mock.patch.object(server, "_desligamento_agendado", True):
            r = self.iniciar()
        self.assertEqual((r.status_code, r.get_json()["codigo"]), (409, "desligando"))

    def test_voz_online_sem_internet_503(self) -> None:
        with mock.patch.object(server, "voz_loja", lambda: {**server.VOZ_LOJA_PADRAO, "motor": "online"}), \
                mock.patch.object(server, "tem_internet", lambda *a, **k: False):
            r = self.iniciar()
        self.assertEqual(r.status_code, 503)
        self.assertEqual(r.get_json()["error"], server.MSG_SEM_INTERNET_VOZ_ONLINE)
        self.assertFalse(server._anuncio_lock.locked())
        self.assertFalse(server._teste_caixas_ativo())

    def test_limite_de_10_inicios_por_minuto(self) -> None:
        codigos = [self.iniciar(corpo={"mensagem_id": "nao-existe"}).status_code for _ in range(11)]
        self.assertEqual(codigos[:10], [404] * 10)
        self.assertEqual(codigos[10], 429)

    # --- durante o loop ----------------------------------------------------------------------
    def test_announce_desligar_e_ouvir_dao_409_com_a_mensagem_do_teste(self) -> None:
        self.bloquear = True
        self.assertEqual(self.iniciar().status_code, 202)
        self.esperar_volta()
        r = self.chamar("POST", "/api/announce", self.rede["adm"], local=False, corpo={"text": "Atenção, clientes."})
        self.assertEqual(r.status_code, 409, r.get_data(as_text=True))
        self.assertEqual(r.get_json()["error"], "Teste das caixas tocando. Aguarde o administrador parar.")
        r = self.chamar("POST", "/api/announce", self.rede["funcionario"], local=False, corpo={"text": "Atenção."})
        self.assertEqual((r.status_code, r.get_json()["error"]), (409, server.MSG_TESTE_CAIXAS_TOCANDO))
        r = self.chamar("POST", "/api/voz/ouvir", self.rede["adm"], local=False, corpo={"aviso": "aeroporto"})
        self.assertEqual((r.status_code, r.get_json()["error"]), (409, server.MSG_TESTE_CAIXAS_TOCANDO))
        self.assertEqual(len(self.chamadas), 1)   # nada do que foi recusado tocou
        self.parar()

    def test_tocar_agora_da_programacao_409(self) -> None:
        itens = {"a" * 8: {"id": "a" * 8, "tipo": "anuncio", "alvo": "Oi"},
                 "b" * 8: {"id": "b" * 8, "tipo": "lista", "alvo": "x" * 8},
                 "c" * 8: {"id": "c" * 8, "tipo": "spotify", "alvo": "y" * 8},
                 "d" * 8: {"id": "d" * 8, "tipo": "audio", "alvo": "z" * 8}}
        self.bloquear = True
        self.assertEqual(self.iniciar().status_code, 202)
        self.esperar_volta()
        with mock.patch.object(server._prog_arquivo, "ler", lambda: {"itens": list(itens.values())}), \
                mock.patch.object(server, "_prog_musica_do_item", lambda item: None):
            for iid in itens:
                with self.subTest(tipo=itens[iid]["tipo"]):
                    r = self.chamar("POST", f"/api/programacao/{iid}/testar", self.rede["adm"], local=False, corpo={})
                    self.assertEqual(r.status_code, 409, r.get_data(as_text=True))
                    self.assertEqual(r.get_json()["error"], server.MSG_TESTE_CAIXAS_TOCANDO)
        self.parar()

    def test_programacao_agendada_nao_toca_com_motivo_teste_caixas_sem_esperar(self) -> None:
        itens = [{"id": "a" * 8, "tipo": "anuncio", "alvo": "Oi", "motor": "offline", "voz": "francisca",
                  "velocidade": 0},
                 {"id": "b" * 8, "tipo": "lista", "alvo": "x" * 8},
                 {"id": "c" * 8, "tipo": "spotify", "alvo": "y" * 8},
                 {"id": "d" * 8, "tipo": "audio", "alvo": "z" * 8}]
        self.bloquear = True
        self.assertEqual(self.iniciar().status_code, 202)
        self.esperar_volta()
        for item in itens:
            with self.subTest(tipo=item["tipo"]):
                agora = time.monotonic()
                ctx = programacao.Execucao(origem="agendado", criado_mono=agora, prazo_mono=agora + 60.0,
                                           sinal=programacao.SinalParada(), lock_tomado=False,
                                           esperar=lambda s: time.sleep(s) or False, monotonic=time.monotonic)
                with self.assertRaises(programacao.NaoTocou) as cm:
                    server._prog_executar(item, ctx)
                self.assertEqual((cm.exception.motivo, cm.exception.texto),
                                 ("teste_caixas", "Teste das caixas tocando"))
                self.assertLess(time.monotonic() - agora, 2.0)   # sem os 60 s de espera da trava
        self.assertEqual(server._PROG_RESULTADO_HISTORICO["teste_caixas"], "ocupado")
        self.parar()
        self.assertEqual(len(self.chamadas), 1)

    # --- parar e fim -------------------------------------------------------------------------
    def test_parar_corta_na_hora_e_devolve_tocando_false(self) -> None:
        self.bloquear = True
        self.assertEqual(self.iniciar().status_code, 202)
        self.esperar_volta()
        t0 = time.monotonic()
        r = self.parar()
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        self.assertLess(time.monotonic() - t0, 3.0)
        corpo = r.get_json()
        self.assertEqual((corpo["tocando"], corpo["titulo"], corpo["restante_s"], corpo["fim_motivo"]),
                         (False, None, None, "parado"))
        self.assertFalse(server._anuncio_lock.locked())
        self.assertEqual(self.estado().get_json()["fim_motivo"], "parado")
        self.assertEqual(self.eventos, ["pausa", "volta"])   # a música pausou uma vez e voltou no fim

    def test_parar_corta_a_pausa_de_3_segundos(self) -> None:
        with mock.patch.object(server, "TESTE_CAIXAS_PAUSA_S", 30.0):
            self.assertEqual(self.iniciar().status_code, 202)
            self.esperar_volta()
            time.sleep(0.1)   # a volta (instantânea) acabou e o loop está na pausa
            t0 = time.monotonic()
            r = self.parar()
        self.assertLess(time.monotonic() - t0, 3.0)
        self.assertEqual((r.status_code, r.get_json()["tocando"], r.get_json()["fim_motivo"]), (200, False, "parado"))

    def test_parar_e_idempotente(self) -> None:
        for _ in range(2):
            r = self.parar()
            self.assertEqual((r.status_code, r.get_json()["tocando"]), (200, False))
        self.bloquear = True
        self.iniciar()
        self.esperar_volta()
        self.assertEqual(self.parar().get_json()["tocando"], False)
        self.assertEqual(self.parar().status_code, 200)

    def test_volta_a_volta_com_pausa_e_contador(self) -> None:
        self.assertEqual(self.iniciar().status_code, 202)
        self.esperar_volta(4)
        corpo = self.estado().get_json()
        self.assertTrue(corpo["tocando"])
        self.assertGreaterEqual(corpo["repeticao"], 4)
        self.parar()
        self.assertEqual(self.eventos, ["pausa", "volta"])   # UMA pausa para o loop inteiro
        self.assertTrue(all(c["contexto"] is contextlib.nullcontext for c in self.chamadas))   # a fala não mexe na música

    def test_teto_de_1h30_com_relogio_injetado(self) -> None:
        self.passo = 2000.0   # cada volta "dura" 2000 s no relógio de mentira: 3 voltas passam dos 5400 s
        self.assertEqual(self.iniciar().status_code, 202)
        self.esperar_fim()
        corpo = self.estado().get_json()
        self.assertEqual((corpo["tocando"], corpo["fim_motivo"], corpo["restante_s"]), (False, "tempo", None))
        self.assertEqual(len(self.chamadas), 3)
        self.assertFalse(server._anuncio_lock.locked())
        linhas = self.linhas_historico()
        self.assertEqual([(x["tipo"], x["resultado"]) for x in linhas], [("teste_caixas", "ok")])

    def test_restante_acompanha_o_relogio(self) -> None:
        self.bloquear = True
        self.assertEqual(self.iniciar().get_json()["restante_s"], 5400)
        self.esperar_volta()
        self.tempo[0] += 100.4
        self.assertEqual(self.estado().get_json()["restante_s"], 5300)   # ceil(5299,6)
        self.tempo[0] += 100000
        self.assertEqual(self.estado().get_json()["restante_s"], 0)
        self.parar()

    def test_erro_numa_volta_encerra_com_motivo_erro(self) -> None:
        self.falha = RuntimeError("segredo da falha: texto da mensagem")
        with self.assertLogs(server.log.name, level="WARNING") as cap:
            self.assertEqual(self.iniciar().status_code, 202)
            self.esperar_fim()
        corpo = self.estado().get_json()
        self.assertEqual((corpo["tocando"], corpo["fim_motivo"]), (False, "erro"))
        self.assertFalse(server._anuncio_lock.locked())
        saida = "\n".join(cap.output)
        self.assertIn("RuntimeError", saida)                   # só o tipo
        self.assertNotIn("segredo da falha", saida)
        self.assertNotIn(MSG["texto"], saida)
        self.assertEqual([x["resultado"] for x in self.linhas_historico()], ["erro"])
        self.assertEqual(self.eventos, ["pausa", "volta"])

    def test_desligamento_do_processo_para_o_loop(self) -> None:
        self.bloquear = True
        self.assertEqual(self.iniciar().status_code, 202)
        self.esperar_volta()
        server._teste_caixas_parar_no_desligamento()
        corpo = self.estado().get_json()
        self.assertEqual((corpo["tocando"], corpo["fim_motivo"]), (False, "desligando"))
        self.assertTrue(server._teste["thread"].daemon)   # nunca segura o fim do processo
        self.assertEqual(self.iniciar().status_code, 409)   # e não aceita outro
        self.assertEqual([x["resultado"] for x in self.linhas_historico()], ["interrompido"])

    def test_novo_inicio_zera_o_fim_motivo_anterior(self) -> None:
        self.bloquear = True
        self.iniciar()
        self.esperar_volta()
        self.parar()
        self.assertEqual(self.estado().get_json()["fim_motivo"], "parado")
        self.assertEqual(self.iniciar().get_json()["fim_motivo"], None)
        self.parar()

    # --- auditoria e histórico ---------------------------------------------------------------
    def test_auditoria_sem_texto_titulo_nem_id(self) -> None:
        self.bloquear = True
        with self.assertLogs(server.auditoria.name, level="INFO") as cap:
            self.assertEqual(self.iniciar(local=True).status_code, 202)
            self.esperar_volta()
            self.parar(local=True)
        saida = "\n".join(cap.output)
        self.assertIn("acao=teste_caixas_iniciar", saida)
        self.assertIn("resultado=ok", saida)
        self.assertIn("acao=teste_caixas_fim", saida)
        self.assertIn("repeticoes=1", saida)
        self.assertIn("motivo=parado", saida)
        self.assertRegex(saida, r"usado_dia_s=[0-9]+")
        for proibido in (MSG["texto"], MSG["titulo"], MSG["id"], "Atenção", "ana.adm", "Ana"):
            self.assertNotIn(proibido, saida)
        self.assertRegex(saida, r"conta=c[0-9a-f]{10}")

    def test_historico_uma_linha_por_loop_com_o_texto(self) -> None:
        self.passo = 1.0
        self.assertEqual(self.iniciar().status_code, 202)
        self.esperar_volta(3)
        self.parar()
        linhas = self.linhas_historico()
        self.assertEqual(len(linhas), 1)
        x = linhas[0]
        self.assertEqual((x["tipo"], x["resultado"], x["texto"], x["origem"]),
                         ("teste_caixas", "interrompido", MSG["texto"], "rede"))
        self.assertEqual(x["usuario"], "ana.adm")
        # o GET /api/historico (qualquer conta logada) aceita e devolve o tipo novo
        r = self.chamar("GET", "/api/historico", self.rede["adm"], local=False)
        self.assertEqual(r.status_code, 200)
        self.assertIn("teste_caixas", json.dumps(r.get_json()))

    def test_historico_cheio_nao_derruba_o_fim(self) -> None:
        with mock.patch.object(server, "registrar_anuncio", side_effect=OSError("disco")):
            self.bloquear = True
            self.iniciar()
            self.esperar_volta()
            self.parar()
        self.assertFalse(server._anuncio_lock.locked())
        self.assertFalse(server._teste_caixas_ativo())


# ---------------------------------------------------------------------------------------------
# Modo PIN
# ---------------------------------------------------------------------------------------------
class TesteModoPin(Simulacao, BaseContas):
    def setUp(self) -> None:
        super().setUp()
        self.simular()
        server.aplicar_pin(PIN)
        self.assertFalse(server.modo_contas())
        token = cookie_da_resposta(self.login())
        self.cab = {"Cookie": f"{server.COOKIE_SESSAO}={token}"}

    def remoto_post(self, rota: str, corpo: dict) -> Any:
        return self.r.post(rota, json=corpo, base_url=HOST_REMOTO, environ_base=ip_remoto(50), headers=self.cab)

    def test_pc_das_caixas_inicia_e_para(self) -> None:
        self.bloquear = True
        r = self.c.post(INICIAR, json={"mensagem_id": MSG["id"]})
        self.assertEqual(r.status_code, 202, r.get_data(as_text=True))
        self.esperar_volta()
        r = self.c.post(PARAR, json={})
        self.assertEqual((r.status_code, r.get_json()["tocando"], r.get_json()["fim_motivo"]), (200, False, "parado"))
        self.assertEqual(self.linhas_historico(), [])   # S11: modo PIN não grava histórico

    def test_remoto_com_pin_403_no_iniciar_e_no_parar_mas_ve(self) -> None:
        self.bloquear = True
        for rota, corpo in ((INICIAR, {"mensagem_id": MSG["id"]}), (PARAR, {})):
            r = self.remoto_post(rota, corpo)
            self.assertEqual(r.status_code, 403, r.get_data(as_text=True))
            self.assertEqual(r.get_json()["codigo"], "sem_permissao")
        self.assertFalse(server._teste_caixas_ativo())
        self.assertFalse(server._anuncio_lock.locked())
        r = self.r.get(ROTA, base_url=HOST_REMOTO, environ_base=ip_remoto(50), headers=self.cab)
        self.assertEqual((r.status_code, r.get_json()["tocando"]), (200, False))
        # com o PC tocando, o aparelho remoto vê e continua sem poder parar
        self.assertEqual(self.c.post(INICIAR, json={"mensagem_id": MSG["id"]}).status_code, 202)
        self.esperar_volta()
        r = self.r.get(ROTA, base_url=HOST_REMOTO, environ_base=ip_remoto(50), headers=self.cab)
        self.assertTrue(r.get_json()["tocando"])
        self.assertEqual(self.remoto_post(PARAR, {}).status_code, 403)
        self.assertTrue(server._teste_caixas_ativo())
        self.c.post(PARAR, json={})

    def test_remoto_sem_pin_401(self) -> None:
        r = self.r.post(INICIAR, json={"mensagem_id": MSG["id"]}, base_url=HOST_REMOTO, environ_base=ip_remoto(51))
        self.assertEqual(r.status_code, 401)

    def test_permissoes_no_status_do_modo_pin(self) -> None:
        self.assertIn("teste_caixas", self.c.get("/api/auth/status").get_json()["permissoes"])
        r = self.r.get("/api/auth/status", base_url=HOST_REMOTO, environ_base=ip_remoto(50), headers=self.cab)
        self.assertNotIn("teste_caixas", r.get_json().get("permissoes", []))


# ---------------------------------------------------------------------------------------------
# Síntese reaproveitada (o _anunciar de verdade, com os registradores globais; edge-tts falso)
# ---------------------------------------------------------------------------------------------
class TesteSintese(BaseContas):
    def setUp(self) -> None:
        super().setUp()
        server.aplicar_pin(PIN)
        self.loja = {**server.VOZ_LOJA_PADRAO, "motor": "online", "voz": "francisca", "velocidade": 0,
                     "aviso": "microfone", "limitador": True}
        self.sinteses: list[tuple[str, str]] = []

        async def gerar(texto: str, voz: str, taxa: str, caminho: Path) -> None:
            self.sinteses.append((voz, taxa))
            caminho.write_bytes(b"ID3-mp3-falso")   # nenhum áudio de verdade

        server._falas_tocadas_em_teste.clear()

        @contextlib.contextmanager
        def musica(avisos: Any = None) -> Any:
            yield

        for alvo, valor in (("voz_loja", lambda: dict(self.loja)), ("_gerar_audio_online_async", gerar),
                            ("tem_internet", lambda *a, **k: True), ("TESTE_CAIXAS_PAUSA_S", 0.02),
                            ("_musica_pausada", musica)):
            p = mock.patch.object(server, alvo, valor)
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(Simulacao._zerar_teste)

    def esperar_ate(self, condicao: Any, msg: str) -> None:
        limite = time.monotonic() + 8.0
        while not condicao():
            if time.monotonic() > limite:
                self.fail(msg)
            time.sleep(0.01)

    def test_uma_sintese_para_n_voltas_e_nova_quando_a_voz_muda(self) -> None:
        self.assertEqual(self.c.post(INICIAR, json={"mensagem_id": MSG["id"]}).status_code, 202)
        self.esperar_ate(lambda: server._teste["repeticao"] >= 4, "o loop não deu 4 voltas")
        self.assertEqual(len(self.sinteses), 1)                       # 4 voltas, UMA chamada ao edge-tts
        falas = [f for f in server._falas_tocadas_em_teste if f[0] == "mp3"]
        self.assertGreaterEqual(len(falas), 3)                        # e cada volta tocou a sua cópia
        self.assertEqual(len({f[1] for f in falas}), len(falas))      # cópias distintas (cada uma apagada ao tocar)
        self.assertTrue(list(server.AUDIO_DIR.glob(server.PREFIXO_FALA_TESTE + "*.mp3")))   # o mestre existe no loop
        # o ADM troca a voz no meio: a próxima volta sintetiza de novo (e só uma vez)
        self.loja["voz"] = "remy"
        antes = server._teste["repeticao"]
        self.esperar_ate(lambda: server._teste["repeticao"] >= antes + 4, "o loop parou depois da troca")
        self.assertEqual(len(self.sinteses), 2)
        self.assertEqual(self.sinteses[1][0], server.VOZES_ONLINE["remy"])
        # mudar só o limitador/volume NÃO ressintetiza (o mp3 serve; o limitador roda a cada volta)
        self.loja.update(limitador=False, voz_volume=80)
        antes = server._teste["repeticao"]
        self.esperar_ate(lambda: server._teste["repeticao"] >= antes + 3, "o loop parou depois do volume")
        self.assertEqual(len(self.sinteses), 2)
        self.assertEqual(server._falas_tocadas_em_teste[-1][2], 80)   # mas o volume novo vale na volta seguinte
        r = self.c.post(PARAR, json={})
        self.assertFalse(r.get_json()["tocando"])
        # nada fica no disco: nem o mestre nem os mp3 das voltas
        self.assertEqual(list(server.AUDIO_DIR.glob(server.PREFIXO_FALA_TESTE + "*")), [])
        self.assertEqual(list(server.AUDIO_DIR.glob("anuncio_*")), [])

    def test_o_cache_so_vale_dentro_do_loop(self) -> None:
        self.assertIsNone(server._fala_cache_ctx.get())
        for _ in range(2):   # anúncio comum fora do loop: cada um sintetiza e não deixa mestre
            caminho = server.gerar_audio_online("Oi", "pt-BR-FranciscaNeural", 0)
            server.apagar_audio(caminho)
        self.assertEqual(len(self.sinteses), 2)
        self.assertEqual(list(server.AUDIO_DIR.glob(server.PREFIXO_FALA_TESTE + "*")), [])

    def test_limpeza_apaga_o_mestre_orfao_ao_ligar(self) -> None:
        orfao = server.AUDIO_DIR / (server.PREFIXO_FALA_TESTE + "abc.mp3")
        orfao.write_bytes(b"x")
        server.limpar_audios_antigos()   # sem forçar: o recente fica (um loop pode estar usando)
        self.assertTrue(orfao.exists())
        server.limpar_audios_antigos(forcar=True)
        self.assertFalse(orfao.exists())


class _Proxy:
    """Trava de áudio de mentira: delega à verdadeira e anota se o teste ainda estava 'ativo' ao soltar."""

    def __init__(self, real: Any, anotar: Any) -> None:
        self._real, self._anotar = real, anotar

    def acquire(self, *a: Any, **k: Any) -> bool:
        return self._real.acquire(*a, **k)

    def release(self) -> None:
        self._anotar(server._teste_caixas_ativo())
        self._real.release()

    def locked(self) -> bool:
        return self._real.locked()


class TesteCorrecoes(Simulacao, tpm.BasePermissoes):
    """D1 (desligar corta o loop), D2 (música bloqueada), D3 (limite diário), M1 (teto do Spotify) e revisor #3/#4."""

    def setUp(self) -> None:
        super().setUp()
        self.simular()
        self.addCleanup(self._soltar_desligar)

    @staticmethod
    def _soltar_desligar() -> None:
        with server._desligar_lock:
            server._desligamento_agendado = False

    def iniciar(self, local: bool = False) -> Any:
        tok = (self.local if local else self.rede)["adm"]
        return self.chamar("POST", INICIAR, tok, local=local, corpo={"mensagem_id": MSG["id"]})

    def parar(self) -> Any:
        return self.chamar("POST", PARAR, self.rede["adm"], local=False, corpo={})

    def estado(self) -> dict:
        return self.chamar("GET", ROTA, self.rede["adm"], local=False).get_json()

    def desligar(self) -> Any:
        return self.chamar("POST", "/api/sistema/desligar", self.local["adm"], local=True, corpo={})

    def uso(self, segundos: int, dia: "str | None" = None) -> None:
        server.update_config(teste_caixas_uso={"data": dia or self.dia[0], "segundos": segundos})
        server._teste_uso.update(data=None, s=0.0, mono=None, gravado_mono=None)   # "reiniciou": lê do config

    # --- D1 ----------------------------------------------------------------------------------
    def test_desligar_durante_o_loop_corta_e_responde_202_e_herda_a_trava(self) -> None:
        self.bloquear = True
        self.assertEqual(self.iniciar().status_code, 202)
        self.esperar_volta()
        r = self.desligar()
        self.assertEqual(r.status_code, 202, r.get_data(as_text=True))
        self.assertTrue(server._desligamento_agendado)
        self.esperar_fim()
        self.assertFalse(server._teste_caixas_ativo())
        self.assertEqual(self.estado()["fim_motivo"], "desligando")
        self.assertTrue(server._anuncio_lock.locked())   # a trava passou DIRETO ao desligamento (nunca ficou livre)
        self.assertEqual([x["resultado"] for x in self.linhas_historico()], ["interrompido"])
        # nada consegue pegar a trava nem iniciar depois
        r = self.chamar("POST", "/api/announce", self.rede["adm"], local=False, corpo={"text": "Oi"})
        self.assertEqual(r.status_code, 409)
        self.assertEqual(self.iniciar().status_code, 409)
        self.assertEqual(self.desligar().status_code, 202)   # 2º pedido repete o 202

    def test_desligar_com_fala_que_nao_corta_responde_202_na_hora(self) -> None:
        self.bloquear = self.ignora_parar = True
        self.assertEqual(self.iniciar().status_code, 202)
        self.esperar_volta()
        t0 = time.monotonic()
        r = self.desligar()
        self.assertEqual(r.status_code, 202, r.get_data(as_text=True))
        self.assertLess(time.monotonic() - t0, 2.0)          # não espera a fala
        self.assertTrue(server._desligamento_agendado)
        self.assertTrue(server._teste_caixas_ativo())         # a fala ainda não acabou
        self.assertEqual(self.iniciar().status_code, 409)
        self.assertEqual(self.chamar("POST", "/api/announce", self.rede["adm"], local=False,
                                     corpo={"text": "Oi"}).status_code, 409)
        self.libera.set()   # a fala termina: o loop acaba SEM soltar a trava
        self.esperar_fim()
        self.assertTrue(server._anuncio_lock.locked())
        self.assertEqual(self.estado()["fim_motivo"], "desligando")

    def test_remoto_nao_desliga_nem_para_o_loop(self) -> None:
        self.bloquear = True
        self.assertEqual(self.iniciar().status_code, 202)
        self.esperar_volta()
        r = self.chamar("POST", "/api/sistema/desligar", self.rede["adm"], local=False, corpo={})
        self.assertEqual(r.status_code, 403, r.get_data(as_text=True))
        # loopback com cabeçalhos de proxy também não é "o PC das caixas"
        for cab in ({"X-Forwarded-For": "192.168.0.9"}, {"Forwarded": "for=192.168.0.9"}, {"X-Real-IP": "10.0.0.2"}):
            with self.subTest(cab=cab):
                r = self.l.post("/api/sistema/desligar", json={},
                                headers={"Cookie": f"{tpm.COOKIE}={self.local['adm']}", **cab})
                self.assertIn(r.status_code, (401, 403), r.get_data(as_text=True))
        self.assertTrue(server._teste_caixas_ativo())
        self.assertFalse(server._desligamento_agendado)
        self.assertEqual(self.estado()["tocando"], True)
        self.parar()

    def test_falha_ao_agendar_devolve_a_trava_ao_loop(self) -> None:
        self.bloquear = True
        self.assertEqual(self.iniciar().status_code, 202)
        self.esperar_volta()
        with mock.patch.object(server, "_agendar_desligamento", side_effect=RuntimeError("sem threads")):
            r = self.desligar()
        self.assertEqual(r.status_code, 500)
        self.assertFalse(server._desligamento_agendado)
        self.assertTrue(server._teste_caixas_ativo() or server._teste["fim_motivo"] == "desligando")
        self.libera.set()
        self.esperar_fim()
        self.assertFalse(server._anuncio_lock.locked())   # o loop soltou a trava normalmente

    # --- música NÃO retoma no desligamento ----------------------------------------------------
    def _musica_real(self) -> mock.Mock:
        """Troca o gravador de música pelo `_musica_pausada` de verdade, com tocador/Spotify falsos tocando."""
        retomar = mock.Mock()
        for alvo, valor in (("_musica_pausada", _MUSICA_PAUSADA_REAL), ("_retomar_musica", retomar),
                            ("_estado_do_tocador", lambda: (True, True, ("arquivo", None, "m1"))),
                            ("_pausar_spotify_se_tocando", lambda avisos: "dev-1"),
                            ("youtube_enqueue", mock.Mock()), ("_prog_dormir", lambda s: None)):
            pa = mock.patch.object(server, alvo, valor)
            pa.start()
            self.addCleanup(pa.stop)
        return retomar

    def test_fim_por_desligando_nao_retoma_a_musica(self) -> None:
        retomar = self._musica_real()
        self.bloquear = True
        self.assertEqual(self.iniciar().status_code, 202)
        self.esperar_volta()
        self.assertEqual(self.desligar().status_code, 202)
        self.esperar_fim()
        retomar.assert_not_called()

    def test_fim_por_desligando_do_processo_tambem_nao_retoma(self) -> None:
        retomar = self._musica_real()
        self.bloquear = True
        self.iniciar()
        self.esperar_volta()
        server._teste_caixas_parar_no_desligamento()
        self.assertEqual(self.estado()["fim_motivo"], "desligando")
        retomar.assert_not_called()

    def test_fim_por_parar_continua_retomando_a_musica(self) -> None:
        retomar = self._musica_real()
        self.bloquear = True
        self.iniciar()
        self.esperar_volta()
        self.parar()
        retomar.assert_called_once()

    def test_desligar_com_anuncio_tocando_segue_como_antes(self) -> None:
        self.assertTrue(server._anuncio_lock.acquire(blocking=False))
        try:
            r = self.desligar()
        finally:
            server._anuncio_lock.release()
        self.assertEqual(r.status_code, 409)
        self.assertFalse(server._desligamento_agendado)

    # --- D2 ----------------------------------------------------------------------------------
    BLOQUEADAS = [
        ("/api/spotify/play", {}),
        ("/api/spotify/next", {}),
        ("/api/spotify/previous", {}),
        ("/api/spotify/atalhos/<atalho_id>/tocar", {}),
        ("/api/listas/<lista_id>/tocar", {}),
        ("/api/youtube/command", {"action": "load", "params": {}}),
        ("/api/youtube/command", {"action": "play"}),
        ("/api/youtube/command", {"action": "next"}),
        ("/api/youtube/command", {"action": "prev"}),
        ("/api/youtube/command", {"action": "play_index", "params": {"index": 0}}),
        ("/api/youtube/queue", {"op": "play_now", "item_id": 1}),
        ("/api/youtube/queue", {"op": "add_many", "music_ids": ["a" * 16], "substituir": True,
                                "tirar_primeiro": True}),
    ]
    LIBERADAS = [
        ("/api/spotify/pause", {}),
        ("/api/spotify/volume", {"value": 30}),
        ("/api/youtube/command", {"action": "pause"}),
        ("/api/youtube/command", {"action": "volume", "params": {"value": 30}}),
        ("/api/youtube/command", {"action": "seek", "params": {"segundos": 5}}),
        ("/api/youtube/queue", {"op": "pop"}),
        ("/api/youtube/queue", {"op": "clear"}),
        ("/api/youtube/queue", {"op": "add_many", "music_ids": ["a" * 16]}),
    ]

    def test_musica_bloqueada_durante_o_loop(self) -> None:
        self.bloquear = True
        self.assertEqual(self.iniciar().status_code, 202)
        self.esperar_volta()
        with mock.patch.object(server, "_lista_da_rota", lambda i: None), \
                mock.patch.object(server, "_atalho_da_rota", lambda i: None), \
                mock.patch.object(server, "spotify_comando", mock.Mock(return_value=(204, None))) as sc, \
                mock.patch.object(server, "spotify_active_device_id", lambda: "dev"):
            for rota, corpo in self.BLOQUEADAS:
                with self.subTest(rota=rota, corpo=corpo):
                    r = self.chamar("POST", rota, self.rede["adm"], local=False, corpo=corpo)
                    self.assertEqual(r.status_code, 409, r.get_data(as_text=True))
                    self.assertEqual((r.get_json()["codigo"], r.get_json()["error"]),
                                     ("teste_tocando", server.MSG_TESTE_CAIXAS_TOCANDO))
            sc.reset_mock()
            for rota, corpo in self.LIBERADAS:
                with self.subTest(liberada=rota, corpo=corpo):
                    r = self.chamar("POST", rota, self.rede["adm"], local=False, corpo=corpo)
                    self.assertNotEqual(r.status_code, 409, r.get_data(as_text=True))
        self.parar()

    def test_bloqueadas_nao_chegam_ao_spotify(self) -> None:
        self.bloquear = True
        self.iniciar()
        self.esperar_volta()
        with mock.patch.object(server, "spotify_comando", mock.Mock(return_value=(204, None))) as sc, \
                mock.patch.object(server, "spotify_active_device_id", lambda: "dev"):
            for rota in ("/api/spotify/play", "/api/spotify/next", "/api/spotify/previous"):
                self.chamar("POST", rota, self.rede["adm"], local=False, corpo={})
        self.assertEqual(sc.call_count, 0)
        self.parar()

    def test_musica_volta_a_liberar_depois_do_loop(self) -> None:
        self.bloquear = True
        self.iniciar()
        self.esperar_volta()
        self.parar()
        with mock.patch.object(server, "spotify_comando", mock.Mock(return_value=(204, None))), \
                mock.patch.object(server, "spotify_active_device_id", lambda: "dev"):
            r = self.chamar("POST", "/api/spotify/play", self.rede["adm"], local=False, corpo={})
        self.assertEqual(r.status_code, 200)

    # --- M1 ----------------------------------------------------------------------------------
    def test_teto_do_spotify_segue_valendo_durante_o_loop(self) -> None:
        dados = {"is_playing": True, "device": {"volume_percent": 100, "id": "dev1"}}
        chamadas: list[Any] = []

        def comando(metodo: str, caminho: str, **kw: Any) -> tuple[int, None]:
            chamadas.append((metodo, caminho, kw.get("params")))
            return 204, None

        server._teto_spotify["proxima"] = 0.0
        self.bloquear = True
        self.assertEqual(self.iniciar().status_code, 202)
        self.esperar_volta()
        with mock.patch.object(server, "spotify_comando", comando):
            server._corrigir_teto_spotify(dados)
        self.assertEqual(chamadas, [("PUT", "/me/player/volume", {"volume_percent": 70, "device_id": "dev1"})])
        self.parar()
        # controle: um anúncio comum (trava presa, sem loop) continua suspendendo a correção
        server._teto_spotify["proxima"] = 0.0
        chamadas.clear()
        self.assertTrue(server._anuncio_lock.acquire(blocking=False))
        try:
            with mock.patch.object(server, "spotify_comando", comando):
                server._corrigir_teto_spotify(dados)
        finally:
            server._anuncio_lock.release()
        self.assertEqual(chamadas, [])

    # --- revisor #3 e #4 ---------------------------------------------------------------------
    def test_ativo_cai_antes_de_a_trava_soltar(self) -> None:
        vistos: list[bool] = []
        with mock.patch.object(server, "_anuncio_lock", _Proxy(server._anuncio_lock, vistos.append)):
            self.bloquear = True
            self.iniciar()
            self.esperar_volta()
            self.parar()
        self.assertEqual(vistos, [False])

    def test_ativo_desde_que_pega_a_trava(self) -> None:
        visto: dict[str, Any] = {}

        def internet(*a: Any, **k: Any) -> bool:
            visto["ativo"] = server._teste_caixas_ativo()
            visto["msg"] = server._msg_ocupado("padrão")
            return False   # voz da loja "online" sem internet: o iniciar falha DEPOIS de marcar

        antes = self.estado()["fim_motivo"]
        with mock.patch.object(server, "voz_loja", lambda: {**server.VOZ_LOJA_PADRAO, "motor": "online"}), \
                mock.patch.object(server, "tem_internet", internet):
            r = self.iniciar()
        self.assertEqual(r.status_code, 503)
        self.assertEqual(visto, {"ativo": True, "msg": server.MSG_TESTE_CAIXAS_TOCANDO})
        self.assertFalse(server._teste_caixas_ativo())      # revertido
        self.assertFalse(server._anuncio_lock.locked())
        self.assertEqual(self.estado()["fim_motivo"], antes)

    # --- B4 ----------------------------------------------------------------------------------
    def test_429_do_iniciar_e_auditoria_agregada(self) -> None:
        with server._agregado_lock:
            server._agregado.clear()   # janela de agregação limpa (estado global entre testes)
        with self.assertLogs(server.auditoria.name, level="INFO") as cap:
            for _ in range(14):
                self.chamar("POST", INICIAR, self.rede["adm"], local=False, corpo={"mensagem_id": "nao-existe"})
        limites = [x for x in cap.output if "acao=teste_caixas_limite" in x]
        self.assertEqual(len(limites), 1)   # 4 recusas, UMA linha (a primeira da janela)

    # --- D3: limite diário -------------------------------------------------------------------
    def test_corpo_traz_limite_e_uso_do_dia(self) -> None:
        corpo = self.estado()
        self.assertEqual((corpo["limite_dia_s"], corpo["usado_hoje_s"]), (10800, 0))
        self.assertIsInstance(corpo["usado_hoje_s"], int)

    def test_dia_esgotado_409_limite_diario(self) -> None:
        self.uso(10790)   # sobram 10 s (< 60)
        r = self.iniciar()
        self.assertEqual(r.status_code, 409, r.get_data(as_text=True))
        self.assertEqual((r.get_json()["codigo"], r.get_json()["error"]),
                         ("limite_diario", "Limite de 3 h de teste por dia atingido. Tente amanhã."))
        self.assertEqual(self.estado()["usado_hoje_s"], 10790)
        self.assertFalse(server._anuncio_lock.locked())
        self.assertTrue(server._anuncio_lock.acquire(blocking=False))   # vem antes da trava ocupada
        try:
            self.assertEqual(self.iniciar().get_json()["codigo"], "limite_diario")
        finally:
            server._anuncio_lock.release()

    def test_restante_e_o_menor_entre_loop_e_dia(self) -> None:
        self.uso(10000)
        self.bloquear = True
        r = self.iniciar()
        self.assertEqual((r.status_code, r.get_json()["restante_s"]), (202, 800))   # dia (800) < loop (5400)
        self.esperar_volta()
        self.tempo[0] += 100
        corpo = self.estado()
        self.assertEqual((corpo["restante_s"], corpo["usado_hoje_s"]), (700, 10100))
        self.parar()

    def test_loop_corta_no_saldo_do_dia(self) -> None:
        self.uso(10000)
        self.passo = 500.0
        self.assertEqual(self.iniciar().status_code, 202)
        self.esperar_fim()
        corpo = self.estado()
        self.assertEqual((corpo["tocando"], corpo["fim_motivo"], corpo["usado_hoje_s"]), (False, "limite_diario", 11000))
        self.assertEqual(len(self.chamadas), 2)   # 800 -> 300 -> -200: confere ENTRE as voltas
        self.assertEqual(server.load_config()["teste_caixas_uso"], {"data": self.dia[0], "segundos": 11000})
        self.assertEqual([(x["tipo"], x["resultado"]) for x in self.linhas_historico()], [("teste_caixas", "ok")])
        self.assertEqual(self.iniciar().status_code, 409)   # e o dia ficou esgotado

    def test_loops_somam_no_dia(self) -> None:
        self.passo = 1000.0
        usos = []
        for _ in range(2):
            self.assertEqual(self.iniciar().status_code, 202)
            self.esperar_volta(2)
            self.parar()
            usos.append(self.estado()["usado_hoje_s"])
        self.assertGreaterEqual(usos[0], 1000)
        self.assertGreaterEqual(usos[1], usos[0] + 1000)   # o 2º loop soma ao 1º

    def test_reinicio_nao_zera_o_dia(self) -> None:
        self.uso(4321)   # config.json tem o uso; a memória foi "esquecida"
        self.assertEqual(self.estado()["usado_hoje_s"], 4321)

    def test_virada_do_dia_zera(self) -> None:
        self.uso(10790)
        self.assertEqual(self.iniciar().status_code, 409)
        self.dia[0] = "2026-11-12"
        self.assertEqual(self.estado()["usado_hoje_s"], 0)
        self.bloquear = True
        self.assertEqual(self.iniciar().status_code, 202)
        self.esperar_volta()
        self.parar()

    def test_config_com_uso_torto_falha_fechado(self) -> None:
        hoje, limite = self.dia[0], 10800
        casos = [("x", limite), ({"data": "ontem", "segundos": 5}, 5),          # data malformada = hoje, valor saneado
                 ({"data": hoje, "segundos": -1}, 0), ({"data": hoje, "segundos": 5.9}, 5),
                 ({"data": hoje, "segundos": True}, limite), ({"data": hoje, "segundos": "9"}, limite),
                 ({"data": hoje, "segundos": None}, limite), ({"data": hoje, "segundos": 10 ** 9}, limite),
                 ({"data": hoje, "segundos": float("nan")}, limite), ({"data": hoje, "segundos": float("inf")}, limite),
                 ({"data": hoje, "segundos": float("-inf")}, limite), ({"data": hoje}, limite),
                 ({"data": "2020-01-01", "segundos": 10 ** 9}, 0)]          # dia ANTIGO bem formado: dia novo, zera
        for torto, esperado in casos:
            with self.subTest(torto=torto):
                with mock.patch.object(server, "load_config", lambda t=torto: {"teste_caixas_uso": t}):
                    server._teste_uso.update(data=None, s=0.0, mono=None, gravado_mono=None)
                    self.assertEqual(server._teste_publico()["usado_hoje_s"], esperado)
        # e o valor torto de hoje esgota o dia: o iniciar recusa
        server.update_config(teste_caixas_uso="x")
        server._teste_uso.update(data=None, s=0.0, mono=None, gravado_mono=None)
        self.assertEqual(self.iniciar().status_code, 409)

    def test_relogio_voltando_mantem_o_uso_gravado(self) -> None:
        self.uso(500, dia="2026-11-12")      # gravado AMANHÃ em relação ao relógio de hoje (2026-11-11)
        self.assertEqual(self.estado()["usado_hoje_s"], 500)   # não zerou
        self.passo = 100.0
        self.assertEqual(self.iniciar().status_code, 202)
        self.esperar_volta(2)
        self.parar()
        self.assertGreaterEqual(self.estado()["usado_hoje_s"], 600)   # continua somando em cima do gravado
        self.assertEqual(server.load_config()["teste_caixas_uso"]["data"], "2026-11-12")
        self.dia[0] = "2026-11-13"            # um dia POSTERIOR ao gravado zera
        self.assertEqual(self.estado()["usado_hoje_s"], 0)

    # --- A1: desligar no meio do iniciar (a trava herdada nunca é solta duas vezes) ---------------
    def test_desligar_no_meio_do_iniciar_nao_solta_a_trava_herdada(self) -> None:
        dentro, segue = threading.Event(), threading.Event()
        resposta: dict[str, Any] = {}

        def internet_lenta(*a: Any, **k: Any) -> bool:
            dentro.set()
            segue.wait(5.0)
            return False   # voz "online" sem internet: o iniciar falha DEPOIS de o desligar herdar a trava

        def chamar_iniciar() -> None:
            try:
                resposta["r"] = self.iniciar()
            except BaseException as exc:  # noqa: BLE001
                resposta["erro"] = exc

        with mock.patch.object(server, "voz_loja", lambda: {**server.VOZ_LOJA_PADRAO, "motor": "online"}), \
                mock.patch.object(server, "tem_internet", internet_lenta):
            t = threading.Thread(target=chamar_iniciar)
            t.start()
            self.assertTrue(dentro.wait(5.0))
            self.assertTrue(server._teste_caixas_ativo())          # JÁ marcado: o desligar vê o teste
            self.assertEqual(self.desligar().status_code, 202)
            self.assertTrue(server._desligamento_agendado)
            segue.set()
            t.join(5.0)
        self.assertNotIn("erro", resposta)                          # nenhum RuntimeError de dupla liberação
        self.assertEqual(resposta["r"].status_code, 503)
        self.assertFalse(server._teste_caixas_ativo())
        self.assertFalse(server._teste["entregar_trava"])
        self.assertTrue(server._anuncio_lock.locked())              # a trava ficou com o desligamento

    # --- A2: "parar" depois do "desligar" não troca o motivo nem retoma a música -------------------
    def test_parar_depois_do_desligar_nao_retoma_a_musica(self) -> None:
        retomar = self._musica_real()
        self.bloquear = self.ignora_parar = True
        self.assertEqual(self.iniciar().status_code, 202)
        self.esperar_volta()
        self.assertEqual(self.desligar().status_code, 202)
        with mock.patch.object(server, "TESTE_CAIXAS_PARAR_ESPERA_S", 0.2):
            self.assertEqual(self.parar().status_code, 200)         # a fala não corta: o parar só espera um instante
        self.assertEqual(server._teste["pedido"], "desligando")
        self.libera.set()
        self.esperar_fim()
        self.assertEqual(self.estado()["fim_motivo"], "desligando")
        retomar.assert_not_called()

    def test_desligamento_agendado_encerra_o_laco_mesmo_sem_pedido(self) -> None:
        self.passo = 1.0
        self.assertEqual(self.iniciar().status_code, 202)
        self.esperar_volta()
        with mock.patch.object(server, "_desligamento_agendado", True):
            self.esperar_fim()
        self.assertEqual(self.estado()["fim_motivo"], "desligando")

    # --- D2 residual: tocar_lista() e tocar_atalho_spotify() reconferem o loop -----------------
    def test_tocar_lista_reconfere_o_loop_antes_de_trocar_a_fila(self) -> None:
        lista = {"id": "abcd1234", "nome": "L", "versao": 1,
                 "musicas": [{"tipo": "youtube", "video_id": "abcdefghijk", "list_id": None, "index": None,
                              "start": None, "titulo": "Vídeo"}]}
        enfileirar = mock.Mock()
        self.bloquear = True
        self.iniciar()
        self.esperar_volta()
        with mock.patch.object(server, "lista_obter", lambda i: lista), \
                mock.patch.object(server, "_tocador_vivo", lambda: True), \
                mock.patch.object(server, "youtube_enqueue", enfileirar), \
                mock.patch.dict(server._yt_estado, {"last_heartbeat": time.monotonic(), "yt_pronto": True}):
            with self.assertRaises(server.ErroLista) as cm:
                server.tocar_lista("abcd1234")
            self.assertEqual((cm.exception.status, cm.exception.codigo), (409, "teste_tocando"))
            enfileirar.assert_not_called()
            # na Programação vira "não tocou" com o motivo estável
            ctx = programacao.Execucao(origem="agendado", criado_mono=0.0, prazo_mono=1e9,
                                       sinal=programacao.SinalParada(), lock_tomado=False,
                                       esperar=lambda s: False, monotonic=time.monotonic)
            with mock.patch.object(server, "_pausar_spotify_se_tocando", lambda avisos: None):
                # chamada direta (sem a guarda de _prog_executar): é o horário que já esperava quando o loop começou
                with self.assertRaises(programacao.NaoTocou) as pm:
                    server._prog_executar_lista({"id": "x" * 8, "tipo": "lista", "alvo": "abcd1234"}, ctx)
            self.assertEqual(pm.exception.motivo, "teste_caixas")
        self.parar()

    def test_tocar_atalho_reconfere_o_loop_antes_do_put(self) -> None:
        comando = mock.Mock(return_value=(204, None))
        self.bloquear = True
        self.iniciar()
        self.esperar_volta()
        with mock.patch.object(server, "atalho_spotify_obter", lambda i: {"uri": "spotify:playlist:" + "a" * 22}), \
                mock.patch.object(server, "spotify_active_device_id", lambda: "dev"), \
                mock.patch.object(server, "spotify_comando", comando):
            with self.assertRaises(server.ErroTocarSpotify) as cm:
                server.tocar_atalho_spotify("abcd1234")
            self.assertEqual((cm.exception.codigo, cm.exception.http), ("teste_tocando", 409))
            comando.assert_not_called()
            with self.assertRaises(programacao.NaoTocou) as pm:
                server._prog_executar_spotify({"id": "x" * 8, "tipo": "spotify", "alvo": "abcd1234"}, None)
            self.assertEqual(pm.exception.motivo, "teste_caixas")
        self.parar()

    def test_uso_gravado_durante_o_loop(self) -> None:
        self.passo = 70.0
        self.assertEqual(self.iniciar().status_code, 202)
        self.esperar_volta(3)
        gravado = server.load_config()["teste_caixas_uso"]
        self.assertGreaterEqual(gravado["segundos"], 70)    # queda de energia não apaga o que já passou
        self.parar()

    def test_uso_nao_sai_em_outras_respostas(self) -> None:
        self.uso(777)
        for rota in ("/api/voz", "/api/mensagens", "/api/auth/status", "/api/status"):
            r = self.chamar("GET", rota, self.rede["adm"], local=False)
            self.assertNotIn("teste_caixas_uso", r.get_data(as_text=True), rota)


class TesteHigiene(unittest.TestCase):
    def test_rotas_no_inventario_do_docstring(self) -> None:
        doc = server.__doc__ or ""
        for rota in (ROTA, INICIAR, PARAR):
            self.assertIn(rota, doc)
        self.assertIn("Contrato v2.8.10", doc)
        self.assertIn("v2.8.10", doc.splitlines()[1])


if __name__ == "__main__":
    unittest.main()
