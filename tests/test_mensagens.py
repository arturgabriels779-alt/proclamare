"""
Testes do contrato v2.5 (Mensagens prontas editáveis): GET/POST /api/mensagens e
POST /api/mensagens/restaurar.
Usa config.json temporário (nunca o real), não toca som e não usa a rede.
"""
from __future__ import annotations

import json
import os
import re
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

RAIZ = Path(__file__).resolve().parent.parent
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))
os.environ.setdefault("PROCLAMARE_NO_BROWSER", "1")
os.environ.setdefault("PROCLAMARE_MUSICAS_DIR", os.path.join(tempfile.gettempdir(), "proclamare-testes-musicas"))

import server  # noqa: E402

PIN = "481926"
HOST_REMOTO = "http://192.168.0.10:8502"
IP_REMOTO = {"REMOTE_ADDR": "192.168.0.50"}
AUDITORIA = "proclamare.som.auditoria"
# Chaves de outras funções que a gravação das mensagens NUNCA pode perder.
OUTRAS_CHAVES = {
    "spotify_client_id": "a" * 32,
    "spotify_client_secret": "b" * 32,
    "som": {"saidas": {}, "saida_anuncio": None},
    "acesso_versao": 3,
}


def msg(titulo: str = "Aviso", texto: str = "Texto do aviso.", **extra) -> dict:
    return {"titulo": titulo, "texto": texto, **extra}


