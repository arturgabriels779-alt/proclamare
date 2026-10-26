"""
Testes do histórico de anúncios e do autor nos carimbos (v2.8 B6).

  * Cada anúncio (digitado/mensagem pronta, "Tocar agora" de um horário e o anúncio agendado que toca sozinho) vira
    UMA linha em anuncios.jsonl (ao lado do config.json temporário): t, conta_id, usuario, nome, funcao, texto,
    resultado, origem (local|rede) e tipo (digitado|tocar_agora|agendado). Sem conta = "antes das contas".
  * GET /api/historico: o mais novo primeiro, 100 por página, filtros de/ate/conta/q/antes; funcionário vê só os
    próprios; nenhuma rota apaga nem edita. Poda de 90 dias com relógio falso; linha ilegível é ignorada/descartada.
  * Carimbos autor_id/autor_nome em horários, listas, atalhos e mensagens: só o servidor grava (cliente = 400).
  * NUNCA há som real: `_anunciar`/`_anunciar_na_saida` viram registradores (e a trava global do aviso vale).
    Senhas, usuários e nomes são FICTÍCIOS. Nenhum teste sobe servidor nem usa as portas 8502/8599.

    python -m unittest tests.test_historico -v
"""
from __future__ import annotations

import json
import logging
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from unittest import mock

import contas
import programacao
import server
from tests import isolamento
from tests import test_programacao as tp
from tests.test_auth import HOST_REMOTO, PIN, ip_remoto
from tests.test_login_contas import SENHA_ADM, SENHA_FUNC, SENHA_GER, BaseContas, cookie_da_resposta

COOKIE = server.COOKIE_SESSAO
TEXTO_SECRETO = "Fiscal ao caixa sete, texto-secreto-xyz"
CAMPOS = {"t", "conta_id", "usuario", "nome", "funcao", "texto", "resultado", "origem", "tipo"}
FUSO = timezone(timedelta(hours=-3))
LINK = "https://open.spotify.com/playlist/" + "H" * 22


class BaseHistorico(BaseContas):
    """BaseContas + anúncio em registrador (nada toca) + programação com relógio falso, sem threads."""

    def setUp(self) -> None:
        super().setUp()
        self.falas: list[str] = []
        self.falhar = False

        def anunciar(texto: str, *a: Any, **kw: Any) -> tuple[str, None]:
            if self.falhar:
                raise RuntimeError("falha simulada da fala")
            self.falas.append(texto)
            return "offline", None

        def anunciar_na_saida(texto: str, *a: Any, **kw: Any) -> tuple[str, None, dict]:
            self.falas.append(texto)
            return "offline", None, {}

        for alvo, valor in (("_anunciar", anunciar), ("_anunciar_na_saida", anunciar_na_saida),
                            ("tem_internet", lambda *a, **k: False), ("obter_vozes_offline", lambda: None)):
            p = mock.patch.object(server, alvo, valor)
            p.start()
            self.addCleanup(p.stop)
        p = mock.patch.object(server.SOM, "config_som", return_value=None)
        p.start()
        self.addCleanup(p.stop)
        # programação: relógio falso e "Tocar agora" na mesma thread
        self.rel = tp.Relogio(datetime(2026, 10, 19, 21, 44, 0))   # segunda-feira
        self.servico = server._prog_criar_servico(agora=self.rel.agora, monotonic=self.rel.mono,
                                                  esperar=self.rel.esperar, disparar=lambda f: f())
        salvo = server.PROGRAMACAO
        server.PROGRAMACAO = self.servico
        self.addCleanup(setattr, server, "PROGRAMACAO", salvo)
        self.addCleanup(self.servico.parar)
        self.addCleanup(self._soltar_trava)
        self.assertTrue(isolamento.dentro_do_temporario(server._anuncios_path()))

    @staticmethod
    def _soltar_trava() -> None:
        if server._anuncio_lock.locked():
            server._anuncio_lock.release()

    # --- utilidades ---------------------------------------------------------------------------
    def linhas(self) -> list[dict]:
        caminho = server._anuncios_path()
        if not caminho.exists():
            return []
        return [json.loads(x) for x in caminho.read_text(encoding="utf-8").splitlines() if x.strip()]

    def anunciar_rede(self, token: str, texto: str = TEXTO_SECRETO, ip: int = 80) -> Any:
        return self.remoto("post", "/api/announce", ip=ip, json={"text": texto}, headers=self.com_cookie(token))

    def get_rede(self, token: str, caminho: str, ip: int = 81) -> Any:
        return self.remoto("get", caminho, ip=ip, headers=self.com_cookie(token))

    @staticmethod
    def anuncio_item(**kw: Any) -> dict:
        base = {"tipo": "anuncio", "alvo": TEXTO_SECRETO, "hora": "21:45", "dias": [1], "ativo": True}
        base.update(kw)
        return base

    def salvar_prog(self, token: str | None, itens: list[dict]) -> Any:
        versao = server._prog_arquivo.ler()["versao"]
        corpo = {"versao": versao, "itens": itens}
        if token is None:
            return self.l.post("/api/programacao", json=corpo, headers={"Cookie": f"{COOKIE}={self.token_adm}"})
        return self.remoto("post", "/api/programacao", json=corpo, headers=self.com_cookie(token))

    def rodar_agendados(self) -> None:
        """Passa o horário 21:45 da segunda-feira e executa o que venceu (sem threads)."""
        self.servico.preparar()
        self.rel.t = datetime(2026, 10, 19, 21, 45, 2)
        self.servico.varrer()
        self.servico.drenar()


