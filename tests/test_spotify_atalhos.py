"""
Testes do contrato v2.7 (Atalhos do Spotify): GET/POST /api/spotify/atalhos, /<id>, /<id>/apagar, /<id>/tocar,
GET /api/spotify/atual-contexto, tocar_atalho_spotify() e o tipo "spotify" da Programação.

A API do Spotify é SEMPRE simulada (o mesmo `SpotifyFalso` do test_spotify.py: `server._http` é trocado por um falso
que só aceita as URLs de api.spotify.com e accounts.spotify.com e NUNCA sai para a rede). Credenciais e tokens são
FALSOS, o config.json/listas.json/programacao.json são TEMPORÁRIOS e nenhum som toca.

Como rodar (na raiz do projeto, com o venv do projeto):
    python -m unittest tests.test_spotify_atalhos -v
"""
from __future__ import annotations

import json
import time
import unittest
from datetime import datetime
from typing import Any
from unittest import mock

import server
from tests import test_listas as tl
from tests import test_programacao as tp
from tests.test_spotify import (CENARIOS, BaseSpotify, HOST_REMOTO, IP_REMOTO, PIN)

PLAYLIST = "37i9dQZF1DXcBWIGoYBM5M"            # 22 caracteres base62 (id de exemplo; nada é buscado)
ALBUM = "4aawyAB9vmqN3uQ7FjRGTy"
OUTRA = "0123456789ABCDEFghijkl"
LINK_PLAYLIST = f"https://open.spotify.com/playlist/{PLAYLIST}"
URI_PLAYLIST = f"spotify:playlist:{PLAYLIST}"
URI_ALBUM = f"spotify:album:{ALBUM}"
AUDITORIA = "proclamare.som.auditoria"


class BaseAtalho(BaseSpotify):
    """BaseSpotify + listas.json temporário + estado do Tocador limpo."""

    def setUp(self) -> None:
        super().setUp()
        server._listas_arquivo.resetar()
        self.addCleanup(server._listas_arquivo.resetar)
        self._zerar_tocador()
        self.addCleanup(self._zerar_tocador)
        self._zerar_nomes()
        self.addCleanup(self._zerar_nomes)
        with server._yt_cond:
            server._yt_comandos.clear()

    @staticmethod
    def _zerar_nomes() -> None:
        with server._contexto_nomes_lock:
            server._contexto_nomes.clear()

    @staticmethod
    def _zerar_tocador() -> None:
        with server._yt_estado_lock:
            server._yt_estado.update(last_heartbeat=None, is_playing=False, source="youtube", music_id=None,
                                     video_id=None, list_id=None, yt_pronto=True)

    def tocador(self, tocando: bool) -> None:
        with server._yt_estado_lock:
            server._yt_estado.update(last_heartbeat=time.monotonic(), is_playing=tocando)

    @staticmethod
    def comandos() -> list[str]:
        with server._yt_cond:
            return [c["action"] for c in server._yt_comandos]

    def arquivo(self) -> dict:
        return json.loads((server.CONFIG_PATH.parent / "listas.json").read_text(encoding="utf-8"))

    def criar(self, nome: str = "Rock da loja", link: str = LINK_PLAYLIST, **kw: Any) -> Any:
        return self.post("/api/spotify/atalhos", {"nome": nome, "link": link}, **kw)

    def novo(self, nome: str = "Rock da loja", link: str = LINK_PLAYLIST) -> dict:
        r = self.criar(nome, link)
        self.assertEqual(r.status_code, 201, r.get_data(as_text=True))
        return r.get_json()["atalho"]

    def remoto(self, metodo: str, caminho: str, **kw: Any) -> Any:
        return getattr(self.r, metodo)(caminho, base_url=HOST_REMOTO, environ_base=IP_REMOTO, **kw)