class Base(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._salvo = (server.CONFIG_PATH, server._config_cache, server._config_assinatura)
        server.CONFIG_PATH = Path(self._tmp.name) / "config.json"
        server._config_cache = {}
        server._config_assinatura = None
        with server._taxa_lock:
            server._taxa_hist.clear()
            server._taxa_janela.clear()
        server._login_resetar_global()
        self.c = server.app.test_client()
        self.r = server.app.test_client()

    def tearDown(self) -> None:
        server.CONFIG_PATH, server._config_cache, server._config_assinatura = self._salvo
        self._tmp.cleanup()

    def gravar_config(self, dados: dict) -> None:
        server.CONFIG_PATH.write_text(json.dumps(dados), encoding="utf-8")

    def config_no_disco(self) -> dict:
        return json.loads(server.CONFIG_PATH.read_text(encoding="utf-8"))

    def get(self) -> dict:
        r = self.c.get("/api/mensagens")
        self.assertEqual(r.status_code, 200)
        return r.get_json()

    def salvar(self, mensagens, versao=None, **kw):
        if versao is None:
            versao = self.get()["versao"]
        return self.c.post("/api/mensagens", json={"versao": versao, "mensagens": mensagens}, **kw)

    def restaurar(self, versao=None, **kw):
        if versao is None:
            versao = self.get()["versao"]
        return self.c.post("/api/mensagens/restaurar", json={"versao": versao}, **kw)

    def remoto(self, metodo, caminho, **kw):
        return getattr(self.r, metodo)(caminho, base_url=HOST_REMOTO, environ_base=IP_REMOTO, **kw)


class TesteFabricaEGet(Base):
    def test_sem_config_devolve_a_fabrica(self) -> None:
        d = self.get()
        self.assertTrue(d["ok"])
        self.assertEqual((d["versao"], d["de_fabrica"]), (0, True))
        self.assertEqual([m["id"] for m in d["mensagens"]], [f"fab-{i}" for i in range(1, 7)])
        self.assertEqual([m["titulo"] for m in d["mensagens"]],
                         ["Farol aceso", "Caixa rápido", "Chamado a colaborador",
                          "Loja fecha em 15 min", "Criança no balcão", "Teste de som"])
        for m in d["mensagens"]:
            self.assertEqual(set(m), {"id", "titulo", "texto"})
        self.assertEqual(set(d), {"ok", "versao", "de_fabrica", "mensagens"})

    def test_a_fabrica_cabe_nas_regras_da_propria_rota(self) -> None:
        for m in server.MENSAGENS_DE_FABRICA:
            self.assertTrue(1 <= len(m["titulo"]) <= server.MENSAGEM_TITULO_MAX)
            self.assertTrue(1 <= len(m["texto"]) <= server.MENSAGEM_TEXTO_MAX)
        # Salvar a fábrica de volta, com os ids, tem de ser aceito.
        r = self.salvar([dict(m) for m in server.MENSAGENS_DE_FABRICA])
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))

    def test_fabrica_igual_aos_chips_do_html(self) -> None:
        html = (RAIZ / "static" / "index.html").read_text(encoding="utf-8")
        chips = re.findall(r'data-msg="([^"]*)"><span class="chip__text">([^<]*)</span>', html)
        # Sem skip: o HTML é o fallback quando a API falha; tem de mostrar a MESMA lista de fábrica.
        self.assertEqual(len(chips), len(server.MENSAGENS_DE_FABRICA),
                         "o fallback do HTML (chips com data-msg) difere da lista de fábrica do servidor")
        import html as _html
        self.assertEqual([(_html.unescape(t), _html.unescape(x)) for x, t in chips],
                         [(m["titulo"], m["texto"]) for m in server.MENSAGENS_DE_FABRICA])

    def test_limite_do_texto_e_o_mesmo_do_anuncio(self) -> None:
        self.assertEqual(server.MENSAGEM_TEXTO_MAX, server.ANUNCIO_TEXTO_MAX)
        self.assertEqual(server.ANUNCIO_TEXTO_MAX, 500)
        # 501 caracteres no /api/announce = 400 antes de qualquer áudio.
        r = self.c.post("/api/announce", json={"text": "x" * 501})
        self.assertEqual(r.status_code, 400)

    def test_nao_guarda_cache_no_navegador(self) -> None:
        self.assertEqual(self.c.get("/api/mensagens").headers["Cache-Control"], "no-store")

    def test_config_com_chave_torta_nao_derruba_a_rota(self) -> None:
        self.gravar_config({"mensagens": "lixo"})
        d = self.get()
        self.assertTrue(d["de_fabrica"])
        self.assertEqual(len(d["mensagens"]), 6)
        self.gravar_config({"mensagens_versao": "x", "mensagens": [
            {"id": "ok1", "titulo": "Bom", "texto": "Texto"},
            {"id": "ok1", "titulo": "Repetido", "texto": "Texto"},
            {"id": "ruim id", "titulo": "Id ruim", "texto": "Texto"},
            {"id": "t", "titulo": "  ", "texto": "Texto"},
            {"id": "n", "titulo": 5, "texto": "Texto"},
            "string", None,
        ]})
        d = self.get()
        self.assertEqual((d["versao"], d["de_fabrica"]), (0, False))
        self.assertEqual(d["mensagens"], [{"id": "ok1", "titulo": "Bom", "texto": "Texto"}])