class BaseComContas(BaseHistorico):
    def setUp(self) -> None:
        super().setUp()
        self.adm = self.criar_adm()
        self.ger = self.criar("gil.gerente", "gerente", SENHA_GER)
        self.func = self.criar("fia.func", "funcionario", SENHA_FUNC)
        self.tok = {}
        for ip, (perfil, usuario, senha) in enumerate((("adm", "ana.adm", SENHA_ADM), ("gerente", "gil.gerente", SENHA_GER),
                                                       ("funcionario", "fia.func", SENHA_FUNC)), start=60):
            r = self.login_conta(usuario, senha, ip=ip)
            self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
            self.tok[perfil] = cookie_da_resposta(r)


# ---------------------------------------------------------------------------------------------
# Gravação
# ---------------------------------------------------------------------------------------------
class TesteGravacao(BaseComContas):
    def test_anuncio_digitado_registra_autor_e_texto(self) -> None:
        acl: list[str] = []
        real = server._restringir_acl_windows
        with mock.patch.object(server, "_restringir_acl_windows", side_effect=lambda c: (acl.append(c.name), real(c))):
            r = self.anunciar_rede(self.tok["funcionario"])
            self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
            # o PC das caixas, com a sessão local do ADM
            r = self.l.post("/api/announce", json={"text": "Do PC"}, headers={"Cookie": f"{COOKIE}={self.token_adm}"})
            self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        self.assertEqual(acl.count(server.ANUNCIOS_ARQUIVO), 1)   # ACL restrita só ao nascer o arquivo
        e1, e2 = self.linhas()
        self.assertEqual(set(e1), CAMPOS)
        self.assertEqual((e1["conta_id"], e1["usuario"], e1["nome"], e1["funcao"]),
                         (self.func["id"], "fia.func", self.func["nome"], self.func["funcao"]))
        self.assertEqual((e1["texto"], e1["resultado"], e1["origem"], e1["tipo"]),
                         (TEXTO_SECRETO, "ok", "rede", "digitado"))
        self.assertIsNotNone(datetime.fromisoformat(e1["t"]).tzinfo)   # ISO com fuso
        self.assertEqual((e2["conta_id"], e2["usuario"], e2["origem"], e2["texto"]),
                         (self.adm["id"], "ana.adm", "local", "Do PC"))
        # acentos guardados como texto (ensure_ascii=False), uma linha por anúncio
        self.anunciar_rede(self.tok["gerente"], "Atenção, promoção")
        self.assertIn("Atenção, promoção", server._anuncios_path().read_text(encoding="utf-8"))
        self.assertEqual(len(server._anuncios_path().read_text(encoding="utf-8").splitlines()), 3)

    def test_ocupado_e_erro_registrados(self) -> None:
        # ocupado (409)
        server._anuncio_lock.acquire()
        try:
            r = self.anunciar_rede(self.tok["funcionario"], "Ocupado")
        finally:
            server._anuncio_lock.release()
        self.assertEqual(r.status_code, 409)
        # erro (500)
        self.falhar = True
        with self.assertLogs(server.log.name, level="ERROR"):
            self.assertEqual(self.anunciar_rede(self.tok["funcionario"], "Erro").status_code, 500)
        self.falhar = False
        # sem internet (503) com a voz da loja em "online"
        cfg = server.load_config()
        cfg["voz_loja"] = {"motor": "online"}
        server.save_config(cfg)
        self.assertEqual(self.anunciar_rede(self.tok["funcionario"], "Sem rede").status_code, 503)
        self.assertEqual([(e["texto"], e["resultado"]) for e in self.linhas()],
                         [("Ocupado", "ocupado"), ("Erro", "erro"), ("Sem rede", "sem_internet")])
        # NÃO registra: 400 (validação), 401 (sem sessão), 403 (sem permissão não existe para anunciar)
        antes = len(self.linhas())
        self.assertEqual(self.anunciar_rede(self.tok["funcionario"], "   ").status_code, 400)
        r = self.remoto("post", "/api/announce", json={"text": "sem sessão"})
        self.assertEqual(r.status_code, 401)
        self.assertEqual(len(self.linhas()), antes)

    def test_limite_429_registrado_com_teto(self) -> None:
        """O 429 entra no histórico como "limite", mas no máximo HISTORICO_LIMITE_MAX_POR_MIN por IP/conta (um
        aparelho insistente não enche o disco com texto)."""
        cfg = server.load_config()
        cfg["voz_loja"] = {"motor": "auto"}
        server.save_config(cfg)
        for i in range(server.ANUNCIO_REMOTO_MAX_POR_MIN):
            self.assertEqual(self.anunciar_rede(self.tok["funcionario"], f"A{i}").status_code, 200)
        for i in range(server.HISTORICO_LIMITE_MAX_POR_MIN + 5):
            self.assertEqual(self.anunciar_rede(self.tok["funcionario"], f"Demais{i}").status_code, 429)
        limites = [e for e in self.linhas() if e["resultado"] == "limite"]
        self.assertEqual(len(limites), server.HISTORICO_LIMITE_MAX_POR_MIN)
        self.assertEqual((limites[0]["texto"], limites[0]["conta_id"], limites[0]["tipo"], limites[0]["origem"]),
                         ("Demais0", self.func["id"], "digitado", "rede"))

    def test_tocar_agora_ocupado_registrado(self) -> None:
        r = self.salvar_prog(self.tok["gerente"], [self.anuncio_item()])
        item = r.get_json()["itens"][0]
        server._anuncio_lock.acquire()
        try:
            r = self.remoto("post", f"/api/programacao/{item['id']}/testar", json={},
                            headers=self.com_cookie(self.tok["gerente"]))
        finally:
            server._anuncio_lock.release()
        self.assertEqual(r.status_code, 409)
        (e,) = self.linhas()
        self.assertEqual((e["conta_id"], e["tipo"], e["resultado"], e["origem"], e["texto"]),
                         (self.ger["id"], "tocar_agora", "ocupado", "rede", TEXTO_SECRETO))
        self.assertEqual(self.falas, [])

    def test_falha_inesperada_ao_gravar_nao_derruba_o_anuncio(self) -> None:
        with mock.patch.object(server, "_restringir_acl_windows", side_effect=RuntimeError("x")), \
                self.assertLogs(server.log.name, level="ERROR") as logs:
            r = self.anunciar_rede(self.tok["funcionario"])
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        self.assertNotIn(TEXTO_SECRETO, "\n".join(logs.output))

    def test_linha_cortada_no_fim_nao_cola_na_proxima(self) -> None:
        """Processo morto no meio da escrita: o arquivo termina sem "\n"; o próximo anúncio começa numa linha nova."""
        self.anunciar_rede(self.tok["funcionario"], "Inteiro")
        with server._anuncios_path().open("a", encoding="utf-8", newline="\n") as f:
            f.write('{"t":"2026-10-2')   # fragmento sem "\n"
        self.anunciar_rede(self.tok["funcionario"], "Depois do corte")
        r = self.get_rede(self.tok["gerente"], "/api/historico")
        self.assertEqual([x["texto"] for x in r.get_json()["itens"]], ["Depois do corte", "Inteiro"])
        brutas = server._anuncios_path().read_text(encoding="utf-8").split("\n")
        self.assertEqual(brutas[1], '{"t":"2026-10-2')   # o fragmento ficou sozinho na linha dele

    def test_t_e_o_inicio_do_anuncio(self) -> None:
        """O "t" é o instante em que o anúncio COMEÇA, não o fim (o relógio anda 30 s durante cada fala)."""
        relogio = {"t": datetime(2026, 10, 19, 21, 44, 0, tzinfo=FUSO)}
        falar_real = server._anunciar

        def falar_devagar(*a: Any, **kw: Any) -> Any:
            relogio["t"] += timedelta(seconds=30)
            return falar_real(*a, **kw)

        r = self.salvar_prog(self.tok["gerente"], [self.anuncio_item()])
        item = r.get_json()["itens"][0]
        inicios = []
        with mock.patch.object(server, "_historico_agora", lambda: relogio["t"]), \
                mock.patch.object(server, "_anunciar", falar_devagar):
            inicios.append(relogio["t"])
            self.assertEqual(self.anunciar_rede(self.tok["funcionario"], "Digitado").status_code, 200)
            inicios.append(relogio["t"])
            r = self.remoto("post", f"/api/programacao/{item['id']}/testar", json={},
                            headers=self.com_cookie(self.tok["gerente"]))
            self.assertEqual(r.status_code, 202, r.get_data(as_text=True))
            inicios.append(relogio["t"])
            self.rodar_agendados()
        self.assertEqual(len(self.falas), 3)
        self.assertEqual([(e["tipo"], datetime.fromisoformat(e["t"])) for e in self.linhas()],
                         list(zip(("digitado", "tocar_agora", "agendado"), inicios)))

    def test_falha_ao_achar_o_autor_nao_troca_o_resultado_do_horario(self) -> None:
        self.salvar_prog(self.tok["gerente"], [self.anuncio_item()])
        with mock.patch.object(server, "_autor_do_item", side_effect=RuntimeError(TEXTO_SECRETO)), \
                self.assertLogs(server.log.name, level="ERROR") as logs:
            self.rodar_agendados()
        self.assertEqual(self.falas, [TEXTO_SECRETO])
        self.assertEqual(self.servico.historico()[0]["status"], "ok")   # o resultado real fica
        self.assertIn("RuntimeError", "\n".join(logs.output))
        self.assertNotIn(TEXTO_SECRETO, "\n".join(logs.output))       # só o nome da exceção
        self.assertEqual(self.linhas(), [])

    def test_contas_ilegivel_no_agendado_usa_o_nome_guardado(self) -> None:
        self.salvar_prog(self.tok["gerente"], [self.anuncio_item()])
        with mock.patch.object(server, "conta_por_id", side_effect=OSError("disco")):
            self.rodar_agendados()
        (e,) = self.linhas()
        self.assertEqual((e["conta_id"], e["nome"], e["usuario"], e["resultado"]),
                         (self.ger["id"], self.ger["nome"], None, "ok"))

    def test_agendado_registra_autor_do_item(self) -> None:
        r = self.salvar_prog(self.tok["gerente"], [self.anuncio_item()])
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        item = r.get_json()["itens"][0]
        self.assertEqual(item["autor_nome"], self.ger["nome"])
        self.assertEqual(server._prog_arquivo.ler()["itens"][0]["autor_id"], self.ger["id"])
        self.rodar_agendados()
        self.assertEqual(self.falas, [TEXTO_SECRETO])
        (e,) = self.linhas()
        self.assertEqual((e["conta_id"], e["usuario"], e["nome"], e["funcao"], e["tipo"], e["origem"], e["resultado"]),
                         (self.ger["id"], "gil.gerente", self.ger["nome"], self.ger["funcao"], "agendado", "local", "ok"))
        # "Tocar agora" pelo ADM na rede: o autor é quem pediu, não o autor do item
        r = self.remoto("post", f"/api/programacao/{item['id']}/testar", json={}, headers=self.com_cookie(self.tok["adm"]))
        self.assertEqual(r.status_code, 202, r.get_data(as_text=True))
        e = self.linhas()[-1]
        self.assertEqual((e["conta_id"], e["usuario"], e["tipo"], e["origem"], e["texto"]),
                         (self.adm["id"], "ana.adm", "tocar_agora", "rede", TEXTO_SECRETO))
        # mexer no item muda o autor; só ligar/desligar pelo PC não muda
        itens = [{k: item[k] for k in ("id", "tipo", "alvo", "hora", "dias", "ativo")}]
        itens[0]["alvo"] = "Texto novo"
        r = self.salvar_prog(self.tok["adm"], itens)
        self.assertEqual(server._prog_arquivo.ler()["itens"][0]["autor_id"], self.adm["id"], r.get_data(as_text=True))

    def test_agendado_que_nao_tocou_fica_registrado_e_audio_nao_entra(self) -> None:
        cfg = server.load_config()
        cfg["voz_loja"] = {"motor": "auto"}
        server.save_config(cfg)
        self.salvar_prog(self.tok["gerente"], [self.anuncio_item(motor="online")])
        self.rodar_agendados()   # motor online sem internet: "não tocou"
        (e,) = self.linhas()
        self.assertEqual((e["resultado"], e["tipo"]), ("sem_internet", "agendado"))