# ---------------------------------------------------------------------------
class TesteLinks(BaseAtalho):
    def test_formas_aceitas(self) -> None:
        casos = {
            LINK_PLAYLIST: URI_PLAYLIST,
            f"  {LINK_PLAYLIST}?si=abcdef123456  ": URI_PLAYLIST,
            f"https://open.spotify.com/intl-pt/playlist/{PLAYLIST}?si=x&utm_source=copy-link": URI_PLAYLIST,
            f"https://open.spotify.com/intl-pt-br/album/{ALBUM}": URI_ALBUM,
            f"https://open.spotify.com/album/{ALBUM}/": URI_ALBUM,
            f"https://OPEN.SPOTIFY.COM/playlist/{PLAYLIST}#frag": URI_PLAYLIST,
            f"https://open.spotify.com:443/playlist/{PLAYLIST}": URI_PLAYLIST,
            URI_PLAYLIST: URI_PLAYLIST,
            URI_ALBUM: URI_ALBUM,
            # o TIPO não diferencia maiúsculas (vira minúsculas); o id continua como veio
            f"https://open.spotify.com/PLAYLIST/{PLAYLIST}": URI_PLAYLIST,
            f"https://open.spotify.com/Album/{ALBUM}?si=x": URI_ALBUM,
            f"https://open.spotify.com/INTL-PT/Playlist/{PLAYLIST}": URI_PLAYLIST,
            f"https://open.spotify.com/intl-por/playlist/{PLAYLIST}": URI_PLAYLIST,      # intl de 3 letras
            f"spotify:PLAYLIST:{PLAYLIST}": URI_PLAYLIST,
            f"spotify:Album:{ALBUM}": URI_ALBUM,
        }
        for i, (link, uri) in enumerate(casos.items()):
            a = self.novo(f"Atalho {i}", link)
            guardado = [x for x in self.arquivo()["spotify"] if x["id"] == a["id"]][0]
            self.assertEqual(guardado["uri"], uri, link)
            with server._taxa_lock:
                server._taxa_hist.clear()
        self.assertEqual(self.f.api, [], "criar atalho não pode chamar nenhuma API")

    def test_o_id_diferencia_maiusculas(self) -> None:
        a = self.novo("Caixa", f"https://open.spotify.com/playlist/{OUTRA}")
        guardado = [x for x in self.arquivo()["spotify"] if x["id"] == a["id"]][0]
        self.assertEqual(guardado["uri"], f"spotify:playlist:{OUTRA}")
        self.assertEqual(a["url"], f"https://open.spotify.com/playlist/{OUTRA}")

    def test_formas_recusadas(self) -> None:
        ruins = [
            f"https://open.spotify.com/track/{PLAYLIST}",
            f"https://open.spotify.com/artist/{PLAYLIST}",
            f"https://open.spotify.com/show/{PLAYLIST}",
            f"https://open.spotify.com/embed/playlist/{PLAYLIST}",
            f"https://open.spotify.com/playlist/{PLAYLIST[:21]}",
            f"https://open.spotify.com/playlist/{PLAYLIST}x",
            f"https://open.spotify.com/playlist/{PLAYLIST[:-1]}-",
            f"http://open.spotify.com/playlist/{PLAYLIST}",
            f"https://play.spotify.com/playlist/{PLAYLIST}",
            f"https://open.spotify.com.evil.example/playlist/{PLAYLIST}",
            f"https://evil.example/open.spotify.com/playlist/{PLAYLIST}",
            f"https://open.spotify.com@evil.example/playlist/{PLAYLIST}",
            f"https://user:pw@open.spotify.com/playlist/{PLAYLIST}",
            f"https://open.spotify.com:8443/playlist/{PLAYLIST}",
            f"https://spoti.fi/{PLAYLIST}",
            f"spotify:track:{PLAYLIST}",
            f"spotify:artist:{PLAYLIST}",
            f"spotify:user:fulano:playlist:{PLAYLIST}",
            f"spotify:playlist:{PLAYLIST}:extra",
            "spotify:playlist:../../me",
            "https://open.spotify.com/playlist/../../me/player",
            f"https://open.spotify.com/playlist/{PLAYLIST} https://x.example",
            "", "   ", "texto qualquer", "x" * 400, 5, None, [LINK_PLAYLIST], {"a": 1},
        ]
        for ruim in ruins:
            r = self.criar("Ruim", ruim)
            self.assertEqual(r.status_code, 400, ruim)
            d = r.get_json()
            self.assertEqual((d["ok"], d["campo"]), (False, "link"), ruim)
            self.assertEqual(d["error"], "Cole o link de uma playlist ou álbum do Spotify.")
        self.assertEqual(self.get("/api/spotify/atalhos").get_json()["atalhos"], [])
        self.assertEqual(self.f.api, [])


