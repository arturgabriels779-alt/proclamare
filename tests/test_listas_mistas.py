"""
Testes do contrato v2.7 (listas MISTAS): itens do YouTube junto das músicas da pasta, "Salvar fila como lista"
(POST /api/listas/da-fila), tocar_lista() com YouTube e a lista agendada com YouTube.

Rodam SEM som, SEM rede e SEM hardware: o oEmbed do YouTube (títulos) é simulado, nenhum áudio toca (as rotas só
mexem na fila e enfileiram o comando "load" para a aba Tocador, que aqui não existe). Tudo acontece em pastas
temporárias: o config.json e a pasta Músicas reais NUNCA são tocados.

Como rodar (na raiz do projeto, com o venv do projeto):
    python -m unittest tests.test_listas_mistas -v
"""
from __future__ import annotations

import json
import logging
import threading
import time
import types
import unittest
from datetime import datetime
from typing import Any
from unittest import mock

import server
from tests import test_listas as tl
from tests import test_programacao as tp

V1, V2, V3 = "dQw4w9WgXcQ", "aaaaaaaaaaa", "bbbbbbbbbbb"
PL = "PLrAXtmErZgOeiKm4sgNOknGvNjby9efdf"
URL_V1 = f"https://www.youtube.com/watch?v={V1}"
URL_PL = f"https://www.youtube.com/playlist?list={PL}"


class BaseMista(tl.Base):
    """A base das listas + oEmbed simulado + cache de títulos limpo."""

    def setUp(self) -> None:
        super().setUp()
        self.titulos: dict[str, str] = {}
        self.consultas: list[str] = []

        self.portao = threading.Event()      # fechado = a busca do título espera (resposta "lenta" do YouTube)
        self.portao.set()
        self.addCleanup(self.portao.set)

        def oembed(url: str) -> str | None:
            self.consultas.append(url)
            self.portao.wait(5)
            return self.titulos.get(url)

        p = mock.patch.object(server, "_oembed_buscar", oembed)
        p.start()
        self.addCleanup(p.stop)
        self._limpar_titulos()
        self.addCleanup(self._limpar_titulos)
        self.addCleanup(self._limpar_estado_do_tocador)
        self._zerar_gravador()
        self.addCleanup(self._zerar_gravador)

    @staticmethod
    def _zerar_gravador() -> None:
        server._cancelar_gravacao_de_titulos()
        server._titulos_gravar_ultimo = -1e9

    @staticmethod
    def _limpar_estado_do_tocador() -> None:
        with server._yt_estado_lock:
            server._yt_estado.update(video_id=None, list_id=None, playlist_ids=[], playlist_index=None,
                                     source="youtube", music_id=None, is_playing=False, yt_pronto=True,
                                     posicao_s=None, duracao_s=None, posicao_em=None)

    @staticmethod
    def _limpar_titulos() -> None:
        with server._yt_titulos_lock:
            server._yt_titulos.clear()
            server._yt_titulos_pendentes.clear()

    def esperar(self, condicao: Any, limite: float = 5.0) -> bool:
        fim = time.monotonic() + limite
        while time.monotonic() < fim:
            if condicao():
                return True
            time.sleep(0.02)
        return bool(condicao())

    def criar_itens(self, itens: list[dict], nome: str = "Mista", **kw: Any) -> Any:
        return self.c.post("/api/listas", json={"nome": nome, "itens": itens}, **kw)

    def lista_itens(self, itens: list[dict], nome: str = "Mista") -> dict:
        r = self.criar_itens(itens, nome)
        self.assertEqual(r.status_code, 201, r.get_data(as_text=True))
        return r.get_json()["lista"]

    def salvar_itens(self, lista: dict, itens: list[dict], **kw: Any) -> Any:
        return self.c.post(f"/api/listas/{lista['id']}", json={"versao": lista["versao"], "itens": itens, **kw})

    def titulo_no_disco(self, indice: int = 0) -> Any:
        try:
            return self.listas_no_disco()["listas"][indice]["musicas"][0]["titulo"]
        except (OSError, ValueError, IndexError):
            return None

    def tocador_pronto(self, pronto: bool = True) -> None:
        self.tocador_vivo()
        with server._yt_estado_lock:
            server._yt_estado["yt_pronto"] = pronto