class TesteSalvar(Base):
    def test_salva_e_o_get_devolve_o_mesmo(self) -> None:
        r = self.salvar([msg("Um", "Primeiro texto."), msg("Dois", "Segundo texto.")])
        self.assertEqual(r.status_code, 200)
        d = r.get_json()
        self.assertEqual((d["ok"], d["versao"], d["de_fabrica"]), (True, 1, False))
        self.assertEqual([(m["titulo"], m["texto"]) for m in d["mensagens"]],
                         [("Um", "Primeiro texto."), ("Dois", "Segundo texto.")])
        for m in d["mensagens"]:
            self.assertRegex(m["id"], r"^[0-9a-f]{8}$")
        self.assertEqual(self.get(), d)

    def test_ids_sao_estaveis_e_a_ordem_vale(self) -> None:
        d1 = self.salvar([msg("A"), msg("B"), msg("C")]).get_json()
        a, b, c = d1["mensagens"]
        # inverte a ordem, remove B e acrescenta uma nova
        d2 = self.salvar([dict(c), dict(a), msg("D")], versao=d1["versao"]).get_json()
        self.assertEqual([m["titulo"] for m in d2["mensagens"]], ["C", "A", "D"])
        self.assertEqual(d2["mensagens"][0]["id"], c["id"])
        self.assertEqual(d2["mensagens"][1]["id"], a["id"])
        self.assertNotIn(d2["mensagens"][2]["id"], (a["id"], b["id"], c["id"]))
        self.assertEqual(d2["versao"], 2)

    def test_a_partir_da_fabrica_os_ids_fab_continuam_valendo(self) -> None:
        d = self.get()
        primeira = dict(d["mensagens"][0])
        primeira["titulo"] = "Farol aceso (loja)"
        r = self.salvar([primeira, msg("Nova")])
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json()["mensagens"][0]["id"], "fab-1")

    def test_strip_crlf_e_emoji_composto(self) -> None:
        zwj = "\U0001F468‍\U0001F469"
        r = self.salvar([msg("  Nome  ", "  linha 1\r\nlinha 2\n\nlinha 4 " + zwj + "  ")])
        self.assertEqual(r.status_code, 200)
        m = r.get_json()["mensagens"][0]
        self.assertEqual(m["titulo"], "Nome")
        self.assertEqual(m["texto"], "linha 1\nlinha 2\n\nlinha 4 " + zwj)

    def test_lista_vazia_e_aceita_e_nao_e_fabrica(self) -> None:
        d = self.salvar([]).get_json()
        self.assertEqual((d["mensagens"], d["de_fabrica"], d["versao"]), ([], False, 1))

    def test_preserva_as_outras_chaves_do_config(self) -> None:
        self.gravar_config(dict(OUTRAS_CHAVES))
        self.assertEqual(self.salvar([msg()]).status_code, 200)
        disco = self.config_no_disco()
        for k, v in OUTRAS_CHAVES.items():
            self.assertEqual(disco[k], v, k)
        self.assertEqual(set(disco), set(OUTRAS_CHAVES) | {"mensagens", "mensagens_versao"})
        self.assertEqual(disco["mensagens_versao"], 1)
        self.assertEqual(len(disco["mensagens"]), 1)

    def test_gravacao_atomica_pelo_caminho_do_config(self) -> None:
        # Usa o mesmo gravador do resto do servidor (escrita atômica + DACL), uma vez por salvar.
        with mock.patch.object(server, "_gravar_config_atomico", wraps=server._gravar_config_atomico) as g:
            self.assertEqual(self.salvar([msg()]).status_code, 200)
            self.assertEqual(g.call_count, 1)
        self.assertEqual(list(Path(self._tmp.name).glob(".config.*")), [])  # nada de temporário sobrando

    def test_erro_nao_mexe_na_lista_gravada(self) -> None:
        self.salvar([msg("Boa")])
        antes = self.config_no_disco()
        self.assertEqual(self.salvar([msg("Boa"), msg("", "x")]).status_code, 400)
        self.assertEqual(self.config_no_disco(), antes)

    def test_corpo_grande_legitimo_passa_do_teto_global_de_16kb(self) -> None:
        itens = [msg(("Título ção " * 4)[:40], ("ã" * 500)) for _ in range(24)]
        corpo = json.dumps({"versao": 0, "mensagens": itens}, ensure_ascii=False).encode("utf-8")
        self.assertGreater(len(corpo), 16 * 1024)
        r = self.c.post("/api/mensagens", data=corpo, content_type="application/json")
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True)[:200])
        self.assertEqual(len(r.get_json()["mensagens"]), 24)

    def test_corpo_acima_de_60kb_e_413(self) -> None:
        corpo = json.dumps({"versao": 0, "mensagens": [msg()], "lixo": "x" * (61 * 1024)}).encode()
        r = self.c.post("/api/mensagens", data=corpo, content_type="application/json")
        self.assertEqual(r.status_code, 413)
        self.assertFalse(server.CONFIG_PATH.exists())

    def test_o_teto_maior_vale_so_nesta_rota(self) -> None:
        corpo = json.dumps({"versao": 0, "x": "y" * (20 * 1024)}).encode()
        r = self.c.post("/api/musicas/atualizar", data=corpo, content_type="application/json")
        self.assertEqual(r.status_code, 413)