# ---------------------------------------------------------------------------
class TesteCrud(BaseAtalho):
    def test_lista_vazia_e_formato(self) -> None:
        d = self.get("/api/spotify/atalhos").get_json()
        self.assertEqual(d, {"ok": True, "versao": 0, "limites": {"atalhos": 50, "nome": 40}, "atalhos": []})

    def test_criar_e_ler(self) -> None:
        r = self.criar("  Rock da loja ")
        self.assertEqual(r.status_code, 201)
        d = r.get_json()
        a = d["atalho"]
        self.assertEqual(d["versao"], 1)
        self.assertRegex(a["id"], r"^[0-9a-f]{8}$")
        self.assertEqual(a["nome"], "Rock da loja")
        self.assertEqual((a["tipo"], a["versao"]), ("playlist", 1))
        self.assertEqual(a["url"], LINK_PLAYLIST)
        self.assertEqual(set(a), {"id", "nome", "tipo", "versao", "atualizada_em", "url"})
        self.assertNotIn("uri", a)
        lista = self.get("/api/spotify/atalhos").get_json()
        self.assertEqual((lista["versao"], lista["atalhos"]), (1, [a]))
        album = self.novo("Álbum", f"spotify:album:{ALBUM}")
        self.assertEqual((album["tipo"], album["url"]), ("album", f"https://open.spotify.com/album/{ALBUM}"))

    def test_nao_mexe_na_versao_das_listas_nem_no_config(self) -> None:
        antes = dict(self.cfg())
        self.novo()
        self.assertEqual(self.get("/api/listas").get_json()["versao"], 0)
        self.assertEqual(self.cfg(), antes)
        self.assertTrue((server.CONFIG_PATH.parent / "listas.json").exists())
        self.assertEqual(self.arquivo()["versao"], 0)
        self.assertEqual(self.arquivo()["spotify_versao"], 1)

    def test_validacao_do_corpo(self) -> None:
        casos = [({"nome": "A"}, "link"), ({"link": LINK_PLAYLIST}, "nome"), ({}, "nome"),
                 ({"nome": "A", "link": LINK_PLAYLIST, "x": 1}, "chave"),
                 ({"nome": "", "link": LINK_PLAYLIST}, "nome"), ({"nome": "x" * 41, "link": LINK_PLAYLIST}, "nome"),
                 ({"nome": "a\nb", "link": LINK_PLAYLIST}, "nome"), ({"nome": "a\u202eb", "link": LINK_PLAYLIST}, "nome"),
                 ({"nome": 5, "link": LINK_PLAYLIST}, "nome")]
        for corpo, campo in casos:
            r = self.post("/api/spotify/atalhos", corpo)
            self.assertEqual((r.status_code, r.get_json()["campo"]), (400, campo), corpo)

    def test_nome_repetido_ignora_caixa(self) -> None:
        self.novo("Rock")
        r = self.criar("ROCK", f"spotify:album:{ALBUM}")
        self.assertEqual((r.status_code, r.get_json()["codigo"]), (409, "nome_repetido"))

    def test_limite_de_50(self) -> None:
        for i in range(50):
            self.novo(f"A{i}")
            with server._taxa_lock:
                server._taxa_hist.clear()
        r = self.criar("A50")
        self.assertEqual((r.status_code, r.get_json()["codigo"]), (409, "limite_atalhos"))

    def test_limite_de_taxa(self) -> None:
        codigos = [self.criar(f"N{i}", LINK_PLAYLIST).status_code for i in range(server.LISTAS_ESCRITA_MAX_POR_MIN + 2)]
        self.assertEqual(codigos[-1], 429)

    def test_salvar_nome_e_link(self) -> None:
        a = self.novo("Antes")
        r = self.post(f"/api/spotify/atalhos/{a['id']}", {"versao": 1, "nome": " Depois ", "link": f"spotify:album:{ALBUM}"})
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        d = r.get_json()
        self.assertEqual((d["versao"], d["atalho"]["nome"], d["atalho"]["tipo"], d["atalho"]["versao"]),
                         (2, "Depois", "album", 2))
        r = self.post(f"/api/spotify/atalhos/{a['id']}", {"versao": 2, "nome": "Só nome"})
        self.assertEqual(r.get_json()["atalho"]["tipo"], "album")

    def test_salvar_exige_algo_versao_e_id(self) -> None:
        a = self.novo()
        url = f"/api/spotify/atalhos/{a['id']}"
        self.assertEqual(self.post(url, {"versao": 1}).get_json()["campo"], "chave")
        self.assertEqual(self.post(url, {"nome": "X"}).get_json()["campo"], "versao")
        self.assertEqual(self.post(url, {"versao": 1, "nome": "X", "extra": 1}).get_json()["campo"], "chave")
        self.assertEqual(self.post(url, {"versao": 1, "link": "https://x.example"}).get_json()["campo"], "link")
        self.assertEqual(self.post("/api/spotify/atalhos/nao-id", {"versao": 1, "nome": "X"}).status_code, 404)
        r = self.post("/api/spotify/atalhos/deadbeef", {"versao": 1, "nome": "X"})
        self.assertEqual((r.status_code, r.get_json()["codigo"]), (404, "atalho_inexistente"))

    def test_versao_mudou_409_por_atalho(self) -> None:
        a, b = self.novo("A"), self.novo("B", f"spotify:album:{ALBUM}")
        self.assertEqual(self.post(f"/api/spotify/atalhos/{a['id']}", {"versao": 1, "nome": "A2"}).status_code, 200)
        r = self.post(f"/api/spotify/atalhos/{a['id']}", {"versao": 1, "nome": "A3"})
        self.assertEqual((r.status_code, r.get_json()["codigo"]), (409, "versao_mudou"))
        # mexer em A não atrapalha quem edita B
        self.assertEqual(self.post(f"/api/spotify/atalhos/{b['id']}", {"versao": 1, "nome": "B2"}).status_code, 200)
        r = self.post(f"/api/spotify/atalhos/{a['id']}/apagar", {"versao": 1})
        self.assertEqual((r.status_code, r.get_json()["codigo"]), (409, "versao_mudou"))

    def test_salvar_nome_repetido_409(self) -> None:
        self.novo("A")
        b = self.novo("B", f"spotify:album:{ALBUM}")
        r = self.post(f"/api/spotify/atalhos/{b['id']}", {"versao": 1, "nome": "a"})
        self.assertEqual((r.status_code, r.get_json()["codigo"]), (409, "nome_repetido"))
        self.assertEqual(self.post(f"/api/spotify/atalhos/{b['id']}", {"versao": 1, "nome": "B"}).status_code, 200)

    def test_apagar(self) -> None:
        a = self.novo()
        r = self.post(f"/api/spotify/atalhos/{a['id']}/apagar", {"versao": 1})
        self.assertEqual((r.status_code, r.get_json()), (200, {"ok": True, "versao": 2}))
        self.assertEqual(self.get("/api/spotify/atalhos").get_json()["atalhos"], [])
        self.assertEqual(self.post(f"/api/spotify/atalhos/{a['id']}/apagar", {"versao": 1}).status_code, 404)
        self.assertEqual(self.post("/api/spotify/atalhos/xx/apagar", {"versao": 1}).status_code, 404)
        self.assertEqual(self.post(f"/api/spotify/atalhos/{a['id']}/apagar", {"versao": 1, "x": 1}).status_code, 400)

    def test_saneamento_do_disco(self) -> None:
        ok = {"id": "0a0a0a0a", "nome": "Bom", "uri": URI_PLAYLIST, "versao": 3, "atualizada_em": 7}
        doc = {"versao": 1, "listas": [], "spotify_versao": 9, "spotify": [
            ok, dict(ok), {**ok, "id": "0b0b0b0b"},                       # id repetido e nome repetido
            {**ok, "id": "0c0c0c0c", "nome": "Outro", "uri": "spotify:track:" + PLAYLIST},
            {**ok, "id": "0d0d0d0d", "nome": "Outro2", "uri": "spotify:playlist:../../x"},
            {**ok, "id": "xx", "nome": "Sem id"}, {**ok, "id": "0e0e0e0e", "nome": "a\nb"},
            "texto", None, 5,
            {"id": "0f0f0f0f", "nome": "Album", "uri": URI_ALBUM, "versao": -4},
        ]}
        (server.CONFIG_PATH.parent / "listas.json").write_text(json.dumps(doc), encoding="utf-8")
        server._listas_arquivo.resetar()
        d = self.get("/api/spotify/atalhos").get_json()
        self.assertEqual([a["nome"] for a in d["atalhos"]], ["Bom", "Album"])
        self.assertEqual((d["versao"], d["atalhos"][0]["versao"], d["atalhos"][1]["versao"]), (9, 3, 1))
        # uri gravada à mão com o tipo em maiúsculas é normalizada
        doc["spotify"] = [{**ok, "uri": f"spotify:PLAYLIST:{PLAYLIST}"}]
        (server.CONFIG_PATH.parent / "listas.json").write_text(json.dumps(doc), encoding="utf-8")
        server._listas_arquivo.resetar()
        self.assertEqual(self.get("/api/spotify/atalhos").get_json()["atalhos"][0]["url"], LINK_PLAYLIST)

    def test_convive_com_as_listas_no_mesmo_arquivo(self) -> None:
        lista = self.post("/api/listas", {"nome": "Minha lista"}).get_json()["lista"]
        a = self.novo()
        self.post("/api/listas", {"nome": "Outra"})
        self.assertEqual(self.post(f"/api/spotify/atalhos/{a['id']}", {"versao": 1, "nome": "Novo nome"}).status_code, 200)
        self.assertEqual(self.post(f"/api/listas/{lista['id']}", {"versao": 1, "nome": "Renomeada"}).status_code, 200)
        doc = self.arquivo()
        self.assertEqual(len(doc["listas"]), 2)
        self.assertEqual(len(doc["spotify"]), 1)
        self.assertEqual(doc["spotify"][0]["nome"], "Novo nome")
        self.assertNotIn(LINK_PLAYLIST, json.dumps(doc))

    def test_auditoria_so_com_contagens(self) -> None:
        with self.assertLogs(AUDITORIA, level="INFO") as cap:
            a = self.novo("Nome secreto da loja")
            self.post(f"/api/spotify/atalhos/{a['id']}", {"versao": 1, "nome": "Outro secreto"})
            self.post(f"/api/spotify/atalhos/{a['id']}/apagar", {"versao": 2})
        tudo = "\n".join(cap.output)
        for acao in ("spotify_atalho_criar", "spotify_atalho_salvar", "spotify_atalho_apagar"):
            self.assertIn(f"acao={acao}", tudo)
        for segredo in ("secreto", a["id"], PLAYLIST):
            self.assertNotIn(segredo, tudo)

    def test_exigencias_de_sempre(self) -> None:
        r = self.c.post("/api/spotify/atalhos", data="{}", content_type="text/plain")
        self.assertEqual(r.status_code, 415)
        r = self.c.post("/api/spotify/atalhos", json={"nome": "A", "link": LINK_PLAYLIST},
                        headers={"Origin": "http://evil.example"})
        self.assertEqual(r.status_code, 403)
        self.assertEqual(self.c.post("/api/spotify/atalhos", data="{", content_type="application/json").status_code, 400)

    def test_acesso_remoto_exige_pin(self) -> None:
        a = self.novo()
        server.aplicar_pin(PIN)
        for metodo, caminho in (("get", "/api/spotify/atalhos"), ("get", "/api/spotify/atual-contexto"),
                                ("post", "/api/spotify/atalhos"), ("post", f"/api/spotify/atalhos/{a['id']}"),
                                ("post", f"/api/spotify/atalhos/{a['id']}/apagar"),
                                ("post", f"/api/spotify/atalhos/{a['id']}/tocar")):
            kw = {"json": {}} if metodo == "post" else {}
            self.assertEqual(self.remoto(metodo, caminho, **kw).status_code, 401, caminho)
        self.assertEqual(self.remoto("post", "/api/auth/login", json={"pin": PIN}).status_code, 200)
        r = self.remoto("get", "/api/spotify/atalhos")
        self.assertEqual(r.status_code, 200)
        r = self.remoto("post", f"/api/spotify/atalhos/{a['id']}/tocar", json={}, headers={"Origin": HOST_REMOTO})
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        self.assertEqual(self.f.chamadas("PUT", "/me/player/play")[0][3], {"context_uri": URI_PLAYLIST})