# ---------------------------------------------------------------------------
class TesteCriarComItens(BaseMista):
    def test_mistura_na_ordem_e_formato_do_detalhe(self) -> None:
        a = self.criar_musica("a.mp3")
        lista = self.lista_itens([
            {"url": URL_V1},
            {"music_id": a},
            {"url": f"{URL_PL}&index=3"},
            {"youtube": {"video_id": V2, "list_id": PL, "index": 4, "start": 90}},
            {"url": f"https://youtu.be/{V3}?t=1m30s"},
        ])
        self.assertEqual(lista["total"], 5)
        self.assertEqual(lista["youtube"], 4)
        self.assertEqual(lista["disponiveis"], 5)
        itens = lista["musicas"]
        self.assertEqual([i["tipo"] for i in itens], ["youtube", "musica", "youtube", "youtube", "youtube"])
        self.assertEqual(itens[0], {"tipo": "youtube", "kind": "video", "video_id": V1, "list_id": None,
                                    "index": None, "start": None, "titulo": None, "disponivel": True,
                                    "url": URL_V1})
        self.assertEqual(itens[1]["id"], a)
        self.assertTrue(itens[1]["disponivel"])
        self.assertEqual(itens[2]["kind"], "playlist")
        self.assertEqual((itens[2]["video_id"], itens[2]["list_id"], itens[2]["index"]), (None, PL, 2))
        self.assertEqual(itens[2]["url"], f"{URL_PL}&index=3")
        self.assertEqual(itens[3]["url"], f"https://www.youtube.com/watch?v={V2}&list={PL}&index=5&t=90s")
        self.assertEqual((itens[4]["start"], itens[4]["kind"]), (90, "video"))

    def test_o_disco_guarda_so_campos_validados_nunca_a_url(self) -> None:
        lista = self.lista_itens([{"url": f"{URL_V1}&utm_source=x&feature=share"}])
        guardado = self.listas_no_disco()["listas"][0]["musicas"][0]
        self.assertEqual(guardado, {"tipo": "youtube", "video_id": V1, "list_id": None, "index": None,
                                    "start": None, "titulo": None})
        self.assertNotIn("utm_source", json.dumps(self.listas_no_disco()))
        self.assertEqual(lista["musicas"][0]["url"], URL_V1)

    def test_mix_automatico_e_ignorado_como_na_fila(self) -> None:
        lista = self.lista_itens([{"url": f"{URL_V1}&list=RDdQw4w9WgXcQ&start_radio=1"}])
        item = lista["musicas"][0]
        self.assertEqual((item["video_id"], item["list_id"], item["kind"]), (V1, None, "video"))
        r = self.criar_itens([{"url": "https://www.youtube.com/watch?list=RDabc123"}], "Só mix")
        self.assertEqual(r.status_code, 400)
        self.assertEqual((r.get_json()["campo"], r.get_json()["indice"]), ("itens", 0))

    def test_forma_youtube_recusa_mix_e_campos_tortos(self) -> None:
        ruins = [
            {"youtube": {"list_id": "RDabcdefgh"}},                      # Mix não vale
            {"youtube": {}},                                              # falta vídeo/playlist
            {"youtube": {"video_id": "curto"}},
            {"youtube": {"video_id": V1, "index": 2}},                    # index só com list_id
            {"youtube": {"list_id": PL, "index": 200}},
            {"youtube": {"list_id": PL, "index": True}},
            {"youtube": {"video_id": V1, "start": 86401}},
            {"youtube": {"video_id": V1, "start": "10"}},
            {"youtube": {"video_id": V1, "url": URL_V1}},                 # campo extra
            {"youtube": V1},
            {"url": URL_V1, "music_id": "0123456789abcdef"},              # duas formas no mesmo item
            {"url": 5},
            {"music_id": 5},
            {"music_id": "../../etc/passwd"},
            {"foo": "bar"},
            {},
            "texto",
        ]
        for ruim in ruins:
            r = self.criar_itens([{"url": URL_V1}, ruim], "Ruim")
            self.assertEqual(r.status_code, 400, (ruim, r.get_data(as_text=True)))
            d = r.get_json()
            self.assertEqual((d["campo"], d["indice"]), ("itens", 1), ruim)
            self.assertFalse(d["ok"])
        self.assertEqual(self.c.get("/api/listas").get_json()["listas"], [])

    def test_mensagens_de_erro_sao_em_portugues_simples(self) -> None:
        r = self.criar_itens([{"youtube": {"video_id": "curto"}}], "Msg")
        self.assertEqual(r.get_json()["error"], "Item 1: Esse item do YouTube não é válido.")
        for ruim in ({"youtube": {}}, {"youtube": {"list_id": "RDabcdefgh"}}, {"youtube": {"video_id": V1, "index": 2}},
                     {"youtube": {"video_id": V1, "start": -1}}):
            texto = self.criar_itens([ruim], "Msg").get_json()["error"]
            self.assertNotIn("_id", texto)
            self.assertNotIn("index", texto)
            self.assertNotIn("start", texto)

    def test_links_que_nao_sao_do_youtube(self) -> None:
        for url in ("http://www.youtube.com/watch?v=" + V1, "https://evil.example/watch?v=" + V1,
                    "https://www.youtube.com.evil.example/watch?v=" + V1, "javascript:alert(1)",
                    "https://www.youtube.com/watch?v=curto", "https://www.youtube.com/", "", "x" * 400):
            r = self.criar_itens([{"url": url}], "Link ruim")
            self.assertEqual(r.status_code, 400, url)
            self.assertEqual(r.get_json()["indice"], 0)
            self.assertNotIn("Traceback", r.get_data(as_text=True))

    def test_repetidos_viram_400_com_indice(self) -> None:
        a = self.criar_musica("a.mp3")
        r = self.criar_itens([{"url": URL_V1}, {"music_id": a}, {"youtube": {"video_id": V1}}], "Dup")
        self.assertEqual((r.status_code, r.get_json()["indice"]), (400, 2))
        r = self.criar_itens([{"music_id": a}, {"music_id": a}], "Dup2")
        self.assertEqual((r.status_code, r.get_json()["indice"]), (400, 1))
        # mesmo vídeo com outro início (ou outra playlist) NÃO é repetido
        lista = self.lista_itens([{"url": URL_V1}, {"url": URL_V1 + "&t=30"}, {"url": URL_V1 + f"&list={PL}"}], "Ok")
        self.assertEqual(lista["total"], 3)
        # t=0 e index=1 são "o começo": é o mesmo item de quem não escreveu nada
        r = self.criar_itens([{"url": URL_V1}, {"url": URL_V1 + "&t=0"}], "Dup3")
        self.assertEqual((r.status_code, r.get_json()["indice"]), (400, 1))

    def test_itens_e_musicas_juntos_sao_400(self) -> None:
        a = self.criar_musica("a.mp3")
        r = self.c.post("/api/listas", json={"nome": "X", "itens": [], "musicas": [a]})
        self.assertEqual((r.status_code, r.get_json()["campo"]), (400, "itens"))
        lista = self.nova_lista("Y")
        r = self.c.post(f"/api/listas/{lista['id']}", json={"versao": 1, "itens": [], "musicas": []})
        self.assertEqual((r.status_code, r.get_json()["campo"]), (400, "itens"))

    def test_musicas_continua_valendo(self) -> None:
        a, b = self.criar_musica("a.mp3"), self.criar_musica("b.mp3")
        lista = self.nova_lista("Antiga", [b, a])
        self.assertEqual([m["id"] for m in lista["musicas"]], [b, a])
        self.assertTrue(all(m["tipo"] == "musica" for m in lista["musicas"]))

    def test_musica_fora_da_pasta_e_400_e_pasta_nao_lida_409(self) -> None:
        r = self.criar_itens([{"url": URL_V1}, {"music_id": "0123456789abcdef"}], "Fantasma")
        self.assertEqual((r.status_code, r.get_json()["campo"], r.get_json()["indice"]), (400, "itens", 1))
        falso = types.SimpleNamespace(pronto=False, obter=lambda _i: None)
        with mock.patch.object(server, "ACERVO", falso):
            r = self.criar_itens([{"music_id": "0123456789abcdef"}], "Fantasma")
            self.assertEqual((r.status_code, r.get_json()["codigo"]), (409, "lendo_pasta"))
            # lista só com YouTube não depende da pasta
            self.assertEqual(self.criar_itens([{"url": URL_V1}], "So yt").status_code, 201)

    def test_limite_de_500_itens(self) -> None:
        itens = [{"youtube": {"video_id": f"v{i:010d}"}} for i in range(500)]
        r = self.criar_itens(itens, "Cheia")
        self.assertEqual(r.status_code, 201, r.get_data(as_text=True)[:200])
        self.assertEqual(r.get_json()["lista"]["total"], 500)
        itens.append({"youtube": {"video_id": "v-extra-0001"}})
        r = self.criar_itens(itens, "Cheia demais")
        self.assertEqual((r.status_code, r.get_json()["campo"]), (400, "itens"))

    def test_500_itens_do_youtube_com_playlist_cabem_no_corpo(self) -> None:
        """O pior caso realista da forma "youtube" (campos nulos omitidos) cabe nos 60 KB desta rota."""
        itens = [{"youtube": {"video_id": f"v{i:010d}", "list_id": PL, "index": i % 150, "start": 1234}}
                 for i in range(500)]
        corpo = json.dumps({"nome": "Cheia", "itens": itens}, separators=(",", ":")).encode("utf-8")
        self.assertLess(len(corpo), server.LISTAS_CORPO_MAX, len(corpo))
        r = self.c.post("/api/listas", data=corpo, content_type="application/json")
        self.assertEqual(r.status_code, 201, r.get_data(as_text=True)[:200])

    def test_corpo_acima_de_60kb_e_413_e_o_teto_cabe_no_waitress(self) -> None:
        self.assertLessEqual(server.LISTAS_CORPO_MAX, server.TETO_CORPO_JSON)
        itens = [{"url": URL_V1 + "&x=" + "a" * 250}] * 300
        r = self.criar_itens(itens, "Gigante")
        self.assertEqual(r.status_code, 413)

    def test_resposta_traz_o_formato_novo(self) -> None:
        lista = self.lista_itens([{"url": URL_V1}])
        self.assertEqual(set(lista), {"id", "nome", "versao", "atualizada_em", "total", "youtube", "disponiveis", "musicas"})