class TesteLimitesEEsquema(Base):
    def erro(self, r, campo=None, indice=None) -> dict:
        self.assertEqual(r.status_code, 400, r.get_data(as_text=True))
        d = r.get_json()
        self.assertFalse(d["ok"])
        self.assertTrue(d["error"])
        if campo is not None:
            self.assertEqual(d.get("campo"), campo)
        if indice is not None:
            self.assertEqual(d.get("indice"), indice)
        self.assertFalse(server.CONFIG_PATH.exists())   # nada foi gravado
        return d

    def test_titulo_1_a_40(self) -> None:
        self.assertEqual(self.salvar([msg("x" * 40)]).status_code, 200)
        r = self.salvar([msg("x" * 41)], versao=1)
        self.assertEqual((r.status_code, r.get_json()["campo"], r.get_json()["indice"]), (400, "titulo", 0))

    def test_titulo_vazio_ou_so_espacos(self) -> None:
        for ruim in ("", "   ", " "):
            with self.subTest(titulo=ruim):
                self.erro(self.salvar([msg("Ok"), msg(ruim)], versao=0), "titulo", 1)

    def test_titulo_conta_depois_do_strip(self) -> None:
        self.assertEqual(self.salvar([msg("  " + "x" * 40 + "  ")]).status_code, 200)

    def test_texto_1_a_500(self) -> None:
        self.assertEqual(self.salvar([msg(texto="x" * 500)]).status_code, 200)
        r = self.salvar([msg(texto="x" * 501)], versao=1)
        self.assertEqual((r.status_code, r.get_json()["campo"], r.get_json()["indice"]), (400, "texto", 0))

    def test_texto_vazio_ou_so_espacos(self) -> None:
        for ruim in ("", " \n ", "\n\n"):
            with self.subTest(texto=ruim):
                self.erro(self.salvar([msg(texto=ruim)], versao=0), "texto", 0)

    def test_texto_conta_depois_do_strip(self) -> None:
        self.assertEqual(self.salvar([msg(texto=" " * 30 + "x" * 500 + "\n  ")]).status_code, 200)

    def test_titulo_nao_aceita_quebra_de_linha(self) -> None:
        self.erro(self.salvar([msg("Duas\nlinhas")], versao=0), "titulo", 0)

    def test_caracteres_de_controle_recusados(self) -> None:
        ruins = ["a\x00b", "a\x07b", "a\tb", "a\rb", "a\x1bb", "a\x7fb", "a\x85b",
                 "a‮b",          # inverte a direção do texto
                 "a​b",          # espaço de largura zero
                 "a﻿b", "a⁦b", "a\ud800b", "ab"]
        for ruim in ruins:
            with self.subTest(texto=ascii(ruim)):
                self.erro(self.salvar([msg(texto=ruim)], versao=0), "texto", 0)
            with self.subTest(titulo=ascii(ruim)):
                self.erro(self.salvar([msg(titulo=ruim)], versao=0), "titulo", 0)

    def test_codepoint_nao_atribuido_cn_passa(self) -> None:
        # "Cn" (ainda não atribuído) depende da versão do Unicode do Python (3.10 a 3.13): um emoji novo
        # não pode virar 400 conforme a máquina. U+0378 é Cn em todas as versões.
        import unicodedata
        self.assertEqual(unicodedata.category("͸"), "Cn")
        r = self.salvar([msg(titulo="Novo ͸", texto="Texto ͸ com emoji novo")])
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))

    def test_json_aninhado_demais_e_400_sem_traceback(self) -> None:
        # '[' * 50000 cabe no teto de 60 KB e estoura a recursão do scanner de JSON (restaurar tem o
        # teto global de 16 KB, então usa menos níveis; 10000 já basta para estourar).
        with self.assertNoLogs(level="ERROR"):
            for rota, n in (("/api/mensagens", 50000), ("/api/mensagens/restaurar", 10000)):
                for corpo in ("[" * n, '{"versao":' + "[" * n):
                    r = self.c.post(rota, data=corpo, content_type="application/json")
                    self.assertEqual(r.status_code, 400, rota)
                    self.assertNotIn("Recursion", r.get_data(as_text=True))

    def test_acentos_e_pontuacao_normais_passam(self) -> None:
        texto = "Atenção: o caixa nº 2 está livre! (até 10 itens) — “obrigado”. 😀"
        self.assertEqual(self.salvar([msg("Caixa ç", texto)]).status_code, 200)
        self.assertEqual(self.get()["mensagens"][0]["texto"], texto)

    def test_24_mensagens_ok_e_25_nao(self) -> None:
        self.assertEqual(self.salvar([msg(f"M{i}") for i in range(24)]).status_code, 200)
        r = self.salvar([msg(f"M{i}") for i in range(25)], versao=1)
        self.assertEqual((r.status_code, r.get_json()["campo"]), (400, "mensagens"))
        self.assertEqual(len(self.get()["mensagens"]), 24)

    def test_versao_invalida(self) -> None:
        for ruim in ("0", 0.5, True, False, None, -1, [], {}, 2**40):
            with self.subTest(versao=ruim):
                r = self.c.post("/api/mensagens", json={"versao": ruim, "mensagens": []})
                self.assertEqual(r.status_code, 400)
                self.assertEqual(r.get_json()["campo"], "versao")
        r = self.c.post("/api/mensagens", json={"mensagens": []})
        self.assertEqual(r.status_code, 400)
        r = self.c.post("/api/mensagens", json={"versao": 0})
        self.assertEqual((r.status_code, r.get_json()["campo"]), (400, "mensagens"))

    def test_lista_invalida(self) -> None:
        for ruim in ("x", None, {}, 5, [["a"]], ["a"], [None], [5], [[]]):
            with self.subTest(mensagens=ruim):
                r = self.c.post("/api/mensagens", json={"versao": 0, "mensagens": ruim})
                self.assertEqual(r.status_code, 400)

    def test_chave_desconhecida_em_qualquer_nivel(self) -> None:
        r = self.c.post("/api/mensagens", json={"versao": 0, "mensagens": [], "extra": 1})
        self.assertEqual((r.status_code, r.get_json()["campo"]), (400, "chave"))
        r = self.salvar([msg(), msg(extra="x")], versao=0)
        self.assertEqual((r.status_code, r.get_json()["campo"], r.get_json()["indice"]), (400, "chave", 1))
        r = self.c.post("/api/mensagens/restaurar", json={"versao": 0, "mensagens": []})
        self.assertEqual((r.status_code, r.get_json()["campo"]), (400, "chave"))
        self.assertFalse(server.CONFIG_PATH.exists())

    def test_titulo_ou_texto_ausente_ou_de_tipo_errado(self) -> None:
        for item in ({"texto": "x"}, {"titulo": "x"}, {"titulo": 5, "texto": "x"},
                     {"titulo": "x", "texto": ["x"]}, {"titulo": None, "texto": "x"},
                     {"titulo": "x", "texto": True}, {}):
            with self.subTest(item=item):
                r = self.c.post("/api/mensagens", json={"versao": 0, "mensagens": [item]})
                self.assertEqual(r.status_code, 400)

    def test_ids_invalidos_repetidos_e_desconhecidos(self) -> None:
        base = [msg()]
        for ruim in ("", "a b", "a/b", "x" * 33, 5, True, [], "ç", "a\nb"):
            with self.subTest(id=ruim):
                r = self.salvar([msg(id=ruim)], versao=0)
                self.assertEqual((r.status_code, r.get_json()["campo"]), (400, "id"))
        r = self.salvar([msg(id="fab-1"), msg(id="fab-1")], versao=0)
        self.assertEqual((r.status_code, r.get_json()["campo"], r.get_json()["indice"]), (400, "id", 1))
        r = self.salvar([msg(id="nao-existe")], versao=0)           # id que o servidor nunca deu
        self.assertEqual((r.status_code, r.get_json()["campo"]), (400, "id"))
        self.assertFalse(server.CONFIG_PATH.exists())
        self.assertEqual(self.salvar(base + [msg(id=None)], versao=0).status_code, 200)   # null = nova

    def test_corpo_nao_objeto(self) -> None:
        for corpo in ([], "x", 5):
            r = self.c.post("/api/mensagens", json=corpo)
            self.assertEqual(r.status_code, 400)

    def test_mensagem_de_erro_diz_qual_mensagem(self) -> None:
        r = self.salvar([msg(), msg(), msg(titulo="")], versao=0)
        self.assertIn("Mensagem 3", r.get_json()["error"])
        self.assertEqual(r.get_json()["indice"], 2)