class TesteAntesDasContas(BaseHistorico):
    """
    v2.8 (revisão final S11): em modo PIN (nenhuma conta) NADA é gravado no histórico (sem autor não há
    responsabilidade, e o texto seria dado pessoal guardado sem aviso); o GET continua listando o que houver. Em modo
    contas, um horário de antes da atualização (sem autor) continua gravado como "antes das contas".
    """

    def gravar_linha_crua(self, texto: str, conta: "dict | None") -> None:
        """Uma linha no anuncios.jsonl escrita direto (o que sobrou de quando havia contas)."""
        entrada = {"t": "2026-10-19T21:00:00.000000-03:00", "conta_id": conta and conta["id"],
                   "usuario": conta and conta["usuario"], "nome": conta["nome"] if conta else "antes das contas",
                   "funcao": conta and conta["funcao"], "texto": texto, "resultado": "ok", "origem": "rede",
                   "tipo": "digitado"}
        with open(server._anuncios_path(), "a", encoding="utf-8") as f:
            f.write(json.dumps(entrada, ensure_ascii=False) + "\n")

    def test_modo_pin_nao_grava_historico(self) -> None:
        self.assertFalse(server.modo_contas())
        self.assertEqual(self.c.post("/api/announce", json={"text": "Modo PIN"}).status_code, 200)
        with server._prog_arquivo.lock:
            server._prog_arquivo.gravar({"versao": 1, "itens": [tp.item("abcd0001", "21:45", dias=[1],
                                                                          alvo="Antigo")]})
        self.rodar_agendados()
        self.assertEqual(self.falas, ["Modo PIN", "Antigo"])            # tocou, mas não foi gravado
        self.assertEqual(self.linhas(), [])
        r = self.c.get("/api/historico")                                 # o GET continua funcionando
        self.assertEqual((r.status_code, r.get_json()["itens"]), (200, []))

    def test_item_antigo_antes_das_contas(self) -> None:
        # modo contas: um item gravado antes das contas (sem autor) e que toca no horário
        self.criar_adm()
        with server._prog_arquivo.lock:
            server._prog_arquivo.gravar({"versao": 1, "itens": [tp.item("abcd0001", "21:45", dias=[1],
                                                                          alvo="Antigo")]})
        self.rodar_agendados()
        (e1,) = self.linhas()
        self.assertEqual((e1["conta_id"], e1["usuario"], e1["nome"], e1["funcao"]),
                         (None, None, "antes das contas", None))
        self.assertEqual((e1["tipo"], e1["origem"], e1["texto"]), ("agendado", "local", "Antigo"))
        cab = {"Cookie": f"{COOKIE}={self.token_adm}"}
        # o GET do item continua sem autor (o painel mostra "antes das contas")
        self.assertNotIn("autor_nome", self.l.get("/api/programacao", headers=cab).get_json()["itens"][0])
        r = self.l.get("/api/historico", headers=cab)
        self.assertEqual(r.status_code, 200)
        self.assertEqual([x["texto"] for x in r.get_json()["itens"]], ["Antigo"])

    def test_sessao_de_pin_remota_le_o_historico_inteiro(self) -> None:
        """Modo PIN: a sessão de PIN pela rede vale como gerente e lê tudo o que houver no arquivo (inclusive com o
        filtro por conta); nada novo entra."""
        server.aplicar_pin(PIN)
        self.assertFalse(server.modo_contas())
        token = cookie_da_resposta(self.login())
        alguem = {"id": "0123abcd", "usuario": "ex.conta", "nome": "Ex Conta", "funcao": "Caixa"}
        self.gravar_linha_crua("De uma conta", alguem)
        self.gravar_linha_crua("Sem conta", None)
        self.assertEqual(self.c.post("/api/announce", json={"text": "Do PC"}).status_code, 200)   # não grava
        r = self.remoto("get", "/api/historico", headers=self.com_cookie(token))
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        self.assertEqual(sorted((x["texto"], x["nome"]) for x in r.get_json()["itens"]),
                         [("De uma conta", "Ex Conta"), ("Sem conta", "antes das contas")])
        r = self.remoto("get", "/api/historico?conta=0123abcd", headers=self.com_cookie(token))
        self.assertEqual([x["texto"] for x in r.get_json()["itens"]], ["De uma conta"])
        self.assertEqual(self.remoto("get", "/api/historico").status_code, 401)

    def test_isolamento_falha_fechado_tambem_para_o_historico(self) -> None:
        self.assertTrue(isolamento.dentro_do_temporario(server._anuncios_path()))
        self.assertEqual(server._anuncios_path().parent, server.CONFIG_PATH.parent)
        self.assertIn("anuncios.jsonl", (Path(server.APP_DIR) / ".gitignore").read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------------------------
# Leitura
# ---------------------------------------------------------------------------------------------
class TesteLeitura(BaseComContas):
    def registrar(self, quando: datetime, conta: dict | None, texto: str, **kw: Any) -> None:
        with mock.patch.object(server, "_historico_agora", lambda: quando):
            server.registrar_anuncio(conta=conta, texto=texto, resultado=kw.get("resultado", "ok"),
                                     origem=kw.get("origem", "rede"), tipo=kw.get("tipo", "digitado"))

    def test_funcionario_ve_so_os_proprios(self) -> None:
        self.anunciar_rede(self.tok["funcionario"], "Do funcionário")
        self.anunciar_rede(self.tok["gerente"], "Do gerente")
        r = self.get_rede(self.tok["funcionario"], f"/api/historico?conta={self.ger['id']}")
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        self.assertEqual([x["texto"] for x in r.get_json()["itens"]], ["Do funcionário"])
        r = self.get_rede(self.tok["gerente"], "/api/historico")
        self.assertEqual([x["texto"] for x in r.get_json()["itens"]], ["Do gerente", "Do funcionário"])
        self.assertEqual(set(r.get_json()["itens"][0]), CAMPOS)
        r = self.get_rede(self.tok["adm"], "/api/historico")
        self.assertEqual(len(r.get_json()["itens"]), 2)
        self.assertEqual(self.remoto("get", "/api/historico").status_code, 401)

    def test_gerente_filtra_por_conta_e_data(self) -> None:
        func = server.conta_por_id(self.func["id"])
        ger = server.conta_por_id(self.ger["id"])
        base = datetime.now().astimezone().replace(hour=10, minute=0, second=0, microsecond=0) - timedelta(days=5)
        self.registrar(base, func, "Caixa UM aberto")
        self.registrar(base + timedelta(days=1), ger, "Promoção de pães")
        self.registrar(base + timedelta(days=2), func, "caixa dois aberto")
        dia = lambda d: (base + timedelta(days=d)).date().isoformat()   # noqa: E731
        g = lambda q: self.get_rede(self.tok["gerente"], "/api/historico" + q)   # noqa: E731
        self.assertEqual([x["texto"] for x in g(f"?conta={self.func['id']}").get_json()["itens"]],
                         ["caixa dois aberto", "Caixa UM aberto"])
        self.assertEqual([x["texto"] for x in g(f"?de={dia(1)}&ate={dia(1)}").get_json()["itens"]],
                         ["Promoção de pães"])
        self.assertEqual([x["texto"] for x in g(f"?de={dia(1)}").get_json()["itens"]],
                         ["caixa dois aberto", "Promoção de pães"])
        self.assertEqual([x["texto"] for x in g("?q=CAIXA").get_json()["itens"]],
                         ["caixa dois aberto", "Caixa UM aberto"])   # sem diferenciar maiúsculas
        self.assertEqual(g("?q=inexistente").get_json(), {"ok": True, "itens": [], "mais": False})
        # parâmetros inválidos = 400 com o campo
        for q, campo in (("?de=2026-13-01", "de"), ("?ate=ontem", "ate"), ("?conta=xyz", "conta"),
                         ("?q=" + "a" * 101, "q"), ("?antes=nao-e-data", "antes"), ("?outro=1", "chave"),
                         (f"?de={dia(2)}&ate={dia(1)}", "ate")):
            with self.subTest(q=q):
                r = g(q)
                self.assertEqual((r.status_code, r.get_json().get("campo")), (400, campo), r.get_data(as_text=True))

    def test_paginacao_100_mais_novos_e_antes(self) -> None:
        func = server.conta_por_id(self.func["id"])
        base = datetime.now().astimezone() - timedelta(days=1)
        for i in range(105):
            self.registrar(base + timedelta(seconds=i), func, f"N{i}")
        corpo = self.get_rede(self.tok["gerente"], "/api/historico").get_json()
        self.assertEqual((len(corpo["itens"]), corpo["mais"]), (100, True))
        self.assertEqual((corpo["itens"][0]["texto"], corpo["itens"][-1]["texto"]), ("N104", "N5"))
        from urllib.parse import quote
        corpo = self.get_rede(self.tok["gerente"], "/api/historico?antes=" + quote(corpo["itens"][-1]["t"])).get_json()
        self.assertEqual(([x["texto"] for x in corpo["itens"]], corpo["mais"]), (["N4", "N3", "N2", "N1", "N0"], False))

    def test_linha_corrompida_ignorada(self) -> None:
        self.anunciar_rede(self.tok["funcionario"], "Antes")
        with server._anuncios_path().open("a", encoding="utf-8") as f:
            f.write("{isto não é json\n")
            f.write("[1, 2, 3]\n")
            f.write(json.dumps({"t": "ontem", "texto": "data torta"}) + "\n")
            f.write("\n")
        self.anunciar_rede(self.tok["funcionario"], "Depois")
        r = self.get_rede(self.tok["gerente"], "/api/historico")
        self.assertEqual(r.status_code, 200)
        self.assertEqual([x["texto"] for x in r.get_json()["itens"]], ["Depois", "Antes"])

    def test_poda_90_dias(self) -> None:
        func = server.conta_por_id(self.func["id"])
        agora = datetime(2026, 10, 22, 12, 0, 0, tzinfo=FUSO)
        self.registrar(agora - timedelta(days=100), func, "Velho")
        self.registrar(agora - timedelta(days=89), func, "Recente")
        with server._anuncios_path().open("a", encoding="utf-8") as f:
            f.write("lixo\n")
        # a leitura já esconde o que passou de 90 dias, mesmo antes da poda
        with mock.patch.object(server, "_historico_agora", lambda: agora):
            r = self.get_rede(self.tok["gerente"], "/api/historico")
            self.assertEqual([x["texto"] for x in r.get_json()["itens"]], ["Recente"])
            acl: list[str] = []
            with mock.patch.object(server, "_restringir_acl_windows", side_effect=lambda c: acl.append(c.name)):
                self.assertEqual(server.podar_historico(), 2)   # o velho e a linha ilegível saíram
            self.assertTrue(acl and acl[0].startswith(".anuncios."))   # ACL no temporário, antes dos dados
        self.assertEqual([e["texto"] for e in self.linhas()], ["Recente"])
        self.assertEqual(list(server._anuncios_path().parent.glob(".anuncios.*.tmp")), [])
        # nada a podar: o arquivo não é regravado
        with mock.patch.object(server, "_historico_agora", lambda: agora):
            assinatura = server._anuncios_path().stat().st_mtime_ns
            self.assertEqual(server.podar_historico(), 0)
            self.assertEqual(server._anuncios_path().stat().st_mtime_ns, assinatura)
        # 91 dias depois o "Recente" também sai
        with mock.patch.object(server, "_historico_agora", lambda: agora + timedelta(days=2)):
            self.assertEqual(server.podar_historico(), 1)
        self.assertEqual(self.linhas(), [])

    def test_poda_roda_na_partida_e_a_cada_6_horas_na_vigia(self) -> None:
        chamadas: list[int] = []
        with mock.patch.object(server, "podar_historico", lambda: chamadas.append(1) or 0):
            server._historico_poda_ultima = None
            server._podar_historico_se_vencido(1000.0)          # a 1ª vez (partida): poda
            server._podar_historico_se_vencido(1000.0 + 60)     # 1 min depois: não
            server._podar_historico_se_vencido(1000.0 + 6 * 3600 - 1)
            self.assertEqual(len(chamadas), 1)
            server._podar_historico_se_vencido(1000.0 + 6 * 3600)
            self.assertEqual(len(chamadas), 2)
        self.assertIn("_podar_historico_se_vencido", server._laco_vigia_tuneis.__code__.co_names)

    def test_auditoria_continua_sem_texto(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        salvo = (list(server.auditoria.handlers), server.auditoria.propagate, server.auditoria.level)

        def restaurar() -> None:
            for h in list(server.auditoria.handlers):
                server.auditoria.removeHandler(h)
                h.close()
            for h in salvo[0]:
                server.auditoria.addHandler(h)
            server.auditoria.propagate = salvo[1]
            server.auditoria.setLevel(salvo[2])

        self.addCleanup(restaurar)
        destino = Path(tmp.name) / "auditoria.log"
        self.assertIsNone(server._configurar_auditoria(destino))
        with self.assertLogs("proclamare", level="DEBUG") as comum:
            self.anunciar_rede(self.tok["funcionario"])
            self.salvar_prog(self.tok["gerente"], [self.anuncio_item()])
            self.rodar_agendados()
            self.get_rede(self.tok["gerente"], "/api/historico?q=secreto")
            logging.getLogger("proclamare").info("fim")
        for h in server.auditoria.handlers:
            h.flush()
        trilha = destino.read_text(encoding="utf-8")
        self.assertIn("acao=anuncio", trilha)
        self.assertIn("acao=historico_ler", trilha)
        for proibido in (TEXTO_SECRETO, "texto-secreto", "fia.func", self.func["nome"], self.func["id"]):
            self.assertNotIn(proibido, trilha)
            self.assertNotIn(proibido, "\n".join(comum.output))
        self.assertEqual(len([e for e in self.linhas() if e["texto"] == TEXTO_SECRETO]), 2)   # só aqui


# ---------------------------------------------------------------------------------------------
# Carimbos de autor
# ---------------------------------------------------------------------------------------------
class TesteCarimbos(BaseComContas):
    def test_cliente_nao_grava_autor_400(self) -> None:
        h = self.com_cookie(self.tok["gerente"])
        for chave, valor in (("autor_id", self.adm["id"]), ("autor_nome", "Outro")):
            with self.subTest(chave=chave):
                r = self.salvar_prog(self.tok["gerente"], [{**self.anuncio_item(), chave: valor}])
                self.assertEqual(r.status_code, 400, r.get_data(as_text=True))
                r = self.remoto("post", "/api/listas", json={"nome": "L", "itens": [], chave: valor}, headers=h)
                self.assertEqual(r.status_code, 400)
                r = self.remoto("post", "/api/spotify/atalhos", json={"nome": "A", "link": LINK, chave: valor}, headers=h)
                self.assertEqual(r.status_code, 400)
                r = self.remoto("post", "/api/mensagens", headers=h, json={
                    "versao": 0, "mensagens": [{"titulo": "T", "texto": "X", chave: valor}]})
                self.assertEqual(r.status_code, 400)
        self.assertEqual(server.listas_ler()["listas"], [])
        # o servidor grava o autor (a conta logada) ao criar e ao alterar
        r = self.remoto("post", "/api/listas", json={"nome": "Manhã", "itens": []}, headers=h)
        self.assertEqual(r.status_code, 201, r.get_data(as_text=True))
        lista = r.get_json()["lista"]
        self.assertEqual(lista["autor_nome"], self.ger["nome"])
        self.assertEqual(server.lista_obter(lista["id"])["autor_id"], self.ger["id"])
        r = self.remoto("post", f"/api/listas/{lista['id']}", json={"versao": lista["versao"], "nome": "Tarde"},
                        headers=self.com_cookie(self.tok["adm"]))
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        self.assertEqual((server.lista_obter(lista["id"])["autor_id"], r.get_json()["lista"]["autor_nome"]),
                         (self.adm["id"], self.adm["nome"]))
        r = self.remoto("post", "/api/spotify/atalhos", json={"nome": "Pop", "link": LINK}, headers=h)
        self.assertEqual(r.status_code, 201, r.get_data(as_text=True))
        self.assertEqual(r.get_json()["atalho"]["autor_nome"], self.ger["nome"])
        self.assertEqual(server.atalho_spotify_obter(r.get_json()["atalho"]["id"])["autor_id"], self.ger["id"])
        r = self.remoto("post", "/api/mensagens", headers=h,
                        json={"versao": 0, "mensagens": [{"titulo": "Farol", "texto": "Faróis acesos"}]})
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        m = r.get_json()["mensagens"][0]
        self.assertEqual(m["autor_nome"], self.ger["nome"])
        self.assertEqual(server.load_config()["mensagens"][0]["autor_id"], self.ger["id"])
        # salvar a mesma mensagem por outra conta não muda o autor (o conteúdo não mudou)
        r = self.remoto("post", "/api/mensagens", headers=self.com_cookie(self.tok["adm"]),
                        json={"versao": 1, "mensagens": [{"id": m["id"], "titulo": "Farol", "texto": "Faróis acesos"}]})
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        self.assertEqual(server.load_config()["mensagens"][0]["autor_id"], self.ger["id"])

    def test_constantes_do_autor_espelham_as_das_contas(self) -> None:
        """programacao.py não importa contas/server: as cópias das regras ficam conferidas aqui."""
        self.assertEqual(programacao.AUTOR_NOME_MAX, contas.NOME_MAX)
        for amostra in ("0123abcd", "ffffffff", "0123ABCD", "0123abc", "0123abcde", "0123abcg", "", " 0123abc"):
            with self.subTest(amostra=amostra):
                self.assertEqual(bool(programacao.RE_AUTOR_ID.fullmatch(amostra)),
                                 bool(server._CONTA_ID_RE.fullmatch(amostra)))
        self.assertEqual(programacao.RE_AUTOR_ID.pattern.strip("^$"), server._CONTA_ID_RE.pattern.strip("^$"))

    def test_so_ligar_desligar_pelo_pc_nao_muda_o_autor(self) -> None:
        r = self.salvar_prog(self.tok["gerente"], [self.anuncio_item()])
        item = r.get_json()["itens"][0]
        itens = [{k: item[k] for k in ("id", "tipo", "alvo", "hora", "dias", "ativo")}]
        itens[0]["ativo"] = False
        r = self.salvar_prog(None, itens)   # o PC das caixas, com a sessão local do ADM
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        salvo = server._prog_arquivo.ler()["itens"][0]
        self.assertEqual((salvo["ativo"], salvo["autor_id"], salvo["autor_nome"]),
                         (False, self.ger["id"], self.ger["nome"]))

    def test_carimbo_torto_no_disco_e_descartado(self) -> None:
        with server._prog_arquivo.lock:
            server._prog_arquivo.gravar({"versao": 1, "itens": [
                {**tp.item("abcd0002"), "autor_id": "NAO-HEX", "autor_nome": "x" * 500}]})
        server._prog_arquivo.resetar()   # relê do disco (a gravação deixa no cache o documento como foi dado)
        it = server._prog_arquivo.ler()["itens"][0]
        self.assertNotIn("autor_id", it)
        self.assertNotIn("autor_nome", it)
        doc = server._sanear_listas({"listas": [{"id": "abcd0003", "nome": "L", "musicas": [],
                                                 "autor_id": self.ger["id"], "autor_nome": "Gil\u202e"}]})
        self.assertEqual(doc["listas"][0]["autor_id"], self.ger["id"])
        self.assertNotIn("autor_nome", doc["listas"][0])   # caractere de formato: descartado


class TesteSemRotaDeApagar(BaseComContas):
    def test_sem_rota_de_apagar(self) -> None:
        self.anunciar_rede(self.tok["funcionario"], "Fica")
        for metodo in ("post", "delete", "put", "patch"):
            with self.subTest(metodo=metodo):
                for token in (self.tok["adm"], self.tok["gerente"]):
                    r = self.remoto(metodo, "/api/historico", json={}, headers=self.com_cookie(token))
                    self.assertEqual(r.status_code, 405, r.get_data(as_text=True))
                r = self.l.open("/api/historico", method=metodo.upper(), json={},
                                headers={"Cookie": f"{COOKIE}={self.token_adm}"})
                self.assertEqual(r.status_code, 405)
        regras = [(r.rule, sorted(r.methods - {"HEAD", "OPTIONS"})) for r in server.app.url_map.iter_rules()
                  if "historico" in r.rule]
        self.assertEqual(regras, [("/api/historico", ["GET"])])
        self.assertEqual(server.PERMISSOES_ROTAS[("GET", "/api/historico")], None)
        self.assertEqual([e["texto"] for e in self.linhas()], ["Fica"])


if __name__ == "__main__":
    unittest.main()