# ---------------------------------------------------------------------------
class TesteTocar(BaseAtalho):
    def tocar(self, atalho: dict, **kw: Any) -> Any:
        return self.post(f"/api/spotify/atalhos/{atalho['id']}/tocar", {}, **kw)

    def test_toca_a_playlist_no_aparelho_salvo(self) -> None:
        a = self.novo()
        r = self.tocar(a)
        self.assertEqual((r.status_code, r.get_json()), (200, {"ok": True, "tocador_pausado": False}))
        chamadas = self.f.chamadas("PUT", "/me/player/play")
        self.assertEqual(len(chamadas), 1)
        _, _, params, corpo = chamadas[0]
        self.assertEqual(params, {"device_id": "DEV123"})
        self.assertEqual(corpo, {"context_uri": URI_PLAYLIST})
        self.assertEqual(self.comandos(), [])                      # a aba Tocador não tocava: nada a pausar

    def test_album_toca_com_a_uri_do_album(self) -> None:
        a = self.novo("Disco", URI_ALBUM)
        self.assertEqual(self.tocar(a).status_code, 200)
        self.assertEqual(self.f.chamadas("PUT", "/me/player/play")[0][3], {"context_uri": URI_ALBUM})

    def test_pausa_a_aba_tocador_que_estava_tocando(self) -> None:
        a = self.novo()
        self.tocador(True)
        d = self.tocar(a).get_json()
        self.assertEqual(d, {"ok": True, "tocador_pausado": True})
        self.assertEqual(self.comandos(), ["pause"])
        self.assertFalse(server._yt_estado["is_playing"])

    def test_aba_parada_ou_fechada_nao_recebe_pause(self) -> None:
        a = self.novo()
        self.tocador(False)
        self.tocar(a)
        with server._yt_estado_lock:
            server._yt_estado.update(last_heartbeat=time.monotonic() - 3600, is_playing=True)   # sinal velho = fechada
        self.tocar(a)
        self.assertEqual(self.comandos(), [])

    def test_erros_do_spotify_no_formato_v242_e_a_aba_continua_tocando(self) -> None:
        a = self.novo()
        esperado = {"premium": "premium", "sem_dispositivo": "sem_dispositivo", "limite": "limite",
                    "cinco_xx": "indisponivel", "timeout": "indisponivel", "conexao": "indisponivel"}
        for nome, codigo in esperado.items():
            with server._taxa_lock:
                server._taxa_hist.clear()
            self.zerar_modulo()
            self.f.rotas[("PUT", "/me/player/play")] = [CENARIOS[nome]]
            self.tocador(True)
            r = self.tocar(a)
            self.assertEqual(r.status_code, 200, nome)
            d = r.get_json()
            self.assertFalse(d["ok"], nome)
            self.assertEqual(d["codigo"], codigo, nome)
            self.assertIsInstance(d["error"], str)
            self.assertNotIn("Traceback", d["error"])
            if nome == "limite":
                self.assertIn("espera_s", d)
        self.assertEqual(self.comandos(), [], "se o Spotify não aceitou, a aba Tocador continua tocando")

    def test_reconectar_sem_token(self) -> None:
        a = self.novo()
        server.update_config(spotify_refresh_token=None, spotify_access_token=None)
        self.zerar_modulo()
        d = self.tocar(a).get_json()
        self.assertEqual((d["ok"], d["codigo"]), (False, "reconectar"))

    def test_sem_aparelho_escolhido(self) -> None:
        a = self.novo()
        server.update_config(spotify_device_id=None)
        r = self.tocar(a)
        self.assertEqual((r.status_code, r.get_json()["codigo"], r.get_json()["ok"]), (400, "sem_dispositivo", False))
        self.assertEqual(self.f.chamadas("PUT", "/me/player/play"), [])

    def test_atalho_inexistente(self) -> None:
        r = self.post("/api/spotify/atalhos/deadbeef/tocar", {})
        self.assertEqual((r.status_code, r.get_json()["codigo"]), (404, "atalho_inexistente"))
        self.assertEqual(self.post("/api/spotify/atalhos/nao-id/tocar", {}).status_code, 404)
        self.assertEqual(self.f.api, [])

    def test_corpo_so_vazio(self) -> None:
        a = self.novo()
        r = self.post(f"/api/spotify/atalhos/{a['id']}/tocar", {"uri": "spotify:playlist:" + OUTRA})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(self.f.chamadas("PUT", "/me/player/play"), [])

    def test_limite_de_6_em_10_s(self) -> None:
        a = self.novo()
        codigos = [self.tocar(a).status_code for _ in range(8)]
        self.assertEqual(codigos[:6], [200] * 6)
        self.assertEqual(codigos[6:], [429, 429])

    def test_auditoria_da_falha_leva_so_o_codigo(self) -> None:
        a = self.novo("Nome secreto")
        self.f.rotas[("PUT", "/me/player/play")] = [CENARIOS["premium"]]
        with self.assertLogs(AUDITORIA, level="INFO") as cap:
            self.tocar(a)
        tudo = "\n".join(cap.output)
        self.assertIn("acao=spotify_atalho_tocar", tudo)
        self.assertIn("resultado=premium", tudo)
        for segredo in ("secreto", a["id"], PLAYLIST, "DEV123"):
            self.assertNotIn(segredo, tudo)

    def test_auditoria_sem_ids(self) -> None:
        a = self.novo("Nome secreto")
        with self.assertLogs(AUDITORIA, level="INFO") as cap:
            self.tocar(a)
        tudo = "\n".join(cap.output)
        self.assertIn("acao=spotify_atalho_tocar", tudo)
        self.assertIn("resultado=ok", tudo)
        for segredo in ("secreto", a["id"], PLAYLIST, "DEV123"):
            self.assertNotIn(segredo, tudo)

    def test_funcao_interna_sem_requisicao(self) -> None:
        a = self.novo()
        self.assertEqual(server.tocar_atalho_spotify(a["id"]), {"tocador_pausado": False})
        with self.assertRaises(server.ErroTocarSpotify) as cm:
            server.tocar_atalho_spotify("deadbeef")
        self.assertEqual((cm.exception.codigo, cm.exception.http), ("atalho_inexistente", 404))