class TesteVersaoERestaurar(Base):
    def test_409_quando_a_versao_mudou(self) -> None:
        self.salvar([msg("A")], versao=0)                               # outro aparelho salvou (v1)
        r = self.salvar([msg("B")], versao=0)                           # este ainda tinha a v0
        self.assertEqual(r.status_code, 409)
        d = r.get_json()
        self.assertEqual((d["ok"], d["codigo"]), (False, "versao_mudou"))
        self.assertEqual(d["error"], "Outro aparelho mudou as mensagens. Recarregue para ver a versão atual.")
        self.assertEqual(self.get()["mensagens"][0]["titulo"], "A")     # nada foi sobrescrito
        self.assertEqual(self.salvar([msg("B")], versao=1).status_code, 200)

    def test_versao_futura_tambem_e_409(self) -> None:
        self.assertEqual(self.salvar([msg()], versao=7).status_code, 409)

    def test_corrida_de_dois_aparelhos_so_um_ganha(self) -> None:
        self.salvar([msg("Base")], versao=0)
        resultados: list[int] = []
        barreira = threading.Barrier(6)

        def tentar(n: int) -> None:
            cli = server.app.test_client()
            barreira.wait()
            r = cli.post("/api/mensagens", json={"versao": 1, "mensagens": [msg(f"T{n}")]})
            resultados.append(r.status_code)

        ts = [threading.Thread(target=tentar, args=(n,)) for n in range(6)]
        for t in ts:
            t.start()
        for t in ts:
            t.join(20)
        self.assertEqual(sorted(resultados), [200, 409, 409, 409, 409, 409])
        self.assertEqual(self.config_no_disco()["mensagens_versao"], 2)

    def test_restaurar_volta_a_fabrica_e_preserva_o_resto(self) -> None:
        self.gravar_config(dict(OUTRAS_CHAVES))
        self.salvar([msg("Minha")])
        r = self.restaurar()
        self.assertEqual(r.status_code, 200)
        d = r.get_json()
        self.assertEqual((d["ok"], d["de_fabrica"], d["versao"]), (True, True, 2))
        self.assertEqual(len(d["mensagens"]), 6)
        self.assertEqual(d["mensagens"][0]["id"], "fab-1")
        self.assertEqual(self.get(), d)
        disco = self.config_no_disco()
        self.assertNotIn("mensagens", disco)
        self.assertEqual(disco["mensagens_versao"], 2)
        for k, v in OUTRAS_CHAVES.items():
            self.assertEqual(disco[k], v, k)

    def test_restaurar_muda_a_versao_para_o_outro_aparelho(self) -> None:
        self.salvar([msg("Minha")])                                     # v1
        self.restaurar(versao=1)                                        # v2
        # o aparelho B ainda estava na v1 e tenta salvar por cima da restauração
        self.assertEqual(self.salvar([msg("B")], versao=1).status_code, 409)

    def test_restaurar_409_com_versao_velha(self) -> None:
        self.salvar([msg("Minha")])
        r = self.restaurar(versao=0)
        self.assertEqual((r.status_code, r.get_json()["codigo"]), (409, "versao_mudou"))
        self.assertFalse(self.get()["de_fabrica"])

    def test_restaurar_ja_na_fabrica_nao_grava(self) -> None:
        with mock.patch.object(server, "_gravar_config_atomico") as g:
            r = self.restaurar(versao=0)
            self.assertEqual(r.status_code, 200)
            self.assertEqual((r.get_json()["versao"], r.get_json()["de_fabrica"]), (0, True))
            g.assert_not_called()

    def test_restaurar_esquema_estrito(self) -> None:
        for corpo in ({}, {"versao": "0"}, {"versao": True}, {"versao": -1}, {"versao": 0, "x": 1}):
            with self.subTest(corpo=corpo):
                r = self.c.post("/api/mensagens/restaurar", json=corpo)
                self.assertEqual(r.status_code, 400)

    def test_salvar_depois_de_restaurar_com_ids_fab(self) -> None:
        self.salvar([msg("Minha")])
        d = self.restaurar().get_json()
        r = self.salvar(d["mensagens"][:2], versao=d["versao"])
        self.assertEqual(r.status_code, 200)
        self.assertEqual([m["id"] for m in r.get_json()["mensagens"]], ["fab-1", "fab-2"])