# ---------------------------------------------------------------------------
class TesteSalvarEResumo(BaseMista):
    def test_salvar_troca_a_lista_inteira_e_preserva_titulos(self) -> None:
        a = self.criar_musica("a.mp3")
        self.titulos[URL_V1] = "Música do vídeo"
        lista = self.lista_itens([{"url": URL_V1}, {"music_id": a}])
        self.assertTrue(self.esperar(lambda: server._titulo_em_cache(URL_V1)[1] == "Música do vídeo"))
        det = self.detalhe(lista["id"])
        self.assertEqual(det["musicas"][0]["titulo"], "Música do vídeo")
        # o editor reenvia o item que já estava (sem reparsear) e acrescenta um link: o título antigo fica
        novo = self.salvar_itens(det, [{"youtube": {"video_id": V1}}, {"music_id": a}, {"url": URL_PL}])
        self.assertEqual(novo.status_code, 200, novo.get_data(as_text=True))
        itens = novo.get_json()["lista"]["musicas"]
        self.assertEqual([i["tipo"] for i in itens], ["youtube", "musica", "youtube"])
        self.assertEqual(itens[0]["titulo"], "Música do vídeo")
        self.assertEqual(novo.get_json()["lista"]["versao"], det["versao"] + 1)
        self.assertEqual(self.listas_no_disco()["listas"][0]["musicas"][0]["titulo"], "Música do vídeo")

    def test_versao_antiga_409(self) -> None:
        lista = self.lista_itens([{"url": URL_V1}])
        self.assertEqual(self.salvar_itens(lista, [{"url": URL_PL}]).status_code, 200)
        r = self.salvar_itens(lista, [])
        self.assertEqual((r.status_code, r.get_json()["codigo"]), (409, "versao_mudou"))

    def test_so_o_nome_nao_mexe_nos_itens(self) -> None:
        lista = self.lista_itens([{"url": URL_V1}])
        r = self.c.post(f"/api/listas/{lista['id']}", json={"versao": 1, "nome": "Outro"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json()["lista"]["musicas"][0]["video_id"], V1)

    def test_musica_indisponivel_continua_ao_resalvar_com_itens(self) -> None:
        a, b = self.criar_musica("a.mp3"), self.criar_musica("b.mp3")
        lista = self.lista_itens([{"music_id": a}, {"url": URL_V1}, {"music_id": b}])
        self.sumir("b.mp3")
        det = self.detalhe(lista["id"])
        self.assertEqual(det["disponiveis"], 2)
        r = self.salvar_itens(det, [{"music_id": b}, {"music_id": a}, {"youtube": {"video_id": V1}}])
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        self.assertEqual([i["tipo"] for i in r.get_json()["lista"]["musicas"]], ["musica", "musica", "youtube"])

    def test_resumo_conta_youtube_e_disponiveis(self) -> None:
        a = self.criar_musica("a.mp3")
        self.lista_itens([{"music_id": a}, {"url": URL_V1}, {"url": URL_PL}], "Tres")
        self.sumir("a.mp3")
        resumo = self.c.get("/api/listas").get_json()["listas"][0]
        self.assertEqual((resumo["total"], resumo["youtube"], resumo["disponiveis"]), (3, 2, 2))
        self.assertNotIn("musicas", resumo)

    def test_disponiveis_com_pasta_nao_lida(self) -> None:
        a = self.criar_musica("a.mp3")
        self.lista_itens([{"url": URL_V1}], "So yt")
        self.lista_itens([{"url": URL_V1}, {"music_id": a}], "Mista")
        falso = types.SimpleNamespace(pronto=False, obter=lambda _i: None)
        with mock.patch.object(server, "ACERVO", falso):
            por_nome = {x["nome"]: x for x in self.c.get("/api/listas").get_json()["listas"]}
        self.assertEqual(por_nome["So yt"]["disponiveis"], 1)      # não depende da pasta
        self.assertIsNone(por_nome["Mista"]["disponiveis"])


# ---------------------------------------------------------------------------
class TesteTitulos(BaseMista):
    def test_titulo_nulo_ate_resolver_e_depois_persiste_sem_mudar_a_versao(self) -> None:
        self.titulos[URL_V1] = "  Ave\x00 Maria  "
        self.portao.clear()
        lista = self.lista_itens([{"url": URL_V1}])
        self.assertIsNone(lista["musicas"][0]["titulo"])
        self.assertTrue(self.esperar(lambda: URL_V1 in self.consultas))
        self.portao.set()
        self.assertTrue(self.esperar(lambda: server._titulo_em_cache(URL_V1)[0]))
        det = self.detalhe(lista["id"])
        self.assertEqual(det["musicas"][0]["titulo"], "Ave Maria")
        self.assertEqual(det["versao"], lista["versao"])        # preencher o título não é uma alteração do dono
        # I6: quem resolveu o título agendou a gravação; ela acontece sozinha, sem depender de outro GET
        self.assertTrue(self.esperar(lambda: self.titulo_no_disco() == "Ave Maria"))
        self.assertEqual(self.c.get(f"/api/listas/{lista['id']}").get_json()["lista"]["versao"], lista["versao"])
        # reinício: o título vem do disco, sem internet
        server._listas_arquivo.resetar()
        self._limpar_titulos()
        self.assertEqual(self.detalhe(lista["id"])["musicas"][0]["titulo"], "Ave Maria")

    def test_titulo_guardado_tem_no_maximo_60(self) -> None:
        self.titulos[URL_V1] = "T" * 150
        lista = self.lista_itens([{"url": URL_V1}])
        self.assertTrue(self.esperar(lambda: server._titulo_em_cache(URL_V1)[0]))
        self.assertEqual(len(self.detalhe(lista["id"])["musicas"][0]["titulo"]), 60)

    def test_sem_titulo_continua_none(self) -> None:
        lista = self.lista_itens([{"url": URL_V1}])
        self.assertTrue(self.esperar(lambda: server._titulo_em_cache(URL_V1)[0]))   # falha em cache
        self.assertIsNone(self.detalhe(lista["id"])["musicas"][0]["titulo"])

    def test_nenhum_host_novo(self) -> None:
        """Os títulos saem do mesmo _oembed_buscar da fila (youtube.com/oembed); a lista não busca mais nada."""
        import inspect
        fonte = inspect.getsource(server._titulo_youtube) + inspect.getsource(server._gravar_titulos_youtube)
        self.assertNotIn("requests", fonte)


# ---------------------------------------------------------------------------
class TesteGravadorDeTitulos(BaseMista):
    """B2/I6: a gravação dos títulos é UMA só, com debounce, para todas as listas."""

    def gravacoes(self) -> mock.MagicMock:
        return mock.patch.object(server._listas_arquivo, "gravar", wraps=server._listas_arquivo.gravar)

    def test_uma_gravacao_para_varias_listas_e_so_se_algo_mudou(self) -> None:
        self.lista_itens([{"url": URL_V1}], "Uma")
        self.lista_itens([{"url": URL_PL}], "Duas")
        self.assertFalse(server._gravar_titulos_youtube(), "nada no cache: nada a gravar")
        server._semear_titulo(URL_V1, "Primeiro")
        server._semear_titulo(URL_PL, "Segundo")
        with self.gravacoes() as g:
            self.assertTrue(server._gravar_titulos_youtube())
            self.assertFalse(server._gravar_titulos_youtube())
        self.assertEqual(g.call_count, 1)
        doc = self.listas_no_disco()
        self.assertEqual([x["musicas"][0]["titulo"] for x in doc["listas"]], ["Primeiro", "Segundo"])
        self.assertEqual([x["versao"] for x in doc["listas"]], [1, 1])      # não é alteração do dono

    def test_debounce_de_30_s(self) -> None:
        self.lista_itens([{"url": URL_V1}], "Uma")
        server._semear_titulo(URL_V1, "Primeiro")
        self.assertTrue(server._gravar_titulos_youtube())
        self.lista_itens([{"url": URL_PL}], "Duas")
        server._semear_titulo(URL_PL, "Segundo")
        with self.gravacoes() as g:
            server._agendar_gravacao_de_titulos()
            server._agendar_gravacao_de_titulos()            # já tem um pendente: não cria outro
            time.sleep(0.4)
            self.assertEqual(g.call_count, 0, "ainda dentro dos 30 s da gravação anterior")
            timer = server._titulos_gravar_timer
            self.assertIsNotNone(timer)
            self.assertGreater(timer.interval, 20)
            self.assertLessEqual(timer.interval, server.TITULOS_GRAVAR_INTERVALO_S)
            server._cancelar_gravacao_de_titulos()
            self.assertIsNone(server._titulos_gravar_timer)

    def test_get_que_pede_titulos_agenda_e_a_gravacao_acontece_uma_vez(self) -> None:
        self.titulos[URL_V1], self.titulos[URL_PL] = "T1", "T2"
        lista = self.lista_itens([{"url": URL_V1}, {"url": URL_PL}])
        with self.gravacoes() as g:
            for _ in range(5):
                self.detalhe(lista["id"])
            self.assertTrue(self.esperar(lambda: self.titulo_no_disco() == "T1"))
            self.assertTrue(self.esperar(lambda: server._titulo_em_cache(URL_PL)[1] == "T2"))
            time.sleep(0.3)
        self.assertLessEqual(g.call_count, 2)               # nunca uma gravação por GET

    def test_leitura_nao_grava_nada(self) -> None:
        self.titulos[URL_V1] = "T"
        lista = self.lista_itens([{"url": URL_V1}])
        self.assertTrue(self.esperar(lambda: server._titulo_em_cache(URL_V1)[0]))
        self.assertTrue(self.esperar(lambda: self.titulo_no_disco() == "T"))
        with self.gravacoes() as g:
            self.detalhe(lista["id"])
            self.c.get("/api/listas")
            time.sleep(0.2)
        self.assertEqual(g.call_count, 0)

    def test_limite_leve_so_corta_o_trabalho_extra_nunca_a_leitura(self) -> None:
        lista = self.lista_itens([{"url": URL_V1}])
        with mock.patch.object(server, "_pedir_titulo") as pedir:
            for _ in range(server.LISTA_DETALHE_BUSCA_MAX_POR_MIN):
                self.assertEqual(self.c.get(f"/api/listas/{lista['id']}").status_code, 200)
            antes = pedir.call_count
            r = self.c.get(f"/api/listas/{lista['id']}")
            self.assertEqual(r.status_code, 200)
            self.assertEqual(r.get_json()["lista"]["musicas"][0]["video_id"], V1)
            self.assertEqual(pedir.call_count, antes, "passou do limite: não pede mais títulos")
        server._cancelar_gravacao_de_titulos()

    def test_semear_substitui_uma_falha_em_cache(self) -> None:
        with server._yt_titulos_lock:
            server._yt_titulos[URL_V1] = (None, time.monotonic() + 600)
        server._semear_titulo(URL_V1, "Guardado")
        self.assertEqual(server._titulo_em_cache(URL_V1), (True, "Guardado"))
        server._semear_titulo(URL_V1, "Outro")                 # já tem título: não troca
        self.assertEqual(server._titulo_em_cache(URL_V1)[1], "Guardado")

    def test_chave_global_do_oembed_nunca_e_descartada(self) -> None:
        self.assertIn("oembed", server._TAXA_GLOBAIS)
        server.excedeu_limite("oembed", 30, 60.0)
        for i in range(server._TAXA_MAX_CHAVES + 10):
            server.excedeu_limite(f"ip:{i}", 5, 60.0)
        self.assertIn("oembed", server._taxa_hist)


# ---------------------------------------------------------------------------
class TesteSaneamento(BaseMista):
    def test_item_torto_do_disco_e_descartado_sem_derrubar(self) -> None:
        a = self.criar_musica("a.mp3")
        bom = {"tipo": "youtube", "video_id": V1, "list_id": None, "index": None, "start": None, "titulo": "Bom"}
        doc = {"versao": 4, "listas": [{"id": "0a0a0a0a", "nome": "Torta", "versao": 2, "atualizada_em": 5, "musicas": [
            {"id": a, "titulo": "antigo sem tipo"},                      # entrada da v2.6
            bom,
            dict(bom),                                                    # repetido
            {"tipo": "youtube", "video_id": "curto"},
            {"tipo": "youtube", "list_id": "RDmixmix"},
            {"tipo": "youtube", "video_id": V2, "index": 3},              # index sem list_id
            {"tipo": "youtube", "video_id": V3, "start": -1},
            {"tipo": "youtube", "video_id": V3, "start": 99999},
            {"tipo": "youtube"},
            {"tipo": "outra-coisa", "id": a},
            {"tipo": "musica", "id": "../x"},
            "texto", 5, None,
            {"tipo": "youtube", "list_id": PL, "index": 7, "start": 0, "titulo": "L" * 200},
        ]}]}
        self.arquivo_listas().write_text(json.dumps(doc), encoding="utf-8")
        server._listas_arquivo.resetar()
        lista = self.detalhe("0a0a0a0a")
        self.assertEqual([i["tipo"] for i in lista["musicas"]], ["musica", "youtube", "youtube"])
        self.assertEqual(lista["musicas"][0]["titulo"], "a")
        self.assertEqual(lista["musicas"][1]["titulo"], "Bom")
        ultimo = lista["musicas"][2]
        self.assertEqual((ultimo["list_id"], ultimo["index"], ultimo["start"], len(ultimo["titulo"])), (PL, 7, None, 60))
        self.assertEqual(self.c.get("/api/listas").get_json()["listas"][0]["total"], 3)

    def test_documento_sem_as_chaves_novas(self) -> None:
        self.arquivo_listas().write_text(json.dumps({"versao": 1, "listas": []}), encoding="utf-8")
        server._listas_arquivo.resetar()
        self.assertEqual(server.listas_ler()["spotify"], [])
        self.assertEqual(server.listas_ler()["spotify_versao"], 0)
        self.assertEqual(self.nova_lista("Depois")["total"], 0)
        self.assertIn("spotify", self.listas_no_disco())


# ---------------------------------------------------------------------------
class TesteTocarMista(BaseMista):
    def test_fila_substituida_na_ordem_com_youtube_e_arquivo(self) -> None:
        a, b = self.criar_musica("a.mp3"), self.criar_musica("b.mp3")
        self.titulos[URL_PL] = "Playlist boa"
        lista = self.lista_itens([{"url": URL_V1}, {"music_id": a}, {"url": f"{URL_PL}&index=2"}, {"music_id": b}])
        self.c.post("/api/youtube/queue", json={"op": "add", "url": URL_V1})      # fila antiga que tem de sumir
        self.tocador_pronto()
        self.assertTrue(self.esperar(lambda: server._titulo_em_cache(URL_PL)[0]))
        self.detalhe(lista["id"])                                                  # guarda o título
        r = self.tocar(lista["id"])
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        d = r.get_json()
        self.assertEqual((d["total"], d["adicionados"], d["indisponiveis"], d["cortados"], d["youtube"],
                          d["pulados_youtube"]), (4, 4, 0, 0, 2, 0))
        self.assertEqual(d["primeiro"]["kind"], "video")
        self.assertEqual(d["primeiro"]["video_id"], V1)
        fila = self.fila()
        self.assertEqual([(i["kind"], i.get("music_id"), i["list_id"], i["index"]) for i in fila],
                         [("arquivo", a, None, None), ("playlist", None, PL, 1), ("arquivo", b, None, None)])
        loads = [x for x in self.comandos() if x["action"] == "load"]
        self.assertEqual(len(loads), 1)
        self.assertEqual(loads[0]["params"], {"video_id": V1, "list_id": None, "index": None, "start": None})
        self.assertEqual(d["id"], loads[0]["id"])

    def test_embaralhar_mistura_tudo_mas_mantem_os_itens(self) -> None:
        a = self.criar_musica("a.mp3")
        lista = self.lista_itens([{"music_id": a}, {"url": URL_V1}, {"url": URL_PL}])
        self.tocador_pronto()
        d = self.tocar(lista["id"], embaralhar=True).get_json()
        self.assertEqual(d["adicionados"], 3)
        itens = ([d["primeiro"]] + self.fila())
        chaves = {(i["kind"], i.get("music_id") or i["video_id"] or i["list_id"]) for i in itens}
        self.assertEqual(chaves, {("arquivo", a), ("video", V1), ("playlist", PL)})

    def test_lista_so_com_youtube_nao_depende_da_pasta(self) -> None:
        lista = self.lista_itens([{"url": URL_V1}, {"url": URL_PL}])
        self.tocador_pronto()
        falso = types.SimpleNamespace(pronto=False, obter=lambda _i: None)
        with mock.patch.object(server, "ACERVO", falso):
            r = self.tocar(lista["id"])
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        self.assertEqual((r.get_json()["adicionados"], len(self.fila())), (2, 1))

    def test_pasta_nao_lida_so_bloqueia_quem_tem_musica(self) -> None:
        a = self.criar_musica("a.mp3")
        lista = self.lista_itens([{"music_id": a}, {"url": URL_V1}])
        self.tocador_pronto()
        falso = types.SimpleNamespace(pronto=False, obter=lambda _i: None)
        with mock.patch.object(server, "ACERVO", falso):
            r = self.tocar(lista["id"])
        self.assertEqual((r.status_code, r.get_json()["codigo"]), (409, "lendo_pasta"))

    def test_musica_sumida_e_pulada_e_youtube_toca(self) -> None:
        a = self.criar_musica("a.mp3")
        lista = self.lista_itens([{"music_id": a}, {"url": URL_V1}])
        self.sumir("a.mp3")
        self.tocador_pronto()
        d = self.tocar(lista["id"]).get_json()
        self.assertEqual((d["total"], d["adicionados"], d["indisponiveis"]), (2, 1, 1))
        self.assertEqual(d["primeiro"]["video_id"], V1)

    def test_nada_tocavel_404_sem_musicas_e_a_fila_fica(self) -> None:
        a = self.criar_musica("a.mp3")
        lista = self.lista_itens([{"music_id": a}])
        self.sumir("a.mp3")
        self.c.post("/api/youtube/queue", json={"op": "add", "url": URL_V1})
        self.tocador_pronto()
        r = self.tocar(lista["id"])
        self.assertEqual((r.status_code, r.get_json()["codigo"]), (404, "sem_musicas"))
        self.assertEqual(len(self.fila()), 1)
        vazia = self.nova_lista("Vazia")
        self.assertEqual(self.tocar(vazia["id"]).get_json()["codigo"], "sem_musicas")

    def test_sem_tocador_409_e_a_fila_nao_e_mexida(self) -> None:
        lista = self.lista_itens([{"url": URL_V1}])
        self.c.post("/api/youtube/queue", json={"op": "add", "url": URL_PL})
        r = self.tocar(lista["id"])
        self.assertEqual((r.status_code, r.get_json()["codigo"]), (409, "sem_tocador"))
        self.assertEqual(len(self.fila()), 1)
        self.assertEqual(self.comandos(), [])

    def test_player_do_youtube_nao_carregado_pula_o_youtube(self) -> None:
        a = self.criar_musica("a.mp3")
        lista = self.lista_itens([{"url": URL_V1}, {"music_id": a}, {"url": URL_PL}])
        self.tocador_pronto(False)
        d = self.tocar(lista["id"]).get_json()
        self.assertEqual((d["adicionados"], d["youtube"], d["pulados_youtube"]), (1, 0, 2))
        self.assertEqual(d["primeiro"]["music_id"], a)
        # só YouTube: 409 e a fila fica como estava
        so_yt = self.lista_itens([{"url": URL_V1}], "So yt")
        self.c.post("/api/youtube/queue", json={"op": "add", "music_id": a})
        antes = len(self.fila())
        r = self.tocar(so_yt["id"])
        self.assertEqual((r.status_code, r.get_json()["codigo"]), (409, "youtube_indisponivel"))
        self.assertEqual(len(self.fila()), antes)

    def test_teto_de_200_da_fila(self) -> None:
        lista = self.lista_itens([{"youtube": {"video_id": f"v{i:010d}"}} for i in range(250)])
        self.tocador_pronto()
        d = self.tocar(lista["id"]).get_json()
        self.assertEqual((d["total"], d["adicionados"], d["cortados"]), (250, 200, 50))
        self.assertEqual(len(self.fila()), 199)

    def test_titulo_guardado_aparece_na_fila(self) -> None:
        self.titulos[URL_V1] = "Titulo do video"
        lista = self.lista_itens([{"url": URL_V1}, {"url": URL_PL}])
        self.assertTrue(self.esperar(lambda: server._titulo_em_cache(URL_V1)[1] == "Titulo do video"))
        self.detalhe(lista["id"])
        self.assertTrue(self.esperar(lambda: self.titulo_no_disco() == "Titulo do video"))
        self._limpar_titulos()                                                    # a fila não tem mais o cache
        self.tocador_pronto()
        d = self.tocar(lista["id"]).get_json()
        self.assertEqual(d["primeiro"]["title"], "Titulo do video")

    def test_rota_tocar_continua_com_os_mesmos_codigos(self) -> None:
        self.assertEqual(self.tocar("deadbeef").status_code, 404)
        self.assertEqual(self.tocar("nao-id").status_code, 404)


# ---------------------------------------------------------------------------
class TesteDaFila(BaseMista):
    def da_fila(self, **corpo: Any) -> Any:
        corpo.setdefault("nome", "Da fila")
        return self.c.post("/api/listas/da-fila", json=corpo)

    def estado_tocando(self, **campos: Any) -> None:
        self.tocador_vivo()
        with server._yt_estado_lock:
            server._yt_estado.update(source="youtube", music_id=None, video_id=None, list_id=None, is_playing=True)
            server._yt_estado.update(campos)

    def test_fila_mais_o_que_toca_agora_na_ordem(self) -> None:
        a, b = self.criar_musica("a.mp3"), self.criar_musica("b.mp3")
        self.estado_tocando(source="arquivo", music_id=a)
        self.c.post("/api/youtube/queue", json={"op": "add", "url": URL_V1})
        self.c.post("/api/youtube/queue", json={"op": "add", "music_id": b})
        self.c.post("/api/youtube/queue", json={"op": "add", "url": f"{URL_PL}&index=2&t=30"})
        r = self.da_fila(nome="  Manhã  ")
        self.assertEqual(r.status_code, 201, r.get_data(as_text=True))
        d = r.get_json()
        self.assertEqual((d["cortados"], d["lista"]["nome"], d["lista"]["total"], d["lista"]["youtube"]), (0, "Manhã", 4, 2))
        itens = d["lista"]["musicas"]
        self.assertEqual([(i["tipo"], i.get("id") or i["video_id"] or i["list_id"]) for i in itens],
                         [("musica", a), ("youtube", V1), ("musica", b), ("youtube", PL)])
        self.assertEqual((itens[3]["index"], itens[3]["start"]), (1, 30))
        self.assertEqual(d["versao"], 1)
        self.assertEqual(self.c.get(f"/api/listas/{d['lista']['id']}").get_json()["lista"]["total"], 4)
        # a fila em si não foi mexida
        self.assertEqual(len(self.fila()), 3)

    def test_o_que_toca_agora_no_youtube_video_e_playlist(self) -> None:
        self.estado_tocando(video_id=V2, list_id=None)
        d = self.da_fila(nome="Video").get_json()
        self.assertEqual([(i["kind"], i["video_id"], i["list_id"]) for i in d["lista"]["musicas"]], [("video", V2, None)])
        self.estado_tocando(video_id=V3, list_id=PL)
        d = self.da_fila(nome="Playlist").get_json()
        item = d["lista"]["musicas"][0]
        self.assertEqual((item["kind"], item["video_id"], item["list_id"], item["index"]), ("playlist", None, PL, None))

    def test_tocando_agora_so_com_item_atual_de_verdade(self) -> None:
        """M1: video_id velho de um vídeo que terminou, ou tocador ocioso, não vira item da lista."""
        a = self.criar_musica("a.mp3")
        self.c.post("/api/youtube/queue", json={"op": "add", "url": URL_PL})
        casos = [
            # (estado, entra?)
            (dict(source="youtube", video_id=V2, is_playing=True), True),
            (dict(source="youtube", video_id=V2, is_playing=False), True),               # pausado
            (dict(source="youtube", video_id=V2, is_playing=False, posicao_s=100.0, duracao_s=100.5,
                  posicao_em=time.monotonic()), False),                                     # terminou
            (dict(source="youtube", video_id=V2, is_playing=False, posicao_s=10.0, duracao_s=100.0,
                  posicao_em=time.monotonic()), True),                                      # pausado no meio
            (dict(source="youtube", video_id=V2, is_playing=False, posicao_s=100.0, duracao_s=100.0,
                  posicao_em=time.monotonic() - 60), True),                                 # posição velha: não prova nada
            (dict(source="youtube", video_id=V2, is_playing=True, yt_pronto=False), False),   # ocioso (sem player)
            (dict(source="youtube", video_id=None, list_id=None, is_playing=False), False),   # nada em cena
            (dict(source="arquivo", music_id=a, is_playing=False), True),
            (dict(source="arquivo", music_id=None, is_playing=False), False),
        ]
        for n, (estado, entra) in enumerate(casos):
            self.estado_tocando(posicao_s=None, duracao_s=None, posicao_em=None, yt_pronto=True)
            self.estado_tocando(**estado)
            with server._taxa_lock:
                server._taxa_hist.clear()
            d = self.da_fila(nome=f"Caso {n}").get_json()
            self.assertEqual(d["lista"]["total"], 2 if entra else 1, (n, estado))
        with server._yt_estado_lock:
            server._yt_estado.update(posicao_s=None, duracao_s=None, posicao_em=None)

    def test_incluir_atual_false(self) -> None:
        a = self.criar_musica("a.mp3")
        self.estado_tocando(source="arquivo", music_id=a)
        self.c.post("/api/youtube/queue", json={"op": "add", "url": URL_V1})
        d = self.da_fila(incluir_atual=False).get_json()
        self.assertEqual([i["tipo"] for i in d["lista"]["musicas"]], ["youtube"])

    def test_tocador_fechado_ignora_o_estado_velho(self) -> None:
        a = self.criar_musica("a.mp3")
        self.estado_tocando(source="arquivo", music_id=a)
        with server._yt_estado_lock:
            server._yt_estado["last_heartbeat"] = time.monotonic() - 3600
        self.c.post("/api/youtube/queue", json={"op": "add", "url": URL_V1})
        d = self.da_fila().get_json()
        self.assertEqual(d["lista"]["total"], 1)

    def test_fila_vazia_409(self) -> None:
        r = self.da_fila()
        self.assertEqual((r.status_code, r.get_json()["codigo"]), (409, "fila_vazia"))
        self.estado_tocando()                                # tocador vivo mas sem nada tocando
        self.assertEqual(self.da_fila().get_json()["codigo"], "fila_vazia")
        self.assertEqual(self.c.get("/api/listas").get_json()["listas"], [])

    def test_ignora_duplicados_e_musica_que_sumiu(self) -> None:
        a, b = self.criar_musica("a.mp3"), self.criar_musica("b.mp3")
        self.estado_tocando(source="arquivo", music_id=a)
        for corpo in ({"op": "add", "music_id": a}, {"op": "add", "music_id": b}, {"op": "add", "url": URL_V1},
                      {"op": "add", "url": URL_V1}):
            self.c.post("/api/youtube/queue", json=corpo)
        self.sumir("b.mp3")
        d = self.da_fila().get_json()
        self.assertEqual([i["tipo"] for i in d["lista"]["musicas"]], ["musica", "youtube"])
        self.assertEqual(d["ignorados"], 3)                  # a repetida, b que sumiu e o link repetido

    def test_corta_em_500_e_avisa(self) -> None:
        self.estado_tocando(video_id=V2)
        for i in range(4):
            self.c.post("/api/youtube/queue", json={"op": "add", "url": f"https://youtu.be/{'x' * 10}{i}"})
        with mock.patch.object(server, "LISTA_ITENS_MAX", 3):
            d = self.da_fila().get_json()
        self.assertEqual((d["lista"]["total"], d["cortados"]), (3, 2))

    def test_o_cliente_nao_manda_os_itens(self) -> None:
        for extra in ({"itens": [{"url": URL_V1}]}, {"musicas": []}, {"fila": []}, {"versao": 1}):
            r = self.da_fila(**extra)
            self.assertEqual((r.status_code, r.get_json()["campo"]), (400, "chave"), extra)
        self.assertEqual(self.da_fila(incluir_atual="sim").get_json()["campo"], "incluir_atual")
        self.assertEqual(self.da_fila(nome="").get_json()["campo"], "nome")
        self.assertEqual(self.da_fila(nome="a\nb").status_code, 400)

    def test_nome_repetido_e_limite_de_listas(self) -> None:
        self.c.post("/api/youtube/queue", json={"op": "add", "url": URL_V1})
        self.assertEqual(self.da_fila(nome="Igual").status_code, 201)
        r = self.da_fila(nome="IGUAL")
        self.assertEqual((r.status_code, r.get_json()["codigo"]), (409, "nome_repetido"))
        for i in range(49):
            self.nova_lista(f"L{i}")
            with server._taxa_lock:
                server._taxa_hist.clear()
        r = self.da_fila(nome="Mais uma")
        self.assertEqual((r.status_code, r.get_json()["codigo"]), (409, "limite_listas"))

    def test_limite_de_taxa_429(self) -> None:
        self.c.post("/api/youtube/queue", json={"op": "add", "url": URL_V1})
        codigos = [self.da_fila(nome=f"N{i}").status_code for i in range(server.LISTAS_ESCRITA_MAX_POR_MIN + 2)]
        self.assertEqual(codigos[-1], 429)

    def test_a_rota_fixa_nao_e_confundida_com_um_id_de_lista(self) -> None:
        r = self.c.post("/api/listas/da-fila", json={"versao": 1, "nome": "x"})
        self.assertEqual(r.status_code, 400)                                   # chave desconhecida, não 404/409 de lista
        self.assertEqual(self.c.get("/api/listas/da-fila").status_code, 404)

    def test_auditoria_so_com_a_quantidade(self) -> None:
        self.c.post("/api/youtube/queue", json={"op": "add", "url": URL_V1})
        with self.assertLogs(tl.AUDITORIA, level="INFO") as cap:
            self.da_fila(nome="Segredo da loja")
        texto = "\n".join(cap.output)
        self.assertIn("acao=lista_da_fila", texto)
        self.assertIn("total=1", texto)
        self.assertNotIn("Segredo", texto)
        self.assertNotIn(V1, texto)

    def test_acesso_remoto_exige_pin(self) -> None:
        self.c.post("/api/youtube/queue", json={"op": "add", "url": URL_V1})
        server.aplicar_pin(tl.PIN)
        r = self.remoto("post", "/api/listas/da-fila", json={"nome": "Remota"})
        self.assertEqual(r.status_code, 401)
        self.assertEqual(self.remoto("post", "/api/listas", json={"nome": "X", "itens": []}).status_code, 401)
        self.logar_remoto()
        r = self.remoto("post", "/api/listas/da-fila", json={"nome": "Remota"}, headers={"Origin": tl.HOST_REMOTO})
        self.assertEqual(r.status_code, 201, r.get_data(as_text=True))


# ---------------------------------------------------------------------------
class TesteSubstituicaoDaMusica(tp.BaseProg):
    """I5: uma playlist do Spotify / uma lista que toma o lugar da música cancela a retomada pendente."""

    def setUp(self) -> None:
        super().setUp()
        self.addCleanup(lambda: server._cancelar_gravacao_de_titulos())

    def test_sem_substituicao_a_aba_volta_a_tocar(self) -> None:
        self.tocador(True)
        with server._musica_pausada([]):
            pass
        self.assertEqual(self.acoes_yt(), ["pause", "play"])

    def test_atalho_do_spotify_durante_a_pausa_nao_deixa_a_aba_voltar(self) -> None:
        r = self.c.post("/api/spotify/atalhos", json={"nome": "Rock", "link": "spotify:playlist:37i9dQZF1DXcBWIGoYBM5M"})
        a = r.get_json()["atalho"]
        self.tocador(True)
        with mock.patch.object(server, "spotify_comando", lambda *a_, **k: (204, None)), \
                mock.patch.object(server, "spotify_active_device_id", lambda: "DEV"):
            with server._musica_pausada([]):
                server.tocar_atalho_spotify(a["id"])
        acoes = self.acoes_yt()
        self.assertNotIn("play", acoes, "a aba Tocador não pode voltar a tocar junto com o Spotify")
        self.assertIn("pause", acoes)
        self.assertFalse(server._yt_estado["is_playing"])

    def test_atalho_que_falha_nao_cancela_a_retomada(self) -> None:
        r = self.c.post("/api/spotify/atalhos", json={"nome": "Rock", "link": "spotify:playlist:37i9dQZF1DXcBWIGoYBM5M"})
        a = r.get_json()["atalho"]
        self.tocador(True)
        with mock.patch.object(server, "spotify_comando", lambda *a_, **k: (403, "PREMIUM_REQUIRED")), \
                mock.patch.object(server, "spotify_active_device_id", lambda: "DEV"):
            with server._musica_pausada([]):
                with self.assertRaises(server.ErroTocarSpotify):
                    server.tocar_atalho_spotify(a["id"])
        self.assertIn("play", self.acoes_yt())

    def test_lista_tocada_durante_a_pausa_nao_devolve_a_musica_antiga(self) -> None:
        ids = self.criar_musicas(2)
        lista = self.nova_lista("L", ids)
        self.tocador(True)
        self.spotify.tocando = True
        with server._musica_pausada([]):
            server.tocar_lista(lista["id"])
        self.assertNotIn("play", self.acoes_yt())
        self.assertNotIn(("PUT", "/me/player/play"), self.spotify.chamadas, "o Spotify antigo não volta por cima da lista")


# ---------------------------------------------------------------------------
class TesteAcessoENegociacao(BaseMista):
    def test_exigencias_de_sempre(self) -> None:
        corpo = json.dumps({"nome": "X", "itens": []})
        r = self.c.post("/api/listas", data=corpo, content_type="text/plain")
        self.assertEqual(r.status_code, 415)
        r = self.c.post("/api/listas", data=corpo, content_type="application/json",
                        headers={"Origin": "http://evil.example"})
        self.assertEqual(r.status_code, 403)

    def test_remoto_sem_pin_e_com_pin(self) -> None:
        server.aplicar_pin(tl.PIN)
        r = self.remoto("get", "/api/listas")
        self.assertEqual(r.status_code, 401)
        self.logar_remoto()
        r = self.remoto("post", "/api/listas", json={"nome": "Do celular", "itens": [{"url": URL_V1}]},
                        headers={"Origin": tl.HOST_REMOTO})
        self.assertEqual(r.status_code, 201, r.get_data(as_text=True))

    def test_nada_de_url_ou_titulo_no_log_nem_na_auditoria(self) -> None:
        capturado: list[str] = []

        class Captura(logging.Handler):
            def emit(self, rec: logging.LogRecord) -> None:
                capturado.append(rec.getMessage())

        h = Captura(level=logging.DEBUG)
        logging.getLogger().addHandler(h)
        nivel = logging.getLogger().level
        logging.getLogger().setLevel(logging.DEBUG)
        try:
            with self.assertLogs(tl.AUDITORIA, level="INFO") as cap:
                self.lista_itens([{"url": URL_V1}], "Nome secreto da loja")
        finally:
            logging.getLogger().removeHandler(h)
            logging.getLogger().setLevel(nivel)
        tudo = "\n".join(capturado + cap.output)
        for segredo in ("Nome secreto", V1, "youtube.com"):
            self.assertNotIn(segredo, tudo)


# ---------------------------------------------------------------------------
class TesteProgramacaoComYoutube(tp.BaseProg):
    """A lista agendada com itens do YouTube usa o mesmo tocar_lista()."""

    def setUp(self) -> None:
        super().setUp()
        p = mock.patch.object(server, "_oembed_buscar", lambda url: None)
        p.start()
        self.addCleanup(p.stop)

    def agendar(self, itens: list[dict]) -> tuple[dict, dict]:
        r = self.c.post("/api/listas", json={"nome": "Loja", "itens": itens})
        self.assertEqual(r.status_code, 201, r.get_data(as_text=True))
        lista = r.get_json()["lista"]
        it = self.gravar([{"tipo": "lista", "alvo": lista["id"], "hora": "08:00", "dias": [1, 2, 3, 4, 5]}])[0]
        return lista, it

    def disparar_8h(self) -> None:
        self.rel.t = datetime(2026, 10, 19, 7, 59, 0)
        self.servico.preparar()
        self.rel.t = datetime(2026, 10, 19, 8, 0, 2)
        self.servico.varrer()
        self.servico.drenar()

    def test_lista_so_com_youtube_toca_sem_esperar_a_pasta(self) -> None:
        lista, _ = self.agendar([{"url": URL_V1}, {"url": URL_PL}])
        self.tocador(True)
        falso = types.SimpleNamespace(pronto=False, obter=lambda _i: None)
        inicio = self.rel.m
        with mock.patch.object(server, "ACERVO", falso):
            self.disparar_8h()
        h = self.historico()[0]
        self.assertEqual((h["status"], h["tipo"], h["origem"]), ("ok", "lista", "agendado"), h)
        self.assertLess(self.rel.m - inicio, 5)                        # não esperou os 10 s da 1ª leitura da pasta
        loads = [c for c in self.comandos() if c["action"] == "load"]
        self.assertEqual(loads[0]["params"]["video_id"], V1)
        self.assertEqual(len(self.fila()), 1)

    def test_player_do_youtube_fora_vira_nao_tocou_com_motivo(self) -> None:
        lista, _ = self.agendar([{"url": URL_V1}])
        self.tocador(True)
        with server._yt_estado_lock:
            server._yt_estado["yt_pronto"] = False
        self.disparar_8h()
        h = self.historico()[0]
        self.assertEqual((h["status"], h["motivo"]), ("nao_tocou", "youtube_indisponivel"))
        with server._yt_estado_lock:
            server._yt_estado["yt_pronto"] = True

    def test_aviso_quando_o_youtube_foi_pulado_numa_lista_mista(self) -> None:
        a = self.criar_musica("a.mp3")
        lista, _ = self.agendar([{"music_id": a}, {"url": URL_V1}])
        self.tocador(True)
        with server._yt_estado_lock:
            server._yt_estado["yt_pronto"] = False
        self.disparar_8h()
        h = self.historico()[0]
        self.assertEqual(h["status"], "ok")
        self.assertIn("YouTube", h["aviso"])
        with server._yt_estado_lock:
            server._yt_estado["yt_pronto"] = True


if __name__ == "__main__":
    unittest.main()