# ---------------------------------------------------------------------------
class TesteContextoAtual(BaseAtalho):
    def estado(self, contexto: Any, tocando: bool = True) -> dict:
        return {"is_playing": tocando, "progress_ms": 1, "context": contexto,
                "device": {**self.f.dispositivos[0], "volume_percent": 50}, "item": self.f.item}

    def pergunta(self) -> dict:
        self.zerar_modulo()
        return self.get("/api/spotify/atual-contexto").get_json()

    def test_playlist_tocando_com_nome(self) -> None:
        self.f.rotas[("GET", "/me/player")] = [(200, self.estado({"type": "playlist", "uri": URI_PLAYLIST}))]
        self.f.rotas[("GET", f"/playlists/{PLAYLIST}")] = [(200, {"name": "Rock dos anos 90"})]
        d = self.pergunta()
        self.assertEqual(d, {"ok": True, "uri_tipo": "playlist", "url": LINK_PLAYLIST, "nome": "Rock dos anos 90",
                             "tocando": True})
        consulta = self.f.chamadas("GET", f"/playlists/{PLAYLIST}")[0]
        self.assertEqual(consulta[2], {"fields": "name"})

    def test_album_tocando(self) -> None:
        self.f.rotas[("GET", "/me/player")] = [(200, self.estado({"type": "album", "uri": URI_ALBUM}, tocando=False))]
        self.f.rotas[("GET", f"/albums/{ALBUM}")] = [(200, {"name": "Um álbum", "tracks": {"items": []}})]
        d = self.pergunta()
        self.assertEqual((d["uri_tipo"], d["url"], d["nome"], d["tocando"]),
                         ("album", f"https://open.spotify.com/album/{ALBUM}", "Um álbum", False))

    def test_o_nome_e_limpo_e_cortado(self) -> None:
        self.f.rotas[("GET", "/me/player")] = [(200, self.estado({"uri": URI_PLAYLIST}))]
        self.f.rotas[("GET", f"/playlists/{PLAYLIST}")] = [(200, {"name": "Linha 1\nLinha 2\x00 " + "z" * 80})]
        nome = self.pergunta()["nome"]
        self.assertLessEqual(len(nome), 40)
        self.assertNotIn("\n", nome)
        self.assertNotIn("\x00", nome)

    def test_falha_no_nome_nao_estraga_nada_nem_o_api_ok(self) -> None:
        self.f.rotas[("GET", "/me/player")] = [(200, self.estado({"uri": URI_PLAYLIST}))]
        self.f.rotas[("GET", f"/playlists/{PLAYLIST}")] = [(404, {"error": {"status": 404, "message": "Not found"}})]
        d = self.pergunta()
        self.assertEqual((d["ok"], d["uri_tipo"], d["nome"]), (True, "playlist", None))
        st = self.get("/api/spotify/status").get_json()
        self.assertIsNot(st["api_ok"], False, "a falha do nome não pode deixar o painel âmbar")

    def test_falha_de_rede_no_nome_nao_loga_o_id_da_playlist(self) -> None:
        self.f.rotas[("GET", "/me/player")] = [(200, self.estado({"uri": URI_PLAYLIST}))]
        self.f.rotas[("GET", f"/playlists/{PLAYLIST}")] = [CENARIOS["conexao"]]
        d = self.pergunta()
        self.assertEqual((d["ok"], d["nome"]), (True, None))
        avisos = [linha for linha in self.logs if "Falha de rede" in linha]
        self.assertTrue(avisos, "a falha de rede deve ser registrada")
        self.assertNotIn(PLAYLIST, "\n".join(self.logs))

    def test_nome_em_cache_nao_repete_a_consulta(self) -> None:
        self.f.rotas[("GET", f"/playlists/{PLAYLIST}")] = [(200, {"name": "Primeira"}), (200, {"name": "Segunda"})]
        self.f.rotas[("GET", "/me/player")] = [(200, self.estado({"uri": URI_PLAYLIST})) for _ in range(3)]
        self.assertEqual(self.pergunta()["nome"], "Primeira")
        self.assertEqual(self.pergunta()["nome"], "Primeira")
        self.assertEqual(len(self.f.chamadas("GET", f"/playlists/{PLAYLIST}")), 1)

    def test_outros_contextos_e_nada_tocando(self) -> None:
        vazio = {"ok": True, "uri_tipo": None, "url": None, "nome": None, "tocando": False}
        for contexto in ({"type": "artist", "uri": f"spotify:artist:{PLAYLIST}"}, {"type": "show", "uri": f"spotify:show:{PLAYLIST}"},
                         {"uri": f"spotify:user:fulano:playlist:{PLAYLIST}"}, {"uri": "spotify:playlist:../../me"},
                         {"uri": 5}, "texto", None, {}):
            self.f.rotas[("GET", "/me/player")] = [(200, self.estado(contexto))]
            self.assertEqual(self.pergunta(), vazio, contexto)
        self.f.ativo = False                                                      # 204: nenhum player ativo
        self.assertEqual(self.pergunta(), vazio)
        self.assertEqual(self.f.chamadas("GET", f"/playlists/{PLAYLIST}"), [])

    def test_erro_do_spotify(self) -> None:
        self.f.rotas[("GET", "/me/player")] = [CENARIOS["premium"]]
        d = self.pergunta()
        self.assertEqual((d["ok"], d["codigo"], d["uri_tipo"]), (False, "premium", None))

    def test_so_fala_com_o_spotify_e_com_ids_validados(self) -> None:
        self.f.rotas[("GET", "/me/player")] = [(200, self.estado({"uri": URI_PLAYLIST}))]
        self.pergunta()
        for _, caminho, _, _ in self.f.api:
            self.assertRegex(caminho, r"^/(me/player|playlists/[A-Za-z0-9]{22})$")

    def test_limite_de_taxa(self) -> None:
        codigos = [self.get("/api/spotify/atual-contexto").status_code for _ in range(24)]
        self.assertEqual(codigos[-1], 429)