class TesteAcessoEAuditoria(Base):
    def test_remoto_sem_pin_configurado_acesso_desligado_403(self) -> None:
        for metodo, rota, corpo in (("get", "/api/mensagens", None),
                                    ("post", "/api/mensagens", {"versao": 0, "mensagens": []}),
                                    ("post", "/api/mensagens/restaurar", {"versao": 0})):
            r = self.remoto(metodo, rota, **({"json": corpo} if corpo is not None else {}))
            self.assertEqual(r.status_code, 403, rota)
            self.assertEqual(r.get_json()["auth"], "pin_nao_configurado")
        self.assertFalse(server.CONFIG_PATH.exists())

    def test_remoto_com_pin_mas_sem_login_401(self) -> None:
        server.aplicar_pin(PIN)
        antes = self.config_no_disco()
        self.assertEqual(self.remoto("get", "/api/mensagens").status_code, 401)
        r = self.remoto("post", "/api/mensagens", json={"versao": 0, "mensagens": [msg()]},
                        headers={"Origin": HOST_REMOTO})
        self.assertEqual(r.status_code, 401)
        r = self.remoto("post", "/api/mensagens/restaurar", json={"versao": 0}, headers={"Origin": HOST_REMOTO})
        self.assertEqual(r.status_code, 401)
        self.assertEqual(self.config_no_disco(), antes)

    def test_remoto_com_pin_edita(self) -> None:
        server.aplicar_pin(PIN)
        self.assertEqual(self.remoto("post", "/api/auth/login", json={"pin": PIN}).status_code, 200)
        cab = {"Origin": HOST_REMOTO}
        self.assertEqual(self.remoto("get", "/api/mensagens").status_code, 200)
        r = self.remoto("post", "/api/mensagens", json={"versao": 0, "mensagens": [msg("Do celular")]},
                        headers=cab)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.get()["mensagens"][0]["titulo"], "Do celular")
        r = self.remoto("post", "/api/mensagens/restaurar", json={"versao": 1}, headers=cab)
        self.assertEqual(r.status_code, 200)
        self.assertTrue(self.get()["de_fabrica"])

    def test_415_nao_json(self) -> None:
        for rota in ("/api/mensagens", "/api/mensagens/restaurar"):
            r = self.c.post(rota, data='{"versao":0,"mensagens":[]}', content_type="text/plain")
            self.assertEqual(r.status_code, 415, rota)
            r = self.c.post(rota, data="versao=0", content_type="application/x-www-form-urlencoded")
            self.assertEqual(r.status_code, 415, rota)
        self.assertFalse(server.CONFIG_PATH.exists())

    def test_403_origin_de_outro_host(self) -> None:
        cab = {"Origin": "http://evil.example"}
        self.assertEqual(self.salvar([msg()], versao=0, headers=cab).status_code, 403)
        self.assertEqual(self.restaurar(versao=0, headers=cab).status_code, 403)
        self.assertEqual(self.c.get("/api/mensagens", headers=cab).status_code, 403)
        self.assertFalse(server.CONFIG_PATH.exists())

    def test_403_sec_fetch_site_cross_site(self) -> None:
        r = self.salvar([msg()], versao=0, headers={"Sec-Fetch-Site": "cross-site"})
        self.assertEqual(r.status_code, 403)

    def test_host_estranho_421(self) -> None:
        r = self.c.get("/api/mensagens", base_url="http://evil.example")
        self.assertEqual(r.status_code, 421)

    def test_limite_de_taxa_por_ip(self) -> None:
        for i in range(server.MENSAGENS_ESCRITA_MAX_POR_MIN):
            self.assertEqual(self.salvar([msg(f"M{i}")], versao=i).status_code, 200, i)
        r = self.salvar([msg("Demais")], versao=server.MENSAGENS_ESCRITA_MAX_POR_MIN)
        self.assertEqual(r.status_code, 429)
        self.assertFalse(r.get_json()["ok"])
        self.assertEqual(self.get()["mensagens"][0]["titulo"], f"M{server.MENSAGENS_ESCRITA_MAX_POR_MIN - 1}")
        # restaurar divide o mesmo limite; o GET não conta
        self.assertEqual(self.restaurar().status_code, 429)
        self.assertEqual(self.c.get("/api/mensagens").status_code, 200)

    def test_limite_de_taxa_e_por_ip(self) -> None:
        for i in range(server.MENSAGENS_ESCRITA_MAX_POR_MIN):
            self.salvar([msg("A")], versao=i)
        self.assertEqual(self.salvar([msg("A")], versao=99).status_code, 429)
        server.aplicar_pin(PIN)
        self.remoto("post", "/api/auth/login", json={"pin": PIN})
        r = self.remoto("post", "/api/mensagens",
                        json={"versao": self.get()["versao"], "mensagens": [msg("Outro IP")]},
                        headers={"Origin": HOST_REMOTO})
        self.assertEqual(r.status_code, 200)

    def test_400_nao_vira_500_e_nao_vaza_detalhe(self) -> None:
        r = self.c.post("/api/mensagens", data="{nao e json", content_type="application/json")
        self.assertEqual(r.status_code, 400)
        self.assertNotIn("Traceback", r.get_data(as_text=True))

    def test_auditoria_so_quantidade_nunca_conteudo(self) -> None:
        segredo_t, segredo_n = "TituloSecretoXYZ", "TextoSecretoQWERTY promoção da semana"
        with self.assertLogs(AUDITORIA, level="INFO") as cap:
            self.salvar([msg(segredo_t, segredo_n), msg("Outro", "Outro texto")], versao=0)
            self.restaurar(versao=1)
        linhas = "\n".join(cap.output)
        self.assertIn("acao=mensagens ", linhas)
        self.assertIn("acao=mensagens_restaurar ", linhas)
        self.assertIn("origem=local", linhas)
        self.assertIn("total=2", linhas)
        self.assertIn("ip=127.0.0.1", linhas)
        for proibido in (segredo_t, segredo_n, "Outro texto", "promoção"):
            self.assertNotIn(proibido, linhas)

    def test_auditoria_remota_diz_remoto_e_o_ip(self) -> None:
        server.aplicar_pin(PIN)
        self.remoto("post", "/api/auth/login", json={"pin": PIN})
        with self.assertLogs(AUDITORIA, level="INFO") as cap:
            self.remoto("post", "/api/mensagens", json={"versao": 0, "mensagens": [msg("Z", "Texto Z")]},
                        headers={"Origin": HOST_REMOTO})
        self.assertIn("origem=remoto", cap.output[0])
        self.assertIn("ip=192.168.0.50", cap.output[0])
        self.assertNotIn("Texto Z", cap.output[0])

    def test_erro_de_validacao_nao_gera_auditoria_nem_log_de_conteudo(self) -> None:
        with self.assertNoLogs(AUDITORIA, level="INFO"):
            self.salvar([msg(texto="")], versao=0)


if __name__ == "__main__":
    unittest.main()