# ---------------------------------------------------------------------------
class TesteProgramacaoSpotify(tp.BaseProg):
    """O tipo "spotify" da Programação: esquema, alvo_ok e execução."""

    def setUp(self) -> None:
        super().setUp()
        server._listas_arquivo.resetar()
        self.addCleanup(server._listas_arquivo.resetar)
        self.tocou: list[dict] = []
        self.resposta: tuple[int, str | None] = (204, None)
        self.device: str | None = "DEV123"

        def comando(metodo: str, caminho: str, *, params: Any = None, json_body: Any = None, critico: bool = False):
            self.tocou.append({"metodo": metodo, "caminho": caminho, "params": params, "corpo": json_body})
            return self.resposta

        for alvo, valor in (("spotify_comando", comando), ("spotify_active_device_id", lambda: self.device)):
            p = mock.patch.object(server, alvo, valor)
            p.start()
            self.addCleanup(p.stop)

    def atalho(self, nome: str = "Rock da loja") -> dict:
        r = self.c.post("/api/spotify/atalhos", json={"nome": nome, "link": LINK_PLAYLIST})
        self.assertEqual(r.status_code, 201, r.get_data(as_text=True))
        return r.get_json()["atalho"]

    def agendar(self, atalho: dict, **kw: Any) -> dict:
        corpo = {"tipo": "spotify", "alvo": atalho["id"], "hora": "08:00", "dias": [1, 2, 3, 4, 5], **kw}
        return self.gravar([corpo])[0]

    def disparar_8h(self) -> None:
        self.rel.t = datetime(2026, 10, 19, 7, 59, 0)
        self.servico.preparar()
        self.rel.t = datetime(2026, 10, 19, 8, 0, 2)
        self.servico.varrer()
        self.servico.drenar()

    # -- esquema
    def test_aceita_e_mostra_o_nome_do_atalho(self) -> None:
        a = self.atalho()
        it = self.agendar(a)
        self.assertEqual((it["tipo"], it["alvo"], it["rotulo"], it["alvo_nome"], it["alvo_ok"], it["alvo_motivo"]),
                         ("spotify", a["id"], "Rock da loja", "Rock da loja", True, None))
        self.assertIsNone(it["voz"])

    def test_esquema_estrito(self) -> None:
        a = self.atalho()
        base = {"tipo": "spotify", "alvo": a["id"], "hora": "08:00", "dias": [1]}
        for extra, campo in (({"embaralhar": True}, "embaralhar"), ({"voz": "francisca"}, "voz"),
                             ({"alvo": "nao-e-id"}, "alvo"), ({"alvo": "0123456789abcdef"}, "alvo"),
                             ({"alvo": None}, "alvo"), ({"alvo": 5}, "alvo")):
            r = self.salvar_itens([{**base, **extra}])
            self.assertEqual(r.status_code, 400, extra)
            self.assertEqual(r.get_json()["campo"], campo, extra)

    def test_atalho_que_nao_existe_e_400_com_codigo(self) -> None:
        r = self.salvar_itens([{"tipo": "spotify", "alvo": "deadbeef", "hora": "08:00", "dias": [1]}])
        d = r.get_json()
        self.assertEqual((r.status_code, d["codigo"], d["campo"], d["indice"]), (400, "atalho_inexistente", "alvo", 0))

    def test_atalho_apagado_nao_impede_regravar_a_programacao(self) -> None:
        a = self.atalho()
        it = self.agendar(a)
        self.assertEqual(self.c.post(f"/api/spotify/atalhos/{a['id']}/apagar", json={"versao": 1}).status_code, 200)
        r = self.c.get("/api/programacao").get_json()["itens"][0]
        self.assertEqual((r["alvo_ok"], r["alvo_motivo"], r["alvo_nome"]), (False, "atalho_inexistente", None))
        self.assertEqual(r["rotulo"], "Spotify das 08:00")
        # regravar tudo (ex.: mudou outro horário) com o item inalterado continua valendo
        novos = [{k: v for k, v in it.items() if k in ("id", "tipo", "alvo", "hora", "dias", "ativo")},
                 self.corpo_anuncio()]
        self.assertEqual(self.salvar_itens(novos).status_code, 200)
        # mas apontar um item NOVO para um atalho que não existe, não
        r = self.salvar_itens([{"tipo": "spotify", "alvo": a["id"], "hora": "09:00", "dias": [1]}])
        self.assertEqual(r.get_json()["codigo"], "atalho_inexistente")

    def test_nao_conta_no_teto_diario_de_audio(self) -> None:
        a = self.atalho()
        itens = [{"tipo": "spotify", "alvo": a["id"], "hora": f"{h:02d}:00", "dias": [0, 1, 2, 3, 4, 5, 6]}
                 for h in range(24)]
        self.assertEqual(self.salvar_itens(itens).status_code, 200)
        self.assertEqual(server._prog_audio_por_dia(self.c.get("/api/programacao").get_json()["itens"]), [0.0] * 7)

    # -- execução
    def test_no_horario_toca_o_atalho_e_pausa_a_aba(self) -> None:
        a = self.atalho()
        self.agendar(a)
        self.tocador(True)
        self.disparar_8h()
        h = self.historico()[0]
        self.assertEqual((h["status"], h["tipo"], h["nome"], h["origem"], h["motivo"]),
                         ("ok", "spotify", "Rock da loja", "agendado", None), h)
        self.assertEqual(len(self.tocou), 1)
        self.assertEqual((self.tocou[0]["metodo"], self.tocou[0]["caminho"]), ("PUT", "/me/player/play"))
        self.assertEqual(self.tocou[0]["corpo"], {"context_uri": URI_PLAYLIST})
        self.assertEqual(self.tocou[0]["params"], {"device_id": "DEV123"})
        self.assertEqual(self.acoes_yt(), ["pause"])
        self.assertFalse(server._anuncio_lock.locked(), "não usa nem deixa presa a trava de áudio")

    def test_aba_parada_nao_recebe_pause(self) -> None:
        a = self.atalho()
        self.agendar(a)
        self.tocador(False)
        self.disparar_8h()
        self.assertEqual(self.historico()[0]["status"], "ok")
        self.assertEqual(self.acoes_yt(), [])

    def test_falhas_viram_nao_tocou_com_motivo_estavel(self) -> None:
        a = self.atalho()
        self.agendar(a)
        casos = [((403, "PREMIUM_REQUIRED"), "spotify_premium"), ((404, "NO_ACTIVE_DEVICE"), "spotify_sem_aparelho"),
                 ((401, "reconectar"), "spotify_desconectado"), ((429, None), "spotify_limite"),
                 ((503, None), "spotify_indisponivel"), ((0, "rede"), "spotify_indisponivel")]
        for n, (resposta, motivo) in enumerate(casos):
            self.resposta = resposta
            self.tocador(True)
            self.rel.t = datetime(2026, 10, 19 + n * 0, 7, 59, 0)
            self.servico.preparar()
            self.rel.t = datetime(2026, 10, 19, 8, 0, 2)
            with self.servico._lock:
                self.servico._feitos.clear()
            self.servico.varrer()
            self.servico.drenar()
            h = self.historico()[0]
            self.assertEqual((h["status"], h["motivo"]), ("nao_tocou", motivo), (resposta, h))
            self.assertTrue(h["texto"])
        self.assertEqual(self.acoes_yt(), [], "se o Spotify não tocou, a aba Tocador continua tocando")

    def test_sem_aparelho_e_sem_conexao(self) -> None:
        a = self.atalho()
        self.agendar(a)
        self.device = None
        with mock.patch.object(server, "spotify_get_token", return_value="tok"):
            self.disparar_8h()
        self.assertEqual(self.historico()[0]["motivo"], "spotify_sem_aparelho")
        self.assertEqual(self.tocou, [])
        with mock.patch.object(server, "spotify_get_token", return_value=None), \
                mock.patch.object(server, "_motivo_sem_token", return_value="sem_token"):
            self.rel.t = datetime(2026, 10, 19, 7, 59, 0)
            self.servico.preparar()
            self.rel.t = datetime(2026, 10, 19, 8, 0, 3)
            with self.servico._lock:
                self.servico._feitos.clear()
            self.servico.varrer()
            self.servico.drenar()
        self.assertEqual(self.historico()[0]["motivo"], "spotify_desconectado")

    def test_atalho_apagado_vira_nao_tocou(self) -> None:
        a = self.atalho()
        self.agendar(a)
        self.c.post(f"/api/spotify/atalhos/{a['id']}/apagar", json={"versao": 1})
        self.disparar_8h()
        h = self.historico()[0]
        self.assertEqual((h["status"], h["motivo"]), ("nao_tocou", "atalho_inexistente"))
        self.assertEqual(self.tocou, [])

    def test_tocar_agora(self) -> None:
        a = self.atalho()
        it = self.agendar(a)
        self.tocador(True)
        r = self.tocar_agora(it["id"])
        self.assertEqual(r.status_code, 202, r.get_data(as_text=True))
        self.assertEqual(r.get_json()["execucao"], {"id": it["id"], "tipo": "spotify", "nome": "Rock da loja"})
        h = self.historico()[0]
        self.assertEqual((h["status"], h["origem"], h["tipo"]), ("ok", "manual", "spotify"))
        self.assertEqual(self.acoes_yt(), ["pause"])

    def test_tocar_agora_de_atalho_apagado_404(self) -> None:
        a = self.atalho()
        it = self.agendar(a)
        self.c.post(f"/api/spotify/atalhos/{a['id']}/apagar", json={"versao": 1})
        r = self.tocar_agora(it["id"])
        self.assertEqual((r.status_code, r.get_json()["codigo"]), (404, "atalho_inexistente"))

    def test_auditoria_da_execucao(self) -> None:
        a = self.atalho("Nome secreto")
        self.agendar(a)
        with self.assertLogs(AUDITORIA, level="INFO") as cap:
            self.disparar_8h()
        tudo = "\n".join(cap.output)
        self.assertIn("tipo=spotify", tudo)
        self.assertIn("status=ok", tudo)
        for segredo in ("secreto", a["id"], PLAYLIST):
            self.assertNotIn(segredo, tudo)


if __name__ == "__main__":
    unittest.main()
